"""A deliberately small task schema and referenced evidence checks."""

from dataclasses import dataclass
from typing import Any

from .runtime import Runtime
from .validation import strings


@dataclass(frozen=True)
class Task:
    identifier: str
    title: str
    status: str
    evidence: Any = None


def load_tasks(runtime: Runtime, name: str) -> tuple[Task, ...]:
    value = runtime.files.read_json(name)
    if not isinstance(value, dict) or not isinstance(value.get("tasks"), list):
        raise ValueError("Expected a task list")
    tasks = []
    seen = set()
    for row in value["tasks"]:
        if not isinstance(row, dict):
            raise ValueError("Expected a task object")
        identifier, title, status = row.get("id"), row.get("title"), row.get("status")
        if not all(isinstance(item, str) and item.strip() for item in (identifier, title, status)):
            raise ValueError("Invalid task fields")
        if identifier in seen:
            raise ValueError("Duplicate task identifier")
        seen.add(identifier)
        tasks.append(Task(identifier, title, status, row.get("evidence")))
    return tuple(tasks)


def has_evidence(runtime: Runtime, task: Task) -> bool:
    evidence = task.evidence
    if not isinstance(evidence, dict):
        return False
    filename = evidence.get("file")
    if isinstance(filename, str):
        try:
            if runtime.files.read_text(filename).strip():
                return True
        except (OSError, ValueError):
            pass
    try:
        command = strings(evidence.get("command"))
    except ValueError:
        return False
    return bool(command) and type(evidence.get("exit_code")) is int and evidence["exit_code"] == 0
