"""Provider proxy support must survive both source and frozen startup."""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import threading
import tomllib
from pathlib import Path

import pytest
from openai import AsyncOpenAI

ROOT = Path(__file__).resolve().parents[1]


def _proxy_environment(monkeypatch, proxy: str) -> None:
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    monkeypatch.setenv("ALL_PROXY", proxy)


def test_socks_is_a_runtime_and_frozen_dependency() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert any(dep.startswith("httpx[socks]") for dep in project["project"]["dependencies"])
    spec = (ROOT / "distribution/packaging/pitwall.spec").read_text(encoding="utf-8")
    assert '"socksio"' in spec


@pytest.mark.parametrize("scheme", ["socks5", "socks5h"])
def test_app_import_with_configured_key_and_socks_proxy(tmp_path, scheme) -> None:
    """A saved key used to make module import crash before the web app existed.

    No API calls are made: the closed local proxy port is deliberate. Merely
    constructing the clients must not require a working external connection.
    """
    env = os.environ.copy()
    for name in list(env):
        if name.upper().endswith("_PROXY") or name.startswith("PITWALL_") or name in {
            "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY",
            "KIMI_API_KEY", "MOONSHOT_API_KEY", "CUSTOM_LLM_API_KEY",
        }:
            del env[name]
    env.update({
        "PYTHONPATH": str(ROOT / "src"),
        "PITWALL_DATA_DIR": str(tmp_path / "data"),
        "PITWALL_OPEN_BROWSER": "false",
        "OPENAI_API_KEY": "sk-test-offline-proxy-regression",
        "ALL_PROXY": f"{scheme}://127.0.0.1:1",
    })
    result = subprocess.run(
        [sys.executable, "-c", "import pitwall.app; print('dashboard-import-ok')"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "dashboard-import-ok" in result.stdout


def _read_exact(connection: socket.socket, count: int) -> bytes:
    result = b""
    while len(result) < count:
        chunk = connection.recv(count - len(result))
        if not chunk:
            raise AssertionError("client closed the SOCKS handshake early")
        result += chunk
    return result


@pytest.mark.parametrize("scheme", ["socks5", "socks5h"])
def test_openai_client_really_routes_through_socks(monkeypatch, scheme) -> None:
    """Observe the actual SOCKS handshake, target hostname and HTTP request.

    The local proxy supplies the models response itself; no internet service,
    DNS lookup, user key, billing account or external proxy is involved.
    """
    evidence: dict[str, object] = {}
    errors: list[BaseException] = []
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(5)
    _proxy_environment(monkeypatch, f"{scheme}://127.0.0.1:{listener.getsockname()[1]}")

    def serve() -> None:
        try:
            with listener:
                connection, _ = listener.accept()
                with connection:
                    connection.settimeout(5)
                    version, method_count = _read_exact(connection, 2)
                    assert version == 5
                    assert 0 in _read_exact(connection, method_count)
                    connection.sendall(b"\x05\x00")
                    assert _read_exact(connection, 4) == b"\x05\x01\x00\x03"
                    host_length = _read_exact(connection, 1)[0]
                    evidence["host"] = _read_exact(connection, host_length).decode("ascii")
                    evidence["port"] = int.from_bytes(_read_exact(connection, 2), "big")
                    connection.sendall(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x50")
                    request = b""
                    while b"\r\n\r\n" not in request:
                        chunk = connection.recv(4096)
                        assert chunk
                        request += chunk
                    evidence["request_line"] = request.split(b"\r\n", 1)[0]
                    body = b'{"object":"list","data":[]}'
                    connection.sendall(
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                        + f"Content-Length: {len(body)}\r\n".encode("ascii")
                        + b"Connection: close\r\n\r\n" + body
                    )
        except (AssertionError, OSError, UnicodeError) as exc:
            errors.append(exc)

    server = threading.Thread(target=serve, daemon=True)
    server.start()

    async def request_models() -> None:
        async with AsyncOpenAI(
            api_key="sk-test-local-only", base_url="http://pitbox.invalid/v1",
            timeout=5, max_retries=0,
        ) as client:
            page = await client.models.list()
            assert page.data == []

    try:
        asyncio.run(request_models())
    finally:
        server.join(timeout=6)
        listener.close()
    assert not server.is_alive()
    assert not errors, errors
    assert evidence == {
        "host": "pitbox.invalid", "port": 80,
        "request_line": b"GET /v1/models HTTP/1.1",
    }
