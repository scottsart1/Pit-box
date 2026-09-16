"""Smoke the real shared engine over UDP and its local HTTP management API.

Runs without credentials or audio in a disposable data directory. This proves
host backend integration, not Android radio, WebView or physical LAN behavior.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]


def main():
    opener = build_opener(ProxyHandler({}))
    with tempfile.TemporaryDirectory(prefix="pitwall-smoke-") as folder:
        with socket.socket() as port_socket:
            port_socket.bind(("127.0.0.1", 0)); web_port = port_socket.getsockname()[1]
        with socket.socket(type=socket.SOCK_DGRAM) as port_socket:
            port_socket.bind(("127.0.0.1", 0)); udp_port = port_socket.getsockname()[1]
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src"),
            "PITWALL_DATA_DIR": folder + "/data", "PITWALL_STATIC_DIR": str(ROOT / "static"),
            "PITWALL_NATIVE_VOICE": "false", "PITWALL_WAKE_ENABLED": "false",
            "PITWALL_UDP_PORT": str(udp_port), "PITWALL_WEB_PORT": str(web_port),
            "PITWALL_WEB_HOST": "127.0.0.1",
            "PITWALL_OPEN_BROWSER": "false"}
        for key in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "KIMI_API_KEY", "CUSTOM_API_KEY"):
            env[key] = ""
        def call(path, method="GET", body=None):
            request = Request(f"http://127.0.0.1:{web_port}" + path, method=method,
                data=json.dumps(body).encode() if body is not None else None,
                headers={"Content-Type": "application/json"})
            with opener.open(request, timeout=10) as response:
                return json.load(response)
        with (Path(folder) / "server.log").open("w+") as log:
            process = subprocess.Popen([sys.executable, "-m", "pitwall.main"],
                cwd=folder, env=env, stdout=log, stderr=log)
            try:
                for _ in range(150):
                    try:
                        if call("/api/health")["ok"]: break
                    except OSError: pass
                    if process.poll() is not None: raise AssertionError("backend exited during startup")
                    time.sleep(.1)
                else: raise AssertionError("backend startup timed out")
                spec = importlib.util.spec_from_file_location("android_fixture", ROOT / "android/emulator-smoke.py")
                fixture = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                    destination = ("127.0.0.1", udp_port)
                    sender.sendto(b"short", destination)
                    wrong = bytearray(fixture.fixture_packets(1)[0]); wrong[:2] = (2025).to_bytes(2, "little")
                    sender.sendto(wrong, destination)
                    for frame in range(1, 31):
                        for packet in fixture.fixture_packets(frame): sender.sendto(packet, destination)
                        if frame == 1:
                            # A new session initializes storage. Let that first
                            # frame finish before sending the sustained fixture.
                            deadline = time.monotonic() + 30
                            while call("/api/v1/network/status")["datagrams"]["parsed"] < 3:
                                assert time.monotonic() < deadline, "Initial session frame was not parsed"
                                time.sleep(.1)
                        time.sleep(.05)
                # Receiving and parsing are asynchronous. Wait for the entire
                # fixture on slower hosts, then retain exact loss/count checks.
                deadline = time.monotonic() + 30
                while True:
                    network = call("/api/v1/network/status")
                    if network["datagrams"]["parsed"] >= 90 or time.monotonic() >= deadline:
                        break
                    time.sleep(.1)
                state = call("/api/state")
                assert network["datagrams"] == {"received": 92, "parsed": 90, "rejected": 2}, network
                assert state["connected"] and state["session_uid"] == 918273645
                assert state["track_name"] == "Suzuka" and state["current_lap"] == 8
                # Thirty frames now carry session, lap and car-telemetry packets.
                assert {p["packet_id"] for p in network["packets"]} == {1, 2, 6}
                assert state["speed_kph"] == state["throttle"] == state["brake"] == 0
                assert len(state["traces"]) == 2, "Unchanged stationary frames must retain only stop endpoints"
                assert call("/api/v1/transfers/status")["running"] is False
                call("/api/v1/network/listener/stop", "POST")
                assert call("/api/health")["udp_listener"] is False
                # A network-isolated CI host should reject sharing explicitly.
                try:
                    assert call("/api/v1/transfers/start", "POST", {})["running"]
                    assert "<svg" in call("/api/v1/transfers/invite", "POST")["qr_svg"]
                    assert not call("/api/v1/transfers/stop", "POST")["running"]
                    transfer_result = "start/invite/stop passed"
                except HTTPError as error:
                    detail = json.load(error).get("detail", {})
                    assert error.code == 422 and detail.get("code") == "no_local_network", detail
                    transfer_result = "correctly reports no private LAN in this host"
                print(json.dumps({"platform": sys.platform, "datagrams": network["datagrams"],
                    "race_state": "Suzuka, lap 8", "transfer_management": transfer_result}))
            except Exception:
                log.seek(0); print(log.read()[-6000:], file=sys.stderr)
                raise
            finally:
                if process.poll() is None:
                    try:
                        call("/api/shutdown", "POST", {})
                        process.wait(timeout=30)
                    except (OSError, subprocess.TimeoutExpired):
                        process.terminate()
                        try: process.wait(timeout=15)
                        except subprocess.TimeoutExpired: process.kill(); process.wait()


if __name__ == "__main__":
    main()
