"""Canonical metric declarations and required-input availability checks."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .availability import Availability, FieldValue, Provenance, propagate_availability


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    key: str
    unit: str
    required_inputs: tuple[str, ...]
    window: str
    algorithm_version: str
    minimum_coverage: float = 0.95
    no_bridge_gap_m: float = 5.0
    provenance: Provenance = Provenance.DERIVATION
    description: str = ""

    def __post_init__(self) -> None:
        if not self.key or not self.unit or not self.algorithm_version:
            raise ValueError("metric key, unit, and algorithm version are required")
        if not 0.0 <= self.minimum_coverage <= 1.0:
            raise ValueError("minimum coverage must be between zero and one")
        if self.no_bridge_gap_m <= 0:
            raise ValueError("no_bridge_gap_m must be positive")


@dataclass(frozen=True, slots=True)
class MetricAvailability:
    metric_key: str
    availability: Availability
    confidence: float
    missing_inputs: tuple[str, ...] = ()
    low_coverage_inputs: tuple[str, ...] = ()
    reason: str | None = None


class MetricRegistry:
    """Immutable-in-practice catalog used by analysis, API, and exports."""

    def __init__(self, definitions: Iterable[MetricDefinition] = ()) -> None:
        self._definitions: dict[str, MetricDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: MetricDefinition) -> None:
        if definition.key in self._definitions:
            raise ValueError(f"metric already registered: {definition.key}")
        self._definitions[definition.key] = definition

    def get(self, key: str) -> MetricDefinition:
        try:
            return self._definitions[key]
        except KeyError as exc:
            raise KeyError(f"unknown metric: {key}") from exc

    def definitions(self) -> Mapping[str, MetricDefinition]:
        return MappingProxyType(dict(self._definitions))

    def availability(
        self,
        key: str,
        inputs: Mapping[str, FieldValue[object] | Availability],
        *,
        coverage: Mapping[str, float] | None = None,
        estimated: bool = False,
    ) -> MetricAvailability:
        definition = self.get(key)
        missing = tuple(name for name in definition.required_inputs if name not in inputs)
        unavailable = tuple(
            name
            for name in definition.required_inputs
            if name in inputs
            and (
                inputs[name].availability
                if isinstance(inputs[name], FieldValue)
                else Availability(inputs[name])
            )
            is Availability.UNAVAILABLE
        )
        missing_all = tuple(dict.fromkeys((*missing, *unavailable)))
        if missing_all:
            return MetricAvailability(
                key,
                Availability.UNAVAILABLE,
                0.0,
                missing_inputs=missing_all,
                reason=f"missing required inputs: {', '.join(missing_all)}",
            )
        low_coverage = tuple(
            name
            for name in definition.required_inputs
            if float((coverage or {}).get(name, 1.0)) < definition.minimum_coverage
        )
        if low_coverage:
            confidence = min(float((coverage or {}).get(name, 0.0)) for name in low_coverage)
            return MetricAvailability(
                key,
                Availability.UNAVAILABLE,
                max(0.0, min(1.0, confidence)),
                low_coverage_inputs=low_coverage,
                reason=f"coverage below {definition.minimum_coverage:.0%}: {', '.join(low_coverage)}",
            )
        selected = [inputs[name] for name in definition.required_inputs]
        status = propagate_availability(selected, estimated=estimated)
        confidence = min(
            (
                item.confidence if isinstance(item, FieldValue) else 1.0
                for item in selected
            ),
            default=0.0,
        )
        coverage_confidence = min(
            (float((coverage or {}).get(name, 1.0)) for name in definition.required_inputs),
            default=1.0,
        )
        return MetricAvailability(
            key,
            status,
            max(0.0, min(1.0, min(confidence, coverage_confidence))),
        )


_DEFINITIONS = (
    MetricDefinition(
        "lap_time",
        "s",
        ("time_s", "distance_m"),
        "lap",
        "lap_time_v1",
        no_bridge_gap_m=5.0,
    ),
    MetricDefinition(
        "cumulative_delta",
        "s",
        ("candidate_time_s", "reference_time_s", "distance_m"),
        "lap",
        "time_delta_v1",
        no_bridge_gap_m=5.0,
    ),
    MetricDefinition(
        "segment_delta",
        "s",
        ("cumulative_delta", "segment_bounds"),
        "segment",
        "segment_delta_v1",
        minimum_coverage=0.90,
    ),
    MetricDefinition(
        "brake_onset_distance",
        "m",
        ("brake", "distance_m"),
        "brake_window",
        "brake_event_v2",
        no_bridge_gap_m=3.0,
    ),
    MetricDefinition(
        "brake_onset_speed",
        "m/s",
        ("brake_onset_distance", "speed", "distance_m"),
        "brake_window",
        "brake_onset_speed_v1",
        no_bridge_gap_m=4.0,
    ),
    MetricDefinition(
        "minimum_speed",
        "m/s",
        ("speed", "distance_m"),
        "apex_window",
        "minimum_speed_v1",
        no_bridge_gap_m=5.0,
    ),
    MetricDefinition(
        "minimum_speed_distance",
        "m",
        ("speed", "distance_m"),
        "apex_window",
        "minimum_speed_event_v1",
        no_bridge_gap_m=5.0,
    ),
    MetricDefinition(
        "throttle_pickup_distance",
        "m",
        ("throttle", "distance_m"),
        "exit_window",
        "throttle_event_v2",
        no_bridge_gap_m=3.0,
    ),
    MetricDefinition(
        "full_throttle_distance",
        "m",
        ("throttle", "distance_m"),
        "exit_window",
        "full_throttle_event_v2",
        no_bridge_gap_m=3.0,
    ),
    MetricDefinition(
        "line_offset",
        "m",
        ("world_position", "track_model", "distance_m"),
        "phase",
        "line_offset_v1",
        minimum_coverage=0.90,
        no_bridge_gap_m=8.0,
    ),
    MetricDefinition(
        "steering_corrections",
        "count",
        ("steering", "distance_m"),
        "segment",
        "steering_corrections_v1",
        minimum_coverage=0.90,
        no_bridge_gap_m=3.0,
    ),
    MetricDefinition(
        "gear_at_apex",
        "gear",
        ("gear", "apex_event", "distance_m"),
        "apex_window",
        "gear_at_apex_v1",
        minimum_coverage=0.90,
        no_bridge_gap_m=4.0,
    ),
    MetricDefinition(
        "segment_consistency_mad",
        "s",
        ("segment_times",),
        "multi_lap",
        "segment_mad_v1",
        minimum_coverage=0.60,
    ),
)


DEFAULT_METRICS = MetricRegistry(_DEFINITIONS)
