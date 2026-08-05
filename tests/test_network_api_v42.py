from __future__ import annotations

import asyncio
import errno
import struct

import httpx
import pytest
from fastapi import FastAPI

from pitwall.api.network import create_network_router
from pitwall.forwarding import DatagramForwarder
from pitwall.network_service import NetworkService
from pitwall.networking import AdapterKind, DiscoveryResult, IPv4Interface


def packet_bytes(frame: int = 1) -> bytes:
    return struct.pack(
        "<HBBBBBQfIIBB",
        2026,
        26,
        1,
        0,
        1,
        6,
        1234,
        2.5,
        frame,
        frame,
        0,
        255,
    )


class FakeTransport:
    def __init__(
        self,
        protocol: asyncio.DatagramProtocol,
        host: str,
        port: int,
    ) -> None:
        self.protocol = protocol
        self.endpoint = (host, port)
        self.closed = False

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.protocol.connection_lost(None)

    def is_closing(self) -> bool:
        return self.closed

    def get_extra_info(self, name: str, default=None):
        return self.endpoint if name == "sockname" else default


class EndpointCreator:
    def __init__(self, conflicting_port: int | None = None) -> None:
        self.conflicting_port = conflicting_port
        self.protocols: list[asyncio.DatagramProtocol] = []

    async def __call__(self, factory, host: str, port: int):
        if port == self.conflicting_port:
            raise OSError(errno.EADDRINUSE, "busy")
        protocol = factory()
        transport = FakeTransport(protocol, host, port)
        protocol.connection_made(transport)  # type: ignore[arg-type]
        self.protocols.append(protocol)
        return transport, protocol


def make_service(
    *, conflicting_port: int | None = None
) -> tuple[NetworkService, EndpointCreator]:
    endpoints = EndpointCreator(conflicting_port)

    async def resolver(host: str, port: int):
        del port
        return [host]

    async def sender(data: bytes, endpoint: tuple[str, int]):
        del endpoint
        return len(data)

    discovery = DiscoveryResult(
        (
            IPv4Interface(
                "wifi-guid",
                "Wi-Fi",
                "192.168.10.42",
                prefix_length=24,
                has_default_gateway=True,
                metric=5,
                kind=AdapterKind.WIFI,
            ),
        ),
        "fixture",
    )
    service = NetworkService(
        asyncio.DatagramProtocol,
        endpoint_creator=endpoints,
        interface_discoverer=lambda: discovery,
        forwarder=DatagramForwarder(resolver=resolver, sender=sender),
        bind_probe=lambda host, port: None,
    )
    return service, endpoints


def make_app(service: NetworkService) -> FastAPI:
    app = FastAPI()
    app.include_router(create_network_router(service))
    return app


@pytest.mark.asyncio
async def test_interfaces_flag_a_fallback_answer_as_provisional() -> None:
    """A client must be able to tell a real adapter list from the fallback.

    The socket-derived fallback cannot report adapter kind, gateway or metric,
    so the Connection Center presents it as provisional and asks again instead
    of stranding the panel on it.
    """
    endpoints = EndpointCreator(None)

    async def resolver(host: str, port: int):
        del port
        return [host]

    fallback = DiscoveryResult(
        (IPv4Interface("fallback:10.1.2.3", "Detected IPv4 interface", "10.1.2.3"),),
        "stdlib-fallback",
        ("Windows interface discovery exceeded 2.0s.",),
    )
    service = NetworkService(
        asyncio.DatagramProtocol,
        endpoint_creator=endpoints,
        interface_discoverer=lambda: fallback,
        forwarder=DatagramForwarder(resolver=resolver),
        bind_probe=lambda host, port: None,
    )
    transport = httpx.ASGITransport(app=make_app(service))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        payload = (await client.get("/api/v1/network/interfaces")).json()

    assert payload["discovery_authoritative"] is False
    assert any("discovery" in warning for warning in payload["warnings"])


@pytest.mark.asyncio
async def test_interfaces_listener_status_and_packet_projection() -> None:
    service, endpoints = make_service()
    transport = httpx.ASGITransport(app=make_app(service))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        interfaces = await client.get("/api/v1/network/interfaces")
        assert interfaces.status_code == 200
        assert interfaces.json()["recommended_ipv4"] == "192.168.10.42"
        assert interfaces.json()["interfaces"][0]["reasons"]
        assert interfaces.json()["discovery_authoritative"] is True

        initial = await client.get("/api/v1/network/status")
        assert initial.json()["listener"]["state"] == "off"

        started = await client.post(
            "/api/v1/network/listener/start",
            json={"bind_host": "0.0.0.0", "port": 20_777},
        )
        assert started.status_code == 200
        assert started.json()["state"] == "listening"

        endpoints.protocols[-1].datagram_received(
            packet_bytes(), ("192.168.10.61", 54_022)
        )
        status_response = await client.get("/api/v1/network/status")
        body = status_response.json()
        assert body["listener"]["state"] == "receiving"
        assert body["source"] == {"ip": "192.168.10.61", "port": 54_022}
        assert body["game"]["session_uid"] == "1234"
        assert body["packets"][0]["packet_name"] == "car_telemetry"
        assert body["packets"][0]["valid"] == 1

        stopped = await client.post("/api/v1/network/listener/stop")
        assert stopped.status_code == 200
        assert stopped.json()["state"] == "off"


@pytest.mark.asyncio
async def test_forwarder_crud_and_actionable_validation_errors() -> None:
    service, _ = make_service()
    transport = httpx.ASGITransport(app=make_app(service))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        rejected = await client.post(
            "/api/v1/network/forwarders",
            json={
                "id": "public_sink",
                "label": "Public sink",
                "host": "8.8.8.8",
                "port": 20_778,
            },
        )
        assert rejected.status_code == 422
        assert rejected.json()["detail"]["code"] == "public_confirmation_required"

        created = await client.post(
            "/api/v1/network/forwarders",
            json={
                "id": "local_overlay",
                "label": "Local overlay",
                "host": "127.0.0.1",
                "port": 20_778,
                "packet_ids": [6, 6],
            },
        )
        assert created.status_code == 201
        assert created.json()["packet_ids"] == [6]
        assert created.json()["resolved_address"] == "127.0.0.1"

        duplicate = await client.post(
            "/api/v1/network/forwarders",
            json={
                "id": "local_overlay",
                "label": "Duplicate",
                "host": "127.0.0.1",
                "port": 20_779,
            },
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["detail"]["code"] == "forward_target_exists"

        patched = await client.patch(
            "/api/v1/network/forwarders/local_overlay",
            json={"label": "Renamed overlay", "enabled": False},
        )
        assert patched.status_code == 200
        assert patched.json()["label"] == "Renamed overlay"
        assert patched.json()["enabled"] is False

        listed = await client.get("/api/v1/network/forwarders")
        assert [item["id"] for item in listed.json()] == ["local_overlay"]

        deleted = await client.delete("/api/v1/network/forwarders/local_overlay")
        assert deleted.status_code == 204
        missing = await client.delete("/api/v1/network/forwarders/local_overlay")
        assert missing.status_code == 404
        assert missing.json()["detail"]["code"] == "forward_target_not_found"


@pytest.mark.asyncio
async def test_public_target_requires_fresh_confirmation_when_host_changes() -> None:
    service, _ = make_service()
    transport = httpx.ASGITransport(app=make_app(service))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/v1/network/forwarders",
            json={
                "id": "confirmed",
                "label": "Confirmed public",
                "host": "8.8.8.8",
                "port": 20_778,
                "confirm_public_address": True,
            },
        )
        assert created.status_code == 201

        rejected = await client.patch(
            "/api/v1/network/forwarders/confirmed",
            json={"host": "1.1.1.1"},
        )
        assert rejected.status_code == 422
        assert rejected.json()["detail"]["code"] == "public_confirmation_required"
        assert service.configured_targets[0].host == "8.8.8.8"

        accepted = await client.patch(
            "/api/v1/network/forwarders/confirmed",
            json={"host": "1.1.1.1", "confirm_public_address": True},
        )
        assert accepted.status_code == 200
        assert accepted.json()["host"] == "1.1.1.1"


@pytest.mark.asyncio
async def test_bind_conflict_maps_to_actionable_http_409() -> None:
    service, _ = make_service(conflicting_port=20_777)
    transport = httpx.ASGITransport(app=make_app(service))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/network/listener/start",
            json={"bind_host": "0.0.0.0", "port": 20_777},
        )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "bind_conflict",
        "message": (
            "UDP 0.0.0.0:20777 is already in use. Stop the other telemetry "
            "receiver or choose a different UDP port."
        ),
        "bind_host": "0.0.0.0",
        "port": 20_777,
    }


@pytest.mark.asyncio
async def test_diagnose_returns_bounded_checks_and_actions() -> None:
    service, _ = make_service()
    transport = httpx.ASGITransport(app=make_app(service))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/network/diagnose")

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == 1
    assert {item["id"] for item in body["checks"]} == {
        "console_interface",
        "udp_bind",
        "telemetry_reception",
        "forwarding",
    }
    assert isinstance(body["actions"], list)
