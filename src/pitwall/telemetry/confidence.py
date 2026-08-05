"""Inspectable confidence composition shared by comparison and coaching."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from math import prod


def _bounded(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclass(frozen=True, slots=True)
class ConfidenceComponents:
    data_coverage: float = 1.0
    detector_stability: float = 1.0
    model_quality: float = 1.0
    causal_support: float = 1.0
    compatibility_weight: float = 1.0
    sample_strength: float = 1.0

    def __post_init__(self) -> None:
        for field_name in self.__dataclass_fields__:
            object.__setattr__(self, field_name, _bounded(getattr(self, field_name)))

    @property
    def score(self) -> float:
        """Geometric mean: one weak component cannot be hidden by strong peers."""

        values = tuple(getattr(self, name) for name in self.__dataclass_fields__)
        if not values or any(value <= 0.0 for value in values):
            return 0.0
        return _bounded(prod(values) ** (1.0 / len(values)))

    def as_dict(self) -> dict[str, float]:
        return {
            name: float(getattr(self, name))
            for name in self.__dataclass_fields__
        }


def combine_confidence(values: Iterable[float], *, method: str = "geometric") -> float:
    bounded = tuple(_bounded(value) for value in values)
    if not bounded:
        return 0.0
    if method == "minimum":
        return min(bounded)
    if method == "product":
        return _bounded(prod(bounded))
    if method != "geometric":
        raise ValueError("method must be geometric, minimum, or product")
    if any(value <= 0 for value in bounded):
        return 0.0
    return _bounded(prod(bounded) ** (1.0 / len(bounded)))
