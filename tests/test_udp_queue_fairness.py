"""A nonempty telemetry queue must not monopolize the asyncio event loop."""
import asyncio
from contextlib import suppress
from types import SimpleNamespace

import pytest

from pitwall.state import StateStore
from pitwall.udp import F1DatagramProtocol


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [False, True])
async def test_packet_backlog_yields_to_other_ready_work(monkeypatch, invalid):
    protocol = F1DatagramProtocol(StateStore(), queue_capacity=256)
    handled = []

    def resolve(data):
        if invalid:
            raise ValueError("invalid synthetic packet")
        return data

    async def handle(packet, received):
        handled.append(packet)

    monkeypatch.setattr("pitwall.udp.resolve", resolve)
    monkeypatch.setattr(protocol, "_handle", handle)
    for index in range(128):
        protocol.packet_queue.put_nowait(SimpleNamespace(data=index, source=("127.0.0.1", 1), health_key=None))
    observed = []

    async def dashboard_work():
        observed.append(protocol.packet_queue.qsize())

    consumer = asyncio.create_task(protocol._consume_packets())
    observer = asyncio.create_task(dashboard_work())
    try:
        await observer
        await asyncio.wait_for(protocol.packet_queue.join(), timeout=1)
        assert observed and 0 < observed[0] < 128, "ready work must run before the backlog drains"
        assert handled == ([] if invalid else list(range(128))), "do not reorder or discard valid packets"
    finally:
        consumer.cancel()
        with suppress(asyncio.CancelledError):
            await consumer
