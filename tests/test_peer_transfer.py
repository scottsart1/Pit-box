from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from pitwall.api.transfers import create_transfer_router
from pitwall.peer_transfer import PeerTransferService, TransferError
from pitwall.networking import AdapterKind, DiscoveryResult, IPv4Interface
from test_history_transfer import _installation, _session, _count


def service(history, root):
    return PeerTransferService(history, root, allow_loopback=True, network_provider=lambda: [])


async def finished(peer, job):
    for _ in range(300):
        result = peer.job(job["id"])
        if result["status"] in {"completed", "failed", "conflict"}:
            return result
        await asyncio.sleep(.05)
    raise AssertionError("transfer did not finish")


@pytest.mark.asyncio
async def test_real_https_pair_transfer_restart_duplicate_and_revoke(tmp_path):
    source, outgoing = await _installation(tmp_path / "source")
    dest, incoming = await _installation(tmp_path / "dest")
    key = await _session(source, 101, trace=True)
    await _session(source, 102, complete=False)
    a, b = service(outgoing, source.path.parent), service(incoming, dest.path.parent)
    try:
        a.start(port=0); b.start(port=0)
        invitation = a.invite()
        assert "<svg" in invitation["qr_svg"] and invitation["pairing_uri"].startswith("pitwall://pair?")
        b.pair(invitation["invitation"])
        with pytest.raises(TransferError):
            b.pair(invitation["invitation"])
        assert [s["id"] for s in b.peer_sessions(a.device_id)["sessions"]] == [key]
        result = await finished(b, b.pull(a.device_id, [key]))
        assert result["status"] == "completed", result
        assert result["result"]["imported_sessions"] == 1
        assert _count(dest, "recorded_laps") == 1
        assert b.device_id in a._peers
        port = b.status()["port"]
        b.close()
        b = service(incoming, dest.path.parent)
        b.start(port=port)
        assert b.job(result["id"])["status"] == "completed"
        repeated = await finished(b, b.pull(a.device_id, [key]))
        assert repeated["status"] == "completed", repeated
        assert repeated["result"]["imported_rows"] == 0
        a.revoke(b.device_id)
        with pytest.raises(TransferError) as error:
            b.peer_sessions(a.device_id)
        assert error.value.status == 401
    finally:
        a.close(); b.close()


@pytest.mark.asyncio
async def test_interrupted_download_resumes_exact_bytes_after_both_apps_restart(tmp_path):
    source, outgoing = await _installation(tmp_path / "source")
    dest, incoming = await _installation(tmp_path / "dest")
    key = await _session(source, 101, trace=True)
    a, b = service(outgoing, source.path.parent), service(incoming, dest.path.parent)
    try:
        a.start(port=0); b.start(port=0)
        b.pair(a.invite()["invitation"])
        def interrupted(peer, bundle, job):
            remote = Path(a._bundles[bundle["id"]]["path"])
            Path(job["partial"]).write_bytes(remote.read_bytes()[:200])
            raise TransferError("simulated lost connection")
        b._download = interrupted
        result = await finished(b, b.pull(a.device_id, [key]))
        assert result["status"] == "failed" and _count(dest, "recorded_sessions") == 0
        a_port, b_port = a.status()["port"], b.status()["port"]
        a.close(); b.close()
        a, b = service(outgoing, source.path.parent), service(incoming, dest.path.parent)
        a.start(port=a_port); b.start(port=b_port)
        assert Path(b._jobs[result["id"]]["partial"]).stat().st_size == 200
        original = b._connection
        ranges = []
        def connection(peer):
            connection = original(peer)
            request = connection.request
            def record(*args, **kwargs):
                ranges.append(kwargs.get("headers", {}).get("Range"))
                return request(*args, **kwargs)
            connection.request = record
            return connection
        b._connection = connection
        resumed = await finished(b, b.retry(result["id"]))
        assert resumed["status"] == "completed", resumed
        assert "bytes=200-" in ranges
        assert _count(dest, "recorded_sessions") == 1
    finally:
        a.close(); b.close()


@pytest.mark.asyncio
async def test_wrong_certificate_is_rejected_before_invitation_is_consumed(tmp_path):
    database, history = await _installation(tmp_path / "data")
    a, b = service(history, tmp_path / "a"), service(history, tmp_path / "b")
    try:
        a.start(port=0); b.start(port=0)
        invitation = a.invite()["invitation"]
        data = invitation.split("://")[1]
        payload = json.loads(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)))
        payload["certificate_sha256"] = "0" * 64
        wrong = "pitwall-pair://" + base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
        with pytest.raises(TransferError) as error:
            b.pair(wrong)
        assert error.value.code == "certificate_mismatch"
        assert a._invite and not a._peers
        b.pair(invitation)
    finally:
        a.close(); b.close()


@pytest.mark.asyncio
async def test_recording_import_block_and_conflicts_have_distinct_terminal_state(tmp_path):
    source, outgoing = await _installation(tmp_path / "source")
    dest, incoming = await _installation(tmp_path / "dest")
    key = await _session(source, 101)
    a, b = service(outgoing, source.path.parent), service(incoming, dest.path.parent)
    try:
        a.start(port=0); b.start(port=0); b.pair(a.invite()["invitation"])
        b._is_recording = lambda: True
        with pytest.raises(TransferError) as error:
            b.pull(a.device_id, [key])
        assert error.value.code == "recording_active"
        b._is_recording = lambda: False
        incoming.import_bundle = lambda path: {"status": "conflict", "imported_sessions": 0, "conflicts": []}
        result = await finished(b, b.pull(a.device_id, [key]))
        assert result["status"] == "conflict"
    finally:
        a.close(); b.close()


@pytest.mark.parametrize("headers,client,expected", [
    ({}, "127.0.0.1", 200),
    ({"Origin": "http://attacker.example"}, "127.0.0.1", 403),
    ({"Host": "attacker.example"}, "127.0.0.1", 403),
    ({"Sec-Fetch-Site": "cross-site"}, "127.0.0.1", 403),
    ({}, "192.168.1.22", 403),
])
def test_management_api_rejects_remote_and_cross_origin_requests(tmp_path, headers, client, expected):
    peer = service(None, tmp_path)
    app = FastAPI(); app.include_router(create_transfer_router(peer))
    try:
        with TestClient(app, base_url="http://127.0.0.1", client=(client, 12000)) as http:
            response = http.get("/api/v1/transfers/status", headers=headers)
            assert response.status_code == expected
            if expected == 200:
                assert response.headers["cache-control"] == "no-store"
                assert "incoming_token" not in response.text
    finally:
        peer.close()


def test_corrupt_pair_store_does_not_break_the_driving_app(tmp_path):
    root = tmp_path / "peer-transfer"; root.mkdir()
    (root / "peers.json").write_text("broken")
    peer = service(None, tmp_path)
    try:
        assert peer.status()["error"]
        with pytest.raises(TransferError): peer.start(port=0)
        assert (root / "peers.json").read_text() == "broken"
    finally: peer.close()


def test_changing_client_addresses_cannot_grow_rate_limit_state_unbounded(tmp_path):
    peer = service(None, tmp_path)
    try:
        for index in range(256): peer._rate_limit(f"pair:{index}", 8)
        for index in range(256, 300):
            with pytest.raises(TransferError): peer._rate_limit(f"pair:{index}", 8)
        assert len(peer._rates) == 256
    finally: peer.close()


def test_route_only_fallback_cannot_start_sharing_before_virtual_adapter_is_identified(tmp_path):
    address = "192.168.50.4"
    discovered = DiscoveryResult((IPv4Interface(
        adapter_id="fallback:" + address, name="Detected IPv4 interface",
        address=address, prefix_length=24, is_up=True, kind=AdapterKind.UNKNOWN,
    ),), "stdlib-fallback", ("Windows interface discovery exceeded 20.0s.",))
    peer = PeerTransferService(None, tmp_path, network_provider=lambda: discovered)
    try:
        with pytest.raises(TransferError) as error:
            peer.start()
        assert error.value.code == "no_local_network"
        assert not peer.status()["running"]
        assert not (peer.root / "identity.pem").exists(), "Unverified routes must be rejected before opening TLS"
        discovered = DiscoveryResult((IPv4Interface(
            adapter_id="windows:7", name="Ethernet", description="Hyper-V Virtual Ethernet Adapter",
            address=address, prefix_length=24, is_up=True, kind=AdapterKind.VIRTUAL,
        ),), "windows")
        with pytest.raises(TransferError) as error:
            peer.start()
        assert error.value.code == "no_local_network"
        assert not peer._allowed_address("192.168.50.5")
    finally:
        peer.close()


@pytest.mark.parametrize("adapter_id,kind", [
    ("android:wlan0", AdapterKind.WIFI), ("windows:7", AdapterKind.ETHERNET),
])
def test_real_interface_provenance_retained_even_with_diagnostic_fallback_wrapper(tmp_path, adapter_id, kind):
    interfaces = (
        IPv4Interface(adapter_id=adapter_id, name="Local network", address="192.168.50.4",
                      prefix_length=24, is_up=True, kind=kind),
        IPv4Interface(adapter_id="fallback:172.19.1.2", name="Detected IPv4 interface",
                      address="172.19.1.2", prefix_length=24, is_up=True, kind=AdapterKind.UNKNOWN),
    )
    peer = PeerTransferService(None, tmp_path,
        network_provider=lambda: DiscoveryResult(interfaces, "stdlib-fallback"))
    try:
        peer._refresh_networks()
        assert peer._addresses == ["192.168.50.4"]
        assert peer._allowed_address("192.168.50.7")
        assert not peer._allowed_address("172.19.1.7")
    finally:
        peer.close()
