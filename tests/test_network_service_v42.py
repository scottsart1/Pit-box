from __future__ import annotations

import asyncio
import errno
import json
import struct
from dataclasses import dataclass

import pytest

from pitwall.forwarding import (
    DatagramForwarder,
    ForwardValidationError,
)
from pitwall.network_service import (
    ForwardTargetNotFound,
    ListenerBindError,
    ListenerState,
    NetworkService,
)
from pitwall.networking import AdapterKind, DiscoveryResult, IPv4Interface


def packet_bytes(
    frame: int,
    *,
    packet_id: int = 6,
    session_uid: int = 42,
    body: bytes = b"telemetry",
) -> bytes:
    return (
        struct.pack(
            "<HBBBBBQfIIBB",
            2026,
            26,
            1,
            2,
            3,
            packet_id,
            session_uid,
            12.5,
            frame,
            frame,
            0,
            255,
        )
        + body
    )


@dataclass
class Clock:
    monotonic_value: float = 100.0
    wall_value: float = 1_786_000_000.0

    def monotonic(self) -> float:
        return self.monotonic_value

    def wall(self) -> float:
        return self.wall_value

    def advance(self, seconds: float) -> None:
        self.monotonic_value += seconds
        self.wall_value += seconds


class RecordingProtocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.received: list[tuple[bytes, tuple[str, int]]] = []
        self.packet_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=8)
        self.transport: asyncio.BaseTransport | None = None
        self.lost = False
        self.receiver_queue_drops = 0

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.received.append((data, addr))
        try:
            self.packet_queue.put_nowait(data)
        except asyncio.QueueFull:
            self.receiver_queue_drops += 1

    def connection_lost(self, exc: Exception | None) -> None:
        del exc
        self.lost = True


class FakeTransport:
    def __init__(
        self,
        protocol: asyncio.DatagramProtocol,
        host: str,
        port: int,
    ) -> None:
        self.protocol = protocol
        self.host = host
        self.port = port
        self.closed = False

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.protocol.connection_lost(None)

    def is_closing(self) -> bool:
        return self.closed

    def get_extra_info(self, name: str, default=None):
        return (self.host, self.port) if name == "sockname" else default


class FakeEndpointCreator:
    def __init__(self, *, conflicting_port: int | None = None) -> None:
        self.conflicting_port = conflicting_port
        self.calls: list[tuple[str, int]] = []
        self.protocols: list[asyncio.DatagramProtocol] = []
        self.transports: list[FakeTransport] = []

    async def __call__(self, factory, host: str, port: int):
        self.calls.append((host, port))
        if port == self.conflicting_port:
            raise OSError(errno.EADDRINUSE, "already in use")
        protocol = factory()
        transport = FakeTransport(protocol, host, port)
        protocol.connection_made(transport)  # type: ignore[arg-type]
        self.protocols.append(protocol)
        self.transports.append(transport)
        return transport, protocol


def discovery() -> DiscoveryResult:
    return DiscoveryResult(
        (
            IPv4Interface(
                "wifi-guid",
                "Personal Wi-Fi",
                "192.168.1.42",
                prefix_length=24,
                is_up=True,
                has_default_gateway=True,
                metric=10,
                kind=AdapterKind.WIFI,
            ),
        ),
        "fixture",
    )


def build_service(
    *,
    clock: Clock | None = None,
    endpoints: FakeEndpointCreator | None = None,
    sender=None,
) -> tuple[NetworkService, FakeEndpointCreator, list[RecordingProtocol]]:
    service_clock = clock or Clock()
    endpoint_creator = endpoints or FakeEndpointCreator()
    delegates: list[RecordingProtocol] = []

    def protocol_factory() -> RecordingProtocol:
        protocol = RecordingProtocol()
        delegates.append(protocol)
        return protocol

    async def resolver(host: str, port: int):
        del port
        return [host]

    async def default_sender(data: bytes, endpoint: tuple[str, int]):
        del endpoint
        return len(data)

    forwarder = DatagramForwarder(
        queue_size=32,
        resolver=resolver,
        sender=sender or default_sender,
    )
    service = NetworkService(
        protocol_factory,
        endpoint_creator=endpoint_creator,
        interface_discoverer=discovery,
        forwarder=forwarder,
        monotonic=service_clock.monotonic,
        wall_time=service_clock.wall,
        stale_after_ms=1_000,
        bind_probe=lambda host, port: None,
    )
    return service, endpoint_creator, delegates


@pytest.mark.asyncio
async def test_listener_lifecycle_receiving_stale_and_queue_projection() -> None:
    clock = Clock()
    service, endpoints, delegates = build_service(clock=clock)

    started = await service.start_listener()
    assert started.state is ListenerState.LISTENING
    assert (await service.start_listener()).state is ListenerState.LISTENING
    assert endpoints.calls == [("0.0.0.0", 20_777)]

    datagram = packet_bytes(10)
    endpoints.protocols[-1].datagram_received(datagram, ("192.168.1.61", 54_022))
    snapshot = await service.snapshot()
    assert snapshot.listener.state is ListenerState.RECEIVING
    assert snapshot.source == {"ip": "192.168.1.61", "port": 54_022}
    assert snapshot.game == {
        "packet_format": 2026,
        "game_year": 26,
        "game_major_version": 1,
        "game_minor_version": 2,
        "packet_version": 3,
        "session_uid": "42",
    }
    assert snapshot.packet_health.packets[0].received == 1
    assert snapshot.queues["receiver"].depth == 1
    assert snapshot.queues["receiver"].high_water == 1
    assert service.prior_working_adapter_ids == {"wifi-guid"}
    assert delegates[0].received == [(datagram, ("192.168.1.61", 54_022))]

    for frame in range(11, 20):
        endpoints.protocols[-1].datagram_received(
            packet_bytes(frame), ("192.168.1.61", 54_022)
        )
    snapshot = await service.snapshot()
    assert snapshot.queues["receiver"].depth == 8
    assert snapshot.queues["receiver"].drops == 2

    clock.advance(1.001)
    assert service.listener_snapshot().state is ListenerState.STALE
    assert (await service.stop_listener()).state is ListenerState.OFF
    assert delegates[0].lost is True
    assert (await service.stop_listener()).state is ListenerState.OFF


@pytest.mark.asyncio
async def test_listener_rebind_closes_previous_endpoint() -> None:
    service, endpoints, _ = build_service()
    await service.start_listener(port=20_777)
    first = endpoints.transports[0]

    rebound = await service.start_listener(port=20_778)

    assert first.closed is True
    assert endpoints.calls == [("0.0.0.0", 20_777), ("0.0.0.0", 20_778)]
    assert rebound.state is ListenerState.LISTENING
    assert rebound.port == 20_778
    await service.stop_listener()


@pytest.mark.asyncio
async def test_bind_conflict_becomes_actionable_error_state() -> None:
    endpoints = FakeEndpointCreator(conflicting_port=20_777)
    service, _, _ = build_service(endpoints=endpoints)

    with pytest.raises(ListenerBindError) as caught:
        await service.start_listener()

    assert caught.value.code == "bind_conflict"
    assert "already in use" in str(caught.value)
    snapshot = service.listener_snapshot()
    assert snapshot.state is ListenerState.ERROR
    assert snapshot.port == 20_777
    await service.stop_listener()


@pytest.mark.asyncio
async def test_invalid_bind_is_rejected_before_disrupting_live_listener() -> None:
    service, endpoints, _ = build_service()
    await service.start_listener()

    with pytest.raises(ListenerBindError) as caught:
        await service.start_listener("not-an-ip", 20_778)

    assert caught.value.code == "invalid_bind_host"
    assert endpoints.transports[0].closed is False
    assert service.listener_snapshot().state is ListenerState.LISTENING
    await service.stop_listener()


@pytest.mark.asyncio
async def test_forward_target_crud_counters_and_atomic_validation() -> None:
    sent: list[tuple[bytes, tuple[str, int]]] = []

    async def sender(data: bytes, endpoint: tuple[str, int]):
        sent.append((data, endpoint))
        return len(data)

    service, endpoints, _ = build_service(sender=sender)
    await service.start_listener()
    created = await service.create_forward_target(
        target_id="local_overlay",
        label="Local overlay",
        host="127.0.0.1",
        port=20_778,
    )
    assert created.target.id == "local_overlay"
    assert created.counters.resolved_addresses == ("127.0.0.1",)

    datagram = packet_bytes(1)
    endpoints.protocols[-1].datagram_received(datagram, ("192.168.1.61", 54_022))
    await asyncio.wait_for(service.forwarder.queue.join(), timeout=1)
    forwarded = service.managed_targets()[0]
    assert sent == [(datagram, ("127.0.0.1", 20_778))]
    assert forwarded.counters.sent_packets == 1
    assert forwarded.counters.sent_bytes == len(datagram)

    with pytest.raises(ForwardValidationError) as caught:
        await service.update_forward_target(
            "local_overlay", host="8.8.8.8", allow_public=False
        )
    assert caught.value.code == "public_confirmation_required"
    assert service.configured_targets[0].host == "127.0.0.1"

    updated = await service.update_forward_target(
        "local_overlay", label="Disabled overlay", enabled=False
    )
    assert updated.target.enabled is False
    await service.delete_forward_target("local_overlay")
    assert service.managed_targets() == ()
    with pytest.raises(ForwardTargetNotFound):
        await service.delete_forward_target("local_overlay")
    await service.stop_listener()


@pytest.mark.asyncio
async def test_live_listener_self_loop_is_rejected() -> None:
    service, _, _ = build_service()
    await service.start_listener()

    with pytest.raises(ForwardValidationError) as caught:
        await service.create_forward_target(
            target_id="cycle",
            label="Cycle",
            host="192.168.1.42",
            port=20_777,
        )

    assert caught.value.code == "self_loop"
    assert service.configured_targets == ()
    await service.stop_listener()


@pytest.mark.asyncio
async def test_diagnosis_is_safe_and_does_not_claim_console_reception() -> None:
    service, _, _ = build_service()
    report = await service.diagnose()

    checks = {item["id"]: item for item in report.checks}
    assert checks["console_interface"]["status"] == "pass"
    assert checks["udp_bind"]["status"] == "pass"
    assert checks["telemetry_reception"]["status"] == "warning"
    encoded = json.dumps(report.redacted_report)
    assert "Personal Wi-Fi" not in encoded
    assert "192.168.1.42" not in encoded
    assert "192.168.1.x" in encoded
