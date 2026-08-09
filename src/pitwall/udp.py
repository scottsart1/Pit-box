from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from f1.packets import SESSIONS, resolve
from f1.packets import TRACKS as F1_TRACKS

from .capture_service import CaptureService
from .forwarding import DatagramForwarder
from .identity import team_name
from .networking import (
    HeaderInspection,
    PacketHealthKey,
    PacketHealthTracker,
    inspect_2026_header,
)
from .session_assembler import (
    EventStamp,
    FlashbackEvent,
    LapEvent,
    ParticipantEvent,
    SampleEvent,
    SessionAssembler,
    SessionEvent,
)
from .state import StateStore

log = logging.getLogger(__name__)

TRACKS = {int(track_id): name for track_id, name in F1_TRACKS.items()}
SESSION_TYPES = {int(session_type): label for session_type, label in SESSIONS.items()}
SESSION_LENGTHS = {
    0: "None",
    2: "Very Short",
    3: "Short",
    4: "Medium",
    5: "Medium Long",
    6: "Long",
    7: "Full",
}
OVERRIDE_LABELS = {
    "race": "Race",
    "sprint": "Sprint",
    "qualifying": "Qualifying",
    "practice": "Practice",
    "time_trial": "Time Trial",
}
# Session type identifiers are more reliable than display-label matching, but
# Race/Race 2/Race 3 are sequence slots rather than fixed "GP versus sprint"
# meanings. On a sprint weekend the Sprint can be Race (15) and the Grand Prix
# Race 2 (16). ``classify_session`` therefore combines these IDs with the
# weekend structure instead of permanently labelling Race 2/3 as sprint.
SESSION_MODE_BY_TYPE_ID = {
    0: "idle",
    1: "practice",
    2: "practice",
    3: "practice",
    4: "practice",
    5: "qualifying",
    6: "qualifying",
    7: "qualifying",
    8: "qualifying",
    9: "qualifying",
    # Sprint Shootout sessions are qualifying sessions that set the sprint grid.
    10: "qualifying",
    11: "qualifying",
    12: "qualifying",
    13: "qualifying",
    14: "qualifying",
    15: "race",
    16: "race",
    17: "race",
    18: "time_trial",
}
WEATHER = {
    0: "Clear",
    1: "Light cloud",
    2: "Overcast",
    3: "Light rain",
    4: "Heavy rain",
    5: "Storm",
}
SAFETY_CAR = {0: "none", 1: "full", 2: "virtual", 3: "formation"}
# The SAME enum as SAFETY_CAR above, and it must stay identical. It was
# previously written without the "none" member, which shifted every value by
# one: a full safety car was announced to the driver as "virtual" and a virtual
# one as "formation". It also silently broke the restart call, because the
# ending-phase guard compares this event-derived label against the
# session-derived one and the two could never agree, so sc_restart had never
# once fired. Every SCAR payload recorded on this machine corroborates the
# shift: safety_car_type 0 only ever appears alongside "resume race", and
# type 3 never appears at all.
EVENT_SAFETY_CAR = dict(SAFETY_CAR)
SAFETY_CAR_EVENTS = {0: "deployed", 1: "returning", 2: "returned", 3: "resume"}
VISUAL_COMPOUNDS = {
    16: "SOFT",
    17: "MEDIUM",
    18: "HARD",
    7: "INTER",
    8: "WET",
    9: "WET",
    19: "C5",
    20: "C4",
    21: "C3",
    22: "C2",
    23: "C1",
}
STATUS = {0: "garage", 1: "flying", 2: "in lap", 3: "out lap", 4: "on track"}
FIA_FLAGS = {-1: "invalid", 0: "none", 1: "green", 2: "blue", 3: "yellow", 4: "red"}
# LapData.result_status. Distinguishing "retired" from "not classified" matters
# for race flow: a retirement permanently removes a rival from the strategy
# picture, while an inactive car may still rejoin.
RESULT_STATUS = {
    0: "invalid",
    1: "inactive",
    2: "active",
    3: "finished",
    4: "did not finish",
    5: "disqualified",
    6: "not classified",
    7: "retired",
}
RETIRED_RESULT_STATUS = frozenset({4, 5, 6, 7})
# Event codes that carry no race-narrative value but arrive at controller or
# packet frequency. BUTN is consumed directly by the PTT callback; recording it
# flooded both the in-memory events log (starving "any race updates" of real
# incidents) and the session_events table (40 000+ rows).
UNLOGGED_EVENTS = frozenset({"BUTN"})


def normalize_wheels(values: Iterable[Any]) -> list[Any]:
    """EA packets use RL, RR, FL, FR; Your Pit Box stores FL, FR, RL, RR."""
    raw = list(values)
    if len(raw) != 4:
        return raw
    return [raw[2], raw[3], raw[0], raw[1]]


def mode_profile(session_type: str) -> str:
    """Label-based fallback classifier for when the type id is unrecognised.

    ``SESSION_MODE_BY_TYPE_ID`` is the primary source; this matches on "shoot"
    rather than "shootout" so the truncated "One-Shot Sprint Shoot" label is
    still recognised as a qualifying session instead of falling through to
    the sprint branch below.
    """
    lowered = session_type.lower()
    if "practice" in lowered:
        return "practice"
    if "qualifying" in lowered or "shoot" in lowered:
        return "qualifying"
    if "sprint" in lowered:
        return "sprint"
    if "race" in lowered:
        return "race"
    if "time trial" in lowered:
        return "time_trial"
    return "idle"


def classify_session(
    *,
    raw_type_id: int,
    total_laps: int,
    current_lap: int,
    session_time_left_s: int,
    session_duration_s: int,
    session_length_id: int,
    weekend_structure: Iterable[int] | None = None,
    override: str = "auto",
) -> tuple[str, str, str, str]:
    """Return effective label, mode profile, source and explanation.

    Use the f1-packets 2026 enum as the primary source. The race-distance
    heuristic remains only as a defensive fallback for corrupt or unsupported
    packet data.
    """
    raw_label = SESSION_TYPES.get(raw_type_id, f"Session {raw_type_id}")
    normalized_override = (override or "auto").strip().lower()
    if normalized_override in OVERRIDE_LABELS:
        label = OVERRIDE_LABELS[normalized_override]
        return (
            label,
            normalized_override,
            "manual",
            f"Manual dashboard override; raw UDP type was {raw_label} ({raw_type_id}).",
        )

    raw_mode = SESSION_MODE_BY_TYPE_ID.get(int(raw_type_id)) or mode_profile(raw_label)
    weekend_session_ids = [int(value) for value in (weekend_structure or [])]
    # The first race slot is the Sprint when the same weekend contains a later
    # race slot. The later Race 2/Race 3 slot remains the Grand Prix. Without
    # weekend structure, default every race slot to the safer grand-prix mode:
    # incorrectly waiving the two-compound rule is worse than a visible manual
    # override being required for an unusual/unsupported packet sequence.
    try:
        first_race_index = weekend_session_ids.index(15)
    except ValueError:
        first_race_index = -1
    has_later_race_slot = first_race_index >= 0 and any(
        value in {16, 17} for value in weekend_session_ids[first_race_index + 1 :]
    )
    if int(raw_type_id) == 15 and has_later_race_slot:
        return (
            "Sprint",
            "sprint",
            "udp",
            (
                "Weekend structure contains a later race slot "
                f"({weekend_session_ids}); treating Race (15) as the Sprint."
            ),
        )

    longest_clock = max(int(session_time_left_s), int(session_duration_s))
    race_distance_is_coherent = (
        int(total_laps) >= max(3, int(current_lap)) and longest_clock >= 3_600
    )

    # Only infer a race when the packet type is unknown/idle. Recognised practice,
    # qualifying and time-trial enums are authoritative even when the game exposes
    # a long clock or a lap count that superficially resembles a race.
    if raw_mode == "idle" and race_distance_is_coherent:
        return (
            "Race",
            "race",
            "inferred",
            (
                f"Raw UDP type {raw_label} ({raw_type_id}) conflicts with "
                f"{total_laps} total laps and a {longest_clock // 60}-minute session clock."
            ),
        )

    return (
        raw_label,
        raw_mode,
        "udp",
        (
            f"Using raw UDP session type {raw_label} ({raw_type_id}); "
            f"session length {SESSION_LENGTHS.get(session_length_id, session_length_id)}."
        ),
    )


def _event_code(packet: Any) -> str:
    return (
        bytes(packet.event_string_code).decode("ascii", errors="ignore").rstrip("\x00")
    )


def _name(raw: Any) -> str:
    return (
        bytes(raw).split(b"\0", 1)[0].decode("utf-8", errors="replace").strip()
        or "Unknown"
    )


def _ms(minutes: int, milliseconds: int) -> int:
    return int(minutes) * 60_000 + int(milliseconds)


def _packet_player_index(packet: Any, values: Any) -> int | None:
    """Return a safe player index, ignoring the 255 spectator sentinel."""
    index = int(packet.header.player_car_index)
    try:
        count = len(values)
    except TypeError:
        count = 24
    return index if 0 <= index < count else None


@dataclass(frozen=True, slots=True)
class ReceivedDatagram:
    """Immutable input handed from the socket callback to parser consumers."""

    data: bytes
    source: tuple[str, int]
    received_monotonic_ns: int
    received_wall_ns: int
    inspection: HeaderInspection

    @property
    def received_monotonic(self) -> float:
        return self.received_monotonic_ns / 1_000_000_000

    @property
    def received_wall(self) -> float:
        return self.received_wall_ns / 1_000_000_000

    @property
    def health_key(self) -> PacketHealthKey | None:
        header = self.inspection.header
        if header is None:
            return None
        return PacketHealthKey(
            int(header.session_uid),
            int(header.packet_id),
            self.source[0],
            self.source[1],
        )


class F1DatagramProtocol(asyncio.DatagramProtocol):
    """Live F1 2026 UDP receiver using the f1-packets parser."""

    def __init__(
        self,
        store: StateStore,
        on_button_status: Callable[[int], None] | None = None,
        queue_capacity: int = 2048,
        on_player_lap_history: (
            Callable[[int, list[dict[str, Any]]], Awaitable[Any]] | None
        ) = None,
        on_final_classification: Callable[[], Awaitable[Any]] | None = None,
        on_qualifying_lap: Callable[[], Awaitable[Any]] | None = None,
        packet_health: PacketHealthTracker | None = None,
        forwarder: DatagramForwarder | None = None,
        capture_service: CaptureService | None = None,
        session_assembler: SessionAssembler | None = None,
        capture_mode: str = "balanced",
        on_session_key_change: Callable[[str], None] | None = None,
    ) -> None:
        self.store = store
        self.on_button_status = on_button_status
        self.on_player_lap_history = on_player_lap_history
        self.on_final_classification = on_final_classification
        self.on_qualifying_lap = on_qualifying_lap
        self.packet_health = packet_health
        self.forwarder = forwarder
        self.capture_service = capture_service
        self.session_assembler = session_assembler
        self.capture_mode = str(capture_mode)
        self.on_session_key_change = on_session_key_change
        self.loop = asyncio.get_running_loop()
        self.transport: asyncio.DatagramTransport | None = None
        self.packet_queue: asyncio.Queue[ReceivedDatagram] = asyncio.Queue(
            maxsize=queue_capacity
        )
        self.consumer_task: asyncio.Task[None] | None = None
        self._stats_counter = 0
        self._unhandled_packets: set[str] = set()
        # Session UIDs whose chequered flag has already been recorded. The game
        # repeats the final-classification packet for as long as the results
        # screen is shown, which previously re-appended CHQF and re-wrote the
        # session row on every repeat.
        self._classified_sessions: set[str] = set()
        self._qualifying_debrief_laps: set[tuple[int, int]] = set()
        self._briefing_tasks: set[asyncio.Task[Any]] = set()
        self._last_header_error: str | None = None
        self._assembler_laps = [0] * 24
        self._assembler_distances = [0.0] * 24
        self._assembler_active_cars = 0
        self._assembler_restricted: set[int] = set()
        self._assembler_context: list[dict[str, Any]] = [{} for _ in range(24)]
        self._assembler_invalid = [False] * 24
        self._assembler_pit_context = [False] * 24
        self._assembler_flag_context = [False] * 24
        self._assembler_fuel_start: list[float | None] = [None] * 24
        self._assembler_fuel_end: list[float | None] = [None] * 24
        self._assembler_global_flag_context = False
        self._assembler_session_id: str | None = None
        self._assembler_sealed_session_id: str | None = None
        self._assembler_errors: set[str] = set()
        self._accepting_datagrams = True
        self._receiver_queue_drops = 0

    @property
    def receiver_queue_drops(self) -> int:
        """Datagrams rejected specifically because the parser queue was full."""

        return self._receiver_queue_drops

    def _reset_archive_tracking(self) -> None:
        self._assembler_laps = [0] * 24
        self._assembler_distances = [0.0] * 24
        self._assembler_active_cars = 0
        self._assembler_restricted.clear()
        self._assembler_context = [{} for _ in range(24)]
        self._assembler_invalid = [False] * 24
        self._assembler_pit_context = [False] * 24
        self._assembler_flag_context = [False] * 24
        self._assembler_fuel_start = [None] * 24
        self._assembler_fuel_end = [None] * 24
        self._assembler_global_flag_context = False

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]
        self._accepting_datagrams = True
        self.consumer_task = self.loop.create_task(
            self._consume_packets(), name="pitwall-udp-consumer"
        )
        self.loop.create_task(
            self.store.set_packet_queue_stats(0, self.packet_queue.maxsize)
        )
        log.info("F1 UDP listening on %s", transport.get_extra_info("sockname"))

    def connection_lost(self, exc: Exception | None) -> None:
        if exc:
            log.warning("F1 UDP listener stopped: %s", exc)
        self._accepting_datagrams = False
        if self.consumer_task:
            self.consumer_task.cancel()
            self.consumer_task = None
        for task in self._briefing_tasks:
            task.cancel()
        self._briefing_tasks.clear()
        self.loop.create_task(self.store.update(connected=False, packet_rate_hz=0.0))

    def datagram_received(self, data: bytes, addr: Any) -> None:
        if not self._accepting_datagrams:
            return
        monotonic_ns = time.monotonic_ns()
        wall_ns = time.time_ns()
        try:
            source = (str(addr[0]), int(addr[1]))
        except (IndexError, TypeError, ValueError):
            source = ("0.0.0.0", 0)
        if self.packet_health is not None:
            inspection = self.packet_health.observe_datagram(
                data,
                source,
                received_monotonic=monotonic_ns / 1_000_000_000,
                received_wall=wall_ns / 1_000_000_000,
                parsed=None,
            )
        else:
            inspection = inspect_2026_header(data)

        # Both optional consumers are bounded and return immediately. They see
        # the exact bytes received; no parsed object is ever reserialized.
        if self.forwarder is not None:
            self.forwarder.submit(data)
        if self.capture_service is not None:
            self.capture_service.submit(
                data,
                source,
                monotonic_ns=monotonic_ns,
                wall_ns=wall_ns,
            )

        if not inspection.valid:
            message = inspection.message or "Unsupported F1 packet format/version"
            if message != self._last_header_error:
                self._last_header_error = message
                self.loop.create_task(self.store.update(last_error=message))
            return

        received = ReceivedDatagram(
            bytes(data), source, monotonic_ns, wall_ns, inspection
        )

        try:
            self.packet_queue.put_nowait(received)
        except asyncio.QueueFull:
            # Preserve packet order and keep the event loop responsive. A full
            # queue is visible in health telemetry rather than spawning another
            # task for every datagram.
            self._receiver_queue_drops += 1
            self.loop.create_task(self.store.increment_dropped_packets())

    async def drain_before_close(self, timeout_s: float = 2.0) -> None:
        """Stop admission and let already accepted datagrams finish parsing."""

        self._accepting_datagrams = False
        task = self.consumer_task
        if task is None:
            return
        try:
            await asyncio.wait_for(
                self.packet_queue.join(), timeout=max(0.0, timeout_s)
            )
        except TimeoutError:
            # Optional queued work must not make listener shutdown unbounded.
            while True:
                try:
                    self.packet_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                self.packet_queue.task_done()
                await self.store.increment_dropped_packets()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        self.consumer_task = None

    async def _consume_packets(self) -> None:
        while True:
            received = await self.packet_queue.get()
            try:
                try:
                    packet = resolve(received.data)
                except KeyError:
                    if (
                        self.packet_health is not None
                        and received.health_key is not None
                    ):
                        self.packet_health.mark_parse_result(
                            received.health_key,
                            valid=False,
                            error_code="unsupported_packet",
                        )
                    await self.store.update(
                        last_error="Unsupported F1 packet format/version"
                    )
                    await self.store.increment_dropped_packets()
                    continue
                except Exception as exc:
                    if (
                        self.packet_health is not None
                        and received.health_key is not None
                    ):
                        self.packet_health.mark_parse_result(
                            received.health_key,
                            valid=False,
                            error_code=type(exc).__name__,
                        )
                    log.debug(
                        "Dropped malformed packet from %s: %s",
                        received.source,
                        exc,
                    )
                    await self.store.increment_dropped_packets()
                    continue
                if self.packet_health is not None and received.health_key is not None:
                    self.packet_health.mark_parse_result(
                        received.health_key, valid=True
                    )
                await self._handle(packet, received)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("UDP packet handler failed: %s", exc)
                await self.store.update(last_error=f"UDP packet handler failed: {exc}")
                await self.store.increment_dropped_packets()
            finally:
                self.packet_queue.task_done()
                self._stats_counter += 1
                if self._stats_counter >= 32:
                    self._stats_counter = 0
                    await self.store.set_packet_queue_stats(
                        self.packet_queue.qsize(), self.packet_queue.maxsize
                    )

    async def _handle(
        self, packet: Any, received: ReceivedDatagram | None = None
    ) -> None:
        header = packet.header
        await self.store.mark_packet(
            header.packet_format,
            header.game_year,
            header.session_uid,
            int(getattr(header, "packet_id", -1)),
            int(getattr(header, "frame_identifier", 0)),
            int(getattr(header, "overall_frame_identifier", 0)),
        )
        name = packet.__class__.__name__
        if self.session_assembler is not None:
            try:
                self._normalise_for_archive(packet, received)
                session = self.session_assembler.session
                if session is not None:
                    await self.store.synchronize_session_epoch(
                        int(session.game_session_uid),
                        session.restart_epoch,
                        self.session_assembler.timeline_epoch,
                    )
            except Exception as exc:  # noqa: BLE001 - archive cannot break live state
                marker = f"{name}:{type(exc).__name__}:{exc}"
                if marker not in self._assembler_errors:
                    self._assembler_errors.add(marker)
                    log.warning(
                        "Full-field normalization skipped for %s: %s", name, exc
                    )
        handler = getattr(self, f"handle_{name}", None)
        if handler:
            await handler(packet)
        elif name not in self._unhandled_packets:
            # Report each unknown packet type once. Silently discarding them
            # makes a newly added telemetry packet invisible during diagnosis.
            self._unhandled_packets.add(name)
            log.info("No handler for %s; packet ignored", name)

    @staticmethod
    def _archive_stamp(packet: Any, received: ReceivedDatagram | None) -> EventStamp:
        header = packet.header
        return EventStamp(
            session_uid=int(getattr(header, "session_uid", 0)),
            frame_identifier=int(getattr(header, "frame_identifier", 0)),
            overall_frame_identifier=int(
                getattr(header, "overall_frame_identifier", 0)
            ),
            session_time_s=float(getattr(header, "session_time", 0.0)),
            monotonic_ns=(
                received.received_monotonic_ns
                if received is not None
                else time.monotonic_ns()
            ),
            wall_ns=(
                received.received_wall_ns if received is not None else time.time_ns()
            ),
        )

    def _archive_event(self, event: Any) -> None:
        assembler = self.session_assembler
        if assembler is None:
            return
        current = assembler.session
        sealed = self._assembler_sealed_session_id
        if sealed is not None:
            incoming_uid = str(event.stamp.session_uid)
            can_transition = isinstance(event, SessionEvent) or (
                current is None or current.game_session_uid != incoming_uid
            )
            if not can_transition:
                return
        previous_id = current.id if current is not None else None
        assembler.consume(event)
        current = assembler.session
        current_id = current.id if current is not None else None
        if current_id != previous_id:
            self._reset_archive_tracking()
            self._assembler_session_id = current_id
            if current_id is not None and self.on_session_key_change is not None:
                self.on_session_key_change(current_id)
        if sealed is not None and current_id != sealed:
            self._assembler_sealed_session_id = None

    def _archive_lap_number(self, index: int) -> int:
        return max(0, int(self._assembler_laps[index]))

    def _archive_distance(self, index: int) -> float:
        return max(0.0, float(self._assembler_distances[index]))

    def _normalise_for_archive(
        self, packet: Any, received: ReceivedDatagram | None
    ) -> None:
        """Convert supported parser packets into bounded typed field events."""

        stamp = self._archive_stamp(packet, received)
        name = packet.__class__.__name__
        player_index = int(getattr(packet.header, "player_car_index", 255))

        if name == "PacketSessionData":
            track_id = int(getattr(packet, "track_id", -1))
            track_length = int(getattr(packet, "track_length", 0))
            packet_format = int(getattr(packet.header, "packet_format", 0))
            safety_car_status = int(getattr(packet, "safety_car_status", 0))
            marshal_zones = list(getattr(packet, "marshal_zones", []))[
                : int(getattr(packet, "num_marshal_zones", 0))
            ]
            global_flag_context = bool(
                safety_car_status
                or any(
                    int(getattr(zone, "zone_flag", 0)) != 0 for zone in marshal_zones
                )
            )
            self._archive_event(
                SessionEvent(
                    stamp,
                    track_id=track_id,
                    layout_signature=(f"f1:{packet_format}:{track_id}:{track_length}"),
                    session_type=int(getattr(packet, "session_type", 0)),
                    packet_format=packet_format,
                    player_car_index=(player_index if 0 <= player_index < 24 else None),
                    metadata={
                        "track_length_m": track_length,
                        "weather_class": WEATHER.get(
                            int(getattr(packet, "weather", -1)), "Unknown"
                        ),
                        "equal_performance": bool(
                            getattr(packet, "equal_car_performance", 0)
                        ),
                        "capture_mode": self.capture_mode,
                    },
                )
            )
            self._assembler_global_flag_context = global_flag_context
            return

        if name == "PacketParticipantsData":
            count = min(24, int(getattr(packet, "num_active_cars", 0)))
            self._assembler_active_cars = count
            restricted: set[int] = set()
            for index, participant in enumerate(list(packet.participants)[:count]):
                is_restricted = (
                    int(getattr(participant, "your_telemetry", 0)) == 0
                    and index != player_index
                )
                if is_restricted:
                    restricted.add(index)
                self._archive_event(
                    ParticipantEvent(
                        stamp,
                        index,
                        {
                            "driver_id": int(getattr(participant, "driver_id", -1)),
                            "network_id": str(
                                getattr(participant, "network_id", "") or ""
                            ),
                            "name": _name(getattr(participant, "name", b"")),
                            "race_number": int(getattr(participant, "race_number", 0)),
                            "team_id": int(getattr(participant, "team_id", -1)),
                            "nationality_id": int(
                                getattr(participant, "nationality", -1)
                            ),
                            "is_ai": bool(getattr(participant, "ai_controlled", True)),
                            "is_player": index == player_index,
                        },
                    )
                )
            self._assembler_active_cars = count
            self._assembler_restricted = restricted
            return

        if name == "PacketLapData":
            entries = list(packet.lap_data)
            count = self._assembler_active_cars or min(24, len(entries))
            for index, lap in enumerate(entries[:count]):
                current_lap = int(getattr(lap, "current_lap_num", 0))
                previous_lap = self._assembler_laps[index]
                lap_invalid = bool(getattr(lap, "current_lap_invalid", False))
                pit_status = int(getattr(lap, "pit_status", 0))
                distance = max(0.0, float(getattr(lap, "lap_distance", 0.0)))
                self._assembler_distances[index] = distance
                if previous_lap > 0 and current_lap > previous_lap:
                    context = dict(self._assembler_context[index])
                    context.update(
                        {
                            "fuel_start_kg": self._assembler_fuel_start[index],
                            "fuel_end_kg": self._assembler_fuel_end[index],
                            "pit_context": self._assembler_pit_context[index],
                            "flag_context": self._assembler_flag_context[index],
                        }
                    )
                    last_lap_ms = int(getattr(lap, "last_lap_time_in_ms", 0))
                    self._archive_event(
                        LapEvent(
                            stamp,
                            index,
                            previous_lap,
                            current_lap,
                            last_lap_ms or None,
                            bool(
                                last_lap_ms > 0 and not self._assembler_invalid[index]
                            ),
                            context=context,
                        )
                    )
                    self._assembler_invalid[index] = False
                    self._assembler_pit_context[index] = False
                    self._assembler_flag_context[index] = False
                    self._assembler_fuel_start[index] = None
                    self._assembler_fuel_end[index] = None
                self._assembler_laps[index] = current_lap
                if current_lap <= 0:
                    continue
                self._assembler_invalid[index] = (
                    self._assembler_invalid[index] or lap_invalid
                )
                self._assembler_pit_context[index] = (
                    self._assembler_pit_context[index] or pit_status != 0
                )
                self._assembler_flag_context[index] = (
                    self._assembler_flag_context[index]
                    or self._assembler_global_flag_context
                )
                self._archive_event(
                    SampleEvent(
                        stamp,
                        index,
                        current_lap,
                        "lap_data",
                        {
                            "lap_distance_m": distance,
                            "total_distance_m": float(
                                getattr(lap, "total_distance", 0.0)
                            ),
                            "current_lap_time_s": int(
                                getattr(lap, "current_lap_time_in_ms", 0)
                            )
                            / 1000.0,
                            "position": int(getattr(lap, "car_position", 0)),
                            "sector": int(getattr(lap, "sector", 0)),
                            "pit_status": pit_status,
                            "invalid": lap_invalid,
                        },
                        units={
                            "lap_distance_m": "m",
                            "total_distance_m": "m",
                            "current_lap_time_s": "s",
                            "position": "position",
                        },
                        freshness_ms=250,
                    )
                )
            return

        if name == "PacketCarTelemetryData":
            entries = list(packet.car_telemetry_data)
            count = self._assembler_active_cars or min(24, len(entries))
            for index, telemetry in enumerate(entries[:count]):
                lap_number = self._archive_lap_number(index)
                if lap_number <= 0:
                    continue
                unavailable = index in self._assembler_restricted
                values: dict[str, int | float | bool | None] = {
                    "lap_distance_m": self._archive_distance(index),
                    "speed_mps": None
                    if unavailable
                    else float(getattr(telemetry, "speed", 0)) / 3.6,
                    "speed_kph": None
                    if unavailable
                    else float(getattr(telemetry, "speed", 0)),
                    "throttle": None
                    if unavailable
                    else float(getattr(telemetry, "throttle", 0.0)),
                    "brake": None
                    if unavailable
                    else float(getattr(telemetry, "brake", 0.0)),
                    "steering": None
                    if unavailable
                    else float(getattr(telemetry, "steer", 0.0)),
                    "gear": None if unavailable else int(getattr(telemetry, "gear", 0)),
                    "engine_rpm": None
                    if unavailable
                    else int(getattr(telemetry, "engine_rpm", 0)),
                    "drs": None
                    if unavailable
                    else bool(getattr(telemetry, "drs", False)),
                }
                availability = {
                    key: "unavailable"
                    for key in values
                    if key != "lap_distance_m" and unavailable
                }
                self._archive_event(
                    SampleEvent(
                        stamp,
                        index,
                        lap_number,
                        "telemetry",
                        values,
                        availability=availability,
                        units={
                            "lap_distance_m": "m",
                            "speed_mps": "m/s",
                            "speed_kph": "km/h",
                            "throttle": "ratio",
                            "brake": "ratio",
                            "steering": "ratio",
                            "gear": "gear",
                            "engine_rpm": "rpm",
                        },
                        freshness_ms=250,
                    )
                )
            return

        if name == "PacketMotionData":
            entries = list(packet.car_motion_data)
            count = self._assembler_active_cars or min(24, len(entries))
            for index, motion in enumerate(entries[:count]):
                lap_number = self._archive_lap_number(index)
                if lap_number <= 0:
                    continue
                self._archive_event(
                    SampleEvent(
                        stamp,
                        index,
                        lap_number,
                        "motion",
                        {
                            "lap_distance_m": self._archive_distance(index),
                            "world_x": float(getattr(motion, "world_position_x", 0.0)),
                            "world_y": float(getattr(motion, "world_position_y", 0.0)),
                            "world_z": float(getattr(motion, "world_position_z", 0.0)),
                            "velocity_x": float(
                                getattr(motion, "world_velocity_x", 0.0)
                            ),
                            "velocity_y": float(
                                getattr(motion, "world_velocity_y", 0.0)
                            ),
                            "velocity_z": float(
                                getattr(motion, "world_velocity_z", 0.0)
                            ),
                            "lateral_g": float(getattr(motion, "g_force_lateral", 0.0)),
                            "longitudinal_g": float(
                                getattr(motion, "g_force_longitudinal", 0.0)
                            ),
                            "yaw": float(getattr(motion, "yaw", 0.0)),
                            "pitch": float(getattr(motion, "pitch", 0.0)),
                            "roll": float(getattr(motion, "roll", 0.0)),
                        },
                        units={
                            "lap_distance_m": "m",
                            "world_x": "m",
                            "world_y": "m",
                            "world_z": "m",
                            "velocity_x": "m/s",
                            "velocity_y": "m/s",
                            "velocity_z": "m/s",
                            "lateral_g": "g",
                            "longitudinal_g": "g",
                            "yaw": "rad",
                            "pitch": "rad",
                            "roll": "rad",
                        },
                        freshness_ms=250,
                    )
                )
            return

        if name == "PacketCarStatusData":
            entries = list(packet.car_status_data)
            count = self._assembler_active_cars or min(24, len(entries))
            for index, status in enumerate(entries[:count]):
                lap_number = self._archive_lap_number(index)
                if lap_number <= 0:
                    continue
                unavailable = index in self._assembler_restricted
                compound = VISUAL_COMPOUNDS.get(
                    int(getattr(status, "visual_tyre_compound", -1)), "UNKNOWN"
                )
                fuel_kg = (
                    None if unavailable else float(getattr(status, "fuel_in_tank", 0.0))
                )
                if fuel_kg is not None:
                    if self._assembler_fuel_start[index] is None:
                        self._assembler_fuel_start[index] = fuel_kg
                    self._assembler_fuel_end[index] = fuel_kg
                self._assembler_context[index].update(
                    {
                        "tyre_compound": compound,
                        "tyre_age_laps": int(getattr(status, "tyres_age_laps", 0)),
                        "fuel_kg": fuel_kg,
                    }
                )
                values = {
                    "lap_distance_m": self._archive_distance(index),
                    "fuel_kg": fuel_kg,
                    "ers_energy_j": None
                    if unavailable
                    else float(getattr(status, "ers_store_energy", 0.0)),
                    "ers_deploy_mode": None
                    if unavailable
                    else int(getattr(status, "ers_deploy_mode", 0)),
                    "tyre_age_laps": int(getattr(status, "tyres_age_laps", 0)),
                    "actual_tyre_compound": int(
                        getattr(status, "actual_tyre_compound", 0)
                    ),
                    "visual_tyre_compound": int(
                        getattr(status, "visual_tyre_compound", 0)
                    ),
                }
                self._archive_event(
                    SampleEvent(
                        stamp,
                        index,
                        lap_number,
                        "status",
                        values,
                        availability={
                            key: "unavailable"
                            for key in ("fuel_kg", "ers_energy_j", "ers_deploy_mode")
                            if unavailable
                        },
                        units={
                            "lap_distance_m": "m",
                            "fuel_kg": "kg",
                            "ers_energy_j": "J",
                            "tyre_age_laps": "lap",
                        },
                        freshness_ms=1_500,
                        retain_all=True,
                    )
                )
            return

        if name == "PacketCarTelemetry2Data":
            entries = list(packet.car_telemetry2_data)
            count = self._assembler_active_cars or min(24, len(entries))
            for index, telemetry in enumerate(entries[:count]):
                lap_number = self._archive_lap_number(index)
                if lap_number <= 0:
                    continue
                unavailable = index in self._assembler_restricted
                self._archive_event(
                    SampleEvent(
                        stamp,
                        index,
                        lap_number,
                        "telemetry_2026",
                        {
                            "lap_distance_m": self._archive_distance(index),
                            "overtake_active": None
                            if unavailable
                            else bool(getattr(telemetry, "overtake_active", False)),
                            "overtake_available": None
                            if unavailable
                            else bool(getattr(telemetry, "overtake_available", False)),
                            "active_aero_mode": None
                            if unavailable
                            else int(getattr(telemetry, "active_aero_mode", 0)),
                        },
                        availability={
                            key: "unavailable"
                            for key in (
                                "overtake_active",
                                "overtake_available",
                                "active_aero_mode",
                            )
                            if unavailable
                        },
                        freshness_ms=500,
                    )
                )
            return

        if name == "PacketCarDamageData":
            entries = list(packet.car_damage_data)
            count = self._assembler_active_cars or min(24, len(entries))
            for index, damage in enumerate(entries[:count]):
                lap_number = self._archive_lap_number(index)
                if lap_number <= 0:
                    continue
                unavailable = index in self._assembler_restricted
                wears = normalize_wheels(getattr(damage, "tyres_wear", []))
                values: dict[str, int | float | bool | None] = {
                    "lap_distance_m": self._archive_distance(index),
                    "front_left_wing_damage": None
                    if unavailable
                    else int(getattr(damage, "front_left_wing_damage", 0)),
                    "front_right_wing_damage": None
                    if unavailable
                    else int(getattr(damage, "front_right_wing_damage", 0)),
                    "rear_wing_damage": None
                    if unavailable
                    else int(getattr(damage, "rear_wing_damage", 0)),
                    "floor_damage": None
                    if unavailable
                    else int(getattr(damage, "floor_damage", 0)),
                }
                for wheel, offset in zip(("fl", "fr", "rl", "rr"), range(4)):
                    values[f"tyre_wear_{wheel}"] = (
                        None
                        if unavailable or offset >= len(wears)
                        else float(wears[offset])
                    )
                self._archive_event(
                    SampleEvent(
                        stamp,
                        index,
                        lap_number,
                        "damage",
                        values,
                        availability={
                            key: "unavailable"
                            for key in values
                            if key != "lap_distance_m" and unavailable
                        },
                        units={
                            "lap_distance_m": "m",
                            **{
                                f"tyre_wear_{wheel}": "%"
                                for wheel in ("fl", "fr", "rl", "rr")
                            },
                        },
                        freshness_ms=3_000,
                        retain_all=True,
                    )
                )
            return

        if name == "PacketEventData" and _event_code(packet) == "FLBK":
            detail = packet.event_details.flashback
            self._archive_event(
                FlashbackEvent(
                    stamp,
                    int(detail.flashback_frame_identifier),
                    float(detail.flashback_session_time),
                    reason="game_flashback_event",
                )
            )
            return

        if name == "PacketFinalClassificationData":
            assembler = self.session_assembler
            if assembler is not None:
                assembler.finalize_current_session(reason="final_classification")
                if assembler.session is not None:
                    self._assembler_sealed_session_id = assembler.session.id

    async def handle_PacketSessionData(self, packet: Any) -> None:
        samples = list(packet.weather_forecast_samples)[
            : int(packet.num_weather_forecast_samples)
        ]
        forecast = [
            {
                "time_offset_min": int(sample.time_offset),
                "weather": WEATHER.get(int(sample.weather), "Unknown"),
                "track_temp_c": int(sample.track_temperature),
                "air_temp_c": int(sample.air_temperature),
                "rain_pct": int(sample.rain_percentage),
            }
            for sample in samples
        ]

        def rain_at(minutes: int) -> int:
            candidates = [
                item for item in forecast if item["time_offset_min"] <= minutes
            ]
            return int(candidates[-1]["rain_pct"]) if candidates else 0

        track_length = max(1, int(packet.track_length))
        # Marshal zones are the only field-wide report of which part of the lap
        # is under a yellow. zone_start is a fraction of the lap, converted here
        # to metres so it can be compared with lap_distance directly.
        marshal_zones = [
            {
                "index": index,
                "start_fraction": round(float(zone.zone_start), 4),
                "start_m": round(float(zone.zone_start) * track_length, 1),
                "flag": FIA_FLAGS.get(int(zone.zone_flag), "unknown"),
            }
            for index, zone in enumerate(
                list(packet.marshal_zones)[: int(packet.num_marshal_zones)]
            )
        ]
        drs_zones = [
            {
                "start_m": round(float(zone.zone_start) * track_length, 1),
                "end_m": round(float(zone.zone_end) * track_length, 1),
            }
            for zone in list(packet.drs_zones)[
                : int(getattr(packet, "num_drs_zones", 0))
            ]
        ]
        active_aero_zones = [
            {
                "kind": kind,
                "start_m": round(float(zone.zone_start) * track_length, 1),
                "end_m": round(float(zone.zone_end) * track_length, 1),
            }
            for kind, attribute, count_attribute in (
                ("full", "active_aero_zones_full", "num_active_aero_zones_full"),
                (
                    "partial",
                    "active_aero_zones_partial",
                    "num_active_aero_zones_partial",
                ),
            )
            for zone in list(getattr(packet, attribute, []))[
                : int(getattr(packet, count_attribute, 0))
            ]
        ]

        snapshot = await self.store.peek(
            "current_lap",
            "session_mode_override",
            "strategy",
            "red_flag_active",
            "race_control_phase",
        )
        raw_session_type_id = int(packet.session_type)
        session_length_id = int(getattr(packet, "session_length", 0))
        num_sessions_in_weekend = int(getattr(packet, "num_sessions_in_weekend", 0))
        weekend_structure = [
            int(value)
            for value in list(getattr(packet, "weekend_structure", []))[
                :num_sessions_in_weekend
            ]
        ]
        session_type, effective_mode, detection_source, detection_reason = (
            classify_session(
                raw_type_id=raw_session_type_id,
                total_laps=int(packet.total_laps),
                current_lap=int(snapshot.get("current_lap", 0)),
                session_time_left_s=int(packet.session_time_left),
                session_duration_s=int(packet.session_duration),
                session_length_id=session_length_id,
                weekend_structure=weekend_structure,
                override=str(snapshot.get("session_mode_override", "auto")),
            )
        )
        strategy = dict(snapshot.get("strategy", {}))
        strategy.update(
            {
                "game_ideal_lap": int(packet.pit_stop_window_ideal_lap),
                "game_latest_lap": int(packet.pit_stop_window_latest_lap),
                "game_rejoin_position": int(packet.pit_stop_rejoin_position),
            }
        )
        safety_car = SAFETY_CAR.get(int(packet.safety_car_status), "unknown")
        red_flag_active = bool(snapshot.get("red_flag_active", False))
        previous_phase = str(snapshot.get("race_control_phase", "green"))
        ending_phase = {
            "full": "safety_car_ending",
            "virtual": "vsc_ending",
            "formation": "formation_ending",
        }.get(safety_car)
        race_control_phase = (
            "red_flag"
            if red_flag_active
            else previous_phase
            if ending_phase and previous_phase == ending_phase
            else "safety_car"
            if safety_car == "full"
            else "vsc"
            if safety_car == "virtual"
            else "formation"
            if safety_car == "formation"
            else "green"
        )
        await self.store.update(
            session_type=session_type,
            mode_profile=effective_mode,
            raw_session_type_id=raw_session_type_id,
            raw_session_type=SESSION_TYPES.get(
                raw_session_type_id, f"Session {raw_session_type_id}"
            ),
            session_detection_source=detection_source,
            session_detection_reason=detection_reason,
            session_length_id=session_length_id,
            session_length_label=SESSION_LENGTHS.get(
                session_length_id, f"Value {session_length_id}"
            ),
            num_sessions_in_weekend=num_sessions_in_weekend,
            weekend_structure=weekend_structure,
            track_id=int(packet.track_id),
            track_name=TRACKS.get(int(packet.track_id), f"Track {packet.track_id}"),
            track_length_m=int(packet.track_length),
            session_time_left_s=int(packet.session_time_left),
            session_duration_s=int(packet.session_duration),
            total_laps=int(packet.total_laps),
            weather=WEATHER.get(int(packet.weather), "Unknown"),
            weather_forecast=forecast,
            rain_next_15_pct=rain_at(15),
            rain_next_30_pct=rain_at(30),
            rain_next_60_pct=rain_at(60),
            track_temp_c=int(packet.track_temperature),
            air_temp_c=int(packet.air_temperature),
            safety_car=safety_car,
            safety_car_periods=int(packet.num_safety_car_periods),
            virtual_safety_car_periods=int(packet.num_virtual_safety_car_periods),
            red_flag_count=int(packet.num_red_flag_periods),
            race_control_phase=race_control_phase,
            game_paused=bool(packet.game_paused),
            sector2_start_m=float(packet.sector2_lap_distance_start),
            sector3_start_m=float(packet.sector3_lap_distance_start),
            marshal_zones=marshal_zones,
            drs_zones=drs_zones,
            active_aero_zones=active_aero_zones,
            active_aero_track_status=int(
                getattr(packet, "active_aero_track_status", 0)
            ),
            pit_speed_limit_kph=int(getattr(packet, "pit_speed_limit", 0)),
            ai_difficulty=int(getattr(packet, "ai_difficulty", 0)),
            equal_car_performance=bool(getattr(packet, "equal_car_performance", 0)),
            parc_ferme_rules=bool(getattr(packet, "parc_ferme_rules", 0)),
            car_damage_rate=int(getattr(packet, "car_damage_rate", 0)),
            tyre_temperature_sim=int(getattr(packet, "tyre_temperature", 0)),
            pit_lane_tyre_sim=bool(getattr(packet, "pit_lane_tyre_sim", 0)),
            forecast_accuracy=int(getattr(packet, "forecast_accuracy", 0)),
            game_mode=int(getattr(packet, "game_mode", 0)),
            rule_set=int(getattr(packet, "rule_set", 0)),
            formula=int(getattr(packet, "formula", 0)),
            time_of_day_min=int(getattr(packet, "time_of_day", 0)),
            network_game=bool(getattr(packet, "network_game", 0)),
            weekend_link_identifier=int(getattr(packet, "weekend_link_identifier", 0)),
            session_link_identifier=int(getattr(packet, "session_link_identifier", 0)),
            strategy=strategy,
        )

    async def handle_PacketParticipantsData(self, packet: Any) -> None:
        count = min(int(packet.num_active_cars), 24)

        def apply(state):  # type: ignore[no-untyped-def]
            state.player_car_index = int(packet.header.player_car_index)
            state.active_cars = count
            for index, driver in enumerate(state.drivers):
                driver.active = index < count
                if index >= count:
                    driver.position = 0
                    continue
                participant = packet.participants[index]
                driver.name = _name(participant.name)
                driver.car_idx = index
                driver.team_id = int(getattr(participant, "team_id", -1))
                # A verified team-id table now exists for this title, so the
                # readable team name is no longer left blank.
                driver.team = team_name(driver.team_id)
                driver.driver_id = int(getattr(participant, "driver_id", -1))
                driver.race_number = int(getattr(participant, "race_number", 0))
                driver.ai_controlled = bool(getattr(participant, "ai_controlled", 1))
                driver.restricted = (
                    int(participant.your_telemetry) == 0
                    and index != state.player_car_index
                )
            # Teammate identification needs every team_id read first, so it runs
            # as a second pass rather than inside the loop above. Spectator
            # packets use player_car_index=255; never index the driver array with
            # that sentinel.
            player_index = state.player_car_index
            player = (
                state.drivers[player_index]
                if 0 <= player_index < count and player_index < len(state.drivers)
                else None
            )
            for index, driver in enumerate(state.drivers):
                driver.is_player = index == player_index
                driver.is_teammate = bool(
                    player is not None
                    and driver.active
                    and player.team_id >= 0
                    and driver.team_id == player.team_id
                    and index != player_index
                )

        await self.store.mutate(apply)

    async def handle_PacketLapData(self, packet: Any) -> None:
        player_index = _packet_player_index(packet, packet.lap_data)
        if player_index is None:
            return
        player = packet.lap_data[player_index]
        before = await self.store.peek(
            "mode_profile", "session_uid", "pit_status", "current_lap"
        )
        session_time_s = float(getattr(packet.header, "session_time", 0.0) or 0.0)
        await self.store.transition_lap(
            new_lap=int(player.current_lap_num),
            last_lap_ms=int(player.last_lap_time_in_ms),
            current_lap_invalid=bool(player.current_lap_invalid),
            position=int(player.car_position),
            pit_status=int(player.pit_status),
            pit_lane_time_ms=int(player.pit_lane_time_in_lane_in_ms),
        )
        player_delta_to_leader = (
            _ms(
                int(player.delta_to_race_leader_minutes_part),
                int(player.delta_to_race_leader_ms_part),
            )
            / 1000.0
        )

        def apply(state):  # type: ignore[no-untyped-def]
            state.player_car_index = player_index
            state.current_lap_time_ms = int(player.current_lap_time_in_ms)
            state.lap_distance_m = max(0.0, float(player.lap_distance))
            state.player_position = int(player.car_position)
            state.grid_position = int(getattr(player, "grid_position", 0))
            state.sector = int(player.sector)
            # Live delta to the reference lap at the current lap distance.
            reference_time = self.store.reference_time_at(state.lap_distance_m)
            if reference_time is not None and state.current_lap_time_ms > 0:
                state.live_delta_s = round(
                    state.current_lap_time_ms / 1000.0 - reference_time, 3
                )
            else:
                state.live_delta_s = None
            state.current_lap_invalid = bool(player.current_lap_invalid)
            state.safety_car_delta_s = float(player.safety_car_delta)
            state.safety_car_delta_valid = True
            state.penalties_s = int(player.penalties)
            state.warnings = int(player.total_warnings)
            state.corner_cutting_warnings = int(player.corner_cutting_warnings)
            state.unserved_drive_through_penalties = int(
                player.num_unserved_drive_through_pens
            )
            state.unserved_stop_go_penalties = int(player.num_unserved_stop_go_pens)
            state.pit_stop_should_serve_penalty = bool(player.pit_stop_should_serve_pen)
            state.pit_status = int(player.pit_status)
            state.pit_lane_time_ms = int(player.pit_lane_time_in_lane_in_ms)
            for index, lap in enumerate(packet.lap_data):
                if index >= len(state.drivers):
                    break
                driver = state.drivers[index]
                if not driver.active:
                    continue
                new_driver_lap = int(lap.current_lap_num)
                if driver.current_lap > 0 and new_driver_lap > driver.current_lap:
                    driver.energy_lap_history.append(
                        {
                            "lap": int(driver.current_lap),
                            "deployed_j": round(float(driver.ers_deployed_lap_j), 1),
                            "harvested_j": round(float(driver.ers_harvested_lap_j), 1),
                            "manual_override_used": bool(driver.overtake_used_this_lap),
                        }
                    )
                    del driver.energy_lap_history[:-30]
                    driver.overtake_used_this_lap = False
                was_in_pit = bool(driver.pit_lane_timer_active)
                driver.position = int(lap.car_position)
                driver.current_lap = new_driver_lap
                driver.delta_to_front_s = (
                    _ms(
                        int(lap.delta_to_car_in_front_minutes_part),
                        int(lap.delta_to_car_in_front_ms_part),
                    )
                    / 1000.0
                )
                driver.last_lap_ms = int(lap.last_lap_time_in_ms)
                driver.pit_stops = int(lap.num_pit_stops)
                driver.status = STATUS.get(int(lap.driver_status), "")
                driver.grid_position = int(getattr(lap, "grid_position", 0))
                # A stop in progress is observable through the pit timers, so a
                # rival's stop can be called as it happens rather than inferred
                # a lap later from the stop counter.
                driver.pit_status = int(lap.pit_status)
                driver.pit_lane_timer_active = bool(lap.pit_lane_timer_active)
                driver.pit_lane_time_ms = int(lap.pit_lane_time_in_lane_in_ms)
                driver.pit_stop_timer_ms = int(lap.pit_stop_timer_in_ms)
                if driver.pit_lane_timer_active:
                    driver.current_pit_stop_max_ms = max(
                        int(driver.current_pit_stop_max_ms),
                        int(driver.pit_stop_timer_ms),
                    )
                elif was_in_pit and driver.current_pit_stop_max_ms > 0:
                    driver.pit_stop_history.append(
                        {
                            "lap": new_driver_lap,
                            "stop_number": max(1, int(driver.pit_stops)),
                            "stationary_time_ms": int(driver.current_pit_stop_max_ms),
                        }
                    )
                    del driver.pit_stop_history[:-12]
                    driver.current_pit_stop_max_ms = 0
                result_status = int(getattr(lap, "result_status", 0))
                driver.result_status = result_status
                driver.result_label = RESULT_STATUS.get(result_status, "unknown")
                driver.speed_trap_kph = float(
                    getattr(lap, "speed_trap_fastest_speed", 0.0)
                )
                driver.speed_trap_lap = int(getattr(lap, "speed_trap_fastest_lap", 0))
                driver.lap_distance_m = max(0.0, float(lap.lap_distance))
                driver.total_distance_m = float(lap.total_distance)
                driver.penalties_s = int(lap.penalties)
                driver.warnings = int(lap.total_warnings)
                driver.delta_to_leader_s = (
                    _ms(
                        int(lap.delta_to_race_leader_minutes_part),
                        int(lap.delta_to_race_leader_ms_part),
                    )
                    / 1000.0
                )
                if index == player_index:
                    driver.gap_to_player_s = 0.0
                elif result_status in RETIRED_RESULT_STATUS:
                    # A retired car must not keep reporting the gap it held when
                    # it stopped; that gap would otherwise be quoted for the rest
                    # of the race as if the car were still circulating.
                    driver.gap_to_player_s = None
                elif driver.position > 0:
                    driver.gap_to_player_s = round(
                        float(driver.delta_to_leader_s or 0.0) - player_delta_to_leader,
                        3,
                    )
                if index != player_index and driver.gap_to_player_s is not None:
                    history = driver.gap_history
                    last = history[-1] if history else None
                    should_sample = (
                        last is None
                        or int(last.get("lap", -1)) != driver.current_lap
                        or session_time_s - float(last.get("session_time_s", 0.0))
                        >= 2.0
                    )
                    if should_sample:
                        history.append(
                            {
                                "session_time_s": round(session_time_s, 3),
                                "lap": driver.current_lap,
                                "gap_s": float(driver.gap_to_player_s),
                            }
                        )
                        del history[:-90]

        await self.store.mutate(apply)

        # A qualifying in-lap is the safe, deterministic delivery point for the
        # preceding timed-lap debrief. Some game modes expose the transition via
        # pit_status, others only via driver_status==2, so support both and
        # de-duplicate by completed lap.
        mode = str(before.get("mode_profile", ""))
        entered_in_lap = (
            int(player.pit_status) != 0 and int(before.get("pit_status", 0) or 0) == 0
        ) or int(getattr(player, "driver_status", 0) or 0) == 2
        timed_lap = max(0, int(player.current_lap_num) - 1)
        marker = (int(before.get("session_uid", 0) or 0), timed_lap)
        if (
            self.on_qualifying_lap
            and mode == "qualifying"
            and entered_in_lap
            and timed_lap > 0
            and int(player.last_lap_time_in_ms) > 0
            and marker not in self._qualifying_debrief_laps
        ):
            self._qualifying_debrief_laps.add(marker)
            task = self.loop.create_task(
                self._qualifying_debrief_after_history(),
                name=f"pitwall-quali-debrief-{timed_lap}",
            )
            self._briefing_tasks.add(task)
            task.add_done_callback(self._briefing_tasks.discard)

    async def _qualifying_debrief_after_history(self) -> None:
        # SessionHistory normally trails the LapData in-lap transition by a few
        # frames. Yielding lets that packet update lap/sector history before the
        # deterministic payload freezes, without blocking the UDP consumer.
        try:
            await asyncio.sleep(0.30)
            if self.on_qualifying_lap:
                await self.on_qualifying_lap()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Qualifying debrief callback failed: %s", exc)

    async def handle_PacketCarTelemetryData(self, packet: Any) -> None:
        player_index = _packet_player_index(packet, packet.car_telemetry_data)

        # Field-wide speed, DRS and tyre temperatures. These make "is he
        # struggling for temperature" and "is his DRS open" answerable.
        field_samples = [
            (
                int(entry.speed),
                bool(entry.drs),
                [float(v) for v in normalize_wheels(entry.tyres_inner_temperature)],
                [float(v) for v in normalize_wheels(entry.tyres_surface_temperature)],
            )
            for entry in packet.car_telemetry_data
        ]

        def apply_field(state):  # type: ignore[no-untyped-def]
            for index, (speed, drs, inner, surface) in enumerate(field_samples):
                if index >= len(state.drivers):
                    break
                driver = state.drivers[index]
                if not driver.active or driver.restricted:
                    continue
                driver.speed_kph = speed
                driver.drs_open = drs
                driver.tyre_inner_temps_c = inner
                driver.tyre_surface_temps_c = surface

        await self.store.mutate(apply_field)
        if player_index is None:
            return
        telemetry = packet.car_telemetry_data[player_index]
        await self.store.update_telemetry_and_trace(
            session_time=float(packet.header.session_time),
            speed_kph=int(telemetry.speed),
            gear=int(telemetry.gear),
            throttle=float(telemetry.throttle),
            brake=float(telemetry.brake),
            steer=float(telemetry.steer),
            inner_temps_c=[
                float(value)
                for value in normalize_wheels(telemetry.tyres_inner_temperature)
            ],
            surface_temps_c=[
                float(value)
                for value in normalize_wheels(telemetry.tyres_surface_temperature)
            ],
            pressures_psi=[
                float(value) for value in normalize_wheels(telemetry.tyres_pressure)
            ],
        )

    async def handle_PacketCarTelemetry2Data(self, packet: Any) -> None:
        player_index = _packet_player_index(packet, packet.car_telemetry2_data)

        # 2026 Manual Override state for every car, so a rival's boost can be
        # seen rather than guessed.
        overrides = [
            (bool(entry.overtake_active), bool(entry.overtake_available))
            for entry in packet.car_telemetry2_data
        ]

        def apply_field(state):  # type: ignore[no-untyped-def]
            for index, (active, available) in enumerate(overrides):
                if index >= len(state.drivers):
                    break
                driver = state.drivers[index]
                if not driver.active or driver.restricted:
                    continue
                driver.overtake_active = active
                driver.overtake_available = available
                if active:
                    driver.overtake_used_this_lap = True

        await self.store.mutate(apply_field)
        if player_index is None:
            return
        telemetry = packet.car_telemetry2_data[player_index]
        await self.store.update(
            active_aero_mode=int(telemetry.active_aero_mode),
            active_aero_available=bool(telemetry.active_aero_available),
            active_aero_activation_distance_m=int(
                telemetry.active_aero_activation_distance
            ),
            overtake_available=bool(telemetry.overtake_available),
            overtake_active=bool(telemetry.overtake_active),
            overtake_activation_distance_m=int(telemetry.overtake_activation_distance),
            regulations_2026=bool(getattr(telemetry, "2026_regulations")),
            driving_wrong_way=bool(telemetry.driving_wrong_way),
        )

    async def handle_PacketCarStatusData(self, packet: Any) -> None:
        player_index = _packet_player_index(packet, packet.car_status_data)
        if player_index is None:
            return
        car = packet.car_status_data[player_index]

        def apply(state):  # type: ignore[no-untyped-def]
            state.fuel_kg = round(float(car.fuel_in_tank), 2)
            # EA documents this as the value shown on the MFD. In practice it is
            # already the fuel delta expressed in laps (for example +1.6), not
            # the total number of laps the tank can cover. Subtracting race laps
            # remaining produces impossible values such as -17.4 on lap 3/22.
            state.fuel_remaining_laps = round(float(car.fuel_remaining_laps), 2)
            state.fuel_laps_delta = state.fuel_remaining_laps
            state.ers_pct = round(float(car.ers_store_energy) / 4_000_000 * 100, 1)
            state.ers_mode = int(car.ers_deploy_mode)
            state.ers_deployed_lap_j = float(car.ers_deployed_this_lap)
            state.ers_harvested_lap_j = float(car.ers_harvested_this_lap_mguk) + float(
                car.ers_harvested_this_lap_mguh
            )
            state.drs_allowed = bool(car.drs_allowed)
            state.fia_flag = FIA_FLAGS.get(int(car.vehicle_fia_flags), "unknown")
            state.tyre.compound = VISUAL_COMPOUNDS.get(
                int(car.visual_tyre_compound), str(car.visual_tyre_compound)
            )
            state.tyre.actual_compound = int(car.actual_tyre_compound)
            state.tyre.age_laps = int(car.tyres_age_laps)
            for index, status in enumerate(packet.car_status_data):
                if index >= len(state.drivers):
                    break
                driver = state.drivers[index]
                if not driver.active:
                    continue
                driver.tyre_compound = VISUAL_COMPOUNDS.get(
                    int(status.visual_tyre_compound), "UNKNOWN"
                )
                driver.tyre_actual_compound = int(status.actual_tyre_compound)
                driver.tyre_age = int(status.tyres_age_laps)
                driver.fia_flag = FIA_FLAGS.get(
                    int(status.vehicle_fia_flags), "unknown"
                )
                if driver.restricted:
                    continue
                # Rival energy and fuel are in the packet. The attack/defence
                # tools previously assumed they were not exposed.
                driver.fuel_kg = round(float(status.fuel_in_tank), 2)
                driver.fuel_remaining_laps = round(float(status.fuel_remaining_laps), 2)
                driver.ers_pct = round(
                    float(status.ers_store_energy) / 4_000_000 * 100, 1
                )
                driver.ers_mode = int(status.ers_deploy_mode)
                driver.ers_deployed_lap_j = float(status.ers_deployed_this_lap)
                driver.ers_harvested_lap_j = float(
                    status.ers_harvested_this_lap_mguk
                ) + float(status.ers_harvested_this_lap_mguh)
                driver.drs_allowed = bool(status.drs_allowed)
                driver.front_brake_bias = int(status.front_brake_bias)
                driver.fuel_mix = int(status.fuel_mix)

        await self.store.mutate(apply)

    @staticmethod
    def _damage_fields(damage: Any) -> dict[str, Any]:
        """Decode one car's damage entry into Your Pit Box's wheel order and keys."""
        blisters = [int(value) for value in normalize_wheels(damage.tyre_blisters)]
        return {
            "wear": [float(value) for value in normalize_wheels(damage.tyres_wear)],
            "tyre_damage": [
                int(value) for value in normalize_wheels(damage.tyres_damage)
            ],
            "blisters": blisters,
            # Power-unit component wear (percent used) is distinct from crash
            # damage and accumulates across a season toward grid penalties.
            "component_wear": {
                "ice": float(getattr(damage, "engine_ice_wear", 0.0)),
                "mguk": float(getattr(damage, "engine_mguk_wear", 0.0)),
                "mguh": float(getattr(damage, "engine_mguh_wear", 0.0)),
                "es": float(getattr(damage, "engine_es_wear", 0.0)),
                "ce": float(getattr(damage, "engine_ce_wear", 0.0)),
                "tc": float(getattr(damage, "engine_tc_wear", 0.0)),
            },
            "damage": {
                "front_left_wing": int(damage.front_left_wing_damage),
                "front_right_wing": int(damage.front_right_wing_damage),
                "rear_wing": int(damage.rear_wing_damage),
                "floor": int(damage.floor_damage),
                "diffuser": int(damage.diffuser_damage),
                "sidepod": int(damage.sidepod_damage),
                "gearbox": int(damage.gear_box_damage),
                "engine": int(damage.engine_damage),
                "drs_fault": bool(damage.drs_fault),
                "ers_fault": bool(damage.ers_fault),
                "engine_blown": bool(getattr(damage, "engine_blown", False)),
                "engine_seized": bool(getattr(damage, "engine_seized", False)),
                "blisters": blisters,
            },
        }

    async def handle_PacketCarDamageData(self, packet: Any) -> None:
        """Damage, tyre wear and power-unit wear for every car in the field.

        The packet has always carried all 24 entries; reading only the player's
        made "does the car ahead have damage" and "how worn are his tyres"
        unanswerable.
        """
        player_index = _packet_player_index(packet, packet.car_damage_data)
        decoded = [self._damage_fields(entry) for entry in packet.car_damage_data]

        def apply(state):  # type: ignore[no-untyped-def]
            for index, fields_ in enumerate(decoded):
                if index >= len(state.drivers):
                    break
                driver = state.drivers[index]
                if not driver.active:
                    continue
                # A restricted car reports zeros rather than real values; keep
                # the defaults so tools report "unavailable" instead of "no wear".
                if driver.restricted:
                    continue
                driver.tyre_wear = fields_["wear"]
                driver.tyre_damage = fields_["tyre_damage"]
                driver.blisters = fields_["blisters"]
                driver.component_wear = fields_["component_wear"]
                driver.damage = fields_["damage"]
            if player_index is None:
                return
            player_fields = decoded[player_index]
            state.tyre.wear = player_fields["wear"]
            state.tyre.damage = player_fields["tyre_damage"]
            state.tyre.blisters = player_fields["blisters"]
            state.component_wear = player_fields["component_wear"]
            state.damage = player_fields["damage"]

        await self.store.mutate(apply)

    async def handle_PacketMotionData(self, packet: Any) -> None:
        player_index = _packet_player_index(packet, packet.car_motion_data)

        # World position for every car. Timing gaps say how far apart two cars
        # are in time; positions say where they actually are, which is what a
        # rejoin-into-traffic estimate needs.
        positions = [
            (
                float(getattr(entry, "world_position_x", 0.0)),
                float(getattr(entry, "world_position_z", 0.0)),
            )
            for entry in packet.car_motion_data
        ]

        def apply_field(state):  # type: ignore[no-untyped-def]
            for index, (x, z) in enumerate(positions):
                if index >= len(state.drivers):
                    break
                driver = state.drivers[index]
                if not driver.active:
                    continue
                driver.world_x = x
                driver.world_z = z

        await self.store.mutate(apply_field)
        if player_index is None:
            return
        motion = packet.car_motion_data[player_index]
        forward_x = float(getattr(motion, "world_forward_dir_x", 0)) / 32767.0
        forward_z = float(getattr(motion, "world_forward_dir_z", 32767)) / 32767.0
        await self.store.update(
            lateral_g=float(motion.g_force_lateral),
            longitudinal_g=float(motion.g_force_longitudinal),
            world_x=float(getattr(motion, "world_position_x", 0.0)),
            world_y=float(getattr(motion, "world_position_y", 0.0)),
            world_z=float(getattr(motion, "world_position_z", 0.0)),
            forward_x=forward_x,
            forward_z=forward_z,
        )

    async def handle_PacketMotionExData(self, packet: Any) -> None:
        await self.store.update(
            wheel_slip_ratio=[
                float(value) for value in normalize_wheels(packet.wheel_slip_ratio)
            ],
            wheel_slip_angle=[
                float(value) for value in normalize_wheels(packet.wheel_slip_angle)
            ],
        )

    async def handle_PacketSessionHistoryData(self, packet: Any) -> None:
        index = int(packet.car_idx)
        if not 0 <= index < 24:
            return
        history = []
        for lap_number, lap in enumerate(
            list(packet.lap_history_data)[: int(packet.num_laps)], start=1
        ):
            history.append(
                {
                    "lap_num": lap_number,
                    "lap_ms": int(lap.lap_time_in_ms),
                    "s1_ms": _ms(
                        int(lap.sector1_time_minutes_part),
                        int(lap.sector1_time_ms_part),
                    ),
                    "s2_ms": _ms(
                        int(lap.sector2_time_minutes_part),
                        int(lap.sector2_time_ms_part),
                    ),
                    "s3_ms": _ms(
                        int(lap.sector3_time_minutes_part),
                        int(lap.sector3_time_ms_part),
                    ),
                    "valid_flags": int(lap.lap_valid_bit_flags),
                }
            )
        stints = []
        start_lap = 1
        for stint in list(packet.tyre_stints_history_data)[
            : int(packet.num_tyre_stints)
        ]:
            end_lap = int(stint.end_lap)
            stints.append(
                {
                    "start_lap": start_lap,
                    "end_lap": end_lap,
                    "compound": VISUAL_COMPOUNDS.get(
                        int(stint.tyre_visual_compound), "UNKNOWN"
                    ),
                    "actual_compound": int(stint.tyre_actual_compound),
                }
            )
            start_lap = end_lap + 1

        def apply(state):  # type: ignore[no-untyped-def]
            driver = state.drivers[index]
            driver.lap_history = history[-100:]
            driver.tyre_stints = stints
            driver.history_updated_at = float(packet.header.session_time)
            valid = [
                item["lap_ms"]
                for item in history
                if item["lap_ms"] > 0 and (item["valid_flags"] & 1)
            ]
            driver.best_lap_ms = min(valid) if valid else 0

        await self.store.mutate(apply)
        snapshot = await self.store.peek("session_uid")
        packet_player_index = int(getattr(packet.header, "player_car_index", 255))
        if index == packet_player_index and 0 <= packet_player_index < 24:
            await self.store.update(player_car_index=packet_player_index)
            await self.store.merge_player_lap_history(history)
            if self.on_player_lap_history:
                await self.on_player_lap_history(
                    int(snapshot.get("session_uid", 0)), history
                )

    async def handle_PacketCarSetupData(self, packet: Any) -> None:
        player_index = _packet_player_index(packet, packet.car_setup_data)
        if player_index is None:
            return
        setup = packet.car_setup_data[player_index]
        data = {field: getattr(setup, field) for field, _ in setup._fields_}
        await self.store.update(
            car_setup=data,
            next_front_wing_value=float(packet.next_front_wing_value),
        )

    async def handle_PacketLobbyInfoData(self, packet: Any) -> None:
        """Multiplayer lobby roster: how many players, human, ready, spectating.

        readyStatus is 0 = not ready, 1 = ready, 2 = spectating; only 1 counts
        as ready.
        """
        players = list(getattr(packet, "lobby_players", []))[
            : int(getattr(packet, "num_players", 0))
        ]
        ready = sum(
            1 for player in players if int(getattr(player, "ready_status", 0)) == 1
        )
        spectating = sum(
            1 for player in players if int(getattr(player, "ready_status", 0)) == 2
        )
        human = sum(
            1 for player in players if not bool(getattr(player, "ai_controlled", True))
        )
        await self.store.update(
            lobby={
                "num_players": int(getattr(packet, "num_players", 0)),
                "human_players": human,
                "ready_players": ready,
                "spectating_players": spectating,
            }
        )

    async def handle_PacketTyreSetsData(self, packet: Any) -> None:
        snapshot = await self.store.peek("player_car_index")
        if int(packet.car_idx) != int(snapshot.get("player_car_index", 0) or 0):
            return
        sets = []
        for index, tyre in enumerate(packet.tyre_set_data):
            compound = VISUAL_COMPOUNDS.get(int(tyre.visual_tyre_compound), "UNKNOWN")
            sets.append(
                {
                    "index": index,
                    "compound": compound,
                    "actual_compound": int(tyre.actual_tyre_compound),
                    "wear_pct": float(tyre.wear),
                    "available": bool(tyre.available),
                    "recommended_session": int(tyre.recommended_session),
                    "life_span_laps": int(tyre.life_span),
                    "usable_life_laps": int(tyre.usable_life),
                    "lap_delta_ms": int(tyre.lap_delta_time),
                    "fitted": bool(tyre.fitted),
                }
            )
        await self.store.update(
            tyre_sets=sets,
            fitted_tyre_set_idx=int(packet.fitted_idx),
        )

    async def handle_PacketTimeTrialData(self, packet: Any) -> None:
        """Time Trial personal best, session best and rival splits.

        This packet had no handler, so Time Trial sessions had no reference to
        compare against beyond Your Pit Box's own stored history.
        """

        def decode(data: Any) -> dict[str, Any] | None:
            lap_ms = int(getattr(data, "lap_time_in_ms", 0) or 0)
            if lap_ms <= 0:
                return None
            return {
                "car_idx": int(data.car_idx),
                "team_id": int(data.team_id),
                "lap_ms": lap_ms,
                "s1_ms": int(data.sector1_time_in_ms),
                "s2_ms": int(data.sector2_time_in_ms),
                "s3_ms": int(data.sector3_time_in_ms),
                "valid": bool(data.valid),
                "assists": {
                    "traction_control": int(data.traction_control),
                    "gearbox": int(data.gearbox_assist),
                    "anti_lock_brakes": bool(data.anti_lock_brakes),
                    "equal_car_performance": bool(data.equal_car_performance),
                    "custom_setup": bool(data.custom_setup),
                },
            }

        await self.store.update(
            time_trial={
                "session_best": decode(packet.player_session_best_data_set),
                "personal_best": decode(packet.personal_best_data_set),
                "rival": decode(packet.rival_data_set),
            }
        )

    async def handle_PacketLapPositionsData(self, packet: Any) -> None:
        num_laps = int(packet.num_laps)
        lap_start = int(packet.lap_start)
        values = list(packet.position_for_vehicle_idx)
        snapshot = await self.store.peek("active_cars")
        active_cars = int(snapshot.get("active_cars", 0) or 0) or 24

        def apply(state):  # type: ignore[no-untyped-def]
            for vehicle_index in range(min(active_cars, 24)):
                history = []
                for offset in range(num_laps):
                    flattened = offset * 24 + vehicle_index
                    if flattened >= len(values):
                        break
                    position = int(values[flattened])
                    if position:
                        history.append(
                            {"lap": lap_start + offset, "position": position}
                        )
                if history:
                    state.drivers[vehicle_index].position_history = history

        await self.store.mutate(apply)

    async def handle_PacketFinalClassificationData(self, packet: Any) -> None:
        player_index = _packet_player_index(packet, packet.classification_data)
        if player_index is None:
            return
        result = packet.classification_data[player_index]
        session_uid = int(getattr(packet.header, "session_uid", 0))
        assembler_session = (
            self.session_assembler.session
            if self.session_assembler is not None
            else None
        )
        classification_key = (
            assembler_session.id
            if assembler_session is not None
            and assembler_session.game_session_uid == str(session_uid)
            else str(session_uid)
        )
        already_recorded = classification_key in self._classified_sessions
        self._classified_sessions.add(classification_key)
        await self.store.update(
            final_classification={
                "position": int(result.position),
                "laps": int(result.num_laps),
                "grid_position": int(result.grid_position),
                "points": int(result.points),
                "pit_stops": int(result.num_pit_stops),
                "best_lap_ms": int(result.best_lap_time_in_ms),
                "total_race_time_s": float(result.total_race_time),
                "penalties_s": int(result.penalties_time),
            }
        )
        if already_recorded:
            return
        await self.store.append_event("CHQF", {"position": int(result.position)})
        if self.on_final_classification:
            await self.on_final_classification()

    async def handle_PacketEventData(self, packet: Any) -> None:
        code = _event_code(packet)
        payload: dict[str, Any] = {}
        if code == "BUTN":
            status = int(packet.event_details.buttons.button_status)
            payload["button_status"] = status
            if self.on_button_status:
                self.on_button_status(status)
        elif code == "PENA":
            detail = packet.event_details.penalty
            payload = {
                "vehicle_idx": int(detail.vehicle_idx),
                "type": int(detail.penalty_type),
                "infringement": int(getattr(detail, "infringement_type", 0)),
                "other_vehicle_idx": int(getattr(detail, "other_vehicle_idx", 255)),
                "places_gained": int(getattr(detail, "places_gained", 0)),
                "time": int(detail.time),
                "lap": int(detail.lap_num),
            }
        elif code == "SCAR":
            detail = packet.event_details.safety_car
            safety_type_id = int(detail.safety_car_type)
            event_type_id = int(detail.event_type)
            safety_name = EVENT_SAFETY_CAR.get(safety_type_id, "unknown")
            event_name = SAFETY_CAR_EVENTS.get(event_type_id, "unknown")
            payload = {
                "safety_car_type": safety_type_id,
                "safety_car": safety_name,
                "event_type": event_type_id,
                "event": event_name,
            }

            def apply_safety_car(state):  # type: ignore[no-untyped-def]
                state.last_safety_car_event_type = event_type_id
                state.race_control_changed_at = time.time()
                if event_type_id == 0 and safety_name not in {"none", "unknown"}:
                    # "none" is a real value of this enum now that the map is
                    # correct, and a deployment of nothing is not a deployment:
                    # without this guard it would set the phase to "formation"
                    # and announce a neutralisation that is not happening.
                    state.safety_car = safety_name
                    state.red_flag_active = False
                    state.race_control_phase = (
                        "safety_car"
                        if safety_name == "full"
                        else "vsc"
                        if safety_name == "virtual"
                        else "formation"
                    )
                elif event_type_id == 1:
                    state.race_control_phase = (
                        "safety_car_ending"
                        if safety_name == "full"
                        else "vsc_ending"
                        if safety_name == "virtual"
                        else "formation_ending"
                    )
                elif event_type_id in {2, 3}:
                    state.safety_car = "none"
                    state.race_control_phase = "green"

            await self.store.mutate(apply_safety_car)
        elif code == "RDFL":
            payload = {"active": True}

            def apply_red_flag(state):  # type: ignore[no-untyped-def]
                state.red_flag_active = True
                state.race_control_phase = "red_flag"
                state.race_control_changed_at = time.time()
                state.safety_car = "none"
                state.restart_grid = [
                    {
                        "car_idx": int(driver.car_idx),
                        "position": int(driver.position),
                        "name": driver.name or "Unknown",
                        "tyre_compound": driver.tyre_compound or "UNKNOWN",
                        "tyre_age": int(driver.tyre_age),
                    }
                    for driver in sorted(
                        (item for item in state.drivers if int(item.position) > 0),
                        key=lambda item: int(item.position),
                    )
                ]

            await self.store.mutate(apply_red_flag)
        elif code in {"SSTA", "LGOT"}:

            def clear_suspension(state):  # type: ignore[no-untyped-def]
                state.red_flag_active = False
                state.race_control_phase = (
                    "safety_car"
                    if state.safety_car == "full"
                    else "vsc"
                    if state.safety_car == "virtual"
                    else "formation"
                    if state.safety_car == "formation"
                    else "green"
                )
                state.race_control_changed_at = time.time()

            await self.store.mutate(clear_suspension)
        elif code == "OVTK":
            detail = packet.event_details.overtake
            payload = {
                "overtaking": int(detail.overtaking_vehicle_idx),
                "overtaken": int(detail.being_overtaken_vehicle_idx),
            }
        # The following carried an empty payload, so the events log recorded
        # only that "something happened": a retirement never said who retired,
        # and 4 800 speed-trap events stored no speed.
        elif code == "FTLP":
            detail = packet.event_details.fastest_lap
            payload = {
                "vehicle_idx": int(detail.vehicle_idx),
                "lap_time_s": round(float(detail.lap_time), 3),
            }
        elif code == "RTMT":
            detail = packet.event_details.retirement
            payload = {
                "vehicle_idx": int(detail.vehicle_idx),
                "reason": int(getattr(detail, "reason", 0)),
            }
        elif code == "RCWN":
            payload = {"vehicle_idx": int(packet.event_details.race_winner.vehicle_idx)}
        elif code == "TMPT":
            payload = {
                "vehicle_idx": int(packet.event_details.team_mate_in_pits.vehicle_idx)
            }
        elif code == "SPTP":
            detail = packet.event_details.speed_trap
            payload = {
                "vehicle_idx": int(detail.vehicle_idx),
                "speed_kph": round(float(detail.speed), 1),
                "overall_fastest": bool(detail.is_overall_fastest_in_session),
                "driver_fastest": bool(detail.is_driver_fastest_in_session),
                "session_fastest_vehicle_idx": int(
                    detail.fastest_vehicle_idx_in_session
                ),
                "session_fastest_speed_kph": round(
                    float(detail.fastest_speed_in_session), 1
                ),
            }
        elif code == "STLG":
            payload = {"num_lights": int(packet.event_details.start_lights.num_lights)}
        elif code == "DTSV":
            payload = {
                "vehicle_idx": int(
                    packet.event_details.drive_through_penalty_served.vehicle_idx
                )
            }
        elif code == "SGSV":
            detail = packet.event_details.stop_go_penalty_served
            payload = {
                "vehicle_idx": int(detail.vehicle_idx),
                "stop_time_s": round(float(getattr(detail, "stop_time", 0.0)), 2),
            }
        elif code == "DRSD":
            payload = {"reason": int(packet.event_details.drs_disabled.reason)}
        elif code == "FLBK":
            detail = packet.event_details.flashback
            payload = {
                "frame_identifier": int(detail.flashback_frame_identifier),
                "session_time": float(detail.flashback_session_time),
            }

            def apply_flashback(state):  # type: ignore[no-untyped-def]
                state.timeline_epoch += 1
                state.traces.clear()

            await self.store.mutate(apply_flashback)
        elif code == "COLL":
            detail = packet.event_details.collision
            player_index = int(getattr(packet.header, "player_car_index", 255))
            v1, v2 = int(detail.vehicle1_idx), int(detail.vehicle2_idx)
            payload = {
                "vehicle1_idx": v1,
                "vehicle2_idx": v2,
                "severity": int(getattr(detail, "severity", 0)),
                "involves_player": player_index in {v1, v2},
            }
        if code not in UNLOGGED_EVENTS:
            await self.store.append_event(code, payload)
