from __future__ import annotations

import sqlite3

import pytest

from pitwall.config import Settings
from pitwall.database import PitWallDatabase
from pitwall.migrations import LATEST_SCHEMA_VERSION, MIGRATIONS


@pytest.mark.asyncio
async def test_existing_database_is_backed_up_before_additive_v42_migration(
    tmp_path,
) -> None:
    path = tmp_path / "pitwall.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE sentinel(value TEXT NOT NULL)")
        db.execute("INSERT INTO sentinel(value) VALUES ('keep me')")

    database = PitWallDatabase(path)
    await database.initialize()

    assert database.schema_version == LATEST_SCHEMA_VERSION
    assert database.last_backup_path is not None
    assert database.last_backup_path.exists()
    with sqlite3.connect(database.last_backup_path) as backup:
        assert backup.execute("SELECT value FROM sentinel").fetchone()[0] == "keep me"
        # The backup is a true pre-migration image.
        assert (
            backup.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE name='recorded_sessions'"
            ).fetchone()[0]
            == 0
        )
    with sqlite3.connect(path) as migrated:
        assert migrated.execute("SELECT value FROM sentinel").fetchone()[0] == "keep me"
        for table in (
            "schema_versions",
            "recorded_sessions",
            "session_cars",
            "recorded_laps",
            "trace_manifests",
            "raw_captures",
            "comparisons",
            "comparison_segment_results",
            "findings",
            "network_profiles",
            "analysis_jobs",
        ):
            assert (
                migrated.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()[0]
                == 1
            )


@pytest.mark.asyncio
async def test_v42_migration_is_idempotent_and_does_not_repeat_backup(tmp_path) -> None:
    path = tmp_path / "pitwall.sqlite3"
    first = PitWallDatabase(path)
    await first.initialize()
    assert first.last_backup_path is None  # a brand-new empty database needs no backup

    second = PitWallDatabase(path)
    await second.initialize()
    assert second.schema_version == LATEST_SCHEMA_VERSION
    assert second.last_backup_path is None
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM schema_versions").fetchone()[0] == len(
            MIGRATIONS
        )


@pytest.mark.asyncio
async def test_integrity_report_exposes_version_without_database_contents(
    tmp_path,
) -> None:
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    report = await database.integrity_report()
    assert report["ok"] is True
    assert report["checks"] == ["ok"]
    assert report["schema_version"] == LATEST_SCHEMA_VERSION


def test_v42_network_and_capture_settings_validate_and_keep_legacy_udp_alias(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PITWALL_UDP_HOST", "192.168.1.42")
    legacy = Settings(_env_file=None)
    assert legacy.udp_bind_host == "192.168.1.42"

    monkeypatch.setenv("PITWALL_UDP_BIND_HOST", "0.0.0.0")
    current = Settings(_env_file=None)
    assert current.udp_bind_host == "0.0.0.0"
    assert current.capture_dir == current.data_dir / "captures"

    with pytest.raises(ValueError, match="port must be between"):
        Settings(_env_file=None, udp_port=0)
    with pytest.raises(ValueError, match="capture mode"):
        Settings(_env_file=None, capture_mode="maximum-ish")
    with pytest.raises(ValueError, match="retention days"):
        Settings(_env_file=None, retention_days=-1)
    with pytest.raises(ValueError, match="WEB_LAN_ACCESS"):
        Settings(_env_file=None, web_host="0.0.0.0")
    with pytest.raises(ValueError, match="WEB_ACCESS_TOKEN"):
        Settings(_env_file=None, web_lan_access=True)
    secured = Settings(
        _env_file=None,
        web_host="0.0.0.0",
        web_lan_access=True,
        web_access_token="correct-horse-battery-staple",
    )
    assert secured.web_lan_access is True
