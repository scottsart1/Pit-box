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

from tools.scenarios import SCENARIOS, SILVERSTONE, Scenario  # noqa: E402

SESSION_UID = 0x5049_5457_414C_4C21  # "PITWALL!" - obviously synthetic

# Module-level names kept for anything importing them. They describe the
# default scenario; a Race built with another scenario reads from that instead.
TRACK_ID = SILVERSTONE.track_id
TRACK_LENGTH_M = SILVERSTONE.track_length_m
TOTAL_LAPS = SILVERSTONE.total_laps
FIELD = [(d.name, d.team_id, d.race_number, d.pace_offset_s) for d in SILVERSTONE.drivers]
PLAYER_INDEX = SILVERSTONE.player_index
BASE_LAP_S = SILVERSTONE.base_lap_s

# Compound ids: 16 soft, 17 medium, 18 hard, 7 inter, 8 wet (visual)
VISUAL = {"SOFT": 16, "MEDIUM": 17, "HARD": 18, "INTER": 7, "WET": 8}
ACTUAL = {"SOFT": 18, "MEDIUM": 17, "HARD": 16, "INTER": 7, "WET": 8}


def _header(
    packet_id: int,
    frame: int,
    session_time: float,
    player_index: int = PLAYER_INDEX,
) -> P.PacketHeader:
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
    header.player_car_index = player_index
    header.secondary_player_car_index = 255
    return header


class Race:
    """A deterministic mid-race state that advances in real time."""

    def __init__(self, seed: int = 4, scenario: Scenario = SILVERSTONE) -> None:
        self.scenario = scenario
        self.field = [
            (d.name, d.team_id, d.race_number, d.pace_offset_s) for d in scenario.drivers
        ]
        self.player = scenario.player_index
        self.track_length = scenario.track_length_m
        self.rng = random.Random(seed)
        self.lap = scenario.display_lap
        self.frame = 120_000
        self.t0 = time.monotonic()
        self.session_time = scenario.session_time_s
        # Stint state per car: compound, age, wear
        self.compound = []
        self.age = []
        self.wear = []
        self.stops = []
        self.distance = []
        for index in range(len(self.field)):
            on_hard = index % 3 == 0
            self.compound.append("HARD" if on_hard else "MEDIUM")
            self.age.append(self.rng.randint(12, 22) if on_hard else self.rng.randint(8, 16))
            base = self.age[-1] * (1.9 if not on_hard else 1.5)
            self.wear.append([min(96.0, base + self.rng.uniform(-3, 7)) for _ in range(4)])
            self.stops.append(1)
            self.distance.append(self.rng.uniform(0, self.track_length))
        # Cars whose stint the scenario pins, because the demo turns on them.
        for index, (compound, age) in scenario.stint_overrides.items():
            self.compound[index] = compound
            self.age[index] = age
            base = age * (1.9 if compound != "HARD" else 1.5)
            self.wear[index] = [min(96.0, base + self.rng.uniform(-2, 4)) for _ in range(4)]
        self.distance[self.player] = self.track_length * 0.31
        self.lap_ms = [
            int((scenario.base_lap_s + offset + self.rng.uniform(-0.15, 0.35)) * 1000)
            for _, _, _, offset in self.field
        ]
        # Cumulative gap to the leader in seconds. A real mid-race field is
        # spread over half a minute or more, which is what makes an undercut
        # or a rejoin position mean anything. A scenario that depends on an
        # exact gap being on screen supplies them instead of rolling for them.
        if scenario.running_order is not None:
            by_name = {name: index for index, (name, *_rest) in enumerate(self.field)}
            missing = [n for n, _ in scenario.running_order if n not in by_name]
            if missing:
                raise ValueError(f"running order names not in the field: {missing}")
            if len(scenario.running_order) != len(self.field):
                raise ValueError(
                    f"running order lists {len(scenario.running_order)} cars, "
                    f"field has {len(self.field)}"
                )
            self.gap_to_leader = [0.0] * len(self.field)
            for name, gap in scenario.running_order:
                self.gap_to_leader[by_name[name]] = gap
            if len(set(self.gap_to_leader)) != len(self.gap_to_leader):
                raise ValueError(
                    "two cars share a gap to the leader; the running order is "
                    "ambiguous and the player would land in the wrong position"
                )
        else:
            self.gap_to_leader = [0.0]
            for _ in range(1, len(self.field)):
                self.gap_to_leader.append(
                    self.gap_to_leader[-1] + self.rng.uniform(0.9, 3.4)
                )
        # Running order is the gap order, not the field order. For a scenario
        # with generated gaps these are the same, so the original demo is
        # unchanged; for a designed one it is what puts the player in the
        # position the scenario describes.
        self.order = sorted(range(len(self.field)), key=lambda i: self.gap_to_leader[i])
        self.position = [0] * len(self.field)
        for place, index in enumerate(self.order, start=1):
            self.position[index] = place
        self.set_display_state()

    def set_display_state(self) -> None:
        """Pin the player's car to the scenario the demo is meant to show.

        The warm-up laps exist only to populate lap history, pace and the map.
        They also age the tyres, so without this the displayed race is not the
        decision point the demo was designed around.
        """
        scenario = self.scenario
        self.age[self.player] = scenario.player_tyre_age
        self.wear[self.player] = list(scenario.player_wear)
        self.compound[self.player] = scenario.player_compound
        for index, (compound, age) in scenario.stint_overrides.items():
            self.compound[index] = compound
            self.age[index] = age
        if scenario.hold_lap:
            # Keep the race at the moment the demo is about. Without this a
            # long capture drifts nine laps and the screen contradicts the
            # narration about how many are left.
            self.lap = scenario.display_lap

    def advance(self, warm_up: bool = False) -> None:
        elapsed = time.monotonic() - self.t0
        self.session_time = self.scenario.session_time_s + elapsed
        self.frame += 1
        for index in range(len(self.field)):
            speed_ms = self.track_length / (self.lap_ms[index] / 1000.0)
            step = speed_ms * (
                0.9 if warm_up else 0.05 * self.scenario.time_scale
            )
            moved = self.distance[index] + step
            if moved >= self.track_length:
                # A lap completed. Vary the next one so the pace trace looks
                # like a stint rather than a metronome.
                self.age[index] += 1
                drift = self.rng.uniform(-0.12, 0.28)
                self.lap_ms[index] = int(
                    (self.scenario.base_lap_s + self.field[index][3] + drift) * 1000
                    + self.age[index] * 26
                )
                for wheel in range(4):
                    self.wear[index][wheel] = min(
                        98.0, self.wear[index][wheel] + self.rng.uniform(1.2, 2.1)
                    )
                if index == self.player:
                    # The lap number must roll over on the same packet the
                    # player crosses the line, or lap completion is never
                    # detected and no lap is ever recorded.
                    self.lap += 1
            self.distance[index] = moved % self.track_length

    def player_speed_kph(self) -> int:
        # A plausible speed trace around the lap rather than a constant.
        phase = self.distance[self.player] / self.track_length * math.tau
        return int(210 + 95 * math.sin(phase * 3.0) ** 2)

    def session_packet(self) -> bytes:
        scenario = self.scenario
        packet = P.PacketSessionData()
        packet.header = _header(1, self.frame, self.session_time, self.player)
        packet.weather = scenario.weather
        packet.track_temperature = scenario.track_temperature
        packet.air_temperature = scenario.air_temperature
        packet.total_laps = scenario.total_laps
        packet.track_length = scenario.track_length_m
        packet.session_type = 15  # Race
        packet.track_id = scenario.track_id
        packet.formula = 0
        packet.session_time_left = scenario.session_time_left_s
        packet.session_duration = scenario.session_duration_s
        packet.pit_speed_limit = 80
        packet.game_paused = 0
        packet.is_spectating = 0
        packet.safety_car_status = 0
        packet.network_game = 0
        packet.num_marshal_zones = 0
        packet.pit_stop_window_ideal_lap = scenario.pit_window_ideal_lap
        packet.num_weather_forecast_samples = len(scenario.forecast)
        for offset, (minutes, weather, rain) in enumerate(scenario.forecast):
            sample = packet.weather_forecast_samples[offset]
            sample.session_type = 15
            sample.time_offset = minutes
            sample.weather = weather
            sample.track_temperature = scenario.track_temperature - offset
            sample.air_temperature = scenario.air_temperature - offset
            sample.rain_percentage = rain
        return bytes(packet.pack())

    def participants_packet(self) -> bytes:
        packet = P.PacketParticipantsData()
        packet.header = _header(4, self.frame, self.session_time, self.player)
        packet.num_active_cars = len(self.field)
        for index, (name, team, number, _) in enumerate(self.field):
            item = packet.participants[index]
            item.ai_controlled = 0 if index == self.player else 1
            item.driver_id = 255
            item.network_id = 0
            item.team_id = team
            item.my_team = 1 if index == self.player else 0
            item.race_number = number
            item.nationality = 1
            item.name = name.encode("utf-8")[:47]
            item.your_telemetry = 1
            item.show_online_names = 1
            item.platform = 3
        return bytes(packet.pack())

    def lap_packet(self) -> bytes:
        packet = P.PacketLapData()
        packet.header = _header(2, self.frame, self.session_time, self.player)
        for index in range(len(self.field)):
            item = packet.lap_data[index]
            item.last_lap_time_in_ms = self.lap_ms[index]
            item.current_lap_time_in_ms = int(
                self.distance[index] / self.track_length * self.lap_ms[index]
            )
            item.lap_distance = self.distance[index]
            item.total_distance = self.distance[index] + (self.lap - 1) * self.track_length
            item.car_position = self.position[index]
            item.current_lap_num = self.lap
            item.pit_status = 0
            item.num_pit_stops = self.stops[index]
            item.sector = 0 if self.distance[index] < self.track_length / 3 else (
                1 if self.distance[index] < 2 * self.track_length / 3 else 2
            )
            item.current_lap_invalid = 0
            item.penalties = 0
            item.grid_position = self.position[index]
            item.driver_status = 4
            item.result_status = 2
            # Realistic mid-race spread: seconds between cars, not tenths, so
            # the rejoin and undercut models see a plausible field.
            gap_to_leader = self.gap_to_leader[index]
            item.delta_to_race_leader_ms_part = int(gap_to_leader * 1000) % 60_000
            item.delta_to_race_leader_minutes_part = int(gap_to_leader // 60)
            # The car in front is the one ahead on the road, which is the
            # previous entry in running order rather than the previous index.
            place = self.position[index]
            ahead = (
                0.0 if place <= 1
                else gap_to_leader - self.gap_to_leader[self.order[place - 2]]
            )
            item.delta_to_car_in_front_ms_part = int(max(0.0, ahead) * 1000) % 60_000
            item.delta_to_car_in_front_minutes_part = 0
            item.speed_trap_fastest_speed = 308.0 - place * 0.7
        return bytes(packet.pack())

    def telemetry_packet(self) -> bytes:
        packet = P.PacketCarTelemetryData()
        packet.header = _header(6, self.frame, self.session_time, self.player)
        for index in range(len(self.field)):
            item = packet.car_telemetry_data[index]
            phase = self.distance[index] / self.track_length * math.tau
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
        packet.header = _header(7, self.frame, self.session_time, self.player)
        for index in range(len(self.field)):
            item = packet.car_status_data[index]
            item.traction_control = 0
            item.anti_lock_brakes = 0
            item.fuel_mix = 1
            item.front_brake_bias = 57
            item.pit_limiter_status = 0
            # Fuel is held steady across the demo laps on purpose. A load that
            # drifts lap to lap makes every pair of laps "comparable with
            # caveats" on fuel, which correctly blocks strict coaching and
            # leaves the analysis screens with nothing to show.
            item.fuel_in_tank = 34.0 if index == self.player else 36.0
            item.fuel_capacity = 110.0
            # The player is marginal on fuel: a real thing to ask about.
            item.fuel_remaining_laps = (
                -0.4 if index == self.player else 0.6 + (index % 4) * 0.3
            )
            item.max_rpm = 13000
            item.idle_rpm = 4000
            item.max_gears = 8
            item.drs_allowed = 1
            item.actual_tyre_compound = ACTUAL[self.compound[index]]
            item.visual_tyre_compound = VISUAL[self.compound[index]]
            item.tyres_age_laps = self.age[index]
            item.vehicle_fia_flags = 0
            item.ers_store_energy = 3.1e6 if index == self.player else 2.4e6
            item.ers_deploy_mode = 2
            item.ers_harvested_this_lap_mguk = 1.4e5
            item.ers_harvested_this_lap_mguh = 9.0e4
            item.ers_deployed_this_lap = 2.6e5
        return bytes(packet.pack())

    def motion_packet(self) -> bytes:
        """World positions, so the track map and racing line can build.

        The shape is a stylised Silverstone-like circuit rather than survey
        data: enough structure for the map and line comparison to be
        demonstrated honestly as synthetic.
        """
        packet = P.PacketMotionData()
        packet.header = _header(0, self.frame, self.session_time, self.player)
        for index in range(len(self.field)):
            item = packet.car_motion_data[index]
            u = self.distance[index] / self.track_length * math.tau
            # A closed asymmetric loop: two long straights, a fast sweep and a
            # slow complex, so corner detection has something real to find.
            x = 620.0 * math.sin(u) + 180.0 * math.sin(3.0 * u + 0.6)
            z = 430.0 * math.cos(u) - 150.0 * math.sin(2.0 * u)
            # Cars run slightly different lines; the player is on the racing line.
            offset = 0.0 if index == self.player else ((index % 5) - 2) * 1.6
            item.world_position_x = x + offset * math.cos(u)
            item.world_position_y = 12.0
            item.world_position_z = z + offset * math.sin(u)
            corner = math.sin(u * 3.0) ** 2
            # g forces and direction vectors are transmitted as scaled shorts,
            # not floats: g in 1/32768 units, directions normalised to 32767.
            def g(value: float) -> int:
                return max(-32768, min(32767, int(value * 32768.0 / 8.0)))

            item.g_force_lateral = g(3.4 * (1.0 - corner) * math.sin(u * 5.0))
            item.g_force_longitudinal = g(-4.1 * (1.0 - corner) + 1.6 * corner)
            item.g_force_vertical = g(1.0)
            heading = math.atan2(math.cos(u), -math.sin(u))
            item.world_forward_dir_x = int(math.sin(heading) * 32767)
            item.world_forward_dir_y = 0
            item.world_forward_dir_z = int(math.cos(heading) * 32767)
            item.world_right_dir_x = int(math.cos(heading) * 32767)
            item.world_right_dir_y = 0
            item.world_right_dir_z = int(-math.sin(heading) * 32767)
            item.yaw = heading
            item.pitch = 0.0
            item.roll = 0.0
        return bytes(packet.pack())

    def damage_packet(self) -> bytes:
        packet = P.PacketCarDamageData()
        packet.header = _header(10, self.frame, self.session_time, self.player)
        for index in range(len(self.field)):
            item = packet.car_damage_data[index]
            for wheel in range(4):
                item.tyres_wear[wheel] = self.wear[index][wheel]
                item.tyres_damage[wheel] = 0
                item.brakes_damage[wheel] = int(self.wear[index][wheel] * 0.12)
                item.tyre_blisters[wheel] = 0
            # A little front wing damage on the player: plausible mid-race.
            item.front_left_wing_damage = 4 if index == self.player else 0
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
    parser.add_argument(
        "--scenario", default=SILVERSTONE.key, choices=sorted(SCENARIOS),
        help="which invented race to broadcast",
    )
    args = parser.parse_args()

    scenario = SCENARIOS[args.scenario]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    race = Race(args.seed, scenario)
    target = (args.host, args.port)
    print(f"broadcasting synthetic {scenario.caption} to {args.host}:{args.port} "
          f"for {args.seconds:.0f}s")

    # Run several laps quickly first, so the pace trace, lap history and track
    # map are populated from real completed laps before the race settles at
    # its display lap. Without this the screens are technically live but empty.
    race.lap = scenario.start_lap
    race.distance = [d * 0.2 for d in race.distance]
    warm_up_target = scenario.display_lap
    guard = 0
    while race.lap < warm_up_target and guard < 4000:
        guard += 1
        race.advance(warm_up=True)
        sock.sendto(race.session_packet(), target)
        sock.sendto(race.participants_packet(), target)
        sock.sendto(race.lap_packet(), target)
        sock.sendto(race.motion_packet(), target)
        sock.sendto(race.telemetry_packet(), target)
        sock.sendto(race.status_packet(), target)
        sock.sendto(race.damage_packet(), target)
        time.sleep(0.015)
    race.set_display_state()
    print(f"warm-up complete: now on lap {race.lap} after {guard} ticks")

    deadline = time.monotonic() + args.seconds
    tick = 0
    while time.monotonic() < deadline:
        race.advance()
        # Hold the designed decision point on screen: the demo is a
        # single moment of a race, not a race that runs away from it.
        race.set_display_state()
        sock.sendto(race.motion_packet(), target)
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
