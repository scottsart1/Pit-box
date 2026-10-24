import asyncio

import numpy as np
import pytest

from pitwall.audio import AudioService
from pitwall.config import settings
from pitwall.state import StateStore
from pitwall.voice import NativeVoiceController


def test_wake_phrase_matches_only_at_transcript_start():
    phrases = ["mark", "hey mark", "mark radio"]

    matched, command, phrase = AudioService.extract_wake_command(
        "Mark, what is the target lap?",
        phrases,
    )
    assert matched is True
    assert command == "what is the target lap"
    assert phrase == "mark"

    matched, command, phrase = AudioService.extract_wake_command(
        "Hey Mark — give me the top three best laps.",
        phrases,
    )
    assert matched is True
    assert command == "give me the top three best laps"
    assert phrase == "hey mark"

    matched, _, _ = AudioService.extract_wake_command(
        "The commentator said Mark was quick.",
        phrases,
    )
    assert matched is False


def test_phrase_only_arms_follow_up():
    matched, command, phrase = AudioService.extract_wake_command(
        "Mark.",
        ["mark"],
    )
    assert matched is True
    assert command == ""
    assert phrase == "mark"


def test_plain_marc_alias_is_accepted_for_mark():
    matched, command, phrase = AudioService.extract_wake_command(
        "Marc, radio check.",
        ["mark", "marc", "hey mark"],
    )
    assert matched is True
    assert command == "radio check"
    assert phrase == "marc"


def test_wake_transcription_prompt_names_mark_without_a_positional_hint():
    # The name must still be steered, or one-word clips alternate between
    # "Mark" and "Marc" (the 3.6.1 fix).
    prompt = AudioService.transcription_prompt(
        ["Verstappen", "Norris"],
        ["mark", "marc", "hey mark"],
    )
    assert "Mark" in prompt
    assert "Marc" in prompt
    assert "Verstappen" in prompt

    # But it must never claim where the name falls. The wake gate matches on
    # exactly this token at position 0, so telling the transcriber that the
    # opening word is probably "Mark" makes the steering self-fulfilling and
    # turns noise into a genuine-looking wake call.
    lowered = prompt.lower()
    assert "opening word" not in lowered
    assert "accepted openings" not in lowered
    assert "never adding a name that was not said" in lowered


def test_prompt_echo_guard_tracks_the_current_prompt_wording():
    # The guard goes stale whenever transcription_prompt changes, so the
    # current opening must be one it recognises.
    prompt = AudioService.transcription_prompt(None, ["mark", "marc"])
    assert AudioService._looks_like_prompt_echo(prompt, prompt) is True


def test_silence_artifacts_are_not_treated_as_speech():
    # What a transcriber returns for silence or room noise, not driver radio.
    for artifact in ("Thank you.", "you", "Thanks for watching!", "Okay", "Um"):
        assert AudioService._looks_like_silence_artifact(artifact) is True


def test_real_radio_survives_the_silence_filter():
    for utterance in (
        "Mark, what is the gap to Norris",
        "box this lap",
        "okay box box",
        "thanks, what is my target lap",
    ):
        assert AudioService._looks_like_silence_artifact(utterance) is False


class _WakeBrain:
    @staticmethod
    def classify_request(text: str) -> str:
        return "fast"


class _WakeAudio:
    extract_wake_command = staticmethod(AudioService.extract_wake_command)

    def __init__(self) -> None:
        self.received_phrases: list[str] = []

    def stop_playback(self) -> None:
        pass

    async def transcribe(
        self,
        path,
        driver_names=None,
        wake_phrases=None,
    ) -> str:
        del path, driver_names
        self.received_phrases = list(wake_phrases or [])
        return "Marc, radio check."


@pytest.mark.asyncio
async def test_wake_candidate_routes_marc_and_passes_wake_prompt_context(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "wake_phrase", "mark")
    monkeypatch.setattr(settings, "wake_aliases", "marc,hey mark")
    audio = _WakeAudio()
    controller = NativeVoiceController(
        StateStore(),
        _WakeBrain(),  # type: ignore[arg-type]
        audio,  # type: ignore[arg-type]
    )
    accepted: dict[str, str] = {}

    async def capture(command: str, phrase: str) -> None:
        accepted["command"] = command
        accepted["phrase"] = phrase

    monkeypatch.setattr(controller, "_accept_wake", capture)
    frames = [np.full((8000, 1), 1000, dtype=np.int16)]
    await controller._process_wake_candidate(frames, "speech_silence")

    assert accepted == {"command": "radio check", "phrase": "marc"}
    assert "mark" in audio.received_phrases
    assert "marc" in audio.received_phrases
    await controller.shutdown()



def test_old_env_alias_list_still_gets_plain_marc(monkeypatch):
    monkeypatch.setattr(settings, "wake_phrase", "mark")
    monkeypatch.setattr(settings, "wake_aliases", "hey mark,mark radio,hey marc")
    assert "marc" in settings.wake_phrases


@pytest.mark.asyncio
async def test_quiet_wake_audio_crosses_adaptive_start_gate(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "wake_enabled", True)
    monkeypatch.setattr(settings, "native_voice", True)
    monkeypatch.setattr(settings, "wake_start_blocks", 2)
    monkeypatch.setattr(settings, "wake_min_speech_rms", 75.0)
    monkeypatch.setattr(settings, "wake_speech_rms", 180.0)

    controller = NativeVoiceController(
        StateStore(),
        _WakeBrain(),  # type: ignore[arg-type]
        _WakeAudio(),  # type: ignore[arg-type]
    )
    quiet_voice = np.full((480, 1), 100, dtype=np.int16)
    controller._consume_audio_block(quiet_voice)
    controller._consume_audio_block(quiet_voice)
    await __import__("asyncio").sleep(0)

    assert controller._wake_speaking is True
    assert controller._wake_effective_threshold == 75.0
    await controller.shutdown()


class _FailingBrain:
    """Brain whose model call fails the way a provider deadline does."""

    @staticmethod
    def classify_request(text: str) -> str:
        return "normal"

    async def ask(self, command: str) -> str:
        raise RuntimeError("No LLM provider completed the request.")


class _AckAudio:
    def __init__(self) -> None:
        self.acks: list[str] = []

    def stop_playback(self) -> None:
        pass

    async def play_ack(self, kind: str = "copy") -> None:
        self.acks.append(kind)


@pytest.mark.asyncio
async def test_brain_failure_is_spoken_not_silent(monkeypatch, tmp_path) -> None:
    """When the model call fails after the ack, the driver must hear it.

    In a real race the provider deadline expired twice and the driver got
    "Copy" followed by dead air; the failure lived only in the log. The
    fallback line goes out over the same TTS path, which is independent of
    the failed model call.
    """
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    store = StateStore()
    controller = NativeVoiceController(
        store,
        _FailingBrain(),  # type: ignore[arg-type]
        _AckAudio(),  # type: ignore[arg-type]
    )
    spoken: list[str] = []

    async def capture_speech(text: str) -> bool:
        spoken.append(text)
        return True

    monkeypatch.setattr(controller, "speak_text", capture_speech)
    from pitwall.voice import BRAIN_FALLBACK_LINE

    # Must not raise: the failure is handled, spoken, and recorded as state.
    await controller._run_command("what's the gap ahead", "wake")

    assert spoken == [BRAIN_FALLBACK_LINE]
    snapshot = await store.snapshot_live()
    assert snapshot["engineer_status"] == "error"
    assert "Engineer error" in snapshot["last_error"]
    assert snapshot["radio_latency"]["stage"] == "error"
    await controller.shutdown()


def test_a_little_filler_before_the_wake_phrase_still_wakes():
    """Reported from Las Vegas: "Mark" detection was too tight.

    Real drivers (and STT itself) prepend filler — "uh, Mark", "okay Mark".
    Up to two known filler words are tolerated; the wake word must still be
    at the effective start, so mid-sentence mentions do not activate.
    """
    phrases = ["mark", "hey mark"]
    for transcript, expected_command in (
        ("Uh, Mark, box confirm", "box confirm"),
        ("Okay Mark what's the gap", "what's the gap"),
        ("yeah, uh, Mark, tyres are gone", "tyres are gone"),
        ("So Mark, plan?", "plan"),
    ):
        matched, command, _ = AudioService.extract_wake_command(transcript, phrases)
        assert matched, transcript
        assert command == expected_command, transcript

    for transcript in (
        "I think Mark said to box",             # wake word mid-sentence
        "the marker board on the left",         # not the wake word at all
        "tell me what Mark thinks",             # mid-sentence again
        "totally random mark",                  # non-filler word before it
    ):
        matched, _, _ = AudioService.extract_wake_command(transcript, phrases)
        assert not matched, transcript


class _SilentAudio(_WakeAudio):
    """Transcriber that hears nothing in the clip, as it does for room noise."""

    async def transcribe(
        self,
        path,
        driver_names=None,
        wake_phrases=None,
    ) -> str:
        del path, driver_names, wake_phrases
        return ""


def _noise_controller(monkeypatch, tmp_path, audio) -> NativeVoiceController:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "wake_enabled", True)
    monkeypatch.setattr(settings, "native_voice", True)
    monkeypatch.setattr(settings, "wake_start_blocks", 2)
    monkeypatch.setattr(settings, "wake_min_speech_rms", 75.0)
    monkeypatch.setattr(settings, "wake_speech_rms", 180.0)
    monkeypatch.setattr(settings, "wake_noise_multiplier", 1.45)
    monkeypatch.setattr(settings, "wake_noise_margin_rms", 18.0)
    return NativeVoiceController(
        StateStore(),
        _WakeBrain(),  # type: ignore[arg-type]
        audio,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_steady_noise_above_the_floor_is_learned_from_a_hard_capped_clip(
    monkeypatch,
    tmp_path,
) -> None:
    # Room noise at 90 RMS sits above the 75 RMS minimum trigger. Block-level
    # tracking never learns a level above the trigger, so without this the
    # gate stays open and every capture runs to the hard cap.
    controller = _noise_controller(monkeypatch, tmp_path, _SilentAudio())
    noise = np.full((480, 1), 90, dtype=np.int16)
    for _ in range(3):
        controller._consume_audio_block(noise)
    assert controller._wake_speaking is True
    assert controller._wake_noise_rms == 0.0

    controller._clear_wake_capture()
    await controller._process_wake_candidate([np.full((16000, 1), 90, dtype=np.int16)], "hard_cap")

    assert controller._wake_noise_rms == pytest.approx(90.0)
    assert controller._wake_effective_threshold > 90.0
    state = await controller.store.snapshot_live()
    assert state["wake_last_reason"] == "empty transcript"

    # The same room no longer opens the gate, but a call spoken over it does.
    for _ in range(3):
        controller._consume_audio_block(noise)
    assert controller._wake_speaking is False
    voice = np.full((480, 1), 200, dtype=np.int16)
    controller._consume_audio_block(voice)
    controller._consume_audio_block(voice)
    assert controller._wake_speaking is True
    await controller.shutdown()


@pytest.mark.asyncio
async def test_an_empty_transcript_does_not_deafen_a_quiet_room(
    monkeypatch,
    tmp_path,
) -> None:
    # A soft "Mark" the transcriber missed must not stop the next soft "Mark"
    # from being captured: the floor only moves halfway towards the clip.
    controller = _noise_controller(monkeypatch, tmp_path, _SilentAudio())
    controller._wake_noise_rms = 15.0
    await controller._process_wake_candidate([np.full((16000, 1), 70, dtype=np.int16)], "speech_silence")

    assert controller._wake_noise_rms == pytest.approx(42.5)
    assert controller._wake_effective_threshold == pytest.approx(79.6, abs=0.1)
    quiet_voice = np.full((480, 1), 100, dtype=np.int16)
    controller._consume_audio_block(quiet_voice)
    controller._consume_audio_block(quiet_voice)
    assert controller._wake_speaking is True
    await controller.shutdown()


@pytest.mark.asyncio
async def test_noise_learning_stops_at_the_configured_ceiling(
    monkeypatch,
    tmp_path,
) -> None:
    controller = _noise_controller(monkeypatch, tmp_path, _SilentAudio())
    await controller._process_wake_candidate([np.full((16000, 1), 500, dtype=np.int16)], "hard_cap")

    # (180 - 18) / 1.45: the level at which the trigger already sits on its
    # ceiling, so a single shout cannot pin the estimate far above the room.
    assert controller._wake_noise_rms == pytest.approx(111.7, abs=0.1)
    assert controller._wake_effective_threshold == 180.0

    # Ordinary tracking still brings it back down once the room is quiet.
    for _ in range(200):
        controller._consume_audio_block(np.full((480, 1), 15, dtype=np.int16))
    assert controller._wake_noise_rms < 20.0
    assert controller._wake_effective_threshold == 75.0
    await controller.shutdown()


@pytest.mark.asyncio
async def test_wake_decisions_follow_capture_time_not_processing_time(
    monkeypatch,
    tmp_path,
) -> None:
    # The event loop stalls while the car is on track (frames for two
    # dashboards, a lap-end burst), so microphone blocks reach the detector
    # late and all at once. Judged by processing time, 0.4 s of "Mark"
    # followed by 0.7 s of silence never ends: no wall-clock time passes
    # between the blocks. Judged by capture time the capture ends when the
    # silence is heard, exactly as it would have on an idle loop.
    controller = _noise_controller(monkeypatch, tmp_path, _SilentAudio())
    monkeypatch.setattr(settings, "wake_silence_s", 0.60)
    monkeypatch.setattr(settings, "wake_min_utterance_s", 0.35)
    launched: list[tuple[int, str]] = []
    monkeypatch.setattr(
        controller, "_launch_wake_candidate", lambda frames, reason: launched.append((len(frames), reason))
    )
    block_s = 0.03
    voice = np.full((480, 1), 200, dtype=np.int16)
    silence = np.full((480, 1), 10, dtype=np.int16)
    captured = 100.0
    for _ in range(14):  # 0.42 s of speech
        controller._consume_audio_block(voice, captured_at=captured)
        captured += block_s
    assert controller._wake_speaking is True
    for _ in range(24):  # 0.72 s of silence
        controller._consume_audio_block(silence, captured_at=captured)
        captured += block_s
    await asyncio.sleep(0)
    assert launched and launched[0][1] == "speech_silence"
    assert controller._wake_speaking is False

    # The queue hands the consumer the capture stamp it was given.
    controller._queue_audio_block(voice, 123.456)
    block, stamp = controller._audio_queue.get_nowait()
    assert stamp == 123.456 and block is voice
    await controller.shutdown()
