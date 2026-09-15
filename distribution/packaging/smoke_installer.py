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
import base64
import hashlib
import ipaddress
import json
import math
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
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


def request_json(
    port: int, route: str, *, method: str = "GET", body: dict | None = None,
    timeout: float = 3,
) -> dict:
    # Never send loopback test traffic through a system proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{route}", method=method,
        data=json.dumps(body or {}).encode() if method == "POST" else None,
        headers={"Content-Type": "application/json"},
    )
    with opener.open(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise SmokeFailure(f"{route} did not return a JSON object")
    return payload


def exercise_transfers(web_port: int, previous_pin: str | None = None) -> dict:
    """Exercise frozen TLS/QR imports through the installed app's real API.

    A hosted VM may have only virtual adapters, which the product deliberately
    excludes. Record that restriction explicitly; never substitute loopback or
    loosen the shipped networking policy to make a smoke check pass.
    """
    prefix = "/api/v1/transfers"
    if request_json(web_port, prefix + "/status").get("running") is not False:
        raise SmokeFailure("Installed transfer sharing is not opt-in")

    def post(route: str, body: dict | None = None) -> dict:
        # Windows discovery may need its full 20-second PowerShell budget.
        return request_json(web_port, prefix + route, method="POST", body=body, timeout=60)

    try:
        started = post("/start", {"device_name": "Pit Box installer smoke"})
    except urllib.error.HTTPError as error:
        try:
            detail = json.load(error).get("detail", {})
        except (ValueError, AttributeError):
            detail = {}
        if error.code == 422 and isinstance(detail, dict) and detail.get("code") == "no_local_network":
            return {"result": "no_usable_private_lan", "management_api_verified": True,
                    "tls_and_qr_verified": False, "paired_history_copy_tested": False}
        raise SmokeFailure(f"Installed transfer startup failed with HTTP {error.code}") from error

    try:
        endpoint = urllib.parse.urlsplit(str(started.get("endpoint", "")))
        try:
            address = ipaddress.IPv4Address(endpoint.hostname)
            private = any(address in ipaddress.IPv4Network(cidr)
                          for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
        except (ValueError, TypeError):
            private = False
        pin = started.get("certificate_sha256")
        if (started.get("running") is not True or endpoint.scheme != "https" or not private
                or not isinstance(pin, str) or not re.fullmatch(r"[0-9a-f]{64}", pin)):
            raise SmokeFailure("Installed transfer listener did not expose a private HTTPS certificate")
        if previous_pin is not None and pin != previous_pin:
            raise SmokeFailure("Installed transfer certificate changed after app restart")

        invite = post("/invite")
        raw = invite.get("invitation", "")
        if not isinstance(raw, str) or not raw.startswith("pitwall-pair://"):
            raise SmokeFailure("Installed transfer invitation is invalid")
        encoded = raw[len("pitwall-pair://"):]
        payload = json.loads(base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True))
        uri = urllib.parse.urlsplit(invite.get("pairing_uri", ""))
        if (payload.get("certificate_sha256") != pin
                or payload.get("endpoint") != started["endpoint"]
                or invite.get("endpoint") != started["endpoint"]
                or uri.scheme != "pitwall" or uri.netloc != "pair"
                or urllib.parse.parse_qs(uri.query).get("invite") != [raw]):
            raise SmokeFailure("Installed invitation does not match its certificate or pairing link")
        qr = ET.fromstring(invite.get("qr_svg", ""))
        if (qr.tag != "{http://www.w3.org/2000/svg}svg"
                or qr.find(".//{http://www.w3.org/2000/svg}path") is None):
            raise SmokeFailure("Installed QR renderer did not produce a usable SVG")
        if post("/stop").get("running") is not False:
            raise SmokeFailure("Installed transfer listener did not stop")
        restarted = post("/start", {"device_name": "Pit Box installer smoke"})
        if (restarted.get("running") is not True or restarted.get("certificate_sha256") != pin
                or restarted.get("device_id") != started.get("device_id")):
            raise SmokeFailure("Installed transfer identity did not survive listener restart")
        # Fingerprints are public identity data; no invitation or token enters
        # the retained report, process output or assertion messages.
        return {"result": "passed", "management_api_verified": True,
                "tls_and_qr_verified": True, "certificate_sha256": pin,
                "identity_survived_listener_restart": True,
                "identity_survived_process_restart": previous_pin is not None,
                "paired_history_copy_tested": False}
    finally:
        if post("/stop").get("running") is not False:
            raise SmokeFailure("Installed transfer listener cleanup failed")


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


def assert_stress_strategy(state: dict) -> bool:
    """Check internal claims, allowing honestly conditional/unfeasible plans."""
    strategy = state.get("strategy") or {}
    rec = strategy.get("recommended") or {}
    if strategy.get("available") is not True or not rec:
        return False
    projected = rec.get("projected_time_s")
    if not isinstance(projected, (int, float)) or not math.isfinite(projected):
        raise SmokeFailure("Stress strategy has a missing/nonfinite projected time")
    if rec.get("finish_projection_valid") is True and (
        rec.get("feasible") is not True or rec.get("legal") is not True
    ):
        raise SmokeFailure("Stress strategy claims a valid finish for an infeasible/illegal plan")
    if (rec.get("inventory_status") == "unknown" and rec.get("stops_remaining", 0) > 0
            and (rec.get("inventory_feasible") is not None or strategy.get("confidence") != "low")):
        raise SmokeFailure("Stress strategy presents unknown spare inventory as confirmed")
    rule = rec.get("compound_rule") or {}
    if rule.get("conditional_on_future_wet_use") and rule.get("wet_waiver"):
        raise SmokeFailure("Stress strategy counts planned wet use as already completed")
    for name in ("position_probabilities", "outcome_distribution"):
        probabilities = rec.get(name) or {}
        if probabilities:
            values = list(probabilities.values())
            if (any(not isinstance(value, (int, float)) or not math.isfinite(value)
                    or not 0 <= value <= 1 for value in values)
                    or not math.isclose(sum(values), 1.0, abs_tol=0.005)):
                raise SmokeFailure(f"Stress strategy has an incoherent {name}")
    stints = rec.get("stint_models") or []
    if stints and sum(int(stint.get("laps", 0)) for stint in stints) != strategy.get("laps_remaining"):
        raise SmokeFailure("Stress strategy stint lengths do not cover its stated remaining distance")
    return True


def exercise_stress_telemetry(
    process, web_port: int, udp_port: int, version: str, data_dir: Path,
    diagnostics: Path, *, timeout: float = 240,
) -> dict:
    """Run a complete synthetic race against the already-running owned EXE.

    Only the emitter imports checkout source. It never starts a second server
    or points at a pre-existing data directory. Every process handle is owned.
    """
    checkout = Path(__file__).resolve().parents[2]
    report = {"result": "running", "laps": 25, "speed": 25, "circuit": "monza",
              "samples": [], "strategy_samples": 0}
    emitter = None
    started = time.monotonic()
    deadline = started + timeout
    uid = None
    latencies = []
    try:
        baseline = request_json(web_port, "/api/state")
        received_before = int(baseline.get("packets_received", 0))
        env = isolated_environment(data_dir, web_port, udp_port)
        env.update(PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        command = [sys.executable, "-m", "tools.replay_demo", "--host", "127.0.0.1",
                   "--port", str(udp_port), "--laps", "25", "--speed", "25",
                   "--circuit", "monza", "--seed", "7",
                   "--summary-output", str(diagnostics / "stress-emitter-summary.json")]
        with (diagnostics / "stress-emitter.log").open("w", encoding="utf-8") as log:
            emitter = subprocess.Popen(command, cwd=checkout, env=env, stdout=log, stderr=subprocess.STDOUT)
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise SmokeFailure("Installed app exited during stress telemetry")
                poll_started = time.monotonic()
                assert_health(request_json(web_port, "/api/health"), version, data_dir)
                state = request_json(web_port, "/api/state")
                latencies.append(time.monotonic() - poll_started)
                received = int(state.get("packets_received", 0))
                # This is the application's counter, not proof of zero UDP
                # loss in the OS/network (the wire protocol has no ACK).
                dropped = int(state.get("packets_dropped", 0))
                if dropped:
                    raise SmokeFailure(f"Installed app reported dropped {dropped} packets during stress telemetry")
                current_uid = state.get("session_uid")
                if current_uid and current_uid != SESSION_UID:
                    if uid is None:
                        uid = current_uid
                    if current_uid != uid or state.get("track_id") != 11 or state.get("packet_format") != 2026:
                        raise SmokeFailure("Stress telemetry changed session identity or wire/circuit fixture")
                    report["strategy_samples"] += int(assert_stress_strategy(state))
                    report["samples"].append({
                        "elapsed_s": round(time.monotonic() - started, 3),
                        "lap": int(state.get("current_lap", 0)), "packets": received,
                        "reported_drops": dropped, "request_latency_s": round(latencies[-1], 4),
                    })
                    report.update(session_uid=uid, packets_received=received,
                                  packets_increase=received - received_before, reported_drops=dropped)
                exit_code = emitter.poll()
                if exit_code is not None:
                    report["emitter_exit_code"] = exit_code
                    if exit_code != 0:
                        raise SmokeFailure(f"Synthetic stress emitter exited with code {exit_code}")
                    emitted = json.loads((diagnostics / "stress-emitter-summary.json").read_text(encoding="utf-8"))
                    expected_final = emitted.get("final_classification") or {}
                    if (emitted.get("session_uid") != uid or emitted.get("packet_format") != 2026
                            or emitted.get("player_car_index") != state.get("player_car_index")):
                        raise SmokeFailure("Stress final packet identity does not match the observed session/player")
                    if (expected_final.get("laps") != 25 or not 1 <= int(expected_final.get("position", 0)) <= 20):
                        raise SmokeFailure("Stress emitter did not send a classified 25-lap player finish")
                    actual_final = state.get("final_classification") or {}
                    if actual_final and actual_final != expected_final:
                        raise SmokeFailure("Stress final classification differs from the actual emitted packet")
                    if (uid is not None and state.get("current_lap", 0) >= 25
                            and received - received_before >= 1000 and report["strategy_samples"] >= 3
                            and actual_final == expected_final):
                        save_json(diagnostics / "stress-final-state.json", state)
                        report["final_classification"] = actual_final
                        report["classification_received"] = True
                        report["result"] = "passed"
                        return report
                time.sleep(0.5)
        raise SmokeFailure(f"Stress telemetry did not complete a verified 25-lap race within {timeout}s")
    except Exception as exc:
        report.update(result="failed", error=str(exc))
        raise
    finally:
        cleanup_error = None
        if emitter is not None:
            try:
                if emitter.poll() is None:
                    emitter.terminate()
                    try:
                        emitter.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        emitter.kill()
                        emitter.wait(timeout=10)
                report["emitter_exit_code"] = emitter.returncode
            except Exception as exc:  # noqa: BLE001 - keep the primary gate failure and diagnostics
                cleanup_error = str(exc)
                report.update(result="failed", emitter_cleanup_error=cleanup_error)
        report["elapsed_s"] = round(time.monotonic() - started, 3)
        if latencies:
            ordered = sorted(latencies)
            report["request_latency_max_s"] = round(ordered[-1], 4)
            report["request_latency_p95_s"] = round(ordered[math.ceil(len(ordered)*0.95)-1], 4)
        save_json(diagnostics / "stress-summary.json", report)
        if cleanup_error and "error" not in report:
            raise SmokeFailure(f"Could not stop owned stress emitter: {cleanup_error}")


def verify_persistence(
    data_dir: Path, session_uid: int = SESSION_UID, *, require_strategy: bool = False,
    diagnostics: Path | None = None, report: dict | None = None,
    expected_classification: dict | None = None,
) -> tuple[str, list[Path]]:
    """An empty but valid startup schema is not proof that telemetry persisted."""
    database = data_dir / "pitwall.sqlite3"
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchall()
        except sqlite3.DatabaseError as exc:
            if diagnostics is not None:
                save_json(diagnostics / "database-integrity.json", {
                    "check": "PRAGMA integrity_check", "result": "failed", "error": str(exc),
                })
            raise SmokeFailure(f"Recorded database failed SQLite integrity_check: {exc}") from exc
        if diagnostics is not None:
            save_json(diagnostics / "database-integrity.json", {
                "check": "PRAGMA integrity_check", "rows": [list(row) for row in integrity],
                "result": "passed" if integrity == [("ok",)] else "failed",
            })
        if integrity != [("ok",)]:
            raise SmokeFailure(f"Recorded database failed SQLite integrity_check: {integrity[:5]}")
        session = connection.execute(
            "SELECT id, track_id, packet_format FROM recorded_sessions WHERE game_session_uid=?",
            (str(session_uid),),
        ).fetchone()
        if not session or session[1:] != (11, 2026):
            raise SmokeFailure("Transmitted session was not persisted with its track and packet format")
        if expected_classification is not None:
            completed = connection.execute(
                "SELECT status, ended_at FROM recorded_sessions WHERE id=?", (session[0],),
            ).fetchone()
            legacy = connection.execute(
                "SELECT result_position, total_laps, ended_at FROM sessions WHERE session_uid=?", (session_uid,),
            ).fetchone()
            if (not completed or completed[0] != "complete" or not completed[1]
                    or not legacy or legacy[:2] != (expected_classification["position"], 25) or not legacy[2]):
                raise SmokeFailure("Stress final classification was not persisted as a completed session/result")
            if report is not None:
                report["classification_persisted"] = True
        captures = connection.execute(
            "SELECT relative_path FROM raw_captures WHERE session_id=? "
            "AND packet_count>0 AND byte_count>0 AND clean_close=1",
            (session[0],),
        ).fetchall()
        if not captures:
            raise SmokeFailure("Transmitted session has no finalized nonempty raw capture")
        if require_strategy:
            snapshots = connection.execute(
                "SELECT count(*) FROM strategy_snapshots WHERE session_uid=?", (session_uid,),
            ).fetchone()[0]
            if snapshots < 5:
                raise SmokeFailure(f"Stress session persisted only {snapshots} strategy snapshots; expected at least five")
            if report is not None:
                report.update(persisted_session_id=session[0], strategy_snapshot_count=snapshots,
                              finalized_capture_count=len(captures))
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


def run_smoke(
    installer: Path, version: str, runner_temp: Path, timeout: float = 120,
    *, stress_telemetry: bool = False,
) -> Path:
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
        transfer = exercise_transfers(web_port)
        summary["transfer"] = transfer
        save_json(diagnostics / "transfer-summary.json", transfer)
        summary["checks"].append("installed_transfer_management")
        state = exercise_telemetry(process, web_port, udp_port)
        save_json(diagnostics / "telemetry-state.json", state)
        summary["checks"].append("real_f1_2026_udp_to_live_state")
        stress = None
        if stress_telemetry:
            stress = exercise_stress_telemetry(process, web_port, udp_port, version, data_dir, diagnostics)
            summary["checks"].append("complete_25_lap_stress_telemetry")
        stop_owned_server(process, web_port, version, data_dir)
        summary["checks"].append("graceful_dashboard_shutdown")
        session_id, captures = verify_persistence(data_dir, diagnostics=diagnostics)
        summary["session_id"] = session_id
        summary["checks"].append("persisted_session_and_finalized_capture")
        sessions_to_reopen = [(session_id, SESSION_UID, "reopened-session.json")]
        if stress is not None:
            stress_id, stress_captures = verify_persistence(
                data_dir, int(stress["session_uid"]), require_strategy=True,
                diagnostics=diagnostics, report=stress,
                expected_classification=stress["final_classification"],
            )
            captures.extend(stress_captures)
            sessions_to_reopen.append((stress_id, int(stress["session_uid"]), "reopened-stress-session.json"))
            save_json(diagnostics / "stress-summary.json", stress)
            summary["stress"] = {key: value for key, value in stress.items() if key != "samples"}
            summary["checks"].append("stress_database_integrity_and_strategy_persistence")
        # Reload through the installed product's API, not just a direct SQLite
        # query. Do not transmit anything on this second launch.
        process = subprocess.Popen([str(executable)], cwd=install_dir, env=env)
        wait_for_health(process, web_port, version, data_dir, timeout)
        transfer = exercise_transfers(web_port, transfer.get("certificate_sha256"))
        summary["transfer_after_restart"] = transfer
        save_json(diagnostics / "transfer-after-restart-summary.json", transfer)
        for recorded_id, expected_uid, filename in sessions_to_reopen:
            reopened = request_json(web_port, f"/api/v1/sessions/{recorded_id}")
            save_json(diagnostics / filename, reopened)
            session = reopened.get("session", {})
            if str(session.get("game_session_uid")) != str(expected_uid) or session.get("track_id") != 11:
                raise SmokeFailure("Installed app did not reload the recorded fixture session")
            if (stress is not None and expected_uid == int(stress["session_uid"])
                    and (session.get("status") != "complete" or not session.get("ended_at"))):
                raise SmokeFailure("Installed app did not reload the stress session as classified/complete")
        stop_owned_server(process, web_port, version, data_dir)
        summary["checks"].append("relaunch_and_read_recorded_session")
        # Restart can also write the catalog. Recheck the whole database after
        # the second graceful exit, before declaring retention safe.
        verify_persistence(data_dir, diagnostics=diagnostics)
        if stress is not None:
            verify_persistence(
                data_dir, int(stress["session_uid"]), require_strategy=True,
                diagnostics=diagnostics, expected_classification=stress["final_classification"],
            )
            stress["classification_read_back"] = True
            save_json(diagnostics / "stress-summary.json", stress)
            summary["stress"] = {key: value for key, value in stress.items() if key != "samples"}
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
    parser.add_argument("--stress-telemetry", action="store_true",
                        help="Gate on a complete 25-lap synthetic race and full SQLite integrity")
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
    run_smoke(args.installer, args.version, runner_temp, args.startup_timeout,
              stress_telemetry=args.stress_telemetry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
