from __future__ import annotations

import bisect
import math
from statistics import mean
from typing import Any

from .config import settings


def _usable_points(trace: list[dict[str, Any]]) -> list[dict[str, float]]:
    points: list[dict[str, float]] = []
    for raw in trace:
        try:
            distance = float(raw.get("d", 0.0))
            x = float(raw.get("x", 0.0))
            z = float(raw.get("z", 0.0))
        except (TypeError, ValueError):
            continue
        # A valid track can cross world origin, so reject only a long sequence
        # that has never received Motion coordinates, not individual zeroes.
        points.append(
            {
                "d": distance,
                "x": x,
                "z": z,
                "t": float(raw.get("t", 0.0)),
                "speed": float(raw.get("speed", 0.0)),
                "brake": float(raw.get("brake", 0.0)),
                "throttle": float(raw.get("throttle", 0.0)),
                "steer": float(raw.get("steer", 0.0)),
            }
        )
    points.sort(key=lambda item: (item["d"], item["t"]))
    if len(points) < 20:
        return []
    span_x = max(item["x"] for item in points) - min(item["x"] for item in points)
    span_z = max(item["z"] for item in points) - min(item["z"] for item in points)
    if math.hypot(span_x, span_z) < 25.0:
        return []
    return points


def downsample_line(
    trace: list[dict[str, Any]],
    bin_m: float | None = None,
) -> list[dict[str, float]]:
    points = _usable_points(trace)
    if not points:
        return []
    bin_m = max(2.0, float(bin_m or settings.map_distance_bin_m))
    bins: dict[int, list[dict[str, float]]] = {}
    for point in points:
        key = int(point["d"] // bin_m)
        bins.setdefault(key, []).append(point)
    result: list[dict[str, float]] = []
    for key in sorted(bins):
        samples = bins[key]
        result.append(
            {
                "d": round(mean(item["d"] for item in samples), 2),
                "x": round(mean(item["x"] for item in samples), 3),
                "z": round(mean(item["z"] for item in samples), 3),
                "speed": round(mean(item["speed"] for item in samples), 1),
                "brake": round(mean(item["brake"] for item in samples), 3),
                "throttle": round(mean(item["throttle"] for item in samples), 3),
            }
        )
    return result


def _interpolate(reference: list[dict[str, float]], distance: float) -> dict[str, float]:
    distances = [point["d"] for point in reference]
    index = bisect.bisect_left(distances, distance)
    if index <= 0:
        return dict(reference[0])
    if index >= len(reference):
        return dict(reference[-1])
    left = reference[index - 1]
    right = reference[index]
    width = max(1e-6, right["d"] - left["d"])
    ratio = min(1.0, max(0.0, (distance - left["d"]) / width))
    return {
        key: left.get(key, 0.0) + ratio * (right.get(key, 0.0) - left.get(key, 0.0))
        for key in {"d", "x", "z", "speed", "brake", "throttle"}
    }


def _tangent(reference: list[dict[str, float]], index: int) -> tuple[float, float]:
    left = reference[max(0, index - 1)]
    right = reference[min(len(reference) - 1, index + 1)]
    dx = right["x"] - left["x"]
    dz = right["z"] - left["z"]
    norm = math.hypot(dx, dz)
    if norm < 1e-6:
        return 1.0, 0.0
    return dx / norm, dz / norm


def compare_lines(
    trace: list[dict[str, Any]],
    reference_trace: list[dict[str, Any]] | None,
    threshold_m: float | None = None,
) -> dict[str, Any]:
    current = downsample_line(trace)
    reference = downsample_line(reference_trace or [])
    threshold = float(threshold_m or settings.map_deviation_threshold_m)
    if not current:
        return {
            "available": False,
            "reason": "World-position telemetry is not available for this lap.",
            "current_line": [],
            "reference_line": reference,
            "zones": [],
        }
    if len(reference) < 10:
        return {
            "available": False,
            "reason": "Complete one valid reference lap to build the racing-line model.",
            "current_line": current,
            "reference_line": [],
            "zones": [],
            "reference_building": True,
        }

    ref_distances = [point["d"] for point in reference]
    samples: list[dict[str, float | str]] = []
    for point in current:
        ref = _interpolate(reference, point["d"])
        ref_index = min(
            len(reference) - 1,
            max(0, bisect.bisect_left(ref_distances, point["d"])),
        )
        tx, tz = _tangent(reference, ref_index)
        dx = point["x"] - ref["x"]
        dz = point["z"] - ref["z"]
        # Cross product of tangent and displacement. Positive is left of the
        # reference direction, negative is right.
        signed = tx * dz - tz * dx
        samples.append(
            {
                "d": point["d"],
                "signed_m": signed,
                "abs_m": abs(signed),
                "side": "left" if signed > 0 else "right",
                "speed_delta_kph": point["speed"] - ref["speed"],
                "brake_delta": point["brake"] - ref["brake"],
                "throttle_delta": point["throttle"] - ref["throttle"],
            }
        )

    zones: list[dict[str, Any]] = []
    active: list[dict[str, float | str]] = []

    def flush() -> None:
        nonlocal active
        if len(active) < 2:
            active = []
            return
        distances = [float(item["d"]) for item in active]
        abs_values = [float(item["abs_m"]) for item in active]
        signed_values = [float(item["signed_m"]) for item in active]
        speed_deltas = [float(item["speed_delta_kph"]) for item in active]
        brake_deltas = [float(item["brake_delta"]) for item in active]
        throttle_deltas = [float(item["throttle_delta"]) for item in active]
        if distances[-1] - distances[0] < max(8.0, settings.map_distance_bin_m):
            active = []
            return
        signed_mean = mean(signed_values)
        side = "left" if signed_mean > 0 else "right"
        average = mean(abs_values)
        max_dev = max(abs_values)
        avg_speed = mean(speed_deltas)
        avg_brake = mean(brake_deltas)
        avg_throttle = mean(throttle_deltas)
        if avg_brake > 0.08 and avg_speed < -3:
            cause = "braking more and overslowing versus the reference"
        elif avg_throttle < -0.10 and avg_speed < -2:
            cause = "later throttle pickup versus the reference"
        elif avg_speed < -4:
            cause = "lower minimum speed versus the reference"
        else:
            cause = "a different path through the section"
        zones.append(
            {
                "start_m": round(distances[0], 1),
                "end_m": round(distances[-1], 1),
                "center_m": round((distances[0] + distances[-1]) / 2, 1),
                "average_deviation_m": round(average, 2),
                "max_deviation_m": round(max_dev, 2),
                "side": side,
                "speed_delta_kph": round(avg_speed, 1),
                "brake_delta": round(avg_brake, 3),
                "throttle_delta": round(avg_throttle, 3),
                "cause": cause,
                "instruction": (
                    f"Around {((distances[0] + distances[-1]) / 2):.0f} m, "
                    f"you are about {average:.1f} m {side} of the reference; "
                    f"focus on matching the entry-to-apex path."
                ),
            }
        )
        active = []

    for sample in samples:
        if float(sample["abs_m"]) >= threshold:
            if active and float(sample["d"]) - float(active[-1]["d"]) > 20:
                flush()
            active.append(sample)
        else:
            flush()
    flush()

    abs_all = [float(item["abs_m"]) for item in samples]
    sorted_abs = sorted(abs_all)
    p95 = sorted_abs[min(len(sorted_abs) - 1, int(len(sorted_abs) * 0.95))]
    zones.sort(
        key=lambda item: (
            item["average_deviation_m"],
            -item["speed_delta_kph"],
        ),
        reverse=True,
    )
    score = max(0.0, 100.0 - mean(abs_all) * 18.0 - p95 * 7.0)
    return {
        "available": True,
        "reference": "personal_best",
        "current_line": current,
        "reference_line": reference,
        "mean_abs_deviation_m": round(mean(abs_all), 2),
        "p95_deviation_m": round(p95, 2),
        "max_deviation_m": round(max(abs_all), 2),
        "line_score": round(score, 1),
        "threshold_m": threshold,
        "zones": zones[:8],
        "top_opportunity": zones[0] if zones else None,
        # A bounded sample is useful to tools and review without returning the
        # full 60 Hz trace.
        "deviation_samples": [
            {
                "d": round(float(item["d"]), 1),
                "signed_m": round(float(item["signed_m"]), 2),
                "speed_delta_kph": round(float(item["speed_delta_kph"]), 1),
            }
            for item in samples[:: max(1, len(samples) // 350)]
        ],
    }
