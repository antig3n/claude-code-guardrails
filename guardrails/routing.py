"""Pure declarative routing; selection never launches an executor."""

from collections.abc import Mapping, Sequence
from types import MappingProxyType

from .validation import percentage

DEFAULT_RULES: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "summarize": ("local_model", "budget_executor", "strong_model"),
    "implement": ("budget_executor", "strong_model"),
    "design": ("strong_model",),
})


def select_executor(
    task_type: str,
    quotas: Mapping[str, float],
    rules: Mapping[str, Sequence[str]] = DEFAULT_RULES,
    *,
    threshold: float = 90,
    thresholds: Mapping[str, float] | None = None,
) -> str | None:
    """Choose the first executor below its threshold; missing quotas mean zero use."""
    default = percentage(threshold)
    candidates = rules.get(task_type, rules.get("*", ()))
    if isinstance(candidates, str):
        raise ValueError("Expected an executor sequence")
    for executor in candidates:
        if not isinstance(executor, str) or not executor.strip():
            raise ValueError("Invalid executor name")
        limit = percentage((thresholds or {}).get(executor, default))
        if percentage(quotas.get(executor, 0)) < limit:
            return executor
    return None
