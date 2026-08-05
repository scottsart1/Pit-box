"""Deterministic closed-track construction and Frenet projection.

The builder consumes multiple already-recorded trajectories and deliberately
publishes nothing when their geometry cannot support honest line analysis.  It
does not read global state, files, packets, or a database.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from itertools import pairwise
from typing import TypeAlias

import numpy as np
from numpy.typing import ArrayLike, NDArray

FloatArray = NDArray[np.float64]
BoolArray = NDArray[np.bool_]
SampleRejectionHook: TypeAlias = Callable[
    ["Trajectory", FloatArray, BoolArray], ArrayLike
]


def _bounded(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _readonly_float(values: ArrayLike) -> FloatArray:
    result = np.asarray(values, dtype=np.float64).copy()
    result.setflags(write=False)
    return result


def _readonly_bool(values: ArrayLike) -> BoolArray:
    result = np.asarray(values, dtype=bool).copy()
    result.setflags(write=False)
    return result


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return normalized[:32] or "track"


class TrackModelOutcome(StrEnum):
    PUBLISHED = "published"
    MAP_CALIBRATION_REQUIRED = "map_calibration_required"


class ProjectionStatus(StrEnum):
    PROJECTED = "projected"
    AMBIGUOUS = "ambiguous"
    OUTSIDE_MODEL = "outside_model"
    JUMP_REJECTED = "jump_rejected"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class Trajectory:
    id: str
    points: FloatArray
    valid_mask: BoolArray | None = None
    pit_mask: BoolArray | None = None
    weight: float = 1.0

    def __post_init__(self) -> None:
        points = np.asarray(self.points, dtype=np.float64)
        if points.ndim != 2 or points.shape[1] != 2:
            raise ValueError("trajectory points must have shape (n, 2)")
        if points.shape[0] < 3:
            raise ValueError("a trajectory requires at least three points")
        object.__setattr__(self, "points", _readonly_float(points))
        for name in ("valid_mask", "pit_mask"):
            supplied = getattr(self, name)
            if supplied is None:
                continue
            mask = np.asarray(supplied, dtype=bool)
            if mask.shape != (points.shape[0],):
                raise ValueError(f"{name} must match trajectory length")
            object.__setattr__(self, name, _readonly_bool(mask))
        if self.weight <= 0:
            raise ValueError("trajectory weight must be positive")


@dataclass(frozen=True, slots=True)
class TrackBuildConfig:
    resample_points: int = 360
    min_clean_trajectories: int = 3
    min_trajectory_coverage: float = 0.90
    max_pit_fraction: float = 0.02
    teleport_threshold_m: float = 75.0
    max_source_gap_m: float = 90.0
    max_source_closure_m: float = 90.0
    trajectory_outlier_mad: float = 4.5
    point_outlier_mad: float = 4.5
    max_trajectory_residual_m: float = 12.0
    max_point_residual_m: float = 15.0
    smoothing_window: int = 5
    smoothing_passes: int = 2
    min_track_length_m: float = 50.0
    max_track_length_m: float = 12_000.0
    max_closure_error_m: float = 4.0
    max_p95_residual_m: float = 5.0
    max_heading_step_deg: float = 45.0
    min_continuity_score: float = 0.95
    min_model_coverage: float = 0.92
    crossing_tolerance_m: float = 0.75
    max_self_crossings: int = 0
    publishability_threshold: float = 0.78
    algorithm_version: str = "track_model_v1"

    def __post_init__(self) -> None:
        if self.resample_points < 24:
            raise ValueError("resample_points must be at least 24")
        if self.min_clean_trajectories < 1:
            raise ValueError("min_clean_trajectories must be positive")
        for name in (
            "min_trajectory_coverage",
            "max_pit_fraction",
            "min_continuity_score",
            "min_model_coverage",
            "publishability_threshold",
        ):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between zero and one")
        if self.smoothing_window < 1 or self.smoothing_window % 2 == 0:
            raise ValueError("smoothing_window must be a positive odd number")
        if self.smoothing_passes < 0:
            raise ValueError("smoothing_passes cannot be negative")
        for name in (
            "teleport_threshold_m",
            "max_source_gap_m",
            "max_source_closure_m",
            "max_trajectory_residual_m",
            "max_point_residual_m",
            "min_track_length_m",
            "max_track_length_m",
            "max_closure_error_m",
            "max_p95_residual_m",
            "max_heading_step_deg",
            "crossing_tolerance_m",
        ):
            if float(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be positive")


@dataclass(frozen=True, slots=True)
class TrajectoryQuality:
    trajectory_id: str
    accepted: bool
    original_samples: int
    retained_samples: int
    coverage_ratio: float
    source_length_m: float | None
    source_closure_m: float | None
    median_residual_m: float | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class TrackQualityReport:
    outcome: TrackModelOutcome
    publishable: bool
    quality_score: float
    clean_trajectories: int
    rejected_trajectories: int
    coverage_ratio: float
    length_m: float | None
    closure_error_m: float | None
    source_closure_m: float | None
    median_residual_m: float | None
    p95_residual_m: float | None
    continuity_score: float
    max_heading_step_deg: float | None
    self_crossings: int
    reasons: tuple[str, ...]
    trajectories: tuple[TrajectoryQuality, ...]


@dataclass(frozen=True, slots=True)
class TrackModel:
    id: str
    track_key: str
    version: int
    algorithm_version: str
    centerline: FloatArray
    cumulative_s_m: FloatArray
    tangents: FloatArray
    normals: FloatArray
    length_m: float
    quality: TrackQualityReport
    checksum: str

    def __post_init__(self) -> None:
        count = self.centerline.shape[0]
        if self.centerline.shape != (count, 2):
            raise ValueError("centerline must have shape (n, 2)")
        if self.cumulative_s_m.shape != (count,):
            raise ValueError("cumulative_s_m must match centerline")
        if self.tangents.shape != (count, 2) or self.normals.shape != (count, 2):
            raise ValueError("tangent and normal arrays must match centerline")
        for name in ("centerline", "cumulative_s_m", "tangents", "normals"):
            object.__setattr__(self, name, _readonly_float(getattr(self, name)))


@dataclass(frozen=True, slots=True)
class TrackModelBuildResult:
    outcome: TrackModelOutcome
    model: TrackModel | None
    quality: TrackQualityReport


@dataclass(frozen=True, slots=True)
class ProjectionHint:
    previous_s_m: float


@dataclass(frozen=True, slots=True)
class ProjectionConfig:
    local_search_radius_m: float = 180.0
    max_projection_distance_m: float = 25.0
    max_s_jump_m: float = 100.0
    ambiguity_distance_m: float = 0.75
    ambiguity_separation_m: float = 60.0

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            if float(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be positive")


@dataclass(frozen=True, slots=True)
class TrackProjection:
    status: ProjectionStatus
    s_m: float | None
    n_m: float | None
    projected_point: tuple[float, float] | None
    tangent: tuple[float, float] | None
    normal: tuple[float, float] | None
    segment_index: int | None
    residual_m: float | None
    confidence: float
    used_local_search: bool
    reason: str | None = None


def _empty_quality(
    reports: Sequence[TrajectoryQuality],
    reasons: Iterable[str],
) -> TrackQualityReport:
    reason_tuple = tuple(dict.fromkeys(reasons))
    clean = sum(report.accepted for report in reports)
    return TrackQualityReport(
        outcome=TrackModelOutcome.MAP_CALIBRATION_REQUIRED,
        publishable=False,
        quality_score=0.0,
        clean_trajectories=clean,
        rejected_trajectories=len(reports) - clean,
        coverage_ratio=0.0,
        length_m=None,
        closure_error_m=None,
        source_closure_m=None,
        median_residual_m=None,
        p95_residual_m=None,
        continuity_score=0.0,
        max_heading_step_deg=None,
        self_crossings=0,
        reasons=reason_tuple or ("map calibration required",),
        trajectories=tuple(reports),
    )


def _trajectory_sort_key(trajectory: Trajectory) -> tuple[str, str]:
    digest = hashlib.blake2s(
        np.round(trajectory.points, 6).astype("<f8", copy=False).tobytes(),
        digest_size=8,
    ).hexdigest()
    return trajectory.id, digest


def _teleport_keep_mask(
    points: FloatArray,
    initial: BoolArray,
    threshold_m: float,
) -> BoolArray:
    keep = np.asarray(initial, dtype=bool).copy()
    indices = np.flatnonzero(keep)
    if indices.size < 3:
        return keep
    # Reject a spike whose neighbours remain mutually plausible.
    for offset in range(1, indices.size - 1):
        previous, current, following = indices[offset - 1 : offset + 2]
        into = float(np.linalg.norm(points[current] - points[previous]))
        out = float(np.linalg.norm(points[following] - points[current]))
        across = float(np.linalg.norm(points[following] - points[previous]))
        if into > threshold_m and out > threshold_m and across <= threshold_m * 1.5:
            keep[current] = False
    # Any remaining impossible edge rejects its later endpoint. Recalculate after
    # spike removal so one bad point does not also discard the next good sample.
    indices = np.flatnonzero(keep)
    for previous, current in pairwise(indices):
        if np.linalg.norm(points[current] - points[previous]) > threshold_m:
            keep[current] = False
    return keep


def _deduplicate(points: FloatArray, tolerance_m: float = 1e-6) -> FloatArray:
    if points.shape[0] < 2:
        return points
    distance = np.linalg.norm(np.diff(points, axis=0), axis=1)
    keep = np.ones(points.shape[0], dtype=bool)
    keep[1:] = distance > tolerance_m
    return points[keep]


def _closed_geometry(points: FloatArray) -> tuple[FloatArray, FloatArray, float]:
    vectors = np.roll(points, -1, axis=0) - points
    lengths = np.linalg.norm(vectors, axis=1)
    return vectors, lengths, float(np.sum(lengths))


def _resample_closed(points: FloatArray, count: int) -> FloatArray:
    points = _deduplicate(points)
    if points.shape[0] < 3:
        raise ValueError("not enough unique points for a closed path")
    _, lengths, total = _closed_geometry(points)
    if total <= 0 or np.count_nonzero(lengths > 0) < 3:
        raise ValueError("closed path has no usable length")
    cumulative_edges = np.concatenate(([0.0], np.cumsum(lengths)))
    targets = np.arange(count, dtype=np.float64) * total / count
    segment = np.searchsorted(cumulative_edges, targets, side="right") - 1
    segment = np.clip(segment, 0, points.shape[0] - 1)
    starts = cumulative_edges[segment]
    fractions = (targets - starts) / np.maximum(lengths[segment], 1e-12)
    following = (segment + 1) % points.shape[0]
    return points[segment] + fractions[:, None] * (points[following] - points[segment])


def _polygon_area(points: FloatArray) -> float:
    following = np.roll(points, -1, axis=0)
    return 0.5 * float(
        np.sum(points[:, 0] * following[:, 1] - following[:, 0] * points[:, 1])
    )


def _canonical_start(points: FloatArray) -> FloatArray:
    anchor = int(np.lexsort((points[:, 1], points[:, 0]))[0])
    return np.roll(points, -anchor, axis=0)


def _canonicalize(points: FloatArray) -> FloatArray:
    result = points
    if _polygon_area(result) < 0:
        result = result[::-1]
    return _canonical_start(result)


def _alignment_cost(candidate: FloatArray, reference: FloatArray) -> float:
    residual = candidate - reference
    return float(np.mean(np.sum(residual * residual, axis=1)))


def _best_cyclic_alignment(path: FloatArray, reference: FloatArray) -> FloatArray:
    best: FloatArray | None = None
    best_key: tuple[float, int, int] | None = None
    for reversed_flag, oriented in enumerate((path, path[::-1])):
        for shift in range(path.shape[0]):
            candidate = np.roll(oriented, -shift, axis=0)
            key = (_alignment_cost(candidate, reference), reversed_flag, shift)
            if best_key is None or key < best_key:
                best_key = key
                best = candidate
    assert best is not None
    return best


def _smooth_periodic(points: FloatArray, window: int, passes: int) -> FloatArray:
    if window <= 1 or passes <= 0:
        return points.copy()
    radius = window // 2
    weights = np.asarray(
        [radius + 1 - abs(offset) for offset in range(-radius, radius + 1)],
        dtype=np.float64,
    )
    weights /= np.sum(weights)
    result = points.copy()
    for _ in range(passes):
        smoothed = np.zeros_like(result)
        for offset, weight in zip(range(-radius, radius + 1), weights, strict=True):
            smoothed += weight * np.roll(result, offset, axis=0)
        result = smoothed
    return result


def _prepare_trajectory(
    trajectory: Trajectory,
    config: TrackBuildConfig,
    hooks: Sequence[SampleRejectionHook],
) -> tuple[FloatArray | None, TrajectoryQuality]:
    points = trajectory.points
    count = points.shape[0]
    keep = np.all(np.isfinite(points), axis=1)
    if trajectory.valid_mask is not None:
        keep &= trajectory.valid_mask
    pit_count = 0
    if trajectory.pit_mask is not None:
        pit_count = int(np.count_nonzero(trajectory.pit_mask))
        keep &= ~trajectory.pit_mask
    if pit_count / count > config.max_pit_fraction:
        return None, TrajectoryQuality(
            trajectory.id,
            False,
            count,
            int(np.count_nonzero(keep)),
            float(np.count_nonzero(keep) / count),
            None,
            None,
            reason="pit-lane coverage exceeds limit",
        )
    keep = _teleport_keep_mask(points, keep, config.teleport_threshold_m)
    for hook in hooks:
        hook_mask = np.asarray(hook(trajectory, points, _readonly_bool(keep)), dtype=bool)
        if hook_mask.shape != keep.shape:
            raise ValueError("sample rejection hook must return a mask matching the trajectory")
        keep &= hook_mask
    retained = int(np.count_nonzero(keep))
    coverage = retained / count
    if retained < 3 or coverage < config.min_trajectory_coverage:
        return None, TrajectoryQuality(
            trajectory.id,
            False,
            count,
            retained,
            coverage,
            None,
            None,
            reason="insufficient clean sample coverage",
        )
    clean = _deduplicate(points[keep])
    if clean.shape[0] >= 4:
        typical = float(np.median(np.linalg.norm(np.diff(clean, axis=0), axis=1)))
        if np.linalg.norm(clean[-1] - clean[0]) <= max(1e-6, typical * 0.5):
            clean = clean[:-1]
    _, step_lengths, source_length = _closed_geometry(clean)
    source_closure = float(np.linalg.norm(clean[-1] - clean[0]))
    if (
        source_length < config.min_track_length_m
        or source_length > config.max_track_length_m
    ):
        return None, TrajectoryQuality(
            trajectory.id,
            False,
            count,
            retained,
            coverage,
            source_length,
            source_closure,
            reason="source track length outside configured range",
        )
    if source_closure > config.max_source_closure_m:
        return None, TrajectoryQuality(
            trajectory.id,
            False,
            count,
            retained,
            coverage,
            source_length,
            source_closure,
            reason="trajectory does not close near its start",
        )
    if float(np.max(step_lengths)) > config.max_source_gap_m:
        return None, TrajectoryQuality(
            trajectory.id,
            False,
            count,
            retained,
            coverage,
            source_length,
            source_closure,
            reason="trajectory contains an unbridgeable source gap",
        )
    try:
        normalized = _canonicalize(_resample_closed(clean, config.resample_points))
    except ValueError as exc:
        return None, TrajectoryQuality(
            trajectory.id,
            False,
            count,
            retained,
            coverage,
            source_length,
            source_closure,
            reason=str(exc),
        )
    return normalized, TrajectoryQuality(
        trajectory.id,
        True,
        count,
        retained,
        coverage,
        source_length,
        source_closure,
    )


def _robust_centerline(
    paths: FloatArray,
    config: TrackBuildConfig,
) -> tuple[FloatArray, FloatArray, float]:
    initial = np.median(paths, axis=0)
    distance = np.linalg.norm(paths - initial[None, :, :], axis=2)
    per_point_median = np.median(distance, axis=0)
    per_point_mad = np.median(
        np.abs(distance - per_point_median[None, :]), axis=0
    )
    thresholds = np.minimum(
        config.max_point_residual_m,
        per_point_median
        + config.point_outlier_mad * np.maximum(per_point_mad, 0.05),
    )
    keep = distance <= thresholds[None, :]
    centerline = np.empty_like(initial)
    sufficient = np.zeros(initial.shape[0], dtype=bool)
    required = min(paths.shape[0], max(2, config.min_clean_trajectories))
    for index in range(initial.shape[0]):
        accepted = paths[keep[:, index], index]
        sufficient[index] = accepted.shape[0] >= required
        centerline[index] = np.median(accepted, axis=0) if accepted.size else initial[index]
    return centerline, keep, float(np.count_nonzero(sufficient) / sufficient.size)


def _segment_geometry(
    centerline: FloatArray,
) -> tuple[FloatArray, FloatArray, FloatArray, FloatArray, float]:
    segment_vectors, segment_lengths, total = _closed_geometry(centerline)
    safe = np.maximum(segment_lengths, 1e-12)
    segment_tangents = segment_vectors / safe[:, None]
    tangents = segment_tangents + np.roll(segment_tangents, 1, axis=0)
    tangent_norm = np.linalg.norm(tangents, axis=1)
    degenerate = tangent_norm <= 1e-12
    tangents[~degenerate] /= tangent_norm[~degenerate, None]
    tangents[degenerate] = segment_tangents[degenerate]
    normals = np.column_stack((-tangents[:, 1], tangents[:, 0]))
    cumulative = np.concatenate(([0.0], np.cumsum(segment_lengths[:-1])))
    return cumulative, tangents, normals, segment_lengths, total


def _heading_quality(centerline: FloatArray, maximum_deg: float) -> tuple[float, float]:
    vectors = np.roll(centerline, -1, axis=0) - centerline
    lengths = np.linalg.norm(vectors, axis=1)
    tangents = vectors / np.maximum(lengths, 1e-12)[:, None]
    cosine = np.sum(tangents * np.roll(tangents, 1, axis=0), axis=1)
    angles = np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0)))
    return float(np.mean(angles <= maximum_deg)), float(np.max(angles))


def _cross_2d(left: FloatArray, right: FloatArray) -> float:
    return float(left[0] * right[1] - left[1] * right[0])


def _self_crossings(centerline: FloatArray, tolerance_m: float) -> int:
    count = centerline.shape[0]
    crossings = 0
    for first in range(count):
        a = centerline[first]
        b = centerline[(first + 1) % count]
        r = b - a
        for second in range(first + 1, count):
            circular_separation = min(
                (second - first) % count, (first - second) % count
            )
            if circular_separation <= 2:
                continue
            c = centerline[second]
            d = centerline[(second + 1) % count]
            endpoint_distance = min(
                np.linalg.norm(a - c),
                np.linalg.norm(a - d),
                np.linalg.norm(b - c),
                np.linalg.norm(b - d),
            )
            if endpoint_distance <= tolerance_m:
                crossings += 1
                continue
            s = d - c
            denominator = _cross_2d(r, s)
            if abs(denominator) <= 1e-9:
                continue
            offset = c - a
            along_first = _cross_2d(offset, s) / denominator
            along_second = _cross_2d(offset, r) / denominator
            if 1e-6 < along_first < 1.0 - 1e-6 and 1e-6 < along_second < 1.0 - 1e-6:
                crossings += 1
    return crossings


def _model_checksum(
    track_key: str,
    version: int,
    algorithm_version: str,
    centerline: FloatArray,
) -> str:
    metadata = json.dumps(
        {
            "track_key": track_key,
            "version": int(version),
            "algorithm_version": algorithm_version,
            "points": int(centerline.shape[0]),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    normalized = np.round(centerline, 6).astype("<f8", copy=False).tobytes()
    return hashlib.sha256(metadata + normalized).hexdigest()


def build_track_model(
    track_key: str,
    version: int,
    trajectories: Iterable[Trajectory],
    *,
    config: TrackBuildConfig | None = None,
    rejection_hooks: Iterable[SampleRejectionHook] = (),
) -> TrackModelBuildResult:
    """Build a robust median centerline or request map calibration explicitly."""

    cfg = config or TrackBuildConfig()
    if version < 1:
        raise ValueError("track-model version must be positive")
    hooks = tuple(rejection_hooks)
    source_trajectories = tuple(trajectories)
    if len({trajectory.id for trajectory in source_trajectories}) != len(
        source_trajectories
    ):
        raise ValueError("trajectory IDs must be unique")
    ordered_trajectories = tuple(
        sorted(source_trajectories, key=_trajectory_sort_key)
    )
    prepared: list[tuple[Trajectory, FloatArray]] = []
    reports: list[TrajectoryQuality] = []
    for trajectory in ordered_trajectories:
        path, report = _prepare_trajectory(trajectory, cfg, hooks)
        reports.append(report)
        if path is not None:
            prepared.append((trajectory, path))
    if len(prepared) < cfg.min_clean_trajectories:
        quality = _empty_quality(
            reports,
            (
                (
                    f"need at least {cfg.min_clean_trajectories} clean trajectories; "
                    f"received {len(prepared)}"
                ),
            ),
        )
        return TrackModelBuildResult(quality.outcome, None, quality)

    lengths = np.asarray([_closed_geometry(path)[2] for _, path in prepared])
    median_length = float(np.median(lengths))
    reference_index = min(
        range(len(prepared)),
        key=lambda index: (
            abs(lengths[index] - median_length),
            _trajectory_sort_key(prepared[index][0]),
        ),
    )
    reference = prepared[reference_index][1]
    aligned = np.stack(
        [_best_cyclic_alignment(path, reference) for _, path in prepared], axis=0
    )
    initial = np.median(aligned, axis=0)
    trajectory_residuals = np.median(
        np.linalg.norm(aligned - initial[None, :, :], axis=2), axis=1
    )
    residual_median = float(np.median(trajectory_residuals))
    residual_mad = float(np.median(np.abs(trajectory_residuals - residual_median)))
    residual_limit = min(
        cfg.max_trajectory_residual_m,
        residual_median
        + cfg.trajectory_outlier_mad * max(residual_mad, 0.30),
    )
    accepted_mask = trajectory_residuals <= residual_limit
    report_by_id = {report.trajectory_id: report for report in reports}
    for index, (trajectory, _) in enumerate(prepared):
        accepted = bool(accepted_mask[index])
        report_by_id[trajectory.id] = replace(
            report_by_id[trajectory.id],
            accepted=accepted,
            median_residual_m=float(trajectory_residuals[index]),
            reason=None if accepted else "trajectory rejected as a geometric outlier",
        )
    reports = [report_by_id[trajectory.id] for trajectory in ordered_trajectories]
    accepted_paths = aligned[accepted_mask]
    if accepted_paths.shape[0] < cfg.min_clean_trajectories:
        quality = _empty_quality(
            reports,
            ("too few mutually consistent trajectories after outlier rejection",),
        )
        return TrackModelBuildResult(quality.outcome, None, quality)

    centerline, _, point_coverage = _robust_centerline(accepted_paths, cfg)
    centerline = _smooth_periodic(
        centerline, cfg.smoothing_window, cfg.smoothing_passes
    )
    centerline = _canonicalize(_resample_closed(centerline, cfg.resample_points))

    final_aligned = np.stack(
        [_best_cyclic_alignment(path, centerline) for path in accepted_paths], axis=0
    )
    residuals = np.linalg.norm(final_aligned - centerline[None, :, :], axis=2)
    median_residual = float(np.median(residuals))
    p95_residual = float(np.percentile(residuals, 95.0, method="linear"))
    cumulative, tangents, normals, segment_lengths, length_m = _segment_geometry(centerline)
    typical_segment = float(np.median(segment_lengths))
    closure_error = abs(float(segment_lengths[-1]) - typical_segment)
    source_closure_values = [
        report.source_closure_m
        for report in reports
        if report.accepted and report.source_closure_m is not None
    ]
    source_closure = (
        float(np.median(source_closure_values)) if source_closure_values else None
    )
    continuity, max_heading = _heading_quality(
        centerline, cfg.max_heading_step_deg
    )
    crossings = _self_crossings(centerline, cfg.crossing_tolerance_m)
    source_coverage = float(
        np.median([report.coverage_ratio for report in reports if report.accepted])
    )
    coverage = min(point_coverage, source_coverage)

    closure_score = 1.0 - _bounded(closure_error / cfg.max_closure_error_m)
    residual_score = 1.0 - _bounded(p95_residual / cfg.max_p95_residual_m)
    count_score = _bounded(accepted_paths.shape[0] / cfg.min_clean_trajectories)
    quality_score = (
        max(closure_score, 1e-9)
        * max(residual_score, 1e-9)
        * max(continuity, 1e-9)
        * max(coverage, 1e-9)
        * max(count_score, 1e-9)
    ) ** 0.2

    reasons: list[str] = []
    if not cfg.min_track_length_m <= length_m <= cfg.max_track_length_m:
        reasons.append("derived track length is outside configured limits")
    if closure_error > cfg.max_closure_error_m:
        reasons.append("centerline seam does not close smoothly")
    if p95_residual > cfg.max_p95_residual_m:
        reasons.append("trajectory residual is too large")
    if continuity < cfg.min_continuity_score:
        reasons.append("centerline tangent continuity is too low")
    if coverage < cfg.min_model_coverage:
        reasons.append("track-model coverage is incomplete")
    if crossings > cfg.max_self_crossings:
        reasons.append("centerline contains ambiguous self-crossings")
    if quality_score < cfg.publishability_threshold:
        reasons.append("combined model quality is below the publishability threshold")
    publishable = not reasons
    outcome = (
        TrackModelOutcome.PUBLISHED
        if publishable
        else TrackModelOutcome.MAP_CALIBRATION_REQUIRED
    )
    quality = TrackQualityReport(
        outcome=outcome,
        publishable=publishable,
        quality_score=_bounded(quality_score),
        clean_trajectories=int(accepted_paths.shape[0]),
        rejected_trajectories=len(reports) - int(accepted_paths.shape[0]),
        coverage_ratio=coverage,
        length_m=length_m,
        closure_error_m=closure_error,
        source_closure_m=source_closure,
        median_residual_m=median_residual,
        p95_residual_m=p95_residual,
        continuity_score=continuity,
        max_heading_step_deg=max_heading,
        self_crossings=crossings,
        reasons=tuple(reasons),
        trajectories=tuple(reports),
    )
    checksum = _model_checksum(
        track_key, version, cfg.algorithm_version, centerline
    )
    model = TrackModel(
        id=f"track_{_slug(track_key)}_v{version}_{checksum[:12]}",
        track_key=track_key,
        version=version,
        algorithm_version=cfg.algorithm_version,
        centerline=centerline,
        cumulative_s_m=cumulative,
        tangents=tangents,
        normals=normals,
        length_m=length_m,
        quality=quality,
        checksum=checksum,
    )
    return TrackModelBuildResult(outcome, model, quality)


def _circular_delta(target_s: float, origin_s: float, length_m: float) -> float:
    return (target_s - origin_s + length_m / 2.0) % length_m - length_m / 2.0


def _project_segments(
    model: TrackModel,
    point: FloatArray,
    indices: NDArray[np.int64],
) -> tuple[int, float, FloatArray, FloatArray, float, float]:
    starts = model.centerline[indices]
    following_indices = (indices + 1) % model.centerline.shape[0]
    vectors = model.centerline[following_indices] - starts
    squared = np.sum(vectors * vectors, axis=1)
    fractions = np.sum((point - starts) * vectors, axis=1) / np.maximum(
        squared, 1e-12
    )
    fractions = np.clip(fractions, 0.0, 1.0)
    projected = starts + fractions[:, None] * vectors
    residuals = np.linalg.norm(point - projected, axis=1)
    local_choice = int(np.argmin(residuals))
    segment_index = int(indices[local_choice])
    segment_length = math.sqrt(float(squared[local_choice]))
    tangent = vectors[local_choice] / max(segment_length, 1e-12)
    s_m = float(
        (model.cumulative_s_m[segment_index] + fractions[local_choice] * segment_length)
        % model.length_m
    )
    return (
        segment_index,
        s_m,
        projected[local_choice],
        tangent,
        float(residuals[local_choice]),
        float(fractions[local_choice]),
    )


def _ambiguous_projection(
    model: TrackModel,
    point: FloatArray,
    best_segment: int,
    best_s: float,
    best_residual: float,
    config: ProjectionConfig,
) -> bool:
    all_indices = np.arange(model.centerline.shape[0], dtype=np.int64)
    starts = model.centerline
    ends = np.roll(starts, -1, axis=0)
    vectors = ends - starts
    squared = np.sum(vectors * vectors, axis=1)
    fractions = np.sum((point - starts) * vectors, axis=1) / np.maximum(
        squared, 1e-12
    )
    fractions = np.clip(fractions, 0.0, 1.0)
    projected = starts + fractions[:, None] * vectors
    residuals = np.linalg.norm(point - projected, axis=1)
    lengths = np.sqrt(np.maximum(squared, 0.0))
    candidate_s = (model.cumulative_s_m + fractions * lengths) % model.length_m
    far = np.abs(
        np.asarray(
            [
                _circular_delta(float(value), best_s, model.length_m)
                for value in candidate_s
            ]
        )
    ) >= config.ambiguity_separation_m
    close = residuals <= best_residual + config.ambiguity_distance_m
    different = all_indices != best_segment
    return bool(np.any(far & close & different))


def project_to_track(
    model: TrackModel,
    point: ArrayLike,
    *,
    hint: ProjectionHint | None = None,
    config: ProjectionConfig | None = None,
) -> TrackProjection:
    """Project a world point to Frenet ``(s, n)`` with continuity safeguards."""

    cfg = config or ProjectionConfig()
    target = np.asarray(point, dtype=np.float64)
    if target.shape != (2,) or not np.all(np.isfinite(target)):
        return TrackProjection(
            ProjectionStatus.UNAVAILABLE,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            0.0,
            False,
            "point must contain two finite coordinates",
        )
    count = model.centerline.shape[0]
    all_indices = np.arange(count, dtype=np.int64)
    used_local = hint is not None
    if hint is not None:
        local = np.asarray(
            [
                index
                for index, start_s in enumerate(model.cumulative_s_m)
                if abs(
                    _circular_delta(
                        float(start_s), hint.previous_s_m % model.length_m, model.length_m
                    )
                )
                <= cfg.local_search_radius_m
            ],
            dtype=np.int64,
        )
        if local.size == 0:
            local = np.asarray(
                [int(np.argmin(np.abs(model.cumulative_s_m - hint.previous_s_m)))],
                dtype=np.int64,
            )
        projection = _project_segments(model, target, local)
        if projection[4] > cfg.max_projection_distance_m:
            projection = _project_segments(model, target, all_indices)
    else:
        projection = _project_segments(model, target, all_indices)
    segment, s_m, projected, tangent, residual, _ = projection
    normal = np.asarray((-tangent[1], tangent[0]), dtype=np.float64)
    n_m = float(np.dot(target - projected, normal))
    if residual > cfg.max_projection_distance_m:
        return TrackProjection(
            ProjectionStatus.OUTSIDE_MODEL,
            s_m,
            n_m,
            (float(projected[0]), float(projected[1])),
            (float(tangent[0]), float(tangent[1])),
            (float(normal[0]), float(normal[1])),
            segment,
            residual,
            0.0,
            used_local,
            "nearest centerline point exceeds the projection-distance limit",
        )
    if hint is not None:
        jump = abs(
            _circular_delta(s_m, hint.previous_s_m % model.length_m, model.length_m)
        )
        if jump > cfg.max_s_jump_m:
            return TrackProjection(
                ProjectionStatus.JUMP_REJECTED,
                s_m,
                n_m,
                (float(projected[0]), float(projected[1])),
                (float(tangent[0]), float(tangent[1])),
                (float(normal[0]), float(normal[1])),
                segment,
                residual,
                0.0,
                used_local,
                f"projection would jump {jump:.1f} m along the track",
            )
    ambiguous = hint is None and _ambiguous_projection(
        model, target, segment, s_m, residual, cfg
    )
    status = ProjectionStatus.AMBIGUOUS if ambiguous else ProjectionStatus.PROJECTED
    residual_scale = max(1.0, cfg.max_projection_distance_m / 3.0)
    ambiguity_weight = 0.45 if ambiguous else 1.0
    confidence = _bounded(
        model.quality.quality_score
        * math.exp(-residual / residual_scale)
        * ambiguity_weight
    )
    return TrackProjection(
        status,
        s_m,
        n_m,
        (float(projected[0]), float(projected[1])),
        (float(tangent[0]), float(tangent[1])),
        (float(normal[0]), float(normal[1])),
        segment,
        residual,
        confidence,
        used_local,
        "multiple distant track sections are equally plausible" if ambiguous else None,
    )


def project_trajectory(
    model: TrackModel,
    points: ArrayLike,
    *,
    initial_hint: ProjectionHint | None = None,
    config: ProjectionConfig | None = None,
) -> tuple[TrackProjection, ...]:
    """Project sequential samples, updating continuity only after accepted points."""

    array = np.asarray(points, dtype=np.float64)
    if array.ndim != 2 or array.shape[1] != 2:
        raise ValueError("points must have shape (n, 2)")
    hint = initial_hint
    output: list[TrackProjection] = []
    for point in array:
        projection = project_to_track(model, point, hint=hint, config=config)
        output.append(projection)
        if (
            projection.status is ProjectionStatus.PROJECTED
            and projection.s_m is not None
        ):
            hint = ProjectionHint(projection.s_m)
    return tuple(output)
