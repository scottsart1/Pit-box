"""The Android audio layer, driven against fake AudioRecord and AudioTrack.

android/app/src/main/python/sounddevice.py and soundfile.py stand in for
the desktop packages on the phone. The Java classes cannot run here, so a
fake `java` module supplies just enough of them to exercise the Python side:
block shapes, threading, the blocking write, drain, stop, and the WAV reader.
"""

from __future__ import annotations

import array
import importlib
import sys
import threading
import time
import types
import wave
from pathlib import Path
from typing import ClassVar

import numpy as np
import pytest

ANDROID_PYTHON = Path(__file__).resolve().parents[2] / "android" / "app" / "src" / "main" / "python"


class _FakeRecord:
    """AudioRecord: hands out a ramp of samples, then returns 0 (no data)."""

    instances: ClassVar[list[_FakeRecord]] = []
    samples_available = 1000

    def __init__(self, source, rate, channel, encoding, buffer_bytes):
        self.args = (source, rate, channel, encoding, buffer_bytes)
        self.cursor = 0
        self.recording = False
        self.released = False
        _FakeRecord.instances.append(self)

    @staticmethod
    def getMinBufferSize(rate, channel, encoding):
        return 3200

    def getState(self):
        return 1

    def startRecording(self):
        self.recording = True

    def read(self, buffer, offset, size):
        if not self.recording:
            return -3
        remaining = _FakeRecord.samples_available - self.cursor
        count = min(size, remaining)
        if count <= 0:
            time.sleep(0.002)
            return 0
        for i in range(count):
            buffer[offset + i] = (self.cursor + i) % 32000
        self.cursor += count
        return count

    def stop(self):
        self.recording = False

    def release(self):
        self.released = True


class _FakeTrack:
    """AudioTrack in stream mode: remembers what was written and 'plays' it."""

    instances: ClassVar[list[_FakeTrack]] = []

    def __init__(self):
        self.written = bytearray()
        self.playing = False
        self.paused = False
        self.flushed = False
        self.released = False
        _FakeTrack.instances.append(self)

    @staticmethod
    def getMinBufferSize(rate, channel, encoding):
        return 4096

    def getState(self):
        return 1

    def play(self):
        self.playing = True

    def write(self, data, offset, size):
        self.written += bytes(data[offset:offset + size])
        return size

    def getPlaybackHeadPosition(self):
        return len(self.written) // 2

    def pause(self):
        self.paused = True

    def flush(self):
        self.flushed = True

    def stop(self):
        self.playing = False

    def release(self):
        self.released = True


class _Builder:
    def __init__(self, product=None):
        self.product = product

    def __getattr__(self, name):
        if name.startswith("set"):
            return lambda *args: self
        raise AttributeError(name)

    def build(self):
        return self.product() if callable(self.product) else object()


class _TrackClass:
    STATE_INITIALIZED = 1
    MODE_STREAM = 1
    getMinBufferSize = staticmethod(_FakeTrack.getMinBufferSize)
    Builder = staticmethod(lambda: _Builder(_FakeTrack))


class _RecordClass(_FakeRecord):
    STATE_INITIALIZED = 1


_CLASSES = {
    "android.media.AudioFormat": types.SimpleNamespace(
        ENCODING_PCM_16BIT=2, CHANNEL_IN_MONO=16, CHANNEL_OUT_MONO=4, Builder=lambda: _Builder()
    ),
    "android.media.AudioRecord": _RecordClass,
    "android.media.AudioTrack": _TrackClass,
    "android.media.AudioAttributes": types.SimpleNamespace(
        USAGE_MEDIA=1, CONTENT_TYPE_SPEECH=1, Builder=lambda: _Builder()
    ),
    "android.media.MediaRecorder$AudioSource": types.SimpleNamespace(VOICE_RECOGNITION=6),
}


def _jarray(kind):
    def factory(spec):
        if isinstance(spec, int):
            return array.array(kind, [0] * spec)
        return array.array(kind, spec) if kind != "b" else bytearray(spec)
    return factory


@pytest.fixture
def android_audio(monkeypatch):
    fake_java = types.ModuleType("java")
    fake_java.jclass = lambda name: _CLASSES[name]
    fake_java.jshort = "h"
    fake_java.jbyte = "b"
    fake_java.jarray = _jarray
    monkeypatch.setitem(sys.modules, "java", fake_java)
    monkeypatch.syspath_prepend(str(ANDROID_PYTHON))
    for name in ("sounddevice", "soundfile"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    _FakeRecord.instances.clear()
    _FakeTrack.instances.clear()
    _FakeRecord.samples_available = 1000
    sd = importlib.import_module("sounddevice")
    sf = importlib.import_module("soundfile")
    assert sd.__file__.startswith(str(ANDROID_PYTHON)), "the Android stand-in must be the one imported"
    yield sd, sf
    for name in ("sounddevice", "soundfile"):
        monkeypatch.delitem(sys.modules, name, raising=False)


def test_the_microphone_delivers_int16_column_blocks_to_the_callback(android_audio):
    sd, _ = android_audio
    blocks: list[np.ndarray] = []
    got_all = threading.Event()

    def callback(indata, frames, time_info, status):
        blocks.append(indata.copy())
        if sum(len(b) for b in blocks) >= 1000:
            got_all.set()

    stream = sd.InputStream(samplerate=16000, channels=1, dtype="int16", blocksize=320, callback=callback)
    stream.start()
    assert got_all.wait(2.0), "capture thread never delivered the samples"
    stream.stop()
    stream.close()

    assert all(b.dtype == np.int16 and b.ndim == 2 and b.shape[1] == 1 for b in blocks)
    joined = np.concatenate(blocks)[:, 0]
    assert joined.tolist() == [i % 32000 for i in range(1000)]
    record = _FakeRecord.instances[-1]
    assert record.args[1] == 16000 and record.released and not record.recording


def test_an_unopenable_microphone_raises_where_sounddevice_would(android_audio, monkeypatch):
    sd, _ = android_audio
    monkeypatch.setattr(_RecordClass, "getState", lambda self: 0)
    with pytest.raises(sd.PortAudioError):
        sd.InputStream(samplerate=16000, blocksize=320, callback=lambda *a: None)


def test_only_mono_int16_is_accepted(android_audio):
    sd, _ = android_audio
    with pytest.raises(sd.PortAudioError):
        sd.RawOutputStream(samplerate=24000, channels=2)
    with pytest.raises(sd.PortAudioError):
        sd.InputStream(samplerate=16000, dtype="float32", callback=lambda *a: None)


def test_raw_output_writes_whole_samples_and_drains(android_audio):
    sd, _ = android_audio
    stream = sd.RawOutputStream(samplerate=24000, channels=1, dtype="int16", blocksize=0)
    stream.start()
    stream.write(b"\x01\x00\x02\x00\x03")  # a trailing odd byte is not a sample
    stream.write(bytearray(b"\x04\x00"))
    stream.drain(1.0)
    stream.stop()
    stream.close()
    track = _FakeTrack.instances[-1]
    assert bytes(track.written) == b"\x01\x00\x02\x00\x04\x00"
    assert track.paused and track.flushed and track.released


def test_play_converts_floats_and_wait_blocks_until_heard(android_audio):
    sd, _ = android_audio
    tone = np.array([0.0, 0.5, -0.5, 1.0, -1.0], dtype=np.float32)
    sd.play(tone, 16000)
    sd.wait()
    track = _FakeTrack.instances[-1]
    samples = np.frombuffer(bytes(track.written), dtype="<i2").tolist()
    assert samples == [0, 16383, -16383, 32767, -32767]
    assert track.released


def test_stop_cancels_a_playback_in_progress(android_audio):
    sd, _ = android_audio
    sd.play(np.zeros(16000 * 5, dtype=np.int16), 16000)
    sd.stop()
    track = _FakeTrack.instances[-1]
    assert track.released and not track.playing
    sd.wait()  # nothing left to wait for


def test_stream_stop_plays_queued_speech_before_flushing(android_audio, monkeypatch):
    sd, _ = android_audio
    stream = sd.RawOutputStream(samplerate=24000)
    track = _FakeTrack.instances[-1]
    head = [0]
    waiting = threading.Event()

    def playback_head():
        waiting.set()
        return head[0]

    monkeypatch.setattr(track, "getPlaybackHeadPosition", playback_head)
    stream.start()
    stream.write(b"\x01\x00" * 240)
    stopper = threading.Thread(target=stream.stop)
    stopper.start()
    try:
        assert waiting.wait(1), "stop never checked whether queued speech was heard"
        assert stopper.is_alive(), "stop discarded the speech still queued in AudioTrack"
        assert not track.paused and not track.flushed
        head[0] = 240
        stopper.join(1)
        assert not stopper.is_alive(), "stop did not finish after queued speech played"
        assert track.paused and track.flushed
    finally:
        stream.abort()
        stopper.join(2)
        stream.close()


@pytest.mark.parametrize("method", ["abort", "close"])
def test_cancel_and_close_discard_queued_speech_without_waiting(android_audio, monkeypatch, method):
    sd, _ = android_audio
    stream = sd.RawOutputStream(samplerate=24000)
    track = _FakeTrack.instances[-1]
    monkeypatch.setattr(track, "getPlaybackHeadPosition", lambda: 0)
    stream.start()
    stream.write(b"\x01\x00" * 240)
    monkeypatch.setattr(stream, "drain", lambda *a: pytest.fail("cancel must not drain pending speech"))
    getattr(stream, method)()
    assert not stream.active and track.paused and track.flushed
    stream.close()


def test_cancel_before_playback_thread_starts_never_plays_stale_speech(android_audio):
    sd, _ = android_audio
    playback = sd._Playback(b"\x01\x00" * 240, 24000)
    playback.cancel()
    playback.thread.start()
    assert playback.done.wait(1)
    track = _FakeTrack.instances[-1]
    assert not track.written, "cancelled speech started after the cancellation"
    assert track.released


def test_only_accepted_audio_frames_are_counted_when_a_write_fails(android_audio, monkeypatch):
    sd, _ = android_audio
    stream = sd.RawOutputStream(samplerate=24000)
    track = _FakeTrack.instances[-1]
    results = iter([2, -6])
    monkeypatch.setattr(track, "write", lambda *a: next(results))
    stream.start()
    try:
        with pytest.raises(sd.PortAudioError, match="write failed"):
            stream.write(b"\x01\x00" * 240)
        assert stream._written_frames == 1
    finally:
        stream.close()


def test_playback_head_uses_android_unsigned_frame_counter(android_audio, monkeypatch):
    sd, _ = android_audio
    stream = sd.RawOutputStream()
    monkeypatch.setattr(_FakeTrack.instances[-1], "getPlaybackHeadPosition", lambda: -2147483648)
    try:
        assert stream.played_frames() == 2147483648
    finally:
        stream.close()


def test_soundfile_read_returns_float32_mono_and_the_rate(android_audio, tmp_path):
    _, sf = android_audio
    path = tmp_path / "ack.wav"
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(24000)
        out.writeframes(np.array([0, 16384, -32768], dtype="<i2").tobytes())
    data, rate = sf.read(path, dtype="float32")
    assert rate == 24000
    assert data.dtype == np.float32 and data.ndim == 1
    assert data.tolist() == pytest.approx([0.0, 0.5, -1.0])
