"""Portable, additive history exchange; the transport never gets database files.

The archive contains typed logical rows, immutable artifacts and a checked
inventory. SQLite's backup API supplies a consistent read snapshot. Imports are
staged on disk, checked against the receiving application's schema, and committed
in one transaction. Local integer IDs are remapped; stable catalog identities
are retained. A differing version of an existing recording is a conflict, never
another lap of learning evidence. Conflicting archives remain available for
explicit review. No settings, credentials, network profiles or runnable jobs are
transferred.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

FORMAT = "pitwall-history"
FORMAT_VERSION = 1
MAX_MANIFEST_BYTES = 16 * 1024**2
MAX_ROW_BYTES = 32 * 1024**2
MAX_ARCHIVE_BYTES = 64 * 1024**3
MAX_ENTRIES = 100_000
RESERVE_BYTES = 64 * 1024**2
_HASH = re.compile(r"^[0-9a-f]{64}$")
_ORIGIN = re.compile(r"^[0-9a-f]{32}$")
_LOCK = threading.RLock()

# Order ensures parents are mapped before their children. The one non-FK
# back-reference recorded_laps.trace_manifest_id is intentionally retained.
TABLES = (
    "sessions", "laps", "corner_metrics", "setup_runs", "setup_recommendations",
    "feedback", "line_metrics", "radio_messages", "strategy_snapshots",
    "proactive_calls", "briefings", "session_events", "user_preferences",
    "recorded_sessions", "session_cars", "recorded_laps", "trace_manifests",
    "trace_chunks", "raw_captures", "track_models", "segment_models", "segments",
    "comparisons", "segment_metrics", "findings", "comparison_segment_results",
    "full_field_lap_batches",
)
LEGACY_CHILDREN = {
    "laps", "corner_metrics", "setup_runs", "feedback", "line_metrics",
    "radio_messages", "strategy_snapshots", "proactive_calls", "briefings",
    "session_events",
}
INTEGER_REFS = {table: {"session_uid": "sessions"} for table in LEGACY_CHILDREN}
INTEGER_REFS.update({"recorded_sessions": {"legacy_session_uid": "sessions"},
                     "recorded_laps": {"legacy_lap_id": "laps"}})


class HistoryTransferError(ValueError):
    """An unsafe, incomplete, incompatible or conflicting transfer."""


class _Conflict(HistoryTransferError):
    def __init__(self, table: str, key: str, reason: str) -> None:
        super().__init__(f"{table} {key}: {reason}")
        self.detail = {"table": table, "key": key, "reason": reason}


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _encoded(row: dict[str, Any]) -> dict[str, Any]:
    return {k: {"$bytes": base64.b64encode(v).decode("ascii")}
            if isinstance(v, bytes) else v for k, v in row.items()}


def _digest(row: dict[str, Any]) -> str:
    return hashlib.sha256(_json(_encoded(row)).encode()).hexdigest()


def _row_digest(table: str, row: dict[str, Any]) -> str:
    # Activation is a local model-selection preference, not new race evidence.
    if table in {"track_models", "segment_models"}:
        row = {key: value for key, value in row.items() if key != "active"}
    return _digest(row)


def _hash_file(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def _relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise HistoryTransferError("Invalid artifact path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in {"..", "."} for p in path.parts) or ":" in value:
        raise HistoryTransferError("Artifact path escapes its storage root")
    if path.as_posix() != value or len(value) > 1024:
        raise HistoryTransferError("Non-canonical artifact path")
    return value


def _safe(root: Path, relative: str) -> Path:
    path = root / _relative(relative)
    if not path.resolve().is_relative_to(root.resolve()):
        raise HistoryTransferError("Artifact symlink escapes its storage root")
    return path


def _connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    db = sqlite3.connect(path.as_uri() + "?mode=ro" if readonly else path,
                         uri=readonly, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA trusted_schema=OFF")
    return db


def _schema(db: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    result = {}
    for table in TABLES:
        columns = [dict(row) for row in db.execute(f'PRAGMA table_info("{table}")')]
        if not columns:
            raise HistoryTransferError(f"History schema is not initialized: {table}")
        result[table] = columns
    return result


def _pk(schema: dict[str, list[dict[str, Any]]], table: str) -> list[str]:
    return [c["name"] for c in sorted(schema[table], key=lambda c: c["pk"]) if c["pk"]]


def _key(row: dict[str, Any], columns: list[str]) -> str:
    return _json([row[name] for name in columns])


class HistoryTransferService:
    def __init__(self, database_path: Path, data_root: Path, *,
                 trace_root: Path | None = None, capture_root: Path | None = None,
                 maximum_archive_bytes: int = MAX_ARCHIVE_BYTES) -> None:
        self.database_path = Path(database_path).resolve()
        self.data_root = Path(data_root).resolve()
        self.roots = {"traces": Path(trace_root or self.data_root / "traces").resolve(),
                      "captures": Path(capture_root or self.data_root / "captures").resolve(),
                      "models": self.data_root}
        self.maximum_archive_bytes = maximum_archive_bytes

    def _state(self, db: sqlite3.Connection) -> str:
        db.execute("CREATE TABLE IF NOT EXISTS history_transfer_identity "
                   "(singleton INTEGER PRIMARY KEY CHECK(singleton=1), origin TEXT NOT NULL)")
        db.execute("INSERT OR IGNORE INTO history_transfer_identity VALUES(1, ?)",
                   (uuid.uuid4().hex,))
        db.execute("""CREATE TABLE IF NOT EXISTS history_transfer_receipts (
            origin TEXT NOT NULL, table_name TEXT NOT NULL, source_key TEXT NOT NULL,
            source_digest TEXT NOT NULL, local_key TEXT NOT NULL, local_digest TEXT NOT NULL,
            PRIMARY KEY(origin, table_name, source_key))""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_history_receipt_local "
                   "ON history_transfer_receipts(table_name, local_key)")
        db.execute("""CREATE TABLE IF NOT EXISTS history_transfer_preference_variants (
            origin TEXT NOT NULL, preference_key TEXT NOT NULL, source_digest TEXT NOT NULL,
            body_json TEXT NOT NULL, PRIMARY KEY(origin, preference_key, source_digest))""")
        return str(db.execute("SELECT origin FROM history_transfer_identity WHERE singleton=1").fetchone()[0])

    @contextlib.contextmanager
    def _snapshot(self, directory: Path) -> Iterator[tuple[sqlite3.Connection, str]]:
        with _LOCK, contextlib.closing(_connect(self.database_path)) as live:
            origin = self._state(live)
            live.commit()
            target = directory / "snapshot.sqlite3"
            self._space(directory, self.database_path.stat().st_size * 2)
            with contextlib.closing(_connect(target)) as snapshot:
                live.backup(snapshot, pages=256)
                if snapshot.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise HistoryTransferError("History snapshot failed its integrity check")
                yield snapshot, origin

    @staticmethod
    def _space(path: Path, size: int) -> None:
        if shutil.disk_usage(path).free < size + RESERVE_BYTES:
            raise HistoryTransferError("Not enough free storage for a safely staged history transfer")

    def list_sessions(self) -> list[dict[str, Any]]:
        with contextlib.closing(_connect(self.database_path, readonly=True)) as db:
            result = [dict(row) for row in db.execute("""
                SELECT id, game_session_uid, legacy_session_uid, track_id, session_type,
                       display_name, started_at, ended_at, status,
                       (SELECT COUNT(*) FROM recorded_laps l JOIN session_cars c
                        ON c.id=l.session_car_id WHERE c.session_id=s.id) AS lap_count,
                       (SELECT COUNT(*) FROM recorded_laps l JOIN session_cars c
                        ON c.id=l.session_car_id LEFT JOIN laps old ON old.id=l.legacy_lap_id
                        WHERE c.session_id=s.id AND (l.trace_manifest_id IS NOT NULL
                        OR COALESCE(old.trace_json, '[]') NOT IN ('', '[]'))) AS detailed_lap_count
                FROM recorded_sessions s ORDER BY started_at DESC, id""")]
            for row in result:
                row["transferable"] = row["status"] in {"complete", "incomplete"} and bool(row["ended_at"])
                row["completeness"] = "cataloged-detail" if row["detailed_lap_count"] else "summary_only"
            for row in db.execute("""SELECT * FROM sessions s WHERE NOT EXISTS
                    (SELECT 1 FROM recorded_sessions r WHERE r.legacy_session_uid=s.session_uid)
                    ORDER BY started_at DESC"""):
                result.append({"id": f"legacy:{row['session_uid']}", "legacy_session_uid": row["session_uid"],
                               "track_id": row["track_id"], "session_type": row["session_type"],
                               "started_at": row["started_at"], "ended_at": row["ended_at"],
                               "status": "complete" if row["ended_at"] else "recording",
                               "transferable": bool(row["ended_at"])})
            return result

    def _select(self, db: sqlite3.Connection, requested: list[str] | None) -> tuple[dict[str, str], list[dict[str, Any]], list[str]]:
        db.executescript("CREATE TEMP TABLE chosen(id TEXT PRIMARY KEY); "
                         "CREATE TEMP TABLE legacy_chosen(uid INTEGER PRIMARY KEY)")
        known = {str(row["id"]): dict(row) for row in db.execute("SELECT * FROM recorded_sessions")}
        legacy = {f"legacy:{row['session_uid']}": dict(row) for row in db.execute("SELECT * FROM sessions")}
        if requested is not None and (len(requested) > 100_000 or any(not isinstance(v, str) for v in requested)):
            raise HistoryTransferError("Invalid session selection")
        selected = requested if requested is not None else list(known) + [key for key, row in legacy.items()
            if not any(r["legacy_session_uid"] == row["session_uid"] for r in known.values())]
        warnings = []
        for key in selected:
            row = known.get(key) or legacy.get(key)
            if row is None:
                raise HistoryTransferError(f"Unknown session: {key}")
            complete = bool(row["ended_at"]) and row.get("status", "complete") in {"complete", "incomplete"}
            if not complete:
                if requested is not None:
                    raise HistoryTransferError(f"Session is still recording: {key}")
                warnings.append(f"Active session excluded: {key}")
                continue
            if key in known:
                db.execute("INSERT OR IGNORE INTO chosen VALUES(?)", (key,))
            else:
                db.execute("INSERT OR IGNORE INTO legacy_chosen VALUES(?)", (row["session_uid"],))
        # Include completed reference sessions so saved comparisons stay usable.
        while True:
            before = db.total_changes
            db.execute("""INSERT OR IGNORE INTO chosen SELECT rc.session_id FROM comparisons cmp
                JOIN recorded_laps cand ON cand.id=cmp.candidate_lap_id
                JOIN session_cars cc ON cc.id=cand.session_car_id
                JOIN recorded_laps ref ON ref.id=cmp.reference_key
                JOIN session_cars rc ON rc.id=ref.session_car_id
                JOIN recorded_sessions rs ON rs.id=rc.session_id
                WHERE cc.session_id IN chosen AND rs.status IN ('complete','incomplete')
                  AND rs.ended_at IS NOT NULL""")
            if db.total_changes == before:
                break
        db.execute("""INSERT OR IGNORE INTO legacy_chosen SELECT legacy_session_uid
            FROM recorded_sessions WHERE id IN chosen AND legacy_session_uid IN (SELECT session_uid FROM sessions)
            AND legacy_session_uid NOT IN (SELECT legacy_session_uid FROM recorded_sessions
            WHERE status='recording' AND legacy_session_uid IS NOT NULL)""")
        db.executescript("""CREATE TEMP VIEW chosen_cars AS SELECT id FROM session_cars WHERE session_id IN chosen;
            CREATE TEMP VIEW chosen_laps AS SELECT id FROM recorded_laps WHERE session_car_id IN chosen_cars;
            CREATE TEMP VIEW chosen_comparisons AS SELECT id FROM comparisons WHERE candidate_lap_id IN chosen_laps
                AND reference_key IN chosen_laps;
            CREATE TEMP VIEW chosen_tracks AS SELECT track_id FROM recorded_sessions WHERE id IN chosen
                UNION SELECT track_id FROM sessions WHERE session_uid IN legacy_chosen;
            CREATE TEMP VIEW chosen_models AS SELECT id FROM track_models WHERE track_id IN chosen_tracks;
            CREATE TEMP VIEW chosen_segments AS SELECT id FROM segment_models WHERE track_model_id IN chosen_models;
        """)
        predicates = {table: "session_uid IN legacy_chosen" for table in LEGACY_CHILDREN}
        predicates.update({
            "sessions": "session_uid IN legacy_chosen", "setup_recommendations": "track_id IN chosen_tracks",
            "user_preferences": "key IN ('driver_preferences','standing_instructions') OR key IN (SELECT 'turn_model:' || track_id FROM chosen_tracks)",
            "recorded_sessions": "id IN chosen", "session_cars": "id IN chosen_cars",
            "recorded_laps": "id IN chosen_laps", "trace_manifests": "session_id IN chosen",
            "trace_chunks": "manifest_id IN (SELECT id FROM trace_manifests WHERE session_id IN chosen)",
            "raw_captures": "session_id IN chosen AND clean_close=1 AND ended_at IS NOT NULL",
            "track_models": "id IN chosen_models", "segment_models": "id IN chosen_segments",
            "segments": "segment_model_id IN chosen_segments", "comparisons": "id IN chosen_comparisons",
            "segment_metrics": "comparison_id IN chosen_comparisons", "findings": "comparison_id IN chosen_comparisons",
            "comparison_segment_results": "comparison_id IN chosen_comparisons",
            "full_field_lap_batches": "session_id IN chosen",
        })
        omitted = db.execute("SELECT COUNT(*) FROM comparisons WHERE candidate_lap_id IN chosen_laps AND id NOT IN chosen_comparisons").fetchone()[0]
        if omitted:
            warnings.append(f"{omitted} comparisons omitted because their reference session is unavailable or recording")
        sessions = [{k: r[k] for k in ("id", "track_id", "session_type", "display_name", "status", "started_at", "ended_at")}
                    for r in db.execute("SELECT * FROM recorded_sessions WHERE id IN chosen")]
        sessions += [{"id": f"legacy:{r['session_uid']}", "track_id": r["track_id"], "session_type": r["session_type"],
                      "status": "complete", "started_at": r["started_at"], "ended_at": r["ended_at"]}
                     for r in db.execute("SELECT * FROM sessions WHERE session_uid IN legacy_chosen AND session_uid NOT IN "
                                         "(SELECT legacy_session_uid FROM recorded_sessions WHERE id IN chosen AND legacy_session_uid IS NOT NULL)")]
        return predicates, sessions, warnings

    def export_bundle(self, session_ids: list[str] | None, destination: Path) -> dict[str, Any]:
        destination = Path(destination).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise HistoryTransferError("Export destination already exists")
        with tempfile.TemporaryDirectory(prefix="pitwall-export-", dir=destination.parent) as tmp:
            scratch = Path(tmp)
            with self._snapshot(scratch) as (db, origin):
                schema = _schema(db)
                predicates, sessions, warnings = self._select(db, session_ids)
                manifest: dict[str, Any] = {"format": FORMAT, "version": FORMAT_VERSION,
                    "schema_version": db.execute("PRAGMA user_version").fetchone()[0],
                    "created_at": time.time(), "origin": origin, "sessions": sessions,
                    "tables": {}, "assets": [], "missing_assets": [], "warnings": warnings,
                    "excluded": ["credentials", "device settings", "network profiles", "active recordings", "analysis job queue"]}
                artifacts: dict[tuple[str, str], dict[str, Any]] = {}
                rows_path = scratch / "rows.jsonl"
                with rows_path.open("w", encoding="utf-8", newline="\n") as stream:
                    for table in TABLES:
                        count = 0
                        keys = _pk(schema, table)
                        for record in db.execute(f'SELECT * FROM "{table}" WHERE {predicates[table]} ORDER BY ' + ",".join(f'"{k}"' for k in keys)):
                            row = dict(record)
                            # A catalogued session may have no safe legacy counterpart.
                            if table == "recorded_sessions" and row["legacy_session_uid"] is not None and not db.execute("SELECT 1 FROM legacy_chosen WHERE uid=?", (row["legacy_session_uid"],)).fetchone():
                                row["legacy_session_uid"] = None
                            if table == "recorded_laps" and row["legacy_lap_id"] is not None and not db.execute("SELECT 1 FROM laps WHERE id=? AND session_uid IN legacy_chosen", (row["legacy_lap_id"],)).fetchone():
                                row["legacy_lap_id"] = None
                            local_key = _key(row, keys)
                            digest = _row_digest(table, row)
                            receipt = db.execute("SELECT * FROM history_transfer_receipts WHERE table_name=? AND local_key=? AND local_digest=? ORDER BY origin LIMIT 1", (table, local_key, digest)).fetchone()
                            envelope = {"table": table, "row": _encoded(row), "origin": receipt["origin"] if receipt else origin,
                                        "key": receipt["source_key"] if receipt else local_key,
                                        "digest": receipt["source_digest"] if receipt else digest}
                            line = _json(envelope)
                            if len(line.encode()) > MAX_ROW_BYTES:
                                raise HistoryTransferError("A history row exceeds the portable format limit")
                            stream.write(line + "\n")
                            count += 1
                            if table in {"trace_chunks", "raw_captures", "track_models"}:
                                root = {"trace_chunks": "traces", "raw_captures": "captures", "track_models": "models"}[table]
                                rel = _relative(row["relative_path"])
                                if root == "models" and not rel.startswith("track-models/"):
                                    raise HistoryTransferError("Track model must be inside track-models")
                                artifacts[(root, rel)] = {"root": root, "path": rel, "kind": table}
                            elif table == "trace_manifests":
                                rel = _relative(f"manifests/{row['id']}.json")
                                artifacts[("traces", rel)] = {"root": "traces", "path": rel, "kind": table}
                        manifest["tables"][table] = {"columns": schema[table], "rows": count}
                manifest["rows_bytes"] = rows_path.stat().st_size
                manifest["rows_sha256"] = _hash_file(rows_path)
                archive_path = scratch / "export.pitbox"
                self._space(scratch, rows_path.stat().st_size)
                total = rows_path.stat().st_size
                with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as archive:
                    archive.write(rows_path, "rows.jsonl")
                    for index, artifact in enumerate(artifacts.values()):
                        path = _safe(self.roots[artifact["root"]], artifact["path"])
                        if not path.is_file():
                            manifest["missing_assets"].append(artifact)
                            continue
                        if path.is_symlink():
                            raise HistoryTransferError("History artifacts must be regular files")
                        size = path.stat().st_size
                        total += size
                        if total > self.maximum_archive_bytes or index + 3 >= MAX_ENTRIES:
                            raise HistoryTransferError("History exceeds the portable archive limit; select fewer sessions")
                        self._space(scratch, size)
                        entry = f"assets/{index:08d}"
                        digest = hashlib.sha256()
                        written = 0
                        with path.open("rb") as source, archive.open(entry, "w", force_zip64=True) as output:
                            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                                written += len(chunk)
                                if written > size:
                                    raise HistoryTransferError("History artifact changed while exporting; retry after recording stops")
                                output.write(chunk)
                                digest.update(chunk)
                        if written != size:
                            raise HistoryTransferError("History artifact changed while exporting")
                        manifest["assets"].append({**artifact, "entry": entry, "bytes": size, "sha256": digest.hexdigest()})
                    manifest["completeness"] = "available-history" if not manifest["missing_assets"] else "missing-artifacts"
                    manifest["warnings"].append("Previously pruned samples cannot be reconstructed; available-history does not guarantee every original telemetry sample.")
                    payload = _json(manifest).encode()
                    if len(payload) > MAX_MANIFEST_BYTES:
                        raise HistoryTransferError("Archive inventory is too large; select fewer sessions")
                    archive.writestr("manifest.json", payload)
                os.replace(archive_path, destination)
                return {**self._summary(manifest), "path": str(destination), "bytes": destination.stat().st_size}

    @staticmethod
    def _summary(manifest: dict[str, Any]) -> dict[str, Any]:
        return {key: manifest[key] for key in ("format", "version", "schema_version", "origin", "sessions", "completeness", "missing_assets", "warnings", "excluded")} | {
            "session_count": len(manifest["sessions"]), "asset_count": len(manifest["assets"]),
            "row_count": sum(value["rows"] for value in manifest["tables"].values()),
            "unpacked_bytes": manifest["rows_bytes"] + sum(a["bytes"] for a in manifest["assets"])}

    def _inventory(self, archive: zipfile.ZipFile, schema: dict[str, Any], version: int) -> dict[str, Any]:
        infos = archive.infolist()
        if len(infos) > MAX_ENTRIES:
            raise HistoryTransferError("Archive contains too many entries")
        names = set()
        total = 0
        for info in infos:
            _relative(info.filename)
            mode = info.external_attr >> 16
            if info.filename in names or info.is_dir() or (stat.S_IFMT(mode) not in {0, stat.S_IFREG}) or info.flag_bits & 1:
                raise HistoryTransferError("Duplicate, encrypted or non-file archive entry")
            names.add(info.filename)
            total += info.file_size
            if total > self.maximum_archive_bytes or info.file_size < 0:
                raise HistoryTransferError("Archive exceeds the unpacked size limit")
        if "manifest.json" not in names or archive.getinfo("manifest.json").file_size > MAX_MANIFEST_BYTES:
            raise HistoryTransferError("Missing or oversized archive inventory")
        try:
            manifest = json.loads(archive.read("manifest.json"))
            if manifest["format"] != FORMAT or type(manifest["version"]) is not int or manifest["version"] != FORMAT_VERSION:
                raise HistoryTransferError("Unsupported history archive format")
            if manifest["schema_version"] != version:
                raise HistoryTransferError("Both devices must use the same history schema; update the older app")
            if not _ORIGIN.fullmatch(manifest["origin"]):
                raise HistoryTransferError("Invalid archive origin")
            if set(manifest["tables"]) != set(TABLES):
                raise HistoryTransferError("Unexpected history tables")
            for table, definition in manifest["tables"].items():
                if definition["columns"] != schema[table] or type(definition["rows"]) is not int or not 0 <= definition["rows"] <= 10_000_000:
                    raise HistoryTransferError("Incompatible history schema or row count")
            expected = {"manifest.json", "rows.jsonl"}
            logical_paths = set()
            for asset in manifest["assets"]:
                self._asset_path(asset)
                if not _HASH.fullmatch(asset["sha256"]) or type(asset["bytes"]) is not int or asset["bytes"] < 0:
                    raise HistoryTransferError("Invalid artifact checksum or size")
                if not re.fullmatch(r"assets/[0-9]{8}", asset["entry"]):
                    raise HistoryTransferError("Invalid artifact archive entry")
                logical = (asset["root"], asset["path"])
                if logical in logical_paths or asset["entry"] in expected:
                    raise HistoryTransferError("Duplicate artifact inventory")
                logical_paths.add(logical)
                expected.add(asset["entry"])
                if archive.getinfo(asset["entry"]).file_size != asset["bytes"]:
                    raise HistoryTransferError("Artifact size mismatch")
            if expected != names or archive.getinfo("rows.jsonl").file_size != manifest["rows_bytes"] or not _HASH.fullmatch(manifest["rows_sha256"]):
                raise HistoryTransferError("Archive inventory does not match its entries")
            for asset in manifest["missing_assets"]:
                self._asset_path(asset)
            if not isinstance(manifest["sessions"], list) or len(manifest["sessions"]) > 100_000:
                raise HistoryTransferError("Invalid session inventory")
            self._summary(manifest)
            return manifest
        except (KeyError, TypeError, ValueError, UnicodeError) as exc:
            if isinstance(exc, HistoryTransferError):
                raise
            raise HistoryTransferError("Malformed history inventory") from exc

    def _asset_path(self, asset: dict[str, Any]) -> Path:
        if asset["root"] not in self.roots:
            raise HistoryTransferError("Unknown artifact root")
        relative = _relative(asset["path"])
        expected = {"trace_chunks": "traces", "trace_manifests": "traces", "raw_captures": "captures", "track_models": "models"}
        if expected.get(asset["kind"]) != asset["root"]:
            raise HistoryTransferError("Invalid artifact type")
        if asset["root"] == "models" and not relative.startswith("track-models/"):
            raise HistoryTransferError("Track model must be inside track-models")
        if asset["kind"] == "trace_manifests" and not re.fullmatch(r"manifests/[A-Za-z0-9_.-]{1,128}\.json", relative):
            raise HistoryTransferError("Invalid trace manifest path")
        return _safe(self.roots[asset["root"]], relative)

    @staticmethod
    def _copy_checked(archive: zipfile.ZipFile, entry: str, target: Path, size: int, expected: str) -> None:
        digest = hashlib.sha256()
        written = 0
        with archive.open(entry) as source, target.open("xb") as output:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                written += len(chunk)
                if written > size:
                    raise HistoryTransferError("Archive entry exceeds its declared size")
                digest.update(chunk)
                output.write(chunk)
        if written != size or digest.hexdigest() != expected:
            raise HistoryTransferError("Archive checksum mismatch")

    def _stage(self, archive: zipfile.ZipFile, scratch: Path, schema: dict[str, Any], version: int) -> tuple[dict[str, Any], sqlite3.Connection]:
        manifest = self._inventory(archive, schema, version)
        self._space(scratch, self._summary(manifest)["unpacked_bytes"] * 3)
        row_path = scratch / "rows.jsonl"
        self._copy_checked(archive, "rows.jsonl", row_path, manifest["rows_bytes"], manifest["rows_sha256"])
        staging = _connect(scratch / "staging.sqlite3")
        try:
            staging.executescript("""CREATE TABLE rows(seq INTEGER PRIMARY KEY, table_name TEXT,
                local_source_key TEXT, origin TEXT, source_key TEXT, digest TEXT, body TEXT,
                UNIQUE(table_name, local_source_key), UNIQUE(table_name, origin, source_key));
                CREATE TABLE mappings(table_name TEXT, source_key TEXT, local_key TEXT,
                PRIMARY KEY(table_name, source_key));""")
            counts = dict.fromkeys(TABLES, 0)
            previous_table = -1
            with row_path.open("rb") as stream:
                while line := stream.readline(MAX_ROW_BYTES + 2):
                    if len(line) > MAX_ROW_BYTES + 1 or not line.endswith(b"\n"):
                        raise HistoryTransferError("Oversized or truncated history row")
                    value = json.loads(line)
                    table = value["table"]
                    if table not in TABLES or TABLES.index(table) < previous_table:
                        raise HistoryTransferError("Unexpected history table order")
                    previous_table = TABLES.index(table)
                    row = self._validate_row(value["row"], schema[table])
                    if not _ORIGIN.fullmatch(value["origin"]) or not _HASH.fullmatch(value["digest"]) or not isinstance(value["key"], str) or len(value["key"]) > 2048:
                        raise HistoryTransferError("Invalid history provenance")
                    source_pk = json.loads(value["key"])
                    if not isinstance(source_pk, list) or len(source_pk) != len(_pk(schema, table)) or any(type(v) not in {str, int} for v in source_pk):
                        raise HistoryTransferError("Invalid history origin key")
                    self._validate_portable_row(table, row)
                    staging.execute("INSERT INTO rows(table_name,local_source_key,origin,source_key,digest,body) VALUES(?,?,?,?,?,?)", (table, _key(row, _pk(schema, table)), value["origin"], value["key"], value["digest"], _json(_encoded(row))))
                    counts[table] += 1
            if counts != {k: v["rows"] for k, v in manifest["tables"].items()}:
                raise HistoryTransferError("History row counts do not match the inventory")
            for index, asset in enumerate(manifest["assets"]):
                self._copy_checked(archive, asset["entry"], scratch / f"asset-{index}", asset["bytes"], asset["sha256"])
            staging.commit()
            self._validate_relationships(staging, schema, manifest, scratch)
            return manifest, staging
        except Exception:
            staging.close()
            raise

    @staticmethod
    def _validate_row(row: Any, columns: list[dict[str, Any]]) -> dict[str, Any]:
        if not isinstance(row, dict) or set(row) != {c["name"] for c in columns}:
            raise HistoryTransferError("History row columns do not match the schema")
        output = {}
        for column in columns:
            value = row[column["name"]]
            kind = column["type"].upper()
            if value is None:
                if column["notnull"] or column["pk"]:
                    raise HistoryTransferError("Required history value is null")
            elif kind == "BLOB":
                if not isinstance(value, dict) or set(value) != {"$bytes"}:
                    raise HistoryTransferError("Invalid binary history value")
                value = base64.b64decode(value["$bytes"], validate=True)
            elif kind == "INTEGER":
                if type(value) is not int or not -(1 << 63) <= value < (1 << 63):
                    raise HistoryTransferError("Invalid integer history value")
            elif kind == "REAL":
                if type(value) not in {int, float} or not math.isfinite(value):
                    raise HistoryTransferError("Invalid numeric history value")
            elif kind == "TEXT" and not isinstance(value, str):
                raise HistoryTransferError("Invalid text history value")
            output[column["name"]] = value
        return output

    @staticmethod
    def _validate_portable_row(table: str, row: dict[str, Any]) -> None:
        if table == "user_preferences" and row["key"] not in {"driver_preferences", "standing_instructions"} and not re.fullmatch(r"turn_model:-?[0-9]+", row["key"]):
            raise HistoryTransferError("Device settings and credentials cannot be imported")
        if table == "recorded_sessions" and (row["status"] not in {"complete", "incomplete"} or not row["ended_at"]):
            raise HistoryTransferError("Active session in history archive")
        if table == "sessions" and not row["ended_at"]:
            raise HistoryTransferError("Active legacy session in history archive")
        if table == "raw_captures" and (row["clean_close"] != 1 or not row["ended_at"]):
            raise HistoryTransferError("Unfinished raw capture in history archive")

    def _validate_relationships(self, staged: sqlite3.Connection, schema: dict[str, Any], manifest: dict[str, Any], scratch: Path) -> None:
        artifacts = {(a["kind"], a["path"]) for a in manifest["assets"] + manifest["missing_assets"]}
        referenced = set()
        with contextlib.closing(_connect(self.database_path, readonly=True)) as local:
            foreign = {t: [dict(r) for r in local.execute(f'PRAGMA foreign_key_list("{t}")')] for t in TABLES}
        for record in staged.execute("SELECT * FROM rows ORDER BY seq"):
            table = record["table_name"]
            row = self._validate_row(json.loads(record["body"]), schema[table])
            refs = {f["from"]: f["table"] for f in foreign[table]}
            refs.update(INTEGER_REFS.get(table, {}))
            if table == "recorded_laps":
                refs["trace_manifest_id"] = "trace_manifests"
            if table == "comparisons":
                refs["reference_key"] = "recorded_laps"
            for column, target in refs.items():
                if row[column] is not None and not staged.execute("SELECT 1 FROM rows WHERE table_name=? AND local_source_key=?", (target, _json([row[column]]))).fetchone():
                    raise HistoryTransferError(f"Dangling history reference: {table}.{column}")
            if table in {"trace_chunks", "raw_captures", "track_models", "trace_manifests"}:
                relative = f"manifests/{row['id']}.json" if table == "trace_manifests" else row["relative_path"]
                referenced.add((table, relative))
                if (table, relative) not in artifacts:
                    raise HistoryTransferError("Referenced artifact is absent from the inventory")
        if referenced != artifacts:
            raise HistoryTransferError("Archive contains an unreferenced artifact")
        # Manifest paths are used by TraceStore independently of SQL: validate
        # their links as well, without decoding large numeric arrays in memory.
        for index, asset in enumerate(manifest["assets"]):
            if asset["kind"] != "trace_manifests":
                continue
            if asset["bytes"] > MAX_MANIFEST_BYTES:
                raise HistoryTransferError("Oversized trace manifest")
            value = json.loads((scratch / f"asset-{index}").read_bytes())
            from .trace_store import TraceManifest
            parsed = TraceManifest.from_dict(value)
            if asset["path"] != f"manifests/{parsed.id}.json":
                raise HistoryTransferError("Trace manifest identity mismatch")
            for chunk in parsed.chunks:
                _relative(chunk.relative_path)
                if ("trace_chunks", chunk.relative_path) not in artifacts:
                    raise HistoryTransferError("Trace manifest references an unlisted chunk")

    def preview_bundle(self, path: Path) -> dict[str, Any]:
        self.data_root.mkdir(parents=True, exist_ok=True)
        try:
            with contextlib.closing(_connect(self.database_path, readonly=True)) as db, tempfile.TemporaryDirectory(prefix="pitwall-preview-", dir=self.data_root) as tmp, zipfile.ZipFile(path) as archive:
                manifest, staging = self._stage(archive, Path(tmp), _schema(db), db.execute("PRAGMA user_version").fetchone()[0])
                staging.close()
                return self._summary(manifest)
        except (sqlite3.Error, zipfile.BadZipFile, KeyError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
            raise HistoryTransferError(f"Invalid history archive: {exc}") from exc

    @staticmethod
    def _lookup(db: sqlite3.Connection, table: str, columns: list[str], key: str) -> dict[str, Any] | None:
        values = json.loads(key)
        row = db.execute(f'SELECT * FROM "{table}" WHERE ' + " AND ".join(f'"{c}"=?' for c in columns), values).fetchone()
        return dict(row) if row is not None else None

    def import_bundle(self, path: Path) -> dict[str, Any]:
        path = Path(path).resolve()
        self.data_root.mkdir(parents=True, exist_ok=True)
        published: list[Path] = []
        try:
            with _LOCK, contextlib.closing(_connect(self.database_path)) as db, tempfile.TemporaryDirectory(prefix="pitwall-import-", dir=self.data_root) as tmp, zipfile.ZipFile(path) as archive:
                origin = self._state(db)
                db.commit()
                schema = _schema(db)
                manifest, staged = self._stage(archive, Path(tmp), schema, db.execute("PRAGMA user_version").fetchone()[0])
                with contextlib.closing(staged):
                    counts = {"imported_rows": 0, "skipped_rows": 0, "imported_sessions": 0, "skipped_sessions": 0}
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("PRAGMA defer_foreign_keys=ON")
                    try:
                        for item in staged.execute("SELECT * FROM rows ORDER BY seq"):
                            table = item["table_name"]
                            row = self._validate_row(json.loads(item["body"]), schema[table])
                            keys = _pk(schema, table)
                            if table == "user_preferences":
                                existing_preference = self._lookup(db, table, keys, _key(row, keys))
                                if existing_preference is not None:
                                    if existing_preference["value_json"] != row["value_json"]:
                                        db.execute("INSERT OR IGNORE INTO history_transfer_preference_variants VALUES(?,?,?,?)", (item["origin"], row["key"], item["digest"], item["body"]))
                                        manifest["warnings"].append(f"Kept this device's {row['key']}; incoming preference preserved as a variant")
                                    counts["skipped_rows"] += 1
                                    staged.execute("INSERT INTO mappings VALUES(?,?,?)", (table, item["local_source_key"], _key(existing_preference, keys)))
                                    continue
                            receipt = db.execute("SELECT * FROM history_transfer_receipts WHERE origin=? AND table_name=? AND source_key=?", (item["origin"], table, item["source_key"])).fetchone()
                            if receipt:
                                if receipt["source_digest"] != item["digest"]:
                                    raise _Conflict(table, item["source_key"], "Source recording changed since its previous transfer")
                                existing = self._lookup(db, table, keys, receipt["local_key"])
                                if existing is None:
                                    raise _Conflict(table, item["source_key"], "Previously transferred record was deleted locally")
                                if _row_digest(table, existing) != receipt["local_digest"]:
                                    raise _Conflict(table, item["source_key"], "Local record changed since its previous transfer")
                                local_key = receipt["local_key"]
                                counts["skipped_rows"] += 1
                                if table == "recorded_sessions":
                                    counts["skipped_sessions"] += 1
                            elif item["origin"] == origin:
                                existing = self._lookup(db, table, keys, item["source_key"])
                                if existing is None or _row_digest(table, existing) != item["digest"]:
                                    raise _Conflict(table, item["source_key"], "Returning history differs from its original recording")
                                local_key = item["source_key"]
                                counts["skipped_rows"] += 1
                                if table == "recorded_sessions":
                                    counts["skipped_sessions"] += 1
                            else:
                                for column, target in INTEGER_REFS.get(table, {}).items():
                                    if row[column] is None:
                                        continue
                                    mapping = staged.execute("SELECT local_key FROM mappings WHERE table_name=? AND source_key=?", (target, _json([row[column]]))).fetchone()
                                    if mapping is None:
                                        raise HistoryTransferError("Missing parent history mapping")
                                    row[column] = json.loads(mapping[0])[0]
                                old_key = _key(row, keys)
                                integer_id = len(keys) == 1 and next(c for c in schema[table] if c["name"] == keys[0])["type"] == "INTEGER"
                                existing = self._lookup(db, table, keys, old_key)
                                if existing is not None and _row_digest(table, existing) == _row_digest(table, row):
                                    local_key = old_key
                                    counts["skipped_rows"] += 1
                                    if table == "recorded_sessions":
                                        counts["skipped_sessions"] += 1
                                else:
                                    if existing is not None:
                                        if integer_id and table != "sessions":
                                            row[keys[0]] = db.execute(f'SELECT COALESCE(MAX("{keys[0]}"),0)+1 FROM "{table}"').fetchone()[0]
                                        else:
                                            raise _Conflict(table, old_key, "Existing recording or model has different content")
                                    if table == "track_models" and row["active"] and db.execute("SELECT 1 FROM track_models WHERE track_id=? AND layout_signature=? AND active=1", (row["track_id"], row["layout_signature"])).fetchone():
                                        row["active"] = 0
                                    if table == "segment_models" and row["active"] and db.execute("SELECT 1 FROM segment_models WHERE track_model_id=? AND active=1", (row["track_model_id"],)).fetchone():
                                        row["active"] = 0
                                    columns = list(row)
                                    try:
                                        db.execute(f'INSERT INTO "{table}" (' + ",".join(f'"{c}"' for c in columns) + ") VALUES (" + ",".join("?" for _ in columns) + ")", [row[c] for c in columns])
                                    except sqlite3.IntegrityError as exc:
                                        raise _Conflict(table, old_key, "Recording conflicts with an existing unique identity") from exc
                                    local_key = _key(row, keys)
                                    counts["imported_rows"] += 1
                                    if table == "recorded_sessions":
                                        counts["imported_sessions"] += 1
                                db.execute("INSERT INTO history_transfer_receipts VALUES(?,?,?,?,?,?)", (item["origin"], table, item["source_key"], item["digest"], local_key, _row_digest(table, self._lookup(db, table, keys, local_key))))
                            staged.execute("INSERT INTO mappings VALUES(?,?,?)", (table, item["local_source_key"], local_key))
                        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                            raise HistoryTransferError("Imported history failed relationship validation")
                        for index, asset in enumerate(manifest["assets"]):
                            target = self._asset_path(asset)
                            if target.exists():
                                if not target.is_file() or _hash_file(target) != asset["sha256"]:
                                    raise _Conflict("artifact", asset["path"], "Existing artifact has different content")
                                continue
                            target.parent.mkdir(parents=True, exist_ok=True)
                            self._space(target.parent, asset["bytes"])
                            # Exclusive creation prevents an external writer from being
                            # overwritten after the existence check.
                            with (Path(tmp) / f"asset-{index}").open("rb") as source, target.open("xb") as output:
                                published.append(target)
                                shutil.copyfileobj(source, output, 1024 * 1024)
                                output.flush()
                                os.fsync(output.fileno())
                        db.commit()
                        return {"status": "imported", **self._summary(manifest), **counts, "conflicts": []}
                    except Exception:
                        db.rollback()
                        for target in reversed(published):
                            target.unlink(missing_ok=True)
                        published.clear()
                        raise
        except _Conflict as exc:
            directory = self.data_root / "transfers" / "conflicts"
            directory.mkdir(parents=True, exist_ok=True)
            preserved = directory / f"{_hash_file(path)}.pitbox"
            if not preserved.exists():
                self._space(directory, path.stat().st_size)
                with path.open("rb") as source, preserved.open("xb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
            return {"status": "conflict", "imported_rows": 0, "imported_sessions": 0,
                    "conflicts": [exc.detail], "preserved_path": str(preserved),
                    "message": "No history was changed. The conflicting archive was preserved for review."}
        except (sqlite3.Error, zipfile.BadZipFile, KeyError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
            raise HistoryTransferError(f"Invalid history archive: {exc}") from exc


__all__ = ["HistoryTransferService", "HistoryTransferError"]
