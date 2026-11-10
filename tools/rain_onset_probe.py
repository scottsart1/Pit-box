"""How many laps of rain does the wet model need before it calls the stop?

A deterministic probe of ``pitwall.rain.evaluate``. Light rain starts at the
beginning of one lap of a dry race. The surface then soaks at the model's own
rate and every car on slicks laps at the model's own penalty for that
wetness, so the model is being asked to recognise its own physics. The only
thing that varies between scenarios is what the game's forecast says about
rain that is already falling, and which laps the evidence filters keep.

Run:
    python -m tools.rain_onset_probe
    python -m tools.rain_onset_probe --assert-within 2

``--assert-within N`` exits non-zero unless every rain scenario produces a
box call within N laps of the rain starting, keeps that call on every later
lap, and the dry control produces no call. Use it as an acceptance gate for
changes to the wet model. See docs/assessment-5.6.0/01-weather.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from statistics import median
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pitwall import rain  # noqa: E402

DRY_LAP_S = 90.0
TOTAL_LAPS = 40
PIT_LOSS_S = 22.0
WINDOW_LAPS = 9
FORECAST_OFFSETS_MIN = (0, 5, 10, 15, 30, 45, 60)
AVAILABLE = ["MEDIUM", "HARD", "INTER", "WET"]


def true_wetness(lap_end: int, rain_lap: int) -> float:
    """Surface at the end of ``lap_end`` when light rain began on ``rain_lap``."""
    wet = 0.0
    for _ in range(max(0, lap_end - rain_lap + 1)):
        wet = rain.step_wetness(wet, 0.52, track_temp_c=28, active_cars=20)
    return wet


def lap_time_s(lap: int, rain_lap: int, compound: str = "MEDIUM") -> float:
    if lap < rain_lap:
        return DRY_LAP_S
    mid = (true_wetness(lap - 1, rain_lap) + true_wetness(lap, rain_lap)) / 2
    return DRY_LAP_S * (1 + rain.lap_penalty_fraction(compound, mid))


def player_laps(
    upto: int, rain_lap: int, exclusions: dict[int, list[str]]
) -> list[dict[str, Any]]:
    laps = []
    for number in range(1, upto + 1):
        ms = int(lap_time_s(number, rain_lap) * 1000)
        third = ms // 3
        laps.append(
            {
                "lap_num": number,
                "lap_time_ms": ms,
                "valid": True,
                "pit_status": 0,
                "weather": "Light rain" if number >= rain_lap else "Clear",
                "compound": "MEDIUM",
                "track_temp_c": 28,
                "s1_ms": third,
                "s2_ms": third,
                "s3_ms": ms - 2 * third,
                "learning_exclusions": list(exclusions.get(number, [])),
                # A neutralised lap in these scenarios is a yellow shown to
                # this car, recorded as the app records it: a local flag,
                # not a safety car.
                "full_course_neutralised": False,
                "local_yellow": "neutralised_lap" in exclusions.get(number, []),
            }
        )
    return laps


def rivals(
    upto: int, rain_lap: int, cars: int = 19, *, sectors_of_lap: int | None = None,
) -> list[dict[str, Any]]:
    """The field, with sector times on every lap.

    ``sectors_of_lap`` posts the first two sectors of that lap, in progress,
    as the lap-data packet does at mid-lap.
    """
    field = []
    for index in range(1, cars + 1):
        history = []
        for number in range(1, upto + 1):
            ms = int(lap_time_s(number, rain_lap) * 1000 + index * 40)
            history.append({
                "lap_num": number, "lap_ms": ms, "valid_flags": 1,
                "s1_ms": ms // 3, "s2_ms": ms // 3, "s3_ms": ms - 2 * (ms // 3),
            })
        car = {
            "car_idx": index,
            "name": f"CAR{index}",
            "active": True,
            "result_label": "active",
            "tyre_compound": "MEDIUM",
            "lap_history": history,
            "tyre_stints": [{"start_lap": 1, "end_lap": 255, "compound": "MEDIUM"}],
        }
        if sectors_of_lap is not None:
            ms = int(lap_time_s(sectors_of_lap, rain_lap) * 1000 + index * 40)
            car.update(current_lap=sectors_of_lap, current_s1_ms=ms // 3, current_s2_ms=ms // 3)
        field.append(car)
    return field


def state_at(
    current_lap: int,
    rain_lap: int,
    rain_pct: int,
    *,
    accuracy: int = 0,
    with_field: bool = True,
    exclusions: dict[int, list[str]] | None = None,
    forecast_weather: str = "Light rain",
    weather_now: str | None = None,
    mid_lap: bool = False,
) -> dict[str, Any]:
    completed = current_lap - 1
    raining = current_lap >= rain_lap
    position = {"track_length_m": 5000, "lap_distance_m": 3400} if mid_lap else {}
    return {
        **position,
        "weather": weather_now or ("Light rain" if raining else "Clear"),
        "weather_forecast": [
            {
                "time_offset_min": offset,
                "weather": forecast_weather,
                "rain_pct": rain_pct,
                "track_temp_c": 26,
                "air_temp_c": 20,
            }
            for offset in FORECAST_OFFSETS_MIN
        ],
        "weather_forecast_session_time_s": 1000.0,
        "session_time_s": 1000.0,
        "forecast_accuracy": accuracy,
        "track_temp_c": 28,
        "active_cars": 20,
        "completed_laps": player_laps(completed, rain_lap, exclusions or {}),
        "tyre": {"compound": "MEDIUM"},
        "current_lap": current_lap,
        "player_car_index": 0,
        "drivers": (
            rivals(completed, rain_lap, sectors_of_lap=current_lap if mid_lap else None)
            if with_field else []
        ),
    }


def evaluate_at(lap: int, rain_lap: int, rain_pct: int, **overrides: Any) -> dict[str, Any]:
    recent = [lap_time_s(number, rain_lap) for number in range(max(1, lap - 4), lap)]
    return rain.evaluate(
        state_at(lap, rain_lap, rain_pct, **overrides),
        base_lap_s=float(median(recent)) if recent else DRY_LAP_S,
        remaining_laps=TOTAL_LAPS - lap + 1,
        pit_loss_s=PIT_LOSS_S,
        available_compounds=AVAILABLE,
    )


def run_scenario(label: str, rain_pct: int, verbose: bool, **overrides: Any) -> dict[str, Any]:
    rain_lap = 11
    calls: list[bool] = []
    reasons: list[str] = []
    if verbose:
        print(f"\n=== {label} ===")
        print("lap  true   est    surf   meas  conf  best    off  margin  needs  box    reason")
    for lap in range(rain_lap, rain_lap + WINDOW_LAPS):
        decision = evaluate_at(lap, rain_lap, rain_pct, **overrides)
        reading = decision["reading"]
        calls.append(bool(decision["should_change"]))
        reasons.append(str(decision["reason"]))
        if verbose:
            measured = reading["measured_wetness"]
            print(
                f"{lap:>3}  {true_wetness(lap - 1, rain_lap):.3f}  {reading['wetness']:.3f}  "
                f"{reading['dynamic_wetness']:.3f}  "
                f"{(measured if measured is not None else float('nan')):.2f}  "
                f"{reading['pace_confidence']:.2f}  {decision['best_compound']:<7} "
                f"{decision['best_box_lap_offset']!s:>3}  {decision['margin_s']:>6.1f}  "
                f"{decision['uncertainty_s']:>5.1f}  {decision['should_change']!s:<6} "
                f"{decision['reason'][:74]}"
            )
    first = next((index for index, called in enumerate(calls) if called), None)
    withdrawn = first is not None and not all(calls[first:])
    lost = (
        None
        if first is None
        else sum(
            lap_time_s(lap, rain_lap) - lap_time_s(lap, rain_lap, "INTER")
            for lap in range(rain_lap, rain_lap + first + 1)
        )
    )
    wrong_tyre = any(
        "HARD is the tyre for it" in reason or "SOFT is the tyre for it" in reason
        for reason in reasons
    )
    return {
        "label": label,
        "first_call_after_laps": first,
        "withdrawn_later": withdrawn,
        "slick_time_lost_s": lost,
        "names_a_dry_tyre_as_the_wet_answer": wrong_tyre,
    }


def run_dry_control(verbose: bool, **timing: Any) -> dict[str, Any]:
    """Dry sky, dry track, and a 50% forecast that never arrives."""
    false_calls = 0
    for lap in (12, 13, 14):
        decision = evaluate_at(lap, 999, 50, weather_now="Overcast", **timing)
        false_calls += int(bool(decision["should_change"]))
        if verbose:
            print(
                f"control lap {lap}: wetness {decision['wetness']}, "
                f"box {decision['should_change']}, {decision['reason'][:80]}"
            )
    return {"label": "dry control, forecast 50%", "false_calls": false_calls}


SCENARIOS: tuple[tuple[str, int, dict[str, Any]], ...] = (
    ("forecast 100% light rain", 100, {}),
    ("forecast 70% light rain", 70, {}),
    ("forecast 50% light rain", 50, {}),
    ("forecast 30% light rain", 30, {}),
    ("forecast 50%, approximate accuracy", 50, {"accuracy": 1}),
    ("forecast 50%, no field history", 50, {"with_field": False}),
    (
        "forecast 50%, onset lap excluded as weather_change, next lap yellow",
        50,
        {"exclusions": {11: ["weather_change"], 12: ["neutralised_lap"]}},
    ),
    ("forecast 50%, forecast category Overcast", 50, {"forecast_weather": "Overcast"}),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--quiet", action="store_true", help="summary only")
    parser.add_argument(
        "--assert-within",
        type=int,
        default=None,
        metavar="LAPS",
        help="fail unless every rain scenario boxes within this many laps and holds the call",
    )
    parser.add_argument(
        "--mid-lap",
        action="store_true",
        help="evaluate two-thirds into each lap, with the field's first two sectors posted",
    )
    args = parser.parse_args()

    timing = {"mid_lap": True} if args.mid_lap else {}
    results = [
        run_scenario(label, pct, not args.quiet, **timing, **overrides)
        for label, pct, overrides in SCENARIOS
    ]
    control = run_dry_control(not args.quiet, **timing)

    print("\nscenario                                                             first call   held  slick loss")
    for item in results:
        first = item["first_call_after_laps"]
        lost = item["slick_time_lost_s"]
        print(
            f"{item['label']:<68} "
            f"{('lap +' + str(first)) if first is not None else 'never':<12} "
            f"{'no' if item['withdrawn_later'] else 'yes' if first is not None else '-':<5} "
            f"{(format(lost, '.1f') + ' s') if lost is not None else '-'}"
        )
    print(f"{control['label']:<68} false calls: {control['false_calls']}")

    if args.assert_within is None:
        return 0
    failures = [
        item["label"]
        for item in results
        if item["first_call_after_laps"] is None
        or item["first_call_after_laps"] > args.assert_within
        or item["withdrawn_later"]
        or item["names_a_dry_tyre_as_the_wet_answer"]
    ]
    if control["false_calls"]:
        failures.append(control["label"])
    for label in failures:
        print(f"FAIL: {label}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
