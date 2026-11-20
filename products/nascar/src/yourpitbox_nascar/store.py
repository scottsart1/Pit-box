from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path


class Store:
    """One database per NASCAR install. Every write is small and transactional."""

    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.root = root
        self.lock = threading.RLock()
        self.db = sqlite3.connect(root / "nascar.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, created REAL NOT NULL, config TEXT NOT NULL,
                snapshot TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS laps (
                session_id TEXT NOT NULL REFERENCES sessions(id), number INTEGER NOT NULL,
                data TEXT NOT NULL, PRIMARY KEY(session_id, number));
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                created REAL NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        self.db.commit()

    def create(self, sid: str, config: dict):
        with self.lock, self.db:
            self.db.execute("INSERT INTO sessions(id,created,config) VALUES(?,?,?)", (sid, time.time(), json.dumps(config)))

    def snapshot(self, sid: str, data: dict):
        with self.lock, self.db:
            self.db.execute("UPDATE sessions SET snapshot=? WHERE id=?", (json.dumps(data), sid))

    def save_lap(self, sid: str, data: dict):
        with self.lock, self.db:
            self.db.execute("INSERT INTO laps VALUES(?,?,?) ON CONFLICT(session_id,number) DO UPDATE SET data=excluded.data", (sid, data["number"], json.dumps(data)))

    def note(self, sid: str, kind: str, data: dict):
        with self.lock, self.db:
            self.db.execute("INSERT INTO notes(session_id,created,kind,data) VALUES(?,?,?,?)", (sid, time.time(), kind, json.dumps(data)))

    def sessions(self) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT s.*, (SELECT count(*) FROM laps WHERE session_id=s.id) AS lap_count FROM sessions s ORDER BY created DESC LIMIT 200").fetchall()
            return [{"id": r["id"], "created": r["created"], "config": json.loads(r["config"]), "lap_count": r["lap_count"]} for r in rows]

    def read(self, sid: str) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
            if row is None:
                return None
            return {"id": sid, "created": row["created"], "config": json.loads(row["config"]),
                    "snapshot": json.loads(row["snapshot"]),
                    "laps": [json.loads(r[0]) for r in self.db.execute("SELECT data FROM laps WHERE session_id=? ORDER BY number", (sid,))],
                    "notes": [{"created": r[0], "kind": r[1], "data": json.loads(r[2])} for r in self.db.execute("SELECT created,kind,data FROM notes WHERE session_id=? ORDER BY id", (sid,))]}

    def setting(self, key: str, default=None):
        with self.lock:
            row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def save_setting(self, key: str, value):
        with self.lock, self.db:
            self.db.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    def close(self):
        with self.lock:
            self.db.close()
