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
from .realtime import RealtimeRadio
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
        self._pending_clips: deque[np.ndarray] = deque(
            maxlen=max(1, settings.voice_clip_queue_size)
        )
        self._ack_prepare_task: asyncio.Task[None] | None = None
        self._persisted_wake_enabled: bool | None = None
        self._wake_config_source = ".env/default"
        self._legacy_wake_setting_ignored = False
        self._interaction_source = ""
        self._interaction_finalized_at = 0.0

        self._signal_pressed = False
        self._transition_lock = asyncio.Lock()
        self._speech_lock = asyncio.Lock()
        self._process_task: asyncio.Task[None] | None = None
        self._speech_task: asyncio.Task[bool] | None = None
        self._recording_guard_task: asyncio.Task[None] | None = None
        self._release_candidate_task: asyncio.Task[None] | None = None

        self._mask_event_count = 0
        self._first_mask_event_at = 0.0
        self._last_mask_event_at = 0.0
        self._speech_detected = False
        self._last_voice_at = 0.0
        self._last_audio_rms = 0.0
        self._wake_noise_rms = 0.0
        self._wake_effective_threshold = float(settings.wake_min_speech_rms)
        self._last_wake_diag_at = 0.0
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
        # Speech-to-speech radio. When enabled, the wake phrase opens a Realtime
        # session and the microphone is routed straight to it; the file-based
        # transcribe/reason/synthesise chain stays available as the fallback.
        self.realtime: RealtimeRadio | None = None
        if settings.voice_realtime_enabled:
            self.realtime = RealtimeRadio(
                store,
                brain.tools,
                on_transcript=self._persist_realtime_transcript,
                situation_header=self._realtime_header,
            )
        self._load_config()

    async def _realtime_header(self) -> str:
        """Live situation summary handed to the speech session."""
        return await self.brain.situation_header()

    async def _persist_realtime_transcript(self, role: str, text: str) -> None:
        state = await self.store.snapshot_analysis()
        await self.brain.database.save_radio_message(
            state, role, text, "user" if role == "driver" else "response:realtime"
        )

    @property
    def realtime_active(self) -> bool:
        return bool(self.realtime is not None and self.realtime.is_open)

    @property
    def is_busy(self) -> bool:
        return bool(
            self.busy
            or self._signal_pressed
            or self._wake_speaking
            or self._wake_armed_until > time.monotonic()
            # An open speech session means a conversation is in progress; an
            # unsolicited call must not be spoken over the top of it.
            or self.realtime_active
            or (self._speech_task and not self._speech_task.done())
            or (self._wake_process_task and not self._wake_process_task.done())
        )

    def _load_config(self) -> None:
        try:
            if self.config_path.exists():
                payload = json.loads(self.config_path.read_text(encoding="utf-8"))
                self.mask = int(payload.get("mask", self.mask))
                version = int(payload.get("version", 1) or 1)
                if version >= 2 and "wake_enabled" in payload:
                    self._persisted_wake_enabled = bool(payload["wake_enabled"])
                    self._wake_config_source = "saved dashboard setting"
                elif "wake_enabled" in payload:
                    # 3.6.0 wrote this flag without a schema version. A stale
                    # false value could silently defeat a new .env/build, so the
                    # one-time migration keeps the L3 mask but trusts .env.
                    self._legacy_wake_setting_ignored = True
                    self._wake_config_source = "legacy saved setting ignored"
        except Exception:
            log.exception("Could not load PTT/voice configuration")

    def _save_config(self) -> None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "mask": int(self.mask),
                    "wake_enabled": bool(settings.wake_enabled),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    async def _begin_interaction(self, source: str) -> None:
        self._interaction_source = source
        await self.store.update(
            radio_indicator="listening",
            radio_source=source,
            radio_latency={
                "stage": "listening",
                "source": source,
                "route": "",
                "capture_finalized_ms": None,
                "transcript_ms": None,
                "model_ms": None,
                "first_audio_ms": None,
                "complete_ms": None,
                "ack": "",
            },
        )

    async def _finalize_interaction(self, source: str) -> None:
        self._interaction_source = source
        self._interaction_finalized_at = time.perf_counter()
        await self.store.update(
            radio_indicator="processing",
            radio_source=source,
            radio_latency={
                "stage": "processing",
                "source": source,
                "route": "",
                "capture_finalized_ms": 0,
                "transcript_ms": None,
                "model_ms": None,
                "first_audio_ms": None,
                "complete_ms": None,
                "ack": "",
            },
        )

    async def _mark_latency(self, field: str, value: Any | None = None) -> None:
        if not self._interaction_finalized_at:
            return
        elapsed = round((time.perf_counter() - self._interaction_finalized_at) * 1000)
        snapshot = await self.store.snapshot_live()
        latency = dict(snapshot.get("radio_latency", {}))
        latency[field] = elapsed if value is None else value
        if field in {"transcript_ms", "model_ms"}:
            latency["stage"] = "processing"
        elif field == "first_audio_ms":
            latency["stage"] = "speaking"
        elif field == "complete_ms":
            latency["stage"] = "complete"
        await self.store.update(radio_latency=latency)

    async def initialize(self) -> None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        if self._persisted_wake_enabled is not None:
            settings.wake_enabled = self._persisted_wake_enabled
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
            wake_last_reason=(
                "legacy saved wake setting ignored; using .env"
                if self._legacy_wake_setting_ignored
                else ""
            ),
            wake_config_source=self._wake_config_source,
            wake_input_rms=0.0,
            wake_noise_rms=0.0,
            wake_threshold_rms=float(settings.wake_min_speech_rms),
        )
        if settings.native_voice:
            await self._ensure_input_stream()
        prepare_acknowledgements = getattr(
            self.audio,
            "prepare_acknowledgements",
            None,
        )
        if callable(prepare_acknowledgements):
            self._ack_prepare_task = self.loop.create_task(
                prepare_acknowledgements(),
                name="pitwall-ack-cache",
            )

    async def shutdown(self) -> None:
        self._shutdown = True
        if self.realtime is not None:
            await self.realtime.close("shutdown")
        self._cancel_recording_guard()
        for task in (
            self._process_task,
            self._speech_task,
            self._wake_process_task,
            self._wake_arm_task,
            self._ack_prepare_task,
            self._release_candidate_task,
        ):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._release_candidate_task = None
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
        self._wake_config_source = "saved dashboard setting"
        self._save_config()
        self._clear_wake_capture()
        await self._disarm_wake("disabled" if not enabled else "ready")
        if enabled and settings.native_voice:
            await self._ensure_input_stream()
        await self.store.update(
            wake_enabled=bool(enabled and settings.native_voice),
            wake_config_source=self._wake_config_source,
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
                self._save_config()
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
        if int(status) != 0:
            # A subsequent controller packet invalidates a tentative zero-status
            # release. This debounces transient UDP/button gaps without requiring
            # a repeated held-button heartbeat.
            self._cancel_release_candidate()
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
        elapsed_ms = (now - self.pressed_at) * 1000.0
        if (
            self._signal_pressed
            and int(status) == 0
            and self._mask_event_count >= 1
            and elapsed_ms >= settings.ptt_release_ignore_ms
            and mode in {"explicit", "explicit_or_silence"}
        ):
            if not self._release_candidate_task or self._release_candidate_task.done():
                self._release_candidate_task = self.loop.create_task(
                    self._confirm_explicit_release(now),
                    name="pitwall-ptt-release-confirm",
                )

    async def _confirm_explicit_release(self, candidate_at: float) -> None:
        """Confirm button-up after a short quiet debounce window.

        Some controller/UDP streams emit a transient all-zero BUTN packet while
        L3 remains held. Waiting briefly and requiring zero to remain the latest
        status prevents those gaps from clipping the driver's sentence.
        """
        try:
            await asyncio.sleep(max(0.0, settings.ptt_release_ignore_ms / 1000.0))
            if (
                self._signal_pressed
                and self.last_status == 0
                and candidate_at >= self._last_mask_event_at
                and settings.ptt_release_mode in {"explicit", "explicit_or_silence"}
            ):
                await self._transition(False, time.monotonic(), "explicit_release")
        except asyncio.CancelledError:
            raise
        finally:
            if self._release_candidate_task is asyncio.current_task():
                self._release_candidate_task = None

    def _cancel_release_candidate(self) -> None:
        task = self._release_candidate_task
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
        self._release_candidate_task = None

    async def _transition(self, pressed: bool, now: float, reason: str) -> None:
        async with self._transition_lock:
            if pressed and self.realtime_active:
                # The session already has the microphone. Pressing L3 during a
                # live conversation must not start a second, parallel capture.
                self.pressed_at = now
                self._signal_pressed = False
                await self.store.update(ptt_pressed=False, ptt_release_reason="realtime_open")
                return
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

            if reason == "explicit_release" and settings.ptt_release_tail_s > 0:
                # Keep capturing a short tail after button-up so the final phoneme
                # is not clipped by controller/network timing.
                await asyncio.sleep(settings.ptt_release_tail_s)
            await self._finish_recording(reason)

    async def _finish_recording(self, reason: str) -> None:
        if self._release_in_progress:
            return
        self._release_in_progress = True
        try:
            self._signal_pressed = False
            self._cancel_release_candidate()
            self._cancel_recording_guard()
            held_ms = max(
                0.0,
                (time.monotonic() - self.pressed_at) * 1000.0,
            )
            await self.store.update(
                ptt_pressed=False,
                ptt_release_reason=reason,
            )
            await self._finalize_interaction("ptt")
            with contextlib.suppress(Exception):
                await self.audio.play_tone(760.0, 0.07, 0.08)
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
                    # Minimum clip length before silence may end the recording.
                    # This used to be a hard-coded 1.0 s that silently overrode a
                    # shorter configured ptt_silence_release_s, so a quick call
                    # could not be released by silence at all no matter how the
                    # release threshold was tuned. With the default 2.2 s
                    # threshold the floor never binds either way.
                    and elapsed >= settings.ptt_min_speech_clip_s
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

    def _update_wake_noise(self, rms: float) -> None:
        """Track background level only while idle and below the configured cap."""
        if rms <= 0 or rms >= max(
            settings.wake_min_speech_rms,
            self._wake_effective_threshold * 0.90,
        ):
            return
        if self._wake_noise_rms <= 0:
            self._wake_noise_rms = rms
        else:
            self._wake_noise_rms = (0.97 * self._wake_noise_rms) + (0.03 * rms)

    def _effective_wake_threshold(self) -> float:
        if self._wake_noise_rms <= 0:
            return float(settings.wake_min_speech_rms)
        adaptive = (
            self._wake_noise_rms * settings.wake_noise_multiplier
            + settings.wake_noise_margin_rms
        )
        return float(
            min(
                settings.wake_speech_rms,
                max(settings.wake_min_speech_rms, adaptive),
            )
        )

    def _publish_wake_diagnostics(self, now: float) -> None:
        if now - self._last_wake_diag_at < 0.50:
            return
        self._last_wake_diag_at = now
        values = {
            "wake_input_rms": round(self._last_audio_rms, 1),
            "wake_noise_rms": round(self._wake_noise_rms, 1),
            "wake_threshold_rms": round(self._wake_effective_threshold, 1),
        }
        self.loop.call_soon_threadsafe(
            lambda: self.loop.create_task(self.store.update(**values))
        )

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
        """Route one microphone block to PTT, wake capture, or a live session."""
        now = time.monotonic()
        rms = self._rms(block)
        self._last_audio_rms = rms

        # While a speech-to-speech session is open the microphone belongs to it:
        # the model does its own turn detection, so no local wake gating applies
        # and the driver can interrupt the engineer mid-sentence.
        if self.realtime_active:
            self._publish_wake_diagnostics(now)
            realtime = self.realtime
            if realtime is not None:
                self.loop.call_soon_threadsafe(
                    lambda: self.loop.create_task(
                        realtime.send_audio(block, settings.audio_sample_rate)
                    )
                )
            return

        if self._signal_pressed:
            self.frames.append(block)
            if rms >= max(settings.audio_min_rms, settings.ptt_speech_rms):
                self._speech_detected = True
                self._last_voice_at = now
            return

        if self._wake_blocked(now):
            self._clear_wake_capture()
            return

        if not self._wake_speaking:
            self._update_wake_noise(rms)
        threshold = self._effective_wake_threshold()
        self._wake_effective_threshold = threshold
        self._publish_wake_diagnostics(now)
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
                        self._begin_interaction("wake")
                    )
                )
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
            lambda: self.loop.create_task(
                self._finalize_interaction("wake")
            )
        )
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
                await self.store.update(radio_indicator="idle")
                self._interaction_finalized_at = 0.0
                return
            data = np.concatenate(frames, axis=0)
            duration = data.shape[0] / max(1, settings.audio_sample_rate)
            if duration < settings.wake_min_utterance_s:
                await self._reject_wake("clip too short", "")
                return
            clip_rms = self._rms(data)
            clip_floor = max(
                settings.wake_clip_min_rms,
                min(self._wake_effective_threshold * 0.55, settings.audio_min_rms),
            )
            if clip_rms < clip_floor:
                await self._reject_wake(
                    f"clip too quiet ({clip_rms:.0f} RMS; need {clip_floor:.0f})",
                    "",
                )
                return

            source = settings.data_dir / "latest_wake.wav"
            self._write_wav(source, data)
            await self.store.update(
                wake_status="checking wake phrase",
                wake_last_reason=reason,
            )
            snapshot = await self.store.snapshot_live()
            names = [driver["name"] for driver in snapshot["drivers"]]
            text = await self.audio.transcribe(
                source,
                names,
                wake_phrases=settings.wake_phrases,
            )
            await self._mark_latency("transcript_ms")
            await self.store.update(radio_last_transcript=text)
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
                if await self._start_realtime(data, "wake phrase"):
                    return
                await self._arm_wake(phrase)
                return

            if matched:
                if await self._start_realtime(data, f"accepted via {phrase}"):
                    return
                await self._accept_wake(command, phrase)
                return

            if armed:
                if await self._start_realtime(data, "armed follow-up"):
                    return
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

    async def _start_realtime(self, data: np.ndarray | None, reason: str) -> bool:
        """Hand the conversation to a speech-to-speech session.

        The clip that triggered the wake phrase is replayed into the session so
        the driver's first question is not lost between the two pipelines: they
        say "Mark, what's the gap to Norris" once, not twice.
        """
        realtime = self.realtime
        if realtime is None:
            return False
        self.audio.stop_playback()
        if not await realtime.open():
            # Fall back to the file-based chain rather than dropping the call.
            return False
        snapshot = await self.store.snapshot_live()
        await self.store.update(
            wake_armed=False,
            wake_trigger_count=int(snapshot.get("wake_trigger_count", 0)) + 1,
            wake_status="live radio open",
            wake_last_reason=reason,
            radio_source="realtime",
            last_error="",
        )
        if data is not None and getattr(data, "size", 0):
            await realtime.send_audio(data, settings.audio_sample_rate)
        return True

    async def _arm_wake(self, phrase: str) -> None:
        self._wake_armed_until = time.monotonic() + settings.wake_arm_timeout_s
        snapshot = await self.store.snapshot_live()
        count = int(snapshot.get("wake_trigger_count", 0)) + 1
        await self.store.update(
            wake_armed=True,
            wake_trigger_count=count,
            wake_status="armed — say the command",
            wake_last_reason=f'heard "{phrase}"',
            radio_indicator="listening",
            engineer_status="listening",
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
            radio_indicator="idle",
            wake_status=(
                f'ready — say "{settings.wake_phrase.title()}"'
                if settings.wake_enabled
                else "disabled"
            ),
        )

    async def _run_command(self, command: str, source: str) -> None:
        route = self.brain.classify_request(command)
        ack_kind = "standby" if route == "deep" else "copy"
        snapshot = await self.store.snapshot_live()
        latency = dict(snapshot.get("radio_latency", {}))
        latency.update({"route": route, "ack": ack_kind, "stage": "processing"})
        await self.store.update(
            engineer_status="thinking",
            radio_indicator="processing",
            radio_source=source,
            radio_latency=latency,
            last_error="",
        )

        # Start the actual work before the acknowledgement. The short cached
        # radio response fills the perceived vacuum without adding model time.
        brain_task = self.loop.create_task(
            self.brain.ask(command),
            name=f"pitwall-brain-{route}",
        )
        ack_task = self.loop.create_task(
            self.audio.play_ack(ack_kind),
            name="pitwall-radio-ack",
        )
        with contextlib.suppress(Exception):
            await ack_task
        reply = await brain_task
        await self._mark_latency("model_ms")
        await self.speak_text(reply)

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
            wake_status="processing command",
            wake_last_reason=f'accepted via {phrase}',
            last_error="",
        )
        self.busy = True
        try:
            await self._run_command(cleaned, "wake")
        finally:
            self.busy = False
            if not self._signal_pressed:
                await self.store.update(engineer_status="standing by")

    async def _reject_wake(self, reason: str, transcript: str) -> None:
        snapshot = await self.store.snapshot_live()
        count = int(snapshot.get("wake_rejected_count", 0)) + 1
        latency = dict(snapshot.get("radio_latency", {}))
        latency["stage"] = "rejected"
        await self.store.update(
            wake_rejected_count=count,
            wake_last_transcript=transcript,
            wake_last_reason=reason,
            wake_status=f'ready — say "{settings.wake_phrase.title()}"',
            radio_indicator="idle",
            radio_latency=latency,
        )
        self._interaction_finalized_at = 0.0

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
        await self._begin_interaction("ptt")
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
        # L3 opens a speech session just as the wake phrase does, so both radio
        # routes reach the same engineer.
        if await self._start_realtime(data, "ptt"):
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
            if len(self._pending_clips) < self._pending_clips.maxlen:
                self._pending_clips.append(data)
                await self.store.update(
                    radio_queue_depth=len(self._pending_clips),
                    last_error="Radio busy; your latest PTT clip is queued.",
                )
            else:
                await self.store.update(
                    last_error="Radio queue full; repeat the call after the engineer finishes."
                )
            return

        self.busy = True
        source = settings.data_dir / "latest_driver.wav"
        try:
            self._write_wav(source, data)
            await self.store.update(
                engineer_status="transcribing",
                radio_indicator="processing",
            )
            snapshot = await self.store.snapshot_live()
            names = [driver["name"] for driver in snapshot["drivers"]]
            text = await self.audio.transcribe(source, names)
            await self._mark_latency("transcript_ms")
            await self.store.update(radio_last_transcript=text)
            if not text:
                await self.store.update(
                    engineer_status="standing by",
                    radio_indicator="idle",
                    last_error="No usable speech was transcribed.",
                )
                self._interaction_finalized_at = 0.0
                return
            await self._run_command(text, "ptt")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("Voice pipeline failed")
            await self.store.update(
                last_error=str(exc),
                engineer_status="error",
                radio_indicator="error",
            )
        finally:
            self.busy = False
            if not self._signal_pressed:
                await self.store.update(engineer_status="standing by")
            if self._pending_clips and not self._shutdown:
                queued = self._pending_clips.popleft()
                await self.store.update(radio_queue_depth=len(self._pending_clips))
                self._process_task = self.loop.create_task(
                    self._process(queued),
                    name="pitwall-queued-ptt",
                )

    async def speak_text(self, text: str) -> bool:
        if not text.strip() or self._signal_pressed:
            return False

        # With a conversation already open, route the call through that session
        # so the engineer never speaks on two audio paths at once.
        if self.realtime_active and self.realtime is not None:
            if await self.realtime.say(text):
                return True

        async def first_audio() -> None:
            await self.store.update(
                radio_indicator="speaking",
                engineer_status="speaking",
            )
            await self._mark_latency("first_audio_ms")

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
                if settings.voice_stream_tts:
                    return await self.audio.stream_speech(
                        text,
                        target=target,
                        on_first_audio=first_audio,
                    )
                await self.audio.synthesize(text, target, "wav")
                if self._signal_pressed:
                    return False
                await first_audio()
                await self.audio.play_wav(target)
                return True

        self._speech_task = asyncio.create_task(perform())
        delivered = False
        try:
            delivered = await self._speech_task
            return delivered
        except asyncio.CancelledError:
            self.audio.stop_playback()
            raise
        finally:
            self._speech_task = None
            self._tts_playing = False
            self._wake_cooldown_until = (
                time.monotonic() + settings.wake_tts_cooldown_s
            )
            if self._interaction_finalized_at:
                await self._mark_latency("complete_ms")
            self._interaction_finalized_at = 0.0
            if not self._signal_pressed and not self.busy:
                await self.store.update(engineer_status="standing by")
            await self.store.update(radio_indicator="idle")
            if settings.wake_enabled:
                await self.store.update(
                    wake_status=f'ready — say "{settings.wake_phrase.title()}"'
                )
