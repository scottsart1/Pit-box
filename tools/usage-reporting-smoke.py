"""Exercise the real app locally with an in-memory receiver, never production.

No credentials, audio, real telemetry ports, saved sessions or installed app are
used. Temporary fixture data is removed after the child exits.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]


def main():
    received = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            assert self.path == "/usage"
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            received.append(body)
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def log_message(self, *_):
            pass

    collector = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    threading.Thread(target=collector.serve_forever, daemon=True).start()
    opener = build_opener(ProxyHandler({}))
    try:
        with tempfile.TemporaryDirectory(prefix="pitbox-usage-draft-") as folder:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                web_port = sock.getsockname()[1]
            with socket.socket(type=socket.SOCK_DGRAM) as sock:
                sock.bind(("127.0.0.1", 0))
                udp_port = sock.getsockname()[1]
            env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PITWALL_DATA_DIR": folder + "/data",
                   "PITWALL_STATIC_DIR": str(ROOT / "static"), "PITWALL_NATIVE_VOICE": "false",
                   "PITWALL_WAKE_ENABLED": "false", "PITWALL_UDP_PORT": str(udp_port),
                   "PITWALL_UDP_BIND_HOST": "127.0.0.1", "PITWALL_WEB_PORT": str(web_port),
                   "PITWALL_WEB_HOST": "127.0.0.1", "PITWALL_WEB_LAN_ACCESS": "false",
                   "PITWALL_RAW_CAPTURE": "off", "PITWALL_DB_MAINTENANCE_ON_START": "false",
                   "PITWALL_OPEN_BROWSER": "false"}
            for key in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "KIMI_API_KEY", "MOONSHOT_API_KEY", "CUSTOM_LLM_API_KEY", "PITWALL_CUSTOM_LLM_API_KEY"):
                env[key] = ""

            def call(path, method="GET", body=None):
                request = Request(f"http://127.0.0.1:{web_port}" + path, method=method,
                                  data=json.dumps(body).encode() if body is not None else None,
                                  headers={"Content-Type": "application/json"})
                with opener.open(request, timeout=10) as response:
                    return json.load(response)

            def wait_until(predicate, timeout=25):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    try:
                        if predicate():
                            return
                    except OSError:
                        pass
                    time.sleep(0.15)
                raise AssertionError("Local draft smoke check timed out")

            with (Path(folder) / "server.log").open("w+") as log:
                for iteration in range(2):
                    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--serve-local-fixture", str(collector.server_port)],
                                               cwd=folder, env=env, stdout=log, stderr=log)
                    try:
                        wait_until(lambda: call("/api/health")["ok"])
                        initial = call("/api/v1/usage")
                        assert initial["enabled"] is False
                        if iteration == 0:
                            assert initial["decided"] is False
                            assert received == []
                            assert call("/api/v1/usage", "POST", {"enabled": True})["enabled"]
                            call("/api/v1/usage/active", "POST", {})
                            wait_until(lambda: len(received) == 1)
                            wait_until(lambda: call("/api/v1/usage")["pending_days"] == 0)
                            report = received[0]
                            assert set(report) == {"consent_version", "installation_id", "platform", "version", "events"}
                            assert {item["event"] for item in report["events"]} == {"app_started", "app_used"}
                            assert report["platform"] == "windows" if sys.platform == "win32" else True
                            identity = report["installation_id"]
                            assert call("/api/v1/usage", "POST", {"enabled": False})["enabled"] is False
                            assert identity not in (Path(folder) / "data/usage-reporting.json").read_text()
                        else:
                            assert initial["decided"] is True
                            assert len(received) == 1
                            assert (Path(folder) / "data/pitwall.sqlite3").is_file()
                        assert call("/api/health")["ok"]
                    except Exception:
                        log.flush()
                        log.seek(0)
                        print(log.read()[-3000:], file=sys.stderr)
                        raise
                    finally:
                        if process.poll() is None:
                            try:
                                call("/api/shutdown", "POST", {})
                                process.wait(timeout=35)
                            except (OSError, subprocess.TimeoutExpired):
                                process.terminate()
                                process.wait(timeout=10)
            print(json.dumps({"result": "passed", "platform": sys.platform, "receiver": "loopback fixture only",
                              "reports_received": len(received), "checks": ["real app startup", "default off", "explicit choice",
                              "background send", "payload allowlist", "opt-out clears identity", "restart retains opt-out", "graceful shutdown"]}))
    finally:
        collector.shutdown()
        collector.server_close()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--serve-local-fixture":
        from pitwall import usage_reporting
        # The only override is in this test child; shipping code has no endpoint
        # setting that could redirect a user's reports to another server.
        usage_reporting.ENDPOINT = f"http://127.0.0.1:{int(sys.argv[2])}/usage"
        from pitwall.main import run
        run()
    else:
        main()
