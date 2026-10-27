"""Require evidence for completed tasks, with at most two blocks per turn."""

from collections.abc import Mapping
from typing import Any

from ..runtime import Runtime
from ..tasks import has_evidence, load_tasks
from ..turns import turn_state
from ..validation import integer


def handle(event: Mapping[str, Any], config: Mapping[str, Any], runtime: Runtime) -> dict[str, Any] | None:
    active = event.get("stop_hook_active", False)
    if type(active) is not bool:
        raise ValueError("Expected a boolean stop flag")
    if active:
        return None
    maximum = integer(config.get("max_blocks", 2), 0, 2)
    with turn_state(runtime, event, config.get("state_dir", ".guardrails")) as state:
        if state["blocks"] >= maximum:
            return None
        missing = [
            task.identifier for task in load_tasks(runtime, config.get("tasks_file", ".guardrails/tasks.json"))
            if task.status == "done" and not has_evidence(runtime, task)
        ]
        if not missing:
            return None
        state["blocks"] += 1
        names = ", ".join(missing)[:500]
        return {"decision": "block", "reason": f"Add evidence for completed tasks: {names}. Reference a nonempty proof file or a test command with exit_code 0."}
