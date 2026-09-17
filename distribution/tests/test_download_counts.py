"""Real SQLite validation of the exact Worker UPSERT and additive migration."""
import re
import json
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "activation-server"
MIGRATION = (SERVER / "migrations/0005_download_counts.sql").read_text(encoding="utf-8")
WORKER = (SERVER / "src/worker.js").read_text(encoding="utf-8")
UPSERT = re.search(r"`(INSERT INTO download_daily.*?)`", WORKER, re.S).group(1)


def test_migration_is_additive_repeatable_and_does_not_touch_customer_tables():
    db = sqlite3.connect(":memory:")
    db.executescript("CREATE TABLE subscribers(email TEXT); INSERT INTO subscribers VALUES ('retained');")
    db.executescript(MIGRATION)
    db.executescript(MIGRATION)
    assert db.execute("SELECT email FROM subscribers").fetchall() == [("retained",)]
    columns = [row[1] for row in db.execute("PRAGMA table_info(download_daily)")]
    assert columns == ["day", "platform", "starts", "first_started_at", "last_started_at"]
    assert db.execute("SELECT count(*) FROM download_daily").fetchone() == (0,)


def test_worker_upsert_increments_separate_platforms_without_overwriting_first_time():
    db = sqlite3.connect(":memory:")
    db.executescript(MIGRATION)
    day, first, last = "2026-09-17", "2026-09-17T01:00:00Z", "2026-09-17T02:00:00Z"
    for _ in range(35):
        db.execute(UPSERT, (day, "windows", first, first))
    db.execute(UPSERT, (day, "windows", last, last))
    db.execute(UPSERT, (day, "android", last, last))
    assert db.execute("SELECT platform, starts, first_started_at, last_started_at FROM download_daily ORDER BY platform").fetchall() == [
        ("android", 1, last, last), ("windows", 36, first, last),
    ]
    plan = db.execute("EXPLAIN QUERY PLAN SELECT * FROM download_daily WHERE day >= ?", (day,)).fetchall()
    assert any("INDEX" in row[3] for row in plan)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(UPSERT, (day, "arbitrary", first, last))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE download_daily SET starts = -1")


def test_report_page_has_no_embedded_secret_and_no_browser_persistence():
    page = (ROOT / "website/download-stats.html").read_text(encoding="utf-8")
    script = (ROOT / "website/download-stats.js").read_text(encoding="utf-8")
    assert 'type="password"' in page
    assert 'content="noindex, nofollow"' in page
    assert 'id="reportData" hidden' in page
    for forbidden in ("localStorage", "sessionStorage", "document.cookie", "innerHTML"):
        assert forbidden not in script
    assert 'Authorization: `Bearer ${accessKey}`' in script
    assert 'cache: "no-store"' in script
    assert "all partial/Range requests are excluded" in page
    assert "not website visits, unique people or completed installations" in page
    assert "names, emails" in (ROOT / "website/index.html").read_text(encoding="utf-8")
    assert 'id="historyData" hidden' in page
    assert "Historical file requests" in page
    assert "two sets of totals are kept separate" in page
    assert 'id="combinedAll"' in page
    assert page.index('id="combinedAll"') < page.index('id="totalAll"')
    assert "mixed-method activity total" in page
    assert "download-stats.js?v=" in page


def test_history_snapshot_reimport_is_idempotent_and_never_changes_live_counts():
    db = sqlite3.connect(":memory:")
    db.executescript(MIGRATION)
    history_migration = (SERVER / "migrations/0006_download_history.sql").read_text(encoding="utf-8")
    db.executescript(history_migration)
    db.executescript(history_migration)
    db.execute(UPSERT, ("2026-09-17", "windows", "2026-09-17T10:00:00Z", "2026-09-17T10:00:00Z"))
    snapshot = json.dumps({"metric": "historical_file_requests", "totals": {"all": 270}})
    sql = "INSERT INTO download_history(id, report_json) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET report_json=excluded.report_json"
    db.execute(sql, (snapshot,))
    db.execute(sql, (snapshot,))
    assert db.execute("SELECT count(*) FROM download_history").fetchone() == (1,)
    assert db.execute("SELECT report_json FROM download_history").fetchone() == (snapshot,)
    assert db.execute("SELECT SUM(starts) FROM download_daily").fetchone() == (1,)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO download_history VALUES (2, '{}')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE download_history SET report_json='not json'")
