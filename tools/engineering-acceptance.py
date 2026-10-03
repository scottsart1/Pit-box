"""Exercise real UDP runs, session switching and Chromium UI in an isolated app."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def free_port(kind):
    with socket.socket(socket.AF_INET, kind) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pitbox-source-qa-") as directory:
        web, udp = free_port(socket.SOCK_STREAM), free_port(socket.SOCK_DGRAM)
        env = dict(
            os.environ,
            PITWALL_SANDBOX_ROOT=directory,
            PITWALL_WEB_PORT=str(web),
            PITWALL_UDP_PORT=str(udp),
            PITWALL_NATIVE_VOICE_ENABLED="false",
            PITWALL_WAKE_ENABLED="false",
            PITWALL_PROACTIVE_ENABLED="false",
            PITWALL_DB_MAINTENANCE_ON_START="false",
            OPENAI_API_KEY="",
            DEEPSEEK_API_KEY="",
        )
        base = f"http://127.0.0.1:{web}"
        with (output / "server.log").open("w", encoding="utf-8") as log:
            server = subprocess.Popen(
                [sys.executable, "-u", "-m", "tools.sandbox_server"],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    if server.poll() is not None:
                        raise RuntimeError("Isolated server exited; see server.log")
                    try:
                        with urllib.request.urlopen(
                            base + "/api/health", timeout=1
                        ) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        time.sleep(0.25)
                else:
                    raise TimeoutError("Isolated server did not become ready")
                evidence = output / "telemetry.json"
                subprocess.run(
                    [
                        sys.executable,
                        "tools/engineering-telemetry-smoke.py",
                        "--base",
                        base,
                        "--port",
                        str(udp),
                        "--output",
                        str(evidence),
                    ],
                    cwd=ROOT,
                    env=env,
                    check=True,
                )
                session = json.loads(evidence.read_text())["session_id"]
                env.update(
                    PITBOX_ENGINEERING_URL=base,
                    PITBOX_ENGINEERING_SESSION=session,
                    PITBOX_ENGINEERING_EVIDENCE=str(output / "browser"),
                )
                subprocess.run(
                    ["node", "tools/engineering-ui-smoke.cjs"],
                    cwd=ROOT,
                    env=env,
                    check=True,
                )
                subprocess.run(
                    ["node", "tools/engineering-groups-ui-smoke.cjs"],
                    cwd=ROOT,
                    env=env,
                    check=True,
                )
            finally:
                if os.name == "nt":
                    # The Windows venv launcher owns a child Python process.
                    subprocess.run(
                        ["taskkill", "/PID", str(server.pid), "/T", "/F"],
                        capture_output=True,
                        check=False,
                    )
                else:
                    server.terminate()
                server.wait(timeout=30)
    print("Engineering UDP and browser acceptance passed.")


if __name__ == "__main__":
    main()
