"""Require an explicit brief justification, permitted model and available usage."""

from collections.abc import Mapping
from typing import Any

from ..runtime import Runtime
from ..validation import integer, percentage, strings, used_percentage


def evaluate(tool: Mapping[str, Any], config: Mapping[str, Any], used: float | None) -> str | None:
    role = tool.get("subagent_type", "general-purpose")
    model = tool.get("model", "")
    brief = tool.get("prompt", "")
    if not all(isinstance(value, str) for value in (role, model, brief)) or not role.strip():
        raise ValueError("Invalid agent input")
    prefix = config.get("justification_prefix", "Why not the cheaper executor:")
    if not isinstance(prefix, str) or not prefix.strip() or "\n" in prefix:
        raise ValueError("Invalid justification prefix")
    minimum = integer(config.get("min_justification_chars", 12), 1, 500)
    exceptions = strings(config.get("exceptions", []))
    if role not in exceptions and not any(
        line.strip().startswith(prefix) and len(line.strip()[len(prefix):].strip()) >= minimum
        for line in brief.splitlines()
    ):
        return f"Add a separate line: {prefix} <reason of at least {minimum} characters>."
    if not model.strip():
        return "Choose an explicit model for this agent."
    rules = config.get("forbidden_models", {})
    if not isinstance(rules, dict):
        raise ValueError("Invalid model rules")
    forbidden = strings(rules.get(role, [])) + strings(rules.get("*", []))
    if model.strip().casefold() in {item.casefold() for item in forbidden}:
        return "The requested model is forbidden for this agent type."
    threshold = percentage(config.get("usage_threshold", 90))
    if used is not None and percentage(used) >= threshold:
        return f"Usage is at or above {threshold:g}%; wait for quota or use another executor."
    return None


def handle(event: Mapping[str, Any], config: Mapping[str, Any], runtime: Runtime) -> dict[str, Any] | None:
    if event.get("tool_name") != "Agent":
        return None
    tool = event.get("tool_input")
    if not isinstance(tool, dict):
        raise ValueError("Expected an agent input object")
    used = None
    if config.get("usage_file") is not None:
        used = used_percentage(runtime.files.read_json(config["usage_file"]))
    reason = evaluate(tool, config, used)
    if reason is None:
        return None
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason,
    }}
