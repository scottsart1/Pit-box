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


def test_wake_transcription_prompt_names_mark_explicitly():
    prompt = AudioService.transcription_prompt(
        ["Verstappen", "Norris"],
        ["mark", "marc", "hey mark"],
    )
    assert "Wake name: Mark" in prompt
    assert "Mark or Marc" in prompt
    assert "Verstappen" in prompt


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
