from copy import deepcopy

import pytest

from pitwall.brain import EngineerBrain
from pitwall.providers import ProviderResult
from pitwall.session_guard import SessionChangedError


def plan(lap=23, *, wear=60):
    best = {"box_lap": lap, "fit_compound": "SOFT", "box_laps": [lap],
            "compounds": ["MEDIUM", "SOFT"], "feasible": True, "legal": True,
            "projected_max_wear_pct": wear}
    alternate = {**best, "box_lap": lap + 1, "box_laps": [lap + 1]}
    return {"recommended": best, "plans": [best, alternate], "confidence": "low",
            "tyre_inventory": {"status": "known"}}


async def radio(stack, monkeypatch, latest, *, use_tool=True, switch_session=False):
    store, db, _, _, _, tools = stack
    for group in (1, 2, 6, 7, 10):
        await store.mark_packet(2026, 26, 1234, packet_id=group)
    old = plan()
    await store.update(current_lap=6, total_laps=31, mode_profile="race", strategy=old)
    brain = EngineerBrain(store, tools, db)

    async def call(name, arguments):
        assert name == "get_pit_strategy"
        return deepcopy(old)

    async def generate(**kwargs):
        if use_tool:
            await kwargs["execute_tool"]("get_pit_strategy", {})
        if switch_session:
            await store.mark_packet(2026, 26, 5678, packet_id=1)
        else:
            await store.update(strategy=deepcopy(latest))
        return ProviderResult("Box lap 23 for softs. Alternative lap 24 for softs.", "test", "test", 1)

    monkeypatch.setattr(tools, "call", call)
    monkeypatch.setattr(brain.router, "generate", generate)
    return store, brain


@pytest.mark.asyncio
@pytest.mark.parametrize("use_tool", [True, False])
async def test_material_plan_change_replaces_stale_narration_with_complete_current_facts(stack, monkeypatch, use_tool):
    store, brain = await radio(stack, monkeypatch, plan(21), use_tool=use_tool)
    before_override = (await store.snapshot_analysis())["strategy_override"]
    answer = await brain.ask("Give me the full pit strategy and its best alternative.")
    assert "Telemetry updated while checking" in answer
    assert "box lap 21 for SOFT" in answer and "alternative: box lap 22 for SOFT" in answer
    assert "lap 23" not in answer and "confidence low" in answer
    state = await store.snapshot_analysis()
    assert state["llm_provider"] == "local" and state["llm_model"] == "strategy-refresh-guard"
    assert state["strategy"] == plan(21)
    assert state["strategy_override"] == before_override


@pytest.mark.asyncio
@pytest.mark.parametrize("wear", [60, 61])
async def test_same_plan_or_minor_wear_change_preserves_narration(stack, monkeypatch, wear):
    store, brain = await radio(stack, monkeypatch, plan(wear=wear))
    answer = await brain.ask("Give me the full pit strategy and its best alternative.")
    assert "Box lap 23 for softs. Alternative lap 24" in answer
    assert "Telemetry updated" not in answer
    assert (await store.snapshot_analysis())["llm_provider"] == "test"


@pytest.mark.asyncio
async def test_changed_best_alternative_refreshes_full_answer_even_when_primary_is_unchanged(stack, monkeypatch):
    latest = plan()
    latest["plans"][1] = {**latest["plans"][1], "box_lap": 25, "box_laps": [25]}
    store, brain = await radio(stack, monkeypatch, latest)
    answer = await brain.ask("Give me the full pit strategy and its best alternative.")
    assert "Telemetry updated while checking" in answer
    assert "box lap 23 for SOFT" in answer and "alternative: box lap 25 for SOFT" in answer
    assert "lap 24" not in answer
    assert (await store.snapshot_analysis())["llm_model"] == "strategy-refresh-guard"


@pytest.mark.asyncio
async def test_session_change_discards_narration_before_using_new_session_plan(stack, monkeypatch):
    store, brain = await radio(stack, monkeypatch, plan(21), switch_session=True)
    with pytest.raises(SessionChangedError):
        await brain.ask("Give me the full pit strategy and its best alternative.")
    assert not any(item["role"] == "engineer" for item in (await store.snapshot_analysis())["radio_log"])


@pytest.mark.asyncio
async def test_confirmed_finish_during_generation_replaces_live_plan_even_if_plan_is_unchanged(stack, monkeypatch):
    store, brain = await radio(stack, monkeypatch, plan())

    async def generate(**kwargs):
        await kwargs["execute_tool"]("get_pit_strategy", {})
        await store.update(final_classification={"position": 1, "laps": 31})
        return ProviderResult("Stay out and finish the lap.", "test", "test", 1)

    monkeypatch.setattr(brain.router, "generate", generate)
    answer = await brain.ask("Give me the full pit strategy and its best alternative.")
    assert answer.startswith("Race complete; final classification P1 is confirmed")
    assert "stay out" not in answer.lower() and "Provisional" not in answer
    assert (await store.snapshot_analysis())["llm_model"] == "strategy-refresh-guard"


@pytest.mark.parametrize("utterance", [
    "What if we box earlier for hards instead?",
    "Compare my current pit strategy with my previous race.",
    "What is the car ahead's pit strategy?",
    "Should we clear the strategy override?",
])
def test_hypothetical_historical_rival_and_override_advice_are_not_replaced(utterance):
    assert not EngineerBrain._requests_current_strategy_plan({"drivers": []}, utterance)


def test_whole_plan_identity_includes_later_stops_without_reranking():
    earlier = plan()
    later = deepcopy(earlier)
    later["recommended"]["box_laps"] = [23, 28]
    later["recommended"]["compounds"] = ["MEDIUM", "SOFT", "HARD"]
    assert EngineerBrain._material_plan_identity(earlier) != EngineerBrain._material_plan_identity(later)
