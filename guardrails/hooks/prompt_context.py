"""Reset the turn counter and inject short local usage and task context."""

from collections.abc import Mapping
from typing import Any

from ..runtime import Runtime
from ..tasks import load_tasks
from ..turns import reset_turn
from ..validation import integer, used_percentage


def handle(event: Mapping[str, Any], config: Mapping[str, Any], runtime: Runtime) -> dict[str, Any] | None:
    reset_turn(runtime, event, config.get("state_dir", ".guardrails"))
    maximum = integer(config.get("max_chars", 800), 0, 3000)
    count = integer(config.get("max_tasks", 3), 0, 20)
    lines = []
    if config.get("usage_file") is not None:
        used = used_percentage(runtime.files.read_json(config["usage_file"]))
        lines.append(f"Usage remaining: {100 - used:g}%.")
    if config.get("tasks_file") is not None:
        active = [task for task in load_tasks(runtime, config["tasks_file"]) if task.status == "in_progress"]
        lines.extend(f"In progress: {task.identifier} - {task.title}" for task in active[:count])
    text = "\n".join(lines)[:maximum]
    if not text:
        return None
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": text}}
