"""Cross-platform tests of the Windows gate; the workflow tests the real EXE."""

from __future__ import annotations

import json
import base64
import io
import socket
import sqlite3
import subprocess
import urllib.error
import urllib.parse
from pathlib import Path

import pytest
from f1 import packets

from distribution.launcher import WELCOME_MARKER
from distribution.packaging import smoke_installer as smoke


def healthy(data: Path, version: str = "4.9.7") -> dict:
    return {"ok": True, "version": version, "database": str(data / "pitwall.sqlite3"),
            "udp_listener": True, "configured_llm_providers": [],
            "openai_key_configured": False}


def fixture_state() -> dict:
    return {"connected": True, "packet_format": 2026, "session_uid": smoke.SESSION_UID,
            "track_id": 11, "current_lap": 3, "player_position": 2, "speed_kph": 123,
            "drivers": [{"name": "Smoke Driver"}, {"name": "Smoke Rival"}]}


def conditional_wet_strategy() -> dict:
    from pitwall.strategy import StrategyEngine

    race = {"mode_profile": "race", "tyre": {"compound": "MEDIUM"}}
    return {"strategy": {
        "available": True,
        "observed_compound_rule": StrategyEngine._compound_rule(race),
        "recommended": {
            "projected_time_s": 2400.0,
            "compound_rule": StrategyEngine._compound_rule(race, ["INTER"]),
            "instruction": "Legal only if the planned wet tyre is actually used.",
        },
    }}


def test_stress_gate_distinguishes_projected_and_observed_wet_waiver():
    # This is the contract of StrategyEngine._compound_rule(state, future):
    # the projected waiver is true while observed compliance remains false.
    assert smoke.assert_stress_strategy(conditional_wet_strategy())


@pytest.mark.parametrize("observed", [{"wet_waiver": True}, {}, None])
def test_stress_gate_rejects_completed_or_missing_wet_evidence(observed):
    state = conditional_wet_strategy()
    state["strategy"]["observed_compound_rule"] = observed
    with pytest.raises(smoke.SmokeFailure, match="observed evidence"):
        smoke.assert_stress_strategy(state)


def test_stress_gate_rejects_unqualified_future_wet_instruction():
    state = conditional_wet_strategy()
    state["strategy"]["recommended"]["instruction"] = "The compound requirement is already met."
    with pytest.raises(smoke.SmokeFailure, match="does not explain"):
        smoke.assert_stress_strategy(state)


def populate_database(data: Path) -> None:
    capture = data / "captures" / "fixture.pwcap"
    capture.parent.mkdir(exist_ok=True)
    capture.write_bytes(b"fixture capture bytes")
    with sqlite3.connect(data / "pitwall.sqlite3") as db:
        db.executescript(
            "CREATE TABLE IF NOT EXISTS recorded_sessions "
            "(id TEXT, game_session_uid TEXT, track_id INTEGER, packet_format INTEGER);"
            "CREATE TABLE IF NOT EXISTS raw_captures "
            "(session_id TEXT, relative_path TEXT, packet_count INTEGER, "
            "byte_count INTEGER, clean_close INTEGER);"
        )
        if not db.execute("SELECT 1 FROM recorded_sessions").fetchone():
            db.execute("INSERT INTO recorded_sessions VALUES (?, ?, 11, 2026)",
                       ("smoke-session", str(smoke.SESSION_UID)))
            db.execute("INSERT INTO raw_captures VALUES ('smoke-session', 'fixture.pwcap', 4, 21, 1)")


class FakeProcess:
    def __init__(self):
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("owned-test-process", timeout)
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    installer = tmp_path / "PitWall-Setup.exe"
    installer.write_bytes(b"fake installer; never executed")
    events, processes = [], []
    context = {"data": None, "corrupt_on_uninstall": False, "wrong_server": False,
               "fail_uninstall": False, "fail_fixture": False}

    def run(command, **kwargs):
        events.append(("run", command, kwargs))
        if command[0] == str(installer):
            install = Path(next(v.removeprefix("/DIR=") for v in command if v.startswith("/DIR=")))
            install.mkdir()
            (install / smoke.APP_EXE).write_bytes(b"fake frozen executable")
            (install / "unins000.exe").write_bytes(b"fake uninstaller")
            (install / "_internal").mkdir()
            context["install"] = install
        else:
            assert Path(command[0]) == context["install"] / "unins000.exe"
            if context["fail_uninstall"]:
                raise subprocess.CalledProcessError(1, command)
            (context["install"] / smoke.APP_EXE).unlink()
            (context["install"] / "_internal").rmdir()
            if context["corrupt_on_uninstall"]:
                (context["data"] / "existing-session-sentinel.txt").unlink()
        return subprocess.CompletedProcess(command, 0)

    def popen(command, **kwargs):
        events.append(("popen", command, kwargs))
        data = Path(kwargs["env"]["PITWALL_DATA_DIR"])
        context["data"] = data
        assert (data / "license" / WELCOME_MARKER).read_text() == "shown\n"
        populate_database(data)
        (data / "pitwall.log").write_text("fake app diagnostics\n")
        process = FakeProcess()
        processes.append(process)
        return process

    def request(port, route, *, method="GET"):
        events.append((method, route))
        if route == "/api/health":
            data = tmp_path / "somebody-elses-data" if context["wrong_server"] else context["data"]
            return healthy(data)
        if route == "/api/shutdown":
            processes[-1].returncode = 0
            return {"stopping": True}
        if route == "/api/v1/sessions/smoke-session":
            return {"session": {"game_session_uid": str(smoke.SESSION_UID), "track_id": 11}}
        raise AssertionError(route)

    def exercise(*_args):
        if context["fail_fixture"]:
            raise smoke.SmokeFailure("UDP fixture rejected")
        return fixture_state()

    monkeypatch.setattr(smoke.subprocess, "run", run)
    monkeypatch.setattr(smoke.subprocess, "Popen", popen)
    monkeypatch.setattr(smoke, "request_json", request)
    monkeypatch.setattr(smoke, "exercise_telemetry", exercise)
    def transfers(_port, previous_pin=None):
        events.append(("transfer", previous_pin))
        return {"result": "passed", "certificate_sha256": "a" * 64,
                "tls_and_qr_verified": True}
    monkeypatch.setattr(smoke, "exercise_transfers", transfers)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "outputs.txt"))
    return installer, events, processes, context


def test_environment_isolated_from_user_secrets_source_imports_and_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-inherit")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "do-not-inherit")
    monkeypatch.setenv("CUSTOM_LLM_API_KEY", "do-not-inherit")
    monkeypatch.setenv("https_proxy", "do-not-inherit")
    monkeypatch.setenv("ALL_PROXY", "do-not-inherit")
    monkeypatch.setenv("PYTHONPATH", "checkout/src")
    monkeypatch.setenv("PITWALL_DATA_DIR", "real-user-data")
    monkeypatch.setenv("PITWALL_WEB_LAN_ACCESS", "true")
    env = smoke.isolated_environment(tmp_path, 18000, 20790)
    assert "do-not-inherit" not in env.values()
    assert "PYTHONPATH" not in env
    assert "PITWALL_WEB_LAN_ACCESS" not in env
    assert env["PITWALL_DATA_DIR"] == str(tmp_path)
    assert env["PITWALL_OPEN_BROWSER"] == "false"
    assert env["PITWALL_WEB_PORT"] == "18000"
    assert env["PITWALL_UDP_PORT"] == "20790"


@pytest.mark.parametrize("field,value", [
    ("version", "older"), ("ok", False), ("database", "unrelated-data.sqlite3"),
    ("udp_listener", False), ("openai_key_configured", True),
    ("configured_llm_providers", ["openai"]),
])
def test_health_rejects_false_positive_servers(tmp_path, field, value):
    health = healthy(tmp_path)
    health[field] = value
    with pytest.raises(smoke.SmokeFailure):
        smoke.assert_health(health, "4.9.7", tmp_path)


def test_refuses_non_disposable_runner(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "false")
    with pytest.raises(smoke.SmokeFailure, match="disposable"):
        smoke.require_disposable_runner()


def test_wire_fixture_is_real_f1_2026_and_advances_frames():
    types = (packets.PacketSessionData, packets.PacketParticipantsData,
             packets.PacketLapData, packets.PacketCarTelemetryData)
    decoded = [cls.unpack(raw) for cls, raw in zip(types, smoke.telemetry_packets(42))]
    assert [packet.header.packet_id for packet in decoded] == [1, 4, 2, 6]
    assert all(packet.header.packet_format == 2026 for packet in decoded)
    assert all(packet.header.game_year == 25 for packet in decoded)
    assert all(packet.header.overall_frame_identifier == 42 for packet in decoded)
    assert decoded[0].track_id == 11
    assert decoded[1].num_active_cars == 2
    assert decoded[1].participants[0].name == b"Smoke Driver"
    assert decoded[2].lap_data[0].car_position == 2
    assert decoded[3].car_telemetry_data[0].speed == 123


def test_assert_telemetry_checks_exact_output_not_just_connectivity():
    smoke.assert_telemetry(fixture_state())
    state = fixture_state()
    state["speed_kph"] = 999
    with pytest.raises(smoke.SmokeFailure, match="speed_kph"):
        smoke.assert_telemetry(state)


def test_reserved_ports_are_loopback_and_unavailable_until_release():
    with smoke.reserve_port(socket.SOCK_DGRAM) as reserved:
        host, port = reserved.getsockname()
        assert host == "127.0.0.1" and port > 0
        with (socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as competitor,
              pytest.raises(OSError)):
            competitor.bind((host, port))


def test_lifecycle_installs_exact_file_launches_frozen_reopens_and_preserves_data(tmp_path, lifecycle):
    installer, events, processes, context = lifecycle
    diagnostics = smoke.run_smoke(installer, "4.9.7", tmp_path)
    summary = json.loads((diagnostics / "summary.json").read_text())
    assert summary["result"] == "passed"
    assert summary["installer_sha256"] == smoke.file_digest(installer)
    assert len(processes) == 2 and all(p.returncode == 0 for p in processes)
    assert not any(p.terminated or p.killed for p in processes)
    launches = [event for event in events if event[0] == "popen"]
    for _, command, kwargs in launches:
        assert command == [str(context["install"] / smoke.APP_EXE)]
        assert kwargs["cwd"] == context["install"]
        assert "PYTHONPATH" not in kwargs["env"]
    assert events[0][1][0] == str(installer)
    assert "/VERYSILENT" in events[0][1] and "/NOICONS" in events[0][1]
    assert "relaunch_and_read_recorded_session" in summary["checks"]
    assert [event for event in events if event[0] == "transfer"] == [
        ("transfer", None), ("transfer", "a" * 64),
    ]
    assert "uninstall_preserves_existing_and_recorded_data" in summary["checks"]
    assert (diagnostics / "pitwall.log").exists()
    assert (diagnostics / "reopened-session.json").exists()
    assert (context["data"] / "pitwall.sqlite3").is_file()
    assert not (context["install"] / smoke.APP_EXE).exists()
    assert str(diagnostics) in (tmp_path / "outputs.txt").read_text()


def test_failure_preserves_diagnostics_and_still_uninstalls(tmp_path, lifecycle):
    installer, _, processes, context = lifecycle
    context["fail_fixture"] = True
    with pytest.raises(smoke.SmokeFailure, match="UDP fixture"):
        smoke.run_smoke(installer, "4.9.7", tmp_path)
    diagnostics = context["data"].parent / "diagnostics"
    summary = json.loads((diagnostics / "summary.json").read_text())
    assert summary["result"] == "failed"
    assert (diagnostics / "failure.txt").is_file()
    assert (diagnostics / "pitwall.log").is_file()
    assert not (context["install"] / smoke.APP_EXE).exists()
    assert processes[0].returncode == 0


def test_diagnostics_copy_failure_does_not_replace_original_failure(tmp_path, lifecycle, monkeypatch):
    installer, _, _, context = lifecycle
    context["fail_fixture"] = True

    def fail_copy(*_args):
        raise OSError("diagnostic copy error")

    monkeypatch.setattr(smoke.shutil, "copy2", fail_copy)
    with pytest.raises(smoke.SmokeFailure, match="UDP fixture rejected"):
        smoke.run_smoke(installer, "4.9.7", tmp_path)
    summary = json.loads((context["data"].parent / "diagnostics" / "summary.json").read_text())
    assert "UDP fixture rejected" in summary["error"]
    assert "diagnostic copy error" in summary["cleanup_errors"][0]


def test_never_shutdown_unrelated_server_and_only_terminate_owned_process(tmp_path, lifecycle):
    installer, events, processes, context = lifecycle
    context["wrong_server"] = True
    with pytest.raises(smoke.SmokeFailure, match="isolated database"):
        smoke.run_smoke(installer, "4.9.7", tmp_path)
    assert ("POST", "/api/shutdown") not in events
    assert processes[0].terminated


@pytest.mark.parametrize("failure", ["corrupt_on_uninstall", "fail_uninstall"])
def test_uninstall_failure_or_retained_data_loss_fails_gate(tmp_path, lifecycle, failure):
    installer, _, _, context = lifecycle
    context[failure] = True
    with pytest.raises(smoke.SmokeFailure):
        smoke.run_smoke(installer, "4.9.7", tmp_path)
    summary = json.loads((context["data"].parent / "diagnostics" / "summary.json").read_text())
    assert summary["result"] == "failed" and summary["cleanup_errors"]


@pytest.mark.parametrize("missing", ["session", "capture", "file"])
def test_persistence_requires_real_recorded_fixture_not_empty_schema(tmp_path, missing):
    populate_database(tmp_path)
    with sqlite3.connect(tmp_path / "pitwall.sqlite3") as db:
        if missing == "session":
            db.execute("DELETE FROM recorded_sessions")
        elif missing == "capture":
            db.execute("UPDATE raw_captures SET clean_close=0")
        else:
            (tmp_path / "captures" / "fixture.pwcap").unlink()
    with pytest.raises(smoke.SmokeFailure):
        smoke.verify_persistence(tmp_path)


def test_workflow_gates_both_upload_and_release_and_preserves_failure_diagnostics():
    workflow = (Path(__file__).resolve().parents[2] / ".github" / "workflows" /
                "windows-installer.yml").read_text()
    gate = workflow.index("name: Smoke-test the installed Windows artifact")
    artifact = workflow.index("name: PitWall-Setup-${{")
    release = workflow.index("name: Attach the installer to a GitHub Release")
    assert gate < artifact < release
    assert "always() && steps.smoke.outputs.diagnostics != ''" in workflow
    assert "if ($LASTEXITCODE -ne 0)" in workflow[gate:artifact]
    assert "github.event_name == 'workflow_dispatch' && inputs.attach_release" in workflow[release:]
    assert "pull_request:" in workflow
    assert "github.head_ref == 'codex/android-wifi-history'" in workflow
    assert "github.event.pull_request.head.repo.full_name == github.repository" in workflow


def test_installed_transfer_check_verifies_identity_qr_and_never_saves_invitation(monkeypatch):
    import qrcode
    from qrcode.image.svg import SvgPathImage

    pin = "a" * 64
    endpoint = "https://192.168.10.5:20778"
    token = "private-invitation-fixture-token"
    payload = {"endpoint": endpoint, "certificate_sha256": pin, "token": token}
    invitation = "pitwall-pair://" + base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    uri = "pitwall://pair?invite=" + urllib.parse.quote(invitation, safe="")
    invite = {"invitation": invitation, "endpoint": endpoint, "pairing_uri": uri,
              "qr_svg": qrcode.make(uri, image_factory=SvgPathImage).to_string().decode()}
    status = {"running": True, "endpoint": endpoint, "certificate_sha256": pin,
              "device_id": "b" * 32}
    routes = []

    def request(_port, route, *, method="GET", body=None, timeout=3):
        routes.append((method, route))
        if route.endswith("/status") or route.endswith("/stop"):
            return {"running": False}
        if route.endswith("/start"):
            assert timeout >= 20 and body["device_name"]
            return status
        if route.endswith("/invite"):
            return invite
        raise AssertionError(route)

    monkeypatch.setattr(smoke, "request_json", request)
    result = smoke.exercise_transfers(18000, pin)
    assert result["tls_and_qr_verified"] and result["identity_survived_process_restart"]
    assert result["paired_history_copy_tested"] is False
    assert token not in json.dumps(result) and invitation not in json.dumps(result)
    assert [route.rsplit("/", 1)[-1] for _, route in routes] == [
        "status", "start", "invite", "stop", "start", "stop",
    ]
    with pytest.raises(smoke.SmokeFailure, match="certificate changed"):
        smoke.exercise_transfers(18000, "c" * 64)
    assert routes[-1][1].endswith("/stop")


@pytest.mark.parametrize("code,status,allowed", [
    ("no_local_network", 422, True), ("tls_unavailable", 503, False),
    ("no_local_network", 500, False), ("not_found", 404, False),
])
def test_transfer_gate_only_records_real_no_lan_restriction(monkeypatch, code, status, allowed):
    def request(_port, route, **_kwargs):
        if route.endswith("/status"):
            return {"running": False}
        payload = io.BytesIO(json.dumps({"detail": {"code": code}}).encode())
        raise urllib.error.HTTPError("http://127.0.0.1/start", status, "fixture", {}, payload)

    monkeypatch.setattr(smoke, "request_json", request)
    if allowed:
        result = smoke.exercise_transfers(18000)
        assert result["result"] == "no_usable_private_lan"
        assert result["tls_and_qr_verified"] is False
    else:
        with pytest.raises(smoke.SmokeFailure, match="transfer startup failed"):
            smoke.exercise_transfers(18000)
