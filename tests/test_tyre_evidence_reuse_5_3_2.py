"""Practice evidence survives compound changes without inventing confidence."""

from __future__ import annotations

import pytest

from pitwall.analysis import AnalysisEngine
from pitwall.config import settings
from pitwall.strategy import StrategyEngine, infer_unrun_compounds
from pitwall.tyre_learning import stint_pace_model


def practice_lap(age, *, compound="MEDIUM", session=101, number=None, **updates):
    fuel = 50 - age
    wear = 2.0 if compound == "MEDIUM" else 1.5
    item = {
        "session_uid": session,
        "track_id": 42,
        "track_name": "Madrid",
        "session_type": "Practice 1",
        "mode_profile": "practice",
        "lap_num": number if number is not None else age,
        "lap_time_ms": round((94 + age * 0.1 + fuel * 0.03) * 1000),
        "valid": True,
        "compound": compound,
        "tyre_age_start": age - 1,
        "tyre_age_end": age,
        "fuel_start_kg": fuel + 0.5,
        "fuel_end_kg": fuel - 0.5,
        "wear_start": [(age - 1) * wear] * 4,
        "wear_end": [age * wear] * 4,
        "weather": "Clear",
        "setup": {},
        "created_at": session * 100 + age,
    }
    item.update(updates)
    return item


def state_with(laps):
    state = {
        "session_uid": 101,
        "track_id": 42,
        "session_type": "Race",
        "mode_profile": "race",
        "current_lap": 25,
        "total_laps": 40,
        "player_position": 4,
        "tyre": {"compound": "HARD", "age_laps": 15, "wear": [23] * 4},
        "completed_laps": laps,
    }
    state["analysis"] = {"deg_model": AnalysisEngine.compute_degradation(state)}
    return state


def test_long_hard_run_retains_previous_medium_wear_and_pace():
    medium = [practice_lap(age) for age in range(2, 12)]
    hard = [practice_lap(age, compound="HARD", number=age + 20) for age in range(2, 16)]
    state = state_with(medium + hard)
    engine = StrategyEngine(None, None)

    # Historical lookup excludes this same session, so all usable samples
    # must come from the retained live laps even after 14 laps on hards.
    assert engine._live_wear_samples(state, "MEDIUM") == [2.0] * 10
    assert engine._live_wear_samples(state, "HARD") == [1.5] * 12
    rates, source, count, _ = engine._wheel_wear_rates(state, "MEDIUM", {}, 1.0)
    assert rates == [2.0] * 4
    assert (source, count) == ("live_per_wheel_wear", 10)
    evidence = engine._compound_evidence(state, {}, 1.0, ["HARD", "MEDIUM"])
    assert evidence["MEDIUM"]["wear_sample_size"] == 10
    assert evidence["MEDIUM"]["pace_sample_size"] == 10
    assert evidence["MEDIUM"]["laps_observed"] == 10
    assert evidence["MEDIUM"]["historical_laps_observed"] == 0
    assert evidence["HARD"]["live_laps_observed"] == 14


def test_per_compound_window_keeps_latest_measured_laps_and_ignores_bad_laps():
    laps = [practice_lap(age) for age in range(2, 18)]
    for index, lap in enumerate(laps):
        lap["wear_start"] = [10] * 4
        lap["wear_end"] = [11 + index / 10] * 4
    laps += [practice_lap(20, valid=False), practice_lap(21, wear_start=[], wear_end=[])]
    expected = [1 + index / 10 for index in range(4, 16)]
    state = state_with(laps)
    assert StrategyEngine._live_wear_samples(state, "MEDIUM") == pytest.approx(expected)
    for wheel in StrategyEngine._live_wheel_wear_samples(state, "MEDIUM"):
        assert wheel == pytest.approx(expected)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"fuel_start_kg": 0, "fuel_end_kg": 0}, "missing_or_invalid_fuel"),
        ({"tyre_age_end": None}, "missing_tyre_age"),
        ({"weather": "Light rain"}, "unresolved_wet_conditions"),
        ({"learning_exclusions": ["traffic"]}, "traffic"),
    ],
)
def test_unavailable_pace_explains_recorded_lap_rejection(changes, reason):
    model = stint_pace_model([practice_lap(age, **changes) for age in range(2, 10)])
    assert model["laps_observed"] == 8
    assert model["sample_size"] == 0
    assert model["slope_s_per_lap"] is None
    assert model["excluded_laps"] == {reason: 8}


def test_short_warm_run_explains_why_laps_do_not_yet_establish_a_slope():
    model = stint_pace_model([practice_lap(age) for age in range(1, 4)])
    assert model["sample_size"] == 0
    assert model["excluded_laps"] == {"warmup_lap": 1, "insufficient_stint_span": 2}


def test_rejected_fit_keeps_explicit_diagnostic_and_does_not_raise_confidence():
    laps = [practice_lap(age, lap_time_ms=90000 + age * 3000) for age in range(2, 10)]
    model = stint_pace_model(laps)
    assert model["sample_size"] == 0
    assert model["excluded_laps"] == {"implausible_pace_slope": 8}
    evidence = StrategyEngine(None, None)._compound_evidence(state_with(laps), {}, 1.0, ["MEDIUM"])
    assert evidence["MEDIUM"]["laps_observed"] == 8
    assert evidence["MEDIUM"]["wear_sample_size"] == 8
    assert evidence["MEDIUM"]["pace_sample_size"] == 0
    assert evidence["MEDIUM"]["pace_source"] == "track_default"


def test_live_missing_wear_is_explained_without_discarding_pace():
    laps = [practice_lap(age, wear_start=[], wear_end=[]) for age in range(2, 10)]
    evidence = StrategyEngine(None, None)._compound_evidence(state_with(laps), {}, 1.0, ["MEDIUM"])
    assert evidence["MEDIUM"]["laps_observed"] == 8
    assert evidence["MEDIUM"]["pace_sample_size"] == 8
    assert evidence["MEDIUM"]["wear_sample_size"] == 0
    assert evidence["MEDIUM"]["wear_excluded_laps"] == {"missing_or_invalid_wear_increment": 8}


def test_held_alternative_keeps_its_unmeasured_compound_evidence(monkeypatch):
    monkeypatch.setattr(settings, "strategy_change_min_gain_s", 100000)
    state = state_with([])
    state.update(current_lap=4, total_laps=12, weather="Clear")
    state["tyre"] = {"compound": "MEDIUM", "age_laps": 3, "wear": [8] * 4}
    engine = StrategyEngine(None, None)
    candidate = engine.compute(state, {})
    assert candidate["recommended"]["compounds"] == ["MEDIUM", "SOFT"]
    alternative = next(plan for plan in engine._candidate_pool
                       if plan.get("compounds") == ["MEDIUM", "HARD"]
                       and plan.get("feasible") and plan.get("legal"))
    previous = {"recommended": {
        **alternative,
        "box_lap": alternative["box_laps"][0],
        "fit_compound": "HARD",
        "committed_at_lap": 4,
        "source_compound": "MEDIUM",
        "neutralisation_phase": "green",
        "instruction": "Keep the hard-tyre plan.",
    }}
    held = engine._stabilize_radio_plan(state, previous, candidate)
    assert held["stability"]["held"] is True
    assert held["recommended"]["compounds"] == ["MEDIUM", "HARD"]
    evidence = held["model_summary"]["compounds"]["HARD"]
    assert evidence["wear_sample_size"] == 0
    assert evidence["pace_sample_size"] == 0
    assert evidence["laps_observed"] == 0
    assert evidence["pace_source"] == "track_default"
    assert held["confidence"] == "low"
    assert len(held["model_summary"]["compounds"]) <= 5


@pytest.mark.asyncio
async def test_saved_medium_and_hard_practice_teach_new_race_but_not_soft_certainty(stack):
    store, db, engine, *_ = stack
    for compound, session in (("MEDIUM", 101), ("HARD", 102)):
        for age in range(2, 10):
            await db.save_lap(practice_lap(age, compound=compound, session=session), [])
    await store.update(session_uid=103, track_id=42, session_type="Race", mode_profile="race")
    state = await store.snapshot_analysis()
    history = infer_unrun_compounds(await db.tyre_history_model(42, context=state))
    evidence = engine._compound_evidence(state, history, 1.0, ["MEDIUM", "HARD", "SOFT"])
    for compound in ("MEDIUM", "HARD"):
        assert evidence[compound]["historical_laps_recorded"] == 8
        assert evidence[compound]["historical_laps_observed"] == 8
        assert evidence[compound]["wear_sample_size"] == 8
        assert evidence[compound]["pace_sample_size"] == 8
        assert evidence[compound]["deg_s_per_lap"] == pytest.approx(0.1)
    assert evidence["SOFT"]["wear_sample_size"] == 0
    assert evidence["SOFT"]["pace_sample_size"] == 0
    assert evidence["SOFT"]["laps_observed"] == 0
    assert evidence["SOFT"]["inferred_from"]
    # Confidence still requires observed wear and pace for every plan stint.
    samples, confidence = engine._plan_confidence({"stint_models": [
        {"laps": 10, "wear_sample_size": 8, "deg_sample_size": 8},
        {"laps": 10, "wear_sample_size": 0, "deg_sample_size": 0},
    ]})
    assert (samples, confidence) == (0, "low")


@pytest.mark.asyncio
async def test_recorded_qualifying_laps_are_visible_but_cannot_teach_practice_model(stack):
    _, db, *_ = stack
    for age in range(2, 10):
        await db.save_lap(practice_lap(age, compound="HARD", mode_profile="qualifying", session_type="Qualifying"), [])
    history = await db.tyre_history_model(42)
    assert history["recorded_laps_by_compound"] == {"HARD": 8}
    assert history["compounds"] == {}
    evidence = StrategyEngine(None, None)._compound_evidence(state_with([]), history, 1.0, ["HARD"])
    assert evidence["HARD"]["historical_laps_recorded"] == 8
    assert evidence["HARD"]["laps_observed"] == 0
    assert evidence["HARD"]["pace_sample_size"] == 0


@pytest.mark.asyncio
async def test_wear_only_history_explains_missing_fuel_instead_of_no_recorded_data(stack):
    _, db, *_ = stack
    for age in range(2, 10):
        await db.save_lap(practice_lap(age, fuel_start_kg=0, fuel_end_kg=0), [])
    history = await db.tyre_history_model(42)
    model = history["compounds"]["MEDIUM"]
    assert model["laps_observed"] == 8
    assert model["wear_sample_size"] == 8
    assert model["sample_size"] == 0
    assert model["pace_excluded_laps"] == {"missing_or_invalid_fuel": 8}
    assert "do not establish matching car formula" in history["compatibility_basis"]
