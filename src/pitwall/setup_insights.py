"""Per-corner causal setup analysis from the driver's own recorded laps.

Everything here is pure and synchronous so it can be tested without a
database: the advisor loads corner rows and traces, this module turns them
into findings.

The causal chain each finding states: WHERE the driver loses time (a turn,
clustered across laps by apex distance), HOW MUCH (median seconds versus
their own best pass through that same turn), WHY (a mechanism read from the
telemetry — which axle locked, where the wheelspin happened, how the entry
compared with the best pass), and WHAT to change (a setup lever only when
the mechanism is one setup can address — braking earlier than your best pass
is driving, and is said so, not converted into wing angle).
"""

from __future__ import annotations

from statistics import median
from typing import Any

# A turn must show at least this many measured passes before it can make a
# claim about the driver, and at least this much repeatable loss before the
# claim is worth acting on.
MIN_SAMPLES = 4
MIN_MEDIAN_LOSS_S = 0.12

# Corners of one lap whose apexes fall within this distance of a cluster's
# mean apex are the same physical turn. Detected apexes for the same corner
# wander by a few metres lap to lap; distinct corners are rarely this close.
CLUSTER_TOLERANCE_M = 60.0

SLOW_APEX_KPH = 120.0
FAST_APEX_KPH = 200.0

# Net accumulation limits per lever across ALL findings in one pass, on top
# of the advisor's absolute clamps. One analysis run must stay a nudge, not
# a redesign of the car.
NET_LIMITS: dict[str, float] = {
    "brake_bias": 2,
    "brake_pressure": 4,
    "on_throttle": 6,
    "front_wing": 2,
    "rear_wing": 2,
    "front_anti_roll_bar": 2,
    "rear_anti_roll_bar": 2,
}

MECHANISM_LABELS: dict[str, str] = {
    "entry-lockup": "wheels locking on entry",
    "exit-wheelspin": "wheelspin on exit",
    "mid-corner-grip": "missing mid-corner grip",
    "hesitant-exit": "late back to power",
    "overslowed-entry": "braking earlier than your best pass",
    "overshot-entry": "braking past your best pass and missing the apex",
    "inconsistent-line": "lap-to-lap line variance",
}

# Mechanisms a setup change can actually address. The rest are driving and
# are reported as coaching notes so the advisor never "fixes" technique with
# hardware.
SETUP_MECHANISMS = frozenset(
    {"entry-lockup", "exit-wheelspin", "mid-corner-grip", "hesitant-exit"}
)


def clean_trace(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order a lap trace by distance and normalise its units.

    Laps recorded before the milli-g motion fix carry lat_g/long_g a thousand
    times too large (int16 milli-g forwarded as g). No car corners at 30 g,
    so the peak tells the populations apart. Copies, never in-place edits —
    trace points are shared with the live state.
    """
    points = [
        point
        for point in trace
        if point.get("d") is not None and point.get("t") is not None
    ]
    points.sort(key=lambda item: (float(item["d"]), float(item["t"])))
    cleaned: list[dict[str, Any]] = []
    last_distance = -1.0
    for point in points:
        distance = float(point["d"])
        if distance + 5 < last_distance:
            continue
        cleaned.append(point)
        last_distance = max(last_distance, distance)
    peak = max((abs(float(p.get("lat_g", 0.0))) for p in cleaned), default=0.0)
    if peak > 30.0:
        cleaned = [
            {
                **point,
                "lat_g": float(point.get("lat_g", 0.0)) / 1000.0,
                "long_g": float(point.get("long_g", 0.0)) / 1000.0,
            }
            for point in cleaned
        ]
    return cleaned


def reference_deltas(
    metric: dict[str, Any], reference: dict[str, Any]
) -> dict[str, float]:
    """Signed differences from a reference pass through the same corner.

    Positive brake delta means the brake point was later than the reference;
    negative apex-speed delta means slower through the apex.
    """
    return {
        "brake_point_delta_m": round(
            float(metric.get("brake_point_m", 0) or 0)
            - float(reference.get("brake_point_m", 0) or 0),
            1,
        ),
        "apex_speed_delta_kph": round(
            float(metric.get("min_speed_kph", 0) or 0)
            - float(reference.get("min_speed_kph", 0) or 0),
            1,
        ),
        "throttle_on_delta_m": round(
            float(metric.get("throttle_on_m", 0) or 0)
            - float(reference.get("throttle_on_m", 0) or 0),
            1,
        ),
    }


def classify_cause(
    metric: dict[str, Any],
    reference: dict[str, Any],
    deltas: dict[str, float] | None = None,
) -> str:
    if metric.get("wheel_lock"):
        return "lock-up"
    if metric.get("wheelspin"):
        return "wheelspin"
    resolved = deltas or reference_deltas(metric, reference)
    brake_delta = resolved["brake_point_delta_m"]
    speed_delta = resolved["apex_speed_delta_kph"]
    throttle_delta = resolved["throttle_on_delta_m"]
    if brake_delta < -8:
        return "early brake"
    if brake_delta > 12 and speed_delta < -5:
        return "late brake / overslow"
    if speed_delta < -5:
        return "low apex speed"
    if throttle_delta > 10:
        return "late throttle"
    return "line or minimum-speed loss"


# ---------------------------------------------------------------------------
# Canonical turn models.
#
# Zone detection finds where a lap's cornering activity happened, but zone
# EXTENTS vary lap to lap — a lift extends a zone, close corners merge on one
# lap and split on the next. Comparing time across differently-sized windows
# measures the segmentation, not the driver (a real Vegas cluster showed a
# "median 14.3 s vs best 6.6 s corner" that was purely extent variance). A
# turn model freezes one window per physical turn so every lap is measured
# over identical ground.
# ---------------------------------------------------------------------------

# A turn must have been detected on this share of laps to be part of the
# model — zones that appear on one lap out of twenty are offs, not corners.
MODEL_SUPPORT_SHARE = 0.25
MODEL_MIN_LAPS = 8


def build_turn_model(
    zone_rows_per_lap: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Derive canonical turn windows from many laps' detected zones.

    Returns [{"turn", "entry_m", "apex_m", "exit_m", "support"}] ordered by
    track position, with overlapping windows clipped at apex midpoints so
    every metre belongs to at most one turn. Empty when there are not enough
    laps to trust the shape.
    """
    lap_count = len([rows for rows in zone_rows_per_lap if rows])
    if lap_count < MODEL_MIN_LAPS:
        return []
    flat = [row for rows in zone_rows_per_lap for row in rows]
    clusters = cluster_turns(flat)
    required = max(2, int(lap_count * MODEL_SUPPORT_SHARE))
    turns: list[dict[str, Any]] = []
    for cluster in clusters:
        rows = cluster["rows"]
        if len(rows) < required:
            continue
        entry = float(median(float(r["entry_m"]) for r in rows))
        apex = float(median(float(r["apex_m"]) for r in rows))
        exit_ = float(median(float(r["exit_m"]) for r in rows))
        if exit_ <= entry:
            continue
        turns.append({"entry_m": entry, "apex_m": apex, "exit_m": exit_,
                      "support": len(rows)})
    turns.sort(key=lambda t: t["apex_m"])
    for previous, current in zip(turns, turns[1:]):
        if current["entry_m"] < previous["exit_m"]:
            midpoint = (previous["apex_m"] + current["apex_m"]) / 2.0
            previous["exit_m"] = min(previous["exit_m"], midpoint)
            current["entry_m"] = max(current["entry_m"], midpoint)
    turns = [t for t in turns if t["exit_m"] > t["entry_m"]]
    for number, turn in enumerate(turns, 1):
        turn["turn"] = number
        for key in ("entry_m", "apex_m", "exit_m"):
            turn[key] = round(turn[key], 1)
    return turns


def _interpolated_time(
    points: list[dict[str, Any]], boundary_m: float
) -> float | None:
    """Session time at an exact distance, linearly interpolated."""
    for before, after in zip(points, points[1:]):
        d0, d1 = float(before["d"]), float(after["d"])
        if d0 <= boundary_m <= d1:
            if d1 == d0:
                return float(before["t"])
            fraction = (boundary_m - d0) / (d1 - d0)
            t0, t1 = float(before["t"]), float(after["t"])
            return t0 + fraction * (t1 - t0)
    return None


# The largest sampling hole tolerated inside a measured window. The trace
# thins to a point every ~0.5-7 m; a bigger gap means the recording dropped
# out and the window time would be an interpolation artifact.
MAX_SAMPLE_GAP_M = 40.0


def measure_turns(
    trace: list[dict[str, Any]],
    model: list[dict[str, Any]],
    pb_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Measure one lap over a track's canonical turn windows.

    Every returned row covers exactly the model's window, so times are
    comparable across laps. Turns the lap does not fully cover (pit entry,
    recording holes) are skipped rather than guessed. When pb_rows (canonical
    rows of the personal-best lap) are given, loss and cause are attached the
    same way the zone path does it.
    """
    points = clean_trace(trace)
    if len(points) < 20 or not model:
        return []
    pb_by_turn = {
        int(row.get("corner_no", 0)): row for row in (pb_rows or [])
    }
    metrics: list[dict[str, Any]] = []
    for turn in model:
        entry = float(turn["entry_m"])
        exit_ = float(turn["exit_m"])
        t_entry = _interpolated_time(points, entry)
        t_exit = _interpolated_time(points, exit_)
        if t_entry is None or t_exit is None or t_exit <= t_entry:
            continue
        window = [p for p in points if entry <= float(p["d"]) <= exit_]
        if len(window) < 3:
            continue
        distances = [float(p["d"]) for p in window]
        gaps = [b - a for a, b in zip(distances, distances[1:])]
        if gaps and max(gaps) > MAX_SAMPLE_GAP_M:
            continue
        apex_point = min(window, key=lambda p: float(p.get("speed", 9999)))
        apex_index = window.index(apex_point)
        braking = [p for p in window if float(p.get("brake", 0)) >= 0.1]
        after_apex = window[apex_index:]
        throttle_on = next(
            (p for p in after_apex if float(p.get("throttle", 0)) >= 0.25),
            window[-1],
        )
        full_throttle = next(
            (p for p in after_apex if float(p.get("throttle", 0)) >= 0.95),
            window[-1],
        )
        wheel_lock = any(
            float(p.get("brake", 0)) > 0.2
            and any(float(v) < -0.15 for v in p.get("slip", []))
            for p in window
        )
        wheelspin = any(
            float(p.get("throttle", 0)) > 0.6
            and any(float(v) > 0.15 for v in p.get("slip", []))
            for p in window
        )
        number = int(turn["turn"])
        metric: dict[str, Any] = {
            "corner_no": number,
            "name": f"Turn {number}",
            "entry_m": round(entry, 1),
            "apex_m": round(float(turn["apex_m"]), 1),
            "exit_m": round(exit_, 1),
            "brake_point_m": (
                round(float(braking[0]["d"]), 1) if braking else None
            ),
            "brake_peak": round(
                max(float(p.get("brake", 0)) for p in window), 3
            ),
            "min_speed_kph": round(float(apex_point.get("speed", 0)), 1),
            "apex_speed_kph": round(float(apex_point.get("speed", 0)), 1),
            "throttle_on_m": round(float(throttle_on["d"]), 1),
            "full_throttle_m": round(float(full_throttle["d"]), 1),
            "gear_at_apex": int(apex_point.get("gear", 0)),
            "max_lat_g": round(
                max(abs(float(p.get("lat_g", 0))) for p in window), 2
            ),
            "wheel_lock": wheel_lock,
            "wheelspin": wheelspin,
            "time_in_corner_s": round(t_exit - t_entry, 3),
            "loss_vs_pb_s": None,
            "cause": "",
        }
        reference = pb_by_turn.get(number)
        if reference and float(reference.get("time_in_corner_s") or 0) > 0:
            loss = metric["time_in_corner_s"] - float(
                reference["time_in_corner_s"]
            )
            metric["loss_vs_pb_s"] = round(loss, 3)
            deltas = reference_deltas(metric, reference)
            metric.update(deltas)
            metric["cause"] = classify_cause(metric, reference, deltas)
            if reference.get("name"):
                metric["reference_name"] = reference["name"]
        elif wheel_lock:
            metric["cause"] = "lock-up"
        elif wheelspin:
            metric["cause"] = "wheelspin"
        metrics.append(metric)
    return metrics


def cluster_turns(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group corner rows from many laps into physical turns.

    Rows must carry apex_m. Returns clusters ordered by track position, each
    {"turn": n, "apex_m": mean, "rows": [...]}. Per-lap corner_no is useless
    for identity (segmentation can split or merge zones between laps), so
    identity is apex distance.
    """
    usable = [row for row in rows if row.get("apex_m") is not None]
    ordered = sorted(usable, key=lambda row: float(row["apex_m"]))
    clusters: list[dict[str, Any]] = []
    for row in ordered:
        apex = float(row["apex_m"])
        if clusters:
            current = clusters[-1]
            mean_apex = current["apex_m"]
            if abs(apex - mean_apex) <= CLUSTER_TOLERANCE_M:
                current["rows"].append(row)
                current["apex_m"] = float(
                    sum(float(r["apex_m"]) for r in current["rows"])
                    / len(current["rows"])
                )
                continue
        clusters.append({"turn": 0, "apex_m": apex, "rows": [row]})
    for index, cluster in enumerate(clusters, 1):
        cluster["turn"] = index
    return clusters


def _speed_class(apex_speed_kph: float) -> str:
    if apex_speed_kph < SLOW_APEX_KPH:
        return "slow"
    if apex_speed_kph < FAST_APEX_KPH:
        return "medium"
    return "fast"


def analyze_turn(cluster: dict[str, Any]) -> dict[str, Any] | None:
    """Measure one turn against the driver's own best pass through it."""
    instances = [
        row
        for row in cluster["rows"]
        if float(row.get("time_in_corner_s") or 0.0) > 0.0
    ]
    if len(instances) < MIN_SAMPLES:
        return None
    best = min(instances, key=lambda row: float(row["time_in_corner_s"]))
    others = [row for row in instances if row is not best]
    losses = [
        float(row["time_in_corner_s"]) - float(best["time_in_corner_s"])
        for row in others
    ]
    median_loss = float(median(losses))
    if median_loss < MIN_MEDIAN_LOSS_S:
        return None

    lock_events = sum(1 for row in instances if row.get("wheel_lock"))
    spin_events = sum(1 for row in instances if row.get("wheelspin"))
    lock_rate = lock_events / len(instances)
    spin_rate = spin_events / len(instances)

    def _field_delta(field: str, ceiling_m: float | None = None) -> float | None:
        def _usable(row: dict[str, Any]) -> float | None:
            value = row.get(field)
            if value is None:
                return None
            value = float(value)
            if ceiling_m is not None and value > ceiling_m:
                return None
            return value

        best_value = _usable(best)
        usable = [v for v in (_usable(row) for row in others) if v is not None]
        if best_value is None or len(usable) < 3:
            return None
        return float(median(usable)) - best_value

    # Brake points past the apex are incidental brushes (stabilising for the
    # NEXT corner inside a long window), not braking for this one; comparing
    # them fabricates hundred-metre "deltas".
    apex_ceiling = float(cluster["apex_m"]) + 10.0
    brake_delta_m = _field_delta("brake_point_m", ceiling_m=apex_ceiling)
    apex_delta_kph = _field_delta("apex_speed_kph")

    def _throttle_after_apex(row: dict[str, Any]) -> float | None:
        throttle_on = row.get("throttle_on_m")
        apex = row.get("apex_m")
        if throttle_on is None or apex is None:
            return None
        return float(throttle_on) - float(apex)

    best_throttle = _throttle_after_apex(best)
    other_throttle = [
        value
        for value in (_throttle_after_apex(row) for row in others)
        if value is not None
    ]
    throttle_delta_m = (
        float(median(other_throttle)) - best_throttle
        if best_throttle is not None and other_throttle
        else None
    )

    apex_speed = float(best.get("apex_speed_kph") or 0.0)
    # Rate alone is not enough at small n: one event in four passes is a
    # moment, two or more is a pattern worth changing the car for.
    if lock_events >= 2 and lock_rate >= 0.25:
        mechanism = "entry-lockup"
    elif spin_events >= 2 and spin_rate >= 0.25:
        mechanism = "exit-wheelspin"
    elif apex_delta_kph is not None and apex_delta_kph <= -4.0:
        # Blaming the car's balance requires knowing the entries matched:
        # slower apexes behind earlier or later braking are the driver's, and
        # slower apexes behind UNKNOWN braking stay unattributed rather than
        # becoming a wing change on a guess.
        if brake_delta_m is None:
            mechanism = "inconsistent-line"
        elif brake_delta_m <= -8.0:
            mechanism = "overslowed-entry"
        elif brake_delta_m >= 10.0:
            mechanism = "overshot-entry"
        else:
            mechanism = "mid-corner-grip"
    elif throttle_delta_m is not None and throttle_delta_m >= 6.0:
        mechanism = "hesitant-exit"
    else:
        mechanism = "inconsistent-line"

    return {
        "turn": int(cluster["turn"]),
        "label": f"Turn {int(cluster['turn'])} ({cluster['apex_m']:.0f} m)",
        "apex_m": round(float(cluster["apex_m"]), 1),
        "samples": len(instances),
        "median_loss_s": round(median_loss, 3),
        "mechanism": mechanism,
        "mechanism_label": MECHANISM_LABELS[mechanism],
        "speed_class": _speed_class(apex_speed),
        "setup_addressable": mechanism in SETUP_MECHANISMS,
        "evidence": {
            "best_time_s": round(float(best["time_in_corner_s"]), 3),
            "lock_rate": round(lock_rate, 2),
            "spin_rate": round(spin_rate, 2),
            "brake_delta_m": None if brake_delta_m is None else round(brake_delta_m, 1),
            "apex_delta_kph": (
                None if apex_delta_kph is None else round(apex_delta_kph, 1)
            ),
            "throttle_delta_m": (
                None if throttle_delta_m is None else round(throttle_delta_m, 1)
            ),
            "best_apex_speed_kph": round(apex_speed, 1),
            "best_lap": {
                "session_uid": best.get("session_uid"),
                "lap_num": best.get("lap_num"),
            },
        },
    }


def characterize_lock(windows: list[list[dict[str, Any]]]) -> str | None:
    """Which axle locks, read from trace samples inside the turn's window.

    A wheel is locking when its slip ratio drops below -0.15 under real brake
    pressure. Slip order is FL, FR, RL, RR.
    """
    front = 0
    rear = 0
    for window in windows:
        for point in window:
            if float(point.get("brake", 0.0)) <= 0.2:
                continue
            slip = point.get("slip") or []
            if len(slip) != 4:
                continue
            if float(slip[0]) < -0.15 or float(slip[1]) < -0.15:
                front += 1
            if float(slip[2]) < -0.15 or float(slip[3]) < -0.15:
                rear += 1
    if not front and not rear:
        return None
    if front and rear and min(front, rear) * 2 > max(front, rear):
        return "both"
    return "front" if front >= rear else "rear"


def adjustments_for(
    finding: dict[str, Any], lock_axle: str | None = None
) -> dict[str, float]:
    """Map a setup-addressable mechanism to lever deltas."""
    mechanism = finding["mechanism"]
    speed_class = finding["speed_class"]
    if mechanism == "entry-lockup":
        if lock_axle == "front":
            return {"brake_bias": -1, "brake_pressure": -2}
        if lock_axle == "rear":
            return {"brake_bias": +1, "brake_pressure": -2}
        return {"brake_pressure": -3}
    if mechanism == "exit-wheelspin":
        if speed_class == "fast":
            return {"rear_wing": +1, "on_throttle": -2}
        return {"on_throttle": -4, "rear_anti_roll_bar": -1}
    if mechanism == "mid-corner-grip":
        if speed_class == "slow":
            return {"front_anti_roll_bar": -1}
        return {"front_wing": +1}
    if mechanism == "hesitant-exit":
        return {"on_throttle": -2}
    return {}


def resolve_adjustments(
    findings: list[dict[str, Any]],
) -> tuple[dict[str, float], list[str]]:
    """Combine per-turn adjustments into one net nudge, largest loss first.

    When two turns pull the same lever in opposite directions, the turn
    costing more time wins and the conflict is reported instead of silently
    averaged away.
    """
    weighted = sorted(
        (f for f in findings if f.get("adjustments")),
        key=lambda f: float(f["median_loss_s"]) * min(int(f["samples"]), 10),
        reverse=True,
    )
    net: dict[str, float] = {}
    winners: dict[str, dict[str, Any]] = {}
    notes: list[str] = []
    for finding in weighted:
        for field, delta in finding["adjustments"].items():
            current = net.get(field, 0.0)
            if current and (current > 0) != (delta > 0):
                notes.append(
                    f"{finding['label']} wants {field.replace('_', ' ')} the "
                    f"other way from {winners[field]['label']} — the bigger "
                    "loss wins; the two corners disagree about the car."
                )
                continue
            limit = NET_LIMITS.get(field)
            merged = current + delta
            if limit is not None:
                merged = max(-limit, min(limit, merged))
            net[field] = merged
            winners.setdefault(field, finding)
    return {k: v for k, v in net.items() if v}, notes
