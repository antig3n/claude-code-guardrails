"""Parse, dispatch, audit and serialize before the command boundary emits JSON."""

import argparse
import importlib
import json
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import audit
from .entry import DeadlineExceeded
from .runtime import Runtime
from .storage import decode_json, read_bytes

EVENTS = {
    "session_resume": "SessionStart",
    "prompt_context": "UserPromptSubmit",
    "agent_guard": "PreToolUse",
    "stop_guard": "Stop",
}
INPUT_LIMIT = 65_536
OUTPUT_LIMIT = 4096


def _read_event() -> dict[str, Any]:
    data = bytearray()
    while len(data) <= INPUT_LIMIT:
        chunk = os.read(sys.stdin.fileno(), min(8192, INPUT_LIMIT + 1 - len(data)))
        if not chunk:
            break
        data.extend(chunk)
    if len(data) > INPUT_LIMIT:
        raise ValueError("Input exceeds the size limit")
    event = decode_json(bytes(data))
    if not isinstance(event, dict):
        raise ValueError("Expected an event object")
    if not isinstance(event.get("cwd"), str) or not event["cwd"]:
        raise ValueError("Expected a project directory")
    if not isinstance(event.get("session_id"), str) or not 1 <= len(event["session_id"]) <= 160:
        raise ValueError("Expected a session identifier")
    return event


def _outcome(response: dict[str, Any] | None) -> str:
    if response is None:
        return "pass"
    if response.get("decision") == "block":
        return "block"
    if response.get("hookSpecificOutput", {}).get("permissionDecision") == "deny":
        return "deny"
    return "context"


def execute(arguments: Sequence[str], deadline: float, started: float) -> bytes | None:
    runtime = None
    audit_config: dict[str, Any] = {}
    hook = ""
    try:
        parser = argparse.ArgumentParser(description="Run a bounded local hook")
        parser.add_argument("hook", choices=tuple(EVENTS))
        parser.add_argument("--config", required=True)
        options = parser.parse_args(arguments)
        hook = options.hook
        event = _read_event()
        if event.get("hook_event_name") != EVENTS[hook]:
            raise ValueError("Hook event does not match the selected policy")
        runtime = Runtime(Path(event["cwd"]), deadline)
        config = decode_json(read_bytes(Path(options.config)))
        if not isinstance(config, dict) or not isinstance(config.get("audit", {}), dict):
            raise ValueError("Expected a configuration object")
        audit_config = config.get("audit", {})
        handler = importlib.import_module(f"guardrails.hooks.{hook}").handle
        response = handler(event, config, runtime)
        payload = None if response is None else (json.dumps(response, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        if payload is not None and len(payload) > OUTPUT_LIMIT:
            raise ValueError("Response exceeds the size limit")
        runtime.remaining()
        audit.record(runtime, audit_config, hook, _outcome(response), started)
        runtime.remaining()
        return payload
    except BaseException as error:
        if runtime is not None and time.monotonic() < deadline:
            try:
                outcome = "timeout" if isinstance(error, DeadlineExceeded) else "error"
                audit.record(runtime, audit_config, hook, outcome, started)
            except BaseException:
                pass
        return None
