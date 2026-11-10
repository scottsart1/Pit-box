"""Every strategy call of a race, joined to what the driver did and what it cost.

After a race the pieces are all stored: the plan the engine published
(``strategy_snapshots``), every radio call with whether it was said
(``proactive_calls``), the driver's radio turns, each lap's conditions and the
session analysis (stints, stops, lap times, finishing order). Nothing joined
them. ``strategy_review`` does, for the player's car:

* ``stops``: each stop with the box lap the engine recommended before it,
  the lap the stop was called, whether that call was heard, and a hindsight
  comparison of stop laps on the lap times that were set (what the stop cost
  against the best stop lap on known pace, and in places);
* ``weather``: each spell of rain with when it started, when the surface
  crossed to intermediates, when the radio first warned and called the box,
  when the stop was made and what being late cost against the cars already
  on wet tyres;
* ``calls``: every call, with its outcome and how long it waited;
* ``overrides``: the driver's declines and disputes of a call;
* ``undelivered``: calls never said.

Hindsight is a comparison of stop laps on known pace, not a claim about
what should have been known at the time.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from statistics import median
from typing import Any

from .rain import WETNESS_SLICK_INTER

WET_TYRES = frozenset({"INTER", "WET"})
WET_SKIES = frozenset({"light rain", "heavy rain", "storm"})
# A stop within this much of the best stop lap on known pace is on the money.
ON_THE_MONEY_S = 1.5
# Stop laps either side of the actual one that hindsight compares.
HINDSIGHT_WINDOW_LAPS = 5
# Clean laps a stint needs before its pace can be fitted.
HINDSIGHT_MIN_LAPS = 3
# Calls that name a stop: their payload carries a box lap and a tyre.
STOP_CALLS = frozenset({
    "strategy_change", "weather_crossover", "weather_box_reminder", "progress_update",
    "tyre_wear", "driver_check", "compound_requirement", "race_control",
})


def _payload_stop(payload: Mapping[str, Any]) -> tuple[int | None, str]:
    """The box lap and tyre a call's payload names, wherever it carries them."""
    for source in (payload, payload.get("strategy") or {}):
        if not isinstance(source, Mapping):
            continue
        lap = source.get("box_lap")
        tyre = source.get("fit_compound") or source.get("compound")
        if lap is not None or tyre:
            try:
                return (int(lap) if lap is not None else None), str(tyre or "").upper()
            except (TypeError, ValueError):
                return None, str(tyre or "").upper()
    return None, ""


def _box_now(call: Mapping[str, Any]) -> bool:
    """A call telling the driver to box on the lap it was said."""
    payload = call.get("payload") or {}
    if call.get("type") == "weather_box_reminder":
        return True
    if call.get("type") == "weather_crossover":
        return int(payload.get("level", 0) or 0) >= 4
    lap, _ = _payload_stop(payload)
    return lap is not None and lap == int(call.get("lap", -1))


def _player(analysis: Mapping[str, Any]) -> int | None:
    return next((int(row["car_index"]) for row in analysis.get("drivers") or [] if row.get("is_player")), None)


def _laps_by_car(analysis: Mapping[str, Any]) -> dict[int, dict[int, dict[str, Any]]]:
    laps: dict[int, dict[int, dict[str, Any]]] = {}
    for row in analysis.get("laps") or []:
        laps.setdefault(int(row["car_index"]), {})[int(row["lap_number"])] = row
    return laps


def _clean(row: Mapping[str, Any] | None) -> bool:
    return bool(
        row and row.get("lap_time_ms") and not row.get("pit") and not row.get("neutralised")
        and not row.get("suspended") and int(row.get("lap_number", 0)) > 1
    )


def _fit(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    """Least-squares lap time = a + b * age, from (age, seconds)."""
    if len(points) < HINDSIGHT_MIN_LAPS:
        return None
    n = len(points)
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    spread = sum((x - mean_x) ** 2 for x, _ in points)
    slope = 0.0 if spread <= 0 else sum((x - mean_x) * (y - mean_y) for x, y in points) / spread
    return mean_y - slope * mean_x, slope


def _stint_model(laps: Mapping[int, Mapping[str, Any]], first: int, last: int) -> tuple[tuple[float, float], int] | None:
    """A stint's pace by tyre age, and the age on its first lap."""
    points = []
    start_age = None
    for number in range(first, last + 1):
        row = laps.get(number)
        if row is None:
            continue
        age = row.get("tyre_age_laps")
        age = (number - first) if age is None else int(age)
        if start_age is None:
            start_age = age - (number - first)
        if _clean(row):
            points.append((float(age), float(row["lap_time_ms"]) / 1000.0))
    model = _fit(points)
    return (model, int(start_age or 0)) if model else None


def _hindsight(
    laps: Mapping[int, Mapping[str, Any]], stop_lap: int, *, first: int, last: int, next_stop: int | None,
) -> tuple[int, float] | None:
    """The stop lap with the least race time on the paces set, and the actual one's cost.

    The tyre before the stop is modelled from its own clean laps, the tyre
    after from its own; the pit loss is the same whichever lap it falls on.
    """
    end = (next_stop if next_stop is not None else last + 1) - 1
    before = _stint_model(laps, first, stop_lap)
    after = _stint_model(laps, stop_lap + 1, end)
    if before is None or after is None:
        return None
    (a_old, b_old), age_old = before
    (a_new, b_new), age_new = after

    def race_time(box: int) -> float:
        total = 0.0
        for number in range(first, end + 1):
            if number <= box:
                total += a_old + b_old * (age_old + number - first)
            else:
                total += a_new + b_new * (age_new + number - box - 1)
        return total

    low = max(first + 1, stop_lap - HINDSIGHT_WINDOW_LAPS)
    high = min(end - 1, stop_lap + HINDSIGHT_WINDOW_LAPS)
    if high < low:
        return None
    times = {box: race_time(box) for box in range(low, high + 1)}
    best = min(times, key=lambda box: (round(times[box], 6), abs(box - stop_lap)))
    return best, max(0.0, race_time(stop_lap) - times[best])


def _places(analysis: Mapping[str, Any], player: int, seconds: float | None) -> int | None:
    """Cars that finished ahead on the same lap by less than ``seconds``."""
    if seconds is None:
        return None
    drivers = {int(row["car_index"]): row for row in analysis.get("drivers") or []}
    me = drivers.get(player) or {}
    mine, position = me.get("race_time_ms"), me.get("finish_position")
    if mine is None or position is None:
        return None
    return sum(
        1 for index, row in drivers.items()
        if index != player and row.get("finish_position") is not None
        and int(row["finish_position"]) < int(position) and not row.get("laps_down")
        and row.get("race_time_ms") is not None and int(mine) - int(row["race_time_ms"]) < seconds * 1000.0
    )


def _phase(analysis: Mapping[str, Any], stop: Mapping[str, Any]) -> str:
    control = analysis.get("race_control") or {}
    laps = set(stop.get("laps") or [stop.get("lap_number")])
    if laps & set(control.get("red_flag_laps") or []):
        return "red_flag"
    if laps & set(control.get("safety_car_laps") or []):
        return "safety_car"
    if laps & set(control.get("vsc_laps") or []):
        return "vsc"
    return "neutralised" if stop.get("under_neutralisation") else "green"


def _review_stops(
    analysis: Mapping[str, Any], player: int, laps: Mapping[int, Mapping[str, Any]],
    snapshots: list[Mapping[str, Any]], calls: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    stops = sorted(
        (row for row in analysis.get("pit_stops") or [] if int(row["car_index"]) == player),
        key=lambda row: int(row["lap_number"]),
    )
    last_lap = max(laps, default=0)
    reviewed = []
    for index, stop in enumerate(stops):
        lap = int(stop["lap_number"])
        previous = int(stops[index - 1]["lap_number"]) if index else 0
        following = int(stops[index + 1]["lap_number"]) if index + 1 < len(stops) else None
        to = str(stop.get("to_compound") or "").upper()
        before = [item for item in snapshots if previous < int(item["lap"]) <= lap]
        recommended = (before[-1].get("recommended") or {}) if before else {}
        recommended_lap = recommended.get("box_lap")
        stint_calls = [item for item in calls if previous < int(item["lap"]) <= lap and item["type"] in STOP_CALLS]
        box_calls = [
            item for item in stint_calls
            if _box_now(item) and (_payload_stop(item.get("payload") or {})[1] in {"", to})
        ]
        heard = [item for item in box_calls if item.get("delivered")]
        called = heard[0] if heard else (box_calls[0] if box_calls else None)
        phase = _phase(analysis, stop)
        hindsight = (
            _hindsight(laps, lap, first=previous + 1, last=last_lap, next_stop=following)
            if phase == "green" and stop.get("kind") == "pit_stop" else None
        )
        cost = round(hindsight[1], 1) if hindsight else None
        reviewed.append({
            "lap": lap,
            "from": stop.get("from_compound"),
            "to": stop.get("to_compound"),
            "kind": stop.get("kind"),
            "phase": phase,
            "recommended_lap_at_the_time": int(recommended_lap) if recommended_lap is not None else None,
            "called_on_lap": int(called["lap"]) if called else None,
            "laps_after_call": lap - int(called["lap"]) if called else None,
            "heard": bool(heard) if box_calls else None,
            "hindsight_best_lap": hindsight[0] if hindsight else None,
            "cost_vs_hindsight_s": cost,
            "places": _places(analysis, player, cost),
            "on_the_money": cost is not None and cost < ON_THE_MONEY_S,
        })
    return reviewed


def _wet(weather: Any) -> bool:
    return str(weather or "").strip().lower() in WET_SKIES


def _review_weather(
    analysis: Mapping[str, Any], player: int, laps_by_car: Mapping[int, Mapping[int, Mapping[str, Any]]],
    conditions: Mapping[int, Mapping[str, Any]], calls: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    wet_laps = sorted(lap for lap, row in conditions.items() if _wet(row.get("weather")))
    spells: list[tuple[int, int]] = []
    for lap in wet_laps:
        if spells and lap - spells[-1][1] <= 2:
            spells[-1] = (spells[-1][0], lap)
        else:
            spells.append((lap, lap))
    stops = [row for row in analysis.get("pit_stops") or [] if str(row.get("to_compound") or "").upper() in WET_TYRES]
    reviewed = []
    for number, (start, end) in enumerate(spells, start=1):
        crossed = next((
            lap for lap in range(start, end + 1)
            if float((conditions.get(lap) or {}).get("surface_estimate") or 0.0) >= WETNESS_SLICK_INTER
        ), None)
        window = [item for item in calls if start - 3 <= int(item["lap"]) <= end and item.get("delivered")]
        heads_up = next((int(item["lap"]) for item in window if item["type"] == "weather_crossover"), None)
        box = next((
            int(item["lap"]) for item in window
            if item["type"] == "weather_crossover" and int((item.get("payload") or {}).get("level", 0) or 0) >= 4
        ), None)
        in_spell = [row for row in stops if start - 1 <= int(row["lap_number"]) <= end]
        mine = next((int(row["lap_number"]) for row in in_spell if int(row["car_index"]) == player), None)
        field = [int(row["lap_number"]) for row in in_spell if int(row["car_index"]) != player]
        cost = None
        if crossed is not None and mine is not None and mine > crossed:
            deltas = []
            for lap in range(crossed + 1, mine):
                own = (laps_by_car.get(player) or {}).get(lap)
                rivals = [
                    float(row["lap_time_ms"]) for index, car in laps_by_car.items() if index != player
                    for row in [car.get(lap)]
                    if row and row.get("lap_time_ms") and not row.get("pit")
                    and str(row.get("compound") or "").upper() in WET_TYRES
                ]
                if own and own.get("lap_time_ms") and rivals:
                    deltas.append(max(0.0, (float(own["lap_time_ms"]) - median(rivals)) / 1000.0))
            cost = round(sum(deltas), 1) if deltas else None
        reviewed.append({
            "spell": number,
            "rain_from_lap": start,
            "surface_crossed_on_lap": crossed,
            "first_heads_up_lap": heads_up,
            "box_called_on_lap": box,
            "stopped_on_lap": mine,
            "laps_late": (mine - crossed) if mine is not None and crossed is not None else None,
            "cost_s": cost,
            "field_median_stop_lap": int(median(field)) if field else None,
        })
    return reviewed


def _review_overrides(
    calls: list[Mapping[str, Any]], driver_turns: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    from .brain import EngineerBrain

    overrides = []
    for turn in driver_turns:
        text = str(turn.get("text") or "")
        declined = EngineerBrain._strategy_refusal(text)
        disputed = str(turn.get("label") or "") == "dispute"
        if not declined and not disputed:
            continue
        lap = int(turn.get("lap", 0) or 0)
        engine = next((
            item for item in reversed(calls)
            if item.get("delivered") and int(item["lap"]) <= lap and item["type"] in STOP_CALLS
            and _payload_stop(item.get("payload") or {})[0] is not None
            and float(item.get("at", 0) or 0) <= float(turn.get("at", math.inf) or math.inf)
        ), None)
        overrides.append({
            "lap": lap,
            "kind": "declined" if declined else "disputed",
            "engine": engine["text"] if engine else None,
            "driver": text,
        })
    return overrides


def strategy_review(
    analysis: Mapping[str, Any],
    *,
    snapshots: Iterable[Mapping[str, Any]] = (),
    calls: Iterable[Mapping[str, Any]] = (),
    driver_turns: Iterable[Mapping[str, Any]] = (),
    conditions: Mapping[int, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """The player's stops, weather spells, calls and overrides, joined.

    ``snapshots``: {lap, recommended}; ``calls``: {lap, type, payload, text,
    delivered, outcome, queued_at, at}; ``driver_turns``: {lap, text, label,
    at}; ``conditions``: lap -> {weather, surface_estimate}. All in time order.
    """
    player = _player(analysis)
    calls = sorted(calls, key=lambda item: (float(item.get("at", 0) or 0), int(item.get("lap", 0))))
    rows = [{
        "lap": int(item["lap"]), "type": item["type"], "text": item.get("text") or "",
        "outcome": item.get("outcome") or ("delivered" if item.get("delivered") else "not said"),
        "delivered": bool(item.get("delivered")),
        "wait_s": (
            round(float(item["at"]) - float(item["queued_at"]), 1)
            if item.get("queued_at") and item.get("at") and float(item["queued_at"]) > 0 else None
        ),
    } for item in calls]
    if player is None:
        return {"available": False, "reason": "no player car in this session",
                "stops": [], "weather": [], "calls": rows, "overrides": [],
                "undelivered": sum(1 for row in rows if not row["delivered"])}
    laps_by_car = _laps_by_car(analysis)
    laps = laps_by_car.get(player, {})
    snapshots = sorted(snapshots, key=lambda item: (int(item.get("lap", 0)), float(item.get("at", 0) or 0)))
    return {
        "available": True,
        "player_car_index": player,
        "on_the_money_s": ON_THE_MONEY_S,
        "stops": _review_stops(analysis, player, laps, list(snapshots), calls),
        "weather": _review_weather(analysis, player, laps_by_car, conditions or {}, calls),
        "calls": rows,
        "overrides": _review_overrides(calls, driver_turns),
        "undelivered": sum(1 for row in rows if not row["delivered"]),
        "basis": (
            "Hindsight compares stop laps on the lap times that were set: each tyre's pace by age is "
            "fitted from its own clean laps and the pit loss is the same on every lap. It is not a claim "
            "about what could have been known at the time."
        ),
    }
