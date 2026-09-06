"""Android implementation of the sounddevice subset Your Pit Box uses.

The desktop build opens the microphone and speaker through the sounddevice
package (PortAudio), which has no Android build. On Android this module is
found first on the Python path and provides the same names on top of the
platform's AudioRecord and AudioTrack, so `pitwall` runs unchanged:

    sd.InputStream(samplerate, channels=1, dtype="int16", blocksize, device,
                   callback)  .start() .stop() .close()
    sd.RawOutputStream(samplerate, channels=1, dtype="int16", blocksize=0)
                   .start() .write(bytes) .stop() .close()
    sd.play(data, samplerate)  sd.wait()  sd.stop()

Only 16-bit mono is supported, which is all the backend asks for.
distribution/tests/test_android_project.py fails if the backend starts
using any name this module does not provide.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from typing import Any

import numpy as np
from java import jarray, jbyte, jclass, jshort

AudioFormat = jclass("android.media.AudioFormat")
AudioRecord = jclass("android.media.AudioRecord")
AudioTrack = jclass("android.media.AudioTrack")
AudioAttributes = jclass("android.media.AudioAttributes")
AudioSource = jclass("android.media.MediaRecorder$AudioSource")

ENCODING = AudioFormat.ENCODING_PCM_16BIT
STATE_INITIALIZED = 1  # AudioRecord.STATE_INITIALIZED == AudioTrack.STATE_INITIALIZED == 1


class PortAudioError(Exception):
    """Raised where sounddevice would raise, so callers' handlers still apply."""


def _check_mono_int16(channels: int, dtype: str) -> None:
    if int(channels) != 1 or str(dtype) != "int16":
        raise PortAudioError(f"Android audio supports mono int16 only, not {channels}ch {dtype}")


class InputStream:
    """Microphone capture delivering int16 blocks to a callback on a thread."""

    def __init__(
        self,
        samplerate: int = 16_000,
        channels: int = 1,
        dtype: str = "int16",
        blocksize: int = 0,
        device: Any = None,
        callback: Callable[..., None] | None = None,
        **_: Any,
    ) -> None:
        del device  # Android routes input itself; the desktop device index is meaningless here.
        _check_mono_int16(channels, dtype)
        if callback is None:
            raise PortAudioError("InputStream needs a callback")
        self.samplerate = int(samplerate)
        self.blocksize = int(blocksize) or max(160, self.samplerate // 50)
        self.callback = callback
        self.active = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        minimum = AudioRecord.getMinBufferSize(self.samplerate, AudioFormat.CHANNEL_IN_MONO, ENCODING)
        if minimum <= 0:
            raise PortAudioError(f"No microphone supports {self.samplerate} Hz mono")
        buffer_bytes = max(int(minimum), self.blocksize * 2 * 4)
        self._record = AudioRecord(
            AudioSource.VOICE_RECOGNITION,
            self.samplerate,
            AudioFormat.CHANNEL_IN_MONO,
            ENCODING,
            buffer_bytes,
        )
        if self._record.getState() != STATE_INITIALIZED:
            self._record.release()
            raise PortAudioError(
                "The microphone could not be opened. Allow the microphone for Your Pit Box "
                "in Android's app settings and restart the app."
            )

    def start(self) -> None:
        if self.active:
            return
        self._stop.clear()
        self._record.startRecording()
        self.active = True
        self._thread = threading.Thread(target=self._pump, name="pitbox-microphone", daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        buffer = jarray(jshort)(self.blocksize)
        while not self._stop.is_set():
            count = self._record.read(buffer, 0, self.blocksize)
            if count <= 0:
                if count < 0:
                    # ERROR_INVALID_OPERATION / ERROR_BAD_VALUE: report once per
                    # block the way PortAudio reports a status flag, then keep going.
                    self._deliver(np.zeros((0, 1), dtype=np.int16), 0, f"read error {count}")
                time.sleep(0.005)
                continue
            block = np.array(buffer, dtype=np.int16)[:count].reshape(-1, 1)
            self._deliver(block, count, None)

    def _deliver(self, block: np.ndarray, frames: int, status: str | None) -> None:
        # A callback error must not kill the capture thread; the backend logs
        # from its own side.
        with contextlib.suppress(Exception):
            self.callback(block, frames, None, status)

    def stop(self) -> None:
        if not self.active:
            return
        self._stop.set()
        self.active = False
        with contextlib.suppress(Exception):
            self._record.stop()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        self._thread = None

    def close(self) -> None:
        self.stop()
        with contextlib.suppress(Exception):
            self._record.release()

    def __enter__(self) -> InputStream:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class RawOutputStream:
    """Speaker output taking raw int16 PCM bytes, blocking on write like PortAudio."""

    def __init__(
        self,
        samplerate: int = 24_000,
        channels: int = 1,
        dtype: str = "int16",
        blocksize: int = 0,
        device: Any = None,
        **_: Any,
    ) -> None:
        del blocksize, device
        _check_mono_int16(channels, dtype)
        self.samplerate = int(samplerate)
        self.active = False
        self._written_frames = 0
        minimum = AudioTrack.getMinBufferSize(self.samplerate, AudioFormat.CHANNEL_OUT_MONO, ENCODING)
        if minimum <= 0:
            raise PortAudioError(f"No output supports {self.samplerate} Hz mono")
        # Half a second of buffer: enough to ride out a scheduling hiccup while
        # the engineer speaks, small enough that stop() feels immediate.
        buffer_bytes = max(int(minimum), self.samplerate)
        attributes = (
            AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_MEDIA)
            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
            .build()
        )
        audio_format = (
            AudioFormat.Builder()
            .setEncoding(ENCODING)
            .setSampleRate(self.samplerate)
            .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
            .build()
        )
        self._track = (
            AudioTrack.Builder()
            .setAudioAttributes(attributes)
            .setAudioFormat(audio_format)
            .setBufferSizeInBytes(buffer_bytes)
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()
        )
        if self._track.getState() != STATE_INITIALIZED:
            self._track.release()
            raise PortAudioError("The audio output could not be opened")

    def start(self) -> None:
        if not self.active:
            self._track.play()
            self.active = True

    def write(self, data: bytes | bytearray | memoryview) -> None:
        payload = bytes(data)
        usable = len(payload) - (len(payload) % 2)
        if usable <= 0:
            return
        chunk = jarray(jbyte)(payload[:usable])
        offset = 0
        while offset < usable and self.active:
            written = self._track.write(chunk, offset, usable - offset)
            if written < 0:
                raise PortAudioError(f"AudioTrack write failed ({written})")
            if written == 0:
                time.sleep(0.005)
                continue
            offset += written
        self._written_frames += usable // 2

    def played_frames(self) -> int:
        try:
            return int(self._track.getPlaybackHeadPosition())
        except Exception:  # noqa: BLE001 - a released track reports nothing; treat as fully played
            return self._written_frames

    def drain(self, timeout_s: float) -> None:
        """Block until everything written has been heard, or the timeout passes."""
        deadline = time.monotonic() + max(0.0, timeout_s)
        while self.active and self.played_frames() < self._written_frames:
            if time.monotonic() >= deadline:
                return
            time.sleep(0.02)

    def stop(self) -> None:
        if not self.active:
            return
        self.active = False
        with contextlib.suppress(Exception):
            self._track.pause()
            self._track.flush()

    def close(self) -> None:
        self.stop()
        with contextlib.suppress(Exception):
            self._track.stop()
        with contextlib.suppress(Exception):
            self._track.release()

    def __enter__(self) -> RawOutputStream:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _to_pcm16(data: Any) -> bytes:
    array = np.asarray(data)
    if array.ndim == 2:
        array = array[:, 0]
    if array.dtype.kind == "f":
        array = np.clip(array, -1.0, 1.0) * 32767.0
    return array.astype("<i2", copy=False).tobytes()


class _Playback:
    def __init__(self, pcm: bytes, samplerate: int) -> None:
        self.stream = RawOutputStream(samplerate=samplerate)
        self.pcm = pcm
        self.samplerate = samplerate
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._run, name="pitbox-playback", daemon=True)

    def _run(self) -> None:
        try:
            with contextlib.suppress(Exception):
                self.stream.start()
                self.stream.write(self.pcm)
                self.stream.drain(len(self.pcm) / 2 / self.samplerate + 1.0)
        finally:
            self.stream.close()
            self.done.set()

    def cancel(self) -> None:
        self.stream.stop()


_lock = threading.Lock()
_current: _Playback | None = None


def play(data: Any, samplerate: int | None = None, **_: Any) -> None:
    """Start playing an array, like sounddevice.play; wait() blocks until done."""
    global _current
    rate = int(samplerate or 44_100)
    pcm = _to_pcm16(data)
    stop()
    playback = _Playback(pcm, rate)
    with _lock:
        _current = playback
    playback.thread.start()


def wait(**_: Any) -> None:
    with _lock:
        playback = _current
    if playback is not None:
        playback.done.wait()


def stop(**_: Any) -> None:
    global _current
    with _lock:
        playback, _current = _current, None
    if playback is not None:
        playback.cancel()
        playback.done.wait(timeout=2.0)


__all__ = ["InputStream", "PortAudioError", "RawOutputStream", "play", "stop", "wait"]
