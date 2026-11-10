"""Synthetic F1 26 telemetry generator.

Drives a complete 20-car race into a running Your Pit Box over UDP, using the real
``f1-packets`` structures, so the dashboard, strategy engine and radio can be
exercised without a PS5 and without a wheel.

    python tools/replay_demo.py --laps 30 --speed 25

``--speed`` is the time compression: 25 means one race lap is simulated in about
four seconds. The generator is deliberately not a physics model; it produces
*plausible and self-consistent* timing, tyre and energy data, which is what the
deterministic layers consume.

A rain spell can be added: from a given lap of the player's the sky turns to
the given rain, the surface soaks at ``rain.py``'s own rate, every car's lap
and sector times follow ``rain.py``'s penalty for its tyre at that wetness, and
the cars stop for intermediates two laps later. The forecast samples carry the
given chance of rain before it arrives.

    python tools/replay_demo.py --laps 20 --rain-from-lap 8 --rain "Light rain" --forecast-chance 50

``race_packets`` yields the same race as (session time, datagram) pairs without
a socket or a clock, for ``tools/strategy_replay.py``.

Nothing here is imported by the application. It exists so a change can be seen
working end to end before it reaches a real session.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import socket
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from f1.packets import (
    PacketCarDamageData,
    PacketCarStatusData,
    PacketCarTelemetry2Data,
    PacketCarTelemetryData,
    PacketEventData,
    PacketFinalClassificationData,
    PacketHeader,
    PacketLapData,
    PacketLapPositionsData,
    PacketMotionData,
    PacketParticipantsData,
    PacketSessionData,
    PacketSessionHistoryData,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pitwall import rain as rain_model

# driver_id, team_id, race number, surname, relative pace (s/lap vs the leader)
GRID = [
    (9, 2, 1, b"VERSTAPPEN", 0.00),
    (54, 8, 4, b"NORRIS", 0.08),
    (112, 8, 81, b"PIASTRI", 0.15),
    (58, 1, 16, b"LECLERC", 0.22),
    (7, 1, 44, b"HAMILTON", 0.31),
    (50, 0, 63, b"RUSSELL", 0.35),
    (165, 0, 12, b"ANTONELLI", 0.44),
    (3, 4, 14, b"ALONSO", 0.58),
    (19, 4, 18, b"STROLL", 0.79),
    (59, 5, 10, b"GASLY", 0.66),
    (162, 5, 43, b"COLAPINTO", 0.83),
    (149, 6, 6, b"HADJAR", 0.61),
    (113, 6, 30, b"LAWSON", 0.72),
    (62, 3, 23, b"ALBON", 0.69),
    (0, 3, 55, b"SAINZ", 0.63),
    (147, 7, 87, b"BEARMAN", 0.77),
    (17, 7, 31, b"OCON", 0.81),
    (80, 9, 24, b"ZHOU", 0.92),
    (161, 9, 5, b"BORTOLETO", 0.88),
    (94, 2, 22, b"TSUNODA", 0.47),
]
PLAYER_INDEX = 5  # the driver runs as Russell, mid-grid, so there is a race on

COMPOUNDS = {"SOFT": 16, "MEDIUM": 17, "HARD": 18, "INTER": 7, "WET": 8}
WEAR_PER_LAP = {"SOFT": 2.9, "MEDIUM": 1.9, "HARD": 1.3, "INTER": 1.6, "WET": 1.4}
# Dry pace by tyre. On a wet surface every tyre is slowed by rain.py's own
# penalty for it, so a wet tyre on a dry track pays that, not an offset here.
PACE_OFFSET = {"SOFT": -0.55, "MEDIUM": 0.0, "HARD": 0.45, "INTER": 0.0, "WET": 0.0}
WEATHER_CODES = {"Clear": 0, "Light cloud": 1, "Overcast": 2, "Light rain": 3, "Heavy rain": 4, "Storm": 5}
TRACK_TEMP_C = 34
# Laps after the rain begins that a car stops for intermediates.
INTER_STOP_DELAY_LAPS = 2
# Share of the lap at which a stopping car turns into the pit lane.
PIT_ENTRY_FRACTION = 0.94


@dataclass(frozen=True)
class RainSpell:
    """Rain from the start of the player's ``from_lap`` to the flag."""

    from_lap: int
    label: str = "Light rain"
    forecast_chance: int = 50

TRACK_ID = 13  # Suzuka
TRACK_LENGTH = 5807
BASE_LAP_S = 92.5

# Circuits this generator can be pointed at, so a replay can be matched to the
# circuit a piece of real footage was driven on. The lap times are representative
# green-flag race laps, not records: the generator wants self-consistent timing,
# not a physics model. Track IDs follow the f1-packets 2026 TRACKS table, which
# is what the application reads back out of the session packet.
CIRCUITS = {
    "melbourne": (0, 5278, 80.5),
    "catalunya": (4, 4675, 77.0),
    "monaco": (5, 3337, 73.0),
    "silverstone": (7, 5891, 89.0),
    "hungaroring": (9, 4381, 78.0),
    "spa": (10, 7004, 105.0),
    "monza": (11, 5793, 82.0),
    "suzuka": (13, 5807, 92.5),
    "zandvoort": (26, 4259, 72.0),
    "imola": (27, 4909, 78.5),
    "jeddah": (29, 6174, 91.0),
    "las-vegas": (31, 6201, 95.0),
}


def select_circuit(name: str) -> None:
    """Point the generator at one of CIRCUITS.

    The packet builders read these as module globals, so this rebinds them once
    before the send loop starts rather than threading three more parameters
    through every builder.
    """
    global TRACK_ID, TRACK_LENGTH, BASE_LAP_S
    TRACK_ID, TRACK_LENGTH, BASE_LAP_S = CIRCUITS[name]


def select_driver(surname: str) -> None:
    """Run the session as a named driver on the grid.

    A demo recorded alongside real footage has to agree with it about who is in
    the car; the application resolves the name from the participants packet, so
    the only thing that has to change is which index is not AI-controlled.
    """
    global PLAYER_INDEX
    wanted = surname.strip().upper().encode()
    for index, spec in enumerate(GRID):
        if spec[3] == wanted:
            PLAYER_INDEX = index
            return
    known = ", ".join(spec[3].decode().title() for spec in GRID)
    raise SystemExit(f"unknown driver {surname!r}; the grid is: {known}")

# A fresh identity per run, as the game issues per session. A fixed UID made
# successive replays append to one stored session, so the review tab showed laps
# and strategy snapshots from different runs interleaved.
SESSION_UID = (int(time.time() * 1000) & 0xFFFFFFFFFFFF) | 0xF1_0000_0000_0000


def header(packet_id: int, session_time: float, frame: int) -> PacketHeader:
    value = PacketHeader()
    value.packet_format = 2026
    value.game_year = 25
    value.game_major_version = 1
    value.game_minor_version = 0
    value.packet_version = 1
    value.packet_id = packet_id
    value.session_uid = SESSION_UID
    value.session_time = float(session_time)
    value.frame_identifier = frame
    value.overall_frame_identifier = frame
    value.player_car_index = PLAYER_INDEX
    value.secondary_player_car_index = 255
    return value


class Car:
    """One car's simulated race state."""

    def __init__(self, index: int, spec: tuple, rng: random.Random | None = None) -> None:
        driver_id, team_id, number, name, pace = spec
        self.index = index
        self.driver_id = driver_id
        self.team_id = team_id
        self.number = number
        self.name = name
        self.pace = pace
        self.grid = index + 1
        self.position = index + 1
        self.lap = 1
        self.distance = 0.0
        self.total_distance = 0.0
        self.race_time = 0.0
        self.last_lap_ms = 0
        self.best_lap_ms = 0
        self.lap_times: list[int] = []
        self.compound = "MEDIUM" if index % 3 else "SOFT"
        self.tyre_age = 0
        self.wear = [0.0, 0.0, 0.0, 0.0]
        self.stops = 0
        self.stop_lap = (rng or random).randint(12, 20)
        self.in_pit_for = 0.0
        self.pit_stationary_ms = 0
        self.fuel = 100.0
        self.ers = 4_000_000.0
        self.damage = 0
        self.retired = False
        self.speed = 0
        self.sectors: list[tuple[int, int, int]] = []
        self.position_history: list[int] = []
        # (last lap on the tyre, compound) for every finished stint.
        self.stints: list[tuple[int, str]] = []
        self.wetness = 0.0
        self.pitting = False

    @property
    def lap_time_s(self) -> float:
        dry = (
            BASE_LAP_S
            + self.pace
            + PACE_OFFSET[self.compound]
            + self.tyre_age * 0.045
            + self.damage * 0.02
        )
        if self.compound in {"INTER", "WET"} or self.wetness > 0:
            return dry * (1.0 + rain_model.lap_penalty_fraction(self.compound, self.wetness))
        return dry

    def stop_after_this_lap(self, spell: RainSpell | None, raining: bool) -> str | None:
        """The tyre this car stops for at the end of the lap it is on, if it does."""
        if (
            spell is not None and spell.forecast_chance > 0
            and self.lap + 1 == spell.from_lap + INTER_STOP_DELAY_LAPS
            and self.compound not in {"INTER", "WET"}
        ):
            return "INTER"
        if self.lap + 1 == self.stop_lap and self.stops == 0 and not raining:
            return "HARD" if self.compound != "HARD" else "MEDIUM"
        return None

    def change_tyres(self, compound: str) -> None:
        self.stints.append((self.lap - 1, self.compound))
        self.stops += 1
        self.compound = compound
        self.tyre_age = 0
        self.wear = [0.0] * 4
        self.in_pit_for = 21.0
        self.pit_stationary_ms = 2400
        self.race_time += 20.0


def build_participants(cars: list[Car], session_time: float, frame: int) -> bytes:
    packet = PacketParticipantsData()
    packet.header = header(4, session_time, frame)
    packet.num_active_cars = len(cars)
    for car in cars:
        entry = packet.participants[car.index]
        entry.ai_controlled = 0 if car.index == PLAYER_INDEX else 1
        entry.driver_id = car.driver_id
        entry.team_id = car.team_id
        entry.race_number = car.number
        entry.name = car.name
        entry.your_telemetry = 1
        entry.show_online_names = 1
        entry.platform = 3
    return bytes(packet)


# The forecast the demo has always shown: rain likely in half an hour.
DEFAULT_FORECAST = ((5, 1, 10), (15, 1, 25), (30, 3, 55))


def forecast_for(spell: RainSpell | None, lap: int) -> tuple[int, tuple[tuple[int, int, int], ...]]:
    """The sky now and the forecast samples (minutes ahead, sky, chance) on the player's lap."""
    if spell is None:
        return 1, DEFAULT_FORECAST
    wet_code = WEATHER_CODES.get(spell.label, 3)
    if spell.forecast_chance <= 0:
        return 1, tuple((minutes, 1, 0) for minutes in (0, 5, 10, 15, 30))
    if lap >= spell.from_lap:
        # Raining to the flag, and the forecast says so.
        return wet_code, tuple((minutes, wet_code, max(80, spell.forecast_chance)) for minutes in (0, 5, 10, 15, 30))
    minutes_to_rain = (spell.from_lap - lap) * BASE_LAP_S / 60.0
    return 2, tuple(
        (minutes, wet_code if minutes >= minutes_to_rain else 2,
         spell.forecast_chance if minutes >= minutes_to_rain else 10)
        for minutes in (0, 5, 10, 15, 30)
    )


def build_session(session_time: float, frame: int, total_laps: int, lap: int,
                  spell: RainSpell | None = None) -> bytes:
    packet = PacketSessionData()
    packet.header = header(1, session_time, frame)
    sky, samples = forecast_for(spell, lap)
    packet.weather = sky
    packet.track_temperature = TRACK_TEMP_C
    packet.air_temperature = 26
    packet.total_laps = total_laps
    packet.track_length = TRACK_LENGTH
    packet.session_type = 15  # Race
    packet.track_id = TRACK_ID
    packet.formula = 0
    packet.session_time_left = max(0, int((total_laps - lap) * BASE_LAP_S))
    packet.session_duration = int(total_laps * BASE_LAP_S)
    packet.pit_speed_limit = 80
    packet.num_marshal_zones = 6
    for zone in range(6):
        packet.marshal_zones[zone].zone_start = zone / 6.0
        # A yellow appears in one sector during the middle of the race.
        packet.marshal_zones[zone].zone_flag = 3 if (zone == 3 and 14 <= lap <= 17) else 1
    packet.safety_car_status = 0
    packet.num_weather_forecast_samples = len(samples)
    for offset, (minutes, weather, rain) in enumerate(samples):
        sample = packet.weather_forecast_samples[offset]
        sample.session_type = 15
        sample.time_offset = minutes
        sample.weather = weather
        sample.track_temperature = TRACK_TEMP_C - offset
        sample.air_temperature = 26 - offset
        sample.rain_percentage = rain
    packet.forecast_accuracy = 0
    packet.ai_difficulty = 92
    packet.num_drs_zones = 2
    packet.drs_zones[0].zone_start = 0.88
    packet.drs_zones[0].zone_end = 0.98
    packet.drs_zones[1].zone_start = 0.42
    packet.drs_zones[1].zone_end = 0.50
    packet.num_active_aero_zones_full = 1
    packet.active_aero_zones_full[0].zone_start = 0.88
    packet.active_aero_zones_full[0].zone_end = 0.98
    packet.sector2_lap_distance_start = TRACK_LENGTH * 0.34
    packet.sector3_lap_distance_start = TRACK_LENGTH * 0.71
    packet.num_sessions_in_weekend = 3
    for slot, value in enumerate((1, 5, 15)):
        packet.weekend_structure[slot] = value
    packet.session_length = 7
    packet.game_mode = 3
    packet.rule_set = 0
    packet.time_of_day = 14 * 60
    packet.parc_ferme_rules = 1
    packet.car_damage = 2
    packet.car_damage_rate = 1
    return bytes(packet)


def build_lap_data(cars: list[Car], session_time: float, frame: int) -> bytes:
    packet = PacketLapData()
    packet.header = header(2, session_time, frame)
    # Gaps come from track position, not elapsed clock: every car advances the
    # same wall time each tick, so only distance covered separates them.
    running = [c for c in cars if not c.retired]
    leader_distance = max((c.total_distance for c in running), default=0.0)

    def behind_leader_s(car: Car) -> float:
        pace = TRACK_LENGTH / car.lap_time_s  # metres per second
        return max(0.0, (leader_distance - car.total_distance) / pace)

    order = sorted(running, key=lambda c: -c.total_distance)
    for slot, car in enumerate(order, start=1):
        car.position = slot
    for car in cars:
        entry = packet.lap_data[car.index]
        entry.last_lap_time_in_ms = car.last_lap_ms
        entry.current_lap_time_in_ms = int((car.distance / TRACK_LENGTH) * car.lap_time_s * 1000)
        entry.lap_distance = car.distance
        entry.total_distance = car.total_distance
        entry.car_position = 0 if car.retired else car.position
        entry.current_lap_num = car.lap
        entry.num_pit_stops = car.stops
        entry.sector = 0 if car.distance < TRACK_LENGTH * 0.34 else (1 if car.distance < TRACK_LENGTH * 0.71 else 2)
        entry.grid_position = car.grid
        entry.driver_status = 1 if not car.in_pit_for else 2
        # 1 in the lane on the way in, 2 in the pit area.
        entry.pit_status = 2 if car.in_pit_for > 0 else 1 if car.pitting else 0
        entry.result_status = 7 if car.retired else 2
        entry.pit_lane_timer_active = 1 if car.in_pit_for > 0 else 0
        entry.pit_lane_time_in_lane_in_ms = int(car.in_pit_for * 1000)
        entry.pit_stop_timer_in_ms = car.pit_stationary_ms
        entry.speed_trap_fastest_speed = 316.0 - car.pace * 4
        delta = 0.0 if car.retired else behind_leader_s(car)
        entry.delta_to_race_leader_minutes_part = int(delta // 60)
        entry.delta_to_race_leader_ms_part = int((delta % 60) * 1000)
        if not car.retired and car.position > 1:
            ahead = next((c for c in order if c.position == car.position - 1), None)
            front = max(0.0, delta - behind_leader_s(ahead)) if ahead else 0.0
            entry.delta_to_car_in_front_minutes_part = int(front // 60)
            entry.delta_to_car_in_front_ms_part = int((front % 60) * 1000)
    return bytes(packet)


def build_car_status(cars: list[Car], session_time: float, frame: int) -> bytes:
    packet = PacketCarStatusData()
    packet.header = header(7, session_time, frame)
    for car in cars:
        entry = packet.car_status_data[car.index]
        entry.fuel_mix = 1
        entry.front_brake_bias = 56
        entry.fuel_in_tank = car.fuel
        entry.fuel_capacity = 110.0
        entry.fuel_remaining_laps = round(0.9 - car.tyre_age * 0.01, 2)
        entry.max_rpm = 15000
        entry.drs_allowed = 1 if car.distance > TRACK_LENGTH * 0.88 else 0
        entry.actual_tyre_compound = COMPOUNDS[car.compound]
        entry.visual_tyre_compound = COMPOUNDS[car.compound]
        entry.tyres_age_laps = car.tyre_age
        entry.vehicle_fia_flags = 0
        entry.ers_store_energy = car.ers
        entry.ers_deploy_mode = 2
        entry.ers_harvested_this_lap_mguk = 1_200_000.0
        entry.ers_deployed_this_lap = 3_100_000.0
    return bytes(packet)


def build_car_damage(cars: list[Car], session_time: float, frame: int) -> bytes:
    packet = PacketCarDamageData()
    packet.header = header(10, session_time, frame)
    for car in cars:
        entry = packet.car_damage_data[car.index]
        # EA order is RL, RR, FL, FR.
        for wheel, value in enumerate((car.wear[2], car.wear[3], car.wear[0], car.wear[1])):
            entry.tyres_wear[wheel] = min(99.0, value)
        entry.front_left_wing_damage = car.damage
        entry.front_right_wing_damage = max(0, car.damage - 4)
        entry.engine_ice_wear = min(99, 20 + car.lap // 3)
        entry.engine_mguk_wear = min(99, 14 + car.lap // 4)
        if car.index == 3 and car.lap > 10:
            entry.ers_fault = 1
    return bytes(packet)


def build_telemetry(cars: list[Car], session_time: float, frame: int) -> bytes:
    packet = PacketCarTelemetryData()
    packet.header = header(6, session_time, frame)
    for car in cars:
        entry = packet.car_telemetry_data[car.index]
        entry.speed = car.speed
        entry.throttle = 0.9 if car.speed > 200 else 0.4
        entry.brake = 0.0 if car.speed > 200 else 0.3
        entry.gear = max(1, min(8, car.speed // 40))
        entry.engine_rpm = 11000 + car.speed * 8
        entry.drs = 1 if car.distance > TRACK_LENGTH * 0.9 else 0
        core = 96 + int(car.wear[0] * 0.12)
        for wheel, value in enumerate((core - 2, core - 1, core + 1, core)):
            entry.tyres_inner_temperature[wheel] = min(140, value)
            entry.tyres_surface_temperature[wheel] = min(140, value + 6)
        for wheel in range(4):
            entry.brakes_temperature[wheel] = 420
            entry.tyres_pressure[wheel] = 23.1
    packet.suggested_gear = 0
    return bytes(packet)


def build_telemetry2(cars: list[Car], session_time: float, frame: int) -> bytes:
    packet = PacketCarTelemetry2Data()
    packet.header = header(16, session_time, frame)
    for car in cars:
        entry = packet.car_telemetry2_data[car.index]
        fraction = car.distance / TRACK_LENGTH
        in_zone = fraction > 0.88 or 0.42 < fraction < 0.50
        entry.active_aero_mode = 1 if in_zone else 0
        entry.active_aero_available = 1
        entry.active_aero_activation_distance = 0 if in_zone else 250
        entry.overtake_available = 1 if car.ers > 350_000 else 0
        entry.overtake_active = 1 if in_zone and car.ers > 350_000 else 0
        entry.overtake_activation_distance = 0 if in_zone else 250
        setattr(entry, "2026_regulations", 1)
    return bytes(packet)


def build_lap_positions(cars: list[Car], session_time: float, frame: int) -> bytes:
    packet = PacketLapPositionsData()
    packet.header = header(15, session_time, frame)
    count = min(50, max((len(car.position_history) for car in cars), default=0))
    packet.num_laps = count
    packet.lap_start = 1
    for lap_offset in range(count):
        for car in cars:
            if lap_offset < len(car.position_history):
                packet.position_for_vehicle_idx[lap_offset * 24 + car.index] = (
                    car.position_history[lap_offset]
                )
    return bytes(packet)


def build_final_classification(
    cars: list[Car], session_time: float, frame: int, total_laps: int
) -> bytes:
    packet = PacketFinalClassificationData()
    packet.header = header(8, session_time, frame)
    packet.num_cars = len(cars)
    points = {1: 25, 2: 18, 3: 15, 4: 12, 5: 10, 6: 8, 7: 6, 8: 4, 9: 2, 10: 1}
    for car in cars:
        entry = packet.classification_data[car.index]
        entry.position = car.position if not car.retired else len(cars)
        entry.num_laps = min(total_laps, max(0, car.lap - 1))
        entry.grid_position = car.grid
        entry.points = points.get(entry.position, 0)
        entry.num_pit_stops = car.stops
        entry.result_status = 3 if not car.retired else 7
        entry.best_lap_time_in_ms = car.best_lap_ms
        entry.total_race_time = car.race_time
        entry.num_tyre_stints = max(1, car.stops + 1)
        entry.tyre_stints_actual[0] = COMPOUNDS[car.compound]
        entry.tyre_stints_visual[0] = COMPOUNDS[car.compound]
        entry.tyre_stints_end_laps[0] = 255
    return bytes(packet)


def build_motion(cars: list[Car], session_time: float, frame: int) -> bytes:
    packet = PacketMotionData()
    packet.header = header(0, session_time, frame)
    for car in cars:
        angle = (car.distance / TRACK_LENGTH) * 2 * math.pi
        entry = packet.car_motion_data[car.index]
        entry.world_position_x = math.cos(angle) * 900.0
        entry.world_position_y = 12.0
        entry.world_position_z = math.sin(angle) * 900.0
        entry.world_forward_dir_x = int(-math.sin(angle) * 32767)
        entry.world_forward_dir_z = int(math.cos(angle) * 32767)
        entry.g_force_lateral = int(1.8 * 100)
        entry.g_force_longitudinal = int(0.4 * 100)
        entry.yaw = angle
    return bytes(packet)


def build_history(car: Car, session_time: float, frame: int) -> bytes:
    packet = PacketSessionHistoryData()
    packet.header = header(11, session_time, frame)
    packet.car_idx = car.index
    packet.num_laps = max(1, len(car.lap_times))
    packet.num_tyre_stints = len(car.stints) + 1
    best = min(car.lap_times) if car.lap_times else 0
    packet.best_lap_time_lap_num = (
        car.lap_times.index(best) + 1 if car.lap_times else 0
    )
    for lap_index, lap_ms in enumerate(car.lap_times[:100]):
        entry = packet.lap_history_data[lap_index]
        entry.lap_time_in_ms = lap_ms
        s1, s2, s3 = car.sectors[lap_index]
        entry.sector1_time_minutes_part = s1 // 60000
        entry.sector1_time_ms_part = s1 % 60000
        entry.sector2_time_minutes_part = s2 // 60000
        entry.sector2_time_ms_part = s2 % 60000
        entry.sector3_time_minutes_part = s3 // 60000
        entry.sector3_time_ms_part = s3 % 60000
        entry.lap_valid_bit_flags = 0x0F
    for slot, (end_lap, compound) in enumerate([*car.stints, (255, car.compound)][:8]):
        stint = packet.tyre_stints_history_data[slot]
        stint.end_lap = end_lap
        stint.tyre_actual_compound = COMPOUNDS[compound]
        stint.tyre_visual_compound = COMPOUNDS[compound]
    return bytes(packet)


def build_event(code: str, session_time: float, frame: int, **fields) -> bytes:
    packet = PacketEventData()
    packet.header = header(3, session_time, frame)
    for position, character in enumerate(code):
        packet.event_string_code[position] = ord(character)
    if code == "RTMT":
        packet.event_details.retirement.vehicle_idx = fields["vehicle_idx"]
    elif code == "SPTP":
        trap = packet.event_details.speed_trap
        trap.vehicle_idx = fields["vehicle_idx"]
        trap.speed = fields["speed"]
        trap.is_overall_fastest_in_session = 1
    elif code == "FTLP":
        packet.event_details.fastest_lap.vehicle_idx = fields["vehicle_idx"]
        packet.event_details.fastest_lap.lap_time = fields["lap_time"]
    elif code == "OVTK":
        packet.event_details.overtake.overtaking_vehicle_idx = fields["overtaking"]
        packet.event_details.overtake.being_overtaken_vehicle_idx = fields["overtaken"]
    return bytes(packet)


def race_packets(
    total_laps: int,
    seed: int,
    *,
    spell: RainSpell | None = None,
    tick: float = 0.10,
    session_uid: int | None = None,
    on_tick: Callable[[Car, int], None] | None = None,
) -> Iterator[tuple[float, bytes]]:
    """The whole race as (session time, datagram), ending with the final classification.

    ``tick`` is simulated seconds per step. Everything random comes from
    ``seed``, so the same arguments give the same bytes.
    """
    global SESSION_UID
    if session_uid is not None:
        SESSION_UID = int(session_uid)
    rng = random.Random(seed)
    cars = [Car(index, spec, rng) for index, spec in enumerate(GRID)]
    frame = 0
    session_time = 0.0
    history_cursor = 0
    player = cars[PLAYER_INDEX]
    wetness = 0.0
    # Steps between the slower packets, as the 10 Hz demo always sent them.
    every = {name: max(1, round(base / tick)) for name, base in
             (("status", 0.5), ("session", 1.0), ("history", 0.3), ("trap", 9.7))}

    yield session_time, build_participants(cars, session_time, frame)
    yield session_time, build_session(session_time, frame, total_laps, 1, spell)

    while player.lap <= total_laps:
        frame += 1
        session_time += tick
        raining = spell is not None and player.lap >= spell.from_lap
        if spell is not None:
            target = rain_model.equilibrium_wetness(spell.label) if raining else 0.0
            wetness = rain_model.step_wetness(
                wetness, target, laps=tick / BASE_LAP_S, track_temp_c=TRACK_TEMP_C,
                active_cars=sum(1 for car in cars if not car.retired),
            )
        events: list[bytes] = []

        for car in cars:
            car.wetness = wetness
            if car.retired:
                continue
            if car.in_pit_for > 0:
                car.in_pit_for = max(0.0, car.in_pit_for - tick)
                car.pit_stationary_ms = max(0, car.pit_stationary_ms - int(tick * 1000))
                car.race_time += tick
                car.speed = 60
                continue

            step = TRACK_LENGTH / car.lap_time_s * tick
            car.distance += step
            car.total_distance += step
            car.race_time += tick
            car.speed = int(300 - 140 * abs(math.sin(car.distance / TRACK_LENGTH * 6 * math.pi)))
            # Deploy on the straights, harvest under braking, so the store
            # oscillates across a lap the way a real one does, between about
            # half and full. At 620 kJ/s out and 240 kJ/s in, the 4 MJ store
            # emptied on every main straight.
            lap_fraction = car.distance / TRACK_LENGTH
            deploying = lap_fraction > 0.82 or 0.40 < lap_fraction < 0.52
            car.ers = max(
                0.0,
                min(
                    4_000_000.0,
                    car.ers + (-120_000 if deploying else 55_000) * tick,
                ),
            )
            car.fuel = max(0.0, car.fuel - 0.019 * tick)

            stopping = car.stop_after_this_lap(spell, raining)
            car.pitting = stopping is not None and car.distance >= PIT_ENTRY_FRACTION * TRACK_LENGTH
            if car.distance >= TRACK_LENGTH:
                car.distance -= TRACK_LENGTH
                lap_ms = int(car.lap_time_s * 1000)
                car.last_lap_ms = lap_ms
                car.lap_times.append(lap_ms)
                car.sectors.append(
                    (int(lap_ms * 0.34), int(lap_ms * 0.37), lap_ms - int(lap_ms * 0.34) - int(lap_ms * 0.37))
                )
                car.best_lap_ms = min(car.lap_times)
                car.position_history.append(car.position)
                car.lap += 1
                car.tyre_age += 1
                rate = WEAR_PER_LAP[car.compound]
                car.wear = [
                    min(99.0, value + rate * factor)
                    for value, factor in zip(car.wear, (0.95, 0.92, 1.08, 1.05))
                ]
                if stopping is not None:
                    car.pitting = False
                    car.change_tyres(stopping)
                # The scripted retirement and damage belong to AI cars. When
                # --driver puts the player in one of those seats the script
                # moves to the neighbouring car: a retired player never
                # completes the race, so the send loop would spin for ever.
                if car.index == (17 if PLAYER_INDEX == 16 else 16) and car.lap == 19:
                    car.retired = True
                    events.append(build_event("RTMT", session_time, frame, vehicle_idx=car.index))
                if car.index == (9 if PLAYER_INDEX == 8 else 8) and car.lap == 7:
                    car.damage = 34

        yield from ((session_time, event) for event in events)
        yield session_time, build_lap_data(cars, session_time, frame)
        yield session_time, build_motion(cars, session_time, frame)
        yield session_time, build_telemetry(cars, session_time, frame)
        yield session_time, build_telemetry2(cars, session_time, frame)

        if frame % every["status"] == 0:
            yield session_time, build_car_status(cars, session_time, frame)
            yield session_time, build_car_damage(cars, session_time, frame)
        if frame % every["session"] == 0:
            yield session_time, build_session(session_time, frame, total_laps, player.lap, spell)
            yield session_time, build_participants(cars, session_time, frame)
            yield session_time, build_lap_positions(cars, session_time, frame)
        if frame % every["history"] == 0:
            car = cars[history_cursor % len(cars)]
            history_cursor += 1
            yield session_time, build_history(car, session_time, frame)
        if frame % every["trap"] == 0:
            yield session_time, build_event("SPTP", session_time, frame, vehicle_idx=0, speed=328.6)
        if on_tick is not None:
            on_tick(player, total_laps)

    frame += 1
    yield session_time, build_lap_positions(cars, session_time, frame)
    yield session_time, build_final_classification(cars, session_time, frame, total_laps)


def run(host: str, port: int, total_laps: int, speed: float, seed: int,
        *, summary_output: Path | None = None, spell: RainSpell | None = None) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    target = (host, port)

    print(f"Sending synthetic F1 26 telemetry to {host}:{port}")
    print(f"  {len(GRID)} cars, {total_laps} laps, {speed:.0f}x speed")
    print(f"  You are {GRID[PLAYER_INDEX][3].decode()} (car {PLAYER_INDEX}), starting P{PLAYER_INDEX + 1}")
    if spell is not None:
        print(f"  {spell.label} from lap {spell.from_lap}, forecast {spell.forecast_chance}%")

    def progress(player: Car, laps: int) -> None:
        print(
            f"\r  lap {player.lap:>2}/{laps}  P{player.position:<2} "
            f"{player.compound:<6} age {player.tyre_age:>2}  "
            f"wear {max(player.wear):>4.1f}%  ",
            end="",
            flush=True,
        )

    final_bytes = b""
    clock = 0.0
    for session_time, data in race_packets(total_laps, seed, spell=spell, on_tick=progress):
        if session_time > clock:
            time.sleep((session_time - clock) / speed)
            clock = session_time
        sock.sendto(data, target)
        final_bytes = data
    # UDP has no delivery acknowledgement. At accelerated replay speeds the
    # receiver's OS buffer can still contain the last burst of race telemetry;
    # a single terminal datagram can disappear even though the race advanced.
    # Give that burst time to drain by repeating the exact same result at a
    # low rate. The product must still parse and persist the real packet, and
    # duplicate-final handling must keep the result/event idempotent.
    final_datagrams = 1
    for _ in range(20):
        time.sleep(0.25)
        sock.sendto(final_bytes, target)
        final_datagrams += 1
    if summary_output is not None:
        # Record exactly the packet just sent, independently of what the app
        # later claims to have received. This is emitter evidence, not a server
        # API write or injected success state.
        packet = PacketFinalClassificationData.unpack(final_bytes)
        result = packet.classification_data[packet.header.player_car_index]
        summary_output.write_text(json.dumps({
            "session_uid": int(packet.header.session_uid),
            "packet_format": int(packet.header.packet_format),
            "player_car_index": int(packet.header.player_car_index),
            "final_classification_datagrams": final_datagrams,
            "final_classification": {
                "position": int(result.position), "laps": int(result.num_laps),
                "grid_position": int(result.grid_position), "points": int(result.points),
                "pit_stops": int(result.num_pit_stops), "best_lap_ms": int(result.best_lap_time_in_ms),
                "total_race_time_s": float(result.total_race_time), "penalties_s": int(result.penalties_time),
            },
        }, indent=2) + "\n", encoding="utf-8")
    print("\nRace complete; final classification sent for the race report.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=20777)
    parser.add_argument("--laps", type=int, default=30)
    parser.add_argument("--speed", type=float, default=25.0, help="time compression")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--summary-output", type=Path,
                        help="write the actual emitted final-classification packet as JSON")
    parser.add_argument(
        "--circuit", default="suzuka", choices=sorted(CIRCUITS),
        help="circuit to run the synthetic race on",
    )
    parser.add_argument(
        "--driver", default=None,
        help="surname of the grid driver to run as (default: Russell)",
    )
    parser.add_argument("--rain-from-lap", type=int, default=None,
                        help="the player's lap the rain starts on (default: a dry race)")
    parser.add_argument("--rain", default="Light rain", choices=["Light rain", "Heavy rain", "Storm"])
    parser.add_argument("--forecast-chance", type=int, default=50,
                        help="the rain chance the forecast gives before it arrives")
    arguments = parser.parse_args()
    select_circuit(arguments.circuit)
    if arguments.driver:
        select_driver(arguments.driver)
    spell = (
        RainSpell(arguments.rain_from_lap, arguments.rain, arguments.forecast_chance)
        if arguments.rain_from_lap else None
    )
    run(arguments.host, arguments.port, arguments.laps, arguments.speed, arguments.seed,
        summary_output=arguments.summary_output, spell=spell)


if __name__ == "__main__":
    main()
