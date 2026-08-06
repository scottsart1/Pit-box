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

# What a transcriber returns when it was handed silence or room noise rather
# than speech. These are subtitle-corpus artefacts, not driver radio, so a clip
# that reduces to one of them carries no request and is dropped.
_SILENCE_ARTIFACTS = frozenset(
    {
        "you",
        "thank you",
        "thanks",
        "thank you very much",
        "thanks for watching",
        "thank you for watching",
        "thanks for watching the video",
        "please subscribe",
        "subscribe",
        "bye",
        "goodbye",
        "okay",
        "ok",
        "so",
        "oh",
        "ah",
        "hmm",
        "mm",
        "mhm",
        "uh",
        "um",
        "the",
        "and",
    }
)

FirstAudioCallback = Callable[[], Awaitable[None] | None]


class AudioService:
    """OpenAI STT/TTS plus low-latency local playback helpers.

    Native engineer speech uses raw 24 kHz PCM and starts playback as soon as
    the first network chunk arrives. File synthesis is retained for browser
    playback, acknowledgement caching, and compatibility fallbacks.
    """

    def __init__(self) -> None:
        self.client: AsyncOpenAI | None = None
        self.rebind_client()
        self.output_lock = asyncio.Lock()
        self._stop_requested = False
        self._ack_paths = {
            "copy": settings.data_dir / f"ack-copy-{settings.voice}.wav",
            "standby": settings.data_dir / f"ack-standby-{settings.voice}.wav",
        }

    def rebind_client(self) -> None:
        """Rebuild the OpenAI client from the current settings.

        Called at construction and again whenever the API key is changed from
        the Connection Center, so a driver who pastes a key mid-session gets a
        working radio without restarting Pit Wall.
        """
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
    def _looks_like_prompt_echo(text: str, prompt: str | None = None) -> bool:
        """Detect a transcript that is really the steering prompt read back.

        The literal-prefix checks below are kept for older prompt wordings, but
        they go stale whenever ``transcription_prompt`` changes: a real session
        recorded the current prompt as a driver utterance because the guard was
        still looking for the previous release's opening words. The primary test
        is therefore derived from the prompt that was actually sent, so the two
        can never drift apart again.
        """
        lowered = re.sub(r"\s+", " ", text.strip().lower())
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
        if repeated_wake_garbage:
            return True

        # Prompt-derived test. Short radio calls legitimately reuse prompt
        # keywords ("box box"), so this only applies to long transcripts, and
        # requires that nearly every word came from the prompt.
        if prompt and len(words) >= 8:
            prompt_words = set(re.findall(r"[a-z]+", prompt.lower()))
            if prompt_words:
                overlap = sum(word in prompt_words for word in words) / len(words)
                if overlap >= 0.85:
                    return True

        hits = sum(term in lowered for term in RACING_VOCAB)
        return (
            lowered.startswith("formula one race radio")
            or lowered.startswith("f1 race radio vocabulary")
            or lowered.startswith("wake name:")
            or lowered.startswith("names in use:")
            or "accepted openings:" in lowered
            or (hits >= 8 and len(lowered) > 100 and "drivers:" in lowered)
        )

    @staticmethod
    def _looks_like_silence_artifact(text: str) -> bool:
        """Detect the stock phrases a transcriber emits when handed no speech.

        Whisper-family models do not return an empty string for silence or
        room noise; they return a small, well-known set of filler and
        sign-off phrases learned from subtitle data. None of them is
        something a driver says to a race engineer, so discarding them costs
        nothing and removes a common source of unrequested radio calls.
        """
        stripped = " ".join(re.sub(r"[^a-z\s]", " ", text.strip().lower()).split())
        return bool(stripped) and stripped in _SILENCE_ARTIFACTS

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

    @staticmethod
    def transcription_prompt(
        driver_names: list[str] | None = None,
        wake_phrases: list[str] | None = None,
    ) -> str:
        """Build a short STT steering prompt without leaking prose into output.

        The wake name is supplied as vocabulary, never as a positional hint.
        An earlier wording told the model that the *opening word* was likely
        "Mark"; because ``extract_wake_command`` then matches on exactly that
        token at position 0, the steering was self-fulfilling and clips of
        noise or of speech not addressed to the engineer transcribed as a
        genuine wake call. Naming the words without claiming where they fall
        keeps the 3.6.1 fix (one-word clips alternating between Mark and Marc)
        while removing the invitation to insert one.
        """
        names = ", ".join(
            name
            for name in (driver_names or [])[:20]
            if name and name != "Unknown"
        )
        parts: list[str] = []
        if wake_phrases:
            variants = ", ".join(dict.fromkeys(p.title() for p in wake_phrases))
            parts.append(
                f"Names in use: {variants}. Transcribe only the words actually "
                "spoken, never adding a name that was not said, and return "
                "nothing at all for silence or background noise"
            )
        parts.append("Keywords: " + ", ".join(RACING_VOCAB))
        if names:
            parts.append(f"Drivers: {names}")
        return ". ".join(parts)

    async def transcribe(
        self,
        path: Path,
        driver_names: list[str] | None = None,
        wake_phrases: list[str] | None = None,
    ) -> str:
        if self.client is None:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        # Keep steering concise. Wake capture gets an explicit name hint because
        # one-word clips otherwise tend to alternate between "Mark" and "Marc".
        prompt = self.transcription_prompt(driver_names, wake_phrases)
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
        if self._looks_like_prompt_echo(text, prompt):
            return ""
        if self._looks_like_silence_artifact(text):
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

