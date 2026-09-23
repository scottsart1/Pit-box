"""Regressions for the Analysis page failures reported from Windows and Android.

Lap Lab comparisons against rivals worked and then failed moments later; six
of eleven session reprocess jobs in a real history died on the same fault;
the tablet silently ignored comparisons against any rival on a different
compound; and the Library took over five seconds to list sessions on every
visit. Each of those is pinned here or beside the service it lives in.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException

from pitwall import database as database_module
from pitwall.api.analysis import _raise_service_error
from pitwall.comparison_service import TracesNotComparableError
from pitwall.database import PitWallDatabase

ROOT = Path(__file__).parents[1]
WORKSPACES = (ROOT / "static" / "js" / "workspaces.js").read_text(encoding="utf-8")
MAIN_ACTIVITY = (
    ROOT / "android" / "app" / "src" / "main" / "java" / "com" / "yourpitbox" / "app" / "MainActivity.java"
).read_text(encoding="utf-8")


def _create_comparison_source() -> str:
    """The function's code, without its comments."""
    start = WORKSPACES.index("async function createComparison()")
    end = WORKSPACES.index("function renderComparison()", start)
    return "\n".join(
        line
        for line in WORKSPACES[start:end].splitlines()
        if not line.strip().startswith("//")
    )


def test_a_caveated_rival_is_compared_without_a_blocking_dialog() -> None:
    """The Android WebView answered confirm() with false and showed nothing.

    Every comparison against a rival on a different compound or fuel load
    silently did nothing on the tablet. The caveats travel with the result.
    """
    source = _create_comparison_source()
    assert "confirm(" not in source
    assert "allow_caveated_reference: allowCaveat" in source
    assert "caveatNote" in source


def test_the_android_webview_shows_javascript_dialogs() -> None:
    """Without a chrome client, every confirmation cancelled itself."""
    assert "import android.webkit.WebChromeClient;" in MAIN_ACTIVITY
    assert "web.setWebChromeClient(new WebChromeClient());" in MAIN_ACTIVITY


def test_laps_that_cannot_be_aligned_are_a_422_not_a_500() -> None:
    with pytest.raises(HTTPException) as caught:
        _raise_service_error(TracesNotComparableError("no shared distance range"))
    assert caught.value.status_code == 422
    assert caught.value.detail["code"] == "traces_not_comparable"


@pytest.mark.asyncio
async def test_the_library_list_uses_an_index_for_trace_sizes(tmp_path: Path) -> None:
    """The Library took 5.8 s to list 164 sessions: every session scanned
    every one of 14,577 trace manifests to total its size."""
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    with sqlite3.connect(database.path) as db:
        indexes = {
            row[1]
            for table in ("trace_manifests", "raw_captures", "recorded_sessions")
            for row in db.execute(f"PRAGMA index_list({table})")
        }
        plan = " ".join(
            str(row[-1])
            for row in db.execute(
                """
                EXPLAIN QUERY PLAN
                SELECT s.id, (SELECT SUM(tc.byte_count) FROM trace_manifests tm
                              JOIN trace_chunks tc ON tc.manifest_id=tm.id
                              WHERE tm.session_id=s.id)
                FROM recorded_sessions s ORDER BY s.started_at DESC, s.id DESC LIMIT 51
                """
            )
        )
    assert {
        "idx_trace_manifests_session",
        "idx_raw_captures_session",
        "idx_recorded_sessions_started",
    } <= indexes
    assert "SCAN tm" not in plan
    assert "idx_trace_manifests_session" in plan


@pytest.mark.asyncio
async def test_startup_maintenance_does_not_rewrite_a_database_with_little_to_reclaim(
    tmp_path: Path,
) -> None:
    """A 228 MB history was rewritten on 54 launches, usually for under 5 MB,
    holding the write lock that comparisons and startup needed."""
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    report = await database.maintain(keep_trace_sessions=12, vacuum=True)
    assert report["vacuumed"] is False
    assert report["reclaimable_bytes"] < database_module.VACUUM_MIN_RECLAIM_BYTES


@pytest.mark.asyncio
async def test_startup_maintenance_still_reclaims_a_large_free_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    with sqlite3.connect(database.path) as db:
        db.execute("CREATE TABLE ballast (payload BLOB)")
        db.executemany(
            "INSERT INTO ballast VALUES (?)", ((b"x" * 4096,) for _ in range(600))
        )
        db.commit()
        db.execute("DROP TABLE ballast")
        db.commit()
    monkeypatch.setattr(database_module, "VACUUM_MIN_RECLAIM_BYTES", 1024 * 1024)
    report = await database.maintain(keep_trace_sessions=12, vacuum=True)
    assert report["vacuumed"] is True
    assert report["size_after_bytes"] < report["size_before_bytes"]
