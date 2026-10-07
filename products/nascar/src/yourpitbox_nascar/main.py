from __future__ import annotations

import argparse
import asyncio
import base64
import datetime
import hashlib
import io
import ipaddress
import logging
import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.parse import urlencode

import qrcode
import qrcode.image.svg
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from .app import create_app


def data_root() -> Path:
    return Path(os.environ.get("NASCAR_DATA_DIR") or Path.home() / "YourPitBoxNASCAR")


def addresses() -> list[str]:
    found = []
    for _, _, _, _, addr in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
        ip = ipaddress.ip_address(addr[0])
        if ip.is_private and not ip.is_loopback and not ip.is_link_local and str(ip) not in found:
            found.append(str(ip))
    return found


def certificate(root: Path, ips: list[str]) -> tuple[Path, Path, str]:
    cert_path, key_path = root / "companion.crt", root / "companion.key"
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = None
    if cert_path.exists() and key_path.exists():
        try:
            candidate = x509.load_pem_x509_certificate(cert_path.read_bytes())
            if candidate.not_valid_after_utc > now + datetime.timedelta(days=7):
                cert = candidate
        except ValueError:
            pass
    if cert is None:
        private_key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "YourPitBox NASCAR companion")])
        cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(private_key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
                .not_valid_after(now + datetime.timedelta(days=365))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), *[x509.IPAddress(ipaddress.ip_address(ip)) for ip in ["127.0.0.1", *ips]]]), critical=False)
                .sign(private_key, hashes.SHA256()))
        key_path.write_bytes(private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        key_path.chmod(0o600)
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path, cert.fingerprint(hashes.SHA256()).hex()


def pairing_info(app, root: Path, ips: list[str], port: int):
    cert, key, fingerprint = certificate(root, ips)
    connections = []
    for ip in ips:
        params = {"host": ip, "port": port, "token": app.state.token, "fingerprint": fingerprint}
        link = "yourpitbox-nascar://pair?" + urlencode(params)
        qr = qrcode.make(link, image_factory=qrcode.image.svg.SvgPathFillImage, border=3)
        output = io.BytesIO()
        qr.save(output)
        connections.append({"url": link, "address": f"{ip}:{port}", "fingerprint": fingerprint,
                            "qr_base64": base64.b64encode(output.getvalue()).decode()})
    if connections:
        app.state.lan_info = {**connections[0], "addresses": ips, "connections": connections}
    return cert, key


async def serve(app, port: int, lan_port: int | None, cert=None, key=None):
    local = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, access_log=False, proxy_headers=False))
    if not lan_port:
        local_task = asyncio.create_task(local.serve())
        quit_task = asyncio.create_task(app.state.shutdown.wait())
        await asyncio.wait([local_task, quit_task], return_when=asyncio.FIRST_COMPLETED)
        local.should_exit = True
        await local_task
        quit_task.cancel()
        return
    # Only one lifespan owns storage and background tasks; the second listener
    # shares the application and is encrypted for a pinned Android companion.
    remote = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=lan_port, ssl_certfile=str(cert), ssl_keyfile=str(key), lifespan="off", log_config=None, access_log=False, proxy_headers=False))
    tasks = [asyncio.create_task(local.serve()), asyncio.create_task(remote.serve())]
    quit_task = asyncio.create_task(app.state.shutdown.wait())
    try:
        await asyncio.wait([*tasks, quit_task], return_when=asyncio.FIRST_COMPLETED)
    finally:
        local.should_exit = remote.should_exit = True
        await asyncio.gather(*tasks)
        quit_task.cancel()


def run():
    parser = argparse.ArgumentParser(description="YourPitBox for NASCAR")
    parser.add_argument("--data-dir", type=Path, default=data_root())
    parser.add_argument("--port", type=int, default=53260)
    parser.add_argument("--lan-port", type=int, default=53261)
    parser.add_argument("--no-lan", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    from logging.handlers import RotatingFileHandler
    logging.basicConfig(level=logging.INFO, handlers=[RotatingFileHandler(args.data_dir / "nascar.log", maxBytes=2_000_000, backupCount=2)], format="%(asctime)s %(levelname)s %(message)s")
    # A second launch should focus the existing product rather than create a
    # competing writer or mistake the F1 app for this one.
    import httpx
    try:
        existing = httpx.get(f"http://127.0.0.1:{args.port}/health", timeout=1)
        if existing.json().get("product") == "yourpitbox-nascar":
            if not args.no_browser:
                webbrowser.open(f"http://127.0.0.1:{args.port}")
            return
    except (httpx.HTTPError, ValueError):
        pass
    app = create_app(args.data_dir)
    cert = key = None
    if not args.no_lan:
        cert, key = pairing_info(app, args.data_dir, addresses(), args.lan_port)
    if not args.no_browser:
        timer = threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}"))
        timer.daemon = True
        timer.start()
    asyncio.run(serve(app, args.port, None if args.no_lan else args.lan_port, cert, key))


if __name__ == "__main__":
    run()
