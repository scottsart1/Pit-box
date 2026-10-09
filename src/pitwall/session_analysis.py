"""Post-session analysis of a saved session's field.

Produces the read model behind the Session Analysis view: race pace
distributions, race order and gaps per lap, stints, pit stops, fastest and
ideal laps. Every figure is derived from stored lap rows; nothing is
interpolated. Where a value cannot be derived (a car's lap time is missing)
the payload says so instead of guessing.

Race order and gaps come from summed lap times, segment by segment. The F1
games time lap 1 from lights out, so the sum of a car's lap times is its race
time at every line crossing. A suspension breaks that: the lap timer does not
run while the race is stopped, by a different amount per car, and the race
restarts from the grid with the restart lap timed from lights out again, like
lap 1. Suspended laps are recognised from the field's lap times (the whole
field at more than 1.8 times its usual pace), and each segment between
suspensions is summed on its own. On a real Singapore race with a red flag
this reproduces the official classification for every finisher, with gaps
within a few milliseconds. Every car has lap times; only some keep full
traces in a race, so lap times are the one basis that covers the whole field.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from statistics import median
from typing import Any

from .identity import TEAM_NAMES

SCHEMA_VERSION = 1
RACE_SESSION_TYPES = frozenset({"Race", "Race 2", "Race 3"})
RACE_SESSION_TYPE_IDS = frozenset({15, 16, 17})
OUTLIER_RATIO = 1.07
NEUTRALISED_SHARE = 0.3
SUSPENDED_PACE_RATIO = 1.8
UNKNOWN_TYRE_RUN = 3
MAX_LAP_ROWS = 20_000
KNOWN_COMPOUNDS = ("SOFT", "MEDIUM", "HARD", "INTER", "WET")


class SessionAnalysisError(RuntimeError):
    """Base error for session analysis."""


class SessionNotFoundError(SessionAnalysisError):
    """The requested saved session does not exist."""


@dataclass(frozen=True, slots=True)
class AnalysisCar:
    car_index: int
    car_ids: tuple[str, ...]
    display_name: str
    team_id: int | None
    race_number: int | None
    is_player: bool


@dataclass(frozen=True, slots=True)
class AnalysisLap:
    car_index: int
    lap_id: str
    lap_number: int
    lap_time_ms: int | None
    s1_ms: int | None
    s2_ms: int | None
    s3_ms: int | None
    valid: bool
    pit_context: bool
    flag_context: bool
    compound: str | None
    tyre_age_laps: int | None


def is_race_session(session_type: str | None, raw_type_id: int | None = None) -> bool:
    if raw_type_id is not None and int(raw_type_id) in RACE_SESSION_TYPE_IDS:
        return True
    return (session_type or "").strip() in RACE_SESSION_TYPES


def normalise_compound(value: str | None) -> str | None:
    """Map stored compound text onto the five dry/wet families, or None."""
    if not value:
        return None
    text = str(value).strip().upper()
    if text in KNOWN_COMPOUNDS:
        return text
    if text.startswith("INTER"):
        return "INTER"
    if "WET" in text:
        return "WET"
    return None


def driver_code(name: str, taken: set[str]) -> str:
    """Three-letter code from a surname, unique within the session."""
    ascii_name = (
        unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    )
    words = [word for word in re.split(r"[^A-Za-z0-9]+", ascii_name) if word]
    base = (words[-1] if words else "CAR").upper()
    candidates = [base[:3].ljust(3, "X")]
    # Collisions (two drivers called "Car", or two Verstappens) take the first
    # letter plus later letters, then a numeric suffix.
    for second in range(1, len(base)):
        for third in range(second + 1, len(base)):
            candidates.append(base[0] + base[second] + base[third])
    candidates.extend(f"{base[:2]}{digit}" for digit in range(10))
    for candidate in candidates:
        if candidate not in taken:
            taken.add(candidate)
            return candidate
    fallback = f"C{len(taken):02d}"
    taken.add(fallback)
    return fallback


def _quantile(sorted_values: list[float], fraction: float) -> float:
    """Linear-interpolated quantile (numpy's default 'linear' method)."""
    if not sorted_values:
        raise ValueError("quantile of an empty sequence")
    position = (len(sorted_values) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[lower])
    weight = position - lower
    return float(sorted_values[lower]) * (1 - weight) + float(sorted_values[upper]) * weight


def box_stats(values: Iterable[float]) -> dict[str, Any] | None:
    """Tukey box statistics: quartiles and 1.5 IQR whiskers."""
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    q1 = _quantile(ordered, 0.25)
    q2 = _quantile(ordered, 0.5)
    q3 = _quantile(ordered, 0.75)
    iqr = q3 - q1
    low_fence = q1 - 1.5 * iqr
    high_fence = q3 + 1.5 * iqr
    inside = [value for value in ordered if low_fence <= value <= high_fence]
    return {
        "n": len(ordered),
        "min_ms": ordered[0],
        "q1_ms": q1,
        "median_ms": q2,
        "q3_ms": q3,
        "max_ms": ordered[-1],
        "mean_ms": sum(ordered) / len(ordered),
        "whisker_low_ms": inside[0] if inside else q1,
        "whisker_high_ms": inside[-1] if inside else q3,
    }


def _ordered_laps(laps: list[AnalysisLap]) -> list[AnalysisLap]:
    return sorted(laps, key=lambda lap: lap.lap_number)


def _timed(lap: AnalysisLap | None) -> bool:
    return lap is not None and lap.lap_time_ms is not None and lap.lap_time_ms > 0


def suspended_laps(by_car: dict[int, list[AnalysisLap]]) -> list[int]:
    """Laps on which the race was stopped, recognised from the whole field.

    A lap counts as suspended when at least three cars timed it and their
    median lap time on it is more than 1.8 times their own usual (median)
    lap time. Safety-car laps run at about 1.3-1.6 times race pace; the laps
    of a red flag - the slow lap to the pit lane and the lap back to the grid
    - run at more than twice it.
    """
    usual = {
        index: median(int(lap.lap_time_ms or 0) for lap in laps if _timed(lap))
        for index, laps in by_car.items()
        if any(_timed(lap) for lap in laps)
    }
    ratios: dict[int, list[float]] = defaultdict(list)
    for index, laps in by_car.items():
        if index not in usual or usual[index] <= 0:
            continue
        for lap in laps:
            if _timed(lap):
                ratios[lap.lap_number].append(int(lap.lap_time_ms or 0) / usual[index])
    return sorted(
        lap_number
        for lap_number, values in ratios.items()
        if len(values) >= 3 and median(values) > SUSPENDED_PACE_RATIO
    )


def race_segments(last_lap: int, suspended: Iterable[int]) -> list[tuple[int, int]]:
    """Runs of racing laps between suspensions, as (first lap, last lap)."""
    stopped = set(suspended)
    segments: list[tuple[int, int]] = []
    start: int | None = None
    for lap_number in range(1, last_lap + 1):
        if lap_number in stopped:
            if start is not None:
                segments.append((start, lap_number - 1))
                start = None
        elif start is None:
            start = lap_number
    if start is not None:
        segments.append((start, last_lap))
    return segments


def segment_times(
    laps: list[AnalysisLap], segments: list[tuple[int, int]]
) -> tuple[dict[int, int], int | None]:
    """Race time within its segment at each completed lap.

    Sums run from each segment's first lap and stop at the first missing lap
    time inside a segment; the next segment starts afresh. Returns the
    milliseconds by lap number and the first lap whose time is missing (a
    hole in the record), or None.
    """
    by_number = {lap.lap_number: lap for lap in laps}
    result: dict[int, int] = {}
    first_gap: int | None = None
    last_timed = max((lap.lap_number for lap in laps if _timed(lap)), default=0)
    for first, last in segments:
        total = 0
        for lap_number in range(first, last + 1):
            lap = by_number.get(lap_number)
            if not _timed(lap):
                if lap_number <= last_timed and first_gap is None:
                    first_gap = lap_number
                break
            total += int(lap.lap_time_ms or 0)
            result[lap_number] = total
    return result, first_gap


def pit_runs(laps: list[AnalysisLap]) -> list[list[AnalysisLap]]:
    """Contiguous runs of laps marked with pit context."""
    runs: list[list[AnalysisLap]] = []
    previous: AnalysisLap | None = None
    for lap in _ordered_laps(laps):
        if lap.pit_context:
            if runs and previous is not None and previous.pit_context and lap.lap_number == previous.lap_number + 1:
                runs[-1].append(lap)
            else:
                runs.append([lap])
        previous = lap
    return runs


def _stint_compound(laps: list[AnalysisLap]) -> str | None:
    counts = Counter(lap.compound for lap in laps if lap.compound)
    return counts.most_common(1)[0][0] if counts else None


def stints_for(laps: list[AnalysisLap]) -> list[dict[str, Any]]:
    """Split a car's laps into stints at pit stops and tyre changes.

    A pit stop spans an in-lap and an out-lap where the pit lane crosses the
    timing line; the out-lap starts the new stint. A one-lap pit run (pit lane
    entirely inside one lap) starts the new stint on the following lap. A
    compound change or tyre-age reset without pit context (tyres changed
    under a red flag) also starts a stint.
    """
    ordered = [lap for lap in _ordered_laps(laps) if lap.lap_time_ms is not None or lap.compound]
    if not ordered:
        return []
    starts: set[int] = set()
    for run in pit_runs(ordered):
        starts.add(run[-1].lap_number if len(run) > 1 else run[0].lap_number + 1)
    known = [lap for lap in ordered if lap.compound or lap.tyre_age_laps is not None]
    for previous, lap in pairwise(known):
        if previous.compound and lap.compound and previous.compound != lap.compound:
            starts.add(lap.lap_number)
        if (
            previous.tyre_age_laps is not None
            and lap.tyre_age_laps is not None
            and lap.tyre_age_laps < previous.tyre_age_laps
        ):
            starts.add(lap.lap_number)
    # Three or more laps in a row with no tyre data form a stint of their own
    # with an unknown compound: a known compound must not be stretched over
    # laps nobody recorded (cars without full traces in a race).
    run: list[AnalysisLap] = []
    for lap in ordered + [None]:  # type: ignore[list-item]
        if lap is not None and not lap.compound and lap.tyre_age_laps is None:
            run.append(lap)
            continue
        if len(run) >= UNKNOWN_TYRE_RUN:
            starts.add(run[0].lap_number)
            if lap is not None:
                starts.add(lap.lap_number)
        run = []
    groups: list[list[AnalysisLap]] = []
    for lap in ordered:
        if not groups or lap.lap_number in starts:
            groups.append([])
        groups[-1].append(lap)
    stints = []
    for ordinal, group in enumerate(groups, 1):
        ages = [lap.tyre_age_laps for lap in group if lap.tyre_age_laps is not None]
        stints.append(
            {
                "ordinal": ordinal,
                "compound": _stint_compound(group),
                "start_lap": group[0].lap_number,
                "end_lap": group[-1].lap_number,
                "laps": len(group),
                "tyre_age_start": min(ages) if ages else None,
            }
        )
    return stints


def _pace_exclusion(lap: AnalysisLap, is_race: bool, suspended: set[int]) -> str | None:
    if not _timed(lap):
        return "missing_time"
    if is_race and lap.lap_number == 1:
        return "lap_one"
    if lap.lap_number in suspended:
        return "suspended"
    if lap.pit_context:
        return "pit"
    if lap.flag_context:
        return "neutralised"
    if not is_race and not lap.valid:
        # Outside a race an invalid lap is a deleted lap, not a race lap.
        return "invalid"
    return None


def race_pace(
    laps: list[AnalysisLap],
    *,
    is_race: bool,
    hide_outliers: bool = True,
    suspended: Iterable[int] = (),
    restart_laps: Iterable[int] = (),
) -> tuple[dict[str, Any] | None, dict[int, str]]:
    """Box statistics of representative laps and why each other lap was left out."""
    stopped = set(suspended)
    restarts = set(restart_laps)
    excluded: dict[int, str] = {}
    candidates: list[AnalysisLap] = []
    for lap in laps:
        reason = _pace_exclusion(lap, is_race, stopped)
        if reason is None and is_race and lap.lap_number in restarts:
            # A restart from the grid is timed from lights out, like lap 1.
            reason = "restart"
        if reason:
            excluded[lap.lap_number] = reason
        else:
            candidates.append(lap)
    if hide_outliers and candidates:
        threshold = median(int(lap.lap_time_ms or 0) for lap in candidates) * OUTLIER_RATIO
        kept = []
        for lap in candidates:
            if int(lap.lap_time_ms or 0) > threshold:
                excluded[lap.lap_number] = "outlier"
            else:
                kept.append(lap)
        candidates = kept
    stats = box_stats(int(lap.lap_time_ms or 0) for lap in candidates)
    if stats is not None:
        stats["outliers"] = [
            {"lap_number": lap.lap_number, "lap_time_ms": int(lap.lap_time_ms or 0)}
            for lap in candidates
            if not stats["whisker_low_ms"] <= int(lap.lap_time_ms or 0) <= stats["whisker_high_ms"]
        ]
        stats["lap_numbers"] = sorted(lap.lap_number for lap in candidates)
    return stats, excluded


def build_session_analysis(
    session: dict[str, Any],
    cars: list[AnalysisCar],
    laps: list[AnalysisLap],
    *,
    hide_outliers: bool = True,
) -> dict[str, Any]:
    """Assemble the analysis payload from cars and their newest lap rows."""
    is_race = is_race_session(session.get("session_type"), session.get("raw_session_type_id"))
    by_car: dict[int, list[AnalysisLap]] = defaultdict(list)
    for lap in laps:
        by_car[lap.car_index].append(lap)
    for index, car_laps in by_car.items():
        by_car[index] = _ordered_laps(car_laps)
    warnings: list[str] = []
    taken_codes: set[str] = set()
    car_by_index = {car.car_index: car for car in cars}

    last_lap = max((lap.lap_number for lap in laps if _timed(lap)), default=0)
    suspended = suspended_laps(by_car) if is_race else []
    segments = race_segments(last_lap, suspended) if is_race else [(1, last_lap)] if last_lap else []
    restart_laps = [first for first, _ in segments[1:]]
    elapsed: dict[int, dict[int, int]] = {}
    record_gaps: dict[int, int | None] = {}
    for index, car_laps in by_car.items():
        elapsed[index], record_gaps[index] = segment_times(car_laps, segments)
    # Laps completed: the last lap with a time, provided the record has no
    # hole; a hole leaves only the laps before it as known.
    completed: dict[int, int] = {}
    for index, car_laps in by_car.items():
        timed = [lap.lap_number for lap in car_laps if _timed(lap)]
        completed[index] = max(timed, default=0)
    leader_laps = max(completed.values(), default=0)

    positions: dict[tuple[int, int], int] = {}
    gaps: dict[tuple[int, int], int] = {}
    intervals: dict[tuple[int, int], int] = {}
    if is_race:
        previous_order: list[int] = []
        for lap_number in range(1, leader_laps + 1):
            if lap_number in suspended:
                # Nobody races while the race is stopped: the order holds.
                for position, index in enumerate(previous_order, 1):
                    if any(lap.lap_number == lap_number for lap in by_car[index]):
                        positions[(index, lap_number)] = position
                continue
            running = sorted(
                (elapsed[index][lap_number], index)
                for index in elapsed
                if lap_number in elapsed[index]
            )
            if not running:
                continue
            order = [index for _, index in running]
            leader_time = running[0][0]
            for position, (time_ms, index) in enumerate(running, 1):
                positions[(index, lap_number)] = position
                gaps[(index, lap_number)] = time_ms - leader_time
                if position > 1:
                    intervals[(index, lap_number)] = time_ms - running[position - 2][0]
            previous_order = order

    # Finishing order: laps completed, then race time within the segment the
    # car finished in. Cars whose record has a hole after the last restart
    # cannot be placed and are listed after the classified cars.
    def placed_key(index: int) -> tuple[int, int] | None:
        laps_done = completed.get(index, 0)
        if not laps_done or laps_done not in elapsed.get(index, {}):
            return None
        return (-laps_done, elapsed[index][laps_done])

    finish_order = sorted(
        (index for index in by_car if placed_key(index) is not None),
        key=lambda index: placed_key(index) or (0, 0),
    ) if is_race else []
    winner = finish_order[0] if finish_order else None
    final_segment = segments[-1] if segments else None

    flagged: Counter[int] = Counter()
    running_count: Counter[int] = Counter()
    for lap in laps:
        running_count[lap.lap_number] += 1
        if lap.flag_context:
            flagged[lap.lap_number] += 1
    neutralised = sorted(
        lap_number
        for lap_number, count in flagged.items()
        if lap_number not in suspended
        and running_count[lap_number]
        and count / running_count[lap_number] >= NEUTRALISED_SHARE
    )

    pace_rows: list[dict[str, Any]] = []
    lap_rows: list[dict[str, Any]] = []
    driver_rows: list[dict[str, Any]] = []
    stint_rows: list[dict[str, Any]] = []
    pit_rows: list[dict[str, Any]] = []
    fastest_rows: list[dict[str, Any]] = []
    sector_best: dict[str, dict[str, Any] | None] = {"s1": None, "s2": None, "s3": None}

    ordered_indices = sorted(
        set(car_by_index) | set(by_car),
        key=lambda index: (
            finish_order.index(index) if index in finish_order else len(finish_order),
            -completed.get(index, 0),
            index,
        ),
    )
    for index in ordered_indices:
        car = car_by_index.get(index) or AnalysisCar(index, (), f"Car {index + 1}", None, None, False)
        car_laps = by_car.get(index, [])
        code = driver_code(car.display_name, taken_codes)
        stats, excluded = race_pace(
            car_laps,
            is_race=is_race,
            hide_outliers=hide_outliers,
            suspended=suspended,
            restart_laps=restart_laps,
        )
        if stats is not None:
            pace_rows.append({"car_index": index, **stats, "excluded": dict(Counter(excluded.values()))})
        valid_timed = [lap for lap in car_laps if lap.valid and _timed(lap)]
        best = min(valid_timed, key=lambda lap: (lap.lap_time_ms, lap.lap_number), default=None)
        sectors: dict[str, dict[str, Any] | None] = {}
        for key in ("s1", "s2", "s3"):
            timed_sector = [lap for lap in car_laps if lap.valid and getattr(lap, f"{key}_ms")]
            fastest = min(
                timed_sector, key=lambda lap: (getattr(lap, f"{key}_ms"), lap.lap_number), default=None
            )
            sectors[key] = (
                {"ms": int(getattr(fastest, f"{key}_ms")), "lap_number": fastest.lap_number}
                if fastest
                else None
            )
            current = sector_best[key]
            if sectors[key] and (current is None or sectors[key]["ms"] < current["ms"]):
                sector_best[key] = {"car_index": index, **sectors[key]}
        ideal = sum(item["ms"] for item in sectors.values() if item) if all(sectors.values()) else None
        if best is not None:
            fastest_rows.append(
                {
                    "car_index": index,
                    "lap_number": best.lap_number,
                    "lap_id": best.lap_id,
                    "lap_time_ms": int(best.lap_time_ms or 0),
                    "compound": best.compound,
                    "ideal_lap_ms": ideal,
                }
            )
        stints = stints_for(car_laps)
        stint_rows.append({"car_index": index, "stints": stints})
        median_pace = stats["median_ms"] if stats else None
        compounds_by_lap = {lap.lap_number: lap.compound for lap in car_laps}
        flags_by_lap = {lap.lap_number: lap.flag_context for lap in car_laps}
        runs = pit_runs(car_laps)
        car_stops: list[dict[str, Any]] = []
        explained: set[int] = set()
        for run in runs:
            before = compounds_by_lap.get(run[0].lap_number - 1) or run[0].compound
            after = compounds_by_lap.get(run[-1].lap_number + 1) or run[-1].compound
            run_time = [int(lap.lap_time_ms or 0) for lap in run if _timed(lap)]
            neutral = any(lap.flag_context or lap.lap_number in suspended for lap in run)
            loss = (
                round(sum(run_time) - len(run_time) * median_pace)
                if median_pace is not None and len(run_time) == len(run) and not neutral
                else None
            )
            explained.update(lap.lap_number for lap in run)
            explained.add(run[-1].lap_number + 1)
            car_stops.append(
                {
                    "car_index": index,
                    "kind": "pit_stop",
                    "lap_number": run[0].lap_number,
                    "laps": [lap.lap_number for lap in run],
                    "from_compound": before,
                    "to_compound": after,
                    "under_neutralisation": neutral,
                    "estimated_loss_ms": loss,
                }
            )
        # A new stint without a pit visit: tyres changed while the race was
        # suspended. The game counts it as a stop; no time is lost to it.
        for previous, stint in pairwise(stints):
            # Only a recorded change counts: a boundary into or out of laps
            # with no tyre data says nothing about what happened.
            if stint["start_lap"] in explained or not previous["compound"] or not stint["compound"]:
                continue
            car_stops.append(
                {
                    "car_index": index,
                    "kind": "tyre_change",
                    "lap_number": previous["end_lap"],
                    "laps": [previous["end_lap"], stint["start_lap"]],
                    "from_compound": previous["compound"],
                    "to_compound": stint["compound"],
                    "under_neutralisation": bool(
                        flags_by_lap.get(previous["end_lap"])
                        or flags_by_lap.get(stint["start_lap"])
                        or previous["end_lap"] in suspended
                        or stint["start_lap"] - 1 in suspended
                    ),
                    "estimated_loss_ms": None,
                }
            )
        car_stops.sort(key=lambda row: row["lap_number"])
        pit_rows.extend(car_stops)

        laps_done = completed.get(index, 0)
        gap_lap = record_gaps.get(index)
        placed = index in finish_order
        status = "no_data"
        laps_down = None
        if is_race and laps_done:
            laps_down = leader_laps - laps_done
            if not placed:
                status = "incomplete_record"
            elif laps_down == 0:
                status = "finished"
            elif final_segment is not None and laps_done >= final_segment[0] and winner is not None and (
                elapsed[index][laps_done] >= elapsed[winner][leader_laps]
            ):
                # Crossed the line after the winner: classified, laps down.
                status = "lapped"
            else:
                status = "retired"
        elif laps_done:
            status = "running"
        uninterrupted = is_race and not suspended and laps_done and laps_done in elapsed.get(index, {})
        race_time = elapsed[index][laps_done] if uninterrupted else None
        winner_time = (
            elapsed[winner][leader_laps]
            if winner is not None and leader_laps in elapsed.get(winner, {})
            else None
        )
        driver_rows.append(
            {
                "car_index": index,
                "car_ids": list(car.car_ids),
                "display_name": car.display_name,
                "code": code,
                "team_id": car.team_id,
                "team_name": TEAM_NAMES.get(car.team_id) if car.team_id is not None else None,
                "race_number": car.race_number,
                "is_player": car.is_player,
                "laps_recorded": len(car_laps),
                "laps_completed": laps_done,
                "finish_position": (finish_order.index(index) + 1) if placed else None,
                "status": status,
                "laps_down": laps_down,
                "race_time_ms": race_time,
                "gap_to_winner_ms": (
                    elapsed[index][laps_done] - winner_time
                    if status == "finished" and winner_time is not None and laps_done in elapsed.get(index, {})
                    else None
                ),
                "record_gap_from_lap": gap_lap,
                "best_lap_ms": best.lap_time_ms if best else None,
                "best_lap_number": best.lap_number if best else None,
                "best_lap_id": best.lap_id if best else None,
                "best_sectors": sectors,
                "ideal_lap_ms": ideal,
                "pit_stops": len(car_stops),
                "pit_lane_visits": len(runs),
            }
        )
        for lap in car_laps:
            key = (index, lap.lap_number)
            lap_rows.append(
                {
                    "car_index": index,
                    "lap_number": lap.lap_number,
                    "lap_id": lap.lap_id,
                    "lap_time_ms": lap.lap_time_ms,
                    "s1_ms": lap.s1_ms,
                    "s2_ms": lap.s2_ms,
                    "s3_ms": lap.s3_ms,
                    "valid": lap.valid,
                    "pit": lap.pit_context,
                    "neutralised": lap.flag_context,
                    "suspended": lap.lap_number in suspended,
                    "compound": lap.compound,
                    "tyre_age_laps": lap.tyre_age_laps,
                    "segment_time_ms": elapsed.get(index, {}).get(lap.lap_number),
                    "position": positions.get(key),
                    "gap_to_leader_ms": gaps.get(key),
                    "interval_ms": intervals.get(key),
                    "pace_excluded": excluded.get(lap.lap_number),
                }
            )
        if gap_lap is not None:
            warnings.append(
                f"{car.display_name}: lap {gap_lap} has no recorded time, so race order and gaps "
                "for this car stop there until the next restart."
            )

    pace_rows.sort(key=lambda row: (row["median_ms"], row["car_index"]))
    fastest_rows.sort(key=lambda row: (row["lap_time_ms"], row["car_index"]))
    if fastest_rows:
        fastest_time = fastest_rows[0]["lap_time_ms"]
        for row in fastest_rows:
            row["delta_to_fastest_ms"] = row["lap_time_ms"] - fastest_time
    overall_ideal = (
        sum(item["ms"] for item in sector_best.values() if item) if all(sector_best.values()) else None
    )
    if not laps:
        warnings.append("No laps are stored for this session.")
    if is_race and laps and not finish_order:
        warnings.append("No car has a continuous lap record, so race order cannot be derived.")

    outlier_note = (
        f"; laps more than {round((OUTLIER_RATIO - 1) * 100)}% slower than the driver's median are hidden"
        if hide_outliers
        else ""
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "session_id": session.get("id"),
        "session": {
            "track_id": session.get("track_id"),
            "track_name": session.get("track_name"),
            "session_type": session.get("session_type"),
            "is_race": is_race,
            "status": session.get("status"),
            "started_at": session.get("started_at"),
            "total_laps": session.get("total_laps"),
            "leader_laps": leader_laps if is_race else None,
        },
        "basis": {
            "race_order": (
                "summed lap times; each racing segment between suspensions is summed from its own start"
                if is_race
                else None
            ),
            "finish_order": "derived from laps completed and race time; penalties are not applied",
            "pace_laps": (
                "excludes lap 1, restart laps, pit in/out laps, neutralised and suspended laps, and laps without a time"
                if is_race
                else "excludes pit laps, neutralised laps, invalid laps and laps without a time"
            )
            + outlier_note,
            "pit_loss": "in-lap and out-lap time minus the same number of the driver's median pace laps; an estimate",
        },
        "hide_outliers": hide_outliers,
        "segments": [{"first_lap": first, "last_lap": last} for first, last in segments] if is_race else [],
        "suspended_laps": suspended,
        "neutralised_laps": neutralised,
        "drivers": driver_rows,
        "laps": lap_rows,
        "race_pace": pace_rows,
        "stints": stint_rows,
        "pit_stops": pit_rows,
        "fastest_laps": fastest_rows,
        "sector_bests": sector_best,
        "ideal_lap": {
            "best_sectors_ms": overall_ideal,
            "best_driver_ideal_ms": min(
                (row["ideal_lap_ms"] for row in fastest_rows if row["ideal_lap_ms"] is not None),
                default=None,
            ),
        },
        "winner_car_index": winner,
        "warnings": warnings,
    }


def _int_or_none(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


class SessionAnalysisService:
    """Reads one saved session's newest lap rows and builds the analysis."""

    def __init__(self, database_path: Path, *, max_lap_rows: int = MAX_LAP_ROWS) -> None:
        self.database_path = Path(database_path)
        self.max_lap_rows = int(max_lap_rows)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.database_path, timeout=15)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            with connection:
                yield connection

    def _load_sync(
        self, session_id: str
    ) -> tuple[dict[str, Any], list[AnalysisCar], list[AnalysisLap]]:
        with self._connect() as db:
            session_row = db.execute(
                "SELECT * FROM recorded_sessions WHERE id=?", (session_id,)
            ).fetchone()
            if session_row is None:
                raise SessionNotFoundError(f"Saved session {session_id!r} was not found")
            car_rows = db.execute(
                """
                SELECT * FROM session_cars WHERE session_id=?
                ORDER BY car_index, identity_revision
                """,
                (session_id,),
            ).fetchall()
            # Newest timeline epoch per car identity and lap, as Field Lab
            # reads it: an abandoned flashback branch never counts.
            lap_rows = db.execute(
                """
                SELECT l.id, l.lap_number, l.timeline_epoch, l.lap_time_ms, l.valid,
                       l.pit_context, l.flag_context, l.tyre_compound, l.tyre_age_laps,
                       l.engineering_json, c.car_index, c.identity_revision
                FROM recorded_laps l
                JOIN session_cars c ON c.id=l.session_car_id
                WHERE c.session_id=?
                  AND NOT EXISTS (
                      SELECT 1 FROM recorded_laps newer
                      WHERE newer.session_car_id=l.session_car_id
                        AND newer.lap_number=l.lap_number
                        AND newer.timeline_epoch>l.timeline_epoch
                  )
                ORDER BY c.car_index, l.lap_number, c.identity_revision, l.timeline_epoch
                LIMIT ?
                """,
                (session_id, self.max_lap_rows + 1),
            ).fetchall()
        if len(lap_rows) > self.max_lap_rows:
            raise SessionAnalysisError(
                f"The session has more than {self.max_lap_rows} lap rows; analysis is bounded."
            )
        cars_by_index: dict[int, list[sqlite3.Row]] = defaultdict(list)
        for row in car_rows:
            cars_by_index[int(row["car_index"])].append(row)
        cars: list[AnalysisCar] = []
        for index, rows in sorted(cars_by_index.items()):
            latest = rows[-1]
            name = latest["display_name"] or latest["anonymized_name"] or f"Car {index + 1}"
            cars.append(
                AnalysisCar(
                    car_index=index,
                    car_ids=tuple(str(row["id"]) for row in rows),
                    display_name=str(name),
                    team_id=int(latest["team_id"]) if latest["team_id"] is not None else None,
                    race_number=int(latest["race_number"]) if latest["race_number"] is not None else None,
                    is_player=any(bool(row["is_player"]) for row in rows),
                )
            )
        # One row per physical car and lap: the newest epoch, then the newest
        # identity revision, wins.
        chosen: dict[tuple[int, int], sqlite3.Row] = {}
        for row in lap_rows:
            chosen[(int(row["car_index"]), int(row["lap_number"]))] = row
        context: dict[str, Any] = {}
        laps: list[AnalysisLap] = []
        for row in chosen.values():
            try:
                engineering = json.loads(row["engineering_json"] or "{}")
            except (TypeError, ValueError):
                engineering = {}
            if not isinstance(engineering, dict):
                engineering = {}
            for key in ("track_name", "total_laps"):
                if engineering.get(key) and key not in context:
                    context[key] = engineering[key]
            laps.append(
                AnalysisLap(
                    car_index=int(row["car_index"]),
                    lap_id=str(row["id"]),
                    lap_number=int(row["lap_number"]),
                    lap_time_ms=_int_or_none(row["lap_time_ms"]),
                    s1_ms=_int_or_none(engineering.get("s1_ms")),
                    s2_ms=_int_or_none(engineering.get("s2_ms")),
                    s3_ms=_int_or_none(engineering.get("s3_ms")),
                    valid=bool(row["valid"]),
                    pit_context=bool(row["pit_context"]),
                    flag_context=bool(row["flag_context"]),
                    compound=normalise_compound(row["tyre_compound"]),
                    tyre_age_laps=(
                        int(row["tyre_age_laps"]) if row["tyre_age_laps"] is not None else None
                    ),
                )
            )
        session = {
            "id": str(session_row["id"]),
            "track_id": session_row["track_id"],
            "session_type": session_row["session_type"],
            "raw_session_type_id": session_row["raw_session_type_id"],
            "status": session_row["status"],
            "started_at": session_row["started_at"],
            "track_name": context.get("track_name"),
            "total_laps": _int_or_none(context.get("total_laps")),
        }
        return session, cars, laps

    def _analysis_sync(self, session_id: str, hide_outliers: bool) -> dict[str, Any]:
        session, cars, laps = self._load_sync(session_id)
        return build_session_analysis(session, cars, laps, hide_outliers=hide_outliers)

    async def analysis(self, session_id: str, *, hide_outliers: bool = True) -> dict[str, Any]:
        return await asyncio.to_thread(self._analysis_sync, session_id, hide_outliers)


__all__ = [
    "AnalysisCar",
    "AnalysisLap",
    "SessionAnalysisError",
    "SessionAnalysisService",
    "SessionNotFoundError",
    "box_stats",
    "build_session_analysis",
    "driver_code",
    "is_race_session",
    "normalise_compound",
    "pit_runs",
    "race_pace",
    "race_segments",
    "segment_times",
    "stints_for",
    "suspended_laps",
]
