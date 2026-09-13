"""Failure-oriented portable checks for the installed Windows stress gate."""

from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest

from distribution.packaging import smoke_installer as smoke
from distribution.tests.test_installer_smoke import (
    FakeProcess,
    healthy,
    lifecycle,  # noqa: F401 - shared pytest fixture
    populate_database,
)

STRESS_UID = 123456789


def state(lap=1, packets=2000):
    return {"session_uid": STRESS_UID, "track_id": 11, "packet_format": 2026,
            "current_lap": lap, "packets_received": packets, "packets_dropped": 0,
            "strategy": {"available": True, "confidence": "low", "laps_remaining": 26-lap,
                         "recommended": {"projected_time_s": 90.0 * (26-lap),
                            "feasible": True, "legal": True, "finish_projection_valid": False,
                            "inventory_status": "unknown", "inventory_feasible": None,
                            "stops_remaining": 1, "stint_models": [{"laps": 26-lap}]}}}


@pytest.fixture
def stress_runtime(tmp_path, monkeypatch):
    app, emitter = FakeProcess(), FakeProcess()
    context = {"polls": 0, "exit_code": 0, "states": [state(1, 2000), state(13, 4000), state(25, 6000)]}

    def request(_port, route, **_kwargs):
        if route == "/api/health":
            return healthy(tmp_path)
        context["polls"] += 1
        if context["polls"] == 1:
            return {"session_uid": smoke.SESSION_UID, "packets_received": 160}
        index = min(context["polls"]-2, len(context["states"])-1)
        if index == len(context["states"])-1:
            emitter.returncode = context["exit_code"]
        return deepcopy(context["states"][index])

    def popen(command, **kwargs):
        context.update(command=command, kwargs=kwargs)
        return emitter

    monkeypatch.setattr(smoke, "request_json", request)
    monkeypatch.setattr(smoke.subprocess, "Popen", popen)
    monkeypatch.setattr(smoke.time, "sleep", lambda _: None)
    return app, emitter, context


def test_stress_uses_host_emitter_against_owned_udp_and_requires_real_progress(tmp_path, stress_runtime):
    app, emitter, context = stress_runtime
    result = smoke.exercise_stress_telemetry(app, 18000, 20799, "4.9.7", tmp_path, tmp_path)
    command = context["command"]
    assert command[:3] == [smoke.sys.executable, "-m", "tools.replay_demo"]
    assert command[command.index("--host")+1] == "127.0.0.1"
    assert command[command.index("--port")+1] == "20799"
    assert command[command.index("--laps")+1] == "25"
    assert command[command.index("--speed")+1] == "25"
    assert result["result"] == "passed" and result["strategy_samples"] == 3
    assert result["packets_increase"] == 5840 and result["emitter_exit_code"] == 0
    assert not app.terminated and not emitter.terminated
    assert (tmp_path / "stress-final-state.json").is_file()
    assert json.loads((tmp_path / "stress-summary.json").read_text())["result"] == "passed"


@pytest.mark.parametrize("failure", ["dropped", "conditional", "emitter"])
def test_stress_fails_and_preserves_diagnostics(tmp_path, stress_runtime, failure):
    app, emitter, context = stress_runtime
    if failure == "dropped":
        context["states"][0]["packets_dropped"] = 1
    elif failure == "conditional":
        context["states"][0]["strategy"]["recommended"]["inventory_feasible"] = True
    else:
        context["exit_code"] = 9
    with pytest.raises(smoke.SmokeFailure):
        smoke.exercise_stress_telemetry(app, 18000, 20799, "4.9.7", tmp_path, tmp_path)
    report = json.loads((tmp_path / "stress-summary.json").read_text())
    assert report["result"] == "failed" and report["error"]
    assert not app.terminated, "Only the emitter handle belongs to this helper."
    assert emitter.poll() is not None


def test_stress_timeout_terminates_only_its_emitter(tmp_path, stress_runtime, monkeypatch):
    app, emitter, _context = stress_runtime
    clock = iter([0., 0.1, 0.2, 0.3, 0.4, 0.5, 1.1, 1.2])
    monkeypatch.setattr(smoke.time, "monotonic", lambda: next(clock, 2.0))
    with pytest.raises(smoke.SmokeFailure, match="within"):
        smoke.exercise_stress_telemetry(app, 18000, 20799, "4.9.7", tmp_path, tmp_path, timeout=1)
    assert emitter.terminated and not app.terminated
    assert json.loads((tmp_path / "stress-summary.json").read_text())["result"] == "failed"


def test_cleanup_failure_preserves_original_drop_failure(tmp_path, stress_runtime, monkeypatch):
    app, emitter, context = stress_runtime
    context["states"][0]["packets_dropped"] = 2
    monkeypatch.setattr(emitter, "terminate", lambda: (_ for _ in ()).throw(OSError("cleanup denied")))
    with pytest.raises(smoke.SmokeFailure, match="dropped 2"):
        smoke.exercise_stress_telemetry(app, 18000, 20799, "4.9.7", tmp_path, tmp_path)
    report = json.loads((tmp_path / "stress-summary.json").read_text())
    assert "cleanup denied" in report["emitter_cleanup_error"]
    assert "dropped 2" in report["error"]


def add_stress_record(data: Path, snapshots=5):
    capture = data / "captures" / "stress.pwcap"
    capture.write_bytes(b"stress capture bytes")
    with sqlite3.connect(data / "pitwall.sqlite3") as db:
        db.execute("INSERT INTO recorded_sessions VALUES (?, ?, 11, 2026)", ("stress-session", str(STRESS_UID)))
        db.execute("INSERT INTO raw_captures VALUES ('stress-session', 'stress.pwcap', 6000, 20000, 1)")
        db.execute("CREATE TABLE strategy_snapshots (session_uid INTEGER)")
        db.executemany("INSERT INTO strategy_snapshots VALUES (?)", [(STRESS_UID,)] * snapshots)


def test_stress_persistence_reads_distinct_session_and_snapshot_count(tmp_path):
    populate_database(tmp_path)
    add_stress_record(tmp_path)
    report = {}
    session, captures = smoke.verify_persistence(tmp_path, STRESS_UID, require_strategy=True,
                                                 diagnostics=tmp_path, report=report)
    assert session == "stress-session" and captures[0].name == "stress.pwcap"
    assert report["strategy_snapshot_count"] == 5
    integrity = json.loads((tmp_path / "database-integrity.json").read_text())
    assert integrity == {"check": "PRAGMA integrity_check", "rows": [["ok"]], "result": "passed"}


def test_stress_cannot_pass_with_only_the_short_fixture_snapshots(tmp_path):
    populate_database(tmp_path)
    add_stress_record(tmp_path, snapshots=0)
    with sqlite3.connect(tmp_path / "pitwall.sqlite3") as db:
        db.executemany("INSERT INTO strategy_snapshots VALUES (?)", [(smoke.SESSION_UID,)] * 10)
    with pytest.raises(smoke.SmokeFailure, match="only 0 strategy snapshots"):
        smoke.verify_persistence(tmp_path, STRESS_UID, require_strategy=True)


def test_corrupt_sqlite_fails_full_integrity_and_keeps_evidence(tmp_path):
    populate_database(tmp_path)
    database = tmp_path / "pitwall.sqlite3"
    database.write_bytes(database.read_bytes()[:512])
    with pytest.raises(smoke.SmokeFailure, match="integrity_check"):
        smoke.verify_persistence(tmp_path, diagnostics=tmp_path)
    report = json.loads((tmp_path / "database-integrity.json").read_text())
    assert report["result"] == "failed" and report["error"]
    assert database.exists(), "Failure evidence must remain for diagnosis."


def test_stress_lifecycle_reopens_both_sessions_and_preserves_both_captures(tmp_path, request, monkeypatch):
    installer, events, processes, context = request.getfixturevalue("lifecycle")
    original_request = smoke.request_json

    def request(port, route, **kwargs):
        if route == "/api/v1/sessions/stress-session":
            events.append(("GET", route))
            return {"session": {"game_session_uid": str(STRESS_UID), "track_id": 11}}
        return original_request(port, route, **kwargs)

    def exercise(_process, _web_port, _udp_port, _version, data_dir, _diagnostics):
        add_stress_record(data_dir)
        return {"result": "passed", "session_uid": STRESS_UID, "samples": []}

    monkeypatch.setattr(smoke, "request_json", request)
    monkeypatch.setattr(smoke, "exercise_stress_telemetry", exercise)
    diagnostics = smoke.run_smoke(installer, "4.9.7", tmp_path, stress_telemetry=True)
    summary = json.loads((diagnostics / "summary.json").read_text())
    assert summary["result"] == "passed"
    assert summary["stress"]["strategy_snapshot_count"] == 5
    assert ("GET", "/api/v1/sessions/smoke-session") in events
    assert ("GET", "/api/v1/sessions/stress-session") in events
    assert (diagnostics / "reopened-stress-session.json").is_file()
    assert (context["data"] / "captures/fixture.pwcap").read_bytes() == b"fixture capture bytes"
    assert (context["data"] / "captures/stress.pwcap").read_bytes() == b"stress capture bytes"
    assert len(processes) == 2 and all(process.returncode == 0 for process in processes)
    assert not (context["install"] / smoke.APP_EXE).exists()


def test_workflow_enables_stress_before_release():
    workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/windows-installer.yml").read_text()
    assert "--stress-telemetry" in workflow
    assert workflow.index("--stress-telemetry") < workflow.index("name: Attach the installer")
