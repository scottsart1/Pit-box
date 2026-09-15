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


def final_classification():
    return {"position": 6, "laps": 25, "grid_position": 6, "points": 8,
            "pit_stops": 1, "best_lap_ms": 89000, "total_race_time_s": 2300., "penalties_s": 0}


def state(lap=1, packets=2000):
    return {"session_uid": STRESS_UID, "track_id": 11, "packet_format": 2026,
            "player_car_index": 5, "final_classification": final_classification() if lap == 25 else {},
            "current_lap": lap, "packets_received": packets, "packets_dropped": 0,
            "strategy": {"available": True, "confidence": "low", "laps_remaining": 26-lap,
                         "recommended": {"projected_time_s": 90.0 * (26-lap),
                            "feasible": True, "legal": True, "finish_projection_valid": False,
                            "inventory_status": "unknown", "inventory_feasible": None,
                            "stops_remaining": 1, "stint_models": [{"laps": 26-lap}]}}}


@pytest.fixture
def stress_runtime(tmp_path, monkeypatch):
    app, emitter = FakeProcess(), FakeProcess()
    context = {"polls": 0, "exit_code": 0, "exit_after": 3,
               "states": [state(1, 2000), state(13, 4000), state(25, 6000)]}

    def request(_port, route, **_kwargs):
        if route == "/api/health":
            return healthy(tmp_path)
        if route == "/api/v1/network/status":
            return {"datagrams": {"received": 6000, "parsed": 6000, "rejected": 0}}
        context["polls"] += 1
        if context["polls"] == 1:
            return {"session_uid": smoke.SESSION_UID, "packets_received": 160}
        index = min(context["polls"]-2, len(context["states"])-1)
        if index >= context["exit_after"] - 1:
            emitter.returncode = context["exit_code"]
        return deepcopy(context["states"][index])

    def popen(command, **kwargs):
        context.update(command=command, kwargs=kwargs)
        smoke.save_json(Path(command[command.index("--summary-output")+1]), {
            "session_uid": STRESS_UID, "packet_format": 2026, "player_car_index": 5,
            "final_classification": final_classification(),
        })
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
    assert result["classification_received"] and result["final_classification"] == final_classification()
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


def test_emitter_exit_alone_cannot_pass_without_receiving_final_classification(tmp_path, stress_runtime, monkeypatch):
    app, _, context = stress_runtime
    context["states"][-1]["final_classification"] = {}
    clock = iter(index * .05 for index in range(200))
    monkeypatch.setattr(smoke.time, "monotonic", lambda: next(clock, 10.))
    with pytest.raises(smoke.SmokeFailure, match="within"):
        smoke.exercise_stress_telemetry(app, 18000, 20799, "4.9.7", tmp_path, tmp_path, timeout=2)
    report = json.loads((tmp_path / "stress-summary.json").read_text())
    assert report["emitter_exit_code"] == 0 and report["result"] == "failed"
    assert not report.get("classification_received")


def test_late_final_packet_is_accepted_after_emitter_exits(tmp_path, stress_runtime):
    app, _, context = stress_runtime
    context["states"].append(state(25, 6001))
    context["states"][2]["final_classification"] = {}
    result = smoke.exercise_stress_telemetry(app, 18000, 20799, "4.9.7", tmp_path, tmp_path)
    assert result["strategy_samples"] == 4 and result["classification_received"]
    assert result["packets_received"] == 6001


@pytest.mark.parametrize("mismatch", ["session_uid", "classification"])
def test_final_packet_identity_and_result_must_match_emitter_evidence(tmp_path, stress_runtime, mismatch):
    app, _, context = stress_runtime
    if mismatch == "session_uid":
        for snapshot in context["states"]:
            snapshot["session_uid"] += 1
    else:
        context["states"][-1]["final_classification"]["position"] += 1
    with pytest.raises(smoke.SmokeFailure, match="final"):
        smoke.exercise_stress_telemetry(app, 18000, 20799, "4.9.7", tmp_path, tmp_path)
    assert json.loads((tmp_path / "stress-summary.json").read_text())["result"] == "failed"


def add_stress_record(data: Path, snapshots=5):
    capture = data / "captures" / "stress.pwcap"
    capture.write_bytes(b"stress capture bytes")
    with sqlite3.connect(data / "pitwall.sqlite3") as db:
        db.execute("INSERT INTO recorded_sessions VALUES (?, ?, 11, 2026)", ("stress-session", str(STRESS_UID)))
        db.execute("INSERT INTO raw_captures VALUES ('stress-session', 'stress.pwcap', 6000, 20000, 1)")
        db.execute("CREATE TABLE strategy_snapshots (session_uid INTEGER)")
        db.executemany("INSERT INTO strategy_snapshots VALUES (?)", [(STRESS_UID,)] * snapshots)
        db.execute("ALTER TABLE recorded_sessions ADD COLUMN status TEXT DEFAULT 'recording'")
        db.execute("ALTER TABLE recorded_sessions ADD COLUMN ended_at TEXT")
        db.execute("UPDATE recorded_sessions SET status='complete', ended_at='2026-09-13' WHERE id='stress-session'")
        db.execute("CREATE TABLE sessions (session_uid INTEGER, result_position INTEGER, total_laps INTEGER, ended_at REAL)")
        db.execute("INSERT INTO sessions VALUES (?, 6, 25, 1000.)", (STRESS_UID,))


def test_stress_persistence_reads_distinct_session_and_snapshot_count(tmp_path):
    populate_database(tmp_path)
    add_stress_record(tmp_path)
    report = {}
    session, captures = smoke.verify_persistence(tmp_path, STRESS_UID, require_strategy=True,
                                                 diagnostics=tmp_path, report=report,
                                                 expected_classification=final_classification())
    assert session == "stress-session" and captures[0].name == "stress.pwcap"
    assert report["strategy_snapshot_count"] == 5
    assert report["classification_persisted"]
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


def test_missing_persisted_result_cannot_pass_even_with_snapshots_and_capture(tmp_path):
    populate_database(tmp_path)
    add_stress_record(tmp_path)
    with sqlite3.connect(tmp_path / "pitwall.sqlite3") as db:
        db.execute("UPDATE sessions SET result_position=NULL")
    with pytest.raises(smoke.SmokeFailure, match="final classification was not persisted"):
        smoke.verify_persistence(tmp_path, STRESS_UID, require_strategy=True,
                                 expected_classification=final_classification())


@pytest.mark.parametrize("reopened_status", ["complete", "recording"])
def test_stress_lifecycle_reopens_both_sessions_and_preserves_both_captures(tmp_path, request, monkeypatch, reopened_status):
    installer, events, processes, context = request.getfixturevalue("lifecycle")
    original_request = smoke.request_json

    def request(port, route, **kwargs):
        if route == "/api/v1/sessions/stress-session":
            events.append(("GET", route))
            return {"session": {"game_session_uid": str(STRESS_UID), "track_id": 11,
                                "status": reopened_status, "ended_at": "2026-09-13"}}
        return original_request(port, route, **kwargs)

    def exercise(_process, _web_port, _udp_port, _version, data_dir, _diagnostics):
        add_stress_record(data_dir)
        return {"result": "passed", "session_uid": STRESS_UID, "samples": [],
                "final_classification": final_classification()}

    monkeypatch.setattr(smoke, "request_json", request)
    monkeypatch.setattr(smoke, "exercise_stress_telemetry", exercise)
    if reopened_status != "complete":
        with pytest.raises(smoke.SmokeFailure, match="did not reload the stress session as classified"):
            smoke.run_smoke(installer, "4.9.7", tmp_path, stress_telemetry=True)
        assert (context["data"].parent / "diagnostics/failure.txt").is_file()
        return
    diagnostics = smoke.run_smoke(installer, "4.9.7", tmp_path, stress_telemetry=True)
    summary = json.loads((diagnostics / "summary.json").read_text())
    assert summary["result"] == "passed"
    assert summary["stress"]["strategy_snapshot_count"] == 5
    assert summary["stress"]["classification_read_back"]
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


def test_emitter_summary_describes_the_real_final_datagram_sent(tmp_path, monkeypatch):
    from f1.packets import PacketFinalClassificationData, PacketHeader

    from tools import replay_demo

    observed = {"uids": set(), "final_bytes": []}

    class OwnedSocket:
        def sendto(self, raw, target):
            assert target == ("127.0.0.1", 20799)
            header = PacketHeader.unpack(raw[:PacketHeader.size()])
            observed["uids"].add(int(header.session_uid))
            if header.packet_id == 8:
                observed["final"] = PacketFinalClassificationData.unpack(raw)
                observed["final_bytes"].append(raw)
            return len(raw)

    monkeypatch.setattr(replay_demo.socket, "socket", lambda *_: OwnedSocket())
    monkeypatch.setattr(replay_demo.time, "sleep", lambda _: None)
    output = tmp_path / "emitted.json"
    replay_demo.run("127.0.0.1", 20799, 1, 25, 7, summary_output=output)
    emitted = json.loads(output.read_text())
    packet = observed["final"]
    player = packet.classification_data[packet.header.player_car_index]
    assert observed["uids"] == {emitted["session_uid"]}
    assert emitted["packet_format"] == 2026
    assert emitted["player_car_index"] == packet.header.player_car_index
    assert emitted["final_classification"]["laps"] == player.num_laps == 1
    assert emitted["final_classification"]["position"] == player.position
    assert emitted["final_classification"]["total_race_time_s"] == player.total_race_time
    assert emitted["final_classification_datagrams"] == len(observed["final_bytes"]) == 21
    assert len(set(observed["final_bytes"])) == 1, "Terminal repeats must contain the identical result"


@pytest.mark.asyncio
async def test_repeated_terminal_datagrams_persist_and_emit_finish_once():
    from f1.packets import PacketFinalClassificationData
    from pitwall.state import StateStore
    from pitwall.udp import F1DatagramProtocol
    from tools import replay_demo

    store = StateStore()
    persisted = []

    async def persist():
        persisted.append((await store.snapshot_live())["final_classification"])

    protocol = F1DatagramProtocol(store, on_final_classification=persist)
    cars = [replay_demo.Car(index, spec) for index, spec in enumerate(replay_demo.GRID)]
    for car in cars:
        car.lap = 26
    raw = replay_demo.build_final_classification(cars, 2110.0, 22000, 25)
    for _ in range(21):
        await protocol._handle(PacketFinalClassificationData.unpack(raw))
    state = await store.snapshot_live()
    assert len(persisted) == 1 and persisted[0]["laps"] == 25
    assert state["final_classification"] == persisted[0]
    assert sum(event["type"] == "CHQF" for event in state["events_log"]) == 1
