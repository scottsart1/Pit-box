"""Broadcast a synthetic but realistic race as genuine F1 2026 UDP packets.

This exists so documentation screenshots show the real application driven by
the real receiver, parser and state layer, rather than mocked markup. Every
datagram here is a real packet structure serialized with the same library the
game's output is parsed by, sent to the configured listener.

The race is invented. It is not a recording of any real session.

    python -m tools.demo_broadcast --host 127.0.0.1 --port 20777 --seconds 25
"""

from __future__ import annotations

import argparse
import math
import random
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import f1.packets as P  # noqa: E402

SESSION_UID = 0x5049_5457_414C_4C21  # "PITWALL!" - obviously synthetic
TRACK_ID = 7  # Silverstone
TRACK_LENGTH_M = 5891
TOTAL_LAPS = 52
FIELD = [
    # (name, team_id, race_number, base pace offset seconds)
    ("VERSTAPPEN", 0, 1, 0.00), ("NORRIS", 2, 4, 0.09),
    ("LECLERC", 1, 16, 0.14), ("PIASTRI", 2, 81, 0.21),
    ("RUSSELL", 3, 63, 0.26), ("HAMILTON", 1, 44, 0.31),
    ("SAINZ", 4, 55, 0.38), ("ALONSO", 5, 14, 0.44),
    ("HULKENBERG", 6, 27, 0.52), ("STROLL", 5, 18, 0.58),
    ("TSUNODA", 7, 22, 0.64), ("ALBON", 4, 23, 0.71),
    ("GASLY", 8, 10, 0.77), ("OCON", 8, 31, 0.83),
    ("BEARMAN", 6, 87, 0.90), ("LAWSON", 7, 30, 0.96),
    ("COLAPINTO", 9, 43, 1.03), ("BORTOLETO", 9, 5, 1.10),
    ("ANTONELLI", 3, 12, 1.16), ("DOOHAN", 0, 7, 1.23),
]
PLAYER_INDEX = 4  # Russell's slot: mid-field, a real race to engineer
BASE_LAP_S = 88.6

# Compound ids: 16 soft, 17 medium, 18 hard, 7 inter, 8 wet (visual)
VISUAL = {"SOFT": 16, "MEDIUM": 17, "HARD": 18, "INTER": 7, "WET": 8}
ACTUAL = {"SOFT": 18, "MEDIUM": 17, "HARD": 16, "INTER": 7, "WET": 8}


def _header(packet_id: int, frame: int, session_time: float) -> P.PacketHeader:
    header = P.PacketHeader()
    header.packet_format = 2026
    header.game_year = 26
    header.game_major_version = 1
    header.game_minor_version = 2
    header.packet_version = 1
    header.packet_id = packet_id
    header.session_uid = SESSION_UID
    header.session_time = session_time
    header.frame_identifier = frame
    header.overall_frame_identifier = frame
    header.player_car_index = PLAYER_INDEX
    header.secondary_player_car_index = 255
    return header


class Race:
    """A deterministic mid-race state that advances in real time."""

    def __init__(self, seed: int = 4) -> None:
        self.rng = random.Random(seed)
        self.lap = 34
        self.frame = 120_000
        self.t0 = time.monotonic()
        self.session_time = 3120.0
        # Stint state per car: compound, age, wear
        self.compound = []
        self.age = []
        self.wear = []
        self.stops = []
        self.distance = []
        for index in range(len(FIELD)):
            on_hard = index % 3 == 0
            self.compound.append("HARD" if on_hard else "MEDIUM")
            self.age.append(self.rng.randint(12, 22) if on_hard else self.rng.randint(8, 16))
            base = self.age[-1] * (1.9 if not on_hard else 1.5)
            self.wear.append([min(96.0, base + self.rng.uniform(-3, 7)) for _ in range(4)])
            self.stops.append(1)
            self.distance.append(self.rng.uniform(0, TRACK_LENGTH_M))
        # The player is deliberately on older mediums with a decision coming.
        self.compound[PLAYER_INDEX] = "MEDIUM"
        self.age[PLAYER_INDEX] = 19
        self.wear[PLAYER_INDEX] = [71.0, 73.5, 78.0, 80.5]
        self.distance[PLAYER_INDEX] = 1820.0
        self.lap_ms = [
            int((BASE_LAP_S + offset + self.rng.uniform(-0.15, 0.35)) * 1000)
            for _, _, _, offset in FIELD
        ]

    def advance(self) -> None:
        elapsed = time.monotonic() - self.t0
        self.session_time = 3120.0 + elapsed
        self.frame += 1
        for index in range(len(FIELD)):
            speed_ms = TRACK_LENGTH_M / (self.lap_ms[index] / 1000.0)
            self.distance[index] = (self.distance[index] + speed_ms * 0.05) % TRACK_LENGTH_M

    def player_speed_kph(self) -> int:
        # A plausible speed trace around the lap rather than a constant.
        phase = self.distance[PLAYER_INDEX] / TRACK_LENGTH_M * math.tau
        return int(210 + 95 * math.sin(phase * 3.0) ** 2)

    def session_packet(self) -> bytes:
        packet = P.PacketSessionData()
        packet.header = _header(1, self.frame, self.session_time)
        packet.weather = 2  # overcast: rain is forecast, none on track yet
        packet.track_temperature = 34
        packet.air_temperature = 21
        packet.total_laps = TOTAL_LAPS
        packet.track_length = TRACK_LENGTH_M
        packet.session_type = 15  # Race
        packet.track_id = TRACK_ID
        packet.formula = 0
        packet.session_time_left = 1680
        packet.session_duration = 5400
        packet.pit_speed_limit = 80
        packet.game_paused = 0
        packet.is_spectating = 0
        packet.safety_car_status = 0
        packet.network_game = 0
        packet.num_marshal_zones = 0
        packet.pit_stop_window_ideal_lap = 36
        packet.num_weather_forecast_samples = 3
        for offset, (minutes, weather, rain) in enumerate(
            ((5, 2, 20), (15, 3, 65), (30, 3, 80))
        ):
            sample = packet.weather_forecast_samples[offset]
            sample.session_type = 15
            sample.time_offset = minutes
            sample.weather = weather
            sample.track_temperature = 33 - offset
            sample.air_temperature = 21 - offset
            sample.rain_percentage = rain
        return bytes(packet.pack())

    def participants_packet(self) -> bytes:
        packet = P.PacketParticipantsData()
        packet.header = _header(4, self.frame, self.session_time)
        packet.num_active_cars = len(FIELD)
        for index, (name, team, number, _) in enumerate(FIELD):
            item = packet.participants[index]
            item.ai_controlled = 0 if index == PLAYER_INDEX else 1
            item.driver_id = 255
            item.network_id = 0
            item.team_id = team
            item.my_team = 1 if index == PLAYER_INDEX else 0
            item.race_number = number
            item.nationality = 1
            item.name = name.encode("utf-8")[:47]
            item.your_telemetry = 1
            item.show_online_names = 1
            item.platform = 3
        return bytes(packet.pack())

    def lap_packet(self) -> bytes:
        packet = P.PacketLapData()
        packet.header = _header(2, self.frame, self.session_time)
        leader_ms = 0
        for index in range(len(FIELD)):
            item = packet.lap_data[index]
            item.last_lap_time_in_ms = self.lap_ms[index]
            item.current_lap_time_in_ms = int(
                self.distance[index] / TRACK_LENGTH_M * self.lap_ms[index]
            )
            item.lap_distance = self.distance[index]
            item.total_distance = self.distance[index] + (self.lap - 1) * TRACK_LENGTH_M
            item.car_position = index + 1
            item.current_lap_num = self.lap
            item.pit_status = 0
            item.num_pit_stops = self.stops[index]
            item.sector = 0 if self.distance[index] < TRACK_LENGTH_M / 3 else (
                1 if self.distance[index] < 2 * TRACK_LENGTH_M / 3 else 2
            )
            item.current_lap_invalid = 0
            item.penalties = 0
            item.grid_position = index + 1
            item.driver_status = 4
            item.result_status = 2
            gap = int(sum(FIELD[i][3] for i in range(index + 1)) * 1000) + index * 480
            item.delta_to_race_leader_ms_part = gap % 1000
            item.delta_to_race_leader_minutes_part = 0
            ahead = 0 if index == 0 else int(
                (FIELD[index][3] - FIELD[index - 1][3]) * 1000
            ) + 480
            item.delta_to_car_in_front_ms_part = max(0, ahead)
            item.delta_to_car_in_front_minutes_part = 0
            item.speed_trap_fastest_speed = 308.0 - index * 0.7
            leader_ms = leader_ms or self.lap_ms[index]
        return bytes(packet.pack())

    def telemetry_packet(self) -> bytes:
        packet = P.PacketCarTelemetryData()
        packet.header = _header(6, self.frame, self.session_time)
        for index in range(len(FIELD)):
            item = packet.car_telemetry_data[index]
            phase = self.distance[index] / TRACK_LENGTH_M * math.tau
            corner = math.sin(phase * 3.0) ** 2
            item.speed = int(210 + 95 * corner)
            item.throttle = max(0.0, min(1.0, 0.35 + 0.65 * corner))
            item.brake = max(0.0, 0.55 * (1.0 - corner) - 0.2)
            item.steer = 0.4 * math.sin(phase * 5.0)
            item.gear = max(1, min(8, int(2 + 6 * corner)))
            item.engine_rpm = int(9500 + 3200 * corner)
            item.drs = 1 if corner > 0.85 else 0
            item.rev_lights_percent = int(60 + 35 * corner)
            for wheel in range(4):
                item.tyres_surface_temperature[wheel] = int(96 + self.wear[index][wheel] * 0.18)
                item.tyres_inner_temperature[wheel] = int(92 + self.wear[index][wheel] * 0.16)
                item.brakes_temperature[wheel] = int(420 + 180 * (1 - corner))
                item.tyres_pressure[wheel] = 22.4 + wheel * 0.1
        return bytes(packet.pack())

    def status_packet(self) -> bytes:
        packet = P.PacketCarStatusData()
        packet.header = _header(7, self.frame, self.session_time)
        for index in range(len(FIELD)):
            item = packet.car_status_data[index]
            item.traction_control = 0
            item.anti_lock_brakes = 0
            item.fuel_mix = 1
            item.front_brake_bias = 57
            item.pit_limiter_status = 0
            remaining = TOTAL_LAPS - self.lap
            item.fuel_in_tank = 1.9 * remaining + (2.4 if index == PLAYER_INDEX else 3.0)
            item.fuel_capacity = 110.0
            # The player is marginal on fuel: a real thing to ask about.
            item.fuel_remaining_laps = (
                -0.4 if index == PLAYER_INDEX else 0.6 + (index % 4) * 0.3
            )
            item.max_rpm = 13000
            item.idle_rpm = 4000
            item.max_gears = 8
            item.drs_allowed = 1
            item.actual_tyre_compound = ACTUAL[self.compound[index]]
            item.visual_tyre_compound = VISUAL[self.compound[index]]
            item.tyres_age_laps = self.age[index]
            item.vehicle_fia_flags = 0
            item.ers_store_energy = 3.1e6 if index == PLAYER_INDEX else 2.4e6
            item.ers_deploy_mode = 2
            item.ers_harvested_this_lap_mguk = 1.4e5
            item.ers_harvested_this_lap_mguh = 9.0e4
            item.ers_deployed_this_lap = 2.6e5
        return bytes(packet.pack())

    def damage_packet(self) -> bytes:
        packet = P.PacketCarDamageData()
        packet.header = _header(10, self.frame, self.session_time)
        for index in range(len(FIELD)):
            item = packet.car_damage_data[index]
            for wheel in range(4):
                item.tyres_wear[wheel] = self.wear[index][wheel]
                item.tyres_damage[wheel] = 0
                item.brakes_damage[wheel] = int(self.wear[index][wheel] * 0.12)
                item.tyre_blisters[wheel] = 0
            # A little front wing damage on the player: plausible mid-race.
            item.front_left_wing_damage = 4 if index == PLAYER_INDEX else 0
            item.front_right_wing_damage = 0
            item.rear_wing_damage = 0
            item.floor_damage = 0
            item.diffuser_damage = 0
            item.sidepod_damage = 0
            item.drs_fault = 0
            item.ers_fault = 0
            item.gear_box_damage = 6
            item.engine_damage = 4
        return bytes(packet.pack())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=20777)
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--seed", type=int, default=4)
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    race = Race(args.seed)
    target = (args.host, args.port)
    deadline = time.monotonic() + args.seconds
    tick = 0
    print(f"broadcasting synthetic Silverstone race to {args.host}:{args.port} "
          f"for {args.seconds:.0f}s")
    while time.monotonic() < deadline:
        race.advance()
        # Rates roughly mirror the game: telemetry/lap fast, session/status slower.
        sock.sendto(race.lap_packet(), target)
        sock.sendto(race.telemetry_packet(), target)
        if tick % 5 == 0:
            sock.sendto(race.status_packet(), target)
            sock.sendto(race.damage_packet(), target)
        if tick % 20 == 0:
            sock.sendto(race.session_packet(), target)
            sock.sendto(race.participants_packet(), target)
        tick += 1
        time.sleep(0.05)
    print(f"sent {tick} ticks")
    sock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
