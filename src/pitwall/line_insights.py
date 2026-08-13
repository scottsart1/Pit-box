"""Per-turn racing-line analysis from the driver's own recorded laps.

Sibling of setup_insights: pure and synchronous so it tests without a
database. Where setup_insights answers "what should the CAR change", this
module answers "where should the LINE change" — it compares the geometry of
the driver's quick passes through a turn against their slow ones and says,
in metres and track direction, what the quick ones do differently.

Everything is self-relative. No published track model or track-edge data
exists for most circuits, so the reference line is the median line of the
driver's own fastest passes through each canonical turn window — advice is
"do what your quick laps already do", never absolute track geometry.

Sign convention: lateral offsets are signed positive to the LEFT of the
direction of travel (cross product of the reference tangent with the
displacement), matching racing_line.compare_lines.
"""

from __future__ import annotations

import math
from statistics import median
from typing import Any

from .setup_insights import clean_trace

# A turn needs enough passes to split into quick and slow groups before any
# geometric claim is honest.
MIN_INSTANCES = 6
# Below this time spread the "slow" passes are not meaningfully slow.
MIN_TIME_SPREAD_S = 0.15
# Stations along the window every STEP_M metres.
STEP_M = 4.0
# A sampling hole bigger than this inside a window means the recording
# dropped out; the pass is skipped rather than interpolated across.
MAX_GAP_M = 40.0
# The geometric signature must point the same way on most slow passes,
# otherwise the loss is not a line problem and we say so.
SIGN_CONSISTENCY_MIN = 0.6
# Offsets smaller than this are line noise (the datum check on real Vegas
# laps showed ~2 m median spread between clean passes).
MIN_OFFSET_M = 1.2


def _interp(x0: float, y0: float, x1: float, y1: float, x: float) -> float:
    if x1 == x0:
        return y0
    frac = (x - x0) / (x1 - x0)
    return y0 + (y1 - y0) * frac


def resample_window(
    points: list[dict[str, Any]],
    start_m: float,
    end_m: float,
    step_m: float = STEP_M,
) -> dict[str, list[float]] | None:
    """Resample x/z/speed/t at fixed stations inside [start_m, end_m].

    Returns None when the window is not covered or has a sampling hole —
    a partial line would attribute recording gaps to the driver.
    """
    window = [p for p in points if start_m - step_m <= float(p["d"]) <= end_m + step_m]
    if len(window) < 3:
        return None
    if float(window[0]["d"]) > start_m or float(window[-1]["d"]) < end_m:
        return None
    for a, b in zip(window, window[1:]):
        if float(b["d"]) - float(a["d"]) > MAX_GAP_M:
            return None
    if any(p.get("x") is None or p.get("z") is None for p in window):
        return None
    stations: list[float] = []
    xs: list[float] = []
    zs: list[float] = []
    speeds: list[float] = []
    times: list[float] = []
    s = start_m
    index = 0
    while s <= end_m + 1e-9:
        while index < len(window) - 2 and float(window[index + 1]["d"]) < s:
            index += 1
        a, b = window[index], window[index + 1]
        d0, d1 = float(a["d"]), float(b["d"])
        stations.append(s)
        xs.append(_interp(d0, float(a["x"]), d1, float(b["x"]), s))
        zs.append(_interp(d0, float(a["z"]), d1, float(b["z"]), s))
        speeds.append(_interp(d0, float(a.get("speed", 0)), d1, float(b.get("speed", 0)), s))
        times.append(_interp(d0, float(a["t"]), d1, float(b["t"]), s))
        s += step_m
    return {"s": stations, "x": xs, "z": zs, "speed": speeds, "t": times}


def signed_offsets(
    reference: dict[str, list[float]],
    candidate: dict[str, list[float]],
) -> list[float]:
    """Per-station lateral offset of candidate vs the reference line.

    Positive = candidate is LEFT of the reference direction of travel.
    """
    n = min(len(reference["s"]), len(candidate["s"]))
    offsets: list[float] = []
    for i in range(n):
        j = min(i + 1, n - 1)
        k = max(i - 1, 0)
        tx = reference["x"][j] - reference["x"][k]
        tz = reference["z"][j] - reference["z"][k]
        norm = math.hypot(tx, tz)
        if norm < 1e-9:
            offsets.append(0.0)
            continue
        tx, tz = tx / norm, tz / norm
        dx = candidate["x"][i] - reference["x"][i]
        dz = candidate["z"][i] - reference["z"][i]
        offsets.append(tx * dz - tz * dx)
    return offsets


def _median_line(instances: list[dict[str, list[float]]]) -> dict[str, list[float]]:
    length = min(len(inst["s"]) for inst in instances)
    return {
        key: [
            median(inst[key][i] for inst in instances) for i in range(length)
        ]
        for key in ("s", "x", "z", "speed", "t")
    }


def _phase_for(station_index: int, station_count: int) -> str:
    third = max(1, station_count // 3)
    if station_index < third:
        return "entry"
    if station_index >= station_count - third:
        return "exit"
    return "apex"


def analyze_turn_line(
    turn: dict[str, Any],
    instances: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """One turn's line finding, or None when no honest geometric claim exists.

    ``instances`` are {"lap": ..., "window": resampled dict, "time_s": float}.
    The fastest third (min 2) form the reference group; their median line is
    "what your quick laps do". The claim requires the slow passes to sit on
    the same side of that line at the decisive station on most passes.
    """
    usable = [i for i in instances if i.get("window")]
    if len(usable) < MIN_INSTANCES:
        return None
    usable.sort(key=lambda i: i["time_s"])
    fast_count = max(2, len(usable) // 3)
    fast, slow = usable[:fast_count], usable[fast_count:]
    best_median_time = median(i["time_s"] for i in fast)
    loss = median(i["time_s"] for i in slow) - best_median_time
    if loss < MIN_TIME_SPREAD_S:
        return None

    reference = _median_line([i["window"] for i in fast])
    slow_offsets = [signed_offsets(reference, i["window"]) for i in slow]
    station_count = min(len(o) for o in slow_offsets)
    if station_count < 3:
        return None
    med_offsets = [
        median(offsets[i] for offsets in slow_offsets)
        for i in range(station_count)
    ]
    peak_index = max(range(station_count), key=lambda i: abs(med_offsets[i]))
    peak_offset = med_offsets[peak_index]
    if abs(peak_offset) < MIN_OFFSET_M:
        return None
    same_side = sum(
        1
        for offsets in slow_offsets
        if offsets[peak_index] * peak_offset > 0
    )
    consistency = same_side / len(slow_offsets)
    if consistency < SIGN_CONSISTENCY_MIN:
        return None

    phase = _phase_for(peak_index, station_count)
    station_m = reference["s"][min(peak_index, len(reference["s"]) - 1)]
    apex_index = min(
        range(len(reference["speed"])), key=lambda i: reference["speed"][i]
    )
    fast_apex = reference["speed"][apex_index]
    slow_apex = median(
        i["window"]["speed"][min(apex_index, len(i["window"]["speed"]) - 1)]
        for i in slow
    )

    # The slow passes sit peak_offset to the left(+)/right(-) of the quick
    # line, so the correction moves the OTHER way.
    side = "right" if peak_offset > 0 else "left"
    magnitude = abs(peak_offset)
    if phase == "entry":
        advice = (
            f"Your quick laps enter about {magnitude:.1f} m further {side} — "
            "use that road before turn-in."
        )
    elif phase == "exit":
        advice = (
            f"Your quick laps release about {magnitude:.1f} m further {side} on "
            "exit — open the corner and carry the speed out."
        )
    else:
        advice = (
            f"Your quick laps run about {magnitude:.1f} m further {side} at the "
            "apex."
        )
    return {
        "turn": int(turn["turn"]),
        "label": f"Turn {int(turn['turn'])}",
        "apex_m": float(turn["apex_m"]),
        "phase": phase,
        "station_m": round(float(station_m), 1),
        "offset_m": round(float(peak_offset), 2),
        "median_loss_s": round(float(loss), 3),
        "samples_fast": len(fast),
        "samples_slow": len(slow),
        "sign_consistency": round(consistency, 2),
        "apex_speed_fast_kph": round(float(fast_apex), 1),
        "apex_speed_slow_kph": round(float(slow_apex), 1),
        "advice": advice,
    }


def line_findings(
    turn_model: list[dict[str, Any]],
    laps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """All line findings for a track.

    ``laps`` are {"session_uid", "lap_num", "trace": legacy point list}.
    Passes through each canonical turn are timed with the same interpolated
    fixed windows the setup analysis uses, so both features agree about how
    much time a turn costs.
    """
    if not turn_model:
        return []
    cleaned = [
        {**lap, "points": clean_trace(lap.get("trace") or [])} for lap in laps
    ]
    findings: list[dict[str, Any]] = []
    for turn in turn_model:
        start_m = float(turn["entry_m"])
        end_m = float(turn["exit_m"])
        instances = []
        for lap in cleaned:
            if len(lap["points"]) < 20:
                continue
            window = resample_window(lap["points"], start_m, end_m)
            if window is None:
                continue
            time_s = window["t"][-1] - window["t"][0]
            if time_s <= 0:
                continue
            instances.append(
                {
                    "lap": (lap.get("session_uid"), lap.get("lap_num")),
                    "window": window,
                    "time_s": time_s,
                }
            )
        finding = analyze_turn_line(turn, instances)
        if finding:
            findings.append(finding)
    findings.sort(key=lambda f: f["median_loss_s"], reverse=True)
    return findings
