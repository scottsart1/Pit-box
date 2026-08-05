"""Stable segment models and uncertainty-aware phase-event detection."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
from numpy.typing import ArrayLike, NDArray


class DetectionStatus(StrEnum):
    DETECTED = "detected"
    NOT_DETECTED = "not_detected"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True, slots=True, order=True)
class DistanceWindow:
    start_m: float
    end_m: float

    def __post_init__(self) -> None:
        if not np.isfinite(self.start_m) or not np.isfinite(self.end_m):
            raise ValueError("window bounds must be finite")
        if self.end_m <= self.start_m:
            raise ValueError("window end must be greater than start")

    @property
    def length_m(self) -> float:
        return self.end_m - self.start_m

    def contains(self, distance_m: float) -> bool:
        return self.start_m <= distance_m <= self.end_m


@dataclass(frozen=True, slots=True)
class PhaseEvent:
    kind: str
    status: DetectionStatus
    distance_m: float | None = None
    range_m: tuple[float, float] | None = None
    value: float | None = None
    confidence: float = 0.0
    evidence_sample_range: tuple[int, int] | None = None
    algorithm_version: str = "sustained_event_v1"
    reason: str | None = None


def _event_confidence(
    values: NDArray[np.float64],
    threshold: float,
    duration_m: float,
    required_duration_m: float,
    coverage: float,
) -> float:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return 0.0
    scale = max(abs(threshold), 0.05)
    margin = float(np.median(np.abs(finite - threshold))) / scale
    margin_score = min(1.0, 0.55 + margin)
    duration_score = min(1.0, duration_m / max(required_duration_m * 2.0, 1e-9))
    return max(0.0, min(1.0, coverage * margin_score * (0.65 + 0.35 * duration_score)))


def detect_sustained_event(
    distance_m: ArrayLike,
    signal: ArrayLike,
    *,
    kind: str,
    enter_threshold: float,
    exit_threshold: float | None = None,
    direction: str = "above",
    min_duration_m: float = 2.0,
    merge_gap_m: float = 0.75,
    search_window: DistanceWindow | tuple[float, float] | None = None,
    select: str = "first",
    algorithm_version: str = "sustained_event_v1",
) -> PhaseEvent:
    """Detect a debounced event using entry/exit hysteresis.

    The returned range describes the full sustained activation. A single noisy
    sample cannot form an event because its distance duration is zero.
    """

    distance = np.asarray(distance_m, dtype=np.float64)
    values = np.asarray(signal, dtype=np.float64)
    if distance.ndim != 1 or values.ndim != 1 or distance.size != values.size:
        raise ValueError("distance and signal must be equal-length one-dimensional arrays")
    if direction not in {"above", "below"}:
        raise ValueError("direction must be above or below")
    if select not in {"first", "last", "strongest"}:
        raise ValueError("select must be first, last, or strongest")
    if distance.size < 2:
        return PhaseEvent(kind, DetectionStatus.INSUFFICIENT_DATA, reason="fewer than two samples")

    if search_window is not None:
        window = (
            search_window
            if isinstance(search_window, DistanceWindow)
            else DistanceWindow(*search_window)
        )
        in_window = (distance >= window.start_m) & (distance <= window.end_m)
    else:
        in_window = np.ones(distance.shape, dtype=bool)
    eligible = in_window & np.isfinite(distance) & np.isfinite(values)
    indices = np.flatnonzero(eligible)
    if indices.size < 2:
        return PhaseEvent(kind, DetectionStatus.INSUFFICIENT_DATA, reason="insufficient finite coverage")

    if np.any(np.diff(distance[indices]) < 0):
        raise ValueError("distance must be monotonic before event detection")
    exit_value = enter_threshold if exit_threshold is None else float(exit_threshold)
    if direction == "above" and exit_value > enter_threshold:
        raise ValueError("above-threshold exit must be <= entry threshold")
    if direction == "below" and exit_value < enter_threshold:
        raise ValueError("below-threshold exit must be >= entry threshold")

    enters = lambda value: value >= enter_threshold if direction == "above" else value <= enter_threshold
    exits = lambda value: value <= exit_value if direction == "above" else value >= exit_value
    runs: list[tuple[int, int]] = []
    active_start: int | None = None
    last_active: int | None = None
    gap_start_distance: float | None = None
    for idx in indices:
        value = float(values[idx])
        if active_start is None:
            if enters(value):
                active_start = int(idx)
                last_active = int(idx)
                gap_start_distance = None
            continue
        if not exits(value):
            last_active = int(idx)
            gap_start_distance = None
            continue
        if gap_start_distance is None:
            gap_start_distance = float(distance[idx])
        if float(distance[idx]) - gap_start_distance <= merge_gap_m:
            continue
        assert last_active is not None
        runs.append((active_start, last_active))
        active_start = None
        last_active = None
        gap_start_distance = None
    if active_start is not None and last_active is not None:
        runs.append((active_start, last_active))

    sustained = [
        run
        for run in runs
        if float(distance[run[1]] - distance[run[0]]) >= min_duration_m
    ]
    if not sustained:
        return PhaseEvent(kind, DetectionStatus.NOT_DETECTED, reason="no sustained threshold crossing")
    if select == "last":
        start, end = sustained[-1]
    elif select == "strongest":
        reducer = np.nanmax if direction == "above" else np.nanmin
        start, end = max(
            sustained,
            key=lambda run: abs(float(reducer(values[run[0] : run[1] + 1])) - enter_threshold),
        )
    else:
        start, end = sustained[0]
    event_slice = values[start : end + 1]
    peak = float(np.nanmax(event_slice) if direction == "above" else np.nanmin(event_slice))
    window_count = max(1, int(np.count_nonzero(in_window)))
    coverage = float(indices.size / window_count)
    duration = float(distance[end] - distance[start])
    confidence = _event_confidence(
        event_slice,
        float(enter_threshold),
        duration,
        min_duration_m,
        coverage,
    )
    return PhaseEvent(
        kind=kind,
        status=DetectionStatus.DETECTED,
        distance_m=float(distance[start]),
        range_m=(float(distance[start]), float(distance[end])),
        value=peak,
        confidence=confidence,
        evidence_sample_range=(start, end),
        algorithm_version=algorithm_version,
    )


def detect_extremum_event(
    distance_m: ArrayLike,
    signal: ArrayLike,
    *,
    kind: str,
    mode: str = "minimum",
    search_window: DistanceWindow | tuple[float, float] | None = None,
    plateau_tolerance: float = 0.2,
    algorithm_version: str = "extremum_event_v1",
) -> PhaseEvent:
    """Locate a minimum/maximum and retain the uncertainty plateau around it."""

    distance = np.asarray(distance_m, dtype=np.float64)
    values = np.asarray(signal, dtype=np.float64)
    if distance.ndim != 1 or values.ndim != 1 or distance.size != values.size:
        raise ValueError("distance and signal must be equal-length one-dimensional arrays")
    if mode not in {"minimum", "maximum"}:
        raise ValueError("mode must be minimum or maximum")
    if search_window is None:
        in_window = np.ones(distance.shape, dtype=bool)
    else:
        window = search_window if isinstance(search_window, DistanceWindow) else DistanceWindow(*search_window)
        in_window = (distance >= window.start_m) & (distance <= window.end_m)
    eligible = in_window & np.isfinite(distance) & np.isfinite(values)
    indices = np.flatnonzero(eligible)
    if indices.size < 3:
        return PhaseEvent(kind, DetectionStatus.INSUFFICIENT_DATA, reason="fewer than three finite samples")
    local = values[indices]
    extreme = float(np.min(local) if mode == "minimum" else np.max(local))
    close = (
        local <= extreme + plateau_tolerance
        if mode == "minimum"
        else local >= extreme - plateau_tolerance
    )
    close_indices = indices[close]
    center = int(close_indices[len(close_indices) // 2])
    first, last = int(close_indices[0]), int(close_indices[-1])
    spread = max(float(np.nanmax(local) - np.nanmin(local)), plateau_tolerance)
    prominence = min(1.0, abs(float(np.nanmedian(local)) - extreme) / spread + 0.5)
    coverage = indices.size / max(1, np.count_nonzero(in_window))
    return PhaseEvent(
        kind=kind,
        status=DetectionStatus.DETECTED,
        distance_m=float(distance[center]),
        range_m=(float(distance[first]), float(distance[last])),
        value=extreme,
        confidence=max(0.0, min(1.0, coverage * prominence)),
        evidence_sample_range=(first, last),
        algorithm_version=algorithm_version,
    )


@dataclass(frozen=True, slots=True)
class Segment:
    id: str
    label: str
    ordinal: int
    start_m: float
    end_m: float
    brake_window: DistanceWindow | None = None
    turn_in_window: DistanceWindow | None = None
    apex_window: DistanceWindow | None = None
    exit_window: DistanceWindow | None = None
    direction: str | None = None
    confidence: float = 1.0
    source: str = "auto_v1"

    def __post_init__(self) -> None:
        if self.end_m <= self.start_m:
            raise ValueError("segment end must be greater than start")
        if self.ordinal < 0:
            raise ValueError("segment ordinal cannot be negative")
        if self.direction not in {None, "left", "right", "straight", "complex"}:
            raise ValueError("invalid segment direction")
        object.__setattr__(self, "confidence", max(0.0, min(1.0, self.confidence)))
        for window in (
            self.brake_window,
            self.turn_in_window,
            self.apex_window,
            self.exit_window,
        ):
            if window and (window.start_m < self.start_m or window.end_m > self.end_m):
                raise ValueError("phase windows must lie inside the segment")


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return normalized[:24] or "track"


def stable_segment_id(
    track_key: str,
    ordinal: int,
    start_m: float,
    end_m: float,
    *,
    quantization_m: float = 5.0,
) -> str:
    """Generate a deterministic ID independent of a segment's display label."""

    if quantization_m <= 0:
        raise ValueError("quantization_m must be positive")
    start_bin = round(float(start_m) / quantization_m)
    end_bin = round(float(end_m) / quantization_m)
    seed = f"{track_key}|{int(ordinal)}|{start_bin}|{end_bin}".encode()
    digest = hashlib.blake2s(seed, digest_size=5).hexdigest()
    return f"seg_{_slug(track_key)}_{int(ordinal):02d}_{digest}"


def make_segment(
    track_key: str,
    ordinal: int,
    label: str,
    start_m: float,
    end_m: float,
    *,
    phases: Mapping[str, DistanceWindow | tuple[float, float]] | None = None,
    direction: str | None = None,
    confidence: float = 1.0,
    source: str = "auto_v1",
) -> Segment:
    normalized: dict[str, DistanceWindow] = {}
    for name, window in (phases or {}).items():
        normalized[name] = window if isinstance(window, DistanceWindow) else DistanceWindow(*window)
    return Segment(
        id=stable_segment_id(track_key, ordinal, start_m, end_m),
        label=label,
        ordinal=ordinal,
        start_m=float(start_m),
        end_m=float(end_m),
        brake_window=normalized.get("brake"),
        turn_in_window=normalized.get("turn_in"),
        apex_window=normalized.get("apex"),
        exit_window=normalized.get("exit"),
        direction=direction,
        confidence=confidence,
        source=source,
    )


@dataclass(frozen=True, slots=True)
class SegmentModel:
    id: str
    track_model_id: str
    version: int
    source: str
    segments: tuple[Segment, ...]
    checksum: str


def build_segment_model(
    track_model_id: str,
    version: int,
    segments: Iterable[Segment],
    *,
    source: str = "auto_v1",
) -> SegmentModel:
    ordered = tuple(sorted(segments, key=lambda segment: (segment.ordinal, segment.start_m)))
    if not ordered:
        raise ValueError("a segment model requires at least one segment")
    if len({segment.id for segment in ordered}) != len(ordered):
        raise ValueError("segment IDs must be unique")
    if len({segment.ordinal for segment in ordered}) != len(ordered):
        raise ValueError("segment ordinals must be unique")
    payload = [
        {
            "id": segment.id,
            "ordinal": segment.ordinal,
            "start_m": round(segment.start_m, 6),
            "end_m": round(segment.end_m, 6),
            "source": segment.source,
        }
        for segment in ordered
    ]
    checksum = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    model_id = f"segments_{_slug(track_model_id)}_v{int(version)}_{checksum[:10]}"
    return SegmentModel(model_id, track_model_id, int(version), source, ordered, checksum)
