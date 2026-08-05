"""Reference compatibility and reproducible pair-comparison results."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import StrEnum
from typing import Any

import numpy as np
from numpy.typing import ArrayLike

from .alignment import TimeDeltaResult, compute_time_delta
from .segments import Segment


class CompatibilityClass(StrEnum):
    STRICT = "strict"
    COMPARABLE_WITH_CAVEATS = "comparable_with_caveats"
    CONTEXT_ONLY = "context_only"
    INCOMPATIBLE = "incompatible"


class IssueSeverity(StrEnum):
    CAVEAT = "caveat"
    CONTEXT = "context"
    INCOMPATIBLE = "incompatible"


@dataclass(frozen=True, slots=True)
class CompatibilityIssue:
    code: str
    message: str
    severity: IssueSeverity


@dataclass(frozen=True, slots=True)
class LapContext:
    game_version: str | None = None
    physics_version: str | None = None
    packet_format: int | None = None
    track_id: int | None = None
    layout_signature: str | None = None
    track_length_m: float | None = None
    car_class: str | None = None
    team_id: int | None = None
    equal_performance: bool | None = None
    session_type: str | None = None
    weather_class: str | None = None
    tyre_compound: str | None = None
    tyre_age_laps: int | None = None
    fuel_kg: float | None = None
    damaged: bool = False
    valid_lap: bool = True
    pit_context: bool = False
    flag_context: bool = False
    assists_signature: str | None = None
    track_model_version: int | None = None
    coverage_ratio: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "coverage_ratio", max(0.0, min(1.0, self.coverage_ratio)))


@dataclass(frozen=True, slots=True)
class CompatibilityReport:
    classification: CompatibilityClass
    issues: tuple[CompatibilityIssue, ...]
    compatibility_weight: float

    @property
    def allows_coaching(self) -> bool:
        return self.classification in {
            CompatibilityClass.STRICT,
            CompatibilityClass.COMPARABLE_WITH_CAVEATS,
        }

    @property
    def caveats(self) -> tuple[str, ...]:
        return tuple(issue.message for issue in self.issues)


def _different(left: object | None, right: object | None) -> bool:
    return left is not None and right is not None and left != right


def classify_compatibility(
    candidate: LapContext,
    reference: LapContext,
    *,
    length_tolerance_m: float = 15.0,
    strict_min_coverage: float = 0.95,
    context_min_coverage: float = 0.75,
    tyre_age_tolerance_laps: int = 5,
    fuel_tolerance_kg: float = 8.0,
) -> CompatibilityReport:
    issues: list[CompatibilityIssue] = []

    def add(code: str, message: str, severity: IssueSeverity) -> None:
        issues.append(CompatibilityIssue(code, message, severity))

    if _different(candidate.track_id, reference.track_id):
        add("track_mismatch", "track IDs differ", IssueSeverity.INCOMPATIBLE)
    if _different(candidate.layout_signature, reference.layout_signature):
        add("layout_mismatch", "track layout signatures differ", IssueSeverity.INCOMPATIBLE)
    if (
        candidate.track_length_m is not None
        and reference.track_length_m is not None
        and abs(candidate.track_length_m - reference.track_length_m) > length_tolerance_m
    ):
        add(
            "track_length_mismatch",
            "track lengths differ materially",
            IssueSeverity.INCOMPATIBLE,
        )
    minimum_coverage = min(candidate.coverage_ratio, reference.coverage_ratio)
    if minimum_coverage < context_min_coverage:
        add("insufficient_coverage", "distance coverage is insufficient", IssueSeverity.INCOMPATIBLE)
    elif minimum_coverage < strict_min_coverage:
        add("partial_coverage", "distance coverage is incomplete", IssueSeverity.CAVEAT)

    if not candidate.valid_lap or not reference.valid_lap:
        add("invalid_lap", "one or both laps are invalid", IssueSeverity.CONTEXT)
    if candidate.pit_context or reference.pit_context:
        add("pit_context", "one or both laps include pit context", IssueSeverity.CONTEXT)
    if candidate.flag_context or reference.flag_context:
        add("flag_context", "one or both laps include flag or neutralisation context", IssueSeverity.CONTEXT)
    if candidate.damaged or reference.damaged:
        add("damage_context", "one or both cars have damage", IssueSeverity.CONTEXT)
    if _different(candidate.weather_class, reference.weather_class):
        add("weather_mismatch", "weather classes differ", IssueSeverity.CONTEXT)
    if _different(candidate.car_class, reference.car_class):
        add("car_class_mismatch", "car performance classes differ", IssueSeverity.CONTEXT)
    if _different(candidate.equal_performance, reference.equal_performance):
        add("performance_mode_mismatch", "equal-performance settings differ", IssueSeverity.CONTEXT)

    for code, label, left, right in (
        ("game_version_mismatch", "game versions differ", candidate.game_version, reference.game_version),
        ("physics_version_mismatch", "physics versions differ", candidate.physics_version, reference.physics_version),
        ("packet_format_mismatch", "packet formats differ", candidate.packet_format, reference.packet_format),
        ("session_type_mismatch", "session types differ", candidate.session_type, reference.session_type),
        ("tyre_compound_mismatch", "tyre compounds differ", candidate.tyre_compound, reference.tyre_compound),
        ("assists_mismatch", "assist/control signatures differ", candidate.assists_signature, reference.assists_signature),
        ("track_model_mismatch", "track-model versions differ", candidate.track_model_version, reference.track_model_version),
    ):
        if _different(left, right):
            add(code, label, IssueSeverity.CAVEAT)
    if (
        candidate.tyre_age_laps is not None
        and reference.tyre_age_laps is not None
        and abs(candidate.tyre_age_laps - reference.tyre_age_laps)
        > tyre_age_tolerance_laps
    ):
        add("tyre_age_mismatch", "tyre ages differ materially", IssueSeverity.CAVEAT)
    if (
        candidate.fuel_kg is not None
        and reference.fuel_kg is not None
        and abs(candidate.fuel_kg - reference.fuel_kg) > fuel_tolerance_kg
    ):
        add("fuel_mismatch", "fuel loads differ materially", IssueSeverity.CAVEAT)

    severities = {issue.severity for issue in issues}
    if IssueSeverity.INCOMPATIBLE in severities:
        classification = CompatibilityClass.INCOMPATIBLE
        weight = 0.0
    elif IssueSeverity.CONTEXT in severities:
        classification = CompatibilityClass.CONTEXT_ONLY
        weight = 0.35
    elif IssueSeverity.CAVEAT in severities:
        classification = CompatibilityClass.COMPARABLE_WITH_CAVEATS
        weight = max(0.5, 0.85 - 0.05 * max(0, len(issues) - 1))
    else:
        classification = CompatibilityClass.STRICT
        weight = 1.0
    return CompatibilityReport(classification, tuple(issues), weight)


def _canonical(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return str(value)
    if is_dataclass(value):
        return _canonical(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_canonical(item) for item in value]
    if isinstance(value, float):
        if not np.isfinite(value):
            return str(value)
        return round(value, 12)
    return value


@dataclass(frozen=True, slots=True)
class ComparisonInput:
    candidate_lap_id: str
    reference_kind: str
    reference_key: str
    candidate_checksum: str
    reference_checksum: str
    track_model_version: str
    segment_model_version: str
    algorithm_bundle: str
    settings: Mapping[str, object] = field(default_factory=dict)


def comparison_input_hash(comparison_input: ComparisonInput) -> str:
    payload = _canonical(comparison_input)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class SegmentComparison:
    segment_id: str
    start_m: float
    end_m: float
    delta_s: float | None
    coverage: float


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    comparison_id: str
    input_hash: str
    comparison_input: ComparisonInput
    compatibility: CompatibilityReport
    lap_delta_s: float | None
    coverage_ratio: float
    quality_score: float
    segment_results: tuple[SegmentComparison, ...]
    cumulative_delta_s: tuple[float | None, ...]
    reconciled: bool
    reconciliation_error_s: float | None
    algorithm_bundle: str


def build_comparison_result(
    comparison_input: ComparisonInput,
    compatibility: CompatibilityReport,
    distance_m: ArrayLike,
    candidate_time_s: ArrayLike,
    reference_time_s: ArrayLike,
    segments: Iterable[Segment],
    *,
    model_quality: float = 1.0,
    max_bridge_gap_m: float = 5.0,
    reconciliation_tolerance_s: float = 0.003,
) -> ComparisonResult:
    """Build a result whose ID and numeric output are reproducible from inputs."""

    input_hash = comparison_input_hash(comparison_input)
    timing: TimeDeltaResult = compute_time_delta(
        distance_m,
        candidate_time_s,
        reference_time_s,
        segments=segments,
        max_bridge_gap_m=max_bridge_gap_m,
        reconciliation_tolerance_s=reconciliation_tolerance_s,
    )
    quality = max(
        0.0,
        min(1.0, timing.coverage * float(model_quality) * compatibility.compatibility_weight),
    )
    segment_results = tuple(
        SegmentComparison(item.segment_id, item.start_m, item.end_m, item.delta_s, item.coverage)
        for item in timing.segment_deltas
    )
    cumulative = tuple(
        None if not np.isfinite(value) else float(value)
        for value in timing.cumulative_delta_s
    )
    return ComparisonResult(
        comparison_id=f"cmp_{input_hash[:24]}",
        input_hash=input_hash,
        comparison_input=comparison_input,
        compatibility=compatibility,
        lap_delta_s=timing.lap_delta_s,
        coverage_ratio=timing.coverage,
        quality_score=quality,
        segment_results=segment_results,
        cumulative_delta_s=cumulative,
        reconciled=timing.reconciled,
        reconciliation_error_s=timing.reconciliation_error_s,
        algorithm_bundle=comparison_input.algorithm_bundle,
    )
