"""Dashboard-backed requests must survive an unavailable reasoning provider."""
from unittest.mock import AsyncMock
import copy
import time
import asyncio
from types import SimpleNamespace

import pytest

from pitwall.brain import EngineerBrain
from pitwall.radio_status import strategy_overview_request, strategy_overview, closing_target, gap_evidence
from pitwall.proactive import ProactiveEngineer


async def race(stack):
    store, database, _, _, _, tools = stack
    def seed(s):
        s.connected = True
        s.mode_profile = "race"
        s.current_lap, s.total_laps = 7, 26
        s.session_uid = 123
        s.session_time_s = 700
        s.player_car_index, s.player_position = 0, 4
        s.last_lap_ms = 96000
        s.tyre.compound = "MEDIUM"
        s.strategy = {"confidence": "low", "recommended": {
            "session_epoch": [123, 0, 0], "source_compound": "MEDIUM",
            "box_lap": 11, "fit_compound": "HARD", "stops_remaining": 2,
            "box_laps": [11, 20], "compounds": ["MEDIUM", "HARD", "SOFT"],
            "legal": True, "feasible": True, "inventory_feasible": True,
            "stop_required_reason": "mandatory compound change",
        }}
        for i, (name, pos, gap, lap) in enumerate([
            ("PLAYER", 4, 0, 96000), ("NORRIS", 5, 2.4, 95300),
            ("ALONSO", 3, -4.0, 96400),
        ]):
            d = s.drivers[i]
            d.name, d.position, d.gap_to_player_s = name, pos, gap
            d.active, d.last_lap_ms, d.current_lap = True, lap, 7
        s.drivers[1].gap_history = [
            {"session_time_s": 690, "lap": 7, "gap_s": 2.8},
            {"session_time_s": 700, "lap": 7, "gap_s": 2.4},
        ]
    await store.mutate(seed)
    brain = EngineerBrain(store, tools, database)
    brain._run = AsyncMock(side_effect=AssertionError("A live lookup contacted the provider"))
    return store, brain


@pytest.mark.asyncio
async def test_verbatim_current_strategy_bypasses_provider_and_reads_whole_plan(stack):
    store, brain = await race(stack)
    answer = await brain.ask("What does our race strategy look like?")
    assert all(word in answer.lower() for word in ("11", "20", "hard", "soft", "low", "2 stops"))
    brain._run.assert_not_awaited()
    assert (await store.snapshot_live())["llm_provider"] == "local"


@pytest.mark.parametrize("utterance", [
    "What is our strategy?", "What's my race plan?", "Any strategy updates?",
    "What does our race strategy look like?", "How is our strategy looking?",
    "Could you please give me a rundown of the overall strategy?",
    "Give me the full race strategy", "Walk me through the whole strategy",
    "Please tell me our current race strategy", "Can you run down of the pit strategy?",
])
def test_status_requests_route_fast(utterance):
    assert strategy_overview_request(utterance)
    assert EngineerBrain.classify_request(utterance) == "fast"


@pytest.mark.parametrize("utterance", [
    "What does our strategy look like if it rains?", "Explain why this strategy beats a two stop",
    "What is Norris's strategy?", "What should our strategy be?", "Can we pit earlier?",
    "Give me the full strategy and tyre temperatures", "Our strategy is horrible",
    "Do not box", "Log that strategy change", "What is our strategy compared to Alonso?",
])
def test_advice_and_compound_requests_are_not_status_lookups(utterance):
    assert not strategy_overview_request(utterance)


@pytest.mark.asyncio
async def test_strategy_state_guards_and_terse_summary(stack):
    store, brain = await race(stack)
    state = await store.snapshot_analysis()
    def answer(s):
        return strategy_overview(s, brain._spoken_strategy_instruction)
    stale = copy.deepcopy(state)
    stale["connected"] = False
    assert answer(stale).startswith("Telemetry stale; last confirmed")
    stale["game_paused"] = True
    assert "while paused" in answer(stale)
    for key in ("session_uid", "restart_epoch", "timeline_epoch"):
        changed = copy.deepcopy(state)
        changed[key] += 1
        assert "previous pit schedule is no longer current" in answer(changed)
    changed = copy.deepcopy(state)
    changed["tyre"]["compound"] = "HARD"
    assert "tyres now fitted" in answer(changed)
    for key in ("legal", "feasible", "inventory_feasible"):
        changed = copy.deepcopy(state)
        changed["strategy"]["recommended"][key] = False
        assert "no confirmed pit schedule" in answer(changed)
    changed = copy.deepcopy(state)
    changed["strategy_hold"] = {"active": True, "until_lap": 12}
    assert "on hold until lap 12" in answer(changed)
    changed["strategy_hold"] = {}
    changed["strategy_override"] = {"enabled": True}
    assert "not yet confirmed" in answer(changed)
    changed["strategy"]["recommended"]["driver_override"] = {"honored": True}
    assert answer(changed).startswith("Your selected plan:")
    changed["strategy"] = {}
    assert "building" in answer(changed)
    await store.update(radio_verbosity="terse")
    terse = await brain.ask("What does our race strategy look like?")
    assert all(word in terse for word in ("11", "20", "hard", "soft", "low confidence"))


@pytest.mark.asyncio
async def test_follow_up_recomputes_current_rival_numbers_not_radio_history(stack):
    store, brain = await race(stack)
    await store.append_radio("engineer", "NORRIS is catching, 8 seconds behind and closing.")
    for question in ("At what pace?", "How fast is he closing?", "How much per lap?", "How quickly is Norris catching us?"):
        assert closing_target(question, await store.snapshot_analysis()) == "NORRIS"
        answer = await brain.ask(question)
        assert "2.4 seconds behind" in answer and "0.7 seconds faster last lap" in answer
        # Restore the context after the numeric answer for each independent wording.
        await store.append_radio("engineer", "NORRIS is catching and closing.")
    await store.mutate(lambda s: setattr(s.drivers[1], "last_lap_ms", 96500))
    answer = await brain.ask("How quickly is Norris closing on us?")
    assert "you were 0.5 seconds faster last lap" in answer
    brain._run.assert_not_awaited()


@pytest.mark.asyncio
async def test_complex_questions_still_use_reasoning(stack):
    _, brain = await race(stack)
    brain._run = AsyncMock(return_value="Reasoned answer.")
    for request in ("What does our strategy look like if it rains?", "Why is Norris catching me?",
                    "How fast is Norris closing and should I defend?"):
        assert await brain.ask(request) == "Reasoned answer."
    assert brain._run.await_count == 3


@pytest.mark.asyncio
async def test_gap_freshness_direction_and_missing_evidence(stack):
    store, brain = await race(stack)
    state = await store.snapshot_analysis()
    rival = state["drivers"][1]
    for elapsed in (9, -1):
        changed = copy.deepcopy(state)
        changed["session_time_s"] = 700 + elapsed
        assert gap_evidence(changed, rival)["gap_change_s"] is None
    for kind in ("pit", "crossing", "rewind", "jump", "too_short", "missing"):
        changed = copy.deepcopy(rival)
        if kind == "pit": changed["pit_lane_timer_active"] = True
        if kind == "crossing": changed["gap_history"][0]["gap_s"] = -2.8
        if kind == "rewind": changed["gap_history"][0]["session_time_s"] = 701
        if kind == "jump": changed["gap_history"][0]["gap_s"] = 20
        if kind == "too_short": changed["gap_history"][0]["session_time_s"] = 699
        if kind == "missing": changed["gap_history"] = []
        assert gap_evidence(state, changed)["gap_change_s"] is None, kind
    await store.mutate(lambda s: s.drivers[1].gap_history.clear())
    answer = await brain.ask("At what pace is the driver behind closing in?")
    assert "no reliable measured closing rate" in answer and "0.7 seconds faster last lap" in answer
    await store.update(connected=False)
    answer = await brain.ask("At what pace is the driver behind closing in?")
    assert "cannot confirm" in answer and "0.7" not in answer


@pytest.mark.asyncio
async def test_proactive_rate_survives_narration_and_obsolete_rival_is_dropped(stack, monkeypatch):
    from pitwall.config import settings
    store, brain = await race(stack)
    state = await store.snapshot_analysis()
    event = {"type": "rival_pace", "session_uid": 123, "expires_at": time.time() + 30,
             "payload": {"car_idx": 1, "driver": "NORRIS", "gap_to_player_s": 2.4,
                         "gap_change_s": -0.4, "window_s": 10}}
    engine = object.__new__(ProactiveEngineer)
    engine.brain = brain
    brain.proactive = AsyncMock(side_effect=AssertionError("Rate alert contacted provider"))
    monkeypatch.setattr(settings, "proactive_narration_enabled", True)
    assert "gained 0.4 seconds over 10 seconds" in await engine._narrate(event, state)
    assert engine._event_still_relevant(event, state)
    for key, value in (("gap_to_player_s", -1), ("gap_to_player_s", 3.1),
                       ("gap_to_player_s", None), ("pit_status", 1), ("name", "ALONSO")):
        changed = copy.deepcopy(state)
        changed["drivers"][1][key] = value
        assert not engine._event_still_relevant(event, changed)


@pytest.mark.asyncio
async def test_car_behind_rate_uses_gap_evidence_and_dashboard_lap_delta(stack):
    _, brain = await race(stack)
    answer = await brain.ask("At what pace is the driver behind closing in?")
    assert "0.4" in answer and "10" in answer and "0.7" in answer
    assert "last lap" in answer.lower()
    brain._run.assert_not_awaited()


@pytest.mark.asyncio
async def test_native_voice_command_delivers_status_with_provider_unavailable(stack, monkeypatch, tmp_path):
    from pitwall.config import settings
    from pitwall.voice import NativeVoiceController
    store, brain = await race(stack)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "voice_realtime_enabled", False)
    audio = SimpleNamespace(play_ack=AsyncMock(), stop_playback=lambda: None)
    voice = NativeVoiceController(store, brain, audio)
    monkeypatch.setattr(voice, "speak_text", AsyncMock(return_value=True))
    try:
        for utterance in ("What does our race strategy look like?", "At what pace is the driver behind closing in?"):
            await asyncio.wait_for(voice._run_command(utterance, "ptt"), timeout=3)
            spoken = voice.speak_text.await_args.args[0]
            assert "took too long" not in spoken and "dropped" not in spoken
            assert "low confidence" in spoken if "strategy" in utterance else "0.7 seconds faster last lap" in spoken
            assert (await store.snapshot_live())["radio_latency"]["error_kind"] == ""
        brain._run.assert_not_awaited()
    finally:
        await voice.shutdown()
