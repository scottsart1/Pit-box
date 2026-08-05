"""Distance-axis cleaning, no-bridge resampling, and time delta maths."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

import numpy as np
from numpy.typing import ArrayLike, NDArray

from .segments import Segment

FloatArray = NDArray[np.float64]


class SignalKind(StrEnum):
    CONTINUOUS = "continuous"
    STEP = "step"
    NEAREST = "nearest"


@dataclass(frozen=True, slots=True)
class GapRule:
    max_gap_m: float
    min_coverage: float = 0.0

    def __post_init__(self) -> None:
        if self.max_gap_m <= 0:
            raise ValueError("max_gap_m must be positive")
        if not 0.0 <= self.min_coverage <= 1.0:
            raise ValueError("min_coverage must be between zero and one")


@dataclass(frozen=True, slots=True)
class SignalSpec:
    kind: SignalKind = SignalKind.CONTINUOUS
    gap_rule: GapRule = GapRule(5.0)


DEFAULT_SIGNAL_SPECS: Mapping[str, SignalSpec] = MappingProxyType(
    {
        "time_s": SignalSpec(SignalKind.CONTINUOUS, GapRule(5.0, 0.95)),
        "speed": SignalSpec(SignalKind.CONTINUOUS, GapRule(6.0, 0.90)),
        "brake": SignalSpec(SignalKind.CONTINUOUS, GapRule(3.0, 0.95)),
        "throttle": SignalSpec(SignalKind.CONTINUOUS, GapRule(3.0, 0.95)),
        "steering": SignalSpec(SignalKind.CONTINUOUS, GapRule(3.0, 0.95)),
        "gear": SignalSpec(SignalKind.STEP, GapRule(4.0, 0.90)),
        "line_n": SignalSpec(SignalKind.CONTINUOUS, GapRule(8.0, 0.90)),
    }
)


def _readonly(array: ArrayLike) -> FloatArray:
    result = np.asarray(array, dtype=np.float64).copy()
    result.setflags(write=False)
    return result


@dataclass(frozen=True, slots=True)
class CleanDistanceTrace:
    distance_m: FloatArray
    signals: Mapping[str, FloatArray]
    source_indices: NDArray[np.int64]
    timeline_epoch: int
    discontinuities: int
    dropped_samples: int


def clean_distance_axis(
    distance_m: ArrayLike,
    signals: Mapping[str, ArrayLike],
    *,
    valid_mask: ArrayLike | None = None,
    track_length_m: float | None = None,
    monotonic_tolerance_m: float = 0.25,
    epoch_policy: str = "last",
) -> CleanDistanceTrace:
    """Remove invalid samples and isolate one monotonic timeline epoch.

    A meaningful backward movement starts a new epoch, preventing pre-flashback
    and post-flashback samples from forming a synthetic lap. Small jitter and
    duplicate distances retain the later sample.
    """

    distance = np.asarray(distance_m, dtype=np.float64)
    if distance.ndim != 1:
        raise ValueError("distance must be one-dimensional")
    arrays = {name: np.asarray(values, dtype=np.float64) for name, values in signals.items()}
    if any(values.ndim != 1 or values.size != distance.size for values in arrays.values()):
        raise ValueError("all signals must be one-dimensional and match distance")
    valid = np.isfinite(distance)
    if valid_mask is not None:
        supplied = np.asarray(valid_mask, dtype=bool)
        if supplied.shape != distance.shape:
            raise ValueError("valid_mask must match distance")
        valid &= supplied
    if track_length_m is not None:
        if track_length_m <= 0:
            raise ValueError("track_length_m must be positive")
        valid &= (distance >= 0.0) & (distance <= float(track_length_m))
    raw_indices = np.flatnonzero(valid)
    if raw_indices.size == 0:
        return CleanDistanceTrace(
            _readonly([]),
            MappingProxyType({name: _readonly([]) for name in arrays}),
            np.asarray([], dtype=np.int64),
            0,
            0,
            int(distance.size),
        )

    epochs: list[list[int]] = [[]]
    previous: float | None = None
    discontinuities = 0
    for raw_idx in raw_indices:
        current = float(distance[raw_idx])
        if previous is not None and current < previous - monotonic_tolerance_m:
            epochs.append([])
            discontinuities += 1
        epochs[-1].append(int(raw_idx))
        previous = current
    non_empty = [epoch for epoch in epochs if epoch]
    if epoch_policy == "last":
        selected_epoch = len(non_empty) - 1
    elif epoch_policy == "longest":
        selected_epoch = max(
            range(len(non_empty)),
            key=lambda idx: distance[non_empty[idx][-1]] - distance[non_empty[idx][0]],
        )
    else:
        raise ValueError("epoch_policy must be last or longest")

    selected = non_empty[selected_epoch]
    # Replace a duplicate/jittered coordinate with the later sample.
    kept: list[int] = []
    for raw_idx in selected:
        if not kept:
            kept.append(raw_idx)
            continue
        if distance[raw_idx] <= distance[kept[-1]] + monotonic_tolerance_m:
            kept[-1] = raw_idx
        else:
            kept.append(raw_idx)
    source_indices = np.asarray(kept, dtype=np.int64)
    source_indices.setflags(write=False)
    clean_signals = MappingProxyType(
        {name: _readonly(values[source_indices]) for name, values in arrays.items()}
    )
    return CleanDistanceTrace(
        distance_m=_readonly(distance[source_indices]),
        signals=clean_signals,
        source_indices=source_indices,
        timeline_epoch=selected_epoch,
        discontinuities=discontinuities,
        dropped_samples=int(distance.size - source_indices.size),
    )


def build_distance_grid(start_m: float, end_m: float, spacing_m: float = 0.5) -> FloatArray:
    if spacing_m <= 0 or end_m < start_m:
        raise ValueError("grid requires positive spacing and end >= start")
    count = int(np.floor((end_m - start_m) / spacing_m))
    grid = start_m + np.arange(count + 1, dtype=np.float64) * spacing_m
    if not grid.size or grid[-1] < end_m - spacing_m * 1e-6:
        grid = np.append(grid, float(end_m))
    else:
        grid[-1] = min(grid[-1], end_m)
    return _readonly(grid)


def _interpolate_with_gaps(
    source_x: FloatArray,
    source_y: FloatArray,
    target_x: FloatArray,
    spec: SignalSpec,
) -> FloatArray:
    output = np.full(target_x.shape, np.nan, dtype=np.float64)
    finite = np.isfinite(source_x) & np.isfinite(source_y)
    x = source_x[finite]
    y = source_y[finite]
    if x.size == 0:
        return _readonly(output)
    if x.size == 1:
        exact = np.isclose(target_x, x[0], atol=1e-9)
        output[exact] = y[0]
        return _readonly(output)

    right = np.searchsorted(x, target_x, side="left")
    exact_right = np.clip(right, 0, x.size - 1)
    exact = np.isclose(target_x, x[exact_right], atol=1e-9)
    output[exact] = y[exact_right[exact]]
    interior = (right > 0) & (right < x.size) & ~exact
    left_idx = np.clip(right - 1, 0, x.size - 1)
    right_idx = np.clip(right, 0, x.size - 1)
    gaps = x[right_idx] - x[left_idx]
    allowed = interior & (gaps <= spec.gap_rule.max_gap_m)
    if spec.kind is SignalKind.CONTINUOUS:
        output[allowed] = np.interp(target_x[allowed], x, y)
    elif spec.kind is SignalKind.STEP:
        output[allowed] = y[left_idx[allowed]]
    else:
        left_distance = np.abs(target_x - x[left_idx])
        right_distance = np.abs(x[right_idx] - target_x)
        nearest = np.where(left_distance <= right_distance, left_idx, right_idx)
        output[allowed] = y[nearest[allowed]]
    return _readonly(output)


@dataclass(frozen=True, slots=True)
class ResampledTrace:
    distance_m: FloatArray
    signals: Mapping[str, FloatArray]
    coverage: Mapping[str, float]
    usable: Mapping[str, bool]


def resample_distance(
    trace: CleanDistanceTrace,
    grid_m: ArrayLike,
    *,
    specs: Mapping[str, SignalSpec] | None = None,
) -> ResampledTrace:
    grid = np.asarray(grid_m, dtype=np.float64)
    if grid.ndim != 1 or (grid.size > 1 and np.any(np.diff(grid) <= 0)):
        raise ValueError("grid must be a strictly increasing one-dimensional array")
    configured = dict(DEFAULT_SIGNAL_SPECS)
    configured.update(specs or {})
    output: dict[str, FloatArray] = {}
    coverage: dict[str, float] = {}
    usable: dict[str, bool] = {}
    for name, source in trace.signals.items():
        spec = configured.get(name, SignalSpec())
        values = _interpolate_with_gaps(trace.distance_m, source, grid, spec)
        output[name] = values
        ratio = float(np.count_nonzero(np.isfinite(values)) / max(1, values.size))
        coverage[name] = ratio
        usable[name] = ratio >= spec.gap_rule.min_coverage
    return ResampledTrace(
        _readonly(grid),
        MappingProxyType(output),
        MappingProxyType(coverage),
        MappingProxyType(usable),
    )


@dataclass(frozen=True, slots=True)
class AlignedPair:
    distance_m: FloatArray
    candidate: ResampledTrace
    reference: ResampledTrace
    common_coverage: Mapping[str, float]


def align_distance_traces(
    candidate: CleanDistanceTrace,
    reference: CleanDistanceTrace,
    *,
    spacing_m: float = 0.5,
    start_m: float | None = None,
    end_m: float | None = None,
    specs: Mapping[str, SignalSpec] | None = None,
) -> AlignedPair:
    if not candidate.distance_m.size or not reference.distance_m.size:
        raise ValueError("both traces require distance samples")
    common_start = max(float(candidate.distance_m[0]), float(reference.distance_m[0]))
    common_end = min(float(candidate.distance_m[-1]), float(reference.distance_m[-1]))
    if start_m is not None:
        common_start = max(common_start, float(start_m))
    if end_m is not None:
        common_end = min(common_end, float(end_m))
    if common_end <= common_start:
        raise ValueError("traces do not share a distance range")
    grid = build_distance_grid(common_start, common_end, spacing_m)
    cand = resample_distance(candidate, grid, specs=specs)
    ref = resample_distance(reference, grid, specs=specs)
    fields = set(cand.signals) & set(ref.signals)
    common = {
        field: float(
            np.count_nonzero(
                np.isfinite(cand.signals[field]) & np.isfinite(ref.signals[field])
            )
            / max(1, grid.size)
        )
        for field in fields
    }
    return AlignedPair(grid, cand, ref, MappingProxyType(common))


@dataclass(frozen=True, slots=True)
class SegmentDelta:
    segment_id: str
    start_m: float
    end_m: float
    delta_s: float | None
    coverage: float


@dataclass(frozen=True, slots=True)
class TimeDeltaResult:
    distance_m: FloatArray
    cumulative_delta_s: FloatArray
    lap_delta_s: float | None
    segment_deltas: tuple[SegmentDelta, ...]
    coverage: float
    reconciled_sum_s: float | None
    reconciliation_error_s: float | None
    reconciled: bool
    sign_convention: str = "positive_candidate_later"


def _interp_finite(x: FloatArray, y: FloatArray, at: float, max_gap_m: float) -> float | None:
    finite = np.isfinite(x) & np.isfinite(y)
    fx, fy = x[finite], y[finite]
    if fx.size == 0 or at < fx[0] or at > fx[-1]:
        return None
    right = int(np.searchsorted(fx, at, side="left"))
    if right < fx.size and np.isclose(fx[right], at, atol=1e-9):
        return float(fy[right])
    if right <= 0 or right >= fx.size or fx[right] - fx[right - 1] > max_gap_m:
        return None
    return float(np.interp(at, fx, fy))


def compute_time_delta(
    distance_m: ArrayLike,
    candidate_time_s: ArrayLike,
    reference_time_s: ArrayLike,
    *,
    segments: Iterable[Segment] = (),
    max_bridge_gap_m: float = 5.0,
    reconciliation_tolerance_s: float = 0.003,
) -> TimeDeltaResult:
    """Compute candidate-minus-reference t(d) and local segment losses."""

    distance = np.asarray(distance_m, dtype=np.float64)
    candidate = np.asarray(candidate_time_s, dtype=np.float64)
    reference = np.asarray(reference_time_s, dtype=np.float64)
    if distance.ndim != 1 or candidate.shape != distance.shape or reference.shape != distance.shape:
        raise ValueError("distance and time arrays must be equal-length and one-dimensional")
    if distance.size > 1 and np.any(np.diff(distance) <= 0):
        raise ValueError("distance must be strictly increasing")
    valid = np.isfinite(distance) & np.isfinite(candidate) & np.isfinite(reference)
    delta = np.full(distance.shape, np.nan, dtype=np.float64)
    if np.any(valid):
        first = int(np.flatnonzero(valid)[0])
        candidate_zero = candidate[first]
        reference_zero = reference[first]
        delta[valid] = (candidate[valid] - candidate_zero) - (reference[valid] - reference_zero)
    coverage = float(np.count_nonzero(valid) / max(1, distance.size))
    valid_indices = np.flatnonzero(np.isfinite(delta))
    lap_delta = float(delta[valid_indices[-1]]) if valid_indices.size >= 2 else None

    segment_results: list[SegmentDelta] = []
    for segment in sorted(segments, key=lambda item: item.ordinal):
        start_delta = _interp_finite(distance, delta, segment.start_m, max_bridge_gap_m)
        end_delta = _interp_finite(distance, delta, segment.end_m, max_bridge_gap_m)
        inside = (distance >= segment.start_m) & (distance <= segment.end_m)
        segment_coverage = float(
            np.count_nonzero(inside & np.isfinite(delta)) / max(1, np.count_nonzero(inside))
        )
        local = None if start_delta is None or end_delta is None else end_delta - start_delta
        segment_results.append(
            SegmentDelta(segment.id, segment.start_m, segment.end_m, local, segment_coverage)
        )
    comparable = [result.delta_s for result in segment_results if result.delta_s is not None]
    reconciled_sum = float(sum(comparable)) if comparable else None
    error = (
        abs(reconciled_sum - lap_delta)
        if reconciled_sum is not None and lap_delta is not None
        else None
    )
    return TimeDeltaResult(
        distance_m=_readonly(distance),
        cumulative_delta_s=_readonly(delta),
        lap_delta_s=lap_delta,
        segment_deltas=tuple(segment_results),
        coverage=coverage,
        reconciled_sum_s=reconciled_sum,
        reconciliation_error_s=error,
        reconciled=error is not None and error <= reconciliation_tolerance_s,
    )
