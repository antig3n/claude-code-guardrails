"""Session-isolated turn counters; prompts and session identifiers are never stored."""

import hashlib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from .runtime import Runtime
from .validation import integer


@contextmanager
def turn_state(
    runtime: Runtime, event: Mapping[str, Any], state_dir: str,
) -> Iterator[dict[str, int]]:
    runtime.files.path(state_dir)
    session = event["session_id"]
    if not isinstance(session, str) or not 1 <= len(session) <= 160:
        raise ValueError("Invalid session identifier")
    key = hashlib.sha256(session.encode("utf-8")).hexdigest()
    name = f"{state_dir}/sessions/{key}.json"
    with runtime.files.lock(name + ".lock"):
        try:
            state = runtime.files.read_json(name)
        except FileNotFoundError:
            state = {"turn": 0, "blocks": 0}
        if not isinstance(state, dict) or set(state) != {"turn", "blocks"}:
            raise ValueError("Invalid turn state")
        integer(state["turn"], 0, 2**53)
        integer(state["blocks"], 0, 2)
        yield state
        runtime.files.write_json(name, state)


def reset_turn(runtime: Runtime, event: Mapping[str, Any], state_dir: str) -> None:
    with turn_state(runtime, event, state_dir) as state:
        state["turn"] += 1
        state["blocks"] = 0
