from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pitwall.database import PitWallDatabase
from pitwall.field_service import FieldAnalysisService
from pitwall.full_field_archive import FullFieldArchiveService
from pitwall.session_assembler import (
    BranchInvalidation,
    EventStamp,
    LapEvent,
    ParticipantEvent,
    SampleEvent,
    SessionAssembler,
    SessionEvent,
)
from pitwall.trace_store import TraceStore


@pytest.mark.asyncio
async def test_opponent_batch_is_archived_and_player_batch_is_left_to_legacy_path(
    tmp_path: Path,
) -> None:
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    trace_store = TraceStore(tmp_path / "traces")
    archive = FullFieldArchiveService(database.path, trace_store, queue_size=8)
    await archive.start()
    assembler = SessionAssembler(batch_sink=archive.submit, field_trace_hz=20)

    def stamp(frame: int, time_s: float) -> EventStamp:
        return EventStamp(
            123, frame, frame, time_s, frame * 1_000_000, frame * 1_000_000
        )

    assembler.consume(
        SessionEvent(
            stamp(1, 0.1),
            track_id=4,
            layout_signature="f1:2026:4:5000",
            session_type=18,
            packet_format=2026,
            player_car_index=0,
        )
    )
    for index in (0, 1):
        assembler.consume(
            ParticipantEvent(
                stamp(2, 0.2),
                index,
                {"name": f"Driver {index}", "is_player": index == 0},
            )
        )
        for frame, distance in enumerate((0.0, 5.0, 10.0), 3):
            assembler.consume(
                SampleEvent(
                    stamp(frame, frame / 10),
                    index,
                    1,
                    "telemetry",
                    {
                        "lap_distance_m": distance,
                        "speed_mps": 50.0 + index,
                        "brake": 0.0,
                    },
                    units={"lap_distance_m": "m", "speed_mps": "m/s"},
                )
            )
            assembler.consume(
                SampleEvent(
                    stamp(frame, frame / 10),
                    index,
                    1,
                    "lap_data",
                    {
                        "lap_distance_m": distance,
                        "position": index + 1,
                    },
                    units={"lap_distance_m": "m", "position": "position"},
                )
            )
        assembler.consume(LapEvent(stamp(8, 1.0), index, 1, 2, 60_000, True))

    await archive.stop()
    snapshot = archive.snapshot()
    assert snapshot.player_batches_skipped == 1
    assert snapshot.persisted_laps == 1
    with sqlite3.connect(database.path) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            """
            SELECT l.*, c.car_index FROM recorded_laps l
            JOIN session_cars c ON c.id=l.session_car_id
            """
        ).fetchone()
        assert row is not None
        assert row["car_index"] == 1
        assert row["trace_manifest_id"]

    session = assembler.session
    assert session is not None
    positions = await FieldAnalysisService(
        database.path,
        trace_store=trace_store,
    ).positions(session.id)
    rival = next(item for item in positions["series"] if item["car_index"] == 1)
    assert positions["availability"] == "observed"
    assert rival["points"] == [
        {
            "lap_number": 1,
            "position": 2,
            "availability": "observed",
            "context_mask": 0,
        }
    ]


@pytest.mark.asyncio
async def test_flashback_invalidates_only_batches_after_rewind_target(
    tmp_path: Path,
) -> None:
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    archive = FullFieldArchiveService(
        database.path, TraceStore(tmp_path / "traces"), queue_size=8
    )
    await archive.start()
    assembler = SessionAssembler(batch_sink=archive.submit, field_trace_hz=20)

    def stamp(frame: int) -> EventStamp:
        return EventStamp(88, frame, frame, frame / 10, frame, frame)

    assembler.consume(
        SessionEvent(stamp(1), track_id=4, packet_format=2026, player_car_index=0)
    )
    assembler.consume(
        ParticipantEvent(stamp(2), 1, {"name": "Rival", "is_player": False})
    )
    for lap_number, frames in ((1, (3, 4, 5)), (2, (7, 8, 9))):
        for frame, distance in zip(frames, (0.0, 5.0, 10.0)):
            assembler.consume(
                SampleEvent(
                    stamp(frame),
                    1,
                    lap_number,
                    "telemetry",
                    {"lap_distance_m": distance, "speed_mps": 50.0},
                )
            )
        assembler.consume(
            LapEvent(stamp(frames[-1]), 1, lap_number, lap_number + 1, 60_000, True)
        )

    await archive.queue.join()
    session = assembler.session
    assert session is not None
    assert archive.submit(
        BranchInvalidation(
            session=session,
            invalidated_timeline_epoch=0,
            replacement_timeline_epoch=1,
            target_overall_frame_identifier=5,
            target_session_time_s=0.5,
            target_lap_number=1,
            affected_batch_ids=(assembler.finalized_batches[-1].batch_id,),
            reason="test_rewind",
        )
    )
    await archive.stop()

    with sqlite3.connect(database.path) as db:
        rows = db.execute(
            "SELECT lap_number, valid, invalid_reason_mask FROM recorded_laps "
            "ORDER BY lap_number"
        ).fetchall()
    assert rows == [(1, 1, 0), (2, 0, 2)]


@pytest.mark.asyncio
async def test_trace_failure_aborts_pending_car_buffers(tmp_path: Path) -> None:
    class FailingTraceStore(TraceStore):
        def finalize_lap(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise OSError("injected trace write failure")

    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    trace_store = FailingTraceStore(tmp_path / "traces")
    archive = FullFieldArchiveService(database.path, trace_store, queue_size=4)
    await archive.start()
    assembler = SessionAssembler(batch_sink=archive.submit)

    def stamp(frame: int) -> EventStamp:
        return EventStamp(99, frame, frame, frame / 10, frame, frame)

    assembler.consume(SessionEvent(stamp(1), player_car_index=0))
    assembler.consume(ParticipantEvent(stamp(2), 1, {"is_player": False}))
    for frame, distance in ((3, 0.0), (4, 10.0)):
        assembler.consume(
            SampleEvent(
                stamp(frame),
                1,
                1,
                "telemetry",
                {"lap_distance_m": distance, "speed_mps": 45.0},
            )
        )
    assembler.consume(LapEvent(stamp(5), 1, 1, 2, 60_000, True))
    await archive.stop()

    assert archive.snapshot().write_errors == 1
    assert (
        trace_store.abort_pending(assembler.finalized_batches[-1].session_car_id) == 0
    )


def test_race_trace_scope_keeps_only_the_cars_the_analysis_reads_back():
    """Per-car telemetry was landing sporadically under full-field write load.

    In a race, keep the player, the teammate, the podium, and the cars the
    player started among (grid +/- 2). Everything else is write pressure for
    data nothing compares.
    """
    from pitwall.full_field_archive import cars_in_trace_scope

    drivers = [
        {"car_idx": 0, "team_id": 5, "position": 8, "grid_position": 10, "is_teammate": False},
        {"car_idx": 1, "team_id": 5, "position": 15, "grid_position": 18, "is_teammate": True},
        {"car_idx": 2, "team_id": 1, "position": 1, "grid_position": 1, "is_teammate": False},
        {"car_idx": 3, "team_id": 1, "position": 2, "grid_position": 2, "is_teammate": False},
        {"car_idx": 4, "team_id": 2, "position": 3, "grid_position": 3, "is_teammate": False},
        {"car_idx": 5, "team_id": 3, "position": 9, "grid_position": 12, "is_teammate": False},
        {"car_idx": 6, "team_id": 4, "position": 12, "grid_position": 9, "is_teammate": False},
        {"car_idx": 7, "team_id": 6, "position": 20, "grid_position": 20, "is_teammate": False},
    ]
    state = {
        "mode_profile": "race",
        "player_car_index": 0,
        "drivers": drivers,
    }
    scope = cars_in_trace_scope(state)
    assert 0 in scope, "the player"
    assert 1 in scope, "the teammate"
    assert {2, 3, 4} <= scope, "the podium"
    assert 5 in scope and 6 in scope, "cars that started within two places"
    assert 7 not in scope, "a car with no relationship to the race is dropped"


def test_practice_and_qualifying_keep_every_car():
    from pitwall.full_field_archive import cars_in_trace_scope

    for mode in ("practice", "qualifying", "time_trial"):
        assert cars_in_trace_scope({"mode_profile": mode, "drivers": []}) is None, mode


@pytest.mark.asyncio
async def test_an_out_of_scope_car_is_not_archived(tmp_path: Path) -> None:
    """The same opponent lap, with the scope excluding that car."""
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    trace_store = TraceStore(tmp_path / "traces")
    archive = FullFieldArchiveService(database.path, trace_store, queue_size=8)
    await archive.start()
    # Car 1 is the only opponent; keeping just the player excludes it.
    archive.set_trace_scope({0})
    assembler = SessionAssembler(batch_sink=archive.submit, field_trace_hz=20)

    def stamp(frame: int, time_s: float) -> EventStamp:
        return EventStamp(
            123, frame, frame, time_s, frame * 1_000_000, frame * 1_000_000
        )

    assembler.consume(
        SessionEvent(
            stamp(1, 0.1),
            track_id=4,
            layout_signature="f1:2026:4:5000",
            session_type=18,
            packet_format=2026,
            player_car_index=0,
        )
    )
    for index in (0, 1):
        assembler.consume(
            ParticipantEvent(
                stamp(2, 0.2),
                index,
                {"name": f"Driver {index}", "is_player": index == 0},
            )
        )
        for frame, distance in enumerate((0.0, 5.0, 10.0), 3):
            assembler.consume(
                SampleEvent(
                    stamp(frame, frame / 10),
                    index,
                    1,
                    "telemetry",
                    {
                        "lap_distance_m": distance,
                        "speed_mps": 50.0 + index,
                        "brake": 0.0,
                    },
                    units={"lap_distance_m": "m", "speed_mps": "m/s"},
                )
            )
        assembler.consume(LapEvent(stamp(8, 1.0), index, 1, 2, 60_000, True))

    await archive.stop()
    snapshot = archive.snapshot()
    assert snapshot.out_of_scope_batches_skipped == 1
    assert snapshot.persisted_laps == 0
    with sqlite3.connect(database.path) as db:
        assert db.execute("SELECT COUNT(*) FROM recorded_laps").fetchone()[0] == 0
