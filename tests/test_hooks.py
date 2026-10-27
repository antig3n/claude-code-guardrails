import json
from typing import Any

import pytest

from guardrails.hooks.agent_guard import evaluate
from guardrails.hooks.session_resume import handle as resume
from guardrails.tasks import Task, has_evidence, load_tasks

from conftest import Workspace


def context(response: dict[str, Any] | None, event: str) -> str:
    assert response is not None
    assert set(response) == {"hookSpecificOutput"}
    output = response["hookSpecificOutput"]
    assert set(output) == {"hookEventName", "additionalContext"}
    assert output["hookEventName"] == event
    return output["additionalContext"]


def denial(response: dict[str, Any] | None) -> str:
    assert response is not None
    assert set(response) == {"hookSpecificOutput"}
    output = response["hookSpecificOutput"]
    assert set(output) == {"hookEventName", "permissionDecision", "permissionDecisionReason"}
    assert output["hookEventName"] == "PreToolUse"
    assert output["permissionDecision"] == "deny"
    return output["permissionDecisionReason"]


def test_resume_protocol(workspace: Workspace) -> None:
    text = context(workspace.call("session_resume"), "SessionStart")
    assert text == (workspace.root / "state.txt").read_text()


@pytest.mark.parametrize("limit", [0, 1, 7, 1200])
def test_resume_character_bound(workspace: Workspace, limit: int) -> None:
    workspace.write("state.txt", "  Ω" * 600)
    result = resume({}, {"state_file": "state.txt", "max_chars": limit}, workspace.runtime())
    if limit == 0:
        assert result is None
    else:
        assert len(context(result, "SessionStart")) == limit


def test_empty_resume_passes(workspace: Workspace) -> None:
    workspace.write("state.txt", " \n\t")
    assert workspace.call("session_resume") is None


def test_prompt_context_protocol_and_bound(workspace: Workspace) -> None:
    workspace.tasks(
        {"id": "DEMO-1", "title": "Active", "status": "in_progress"},
        {"id": "DEMO-2", "title": "Queued", "status": "todo"},
        {"id": "DEMO-3", "title": "Closed", "status": "done"},
    )
    result = context(workspace.call("prompt_context"), "UserPromptSubmit")
    assert result == "Usage remaining: 58%.\nIn progress: DEMO-1 - Active"
    config = workspace.config("prompt_context", max_chars=8)
    assert len(context(workspace.call("prompt_context", config=config), "UserPromptSubmit")) == 8
    config = workspace.config("prompt_context", max_tasks=0)
    assert context(workspace.call("prompt_context", config=config), "UserPromptSubmit") == "Usage remaining: 58%."


def test_prompt_optional_sources_and_zero_limit(workspace: Workspace) -> None:
    for options in ({"usage_file": None, "tasks_file": None}, {"max_chars": 0}):
        assert workspace.call("prompt_context", config=workspace.config("prompt_context", **options)) is None


def test_agent_allow_and_deny_protocol(workspace: Workspace) -> None:
    assert workspace.call("agent_guard") is None
    event = workspace.event("agent_guard")
    event["tool_input"]["prompt"] = "Implement the example."
    assert "separate line" in denial(workspace.call("agent_guard", event=event))


@pytest.mark.parametrize("brief,allowed", [
    ("Why not the cheaper executor: adequate design reason", True),
    ("Work\n  Why not the cheaper executor: adequate design reason  ", True),
    ("Work Why not the cheaper executor: adequate design reason", False),
    ("Why not the cheaper executor:\nadequate design reason", False),
    ("Why not the cheaper executor: ...", False),
    ("Why not the cheaper executor:            ", False),
    ("why not the cheaper executor: adequate design reason", False),
    ("", False),
])
def test_justification_is_a_separate_nonempty_line(brief: str, allowed: bool) -> None:
    tool = {"prompt": brief, "model": "strong-model", "subagent_type": "builder"}
    assert (evaluate(tool, {}, 42) is None) == allowed


def test_justification_prefix_and_length_are_configurable() -> None:
    config = {"justification_prefix": "Reason:", "min_justification_chars": 3}
    tool = {"prompt": "Reason: abc", "model": "strong-model"}
    assert evaluate(tool, config, 42) is None
    tool["prompt"] = "Reason: ab"
    assert evaluate(tool, config, 42) is not None


def test_exceptions_only_skip_justification(workspace: Workspace) -> None:
    event = workspace.event("agent_guard", tool_input={
        "subagent_type": "reader", "model": "economy-model", "prompt": "Read the example.",
    })
    assert workspace.call("agent_guard", event=event) is None
    event["tool_input"]["model"] = "retired-model"
    assert "forbidden" in denial(workspace.call("agent_guard", event=event))
    event["tool_input"]["model"] = ""
    assert "explicit model" in denial(workspace.call("agent_guard", event=event))
    event["tool_input"]["model"] = "economy-model"
    workspace.write("usage.json", {"used_percent": 99})
    assert "Usage" in denial(workspace.call("agent_guard", event=event))


@pytest.mark.parametrize("model,role,denied", [
    ("economy-model", "builder", True), ("ECONOMY-MODEL", "builder", True),
    ("economy-model", "reviewer", False), ("strong-model", "builder", False),
    ("retired-model", "reviewer", True), (" ", "builder", True),
])
def test_model_rules(model: str, role: str, denied: bool) -> None:
    tool = {"prompt": "Reason: enough", "model": model, "subagent_type": role}
    config = {
        "justification_prefix": "Reason:", "min_justification_chars": 3,
        "forbidden_models": {"builder": ["economy-model"], "*": ["retired-model"]},
    }
    assert (evaluate(tool, config, 42) is not None) == denied


@pytest.mark.parametrize("used,denied", [(0, False), (89.999, False), (90, True), (100, True)])
def test_agent_usage_boundary(workspace: Workspace, used: float, denied: bool) -> None:
    workspace.write("usage.json", {"used_percent": used})
    response = workspace.call("agent_guard")
    assert (response is not None) == denied
    if denied:
        assert "90%" in denial(response)


def test_agent_threshold_and_disabled_usage(workspace: Workspace) -> None:
    config = workspace.config("agent_guard", usage_threshold=40)
    assert "40%" in denial(workspace.call("agent_guard", config=config))
    config = workspace.config("agent_guard", usage_file=None)
    (workspace.root / "usage.json").unlink()
    assert workspace.call("agent_guard", config=config) is None


def test_non_agent_tool_passes_without_reading_usage(workspace: Workspace) -> None:
    (workspace.root / "usage.json").unlink()
    assert workspace.call("agent_guard", event=workspace.event("agent_guard", tool_name="Read", tool_input=42)) is None


@pytest.mark.parametrize("evidence,valid", [
    (None, False), ("claimed complete", False), ({}, False),
    ({"file": "proof.txt"}, True), ({"file": "absent.txt"}, False),
    ({"file": "empty.txt"}, False), ({"file": "../escape.txt"}, False),
    ({"command": ["python3", "-m", "unittest"], "exit_code": 0}, True),
    ({"command": ["python3"], "exit_code": 1}, False),
    ({"command": ["python3"], "exit_code": False}, False),
    ({"command": ["python3"]}, False),
    ({"command": [], "exit_code": 0}, False),
    ({"command": "python3 -m unittest", "exit_code": 0}, False),
    ({"command": [""], "exit_code": 0}, False),
])
def test_evidence_schema(workspace: Workspace, evidence: Any, valid: bool) -> None:
    workspace.write("proof.txt", "Fictional test output.")
    workspace.write("empty.txt", " \n")
    task = Task("DEMO-1", "Example", "done", evidence)
    assert has_evidence(workspace.runtime(), task) == valid


def test_stop_protocol_and_maximum_two_blocks(workspace: Workspace) -> None:
    workspace.tasks({"id": "DEMO-1", "title": "Closed", "status": "done"})
    for _ in range(2):
        response = workspace.call("stop_guard")
        assert response is not None and set(response) == {"decision", "reason"}
        assert response["decision"] == "block"
        assert "DEMO-1" in response["reason"]
    for _ in range(2):
        assert workspace.call("stop_guard") is None


def test_stop_counter_resets_on_prompt_and_isolates_sessions(workspace: Workspace) -> None:
    workspace.tasks({"id": "DEMO-1", "title": "Closed", "status": "done"})
    assert workspace.call("stop_guard") is not None
    assert workspace.call("stop_guard") is not None
    assert workspace.call("stop_guard") is None
    assert workspace.call("stop_guard", event=workspace.event("stop_guard", session_id="another-fictional-session")) is not None
    workspace.call("prompt_context", event=workspace.event("prompt_context", prompt="Private prompt content."))
    assert workspace.call("stop_guard") is not None
    states = list((workspace.root / ".guardrails/sessions").glob("*.json"))
    assert len(states) == 2
    assert all(set(json.loads(path.read_text())) == {"turn", "blocks"} for path in states)
    assert all("Private" not in path.read_text() for path in states)


def test_active_stop_never_reads_or_mutates_task_state(workspace: Workspace) -> None:
    workspace.write("tasks.json", "broken")
    assert workspace.call("stop_guard", event=workspace.event("stop_guard", stop_hook_active=True)) is None
    assert not (workspace.root / ".guardrails/sessions").exists()
    workspace.tasks({"id": "DEMO-1", "title": "Closed", "status": "done"})
    assert workspace.call("stop_guard") is not None


def test_stop_accepts_file_or_recorded_command_without_execution(workspace: Workspace) -> None:
    workspace.write("proof.txt", "Fictional evidence.")
    workspace.tasks(
        {"id": "DEMO-1", "title": "File", "status": "done", "evidence": {"file": "proof.txt"}},
        {"id": "DEMO-2", "title": "Command", "status": "done", "evidence": {
            "command": ["imaginary-test-command", "--write", "must-not-exist"], "exit_code": 0,
        }},
        {"id": "DEMO-3", "title": "Unfinished", "status": "todo"},
    )
    assert workspace.call("stop_guard") is None
    assert not (workspace.root / "must-not-exist").exists()


def test_stop_lower_limit_and_custom_state_directory(workspace: Workspace) -> None:
    workspace.tasks({"id": "DEMO-1", "title": "Closed", "status": "done"})
    config = workspace.config("stop_guard", max_blocks=1, state_dir="custom-state")
    assert workspace.call("stop_guard", config=config) is not None
    assert workspace.call("stop_guard", config=config) is None
    workspace.call("prompt_context", config=workspace.config("prompt_context", state_dir="custom-state"))
    assert workspace.call("stop_guard", config=config) is not None


def test_stop_does_not_consume_a_block_when_evidence_exists(workspace: Workspace) -> None:
    workspace.tasks({"id": "DEMO-1", "title": "Closed", "status": "done", "evidence": {
        "command": ["imaginary-test-command"], "exit_code": 0,
    }})
    assert workspace.call("stop_guard") is None
    workspace.tasks({"id": "DEMO-1", "title": "Closed", "status": "done"})
    assert workspace.call("stop_guard") is not None
    assert workspace.call("stop_guard") is not None
    assert workspace.call("stop_guard") is None


@pytest.mark.parametrize("value", [
    [], {"tasks": {}}, {"tasks": [42]}, {"tasks": [{"id": "x", "status": "done"}]},
    {"tasks": [{"id": "x", "title": "Example", "status": ""}]},
    {"tasks": [{"id": "x", "title": "A", "status": "done"}] * 2},
])
def test_corrupt_task_data_passes_without_a_stop_block(workspace: Workspace, value: Any) -> None:
    workspace.write("tasks.json", value)
    assert workspace.call("stop_guard") is None
    with pytest.raises(ValueError):
        load_tasks(workspace.runtime(), "tasks.json")
