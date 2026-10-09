"""A retirement must not stop the recording of other cars.

The game lowers num_active_cars when a car retires but every other car keeps
its index. Bounding per-car loops by that count stopped recording the
highest-index cars: in a real 22-car race two finishers lost every trace and
all pit and flag context from lap 3 onwards.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from f1.packets import (
    PacketCarTelemetryData,
    PacketHeader,
    PacketLapData,
    PacketLapPositionsData,
    PacketParticipantsData,
    PacketSessionData,
)

from pitwall.database import PitWallDatabase
from pitwall.full_field_archive import FullFieldArchiveService
from pitwall.session_assembler import SessionAssembler
from pitwall.state import StateStore
from pitwall.trace_store import TraceStore
from pitwall.udp import F1DatagramProtocol


class _Transport:
    def get_extra_info(self, name, default=None):  # type: ignore[no-untyped-def]
        return default

    def close(self) -> None:
        pass


def _header(packet_id: int, frame: int, uid: int = 7_777) -> PacketHeader:
    header = PacketHeader()
    header.packet_format = 2026
    header.game_year = 26
    header.packet_version = 1
    header.packet_id = packet_id
    header.session_uid = uid
    header.player_car_index = 0
    header.frame_identifier = frame
    header.overall_frame_identifier = frame
    header.session_time = frame / 10
    return header


def _participants(frame: int, active: int) -> PacketParticipantsData:
    packet = PacketParticipantsData()
    packet.header = _header(4, frame)
    packet.num_active_cars = active
    for index, name in enumerate((b"PLAYER", b"RETIREE", b"LAST")):
        packet.participants[index].name = name
        packet.participants[index].your_telemetry = 1
        packet.participants[index].race_number = index + 1
    return packet


def _laps(frame: int, laps: dict[int, int], distance: float, last_ms: int = 0) -> PacketLapData:
    packet = PacketLapData()
    packet.header = _header(2, frame)
    for index, number in laps.items():
        packet.lap_data[index].current_lap_num = number
        packet.lap_data[index].lap_distance = distance
        packet.lap_data[index].last_lap_time_in_ms = last_ms
    return packet


def _telemetry(frame: int, cars: list[int]) -> PacketCarTelemetryData:
    packet = PacketCarTelemetryData()
    packet.header = _header(6, frame)
    for index in cars:
        packet.car_telemetry_data[index].speed = 200 + index
    return packet


@pytest.mark.asyncio
async def test_the_highest_index_car_is_still_recorded_after_another_car_retires(tmp_path: Path) -> None:
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    archive = FullFieldArchiveService(database.path, TraceStore(tmp_path / "traces"), queue_size=32)
    await archive.start()
    assembler = SessionAssembler(batch_sink=archive.submit, invalidation_sink=archive.submit)
    protocol = F1DatagramProtocol(StateStore(), session_assembler=assembler, capture_mode="full_fidelity")
    protocol.connection_made(_Transport())

    session = PacketSessionData()
    session.header = _header(1, 1)
    session.track_id = 13
    session.track_length = 5_807
    session.session_type = 10  # practice: every car's trace is in scope
    packets = [
        session,
        _participants(2, 3),
        _laps(3, {0: 1, 1: 1, 2: 1}, 0.0),
        _telemetry(4, [0, 1, 2]),
        _laps(5, {0: 1, 1: 1, 2: 1}, 30.0),
        _telemetry(6, [0, 1, 2]),
        _laps(7, {0: 2, 1: 2, 2: 2}, 0.0, last_ms=90_000),
        # Car 1 retires: the game now reports two active cars, but car 2
        # keeps index 2.
        _participants(8, 2),
        _telemetry(9, [0, 2]),
        _laps(10, {0: 2, 1: 2, 2: 2}, 30.0),
        _telemetry(11, [0, 2]),
        _laps(12, {0: 3, 1: 2, 2: 3}, 0.0, last_ms=91_000),
    ]
    for packet in packets:
        protocol.datagram_received(bytes(packet), ("192.168.1.61", 54_022))
    await protocol.drain_before_close()
    await archive.stop()

    with sqlite3.connect(database.path) as db:
        rows = db.execute(
            """
            SELECT c.car_index, l.lap_number, l.trace_manifest_id IS NOT NULL
            FROM recorded_laps l JOIN session_cars c ON c.id=l.session_car_id
            ORDER BY c.car_index, l.lap_number
            """
        ).fetchall()
    assert (2, 2, 1) in rows, rows


@pytest.mark.asyncio
async def test_the_lap_chart_keeps_cars_beyond_the_active_count_and_is_completed_at_the_flag() -> None:
    from f1.packets import PacketFinalClassificationData

    store = StateStore()
    await store.update(session_uid=7_777, active_cars=2)
    protocol = F1DatagramProtocol(store)

    def chart(frame: int, laps: list[list[int]]) -> PacketLapPositionsData:
        packet = PacketLapPositionsData()
        packet.header = _header(15, frame)
        packet.lap_start = 0
        packet.num_laps = len(laps)
        for offset, row in enumerate(laps):
            for car, position in enumerate(row):
                packet.position_for_vehicle_idx[offset * 24 + car] = position
        return packet

    await protocol.handle_PacketLapPositionsData(chart(10, [[1, 2, 3], [1, 0, 2]]))
    # The rest of the field crosses the line: same number of laps, not saved yet.
    await protocol.handle_PacketLapPositionsData(chart(11, [[1, 2, 3], [1, 0, 3]]))
    final = PacketFinalClassificationData()
    final.header = _header(8, 12)
    final.num_cars = 3
    for index, position in enumerate((1, 3, 2)):
        final.classification_data[index].position = position
        final.classification_data[index].result_status = 3 if index != 1 else 7
    await protocol.handle_PacketFinalClassificationData(final)

    saved = []
    while not store.event_queue.empty():
        event = store.event_queue.get_nowait()
        store.event_queue.task_done()
        if event["event_type"] == "LPOS":
            saved.append(event["payload"]["positions"])
    # The third car is kept although only two were reported active, and the
    # flag saves the completed last row.
    assert saved == [[[1, 2, 3], [1, 0, 2]], [[1, 2, 3], [1, 0, 3]]]
    # The in-memory position history keeps the third car too.
    assert store.state.drivers[2].position_history == [{"lap": 0, "position": 3}, {"lap": 1, "position": 3}]
