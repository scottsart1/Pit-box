import json

import pytest

from pitwall.brain import EngineerBrain
from pitwall.providers import ProviderFailure, ProviderRequestError
from pitwall.session_guard import SessionChangedError


REQUEST = "Give me the full pit strategy and its best alternative."


async def configured(stack, monkeypatch, *, failure_kind="deadline"):
    store, database, _, _, _, tools = stack
    for group in (1, 2, 6, 7, 10):
        await store.mark_packet(2026, 26, 42, packet_id=group)
    best = {"feasible": True, "legal": True, "box_laps": [12],
            "compounds": ["MEDIUM", "HARD"], "box_lap": 12, "fit_compound": "HARD",
            "instruction": "Box lap 12 for HARD.", "stops_remaining": 1,
            "projected_finish_wear_fl_fr_rl_rr": [51, 52, 53, 54],
            "stint_models": [{"laps": 4, "lap_times_s": [90] * 60,
                              "wheel_projection": [[1, 2, 3, 4]] * 60}]}
    alternate = {**best, "box_laps": [10, 16], "compounds": ["MEDIUM", "SOFT", "SOFT"]}
    await store.update(current_lap=8, total_laps=20, mode_profile="race", session_type="Race",
                       strategy={"recommended": best, "plans": [best, alternate],
                                 "confidence": "low", "tyre_inventory": {"status": "unknown"}})
    brain = EngineerBrain(store, tools, database)

    async def fail(**kwargs):
        raise ProviderRequestError([ProviderFailure("openai", failure_kind, "test provider deadline")])

    monkeypatch.setattr(brain.router, "generate", fail)
    return store, tools, brain


@pytest.mark.asyncio
async def test_deadline_keeps_full_verified_plan_and_alternative_with_failure_diagnostic(stack, monkeypatch):
    store, _, brain = await configured(stack, monkeypatch)
    answer = await brain.ask(REQUEST)
    assert "analysis timed out" in answer
    assert "box lap 12 for HARD" in answer
    assert "alternative: box lap 10 for SOFT, then box lap 16 for SOFT" in answer
    assert "confidence low" in answer and "confirm spare sets" in answer
    state = await store.snapshot_analysis()
    assert "ProviderRequestError" in state["llm_last_error"]
    assert state["llm_model"] == "strategy-deadline-fallback"
    assert state["radio_log"][-1]["text"] == answer


@pytest.mark.asyncio
async def test_deadline_does_not_promote_stale_plan_to_current_call(stack, monkeypatch):
    store, _, brain = await configured(stack, monkeypatch)
    await store.update(connected=False)
    answer = await brain.ask(REQUEST)
    assert "last confirmed computed plan" in answer
    assert "live telemetry must confirm" in answer


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [{"feasible": False}, {"legal": False}, {"box_laps": [7]}])
async def test_deadline_never_reissues_expired_or_unsupported_plan(stack, monkeypatch, invalid):
    store, _, brain = await configured(stack, monkeypatch)
    await store.mutate(lambda state: state.strategy["recommended"].update(invalid))
    answer = await brain.ask(REQUEST)
    assert "cannot give a new pit instruction" in answer
    assert "box lap" not in answer


@pytest.mark.asyncio
async def test_non_deadline_errors_and_unrelated_requests_keep_error_contract(stack, monkeypatch):
    _, _, brain = await configured(stack, monkeypatch, failure_kind="configuration")
    with pytest.raises(ProviderRequestError):
        await brain.ask(REQUEST)
    _, _, brain = await configured(stack, monkeypatch)
    with pytest.raises(ProviderRequestError):
        await brain.ask("Why am I losing speed on corner exit?")


@pytest.mark.asyncio
async def test_deadline_during_session_switch_discards_old_answer(stack, monkeypatch):
    store, _, brain = await configured(stack, monkeypatch)

    async def switch_then_fail(**kwargs):
        await store.mark_packet(2026, 26, 43, packet_id=1)
        raise ProviderRequestError([ProviderFailure("openai", "deadline", "test deadline")])

    monkeypatch.setattr(brain.router, "generate", switch_then_fail)
    with pytest.raises(SessionChangedError):
        await brain.ask(REQUEST)
    assert not any(entry["role"] == "engineer" for entry in (await store.snapshot_analysis())["radio_log"])


@pytest.mark.asyncio
async def test_radio_strategy_payload_omits_repeated_simulation_series_preserves_decision(stack, monkeypatch):
    store, tools, _ = await configured(stack, monkeypatch)
    raw = (await store.snapshot_analysis())["strategy"]
    assert await tools.get_pit_strategy() == raw
    payload = await tools.call("get_pit_strategy", {})
    assert len(json.dumps(payload)) < len(json.dumps(raw)) / 2
    assert payload["recommended"]["box_laps"] == [12]
    assert payload["recommended"]["projected_finish_wear_fl_fr_rl_rr"] == [51, 52, 53, 54]
    assert payload["plans"][1]["compounds"] == ["MEDIUM", "SOFT", "SOFT"]
    assert payload["tyre_inventory"] == {"status": "unknown"}
    assert "lap_times_s" not in json.dumps(payload)
    assert "wheel_projection" not in json.dumps(payload)
    assert (await store.snapshot_analysis())["strategy"] == raw
    assert await tools.get_pit_strategy() == raw
