import copy
import json

import pytest

from guardrails.routing import DEFAULT_RULES, select_executor

from conftest import ROOT


@pytest.mark.parametrize("task,quotas,expected", [
    ("summarize", {}, "local_model"),
    ("implement", {}, "budget_executor"),
    ("design", {}, "strong_model"),
    ("summarize", {"local_model": 90}, "budget_executor"),
    ("summarize", {"local_model": 100, "budget_executor": 90}, "strong_model"),
    ("design", {"strong_model": 90}, None),
    ("unknown", {}, None),
    ("summarize", {"local_model": 90, "budget_executor": 90, "strong_model": 90}, None),
    ("summarize", {"local_model": 89.999}, "local_model"),
])
def test_default_routes(task: str, quotas: dict[str, float], expected: str | None) -> None:
    assert select_executor(task, quotas) == expected


def test_declarative_rules_per_executor_thresholds_and_no_mutation() -> None:
    rules = {"transform": ["local_model", "budget_executor"], "*": ["strong_model"]}
    quotas = {"local_model": 85, "budget_executor": 80}
    thresholds = {"local_model": 85, "budget_executor": 81}
    before = copy.deepcopy((rules, quotas, thresholds))
    assert select_executor("transform", quotas, rules, thresholds=thresholds) == "budget_executor"
    assert select_executor("unknown", quotas, rules) == "strong_model"
    assert (rules, quotas, thresholds) == before


@pytest.mark.parametrize("invalid", [-1, 101, float("nan"), float("inf"), True, "90"])
def test_invalid_quotas_and_thresholds(invalid: object) -> None:
    with pytest.raises(ValueError):
        select_executor("summarize", {"local_model": invalid})
    with pytest.raises(ValueError):
        select_executor("summarize", {}, threshold=invalid)
    with pytest.raises(ValueError):
        select_executor("summarize", {}, thresholds={"local_model": invalid})


def test_zero_and_full_threshold_boundaries() -> None:
    assert select_executor("design", {"strong_model": 0}, threshold=0) is None
    assert select_executor("design", {"strong_model": 99.999}, threshold=100) == "strong_model"
    assert select_executor("design", {"strong_model": 100}, threshold=100) is None


@pytest.mark.parametrize("rules", [{"x": "local_model"}, {"x": [""]}, {"x": [42]}])
def test_invalid_rules(rules: dict) -> None:
    with pytest.raises(ValueError):
        select_executor("x", {}, rules)


def test_example_routing_configuration() -> None:
    config = json.loads((ROOT / "examples/routing.json").read_text())
    assert {key: list(value) for key, value in DEFAULT_RULES.items()} == config["rules"]
    assert select_executor("summarize", {"local_model": 95, "budget_executor": 85}, **config) == "strong_model"
