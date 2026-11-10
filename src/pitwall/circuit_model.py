"""What each circuit's recorded races measure, in place of hand-written tables.

Every race the app records measures quantities the planner otherwise takes
from tables written by hand: the pit loss (from every rival's in-lap and
out-lap), how hard the circuit is on tyres (from the field's wear a lap) and
how often a safety car comes out. ``learn_from_session`` folds one finished
race into a circuit's record; ``measured`` gives the planner a value only
when it has enough samples and is not implausibly far from the table, which
would be a measurement error until someone looks.

Overtaking difficulty is not measured here: telling passes on track from
places changed in the pit cycle needs a recorded race replayed, and it stays
a hand-written prior.
"""

from __future__ import annotations

import time
from statistics import median
from typing import Any

from .tyre_learning import finite

# Minimum samples before a measured value replaces the table. Judgements:
# eight stops average out traffic on in- and out-laps; thirty car-laps of wear
# cover a stint's spread; three races before a safety-car rate means anything.
MIN_PIT_STOPS = 8
MIN_WEAR_CAR_LAPS = 30
MIN_SAFETY_CAR_RACES = 3
# A measured value further than this share from its table value is reported,
# not used.
MAX_DEPARTURE_FROM_PRIOR = 0.30
# A stop stationary for longer than this was a repair or a penalty.
MAX_STATIONARY_MS = 6_000
# Clean laps either side of a stop that make its reference pace.
REFERENCE_LAPS = 3
# How many recent samples each quantity keeps.
SAMPLE_CAP = 60
# Default wear a lap per compound the severity is measured against. Kept here
# rather than imported so this module has no planner dependency.
_DEFAULT_WEAR_PER_LAP = {"SOFT": 4.8, "MEDIUM": 3.2, "HARD": 2.1}


def preference_key(track_id: int) -> str:
    return f"circuit_model:{int(track_id)}"


def _neutralised_laps(state: dict[str, Any]) -> set[int]:
    """Laps the player recorded under a safety car, VSC or red flag."""
    laps = set()
    for lap in state.get("completed_laps", []) or []:
        tags = lap.get("learning_exclusions") or []
        if lap.get("full_course_neutralised") or (
            "full_course_neutralised" not in lap and "neutralised_lap" in tags
        ):
            laps.add(int(lap.get("lap_num", 0) or 0))
    return laps


def pit_losses(state: dict[str, Any]) -> list[float]:
    """Net time each green-flag rival stop cost, from its own laps either side.

    In-lap plus out-lap, less twice the median of that car's clean laps within
    three laps either side. Repairs, penalties and stops under a flag are
    left out: they do not measure the lane.
    """
    flagged = _neutralised_laps(state)
    player = int(state.get("player_car_index", -1))
    losses: list[float] = []
    for driver in state.get("drivers", []) or []:
        if int(driver.get("car_idx", -1)) == player or driver.get("restricted"):
            continue
        times = {
            int(lap.get("lap_num", 0) or 0): float(lap["lap_ms"]) / 1000.0
            for lap in driver.get("lap_history", []) or []
            if (finite(lap.get("lap_ms")) or 0) > 0
        }
        valid = {
            int(lap.get("lap_num", 0) or 0)
            for lap in driver.get("lap_history", []) or []
            if int(lap.get("valid_flags", 0) or 0) & 1
        }
        stops = [stop for stop in driver.get("pit_stop_history", []) or [] if int(stop.get("lap", 0) or 0) > 1]
        stop_laps = {int(stop["lap"]) for stop in stops} | {int(stop["lap"]) - 1 for stop in stops}
        for stop in stops:
            if int(stop.get("stationary_time_ms", 0) or 0) > MAX_STATIONARY_MS:
                continue
            out_lap = int(stop["lap"])
            in_lap = out_lap - 1
            if in_lap not in times or out_lap not in times or {in_lap, out_lap} & flagged:
                continue
            nearby = [
                times[number]
                for number in [*range(in_lap - REFERENCE_LAPS, in_lap), *range(out_lap + 1, out_lap + 1 + REFERENCE_LAPS)]
                if number in times and number in valid and number not in stop_laps and number not in flagged
                and number > 1
            ]
            if len(nearby) < 2:
                continue
            loss = times[in_lap] + times[out_lap] - 2 * float(median(nearby))
            if 5.0 < loss < 60.0:
                losses.append(round(loss, 2))
    return losses


def wear_rates(state: dict[str, Any]) -> list[tuple[float, int]]:
    """Each car's current stint wear a lap against the default, with its laps."""
    rates: list[tuple[float, int]] = []
    for driver in state.get("drivers", []) or []:
        if driver.get("restricted"):
            continue
        compound = str(driver.get("tyre_compound") or "").upper()
        age = int(driver.get("tyre_age", 0) or 0)
        wear = [float(value) for value in driver.get("tyre_wear") or [] if finite(value) is not None]
        if compound not in _DEFAULT_WEAR_PER_LAP or age < 5 or not wear or max(wear) <= 0:
            continue
        per_lap = (sum(wear) / len(wear)) / age
        rates.append((round(per_lap / _DEFAULT_WEAR_PER_LAP[compound], 3), age))
    return rates


def safety_car_periods(state: dict[str, Any]) -> tuple[int, int]:
    """Safety-car periods begun, and green laps run, in this race."""
    periods = green = 0
    previous = False
    flagged = _neutralised_laps(state)
    for lap in sorted(state.get("completed_laps", []) or [], key=lambda item: int(item.get("lap_num", 0) or 0)):
        neutralised = int(lap.get("lap_num", 0) or 0) in flagged
        if neutralised and not previous:
            periods += 1
        green += 0 if neutralised else 1
        previous = neutralised
    return periods, green


def _merge_samples(record: dict[str, Any] | None, samples: list[float]) -> dict[str, Any]:
    kept = [*((record or {}).get("samples") or []), *samples][-SAMPLE_CAP:]
    return {
        "value": round(float(median(kept)), 3) if kept else None,
        "n": len(kept),
        "samples": kept,
        "updated": time.strftime("%Y-%m-%d"),
    }


def learn_from_session(state: dict[str, Any], model: dict[str, Any] | None) -> dict[str, Any]:
    """Fold one finished race into a circuit's record. Pure."""
    model = dict(model or {})
    if str(state.get("mode_profile", "")) not in {"race", "sprint"}:
        return model
    # The final classification can arrive more than once; a race is learned once.
    session = int(state.get("session_uid", 0) or 0)
    learned = list(model.get("sessions") or [])
    if session and session in learned:
        return model
    if session:
        model["sessions"] = [*learned, session][-50:]
    losses = pit_losses(state)
    if losses:
        model["pit_loss_s"] = _merge_samples(model.get("pit_loss_s"), losses)
    rates = wear_rates(state)
    if rates:
        previous = model.get("severity") or {}
        merged = _merge_samples(previous, [rate for rate, _ in rates])
        merged["car_laps"] = int(previous.get("car_laps", 0) or 0) + sum(age for _, age in rates)
        model["severity"] = merged
    periods, green = safety_car_periods(state)
    if green:
        record = dict(model.get("safety_car_per_lap") or {})
        record["periods"] = int(record.get("periods", 0) or 0) + periods
        record["green_laps"] = int(record.get("green_laps", 0) or 0) + green
        record["races"] = int(record.get("races", 0) or 0) + 1
        record["value"] = round(record["periods"] / max(1, record["green_laps"]), 4)
        model["safety_car_per_lap"] = record
    return model


def measured(model: dict[str, Any] | None, name: str, prior: float) -> tuple[float, str]:
    """The value to use and where it came from: the measurement, or the table.

    A measurement needs its minimum count, and one further than 30% from the
    table is reported in the source and not used.
    """
    record = (model or {}).get(name) or {}
    value = finite(record.get("value"))
    if value is None:
        return prior, "circuit table"
    if name == "pit_loss_s":
        count, needed, unit = int(record.get("n", 0) or 0), MIN_PIT_STOPS, "stops"
    elif name == "severity":
        count, needed, unit = int(record.get("car_laps", 0) or 0), MIN_WEAR_CAR_LAPS, "car-laps"
    elif name == "safety_car_per_lap":
        count, needed, unit = int(record.get("races", 0) or 0), MIN_SAFETY_CAR_RACES, "races"
    else:
        return prior, "circuit table"
    if count < needed:
        return prior, f"circuit table ({count} of {needed} {unit} measured)"
    if prior and abs(value - prior) > MAX_DEPARTURE_FROM_PRIOR * prior:
        return prior, f"circuit table (measured {value:g} from {count} {unit} is too far from it to use)"
    return value, f"measured, {count} {unit}"


def summary(track_id: int, track_name: str, model: dict[str, Any] | None, priors: dict[str, float]) -> dict[str, Any]:
    """One circuit's quantities for the CONNECTION panel, without raw samples."""
    rows = []
    for name, label in (("pit_loss_s", "Pit loss"), ("severity", "Tyre severity"),
                        ("safety_car_per_lap", "Safety cars a lap")):
        prior = float(priors.get(name, 0.0) or 0.0)
        value, source = measured(model, name, prior)
        record = (model or {}).get(name) or {}
        rows.append({
            "name": name, "label": label, "value": value, "source": source,
            "measured_value": record.get("value"), "prior": prior or None,
        })
    return {"track_id": int(track_id), "track_name": track_name, "quantities": rows}
