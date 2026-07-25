from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Iterable

from f1.packets import SESSIONS, TRACKS as F1_TRACKS, resolve

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
EVENT_SAFETY_CAR = {0: "full", 1: "virtual", 2: "formation"}
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


def normalize_wheels(values: Iterable[Any]) -> list[Any]:
    """EA packets use RL, RR, FL, FR; Pit Wall stores FL, FR, RL, RR."""
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
        value in {16, 17}
        for value in weekend_session_ids[first_race_index + 1 :]
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

    # A shootout/qualifying session is timed and should not present a coherent
    # multi-lap race distance plus a race-length clock. In that conflict, the
    # race evidence is more useful than the enum for strategy and fuel logic.
    if (
        raw_mode in {"qualifying", "practice", "time_trial", "idle"}
        and race_distance_is_coherent
    ):
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
    ) -> None:
        self.store = store
        self.on_button_status = on_button_status
        self.on_player_lap_history = on_player_lap_history
        self.on_final_classification = on_final_classification
        self.loop = asyncio.get_running_loop()
        self.transport: asyncio.DatagramTransport | None = None
        self.packet_queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=queue_capacity)
        self.consumer_task: asyncio.Task[None] | None = None
        self._stats_counter = 0
        self._unhandled_packets: set[str] = set()

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]
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
        if self.consumer_task:
            self.consumer_task.cancel()
            self.consumer_task = None
        self.loop.create_task(self.store.update(connected=False, packet_rate_hz=0.0))

    def datagram_received(self, data: bytes, addr: Any) -> None:
        try:
            packet = resolve(data)
        except KeyError:
            self.loop.create_task(
                self.store.update(last_error="Unsupported F1 packet format/version")
            )
            return
        except Exception as exc:
            log.debug("Dropped malformed packet from %s: %s", addr, exc)
            self.loop.create_task(self.store.increment_dropped_packets())
            return

        try:
            self.packet_queue.put_nowait(packet)
        except asyncio.QueueFull:
            # Preserve packet order and keep the event loop responsive. A full
            # queue is visible in health telemetry rather than spawning another
            # task for every datagram.
            self.loop.create_task(self.store.increment_dropped_packets())

    async def _consume_packets(self) -> None:
        while True:
            packet = await self.packet_queue.get()
            try:
                await self._handle(packet)
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

    async def _handle(self, packet: Any) -> None:
        header = packet.header
        await self.store.mark_packet(
            header.packet_format,
            header.game_year,
            header.session_uid,
        )
        name = packet.__class__.__name__
        handler = getattr(self, f"handle_{name}", None)
        if handler:
            await handler(packet)
        elif name not in self._unhandled_packets:
            # Report each unknown packet type once. Silently discarding them
            # makes a newly added telemetry packet invisible during diagnosis.
            self._unhandled_packets.add(name)
            log.info("No handler for %s; packet ignored", name)

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

        snapshot = await self.store.snapshot_live()
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
            state.unserved_stop_go_penalties = int(
                player.num_unserved_stop_go_pens
            )
            state.pit_stop_should_serve_penalty = bool(
                player.pit_stop_should_serve_pen
            )
            state.pit_status = int(player.pit_status)
            state.pit_lane_time_ms = int(player.pit_lane_time_in_lane_in_ms)
            for index, lap in enumerate(packet.lap_data):
                driver = state.drivers[index]
                if not driver.active:
                    continue
                driver.position = int(lap.car_position)
                driver.current_lap = int(lap.current_lap_num)
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
                if index == player_index:
                    driver.gap_to_player_s = 0.0
                elif driver.position > 0:
                    leader_delta = (
                        _ms(
                            int(lap.delta_to_race_leader_minutes_part),
                            int(lap.delta_to_race_leader_ms_part),
                        )
                        / 1000.0
                    )
                    driver.gap_to_player_s = round(
                        leader_delta - player_delta_to_leader, 3
                    )

        await self.store.mutate(apply)

    async def handle_PacketCarTelemetryData(self, packet: Any) -> None:
        player_index = _packet_player_index(packet, packet.car_telemetry_data)
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
                driver = state.drivers[index]
                if not driver.active:
                    continue
                driver.tyre_compound = VISUAL_COMPOUNDS.get(
                    int(status.visual_tyre_compound), "UNKNOWN"
                )
                driver.tyre_age = int(status.tyres_age_laps)

        await self.store.mutate(apply)

    async def handle_PacketCarDamageData(self, packet: Any) -> None:
        player_index = _packet_player_index(packet, packet.car_damage_data)
        if player_index is None:
            return
        damage = packet.car_damage_data[player_index]
        wear = [float(value) for value in normalize_wheels(damage.tyres_wear)]
        tyre_damage = [int(value) for value in normalize_wheels(damage.tyres_damage)]
        blisters = [int(value) for value in normalize_wheels(damage.tyre_blisters)]

        # Power-unit component wear (percent used) is distinct from crash damage
        # and accumulates across a season toward grid-penalty thresholds.
        component_wear = {
            "ice": float(getattr(damage, "engine_ice_wear", 0.0)),
            "mguk": float(getattr(damage, "engine_mguk_wear", 0.0)),
            "mguh": float(getattr(damage, "engine_mguh_wear", 0.0)),
            "es": float(getattr(damage, "engine_es_wear", 0.0)),
            "ce": float(getattr(damage, "engine_ce_wear", 0.0)),
            "tc": float(getattr(damage, "engine_tc_wear", 0.0)),
        }
        engine_blown = bool(getattr(damage, "engine_blown", False))
        engine_seized = bool(getattr(damage, "engine_seized", False))

        def apply(state):  # type: ignore[no-untyped-def]
            state.tyre.wear = wear
            state.tyre.damage = tyre_damage
            state.tyre.blisters = blisters
            state.component_wear = component_wear
            state.damage = {
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
                "engine_blown": engine_blown,
                "engine_seized": engine_seized,
                "blisters": blisters,
            }

        await self.store.mutate(apply)

    async def handle_PacketMotionData(self, packet: Any) -> None:
        player_index = _packet_player_index(packet, packet.car_motion_data)
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
        snapshot = await self.store.snapshot_live()
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

    async def handle_PacketTyreSetsData(self, packet: Any) -> None:
        snapshot = await self.store.snapshot_live()
        if int(packet.car_idx) != int(snapshot.get("player_car_index", 0)):
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

    async def handle_PacketLapPositionsData(self, packet: Any) -> None:
        num_laps = int(packet.num_laps)
        lap_start = int(packet.lap_start)
        values = list(packet.position_for_vehicle_idx)
        snapshot = await self.store.snapshot_live()
        active_cars = int(snapshot.get("active_cars", 0)) or 24

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
                if event_type_id == 0:
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
        elif code == "FLBK":
            detail = packet.event_details.flashback
            payload = {
                "frame_identifier": int(detail.flashback_frame_identifier),
                "session_time": float(detail.flashback_session_time),
            }
            await self.store.mutate(lambda state: state.traces.clear())
        await self.store.append_event(code, payload)
