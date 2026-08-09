"""Field-wide capture: every F1 26 packet carries all 24 cars, not just yours.

Before this, the damage, status, telemetry and motion handlers read only
``player_car_index``. A driver asking "does the car ahead have damage", "how
old are his tyres", "what's his battery" or "has Max boxed yet" could not be
answered even though the data had already been received and discarded.
"""

from __future__ import annotations

import pytest
from f1.packets import (
    PacketCarDamageData,
    PacketCarStatusData,
    PacketCarTelemetryData,
    PacketEventData,
    PacketHeader,
    PacketLapData,
    PacketParticipantsData,
    PacketSessionData,
)

from pitwall.identity import display_name, match_drivers
from pitwall.state import StateStore
from pitwall.tools import TelemetryTools
from pitwall.udp import F1DatagramProtocol


def header(packet_id: int, player_index: int = 0) -> PacketHeader:
    value = PacketHeader()
    value.packet_format = 2026
    value.game_year = 25
    value.packet_version = 1
    value.packet_id = packet_id
    value.session_uid = 4242
    value.player_car_index = player_index
    return value


async def _field_of_three(store: StateStore) -> F1DatagramProtocol:
    """Player in car 0, Verstappen in car 1, Hamilton in car 2."""
    protocol = F1DatagramProtocol(store)
    participants = PacketParticipantsData()
    participants.header = header(4)
    participants.num_active_cars = 3
    for index, (name, driver_id, team_id, number) in enumerate(
        [(b"PLAYER", 255, 0, 63), (b"VERSTAPPEN", 9, 2, 1), (b"HAMILTON", 7, 1, 44)]
    ):
        participants.participants[index].name = name
        participants.participants[index].driver_id = driver_id
        participants.participants[index].team_id = team_id
        participants.participants[index].race_number = number
        participants.participants[index].your_telemetry = 1
        participants.participants[index].ai_controlled = 1
    await protocol._handle(participants)
    return protocol


@pytest.mark.asyncio
async def test_rival_damage_and_tyre_wear_are_captured():
    """The exact production failure: asking about a rival returned your own car."""
    store = StateStore()
    protocol = await _field_of_three(store)

    damage = PacketCarDamageData()
    damage.header = header(10)
    # Player: clean. Verstappen: broken front wing and worn tyres.
    damage.car_damage_data[0].front_left_wing_damage = 0
    damage.car_damage_data[1].front_left_wing_damage = 32
    damage.car_damage_data[1].rear_wing_damage = 5
    damage.car_damage_data[1].engine_damage = 11
    # EA wheel order is RL, RR, FL, FR.
    for wheel, value in enumerate((61.0, 58.0, 44.0, 43.0)):
        damage.car_damage_data[1].tyres_wear[wheel] = value
    damage.car_damage_data[1].engine_ice_wear = 74
    await protocol._handle(damage)

    state = await store.snapshot()
    verstappen = state["drivers"][1]

    assert verstappen["damage"]["front_left_wing"] == 32
    assert verstappen["damage"]["engine"] == 11
    # Normalised to Your Pit Box's FL, FR, RL, RR order.
    assert verstappen["tyre_wear"] == [44.0, 43.0, 61.0, 58.0]
    assert verstappen["component_wear"]["ice"] == 74.0
    # The player's own damage is still reported separately and is unaffected.
    assert state["damage"]["front_left_wing"] == 0
    assert state["drivers"][0]["damage"]["front_left_wing"] == 0


@pytest.mark.asyncio
async def test_rival_energy_and_fuel_are_captured():
    """get_attack_plan claimed opponent ERS was not exposed. It is."""
    store = StateStore()
    protocol = await _field_of_three(store)

    status = PacketCarStatusData()
    status.header = header(7)
    status.car_status_data[1].ers_store_energy = 2_000_000.0  # half of 4 MJ
    status.car_status_data[1].fuel_in_tank = 42.5
    status.car_status_data[1].fuel_remaining_laps = 1.4
    status.car_status_data[1].visual_tyre_compound = 18  # HARD
    status.car_status_data[1].tyres_age_laps = 21
    status.car_status_data[1].drs_allowed = 1
    await protocol._handle(status)

    verstappen = (await store.snapshot())["drivers"][1]
    assert verstappen["ers_pct"] == 50.0
    assert verstappen["fuel_kg"] == 42.5
    assert verstappen["fuel_remaining_laps"] == 1.4
    assert verstappen["tyre_compound"] == "HARD"
    assert verstappen["tyre_age"] == 21
    assert verstappen["drs_allowed"] is True


@pytest.mark.asyncio
async def test_restricted_rivals_report_unavailable_not_zero():
    """Online privacy hides rival telemetry; it must not read as "no wear"."""
    store = StateStore()
    protocol = F1DatagramProtocol(store)
    participants = PacketParticipantsData()
    participants.header = header(4)
    participants.num_active_cars = 2
    participants.participants[0].name = b"PLAYER"
    participants.participants[0].your_telemetry = 1
    participants.participants[1].name = b"RIVAL"
    participants.participants[1].your_telemetry = 0  # restricted
    await protocol._handle(participants)

    status = PacketCarStatusData()
    status.header = header(7)
    status.car_status_data[1].visual_tyre_compound = 16
    await protocol._handle(status)

    rival = (await store.snapshot())["drivers"][1]
    assert rival["restricted"] is True
    # Compound stays public; energy and fuel stay unknown rather than zero.
    assert rival["tyre_compound"] == "SOFT"
    assert rival["ers_pct"] is None
    assert rival["fuel_kg"] is None


@pytest.mark.asyncio
async def test_live_pit_stop_and_retirement_are_visible():
    """A stop in progress and a retirement change the race picture immediately."""
    store = StateStore()
    protocol = await _field_of_three(store)

    lap = PacketLapData()
    lap.header = header(2)
    lap.lap_data[0].car_position = 3
    lap.lap_data[0].current_lap_num = 12
    lap.lap_data[0].result_status = 2
    # Verstappen is stationary in the pit box.
    lap.lap_data[1].car_position = 1
    lap.lap_data[1].current_lap_num = 12
    lap.lap_data[1].result_status = 2
    lap.lap_data[1].pit_lane_timer_active = 1
    lap.lap_data[1].pit_stop_timer_in_ms = 2400
    lap.lap_data[1].pit_lane_time_in_lane_in_ms = 19800
    lap.lap_data[1].speed_trap_fastest_speed = 328.4
    # Hamilton has retired.
    lap.lap_data[2].car_position = 0
    lap.lap_data[2].result_status = 7
    await protocol._handle(lap)

    drivers = (await store.snapshot())["drivers"]
    verstappen = next(d for d in drivers if d["car_idx"] == 1)
    hamilton = next(d for d in drivers if d["car_idx"] == 2)

    assert verstappen["pit_lane_timer_active"] is True
    assert verstappen["pit_stop_timer_ms"] == 2400
    assert round(verstappen["speed_trap_kph"], 1) == 328.4
    assert hamilton["result_label"] == "retired"
    # A retired car must not keep quoting the gap it held when it stopped.
    assert hamilton["gap_to_player_s"] is None


@pytest.mark.asyncio
async def test_rival_telemetry_and_manual_override_are_captured():
    store = StateStore()
    protocol = await _field_of_three(store)

    telemetry = PacketCarTelemetryData()
    telemetry.header = header(6)
    telemetry.car_telemetry_data[1].speed = 291
    telemetry.car_telemetry_data[1].drs = 1
    for wheel, value in enumerate((95, 96, 88, 89)):  # RL, RR, FL, FR
        telemetry.car_telemetry_data[1].tyres_inner_temperature[wheel] = value
    await protocol._handle(telemetry)

    verstappen = (await store.snapshot())["drivers"][1]
    assert verstappen["speed_kph"] == 291
    assert verstappen["drs_open"] is True
    assert verstappen["tyre_inner_temps_c"] == [88.0, 89.0, 95.0, 96.0]


@pytest.mark.asyncio
async def test_marshal_zones_and_session_rules_are_captured():
    store = StateStore()
    protocol = F1DatagramProtocol(store)

    session = PacketSessionData()
    session.header = header(1)
    session.session_type = 15
    session.track_id = 13
    session.total_laps = 53
    session.track_length = 5807
    session.num_marshal_zones = 3
    session.marshal_zones[0].zone_start = 0.0
    session.marshal_zones[0].zone_flag = 1
    session.marshal_zones[1].zone_start = 0.5
    session.marshal_zones[1].zone_flag = 3  # yellow
    session.marshal_zones[2].zone_start = 0.75
    session.marshal_zones[2].zone_flag = 0
    session.num_drs_zones = 1
    session.drs_zones[0].zone_start = 0.9
    session.drs_zones[0].zone_end = 0.98
    session.parc_ferme_rules = 1
    session.equal_car_performance = 1
    session.ai_difficulty = 95
    session.pit_speed_limit = 80
    await protocol._handle(session)

    state = await store.snapshot()
    zones = state["marshal_zones"]
    assert len(zones) == 3
    assert zones[1]["flag"] == "yellow"
    # Fractions are converted to metres so they compare with lap distance.
    assert zones[1]["start_m"] == pytest.approx(2903.5, abs=0.1)
    assert state["drs_zones"][0]["start_m"] == pytest.approx(5226.3, abs=0.1)
    assert state["parc_ferme_rules"] is True
    assert state["equal_car_performance"] is True
    assert state["ai_difficulty"] == 95
    assert state["pit_speed_limit_kph"] == 80


@pytest.mark.asyncio
async def test_previously_empty_event_payloads_are_decoded():
    """A retirement recorded who retired; a speed trap recorded the speed."""
    store = StateStore()
    protocol = await _field_of_three(store)

    retirement = PacketEventData()
    retirement.header = header(3)
    retirement.event_string_code[:] = [ord(c) for c in "RTMT"]
    retirement.event_details.retirement.vehicle_idx = 2
    await protocol._handle(retirement)

    trap = PacketEventData()
    trap.header = header(3)
    trap.event_string_code[:] = [ord(c) for c in "SPTP"]
    trap.event_details.speed_trap.vehicle_idx = 1
    trap.event_details.speed_trap.speed = 331.2
    trap.event_details.speed_trap.is_overall_fastest_in_session = 1
    await protocol._handle(trap)

    events = {e["type"]: e["payload"] for e in (await store.snapshot())["events_log"]}
    assert events["RTMT"]["vehicle_idx"] == 2
    assert events["SPTP"]["speed_kph"] == pytest.approx(331.2, abs=0.1)
    assert events["SPTP"]["overall_fastest"] is True


@pytest.mark.asyncio
async def test_button_events_never_reach_the_events_log():
    """Button noise used to crowd real incidents out of the 200-entry log."""
    store = StateStore()
    protocol = F1DatagramProtocol(store)
    for _ in range(50):
        buttons = PacketEventData()
        buttons.header = header(3)
        buttons.event_string_code[:] = [ord(c) for c in "BUTN"]
        buttons.event_details.buttons.button_status = 4
        await protocol._handle(buttons)

    assert (await store.snapshot())["events_log"] == []


@pytest.mark.asyncio
async def test_race_flow_describes_the_field_not_just_the_player(stack):
    """"any race updates" previously reported only position, gap and rain."""
    store, database, strategy, setup, analysis, tools = stack
    protocol = await _field_of_three(store)
    await store.update(
        session_type="Race", mode_profile="race", total_laps=53, current_lap=28,
    )

    lap = PacketLapData()
    lap.header = header(2)
    # Player P2 from P5. Verstappen leads, one stop made. Hamilton retired.
    for index, (position, grid, stops, result) in enumerate(
        [(2, 5, 1, 2), (1, 1, 1, 2), (0, 3, 0, 7)]
    ):
        entry = lap.lap_data[index]
        entry.car_position = position
        entry.grid_position = grid
        entry.num_pit_stops = stops
        entry.result_status = result
        entry.current_lap_num = 28
    await protocol._handle(lap)

    flow = await tools.get_race_flow()
    assert flow["available"] is True
    assert flow["leader"] == "Max Verstappen"
    assert flow["player_position"] == 2
    assert flow["cars_stopped"] == 2
    assert {r["driver"] for r in flow["retired"]} == {"Lewis Hamilton"}
    # Position change is taken from the packet's own grid position.
    player_move = next(m for m in flow["biggest_movers"] if m["grid"] == 5)
    assert player_move["places_gained"] == 3


@pytest.mark.asyncio
async def test_undercut_range_requires_the_car_to_be_behind_on_position(stack):
    """A lapped car can show a positive gap while running ahead."""
    store, _database, _strategy, _setup, _analysis, tools = stack
    protocol = await _field_of_three(store)
    await store.update(mode_profile="race", player_position=2)

    lap = PacketLapData()
    lap.header = header(2)
    lap.lap_data[0].car_position = 2  # player
    lap.lap_data[0].current_lap_num = 28
    lap.lap_data[0].result_status = 2
    lap.lap_data[1].car_position = 1  # leader, ahead on position
    lap.lap_data[1].current_lap_num = 28
    lap.lap_data[1].result_status = 2
    lap.lap_data[1].delta_to_race_leader_ms_part = 0
    lap.lap_data[2].car_position = 3  # genuinely behind
    lap.lap_data[2].current_lap_num = 28
    lap.lap_data[2].result_status = 2
    lap.lap_data[2].delta_to_race_leader_ms_part = 9000
    await protocol._handle(lap)

    flow = await tools.get_race_flow()
    threats = {car["driver"] for car in flow["cars_within_undercut_range"]}
    assert threats == {"Lewis Hamilton"}, "only cars classified behind can threaten"


@pytest.mark.asyncio
async def test_flag_status_locates_the_yellow_around_the_lap(stack):
    store, _database, _strategy, _setup, _analysis, tools = stack
    protocol = F1DatagramProtocol(store)

    session = PacketSessionData()
    session.header = header(1)
    session.session_type = 15
    session.track_id = 13
    session.track_length = 5807
    session.num_marshal_zones = 3
    session.marshal_zones[0].zone_flag = 1  # green
    session.marshal_zones[1].zone_start = 0.62
    session.marshal_zones[1].zone_flag = 3  # yellow
    session.marshal_zones[2].zone_start = 0.8
    session.marshal_zones[2].zone_flag = 0
    await protocol._handle(session)
    await store.update(lap_distance_m=2000.0)

    flags = await tools.get_flag_status()
    assert flags["available"] is True
    assert len(flags["active_zones"]) == 1, "green and none are not incidents"
    assert flags["next_incident_zone"]["flag"] == "yellow"
    assert flags["next_incident_zone"]["distance_ahead_m"] == pytest.approx(
        1600.3, abs=1.0
    )


@pytest.mark.asyncio
async def test_rival_car_state_reports_restriction_rather_than_zeros(stack):
    store, _database, _strategy, _setup, _analysis, tools = stack
    protocol = F1DatagramProtocol(store)
    participants = PacketParticipantsData()
    participants.header = header(4)
    participants.num_active_cars = 2
    participants.participants[0].name = b"PLAYER"
    participants.participants[0].your_telemetry = 1
    participants.participants[1].name = b"RIVAL"
    participants.participants[1].driver_id = 9
    participants.participants[1].your_telemetry = 0
    await protocol._handle(participants)

    report = await tools.get_rival_car_state("Max")
    assert report["available"] is True
    assert report["telemetry_restricted"] is True
    assert "restricted" in report["note"]
    # No fabricated condition data for a car that does not publish it.
    assert "tyre_wear_pct" not in report
    assert "ers_pct" not in report

    missing = await tools.get_rival_car_state("Schumacher")
    assert missing["available"] is False


@pytest.mark.asyncio
async def test_driver_lookup_resolves_first_names_and_car_numbers():
    """"has Max boxed yet" previously returned "No data available for Max"."""
    store = StateStore()
    await _field_of_three(store)
    state = await store.snapshot()

    for query, expected in (
        ("Max", "Max Verstappen"),
        ("verstappen", "Max Verstappen"),
        ("Lewis", "Lewis Hamilton"),
        ("car 44", "Lewis Hamilton"),
    ):
        match = TelemetryTools._resolve_driver(state, query)
        assert match is not None, query
        assert display_name(match) == expected, query

    # Team names are now resolved from the verified appendix.
    assert state["drivers"][1]["team"] == "Red Bull Racing"
    assert state["drivers"][2]["team"] == "Ferrari"

    # A word that merely contains a name fragment matches nobody.
    assert match_drivers(state["drivers"], "check the ers deployment") == []
