"""Regressions for the 3.5 feature set.

Grouped by feature area. Each test drives the deterministic layer directly so
the numeric behaviour is pinned independently of any language model.
"""

from __future__ import annotations

import time as _time
import types

import pytest

from pitwall.proactive import ProactiveEngineer
from pitwall.state import DriverState, StateStore
from pitwall.strategy import TYPICAL_STINT_LAPS, points_for_position


def _lap(uid: int, num: int, ms: int, s1: int, s2: int, s3: int, created: float) -> dict:
    return {
        "session_uid": uid,
        "track_id": 10,
        "track_name": "Spa",
        "session_type": "Race",
        "lap_num": num,
        "lap_time_ms": ms,
        "valid": True,
        "compound": "MEDIUM",
        "s1_ms": s1,
        "s2_ms": s2,
        "s3_ms": s3,
        "trace": [],
        "setup": {},
        "created_at": created,
    }


# --- sector bests / theoretical best (item 13) ------------------------------


@pytest.mark.asyncio
async def test_sector_bests_compose_theoretical_from_independent_minima(stack) -> None:
    _, database, _, _, _, _ = stack
    uid = 2**63 + 1
    await database.upsert_session(
        {"session_uid": uid, "track_id": 10, "track_name": "Spa",
         "session_type": "Race", "mode_profile": "race", "total_laps": 20}
    )
    # Best S1 and S3 live on lap 2; best S2 on lap 1. No single lap holds all.
    await database.save_lap(_lap(uid, 1, 105_000, 34_000, 35_500, 35_000, 1.0), [])
    await database.save_lap(_lap(uid, 2, 104_000, 33_500, 36_200, 34_200, 2.0), [])
    data = await database.sector_bests(10)
    assert int(data["sector_bests"]["s1_ms"]["ms"]) == 33_500
    assert int(data["sector_bests"]["s2_ms"]["ms"]) == 35_500
    assert int(data["sector_bests"]["s3_ms"]["ms"]) == 34_200
    assert data["theoretical_best_ms"] == 33_500 + 35_500 + 34_200
    assert data["personal_best_lap_ms"] == 104_000
    # Theoretical is quicker than any real lap, so time is left on the table.
    assert data["time_left_on_table_ms"] == 104_000 - 103_200


@pytest.mark.asyncio
async def test_sector_bests_ignore_zero_sector_legacy_rows(stack) -> None:
    """Pre-3.4 rows stored zero sectors and must not become the minimum."""
    _, database, _, _, _, _ = stack
    uid = 2**63 + 5
    await database.upsert_session(
        {"session_uid": uid, "track_id": 10, "track_name": "Spa",
         "session_type": "Race", "mode_profile": "race", "total_laps": 20}
    )
    await database.save_lap(_lap(uid, 1, 105_000, 0, 0, 0, 1.0), [])
    await database.save_lap(_lap(uid, 2, 104_000, 33_500, 36_000, 34_000, 2.0), [])
    data = await database.sector_bests(10)
    assert int(data["sector_bests"]["s1_ms"]["ms"]) == 33_500


@pytest.mark.asyncio
async def test_sector_bests_theoretical_none_when_a_sector_missing(stack) -> None:
    _, database, _, _, _, _ = stack
    uid = 2**63 + 6
    await database.upsert_session(
        {"session_uid": uid, "track_id": 10, "track_name": "Spa",
         "session_type": "Race", "mode_profile": "race", "total_laps": 20}
    )
    await database.save_lap(_lap(uid, 1, 105_000, 34_000, 0, 35_000, 1.0), [])
    data = await database.sector_bests(10)
    assert data["theoretical_best_ms"] is None
    assert data["time_left_on_table_ms"] is None


# --- progress trend (item 12) -----------------------------------------------


@pytest.mark.asyncio
async def test_progress_trend_orders_sessions_and_signs_improvement(stack) -> None:
    _, database, _, _, _, _ = stack
    for i, (uid, best) in enumerate([(2**63 + 10, 105_000), (2**63 + 11, 104_000)]):
        await database.upsert_session(
            {"session_uid": uid, "track_id": 10, "track_name": "Spa",
             "session_type": "Race", "mode_profile": "race", "total_laps": 20}
        )
        await database.save_lap(_lap(uid, 1, best, 34_000, 36_000, 35_000, float(i)), [])
    data = await database.progress_trend(10)
    assert data["session_count"] == 2
    # Newer session is quicker, so improvement is negative.
    assert data["improvement_ms"] == 104_000 - 105_000
    assert data["sessions"][0]["best_lap_ms"] == 105_000
    assert data["sessions"][-1]["best_lap_ms"] == 104_000


# --- setup correlation (item 14) --------------------------------------------


@pytest.mark.asyncio
async def test_setup_correlation_ranks_runs_by_score(stack) -> None:
    _, database, _, _, _, _ = stack
    uid = 2**63 + 20
    await database.save_setup_run(uid, 10, "Spa", "race", {"front_wing": 30}, {"top_speed": 330}, 1.20)
    await database.save_setup_run(uid, 10, "Spa", "race", {"front_wing": 40}, {"top_speed": 322}, 0.95)
    data = await database.setup_correlation(10, "race")
    assert data["run_count"] == 2
    assert data["best_run"]["score"] == 0.95
    assert data["best_run"]["setup"]["front_wing"] == 40
    assert data["worst_run"]["score"] == 1.20


@pytest.mark.asyncio
async def test_setup_correlation_worst_run_none_with_single_run(stack) -> None:
    _, database, _, _, _, _ = stack
    uid = 2**63 + 21
    await database.save_setup_run(uid, 10, "Spa", "race", {"front_wing": 30}, {}, 1.0)
    data = await database.setup_correlation(10, "race")
    assert data["best_run"] is not None
    assert data["worst_run"] is None


# --- tool surface -----------------------------------------------------------


@pytest.mark.asyncio
async def test_new_analytics_tools_are_registered_and_callable(stack) -> None:
    store, database, _, _, _, tools = stack
    await store.update(track_id=10, track_name="Spa")
    names = {schema["name"] for schema in tools.schemas()}
    for expected in ("get_sector_bests", "get_progress_trend", "get_setup_correlation"):
        assert expected in names
        assert await tools.call(expected, {} if expected != "get_setup_correlation" else {"profile": "all"})


# ==========================================================================
# Batch 2 — strategy depth
# ==========================================================================


def _driver(idx: int, name: str, pos: int, gap: float, comp: str, age: int) -> DriverState:
    d = DriverState(car_idx=idx)
    d.active = True
    d.name = name
    d.position = pos
    d.gap_to_player_s = gap
    d.tyre_compound = comp
    d.tyre_age = age
    return d


def test_points_table_matches_f1_system() -> None:
    assert points_for_position(1) == 25
    assert points_for_position(5) == 10
    assert points_for_position(10) == 1
    assert points_for_position(11) == 0


def test_typical_stint_life_orders_by_compound_hardness() -> None:
    assert TYPICAL_STINT_LAPS["SOFT"] < TYPICAL_STINT_LAPS["MEDIUM"] < TYPICAL_STINT_LAPS["HARD"]


@pytest.mark.asyncio
async def test_cold_tyre_penalty_only_hits_fresh_stints(stack) -> None:
    """A fresh-fit stint carries an out-lap penalty; continuing one does not."""
    store, database, strategy, _, _, _ = stack
    await store.update(track_id=10, tyre={"compound": "MEDIUM", "age_laps": 0, "wear": [0, 0, 0, 0]})
    state = await store.snapshot_analysis()
    fresh = strategy._simulate_stint(state, "MEDIUM", 10, 0, 0.0, 90.0, {}, 1.0)
    # A stint continuing on already-warm tyres (non-zero starting age) pays no
    # out-lap premium, so its early laps rise only with age, not a cold jump.
    warm = strategy._simulate_stint(state, "MEDIUM", 10, 10, 0.0, 90.0, {}, 1.0)
    # The fresh stint's out-lap carries the cold penalty over its second lap.
    assert fresh["lap_times_s"][0] - fresh["lap_times_s"][1] > 0.4
    # The continuing stint shows no such out-lap spike.
    assert warm["lap_times_s"][0] - warm["lap_times_s"][1] < 0.2


@pytest.mark.asyncio
async def test_pace_mode_recommends_saving_when_fuel_short(stack) -> None:
    store, _, _, _, _, tools = stack
    await store.update(fuel_laps_delta=-1.5, total_laps=40, current_lap=10)
    short = await tools.get_pace_mode_options()
    assert short["recommended_mode"] == "fuel_save"
    assert short["estimated_cost_s_per_lap"] is not None
    await store.update(fuel_laps_delta=1.2)
    assert (await tools.get_pace_mode_options())["recommended_mode"] == "push"


@pytest.mark.asyncio
async def test_rival_prediction_flags_behind_car_on_dying_tyres(stack) -> None:
    store, _, _, _, _, tools = stack
    store.state.player_car_index = 0
    store.state.drivers[0] = _driver(0, "You", 4, 0.0, "MEDIUM", 8)
    store.state.drivers[1] = _driver(1, "Behind", 5, -1.8, "SOFT", 15)
    store.state.drivers[2] = _driver(2, "Ahead", 3, 2.5, "HARD", 5)
    await store.update(player_position=4, current_lap=10, total_laps=40)
    result = await tools.predict_rival_strategy(top_n=5)
    by_name = {r["driver"]: r for r in result["rivals"]}
    assert by_name["Behind"]["undercut_threat"] is True
    assert by_name["Behind"]["laps_until_estimated_stop"] <= 2
    # A car well ahead on fresh hards is no undercut threat.
    assert by_name["Ahead"]["undercut_threat"] is False


@pytest.mark.asyncio
async def test_championship_scenario_scores_plans_by_projected_points(stack) -> None:
    store, _, _, _, _, tools = stack
    await store.update(
        player_position=4,
        strategy={
            "plans": [
                {"instruction": "Stay out", "stops_remaining": 1, "projected_rejoin_position": 4, "projected_time_s": 3600},
                {"instruction": "Aggressive", "stops_remaining": 2, "projected_rejoin_position": 3, "projected_time_s": 3605},
            ]
        },
    )
    scenario = await tools.get_championship_scenario()
    assert scenario["current_points_if_held"] == 12  # P4
    assert scenario["best_projected_points"] == 15  # P3
    plans = {p["instruction"]: p for p in scenario["plans"]}
    assert plans["Aggressive"]["projected_points"] == 15


@pytest.mark.asyncio
async def test_strategy_depth_tools_registered(stack) -> None:
    _, _, _, _, _, tools = stack
    names = {schema["name"] for schema in tools.schemas()}
    for expected in ("get_pace_mode_options", "predict_rival_strategy", "get_championship_scenario"):
        assert expected in names


# ==========================================================================
# Batch 3 — proactive & real-time
# ==========================================================================


class _Brain:
    def __init__(self, database) -> None:
        self.database = database

    async def proactive(self, event) -> str:
        return "ok"


def _proactive(stack) -> ProactiveEngineer:
    store, database, strategy, setup, _, _ = stack
    voice = types.SimpleNamespace(is_busy=False)
    return ProactiveEngineer(store, _Brain(database), voice, setup, strategy)


def _pending_types(engine: ProactiveEngineer) -> list[str]:
    return [event["type"] for event in engine.pending]


# --- live delta backend (item 1) --------------------------------------------


@pytest.mark.asyncio
async def test_live_delta_reference_interpolates_time_into_lap() -> None:
    store = StateStore()
    # Absolute session time offset; time-into-lap runs 0..90 over 0..1000 m.
    trace = [{"d": i * 100.0, "t": 1000.0 + i * 9.0} for i in range(11)]
    await store.set_delta_reference(trace, "PB 1:30.000")
    assert store.state.live_delta_reference == "PB 1:30.000"
    assert store.reference_time_at(450) == pytest.approx(40.5)
    assert store.reference_time_at(0) == pytest.approx(0.0)
    assert store.reference_time_at(1000) == pytest.approx(90.0)


@pytest.mark.asyncio
async def test_live_delta_reference_clears_and_drops_non_monotonic() -> None:
    store = StateStore()
    await store.set_delta_reference([], "")
    assert store.reference_time_at(100) is None
    # A distance that goes backwards is dropped so interpolation stays monotonic.
    trace = [{"d": 0.0, "t": 0.0}, {"d": 500.0, "t": 45.0}, {"d": 300.0, "t": 60.0}]
    await store.set_delta_reference(trace, "ref")
    assert store.reference_time_at(400) == pytest.approx(36.0)


# --- proactive detectors ----------------------------------------------------


@pytest.mark.asyncio
async def test_race_start_fires_once_on_green(stack) -> None:
    store, *_ = stack
    engine = _proactive(stack)
    await store.update(
        mode_profile="race", race_control_phase="green", current_lap=1,
        grid_position=3, ers_pct=100,
        tyre={"inner_temps_c": [90, 91, 88, 89], "wear": [0, 0, 0, 0], "compound": "MEDIUM"},
    )
    engine._detect_race_start(await store.snapshot_analysis())
    engine._detect_race_start(await store.snapshot_analysis())
    assert _pending_types(engine).count("race_start") == 1


@pytest.mark.asyncio
async def test_race_start_skipped_in_qualifying(stack) -> None:
    store, *_ = stack
    engine = _proactive(stack)
    await store.update(mode_profile="qualifying", race_control_phase="green", current_lap=1)
    engine._detect_race_start(await store.snapshot_analysis())
    assert "race_start" not in _pending_types(engine)


@pytest.mark.asyncio
async def test_component_wear_escalates_by_band(stack) -> None:
    store, *_ = stack
    engine = _proactive(stack)
    await store.update(component_wear={"ice": 72.0, "mguk": 40.0})
    engine._detect_component_wear(await store.snapshot_analysis())
    assert "component_wear" in _pending_types(engine)
    assert engine._component_alert_pct == 70
    # Same 70s band produces no new alert (coalescing keeps one pending anyway,
    # so assert the tracked band did not advance).
    engine.pending.clear()
    await store.update(component_wear={"ice": 75.0})
    engine._detect_component_wear(await store.snapshot_analysis())
    assert "component_wear" not in _pending_types(engine)
    assert engine._component_alert_pct == 70
    # Crossing into the 80s band escalates. The per-type cooldown (120s) would
    # normally suppress an alert seconds after the last one, so clear it to prove
    # the band crossing itself produces a fresh call.
    engine.pending.clear()
    engine._cooldowns.pop("component_wear", None)
    await store.update(component_wear={"ice": 81.0})
    engine._detect_component_wear(await store.snapshot_analysis())
    assert engine._component_alert_pct == 80
    assert "component_wear" in _pending_types(engine)


@pytest.mark.asyncio
async def test_energy_low_fires_when_attacking_on_empty_battery(stack) -> None:
    store, *_ = stack
    engine = _proactive(stack)
    await store.update(mode_profile="race", ers_pct=10, overtake_available=True, current_lap=5)
    engine._detect_energy(await store.snapshot_analysis())
    assert "energy_low" in _pending_types(engine)
    # Healthy battery does not warn.
    engine.pending.clear()
    await store.update(ers_pct=60, current_lap=6)
    engine._detect_energy(await store.snapshot_analysis())
    assert "energy_low" not in _pending_types(engine)


@pytest.mark.asyncio
async def test_rival_pace_flags_closing_car_behind(stack) -> None:
    store, *_ = stack
    engine = _proactive(stack)
    store.state.player_car_index = 0
    chaser = _driver(1, "Chaser", 5, -2.5, "SOFT", 6)
    store.state.drivers[1] = chaser
    await store.update(mode_profile="race")
    # Seed a prior sample 9s ago with a larger gap so the car is closing.
    engine._rival_gap_history[1] = (-3.0, _time.monotonic() - 9.0)
    engine._detect_rival_pace(await store.snapshot_analysis())
    assert "rival_pace" in _pending_types(engine)


@pytest.mark.asyncio
async def test_sc_restart_fires_on_transition_to_ending_phase(stack) -> None:
    store, *_ = stack
    engine = _proactive(stack)
    engine._last_race_control_phase = "safety_car"
    await store.update(
        connected=True, proactive={"enabled": True, "cadence_laps": 2},
        race_control_phase="safety_car_ending", safety_car="full",
        player_position=3, ers_pct=70,
        tyre={"inner_temps_c": [90, 91, 88, 89], "wear": [10, 10, 10, 10], "compound": "MEDIUM"},
    )
    await engine._detect(await store.snapshot_analysis())
    assert "sc_restart" in _pending_types(engine)


# --- energy plan tool + registration ----------------------------------------


@pytest.mark.asyncio
async def test_energy_plan_tool_recommends_harvest_on_empty(stack) -> None:
    store, _, _, _, _, tools = stack
    await store.update(ers_pct=10, overtake_available=True, regulations_2026=True)
    plan = await tools.get_energy_plan()
    assert plan["recommended_mode"] == "harvest"
    assert plan["overtaking_aid"] == "Manual Override"
    assert plan["attack_window_open"] is False


@pytest.mark.asyncio
async def test_batch3_fallback_text_is_specific(stack) -> None:
    payloads = {
        "race_start": {"grid_position": 3, "clean_side": True},
        "sc_restart": {},
        "energy_low": {"ers_pct": 10, "regulations_2026": True},
        "component_wear": {"component": "ice", "wear_pct": 72},
        "rival_pace": {"driver": "Chaser", "gap_to_player_s": -2.5},
    }
    for kind, payload in payloads.items():
        text = ProactiveEngineer._fallback_text({"type": kind, "payload": payload}, {})
        assert text and "dashboard" not in text, kind


@pytest.mark.asyncio
async def test_batch3_energy_tool_registered(stack) -> None:
    _, _, _, _, _, tools = stack
    names = {schema["name"] for schema in tools.schemas()}
    assert "get_energy_plan" in names
