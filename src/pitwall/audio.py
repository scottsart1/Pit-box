from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from .config import settings


RACING_VOCAB = (
    "box",
    "pit",
    "undercut",
    "overcut",
    "drs",
    "ers",
    "degradation",
    "safety car",
    "virtual safety car",
    "mediums",
    "hards",
    "inters",
    "wets",
)


class AudioService:
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

    @staticmethod
    def _looks_like_prompt_echo(text: str) -> bool:
        lowered = re.sub(r"\s+", " ", text.strip().lower())
        hits = sum(term in lowered for term in RACING_VOCAB)
        return (
            lowered.startswith("formula one race radio")
            or lowered.startswith("f1 race radio vocabulary")
            or (hits >= 8 and len(lowered) > 100 and "drivers:" in lowered)
        )


    @staticmethod
    def extract_wake_command(
        text: str,
        phrases: list[str],
    ) -> tuple[bool, str, str]:
        """Match a wake phrase only at the beginning of a transcript.

        Returns ``(matched, command, phrase)``. A phrase-only transcript such
        as ``Mark`` is valid and returns an empty command so the controller can
        arm a short follow-up window.
        """
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
            name for name in (driver_names or [])[:20] if name and name != "Unknown"
        )
        prompt = "F1 team radio vocabulary: " + ", ".join(RACING_VOCAB)
        if names:
            prompt += f". Driver surnames: {names}"
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
        async with self.client.audio.speech.with_streaming_response.create(
            model=settings.tts_model,
            voice=settings.voice,
            input=text,
            instructions=(
                "Calm professional motorsport race engineer. Concise team-radio delivery, "
                "controlled urgency, no theatrical accent imitation."
            ),
            response_format=response_format,
        ) as response:
            await response.stream_to_file(target)
        return target

    @staticmethod
    async def play_wav(path: Path) -> None:
        def play() -> None:
            import sounddevice as sd
            import soundfile as sf

            data, sample_rate = sf.read(path, dtype="float32")
            sd.play(data, sample_rate)
            sd.wait()

        await asyncio.to_thread(play)

    @staticmethod
    async def play_tone(
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
            envelope = np.minimum(1.0, np.arange(samples) / max(1, samples * 0.15))
            envelope *= np.minimum(1.0, np.arange(samples)[::-1] / max(1, samples * 0.20))
            data = (np.sin(2 * np.pi * frequency_hz * times) * envelope * volume).astype(np.float32)
            sd.play(data, sample_rate)
            sd.wait()

        await asyncio.to_thread(play)

    @staticmethod
    def stop_playback() -> None:
        try:
            import sounddevice as sd

            sd.stop()
        except Exception:
            pass
