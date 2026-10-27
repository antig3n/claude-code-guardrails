"""Small schema checks shared by configuration and local usage data."""

import math
from collections.abc import Mapping
from typing import Any


def integer(value: Any, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("Integer outside the supported range")
    return value


def percentage(value: Any) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError("Expected a finite percentage")
    return float(value)


def strings(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(x, str) and x.strip() for x in value):
        raise ValueError("Expected a list of nonempty strings")
    return value


def used_percentage(value: Any) -> float:
    if not isinstance(value, Mapping):
        raise ValueError("Expected a usage object")
    return percentage(value["used_percent"])
