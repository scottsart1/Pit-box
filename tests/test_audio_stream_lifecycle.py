"""Streaming speech must finish audibly without blocking telemetry or cancellation."""

import asyncio
import sys
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pitwall.audio import AudioService


@pytest.fixture
def streaming_audio(monkeypatch):
    class Stream:
        def __init__(self, **kwargs):
            self.stopping = threading.Event()
            self.release = threading.Event()
            self.aborted = False
            self.closed = False
            self.timed_out = False
            self.stop_thread = None

        def start(self):
            pass

        def write(self, data):
            pass

        def stop(self):
            self.stop_thread = threading.get_ident()
            self.stopping.set()
            self.timed_out = not self.release.wait(2)

        def abort(self):
            self.aborted = True
            self.release.set()

        def close(self):
            self.closed = True

    class Response:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def iter_bytes(self, **kwargs):
            yield b"\x01\x00" * 240

    stream = Stream()
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(
        RawOutputStream=lambda **kwargs: stream, stop=lambda: None,
    ))
    monkeypatch.setattr(AudioService, "rebind_client", lambda self: None)
    audio = AudioService()
    audio.client = SimpleNamespace(audio=SimpleNamespace(speech=SimpleNamespace(
        with_streaming_response=SimpleNamespace(create=lambda **kwargs: Response()),
    )))
    audio.synthesize = AsyncMock(side_effect=AssertionError("cancelled speech must not synthesize again"))
    return audio, stream


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ["heard", "stop", "cancel"])
async def test_stream_drain_keeps_loop_live_and_can_be_interrupted(streaming_audio, finish):
    audio, stream = streaming_audio
    event_loop_thread = threading.get_ident()
    task = asyncio.create_task(audio.stream_speech("Box this lap"))
    try:
        assert await asyncio.to_thread(stream.stopping.wait, 1), "stream did not reach its queued audio"
        assert stream.stop_thread != event_loop_thread, "audio drain blocked the telemetry event loop"
        assert not task.done() and not stream.closed
        if finish == "heard":
            stream.release.set()
            assert await asyncio.wait_for(task, 1)
            assert not stream.aborted
        elif finish == "stop":
            audio.stop_playback()
            assert stream.aborted, "Stop did not immediately discard the queued speech"
            assert not await asyncio.wait_for(task, 1)
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
            assert stream.aborted, "task cancellation let the queued speech finish"
        assert stream.closed and not stream.timed_out
        assert audio._streaming_output is None
        audio.synthesize.assert_not_awaited()
    finally:
        stream.release.set()
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


@pytest.mark.asyncio
async def test_cancel_during_network_wait_aborts_without_draining(streaming_audio):
    audio, stream = streaming_audio
    waiting = asyncio.Event()

    class Response:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def iter_bytes(self, **kwargs):
            yield b"\x01\x00" * 240
            waiting.set()
            await asyncio.Event().wait()

    audio.client.audio.speech.with_streaming_response.create = lambda **kwargs: Response()
    task = asyncio.create_task(audio.stream_speech("Box this lap"))
    await asyncio.wait_for(waiting.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert stream.aborted and stream.closed
    assert not stream.stopping.is_set(), "cancelled response must never drain"
    assert audio._streaming_output is None
    audio.synthesize.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_native_start_releases_the_stream_before_file_fallback(streaming_audio):
    audio, stream = streaming_audio

    def fail_start():
        raise RuntimeError("audio device unavailable")

    stream.start = fail_start
    audio.synthesize = AsyncMock()
    audio._play_wav_unlocked = AsyncMock()
    assert await audio.stream_speech("Box this lap")
    assert stream.aborted and stream.closed
    assert audio._streaming_output is None
    audio.synthesize.assert_awaited_once()
    audio._play_wav_unlocked.assert_awaited_once()
