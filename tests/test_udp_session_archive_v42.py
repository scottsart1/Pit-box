from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from f1.packets import (
    PacketCarTelemetryData,
    PacketFinalClassificationData,
    PacketHeader,
    PacketLapData,
    PacketParticipantsData,
    PacketSessionData,
    PacketSessionHistoryData,
)

from pitwall.database import PitWallDatabase
from pitwall.full_field_archive import FullFieldArchiveService
from pitwall.session_assembler import SessionAssembler
from pitwall.state import StateStore
from pitwall.trace_store import TraceStore
from pitwall.udp import F1DatagramProtocol


class _Transport(asyncio.DatagramTransport):
    def get_extra_info(self, name: str, default=None):  # type: ignore[no-untyped-def]
        return ("127.0.0.1", 20_777) if name == "sockname" else default


@pytest.mark.asyncio
@pytest.mark.parametrize("known_identity", [False, True])
async def test_history_packet_bytes_preserve_field_identity_without_player_rows(tmp_path, known_identity):
    database = PitWallDatabase(tmp_path / "history.sqlite3")
    await database.initialize()
    archive = FullFieldArchiveService(database.path, TraceStore(tmp_path / "traces"))
    archive.set_trace_scope({0})
    await archive.start()
    assembler = SessionAssembler(batch_sink=archive.submit, invalidation_sink=archive.submit)
    contexts = []

    def submit(context, rows):
        contexts.append(context)
        return archive.submit_history(context, rows)

    protocol = F1DatagramProtocol(StateStore(), session_assembler=assembler,
                                  on_player_lap_history=database.reconcile_player_lap_history,
                                  on_field_lap_history=submit)
    protocol.connection_made(_Transport())
    session = PacketSessionData()
    session.header = _header(1, 1, 0.1, uid=(1 << 63) + 147)
    session.track_id = 12
    session.track_length = 5000
    protocol.datagram_received(bytes(session), ("127.0.0.1", 50000))
    if known_identity:
        participants = PacketParticipantsData()
        participants.header = _header(4, 2, 0.2, uid=(1 << 63) + 147)
        participants.num_active_cars = 2
        participants.participants[0].name = b"PLAYER"
        participants.participants[1].name = b"RIVAL"
        participants.participants[1].driver_id = 77
        participants.participants[1].race_number = 9
        protocol.datagram_received(bytes(participants), ("127.0.0.1", 50000))
    history = PacketSessionHistoryData()
    history.header = _header(11, 3, 0.3, uid=(1 << 63) + 147)
    history.car_idx = 1
    history.num_laps = 3
    for index, row in enumerate(history.lap_history_data[:3]):
        row.lap_time_in_ms = 90000 + index * 1000
        row.sector1_time_ms_part = 30000 + index * 1000
        row.sector2_time_ms_part = row.sector3_time_ms_part = 30000
        row.lap_valid_bit_flags = 15 if index != 1 else 14
    protocol.datagram_received(bytes(history), ("127.0.0.1", 50000))
    await protocol.drain_before_close()
    await archive.stop()
    assert archive.snapshot().write_errors == 0
    assert not await database.recent_laps(12, 100)
    with sqlite3.connect(database.path) as db:
        rows = db.execute("SELECT l.lap_number,l.lap_time_ms,l.valid,c.car_index,c.is_player,"
                          "c.identity_revision FROM recorded_laps l JOIN session_cars c ON c.id=l.session_car_id "
                          "ORDER BY l.lap_number").fetchall()
    if known_identity:
        assert [row[:5] for row in rows] == [(1,90000,1,1,0),(2,91000,0,1,0),(3,92000,1,1,0)]
        assert len(contexts) == 1 and contexts[0]["history_complete"] is True
        assert contexts[0]["identity_revision"] == assembler.identity_registry.current(1).identity_revision
        assert contexts[0]["session_uid"] == (1 << 63) + 147
        assert archive.snapshot().history_laps_reconciled == 3
    else:
        assert not contexts and not rows


def _header(
    packet_id: int, frame: int, time_s: float, uid: int = 4_242
) -> PacketHeader:
    header = PacketHeader()
    header.packet_format = 2026
    header.game_year = 26
    header.packet_version = 1
    header.packet_id = packet_id
    header.session_uid = uid
    header.player_car_index = 0
    header.frame_identifier = frame
    header.overall_frame_identifier = frame
    header.session_time = time_s
    return header


@pytest.mark.asyncio
async def test_real_packet_bytes_reach_full_field_archive_and_drain(
    tmp_path: Path,
) -> None:
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    trace_store = TraceStore(tmp_path / "traces")
    archive = FullFieldArchiveService(database.path, trace_store, queue_size=16)
    await archive.start()
    assembler = SessionAssembler(
        batch_sink=archive.submit, invalidation_sink=archive.submit
    )
    store = StateStore()
    protocol = F1DatagramProtocol(
        store,
        session_assembler=assembler,
        capture_mode="full_fidelity",
    )
    protocol.connection_made(_Transport())

    session = PacketSessionData()
    session.header = _header(1, 1, 0.1)
    session.track_id = 13
    session.track_length = 5_807
    session.session_type = 15

    participants = PacketParticipantsData()
    participants.header = _header(4, 2, 0.2)
    participants.num_active_cars = 2
    for index, name in enumerate((b"PLAYER", b"RIVAL")):
        participants.participants[index].name = name
        participants.participants[index].your_telemetry = 1
        participants.participants[index].race_number = index + 1

    lap_start = PacketLapData()
    lap_start.header = _header(2, 3, 0.3)
    for index in range(2):
        lap_start.lap_data[index].current_lap_num = 1
        lap_start.lap_data[index].lap_distance = 0.0

    telemetry_start = PacketCarTelemetryData()
    telemetry_start.header = _header(6, 4, 0.4)
    telemetry_start.car_telemetry_data[0].speed = 210
    telemetry_start.car_telemetry_data[1].speed = 205

    lap_middle = PacketLapData()
    lap_middle.header = _header(2, 5, 0.5)
    for index in range(2):
        lap_middle.lap_data[index].current_lap_num = 1
        lap_middle.lap_data[index].lap_distance = 20.0

    telemetry_middle = PacketCarTelemetryData()
    telemetry_middle.header = _header(6, 6, 0.6)
    telemetry_middle.car_telemetry_data[0].speed = 215
    telemetry_middle.car_telemetry_data[1].speed = 211

    lap_finish = PacketLapData()
    lap_finish.header = _header(2, 7, 0.7)
    for index in range(2):
        lap_finish.lap_data[index].current_lap_num = 2
        lap_finish.lap_data[index].last_lap_time_in_ms = 90_000 + index * 500

    for packet in (
        session,
        participants,
        lap_start,
        telemetry_start,
        lap_middle,
        telemetry_middle,
        lap_finish,
    ):
        protocol.datagram_received(bytes(packet), ("192.168.1.61", 54_022))

    await protocol.drain_before_close()
    await archive.stop()

    assert protocol.packet_queue.empty()
    assert archive.snapshot().persisted_laps == 1
    with sqlite3.connect(database.path) as db:
        db.row_factory = sqlite3.Row
        rival = db.execute(
            """
            SELECT l.*, c.car_index, s.capture_mode
            FROM recorded_laps l
            JOIN session_cars c ON c.id=l.session_car_id
            JOIN recorded_sessions s ON s.id=c.session_id
            WHERE c.car_index=1
            """
        ).fetchone()
    assert rival is not None
    assert rival["valid"] == 1
    assert rival["trace_manifest_id"]
    assert rival["capture_mode"] == "full_fidelity"


@pytest.mark.asyncio
async def test_same_uid_restart_keeps_live_and_field_epochs_aligned() -> None:
    store = StateStore()
    assembler = SessionAssembler()
    protocol = F1DatagramProtocol(store, session_assembler=assembler)

    first = PacketSessionData()
    first.header = _header(1, 100, 10.0, uid=77)
    first.track_id = 10
    first.track_length = 7_004
    await protocol._handle(first)
    protocol._assembler_laps[1] = 9

    restarted = PacketSessionData()
    restarted.header = _header(1, 1, 0.1, uid=77)
    restarted.track_id = 10
    restarted.track_length = 7_004
    await protocol._handle(restarted)

    state = await store.snapshot_live()
    assert assembler.session is not None
    assert assembler.session.restart_epoch == 1
    assert state["restart_epoch"] == 1
    assert state["timeline_epoch"] == assembler.timeline_epoch
    assert protocol._assembler_laps == [0] * 24


@pytest.mark.asyncio
async def test_final_classification_seals_field_ingest_until_new_session() -> None:
    store = StateStore()
    assembler = SessionAssembler()
    protocol = F1DatagramProtocol(store, session_assembler=assembler)

    session = PacketSessionData()
    session.header = _header(1, 1, 0.1)
    await protocol._handle(session)
    participants = PacketParticipantsData()
    participants.header = _header(4, 2, 0.2)
    participants.num_active_cars = 2
    participants.participants[1].name = b"RIVAL"
    participants.participants[1].your_telemetry = 1
    await protocol._handle(participants)
    lap = PacketLapData()
    lap.header = _header(2, 3, 0.3)
    lap.lap_data[1].current_lap_num = 1
    await protocol._handle(lap)
    telemetry = PacketCarTelemetryData()
    telemetry.header = _header(6, 4, 0.4)
    telemetry.car_telemetry_data[1].speed = 200
    await protocol._handle(telemetry)

    final = PacketFinalClassificationData()
    final.header = _header(8, 5, 0.5)
    final.num_cars = 2
    final.classification_data[0].position = 1
    await protocol._handle(final)
    finalized_count = len(assembler.finalized_batches)
    assert assembler.quality_report().open_laps == 0

    trailing = PacketCarTelemetryData()
    trailing.header = _header(6, 6, 0.6)
    trailing.car_telemetry_data[1].speed = 201
    await protocol._handle(trailing)
    await protocol._handle(final)

    assert assembler.quality_report().open_laps == 0
    assert len(assembler.finalized_batches) == finalized_count
