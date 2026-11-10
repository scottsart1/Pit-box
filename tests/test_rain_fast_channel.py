"""The surface read from the sectors of the lap in progress.

A completed lap reports the surface about half a lap late, and only once a
lap. The lap-data packet carries every car's first and second sectors of the
lap in progress; those arrive a third of a lap after the surface changes. The
tyre surface temperature falls when water reaches the tyre; it is published
and never fitted until its relation to the surface is measured.
"""

from __future__ import annotations

import pytest

from pitwall import rain
from pitwall.proactive import ProactiveEngineer
from tools import rain_onset_probe as probe

DRY_SECTOR_MS = 30_000


def _rival(index, *, current_s1=32_400, current_s2=0, lap=6, **extra):
    return {
        "car_idx": index, "name": f"CAR{index}", "active": True, "result_label": "active",
        "tyre_compound": "MEDIUM", "current_lap": lap,
        "current_s1_ms": current_s1, "current_s2_ms": current_s2,
        "lap_history": [
            {"lap_num": number, "lap_ms": 3 * DRY_SECTOR_MS, "valid_flags": 1,
             "s1_ms": DRY_SECTOR_MS, "s2_ms": DRY_SECTOR_MS, "s3_ms": DRY_SECTOR_MS}
            for number in range(1, lap)
        ],
        "tyre_stints": [{"start_lap": 1, "end_lap": 255, "compound": "MEDIUM"}],
        **extra,
    }


def _player_laps(count=5):
    return [
        {"lap_num": number, "lap_time_ms": 90_000, "valid": True, "pit_status": 0, "weather": "Clear",
         "compound": "MEDIUM", "s1_ms": DRY_SECTOR_MS, "s2_ms": DRY_SECTOR_MS, "s3_ms": DRY_SECTOR_MS,
         "learning_exclusions": []}
        for number in range(1, count + 1)
    ]


def _state(drivers, **extra):
    return {
        "weather": "Clear", "track_temp_c": 28, "active_cars": 20, "current_lap": 6,
        "player_car_index": 0, "tyre": {"compound": "MEDIUM"}, "completed_laps": _player_laps(),
        "drivers": drivers, "sector2_start_m": 1700.0, "sector3_start_m": 3400.0,
        **extra,
    }


def _field(count=12, **extra):
    return [_rival(index, **extra) for index in range(1, count + 1)]


def test_sector_pace_reads_the_surface_before_the_lap_ends():
    state = _state(_field())
    observations = rain.sector_pace_observations(state, dry_until_lap=5)
    assert len(observations) == 8
    assert all(item["ratio"] == pytest.approx(1.08) for item in observations)
    reading = rain.estimate_wetness(state)
    assert 0.2 <= reading["fast_measured_wetness"] <= 0.3
    assert reading["fast_cars"] == 8
    assert reading["fast_confidence"] >= 0.5
    # The estimate moves before any wet lap is completed.
    assert reading["wetness"] > reading["dynamic_wetness"]


def test_a_dry_field_reads_dry():
    reading = rain.estimate_wetness(_state(_field(current_s1=DRY_SECTOR_MS, current_s2=DRY_SECTOR_MS)))
    assert reading["fast_measured_wetness"] == 0.0
    assert reading["wetness"] == 0.0


def test_a_yellow_sector_is_not_evidence():
    zones = [{"start_m": 500.0, "flag": "yellow"}]
    state = _state(_field(), marshal_zones=zones)
    assert rain.sector_pace_observations(state, dry_until_lap=5) == []
    # A yellow in sector three leaves the first two as evidence.
    state["marshal_zones"] = [{"start_m": 4000.0, "flag": "yellow"}]
    assert len(rain.sector_pace_observations(state, dry_until_lap=5)) == 8


def test_a_car_in_the_pit_lane_posts_no_sector():
    state = _state(_field(pit_lane_timer_active=True))
    assert rain.sector_pace_observations(state, dry_until_lap=5) == []


def test_an_out_lap_posts_no_sector():
    stints = [{"start_lap": 1, "end_lap": 5, "compound": "MEDIUM"},
              {"start_lap": 6, "end_lap": 255, "compound": "INTER"}]
    state = _state(_field(tyre_stints=stints))
    assert rain.sector_pace_observations(state, dry_until_lap=5) == []


def test_restricted_cars_contribute_nothing():
    state = _state(_field(restricted=True))
    assert rain.sector_pace_observations(state, dry_until_lap=5) == []


def test_a_safety_car_switches_the_sectors_off():
    state = _state(_field(), safety_car="full")
    assert rain.sector_pace_observations(state, dry_until_lap=5) == []


def test_sector_pace_confirms_a_stop_the_label_alone_could_not():
    state = probe.state_at(11, 11, 100, with_field=False)
    label_alone = rain.evaluate(
        state, base_lap_s=probe.DRY_LAP_S, remaining_laps=probe.TOTAL_LAPS - 10,
        pit_loss_s=probe.PIT_LOSS_S, available_compounds=probe.AVAILABLE,
    )
    assert label_alone["should_change"] is False
    state["drivers"] = [_rival(index, lap=11, current_s1=32_400, current_s2=32_400) for index in range(1, 13)]
    decision = rain.evaluate(
        state, base_lap_s=probe.DRY_LAP_S, remaining_laps=probe.TOTAL_LAPS - 10,
        pit_loss_s=probe.PIT_LOSS_S, available_compounds=probe.AVAILABLE,
    )
    assert decision["confirmed"] is True
    assert decision["should_change"] is True


def test_the_probe_reads_the_first_wet_lap_from_its_sectors():
    first_wet_lap = probe.evaluate_at(11, 11, 100, mid_lap=True)["reading"]
    second = probe.evaluate_at(12, 11, 100, mid_lap=True)["reading"]
    assert 0.05 < first_wet_lap["fast_measured_wetness"] < second["fast_measured_wetness"]
    assert second["fast_measured_wetness"] >= 0.2


# --- tyre surface temperature: published, not fitted --------------------------


def test_the_field_surface_temperature_drop_is_published_not_fitted():
    cooled = _field(current_s1=DRY_SECTOR_MS, current_s2=DRY_SECTOR_MS,
                    surface_temp_baseline_c=95.0, surface_temp_recent_c=83.0)
    reading = rain.estimate_wetness(_state(cooled))
    assert reading["field_surface_temp_drop_c"] == pytest.approx(12.0)
    plain = rain.estimate_wetness(_state(_field(current_s1=DRY_SECTOR_MS, current_s2=DRY_SECTOR_MS)))
    assert plain["field_surface_temp_drop_c"] is None
    assert reading["wetness"] == plain["wetness"]


def test_a_drop_needs_three_cars():
    drivers = _field(3, surface_temp_baseline_c=95.0, surface_temp_recent_c=90.0)
    drivers[0]["pit_lane_timer_active"] = True
    assert rain.field_surface_temp_drop_c({"drivers": drivers}) is None


# --- recompute as the sectors land --------------------------------------------


def test_sectors_landing_recompute_the_call_in_the_rain_only():
    drivers = [{"current_s1_ms": 31_000 if index < 7 else 0, "current_s2_ms": 0} for index in range(20)]
    assert ProactiveEngineer._sector_progress({"weather": "Clear", "drivers": drivers}) is None
    raining = {"weather": "Light rain", "drivers": drivers}
    assert ProactiveEngineer._sector_progress(raining) == (1, 0)
    assert ProactiveEngineer._sector_progress({"weather": "Overcast", "rain_now_pct": 40, "drivers": drivers}) == (1, 0)


# --- off the wire ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_sector_times_and_surface_temperatures_come_off_the_packets():
    import asyncio
    from contextlib import suppress

    from pitwall.state import StateStore
    from pitwall.udp import F1DatagramProtocol
    from tools import replay_demo as replay

    store = StateStore()
    protocol = F1DatagramProtocol(store)
    consumer = asyncio.create_task(protocol._consume_packets())
    cars = [replay.Car(i, spec) for i, spec in enumerate(replay.GRID)]
    for car in cars:
        car.lap, car.speed = 5, 260

    def send(packet: bytes) -> None:
        protocol.datagram_received(packet, ("127.0.0.1", 9999))

    def telemetry(stamp: float, surface: int) -> bytes:
        packet = replay.PacketCarTelemetryData.from_buffer_copy(replay.build_telemetry(cars, stamp, 2))
        for car in cars:
            for wheel in range(4):
                packet.car_telemetry_data[car.index].tyres_surface_temperature[wheel] = surface
        return bytes(packet)

    try:
        send(replay.build_participants(cars, 100.0, 1))
        send(replay.build_session(100.0, 1, 30, 5))
        lap = replay.PacketLapData.from_buffer_copy(replay.build_lap_data(cars, 100.0, 1))
        lap.lap_data[3].sector1_time_ms_part = 31_250
        lap.lap_data[3].sector1_time_minutes_part = 0
        send(bytes(lap))
        await protocol.packet_queue.join()
        driver = store.state.drivers[3]
        assert (driver.current_s1_ms, driver.current_s2_ms) == (31_250, 0)

        await store.mutate(lambda state: setattr(state, "weather", "Clear"))
        send(telemetry(100.0, 100))
        for second in range(1, 31):
            send(telemetry(100.0 + second, 100))
        await protocol.packet_queue.join()
        assert driver.surface_temp_baseline_c == pytest.approx(100.0)

        # Rain: the recent value follows the cooled tyre, the dry baseline holds.
        await store.mutate(lambda state: setattr(state, "weather", "Light rain"))
        for second in range(31, 61):
            send(telemetry(100.0 + second, 88))
        await protocol.packet_queue.join()
        assert driver.surface_temp_baseline_c == pytest.approx(100.0)
        assert driver.surface_temp_recent_c == pytest.approx(88.0, abs=1.0)
    finally:
        consumer.cancel()
        with suppress(asyncio.CancelledError):
            await consumer


def test_each_lap_records_the_surface_it_was_read_as():
    from pitwall.state import _surface_at_line

    strategy = {"weather_crossover": {"wetness": 0.41, "wetness_source": {
        "field_sector_ratio": 1.07, "field_surface_temp_drop_c": 9.5}}}
    assert _surface_at_line(strategy) == {
        "surface_estimate": 0.41, "field_sector_ratio": 1.07, "field_surface_temp_drop_c": 9.5,
    }
    assert _surface_at_line({}) == {
        "surface_estimate": None, "field_sector_ratio": None, "field_surface_temp_drop_c": None,
    }
