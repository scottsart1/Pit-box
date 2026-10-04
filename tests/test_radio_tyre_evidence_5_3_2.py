import pytest

from pitwall.brain import EngineerBrain


def practice_evidence():
    return {
        "confidence": "low",
        "recommended": {"instruction": "Box lap 12 for SOFT."},
        "model_summary": {
            "confidence_basis": "Least-supported tyre stint in the selected plan.",
            "history_compatibility_basis": "Legacy track-matched history; car/formula compatibility unknown.",
            "compounds": {
                "MEDIUM": {
                    "historical_laps_recorded": 5, "historical_laps_observed": 5,
                    "live_laps_observed": 0, "wear_sample_size": 4, "pace_sample_size": 3,
                    "wear_source": "personal_history", "pace_source": "personal_history",
                    "deg_s_per_lap": .02,
                },
                "HARD": {
                    "historical_laps_recorded": 6, "historical_laps_observed": 6,
                    "wear_sample_size": 5, "pace_sample_size": 4,
                    "wear_source": "personal_history", "pace_source": "personal_history",
                },
                "SOFT": {
                    "historical_laps_recorded": 0, "historical_laps_observed": 0,
                    "wear_sample_size": 0, "pace_sample_size": 0,
                    "wear_source": "inferred_from_hard", "pace_source": "inferred_from_hard",
                    "inferred_from": "HARD", "deg_s_per_lap": .06,
                },
            },
        },
    }


@pytest.mark.asyncio
async def test_tyre_tool_preserves_practice_when_live_fit_is_missing(stack):
    store, _, _, _, _, tools = stack

    def setup(state):
        state.tyre.compound = "MEDIUM"
        state.strategy = practice_evidence()
        state.analysis = {"deg_model": {"current_slope_s_per_lap": None}}

    await store.mutate(setup)
    result = await tools.get_tyre_condition()
    assert result["deg_s_per_lap"] is None
    assert result["strategy_deg_s_per_lap"] == .02
    assert result["strategy_deg_source"] == "personal_history"
    assert result["compound_evidence"]["historical_laps_recorded"] == 5
    assert result["compound_evidence"]["pace_sample_size"] == 3
    assert result["strategy_confidence"] == "low"
    assert "compatibility unknown" in result["history_compatibility_basis"]


@pytest.mark.asyncio
async def test_inferred_soft_is_not_reported_as_a_measured_soft_fit(stack):
    store, _, _, _, _, tools = stack

    def setup(state):
        state.tyre.compound = "SOFT"
        state.strategy = practice_evidence()

    await store.mutate(setup)
    result = await tools.get_tyre_condition()
    assert result["strategy_deg_source"] == "inferred_from_hard"
    assert result["strategy_deg_s_per_lap"] == .06
    assert result["compound_evidence"]["pace_sample_size"] == 0
    assert result["compound_evidence"]["historical_laps_recorded"] == 0


@pytest.mark.asyncio
async def test_strategy_radio_gets_practice_evidence_without_an_extra_tool_round(stack):
    store, database, _, _, _, tools = stack
    await store.update(strategy=practice_evidence())
    brain = EngineerBrain(store, tools, database)
    header = await brain._header(include_strategy=True)
    assert '"MEDIUM":{"historical_laps_recorded":5' in header
    assert '"HARD":{"historical_laps_recorded":6' in header
    assert '"SOFT":{"historical_laps_recorded":0' in header
    assert "plan confidence low: Least-supported" in header
    assert "car/formula compatibility unknown" in header
    assert "TYRE EVIDENCE" not in await brain._header(include_strategy=False)
    assert brain._is_strategy_request("Why is confidence low despite my practice data?")
    assert not brain._is_strategy_request("What is my gap ahead?")


@pytest.mark.asyncio
async def test_no_history_payload_keeps_unknown_evidence_unknown(stack):
    store, _, _, _, _, tools = stack
    await store.update(strategy={})
    result = await tools.get_tyre_condition()
    assert result["compound_evidence"] == {}
    assert result["strategy_deg_s_per_lap"] is None
    assert result["strategy_deg_source"] is None
    assert result["history_compatibility_basis"] is None


@pytest.mark.asyncio
async def test_previous_compound_fit_is_not_spoken_as_current_tyre_evidence(stack):
    store, _, _, _, _, tools = stack

    def setup(state):
        state.tyre.compound = "MEDIUM"
        state.strategy = practice_evidence()
        state.analysis = {"deg_model": {
            "current_compound": "SOFT", "current_slope_s_per_lap": .4,
            "projected_cliff_lap": 6,
        }}

    await store.mutate(setup)
    result = await tools.get_tyre_condition()
    assert result["deg_s_per_lap"] is None
    assert result["projected_cliff_lap"] is None
    assert result["strategy_deg_s_per_lap"] == .02


@pytest.mark.asyncio
async def test_new_question_after_flashback_does_not_quote_discarded_live_evidence(stack):
    store, database, _, _, _, tools = stack
    await store.mark_packet(2026, 26, 77)
    previous = practice_evidence()
    previous["model_summary"]["compounds"]["MEDIUM"]["live_laps_observed"] = 5
    await store.update(strategy=previous)
    await store.synchronize_session_epoch(77, 0, 1)
    result = await tools.get_tyre_condition()
    assert result["compound_evidence"] == {}
    assert result["strategy_confidence"] is None
    brain = EngineerBrain(store, tools, database)
    header = await brain._header(include_strategy=True)
    assert "TYRE EVIDENCE" not in header
    assert "Box lap 12" not in header
