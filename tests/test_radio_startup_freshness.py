"""Disabled radio must stay idle; parser backlog must not look live."""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from f1.packets import PacketEventData

from pitwall.config import settings
from pitwall.proactive import ProactiveEngineer
from pitwall.state import StateStore
from pitwall.udp import F1DatagramProtocol, ReceivedDatagram, inspect_2026_header


@pytest.mark.asyncio
async def test_startup_disabled_preference_skips_planning_and_radio(stack, monkeypatch):
    monkeypatch.setattr(settings, "proactive_enabled", False)
    monkeypatch.setattr(settings, "proactive_cadence_laps", 4)
    store = StateStore()
    assert store.state.proactive["enabled"] is False
    assert store.state.proactive["cadence_laps"] == 4
    _, database, *_ = stack
    strategy = SimpleNamespace(recompute=AsyncMock())
    engineer = ProactiveEngineer(
        store, SimpleNamespace(database=database), SimpleNamespace(is_busy=False),
        SimpleNamespace(learn_current_session=AsyncMock()), strategy,
    )
    await store.update(connected=True, session_uid=1, mode_profile="race")
    await engineer._detect(await store.snapshot_radio())
    strategy.recompute.assert_not_called()
    assert not engineer.pending
    # Applying the saved preference also cancels previously queued calls.
    engineer._enqueue("strategy_change", {"instruction": "Box"}, cooldown_s=0)
    await engineer.configure(False, 4)
    assert not engineer.pending


@pytest.mark.asyncio
async def test_backlogged_packet_keeps_receive_time_for_freshness():
    store = StateStore()
    protocol = F1DatagramProtocol(store)
    packet = PacketEventData()
    packet.header.packet_format = 2026
    packet.header.packet_id = 3
    packet.header.session_uid = 456
    packet.event_string_code[:] = [ord(char) for char in "RDFL"]
    wall = time.time() - 10
    data = bytes(packet)
    received = ReceivedDatagram(
        data, ("127.0.0.1", 20777), int((time.monotonic() - 10) * 1e9),
        int(wall * 1e9), inspect_2026_header(data),
    )
    await protocol._handle(packet, received)
    state = await store.snapshot_live()
    assert state["red_flag_active"], "Backlog must still preserve race-control events"
    assert state["packets_received"] == 1
    assert state["last_packet_at"] == pytest.approx(wall)
    assert state["packet_group_freshness"]["3"] == pytest.approx(wall)
    assert state["connected"] is False
    assert state["packet_rate_hz"] == 0
