"""Connections must be closed by their owner, not by delayed cyclic GC."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest

from pitwall import history_transfer
from pitwall.analysis_jobs import AnalysisJobService
from pitwall.catalog import SessionCatalog
from pitwall.comparison_service import ComparisonService
from pitwall.database import PitWallDatabase
from pitwall.field_service import FieldAnalysisService
from pitwall.full_field_archive import FullFieldArchiveService
from pitwall.network_profiles import NetworkProfileRepository
from pitwall.settings_service import PREFERENCE_KEY, load_saved
from pitwall.storage_service import StorageService
from pitwall.track_model_service import TrackModelService

FACTORIES = [
    PitWallDatabase._connect,
    SessionCatalog._connect,
    AnalysisJobService._connect,
    ComparisonService._connect,
    FieldAnalysisService._connect,
    FullFieldArchiveService._connect,
    NetworkProfileRepository._connect,
    StorageService._connect,
    TrackModelService._connect,
]


@pytest.fixture
def connections(monkeypatch):
    original = sqlite3.connect
    retained = []

    def connect(*args, **kwargs):
        db = original(*args, **kwargs)
        retained.append(db)  # Deliberately prevent garbage collection.
        return db

    monkeypatch.setattr(sqlite3, "connect", connect)
    yield retained
    for db in retained:
        db.close()


def assert_closed(db):
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        db.execute("SELECT 1")


@pytest.mark.parametrize("factory", FACTORIES, ids=lambda f: f.__qualname__)
@pytest.mark.parametrize("fail", [False, True], ids=["success", "error"])
def test_repository_scope_closes_connection(tmp_path, connections, factory, fail):
    path = tmp_path / "pitwall.sqlite3"
    owner = SimpleNamespace(path=path, database_path=path)
    try:
        with factory(owner) as db:
            assert db.execute("SELECT 1").fetchone()[0] == 1
            assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert db.row_factory is sqlite3.Row
            if fail:
                raise ValueError("operation failed")
    except ValueError as exc:
        assert fail and str(exc) == "operation failed"
    assert len(connections) == 1
    assert_closed(connections[0])


@pytest.mark.parametrize("factory", FACTORIES, ids=lambda f: f.__qualname__)
def test_pragma_failure_also_closes_connection(tmp_path, monkeypatch, factory):
    original = sqlite3.connect
    retained = []

    class RejectPragma(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql.startswith("PRAGMA"):
                raise sqlite3.OperationalError("configuration failed")
            return super().execute(sql, *args, **kwargs)

    def connect(*args, **kwargs):
        db = original(*args, factory=RejectPragma, **kwargs)
        retained.append(db)
        return db

    monkeypatch.setattr(sqlite3, "connect", connect)
    path = tmp_path / "pitwall.sqlite3"
    try:
        with pytest.raises(sqlite3.OperationalError, match="configuration failed"):
            with factory(SimpleNamespace(path=path, database_path=path)):
                pytest.fail("Invalid connection escaped its factory")
        assert len(retained) == 1
        assert_closed(retained[0])
    finally:
        for db in retained:
            db.close()


@pytest.mark.parametrize("fail", [False, True], ids=["commit", "rollback"])
def test_transaction_semantics_are_preserved(tmp_path, connections, fail):
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    with database._connect() as db:
        db.execute("CREATE TABLE sentinel(value INTEGER)")
    try:
        with database._connect() as db:
            db.execute("INSERT INTO sentinel VALUES (42)")
            if fail:
                raise ValueError("rollback this write")
    except ValueError:
        assert fail
    with database._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM sentinel").fetchone()[0] == (0 if fail else 1)
    for db in connections:
        assert_closed(db)


def test_failed_commit_rolls_back_and_closes(tmp_path, connections):
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    with database._connect() as db:
        db.executescript("""
            CREATE TABLE parent(id INTEGER PRIMARY KEY);
            CREATE TABLE child(parent_id REFERENCES parent(id)
                              DEFERRABLE INITIALLY DEFERRED);
        """)
    with pytest.raises(sqlite3.IntegrityError):
        with database._connect() as db:
            db.execute("INSERT INTO child VALUES (123)")
    with database._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM child").fetchone()[0] == 0
    for db in connections:
        assert_closed(db)


@pytest.mark.parametrize("missing_table", [False, True])
def test_saved_settings_closes_read_connection(tmp_path, connections, missing_table):
    path = tmp_path / "pitwall.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        if not missing_table:
            db.execute("CREATE TABLE user_preferences(key TEXT, value_json TEXT)")
            db.execute("INSERT INTO user_preferences VALUES (?, ?)", (PREFERENCE_KEY, '{"voice": "echo"}'))
    assert load_saved(path) == ({} if missing_table else {"voice": "echo"})
    for db in connections:
        assert_closed(db)


@pytest.mark.parametrize("restore", [False, True], ids=["backup", "restore"])
def test_second_backup_connection_failure_closes_first(tmp_path, monkeypatch, restore):
    path = tmp_path / "pitwall.sqlite3"
    backup = tmp_path / "backups" / "saved.sqlite3"
    backup.parent.mkdir()
    for target in (path, backup):
        with closing(sqlite3.connect(target)) as db, db:
            db.execute("CREATE TABLE sentinel(value INTEGER)")
    original = sqlite3.connect
    retained = []

    def connect(*args, **kwargs):
        if retained:
            raise sqlite3.OperationalError("second open failed")
        db = original(*args, **kwargs)
        retained.append(db)
        return db

    monkeypatch.setattr(sqlite3, "connect", connect)
    try:
        database = PitWallDatabase(path)
        with pytest.raises(sqlite3.OperationalError, match="second open failed"):
            if restore:
                database._restore_backup_sync(backup)
            else:
                database._backup_sync()
        assert_closed(retained[0])
    finally:
        for db in retained:
            db.close()


def test_transfer_configuration_failure_closes_connection(tmp_path, monkeypatch):
    original = sqlite3.connect
    retained = []

    class RejectPragma(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql.startswith("PRAGMA"):
                raise sqlite3.OperationalError("configuration failed")
            return super().execute(sql, *args, **kwargs)

    def connect(*args, **kwargs):
        db = original(*args, factory=RejectPragma, **kwargs)
        retained.append(db)
        return db

    monkeypatch.setattr(sqlite3, "connect", connect)
    try:
        with pytest.raises(sqlite3.OperationalError, match="configuration failed"):
            history_transfer._connect(tmp_path / "transfer.sqlite3")
        assert_closed(retained[0])
    finally:
        for db in retained:
            db.close()
