import fcntl
import json
import time

import pytest

from guardrails import audit
from guardrails.storage import FILE_LIMIT, ProjectFiles, read_bytes

from conftest import ROOT, Workspace


def test_audit_rotation_is_bounded_jsonl(workspace: Workspace) -> None:
    runtime = workspace.runtime()
    config = {"path": "audit.jsonl", "max_bytes": 300, "backups": 2}
    for _ in range(12):
        audit.record(runtime, config, "session_resume", "context", time.monotonic())
    files = sorted(workspace.root.glob("audit.jsonl*"))
    assert {path.name for path in files} == {"audit.jsonl", "audit.jsonl.1", "audit.jsonl.2", "audit.jsonl.lock"}
    for path in files:
        if path.suffix == ".lock":
            continue
        assert 0 < path.stat().st_size <= 300
        for line in path.read_text().splitlines():
            assert set(json.loads(line)) == {"timestamp", "hook", "outcome", "duration_ms"}


def test_oversized_preexisting_audit_is_discarded(workspace: Workspace) -> None:
    workspace.write("audit.jsonl", "x" * 5000)
    audit.record(workspace.runtime(), {"path": "audit.jsonl", "max_bytes": 300}, "session_resume", "pass", time.monotonic())
    assert (workspace.root / "audit.jsonl").stat().st_size <= 300
    assert not (workspace.root / "audit.jsonl.1").exists()


def test_reduced_audit_limits_prune_old_backups(workspace: Workspace) -> None:
    workspace.write("audit.jsonl.1", "x" * 5000)
    workspace.write("audit.jsonl.2", "Old backup")
    workspace.write("audit.jsonl.5", "Old backup")
    audit.record(workspace.runtime(), {"path": "audit.jsonl", "max_bytes": 300, "backups": 1}, "session_resume", "pass", time.monotonic())
    assert {path.name for path in workspace.root.glob("audit.jsonl*")} == {"audit.jsonl", "audit.jsonl.lock"}


def test_audit_has_no_prompt_brief_session_or_error_content(workspace: Workspace) -> None:
    event = workspace.event("agent_guard", session_id="private-session-content", prompt="private-prompt-content")
    event["tool_input"]["prompt"] = "private-brief-content"
    assert workspace.call("agent_guard", event=event) is not None
    content = (workspace.root / ".guardrails/audit.jsonl").read_text()
    assert "private" not in content and "cheaper" not in content
    row = json.loads(content)
    assert set(row) == {"timestamp", "hook", "outcome", "duration_ms"}
    assert row["outcome"] == "deny"


def test_audit_lock_contention_discards_the_response(workspace: Workspace) -> None:
    lock = workspace.root / ".guardrails/audit.jsonl.lock"
    lock.parent.mkdir()
    with lock.open("w") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert workspace.call("session_resume") is None


@pytest.mark.parametrize("name", ["../escape.txt", "/absolute-example.txt", ""])
def test_project_paths_are_confined(workspace: Workspace, name: str) -> None:
    with pytest.raises(ValueError):
        workspace.runtime().files.path(name)


def test_symlink_escape_is_rejected_for_reads_and_writes(workspace: Workspace) -> None:
    # The sibling fixture directory is still inside the configured test base.
    sibling = workspace.root.parent / (workspace.root.name + "-sibling")
    sibling.mkdir()
    (sibling / "state.txt").write_text("Example outside the project root.")
    (workspace.root / "linked").symlink_to(sibling, target_is_directory=True)
    files = workspace.runtime().files
    with pytest.raises(ValueError):
        files.read_text("linked/state.txt")
    with pytest.raises(ValueError):
        files.write_json("linked/new.json", {})
    assert not (sibling / "new.json").exists()


def test_symlink_inside_project_is_rejected_as_a_data_file(workspace: Workspace) -> None:
    (workspace.root / "linked.txt").symlink_to(workspace.root / "state.txt")
    with pytest.raises(OSError):
        workspace.runtime().files.read_text("linked.txt")


def test_regular_files_have_exact_byte_bounds(workspace: Workspace) -> None:
    file = workspace.write("large.txt", "x" * FILE_LIMIT)
    assert len(read_bytes(file)) == FILE_LIMIT
    file.write_text("x" * (FILE_LIMIT + 1))
    with pytest.raises(ValueError):
        read_bytes(file)
    assert workspace.call("session_resume", config=workspace.config("session_resume", state_file="large.txt")) is None


def test_atomic_state_replacement_and_failure_leave_no_temporary_file(workspace: Workspace) -> None:
    files = workspace.runtime().files
    files.write_json("nested/state.json", {"blocks": 1})
    files.write_json("nested/state.json", {"blocks": 2})
    assert files.read_json("nested/state.json") == {"blocks": 2}
    with pytest.raises(ValueError):
        files.write_json("nested/state.json", {"data": "x" * FILE_LIMIT})
    assert files.read_json("nested/state.json") == {"blocks": 2}
    assert not list(workspace.root.rglob(".pending-*"))


def test_settings_wire_only_the_four_examples() -> None:
    settings = json.loads((ROOT / "examples/settings.json").read_text())
    assert set(settings) == {"hooks"}
    assert set(settings["hooks"]) == {"SessionStart", "UserPromptSubmit", "PreToolUse", "Stop"}
    assert settings["hooks"]["PreToolUse"][0]["matcher"] == "Agent"
    for entries in settings["hooks"].values():
        command = entries[0]["hooks"][0]
        assert command["type"] == "command" and command["timeout"] == 2
        assert "$CLAUDE_PROJECT_DIR/hook.py" in command["command"]
        hook = command["command"].split('hook.py" ', 1)[1].split()[0]
        assert (ROOT / "examples" / f"{hook}.json").is_file()


def test_example_data_is_valid(workspace: Workspace) -> None:
    assert ProjectFiles(ROOT).read_json("examples/project/usage.json") == {"used_percent": 42}
    for hook in ("session_resume", "prompt_context", "agent_guard", "stop_guard"):
        config = json.loads((ROOT / "examples" / f"{hook}.json").read_text())
        for key in ("state_file", "tasks_file", "usage_file"):
            if key in config:
                assert (ROOT / config[key]).is_file()
