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
