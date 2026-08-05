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
