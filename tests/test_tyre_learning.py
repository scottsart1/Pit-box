"""Regression scenarios for personal race strategy evidence, not LLM output."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from pitwall.analysis import AnalysisEngine
from pitwall.strategy import StrategyEngine
from pitwall.tyre_learning import stint_pace_model, wear_deltas


def lap(
    age=4,
    *,
    number=None,
    session=11,
    compound="MEDIUM",
    offset=0.0,
    slope=0.20,
    wear=3.0,
    **changes,
):
    fuel = 55 - age * 2
    result = {
        "session_uid": session,
        "track_id": 7,
        "track_name": "Montreal",
        "session_type": "Practice",
        "mode_profile": "practice",
        "compound": compound,
        "lap_num": number if number is not None else age,
        "valid": True,
        "lap_time_ms": round((90 + offset + slope * age + fuel * 0.030) * 1000),
        "tyre_age_start": age - 1,
        "tyre_age_end": age,
        "fuel_start_kg": fuel + 1,
        "fuel_end_kg": fuel - 1,
        "wear_start": [(age - 1) * wear] * 4,
        "wear_end": [age * wear] * 4,
        "track_temp_c": 30,
        "air_temp_c": 22,
        "weather": "Clear",
        "setup": {},
        "trace": [],
        "created_at": session * 100 + (number or age),
    }
    result.update(changes)
    return result


def race_state(laps=()):
    return {
        "mode_profile": "race",
        "session_type": "Race",
        "track_id": 7,
        "current_lap": 10,
        "total_laps": 25,
        "player_position": 5,
        "tyre": {"compound": "MEDIUM", "age_laps": 9, "wear": [25] * 4},
        "car_setup": {},
        "completed_laps": list(laps),
    }


def history(wear=3.0, samples=20):
    return {
        "compounds": {
            "MEDIUM": {
                "sample_size": samples,
                "wear_sample_size": samples,
                "slope_s_per_lap": 0.20,
                "max_wear_per_lap_pct": wear,
                "wheel_wear_per_lap_pct": [wear] * 4,
            }
        }
    }


def test_fuel_burn_does_not_hide_degradation():
    # The observed lap slope is .14; true degradation is .20 with 2kg/lap burn.
    fit = stint_pace_model([lap(age) for age in range(2, 12)])
    assert fit["slope_s_per_lap"] == pytest.approx(0.20, abs=0.001)
    assert fit["sample_size"] == 10
    assert fit["stint_count"] == 1


def test_separate_runs_do_not_create_cross_stint_pace_slopes():
    # A faster second run starts at a different age and fuel load. Its offset
    # must not flatten the slope of the longer first run.
    laps = [lap(age, number=age, offset=4) for age in range(2, 8)]
    laps += [lap(age, number=age + 20, offset=-3) for age in range(8, 14)]
    fit = stint_pace_model(laps)
    assert fit["slope_s_per_lap"] == pytest.approx(0.20, abs=0.001)
    assert fit["stint_count"] == 2
    live = AnalysisEngine.compute_degradation(race_state(laps))
    assert live["compounds"]["MEDIUM"]["slope_s_per_lap"] == fit["slope_s_per_lap"]


def test_pace_can_learn_without_wear_packets_but_requires_fuel():
    laps = [lap(age, wear_start=[], wear_end=[]) for age in range(2, 10)]
    assert stint_pace_model(laps)["slope_s_per_lap"] == pytest.approx(0.2)
    missing_fuel = [{**item, "fuel_start_kg": 0, "fuel_end_kg": 0} for item in laps]
    assert stint_pace_model(missing_fuel)["slope_s_per_lap"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"mode_profile": "time_trial"},
        {"mode_profile": "qualifying"},
        {"session_type": "Time Trial", "mode_profile": ""},
        {"pit_status": 1},
        {"pit_lane_time_ms": 100},
        {"learning_exclusions": ["neutralised_lap"]},
        {"safety_car": "virtual"},
        {"valid": False},
        {"tyre_age_end": 0},
        {"wear_end": [0] * 4},
        {"wear_end": [float("nan")] * 4},
        {"wear_end": [101] * 4},
        {"wear_end": [9] * 4},  # unchanged telemetry
    ],
)
def test_unusable_laps_cannot_teach_zero_or_corrupt_wear(changes):
    assert wear_deltas(lap(**changes)) is None


def test_unknown_live_pace_is_unavailable_instead_of_zero():
    assert (
        AnalysisEngine.compute_degradation(race_state())["current_slope_s_per_lap"]
        is None
    )


def test_single_live_sample_cannot_replace_established_history():
    engine = StrategyEngine.__new__(StrategyEngine)
    state = race_state([lap(wear=0.1)])
    assert engine._wear_rate(state, "MEDIUM", history(), 1.0)[0] == 3.0
    rates, _, _, _ = engine._wheel_wear_rates(state, "MEDIUM", history(), 1.0)
    assert rates == [3.0] * 4


def test_live_learning_blends_then_adapts_to_persistent_change():
    engine = StrategyEngine.__new__(StrategyEngine)
    partial = race_state([lap(age, wear=5) for age in range(2, 5)])
    full = race_state([lap(age, wear=5) for age in range(2, 8)])
    assert 3.0 < engine._wear_rate(partial, "MEDIUM", history(), 1.0)[0] < 5.0
    assert engine._wear_rate(full, "MEDIUM", history(), 1.0)[0] == 5.0
    assert engine._wheel_wear_rates(full, "MEDIUM", history(), 1.0)[0] == [5.0] * 4


def test_condition_and_feedback_reach_per_wheel_simulation_once():
    engine = StrategyEngine.__new__(StrategyEngine)
    prior = history()
    prior["compounds"]["MEDIUM"]["condition_adjusted_wear_per_lap_pct"] = 4.0
    state = race_state()
    state["driver_tyre_feedback"] = {
        "category": "tyres_gone",
        "lap": 10,
        "confidence": 1.0,
    }
    assert engine._wear_rate(state, "MEDIUM", prior, 1.0)[0] == 5.0
    assert engine._wheel_wear_rates(state, "MEDIUM", prior, 1.0)[0] == [5.0] * 4


def test_inferred_compound_does_not_double_count_driver_style():
    prior = {
        "compounds": {
            "HARD": {"inferred_wear_per_lap_pct": 4.0, "inferred_from": ["MEDIUM"]}
        }
    }
    assert StrategyEngine._wear_rate(race_state(), "HARD", prior, 2.0)[0] == 4.0


@pytest.mark.asyncio
async def test_history_recovers_true_pace_and_ignores_newer_time_trials(stack):
    _, db, *_ = stack
    for age in range(2, 12):
        await db.save_lap(lap(age), [])
    for age in range(2, 30):
        await db.save_lap(
            lap(
                age,
                session=99,
                mode_profile="time_trial",
                session_type="Time Trial",
                wear=0,
            ),
            [],
        )
    result = await db.tyre_history_model(7, limit=20)
    model = result["compounds"]["MEDIUM"]
    assert model["slope_s_per_lap"] == pytest.approx(0.20, abs=0.001)
    assert model["wear_sample_size"] == 10
    assert model["max_wear_per_lap_pct"] == 3.0
    assert model["stint_count"] == 1
    # No double counting the just-saved live session as historical evidence.
    assert (await db.tyre_history_model(7, context={"session_uid": 11}))[
        "compounds"
    ] == {}


@pytest.mark.asyncio
async def test_neutralisation_and_pit_flags_survive_green_finish_and_storage(stack):
    store, db, *_ = stack
    await store.update(
        session_uid=51,
        track_id=7,
        track_name="Montreal",
        mode_profile="race",
        session_type="Race",
    )
    await store.transition_lap(4, 90000, False, 5, 0, 0)
    await store.update(safety_car="virtual")
    await store.update(safety_car="none")
    await store.update(pit_status=1)
    await store.update(pit_status=0)
    await store.add_trace_point({"t": 10.0, "d": 10.0, "speed": 100})
    completed = await store.transition_lap(5, 90000, False, 5, 0, 0)
    assert set(completed["learning_exclusions"]) == {"neutralised_lap", "pit_lap"}
    await db.save_lap(completed, [])
    result = await db.tyre_history_model(7)
    assert result["compounds"] == {}
    with db._connect() as conn:
        reasons = json.loads(
            conn.execute("SELECT learning_exclusions_json FROM laps").fetchone()[0]
        )
    assert set(reasons) == {"neutralised_lap", "pit_lap"}


@pytest.mark.asyncio
async def test_clean_history_is_reused_after_reopening_database(stack):
    from pitwall.database import PitWallDatabase

    _, db, *_ = stack
    for age in range(2, 10):
        await db.save_lap(lap(age), [])
    reopened = PitWallDatabase(db.path)
    await reopened.initialize()
    model = (await reopened.tyre_history_model(7))["compounds"]["MEDIUM"]
    assert model["slope_s_per_lap"] == pytest.approx(0.2)
    assert model["wear_sample_size"] == 8


def test_unmeasured_final_compound_cannot_inherit_high_confidence():
    engine = StrategyEngine.__new__(StrategyEngine)
    state = race_state()
    state["tyre_sets"] = [
        {"compound": "HARD", "available": True, "usable_life_laps": 40}
    ]
    result = engine.compute(state, history())
    assert result["available"]
    assert result["recommended"]["fit_compound"] == "HARD"
    assert result["confidence"] == "low"


@pytest.mark.skipif(
    shutil.which("node") is None,
    reason="Node is needed for the desktop JavaScript check",
)
def test_strategy_board_updates_when_only_estimates_change():
    js = Path(__file__).parents[1] / "static/js/strategy.js"
    # Execute the shipped module, rather than matching strings in its source.
    code = f"""import fs from 'node:fs';
      const m = await import('data:text/javascript;base64,' + fs.readFileSync({json.dumps(str(js))}).toString('base64'));
      const original = {{stops_remaining:1,compounds:['MEDIUM','HARD'],box_laps:[12],feasible:true,projected_finish_position:5,projected_max_wear_pct:60,monte_carlo:{{p75_s:1500}}}};
      for (const delta of [{{projected_max_wear_pct:70}},{{monte_carlo:{{p75_s:1510}}}},{{projected_points:10}}]) {{
        if (m.planKey(original) === m.planKey({{...original,...delta}})) throw new Error('Stale plan');
      }}
      const text = m.describeEvidence({{HARD:{{wear_per_lap_pct:3,deg_s_per_lap:null}}}});
      if (!text.includes('pace still learning') || !text.includes('3.00%')) throw new Error(text);
    """
    subprocess.run(
        ["node", "--input-type=module", "-e", code],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.asyncio
async def test_future_race_does_not_inherit_other_session_state(stack):
    store, db, strategy, *_ = stack
    for age in range(2, 10):
        await db.save_lap(lap(age), [])
    baseline = await strategy.plan_race(track_id=7, total_laps=20, start_compound="MEDIUM")

    def contaminate(s):
        s.track_id = 12
        s.mode_profile = "time_trial"
        s.current_lap = 10
        s.safety_car = "full"
        s.red_flag_active = True
        s.rain_next_15_pct = 100
        s.completed_laps = [lap(compound="INTER")]
        s.analysis = {"deg_model": {"compounds": {"MEDIUM": {
            "slope_s_per_lap": 1.4, "sample_size": 50,
        }}}}
        s.tyre_sets = [{"compound": "SOFT", "available": True, "wear_pct": 80}]

    await store.mutate(contaminate)
    planned = await strategy.plan_race(track_id=7, total_laps=20, start_compound="MEDIUM")
    assert planned["available"]
    assert planned["compound_rule"]["wet_waiver"] is False
    assert planned["compound_rule"]["used_compounds"] == ["MEDIUM"]
    assert all(len(set(plan["compounds"])) >= 2 for plan in planned["plans"])
    assert planned["plans"] == baseline["plans"]


@pytest.mark.asyncio
async def test_planner_learns_from_practice_before_session_changes(stack):
    store, db, strategy, *_ = stack
    for age in range(2, 10):
        await db.save_lap(lap(age), [])
    await store.update(session_uid=11, track_id=7, mode_profile="practice")
    planned = await strategy.plan_race(track_id=7, total_laps=20, start_compound="MEDIUM")
    assert planned["tyre_evidence"]["MEDIUM"]["pace_laps_observed"] == 8
    assert planned["tyre_evidence"]["MEDIUM"]["deg_s_per_lap"] == pytest.approx(.2)
