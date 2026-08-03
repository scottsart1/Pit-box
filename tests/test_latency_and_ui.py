from pathlib import Path

import pytest

from pitwall.audio import AudioService
from pitwall.brain import EngineerBrain
from pitwall.config import settings
from pitwall.setup_model import track_name
from pitwall.state import StateStore
from pitwall.voice import NativeVoiceController


class _Brain:
    @staticmethod
    def classify_request(text: str) -> str:
        return EngineerBrain.classify_request(text)

    async def ask(self, text: str) -> str:
        return "Copy."


class _Audio:
    def stop_playback(self) -> None:
        pass


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("What is my target lap?", "fast"),
        ("Why am I slower in sector two?", "normal"),
        ("Where am I losing time in that corner?", "normal"),
        ("Should we pit now under the safety car?", "deep"),
        ("Compare the medium-hard and medium-soft strategy", "deep"),
    ],
)
def test_request_routing_reserves_deep_reasoning_for_planning(
    utterance: str,
    expected: str,
) -> None:
    assert EngineerBrain.classify_request(utterance) == expected


def test_transcription_rejects_prompt_echo_and_repeated_wake_garbage() -> None:
    assert AudioService._looks_like_prompt_echo(
        "Formula One race radio: box, pit, undercut, overcut, manual override, "
        "ERS, degradation, safety car, mediums, hards. Drivers: Norris, Leclerc"
    )
    assert AudioService._looks_like_prompt_echo("Mark mark hey mark radio mark")
    assert not AudioService._looks_like_prompt_echo(
        "Mark, what is the gap ahead?"
    )


def test_dashboard_contains_radio_state_rail_and_latency_metrics() -> None:
    html = Path("static/index.html").read_text(encoding="utf-8")
    assert 'id="radioRail"' in html
    assert ".radio-rail.listening" in html
    assert ".radio-rail.processing" in html
    assert 'id="latStt"' in html
    assert 'id="latAudio"' in html


def test_setup_track_fallback_contains_spa_and_monza() -> None:
    assert track_name(10) == "Spa"
    assert track_name(11) == "Monza"


@pytest.mark.asyncio
async def test_wake_toggle_persists_across_controller_restart(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(settings, "native_voice", False)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "wake_enabled", True)

    first = NativeVoiceController(StateStore(), _Brain(), _Audio())  # type: ignore[arg-type]
    await first.initialize()
    await first.configure_wake(False)
    await first.shutdown()

    monkeypatch.setattr(settings, "wake_enabled", True)
    second_store = StateStore()
    second = NativeVoiceController(second_store, _Brain(), _Audio())  # type: ignore[arg-type]
    await second.initialize()
    state = await second_store.snapshot_live()
    assert state["wake_enabled"] is False
    assert state["wake_status"] == "disabled"
    await second.shutdown()


@pytest.mark.asyncio
async def test_legacy_saved_false_cannot_silently_disable_new_wake_build(
    monkeypatch,
    tmp_path,
) -> None:
    import json

    monkeypatch.setattr(settings, "native_voice", True)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "wake_enabled", True)
    (tmp_path / "ptt.json").write_text(
        json.dumps({"mask": 8, "wake_enabled": False}),
        encoding="utf-8",
    )

    controller = NativeVoiceController(StateStore(), _Brain(), _Audio())  # type: ignore[arg-type]

    async def no_stream() -> None:
        return None

    monkeypatch.setattr(controller, "_ensure_input_stream", no_stream)
    await controller.initialize()
    state = await controller.store.snapshot_live()
    assert controller.mask == 8
    assert settings.wake_enabled is True
    assert state["wake_enabled"] is True
    assert state["wake_config_source"] == "legacy saved setting ignored"
    assert "using .env" in state["wake_last_reason"]
    await controller.shutdown()


@pytest.mark.asyncio
async def test_wake_threshold_starts_sensitive_and_adapts_to_noise(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(settings, "native_voice", False)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "wake_min_speech_rms", 75.0)
    monkeypatch.setattr(settings, "wake_speech_rms", 180.0)
    monkeypatch.setattr(settings, "wake_noise_multiplier", 1.45)
    monkeypatch.setattr(settings, "wake_noise_margin_rms", 18.0)

    controller = NativeVoiceController(StateStore(), _Brain(), _Audio())  # type: ignore[arg-type]
    assert controller._effective_wake_threshold() == 75.0
    controller._wake_noise_rms = 60.0
    assert controller._effective_wake_threshold() == pytest.approx(105.0)
    controller._wake_noise_rms = 150.0
    assert controller._effective_wake_threshold() == 180.0
    await controller.shutdown()
