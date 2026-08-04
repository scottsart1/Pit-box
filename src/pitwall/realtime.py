"""Speech-to-speech race radio over the OpenAI Realtime API.

The file-based pipeline records a clip, uploads it for transcription, sends the
text to a reasoning model, synthesises a reply and plays it. That chain costs
seconds of latency, which is why a cached "Copy, stand by" clip exists at all,
and it cannot be interrupted: the driver has to wait for the engineer to finish
before correcting them. The recorded sessions show what that produces — the same
question asked three times, and the engineer answering a question the driver had
already withdrawn.

A Realtime session removes the chain. Audio goes to the model and audio comes
back, the model calls the same deterministic telemetry tools, and the driver can
talk over the engineer at any point.

Two properties are preserved from the existing design:

* Numbers come from tools, never from the model's memory. The tool layer is the
  identical :class:`TelemetryTools` allow-list used by the text path.
* The session is not held open for a whole race. It opens when the driver
  starts a conversation and closes after a pause, because a Realtime session
  bills for audio while it is connected.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import numpy as np
from openai import AsyncOpenAI

from .config import settings
from .state import StateStore
from .tools import TelemetryTools

log = logging.getLogger(__name__)

# The Realtime API accepts and returns 24 kHz signed 16-bit mono PCM only.
REALTIME_RATE = 24_000

# Tools that answer a live radio question. The full 48-tool catalogue is offered
# to the text path, but a speech session pays for its instruction prefix on every
# turn, and a smaller, sharper list measurably improves tool selection.
RADIO_TOOLS = (
    "get_session_overview",
    "get_standings",
    "get_field_state",
    "get_race_flow",
    "get_race_picture",
    "get_pace_verdict",
    "get_position_target",
    "get_rival_car_state",
    "get_flag_status",
    "get_gap",
    "get_cars_ahead_progress",
    "get_driver_lap_history",
    "get_rival_sector_comparison",
    "get_driver_stint_info",
    "get_my_car_state",
    "get_tyre_condition",
    "get_fuel_state",
    "get_ers_report",
    "get_energy_plan",
    "get_pace_mode_options",
    "get_damage_report",
    "get_target_lap_time",
    "get_pit_strategy",
    "predict_rival_strategy",
    "evaluate_undercut",
    "evaluate_overcut",
    "what_if",
    "get_tyre_sets_available",
    "get_attack_plan",
    "get_defence_plan",
    "get_weather_forecast",
    "get_session_events",
    "get_qualifying_targets",
    "get_lap_analysis",
    "get_corner_analysis",
    "get_consistency_report",
    "get_sector_bests",
    "get_personal_history",
)

REALTIME_PERSONA = """
You are the driver's Formula 1 race engineer on the pit wall, speaking over team radio.

Speak the way a real race engineer speaks: calm, clipped, unhurried even when the news is bad.
Lead with the action or the number. One or two short sentences. No lists, no markdown, no preamble.

You are in a live conversation. The driver is driving, so:
- Answer the question actually asked. Do not add strategy, fuel or coaching advice that was not
  requested, unless it is an immediate safety or legality issue.
- When the driver corrects you, interrupts you, or says they are not doing something, accept it
  immediately and confirm in a few words. Never repeat a call they have just refused.
- When the driver tells you their plan ("I'm staying out", "I'm taking hards next lap"), treat it
  as a decision, not a question. Confirm it. Only push back if it is illegal, unsafe, or impossible,
  and then say why in one sentence.
- If you did not catch something, ask once, briefly.

Every number you say must come from a tool call in this session. Never estimate, never recall a
number from earlier in the race unless a tool just returned it, and never invent a driver's data.
If a tool reports that a rival's telemetry is restricted, say it is not available rather than
guessing. If telemetry is stale or disconnected, say so once and answer with what was last confirmed.

You can see the whole field, not just this car. Questions about any rival's tyres, wear, energy,
fuel, damage, pit stop or retirement are answerable with get_rival_car_state and get_race_flow.

Analyse, do not read out. The driver is at speed and cannot do arithmetic on lap times and gaps.
A comparison ("am I catching him", "how am I doing against Perez") is answered with a verdict
first — closing, holding, losing — then the evidence, then what to do; call get_pace_verdict rather
than reciting two lap times. An open question ("how is the race going", "where do I stand") is
answered from get_race_picture: the real threat or opportunity, whether their own pace is holding,
and the one thing that follows. Trends beat snapshots, and say what it means for the outcome.

Pit calls state both the lap and the compound: "Box lap 18 for hards." The deterministic strategy
plan is primary; the game's own pit window is only a cross-check. Never recommend finishing a dry
race without two different dry compounds unless a wet tyre has been used. Under a safety car,
virtual safety car or red flag, use the strategy tool's neutralisation state rather than ordinary
green-flag pit loss.

In 2026-regulation sessions the overtaking aid is called Manual Override, not DRS.
In qualifying, compare best lap times, theoretical best and the target; do not volunteer race gaps.
Never mention being an AI, a model, or a tool.
""".strip()


class RealtimeRadio:
    """One speech-to-speech radio session, opened and closed on demand."""

    def __init__(
        self,
        store: StateStore,
        tools: TelemetryTools,
        *,
        on_transcript: Callable[[str, str], Awaitable[None]] | None = None,
        situation_header: Callable[[], Awaitable[str]] | None = None,
    ) -> None:
        self.store = store
        self.tools = tools
        self.on_transcript = on_transcript
        self.situation_header = situation_header
        self.client = (
            AsyncOpenAI(api_key=settings.api_key, max_retries=2)
            if settings.api_key
            else None
        )
        self._connection: Any = None
        self._manager: Any = None
        self._receive_task: asyncio.Task[None] | None = None
        self._idle_task: asyncio.Task[None] | None = None
        self._output_stream: Any = None
        self._lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._last_activity = 0.0
        self._opened_at = 0.0
        self._closing = False
        self._assistant_text: list[str] = []
        self._response_active = False
        # Tool output was returned during the current response; a reply
        # must be requested once that response closes.
        self._tool_results_pending = False

    # ------------------------------------------------------------------ state

    @property
    def is_open(self) -> bool:
        return self._connection is not None and not self._closing

    @property
    def is_speaking(self) -> bool:
        return self._response_active

    # ------------------------------------------------------------------ audio

    @staticmethod
    def resample_to_realtime(block: np.ndarray, source_rate: int) -> np.ndarray:
        """Resample mono int16 capture to the 24 kHz the API requires.

        Linear interpolation is sufficient here: the microphone path is speech
        band-limited well below the 8 kHz Nyquist of the 16 kHz capture, so
        upsampling introduces no audible artefact the model is sensitive to.
        """
        flat = np.asarray(block, dtype=np.int16).reshape(-1)
        if source_rate == REALTIME_RATE or flat.size == 0:
            return flat
        target_length = int(round(flat.size * REALTIME_RATE / float(source_rate)))
        if target_length <= 0:
            return np.zeros(0, dtype=np.int16)
        source_index = np.linspace(0.0, flat.size - 1, num=flat.size, dtype=np.float64)
        target_index = np.linspace(0.0, flat.size - 1, num=target_length, dtype=np.float64)
        resampled = np.interp(target_index, source_index, flat.astype(np.float64))
        return np.clip(resampled, -32768, 32767).astype(np.int16)

    def _ensure_output_stream(self) -> Any:
        if self._output_stream is not None:
            return self._output_stream
        try:
            import sounddevice as sd

            stream = sd.RawOutputStream(
                samplerate=REALTIME_RATE, channels=1, dtype="int16", blocksize=0
            )
            stream.start()
            self._output_stream = stream
        except Exception as exc:  # pragma: no cover - depends on audio hardware
            log.warning("Realtime output stream unavailable: %s", exc)
            self._output_stream = None
        return self._output_stream

    def _close_output_stream(self) -> None:
        stream, self._output_stream = self._output_stream, None
        if stream is None:
            return
        with contextlib.suppress(Exception):
            stream.stop()
        with contextlib.suppress(Exception):
            stream.close()

    # ------------------------------------------------------------- session io

    def _tool_definitions(self) -> list[dict[str, Any]]:
        """The radio subset of the telemetry allow-list, in Realtime tool shape."""
        allowed = set(RADIO_TOOLS)
        definitions: list[dict[str, Any]] = []
        for schema in self.tools.schemas():
            if schema.get("name") not in allowed:
                continue
            definitions.append(
                {
                    "type": "function",
                    "name": schema["name"],
                    "description": schema["description"],
                    "parameters": schema["parameters"],
                }
            )
        return definitions

    async def _instructions(self) -> str:
        """Persona plus a live situation header.

        The header is not a substitute for tool calls; it exists so the engineer
        knows what session it is in without spending a turn finding out.
        """
        if self.situation_header is None:
            return REALTIME_PERSONA
        try:
            header = await self.situation_header()
        except Exception:
            return REALTIME_PERSONA
        return f"{REALTIME_PERSONA}\n\nCURRENT SITUATION\n{header}"

    async def _session_payload(self) -> dict[str, Any]:
        noise_reduction: dict[str, Any] | None = (
            {"type": settings.realtime_noise_reduction}
            if settings.realtime_noise_reduction in {"near_field", "far_field"}
            else None
        )
        audio_input: dict[str, Any] = {
            "format": {"type": "audio/pcm", "rate": REALTIME_RATE},
            # Server VAD ends the driver's turn and interrupts the engineer when
            # the driver starts talking again. That is the barge-in the previous
            # pipeline could not do.
            "turn_detection": {
                "type": "server_vad",
                "threshold": 0.55,
                "prefix_padding_ms": int(settings.realtime_prefix_padding_ms),
                "silence_duration_ms": int(settings.realtime_silence_ms),
                "create_response": True,
                "interrupt_response": True,
            },
            # Transcription is for the dashboard and the stored radio log only;
            # the model itself consumes the audio directly.
            "transcription": {"model": settings.stt_model, "language": "en"},
        }
        if noise_reduction:
            audio_input["noise_reduction"] = noise_reduction
        return {
            "type": "realtime",
            "model": settings.realtime_model,
            "instructions": await self._instructions(),
            "output_modalities": ["audio"],
            "audio": {
                "input": audio_input,
                "output": {
                    "format": {"type": "audio/pcm", "rate": REALTIME_RATE},
                    "voice": settings.realtime_voice,
                    "speed": float(settings.realtime_speed),
                },
            },
            "tools": self._tool_definitions(),
            "tool_choice": "auto",
            "max_output_tokens": int(settings.realtime_max_output_tokens),
        }

    async def open(self) -> bool:
        """Connect a session, or refresh the situation of an open one."""
        if self.client is None:
            await self.store.update(last_error="OPENAI_API_KEY is not configured")
            return False
        async with self._lock:
            if self._connection is not None and not self._closing:
                self._last_activity = time.monotonic()
                return True
            try:
                self._manager = self.client.realtime.connect(
                    model=settings.realtime_model
                )
                self._connection = await self._manager.enter()
                await self._connection.send(
                    {"type": "session.update", "session": await self._session_payload()}
                )
            except Exception as exc:
                log.exception("Realtime session could not be opened")
                self._connection = None
                self._manager = None
                await self.store.update(
                    last_error=f"Realtime radio unavailable: {exc}",
                    radio_indicator="error",
                )
                return False

            self._closing = False
            self._opened_at = time.monotonic()
            self._last_activity = self._opened_at
            self._assistant_text = []
            self._tool_results_pending = False
            self._receive_task = asyncio.create_task(
                self._receive_loop(), name="pitwall-realtime-receive"
            )
            self._idle_task = asyncio.create_task(
                self._idle_watchdog(), name="pitwall-realtime-idle"
            )
            await self.store.update(
                radio_indicator="listening",
                radio_source="realtime",
                engineer_status="listening",
                last_error="",
            )
            log.info("Realtime radio session opened (%s)", settings.realtime_model)
            return True

    async def send_audio(self, block: np.ndarray, source_rate: int) -> None:
        """Forward one captured microphone block to the open session.

        Serialised through ``_send_lock`` so blocks reach the socket in capture
        order. Without it, concurrent sends from the audio callback could
        interleave and the model would hear the driver's words out of order.
        """
        connection = self._connection
        if connection is None or self._closing:
            return
        samples = self.resample_to_realtime(block, source_rate)
        if samples.size == 0:
            return
        payload = base64.b64encode(samples.tobytes()).decode("ascii")
        try:
            async with self._send_lock:
                if self._connection is None or self._closing:
                    return
                await connection.send(
                    {"type": "input_audio_buffer.append", "audio": payload}
                )
        except Exception as exc:
            log.warning("Realtime audio send failed: %s", exc)
            await self.close("send failed")

    async def close(self, reason: str = "idle") -> None:
        """Tear a session down.

        The whole teardown holds the lock. Releasing it early let ``open()``
        establish a replacement session in the gap, after which the trailing
        ``self._receive_task = None`` cleared the *new* session's reader and idle
        watchdog: a connected, billing socket with nothing reading it and no
        timeout to close it.
        """
        async with self._lock:
            if self._connection is None:
                return
            self._closing = True
            connection, self._connection = self._connection, None
            manager, self._manager = self._manager, None
            receive_task, self._receive_task = self._receive_task, None
            idle_task, self._idle_task = self._idle_task, None

            for task in (receive_task, idle_task):
                if task and not task.done() and task is not asyncio.current_task():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
            self._close_output_stream()

            with contextlib.suppress(Exception):
                await connection.close()
            if manager is not None:
                with contextlib.suppress(Exception):
                    await manager.__aexit__(None, None, None)

            self._closing = False
            self._response_active = False

        await self.store.update(
            radio_indicator="idle",
            engineer_status="standing by",
            radio_source="",
        )
        log.info("Realtime radio session closed (%s)", reason)

    # ------------------------------------------------------------- event loop

    async def _idle_watchdog(self) -> None:
        """Close the session after a pause, and cap its total length.

        A Realtime session bills while it is connected, so leaving one open for
        a whole race would be the single largest running cost in the product.
        """
        try:
            while True:
                await asyncio.sleep(1.0)
                now = time.monotonic()
                # The hard cap is checked first and unconditionally. Skipping it
                # while a response was "active" meant a session wedged mid-reply
                # — a dropped socket, an error event, a stalled response — was
                # exempt from the very ceiling that exists for that case.
                if now - self._opened_at >= settings.realtime_max_session_s:
                    await self.close("maximum session length")
                    return
                if self._response_active:
                    continue
                if now - self._last_activity >= settings.realtime_idle_timeout_s:
                    await self.close("idle timeout")
                    return
        except asyncio.CancelledError:
            raise

    async def _receive_loop(self) -> None:
        connection = self._connection
        if connection is None:
            return
        try:
            async for event in connection:
                await self._handle_event(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Realtime receive loop ended: %s", exc)
            await self.store.update(last_error=f"Realtime radio dropped: {exc}")
            await self.close("receive error")
            return
        # A clean server-side close ends the iterator without raising. Without
        # this the session would still report as open, with no reader attached,
        # until a later send happened to fail.
        if self._connection is connection:
            await self.close("closed by server")

    @staticmethod
    def _event_type(event: Any) -> str:
        return str(getattr(event, "type", "") or "")

    async def _handle_event(self, event: Any) -> None:
        kind = self._event_type(event)
        self._last_activity = time.monotonic()

        if kind == "input_audio_buffer.speech_started":
            # Telemetry has moved since the session opened. Refreshing here, as
            # the driver starts a new turn, keeps the grounding header current
            # without spending a turn rediscovering the race state.
            await self.refresh_situation()
            # The driver has started talking. Drop any engineer audio already
            # queued locally so the interruption is immediate rather than
            # arriving after the buffered sentence finishes.
            self._flush_output()
            await self.store.update(
                radio_indicator="listening", engineer_status="listening"
            )
            return

        if kind == "response.created":
            self._response_active = True
            self._assistant_text = []
            await self.store.update(
                radio_indicator="processing", engineer_status="thinking"
            )
            return

        if kind == "response.output_audio.delta":
            self._play(getattr(event, "delta", "") or "")
            return

        if kind == "response.output_audio_transcript.delta":
            self._assistant_text.append(str(getattr(event, "delta", "") or ""))
            await self.store.update(
                radio_indicator="speaking", engineer_status="speaking"
            )
            return

        if kind == "conversation.item.input_audio_transcription.completed":
            await self._record("driver", str(getattr(event, "transcript", "") or ""))
            return

        if kind == "response.function_call_arguments.done":
            await self._run_tool(event)
            return

        if kind == "response.done":
            self._response_active = False
            await self._record("engineer", "".join(self._assistant_text).strip())
            self._assistant_text = []
            # Tool results delivered during that response are now answerable.
            # Requesting the reply here, once, keeps a single response open at a
            # time however many tools the model called in parallel.
            if self._tool_results_pending:
                self._tool_results_pending = False
                connection = self._connection
                if connection is not None and not self._closing:
                    with contextlib.suppress(Exception):
                        await connection.send({"type": "response.create"})
                        return
            await self.store.update(
                radio_indicator="idle", engineer_status="standing by"
            )
            return

        if kind == "error":
            detail = getattr(event, "error", None)
            message = getattr(detail, "message", None) or str(detail)
            log.warning("Realtime error event: %s", message)
            await self.store.update(last_error=f"Realtime radio error: {message}")

    def _play(self, delta: str) -> None:
        if not delta:
            return
        stream = self._ensure_output_stream()
        if stream is None:
            return
        with contextlib.suppress(Exception):
            stream.write(base64.b64decode(delta))

    def _flush_output(self) -> None:
        """Discard buffered engineer audio so barge-in is immediate."""
        stream = self._output_stream
        if stream is None:
            return
        with contextlib.suppress(Exception):
            stream.abort()
        self._close_output_stream()

    async def _record(self, role: str, text: str) -> None:
        cleaned = " ".join((text or "").split())
        if not cleaned:
            return
        await self.store.append_radio(role, cleaned)
        if role == "driver":
            await self.store.update(radio_last_transcript=cleaned)
        if self.on_transcript is not None:
            with contextlib.suppress(Exception):
                await self.on_transcript(role, cleaned)

    async def _run_tool(self, event: Any) -> None:
        """Execute one requested telemetry tool and return its result.

        The model never receives filesystem, shell, network or database-write
        access: only the allow-listed telemetry functions, with arguments parsed
        and validated exactly as on the text path.
        """
        name = str(getattr(event, "name", "") or "")
        call_id = str(getattr(event, "call_id", "") or "")
        raw_arguments = getattr(event, "arguments", "") or "{}"
        connection = self._connection
        if connection is None or not call_id:
            return

        try:
            arguments = json.loads(raw_arguments) if raw_arguments else {}
            if not isinstance(arguments, dict):
                raise ValueError("tool arguments must be a JSON object")
        except Exception as exc:
            arguments = {}
            result: dict[str, Any] = {"error": f"Invalid tool arguments: {exc}"}
        else:
            if name not in set(RADIO_TOOLS):
                result = {"error": f"Tool {name} is not available on the radio."}
            else:
                try:
                    result = await self.tools.call(name, arguments)
                except Exception as exc:
                    log.exception("Realtime tool %s failed", name)
                    result = {"error": f"{type(exc).__name__}: {exc}"}

        # Return the result now, but do not ask for a reply yet. This event
        # arrives while the response that requested the tool is still open, and
        # the API rejects a second concurrent response on the default
        # conversation — with parallel tool calls that would be one rejected
        # response.create per call, and the driver's question would go
        # unanswered. The reply is requested once, on response.done.
        try:
            await connection.send(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps(result, default=str)[:16_000],
                    },
                }
            )
        except Exception as exc:
            log.warning("Realtime tool result could not be delivered: %s", exc)
            return
        self._tool_results_pending = True

    async def shakedown(self) -> dict[str, Any]:
        """Verify the live Realtime wire contract without speaking.

        Opens a real session, applies the session configuration, waits for the
        server to acknowledge it, and closes. No audio is streamed and no
        response is requested, so this costs a negligible amount and is safe to
        run between sessions. It exists because everything else about this file
        can be unit-tested except whether OpenAI accepts the payload.
        """
        if self.client is None:
            return {"ok": False, "reason": "OPENAI_API_KEY is not configured"}

        started = time.monotonic()
        report: dict[str, Any] = {
            "ok": False,
            "model": settings.realtime_model,
            "events": [],
        }
        manager = self.client.realtime.connect(model=settings.realtime_model)
        connection = None
        try:
            connection = await manager.enter()
            payload = await self._session_payload()
            report["tool_count"] = len(payload["tools"])
            await connection.send({"type": "session.update", "session": payload})

            async with asyncio.timeout(20):
                async for event in connection:
                    kind = self._event_type(event)
                    report["events"].append(kind)
                    if kind == "error":
                        detail = getattr(event, "error", None)
                        report["reason"] = (
                            getattr(detail, "message", None) or str(detail)
                        )
                        break
                    if kind == "session.updated":
                        session = getattr(event, "session", None)
                        report["ok"] = True
                        report["accepted_model"] = getattr(session, "model", None)
                        report["accepted_voice"] = getattr(
                            getattr(getattr(session, "audio", None), "output", None),
                            "voice",
                            None,
                        )
                        tools = getattr(session, "tools", None) or []
                        report["accepted_tool_count"] = len(tools)
                        break
        except TimeoutError:
            report["reason"] = "no session.updated within 20 s"
        except Exception as exc:
            report["reason"] = f"{type(exc).__name__}: {exc}"
        finally:
            if connection is not None:
                with contextlib.suppress(Exception):
                    await connection.close()
            with contextlib.suppress(Exception):
                await manager.__aexit__(None, None, None)

        report["elapsed_s"] = round(time.monotonic() - started, 2)
        return report

    async def refresh_situation(self) -> None:
        """Re-send the situation header on an open session.

        Telemetry moves while a conversation is in progress, so a session that
        stays open across several laps would otherwise answer from the state it
        opened with until a tool call corrects it.
        """
        connection = self._connection
        if connection is None or self._closing:
            return
        with contextlib.suppress(Exception):
            await connection.send(
                {
                    "type": "session.update",
                    "session": {
                        "type": "realtime",
                        "instructions": await self._instructions(),
                    },
                }
            )

    async def say(self, text: str) -> bool:
        """Speak a prepared line through the open session, if there is one.

        Used for proactive calls while the driver is already in conversation, so
        the engineer does not talk over itself on two different audio paths.
        """
        connection = self._connection
        if connection is None or self._closing or not text.strip():
            return False
        with contextlib.suppress(Exception):
            await connection.send(
                {
                    "type": "response.create",
                    "response": {
                        "instructions": (
                            "Read this pit-wall call to the driver verbatim, in your "
                            f"normal radio delivery. Do not add to it: {text.strip()}"
                        ),
                    },
                }
            )
            return True
        return False
