import pytest
from f1.packets import (
    PacketCarStatusData,
    PacketHeader,
    PacketLapData,
    PacketSessionData,
)

from pitwall.state import StateStore
from pitwall.udp import F1DatagramProtocol, SESSION_TYPES, TRACKS


def header(packet_id: int) -> PacketHeader:
    value = PacketHeader()
    value.packet_format = 2026
    value.game_year = 25
    value.packet_version = 1
    value.packet_id = packet_id
    value.session_uid = 123
    value.player_car_index = 0
    return value


@pytest.mark.asyncio
async def test_session_and_lap_packets_update_state() -> None:
    store = StateStore()
    protocol = F1DatagramProtocol(store)

    session = PacketSessionData()
    session.header = header(1)
    session.session_type = 15
    session.track_id = 13
    session.total_laps = 53
    session.weather = 0
    session.num_weather_forecast_samples = 1
    session.weather_forecast_samples[0].time_offset = 15
    session.weather_forecast_samples[0].rain_percentage = 20
    await protocol._handle(session)

    lap = PacketLapData()
    lap.header = header(2)
    lap.lap_data[0].current_lap_num = 8
    lap.lap_data[0].car_position = 4
    lap.lap_data[0].lap_distance = 1200
    await protocol._handle(lap)

    state = await store.snapshot()
    assert state["track_name"] == "Suzuka"
    assert state["current_lap"] == 8
    assert state["player_position"] == 4


@pytest.mark.asyncio
async def test_car_status_updates_tyre_and_fuel() -> None:
    store = StateStore()
    protocol = F1DatagramProtocol(store)
    await store.update(total_laps=20, current_lap=10)

    status = PacketCarStatusData()
    status.header = header(7)
    car = status.car_status_data[0]
    car.fuel_in_tank = 15.5
    car.fuel_remaining_laps = 11.0
    car.visual_tyre_compound = 17
    car.tyres_age_laps = 5
    car.ers_store_energy = 2_000_000
    await protocol._handle(status)

    state = await store.snapshot()
    assert state["tyre"]["compound"] == "MEDIUM"
    assert state["fuel_laps_delta"] == 11.0
    assert state["ers_pct"] == 50.0


@pytest.mark.asyncio
async def test_current_race_enum_is_used_without_heuristic() -> None:
    store = StateStore()
    protocol = F1DatagramProtocol(store)

    session = PacketSessionData()
    session.header = header(1)
    session.session_type = 15  # Current F1 25/2026 enum: Race.
    session.track_id = 10
    session.total_laps = 22
    session.session_time_left = 6_879
    session.session_duration = 7_200
    session.session_length = 3
    session.weather = 0
    await protocol._handle(session)

    state = await store.snapshot()
    assert state["raw_session_type"] == "Race"
    assert state["session_type"] == "Race"
    assert state["mode_profile"] == "race"
    assert state["total_laps"] == 22
    assert state["session_detection_source"] == "udp"


def test_current_track_and_session_enums_are_not_shifted() -> None:
    assert TRACKS[10] == "Spa"
    assert TRACKS[11] == "Monza"
    assert TRACKS[12] == "Singapore"
    assert TRACKS[13] == "Suzuka"
    assert SESSION_TYPES[15] == "Race"
    assert SESSION_TYPES[18] == "Time Trial"


@pytest.mark.asyncio
async def test_session_packet_preserves_safety_car_ending_phase() -> None:
    store = StateStore()
    protocol = F1DatagramProtocol(store)
    await store.update(race_control_phase="safety_car_ending", safety_car="full")

    session = PacketSessionData()
    session.header = header(1)
    session.session_type = 15
    session.track_id = 10
    session.total_laps = 22
    session.safety_car_status = 1
    await protocol._handle(session)

    state = await store.snapshot_analysis()
    assert state["race_control_phase"] == "safety_car_ending"

@pytest.mark.asyncio
async def test_flags_and_unserved_penalties_are_exposed() -> None:
    store = StateStore()
    protocol = F1DatagramProtocol(store)

    lap = PacketLapData()
    lap.header = header(2)
    player = lap.lap_data[0]
    player.current_lap_num = 4
    player.car_position = 10
    player.safety_car_delta = -0.35
    player.num_unserved_drive_through_pens = 1
    player.num_unserved_stop_go_pens = 0
    player.pit_stop_should_serve_pen = 1
    await protocol._handle(lap)

    status = PacketCarStatusData()
    status.header = header(7)
    status.car_status_data[0].vehicle_fia_flags = 2
    await protocol._handle(status)

    state = await store.snapshot_analysis()
    assert state["fia_flag"] == "blue"
    assert state["unserved_drive_through_penalties"] == 1
    assert state["pit_stop_should_serve_penalty"] is True
    assert state["safety_car_delta_s"] == pytest.approx(-0.35, abs=0.01)
