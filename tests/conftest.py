import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from guardrails.runner import EVENTS
from guardrails.runtime import Runtime

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Workspace:
    root: Path

    def write(self, name: str, value: Any) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
        return path

    def event(self, hook: str, **updates: Any) -> dict[str, Any]:
        event: dict[str, Any] = {
            "session_id": "fictional-session",
            "cwd": str(self.root),
            "hook_event_name": EVENTS[hook],
        }
        if hook == "agent_guard":
            event.update(tool_name="Agent", tool_input={
                "subagent_type": "builder", "model": "strong-model",
                "prompt": "Implement the example.\nWhy not the cheaper executor: Requires a complex design decision.",
            })
        event.update(updates)
        return event

    def config(self, hook: str, **updates: Any) -> Path:
        config = {
            "session_resume": {"state_file": "state.txt", "max_chars": 1200},
            "prompt_context": {"usage_file": "usage.json", "tasks_file": "tasks.json"},
            "agent_guard": {
                "usage_file": "usage.json", "exceptions": ["reader"],
                "forbidden_models": {"builder": ["economy-model"], "*": ["retired-model"]},
            },
            "stop_guard": {"tasks_file": "tasks.json"},
        }[hook]
        config.update(updates)
        return self.write(f"{hook}.json", config)

    def command(self, hook: str, config: Path, program: str | None = None) -> list[str]:
        entry = ["-c", program] if program is not None else [str(ROOT / "hook.py")]
        return [sys.executable, "-B", *entry, hook, "--config", str(config)]

    def environment(self, budget: float | str | None = None) -> dict[str, str]:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        env.pop("GUARDRAILS_TIMEOUT_SECONDS", None)
        if budget is not None:
            env["GUARDRAILS_TIMEOUT_SECONDS"] = str(budget)
        return env

    def call(
        self, hook: str, *, event: dict[str, Any] | None = None, raw: bytes | None = None,
        config: Path | None = None, budget: float | str | None = None, program: str | None = None,
    ) -> dict[str, Any] | None:
        process = subprocess.run(
            self.command(hook, config or self.config(hook), program),
            input=raw if raw is not None else json.dumps(event or self.event(hook)).encode(),
            cwd=ROOT, env=self.environment(budget), capture_output=True, timeout=3,
        )
        assert process.returncode == 0
        assert process.stderr == b""
        if not process.stdout:
            return None
        assert process.stdout.endswith(b"\n") and process.stdout.count(b"\n") == 1
        return json.loads(process.stdout)

    def runtime(self, seconds: float = 2) -> Runtime:
        return Runtime(self.root, time.monotonic() + seconds)

    def tasks(self, *tasks: dict[str, Any]) -> None:
        self.write("tasks.json", {"tasks": list(tasks)})


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    work = Workspace(tmp_path)
    work.write("state.txt", "Fictional parser ready for review.\nNext: empty input case.")
    work.write("usage.json", {"used_percent": 42})
    work.tasks({"id": "DEMO-1", "title": "Review parser", "status": "in_progress"})
    return work
