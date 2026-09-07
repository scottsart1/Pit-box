"""Shared, deterministic evidence rules for live and persisted tyre learning.

Fuel mass and tyre age are almost perfectly correlated inside a stint. Fitting
both freely cannot identify degradation. Use the existing 0.030 s/kg fuel
prior, compare laps only within a run, and keep independent runs independent.
"""

from __future__ import annotations

import math
from statistics import median
from typing import Any

FUEL_SECONDS_PER_KG = 0.030


def finite(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def exclusion_reason(lap: dict[str, Any]) -> str | None:
    mode = str(lap.get("mode_profile") or "").lower()
    session = str(lap.get("session_type") or "").lower()
    if mode == "time_trial" or "time trial" in session:
        return "time_trial"
    if mode == "qualifying" or "qualifying" in session or "shootout" in session:
        return "qualifying"
    if not lap.get("valid") or (finite(lap.get("lap_time_ms")) or 0) <= 0:
        return "invalid_lap"
    if lap.get("pit_status") or lap.get("pit_lane_time_ms"):
        return "pit_lap"
    if lap.get("learning_exclusions"):
        return str(lap["learning_exclusions"][0])
    if lap.get("safety_car") not in (None, "none", "") or lap.get("red_flag_active"):
        return "neutralised_lap"
    if lap.get("race_control_phase") in {
        "safety_car",
        "safety_car_ending",
        "vsc",
        "vsc_ending",
        "formation",
        "red_flag",
    }:
        return "neutralised_lap"
    start, end = finite(lap.get("tyre_age_start")), finite(lap.get("tyre_age_end"))
    if start is not None and end is not None and (end < start or end - start > 1):
        return "tyre_reset_or_missing_laps"
    return None


def wear_deltas(lap: dict[str, Any]) -> list[float] | None:
    """A complete wear increment; resets and zero-only packets are not rates."""
    if exclusion_reason(lap):
        return None
    start, end = lap.get("wear_start") or [], lap.get("wear_end") or []
    if len(start) != 4 or len(end) != 4:
        return None
    values = [finite(v) for v in [*start, *end]]
    if any(v is None or not 0 <= v <= 100 for v in values):
        return None
    deltas = [b - a for a, b in zip(values[:4], values[4:])]
    # A negative corner means a set change or mismatched packets, not no wear.
    if min(deltas) < 0 or max(deltas) <= 0 or max(deltas) > 15:
        return None
    return deltas


def stint_pace_model(laps: list[dict[str, Any]]) -> dict[str, Any]:
    """Robust fuel-corrected slopes without comparisons across set changes.

    Each run has an independent intercept. A long old stint cannot dominate
    several newer runs just by contributing quadratically more lap pairs.
    Sparse, noisy or implausible fits remain unavailable, rather than being
    clamped into seemingly precise measurements.
    """
    runs: list[list[tuple[float, float]]] = []
    previous: dict[str, Any] | None = None
    previous_key: tuple[Any, ...] | None = None
    for lap in sorted(
        laps,
        key=lambda x: (
            str(x.get("session_uid", "live")),
            int(x.get("restart_epoch", 0) or 0),
            int(x.get("timeline_epoch", 0) or 0),
            int(x.get("lap_num", 0) or 0),
        ),
    ):
        key = (
            lap.get("session_uid"),
            lap.get("restart_epoch"),
            lap.get("timeline_epoch"),
            lap.get("compound"),
            lap.get("mode_profile"),
            lap.get("weather"),
            repr(sorted((lap.get("setup") or {}).items())),
        )
        age = finite(lap.get("tyre_age_end")) or 0
        previous_age = finite((previous or {}).get("tyre_age_end")) or 0
        new_run = (
            previous is None
            or key != previous_key
            or age <= previous_age
            or int(lap.get("lap_num", 0)) != int(previous.get("lap_num", 0)) + 1
        )
        if new_run:
            runs.append([])
        previous, previous_key = lap, key
        if exclusion_reason(lap):
            previous = None
            continue
        # Standing starts and cold out-laps do not measure degradation.
        if age <= 1:
            continue
        start = finite(lap.get("fuel_start_kg"))
        end = finite(lap.get("fuel_end_kg"))
        if start is None or end is None or not start >= end > 0:
            previous = None
            continue
        time_s = float(lap["lap_time_ms"]) / 1000
        corrected = time_s - (start + end) / 2 * FUEL_SECONDS_PER_KG
        runs[-1].append((age, corrected))

    slopes, errors, counts, spans, spreads = [], [], [], [], []
    for points in runs:
        if len(points) < 3 or points[-1][0] - points[0][0] < 2:
            continue
        pairs = [
            (y2 - y1) / (x2 - x1)
            for i, (x1, y1) in enumerate(points)
            for x2, y2 in points[i + 1 :]
            if x2 - x1 >= 2
        ]
        slope = float(median(pairs))
        intercept = median([y - slope * x for x, y in points])
        error = float(median([abs(y - (intercept + slope * x)) for x, y in points]))
        spread = float(median([abs(v - slope) for v in pairs]))
        if not -0.1 <= slope <= 1.5 or error > 1.0 or spread > 0.30:
            continue
        slopes.append(slope)
        errors.append(error)
        spreads.append(spread)
        counts.append(len(points))
        spans.append(points[-1][0] - points[0][0])
    return {
        "sample_size": sum(counts),
        "stint_count": len(slopes),
        "age_span_laps": max(spans, default=0),
        "slope_s_per_lap": round(float(median(slopes)), 4) if slopes else None,
        "fit_error_s": round(float(median(errors)), 4) if errors else None,
        "slope_spread_s_per_lap": round(
            max(
                float(median(spreads)),
                float(median([abs(s - median(slopes)) for s in slopes])),
            ),
            4,
        )
        if slopes
        else None,
        "fuel_correction_s_per_kg": FUEL_SECONDS_PER_KG,
        "source": "fuel_corrected_stint_fit",
    }
