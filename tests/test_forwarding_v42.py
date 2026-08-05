from __future__ import annotations

import asyncio
import random
import struct

import pytest

from pitwall.forwarding import (
    DatagramForwarder,
    ForwardTarget,
    ForwardValidationError,
    resolve_forward_targets,
    validate_resolved_targets,
    validate_target_shape,
)
from pitwall.networking import AdapterKind, IPv4Interface, parse_2026_header


def packet_bytes(
    frame: int,
    *,
    packet_id: int = 6,
    packet_format: int = 2026,
    body: bytes = b"\x00\xfforiginal-bytes",
) -> bytes:
    return (
        struct.pack(
            "<HBBBBBQfIIBB",
            packet_format,
            25,
            1,
            0,
            1,
            packet_id,
            99,
            float(frame),
            frame,
            frame,
            0,
            255,
        )
        + body
    )


def target(
    target_id: str,
    host: str,
    port: int,
    **values,
) -> ForwardTarget:
    return ForwardTarget(
        id=target_id,
        label=target_id.replace("_", " ").title(),
        host=host,
        port=port,
        **values,
    )


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_target_rejects_invalid_ports(port: int) -> None:
    with pytest.raises(ForwardValidationError) as caught:
        validate_target_shape(target("bad_port", "127.0.0.1", port))
    assert caught.value.code == "invalid_port"


def test_resolved_validation_rejects_self_loop_and_duplicate() -> None:
    local = IPv4Interface(
        "wifi",
        "Wi-Fi",
        "192.168.1.42",
        kind=AdapterKind.WIFI,
    )
    with pytest.raises(ForwardValidationError) as caught:
        validate_resolved_targets(
            [target("loop", "192.168.1.42", 20777)],
            {"loop": ["192.168.1.42"]},
            listen_endpoints=[("0.0.0.0", 20777)],
            local_interfaces=[local],
        )
    assert caught.value.code == "self_loop"

    with pytest.raises(ForwardValidationError) as caught:
        validate_resolved_targets(
            [
                target("first", "localhost", 20778),
                target("second", "127.0.0.1", 20778),
            ],
            {"first": ["127.0.0.1"], "second": ["127.0.0.1"]},
        )
    assert caught.value.code == "duplicate_destination"


@pytest.mark.parametrize(
    ("address", "code"),
    [
        ("8.8.8.8", "public_confirmation_required"),
        ("255.255.255.255", "broadcast_not_allowed"),
        ("239.1.2.3", "multicast_not_allowed"),
    ],
)
def test_unsafe_destinations_require_explicit_confirmation(
    address: str, code: str
) -> None:
    with pytest.raises(ForwardValidationError) as caught:
        validate_resolved_targets(
            [target("unsafe", address, 20778)], {"unsafe": [address]}
        )
    assert caught.value.code == code


def test_public_and_advanced_destinations_can_be_confirmed() -> None:
    public = target("public", "8.8.8.8", 20778, allow_public=True)
    broadcast = target(
        "broadcast",
        "255.255.255.255",
        20779,
        allow_broadcast_multicast=True,
    )
    resolved = validate_resolved_targets(
        [public, broadcast],
        {"public": ["8.8.8.8"], "broadcast": ["255.255.255.255"]},
    )
    assert [item.endpoint for item in resolved] == [
        ("8.8.8.8", 20778),
        ("255.255.255.255", 20779),
    ]


@pytest.mark.asyncio
async def test_unresolvable_host_is_actionable() -> None:
    async def resolver(host: str, port: int):
        del host, port
        return []

    with pytest.raises(ForwardValidationError) as caught:
        await resolve_forward_targets(
            [target("missing", "does-not-exist.invalid", 20778)],
            resolver=resolver,
        )
    assert caught.value.code == "unresolvable_host"
    assert caught.value.target_id == "missing"


@pytest.mark.asyncio
async def test_forwarder_sends_byte_identical_datagrams_and_filters_packets() -> None:
    resolver_calls: list[tuple[str, int]] = []
    sent: list[tuple[tuple[str, int], bytes]] = []

    async def resolver(host: str, port: int):
        resolver_calls.append((host, port))
        return [host]

    async def sender(data: bytes, endpoint: tuple[str, int]):
        sent.append((endpoint, data))
        return len(data)

    forwarder = DatagramForwarder(queue_size=8, resolver=resolver, sender=sender)
    await forwarder.reconfigure(
        [
            target("all", "127.0.0.1", 20778),
            target(
                "telemetry_only",
                "127.0.0.1",
                20779,
                packet_ids=frozenset({6}),
            ),
        ]
    )
    assert len(resolver_calls) == 2
    await forwarder.start()
    telemetry = packet_bytes(1, packet_id=6)
    session = packet_bytes(2, packet_id=1)
    assert forwarder.submit(telemetry) is True
    assert forwarder.submit(session) is True
    await asyncio.wait_for(forwarder.queue.join(), timeout=1.0)
    await forwarder.close()

    assert sent == [
        (("127.0.0.1", 20778), telemetry),
        (("127.0.0.1", 20779), telemetry),
        (("127.0.0.1", 20778), session),
    ]
    assert len(resolver_calls) == 2  # submit/worker never resolve DNS
    snapshot = forwarder.snapshot()
    by_id = {item.target_id: item for item in snapshot.targets}
    assert by_id["all"].sent_packets == 2
    assert by_id["all"].sent_bytes == len(telemetry) + len(session)
    assert by_id["telemetry_only"].sent_packets == 1
    assert by_id["telemetry_only"].filtered_packets == 1


@pytest.mark.asyncio
async def test_ten_thousand_datagrams_remain_byte_identical_and_ordered() -> None:
    rng = random.Random(42)
    sent: dict[int, list[bytes]] = {20778: [], 20779: []}

    async def resolver(host: str, port: int):
        del port
        return [host]

    async def sender(data: bytes, endpoint: tuple[str, int]):
        sent[endpoint[1]].append(data)
        return len(data)

    forwarder = DatagramForwarder(
        queue_size=10_001,
        resolver=resolver,
        sender=sender,
    )
    await forwarder.reconfigure(
        [
            target("sink_a", "127.0.0.1", 20778),
            target("sink_b", "127.0.0.1", 20779),
        ]
    )
    await forwarder.start()
    datagrams = [
        packet_bytes(frame, body=rng.randbytes(rng.randrange(0, 65)))
        for frame in range(10_000)
    ]
    assert all(forwarder.submit(datagram) for datagram in datagrams)
    await asyncio.wait_for(forwarder.queue.join(), timeout=5.0)
    await forwarder.close()

    assert sent[20778] == datagrams
    assert sent[20779] == datagrams
    assert forwarder.snapshot().queue_drops == 0


@pytest.mark.asyncio
async def test_unknown_format_requires_per_target_opt_in() -> None:
    sent: list[tuple[int, bytes]] = []

    async def resolver(host: str, port: int):
        del port
        return [host]

    async def sender(data: bytes, endpoint: tuple[str, int]):
        sent.append((endpoint[1], data))
        return len(data)

    forwarder = DatagramForwarder(resolver=resolver, sender=sender)
    await forwarder.reconfigure(
        [
            target("normal", "127.0.0.1", 20778),
            target(
                "unknown_ok",
                "127.0.0.1",
                20779,
                forward_unknown_packets=True,
            ),
        ]
    )
    await forwarder.start()
    unknown = packet_bytes(1, packet_format=2025)
    assert forwarder.submit(unknown) is True
    assert forwarder.submit(b"too short") is False
    await asyncio.wait_for(forwarder.queue.join(), timeout=1.0)
    await forwarder.close()
    assert sent == [(20779, unknown)]
    assert forwarder.snapshot().rejected_datagrams == 1


@pytest.mark.asyncio
async def test_one_target_failure_does_not_stop_other_targets() -> None:
    sent: list[int] = []

    async def resolver(host: str, port: int):
        del port
        return [host]

    async def sender(data: bytes, endpoint: tuple[str, int]):
        del data
        if endpoint[1] == 20779:
            raise OSError("synthetic sink failure")
        sent.append(endpoint[1])
        return 1

    forwarder = DatagramForwarder(resolver=resolver, sender=sender)
    await forwarder.reconfigure(
        [
            target("healthy", "127.0.0.1", 20778),
            target("broken", "127.0.0.1", 20779),
        ]
    )
    await forwarder.start()
    assert forwarder.submit(packet_bytes(1)) is True
    assert forwarder.submit(packet_bytes(2)) is True
    await asyncio.wait_for(forwarder.queue.join(), timeout=1.0)
    await forwarder.close()
    assert sent == [20778, 20778]
    by_id = {item.target_id: item for item in forwarder.snapshot().targets}
    assert by_id["healthy"].sent_packets == 2
    assert by_id["broken"].socket_errors == 2
    assert "synthetic sink failure" in str(by_id["broken"].last_error)


@pytest.mark.asyncio
async def test_queue_overload_drops_oldest_without_blocking_submit() -> None:
    first_send_started = asyncio.Event()
    release_sender = asyncio.Event()
    sent_frames: list[int] = []

    async def resolver(host: str, port: int):
        del port
        return [host]

    async def sender(data: bytes, endpoint: tuple[str, int]):
        del endpoint
        sent_frames.append(parse_2026_header(data).frame_identifier)
        if len(sent_frames) == 1:
            first_send_started.set()
            await release_sender.wait()
        return len(data)

    forwarder = DatagramForwarder(queue_size=2, resolver=resolver, sender=sender)
    await forwarder.reconfigure([target("sink", "127.0.0.1", 20778)])
    await forwarder.start()
    assert forwarder.submit(packet_bytes(1)) is True
    await asyncio.wait_for(first_send_started.wait(), timeout=1.0)
    assert forwarder.submit(packet_bytes(2)) is True
    assert forwarder.submit(packet_bytes(3)) is True
    assert forwarder.submit(packet_bytes(4)) is True
    release_sender.set()
    await asyncio.wait_for(forwarder.queue.join(), timeout=1.0)
    await forwarder.close()

    assert sent_frames == [1, 3, 4]
    snapshot = forwarder.snapshot()
    assert snapshot.queue_drops == 1
    assert snapshot.queue_high_water == 2
    assert snapshot.targets[0].queue_drops == 1


@pytest.mark.asyncio
async def test_reconfigure_is_atomic_and_close_is_idempotent() -> None:
    sent_ports: list[int] = []

    async def resolver(host: str, port: int):
        del port
        return [host]

    async def sender(data: bytes, endpoint: tuple[str, int]):
        del data
        sent_ports.append(endpoint[1])
        return 1

    forwarder = DatagramForwarder(resolver=resolver, sender=sender)
    await forwarder.reconfigure([target("old", "127.0.0.1", 20778)])
    await forwarder.start()
    await forwarder.reconfigure([target("new", "127.0.0.1", 20779)])
    assert forwarder.submit(packet_bytes(10)) is True
    await asyncio.wait_for(forwarder.queue.join(), timeout=1.0)
    await forwarder.close()
    await forwarder.close()
    assert sent_ports == [20779]
    assert forwarder.snapshot().running is False
