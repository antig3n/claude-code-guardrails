"""Inject at most max_chars characters of a project state file."""

from collections.abc import Mapping
from typing import Any

from ..runtime import Runtime
from ..validation import integer


def handle(event: Mapping[str, Any], config: Mapping[str, Any], runtime: Runtime) -> dict[str, Any] | None:
    maximum = integer(config.get("max_chars", 1200), 0, 3000)
    text = runtime.files.read_text(config.get("state_file", ".guardrails/state.txt")).strip()[:maximum]
    if not text:
        return None
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}
