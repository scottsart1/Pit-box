from __future__ import annotations

import pytest

from pitwall.brain import EngineerBrain
from pitwall.state import DriverState


async def _brain(stack):
    store, database, _, _, _, tools = stack
    return store, EngineerBrain(store, tools, database)


@pytest.mark.asyncio
async def test_cars_ahead_uses_measured_gap_trend_not_reversed_lap_math(stack) -> None:
    store, brain = await _brain(stack)

    def apply(state):
        state.connected = True
        state.player_car_index = 0
        state.last_lap_ms = 96_000
        state.drivers[0].name = "Player"
        state.drivers[0].last_lap_ms = 96_000
        rival = state.drivers[1]
        rival.name = "Perez"
        rival.active = True
        rival.position = 14
        rival.gap_to_player_s = -5.7
        rival.last_lap_ms = 95_800
        rival.gap_history = [
            {"session_time_s": 100.0, "gap_s": -6.0},
            {"session_time_s": 110.0, "gap_s": -5.7},
        ]
    await store.mutate(apply)

    answer = await brain._fast_answer("am I closing in to the cars in front")
    assert answer is not None
    assert "closing on Perez" in answer
    assert "gained 0.3" in answer
    assert "Box" not in answer


@pytest.mark.asyncio
async def test_cars_ahead_does_not_claim_closing_from_one_slower_lap(stack) -> None:
    store, brain = await _brain(stack)

    def apply(state):
        state.connected = True
        state.player_car_index = 0
        state.last_lap_ms = 96_000
        state.drivers[0].last_lap_ms = 96_000
        rival = state.drivers[1]
        rival.name = "Perez"
        rival.active = True
        rival.position = 14
        rival.gap_to_player_s = -5.7
        rival.last_lap_ms = 95_800
    await store.mutate(apply)

    answer = await brain._fast_answer("am I closing in to the cars in front")
    assert answer is not None
    assert "slower" in answer
    assert "You are closing" not in answer


@pytest.mark.asyncio
async def test_named_driver_history_never_appends_strategy(stack) -> None:
    store, brain = await _brain(stack)

    def apply(state):
        state.connected = True
        state.strategy = {"recommended": {"box_lap": 17, "fit_compound": "MEDIUM", "stops_remaining": 1}}
        for idx, name, lap in ((1, "Perez", 95_925), (2, "Bottas", 96_100)):
            state.drivers[idx].name = name
            state.drivers[idx].active = True
            state.drivers[idx].position = idx + 10
            state.drivers[idx].last_lap_ms = lap
    await store.mutate(apply)

    answer = await brain._fast_answer("give me an update on the last five laps by Perez and Bottas")
    assert answer is not None
    assert "Perez" in answer and "Bottas" in answer
    assert "history unavailable" in answer
    assert "box" not in answer.lower()


@pytest.mark.asyncio
async def test_overtaking_aid_temperature_has_no_invented_target(stack) -> None:
    store, brain = await _brain(stack)
    await store.update(connected=True)
    answer = await brain._fast_answer("what should be my target tire temperature to optimize DRS")
    assert answer is not None
    assert "no overtaking-aid tyre-temperature target" in answer.lower()
    assert "98" not in answer and "105" not in answer


@pytest.mark.asyncio
async def test_short_answers_and_temperature_unit_persist(stack) -> None:
    store, brain = await _brain(stack)
    await store.update(
        connected=True,
        tyre={
            "compound": "HARD",
            "age_laps": 5,
            "wear": [20, 21, 30, 33],
            "inner_temps_c": [92, 100, 107, 111],
        },
    )
    assert await brain._fast_answer("could you please keep your answers short") == "Short answers confirmed."
    await store.update(temperature_unit="f")
    answer = await brain._fast_answer("give me tyre temperatures in Fahrenheit")
    state = await store.snapshot_analysis()
    assert state["radio_verbosity"] == "terse"
    assert answer == "Tyres: front 198/212 F; rear 225/232 F."


@pytest.mark.asyncio
async def test_strategy_no_change_keeps_instruction_in_one_sentence(stack) -> None:
    store, brain = await _brain(stack)
    recommended = {"box_lap": 17, "fit_compound": "MEDIUM", "stops_remaining": 1}
    await store.update(
        connected=True,
        current_lap=14,
        radio_verbosity="terse",
        strategy={"recommended": recommended},
        strategy_spoken_signature="17:MEDIUM:1",
    )
    answer = await brain._fast_answer("any strategy updates")
    assert answer == "No change — Box lap 17 for mediums."


@pytest.mark.asyncio
async def test_rear_slide_control_advice_is_adjustable_and_directionally_correct(stack) -> None:
    store, brain = await _brain(stack)
    await store.update(connected=True, car_setup={"on_throttle": 50, "brake_bias": 54})
    answer = await brain._fast_answer("what can I do to differential or brake bias for rear sliding")
    assert answer is not None
    assert "50 to 47" in answer
    assert "one point forward" in answer
    assert "garage" not in answer.lower()


@pytest.mark.asyncio
async def test_strategy_stability_holds_small_plan_changes(stack) -> None:
    store, _, strategy, *_ = stack
    await store.update(
        current_lap=14,
        tyre={
            "compound": "HARD",
            "age_laps": 12,
            "wear": [35, 36, 40, 41],
            "inner_temps_c": [92, 100, 106, 110],
        },
    )
    state = await store.snapshot_analysis()
    previous = {
        "neutralisation": {"phase": "green"},
        "recommended": {
            "box_lap": 17,
            "fit_compound": "MEDIUM",
            "stops_remaining": 1,
            "risk_adjusted_time_s": 1801.0,
            "instruction": "Box lap 17 for MEDIUM.",
            "committed_at_lap": 13,
            "source_compound": "HARD",
            "neutralisation_phase": "green",
        },
    }
    candidate = {
        "neutralisation": {"phase": "green"},
        "recommended": {
            "box_lap": 21,
            "fit_compound": "SOFT",
            "stops_remaining": 1,
            "risk_adjusted_time_s": 1800.2,
            "instruction": "Box lap 21 for SOFT.",
        },
        "plans": [
            {
                "box_laps": [21], "compounds": ["HARD", "SOFT"],
                "stops_remaining": 1, "risk_adjusted_time_s": 1800.2,
            },
            {
                "box_laps": [17], "compounds": ["HARD", "MEDIUM"],
                "stops_remaining": 1, "risk_adjusted_time_s": 1801.0,
            },
        ],
    }
    result = strategy._stabilize_radio_plan(state, previous, candidate)
    assert result["stability"]["held"] is True
    assert result["recommended"]["box_lap"] == 17
    assert result["recommended"]["fit_compound"] == "MEDIUM"


@pytest.mark.asyncio
async def test_strategy_stability_accepts_material_gain_after_hold(stack) -> None:
    store, _, strategy, *_ = stack
    await store.update(
        current_lap=16,
        tyre={
            "compound": "HARD",
            "age_laps": 14,
            "wear": [40, 42, 46, 48],
            "inner_temps_c": [92, 100, 106, 110],
        },
    )
    state = await store.snapshot_analysis()
    previous = {
        "neutralisation": {"phase": "green"},
        "recommended": {
            "box_lap": 19, "fit_compound": "MEDIUM", "stops_remaining": 1,
            "risk_adjusted_time_s": 1810.0, "instruction": "Box lap 19 for MEDIUM.",
            "committed_at_lap": 13, "source_compound": "HARD",
            "neutralisation_phase": "green",
        },
    }
    candidate = {
        "neutralisation": {"phase": "green"},
        "recommended": {
            "box_lap": 17, "fit_compound": "MEDIUM", "stops_remaining": 1,
            "risk_adjusted_time_s": 1805.0, "instruction": "Box lap 17 for MEDIUM.",
        },
        "plans": [
            {"box_laps": [17], "compounds": ["HARD", "MEDIUM"], "stops_remaining": 1, "risk_adjusted_time_s": 1805.0},
            {"box_laps": [19], "compounds": ["HARD", "MEDIUM"], "stops_remaining": 1, "risk_adjusted_time_s": 1810.0},
        ],
    }
    result = strategy._stabilize_radio_plan(state, previous, candidate)
    assert result["stability"]["held"] is False
    assert result["recommended"]["box_lap"] == 17

@pytest.mark.asyncio
async def test_verstappen_sector_question_cannot_route_to_ers(stack) -> None:
    store, brain = await _brain(stack)

    def apply(state):
        state.connected = True
        state.player_car_index = 0
        state.drivers[0].name = "Player"
        state.drivers[0].lap_history = [{
            "lap_num": 4, "lap_ms": 96_000, "s1_ms": 31_000,
            "s2_ms": 33_000, "s3_ms": 32_000, "valid_flags": 1,
        }]
        rival = state.drivers[1]
        rival.name = "Verstappen"
        rival.active = True
        rival.position = 11
        rival.lap_history = [{
            "lap_num": 4, "lap_ms": 94_685, "s1_ms": 30_619,
            "s2_ms": 32_619, "s3_ms": 31_447, "valid_flags": 1,
        }]
    await store.mutate(apply)

    answer = await brain._fast_answer("where am I losing time in comparison to Verstappen ahead")
    assert answer is not None
    assert "Battery" not in answer and "ERS" not in answer
    assert "S3 0.553" in answer
    assert "biggest loss S3" in answer


@pytest.mark.asyncio
async def test_driver_can_override_session_before_udp_connects(stack) -> None:
    store, brain = await _brain(stack)
    answer = await brain._fast_answer("This is the race, tell me that")
    state = await store.snapshot_analysis()
    assert answer == "Session locked to Race. I’ll use race logic until you clear it."
    assert state["session_mode_override"] == "race"
    assert state["mode_profile"] == "race"


@pytest.mark.asyncio
async def test_driver_strategy_plan_becomes_locked_constraint(stack) -> None:
    store, brain = await _brain(stack)
    answer = await brain._fast_answer(
        "I'm starting on mediums, with the expectation of lap 12 change to hards"
    )
    state = await store.snapshot_analysis()
    override = state["strategy_override"]
    assert override["enabled"] is True and override["locked"] is True
    assert override["start_compound"] == "MEDIUM"
    assert override["next_box_lap"] == 12
    assert override["next_compound"] == "HARD"
    assert "box lap 12 for hards" in answer.lower()


@pytest.mark.asyncio
async def test_driver_next_lap_compound_override_uses_current_lap(stack) -> None:
    store, brain = await _brain(stack)
    await store.update(current_lap=10)
    await brain._fast_answer("I'm going to take the hard tyres next lap")
    override = (await store.snapshot_analysis())["strategy_override"]
    assert override["next_box_lap"] == 11
    assert override["next_compound"] == "HARD"


@pytest.mark.asyncio
async def test_driver_position_report_is_flagged_as_conflict_not_flatly_denied(stack) -> None:
    store, brain = await _brain(stack)
    await store.update(connected=True, player_position=21)
    answer = await brain._fast_answer("I'm currently in first")
    assert answer is not None
    assert "Your report is P1" in answer
    assert "timing shows P21" in answer
    assert "Negative" not in answer


@pytest.mark.asyncio
async def test_restart_request_uses_provisional_timing_when_event_grid_is_missing(stack) -> None:
    store, brain = await _brain(stack)

    def apply(state):
        state.connected = True
        state.player_car_index = 0
        state.player_position = 16
        for idx, position, name in ((0, 16, "Player"), (1, 15, "Lawson"), (2, 17, "Colapinto")):
            state.drivers[idx].car_idx = idx
            state.drivers[idx].position = position
            state.drivers[idx].name = name
            state.drivers[idx].active = True

    await store.mutate(apply)
    answer = await brain._fast_answer("please update the restart grid")
    assert answer == "Provisional timing order: P16, Lawson ahead, Colapinto behind."
