import asyncio
from copy import deepcopy

import pytest

from pitwall.brain import EngineerBrain
from pitwall.proactive import ProactiveEngineer
from pitwall.providers import ProviderResult


def plan(confidence="low", source="track_default"):
    return {
        "confidence": confidence,
        "recommended": {"compounds": ["MEDIUM", "HARD"], "box_laps": [12]},
        "model_summary": {"compounds": {"HARD": {"wear_source": source, "pace_source": source}}},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("verbosity", ["standard", "terse"])
async def test_strategy_uncertainty_survives_provider_sentence_cap(stack, monkeypatch, verbosity):
    store, db, _, _, _, tools = stack
    brain = EngineerBrain(store, tools, db)
    await store.update(radio_verbosity=verbosity)

    async def generate(**kwargs):
        return ProviderResult("Box lap 12 for hards; alternative lap 13. That is legal. Keep the current tyres until then. This is only provisional.", "test", "test", 1)

    monkeypatch.setattr(brain.router, "generate", generate)
    answer = await brain._run("Full strategy", "low", "instructions", route="deep", strategy_context=plan())
    assert "confidence low" in answer and "tyre estimates include track defaults" in answer
    assert "Box lap 12 for hards; alternative lap 13." in answer
    assert "This is only provisional" not in answer  # Provider's late caveat was capped.


@pytest.mark.asyncio
async def test_actual_tool_plan_supersedes_header_confidence(stack, monkeypatch):
    store, db, _, _, _, tools = stack
    brain = EngineerBrain(store, tools, db)
    returned = plan()

    async def call(name, arguments):
        return returned

    async def generate(**kwargs):
        await kwargs["execute_tool"]("get_pit_strategy", {})
        # Mutating the caller's object after the tool boundary cannot rewrite
        # the frozen evidence used for this particular response.
        returned["confidence"] = "high"
        return ProviderResult("Box lap 12 for hards.", "test", "test", 1)

    monkeypatch.setattr(tools, "call", call)
    monkeypatch.setattr(brain.router, "generate", generate)
    answer = await brain._run("Full strategy", "low", "instructions", strategy_context=plan("high", "measured"))
    assert "confidence low" in answer and "track defaults" in answer


@pytest.mark.asyncio
async def test_concurrent_generations_do_not_share_strategy_evidence(stack, monkeypatch):
    store, db, _, _, _, tools = stack
    brain = EngineerBrain(store, tools, db)
    both_started = asyncio.Event()
    arrived = 0

    async def call(name, arguments):
        return plan(arguments["confidence"], "measured")

    async def generate(**kwargs):
        nonlocal arrived
        await kwargs["execute_tool"]("get_pit_strategy", {"confidence": kwargs["prompt"]})
        arrived += 1
        if arrived == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), 2)
        return ProviderResult("Box lap 12 for hards.", "test", "test", 1)

    monkeypatch.setattr(tools, "call", call)
    monkeypatch.setattr(brain.router, "generate", generate)
    low, high = await asyncio.gather(*[
        brain._run(confidence, "low", "instructions", strategy_context={})
        for confidence in ("low", "high")
    ])
    assert "confidence low" in low
    assert high == "Box lap 12 for hards."


@pytest.mark.asyncio
async def test_unrelated_request_never_gets_strategy_disclaimer(stack, monkeypatch):
    store, db, _, _, _, tools = stack
    brain = EngineerBrain(store, tools, db)

    async def generate(**kwargs):
        return ProviderResult("Fuel margin is positive.", "test", "test", 1)

    monkeypatch.setattr(brain.router, "generate", generate)
    assert await brain._run("Fuel advice", "low", "instructions") == "Fuel margin is positive."


@pytest.mark.parametrize("confidence", ["medium", "high"])
def test_supported_confidence_is_not_downgraded(confidence):
    text = "Box lap 12 for hards."
    assert EngineerBrain.qualify_strategy_text(text, plan(confidence, "measured")) == text


def test_missing_evidence_is_unreported_not_invented_track_defaults():
    payload = {"recommended": {"compounds": ["MEDIUM", "HARD"]}}
    original = deepcopy(payload)
    answer = EngineerBrain.qualify_strategy_text("Box lap 12 for hards.", payload)
    assert "confidence unreported" in answer and "track defaults" not in answer
    assert payload == original
    assert EngineerBrain.qualify_strategy_text(answer, payload) == answer


def test_red_flag_proactive_keeps_urgent_prefix_plan_and_uncertainty():
    advice = "Best strategy: spare softs, then mediums on lap 13. Alternative: fresh mediums, then softs on lap 21."
    event = {"type": "race_control", "payload": {"to": "red_flag", "red_flag_restart": {"instruction": advice}}}
    answer = ProactiveEngineer._fallback_text(event, {"strategy": plan()})
    assert answer.startswith("Red flag, red flag.")
    assert advice in answer
    assert "confidence low" in answer and "track defaults" in answer
