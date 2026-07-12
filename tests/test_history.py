import pytest


@pytest.mark.asyncio
async def test_persisted_history_returns_radio_strategy_line_and_events(stack):
    store, database, _, _, _, _ = stack
    await store.update(
        session_uid=9191,
        track_id=10,
        track_name="Spa",
        session_type="Race",
        mode_profile="race",
        current_lap=7,
        total_laps=22,
    )
    state = await store.snapshot_analysis()
    await database.upsert_session(state)
    await database.save_radio_message(state, "driver", "What is the plan?")
    await database.save_radio_message(state, "engineer", "Box lap 10 for hard.")
    await database.save_strategy_snapshot(
        state,
        {
            "recommended": {"box_lap": 10, "fit_compound": "HARD"},
            "plans": [{"stops_remaining": 1}],
            "confidence": "medium",
            "model_summary": {"evidence_samples": 4},
            "compound_rule": {"compliant": True},
            "neutralisation": {"phase": "green"},
        },
    )
    await database.save_line_metrics(
        9191,
        10,
        7,
        {"line_score": 82.5, "mean_abs_deviation_m": 0.55},
    )
    await database.save_queued_session_event(
        {
            "session_uid": 9191,
            "track_id": 10,
            "current_lap": 7,
            "event_type": "SCAR",
            "payload": {"phase": "vsc"},
        }
    )

    history = await database.history_query(track_id=10, session_uid=9191)
    assert history["sessions"]
    assert len(history["radio"]) == 2
    assert history["strategies"][0]["recommended"]["fit_compound"] == "HARD"
    assert history["line_metrics"][0]["metrics"]["line_score"] == 82.5
    assert history["session_events"][0]["payload"]["phase"] == "vsc"

@pytest.mark.asyncio
async def test_v23_database_upgrade_preserves_existing_laps_and_adds_v3_tables(tmp_path):
    import sqlite3

    from pitwall.database import PitWallDatabase

    path = tmp_path / "pitwall.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE sessions (
                session_uid INTEGER PRIMARY KEY,
                track_id INTEGER NOT NULL,
                track_name TEXT NOT NULL,
                session_type TEXT NOT NULL,
                mode_profile TEXT NOT NULL,
                started_at REAL NOT NULL,
                ended_at REAL,
                result_position INTEGER,
                total_laps INTEGER,
                setup_json TEXT
            );
            CREATE TABLE laps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_uid INTEGER NOT NULL,
                track_id INTEGER NOT NULL,
                track_name TEXT NOT NULL,
                session_type TEXT NOT NULL,
                lap_num INTEGER NOT NULL,
                lap_time_ms INTEGER NOT NULL,
                valid INTEGER NOT NULL,
                compound TEXT,
                tyre_age_start INTEGER,
                tyre_age_end INTEGER,
                wear_start_json TEXT,
                wear_end_json TEXT,
                temps_end_json TEXT,
                fuel_start_kg REAL,
                fuel_end_kg REAL,
                position INTEGER,
                s1_ms INTEGER DEFAULT 0,
                s2_ms INTEGER DEFAULT 0,
                s3_ms INTEGER DEFAULT 0,
                pit_status INTEGER DEFAULT 0,
                pit_lane_time_ms INTEGER DEFAULT 0,
                setup_json TEXT,
                trace_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                UNIQUE(session_uid, lap_num)
            );
            INSERT INTO sessions VALUES
                (1234, 10, 'Spa', 'Race', 'race', 1.0, NULL, NULL, 22, '{}');
            INSERT INTO laps(
                session_uid, track_id, track_name, session_type, lap_num,
                lap_time_ms, valid, compound, trace_json, created_at
            ) VALUES (1234, 10, 'Spa', 'Race', 3, 109500, 1, 'MEDIUM', '[]', 2.0);
            """
        )

    database = PitWallDatabase(path)
    await database.initialize()
    history = await database.history_query(track_id=10, session_uid=1234)

    assert history["laps"][0]["lap_time_ms"] == 109500
    with sqlite3.connect(path) as db:
        names = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {
        "line_metrics",
        "radio_messages",
        "strategy_snapshots",
        "proactive_calls",
        "session_events",
    } <= names


@pytest.mark.asyncio
async def test_unsigned_64bit_session_uid_is_persisted_without_sqlite_overflow(stack):
    import sqlite3

    store, database, _, _, _, _ = stack
    session_uid = (1 << 64) - 12345
    await store.update(
        session_uid=session_uid,
        track_id=10,
        track_name="Spa",
        session_type="Race",
        mode_profile="race",
        current_lap=3,
        total_laps=22,
    )
    state = await store.snapshot_analysis()

    await database.upsert_session(state)
    await database.save_radio_message(state, "driver", "Radio check")
    await database.save_strategy_snapshot(
        state,
        {
            "recommended": {"box_lap": 10, "fit_compound": "HARD"},
            "plans": [],
            "confidence": "low",
            "model_summary": {},
            "compound_rule": {},
            "neutralisation": {},
        },
    )
    await database.save_line_metrics(
        session_uid,
        10,
        3,
        {"line_score": 90.0},
    )
    await database.save_proactive_call(
        state,
        {"type": "lap_summary", "payload": {}},
        "Pace is improving.",
        True,
    )
    await database.save_queued_session_event(
        {
            "session_uid": session_uid,
            "track_id": 10,
            "current_lap": 3,
            "event_type": "SCAR",
            "payload": {"phase": "vsc"},
        }
    )
    await database.save_session_event(state, "PENA", {"seconds": 5})
    await database.save_setup_run(
        session_uid,
        10,
        "Spa",
        "race",
        {"front_wing": 20},
        {"lap_time_ms": 110000},
        110.0,
    )
    await database.add_feedback(session_uid, 10, "handling", "rear is loose")
    await database.save_lap(
        {
            "session_uid": session_uid,
            "track_id": 10,
            "track_name": "Spa",
            "session_type": "Race",
            "lap_num": 3,
            "lap_time_ms": 110000,
            "valid": True,
            "compound": "MEDIUM",
            "trace": [],
        },
        [],
    )

    history = await database.history_query(
        track_id=10,
        session_uid=session_uid,
    )

    assert history["sessions"][0]["session_uid"] == session_uid
    assert history["laps"][0]["session_uid"] == session_uid
    assert history["radio"][0]["session_uid"] == session_uid
    assert history["strategies"][0]["session_uid"] == session_uid
    assert history["line_metrics"][0]["session_uid"] == session_uid
    assert history["proactive_calls"][0]["session_uid"] == session_uid
    assert history["session_events"][0]["session_uid"] == session_uid

    with sqlite3.connect(database.path) as db:
        stored_uid = db.execute(
            "SELECT session_uid FROM sessions LIMIT 1"
        ).fetchone()[0]
    assert stored_uid < 0
