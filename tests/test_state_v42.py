from __future__ import annotations

import pytest
from f1.packets import PacketEventData, PacketHeader

from pitwall.state import StateStore
from pitwall.udp import F1DatagramProtocol


@pytest.mark.asyncio
async def test_packet_group_freshness_and_revision_are_additive() -> None:
    store = StateStore()
    await store.mark_packet(2026, 26, 123, 6, 44, 45)
    state = await store.snapshot_live()
    assert state["packet_group_freshness"]["6"] > 0
    assert state["availability"]["packet:6"] == "observed"
    assert state["frame_identifier"] == 44
    assert state["overall_frame_identifier"] == 45
    assert state["state_revision"] > 0


@pytest.mark.asyncio
async def test_new_session_preserves_persistent_standing_instructions() -> None:
    store = StateStore()
    await store.update(
        standing_instructions=[
            {"subject": "pace", "action": "suppress", "created_at": 1.0}
        ]
    )
    await store.mark_packet(2026, 26, 111)
    await store.mark_packet(2026, 26, 222)
    state = await store.snapshot()
    assert state["session_uid"] == 222
    assert state["standing_instructions"][0]["subject"] == "pace"


@pytest.mark.asyncio
async def test_same_uid_restart_epoch_resets_session_and_keeps_packet_fact() -> None:
    store = StateStore()
    await store.update(
        standing_instructions=[{"subject": "fuel", "action": "report"}],
        current_lap=18,
        traces=[{"d": 900.0, "t": 30.0}],
    )
    await store.mark_packet(2026, 26, 77, 1, 3, 3)

    await store.synchronize_session_epoch(77, 1, 0)

    state = await store.snapshot()
    assert state["session_uid"] == 77
    assert state["restart_epoch"] == 1
    assert state["timeline_epoch"] == 0
    assert state["current_lap"] == 0
    assert state["traces"] == []
    assert state["connected"] is True
    assert state["packet_format"] == 2026
    assert state["standing_instructions"][0]["subject"] == "fuel"


@pytest.mark.asyncio
async def test_flashback_starts_new_timeline_and_clears_only_in_progress_trace() -> (
    None
):
    store = StateStore()
    await store.update(
        traces=[{"d": 100.0, "t": 1.0}],
        completed_laps=[{"lap_num": 1}],
    )
    protocol = F1DatagramProtocol(store)
    packet = PacketEventData()
    header = PacketHeader()
    header.packet_format = 2026
    header.session_uid = 44
    packet.header = header
    packet.event_string_code[:] = [ord(value) for value in "FLBK"]
    packet.event_details.flashback.flashback_frame_identifier = 10
    packet.event_details.flashback.flashback_session_time = 2.5
    await protocol.handle_PacketEventData(packet)
    state = await store.snapshot()
    assert state["timeline_epoch"] == 1
    assert state["traces"] == []
    assert state["completed_laps"] == [{"lap_num": 1}]


@pytest.mark.asyncio
async def test_red_flag_restart_grid_uses_typed_driver_state() -> None:
    store = StateStore()

    def arrange(state):  # type: ignore[no-untyped-def]
        state.drivers[2].active = True
        state.drivers[2].name = "Driver B"
        state.drivers[2].position = 2
        state.drivers[2].tyre_compound = "HARD"
        state.drivers[2].tyre_age = 8
        state.drivers[7].active = True
        state.drivers[7].name = "Driver A"
        state.drivers[7].position = 1
        state.drivers[7].tyre_compound = "MEDIUM"
        state.drivers[7].tyre_age = 5

    await store.mutate(arrange)
    protocol = F1DatagramProtocol(store)
    packet = PacketEventData()
    packet.event_string_code[:] = [ord(value) for value in "RDFL"]
    await protocol.handle_PacketEventData(packet)
    state = await store.snapshot()
    assert [item["car_idx"] for item in state["restart_grid"]] == [7, 2]
    assert state["restart_grid"][0]["tyre_compound"] == "MEDIUM"


def test_trace_thinning_stays_dense_enough_for_segment_analysis() -> None:
    """Trace spacing must stay well inside the alignment bridge threshold.

    Distance alignment refuses to interpolate across a gap wider than its
    bridge threshold (5 m). If trace thinning is ever loosened past that, a
    corner silently reports "unavailable" instead of its metrics, which reads
    as missing data rather than as a capture setting.
    """
    from pitwall.config import settings
    from pitwall.state import SessionState

    state = SessionState()
    length_m, lap_s, hz = 4520.0, 78.0, 60.0
    for index in range(int(lap_s * hz)):
        seconds = index / hz
        StateStore._append_trace_locked(
            state,
            {
                "t": seconds,
                "d": length_m * (seconds / lap_s),
                "speed": 250, "throttle": 1.0, "brake": 0.0, "steer": 0.0,
                "gear": 7, "lat_g": 0.0, "long_g": 0.0,
            },
        )

    traces = state.traces
    assert len(traces) > 3_000, "a full lap should retain analysis-grade density"
    gaps = [traces[i + 1]["d"] - traces[i]["d"] for i in range(len(traces) - 1)]
    assert max(gaps) < 5.0, "trace spacing exceeded the alignment bridge threshold"
    assert settings.trace_min_distance_m <= 1.0


@pytest.mark.asyncio
async def test_full_snapshot_bounds_the_trace_payload() -> None:
    """Tool snapshots must stay bounded however dense the trace becomes.

    snapshot() serves tool calls, and the engineer must never be handed
    thousands of raw samples to reason over. Copying the whole list also made
    every tool call scale with trace density: 30,000 points cost 1.7 s before
    this bound, against 63 ms after it.
    """
    store = StateStore()
    for index in range(30_000):
        store.state.traces.append(
            {"t": index / 60.0, "d": index * 0.5, "speed": 250, "throttle": 1.0,
             "brake": 0.0, "steer": 0.0, "gear": 7, "lat_g": 0.0, "long_g": 0.0}
        )

    full = await store.snapshot()
    live = await store.snapshot_live()
    analysis = await store.snapshot_analysis()

    assert len(full["traces"]) <= 1_300, "tool payload must not carry raw samples"
    assert len(live["traces"]) <= 1_300
    assert analysis["traces"] == []
    # The most recent sample must survive downsampling; it is the current state.
    assert full["traces"][-1]["d"] == store.state.traces[-1]["d"]


@pytest.mark.asyncio
async def test_stationary_samples_do_not_shift_history_at_the_trace_cap() -> None:
    """A stopped car must not make old speed/pedal curves dance on each refresh."""
    from pitwall.config import settings

    store = StateStore()
    store.state.lap_distance_m = 6000.0
    store.state.traces = [
        {"t": i / 60, "d": i / 5, "speed": 120 + i % 180,
         "throttle": float(i % 40 < 30), "brake": float(i % 40 >= 30),
         "steer": 0.0, "gear": 5}
        for i in range(settings.trace_max_points - 2)
    ]
    stopped = {"d": 6000.0, "speed": 0, "throttle": 0.0, "brake": 0.0,
               "steer": 0.0, "gear": 1}
    store.state.traces.extend([dict(stopped, t=500.0), dict(stopped, t=500.1)])
    first = dict(store.state.traces[0])

    def shape(snapshot):
        return [tuple(p.get(key) for key in ("d", "speed", "throttle", "brake"))
                for p in snapshot["traces"]]

    before = shape(await store.snapshot_live())
    for i in range(1, 101):
        await store.update_telemetry_and_trace(
            session_time=500.1 + i / 10, speed_kph=0, gear=1,
            throttle=0.0, brake=0.0, steer=0.0,
            inner_temps_c=[80.0] * 4, surface_temps_c=[70.0] * 4,
            pressures_psi=[22.0] * 4,
        )
        assert shape(await store.snapshot_live()) == before
    assert len(store.state.traces) == settings.trace_max_points
    assert store.state.traces[0] == first
    assert store.state.traces[-2]["t"] == 500.0, "keep when the stop began"
    assert store.state.traces[-1]["t"] == 510.1, "keep the latest stop timestamp"
    assert store.state.speed_kph == 0 and store.state.throttle == store.state.brake == 0


def test_stationary_run_keeps_its_endpoints_and_real_control_changes() -> None:
    from pitwall.state import SessionState

    state = SessionState()
    stopped = {"d": 50.0, "speed": 0, "throttle": 0.0, "brake": 0.0,
               "steer": 0.0, "gear": 1}
    for t in (1.0, 2.0, 3.0, 4.0):
        StateStore._append_trace_locked(state, dict(stopped, t=t))
    assert [p["t"] for p in state.traces] == [1.0, 4.0]
    for t in (5.0, 6.0, 7.0):
        StateStore._append_trace_locked(state, dict(stopped, t=t, brake=1.0))
    assert [(p["t"], p["brake"]) for p in state.traces] == [
        (1.0, 0.0), (4.0, 0.0), (5.0, 1.0), (7.0, 1.0),
    ]
    StateStore._append_trace_locked(state, dict(stopped, t=8.0, throttle=1.0))
    StateStore._append_trace_locked(state, dict(stopped, t=9.0, steer=0.5))
    StateStore._append_trace_locked(state, dict(stopped, t=10.0, gear=2))
    StateStore._append_trace_locked(state, dict(stopped, t=11.0, d=50.1))
    StateStore._append_trace_locked(state, dict(stopped, t=12.0, d=51.0, speed=5))
    assert [p["t"] for p in state.traces[-5:]] == [8.0, 9.0, 10.0, 11.0, 12.0]


@pytest.mark.asyncio
async def test_live_snapshot_serializes_drivers_without_touching_histories() -> None:
    """The 4 Hz live payload drops per-lap histories without paying for them.

    The previous implementation asdict-ed each driver (deep copying every
    history entry of the whole race) and then popped the heavy fields. The
    replacement must produce the same shape: every scalar field present, the
    six history fields absent, and mutable fields copied, not shared.
    """
    from dataclasses import fields as dataclass_fields

    from pitwall.state import DriverState

    store = StateStore()
    driver = store.state.drivers[3]
    driver.active = True
    driver.name = "Rival"
    driver.tyre_wear = [10.0, 11.0, 12.0, 13.0]
    driver.lap_history = [{"lap": i, "lap_time_ms": 90_000 + i} for i in range(60)]
    driver.gap_history = [{"session_time_s": i, "lap": 1, "gap_s": 2.0} for i in range(90)]
    driver.position_history = [{"lap": i, "position": 5} for i in range(60)]

    live = await store.snapshot_live()
    serialized = next(d for d in live["drivers"] if d["car_idx"] == 3)

    excluded = {
        "lap_history", "tyre_stints", "position_history",
        "gap_history", "energy_lap_history", "pit_stop_history",
    }
    expected_keys = {f.name for f in dataclass_fields(DriverState)} - excluded
    assert set(serialized) == expected_keys
    assert serialized["name"] == "Rival"

    # Mutable fields must be copies: rendering state must never alias live state.
    serialized["tyre_wear"][0] = 99.0
    assert driver.tyre_wear[0] == 10.0

    # The analysis/full profiles still carry the histories for tools/briefings.
    full = await store.snapshot()
    complete = next(d for d in full["drivers"] if d["car_idx"] == 3)
    assert len(complete["lap_history"]) == 60
    assert len(complete["gap_history"]) == 90
