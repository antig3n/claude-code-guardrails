import fcntl
import json
import os
import subprocess
import sys
import time

import pytest

from guardrails.entry import DeadlineExceeded
from guardrails.runner import EVENTS, INPUT_LIMIT

from conftest import ROOT, Workspace


@pytest.mark.parametrize("hook", EVENTS)
@pytest.mark.parametrize("raw", [b"broken", b"null", b"[]"])
def test_corrupt_input_is_silent_and_successful(workspace: Workspace, hook: str, raw: bytes) -> None:
    assert workspace.call(hook, raw=raw) is None


@pytest.mark.parametrize("raw", [b"42", b"{}", b"\xff", b'{"cwd":NaN}'])
def test_invalid_json_types_encoding_and_constants(workspace: Workspace, raw: bytes) -> None:
    assert workspace.call("session_resume", raw=raw) is None


@pytest.mark.parametrize("hook", EVENTS)
def test_wrong_event_fails_open(workspace: Workspace, hook: str) -> None:
    assert workspace.call(hook, event=workspace.event(hook, hook_event_name="Unexpected")) is None


def test_missing_or_invalid_config_fails_open(workspace: Workspace) -> None:
    workspace.write("invalid.json", "broken")
    assert workspace.call("session_resume", config=workspace.root / "invalid.json") is None
    assert workspace.call("session_resume", config=workspace.root / "missing.json") is None


@pytest.mark.parametrize("field,value", [("session_id", None), ("session_id", ""), ("session_id", "x" * 161), ("cwd", 42)])
def test_invalid_common_fields(workspace: Workspace, field: str, value: object) -> None:
    assert workspace.call("session_resume", event=workspace.event("session_resume", **{field: value})) is None


@pytest.mark.parametrize("value", [-1, 101, "42", True, None])
def test_invalid_usage_data(workspace: Workspace, value: object) -> None:
    workspace.write("usage.json", {"used_percent": value})
    assert workspace.call("prompt_context") is None
    assert workspace.call("agent_guard") is None


@pytest.mark.parametrize("raw", ['{"used_percent":NaN}', '{"used_percent":Infinity}', '{"used_percent":-Infinity}'])
def test_nonfinite_usage_json(workspace: Workspace, raw: str) -> None:
    workspace.write("usage.json", raw)
    assert workspace.call("agent_guard") is None


@pytest.mark.parametrize("budget", ["bad", "nan", "inf", "0", "-1", "61"])
def test_invalid_budget_is_silent(workspace: Workspace, budget: str) -> None:
    assert workspace.call("session_resume", budget=budget) is None


@pytest.mark.parametrize("budget", [None, 0.16])
def test_open_stdin_obeys_default_or_configured_deadline(workspace: Workspace, budget: float | None) -> None:
    process = subprocess.Popen(
        workspace.command("session_resume", workspace.config("session_resume")),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=workspace.environment(budget), cwd=ROOT,
    )
    started = time.monotonic()
    try:
        process.wait(timeout=(budget or 1.2) + 0.8)
        elapsed = time.monotonic() - started
        assert elapsed < (budget or 1.2) + 0.8
        assert elapsed >= (budget or 1.2) * 0.8
        output, errors = process.communicate()
        assert (process.returncode, output, errors) == (0, b"", b"")
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()


def test_oversized_stdin_is_rejected_without_waiting_for_eof(workspace: Workspace) -> None:
    process = subprocess.Popen(
        workspace.command("session_resume", workspace.config("session_resume")),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=workspace.environment(), cwd=ROOT,
    )
    try:
        assert process.stdin is not None
        process.stdin.write(b" " * (INPUT_LIMIT + 1))
        process.stdin.flush()
        process.wait(timeout=0.8)
        output, errors = process.communicate()
        assert (process.returncode, output, errors) == (0, b"", b"")
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()


def test_imports_are_inside_the_deadline(workspace: Workspace) -> None:
    program = """
import builtins
import time
from guardrails.entry import main
original = builtins.__import__
def importing(name, *args, **kwargs):
    if name == 'runner':
        time.sleep(5)
    return original(name, *args, **kwargs)
builtins.__import__ = importing
main()
"""
    started = time.monotonic()
    assert workspace.call("session_resume", budget=0.16, program=program) is None
    assert time.monotonic() - started < 0.8


def test_handler_timeout_discards_output(workspace: Workspace) -> None:
    program = """
import time
from guardrails.hooks import session_resume
from guardrails.entry import main
def delayed(*args):
    print('must never be visible')
    time.sleep(5)
    return {'decision': 'block', 'reason': 'too late'}
session_resume.handle = delayed
main()
"""
    assert workspace.call("session_resume", budget=0.16, program=program) is None


def test_handler_error_discards_early_prints_and_exception_text(workspace: Workspace) -> None:
    program = """
import os
from guardrails.hooks import session_resume
from guardrails.entry import main
def failing(*args):
    print('private content')
    os.write(2, b'private content')
    raise RuntimeError('private content')
session_resume.handle = failing
main()
"""
    assert workspace.call("session_resume", program=program) is None
    rows = (workspace.root / ".guardrails/audit.jsonl").read_text()
    assert "private content" not in rows
    assert json.loads(rows)["outcome"] == "error"


def test_successful_handler_prints_are_discarded(workspace: Workspace) -> None:
    program = """
from guardrails.hooks import session_resume
from guardrails.entry import main
original = session_resume.handle
def noisy(*args):
    print('unstructured output')
    return original(*args)
session_resume.handle = noisy
main()
"""
    response = workspace.call("session_resume", program=program)
    assert response is not None
    assert response["hookSpecificOutput"]["additionalContext"].startswith("Fictional")


@pytest.mark.parametrize("expression", ["{'bad': {1, 2}}", "{'bad': float('nan')}", "{'bad': 'x' * 5000}"])
def test_serialization_and_output_bounds_are_fail_open(workspace: Workspace, expression: str) -> None:
    program = f"""
from guardrails.hooks import session_resume
from guardrails.entry import main
session_resume.handle = lambda *args: {expression}
main()
"""
    assert workspace.call("session_resume", program=program) is None


@pytest.mark.parametrize("target", ["state.txt", ".guardrails/audit.jsonl"])
def test_fifo_inputs_and_audit_never_block(workspace: Workspace, target: str) -> None:
    path = workspace.root / target
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    os.mkfifo(path)
    started = time.monotonic()
    assert workspace.call("session_resume", budget=0.3) is None
    assert time.monotonic() - started < 0.8


def test_state_lock_contention_passes_without_waiting(workspace: Workspace) -> None:
    workspace.tasks({"id": "DEMO-1", "title": "Closed", "status": "done"})
    assert workspace.call("stop_guard") is not None
    lock = next((workspace.root / ".guardrails/sessions").glob("*.lock"))
    with lock.open("r+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert workspace.call("stop_guard") is None
    assert workspace.call("stop_guard") is not None
    assert workspace.call("stop_guard") is None


def test_corrupt_turn_state_fails_open(workspace: Workspace) -> None:
    workspace.call("prompt_context")
    state = next((workspace.root / ".guardrails/sessions").glob("*.json"))
    state.write_text('{"turn": 0, "blocks": -1}')
    assert workspace.call("stop_guard") is None


@pytest.mark.parametrize("hook,options", [
    ("session_resume", {"max_chars": -1}),
    ("session_resume", {"max_chars": True}),
    ("prompt_context", {"max_tasks": -1}),
    ("agent_guard", {"justification_prefix": ""}),
    ("agent_guard", {"exceptions": "reader"}),
    ("agent_guard", {"usage_threshold": 101}),
    ("stop_guard", {"max_blocks": 3}),
    ("stop_guard", {"state_dir": None}),
    ("prompt_context", {"state_dir": 42}),
])
def test_invalid_policy_configuration(workspace: Workspace, hook: str, options: dict) -> None:
    assert workspace.call(hook, config=workspace.config(hook, **options)) is None


def test_invalid_agent_and_stop_field_types(workspace: Workspace) -> None:
    assert workspace.call("agent_guard", event=workspace.event("agent_guard", tool_input=42)) is None
    event = workspace.event("agent_guard")
    event["tool_input"]["prompt"] = ["invalid"]
    assert workspace.call("agent_guard", event=event) is None
    assert workspace.call("stop_guard", event=workspace.event("stop_guard", stop_hook_active="false")) is None


def test_subprocess_stdout_is_bounded_and_errors_propagate(workspace: Workspace) -> None:
    runtime = workspace.runtime()
    assert runtime.run([sys.executable, "-B", "-c", "print('example')"]) == b"example\n"
    with pytest.raises(ValueError):
        runtime.run([sys.executable, "-B", "-c", "print('x' * 10000)"], output_limit=100)
    with pytest.raises(RuntimeError):
        runtime.run([sys.executable, "-B", "-c", "raise SystemExit(1)"])
    with pytest.raises(ValueError):
        runtime.run("a shell string")


def test_subprocess_groups_are_killed_on_timeout_error_and_success(workspace: Workspace) -> None:
    for index, ending in enumerate(("time.sleep(5)", "raise SystemExit(1)", "pass")):
        descendant = f"import time; from pathlib import Path; time.sleep(0.5); Path('orphan-marker-{index}').touch()"
        program = (
            "import subprocess,sys,time; from pathlib import Path; "
            f"subprocess.Popen([sys.executable,'-B','-c',{descendant!r}], stdout=subprocess.DEVNULL); "
            f"Path('descendant-created-{index}').touch(); "
            + ending
        )
        if index == 0:
            with pytest.raises(DeadlineExceeded):
                workspace.runtime().run([sys.executable, "-B", "-c", program], timeout=0.15)
        elif index == 1:
            with pytest.raises(RuntimeError):
                workspace.runtime().run([sys.executable, "-B", "-c", program])
        else:
            assert workspace.runtime().run([sys.executable, "-B", "-c", program]) == b""
        assert (workspace.root / f"descendant-created-{index}").exists()
    time.sleep(0.6)
    assert not list(workspace.root.glob("orphan-marker-*"))


def test_command_deadline_cleans_up_the_process_group(workspace: Workspace) -> None:
    program = """
import sys
from guardrails.hooks import session_resume
from guardrails.entry import main
def delayed(event, config, runtime):
    command = "import time; from pathlib import Path; time.sleep(0.5); Path('orphan-marker').touch()"
    runtime.run([sys.executable, '-B', '-c', command])
session_resume.handle = delayed
main()
"""
    assert workspace.call("session_resume", program=program, budget=0.16) is None
    time.sleep(0.6)
    assert not (workspace.root / "orphan-marker").exists()


def test_full_output_pipe_is_a_silent_pass(workspace: Workspace) -> None:
    reader, writer = os.pipe()
    os.set_blocking(writer, False)
    try:
        while True:
            try:
                os.write(writer, b"x" * 4096)
            except BlockingIOError:
                break
        process = subprocess.run(
            workspace.command("session_resume", workspace.config("session_resume")),
            input=json.dumps(workspace.event("session_resume")).encode(), stdout=writer,
            stderr=subprocess.PIPE, env=workspace.environment(), cwd=ROOT, timeout=2,
        )
        assert process.returncode == 0 and process.stderr == b""
        os.close(writer)
        writer = -1
        data = bytearray()
        while chunk := os.read(reader, 8192):
            data.extend(chunk)
        assert data and set(data) == {ord("x")}
    finally:
        os.close(reader)
        if writer >= 0:
            os.close(writer)
