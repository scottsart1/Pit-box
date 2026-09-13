"""Gate publication on the installed Windows artifact, not the source checkout.

Run only on a disposable GitHub-hosted Windows runner::

    python -m distribution.packaging.smoke_installer --installer <exe> --version <v>

The optional welcome dialog is skipped by seeding its ordinary per-data-dir
marker. The frozen entry point, integrity check, splash, HTTP server, real UDP
receiver, persistence, dashboard shutdown and silent uninstaller still run.
This is not a visual test of the first-run dialog, browser or audio hardware.
Nothing is recursively deleted; the unique test data remains for diagnostics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

APP_EXE = "Your Pit Box.exe"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{8C4B1E77-2A5D-4F3E-9B61-0D7A2F5C8E14}_is1"
SESSION_UID = 0x504954534D4F4B45


class SmokeFailure(RuntimeError):
    """An installed-artifact assertion failed."""


def require_disposable_runner() -> Path:
    if (
        sys.platform != "win32"
        or os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
    ):
        raise SmokeFailure("Installer smoke requires a disposable GitHub-hosted Windows runner")
    value = os.environ.get("RUNNER_TEMP")
    if not value or not Path(value).is_dir():
        raise SmokeFailure("RUNNER_TEMP must name an existing runner temporary directory")

    # Inno's AppId also owns an uninstall registry entry. A /DIR override alone
    # is not enough isolation if someone tries this on an existing installation.
    import winreg

    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(hive, UNINSTALL_KEY, 0, winreg.KEY_READ | view):
                    raise SmokeFailure("Refusing to replace an existing Your Pit Box installation")
            except FileNotFoundError:
                pass
    return Path(value).resolve()


def isolated_environment(data_dir: Path, web_port: int, udp_port: int) -> dict[str, str]:
    """Do not let runner credentials, .env defaults or source imports mask bugs."""
    excluded = {
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "KIMI_API_KEY",
        "MOONSHOT_API_KEY", "CUSTOM_LLM_API_KEY", "PYTHONPATH", "PYTHONHOME",
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    }
    env = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith("PITWALL_") and key.upper() not in excluded
    }
    env.update({
        "PITWALL_DATA_DIR": str(data_dir),
        "PITWALL_WEB_HOST": "127.0.0.1",
        "PITWALL_WEB_PORT": str(web_port),
        "PITWALL_UDP_BIND_HOST": "127.0.0.1",
        "PITWALL_UDP_PORT": str(udp_port),
        "PITWALL_OPEN_BROWSER": "false",
        "PITWALL_NATIVE_VOICE": "false",
        "PITWALL_WAKE_ENABLED": "false",
        "PITWALL_PROACTIVE_ENABLED": "false",
        "PITWALL_RAW_CAPTURE": "full",
        "NO_PROXY": "127.0.0.1,localhost",
        "PYTHONNOUSERSITE": "1",
    })
    return env


def reserve_port(kind: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, kind)
    if sys.platform == "win32":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    sock.bind(("127.0.0.1", 0))
    return sock


def request_json(port: int, route: str, *, method: str = "GET") -> dict:
    # Never send loopback test traffic through a system proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{route}", method=method,
        data=b"{}" if method == "POST" else None,
        headers={"Content-Type": "application/json"},
    )
    with opener.open(request, timeout=3) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise SmokeFailure(f"{route} did not return a JSON object")
    return payload


def assert_health(health: dict, version: str, data_dir: Path) -> None:
    if health.get("ok") is not True or health.get("version") != version:
        raise SmokeFailure(f"Wrong installed version/health: {health.get('version')!r}")
    database = Path(str(health.get("database", ""))).resolve()
    if database != (data_dir / "pitwall.sqlite3").resolve():
        raise SmokeFailure(f"Server is not using the isolated database: {database}")
    if health.get("openai_key_configured") or health.get("configured_llm_providers"):
        raise SmokeFailure("Smoke app unexpectedly loaded provider credentials")
    if health.get("udp_listener") is not True:
        raise SmokeFailure(f"Installed UDP listener failed: {health.get('last_error')}")


def wait_for_health(process, port: int, version: str, data_dir: Path, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    last_error = "no response"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SmokeFailure(f"Installed app exited before readiness: {process.returncode}")
        try:
            health = request_json(port, "/api/health")
        except (OSError, ValueError) as exc:
            last_error = str(exc)
            time.sleep(0.25)
            continue
        # A different server is not a transient startup failure. In particular,
        # never POST /api/shutdown to it during cleanup.
        assert_health(health, version, data_dir)
        return health
    raise SmokeFailure(f"Installed app did not become ready within {timeout}s: {last_error}")


def telemetry_packets(frame: int) -> list[bytes]:
    """Two invented cars serialized in the supported game's actual wire format."""
    from f1 import packets

    output = []
    for packet_type, packet_id in (
        (packets.PacketSessionData, 1),
        (packets.PacketParticipantsData, 4),
        (packets.PacketLapData, 2),
        (packets.PacketCarTelemetryData, 6),
    ):
        packet = packet_type()
        packet.header.packet_format = 2026
        packet.header.game_year = 25  # F1 25 running the 2026 Season Pack
        packet.header.game_major_version = 1
        packet.header.packet_version = 1
        packet.header.packet_id = packet_id
        packet.header.session_uid = SESSION_UID
        packet.header.session_time = 200 + frame / 20
        packet.header.frame_identifier = frame
        packet.header.overall_frame_identifier = frame
        packet.header.player_car_index = 0
        packet.header.secondary_player_car_index = 255
        if packet_id == 1:
            packet.track_id = 11  # Monza
            packet.track_length = 5793
            packet.total_laps = 10
            packet.session_type = 15  # Race
            packet.session_duration = 900
            packet.session_time_left = 700
            packet.pit_speed_limit = 80
        elif packet_id == 4:
            packet.num_active_cars = 2
            for index, name in enumerate((b"Smoke Driver", b"Smoke Rival")):
                driver = packet.participants[index]
                driver.name = name
                driver.driver_id = 255
                driver.race_number = index + 1
                driver.your_telemetry = 1
                driver.ai_controlled = index
        elif packet_id == 2:
            for index in range(2):
                lap = packet.lap_data[index]
                lap.current_lap_num = 3
                lap.car_position = 2 - index
                lap.grid_position = 2 - index
                lap.driver_status = 1
                lap.result_status = 2
                lap.lap_distance = 1000 + frame
                lap.total_distance = 11586 + 1000 + frame
                lap.last_lap_time_in_ms = 90000
                lap.current_lap_time_in_ms = 10000 + frame * 50
        else:
            for index in range(2):
                car = packet.car_telemetry_data[index]
                car.speed = 123 + index
                car.throttle = 0.5
                car.gear = 4
                car.engine_rpm = 9000
        output.append(bytes(packet.pack()))
    return output


def assert_telemetry(state: dict) -> None:
    expected = {
        "connected": True, "packet_format": 2026, "session_uid": SESSION_UID,
        "track_id": 11, "current_lap": 3, "player_position": 2, "speed_kph": 123,
    }
    mismatches = {key: (value, state.get(key)) for key, value in expected.items()
                  if state.get(key) != value}
    names = {driver.get("name") for driver in state.get("drivers", [])}
    if names != {"Smoke Driver", "Smoke Rival"}:
        mismatches["drivers"] = (["Smoke Driver", "Smoke Rival"], sorted(names))
    if mismatches:
        raise SmokeFailure(f"Installed telemetry did not match the transmitted fixture: {mismatches}")


def exercise_telemetry(process, web_port: int, udp_port: int, timeout: float = 15) -> dict:
    deadline = time.monotonic() + timeout
    last_error = "no state"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        frame = 1
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise SmokeFailure("Installed app exited while receiving telemetry")
            for packet in telemetry_packets(frame):
                sender.sendto(packet, ("127.0.0.1", udp_port))
            time.sleep(0.05)
            state = request_json(web_port, "/api/state")
            try:
                assert_telemetry(state)
                if frame >= 40:
                    return state
            except SmokeFailure as exc:
                last_error = str(exc)
            frame += 1
    raise SmokeFailure(last_error)


def file_digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def save_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def verify_persistence(data_dir: Path) -> tuple[str, list[Path]]:
    """An empty but valid startup schema is not proof that telemetry persisted."""
    database = data_dir / "pitwall.sqlite3"
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise SmokeFailure("Recorded database failed SQLite quick_check")
        session = connection.execute(
            "SELECT id, track_id, packet_format FROM recorded_sessions WHERE game_session_uid=?",
            (str(SESSION_UID),),
        ).fetchone()
        if not session or session[1:] != (11, 2026):
            raise SmokeFailure("Transmitted session was not persisted with its track and packet format")
        captures = connection.execute(
            "SELECT relative_path FROM raw_captures WHERE session_id=? "
            "AND packet_count>0 AND byte_count>0 AND clean_close=1",
            (session[0],),
        ).fetchall()
        if not captures:
            raise SmokeFailure("Transmitted session has no finalized nonempty raw capture")
    capture_root = (data_dir / "captures").resolve()
    paths = [(capture_root / row[0]).resolve() for row in captures]
    for path in paths:
        if not path.is_relative_to(capture_root) or not path.is_file() or path.stat().st_size == 0:
            raise SmokeFailure("Catalogued raw capture is missing, empty or outside isolated data")
    return session[0], paths


def stop_owned_server(process, web_port: int, version: str, data_dir: Path) -> None:
    assert_health(request_json(web_port, "/api/health"), version, data_dir)
    if request_json(web_port, "/api/shutdown", method="POST").get("stopping") is not True:
        raise SmokeFailure("Dashboard shutdown was not acknowledged")
    process.wait(timeout=30)
    if process.returncode != 0:
        raise SmokeFailure(f"Installed app shutdown exit code: {process.returncode}")


def run_smoke(installer: Path, version: str, runner_temp: Path, timeout: float = 120) -> Path:
    """Run after require_disposable_runner; return durable-in-job diagnostics."""
    installer = installer.resolve(strict=True)
    root = Path(tempfile.mkdtemp(prefix="pitwall-installed-smoke-", dir=runner_temp)).resolve()
    install_dir, data_dir, diagnostics = root / "app", root / "data", root / "diagnostics"
    diagnostics.mkdir()
    data_dir.mkdir()
    # This is the real free-edition "already chose Skip for now" state, not an
    # integrity/license bypass. First-run UI remains a separate manual QA step.
    (data_dir / "license").mkdir()
    (data_dir / "license" / "welcome_shown").write_text("shown\n", encoding="utf-8")
    sentinel = data_dir / "existing-session-sentinel.txt"
    sentinel.write_text("Pre-existing test data must survive uninstall.\n", encoding="utf-8")
    preserved = {sentinel: file_digest(sentinel)}
    summary = {"version": version, "installer_sha256": file_digest(installer),
               "root": str(root), "result": "running", "checks": []}
    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with Path(output_file).open("a", encoding="utf-8") as output:
            output.write(f"diagnostics={diagnostics}\n")
    print(f"Installed-artifact smoke diagnostics: {diagnostics}", flush=True)
    executable, uninstaller = install_dir / APP_EXE, install_dir / "unins000.exe"
    process = None
    web_reservation = reserve_port(socket.SOCK_STREAM)
    udp_reservation = reserve_port(socket.SOCK_DGRAM)
    web_port = web_reservation.getsockname()[1]
    udp_port = udp_reservation.getsockname()[1]
    env = isolated_environment(data_dir, web_port, udp_port)
    try:
        subprocess.run([
            str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
            "/NOICONS", "/TASKS=", f"/DIR={install_dir}",
            f"/LOG={diagnostics / 'install.log'}",
        ], check=True, timeout=180, cwd=root, env=env)
        if not executable.is_file() or not uninstaller.is_file():
            raise SmokeFailure("Installer did not create the app and uninstaller at the isolated target")
        summary["checks"].append("silent_install")
        web_reservation.close()
        udp_reservation.close()
        # Crucially: not `python -m pitwall.main`, and not cwd=the checkout.
        process = subprocess.Popen([str(executable)], cwd=install_dir, env=env)
        health = wait_for_health(process, web_port, version, data_dir, timeout)
        save_json(diagnostics / "health.json", health)
        summary["checks"].append("frozen_startup_and_isolated_health")
        state = exercise_telemetry(process, web_port, udp_port)
        save_json(diagnostics / "telemetry-state.json", state)
        summary["checks"].append("real_f1_2026_udp_to_live_state")
        stop_owned_server(process, web_port, version, data_dir)
        summary["checks"].append("graceful_dashboard_shutdown")
        session_id, captures = verify_persistence(data_dir)
        summary["session_id"] = session_id
        summary["checks"].append("persisted_session_and_finalized_capture")
        # Reload through the installed product's API, not just a direct SQLite
        # query. Do not transmit anything on this second launch.
        process = subprocess.Popen([str(executable)], cwd=install_dir, env=env)
        wait_for_health(process, web_port, version, data_dir, timeout)
        reopened = request_json(web_port, f"/api/v1/sessions/{session_id}")
        save_json(diagnostics / "reopened-session.json", reopened)
        session = reopened.get("session", {})
        if str(session.get("game_session_uid")) != str(SESSION_UID) or session.get("track_id") != 11:
            raise SmokeFailure("Installed app did not reload the recorded fixture session")
        stop_owned_server(process, web_port, version, data_dir)
        summary["checks"].append("relaunch_and_read_recorded_session")
        database = data_dir / "pitwall.sqlite3"
        preserved[database] = file_digest(database)
        for path in captures:
            preserved[path] = file_digest(path)
    except Exception as exc:
        summary["result"] = "failed"
        summary["error"] = str(exc)
        (diagnostics / "failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
        raise
    finally:
        web_reservation.close()
        udp_reservation.close()
        cleanup_errors = []
        if process is not None and process.poll() is None:
            try:
                # Only a server identifying this exact random test data path
                # can receive a shutdown request. Never stop a port's stranger.
                assert_health(request_json(web_port, "/api/health"), version, data_dir)
                request_json(web_port, "/api/shutdown", method="POST")
                process.wait(timeout=30)
            except Exception:  # noqa: BLE001 - teardown must preserve the primary failure
                # Popen owns this process handle; no process-name/global kill.
                try:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)
                except Exception as exc:  # noqa: BLE001 - still collect logs and uninstall evidence
                    cleanup_errors.append(f"Could not stop owned app process: {exc}")
        try:
            if uninstaller.is_file():
                if not uninstaller.resolve().is_relative_to(root):
                    raise SmokeFailure("Uninstaller escaped the unique test directory")
                subprocess.run([
                    str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                    f"/LOG={diagnostics / 'uninstall.log'}",
                ], check=True, timeout=120, cwd=root, env=env)
                if executable.exists() or (install_dir / "_internal").exists():
                    raise SmokeFailure("Silent uninstall left installed application files behind")
                summary["checks"].append("silent_uninstall_removes_app")
            for path, digest in preserved.items():
                if not path.is_file() or file_digest(path) != digest:
                    raise SmokeFailure(f"Uninstall changed retained test data: {path.name}")
            summary["checks"].append("uninstall_preserves_existing_and_recorded_data")
        except Exception as exc:  # noqa: BLE001 - report cleanup alongside the original test failure
            cleanup_errors.append(str(exc))
        try:
            for log in data_dir.glob("pitwall.log*"):
                if log.is_file():
                    shutil.copy2(log, diagnostics / log.name)
        except OSError as exc:
            cleanup_errors.append(f"Could not copy application diagnostics: {exc}")
        if cleanup_errors:
            summary["cleanup_errors"] = cleanup_errors
        if summary["result"] == "running":
            summary["result"] = "failed" if cleanup_errors else "passed"
        save_json(diagnostics / "summary.json", summary)
        if cleanup_errors and "error" not in summary:
            raise SmokeFailure("; ".join(cleanup_errors))
    print(
        "Installed artifact: install -> launch -> UDP -> persist -> quit -> uninstall passed",
        flush=True,
    )
    return diagnostics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--startup-timeout", type=float, default=120)
    args = parser.parse_args(argv)
    # Python writes this console in the code page Windows hands it, which on a
    # runner is cp1252. Anything outside it raises while being printed, so a
    # non-ASCII character in a diagnostic would replace the failure it was
    # reporting with a UnicodeEncodeError. Say it in UTF-8, and never let the
    # reporting path be the thing that fails.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    runner_temp = require_disposable_runner()
    run_smoke(args.installer, args.version, runner_temp, args.startup_timeout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
