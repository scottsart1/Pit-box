"""Regressions for the storage and capture defects found in the 3.6.1 review.

The live database had grown to 677 MB for 327 laps. Strategy snapshots were
being written several times a second, controller-button packets were persisted
at packet frequency, the chequered flag was recorded on every repeat of the
final-classification packet, and the transcription steering prompt had been
captured as a driver radio message.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from pitwall.audio import AudioService
from pitwall.proactive import ProactiveEngineer
from pitwall.udp import UNLOGGED_EVENTS


def _race_state(**overrides):
    state = {
        "session_uid": 4242,
        "track_id": 9,
        "current_lap": 12,
        "race_control_phase": "green",
        "player_position": 5,
        "drivers": [],
    }
    state.update(overrides)
    return state


def _plan(box_lap=18, compound="HARD", stops=1, confidence="medium"):
    return {
        "recommended": {
            "box_lap": box_lap,
            "fit_compound": compound,
            "stops_remaining": stops,
        },
        "confidence": confidence,
        "compound_rule": {"change_outstanding": False},
        "plans": [
            {
                "box_lap": box_lap,
                "fit_compound": compound,
                "stops_remaining": stops,
                "risk_adjusted_time_s": 1234.5,
                # The per-lap simulation is the bulk of a stored snapshot.
                "stint_models": [{"lap": n, "wear": n * 1.5} for n in range(40)],
            }
            for _ in range(6)
        ],
    }


@pytest.mark.asyncio
async def test_strategy_snapshot_written_once_per_material_change(stack):
    """recompute() runs on a 0.35 s tick; only real changes may reach SQLite."""
    _store, database, strategy, *_ = stack
    state = _race_state()

    # Same lap, same call, repeated ticks: one row.
    for _ in range(25):
        key = strategy._snapshot_key(state, _plan())
        if key != strategy._last_snapshot_key:
            strategy._last_snapshot_key = key
            await database.save_strategy_snapshot(state, _plan())

    history = await database.history_query(session_uid=4242, limit=100)
    assert len(history["strategies"]) == 1

    # A changed pit call is material and must be recorded.
    changed = _plan(box_lap=21, compound="MEDIUM")
    key = strategy._snapshot_key(state, changed)
    assert key != strategy._last_snapshot_key
    strategy._last_snapshot_key = key
    await database.save_strategy_snapshot(state, changed)

    history = await database.history_query(session_uid=4242, limit=100)
    assert len(history["strategies"]) == 2

    # A new lap is also material, even with an unchanged call.
    next_lap = _race_state(current_lap=13)
    assert strategy._snapshot_key(next_lap, changed) != strategy._last_snapshot_key


@pytest.mark.asyncio
async def test_stored_snapshot_drops_per_lap_stint_models(stack):
    """Ranked alternatives are kept for explanation; the simulation is not."""
    _store, database, *_ = stack
    await database.save_strategy_snapshot(_race_state(), _plan())
    history = await database.history_query(session_uid=4242, limit=10)
    plans = history["strategies"][0]["plans"]
    assert len(plans) == 3, "only the top alternatives are retained"
    assert all("stint_models" not in plan for plan in plans)
    assert all("risk_adjusted_time_s" in plan for plan in plans)


def test_controller_button_events_are_not_logged():
    """BUTN arrives at controller frequency and is consumed by the PTT callback.

    Logging it flooded the 200-entry events log, which is what "any race
    updates" reads, so real incidents were pushed out by button noise.
    """
    assert "BUTN" in UNLOGGED_EVENTS
    for narrative_code in ("SCAR", "PENA", "RTMT", "OVTK", "RDFL", "CHQF"):
        assert narrative_code not in UNLOGGED_EVENTS


def test_prompt_echo_guard_tracks_the_prompt_actually_sent():
    """The guard must not go stale when transcription_prompt() changes."""
    prompt = AudioService.transcription_prompt(
        ["VERSTAPPEN", "ALONSO"], wake_phrases=["mark", "marc", "hey mark"]
    )
    # The exact failure seen in production: the steering prompt transcribed back.
    assert AudioService._looks_like_prompt_echo(prompt, prompt)
    assert AudioService._looks_like_prompt_echo(
        "the opening word may sound like Mark or Marc; transcribe it literally. "
        "Accepted openings: Mark, Hey Mark, Mark Radio, Hey Marc, Marc.",
        prompt,
    )
    # Real radio calls survive, including ones built from prompt keywords.
    for utterance in (
        "box box",
        "what is the gap to the car ahead",
        "I am not boxing this lap, stay out",
        "does Verstappen have any damage on his car",
        "give me the undercut window against Alonso",
    ):
        assert not AudioService._looks_like_prompt_echo(utterance, prompt), utterance


def test_neighbour_gaps_replace_the_nonexistent_state_fields():
    """Progress payloads previously read gap_ahead_s/gap_behind_s, always None."""
    state = _race_state(
        player_position=5,
        drivers=[
            {"position": 4, "name": "ALONSO", "gap_to_player_s": -1.8,
             "tyre_compound": "HARD", "tyre_age": 14},
            {"position": 5, "name": "PLAYER", "gap_to_player_s": 0.0,
             "tyre_compound": "MEDIUM", "tyre_age": 3},
            {"position": 6, "name": "GASLY", "gap_to_player_s": 2.4,
             "tyre_compound": "SOFT", "tyre_age": 2},
        ],
    )
    gaps = ProactiveEngineer._neighbour_gaps(state)
    assert gaps["ahead"] == {
        "driver": "ALONSO", "gap_s": 1.8, "tyre": "HARD", "tyre_age": 14,
    }
    assert gaps["behind"]["driver"] == "GASLY"
    assert gaps["behind"]["gap_s"] == 2.4
    # Gaps are reported as positive magnitudes in both directions.
    assert gaps["ahead"]["gap_s"] > 0 and gaps["behind"]["gap_s"] > 0


def test_neighbour_gaps_are_empty_without_a_timing_position():
    assert ProactiveEngineer._neighbour_gaps(_race_state(player_position=0)) == {
        "ahead": None,
        "behind": None,
    }


@pytest.mark.asyncio
async def test_maintenance_reclaims_space_but_keeps_reference_laps(stack):
    """The fastest valid lap per track must keep its trace at any age.

    That trace is the live-delta reference and the racing-line comparison
    baseline, so age-based pruning alone would silently disable both.
    """
    _store, database, *_ = stack
    trace = [{"t": n * 0.02, "d": n * 2.0, "speed": 200} for n in range(400)]

    # An old session holding this track's reference lap, plus a slower lap.
    for lap_num, lap_ms in ((1, 90_000), (2, 95_000)):
        await database.save_lap(
            {
                "session_uid": 111, "track_id": 9, "track_name": "Baku",
                "session_type": "Race", "lap_num": lap_num, "lap_time_ms": lap_ms,
                "valid": True, "trace": trace, "created_at": 1.0,
            },
            [],
        )
    await database.upsert_session(
        {"session_uid": 111, "track_id": 9, "track_name": "Baku",
         "session_type": "Race", "mode_profile": "race", "total_laps": 20}
    )
    # A newer session, so the one above falls outside the retention window.
    await database.upsert_session(
        {"session_uid": 112, "track_id": 9, "track_name": "Baku",
         "session_type": "Race", "mode_profile": "race", "total_laps": 20}
    )

    # Junk rows that maintenance is expected to remove.
    for _ in range(50):
        await database.save_session_event(
            {"session_uid": 111, "track_id": 9, "current_lap": 1}, "BUTN",
            {"button_status": 4},
        )
    for _ in range(30):
        await database.save_strategy_snapshot(_race_state(session_uid=111), _plan())

    report = await database.maintain(keep_trace_sessions=1, vacuum=True)

    assert report["button_events_removed"] == 50
    assert report["strategy_snapshots_removed"] >= 29
    assert report["size_after_bytes"] <= report["size_before_bytes"]

    with sqlite3.connect(database.path) as db:
        db.row_factory = sqlite3.Row
        rows = {
            int(row["lap_num"]): json.loads(row["trace_json"])
            for row in db.execute("SELECT lap_num, trace_json FROM laps").fetchall()
        }
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute(
            "SELECT COUNT(*) FROM session_events WHERE event_type='BUTN'"
        ).fetchone()[0] == 0

    assert rows[1], "the track reference lap must keep its trace"
    assert rows[2] == [], "slower out-of-window laps release their trace"

    # The personal-best lookup, which drives the live delta, still works.
    personal_best = await database.get_personal_best(9)
    assert personal_best is not None
    assert personal_best["lap_time_ms"] == 90_000
    assert len(personal_best["trace"]) == len(trace)


@pytest.mark.asyncio
async def test_maintenance_repairs_sessions_that_ended_before_they_started(stack):
    """Two time.time() reads made short sessions finish before starting."""
    _store, database, *_ = stack
    await database.upsert_session(
        {"session_uid": 222, "track_id": 9, "track_name": "Baku",
         "session_type": "Race", "mode_profile": "race", "total_laps": 20}
    )
    with sqlite3.connect(database.path) as db:
        db.execute(
            "UPDATE sessions SET started_at=1000.0, ended_at=999.0 WHERE session_uid=222"
        )

    report = await database.maintain(keep_trace_sessions=5, vacuum=False)
    assert report["session_times_repaired"] == 1

    with sqlite3.connect(database.path) as db:
        ended_at = db.execute(
            "SELECT ended_at FROM sessions WHERE session_uid=222"
        ).fetchone()[0]
    assert ended_at is None


@pytest.mark.asyncio
async def test_upsert_session_never_finishes_before_it_starts(stack):
    """A session first written after classification must stay ordered."""
    _store, database, *_ = stack
    await database.upsert_session(
        {
            "session_uid": 333, "track_id": 9, "track_name": "Baku",
            "session_type": "Race", "mode_profile": "race", "total_laps": 20,
            "final_classification": {"position": 4},
        }
    )
    with sqlite3.connect(database.path) as db:
        row = db.execute(
            "SELECT started_at, ended_at, result_position FROM sessions WHERE session_uid=333"
        ).fetchone()
    assert row[2] == 4
    assert row[1] >= row[0]
