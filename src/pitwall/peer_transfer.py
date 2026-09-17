"""Opt-in, certificate-pinned LAN history exchange.

The listener deliberately has no routes to the application dashboard, credentials,
or settings.  A copied invitation authorizes one pairing for five minutes; a
separate random bearer token authorizes each direction afterwards.  The client
checks the peer certificate *before* sending an HTTP request or any secret.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import sys
import threading
import time
import uuid
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any, Callable
from urllib.parse import quote, urlsplit

from .transfer_progress import ProgressCallback, TransferProgress, public_progress

PROTOCOL_VERSION = 1
DEFAULT_PORT = 20778
INVITATION_TTL = 300
BUNDLE_TTL = 3600
MAX_JSON = 1024 * 1024
MAX_REQUEST = 64 * 1024
MAX_BUNDLE = 8 * 1024**3
CHUNK_SIZE = 256 * 1024
_RFC1918 = tuple(ipaddress.ip_network(value) for value in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
))


class TransferError(RuntimeError):
    def __init__(self, message: str, code: str = "transfer_error", status: int = 422):
        super().__init__(message)
        self.code = code
        self.status = status


def _sha256(path: Path, progress: ProgressCallback | None = None) -> str:
    digest = hashlib.sha256()
    report = TransferProgress(progress)
    total, done = path.stat().st_size, 0
    report("checksum", done, total, "bytes")
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(chunk)
            done += len(chunk)
            report("checksum", done, total, "bytes")
    return digest.hexdigest()


def _private_write(path: Path, content: bytes) -> None:
    """Atomically replace a private file without an initially world-readable window."""
    temporary = path.with_name(path.name + ".tmp-" + secrets.token_hex(6))
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _name(value: Any) -> str:
    return str(value or "Your Pit Box").strip()[:80] or "Your Pit Box"


def _valid_secret(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9_-]{40,128}", value))


def _valid_id(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[a-f0-9]{32}", value))


def _valid_pin(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[a-f0-9]{64}", value))


class _PinnedConnection(http.client.HTTPSConnection):
    """No proxy lookup, DNS, redirects, or unverified HTTP requests."""

    def __init__(self, host: str, port: int, pin: str, source_host: str | None = None):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        super().__init__(host, port, timeout=20, context=context)
        self.pin = pin
        self.source_host = source_host

    def connect(self) -> None:
        # Bind only this local-transfer socket when Android's default route is
        # cellular but the peer lives on a Wi-Fi network without internet.
        raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw.settimeout(self.timeout)
        try:
            bridge = sys.modules.get("pitbox_android")
            bind = getattr(bridge, "bind_lan_socket", None)
            if callable(bind):
                bind(raw, self.host)
            if self.source_host:
                raw.bind((self.source_host, 0))
            raw.connect((self.host, self.port))
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise
        actual = hashlib.sha256(self.sock.getpeercert(binary_form=True)).hexdigest()
        if not hmac.compare_digest(actual, self.pin):
            self.close()
            raise TransferError(
                "The peer certificate has changed. Re-pair the devices after checking the invitation.",
                "certificate_mismatch", 409,
            )


class _TransferServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    block_on_close = False
    request_queue_size = 4

    def __init__(self, address: tuple[str, int], service: "PeerTransferService", context: ssl.SSLContext):
        self.service = service
        self.context = context
        self.slots = threading.BoundedSemaphore(4)
        super().__init__(address, _TransferHandler)

    def verify_request(self, request: socket.socket, client_address: tuple[str, int]) -> bool:
        return self.service._allowed_address(client_address[0])

    def process_request(self, request: socket.socket, client_address: tuple[str, int]) -> None:
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request: socket.socket, client_address: tuple[str, int]) -> None:
        try:
            request.settimeout(15)
            # Handshake in the bounded worker, never in the accept loop.
            with self.context.wrap_socket(request, server_side=True) as secured:
                super().process_request_thread(secured, client_address)
        except (OSError, ssl.SSLError):
            request.close()
        finally:
            self.slots.release()

    def handle_error(self, request: socket.socket, client_address: tuple[str, int]) -> None:
        # Do not print peer tokens or request contents through traceback logging.
        pass


class _TransferHandler(BaseHTTPRequestHandler):
    server_version = "PitWallTransfer/1"

    def log_message(self, format: str, *args: Any) -> None:
        pass

    @property
    def service(self) -> "PeerTransferService":
        return self.server.service

    def _json(self, status: int, payload: Any) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode()
        if len(encoded) > MAX_JSON:
            status, encoded = 413, b'{"error":"Response is too large; select fewer sessions."}'
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)
        self.close_connection = True

    def _body(self) -> dict[str, Any]:
        if self.headers.get("Transfer-Encoding"):
            raise TransferError("Chunked JSON requests are not supported.", "invalid_request")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise TransferError("Invalid content length.", "invalid_request") from exc
        if not 0 < length <= MAX_REQUEST:
            raise TransferError("Request exceeds the size limit.", "request_too_large", 413)
        try:
            body = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError) as exc:
            raise TransferError("Invalid JSON.", "invalid_request") from exc
        if not isinstance(body, dict):
            raise TransferError("Expected a JSON object.", "invalid_request")
        return body

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_GET(self) -> None:
        self._dispatch("GET")

    def _dispatch(self, method: str) -> None:
        try:
            if not self.service._allowed_address(self.client_address[0]):
                raise TransferError("Only devices on the local network may connect.", "local_network_only", 403)
            # Browser-origin requests are never part of the native peer protocol.
            if self.headers.get("Origin"):
                raise TransferError("Browser requests are not accepted.", "browser_denied", 403)
            if self.path == "/v1/pair" and method == "POST":
                self.service._rate_limit("pair:" + self.client_address[0], 8)
                self.service._rate_limit("pair:global", 24)
                self._json(200, self.service._accept_pair(self._body(), self.client_address[0]))
                return
            peer = self.service._authenticate(self.headers.get("Authorization", ""))
            self.service._rate_limit("peer:" + peer["id"], 180)
            if self.path == "/v1/sessions" and method == "GET":
                self._json(200, {"sessions": self.service._sessions()})
                return
            if self.path == "/v1/bundles" and method == "POST":
                body = self._body()
                self._json(202, self.service._prepare_bundle(peer["id"], body.get("session_ids")))
                return
            match = re.fullmatch(r"/v1/bundles/([a-f0-9]{32})(/content)?", self.path)
            if match and method == "GET":
                bundle = self.service._get_bundle(peer["id"], match[1])
                if match[2]:
                    self._content(bundle)
                else:
                    self._json(200, self.service._public_bundle(bundle))
                return
            raise TransferError("Route not found.", "not_found", 404)
        except TransferError as exc:
            self._json(exc.status, {"error": str(exc), "code": exc.code})
        except (OSError, ConnectionError):
            self.close_connection = True
        except Exception:
            self._json(500, {"error": "The peer could not complete this request.", "code": "peer_error"})

    def _content(self, bundle: dict[str, Any]) -> None:
        if bundle["status"] != "ready":
            raise TransferError("Bundle is not ready.", "bundle_not_ready", 409)
        path = Path(bundle["path"])
        size = bundle["size"]
        start = 0
        range_value = self.headers.get("Range")
        if range_value:
            match = re.fullmatch(r"bytes=(\d+)-", range_value)
            if not match or int(match[1]) >= size:
                raise TransferError("Invalid download range.", "invalid_range", 416)
            start = int(match[1])
        self.send_response(206 if range_value else 200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(size - start))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("ETag", '"' + bundle["sha256"] + '"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        if range_value:
            self.send_header("Content-Range", f"bytes {start}-{size - 1}/{size}")
        self.end_headers()
        sent = start
        self.service._delivery_progress(bundle["id"], "sending", sent)
        try:
            with path.open("rb") as stream:
                stream.seek(start)
                while chunk := stream.read(CHUNK_SIZE):
                    # Revocation/stop interrupts an in-flight stream at a chunk boundary.
                    try:
                        self.service._authenticate(self.headers.get("Authorization", ""))
                    except TransferError:
                        # Headers have been sent; do not append JSON to the archive.
                        return
                    self.wfile.write(chunk)
                    sent += len(chunk)
                    self.service._delivery_progress(bundle["id"], "sending", sent)
        finally:
            # Socket writes do NOT confirm that the receiver imported anything.
            self.service._delivery_progress(bundle["id"], "sent" if sent == size else "interrupted", sent)
            self.close_connection = True


class PeerTransferService:
    """Synchronous service: HTTP routers run blocking operations in worker threads."""

    def __init__(
        self, history: Any, data_root: Path | str, *,
        device_name: str = "Your Pit Box", network_provider: Callable[[], Any] | None = None,
        allow_loopback: bool = False, clock: Callable[[], float] = time.time,
        is_recording: Callable[[], bool] | None = None,
    ) -> None:
        self.history = history
        self.root = Path(data_root) / "peer-transfer"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.cache = self.root / "cache"
        self.cache.mkdir(exist_ok=True, mode=0o700)
        self._lock = threading.RLock()
        self._clock = clock
        self._is_recording = is_recording or (lambda: False)
        self._allow_loopback = allow_loopback
        self._network_provider = network_provider
        self._networks: list[Any] = []
        self._addresses: list[str] = []
        self._peers: dict[str, dict[str, Any]] = {}
        self._jobs: dict[str, dict[str, Any]] = {}
        self._bundles: dict[str, dict[str, Any]] = {}
        self._rates: dict[str, deque] = defaultdict(deque)
        self._invite: dict[str, Any] | None = None
        self._server: _TransferServer | None = None
        self._thread: threading.Thread | None = None
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pitwall-transfer")
        self._export_lock = threading.Lock()
        self._endpoint: str | None = None
        self._pin: str | None = None
        self._error: str | None = None
        self._store_error = False
        self._port = DEFAULT_PORT
        state_path = self.root / "peers.json"
        if state_path.exists():
            try:
                if state_path.stat().st_size > MAX_JSON:
                    raise ValueError("peer store too large")
                saved = json.loads(state_path.read_text())
                self.device_id = saved["device_id"]
                self.device_name = _name(saved.get("device_name", device_name))
                if not _valid_id(self.device_id) or not isinstance(saved["peers"], dict):
                    raise ValueError("invalid peer store")
                self._peers = saved["peers"]
                os.chmod(state_path, 0o600)
                for key, peer in self._peers.items():
                    if (not _valid_id(key) or peer.get("id") != key
                            or not _valid_pin(peer.get("certificate_sha256"))
                            or not _valid_secret(peer.get("incoming_token"))
                            or not _valid_secret(peer.get("outgoing_token"))):
                        raise ValueError("invalid peer identity")
            except (KeyError, ValueError, OSError, AttributeError, TypeError):
                self.device_id = uuid.uuid4().hex
                self.device_name = _name(device_name)
                self._peers = {}
                self._store_error = True
                self._error = "Saved pairing data is unreadable. Restore peer-transfer/peers.json before enabling transfers. Your driving history is unaffected."
        else:
            self.device_id = uuid.uuid4().hex
            self.device_name = _name(device_name)
            self._save()
        self._restore_transfers()

    def _save_transfers(self) -> None:
        with self._lock:
            _private_write(self.root / "transfers.json", json.dumps({
                "jobs": self._jobs, "bundles": self._bundles,
            }, separators=(",", ":")).encode())

    def _restore_transfers(self) -> None:
        path = self.root / "transfers.json"
        try:
            saved = json.loads(path.read_text()) if path.exists() and path.stat().st_size <= 4 * MAX_JSON else {}
            now = self._clock()
            for key, bundle in saved.get("bundles", {}).items():
                archive = self.cache / f"export-{key}.pitbox"
                if (_valid_id(key) and bundle.get("peer_id") in self._peers
                        and bundle.get("status") == "ready"
                        and 0 <= now - bundle.get("created_at", 0) <= BUNDLE_TTL
                        and archive.is_file() and archive.stat().st_size == bundle.get("size")
                        and _valid_pin(bundle.get("sha256"))):
                    bundle["path"] = str(archive)
                    # There is no live stream after restart, even if one was saved.
                    bundle.pop("delivery_status", None)
                    bundle.pop("bytes_sent", None)
                    self._bundles[key] = bundle
            for key, job in list(saved.get("jobs", {}).items())[-50:]:
                if not _valid_id(key) or job.get("peer_id") not in self._peers:
                    continue
                self._session_ids(job.get("session_ids"))
                job["partial"] = str(self.cache / f"incoming-{key}.part")
                if job.get("status") not in {"completed", "conflict"}:
                    job.update(status="failed", cancelled=False,
                        error="The app restarted. Enable Wi-Fi transfers and retry to resume.")
                self._jobs[key] = job
        except (ValueError, TypeError, AttributeError, OSError, TransferError):
            self._jobs.clear()
            self._bundles.clear()
            self._error = "Saved transfer progress could not be read. Start a new transfer; existing history is intact."
        retained = {Path(b["path"]) for b in self._bundles.values()} | {Path(j["partial"]) for j in self._jobs.values()}
        for pattern in ("export-*.pitbox", "incoming-*.part"):
            for cached in self.cache.glob(pattern):
                if cached not in retained:
                    cached.unlink(missing_ok=True)

    def _save(self) -> None:
        if self._store_error:
            raise TransferError(self._error, "invalid_peer_store", 409)
        _private_write(self.root / "peers.json", json.dumps({
            "device_id": self.device_id, "device_name": self.device_name, "peers": self._peers,
        }, separators=(",", ":")).encode())

    def _refresh_networks(self) -> None:
        if self._network_provider:
            result = self._network_provider()
        else:
            from .networking import discover_ipv4_interfaces
            result = discover_ipv4_interfaces()
        interfaces = getattr(result, "interfaces", result)
        networks, addresses = [], []
        for item in interfaces:
            # The stdlib diagnostic fallback knows only a hostname/default-
            # route address and guesses /24. It cannot establish whether that
            # address belongs to Wi-Fi, cellular, a VPN or a virtual adapter.
            # Keep it useful in CONNECTION, but never use it to authorize a
            # sharing listener or peer subnet. Native Android interfaces keep
            # their android: IDs even when returned by fallback discovery.
            if str(getattr(item, "adapter_id", "")).startswith("fallback:"):
                continue
            address = ipaddress.ip_address(item.address)
            if not getattr(item, "is_up", True):
                continue
            kind = str(getattr(getattr(item, "kind", None), "value", getattr(item, "kind", "")))
            if kind in {"vpn", "cellular", "virtual", "loopback"}:
                continue
            if not any(address in net for net in _RFC1918):
                continue
            # Prefixes less specific than RFC1918 ranges cannot authorize a
            # public destination. _allowed_address also checks RFC1918 below.
            prefix = getattr(item, "prefix_length", None)
            if prefix is None or not 1 <= prefix <= 32:
                continue
            networks.append(ipaddress.ip_network(f"{address}/{prefix}", strict=False))
            addresses.append(str(address))
        if self._allow_loopback:
            networks.append(ipaddress.ip_network("127.0.0.0/8"))
            addresses.append("127.0.0.1")
        with self._lock:
            self._networks, self._addresses = networks, addresses

    def _allowed_address(self, value: str) -> bool:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            return False
        if not isinstance(address, ipaddress.IPv4Address):
            return False
        if address.is_loopback:
            return self._allow_loopback
        return any(address in network for network in _RFC1918) and any(
            address in network and address not in {network.network_address, network.broadcast_address}
            for network in self._networks
        )

    def _parse_endpoint(self, value: Any) -> tuple[str, int]:
        if not isinstance(value, str) or len(value) > 100:
            raise TransferError("Invalid peer address.", "invalid_endpoint")
        try:
            parsed = urlsplit(value)
            host, port = parsed.hostname, parsed.port
        except ValueError as exc:
            raise TransferError("Invalid peer address.", "invalid_endpoint") from exc
        if (parsed.scheme != "https" or not host or port is None
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
                or not 1024 <= port <= 65535 or not self._allowed_address(host)):
            raise TransferError("Peer must use an HTTPS IPv4 address on this local network (port 1024–65535).", "local_network_only")
        return host, port

    def _certificate(self) -> ssl.SSLContext:
        try:
            from cryptography import x509
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import ec
            from cryptography.x509.oid import NameOID
        except ImportError as exc:
            raise TransferError("Secure transfers need the cryptography package. Install the current complete app build.", "tls_unavailable", 503) from exc
        cert_path, key_path = self.root / "identity.pem", self.root / "identity-key.pem"
        if cert_path.exists() != key_path.exists():
            raise TransferError("Transfer identity is incomplete. Restore its certificate and key together.", "identity_incomplete")
        if not cert_path.exists():
            key = ec.generate_private_key(ec.SECP256R1())
            subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Pit Wall " + self.device_id)])
            now = datetime.now(UTC)
            cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                    .public_key(key.public_key()).serial_number(x509.random_serial_number())
                    .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=3650))
                    .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                    .sign(key, hashes.SHA256()))
            _private_write(key_path, key.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            _private_write(cert_path, cert.public_bytes(serialization.Encoding.PEM))
        certificate = x509.load_pem_x509_certificate(cert_path.read_bytes())
        self._pin = certificate.fingerprint(hashes.SHA256()).hex()
        os.chmod(key_path, 0o600)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(cert_path, key_path)
        return context

    def start(self, advertise_host: str | None = None, port: int = DEFAULT_PORT,
              device_name: str | None = None) -> dict[str, Any]:
        with self._lock:
            if self._store_error:
                raise TransferError(self._error, "invalid_peer_store", 409)
            if self._server:
                return self.status()
            if not 1024 <= port <= 65535 and not (self._allow_loopback and port == 0):
                raise TransferError("Transfer port must be between 1024 and 65535.", "invalid_port")
            self._refresh_networks()
            host = advertise_host or (self._addresses[0] if self._addresses else None)
            if not host or host not in self._addresses:
                raise TransferError("No confirmed local interface is available. Connect to home Wi-Fi or Ethernet, refresh Listener & source in Connection, then try again.", "no_local_network")
            try:
                context = self._certificate()
                server = _TransferServer((host, port), self, context)
            except OSError as exc:
                self._error = "Cannot start the transfer listener. Check whether its port is already in use."
                raise TransferError(self._error, "listener_bind_failed", 409) from exc
            self._server = server
            self._port = server.server_port
            self._endpoint = f"https://{host}:{self._port}"
            self._error = None
            if device_name:
                self.device_name = _name(device_name)
                self._save()
            self._thread = threading.Thread(target=server.serve_forever,
                kwargs={"poll_interval": 0.2}, daemon=True, name="pitwall-transfer-listener")
            self._thread.start()
            return self.status()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            server, self._server = self._server, None
            self._invite = None
            for job in self._jobs.values():
                if job["status"] not in {"completed", "failed", "conflict"}:
                    job["cancelled"] = True
            self._endpoint = None
        if server:
            server.shutdown()
            server.server_close()
        self._save_transfers()
        return self.status()

    def close(self) -> None:
        self.stop()
        self._pool.shutdown(wait=True, cancel_futures=True)
        self._save_transfers()

    @staticmethod
    def _public_peer(peer: dict[str, Any]) -> dict[str, Any]:
        return {key: peer[key] for key in ("id", "name", "endpoint")}

    @staticmethod
    def _public_job(job: dict[str, Any]) -> dict[str, Any]:
        keys = ("id", "peer_id", "session_ids", "status", "bytes_received", "total_bytes", "result", "error")
        return {key: job.get(key) for key in keys} | {"progress": public_progress(job.get("progress"))}

    def _public_export(self, bundle: dict[str, Any]) -> dict[str, Any]:
        status = bundle["status"]
        if status == "ready":
            status = bundle.get("delivery_status", "ready")
            if status in {"ready", "interrupted"} and self._clock() - bundle["created_at"] > BUNDLE_TTL:
                status = "expired"
        return {"id": bundle["id"], "peer_id": bundle["peer_id"],
                "session_ids": bundle["session_ids"], "status": status,
                "progress": public_progress(bundle.get("progress")),
                "bytes_sent": bundle.get("bytes_sent", 0), "total_bytes": bundle.get("size"),
                "error": bundle.get("error")}

    def _progress(self, record: dict[str, Any], event: dict[str, Any] | None) -> None:
        # Deliberately in memory only: progress must not fsync the journal per row/file.
        with self._lock:
            record["progress"] = event

    def _delivery_progress(self, bundle_id: str, status: str, sent: int) -> None:
        with self._lock:
            if bundle_id in self._bundles:
                self._bundles[bundle_id].update(delivery_status=status, bytes_sent=sent)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {"running": self._server is not None, "port": self._port,
                "device_id": self.device_id, "device_name": self.device_name,
                "endpoint": self._endpoint, "certificate_sha256": self._pin,
                "error": self._error, "peers": [self._public_peer(p) for p in self._peers.values()],
                "jobs": [self._public_job(j) for j in self._jobs.values()],
                "exports": [self._public_export(b) for b in self._bundles.values()]}

    def invite(self) -> dict[str, Any]:
        self._refresh_networks()
        with self._lock:
            if not self._server:
                raise TransferError("Enable Wi-Fi transfers first.", "listener_off", 409)
            if urlsplit(self._endpoint).hostname not in self._addresses:
                raise TransferError("This device's address changed. Turn Wi-Fi transfers off and on, then create a new invitation.", "address_changed", 409)
            expires_at = self._clock() + INVITATION_TTL
            token = secrets.token_urlsafe(32)
            self._invite = {"token": token, "expires_at": expires_at}
            payload = {"version": PROTOCOL_VERSION, "endpoint": self._endpoint,
                "certificate_sha256": self._pin, "token": token, "expires_at": expires_at}
            encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
            invitation = "pitwall-pair://" + encoded
            pairing_uri = "pitwall://pair?invite=" + quote(invitation, safe="")
            import qrcode
            from qrcode.image.svg import SvgPathImage
            qr = qrcode.make(pairing_uri, image_factory=SvgPathImage, border=4)
            return {"invitation": invitation, "expires_at": expires_at,
                "endpoint": self._endpoint, "pairing_uri": pairing_uri,
                "qr_svg": qr.to_string().decode("utf-8")}

    def _rate_limit(self, key: str, limit: int) -> None:
        with self._lock:
            now = self._clock()
            if key not in self._rates and len(self._rates) >= 256:
                for old in list(self._rates):
                    if not self._rates[old] or self._rates[old][-1] < now - 60:
                        del self._rates[old]
                if len(self._rates) >= 256:
                    raise TransferError("The listener is busy. Retry later.", "rate_limited", 429)
            queue = self._rates[key]
            while queue and queue[0] < now - 60:
                queue.popleft()
            if len(queue) >= limit:
                raise TransferError("Too many requests. Wait one minute and retry.", "rate_limited", 429)
            queue.append(now)

    def _accept_pair(self, body: dict[str, Any], source: str) -> dict[str, Any]:
        with self._lock:
            invite = self._invite
            if (not self._server or not invite or self._clock() >= invite["expires_at"]
                    or not _valid_secret(body.get("token"))
                    or not hmac.compare_digest(invite["token"], body["token"])):
                raise TransferError("The invitation is invalid, expired, or already used.", "invalid_invitation", 403)
            host, _ = self._parse_endpoint(body.get("endpoint"))
            if host != source:
                raise TransferError("The advertised address does not match the connecting device.", "address_mismatch", 403)
            peer_id = body.get("device_id")
            if (body.get("version") != PROTOCOL_VERSION or not _valid_id(peer_id)
                    or peer_id == self.device_id or not _valid_pin(body.get("certificate_sha256"))
                    or not _valid_secret(body.get("reciprocal_token"))):
                raise TransferError("Invalid pairing details or incompatible protocol.", "invalid_pairing")
            if len(self._peers) >= 32 and peer_id not in self._peers:
                raise TransferError("Remove an old paired device before adding another.", "peer_limit", 409)
            token = secrets.token_urlsafe(32)
            peer = {"id": peer_id, "name": _name(body.get("device_name")),
                "endpoint": body["endpoint"], "certificate_sha256": body["certificate_sha256"],
                "incoming_token": token, "outgoing_token": body["reciprocal_token"]}
            self._peers[peer_id] = peer
            self._save()
            self._invite = None
            return {"version": PROTOCOL_VERSION, "device_id": self.device_id,
                "device_name": self.device_name, "endpoint": self._endpoint, "token": token}

    def pair(self, invitation: str, device_name: str | None = None) -> dict[str, Any]:
        if not self._server:
            raise TransferError("Enable Wi-Fi transfers on this device before pairing.", "listener_off", 409)
        if not isinstance(invitation, str) or len(invitation) > 4096:
            raise TransferError("Invalid invitation.", "invalid_invitation")
        try:
            value = invitation.strip()
            if not value.startswith("pitwall-pair://"):
                raise ValueError("scheme")
            value = value[len("pitwall-pair://"):]
            payload = json.loads(base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True))
            if (payload["version"] != PROTOCOL_VERSION or not _valid_pin(payload["certificate_sha256"])
                    or not _valid_secret(payload["token"])):
                raise ValueError("payload")
            if float(payload["expires_at"]) <= self._clock():
                raise TransferError("The invitation has expired. Create a new one on the other device.", "expired_invitation", 409)
        except (KeyError, ValueError, TypeError) as exc:
            raise TransferError("Invalid invitation. Copy the entire invitation from the other device.", "invalid_invitation") from exc
        self._refresh_networks()
        self._parse_endpoint(payload["endpoint"])
        if payload["endpoint"].rstrip("/") == self._endpoint:
            raise TransferError("This invitation belongs to this device.", "self_pairing")
        reciprocal = secrets.token_urlsafe(32)
        connection_peer = {"endpoint": payload["endpoint"], "certificate_sha256": payload["certificate_sha256"]}
        answer = self._request(connection_peer, "POST", "/v1/pair", {
            "version": PROTOCOL_VERSION, "token": payload["token"], "device_id": self.device_id,
            "device_name": _name(device_name or self.device_name), "endpoint": self._endpoint,
            "certificate_sha256": self._pin, "reciprocal_token": reciprocal,
        }, authenticated=False)
        if (answer.get("version") != PROTOCOL_VERSION or not _valid_id(answer.get("device_id"))
                or answer["device_id"] == self.device_id or not _valid_secret(answer.get("token"))
                or answer.get("endpoint", "").rstrip("/") != payload["endpoint"].rstrip("/")):
            raise TransferError("The peer returned invalid pairing details.", "invalid_pairing")
        peer = {"id": answer["device_id"], "name": _name(answer.get("device_name")),
            "endpoint": payload["endpoint"], "certificate_sha256": payload["certificate_sha256"],
            "incoming_token": reciprocal, "outgoing_token": answer["token"]}
        with self._lock:
            if len(self._peers) >= 32 and peer["id"] not in self._peers:
                raise TransferError("Remove an old paired device before adding another.", "peer_limit", 409)
            self._peers[peer["id"]] = peer
            self._save()
        return self._public_peer(peer)

    def _authenticate(self, authorization: str) -> dict[str, Any]:
        if not authorization.startswith("Bearer ") or not self._server:
            raise TransferError("Pair this device before accessing history.", "unpaired", 401)
        token = authorization[7:]
        if not _valid_secret(token):
            raise TransferError("Pair this device before accessing history.", "unpaired", 401)
        with self._lock:
            for peer in self._peers.values():
                if hmac.compare_digest(peer["incoming_token"], token):
                    return dict(peer)
        raise TransferError("The device is not paired or its access was revoked.", "unpaired", 401)

    def _peer(self, peer_id: str) -> dict[str, Any]:
        with self._lock:
            if peer_id not in self._peers:
                raise TransferError("Paired device not found.", "peer_not_found", 404)
            return dict(self._peers[peer_id])

    def revoke(self, peer_id: str) -> dict[str, Any]:
        with self._lock:
            if peer_id not in self._peers:
                raise TransferError("Paired device not found.", "peer_not_found", 404)
            del self._peers[peer_id]
            for job in self._jobs.values():
                if job["peer_id"] == peer_id:
                    job["cancelled"] = True
            self._save()
        return {"revoked": True, "peer_id": peer_id}

    def _connection(self, peer: dict[str, Any]) -> _PinnedConnection:
        host, port = self._parse_endpoint(peer["endpoint"])
        source = urlsplit(self._endpoint).hostname if self._endpoint else None
        if source and source not in self._addresses:
            raise TransferError("This device's address changed. Turn transfers off and on and pair again.", "address_changed", 409)
        return _PinnedConnection(host, port, peer["certificate_sha256"], source)

    def _request(self, peer: dict[str, Any], method: str, path: str,
                 body: dict[str, Any] | None = None, *, authenticated: bool = True) -> dict[str, Any]:
        connection = self._connection(peer)
        headers = {"Accept": "application/json", "Connection": "close"}
        if authenticated:
            headers["Authorization"] = "Bearer " + peer["outgoing_token"]
        encoded = json.dumps(body).encode() if body is not None else None
        if encoded is not None:
            headers["Content-Type"] = "application/json"
        try:
            connection.request(method, path, body=encoded, headers=headers)
            response = connection.getresponse()
            raw = response.read(MAX_JSON + 1)
            if len(raw) > MAX_JSON:
                raise TransferError("Peer response exceeds the size limit.", "response_too_large")
            try:
                result = json.loads(raw)
            except (ValueError, UnicodeDecodeError) as exc:
                raise TransferError("Peer returned invalid data.", "invalid_response") from exc
            if not isinstance(result, dict):
                raise TransferError("Peer returned invalid data.", "invalid_response")
            if not 200 <= response.status < 300:
                raise TransferError(str(result.get("error", "Peer request failed."))[:400],
                    str(result.get("code", "peer_error"))[:80], response.status)
            return result
        except (OSError, http.client.HTTPException) as exc:
            raise TransferError("Cannot reach the paired device. Keep both apps open on the same Wi-Fi; check firewall access to the transfer port.", "peer_unreachable", 503) from exc
        finally:
            connection.close()

    def _sessions(self) -> list[dict[str, Any]]:
        result = self.history.list_sessions()
        rows = result.get("sessions", result.get("items", [])) if isinstance(result, dict) else result
        # The bundle service supplies the authoritative completed-session view.
        # Defence in depth also excludes conventional active status markers.
        return [row for row in rows if row.get("transferable", True) and str(row.get("status", "completed")).lower()
                not in {"active", "recording", "live", "in_progress"}]

    def peer_sessions(self, peer_id: str) -> dict[str, Any]:
        self._refresh_networks()
        return self._request(self._peer(peer_id), "GET", "/v1/sessions")

    @staticmethod
    def _session_ids(values: Any) -> list[str]:
        if (not isinstance(values, list) or not 1 <= len(values) <= 500
                or any(not isinstance(value, str) or not 1 <= len(value) <= 160 for value in values)):
            raise TransferError("Select between 1 and 500 completed sessions.", "invalid_sessions")
        return list(dict.fromkeys(values))

    @staticmethod
    def _public_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
        return {key: bundle.get(key) for key in ("id", "status", "size", "sha256", "error")} | {
            "progress": public_progress(bundle.get("progress"))}

    def _prepare_bundle(self, peer_id: str, values: Any) -> dict[str, Any]:
        ids = self._session_ids(values)
        available = {str(row.get("session_id", row.get("id"))) for row in self._sessions()}
        if not set(ids).issubset(available):
            raise TransferError("Only completed sessions in the current catalog can be transferred.", "session_unavailable", 409)
        with self._lock:
            now = self._clock()
            for key, previous in list(self._bundles.items()):
                if now - previous["created_at"] > BUNDLE_TTL and previous["status"] != "preparing":
                    Path(previous["path"]).unlink(missing_ok=True)
                    del self._bundles[key]
            for previous in self._bundles.values():
                if previous["peer_id"] == peer_id and previous["session_ids"] == ids and previous["status"] in {"preparing", "ready"}:
                    return self._public_bundle(previous)
            if len(self._bundles) >= 4:
                # Never evict another transfer silently. Finished exports expire.
                raise TransferError("The export cache is full. Retry after older transfers expire or restart sharing.", "export_busy", 409)
            if any(b["status"] == "preparing" for b in self._bundles.values()):
                raise TransferError("Another export is being prepared. Try again when it finishes.", "export_busy", 409)
            if shutil.disk_usage(self.cache).free < 100 * 1024**2:
                raise TransferError("The sending device needs more free space to prepare history.", "insufficient_space", 409)
            bundle_id = uuid.uuid4().hex
            bundle = {"id": bundle_id, "peer_id": peer_id, "session_ids": ids,
                "created_at": now, "status": "preparing", "size": None, "sha256": None,
                "error": None, "path": str(self.cache / f"export-{bundle_id}.pitbox")}
            self._bundles[bundle_id] = bundle
            self._save_transfers()
            self._pool.submit(self._export, bundle)
            return self._public_bundle(bundle)

    def _export(self, bundle: dict[str, Any]) -> None:
        path = Path(bundle["path"])
        try:
            with self._export_lock:
                self.history.export_bundle(bundle["session_ids"], path,
                    progress=lambda event: self._progress(bundle, event))
            size = path.stat().st_size
            if size > MAX_BUNDLE:
                raise TransferError("This transfer exceeds 8 GiB. Select fewer sessions.", "bundle_too_large")
            digest = _sha256(path, lambda event: self._progress(bundle, event))
            with self._lock:
                bundle.update(status="ready", size=size, sha256=digest)
        except Exception as exc:
            path.unlink(missing_ok=True)
            with self._lock:
                bundle.update(status="failed", error=str(exc)[:400])
        finally:
            self._save_transfers()

    def _get_bundle(self, peer_id: str, bundle_id: str) -> dict[str, Any]:
        with self._lock:
            bundle = self._bundles.get(bundle_id)
            if not bundle or bundle["peer_id"] != peer_id:
                raise TransferError("Export not found.", "bundle_not_found", 404)
            if self._clock() - bundle["created_at"] > BUNDLE_TTL:
                raise TransferError("The export has expired. Start the transfer again.", "bundle_expired", 410)
            return dict(bundle)

    def pull(self, peer_id: str, session_ids: list[str]) -> dict[str, Any]:
        self._check_idle()
        ids = self._session_ids(session_ids)
        self._peer(peer_id)
        if not self._server:
            raise TransferError("Enable Wi-Fi transfers first.", "listener_off", 409)
        with self._lock:
            if any(j["status"] not in {"completed", "failed", "conflict"} for j in self._jobs.values()):
                raise TransferError("Wait for the current transfer to finish.", "transfer_busy", 409)
            if len(self._jobs) >= 50:
                oldest = next(iter(self._jobs))
                Path(self._jobs[oldest]["partial"]).unlink(missing_ok=True)
                del self._jobs[oldest]
            job_id = uuid.uuid4().hex
            job = {"id": job_id, "peer_id": peer_id, "session_ids": ids, "status": "queued",
                "bytes_received": 0, "total_bytes": 0, "result": None, "error": None,
                "cancelled": False, "bundle": None, "partial": str(self.cache / f"incoming-{job_id}.part")}
            self._jobs[job_id] = job
            self._save_transfers()
            self._pool.submit(self._pull, job)
            return self._public_job(job)

    def job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            if job_id not in self._jobs:
                raise TransferError("Transfer not found.", "job_not_found", 404)
            return self._public_job(self._jobs[job_id])

    def retry(self, job_id: str) -> dict[str, Any]:
        self._check_idle()
        with self._lock:
            if not self._server:
                raise TransferError("Enable Wi-Fi transfers first.", "listener_off", 409)
            self.job(job_id)
            job = self._jobs[job_id]
            self._peer(job["peer_id"])
            if job["status"] != "failed":
                raise TransferError("Only a failed transfer can be retried.", "invalid_job_state", 409)
            if any(j["status"] not in {"completed", "failed", "conflict"} for j in self._jobs.values()):
                raise TransferError("Wait for the current transfer to finish.", "transfer_busy", 409)
            job.update(status="queued", error=None, cancelled=False, progress=None)
            self._save_transfers()
            self._pool.submit(self._pull, job)
            return self._public_job(job)

    def _check_job(self, job: dict[str, Any]) -> None:
        if job.get("cancelled") or not self._server:
            raise TransferError("Transfer stopped. Enable sharing and retry to resume.", "transfer_stopped", 409)
        self._peer(job["peer_id"])

    def _check_idle(self) -> None:
        if self._is_recording():
            raise TransferError("Stop live telemetry on this device before importing history. Your active session will not be changed.", "recording_active", 409)

    def _pull(self, job: dict[str, Any]) -> None:
        try:
            self._check_job(job)
            self._refresh_networks()
            peer = self._peer(job["peer_id"])
            job.update(status="preparing", progress=None)
            previous = job.get("bundle") or {}
            bundle = job.get("bundle")
            if bundle:
                try:
                    bundle = self._request(peer, "GET", "/v1/bundles/" + bundle["id"])
                except TransferError as exc:
                    if exc.status not in {404, 410}:
                        raise
                    bundle = None
            if not bundle:
                Path(job["partial"]).unlink(missing_ok=True)
                bundle = self._request(peer, "POST", "/v1/bundles", {"session_ids": job["session_ids"]})
            if not _valid_id(bundle.get("id")):
                raise TransferError("Peer returned an invalid export identifier.", "invalid_response")
            job["bundle"] = bundle
            self._progress(job, public_progress(bundle.get("progress")))
            deadline = time.monotonic() + 900
            while bundle.get("status") == "preparing":
                self._check_job(job)
                if time.monotonic() > deadline:
                    raise TransferError("Export preparation timed out. Select fewer sessions and retry.", "export_timeout")
                time.sleep(1)
                bundle = self._request(peer, "GET", "/v1/bundles/" + bundle["id"])
                self._progress(job, public_progress(bundle.get("progress")))
            if bundle.get("status") != "ready":
                raise TransferError(str(bundle.get("error") or "The peer could not prepare the export.")[:400], "export_failed")
            if (not isinstance(bundle.get("size"), int) or not 0 < bundle["size"] <= MAX_BUNDLE
                    or not _valid_pin(bundle.get("sha256"))):
                raise TransferError("The export manifest is invalid or exceeds 8 GiB.", "invalid_manifest")
            if previous.get("sha256") and previous["sha256"] != bundle["sha256"]:
                Path(job["partial"]).unlink(missing_ok=True)
            job["bundle"] = bundle
            job["total_bytes"] = bundle["size"]
            self._save_transfers()
            self._download(peer, bundle, job)
            self._check_job(job)
            path = Path(job["partial"])
            job.update(status="verifying", progress=None)
            if not hmac.compare_digest(_sha256(path, lambda event: self._progress(job, event)), bundle["sha256"]):
                path.unlink(missing_ok=True)
                job["bytes_received"] = 0
                raise TransferError("The downloaded archive failed its integrity check. Retry the transfer.", "checksum_mismatch")
            job.update(status="importing", progress=None)
            self._check_idle()
            report = self.history.import_bundle(path, progress=lambda event: self._progress(job, event))
            # Polling and persisted progress need counts, not a second copy of
            # every session and artifact in a months-long archive inventory.
            job["result"] = {key: report[key] for key in (
                "status", "imported_rows", "skipped_rows", "imported_sessions", "skipped_sessions",
                "completeness", "conflicts", "message", "preserved_path",
            ) if key in report}
            job["result"]["warnings"] = report.get("warnings", [])[:50]
            job["result"]["missing_asset_count"] = len(report.get("missing_assets", []))
            path.unlink(missing_ok=True)
            job["status"] = "conflict" if job["result"].get("status") == "conflict" else "completed"
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = str(exc)[:400] or "Transfer failed."
        finally:
            self._save_transfers()

    def _download(self, peer: dict[str, Any], bundle: dict[str, Any], job: dict[str, Any]) -> None:
        path = Path(job["partial"])
        offset = path.stat().st_size if path.exists() else 0
        if offset > bundle["size"]:
            path.unlink()
            offset = 0
        job.update(status="downloading", bytes_received=offset, progress=None)
        if offset == bundle["size"]:
            return
        # Allow additional room for extraction/SQLite import; fail before reading.
        if shutil.disk_usage(self.cache).free < (bundle["size"] - offset) * 2 + 100 * 1024**2:
            raise TransferError("This device needs more free space to download and import the selected history.", "insufficient_space")
        headers = {"Authorization": "Bearer " + peer["outgoing_token"], "Connection": "close"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        connection = self._connection(peer)
        deadline = time.monotonic() + 3600
        try:
            connection.request("GET", "/v1/bundles/" + bundle["id"] + "/content", headers=headers)
            response = connection.getresponse()
            expected_status = 206 if offset else 200
            expected_range = f"bytes {offset}-{bundle['size'] - 1}/{bundle['size']}"
            if (response.status != expected_status
                    or response.getheader("ETag") != '"' + bundle["sha256"] + '"'
                    or response.getheader("Content-Length") != str(bundle["size"] - offset)
                    or (offset and response.getheader("Content-Range") != expected_range)):
                raise TransferError("The peer returned an unexpected download response. Re-pair if its identity changed.", "invalid_download")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "ab") as target:
                received = offset
                while received < bundle["size"]:
                    self._check_job(job)
                    if time.monotonic() > deadline:
                        raise TransferError("Download timed out. Retry to resume.", "download_timeout")
                    chunk = response.read(min(CHUNK_SIZE, bundle["size"] - received))
                    if not chunk:
                        raise TransferError("The connection ended early. Retry to resume the download.", "download_interrupted")
                    target.write(chunk)
                    received += len(chunk)
                    job["bytes_received"] = received
                target.flush()
                os.fsync(target.fileno())
        except (OSError, http.client.HTTPException) as exc:
            raise TransferError("Download was interrupted. Keep both devices on the same Wi-Fi and retry to resume.", "download_interrupted", 503) from exc
        finally:
            connection.close()


__all__ = ["PeerTransferService", "TransferError", "DEFAULT_PORT"]
