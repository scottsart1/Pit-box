from __future__ import annotations

import asyncio
import json
import socket
import sqlite3

import pytest

from pitwall.database import PitWallDatabase
from pitwall.forwarding import DatagramForwarder
from pitwall.network_profiles import NetworkProfileRepository
from pitwall.network_service import NetworkService
from pitwall.networking import AdapterKind, DiscoveryResult, IPv4Interface


def _discovery(address: str) -> DiscoveryResult:
    return DiscoveryResult(
        interfaces=(
            IPv4Interface(
                adapter_id="wifi-guid",
                name="Personal Wi-Fi",
                address=address,
                prefix_length=24,
                is_up=True,
                has_default_gateway=True,
                metric=10,
                kind=AdapterKind.WIFI,
            ),
        ),
        source="test",
    )


def _service(
    repository: NetworkProfileRepository,
    *,
    address: str = "192.168.1.42",
) -> NetworkService:
    async def resolver(host: str, port: int) -> list[str]:
        del port
        return [host]

    async def sender(data: bytes, endpoint: tuple[str, int]) -> int:
        del endpoint
        return len(data)

    return NetworkService(
        asyncio.DatagramProtocol,
        interface_discoverer=lambda: _discovery(address),
        forwarder=DatagramForwarder(resolver=resolver, sender=sender),
        profile_repository=repository,
    )


def _available_udp_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])
    finally:
        probe.close()


@pytest.mark.asyncio
async def test_forward_targets_and_filters_survive_service_restart(tmp_path) -> None:
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    repository = NetworkProfileRepository(database.path)
    first = _service(repository)

    await first.create_forward_target(
        target_id="local_overlay",
        label="Local overlay",
        host="127.0.0.1",
        port=20_778,
        packet_ids=(0, 2, 6),
        forward_unknown_packets=True,
    )
    await first.create_forward_target(
        target_id="public_confirmed",
        label="Explicit remote sink",
        host="8.8.8.8",
        port=20_779,
        enabled=False,
        allow_public=True,
    )

    restarted = _service(repository)
    assert await restarted.load_persisted_profile() is True
    targets = {target.id: target for target in restarted.configured_targets}
    assert targets["local_overlay"].packet_ids == frozenset({0, 2, 6})
    assert targets["local_overlay"].forward_unknown_packets is True
    assert targets["public_confirmed"].enabled is False
    assert targets["public_confirmed"].allow_public is True

    await restarted.update_forward_target("local_overlay", port=20_780)
    third = _service(repository)
    await third.load_persisted_profile()
    assert {
        target.id: target.port for target in third.configured_targets
    }["local_overlay"] == 20_780

    await third.delete_forward_target("public_confirmed")
    fourth = _service(repository)
    await fourth.load_persisted_profile()
    assert [target.id for target in fourth.configured_targets] == [
        "local_overlay"
    ]


@pytest.mark.asyncio
async def test_successful_listener_rebind_survives_restart(tmp_path) -> None:
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    repository = NetworkProfileRepository(database.path)
    first = _service(repository)
    port = _available_udp_port()

    await first.start_listener("127.0.0.1", port)
    await first.stop_listener()

    restarted = _service(repository)
    await restarted.load_persisted_profile()
    listener = restarted.listener_snapshot()
    assert listener.bind_host == "127.0.0.1"
    assert listener.port == port


@pytest.mark.asyncio
async def test_pinned_adapter_keeps_prior_address_for_dhcp_change_warning(
    tmp_path,
) -> None:
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    repository = NetworkProfileRepository(database.path)
    first = _service(repository, address="192.168.1.10")
    await first.set_pinned_adapter("wifi-guid", "192.168.1.10")

    restarted = _service(repository, address="192.168.1.42")
    await restarted.load_persisted_profile()
    snapshot = await restarted.snapshot()

    assert restarted.pinned_adapter_id == "wifi-guid"
    assert snapshot.recommendation.recommended is not None
    assert snapshot.recommendation.recommended.address == "192.168.1.42"
    assert any(
        "changed from 192.168.1.10 to 192.168.1.42" in warning
        for warning in snapshot.warnings
    )


@pytest.mark.asyncio
async def test_network_profile_storage_is_a_non_secret_whitelist(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-enter-profile-storage")
    monkeypatch.setenv("PITWALL_WEB_ACCESS_TOKEN", "also-not-persisted")
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    repository = NetworkProfileRepository(database.path)
    service = _service(repository)

    await service.set_pinned_adapter("wifi-guid", "192.168.1.42")
    await service.create_forward_target(
        target_id="overlay",
        label="Overlay",
        host="127.0.0.1",
        port=20_778,
    )

    with sqlite3.connect(database.path) as connection:
        profile_json = connection.execute(
            "SELECT config_json FROM network_profiles WHERE id = 'default'"
        ).fetchone()[0]
        stored = json.loads(profile_json)
        serialized_rows = " ".join(
            str(value)
            for row in connection.execute(
                "SELECT * FROM network_profiles"
            ).fetchall()
            for value in row
        )
    assert set(stored) == {
        "prior_working_adapter_ids",
        "prior_working_addresses",
        "schema_version",
        "target_options",
    }
    assert "must-not-enter-profile-storage" not in serialized_rows
    assert "also-not-persisted" not in serialized_rows


@pytest.mark.asyncio
async def test_an_unproven_bind_is_never_persisted_over_a_proven_one(tmp_path) -> None:
    """From a real install: a CIDR prefix ("/24") typed as the port.

    The mistyped listener bound successfully (port 24 binds fine), shutdown
    persisted it unconditionally with last_working_at stamped, and every later
    launch faithfully rebound the dead port — three days of "telemetry is not
    connected". A bind that never received a valid F1 packet must not
    overwrite the stored profile.
    """
    from tests.test_network_service_v42 import build_service, packet_bytes

    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    repository = NetworkProfileRepository(database.path)

    # A proven session: bind, receive valid traffic, stop -> persisted.
    service, endpoints, _ = build_service()
    service._profile_repository = repository
    await service.start_listener("0.0.0.0", 20_777)
    endpoints.protocols[-1].datagram_received(packet_bytes(5), ("192.168.1.61", 51_000))
    await service.stop_listener()
    stored = await repository.load("default")
    assert stored is not None and stored.udp_port == 20_777

    # The mistyped session: bind port 24, receive NOTHING, stop.
    second, _, _ = build_service()
    second._profile_repository = repository
    await second.start_listener("0.0.0.0", 24)
    await second.stop_listener()
    stored = await repository.load("default")
    assert stored.udp_port == 20_777, "an unproven bind must not be remembered"


@pytest.mark.asyncio
async def test_a_low_port_is_warned_about_but_respected(tmp_path) -> None:
    """No F1 title sends telemetry to a privileged port, but a user who has
    matched the game to a mistyped port has a WORKING setup — warn, never
    silently rebind."""
    from tests.test_network_service_v42 import build_service

    service, _, _ = build_service()
    await service.start_listener("0.0.0.0", 24)
    snapshot = await service.snapshot()
    assert any("unusual for F1 telemetry" in warning for warning in snapshot.warnings)
    assert snapshot.listener.port == 24, "the working bind is left alone"


@pytest.mark.asyncio
async def test_sparse_telemetry_feed_is_measured_and_named(tmp_path) -> None:
    """Raw captures from two real race nights showed car telemetry ARRIVING
    at 4-12 Hz while the game can send 60 — every arriving packet was stored,
    so "telemetry is stored sporadically" was a feed problem the app never
    surfaced. The rate is measured at the socket and warned about with the
    actual number; a healthy feed stays quiet."""
    from tests.test_network_service_v42 import Clock, build_service, packet_bytes

    clock = Clock()
    service, endpoints, _ = build_service(clock=clock)
    await service.start_listener("0.0.0.0", 20_777)
    protocol = endpoints.protocols[-1]

    # ~5 Hz for 10 simulated seconds.
    for frame in range(50):
        protocol.datagram_received(packet_bytes(frame), ("192.168.1.61", 51_000))
        clock.advance(0.2)
    snapshot = await service.snapshot()
    sparse = [w for w in snapshot.warnings if "arriving at" in w]
    assert sparse and "5 Hz" in sparse[0] and "UDP send rate" in sparse[0]

    # A healthy 30 Hz feed clears the warning.
    for frame in range(50, 350):
        protocol.datagram_received(packet_bytes(frame), ("192.168.1.61", 51_000))
        clock.advance(1 / 30)
    snapshot = await service.snapshot()
    assert not any("arriving at" in w for w in snapshot.warnings)
