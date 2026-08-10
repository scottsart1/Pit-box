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
