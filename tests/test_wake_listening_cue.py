"""Two ways to talk to the engineer, and only one of them needs a ping.

  "Mark, what's the gap"  -> answered directly; the answer is the confirmation.
  "Mark" ... <pause>      -> nothing comes back, so the driver needs to hear
                             that the phrase landed and a command is awaited.

The cue existed but was 80 ms at 10% volume, inaudible next to a racing game
through headphones, so in practice a driver who paused got no confirmation and
had no idea whether to start talking.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest

from pitwall.audio import AudioService
from pitwall.config import settings
from pitwall.voice import NativeVoiceController


class _Recorder(AudioService):
    """Captures what would be played without touching a sound device."""

    def __init__(self) -> None:  # noqa: D107 - deliberately skips AudioService.__init__
        self.tones: list[tuple[float, float, float]] = []

    async def play_tone(self, frequency_hz=880.0, duration_s=0.08, volume=0.10):
        self.tones.append((frequency_hz, duration_s, volume))


# ---------------------------------------------------------------------------
# The cue itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_listening_cue_is_a_rising_pair() -> None:
    audio = _Recorder()
    await AudioService.play_listening_cue(audio)
    assert len(audio.tones) == 2, audio.tones
    first, second = audio.tones
    # Rising reads as a question. Falling and flat are taken by the other acks.
    assert second[0] > first[0]


@pytest.mark.asyncio
async def test_it_is_loud_enough_to_hear_over_a_race() -> None:
    audio = _Recorder()
    await AudioService.play_listening_cue(audio)
    # The old default was 0.08 s at 0.10 volume, which is what made it useless.
    for _, duration, volume in audio.tones:
        assert volume > 0.10, "no louder than the cue nobody could hear"
        assert duration > 0.08, "no longer than the cue nobody could hear"


@pytest.mark.asyncio
async def test_it_respects_the_ack_setting(monkeypatch) -> None:
    # A driver who turned acknowledgement sounds off must not get this one.
    monkeypatch.setattr(settings, "voice_ack_enabled", False)
    audio = _Recorder()
    await AudioService.play_listening_cue(audio)
    assert audio.tones == []


# ---------------------------------------------------------------------------
# Which utterances actually trigger it
# ---------------------------------------------------------------------------


class _Store:
    def __init__(self) -> None:
        self.fields: dict = {}

    async def update(self, **fields) -> None:
        self.fields.update(fields)

    async def snapshot_live(self) -> dict:
        return {"drivers": [], "wake_trigger_count": 0}


def _controller(transcript: str, audio: _Recorder):
    """A controller wired up just far enough to run the wake pipeline."""
    voice = object.__new__(NativeVoiceController)
    voice.store = _Store()
    voice.audio = audio
    voice.realtime = None
    voice.loop = asyncio.get_running_loop()
    voice._signal_pressed = False
    voice._tts_playing = False
    voice._interaction_finalized_at = 0.0
    voice._wake_armed_until = 0.0
    voice._wake_cooldown_until = 0.0
    voice._wake_effective_threshold = 0.0
    voice._wake_arm_task = None
    voice._wake_process_task = None
    voice.busy = False

    async def transcribe(*_args, **_kwargs):
        return transcript

    async def mark_latency(*_args, **_kwargs):
        return None

    audio.transcribe = transcribe
    voice._mark_latency = mark_latency
    voice._write_wav = lambda *_a, **_k: None

    accepted: list[str] = []

    async def accept(command, phrase):
        accepted.append(command)

    voice._accept_wake = accept
    return voice, accepted


def _loud_clip() -> list[np.ndarray]:
    """Two seconds of noise: long enough and loud enough to reach the parser."""
    samples = int(settings.audio_sample_rate * 2.0)
    return [np.full(samples, 8000, dtype=np.int16)]


@pytest.mark.asyncio
async def test_the_phrase_alone_pings(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    audio = _Recorder()
    voice, accepted = _controller("Mark.", audio)

    await voice._process_wake_candidate(_loud_clip(), "test")

    assert audio.tones, "said the wake phrase, paused, and heard nothing back"
    assert voice._wake_armed_until > 0.0, "should be waiting for the command"
    assert accepted == [], "there was no command to act on"


@pytest.mark.asyncio
async def test_the_phrase_with_a_command_is_unchanged(tmp_path, monkeypatch) -> None:
    """The half that already worked must not grow a ping in front of it.

    The answer is the acknowledgement here. A tone before every reply would be
    noise, and worse, it would train the driver to wait for a beep that carries
    no information.
    """
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    audio = _Recorder()
    voice, accepted = _controller("Mark, what's the gap to Norris?", audio)

    await voice._process_wake_candidate(_loud_clip(), "test")

    assert accepted == ["what's the gap to Norris"]
    assert audio.tones == [], "inline commands are answered, not pinged"
    assert voice._wake_armed_until == 0.0, "no arming when a command arrived"


@pytest.mark.asyncio
async def test_the_follow_up_command_is_not_pinged_again(tmp_path, monkeypatch) -> None:
    # Already armed and already pinged; the follow-up just gets answered.
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    audio = _Recorder()
    voice, accepted = _controller("What's the gap to Norris?", audio)
    voice._wake_armed_until = float("inf")

    await voice._process_wake_candidate(_loud_clip(), "test")

    assert accepted == ["What's the gap to Norris?"]
    assert audio.tones == []


@pytest.mark.asyncio
async def test_speech_that_is_not_the_wake_phrase_is_silent(tmp_path, monkeypatch) -> None:
    # Talking to someone else in the room must never make the app beep.
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    audio = _Recorder()
    voice, accepted = _controller("the commentator said Mark was quick", audio)

    rejected: list[str] = []

    async def reject(reason, transcript):
        rejected.append(reason)

    voice._reject_wake = reject

    await voice._process_wake_candidate(_loud_clip(), "test")

    assert audio.tones == []
    assert accepted == []
    assert rejected, "should have been rejected, not silently swallowed"
