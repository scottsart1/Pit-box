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
