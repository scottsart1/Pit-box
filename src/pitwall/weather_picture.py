"""The whole wet model in one picture, for the screen, the engineer and the radio.

The wet model reached the driver as one line on DRIVE and one sentence of
reason, while it holds a surface estimate with four sources, a forecast, a
projection to the flag and a cost for every tyre. A driver deciding whether
to trust "stay out" needs to see those, and the engineer asked "is it going
to rain?" must answer from the same model the radio calls the stop from.

``weather_picture`` is pure: it reads the crossover the strategy engine
published and the forecast in the state, and computes nothing the engine
has not already decided.
"""

from __future__ import annotations

from typing import Any

from . import rain
from .strategy import _surface_word
from .tyre_learning import finite

WET_TYRES = frozenset({"INTER", "WET"})
# A forecast sample at this chance or above makes the weather worth showing
# open, before any rain has fallen.
RELEVANT_CHANCE_PCT = 20
# A driver's report on the surface keeps the picture open for this many laps.
RELEVANT_REPORT_LAPS = 4


def _since_lap(state: dict[str, Any]) -> int | None:
    """First lap of the trailing run of completed laps under the current sky."""
    weather = str(state.get("weather", ""))
    since = None
    for lap in reversed(state.get("completed_laps", []) or []):
        if str(lap.get("weather", "")) != weather:
            break
        since = int(lap.get("lap_num", 0) or 0) or since
    return since


def _forecast(state: dict[str, Any], lap_s: float) -> list[dict[str, Any]]:
    samples = sorted(state.get("weather_forecast", []) or [], key=lambda item: int(item.get("time_offset_min", 0) or 0))
    now = next((item for item in samples if int(item.get("time_offset_min", 0) or 0) == 0), None)
    now_chance = finite((now or {}).get("rain_pct"))
    raining = rain.equilibrium_wetness(str(state.get("weather", ""))) > 0
    rows = []
    for item in samples:
        minutes = int(item.get("time_offset_min", 0) or 0)
        chance = finite(item.get("rain_pct"))
        given_now = None
        if chance is not None and minutes > 0:
            given_now = round(100 * rain.conditioned_rain_probability(
                chance / 100.0, None if now_chance is None else now_chance / 100.0, raining,
            ))
        rows.append({
            "in_min": minutes,
            "in_laps": round(minutes * 60.0 / lap_s, 1) if lap_s > 0 else None,
            "sky": item.get("weather"),
            "chance_pct": int(chance) if chance is not None else None,
            "chance_given_now_pct": given_now,
            "track_temp_c": item.get("track_temp_c"),
        })
    return rows


def _projection(crossover: dict[str, Any], current_lap: int) -> dict[str, Any] | None:
    mean = [round(float(value), 3) for value in crossover.get("trajectory") or []]
    scenarios = [item for item in crossover.get("scenarios") or [] if item.get("trajectory")]
    if not mean and not scenarios:
        return None
    def final(item: dict[str, Any]) -> float:
        return float(item["trajectory"][-1])

    wet = max(scenarios, key=final) if scenarios else None
    dry = min(scenarios, key=final) if scenarios else None
    return {
        "first_lap": current_lap,
        "mean": mean,
        "wet_case": [round(float(value), 3) for value in (wet or {}).get("trajectory", mean)],
        "dry_case": [round(float(value), 3) for value in (dry or {}).get("trajectory", mean)],
        "p_wet_at_flag": round(sum(
            float(item.get("probability", 0.0) or 0.0) for item in scenarios
            if final(item) >= rain.WETNESS_SLICK_INTER
        ), 3),
    }


def _tyres(crossover: dict[str, Any], fitted: str) -> list[dict[str, Any]]:
    options = crossover.get("options") or []
    penalties = crossover.get("expected_lap_penalties") or {}
    base = finite(crossover.get("dry_base_lap_s")) or 0.0
    staying = next((item for item in options if not item.get("is_change")), None)
    box_compound = str(crossover.get("compound") or "").upper() if crossover.get("worth_stopping") else ""
    rows = []
    for item in options:
        compound = str(item.get("compound", "")).upper()
        loss = penalties.get(compound) or [None]
        row: dict[str, Any] = {
            "compound": compound,
            "fitted": compound == fitted,
            "loss_now_s_per_lap": round(float(loss[0]) * base, 1) if loss and loss[0] is not None and base else None,
            "cost_to_flag_s": round(float(item.get("weather_cost_s", 0.0)), 1),
        }
        if item.get("is_change"):
            row["stop_cost_s"] = round(float(item.get("change_overhead_s", 0.0)), 1)
            if staying is not None:
                row["net_vs_staying_s"] = round(float(item.get("total_s", 0.0)) - float(staying.get("total_s", 0.0)), 1)
            if compound == box_compound:
                row["box_lap"] = crossover.get("box_lap")
        rows.append(row)
    return rows


def _confirmed_by(crossover: dict[str, Any], direction: str) -> list[str]:
    """Which evidence agrees with the call, in the confirmation gate's terms."""
    source = crossover.get("wetness_source") or {}
    wetness = float(crossover.get("wetness", 0.0) or 0.0)
    split = float((crossover.get("compound_split") or {}).get("wet_advantage_fraction", 0.0) or 0.0)
    report = str((source.get("driver_report") or {}).get("category") or "")
    measured = finite(source.get("measured_pace"))
    fast = finite(source.get("fast_measured"))
    pace_ok = float(source.get("pace_confidence", 0.0) or 0.0) >= 0.5 and measured is not None
    fast_ok = float(source.get("fast_confidence", 0.0) or 0.0) >= 0.5 and fast is not None
    found = []
    if direction == "to_dry":
        checks = (
            ("surface_model", wetness <= rain.WETNESS_SLICK_INTER + 0.02),
            ("lap_pace", pace_ok and measured is not None and measured <= 0.30),
            ("sector_pace", fast_ok and fast is not None and fast <= 0.30),
            ("split_field", split < 0.0),
            ("driver", report in {"drying", "dry_line"}),
        )
    else:
        checks = (
            ("surface_model", wetness >= rain.WETNESS_SLICK_INTER - 0.02),
            ("lap_pace", pace_ok and measured is not None and measured >= 0.20),
            ("sector_pace", fast_ok and fast is not None and fast >= 0.20),
            ("split_field", split > 0.0),
            ("driver", report in {"damp", "wet", "soaked", "flooded"}),
        )
    for name, holds in checks:
        if holds:
            found.append(name)
    return found


def _crossover_clock(mean: list[float], fitted: str) -> dict[str, Any] | None:
    """Laps until the projected surface crosses into the other tyre's range."""
    if not mean:
        return None
    if fitted in WET_TYRES:
        threshold = rain.WETNESS_SLICK_INTER
        index = next((i for i, value in enumerate(mean) if value < threshold), None)
        target = "slicks"
    else:
        threshold = rain.WETNESS_SLICK_INTER
        index = next((i for i, value in enumerate(mean) if value >= threshold), None)
        target = "INTER"
    return {
        "to": target,
        "laps": index,
        "basis": "projected mean surface against the slick and intermediate crossover",
    }


def weather_picture(state: dict[str, Any]) -> dict[str, Any]:
    """The wet model as one picture. Pure: reads only what the engine published."""
    from .proactive import WEATHER_LEVEL_LABELS, ProactiveEngineer

    crossover = (state.get("strategy", {}) or {}).get("weather_crossover") or {}
    fitted = str((state.get("tyre", {}) or {}).get("compound") or "UNKNOWN").upper()
    current_lap = int(state.get("current_lap", 0) or 0)
    lap_s = finite(crossover.get("lap_s")) or finite(crossover.get("dry_base_lap_s")) or 90.0
    forecast = _forecast(state, lap_s)
    source = crossover.get("wetness_source") or {}
    wetness = float(crossover.get("wetness", 0.0) or 0.0)
    projection = _projection(crossover, current_lap) if crossover else None
    report = source.get("driver_report") or {}
    report_recent = bool(report) and int(report.get("laps_ago", 99) or 0) <= RELEVANT_REPORT_LAPS
    relevant = bool(
        wetness > 0.05
        or rain.equilibrium_wetness(str(state.get("weather", ""))) > 0
        or any(max(item.get("trajectory") or [0.0]) >= rain.WETNESS_SLICK_INTER for item in crossover.get("scenarios") or [])
        or any((row["chance_pct"] or 0) >= RELEVANT_CHANCE_PCT for row in forecast)
        or report_recent
    )
    call = ProactiveEngineer._weather_call(state)
    level = int(call["level"])
    picture: dict[str, Any] = {
        "available": bool(crossover) or bool(forecast) or bool(state.get("weather")),
        "relevant": relevant,
        "sky": {
            "now": state.get("weather"),
            "since_lap": _since_lap(state),
            "track_temp_c": state.get("track_temp_c"),
            "air_temp_c": state.get("air_temp_c"),
        },
        "surface": {
            "wetness": round(wetness, 3),
            "word": _surface_word(wetness),
            "trend": crossover.get("trend"),
            "sources": {
                "surface_model": source.get("surface_model"),
                "lap_pace": source.get("measured_pace"),
                "sector_pace": source.get("fast_measured"),
                "driver": report.get("wetness") if report else None,
            },
            "confidence": source.get("pace_confidence"),
            "field_cars": source.get("field_cars"),
            "crossovers": {"slick_inter": rain.WETNESS_SLICK_INTER, "inter_wet": rain.WETNESS_INTER_WET},
            "flags": {"field_surface_temp_drop_c": source.get("field_surface_temp_drop_c")},
        },
        "forecast": forecast,
        "projection": projection,
        "tyres": _tyres(crossover, fitted) if crossover else [],
        "call": {
            "level": level,
            "label": WEATHER_LEVEL_LABELS[level],
            "compound": call.get("compound") or None,
            "box_lap": call.get("box_lap"),
            "direction": call.get("direction"),
            "shows_s": crossover.get("margin_s"),
            "needs_s": crossover.get("uncertainty_s"),
            "confirmed": crossover.get("confirmed", True) if crossover else None,
            "confirmed_by": _confirmed_by(crossover, str(call.get("direction"))) if crossover else [],
            "reason": crossover.get("reason"),
        },
        "crossover_clock": _crossover_clock((projection or {}).get("mean") or [], fitted),
        # Every curve behind these numbers is a judgement until recorded wet
        # races have fitted them.
        "calibration": "judgement",
    }
    if not relevant:
        picture["summary"] = "Dry. No rain forecast inside the race."
    return picture
