"""Post-session analysis of a saved session's field.

Produces the read model behind the Session Analysis view: race pace
distributions, race order and gaps per lap, stints, pit stops, fastest and
ideal laps. Every figure is derived from stored lap rows; nothing is
interpolated. Where a value cannot be derived (a car's lap time is missing,
a lap was recorded without telemetry context) the payload says so instead of
guessing: unknown is reported as unknown, never as zero or as "no".

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

Safety-car and virtual-safety-car laps come from the race-control messages
the game sends (stored as session events), never from how many cars had a
flag on a lap: since 4.13.1 a lap's flag context is that car's own (a local
yellow), so a share of flagged cars is not race-wide evidence.

Only the active flashback branch counts. Rows abandoned by a flashback are
flagged in place (invalid_reason_mask bit 2) and are never read; rows still
awaiting confirmation after a frame-only flashback are marked unconfirmed.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import closing, contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from statistics import median
from typing import Any

from .catalog import track_name as catalog_track_name
from .identity import TEAM_NAMES

SCHEMA_VERSION = 2
RACE_SESSION_NAMES = frozenset({"race", "race 2", "race 3", "sprint"})
RACE_SESSION_TYPE_IDS = frozenset({15, 16, 17})
PROVISIONAL_STATUSES = frozenset({"recording", "incomplete"})
OUTLIER_RATIO = 1.07
SUSPENDED_PACE_RATIO = 1.8
# Safety-car laps run at about 1.3-1.6 times race pace; anything at least
# 1.15 times it is treated as neutralised pace when race control is patchy.
NEUTRAL_PACE_RATIO = 1.15
UNKNOWN_TYRE_RUN = 3
SECTOR_TOLERANCE_MS = 5
MAX_LAP_ROWS = 20_000
KNOWN_COMPOUNDS = ("SOFT", "MEDIUM", "HARD", "INTER", "WET")
ABANDONED_BRANCH_BIT = 2
RACE_CONTROL_EVENTS = ("SCAR", "RDFL", "LGOT")
# Race-control messages can trail the session's recorded end by a little.
EVENT_GRACE_S = 900.0
SAFETY_CAR_KINDS = {1: "SC", 2: "VSC"}


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
    # False for a lap known only from the game's lap history: its time is
    # authoritative, but pit and flag context were never observed (the
    # stored 0s are column defaults, not evidence).
    context_observed: bool = True
    # A timing-only lap kept through a frame-only flashback until a complete
    # history confirms which branch it belongs to.
    unconfirmed: bool = False
    traced: bool = False
    # Excluded from pace by a lap note (the driver or the engineer said the
    # lap was compromised).
    driver_excluded: bool = False
    # The lap's trace, from which the game's race position at the end of the
    # lap can be read, and that position as the player's lap row recorded it.
    trace_manifest_id: str | None = None
    recorded_position: int | None = None
    # How much of the lap its trace covers (0-1), when it has one.
    coverage_ratio: float | None = None


@dataclass(frozen=True, slots=True)
class RaceControl:
    """Safety cars, red flags and starts from the game's race-control messages."""

    available: bool = False
    periods: tuple[tuple[str, int, int], ...] = ()
    red_flag_laps: tuple[int, ...] = ()
    lights_out_laps: tuple[int, ...] = ()

    def neutral_laps(self) -> dict[int, str]:
        """Lap number -> "SC" or "VSC" (a full safety car wins an overlap)."""
        laps: dict[int, str] = {}
        for kind, first, last in self.periods:
            for lap_number in range(first, last + 1):
                if laps.get(lap_number) != "SC":
                    laps[lap_number] = kind
        return laps


def is_race_session(session_type: str | None, raw_type_id: int | None = None) -> bool:
    """The Library's race family: Race, Race 2, Race 3 and Sprint.

    Raw game ids 15-17 and digit labels (stored before 4.9.7) count too. On a
    sprint weekend the classifier relabels the game's Race (15) as Sprint.
    """
    if raw_type_id is not None:
        try:
            if int(raw_type_id) in RACE_SESSION_TYPE_IDS:
                return True
        except (TypeError, ValueError):
            pass
    text = str(session_type or "").strip().lower()
    if text.isdigit():
        return int(text) in RACE_SESSION_TYPE_IDS
    return text in RACE_SESSION_NAMES


def normalise_compound(value: str | None) -> str | None:
    """Map stored compound text onto the dry/wet families, or None.

    The game's C1-C6 labels (visual compounds 19-23 on other formulae) are
    real recorded compounds and keep their label; only missing or
    unrecognised text is unknown.
    """
    if not value:
        return None
    text = str(value).strip().upper()
    if text in KNOWN_COMPOUNDS:
        return text
    if text.startswith("INTER"):
        return "INTER"
    if "WET" in text:
        return "WET"
    if re.fullmatch(r"C[1-6]", text):
        return text
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


def _tyre_evidence(lap: AnalysisLap) -> bool:
    return bool(lap.compound) and lap.tyre_age_laps is not None


def _event_lap(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return max(1, number)


def _slow_run_end(start: int, last_lap: int, field_pace: Mapping[int, float]) -> int:
    """Last lap of the run of neutralised-pace laps beginning at ``start``.

    The deployment lap itself may be mostly green (the safety car came out
    late in it), so one quick lap at the start is allowed.
    """
    end = start
    for lap_number in range(start, last_lap + 1):
        if field_pace.get(lap_number, 0.0) >= NEUTRAL_PACE_RATIO:
            end = lap_number
        elif lap_number > start:
            break
    return end


def _slow_run_start(end: int, field_pace: Mapping[int, float]) -> int:
    """First lap of the run of neutralised-pace laps ending at ``end``."""
    start = end
    for lap_number in range(end - 1, 0, -1):
        if field_pace.get(lap_number, 0.0) < NEUTRAL_PACE_RATIO:
            break
        start = lap_number
    return start


def race_control_from_events(
    events: Iterable[Mapping[str, Any]] | None,
    last_lap: int,
    field_pace: Mapping[int, float] | None = None,
) -> RaceControl:
    """Safety-car periods, red flags and lights-out laps from race control.

    ``events`` are ``{"type", "lap", "payload"}`` mappings in the order the
    game sent them; None means race control was not recorded. A safety car
    runs from the lap it was deployed on to the lap it returned on (or the
    race resumed). Race-control packets are sent once and can be lost: with
    ``field_pace`` (the field's median lap time on each lap as a multiple of
    its usual pace) a period whose end was lost closes when the field is
    back at racing pace, and an end whose deployment was lost opens where
    the slow laps began. Without it, a period that never ends runs to the
    last lap and an orphaned end is ignored.
    """
    if events is None:
        return RaceControl()
    open_periods: dict[str, list[int | None]] = {}
    periods: list[tuple[str, int, int]] = []
    red: set[int] = set()
    lights: set[int] = set()
    pace = field_pace or {}

    def close(kinds: Iterable[str], lap_number: int) -> None:
        for kind in list(kinds):
            start = open_periods.pop(kind, None)
            if start is not None and start[0] is not None:
                periods.append((kind, int(start[0]), max(int(start[0]), lap_number)))
            elif start is None and field_pace is not None and kind in SAFETY_CAR_KINDS.values():
                # The deployment message was lost: the period began where the
                # field's pace dropped.
                periods.append((kind, _slow_run_start(lap_number, pace), lap_number))

    for event in events:
        kind = str(event.get("type") or "").upper()
        lap_number = _event_lap(event.get("lap"))
        if lap_number is None:
            continue
        payload = event.get("payload") or {}
        if not isinstance(payload, Mapping):
            payload = {}
        if kind == "RDFL":
            red.add(lap_number)
        elif kind == "LGOT":
            lights.add(lap_number)
        elif kind == "SCAR":
            try:
                car_type = int(payload.get("safety_car_type", -1))
                action = int(payload.get("event_type", -1))
            except (TypeError, ValueError):
                continue
            car_kind = SAFETY_CAR_KINDS.get(car_type)
            if action == 0 and car_kind:
                open_periods.setdefault(car_kind, [lap_number, None])
            elif action == 1:
                if car_kind and car_kind not in open_periods and field_pace is not None:
                    open_periods[car_kind] = [_slow_run_start(lap_number, pace), lap_number]
                for kind_name, state in open_periods.items():
                    if (car_kind is None or kind_name == car_kind) and state[1] is None:
                        state[1] = lap_number
            elif action in (2, 3):
                # A "resume" with no safety-car type ends a suspension, not a
                # safety car that was never deployed.
                close([car_kind] if car_kind else list(open_periods), lap_number)
    for kind_name, state in list(open_periods.items()):
        start = int(state[0] or 1)
        if state[1] is not None:
            end = int(state[1])
        elif field_pace is not None:
            end = _slow_run_end(start, last_lap, pace)
        else:
            end = max(last_lap, start)
        close([kind_name], end)
    return RaceControl(
        available=True,
        periods=tuple(sorted(periods, key=lambda item: (item[1], item[0]))),
        red_flag_laps=tuple(sorted(red)),
        lights_out_laps=tuple(sorted(lights)),
    )


def usual_pace(by_car: Mapping[int, list[AnalysisLap]]) -> dict[int, float]:
    """Each car's usual lap time: the lower quartile of its timed laps.

    Not the median: in a short race safety-car, red-flag and restart laps
    can be half the record, and a median there is a safety-car lap.
    """
    usual: dict[int, float] = {}
    for index, laps in by_car.items():
        times = sorted(float(lap.lap_time_ms or 0) for lap in laps if _timed(lap))
        if times:
            usual[index] = _quantile(times, 0.25)
    return usual


def field_pace(by_car: Mapping[int, list[AnalysisLap]]) -> tuple[dict[int, float], dict[int, int]]:
    """Per lap: the field's median lap time as a multiple of each car's
    usual pace, and how many cars timed that lap."""
    usual = usual_pace(by_car)
    ratios: dict[int, list[float]] = defaultdict(list)
    for index, laps in by_car.items():
        if usual.get(index, 0) <= 0:
            continue
        for lap in laps:
            if _timed(lap):
                ratios[lap.lap_number].append(int(lap.lap_time_ms or 0) / usual[index])
    return (
        {lap_number: float(median(values)) for lap_number, values in ratios.items()},
        {lap_number: len(values) for lap_number, values in ratios.items()},
    )


def suspended_laps(
    by_car: Mapping[int, list[AnalysisLap]], red_flag_laps: Iterable[int] = ()
) -> list[int]:
    """Laps on which the race was stopped, recognised from the whole field.

    A lap counts as suspended when at least three cars timed it and their
    median lap time on it is more than 1.8 times their own usual (median)
    lap time. Safety-car laps run at about 1.3-1.6 times race pace; the laps
    of a red flag - the slow lap to the pit lane and the lap back to the grid
    - run at more than twice it. With fewer than three cars recorded (a
    player-only legacy session) one slow lap could be a spin, so a red flag
    from race control on that lap or the two before it must confirm it.
    """
    pace, counts = field_pace(by_car)
    red = set(red_flag_laps)
    return sorted(
        lap_number
        for lap_number, ratio in pace.items()
        if ratio > SUSPENDED_PACE_RATIO
        and (counts[lap_number] >= 3 or any(lap_number - 2 <= flag <= lap_number for flag in red))
    )


def _runs(numbers: Iterable[int]) -> list[tuple[int, int]]:
    ordered = sorted(set(numbers))
    runs: list[tuple[int, int]] = []
    for number in ordered:
        if runs and number == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], number)
        else:
            runs.append((number, number))
    return runs


def car_suspended_laps(
    laps: list[AnalysisLap], suspended: Iterable[int], usual: float | None
) -> set[int]:
    """The laps a car spent under a suspension, in its own lap numbering.

    A car a lap down reaches the red flag on an earlier lap number than the
    leaders and restarts a lap earlier too. Its own slow laps (more than 1.8
    times its usual pace) near each field suspension are its suspended laps;
    a car with none there takes the field's laps.
    """
    own: set[int] = set()
    numbers = {lap.lap_number for lap in laps}
    for first, last in _runs(suspended):
        window = range(first - 2, last + 2)
        slow = {
            lap.lap_number
            for lap in laps
            if lap.lap_number in window
            and _timed(lap)
            and usual
            and int(lap.lap_time_ms or 0) / usual > SUSPENDED_PACE_RATIO
        }
        own.update(slow or {number for number in range(first, last + 1) if number in numbers})
        # A lap of the stoppage this car has no time for is stopped for it
        # too, never the start of a new racing segment.
        own.update(
            lap.lap_number for lap in laps if first <= lap.lap_number <= last and not _timed(lap)
        )
    return own


def untimed_after_suspension(suspended: Iterable[int], timed_laps: set[int], last_lap: int) -> list[int]:
    """Laps nobody timed that directly follow a stoppage, up to the first timed lap.

    On a real Singapore recording the lap after the red flag was lost for
    the whole field; the full record times it at 1.9-2.5 times race pace, a
    stoppage lap. A whole-field hole anywhere else stays a hole.
    """
    added: list[int] = []
    for _first, last in _runs(suspended):
        number = last + 1
        while number <= last_lap and number not in timed_laps:
            added.append(number)
            number += 1
    return added


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
            total += int(lap.lap_time_ms or 0)  # type: ignore[union-attr]
            result[lap_number] = total
    return result, first_gap


def mark_out_laps(laps: list[AnalysisLap]) -> list[AnalysisLap]:
    """Give a one-lap pit visit its out-lap when the lap record split them.

    The player's laps store the pit status at the moment the lap ends: the
    in-lap ends in the pit lane (pit context), the out-lap ends on track (no
    pit context). Field cars record pit status over the whole lap, so both
    laps carry it. A lap right after a one-lap pit visit that starts on a new
    set of tyres (age back to 0, or another compound) is that visit's
    out-lap.
    """
    ordered = _ordered_laps(laps)
    marked = list(ordered)
    for position, lap in enumerate(ordered[:-1]):
        following = ordered[position + 1]
        previous = ordered[position - 1] if position else None
        if not (lap.pit_context and lap.context_observed) or following.pit_context:
            continue
        if previous is not None and previous.pit_context and previous.lap_number == lap.lap_number - 1:
            continue  # already a two-lap visit
        if following.lap_number != lap.lap_number + 1 or not following.context_observed:
            continue
        fresh = following.tyre_age_laps == 0 or (
            bool(lap.compound) and bool(following.compound) and lap.compound != following.compound
        )
        if fresh:
            marked[position + 1] = replace(following, pit_context=True)
    return marked


def pit_runs(laps: list[AnalysisLap]) -> list[list[AnalysisLap]]:
    """Contiguous runs of laps observed with pit context."""
    runs: list[list[AnalysisLap]] = []
    previous: AnalysisLap | None = None
    for lap in _ordered_laps(laps):
        if lap.pit_context and lap.context_observed:
            if runs and previous is not None and previous.pit_context and previous.context_observed and (
                lap.lap_number == previous.lap_number + 1
            ):
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
    under a red flag, or a stop nobody recorded) also starts a stint.
    """
    ordered = [
        lap for lap in _ordered_laps(laps)
        if lap.lap_number >= 1 and (lap.lap_time_ms is not None or lap.compound)
    ]
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
    for lap in [*ordered, None]:
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


def stops_known(laps: list[AnalysisLap], last_lap: int, *, suspended: Iterable[int] = ()) -> bool:
    """Whether every completed lap that could hide a stop is accounted for.

    A lap recorded without telemetry context could have been a pit lap. It is
    accounted for when tyre data (compound and age) exists on both sides of
    it - a stop there would show as a tyre change, and no change means no
    stop - or when it was driven at racing pace: a stop always puts the time
    stationary and at the pit-lane limit into one lap, far beyond 107% of the
    car's usual pace.
    """
    stopped = set(suspended)
    evidence = sorted(lap.lap_number for lap in laps if _tyre_evidence(lap))
    racing = [
        int(lap.lap_time_ms or 0) for lap in laps
        if _timed(lap) and lap.lap_number > 1 and lap.lap_number not in stopped
    ]
    usual = median(racing) if racing else None
    for lap in laps:
        if lap.lap_number > last_lap or lap.context_observed or _tyre_evidence(lap):
            continue
        before = any(number < lap.lap_number for number in evidence)
        after = any(number > lap.lap_number for number in evidence)
        if before and after:
            continue
        if (
            usual is not None
            and _timed(lap)
            and lap.lap_number not in stopped
            and int(lap.lap_time_ms or 0) <= usual * OUTLIER_RATIO
        ):
            continue
        return False
    return True


PACE_EXCLUSION_REASONS = (
    "missing_time",
    "lap_one",
    "suspended",
    "restart",
    "safety_car",
    "vsc",
    "unconfirmed",
    "driver_reported",
    "no_context",
    "pit",
    "flags",
    "invalid",
    "outlier",
)
STOP_KINDS = ("pit_stop", "tyre_change", "unrecorded_stop")
DRIVER_STATUSES = ("finished", "lapped", "retired", "incomplete_record", "running", "unranked", "no_data")


def _pace_exclusion(
    lap: AnalysisLap,
    *,
    is_race: bool,
    suspended: set[int],
    restarts: set[int],
    neutral: Mapping[int, str],
) -> str | None:
    if not _timed(lap):
        return "missing_time"
    if is_race and lap.lap_number == 1:
        return "lap_one"
    if lap.lap_number in suspended:
        return "suspended"
    if is_race and lap.lap_number in restarts:
        # A restart from the grid is timed from lights out, like lap 1.
        return "restart"
    if lap.lap_number in neutral:
        return "vsc" if neutral[lap.lap_number] == "VSC" else "safety_car"
    if lap.unconfirmed:
        return "unconfirmed"
    if lap.driver_excluded:
        # A lap note from the driver or the engineer: compromised, not pace.
        return "driver_reported"
    if not lap.context_observed:
        # Nobody saw whether this was a pit or a flag lap.
        return "no_context"
    if lap.pit_context:
        return "pit"
    if lap.flag_context:
        # A yellow (or other flag) shown to this car on this lap.
        return "flags"
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
    neutral: Mapping[int, str] | None = None,
) -> tuple[dict[str, Any] | None, dict[int, str]]:
    """Box statistics of representative laps and why each other lap was left out."""
    stopped = set(suspended)
    restarts = set(restart_laps)
    neutral_laps = neutral or {}
    excluded: dict[int, str] = {}
    candidates: list[AnalysisLap] = []
    for lap in laps:
        reason = _pace_exclusion(
            lap, is_race=is_race, suspended=stopped, restarts=restarts, neutral=neutral_laps
        )
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


def _consistent_sectors(lap: AnalysisLap) -> bool:
    """All three sectors present and summing to the lap time (rounding allowed)."""
    if not _timed(lap) or not (lap.s1_ms and lap.s2_ms and lap.s3_ms):
        return False
    total = int(lap.s1_ms) + int(lap.s2_ms) + int(lap.s3_ms)
    return abs(total - int(lap.lap_time_ms or 0)) <= SECTOR_TOLERANCE_MS


def _ideal_within(ideal: int | None, best: int | None) -> int | None:
    """An ideal lap slower than an actual lap is not an ideal lap.

    Sector splits are rounded, so an ideal up to the tolerance above the best
    lap is the best lap itself.
    """
    if ideal is None or best is None:
        return ideal
    if ideal <= best:
        return ideal
    return best if ideal - best <= SECTOR_TOLERANCE_MS else None


# The game's final-classification result statuses.
OFFICIAL_RESULT_STATUSES = {3: "finished", 4: "dnf", 5: "dsq", 6: "nc", 7: "retired"}


@dataclass(frozen=True, slots=True)
class OfficialResult:
    """One car's result from the game's final classification."""

    car_index: int
    position: int
    laps: int
    status: str  # a DRIVER_STATUSES value: finished, lapped or retired
    result: str  # the game's status: finished, dnf, dsq, nc or retired
    laps_down: int | None
    race_time_ms: int | None
    penalties_ms: int
    gap_to_winner_ms: int | None
    grid_position: int | None
    pit_stops: int | None
    best_lap_ms: int | None


def official_results(classification: Mapping[str, Any] | None) -> dict[int, OfficialResult]:
    """Every car's result from the game's final classification, by car index.

    The game ranks by laps and race time with penalties applied, so the gap to
    the winner includes penalties too. Cars with a status other than
    finished, did not finish, disqualified, not classified or retired are
    left out; fewer than two classified cars is no classification at all.
    """
    if not isinstance(classification, Mapping):
        return {}
    entries = []
    for entry in classification.get("cars") or []:
        if not isinstance(entry, Mapping):
            continue
        index = entry.get("car_index")
        position = _int_or_none(entry.get("position"))
        code = entry.get("result_status")
        if not isinstance(index, int) or position is None or code not in OFFICIAL_RESULT_STATUSES:
            continue
        entries.append(entry)
    positions = [int(entry["position"]) for entry in entries]
    if len(entries) < 2 or len(set(positions)) != len(positions):
        return {}
    winner = min(entries, key=lambda entry: int(entry["position"]))
    if OFFICIAL_RESULT_STATUSES[winner["result_status"]] != "finished":
        return {}

    def total(entry: Mapping[str, Any]) -> float:
        return float(entry.get("total_race_time_s") or 0.0) + float(entry.get("penalties_s") or 0.0)

    winner_laps = int(winner.get("laps") or 0)
    results: dict[int, OfficialResult] = {}
    for entry in entries:
        result = OFFICIAL_RESULT_STATUSES[entry["result_status"]]
        laps = int(entry.get("laps") or 0)
        classified = result == "finished"
        status = ("finished" if laps >= winner_laps else "lapped") if classified else "retired"
        race_time = float(entry.get("total_race_time_s") or 0.0)
        results[int(entry["car_index"])] = OfficialResult(
            car_index=int(entry["car_index"]),
            position=int(entry["position"]),
            laps=laps,
            status=status,
            result=result,
            laps_down=max(0, winner_laps - laps) if classified else None,
            race_time_ms=round(race_time * 1000) if classified and race_time > 0 else None,
            penalties_ms=round(float(entry.get("penalties_s") or 0.0) * 1000),
            gap_to_winner_ms=round((total(entry) - total(winner)) * 1000) if status == "finished" else None,
            grid_position=_int_or_none(entry.get("grid_position")),
            pit_stops=int(entry["pit_stops"]) if isinstance(entry.get("pit_stops"), int) else None,
            best_lap_ms=_int_or_none(entry.get("best_lap_ms")),
        )
    return results


# A recorded lap-end position from a trace covering less of the lap than this
# may have been sampled well before the line: shown per lap, never a finish.
MIN_POSITION_COVERAGE = 0.8
# PENA penalty types: 4 a time penalty (seconds in "time"), 16 retired.
PENALTY_TIME = 4
PENALTY_RETIRED = 16
RETIREMENT_REASONS = {
    1: "retired", 2: "finished", 3: "terminal damage", 4: "inactive", 5: "not enough laps completed",
    6: "black flagged", 7: "red flagged", 8: "mechanical failure", 9: "session skipped", 10: "session simulated",
}


def retirement_events(incidents: Iterable[Mapping[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """RTMT and PENA type-16 messages per car, in order (RTMT is re-sent after flashbacks)."""
    found: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for event in incidents:
        kind = str(event.get("type") or "").upper()
        payload = event.get("payload") or {}
        if not isinstance(payload, Mapping) or not isinstance(payload.get("vehicle_idx"), int):
            continue
        lap = _event_lap(event.get("lap"))
        if kind == "RTMT":
            reason = int(payload.get("reason") or 0)
            if reason != 2:  # "finished" is not a retirement
                found[int(payload["vehicle_idx"])].append(
                    {"lap": lap, "reason": RETIREMENT_REASONS.get(reason, "retired")}
                )
        elif kind == "PENA" and payload.get("type") == PENALTY_RETIRED:
            reason = {41: "mechanical failure", 42: "terminal damage"}.get(payload.get("infringement"), "retired")
            found[int(payload["vehicle_idx"])].append({"lap": lap, "reason": reason})
    return found


def time_penalties(incidents: Iterable[Mapping[str, Any]]) -> dict[int, int]:
    """Seconds of time penalty per car from PENA messages (never applied here)."""
    found: dict[int, int] = defaultdict(int)
    for event in incidents:
        payload = event.get("payload") or {}
        if str(event.get("type") or "").upper() != "PENA" or not isinstance(payload, Mapping):
            continue
        seconds = payload.get("time")
        if (
            payload.get("type") == PENALTY_TIME
            and isinstance(payload.get("vehicle_idx"), int)
            and isinstance(seconds, int)
            and 0 < seconds < 255
        ):
            found[int(payload["vehicle_idx"])] += seconds
    return dict(found)


def chequered_flag_position(incidents: Iterable[Mapping[str, Any]]) -> int | None:
    """The player's position in the game's chequered-flag message, if one was sent."""
    for event in incidents:
        payload = event.get("payload") or {}
        if str(event.get("type") or "").upper() == "CHQF" and isinstance(payload, Mapping):
            return _int_or_none(payload.get("position"))
    return None


@dataclass(slots=True)
class RaceOrder:
    """The race order per lap and at the finish, and where each figure comes from."""

    positions: dict[tuple[int, int], int]
    sources: dict[tuple[int, int], str]
    partial: set[tuple[int, int]]
    gaps: dict[tuple[int, int], int]
    intervals: dict[tuple[int, int], int]
    use_recorded: bool
    derived_sufficient: bool
    finish: dict[int, int]
    finish_source: dict[int, str]
    finish_lap: dict[int, int]
    status: dict[int, str]
    laps_completed: dict[int, int | None]
    laps_down: dict[int, int | None]
    retirement: dict[int, dict[str, Any]]
    classified_at: dict[int, int | None]
    last_known: dict[int, dict[str, int] | None]
    last_row: dict[int, int]
    winner: int | None
    winner_laps: int | None
    classification: dict[int, OfficialResult] | None
    penalties: dict[int, int]
    reached: bool
    last_row_lap: int
    slots: int
    warnings: list[str]


def classify_race(
    *,
    by_car: Mapping[int, list[AnalysisLap]],
    session: Mapping[str, Any],
    is_race: bool,
    provisional: bool,
    names: Mapping[int, str],
    player_index: int | None,
    completed: Mapping[int, int],
    elapsed: Mapping[int, Mapping[int, int]],
    own_segments: Mapping[int, list[tuple[int, int]]],
    record_gaps: Mapping[int, int | None],
    suspended: set[int],
    derived_positions: Mapping[tuple[int, int], int],
    derived_finish: list[int],
    order_lap: Callable[[int], int | None],
    cars_with_laps: int,
    last_timed_lap: int,
    incidents: list[Mapping[str, Any]],
    lap_chart: Mapping[int, Iterable[int]] | None,
    recorded_positions: Callable[[Iterable[AnalysisLap]], Mapping[str, int]] | None,
    classification: Mapping[str, Any] | None = None,
) -> RaceOrder:
    """Order the field per lap and at the finish from the best evidence on record.

    The game's final classification, when the recording kept it, is the
    finish. Otherwise lap times summed between suspensions order the field
    when the record is sufficient: no car has a hole, every unfinished last
    lap is a reported retirement and the leader is timed to the race
    distance. When it is not (older recordings lost lap rows), the game's
    own race position at the end of each lap - its lap chart, the player's
    lap rows, or the last position sample of a lap's trace - is the order.
    A finish is placed only from a settled position on the final lap, or
    from the lap before when that position is free at the finish and the car
    behind it is still behind; retired cars and records that simply end are
    not placed. Positions are never ranks within the cars on record.
    """
    all_indices = sorted(set(names) | set(by_car))
    rows = {(lap.car_index, lap.lap_number): lap for car_laps in by_car.values() for lap in car_laps}
    last_row = {index: max(lap.lap_number for lap in car_laps) for index, car_laps in by_car.items() if car_laps}
    last_row_lap = max(last_row.values(), default=0)
    total_laps = _int_or_none(session.get("total_laps"))
    warnings: list[str] = []
    notes: dict[int, list[str]] = defaultdict(list)
    retire_events = retirement_events(incidents)
    official = official_results(classification) if is_race and not provisional else {}
    record_holes = any(gap is not None for gap in record_gaps.values())
    unconfirmed = any(lap.unconfirmed for lap in rows.values())
    untimed_unexplained = [
        index for index in by_car
        if last_row[index] > completed.get(index, 0)
        and not any(
            event["lap"] is not None and event["lap"] >= last_row[index] - 1
            for event in retire_events.get(index, [])
        )
    ]
    reached = not provisional and bool(total_laps) and last_row_lap >= int(total_laps or 0)
    derived_sufficient = (
        not record_holes
        and not unconfirmed
        and not untimed_unexplained
        and (provisional or not total_laps or last_timed_lap >= int(total_laps))
    )

    # The game's recorded positions, read only when lap times cannot order the field.
    recorded: dict[tuple[int, int], int] = {}
    settled_keys: set[tuple[int, int]] = set()
    if is_race and not derived_sufficient:
        chart = {int(number): list(values) for number, values in (lap_chart or {}).items()}
        for (index, number) in rows:
            values = chart.get(number) or []
            if 0 <= index < len(values) and int(values[index] or 0) > 0:
                recorded[(index, number)] = int(values[index])
                settled_keys.add((index, number))
        unread = [lap for key, lap in rows.items() if key not in recorded]
        for lap in unread:
            if lap.recorded_position:
                recorded[(lap.car_index, lap.lap_number)] = int(lap.recorded_position)
                settled_keys.add((lap.car_index, lap.lap_number))
        unread = [lap for lap in unread if (lap.car_index, lap.lap_number) not in recorded]
        if recorded_positions is not None and unread:
            read = recorded_positions(unread)
            for lap in unread:
                value = _int_or_none(read.get(lap.lap_id))
                if value is not None:
                    recorded[(lap.car_index, lap.lap_number)] = value
    use_recorded = bool(recorded)
    partial = {
        key for key in recorded
        if key not in settled_keys
        and rows[key].coverage_ratio is not None
        and rows[key].coverage_ratio < MIN_POSITION_COVERAGE
    }
    settled = {key: value for key, value in recorded.items() if key not in partial}
    field_size = len(all_indices)

    # Per-lap positions.
    positions: dict[tuple[int, int], int] = {}
    sources: dict[tuple[int, int], str] = {}
    if is_race and use_recorded:
        for number in range(1, last_row_lap + 1):
            at_lap = {index: recorded[(index, number)] for index in by_car if (index, number) in recorded}
            for index, value in at_lap.items():
                positions[(index, number)] = value
                sources[(index, number)] = "recorded"
            if number in suspended:
                # Nobody races while the race is stopped: a car without a sample holds its place.
                for index in by_car:
                    key = (index, number)
                    if key in rows and key not in positions and (index, number - 1) in positions:
                        positions[key] = positions[(index, number - 1)]
                        sources[key] = "held"
                continue
            # Lap times fill an untraced car only where the filled order agrees with every traced car.
            timed_here = {index: elapsed[index][number] for index in elapsed if number in elapsed[index]}
            untraced = sorted(
                (index for index in timed_here if (index, number) not in positions and (index, number) in rows),
                key=lambda index: timed_here[index],
            )
            if not untraced or not at_lap:
                continue
            free = [value for value in range(1, field_size + 1) if value not in set(at_lap.values())]
            if len(untraced) > len(free):
                continue
            merged = dict(zip(untraced, free))
            sequence = sorted([(value, index) for index, value in at_lap.items()] + [(value, index) for index, value in merged.items()])
            times = [timed_here[index] for _, index in sequence if index in timed_here]
            if all(a <= b for a, b in pairwise(times)):
                for index, value in merged.items():
                    positions[(index, number)] = value
                    sources[(index, number)] = "derived"
    elif is_race:
        positions = dict(derived_positions)
        sources = {key: "derived" for key in positions}

    # Gaps: summed times relative to the car placed first on the lap.
    gaps: dict[tuple[int, int], int] = {}
    intervals: dict[tuple[int, int], int] = {}
    if is_race and use_recorded:
        placed_by_lap: dict[int, dict[int, int]] = defaultdict(dict)
        for (index, number), value in positions.items():
            placed_by_lap[number][index] = value
        for number, placed in placed_by_lap.items():
            if number in suspended:
                continue
            leader = min(placed, key=lambda index: placed[index])
            if placed[leader] != 1 or number not in elapsed.get(leader, {}):
                continue
            leader_time = elapsed[leader][number]
            leader_segment = _segment_of(own_segments[leader], number)
            by_position = {value: index for index, value in placed.items()}
            for index, value in placed.items():
                own = elapsed.get(index, {}).get(number)
                if own is None or _segment_of(own_segments[index], number) != leader_segment:
                    continue
                gaps[(index, number)] = own - leader_time
                ahead = by_position.get(value - 1)
                ahead_time = elapsed.get(ahead, {}).get(number) if ahead is not None else None
                if value > 1 and ahead_time is not None and _segment_of(own_segments[ahead], number) == leader_segment:
                    intervals[(index, number)] = own - ahead_time

    finish: dict[int, int] = {}
    finish_source: dict[int, str] = {}
    finish_lap: dict[int, int] = {}
    status: dict[int, str] = {index: "no_data" for index in all_indices}
    laps_completed: dict[int, int | None] = {index: None for index in all_indices}
    laps_down: dict[int, int | None] = {index: None for index in all_indices}
    classified_at: dict[int, int | None] = {index: None for index in all_indices}
    retirement: dict[int, dict[str, Any]] = {}
    winner: int | None = None
    winner_laps: int | None = None
    last_known: dict[int, dict[str, int] | None] = {}
    for index in all_indices:
        points = sorted((number, value) for (car, number), value in recorded.items() if car == index)
        last_known[index] = {"lap": points[-1][0], "position": points[-1][1]} if points else None

    def event_retirement(index: int) -> dict[str, Any] | None:
        found = [event for event in retire_events.get(index, []) if event["lap"] is not None]
        if not found:
            return None
        latest = max(found, key=lambda event: event["lap"])
        if last_row[index] > latest["lap"] + 1:
            notes[index].append("a retirement reported by the game was undone by a flashback")
            return None
        unfinished = not _timed(rows[(index, last_row[index])])
        return {
            "lap": last_row[index] if unfinished else None,
            "after_lap": None if unfinished else last_row[index],
            "evidence": "event",
            "reason": latest["reason"],
        }

    if not is_race:
        for index in all_indices:
            if completed.get(index):
                status[index] = "running"
                laps_completed[index] = completed[index]
    elif not use_recorded:
        # Lap times order the field (validated against an official result).
        winner = derived_finish[0] if derived_finish else None
        winner_laps = order_lap(winner) if winner is not None else None
        winner_time = elapsed[winner][winner_laps] if winner is not None and winner_laps is not None else None
        uncertain: set[int] = set()
        if not derived_sufficient:
            # Without the game's positions a hole leaves some cars unplaced, and
            # an unplaced car that ran as far could be ahead of anyone: a place
            # is only kept when every car that could be ahead of it is placed.
            unplaced = [index for index in by_car if index not in derived_finish and completed.get(index, 0)]
            for index in derived_finish:
                classified_lap = order_lap(index) or 0
                if any(last_row[other] >= classified_lap for other in unplaced):
                    uncertain.add(index)
            if uncertain:
                first = min(derived_finish.index(index) for index in uncertain)
                uncertain = set(derived_finish[first:])
                if derived_finish[0] in uncertain:
                    winner, winner_laps, winner_time = None, None, None
        for position, index in enumerate(derived_finish, 1):
            if index in uncertain:
                continue
            finish[index] = position
            finish_source[index] = "lap_times"
            finish_lap[index] = order_lap(index) or 0
        for index in all_indices:
            car_laps = by_car.get(index, [])
            done = completed.get(index, 0)
            placed = index in finish
            number = order_lap(index) if placed else None
            classified_at[index] = number if number is not None and number != done else None
            laps_completed[index] = done if car_laps else 0
            if not done and car_laps and cars_with_laps:
                # It started (a lap row exists) and never completed a lap.
                status[index] = "running" if provisional else "retired"
            elif done:
                if cars_with_laps < 2:
                    status[index] = "running" if provisional else "unranked"
                elif not placed or winner is None:
                    status[index] = "incomplete_record"
                else:
                    down = max(0, int(winner_laps or 0) - int(number or 0))
                    laps_down[index] = down
                    own_time = elapsed.get(index, {}).get(int(number or 0))
                    same_segment = winner is not None and _segment_of(own_segments[index], int(number or 0)) == _segment_of(
                        own_segments[winner], int(winner_laps or 0)
                    )
                    if provisional:
                        status[index] = "running"
                    elif down == 0:
                        status[index] = "finished"
                    elif same_segment and winner_time is not None and own_time is not None and own_time >= winner_time:
                        # Crossed the line after the winner: classified, laps down.
                        status[index] = "lapped"
                    else:
                        status[index] = "retired"
            if status[index] == "retired" and car_laps:
                retirement[index] = event_retirement(index) or {
                    "lap": None, "after_lap": done, "evidence": "record", "reason": None,
                }
            if record_gaps.get(index) is not None:
                warnings.append(
                    f"{names.get(index, index)}: lap {record_gaps[index]} has no recorded time, so race order and gaps "
                    "for this car stop there until the next restart."
                )
    else:
        final = last_row_lap
        continuing_after: dict[int, set[int]] = defaultdict(set)
        for index in by_car:
            for number in range(1, last_row[index]):
                continuing_after[number].add(index)
        for index in all_indices:
            if not by_car.get(index):
                continue
            found = event_retirement(index)
            if found:
                retirement[index] = found
                continue
            # The game moves a retired car behind every running car at once: an
            # unfinished last lap, a lost place doing so, and the race going on at
            # least two more laps without it.
            number = last_row[index]
            value = recorded.get((index, number))
            previous = [recorded[(index, m)] for m in range(number - 1, 0, -1) if (index, m) in recorded][:1]
            if not _timed(rows[(index, number)]) and number <= final - 2 and value is not None and previous and value > previous[0]:
                going_on = continuing_after.get(number, set())
                ahead = [recorded[(car, number)] for car in going_on if (car, number) in recorded]
                if going_on and value > len(going_on) and (not ahead or value > max(ahead)):
                    retirement[index] = {
                        "lap": number, "after_lap": None, "evidence": "record",
                        "reason": "moved to the back of the field",
                    }
        finish_known = reached and cars_with_laps >= 2 and not official
        if not reached and not official and not provisional and cars_with_laps >= 2 and total_laps:
            warnings.append(
                f"The recording stops at lap {last_row_lap} of {total_laps}, so the finish is not on record; "
                "positions are shown per lap only."
            )
        at_final = {index: settled[(index, final)] for index in by_car if (index, final) in settled and index not in retirement}
        partial_final = [index for index in by_car if (index, final) in partial and index not in retirement]
        if partial_final and (finish_known or provisional):
            warnings.append(
                f"{', '.join(names.get(index, str(index)) for index in partial_final)}: the final-lap trace stops "
                "before the line, so the recorded position there does not settle the result."
            )
        shared = {value for value, count in Counter(at_final.values()).items() if count > 1}
        for value in sorted(shared):
            together = [names.get(index, str(index)) for index in at_final if at_final[index] == value]
            warnings.append(f"{' and '.join(together)} share recorded position {value} on lap {final}, so neither can be placed.")
        taken = {value for value in at_final.values() if value not in shared}
        flag_position = chequered_flag_position(incidents) if player_index is not None else None
        flag_laps_unknown: set[int] = set()
        candidates: dict[int, int] = {}
        if finish_known or provisional:
            for index, value in at_final.items():
                if value not in shared:
                    candidates[index] = value
                    finish_source[index] = "recorded"
                    finish_lap[index] = final
        if finish_known:
            others = [index for index in by_car if index not in at_final and index not in retirement and index not in partial_final]
            last_settled: dict[int, tuple[int, int]] = {}
            for index in others:
                points = sorted((number, value) for (car, number), value in settled.items() if car == index)
                if points:
                    last_settled[index] = points[-1]
            for index in others:
                if index not in last_settled:
                    continue
                lap_q, value = last_settled[index]
                if lap_q != final - 1:
                    continue
                if value in taken or value in shared:
                    holder = next((names.get(car, str(car)) for car, held in at_final.items() if held == value), "another car")
                    notes[index].append(f"last known P{value} on lap {lap_q}, but {holder} holds P{value} at the finish, so its result is unknown")
                    continue
                if any(car != index and lap >= final - 1 and held == value for car, (lap, held) in last_settled.items()):
                    notes[index].append(f"last known P{value} on lap {lap_q}, shared with another car without a final-lap record")
                    continue
                if value > max(at_final.values(), default=0):
                    notes[index].append(
                        f"last known P{value} on lap {lap_q}, behind every car recorded at the finish, so it may have "
                        "been lapped; its result is unknown"
                    )
                    continue
                behind = [car for car in by_car if settled.get((car, final - 1)) == value + 1]
                if len(behind) != 1 or settled.get((behind[0], final)) != value + 1:
                    notes[index].append(
                        f"last known P{value} on lap {lap_q}; the car behind it then has no matching final-lap record, "
                        "so its result is unknown"
                    )
                    continue
                if index == player_index and flag_position is not None and flag_position != value:
                    notes[index].append(f"last known P{value} on lap {lap_q}, but the game reported P{flag_position} at the flag, so its result is unknown")
                    continue
                candidates[index] = value
                finish_source[index] = "matched"
                finish_lap[index] = lap_q
                notes[index].append(f"no recorded final lap; placed P{value} from lap {lap_q}, a position no other car holds at the finish")
            if player_index is not None and player_index not in candidates and player_index not in retirement and flag_position:
                known = last_known.get(player_index) or {}
                if flag_position in taken or flag_position in shared:
                    holder = next((names.get(car, str(car)) for car, held in at_final.items() if held == flag_position), "another car")
                    notes[player_index].append(f"the game reported P{flag_position} at the flag, but {holder} holds P{flag_position} on lap {final}, so the result is unknown")
                elif player_index in last_row and last_row[player_index] >= final - 1 and known.get("position") == flag_position:
                    candidates[player_index] = flag_position
                    finish_source[player_index] = "flag"
                    finish_lap[player_index] = final
                    notes[player_index] = [
                        f"no recorded final lap; placed P{flag_position} from the game's chequered-flag message, "
                        f"the same position it held on lap {known.get('lap')}"
                    ]
                    if flag_position > max(at_final.values(), default=0):
                        flag_laps_unknown.add(player_index)
                else:
                    notes[player_index].append(
                        f"the game reported P{flag_position} at the flag, which differs from its last recorded position, so the result is unknown"
                    )
            # A retirement on the final lap unsettles every position from the
            # retiree's own final-lap sample backwards: cars behind it were sampled
            # before or after the game moved it back, and nobody can tell which.
            late = [index for index in retirement if (retirement[index].get("lap") or 0) >= final and (index, final) in recorded]
            if late:
                unsettled_from = min(recorded[(index, final)] for index in late)
                for index in [car for car, value in candidates.items() if value >= unsettled_from]:
                    del candidates[index]
                    finish_source.pop(index, None)
                    finish_lap.pop(index, None)
                warnings.append(
                    f"{', '.join(names.get(index, str(index)) for index in late)} retired on the final lap; "
                    f"positions behind P{unsettled_from} could not be settled."
                )
        finish = dict(candidates)
        for index in all_indices:
            if not by_car.get(index):
                continue
            if index in retirement:
                status[index] = "retired"
                laps_completed[index] = completed.get(index, 0)
            elif provisional:
                status[index] = "running"
                laps_completed[index] = last_row[index] if (index, last_row[index]) in recorded else completed.get(index, 0)
            elif cars_with_laps < 2:
                status[index] = "unranked"
                laps_completed[index] = completed.get(index, 0)
            elif index in finish:
                status[index] = "finished"
                if index in flag_laps_unknown:
                    notes[index].append(
                        "its lap count is unknown: it finished behind every car recorded on the final lap and may have been lapped"
                    )
                else:
                    laps_completed[index] = final
                    laps_down[index] = 0
            else:
                status[index] = "incomplete_record"
                if finish_known:
                    known = last_known.get(index)
                    tail = f"; last recorded P{known['position']} on lap {known['lap']}" if known else ""
                    notes[index].append(
                        f"the record ends at lap {last_row[index]} of {final} with no sign of a retirement, so its result is unknown{tail}"
                    )
        if finish:
            winner = next((index for index, value in finish.items() if value == 1), None)
            winner_laps = final if winner is not None else None
        for index, found in ({} if official else retirement).items():
            where = f"on lap {found['lap']}" if found.get("lap") else f"after lap {found.get('after_lap')}"
            if found["evidence"] == "event":
                warnings.append(f"{names.get(index, index)}: retired {where} ({found['reason']}, reported by the game); retired cars are not placed.")
            else:
                warnings.append(f"{names.get(index, index)}: retired {where} (the game moved it to the back of the field); retired cars are not placed.")
        if finish_known and not finish:
            warnings.append("No car has a usable recorded position on the final lap, so the finish cannot be placed.")
        if finish_known and finish and all(final not in elapsed.get(index, {}) for index in finish):
            warnings.append("Finish gaps are unknown: the final lap has no recorded times.")
    for index, items in notes.items():
        for text in items:
            warnings.append(f"{names.get(index, index)}: {text}.")

    # The game's own final classification, when the recording kept it, is the finish.
    classified: dict[int, OfficialResult] | None = None
    if official:
        classified = {index: result for index, result in official.items() if index in names or index in by_car}
        finish, finish_source, finish_lap = {}, {}, {}
        for index in all_indices:
            result = classified.get(index)
            if result is None:
                if by_car.get(index) and status[index] in ("finished", "lapped"):
                    status[index] = "incomplete_record"
                continue
            finish[index] = result.position
            finish_source[index] = "classification"
            status[index] = result.status
            laps_completed[index] = result.laps
            laps_down[index] = result.laps_down
            classified_at[index] = None
            if result.status == "retired":
                retirement[index] = {"lap": None, "after_lap": result.laps, "evidence": "classification", "reason": result.result}
            else:
                retirement.pop(index, None)
        winner = next((index for index, result in classified.items() if result.position == 1), None)
        winner_laps = classified[winner].laps if winner is not None else None
    penalties = time_penalties(incidents)
    named_penalties = [(index, seconds) for index, seconds in penalties.items() if index in names]
    if is_race and use_recorded and finish and named_penalties and classified is None:
        text = ", ".join(f"{names[index]} {seconds} s" for index, seconds in sorted(named_penalties, key=lambda item: -item[1]))
        warnings.append(
            f"Time penalties on record: {text}. Positions here are the order on the road; the game applies "
            "penalties in its final classification, so cars within those gaps may be classified differently."
        )
    slots = max(field_size, max(recorded.values(), default=0))
    if use_recorded and slots > field_size:
        warnings.append(f"The game numbers positions over {slots} grid slots; {field_size} cars have laps on record.")
    return RaceOrder(
        positions=positions, sources=sources, partial=partial, gaps=gaps, intervals=intervals,
        use_recorded=use_recorded, derived_sufficient=derived_sufficient, finish=finish,
        finish_source=finish_source, finish_lap=finish_lap, status=status, laps_completed=laps_completed,
        laps_down=laps_down, retirement=retirement, classified_at=classified_at, last_known=last_known,
        last_row=last_row, winner=winner, winner_laps=winner_laps, classification=classified,
        penalties=penalties, reached=reached, last_row_lap=last_row_lap, slots=slots, warnings=warnings,
    )


def _grid_position(lap_chart: Mapping[int, Iterable[int]] | None, index: int) -> int | None:
    """A car's grid slot from the game's lap chart (its row 0), when recorded."""
    grid = list((lap_chart or {}).get(0) or [])
    return int(grid[index]) if 0 <= index < len(grid) and int(grid[index] or 0) > 0 else None


def _segment_of(segments: list[tuple[int, int]], lap_number: int) -> int | None:
    for position, (first, last) in enumerate(segments):
        if first <= lap_number <= last:
            return position
    return None


def build_session_analysis(
    session: dict[str, Any],
    cars: list[AnalysisCar],
    laps: list[AnalysisLap],
    *,
    hide_outliers: bool = True,
    events: Iterable[Mapping[str, Any]] | None = None,
    official_player_position: int | None = None,
    race_control_from_lap: int | None = None,
    incidents: Iterable[Mapping[str, Any]] = (),
    classification: Mapping[str, Any] | None = None,
    lap_chart: Mapping[int, Iterable[int]] | None = None,
    recorded_positions: Callable[[Iterable[AnalysisLap]], Mapping[str, int]] | None = None,
) -> dict[str, Any]:
    """Assemble the analysis payload from cars and their active lap rows.

    ``events`` are the session's race-control messages (None when they were
    not recorded) and ``race_control_from_lap`` the first lap any session
    message was recorded on; ``official_player_position`` is the player's
    result from the game's final classification when one is on record.
    ``incidents`` are the game's RTMT, PENA and CHQF messages, ``classification`` its
    final classification of every car and ``lap_chart`` its lap chart, when
    the recording kept them. ``recorded_positions`` returns the game's race
    position at the end of the given laps where one was recorded (lap id ->
    position); it may read traces, so it is only called when lap times
    cannot order the field.
    """
    laps = [lap for lap in laps if lap.lap_number >= 1]
    is_race = is_race_session(session.get("session_type"), session.get("raw_session_type_id"))
    # The game's final classification means the race finished, whatever
    # became of the recording afterwards.
    provisional = str(session.get("status") or "").strip().lower() in PROVISIONAL_STATUSES and not (
        is_race and official_results(classification)
    )
    by_car: dict[int, list[AnalysisLap]] = defaultdict(list)
    for lap in laps:
        by_car[lap.car_index].append(lap)
    for index, car_laps in by_car.items():
        by_car[index] = mark_out_laps(car_laps)
    warnings: list[str] = []
    taken_codes: set[str] = set()
    car_by_index = {car.car_index: car for car in cars}

    last_lap = max((lap.lap_number for lap in laps if _timed(lap)), default=0)
    pace_by_lap, _ = field_pace(by_car)
    usual = usual_pace(by_car)
    race_control = race_control_from_events(events, last_lap, pace_by_lap)
    timed_laps = {lap.lap_number for lap in laps if _timed(lap)}
    suspended = suspended_laps(by_car, race_control.red_flag_laps) if is_race else []
    # A lap nobody timed right after a stoppage is part of it: older
    # recordings can lose the lap back to the grid for the whole field.
    untimed_stoppage = untimed_after_suspension(suspended, timed_laps, last_lap) if is_race else []
    suspended = sorted(set(suspended) | set(untimed_stoppage))
    suspended_set = set(suspended)
    segments = race_segments(last_lap, suspended) if is_race else [(1, last_lap)] if last_lap else []
    # Each car's own suspension: a car a lap down meets the red flag, and
    # restarts, on earlier lap numbers than the leaders.
    own_suspended: dict[int, set[int]] = {}
    own_segments: dict[int, list[tuple[int, int]]] = {}
    elapsed: dict[int, dict[int, int]] = {}
    record_gaps: dict[int, int | None] = {}
    for index, car_laps in by_car.items():
        own = car_suspended_laps(car_laps, suspended, usual.get(index)) if is_race and suspended else set()
        own |= {number for number in untimed_stoppage if any(lap.lap_number > number for lap in car_laps)}
        own_suspended[index] = own
        own_segments[index] = race_segments(last_lap, own) if is_race else segments
        elapsed[index], record_gaps[index] = segment_times(car_laps, own_segments[index])
    for number in untimed_stoppage:
        warnings.append(
            f"Lap {number} has no recorded time for any car; it follows the stoppage on lap {number - 1} "
            "and counts as part of it."
        )
    # Laps completed: the last lap with a time. A hole in the record leaves
    # the car unplaced (it cannot be timed past the hole).
    completed: dict[int, int] = {}
    for index, car_laps in by_car.items():
        timed = [lap.lap_number for lap in car_laps if _timed(lap)]
        completed[index] = max(timed, default=0)
    leader_laps = max(completed.values(), default=0)
    cars_with_laps = sum(1 for done in completed.values() if done)
    field_size = len(set(car_by_index) | set(by_car))
    field_complete = cars_with_laps >= 2 and cars_with_laps == field_size

    def order_lap(index: int) -> int | None:
        """The lap a car is classified on.

        Normally its last completed lap. When the race was stopped and not
        resumed, every lap after the car's last racing lap is a suspended lap
        and the order is taken at that last racing lap (countback).
        """
        laps_done = completed.get(index, 0)
        times = elapsed.get(index, {})
        if not laps_done:
            return None
        if laps_done in times:
            return laps_done
        racing = [number for number in times if number < laps_done]
        if not racing:
            return None
        last_racing = max(racing)
        stopped = own_suspended.get(index, set())
        if all(number in stopped for number in range(last_racing + 1, laps_done + 1)):
            return last_racing
        return None

    positions: dict[tuple[int, int], int] = {}
    gaps: dict[tuple[int, int], int] = {}
    intervals: dict[tuple[int, int], int] = {}
    if is_race:
        previous_order: list[int] = []
        for lap_number in range(1, leader_laps + 1):
            if lap_number in suspended_set:
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
    # cannot be placed and are listed after the classified cars. One car on
    # its own has no race order.
    def placed_key(index: int) -> tuple[int, int] | None:
        number = order_lap(index)
        if number is None:
            return None
        return (-number, elapsed[index][number])

    finish_order = sorted(
        (index for index in by_car if placed_key(index) is not None),
        key=lambda index: placed_key(index) or (0, 0),
    ) if is_race and cars_with_laps >= 2 else []
    winner = finish_order[0] if finish_order else None
    winner_order = order_lap(winner) if winner is not None else None
    winner_time = elapsed[winner][winner_order] if winner is not None and winner_order is not None else None
    countback = winner is not None and winner_order is not None and winner_order < completed.get(winner, 0)

    player_index = next((car.car_index for car in cars if car.is_player and car.car_index in by_car), None)
    names = {
        index: (car_by_index[index].display_name if index in car_by_index else f"Car {index + 1}")
        for index in set(car_by_index) | set(by_car)
    }
    order = classify_race(
        by_car=by_car,
        session=session,
        is_race=is_race,
        provisional=provisional,
        names=names,
        player_index=player_index,
        completed=completed,
        elapsed=elapsed,
        own_segments=own_segments,
        record_gaps=record_gaps,
        suspended=suspended_set,
        derived_positions=positions,
        derived_finish=finish_order,
        order_lap=order_lap,
        cars_with_laps=cars_with_laps,
        last_timed_lap=last_lap,
        incidents=list(incidents),
        lap_chart=lap_chart,
        recorded_positions=recorded_positions,
        classification=classification,
    )
    warnings.extend(order.warnings)
    positions, sources = order.positions, order.sources
    if order.use_recorded:
        gaps, intervals = order.gaps, order.intervals
    finish_order = sorted(order.finish, key=lambda index: order.finish[index])
    winner = order.winner
    winner_order = order.winner_laps
    winner_time = elapsed.get(winner, {}).get(winner_order) if winner is not None and winner_order else None
    countback = countback and not order.use_recorded and order.classification is None
    official = order.classification or {}

    def race_lap(player_lap: int) -> int:
        """The leading lap while the player was on ``player_lap``.

        Race-control messages carry the player's lap number. A player a lap
        or more down hears them on an earlier lap than the leaders run.
        """
        if player_index is None or not is_race:
            return player_lap
        player_segments = own_segments.get(player_index, [])
        start = 0 if player_lap - 1 < 1 else elapsed.get(player_index, {}).get(player_lap - 1)
        segment = _segment_of(player_segments, max(1, player_lap - 1))
        if start is None or segment is None:
            return player_lap
        leading = player_lap
        for index, times in elapsed.items():
            if index == player_index:
                continue
            ahead = [
                number for number, time_ms in times.items()
                if time_ms <= start and _segment_of(own_segments[index], number) == segment
            ]
            if ahead:
                leading = max(leading, max(ahead) + 1)
        return leading

    neutral_runs = [
        {"kind": kind, "first_lap": race_lap(first), "last_lap": max(race_lap(first), race_lap(last))}
        for kind, first, last in race_control.periods
    ]
    neutral: dict[int, str] = {}
    for run in neutral_runs:
        for lap_number in range(run["first_lap"], run["last_lap"] + 1):
            if lap_number not in suspended_set and neutral.get(lap_number) != "SC":
                neutral[lap_number] = run["kind"]
    red_flag_laps = sorted({race_lap(number) for number in race_control.red_flag_laps})
    red_flag_set = set(red_flag_laps) | set(race_control.red_flag_laps)

    pace_rows: list[dict[str, Any]] = []
    lap_rows: list[dict[str, Any]] = []
    driver_rows: list[dict[str, Any]] = []
    stint_rows: list[dict[str, Any]] = []
    pit_rows: list[dict[str, Any]] = []
    fastest_rows: list[dict[str, Any]] = []
    sector_best: dict[str, dict[str, Any] | None] = {"s1": None, "s2": None, "s3": None}
    unknown_stops: list[str] = []
    unconfirmed_count = 0

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
        car_suspended = own_suspended.get(index, set())
        # Every segment that begins after lap 1 begins with a standing restart,
        # including the first one when the red flag came out on lap 1.
        car_restarts = [first for first, _ in own_segments.get(index, []) if first > 1] if is_race else []
        unconfirmed_count += sum(1 for lap in car_laps if lap.unconfirmed)
        stats, excluded = race_pace(
            car_laps,
            is_race=is_race,
            hide_outliers=hide_outliers,
            suspended=car_suspended,
            restart_laps=car_restarts,
            neutral=neutral,
        )
        if stats is not None:
            pace_rows.append({"car_index": index, **stats, "excluded": dict(Counter(excluded.values()))})
        valid_timed = [lap for lap in car_laps if lap.valid and not lap.unconfirmed and _timed(lap)]
        best = min(valid_timed, key=lambda lap: (lap.lap_time_ms, lap.lap_number), default=None)
        sector_laps = [lap for lap in valid_timed if _consistent_sectors(lap)]
        inconsistent = sum(
            1 for lap in valid_timed if (lap.s1_ms or lap.s2_ms or lap.s3_ms) and not _consistent_sectors(lap)
        )
        sectors: dict[str, dict[str, Any] | None] = {}
        for key in ("s1", "s2", "s3"):
            fastest = min(
                sector_laps, key=lambda lap, name=f"{key}_ms": (getattr(lap, name), lap.lap_number), default=None
            )
            sectors[key] = (
                {"ms": int(getattr(fastest, f"{key}_ms")), "lap_number": fastest.lap_number}
                if fastest
                else None
            )
            current = sector_best[key]
            if sectors[key] and (current is None or sectors[key]["ms"] < current["ms"]):  # type: ignore[index]
                sector_best[key] = {"car_index": index, **sectors[key]}  # type: ignore[dict-item]
        ideal = _ideal_within(
            sum(item["ms"] for item in sectors.values() if item) if all(sectors.values()) else None,
            int(best.lap_time_ms) if best and best.lap_time_ms else None,
        )
        if best is not None:
            fastest_rows.append(
                {
                    "car_index": index,
                    "lap_number": best.lap_number,
                    "lap_id": best.lap_id,
                    "lap_time_ms": int(best.lap_time_ms or 0),
                    "compound": best.compound,
                    "traced": best.traced,
                    "ideal_lap_ms": ideal,
                }
            )
        stints = stints_for(car_laps)
        stint_rows.append({"car_index": index, "stints": stints})
        median_pace = stats["median_ms"] if stats else None
        compounds_by_lap = {lap.lap_number: lap.compound for lap in car_laps}
        runs = pit_runs(car_laps)
        car_stops: list[dict[str, Any]] = []
        explained: set[int] = set()
        for run in runs:
            before = compounds_by_lap.get(run[0].lap_number - 1) or run[0].compound
            after = compounds_by_lap.get(run[-1].lap_number + 1) or run[-1].compound
            run_time = [int(lap.lap_time_ms or 0) for lap in run if _timed(lap)]
            neutral_run = any(
                lap.lap_number in neutral
                or lap.lap_number in car_suspended
                or (not race_control.available and lap.flag_context)
                for lap in run
            )
            loss = (
                round(sum(run_time) - len(run_time) * median_pace)
                if median_pace is not None and len(run_time) == len(run) and not neutral_run
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
                    "under_neutralisation": neutral_run,
                    "estimated_loss_ms": loss,
                }
            )
        # A new stint without a recorded pit visit. With a suspension next to
        # it the tyres were changed while the race was stopped: the game
        # counts it as a stop and no time is lost. Without one, a stop
        # happened that nobody recorded; its cost is unknown.
        for previous, stint in pairwise(stints):
            # Only a recorded change counts: a boundary into or out of laps
            # with no tyre data says nothing about what happened.
            if stint["start_lap"] in explained or not previous["compound"] or not stint["compound"]:
                continue
            between = range(int(previous["end_lap"]), int(stint["start_lap"]))
            stopped_race = any(number in car_suspended for number in between) or any(
                number in red_flag_set
                for number in range(int(previous["end_lap"]) - 1, int(stint["start_lap"]) + 1)
            )
            kind = "tyre_change" if stopped_race else "unrecorded_stop"
            car_stops.append(
                {
                    "car_index": index,
                    "kind": kind,
                    "lap_number": previous["end_lap"],
                    "laps": [previous["end_lap"], stint["start_lap"]],
                    "from_compound": previous["compound"],
                    "to_compound": stint["compound"],
                    "under_neutralisation": stopped_race or any(
                        number in neutral for number in (previous["end_lap"], stint["start_lap"])
                    ),
                    "estimated_loss_ms": None,
                }
            )
            if kind == "unrecorded_stop":
                warnings.append(
                    f"{car.display_name}: tyres changed between laps {previous['end_lap']} and "
                    f"{stint['start_lap']} without a recorded pit stop; its time loss is unknown."
                )
        car_stops.sort(key=lambda row: row["lap_number"])
        pit_rows.extend(car_stops)

        laps_done = completed.get(index, 0)
        gap_lap = record_gaps.get(index)
        status = order.status.get(index, "no_data")
        laps_down = order.laps_down.get(index)
        classified_at = order.classified_at.get(index)
        finish_lap = order.finish_lap.get(index)
        laps_value = order.laps_completed.get(index)
        if laps_value is None and not order.use_recorded and order.classification is None:
            laps_value = laps_done
        result = official.get(index)
        known_stops = stops_known(car_laps, laps_done, suspended=car_suspended)
        if not known_stops and laps_done:
            unknown_stops.append(code)
        own_finish = elapsed.get(index, {}).get(finish_lap) if finish_lap else None
        uninterrupted = is_race and not suspended and laps_done and own_finish is not None
        if result is not None:
            race_time, gap_to_winner = result.race_time_ms, result.gap_to_winner_ms
        else:
            race_time = own_finish if uninterrupted and index in order.finish else None
            gap_to_winner = (
                own_finish - winner_time
                if status == "finished" and winner_time is not None and own_finish is not None
                and finish_lap == winner_order
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
                "laps_completed": laps_value,
                "laps_without_context": sum(
                    1 for lap in car_laps if not lap.context_observed and lap.lap_number <= laps_done
                ),
                "finish_position": order.finish.get(index),
                "finish_source": order.finish_source.get(index),
                "classified_at_lap": classified_at,
                "status": status,
                "laps_down": laps_down,
                "race_time_ms": race_time,
                "gap_to_winner_ms": gap_to_winner,
                "result": result.result if result is not None else None,
                "grid_position": result.grid_position if result is not None else _grid_position(lap_chart, index),
                "penalties_ms": result.penalties_ms if result is not None else None,
                "time_penalties_s": order.penalties.get(index) if result is None else None,
                "retirement": order.retirement.get(index),
                "last_known_position": order.last_known.get(index) if order.use_recorded else None,
                "record_ends_lap": order.last_row.get(index),
                "record_gap_from_lap": gap_lap,
                "best_lap_ms": best.lap_time_ms if best else None,
                "best_lap_number": best.lap_number if best else None,
                "best_lap_id": best.lap_id if best else None,
                "best_lap_traced": best.traced if best else None,
                "best_sectors": sectors,
                "sector_laps_inconsistent": inconsistent,
                "ideal_lap_ms": ideal,
                "pit_stops": len(car_stops) if known_stops else None,
                "pit_stops_recorded": len(car_stops),
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
                    "pit": lap.pit_context if lap.context_observed else None,
                    "flags": lap.flag_context if lap.context_observed else None,
                    "neutralised": neutral.get(lap.lap_number),
                    "suspended": lap.lap_number in car_suspended,
                    "context_observed": lap.context_observed,
                    "unconfirmed": lap.unconfirmed,
                    "driver_excluded": lap.driver_excluded,
                    "traced": lap.traced,
                    "compound": lap.compound,
                    "tyre_age_laps": lap.tyre_age_laps,
                    "segment_time_ms": elapsed.get(index, {}).get(lap.lap_number),
                    "position": positions.get(key),
                    "position_source": sources.get(key),
                    "position_partial_trace": key in order.partial if sources.get(key) == "recorded" else None,
                    "gap_to_leader_ms": gaps.get(key),
                    "interval_ms": intervals.get(key),
                    "pace_excluded": excluded.get(lap.lap_number),
                }
            )

    pace_rows.sort(key=lambda row: (row["median_ms"], row["car_index"]))
    fastest_rows.sort(key=lambda row: (row["lap_time_ms"], row["car_index"]))
    if fastest_rows:
        fastest_time = fastest_rows[0]["lap_time_ms"]
        for row in fastest_rows:
            row["delta_to_fastest_ms"] = row["lap_time_ms"] - fastest_time
    overall_ideal = _ideal_within(
        sum(item["ms"] for item in sector_best.values() if item) if all(sector_best.values()) else None,
        fastest_rows[0]["lap_time_ms"] if fastest_rows else None,
    )
    record_complete = not any(gap is not None for gap in record_gaps.values()) and not unconfirmed_count
    player = next((row for row in driver_rows if row["is_player"]), None)
    derived_player_position = player["finish_position"] if player else None
    official_position = official_player_position if official_player_position and official_player_position > 0 else None
    if player is not None and player["car_index"] in official:
        official_position = official[player["car_index"]].position
    # The official result is compared only with a player identified by its own
    # lap rows: a car flagged as the player in an older recording may not be.
    identity = session.get("player_identity")
    comparable = identity in (None, "legacy_laps")
    if player is not None and identity == "flag_only":
        warnings.append(f"{player['display_name']}: identified as the player by the car flag only; no player lap rows were recorded.")
    phantoms = [names.get(index, str(index)) for index in session.get("player_flagged") or [] if index != player_index]
    if phantoms and player is not None and identity == "legacy_laps":
        warnings.append(
            f"{', '.join(phantoms)}: flagged as the player by the recorder, but {player['display_name']}'s "
            "lap rows are the player's."
        )
    if order.use_recorded:
        leader_laps = max((value or 0 for value in order.laps_completed.values()), default=0)
    if order.classification:
        leader_laps = max(result.laps for result in order.classification.values())
    covered_from = (
        race_control_from_lap
        if race_control.available and race_control_from_lap and race_control_from_lap > 1
        else 1 if race_control.available else None
    )

    if not laps:
        warnings.append("No laps are stored for this session.")
    if is_race and laps and cars_with_laps == 1:
        warnings.append("Only one car's laps were recorded, so there is no race order to derive.")
    elif is_race and laps and not field_complete and cars_with_laps and not order.use_recorded and not order.classification:
        warnings.append(
            f"Only {cars_with_laps} of {field_size} cars have recorded laps, so positions are among them only."
        )
    if is_race and laps and cars_with_laps >= 2 and not finish_order and not order.use_recorded:
        warnings.append("No car has a continuous lap record, so race order cannot be derived.")
    if countback:
        warnings.append(
            f"The race was stopped and not resumed; the order is taken at lap {winner_order}, "
            "the last lap completed before the suspension."
        )
    if provisional and laps:
        warnings.append(
            "This session did not finish recording, so the order is provisional: it stops at the last recorded lap."
        )
    if unconfirmed_count:
        warnings.append(
            f"{unconfirmed_count} lap{'s' if unconfirmed_count != 1 else ''} still await confirmation after a "
            "flashback; they count for race order but not for pace or fastest laps."
        )
    if unknown_stops:
        warnings.append(
            "Pit stops are unknown for " + ", ".join(unknown_stops)
            + ": some of their laps were recorded without telemetry context and could hide a stop."
        )
    if is_race and laps and not race_control.available:
        warnings.append(
            "Race-control messages were not recorded for this session, so safety-car periods are not marked."
        )
    elif is_race and laps and covered_from and covered_from > 1:
        warnings.append(
            f"Race-control messages were recorded from lap {covered_from}; safety cars before it are not marked."
        )
    if (
        comparable
        and not order.classification
        and official_position is not None
        and derived_player_position is not None
        and official_position != derived_player_position
    ):
        source = "the game's recorded positions put" if order.use_recorded else "the order derived from lap times puts"
        warnings.append(
            f"Your official result is P{official_position}; {source} you "
            f"P{derived_player_position} (penalties are not applied to this order)."
        )
    finish_source = (
        "classification" if order.classification
        else "game_positions" if order.use_recorded
        else "lap_times" if is_race
        else None
    )

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
            "display_name": session.get("display_name"),
            "session_type": session.get("session_type"),
            "is_race": is_race,
            "status": session.get("status"),
            "provisional": provisional,
            "started_at": session.get("started_at"),
            "total_laps": session.get("total_laps"),
            "leader_laps": leader_laps if is_race else None,
            "winner_laps": winner_order if is_race and winner is not None else None,
            "last_recorded_lap": order.last_row_lap if is_race else None,
            "reached_distance": order.reached if is_race else None,
        },
        "field": {"cars": field_size, "cars_with_laps": cars_with_laps, "complete": field_complete},
        "basis": {
            "race_order": (
                (
                    "the game's recorded position at the end of each lap; summed lap times fill a lap only "
                    "where they agree with it; the order holds while the race is stopped"
                    if order.use_recorded
                    else "summed lap times; each racing segment between suspensions is summed from its own start"
                )
                if is_race
                else None
            ),
            "finish_order": (
                "the game's final classification, with penalties applied"
                if order.classification
                else "the game's recorded positions on the final lap; a car without a final-lap record keeps "
                "its position from the lap before when nothing contradicts it; retired cars take no position; "
                "penalties are not applied"
                if order.use_recorded
                else "derived from laps completed and race time; penalties are not applied"
            ),
            "finish_source": finish_source,
            "pace_laps": (
                "excludes lap 1, restart laps, pit in/out laps, safety-car, flag and suspended laps, "
                "laps excluded in lap notes, laps recorded without telemetry context, and laps without a time"
                if is_race
                else "excludes pit laps, flag laps, invalid laps, laps excluded in lap notes, laps recorded "
                "without telemetry context and laps without a time"
            )
            + outlier_note,
            "pit_loss": "in-lap and out-lap time minus the same number of the driver's median pace laps; an estimate",
            "neutralisation": (
                "safety-car and virtual-safety-car messages from race control"
                if race_control.available
                else "race-control messages were not recorded"
            ),
            "sectors": f"sector bests use laps whose three sectors add up to the lap time (within {SECTOR_TOLERANCE_MS} ms)",
        },
        "hide_outliers": hide_outliers,
        "segments": [{"first_lap": first, "last_lap": last} for first, last in segments] if is_race else [],
        "suspended_laps": suspended,
        "neutralised_laps": sorted(neutral),
        "race_control": {
            "available": race_control.available,
            "covered_from_lap": covered_from,
            "neutralisations": neutral_runs,
            "safety_car_laps": sorted(lap for lap, kind in neutral.items() if kind == "SC"),
            "vsc_laps": sorted(lap for lap, kind in neutral.items() if kind == "VSC"),
            "red_flag_laps": red_flag_laps,
        },
        "record_complete": record_complete,
        "official_result": {
            "player_position": official_position,
            "derived_player_position": derived_player_position if comparable else None,
            "agrees": (
                official_position == derived_player_position
                if comparable and official_position is not None and derived_player_position is not None
                else None
            ),
            "player_identity": identity,
        },
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


def _unix(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """What a recording's saved session events add to its lap rows.

    ``race_control`` is None when no session message was stored at all;
    ``incidents`` lists the game's retirement (RTMT), penalty (PENA) and
    chequered-flag (CHQF) messages, as {type, lap, payload}; RTMT is
    repeated after every flashback. ``classification``
    is the game's final classification of every car (FCLS) and
    ``lap_chart`` its lap chart (LPOS): lap number -> each car's position at
    the end of that lap, 0 for none. Lap 0 is the grid.
    """

    race_control: list[dict[str, Any]] | None = None
    first_lap: int | None = None
    incidents: tuple[dict[str, Any], ...] = ()
    classification: dict[str, Any] | None = None
    lap_chart: Mapping[int, tuple[int, ...]] | None = None


@dataclass(frozen=True, slots=True)
class LoadedSession:
    session: dict[str, Any]
    cars: list[AnalysisCar]
    laps: list[AnalysisLap]
    events: list[dict[str, Any]] | None
    official_player_position: int | None
    race_control_from_lap: int | None = None
    record: SessionRecord = SessionRecord()


def _note_excluded_laps(notes_json: Any, laps: list[sqlite3.Row], player_index: int | None) -> set[str]:
    """Lap ids that lap notes exclude from pace (5.3.0 Test Engineer notes).

    A note names stored laps by id, or - for a lap noted while it was still
    being driven - the player's lap number on one timeline.
    """
    try:
        notes = json.loads(notes_json or "{}")
    except (TypeError, ValueError):
        return set()
    if not isinstance(notes, dict):
        return set()
    excluded: set[str] = set()
    for note in notes.get("lap_notes") or []:
        if not isinstance(note, dict) or note.get("exclude_from_pace") is not True:
            continue
        excluded.update(str(lap_id) for lap_id in note.get("lap_ids") or [])
        pending = {
            (item.get("timeline_epoch"), item.get("lap_num"))
            for item in note.get("pending_laps") or []
            if isinstance(item, dict)
        }
        if pending and player_index is not None:
            excluded.update(
                str(row["id"]) for row in laps
                if int(row["car_index"]) == player_index
                and (int(row["timeline_epoch"]), int(row["lap_number"])) in pending
            )
    return excluded


class SessionAnalysisService:
    """Reads one saved session's active lap rows and builds the analysis."""

    def __init__(
        self,
        database_path: Path,
        *,
        max_lap_rows: int = MAX_LAP_ROWS,
        lap_end_positions: Callable[[Iterable[str]], Mapping[str, int | None]] | None = None,
    ) -> None:
        """``lap_end_positions`` reads the game's race position at the end of
        traced laps, by trace manifest id (Field Lab's reader in the app). It
        is only called for recordings whose lap times cannot order the field.
        """
        self.database_path = Path(database_path)
        self.max_lap_rows = int(max_lap_rows)
        self.lap_end_positions = lap_end_positions

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.database_path, timeout=15)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA query_only=ON")
            with connection:
                yield connection

    @staticmethod
    def _session_record(db: sqlite3.Connection, session_row: sqlite3.Row) -> SessionRecord:
        """This recording's race-control messages, retirements, final
        classification and lap chart, and the first lap any session message
        was recorded on.

        Events are kept per game session uid; restarts of a session share the
        uid, so the window runs from this recording's start to the next
        restart's start (or a grace period after this one ended). A recording
        that began mid-race has messages only from the lap it began on.
        """
        legacy_uid = session_row["legacy_session_uid"]
        start = _unix(session_row["started_at"])
        if legacy_uid is None or start is None:
            return SessionRecord()
        try:
            following = db.execute(
                "SELECT MIN(started_at) FROM recorded_sessions WHERE legacy_session_uid=? AND started_at>?",
                (legacy_uid, session_row["started_at"]),
            ).fetchone()[0]
            ended = _unix(session_row["ended_at"])
            # Older recordings can carry an end time equal to the start; that
            # is no end at all, and would cut off every message after it.
            if ended is not None and ended <= start:
                ended = None
            limits = [value for value in (_unix(following), ended + EVENT_GRACE_S if ended else None) if value]
            end = min(limits) if limits else math.inf
            rows = db.execute(
                "SELECT event_type, lap_num, payload_json, created_at FROM session_events "
                "WHERE session_uid=? AND created_at>=? AND created_at<? ORDER BY created_at, id",
                (legacy_uid, start - 5.0, end if math.isfinite(end) else 1e18),
            ).fetchall()
        except sqlite3.OperationalError:
            return SessionRecord()  # Legacy tables absent (a catalog-only database).
        if not rows:
            return SessionRecord()
        first_lap = min((int(row["lap_num"]) for row in rows if row["lap_num"] is not None), default=None)
        events: list[dict[str, Any]] = []
        incidents: list[dict[str, Any]] = []
        classification: dict[str, Any] | None = None
        chart: dict[int, tuple[int, ...]] = {}
        for row in rows:
            kind = row["event_type"]
            if kind not in RACE_CONTROL_EVENTS and kind not in ("RTMT", "PENA", "CHQF", "FCLS", "LPOS"):
                continue
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except (TypeError, ValueError):
                payload = {}
            if not isinstance(payload, dict):
                continue
            if kind in RACE_CONTROL_EVENTS:
                events.append({"type": kind, "lap": row["lap_num"], "payload": payload})
            elif kind in ("RTMT", "PENA", "CHQF"):
                incidents.append({"type": kind, "lap": row["lap_num"], "payload": payload})
            elif kind == "FCLS" and isinstance(payload.get("cars"), list):
                classification = payload
            elif kind == "LPOS" and isinstance(payload.get("positions"), list):
                first = int(payload.get("lap_start") or 0)
                for offset, values in enumerate(payload["positions"]):
                    if isinstance(values, list):
                        chart[first + offset] = tuple(int(value or 0) for value in values)
        return SessionRecord(
            race_control=events,
            first_lap=max(1, first_lap) if first_lap is not None else None,
            incidents=tuple(incidents),
            classification=classification,
            lap_chart=chart or None,
        )

    @staticmethod
    def _legacy_result(db: sqlite3.Connection, session_row: sqlite3.Row) -> tuple[int | None, int | None]:
        """The official player result and race distance from the legacy session row.

        The legacy row is per game uid and keeps the first result recorded, so
        a result is only attributed when this is the uid's only recording.
        """
        legacy_uid = session_row["legacy_session_uid"]
        if legacy_uid is None:
            return None, None
        try:
            recordings = db.execute(
                "SELECT COUNT(*) FROM recorded_sessions WHERE legacy_session_uid=?", (legacy_uid,)
            ).fetchone()[0]
            row = db.execute(
                "SELECT result_position, total_laps FROM sessions WHERE session_uid=?", (legacy_uid,)
            ).fetchone()
        except sqlite3.OperationalError:
            return None, None
        if row is None:
            return None, None
        position = _int_or_none(row["result_position"]) if recordings == 1 else None
        return position, _int_or_none(row["total_laps"])

    def _load_sync(self, session_id: str) -> LoadedSession:
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
            # The active branch only: rows a flashback abandoned are flagged
            # in place (bit 2) and never count, then the newest remaining
            # timeline epoch per car identity and lap wins.
            lap_rows = db.execute(
                """
                SELECT l.id, l.lap_number, l.timeline_epoch, l.lap_time_ms, l.valid,
                       l.pit_context, l.flag_context, l.tyre_compound, l.tyre_age_laps,
                       l.trace_manifest_id IS NOT NULL AS traced, l.trace_manifest_id, l.coverage_ratio,
                       l.legacy_lap_id, l.engineering_json, c.car_index, c.identity_revision
                FROM recorded_laps l
                JOIN session_cars c ON c.id=l.session_car_id
                WHERE c.session_id=?
                  AND l.lap_number>=1
                  AND (l.invalid_reason_mask & ?)=0
                  AND NOT EXISTS (
                      SELECT 1 FROM recorded_laps newer
                      WHERE newer.session_car_id=l.session_car_id
                        AND newer.lap_number=l.lap_number
                        AND newer.timeline_epoch>l.timeline_epoch
                        AND (newer.invalid_reason_mask & ?)=0
                  )
                ORDER BY c.car_index, l.lap_number, l.timeline_epoch, c.identity_revision
                LIMIT ?
                """,
                (session_id, ABANDONED_BRANCH_BIT, ABANDONED_BRANCH_BIT, self.max_lap_rows + 1),
            ).fetchall()
            record = self._session_record(db, session_row)
            official_position, legacy_total_laps = self._legacy_result(db, session_row)
            legacy_positions = self._legacy_positions(db, lap_rows)
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
        # The player is the car with legacy lap rows: those are only ever
        # written for the player's own car. Older recordings can flag a second
        # identity (car 0, registered before the participants packet named
        # anyone) as the player too.
        legacy_cars = Counter(int(row["car_index"]) for row in lap_rows if row["legacy_lap_id"] is not None)
        flagged = [car.car_index for car in cars if car.is_player]
        identity: str | None = None
        player_index: int | None = None
        if legacy_cars:
            player_index = max(legacy_cars, key=lambda index: (legacy_cars[index], index in flagged, -index))
            identity = "legacy_laps"
        elif len(flagged) == 1:
            player_index, identity = flagged[0], "flag_only"
        elif flagged:
            identity = "ambiguous"  # several flagged cars and no player lap rows: nobody
        cars = [replace(car, is_player=car.car_index == player_index) for car in cars]
        noted = _note_excluded_laps(session_row["engineering_notes_json"], lap_rows, player_index)
        # One row per physical car and lap. Each identity revision already
        # contributes only its active branch. Across revisions a row that
        # observed the lap beats a timing-only copy: after an identity
        # revision the game's lap history is reconciled again under the new
        # revision and inserts timing-only rows for every earlier lap. Then
        # the newest epoch, then the newest revision. A chosen row without a
        # time borrows the time and sectors of a row that has one.
        parsed: dict[str, dict[str, Any]] = {}
        for row in lap_rows:
            try:
                engineering = json.loads(row["engineering_json"] or "{}")
            except (TypeError, ValueError):
                engineering = {}
            parsed[str(row["id"])] = engineering if isinstance(engineering, dict) else {}
        candidates: dict[tuple[int, int], list[sqlite3.Row]] = defaultdict(list)
        for row in lap_rows:
            candidates[(int(row["car_index"]), int(row["lap_number"]))].append(row)

        def preference(row: sqlite3.Row) -> tuple[bool, int, int]:
            observed = parsed[str(row["id"])].get("context_observed") is not False
            return (observed, int(row["timeline_epoch"] or 0), int(row["identity_revision"] or 0))

        chosen: dict[tuple[int, int], tuple[sqlite3.Row, sqlite3.Row]] = {}
        for key, rows in candidates.items():
            best_row = max(rows, key=preference)
            timed = [row for row in rows if _int_or_none(row["lap_time_ms"])]
            if _int_or_none(best_row["lap_time_ms"]) or not timed:
                chosen[key] = (best_row, best_row)
            else:
                chosen[key] = (best_row, max(timed, key=preference))
        context: dict[str, Any] = {}
        laps: list[AnalysisLap] = []
        for row, timing_row in chosen.values():
            engineering = parsed[str(row["id"])]
            timing = parsed[str(timing_row["id"])]
            for key in ("track_name", "total_laps"):
                if engineering.get(key) and key not in context:
                    context[key] = engineering[key]
            pending = _int_or_none(engineering.get("history_revalidation_required_epoch"))
            exclusions = engineering.get("learning_exclusions") or []
            laps.append(
                AnalysisLap(
                    car_index=int(row["car_index"]),
                    lap_id=str(row["id"]),
                    lap_number=int(row["lap_number"]),
                    lap_time_ms=_int_or_none(timing_row["lap_time_ms"]),
                    s1_ms=_int_or_none(timing.get("s1_ms")),
                    s2_ms=_int_or_none(timing.get("s2_ms")),
                    s3_ms=_int_or_none(timing.get("s3_ms")),
                    valid=bool(timing_row["valid"]),
                    # The recorder's own reading of the lap: a pit lap even when
                    # the pit status at the line was already back to 0.
                    pit_context=bool(row["pit_context"]) or (
                        isinstance(exclusions, list) and "pit_lap" in exclusions
                    ),
                    flag_context=bool(row["flag_context"]),
                    compound=normalise_compound(row["tyre_compound"]),
                    tyre_age_laps=(
                        int(row["tyre_age_laps"]) if row["tyre_age_laps"] is not None else None
                    ),
                    context_observed=engineering.get("context_observed") is not False,
                    unconfirmed=bool(pending and pending > int(row["timeline_epoch"] or 0)),
                    traced=bool(row["traced"]),
                    driver_excluded=str(row["id"]) in noted,
                    trace_manifest_id=str(row["trace_manifest_id"]) if row["trace_manifest_id"] else None,
                    recorded_position=legacy_positions.get(row["legacy_lap_id"]),
                    coverage_ratio=float(row["coverage_ratio"]) if row["coverage_ratio"] is not None else None,
                )
            )
        session = {
            "id": str(session_row["id"]),
            "track_id": session_row["track_id"],
            "display_name": session_row["display_name"],
            "session_type": session_row["session_type"],
            "raw_session_type_id": session_row["raw_session_type_id"],
            "status": session_row["status"],
            "started_at": session_row["started_at"],
            # The Library names circuits from the game's track table; the lap
            # context is only a fallback for ids that table does not know.
            "track_name": catalog_track_name(session_row["track_id"]) or context.get("track_name"),
            "total_laps": _int_or_none(context.get("total_laps")) or legacy_total_laps,
            "player_identity": identity,
            "player_flagged": flagged,
        }
        return LoadedSession(
            session, cars, laps, record.race_control, official_position, record.first_lap, record
        )

    @staticmethod
    def _legacy_positions(db: sqlite3.Connection, lap_rows: list[sqlite3.Row]) -> dict[int, int]:
        """The race position the player's legacy lap rows recorded at each lap end."""
        ids = sorted({int(row["legacy_lap_id"]) for row in lap_rows if row["legacy_lap_id"] is not None})
        positions: dict[int, int] = {}
        try:
            for start in range(0, len(ids), 500):
                chunk = ids[start:start + 500]
                for row in db.execute(
                    f"SELECT id, position FROM laps WHERE id IN ({','.join('?' * len(chunk))})", chunk
                ):
                    if _int_or_none(row["position"]):
                        positions[int(row["id"])] = int(row["position"])
        except sqlite3.OperationalError:
            return {}  # Legacy tables absent (a catalog-only database).
        return positions

    def _recorded_positions(self, laps: Iterable[AnalysisLap]) -> dict[str, int]:
        """The game's race position at the end of each lap that has one: the
        player's legacy lap row, else the last position sample in the lap's
        trace. Laps with neither are left out."""
        wanted = list(laps)
        positions = {lap.lap_id: int(lap.recorded_position) for lap in wanted if lap.recorded_position}
        unread = [lap for lap in wanted if lap.lap_id not in positions and lap.trace_manifest_id]
        if unread and self.lap_end_positions is not None:
            read = self.lap_end_positions([str(lap.trace_manifest_id) for lap in unread])
            for lap in unread:
                value = _int_or_none(read.get(str(lap.trace_manifest_id)))
                if value is not None:
                    positions[lap.lap_id] = value
        return positions

    def _analysis_sync(self, session_id: str, hide_outliers: bool) -> dict[str, Any]:
        try:
            loaded = self._load_sync(session_id)
        except sqlite3.DatabaseError as exc:
            # A data directory still being created or migrated: a state of the
            # store, not a server fault.
            raise SessionAnalysisError("Saved sessions cannot be read right now; try again shortly.") from exc
        return build_session_analysis(
            loaded.session,
            loaded.cars,
            loaded.laps,
            hide_outliers=hide_outliers,
            events=loaded.events,
            official_player_position=loaded.official_player_position,
            race_control_from_lap=loaded.race_control_from_lap,
            incidents=loaded.record.incidents,
            classification=loaded.record.classification,
            lap_chart=loaded.record.lap_chart,
            recorded_positions=self._recorded_positions,
        )

    async def analysis(self, session_id: str, *, hide_outliers: bool = True) -> dict[str, Any]:
        return await asyncio.to_thread(self._analysis_sync, session_id, hide_outliers)


__all__ = [
    "DRIVER_STATUSES",
    "PACE_EXCLUSION_REASONS",
    "STOP_KINDS",
    "AnalysisCar",
    "AnalysisLap",
    "LoadedSession",
    "RaceControl",
    "SessionAnalysisError",
    "SessionAnalysisService",
    "SessionNotFoundError",
    "box_stats",
    "build_session_analysis",
    "car_suspended_laps",
    "driver_code",
    "field_pace",
    "is_race_session",
    "normalise_compound",
    "pit_runs",
    "race_control_from_events",
    "race_pace",
    "race_segments",
    "segment_times",
    "stints_for",
    "stops_known",
    "suspended_laps",
    "usual_pace",
]
