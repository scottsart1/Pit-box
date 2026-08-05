from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pitwall.database import PitWallDatabase
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
    archive = FullFieldArchiveService(
        database.path, TraceStore(tmp_path / "traces"), queue_size=8
    )
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
