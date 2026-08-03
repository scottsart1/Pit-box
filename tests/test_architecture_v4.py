from __future__ import annotations

import pytest

from pitwall.udp import classify_session


def test_known_practice_type_is_never_relabelled_as_race() -> None:
    label, profile, source, _ = classify_session(
        raw_type_id=1,
        total_laps=57,
        current_lap=2,
        session_time_left_s=5400,
        session_duration_s=5400,
        session_length_id=7,
        weekend_structure=[1, 2, 3, 5, 15],
        override="auto",
    )
    assert profile == "practice"
    assert source == "udp"
    assert "Practice" in label


@pytest.mark.asyncio
async def test_strategy_override_selects_hard_plan(stack) -> None:
    store, _, strategy, _, _, _ = stack

    def setup(state):
        state.session_type = "Race"
        state.mode_profile = "race"
        state.current_lap = 9
        state.total_laps = 22
        state.track_id = 10
        state.player_position = 8
        state.active_cars = 20
        state.tyre.compound = "MEDIUM"
        state.tyre.age_laps = 8
        state.tyre.wear = [28, 29, 31, 32]
        state.tyre_sets = [
            {"compound": "HARD", "available": True, "wear_pct": 0, "usable_life_laps": 30},
            {"compound": "SOFT", "available": True, "wear_pct": 0, "usable_life_laps": 12},
        ]
        state.strategy_override.update({
            "enabled": True,
            "locked": True,
            "next_box_lap": 11,
            "next_compound": "HARD",
            "preferred_stops": 1,
        })
    await store.mutate(setup)
    result = await strategy.recompute()
    rec = result["recommended"]
    assert rec["fit_compound"] == "HARD"
    assert rec["box_lap"] == 11
    assert rec["driver_override"]["active"] is True
    assert rec["driver_override"]["honored"] is True


@pytest.mark.asyncio
async def test_condition_regression_uses_temperature_and_session_context(stack) -> None:
    _, database, *_ = stack
    for lap_num in range(1, 13):
        track_temp = 25 + lap_num
        wear = 1.0 + 0.08 * track_temp
        await database.save_lap({
            "session_uid": 10,
            "track_id": 99,
            "track_name": "Regression Test",
            "session_type": "Practice",
            "mode_profile": "practice",
            "lap_num": lap_num,
            "lap_time_ms": int((90 + 0.06 * lap_num) * 1000),
            "valid": True,
            "compound": "HARD",
            "tyre_age_start": lap_num - 1,
            "tyre_age_end": lap_num,
            "wear_start": [0, 0, 0, 0],
            "wear_end": [wear - .1, wear - .05, wear, wear],
            "temps_end": [90, 90, 95, 95],
            "track_temp_c": track_temp,
            "air_temp_c": 20 + lap_num / 3,
            "weather": "Clear",
            "fuel_start_kg": 60 - lap_num,
            "fuel_end_kg": 59 - lap_num,
            "position": 1,
            "setup": {"front_wing": 20, "rear_wing": 22, "on_throttle": 50},
            "trace": [],
        }, [])
    cool = await database.tyre_history_model(99, context={
        "tyre": {"age_laps": 5}, "fuel_kg": 45, "track_temp_c": 27,
        "air_temp_c": 21, "mode_profile": "race",
        "car_setup": {"front_wing": 20, "rear_wing": 22, "on_throttle": 50},
    })
    hot = await database.tyre_history_model(99, context={
        "tyre": {"age_laps": 5}, "fuel_kg": 45, "track_temp_c": 38,
        "air_temp_c": 26, "mode_profile": "race",
        "car_setup": {"front_wing": 20, "rear_wing": 22, "on_throttle": 50},
    })
    cool_wear = cool["compounds"]["HARD"]["condition_adjusted_wear_per_lap_pct"]
    hot_wear = hot["compounds"]["HARD"]["condition_adjusted_wear_per_lap_pct"]
    assert cool_wear is not None and hot_wear is not None
    assert hot_wear > cool_wear


@pytest.mark.asyncio
async def test_setup_preferences_change_recommendation(stack) -> None:
    store, _, _, setup, _, _ = stack
    await store.update(track_id=10, track_name="Spa")
    neutral = await setup.generate("race", 10)
    await store.update(driver_preferences={
        "strategy_priority": "balanced", "setup_bias": "rear_stability",
        "rear_stability": 3, "rotation": 0, "traction": 2,
        "tyre_life": 0, "straight_line": 0,
    })
    stable = await setup.generate("race", 10)
    assert stable["recommended"]["rear_wing"] > neutral["recommended"]["rear_wing"]
    assert stable["recommended"]["on_throttle"] < neutral["recommended"]["on_throttle"]

@pytest.mark.asyncio
async def test_driver_preferences_persist_in_database(stack) -> None:
    _, database, *_ = stack
    expected = {
        "strategy_priority": "tyre_life",
        "rear_stability": 3,
        "traction": 2,
    }
    await database.save_preference("driver_preferences", expected)
    assert await database.load_preference("driver_preferences", {}) == expected


@pytest.mark.asyncio
async def test_manual_race_override_can_answer_start_tyre_before_udp(stack) -> None:
    from pitwall.brain import EngineerBrain

    store, database, _, _, _, tools = stack
    brain = EngineerBrain(store, tools, database)
    answer = await brain._fast_answer(
        "please help me with the first tyre for the race. This is the race, tell me that"
    )
    assert answer is not None
    assert "Session locked to Race" in answer
    assert "Start on mediums" in answer
    snapshot = await store.snapshot_analysis()
    assert snapshot["session_mode_override"] == "race"
    assert snapshot["mode_profile"] == "race"


@pytest.mark.asyncio
async def test_old_laps_inherit_session_profile_for_practice_summary(stack) -> None:
    _, database, *_ = stack
    await database.upsert_session({
        "session_uid": 123,
        "track_id": 5,
        "track_name": "Test Track",
        "session_type": "Practice 1",
        "mode_profile": "practice",
        "total_laps": 20,
        "car_setup": {},
    })
    await database.save_lap({
        "session_uid": 123,
        "track_id": 5,
        "track_name": "Test Track",
        "session_type": "Practice 1",
        "lap_num": 1,
        "lap_time_ms": 96_276,
        "valid": True,
        "compound": "HARD",
        "tyre_age_start": 0,
        "tyre_age_end": 1,
        "wear_start": [0, 0, 0, 0],
        "wear_end": [3.0, 3.1, 3.2, 3.3],
        "temps_end": [90, 91, 95, 96],
        "fuel_start_kg": 50,
        "fuel_end_kg": 48,
        "position": 1,
        "setup": {},
        "trace": [],
    }, [])
    # Simulate a pre-3.6 lap row with no direct mode_profile value.
    with database._connect() as db:
        db.execute("UPDATE laps SET mode_profile=NULL WHERE session_uid=?", (123,))
    summary = await database.compound_run_summary(5, "HARD", "practice")
    assert summary["available"] is True
    assert summary["laps"] == 1


@pytest.mark.asyncio
async def test_strategy_challenge_explains_evidence_instead_of_blind_yes(stack) -> None:
    from pitwall.brain import EngineerBrain

    store, database, _, _, _, tools = stack
    brain = EngineerBrain(store, tools, database)
    await store.update(
        connected=True,
        current_lap=8,
        strategy={
            "confidence": "low",
            "recommended": {
                "box_lap": 9,
                "fit_compound": "SOFT",
                "stops_remaining": 2,
                "tyre_reason": "The recommendation uses only two personal wear samples.",
            },
        },
    )
    answer = await brain._fast_answer("are you sure about boxing this lap for softs? it does not make sense")
    assert answer is not None
    assert "two personal wear samples" in answer
    assert "Confidence low" in answer

class _VoiceBrain:
    async def ask(self, text: str) -> str:
        return "Copy."


class _VoiceAudio:
    def stop_playback(self) -> None:
        pass


@pytest.mark.asyncio
async def test_ptt_natural_pause_does_not_end_before_silence_threshold(monkeypatch, tmp_path) -> None:
    import asyncio
    import time

    from pitwall.config import settings
    from pitwall.state import StateStore
    from pitwall.voice import NativeVoiceController

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "native_voice", False)
    monkeypatch.setattr(settings, "ptt_release_mode", "explicit_or_silence")
    monkeypatch.setattr(settings, "ptt_silence_release_s", 0.30)
    monkeypatch.setattr(settings, "ptt_max_recording_s", 5.0)
    store = StateStore()
    voice = NativeVoiceController(store, _VoiceBrain(), _VoiceAudio())  # type: ignore[arg-type]
    voice._signal_pressed = True
    voice._speech_detected = True
    voice.pressed_at = time.monotonic() - 1.2
    voice._last_voice_at = time.monotonic() - 0.12
    await store.update(ptt_pressed=True)
    guard = asyncio.create_task(voice._recording_guard())
    await asyncio.sleep(0.10)
    assert (await store.snapshot_live())["ptt_pressed"] is True
    voice._last_voice_at = time.monotonic() - 0.40
    await asyncio.sleep(0.10)
    assert (await store.snapshot_live())["ptt_pressed"] is False
    if not guard.done():
        guard.cancel()
        with pytest.raises(asyncio.CancelledError):
            await guard
    else:
        await guard

@pytest.mark.asyncio
async def test_ptt_transient_zero_packet_does_not_clip_recording(monkeypatch, tmp_path) -> None:
    import asyncio

    from pitwall.config import settings
    from pitwall.state import StateStore
    from pitwall.voice import NativeVoiceController

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "native_voice", False)
    monkeypatch.setattr(settings, "ptt_release_mode", "explicit_or_silence")
    monkeypatch.setattr(settings, "ptt_release_ignore_ms", 50)
    monkeypatch.setattr(settings, "ptt_release_tail_s", 0.0)
    monkeypatch.setattr(settings, "ptt_silence_release_s", 2.2)
    store = StateStore()
    voice = NativeVoiceController(store, _VoiceBrain(), _VoiceAudio())  # type: ignore[arg-type]
    voice.mask = 4
    await voice.initialize()

    voice.on_button_status(4)
    await asyncio.sleep(0.07)
    assert (await store.snapshot_live())["ptt_pressed"] is True

    # A brief all-zero packet followed by the held-button heartbeat must not
    # end recording midway through a sentence.
    voice.on_button_status(0)
    await asyncio.sleep(0.02)
    voice.on_button_status(4)
    await asyncio.sleep(0.07)
    assert (await store.snapshot_live())["ptt_pressed"] is True

    # A stable zero status is a real button release.
    voice.on_button_status(0)
    await asyncio.sleep(0.08)
    assert (await store.snapshot_live())["ptt_pressed"] is False
    await voice.shutdown()


@pytest.mark.asyncio
async def test_stale_strategy_never_generates_a_new_pit_call(stack) -> None:
    from pitwall.brain import EngineerBrain

    store, database, _, _, _, tools = stack
    brain = EngineerBrain(store, tools, database)
    await store.update(connected=False, current_lap=7, strategy={})
    answer = await brain._fast_answer("any race strategy updates")
    assert answer is not None
    assert "cannot safely issue a new pit call" in answer


@pytest.mark.asyncio
async def test_fallback_soft_life_cannot_be_projected_indefinitely(stack) -> None:
    store, _, strategy, _, _, _ = stack

    def setup(state):
        state.connected = True
        state.session_type = "Race"
        state.mode_profile = "race"
        state.current_lap = 10
        state.total_laps = 35
        state.track_id = 10
        state.player_position = 8
        state.active_cars = 20
        state.tyre.compound = "SOFT"
        state.tyre.age_laps = 2
        state.tyre.wear = [8, 8, 9, 9]
        state.completed_laps = [
            {"compound": "HARD", "valid": True},
            {"compound": "MEDIUM", "valid": True},
        ]
        state.tyre_sets = []
    await store.mutate(setup)
    result = await strategy.recompute()
    assert result["recommended"]["stops_remaining"] >= 1
    assert result["recommended"]["instruction"] != "Stay out to the finish."
