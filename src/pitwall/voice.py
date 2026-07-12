from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import time
import wave
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np

from .audio import AudioService
from .brain import EngineerBrain
from .config import settings
from .state import StateStore

log = logging.getLogger(__name__)


class NativeVoiceController:
    """Shared Windows microphone for hands-free wake radio and L3 fallback.

    One input stream is kept open and routed by software. The configured
    ``L3 -> UDP Action 1`` bit remains an independent fallback. Unrelated
    controller inputs never start or stop audio capture.

    Hands-free mode uses local RMS voice activity detection to create bounded
    candidate clips. The clip is transcribed through OpenAI and accepted only
    when its transcript begins with a configured wake phrase such as ``Mark``
    or ``Hey Mark``. Saying only the wake phrase arms a short follow-up window;
    saying ``Mark, what is the gap ahead?`` works in one utterance.
    """

    def __init__(
        self,
        store: StateStore,
        brain: EngineerBrain,
        audio: AudioService,
    ) -> None:
        self.store = store
        self.brain = brain
        self.audio = audio
        self.loop = asyncio.get_running_loop()
        self.mask = settings.ptt_mask
        self.config_path = settings.data_dir / "ptt.json"
        self.calibrating = False
        self.baseline_status = 0
        self.last_status = 0
        self.pressed_at = 0.0
        self.stream: Any = None
        self.frames: list[np.ndarray] = []
        self.busy = False

        self._signal_pressed = False
        self._transition_lock = asyncio.Lock()
        self._speech_lock = asyncio.Lock()
        self._process_task: asyncio.Task[None] | None = None
        self._speech_task: asyncio.Task[bool] | None = None
        self._recording_guard_task: asyncio.Task[None] | None = None

        self._mask_event_count = 0
        self._first_mask_event_at = 0.0
        self._last_mask_event_at = 0.0
        self._speech_detected = False
        self._last_voice_at = 0.0
        self._last_audio_rms = 0.0
        self._release_in_progress = False

        block_s = max(0.01, settings.wake_block_ms / 1000.0)
        preroll_blocks = max(1, math.ceil(settings.wake_preroll_s / block_s))
        self._wake_preroll: deque[np.ndarray] = deque(maxlen=preroll_blocks)
        self._wake_frames: list[np.ndarray] = []
        self._wake_speaking = False
        self._wake_consecutive = 0
        self._wake_started_at = 0.0
        self._wake_last_voice_at = 0.0
        self._wake_finalize_pending = False
        self._wake_process_task: asyncio.Task[None] | None = None
        self._wake_arm_task: asyncio.Task[None] | None = None
        self._wake_armed_until = 0.0
        self._wake_cooldown_until = 0.0
        self._tts_playing = False
        self._shutdown = False
        self._load_config()

    @property
    def is_busy(self) -> bool:
        return bool(
            self.busy
            or self._signal_pressed
            or self._wake_speaking
            or self._wake_armed_until > time.monotonic()
            or (self._speech_task and not self._speech_task.done())
            or (self._wake_process_task and not self._wake_process_task.done())
        )

    def _load_config(self) -> None:
        try:
            if self.config_path.exists():
                payload = json.loads(self.config_path.read_text(encoding="utf-8"))
                self.mask = int(payload["mask"])
        except Exception:
            log.exception("Could not load PTT configuration")

    async def initialize(self) -> None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        await self.store.update(
            ptt_mask=self.mask,
            ptt_status="ready" if self.mask else "calibration required",
            ptt_release_mode=settings.ptt_release_mode,
            ptt_release_reason="",
            wake_enabled=bool(settings.wake_enabled and settings.native_voice),
            wake_status=(
                f'ready — say "{settings.wake_phrase.title()}"'
                if settings.wake_enabled and settings.native_voice
                else "disabled"
            ),
            wake_phrase=settings.wake_phrase,
            wake_armed=False,
            wake_last_reason="",
        )
        if settings.native_voice:
            await self._ensure_input_stream()

    async def shutdown(self) -> None:
        self._shutdown = True
        self._cancel_recording_guard()
        for task in (
            self._process_task,
            self._speech_task,
            self._wake_process_task,
            self._wake_arm_task,
        ):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self.audio.stop_playback()
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                log.exception("Could not close microphone stream cleanly")
            finally:
                self.stream = None

    async def configure_wake(self, enabled: bool) -> dict[str, Any]:
        settings.wake_enabled = bool(enabled)
        self._clear_wake_capture()
        await self._disarm_wake("disabled" if not enabled else "ready")
        if enabled and settings.native_voice:
            await self._ensure_input_stream()
        await self.store.update(
            wake_enabled=bool(enabled and settings.native_voice),
            wake_status=(
                f'ready — say "{settings.wake_phrase.title()}"'
                if enabled and settings.native_voice
                else "disabled"
            ),
        )
        return {
            "enabled": bool(enabled and settings.native_voice),
            "phrase": settings.wake_phrase,
            "aliases": settings.wake_phrases,
        }

    async def begin_calibration(self) -> dict[str, Any]:
        self.calibrating = True
        self.baseline_status = self.last_status
        await self.store.update(ptt_status="press and hold L3 now")
        return {
            "ok": True,
            "message": (
                "Press and hold only L3 (mapped to UDP Action 1), then release it. "
                "The existing game mapping is not changed."
            ),
        }

    def on_button_status(self, status: int) -> None:
        """Consume BUTN data without treating unrelated bits as PTT release."""
        self.last_status = int(status)
        now = time.monotonic()
        self.loop.create_task(
            self.store.update(ptt_last_button_status=int(status))
        )

        if self.calibrating:
            changed_on = int(status) & ~int(self.baseline_status)
            if changed_on:
                mask = changed_on & -changed_on
                self.mask = int(mask)
                self.calibrating = False
                settings.data_dir.mkdir(parents=True, exist_ok=True)
                self.config_path.write_text(
                    json.dumps({"mask": self.mask}, indent=2),
                    encoding="utf-8",
                )
                self.loop.create_task(
                    self.store.update(
                        ptt_mask=self.mask,
                        ptt_status=f"bound to bit 0x{self.mask:X}",
                        ptt_release_mode=settings.ptt_release_mode,
                    )
                )
            return

        if not self.mask:
            return

        mask_present = bool(int(status) & self.mask)
        if mask_present:
            self._last_mask_event_at = now
            self._mask_event_count += 1
            self.loop.create_task(
                self.store.update(ptt_mask_events=self._mask_event_count)
            )
            if self._signal_pressed:
                return
            self._first_mask_event_at = now
            self._mask_event_count = 1
            self._signal_pressed = True
            self.loop.create_task(self._transition(True, now, "mask_press"))
            return

        mode = settings.ptt_release_mode
        established_hold = (
            self._mask_event_count >= 2
            and self._last_mask_event_at - self._first_mask_event_at >= 0.08
        )
        elapsed_ms = (now - self.pressed_at) * 1000.0
        if (
            self._signal_pressed
            and int(status) == 0
            and established_hold
            and elapsed_ms >= settings.ptt_release_ignore_ms
            and mode in {"explicit", "explicit_or_silence"}
        ):
            self.loop.create_task(
                self._transition(False, now, "explicit_release")
            )

    async def _transition(self, pressed: bool, now: float, reason: str) -> None:
        async with self._transition_lock:
            if pressed:
                self.pressed_at = now
                self._speech_detected = False
                self._last_voice_at = now
                self._last_audio_rms = 0.0
                self._release_in_progress = False
                await self.store.update(
                    ptt_pressed=True,
                    ptt_release_reason="",
                    ptt_mask_events=self._mask_event_count,
                    wake_armed=False,
                    wake_status="paused for L3 radio",
                )
                await self._interrupt_pipeline()
                await self._start_recording()
                self._start_recording_guard()
                return

            await self._finish_recording(reason)

    async def _finish_recording(self, reason: str) -> None:
        if self._release_in_progress:
            return
        self._release_in_progress = True
        try:
            self._signal_pressed = False
            self._cancel_recording_guard()
            held_ms = max(
                0.0,
                (time.monotonic() - self.pressed_at) * 1000.0,
            )
            await self.store.update(
                ptt_pressed=False,
                ptt_release_reason=reason,
            )
            await self._stop_recording(
                process=held_ms >= settings.ptt_min_hold_ms
            )
        finally:
            self._release_in_progress = False
            if settings.wake_enabled:
                await self.store.update(
                    wake_status=f'ready — say "{settings.wake_phrase.title()}"'
                )

    def _start_recording_guard(self) -> None:
        self._cancel_recording_guard()
        self._recording_guard_task = self.loop.create_task(
            self._recording_guard(),
            name="pitwall-ptt-recording-guard",
        )

    def _cancel_recording_guard(self) -> None:
        task = self._recording_guard_task
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
        self._recording_guard_task = None

    async def _recording_guard(self) -> None:
        try:
            while self._signal_pressed:
                await asyncio.sleep(0.04)
                now = time.monotonic()
                elapsed = now - self.pressed_at

                if elapsed >= settings.ptt_max_recording_s:
                    await self._finish_recording("hard_cap")
                    await self.store.update(
                        last_error=(
                            "Radio reached the recording safety cap; the clip "
                            "was processed automatically."
                        )
                    )
                    break

                mode = settings.ptt_release_mode
                if (
                    mode in {"silence", "explicit_or_silence"}
                    and self._speech_detected
                    and elapsed >= 0.70
                    and now - self._last_voice_at
                    >= settings.ptt_silence_release_s
                ):
                    await self._finish_recording("speech_silence")
                    break

                if (
                    mode == "heartbeat"
                    and settings.ptt_release_watchdog_s > 0
                    and self._mask_event_count >= 2
                    and now - self._last_mask_event_at
                    >= settings.ptt_release_watchdog_s
                ):
                    await self._finish_recording("heartbeat_timeout")
                    break
        except asyncio.CancelledError:
            raise
        finally:
            if self._recording_guard_task is asyncio.current_task():
                self._recording_guard_task = None

    async def _ensure_input_stream(self) -> None:
        if self.stream is not None or self._shutdown:
            return
        try:
            import sounddevice as sd

            blocksize = max(
                160,
                int(settings.audio_sample_rate * settings.wake_block_ms / 1000),
            )

            def callback(indata, frames, time_info, status):  # type: ignore[no-untyped-def]
                del frames, time_info
                if status:
                    log.debug("Audio input status: %s", status)
                self._consume_audio_block(indata.copy())

            self.stream = sd.InputStream(
                samplerate=settings.audio_sample_rate,
                channels=1,
                dtype="int16",
                blocksize=blocksize,
                device=settings.audio_device,
                callback=callback,
            )
            self.stream.start()
            await self.store.update(last_error="")
        except Exception as exc:
            self.stream = None
            await self.store.update(
                last_error=f"Microphone error: {exc}",
                engineer_status="audio error",
                wake_status="microphone unavailable",
            )

    @staticmethod
    def _rms(block: np.ndarray) -> float:
        if not block.size:
            return 0.0
        return float(np.sqrt(np.mean(block.astype(np.float64) ** 2)))

    def _wake_blocked(self, now: float) -> bool:
        return bool(
            not settings.wake_enabled
            or not settings.native_voice
            or self._signal_pressed
            or self.busy
            or self._tts_playing
            or now < self._wake_cooldown_until
            or (self._wake_process_task and not self._wake_process_task.done())
        )

    def _consume_audio_block(self, block: np.ndarray) -> None:
        """Route one microphone block to PTT or hands-free wake capture."""
        now = time.monotonic()
        rms = self._rms(block)
        self._last_audio_rms = rms

        if self._signal_pressed:
            self.frames.append(block)
            if rms >= max(settings.audio_min_rms, settings.ptt_speech_rms):
                self._speech_detected = True
                self._last_voice_at = now
            return

        if self._wake_blocked(now):
            self._clear_wake_capture()
            return

        threshold = max(settings.audio_min_rms, settings.wake_speech_rms)
        if not self._wake_speaking:
            self._wake_preroll.append(block)
            if rms >= threshold:
                self._wake_consecutive += 1
            else:
                self._wake_consecutive = 0
            if self._wake_consecutive >= max(1, settings.wake_start_blocks):
                self._wake_speaking = True
                self._wake_started_at = now
                self._wake_last_voice_at = now
                self._wake_frames = list(self._wake_preroll)
                self._wake_preroll.clear()
                self.loop.call_soon_threadsafe(
                    lambda: self.loop.create_task(
                        self.store.update(wake_status="hearing speech")
                    )
                )
            return

        self._wake_frames.append(block)
        if rms >= threshold:
            self._wake_last_voice_at = now

        elapsed = now - self._wake_started_at
        if elapsed >= settings.wake_max_utterance_s:
            self._schedule_wake_finalize("hard_cap")
            return
        if (
            elapsed >= settings.wake_min_utterance_s
            and now - self._wake_last_voice_at >= settings.wake_silence_s
        ):
            self._schedule_wake_finalize("speech_silence")

    def _schedule_wake_finalize(self, reason: str) -> None:
        if self._wake_finalize_pending:
            return
        self._wake_finalize_pending = True
        frames = self._wake_frames
        self._clear_wake_capture(keep_pending=True)
        self.loop.call_soon_threadsafe(
            self._launch_wake_candidate,
            frames,
            reason,
        )

    def _launch_wake_candidate(
        self,
        frames: list[np.ndarray],
        reason: str,
    ) -> None:
        self._wake_finalize_pending = False
        if not frames or self._shutdown:
            return
        if self._wake_process_task and not self._wake_process_task.done():
            return
        self._wake_process_task = self.loop.create_task(
            self._process_wake_candidate(frames, reason),
            name="pitwall-wake-candidate",
        )

    def _clear_wake_capture(self, keep_pending: bool = False) -> None:
        self._wake_frames = []
        self._wake_speaking = False
        self._wake_consecutive = 0
        self._wake_started_at = 0.0
        self._wake_last_voice_at = 0.0
        self._wake_preroll.clear()
        if not keep_pending:
            self._wake_finalize_pending = False

    async def _process_wake_candidate(
        self,
        frames: list[np.ndarray],
        reason: str,
    ) -> None:
        try:
            if self._signal_pressed or not frames:
                return
            data = np.concatenate(frames, axis=0)
            duration = data.shape[0] / max(1, settings.audio_sample_rate)
            if duration < settings.wake_min_utterance_s:
                return
            if self._rms(data) < settings.audio_min_rms:
                return

            source = settings.data_dir / "latest_wake.wav"
            self._write_wav(source, data)
            await self.store.update(
                wake_status="checking wake phrase",
                wake_last_reason=reason,
            )
            snapshot = await self.store.snapshot_live()
            names = [driver["name"] for driver in snapshot["drivers"]]
            text = await self.audio.transcribe(source, names)
            if not text:
                await self._reject_wake("empty transcript", "")
                return

            now = time.monotonic()
            armed = now <= self._wake_armed_until
            matched, command, phrase = self.audio.extract_wake_command(
                text,
                settings.wake_phrases,
            )
            await self.store.update(wake_last_transcript=text)

            lowered = " ".join(text.lower().split())
            if lowered in {"cancel radio", "cancel", "never mind", "nevermind"}:
                await self._disarm_wake("cancelled")
                return

            if matched and not command:
                await self._arm_wake(phrase)
                return

            if matched:
                await self._accept_wake(command, phrase)
                return

            if armed:
                await self._accept_wake(text, "armed follow-up")
                return

            await self._reject_wake("wake phrase not at transcript start", text)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("Wake phrase pipeline failed")
            await self.store.update(
                last_error=f"Wake phrase error: {exc}",
                wake_status="error",
                engineer_status="error",
            )
        finally:
            if self._wake_process_task is asyncio.current_task():
                self._wake_process_task = None
            if (
                settings.wake_enabled
                and not self._signal_pressed
                and not self._tts_playing
                and self._wake_armed_until <= time.monotonic()
            ):
                await self.store.update(
                    wake_status=f'ready — say "{settings.wake_phrase.title()}"'
                )

    async def _arm_wake(self, phrase: str) -> None:
        self._wake_armed_until = time.monotonic() + settings.wake_arm_timeout_s
        snapshot = await self.store.snapshot_live()
        count = int(snapshot.get("wake_trigger_count", 0)) + 1
        await self.store.update(
            wake_armed=True,
            wake_trigger_count=count,
            wake_status="armed — say the command",
            wake_last_reason=f'heard "{phrase}"',
        )
        self._wake_cooldown_until = time.monotonic() + 0.20
        with contextlib.suppress(Exception):
            await self.audio.play_tone()
        if self._wake_arm_task and not self._wake_arm_task.done():
            self._wake_arm_task.cancel()
        self._wake_arm_task = self.loop.create_task(
            self._wake_arm_timeout(),
            name="pitwall-wake-arm-timeout",
        )

    async def _wake_arm_timeout(self) -> None:
        try:
            await asyncio.sleep(settings.wake_arm_timeout_s)
            if time.monotonic() >= self._wake_armed_until:
                await self._disarm_wake("follow-up timed out")
        except asyncio.CancelledError:
            raise
        finally:
            if self._wake_arm_task is asyncio.current_task():
                self._wake_arm_task = None

    async def _disarm_wake(self, reason: str) -> None:
        self._wake_armed_until = 0.0
        await self.store.update(
            wake_armed=False,
            wake_last_reason=reason,
            wake_status=(
                f'ready — say "{settings.wake_phrase.title()}"'
                if settings.wake_enabled
                else "disabled"
            ),
        )

    async def _accept_wake(self, command: str, phrase: str) -> None:
        cleaned = command.strip()
        if not cleaned:
            await self._arm_wake(phrase)
            return
        if self._wake_arm_task and not self._wake_arm_task.done():
            self._wake_arm_task.cancel()
        self._wake_armed_until = 0.0
        snapshot = await self.store.snapshot_live()
        count = int(snapshot.get("wake_trigger_count", 0)) + 1
        await self.store.update(
            wake_armed=False,
            wake_trigger_count=count,
            wake_status="thinking",
            wake_last_reason=f'accepted via {phrase}',
            engineer_status="thinking",
            last_error="",
        )
        self.busy = True
        try:
            reply = await self.brain.ask(cleaned)
            await self.speak_text(reply)
        finally:
            self.busy = False
            if not self._signal_pressed:
                await self.store.update(engineer_status="standing by")

    async def _reject_wake(self, reason: str, transcript: str) -> None:
        snapshot = await self.store.snapshot_live()
        count = int(snapshot.get("wake_rejected_count", 0)) + 1
        await self.store.update(
            wake_rejected_count=count,
            wake_last_transcript=transcript,
            wake_last_reason=reason,
            wake_status=f'ready — say "{settings.wake_phrase.title()}"',
        )

    async def _interrupt_pipeline(self) -> None:
        self.audio.stop_playback()
        self._tts_playing = False
        self._clear_wake_capture()
        self._wake_armed_until = 0.0
        for task in (
            self._process_task,
            self._speech_task,
            self._wake_process_task,
            self._wake_arm_task,
        ):
            if task and not task.done() and task is not asyncio.current_task():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._process_task = None
        self._speech_task = None
        self._wake_process_task = None
        self._wake_arm_task = None
        self.busy = False

    async def _start_recording(self) -> None:
        if not settings.native_voice:
            await self.store.update(
                engineer_status="PTT active; native voice disabled"
            )
            return
        await self._ensure_input_stream()
        if self.stream is None:
            self._signal_pressed = False
            await self.store.update(ptt_pressed=False)
            return
        self.frames = []
        await self.store.update(
            engineer_status="listening",
            last_error="",
        )

    async def _stop_recording(self, process: bool) -> None:
        if not process or not self.frames:
            self.frames = []
            await self.store.update(engineer_status="standing by")
            return
        data = np.concatenate(self.frames, axis=0)
        self.frames = []
        if self._rms(data) < settings.audio_min_rms:
            await self.store.update(
                engineer_status="standing by",
                last_error="Radio clip was silent or too quiet.",
            )
            return
        self._process_task = self.loop.create_task(self._process(data))

    @staticmethod
    def _write_wav(path: Path, data: np.ndarray) -> None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(settings.audio_sample_rate)
            output.writeframes(data.astype(np.int16, copy=False).tobytes())

    async def _process(self, data: np.ndarray) -> None:
        if self.busy:
            return
        self.busy = True
        source = settings.data_dir / "latest_driver.wav"
        try:
            self._write_wav(source, data)
            await self.store.update(engineer_status="transcribing")
            snapshot = await self.store.snapshot_live()
            names = [driver["name"] for driver in snapshot["drivers"]]
            text = await self.audio.transcribe(source, names)
            if not text:
                await self.store.update(engineer_status="standing by")
                return
            await self.store.update(engineer_status="thinking")
            reply = await self.brain.ask(text)
            await self.speak_text(reply)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("Voice pipeline failed")
            await self.store.update(
                last_error=str(exc),
                engineer_status="error",
            )
        finally:
            self.busy = False
            if not self._signal_pressed:
                await self.store.update(engineer_status="standing by")

    async def speak_text(self, text: str) -> bool:
        if not text.strip() or self._signal_pressed:
            return False

        async def perform() -> bool:
            async with self._speech_lock:
                if self._signal_pressed:
                    return False
                self._tts_playing = True
                self._clear_wake_capture()
                target = settings.data_dir / "latest_engineer.wav"
                await self.store.update(
                    engineer_status="speaking",
                    wake_status="paused while engineer speaks",
                )
                await self.audio.synthesize(text, target, "wav")
                if self._signal_pressed:
                    return False
                await self.audio.play_wav(target)
                return True

        self._speech_task = asyncio.create_task(perform())
        try:
            return await self._speech_task
        except asyncio.CancelledError:
            self.audio.stop_playback()
            raise
        finally:
            self._speech_task = None
            self._tts_playing = False
            self._wake_cooldown_until = (
                time.monotonic() + settings.wake_tts_cooldown_s
            )
            if not self._signal_pressed and not self.busy:
                await self.store.update(engineer_status="standing by")
            if settings.wake_enabled:
                await self.store.update(
                    wake_status=f'ready — say "{settings.wake_phrase.title()}"'
                )
