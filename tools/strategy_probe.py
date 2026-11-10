"""Deterministic probes of the strategy engine's race picture.

Five questions, each answered from a synthetic but internally consistent race
state driven through the real engine:

1. Does the engine expect rivals to make the stop the rules force on them?
2. Does a what-if for the stop the engine itself recommends price as level
   with that recommendation on a wet track?
3. Can the radio say "stay out" over a stop the engine has called for the
   weather?
4. Does the strategic-rivals view report the player's projected position?
5. When the weather verdict changes from "watching" to "box now", is a
   weather call queued for it?

Run:
    python -m tools.strategy_probe
    python -m tools.strategy_probe --assert

``--assert`` exits non-zero when any answer is the wrong one, so the probe can
gate changes to rival modelling, what-if pricing and the radio guards. See
docs/assessment-5.6.0/02-rivals.md and 03-strategy-core.md.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import time
import types
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pitwall.database import PitWallDatabase  # noqa: E402
from pitwall.engineering import strategic_rivals  # noqa: E402
from pitwall.proactive import ProactiveEngineer  # noqa: E402
from pitwall.session_guard import session_key  # noqa: E402
from pitwall.state import DriverState, StateStore  # noqa: E402
from pitwall.strategy import StrategyEngine  # noqa: E402

SPACING_S = 1.6


def build_field(
    *,
    current_lap: int,
    player_position: int,
    compound: str,
    tyre_age: int,
    pit_stops: int = 0,
    spacing_s: float = SPACING_S,
    lap_ms: Any = 90_000,
) -> list[DriverState]:
    """Twenty cars in classification order with the player at one position."""
    drivers = []
    for index in range(20):
        driver = DriverState(car_idx=index, name="PLAYER" if index == 0 else f"CAR{index}")
        driver.active = True
        driver.is_player = index == 0
        driver.result_label = "active"
        driver.result_status = 2
        driver.current_lap = current_lap
        driver.tyre_compound = compound
        driver.tyre_age = tyre_age
        driver.pit_stops = pit_stops
        driver.lap_history = [
            {
                "lap_num": number,
                "lap_ms": (lap_ms(number) if callable(lap_ms) else lap_ms) + index * 30,
                "valid_flags": 1,
            }
            for number in range(1, current_lap)
        ]
        driver.tyre_stints = [{"start_lap": 1, "end_lap": 255, "compound": compound}]
        if index == 0:
            # The player's own pace when no completed laps are recorded: the
            # same as the field's, so a finishing position measures the plan
            # rather than a car 5 s a lap off the pace.
            driver.best_lap_ms = int(lap_ms(current_lap - 1) if callable(lap_ms) else lap_ms)
        drivers.append(driver)
    drivers[0].position = player_position
    others = [position for position in range(1, 21) if position != player_position]
    for driver, position in zip(drivers[1:], others):
        driver.position = position
    for driver in drivers:
        driver.delta_to_leader_s = (driver.position - 1) * spacing_s
        driver.gap_to_player_s = (driver.position - player_position) * spacing_s
    return drivers


async def new_engine() -> tuple[StateStore, StrategyEngine]:
    store = StateStore()
    database = PitWallDatabase(Path(tempfile.mkdtemp()) / "probe.sqlite3")
    await database.initialize()
    return store, StrategyEngine(store, database)


def race_basics(state: Any, *, total_laps: int, current_lap: int, position: int) -> None:
    state.session_type = "Race"
    state.mode_profile = "race"
    state.track_id = 3
    state.total_laps = total_laps
    state.current_lap = current_lap
    state.player_car_index = 0
    state.player_position = position
    state.active_cars = 20
    state.connected = True
    state.track_temp_c = 30


async def probe_rival_mandatory_stops() -> list[str]:
    failures: list[str] = []
    print("\n1. Rivals who still owe a second dry compound")
    for total_laps, current_lap, label in (
        (14, 4, "25% distance"),
        (20, 5, "35% distance"),
        (29, 8, "50% distance"),
        (57, 12, "100% distance"),
    ):
        store, engine = await new_engine()

        def setup(state: Any, total_laps: int = total_laps, current_lap: int = current_lap) -> None:
            race_basics(state, total_laps=total_laps, current_lap=current_lap, position=10)
            state.weather = "Clear"
            state.tyre.compound = "MEDIUM"
            state.tyre.age_laps = current_lap - 1
            state.tyre.wear = [3.0 * (current_lap - 1)] * 4
            state.tyre_sets = [
                {"compound": name, "available": True, "index": index}
                for index, name in enumerate(("SOFT", "MEDIUM", "HARD", "HARD"))
            ]
            state.drivers = build_field(
                current_lap=current_lap,
                player_position=10,
                compound="MEDIUM",
                tyre_age=current_lap - 1,
            )

        await store.mutate(setup)
        started = time.perf_counter()
        plan = await engine.recompute()
        elapsed_ms = (time.perf_counter() - started) * 1000
        recommended = plan.get("recommended", {})
        projections = plan.get("rival_finish_projections", [])
        stops = sorted({int(item["likely_remaining_stops"]) for item in projections})
        prediction = await engine.predict_rival_strategy(top_n=24)
        box_lap = recommended.get("box_lap")
        print(
            f"   {label:<14} {total_laps:>2} laps | rival stops projected {stops} | "
            f"{prediction.get('another_stop_counts')} | call: {recommended.get('instruction')} "
            f"| projected P{recommended.get('projected_finish_position')} | {elapsed_ms:.0f} ms"
        )
        rule_applies = bool((plan.get("observed_compound_rule") or {}).get("applies"))
        if rule_applies and stops and min(stops) < 1:
            failures.append(f"{label}: rivals owing a compound are projected to make no stop")
        if box_lap is not None and int(box_lap) > total_laps - 2:
            failures.append(f"{label}: first stop called for lap {box_lap} of {total_laps}")
    return failures


def wet_setup(state: Any, *, total_laps: int, current_lap: int, position: int) -> None:
    race_basics(state, total_laps=total_laps, current_lap=current_lap, position=position)
    state.weather = "Heavy rain"
    state.track_temp_c = 24
    state.weather_forecast = [
        {
            "time_offset_min": offset,
            "weather": "Heavy rain",
            "rain_pct": 100,
            "track_temp_c": 22,
            "air_temp_c": 18,
        }
        for offset in (0, 5, 10, 15, 30, 45, 60)
    ]
    state.session_time_s = 1500.0
    state.weather_forecast_session_time_s = 1500.0
    state.tyre_sets = [
        {"compound": name, "available": True, "index": index}
        for index, name in enumerate(("SOFT", "HARD", "INTER", "WET"))
    ]


async def probe_wet_what_if() -> list[str]:
    failures: list[str] = []
    print("\n2. What-if on a wet track")
    store, engine = await new_engine()

    def setup(state: Any) -> None:
        wet_setup(state, total_laps=40, current_lap=15, position=10)
        state.tyre.compound = "MEDIUM"
        state.tyre.age_laps = 14
        state.tyre.wear = [30.0] * 4
        state.completed_laps = [
            {
                "lap_num": number,
                "lap_time_ms": 90_000 if number < 11 else 104_000,
                "valid": True,
                "pit_status": 0,
                "weather": "Clear" if number < 11 else "Heavy rain",
                "compound": "MEDIUM",
                "track_temp_c": 24,
            }
            for number in range(1, 15)
        ]
        state.drivers = build_field(
            current_lap=15,
            player_position=10,
            compound="MEDIUM",
            tyre_age=14,
            lap_ms=lambda number: 90_000 if number < 11 else 104_000,
        )

    await store.mutate(setup)
    plan = await engine.recompute()
    recommended = plan.get("recommended", {})
    box_lap, fit = recommended.get("box_lap"), recommended.get("fit_compound")
    print(
        f"   engine: {recommended.get('instruction')} "
        f"(surface {(plan.get('weather_crossover') or {}).get('wetness')})"
    )
    deltas: dict[str, float] = {}
    for compound in ("INTER", "HARD"):
        outcome = await engine.what_if(
            f"box lap {box_lap or 15} for {compound}", lap=int(box_lap or 15), compound=compound
        )
        deltas[compound] = float(outcome.get("delta_to_best_s", 0.0))
        print(f"   what-if {compound:<5}: {deltas[compound]:+.1f} s against the recommended plan")
    if fit == "INTER" and abs(deltas["INTER"]) > 5.0:
        failures.append(
            f"what-if for the recommended stop is {deltas['INTER']:+.0f} s from the plan it matches"
        )
    if deltas["INTER"] >= deltas["HARD"]:
        failures.append("what-if prices slicks ahead of intermediates in heavy rain")
    return failures


async def probe_radio_guard_and_rivals_view() -> list[str]:
    failures: list[str] = []
    print("\n3. The radio's stay-out guard against a weather stop")
    store, engine = await new_engine()

    def setup(state: Any) -> None:
        wet_setup(state, total_laps=40, current_lap=30, position=1)
        state.tyre.compound = "HARD"
        state.tyre.age_laps = 9
        state.tyre.wear = [14.0] * 4
        state.completed_laps = [
            {
                "lap_num": number,
                "lap_time_ms": 90_000 if number < 26 else 104_000,
                "valid": True,
                "pit_status": 0,
                "weather": "Clear" if number < 26 else "Heavy rain",
                "compound": "SOFT" if number < 21 else "HARD",
                "track_temp_c": 24,
            }
            for number in range(1, 30)
        ]
        state.drivers = build_field(
            current_lap=30,
            player_position=1,
            compound="HARD",
            tyre_age=9,
            pit_stops=1,
            spacing_s=3.0,
            lap_ms=lambda number: 90_000 if number < 26 else 104_000,
        )

    await store.mutate(setup)
    plan = await engine.recompute()
    recommended = plan.get("recommended", {})
    state = await store.snapshot_radio()
    spoken = str(ProactiveEngineer._spoken_advice(recommended, state).get("instruction") or "")
    print(f"   engine: {recommended.get('instruction')}")
    print(
        f"   the stop is {recommended.get('net_gain_vs_stay_out_s')} s quicker to the flag than "
        f"staying out | projected P{recommended.get('projected_finish_position')}, "
        f"{recommended.get('positions_gained_vs_stay_out')} place(s) against staying out, "
        f"{recommended.get('positions_lost_by_stopping')} lost in the lane"
    )
    print(f"   radio:  {spoken}")
    weather_stop = bool((recommended.get("weather_crossover") or {}).get("worth_stopping"))
    if weather_stop and spoken.lower().startswith("stay out"):
        failures.append("the radio says stay out over a stop called for the weather")

    print("\n4. Strategic rivals view")
    view = strategic_rivals(await store.snapshot_analysis())
    print(f"   player_projected_position: {view.get('player_projected_position')}")
    if view.get("available") and view.get("player_projected_position") is None:
        failures.append("strategic rivals view has no projected position for the player")
    return failures


class _SilentBrain:
    def __init__(self, database: PitWallDatabase) -> None:
        self.database = database

    async def proactive(self, event: dict[str, Any]) -> str:
        return ""

    async def record_spoken_call(self, text: str) -> None:
        return None


class _SilentVoice:
    is_busy = False

    async def speak_text(self, text: str) -> bool:
        return True


class _NoSetup:
    async def learn_current_session(self) -> bool:
        return True


async def probe_weather_radio_trigger() -> list[str]:
    """Two detection passes: the track is wet and watched, then the stop is called."""
    failures: list[str] = []
    print("\n5. The weather radio trigger")
    store, strategy = await new_engine()
    engineer = ProactiveEngineer(
        store,
        _SilentBrain(strategy.database),  # type: ignore[arg-type]
        _SilentVoice(),  # type: ignore[arg-type]
        _NoSetup(),  # type: ignore[arg-type]
        strategy,
    )

    async def published_plan_stands(self: ProactiveEngineer, state: dict[str, Any]) -> dict[str, Any]:
        return state

    engineer._refresh_strategy_if_needed = types.MethodType(published_plan_stands, engineer)  # type: ignore[method-assign]
    queued: list[tuple[str, Any]] = []
    enqueue = engineer._enqueue

    def recording(event_type: str, payload: dict[str, Any], **options: Any) -> bool:
        accepted = enqueue(event_type, payload, **options)
        if accepted and event_type in {"weather_crossover", "strategy_change"}:
            queued.append((event_type, payload.get("instruction") or payload.get("reason")))
        return accepted

    engineer._enqueue = recording  # type: ignore[method-assign]

    async def detect(crossover: dict[str, Any], recommended: dict[str, Any]) -> None:
        await store.update(
            connected=True, session_uid=42, game_paused=False, mode_profile="race",
            current_lap=13, total_laps=40, speed_kph=200, weather="Light rain",
            strategy={"weather_crossover": crossover, "recommended": recommended,
                      "stability": {"held": False}},
        )
        state = await store.snapshot_radio()
        engineer._session_uid = 42
        engineer._session_context = session_key(state)
        await engineer._detect(state)

    watching = {
        "wetness": 0.41, "worth_stopping": False, "ask_driver": False, "compound": "MEDIUM",
        "box_lap": None, "reason": "Track is wet; staying out for now.",
    }
    box_now = {
        "wetness": 0.47, "worth_stopping": True, "ask_driver": False, "compound": "INTER",
        "box_lap": 13, "time_offset_min": 0, "reason": "Track is wet; INTER is worth 80s.",
    }
    await detect(watching, {
        "box_lap": 25, "fit_compound": "HARD", "stops_remaining": 1,
        "instruction": "Box lap 25 for HARD.",
    })
    first_pass = len(queued)
    await detect(box_now, {
        "box_lap": 13, "fit_compound": "INTER", "stops_remaining": 1,
        "instruction": "Box lap 13 for INTER.", "weather_crossover": box_now,
        "positions_gained_vs_stay_out": -2, "positions_lost_by_stopping": 7,
        "stop_required_reason": "best projected finishing position and expected-points outcome",
    })
    for kind, text in queued[:first_pass]:
        print(f"   track wet, watching -> {kind}: {text}")
    for kind, text in queued[first_pass:]:
        print(f"   verdict: box now    -> {kind}: {text}")
    after = queued[first_pass:]
    if not any(kind == "weather_crossover" for kind, _ in after):
        print("   verdict: box now    -> no weather call queued")
        failures.append("no weather call is queued when a watched track becomes a stop")
    if any(str(text or "").lower().startswith("stay out") for _, text in after):
        failures.append("the only call queued for the weather stop says stay out")
    return failures


async def run() -> list[str]:
    failures = await probe_rival_mandatory_stops()
    failures += await probe_wet_what_if()
    failures += await probe_radio_guard_and_rivals_view()
    failures += await probe_weather_radio_trigger()
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--assert", dest="gate", action="store_true", help="exit non-zero on a wrong answer"
    )
    args = parser.parse_args()
    failures = asyncio.run(run())
    print()
    for failure in failures:
        print(f"FAIL: {failure}")
    if not failures:
        print("All probes gave the expected answer.")
    return 1 if failures and args.gate else 0


if __name__ == "__main__":
    raise SystemExit(main())
