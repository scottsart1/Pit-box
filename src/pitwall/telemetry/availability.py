"""Availability and provenance propagation for telemetry values."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, TypeVar


class Availability(StrEnum):
    """User-visible truth state for a value."""

    OBSERVED = "observed"
    DERIVED = "derived"
    ESTIMATED = "estimated"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class Provenance(StrEnum):
    """How a value entered the deterministic system."""

    PACKET = "packet"
    DERIVATION = "derivation"
    MODEL = "model"
    USER = "user"
    REPLAY = "replay"


T = TypeVar("T")


def _bounded(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclass(frozen=True, slots=True)
class FieldValue(Generic[T]):
    """A value coupled to the metadata needed to describe it honestly."""

    value: T | None
    availability: Availability
    provenance: tuple[Provenance, ...] = ()
    confidence: float = 1.0
    source_ids: tuple[str, ...] = ()
    required_inputs: tuple[str, ...] = ()
    reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "confidence", _bounded(self.confidence))
        if self.availability is Availability.UNAVAILABLE and self.value is not None:
            raise ValueError("unavailable values must use value=None")
        if self.value is None and self.availability not in {
            Availability.UNAVAILABLE,
            Availability.STALE,
        }:
            raise ValueError("available values must carry a value")

    @classmethod
    def observed(
        cls,
        value: T,
        *,
        source_id: str | None = None,
        provenance: Provenance = Provenance.PACKET,
        confidence: float = 1.0,
    ) -> FieldValue[T]:
        return cls(
            value=value,
            availability=Availability.OBSERVED,
            provenance=(provenance,),
            confidence=confidence,
            source_ids=(source_id,) if source_id else (),
        )

    @classmethod
    def unavailable(
        cls,
        reason: str,
        *,
        required_inputs: Iterable[str] = (),
    ) -> FieldValue[T]:
        return cls(
            value=None,
            availability=Availability.UNAVAILABLE,
            confidence=0.0,
            required_inputs=tuple(required_inputs),
            reason=reason,
        )


def propagate_availability(
    inputs: Iterable[FieldValue[object] | Availability],
    *,
    estimated: bool = False,
    output_is_observation: bool = False,
) -> Availability:
    """Return the strictest truthful state for an output.

    Missing dominates stale, stale dominates estimated, and any calculation over
    otherwise observed inputs is labelled derived unless explicitly declared to
    be a pass-through observation.
    """

    states = [
        item.availability if isinstance(item, FieldValue) else Availability(item)
        for item in inputs
    ]
    if not states or Availability.UNAVAILABLE in states:
        return Availability.UNAVAILABLE
    if Availability.STALE in states:
        return Availability.STALE
    if estimated or Availability.ESTIMATED in states:
        return Availability.ESTIMATED
    if output_is_observation and all(state is Availability.OBSERVED for state in states):
        return Availability.OBSERVED
    return Availability.DERIVED


def derive_value(
    value: T | None,
    inputs: Iterable[FieldValue[object]],
    *,
    algorithm: str,
    required_inputs: Iterable[str] = (),
    estimated: bool = False,
    confidence: float | None = None,
) -> FieldValue[T]:
    """Create a derived value while retaining source IDs and provenance."""

    source = tuple(inputs)
    status = propagate_availability(source, estimated=estimated)
    requirements = tuple(required_inputs)
    if status is Availability.UNAVAILABLE or value is None:
        missing = [
            item.reason or "required input unavailable"
            for item in source
            if item.availability is Availability.UNAVAILABLE
        ]
        return FieldValue.unavailable(
            "; ".join(missing) or "derived value was not produced",
            required_inputs=requirements,
        )

    seen_provenance: list[Provenance] = []
    seen_sources: list[str] = []
    for item in source:
        for provenance in item.provenance:
            if provenance not in seen_provenance:
                seen_provenance.append(provenance)
        for source_id in item.source_ids:
            if source_id not in seen_sources:
                seen_sources.append(source_id)
    output_provenance = Provenance.MODEL if estimated else Provenance.DERIVATION
    if output_provenance not in seen_provenance:
        seen_provenance.append(output_provenance)
    input_confidence = min((item.confidence for item in source), default=1.0)
    return FieldValue(
        value=value,
        availability=status,
        provenance=tuple(seen_provenance),
        confidence=input_confidence if confidence is None else min(input_confidence, confidence),
        source_ids=tuple(seen_sources) + (algorithm,),
        required_inputs=requirements,
    )
