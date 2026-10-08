"""Suspension tyre advice from real packet types through the radio pipeline."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from f1.packets import PacketCarStatusData, PacketEventData, PacketSessionData

from pitwall.proactive import ProactiveEngineer
from pitwall.udp import F1DatagramProtocol


def event(code):
    packet = PacketEventData()
    packet.event_string_code[:] = [ord(char) for char in code]
    return packet


async def configure(stack, *, remaining=8, sets=True):
    store, _, engine, *_ = stack
    await store.update(connected=True, session_uid=123, mode_profile="race",
                       session_type="Race", current_lap=10, total_laps=9 + remaining,
                       track_id=0, player_position=1, active_cars=1)

    def arrange(state):
        state.tyre.compound = "MEDIUM"
        state.tyre.age_laps = 10
        state.tyre.wear = [65] * 4
        state.proactive["enabled"] = True
        state.completed_laps = [{"lap_num": 1, "compound": "HARD", "valid": True,
                                 "lap_time_ms": 90000}]
        state.tyre_sets = [
            {"index": idx, "compound": compound, "available": True,
             "wear_pct": 0, "usable_life_laps": 40, "life_span_laps": 40}
            for idx, compound in enumerate(("SOFT", "MEDIUM", "HARD"))
        ] if sets else []
    await store.mutate(arrange)
    return store, engine, F1DatagramProtocol(store)


def radio(stack):
    store, database, strategy, setup, *_ = stack
    brain = SimpleNamespace(database=database, record_spoken_call=AsyncMock(), proactive=AsyncMock())
    voice = SimpleNamespace(is_busy=False, speak_text=AsyncMock(return_value=True))
    return ProactiveEngineer(store, brain, voice, setup, strategy)


@pytest.mark.asyncio
async def test_rdfl_paused_radio_has_primary_and_distinct_alternative_once(stack):
    store, strategy, protocol = await configure(stack)
    engineer = radio(stack)
    await engineer._reset_for_session(123)
    # The same RDFL -> SCAR(none, resume) -> LGOT order was observed in a
    # physical-tablet Singapore capture. The SC event is NOT the restart.
    await protocol.handle_PacketEventData(event("RDFL"))
    resume_marker = event("SCAR")
    resume_marker.event_details.safety_car.safety_car_type = 0
    resume_marker.event_details.safety_car.event_type = 3
    await protocol.handle_PacketEventData(resume_marker)
    await store.update(game_paused=True)
    await engineer._detect(await store.snapshot_radio())
    state = await store.snapshot_radio()
    advice = state["strategy"]["red_flag_restart"]
    assert advice["primary"]["compound"] != advice["alternative"]["compound"]
    assert advice["primary"]["inventory_confirmed"]
    assert state["strategy"]["recommended"]["action"] == "red_flag_tyre_change"
    assert [item["type"] for item in engineer.pending] == ["race_control"]
    await engineer._deliver(state)
    spoken = engineer.voice.speak_text.call_args.args[0]
    assert spoken.startswith("Red flag, red flag.")
    assert "Best strategy:" in spoken and "Alternative:" in spoken
    assert "8 laps remaining" in spoken and "fresh " in spoken
    assert "box" not in spoken.lower()
    engineer.brain.proactive.assert_not_called()
    for _ in range(3):
        await protocol.handle_PacketEventData(event("RDFL"))
        await engineer._detect(await store.snapshot_radio())
        await engineer._deliver(await store.snapshot_radio())
    assert engineer.voice.speak_text.await_count == 1
    assert (await strategy.get_plan())["red_flag_restart"]["active"]
    await protocol.handle_PacketEventData(event("LGOT"))
    await store.update(game_paused=False)
    resumed = await strategy.get_plan()
    assert "red_flag_restart" not in resumed
    assert resumed["neutralisation"]["phase"] == "green"


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["green", "safety_car", "vsc"])
async def test_pause_and_historical_counter_do_not_invent_red_flag(stack, phase):
    store, strategy, _ = await configure(stack)
    await store.update(game_paused=True, red_flag_count=2, race_control_phase=phase)
    engineer = radio(stack)
    await engineer._reset_for_session(123)
    await engineer._detect(await store.snapshot_radio())
    assert not engineer.pending
    assert "red_flag_restart" not in await strategy.recompute()


@pytest.mark.asyncio
async def test_last_lap_dry_suspension_change_is_available(stack):
    _, strategy, protocol = await configure(stack, remaining=1)
    await protocol.handle_PacketEventData(event("RDFL"))
    plan = await strategy.get_plan()
    assert plan["recommended"]["action"] == "red_flag_tyre_change"
    assert plan["recommended"]["box_lap"] == 10
    assert plan["recommended"]["pit_stop_costs_s"] == [0]
    assert plan["red_flag_restart"]["laps_remaining"] == 1


@pytest.mark.asyncio
async def test_unknown_inventory_never_promises_fresh_available_tyres(stack):
    _, strategy, protocol = await configure(stack, sets=False)
    await protocol.handle_PacketEventData(event("RDFL"))
    advice = (await strategy.get_plan())["red_flag_restart"]
    assert advice["inventory_status"] == "unknown"
    for choice in (advice["primary"], advice["alternative"]):
        if choice:
            assert choice["inventory_confirmed"] is False
            assert choice["tyre_set_index"] is None
            assert "Confirm" in choice["instruction"]
            assert "fresh" not in choice["instruction"]


@pytest.mark.asyncio
async def test_wet_restart_respects_only_available_worn_spare(stack):
    store, strategy, protocol = await configure(stack, remaining=4)
    await store.update(weather="Heavy rain", rain_now_pct=100, rain_next_15_pct=100,
                       tyre_sets=[{"index": 8, "compound": "WET", "available": True,
                                   "wear_pct": 15, "life_span_laps": 20, "usable_life_laps": 20}])
    await protocol.handle_PacketEventData(event("RDFL"))
    advice = (await strategy.get_plan())["red_flag_restart"]
    assert advice["primary"]["compound"] == "WET"
    assert advice["primary"]["tyre_set_index"] == 8
    assert advice["primary"]["starting_wear_pct"] == 15
    assert "fresh" not in advice["instruction"]
    assert advice["alternative"] is None


@pytest.mark.asyncio
async def test_drive_to_suspension_does_not_expire_red_flag(stack):
    store, _, protocol = await configure(stack)
    await protocol.handle_PacketEventData(event("RDFL"))
    await store.update(race_control_changed_at=1.0)
    telemetry = {"session_time": 60, "speed_kph": 100, "gear": 4, "throttle": .2,
                 "brake": 0, "steer": 0, "inner_temps_c": [80] * 4,
                 "surface_temps_c": [80] * 4, "pressures_psi": [24] * 4}
    await store.update_telemetry_and_trace(**telemetry)
    assert (await store.snapshot_live())["red_flag_active"]
    packet = PacketSessionData()
    packet.game_paused = 1
    packet.session_type = 10
    packet.total_laps = 17
    await store.update(speed_kph=0)
    await protocol.handle_PacketSessionData(packet)
    await store.update(game_paused=False)
    await store.update_telemetry_and_trace(**telemetry)
    assert not (await store.snapshot_live())["red_flag_active"]


@pytest.mark.asyncio
async def test_stale_red_flag_call_cannot_play_under_safety_car(stack):
    store, _, protocol = await configure(stack)
    engineer = radio(stack)
    await engineer._reset_for_session(123)
    await protocol.handle_PacketEventData(event("RDFL"))
    await engineer._detect(await store.snapshot_radio())
    await protocol.handle_PacketEventData(event("LGOT"))
    await store.update(safety_car="full", race_control_phase="safety_car")
    await engineer._deliver(await store.snapshot_radio())
    engineer.voice.speak_text.assert_not_called()


@pytest.mark.asyncio
async def test_unpaused_suspension_survives_packet_gap_and_same_compound_change(stack):
    store, _, protocol = await configure(stack)
    status = PacketCarStatusData()
    status.car_status_data[0].visual_tyre_compound = 17  # F1 packet MEDIUM
    status.car_status_data[0].tyres_age_laps = 3
    await protocol.handle_PacketCarStatusData(status)
    await protocol.handle_PacketEventData(event("RDFL"))
    await store.update(last_packet_at=1.0, race_control_changed_at=1.0)
    await store.mark_disconnected_if_stale(3.0)
    state = await store.snapshot_live()
    assert not state["connected"] and not state["game_paused"]
    assert state["red_flag_active"] and state["race_control_phase"] == "red_flag"
    # Captured game behavior: after the menu gap, the same compound returns
    # at age zero before lights out. Changing tyres is not itself a restart.
    status.car_status_data[0].tyres_age_laps = 0
    await protocol.handle_PacketCarStatusData(status)
    state = await store.snapshot_live()
    assert state["tyre"]["age_laps"] == 0
    assert state["red_flag_active"]
    await protocol.handle_PacketEventData(event("LGOT"))
    assert not (await store.snapshot_live())["red_flag_active"]
