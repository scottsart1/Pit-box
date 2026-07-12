from __future__ import annotations

import asyncio
import contextlib
import inspect
import re
import wave
from pathlib import Path
from typing import Any, Awaitable, Callable

from openai import AsyncOpenAI

from .config import settings


RACING_VOCAB = (
    "box",
    "pit",
    "undercut",
    "overcut",
    "manual override",
    "overtake",
    "active aero",
    "ers",
    "degradation",
    "safety car",
    "virtual safety car",
    "mediums",
    "hards",
    "inters",
    "wets",
)

FirstAudioCallback = Callable[[], Awaitable[None] | None]


class AudioService:
    """OpenAI STT/TTS plus low-latency local playback helpers.

    Native engineer speech uses raw 24 kHz PCM and starts playback as soon as
    the first network chunk arrives. File synthesis is retained for browser
    playback, acknowledgement caching, and compatibility fallbacks.
    """

    def __init__(self) -> None:
        self.client = (
            AsyncOpenAI(
                api_key=settings.api_key,
                timeout=settings.openai_timeout_s,
                max_retries=2,
            )
            if settings.api_key
            else None
        )
        self.output_lock = asyncio.Lock()
        self._stop_requested = False
        self._ack_paths = {
            "copy": settings.data_dir / f"ack-copy-{settings.voice}.wav",
            "standby": settings.data_dir / f"ack-standby-{settings.voice}.wav",
        }

    @staticmethod
    def _looks_like_prompt_echo(text: str) -> bool:
        lowered = re.sub(r"\s+", " ", text.strip().lower())
        hits = sum(term in lowered for term in RACING_VOCAB)
        words = re.findall(r"[a-z]+", lowered)
        wake_noise = {
            "mark",
            "marc",
            "hey",
            "radio",
            "uh",
            "um",
            "hmm",
            "the",
            "a",
        }
        repeated_wake_garbage = bool(
            len(words) >= 5
            and set(words).issubset(wake_noise)
            and sum(word in {"mark", "marc"} for word in words) >= 2
        )
        return (
            lowered.startswith("formula one race radio")
            or lowered.startswith("f1 race radio vocabulary")
            or (hits >= 8 and len(lowered) > 100 and "drivers:" in lowered)
            or repeated_wake_garbage
        )

    @staticmethod
    def extract_wake_command(
        text: str,
        phrases: list[str],
    ) -> tuple[bool, str, str]:
        """Match a wake phrase only at the beginning of a transcript."""
        original = re.sub(r"\s+", " ", text.strip())
        if not original:
            return False, "", ""

        for phrase in sorted(phrases, key=len, reverse=True):
            words = [re.escape(part) for part in phrase.strip().split() if part]
            if not words:
                continue
            prefix = r"\s+".join(words)
            match = re.match(
                rf"^\s*{prefix}\b[\s,.:;!?—–-]*(.*)$",
                original,
                flags=re.IGNORECASE,
            )
            if match:
                command = match.group(1).strip(" \t,.:;!?—–-")
                return True, command, phrase

        return False, "", ""

    async def transcribe(
        self,
        path: Path,
        driver_names: list[str] | None = None,
    ) -> str:
        if self.client is None:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        names = ", ".join(
            name
            for name in (driver_names or [])[:20]
            if name and name != "Unknown"
        )
        # Keep steering concise. Long prose prompts are more likely to leak into
        # the transcript when a clip contains only breath or background audio.
        prompt = "Keywords: " + ", ".join(RACING_VOCAB)
        if names:
            prompt += f". Drivers: {names}"
        with path.open("rb") as audio_file:
            result: Any = await self.client.audio.transcriptions.create(
                model=settings.stt_model,
                file=audio_file,
                response_format="text",
                prompt=prompt,
                language="en",
            )
        text = (
            result.strip()
            if isinstance(result, str)
            else str(getattr(result, "text", result)).strip()
        )
        if self._looks_like_prompt_echo(text):
            return ""
        return text

    async def synthesize(
        self,
        text: str,
        target: Path,
        response_format: str = "wav",
    ) -> Path:
        if self.client is None:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        target.parent.mkdir(parents=True, exist_ok=True)
        async with self.client.audio.speech.with_streaming_response.create(
            model=settings.tts_model,
            voice=settings.voice,
            input=text,
            instructions=(
                "Calm professional motorsport race engineer. Concise team-radio "
                "delivery, controlled urgency, no theatrical accent imitation."
            ),
            response_format=response_format,
        ) as response:
            await response.stream_to_file(target)
        return target

    async def prepare_acknowledgements(self) -> None:
        """Create tiny reusable acknowledgements once; failure is non-fatal."""
        if self.client is None or not settings.voice_ack_enabled:
            return
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        phrases = {
            "copy": "Copy.",
            "standby": "Copy, stand by.",
        }
        for name, phrase in phrases.items():
            target = self._ack_paths[name]
            if target.exists() and target.stat().st_size > 128:
                continue
            try:
                await self.synthesize(phrase, target, "wav")
            except Exception:
                # Acknowledgements are an experience enhancement, never a
                # startup dependency.
                return

    @staticmethod
    async def _call_first_audio(callback: FirstAudioCallback | None) -> None:
        if callback is None:
            return
        result = callback()
        if inspect.isawaitable(result):
            await result

    async def stream_speech(
        self,
        text: str,
        *,
        target: Path | None = None,
        on_first_audio: FirstAudioCallback | None = None,
    ) -> bool:
        """Stream raw PCM directly to the Windows audio device.

        The Speech API PCM format is 24 kHz, signed 16-bit little-endian mono.
        A WAV copy is written after playback so the dashboard's latest-audio
        endpoint continues to work.
        """
        if self.client is None:
            raise RuntimeError("OPENAI_API_KEY is not configured")

        async with self.output_lock:
            self._stop_requested = False
            chunks: list[bytes] = []
            first = True
            try:
                import sounddevice as sd

                stream = sd.RawOutputStream(
                    samplerate=24_000,
                    channels=1,
                    dtype="int16",
                    blocksize=0,
                    device=None,
                )
                stream.start()
                try:
                    remainder = b""
                    async with self.client.audio.speech.with_streaming_response.create(
                        model=settings.tts_model,
                        voice=settings.voice,
                        input=text,
                        instructions=(
                            "Calm professional motorsport race engineer. Concise "
                            "team-radio delivery, controlled urgency, no theatrical "
                            "accent imitation."
                        ),
                        response_format="pcm",
                    ) as response:
                        async for chunk in response.iter_bytes(chunk_size=4096):
                            if self._stop_requested:
                                break
                            if not chunk:
                                continue
                            raw = remainder + bytes(chunk)
                            usable_length = len(raw) - (len(raw) % 2)
                            playable = raw[:usable_length]
                            remainder = raw[usable_length:]
                            if not playable:
                                continue
                            if first:
                                first = False
                                await self._call_first_audio(on_first_audio)
                            chunks.append(playable)
                            await asyncio.to_thread(stream.write, playable)
                finally:
                    with contextlib.suppress(Exception):
                        stream.stop()
                    with contextlib.suppress(Exception):
                        stream.close()
            except Exception:
                # Preserve the working v3.1 behavior on unusual audio devices or
                # SDK versions while still using low-latency streaming normally.
                fallback = target or (settings.data_dir / "latest_engineer.wav")
                await self.synthesize(text, fallback, "wav")
                if first:
                    first = False
                    await self._call_first_audio(on_first_audio)
                await self._play_wav_unlocked(fallback)
                return not self._stop_requested

            if target is not None and chunks:
                target.parent.mkdir(parents=True, exist_ok=True)
                with wave.open(str(target), "wb") as output:
                    output.setnchannels(1)
                    output.setsampwidth(2)
                    output.setframerate(24_000)
                    output.writeframes(b"".join(chunks))
            return not self._stop_requested and bool(chunks)

    @staticmethod
    async def _play_wav_unlocked(path: Path) -> None:
        def play() -> None:
            import sounddevice as sd
            import soundfile as sf

            data, sample_rate = sf.read(path, dtype="float32")
            sd.play(data, sample_rate)
            sd.wait()

        await asyncio.to_thread(play)

    async def play_wav(self, path: Path) -> None:
        async with self.output_lock:
            self._stop_requested = False
            await self._play_wav_unlocked(path)

    async def play_ack(self, kind: str = "copy") -> None:
        if not settings.voice_ack_enabled:
            return
        target = self._ack_paths.get(kind, self._ack_paths["copy"])
        if target.exists() and target.stat().st_size > 128:
            await self.play_wav(target)
            return
        await self.play_tone(
            frequency_hz=780.0 if kind == "standby" else 920.0,
            duration_s=0.10,
            volume=0.11,
        )

    async def play_tone(
        self,
        frequency_hz: float = 880.0,
        duration_s: float = 0.08,
        volume: float = 0.10,
    ) -> None:
        """Play a short local acknowledgement without using the API."""

        def play() -> None:
            import numpy as np
            import sounddevice as sd

            sample_rate = 16_000
            samples = max(1, int(sample_rate * duration_s))
            times = np.arange(samples, dtype=np.float32) / sample_rate
            envelope = np.minimum(
                1.0, np.arange(samples) / max(1, samples * 0.15)
            )
            envelope *= np.minimum(
                1.0, np.arange(samples)[::-1] / max(1, samples * 0.20)
            )
            data = (
                np.sin(2 * np.pi * frequency_hz * times) * envelope * volume
            ).astype(np.float32)
            sd.play(data, sample_rate)
            sd.wait()

        async with self.output_lock:
            self._stop_requested = False
            await asyncio.to_thread(play)

    def stop_playback(self) -> None:
        self._stop_requested = True
        try:
            import sounddevice as sd

            sd.stop()
        except Exception:
            pass

