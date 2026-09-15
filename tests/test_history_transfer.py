from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from pitwall.database import PitWallDatabase
from pitwall.history_transfer import HistoryTransferError, HistoryTransferService
from pitwall.trace_store import TraceStore


async def _installation(root: Path) -> tuple[PitWallDatabase, HistoryTransferService]:
    root.mkdir()
    database = PitWallDatabase(root / "pitwall.sqlite3")
    await database.initialize()
    return database, HistoryTransferService(database.path, root)


async def _session(database: PitWallDatabase, uid: int, *, complete: bool = True, trace: bool = False) -> str:
    state = {"session_uid": uid, "track_id": 10, "track_name": "Spa",
             "session_type": "Race", "mode_profile": "race", "total_laps": 3,
             "car_setup": {}, "player_car_index": 0}
    await database.upsert_session(state)
    session_key = await database.catalog.upsert_live_session(state)
    lap_id = await database.save_lap({"session_uid": uid, "track_id": 10,
        "track_name": "Spa", "session_type": "Race", "mode_profile": "race",
        "lap_num": 1, "lap_time_ms": 92001, "valid": True, "compound": "MEDIUM",
        "trace": [{"d": 0.0, "t": 0.0}, {"d": 50.0, "t": 1.0}]}, [])
    await database.add_feedback(uid, 10, "handling", "Rear slides on exit")
    if trace:
        with sqlite3.connect(database.path) as db:
            car_id = db.execute("SELECT session_car_id FROM recorded_laps WHERE id=?", (lap_id,)).fetchone()[0]
        store = TraceStore(database.path.parent / "traces")
        store.append_samples(car_id, "telemetry", {"distance": [0.0, 50.0], "speed": [40.0, 45.0]}, axis_field="distance")
        manifest = store.finalize_lap(lap_id, session_car_id=car_id)
        await database.catalog.register_trace_manifest(session_key, manifest)
    if complete:
        await database.catalog.finalize_session(session_key)
        with sqlite3.connect(database.path) as db:
            db.execute("UPDATE sessions SET ended_at=started_at+300 WHERE session_uid=?", (uid,))
    return session_key


def _count(database: PitWallDatabase, table: str) -> int:
    with sqlite3.connect(database.path) as db:
        return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _rewrite(path: Path, *, row_change=None, manifest_change=None, extra=None) -> Path:
    with zipfile.ZipFile(path) as z:
        content = {name: z.read(name) for name in z.namelist()}
    manifest = json.loads(content["manifest.json"])
    if row_change:
        rows = [json.loads(line) for line in content["rows.jsonl"].splitlines()]
        row_change(rows)
        content["rows.jsonl"] = b"".join(json.dumps(r).encode() + b"\n" for r in rows)
        manifest["rows_bytes"] = len(content["rows.jsonl"])
        manifest["rows_sha256"] = hashlib.sha256(content["rows.jsonl"]).hexdigest()
    if manifest_change:
        manifest_change(manifest)
    content["manifest.json"] = json.dumps(manifest).encode()
    if extra:
        content.update(extra)
    target = path.with_name(path.stem + "-changed.pitbox")
    with zipfile.ZipFile(target, "w") as z:
        for name, value in content.items():
            z.writestr(name, value)
    return target


@pytest.mark.asyncio
async def test_complete_bidirectional_transfer_preserves_trace_feedback_and_idempotency(tmp_path):
    source, outgoing = await _installation(tmp_path / "source")
    dest, incoming = await _installation(tmp_path / "destination")
    key = await _session(source, 101, trace=True)
    await source.save_preference("driver_preferences", {"style": "concise"})
    await source.save_preference("app_settings", {"api_key": "must-not-travel", "udp_host": "192.168.1.10"})
    bundle = tmp_path / "first.pitbox"
    exported = outgoing.export_bundle([key], bundle)
    assert exported["asset_count"] == 2
    assert incoming.preview_bundle(bundle)["session_count"] == 1
    report = incoming.import_bundle(bundle)
    assert report["status"] == "imported"
    assert report["imported_sessions"] == 1
    assert _count(dest, "laps") == _count(dest, "recorded_laps") == 1
    assert _count(dest, "feedback") == 1
    assert await dest.load_preference("driver_preferences") == {"style": "concise"}
    assert await dest.load_preference("app_settings") is None
    with sqlite3.connect(dest.path) as db:
        manifest_id = db.execute("SELECT trace_manifest_id FROM recorded_laps").fetchone()[0]
    assert TraceStore(dest.path.parent / "traces").verify_manifest(manifest_id).valid
    again = incoming.import_bundle(bundle)
    assert again["status"] == "imported" and again["imported_rows"] == 0
    returned = tmp_path / "return.pitbox"
    incoming.export_bundle(None, returned)
    result = outgoing.import_bundle(returned)
    assert result["status"] == "imported" and result["imported_rows"] == 0
    assert _count(source, "laps") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("different_value", [False, True])
async def test_existing_driver_preference_never_blocks_history_import(tmp_path, different_value):
    source, outgoing = await _installation(tmp_path / "source")
    dest, incoming = await _installation(tmp_path / "destination")
    key = await _session(source, 101)
    await source.save_preference("driver_preferences", {"style": "concise"})
    local = {"style": "detailed" if different_value else "concise"}
    await dest.save_preference("driver_preferences", local)
    with sqlite3.connect(dest.path) as db:
        db.execute("UPDATE user_preferences SET updated_at=updated_at+12345")
    bundle = tmp_path / "prefs.pitbox"
    outgoing.export_bundle([key], bundle)
    result = incoming.import_bundle(bundle)
    assert result["status"] == "imported" and result["imported_sessions"] == 1
    assert await dest.load_preference("driver_preferences") == local
    assert _count(dest, "history_transfer_preference_variants") == int(different_value)


@pytest.mark.asyncio
async def test_colliding_integer_ids_are_remapped_with_catalog_relationships(tmp_path):
    source, outgoing = await _installation(tmp_path / "source")
    dest, incoming = await _installation(tmp_path / "destination")
    key = await _session(source, 101)
    await _session(dest, 202)
    bundle = tmp_path / "history.pitbox"
    outgoing.export_bundle([key], bundle)
    assert incoming.import_bundle(bundle)["status"] == "imported"
    with sqlite3.connect(dest.path) as db:
        pairs = db.execute("SELECT l.session_uid,r.legacy_lap_id,l.id FROM recorded_laps r JOIN laps l ON l.id=r.legacy_lap_id ORDER BY l.session_uid").fetchall()
    assert pairs == [(101, 2, 2), (202, 1, 1)]
    returned = tmp_path / "return.pitbox"
    incoming.export_bundle([key], returned)
    assert outgoing.import_bundle(returned)["imported_rows"] == 0
    assert incoming.import_bundle(bundle)["imported_rows"] == 0


@pytest.mark.asyncio
async def test_recording_session_is_excluded_and_explicit_selection_rejected(tmp_path):
    source, service = await _installation(tmp_path / "source")
    active = await _session(source, 333, complete=False)
    completed = await _session(source, 444)
    sessions = {s["id"]: s for s in service.list_sessions()}
    assert not sessions[active]["transferable"] and sessions[completed]["transferable"]
    with pytest.raises(HistoryTransferError, match="still recording"):
        service.export_bundle([active], tmp_path / "bad.pitbox")
    result = service.export_bundle(None, tmp_path / "good.pitbox")
    assert [s["id"] for s in result["sessions"]] == [completed]


@pytest.mark.asyncio
async def test_changed_recording_preserved_without_partial_rows_or_double_learning(tmp_path):
    source, outgoing = await _installation(tmp_path / "source")
    dest, incoming = await _installation(tmp_path / "destination")
    key = await _session(source, 101)
    bundle = tmp_path / "history.pitbox"
    outgoing.export_bundle([key], bundle)
    assert incoming.import_bundle(bundle)["status"] == "imported"
    with sqlite3.connect(source.path) as db:
        db.execute("UPDATE laps SET lap_time_ms=90000")
    changed = tmp_path / "changed.pitbox"
    outgoing.export_bundle([key], changed)
    result = incoming.import_bundle(changed)
    assert result["status"] == "conflict"
    assert Path(result["preserved_path"]).read_bytes() == changed.read_bytes()
    with sqlite3.connect(dest.path) as db:
        assert db.execute("SELECT lap_time_ms FROM laps").fetchall() == [(92001,)]


@pytest.mark.asyncio
async def test_missing_detail_is_explicit_and_summary_remains_importable(tmp_path):
    source, outgoing = await _installation(tmp_path / "source")
    dest, incoming = await _installation(tmp_path / "destination")
    key = await _session(source, 101, trace=True)
    for path in (source.path.parent / "traces" / "chunks").rglob("*.pwt"):
        path.unlink()
    bundle = tmp_path / "history.pitbox"
    report = outgoing.export_bundle([key], bundle)
    assert report["completeness"] == "missing-artifacts" and report["missing_assets"]
    assert incoming.import_bundle(bundle)["status"] == "imported"
    assert _count(dest, "recorded_laps") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", ["traversal", "checksum", "foreign", "active", "type", "settings", "schema", "extra"])
async def test_untrusted_archive_rejected_before_any_history_changes(tmp_path, attack):
    source, outgoing = await _installation(tmp_path / "source")
    dest, incoming = await _installation(tmp_path / "destination")
    key = await _session(source, 101)
    await source.save_preference("driver_preferences", {"style": "concise"})
    bundle = tmp_path / "history.pitbox"
    outgoing.export_bundle([key], bundle)
    def change(rows):
        if attack == "foreign":
            next(r for r in rows if r["table"] == "recorded_laps")["row"]["session_car_id"] = "absent"
        elif attack == "active":
            next(r for r in rows if r["table"] == "recorded_sessions")["row"]["status"] = "recording"
        elif attack == "type":
            next(r for r in rows if r["table"] == "laps")["row"]["lap_time_ms"] = "oops"
        elif attack == "settings":
            next(r for r in rows if r["table"] == "user_preferences")["row"]["key"] = "app_settings"
    if attack == "traversal":
        bad = _rewrite(bundle, extra={"../outside": b"unsafe"})
    elif attack == "extra":
        bad = _rewrite(bundle, extra={"extra": b"not-in-inventory"})
    elif attack == "schema":
        bad = _rewrite(bundle, manifest_change=lambda m: m.update(schema_version=99999))
    elif attack == "checksum":
        bad = _rewrite(bundle, manifest_change=lambda m: m.update(rows_sha256="0" * 64))
    else:
        bad = _rewrite(bundle, row_change=change)
    with pytest.raises(HistoryTransferError):
        incoming.import_bundle(bad)
    assert _count(dest, "sessions") == _count(dest, "laps") == 0


@pytest.mark.asyncio
async def test_insufficient_storage_does_not_modify_history(tmp_path, monkeypatch):
    source, outgoing = await _installation(tmp_path / "source")
    dest, incoming = await _installation(tmp_path / "destination")
    key = await _session(source, 101)
    bundle = tmp_path / "history.pitbox"
    outgoing.export_bundle([key], bundle)
    monkeypatch.setattr("pitwall.history_transfer.shutil.disk_usage", lambda path: type("Usage", (), {"free": 1})())
    with pytest.raises(HistoryTransferError, match="free storage"):
        incoming.import_bundle(bundle)
    assert _count(dest, "sessions") == 0
