from __future__ import annotations

import asyncio
import base64
import json
import io
import threading
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
        incoming.import_bundle = lambda path, **kwargs: {"status": "conflict", "imported_sessions": 0, "conflicts": []}
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


@pytest.mark.asyncio
async def test_preparing_progress_visible_on_both_devices_and_sent_is_not_imported(tmp_path):
    source, outgoing = await _installation(tmp_path / "source")
    dest, incoming = await _installation(tmp_path / "dest")
    key = await _session(source, 301, trace=True)
    a, b = service(outgoing, source.path.parent), service(incoming, dest.path.parent)
    release = threading.Event()
    original_export = outgoing.export_bundle
    original_import = incoming.import_bundle
    importing = threading.Event()
    finish_import = threading.Event()

    def paused_export(ids, path, *, progress):
        def observed(event):
            progress(event)
            if event["phase"] == "packing" and event.get("done") == 0:
                assert release.wait(10), "test did not release export"
        return original_export(ids, path, progress=observed)

    def paused_import(path, *, progress):
        importing.set()
        assert finish_import.wait(10), "test did not release import"
        return original_import(path, progress=progress)

    outgoing.export_bundle, incoming.import_bundle = paused_export, paused_import
    try:
        a.start(port=0); b.start(port=0); b.pair(a.invite()["invitation"])
        job = b.pull(a.device_id, [key])
        for _ in range(160):
            received = b.job(job["id"])
            if (received.get("progress") or {}).get("phase") == "packing":
                break
            await asyncio.sleep(.05)
        assert received["progress"] == {"phase": "packing", "done": 0, "total": 3, "unit": "files"}
        sending = a.status()["exports"][0]
        assert sending["status"] == "preparing" and sending["progress"] == received["progress"]
        assert not {"path", "incoming_token", "outgoing_token", "partial"} & sending.keys()
        release.set()
        for _ in range(160):
            if importing.is_set():
                break
            await asyncio.sleep(.05)
        assert importing.is_set()
        sending = a.status()["exports"][0]
        assert sending["status"] == "sent"
        assert sending["bytes_sent"] == sending["total_bytes"] > 0
        assert b.job(job["id"])["status"] == "importing"
        assert _count(dest, "recorded_sessions") == 0
        finish_import.set()
        assert (await finished(b, job))["status"] == "completed"
        assert a.status()["exports"][0]["status"] == "sent", "Socket writes do not acknowledge import"
    finally:
        release.set(); finish_import.set()
        a.close(); b.close()


@pytest.mark.asyncio
async def test_peer_without_optional_progress_fields_still_transfers(tmp_path):
    source, outgoing = await _installation(tmp_path / "source")
    dest, incoming = await _installation(tmp_path / "dest")
    key = await _session(source, 401)
    a, b = service(outgoing, source.path.parent), service(incoming, dest.path.parent)
    request = b._request
    def old_peer(*args, **kwargs):
        result = request(*args, **kwargs)
        result.pop("progress", None)
        return result
    b._request = old_peer
    try:
        a.start(port=0); b.start(port=0); b.pair(a.invite()["invitation"])
        assert (await finished(b, b.pull(a.device_id, [key])))["status"] == "completed"
    finally:
        a.close(); b.close()


def test_sender_byte_progress_freezes_on_interruption_and_resumes_from_range(tmp_path, monkeypatch):
    from pitwall import peer_transfer
    monkeypatch.setattr(peer_transfer, "CHUNK_SIZE", 4)
    peer = service(None, tmp_path)
    path = peer.cache / ("export-" + "a" * 32 + ".pitbox")
    path.write_bytes(b"0123456789")
    bundle = {"id": "a" * 32, "peer_id": "b" * 32, "session_ids": ["test"],
              "created_at": peer._clock(), "status": "ready", "size": 10, "sha256": "0" * 64, "path": str(path)}
    peer._bundles[bundle["id"]] = bundle
    peer._authenticate = lambda value: {}
    class BrokenStream(io.BytesIO):
        def write(self, data):
            if self.tell():
                raise OSError("connection lost")
            return super().write(data)
    handler = peer_transfer._TransferHandler.__new__(peer_transfer._TransferHandler)
    handler.server = type("Server", (), {"service": peer})()
    handler.headers = {"Range": "bytes=3-"}
    handler.send_response = handler.send_header = lambda *args: None
    handler.end_headers = lambda: None
    handler.wfile = BrokenStream()
    try:
        with pytest.raises(OSError):
            handler._content(dict(bundle))
        assert peer.status()["exports"][0]["status"] == "interrupted"
        assert peer.status()["exports"][0]["bytes_sent"] == 7
        handler.headers["Range"] = "bytes=7-"
        handler.wfile = io.BytesIO()
        handler._content(dict(bundle))
        assert handler.wfile.getvalue() == b"789"
        assert peer.status()["exports"][0]["status"] == "sent"
        assert peer.status()["exports"][0]["bytes_sent"] == 10
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
