"""Raw recordings must survive sessions shorter than the periodic DB writer."""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing

import pytest
from f1.packets import PacketHeader, PacketSessionData

from pitwall.capture import CaptureReader, CaptureWriter, scan_capture
from pitwall.capture_lifecycle import SessionCaptureCoordinator
from pitwall.capture_service import CaptureService
from pitwall.catalog import session_id
from pitwall.database import PitWallDatabase
from pitwall.session_assembler import EventStamp, SessionAssembler, SessionEvent
from pitwall.state import StateStore
from pitwall.udp import F1DatagramProtocol


def context(uid=123, epoch=0, track=10):
    return {
        "game_session_uid": str(uid),
        "restart_epoch": epoch,
        "track_id": track,
        "layout_signature": f"f1:2026:{track}:7004",
        "raw_session_type_id": 1,
        "packet_format": 2026,
        "capture_mode": "balanced",
    }


def rows(database, table):
    assert table in {
        "recorded_sessions",
        "raw_captures",
        "recorded_laps",
        "session_cars",
    }
    with closing(sqlite3.connect(database.path)) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]


def capture_report(tmp_path, key, origin):
    path = tmp_path / "capture.pwcap"
    with CaptureWriter(
        path, metadata={"session_id": key, "session_context": origin}
    ) as writer:
        writer.write(b"session packet", ("127.0.0.1", 20777))
    return scan_capture(path)


@pytest.mark.asyncio
async def test_single_real_session_packet_drains_and_registers_on_immediate_shutdown(
    tmp_path,
):
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    capture_root = tmp_path / "captures"
    service = CaptureService(capture_root)
    coordinator = SessionCaptureCoordinator(service, database.catalog, capture_root)
    protocol = F1DatagramProtocol(
        StateStore(),
        capture_service=service,
        session_assembler=SessionAssembler(),
        on_session_key_change=coordinator.observe_session,
    )

    class Transport(asyncio.DatagramTransport):
        def get_extra_info(self, name, default=None):
            return ("127.0.0.1", 20777) if name == "sockname" else default

    packet = PacketSessionData()
    packet.header = PacketHeader()
    packet.header.packet_format = 2026
    packet.header.game_year = 26
    packet.header.packet_id = 1
    packet.header.packet_version = 1
    packet.header.session_uid = 123
    packet.header.player_car_index = 0
    packet.track_id = 11
    packet.track_length = 5793
    packet.session_type = 1
    await coordinator.start()
    protocol.connection_made(Transport())
    try:
        protocol.datagram_received(bytes(packet), ("127.0.0.1", 20777))
    finally:
        # Same ordering as application shutdown, without starting a watchdog.
        await protocol.drain_before_close()
        await coordinator.stop()
    sessions = rows(database, "recorded_sessions")
    assert len(sessions) == 1
    assert sessions[0]["id"] == session_id(123)
    assert sessions[0]["track_id"] == 11
    assert sessions[0]["session_type"] == "Practice 1"
    assert sessions[0]["status"] == "incomplete"
    captures = rows(database, "raw_captures")
    assert any(row["session_id"] == session_id(123) for row in captures)
    assert all(row["clean_close"] == 1 for row in captures)
    assert sum(row["packet_count"] for row in captures) == 1
    frames = [
        frame.data
        for row in captures
        for frame in CaptureReader(capture_root / row["relative_path"])
    ]
    assert frames == [bytes(packet)]
    assert coordinator.snapshot().last_error is None
    assert service.snapshot().write_errors == 0


@pytest.mark.asyncio
async def test_rapid_sessions_register_exact_parents_without_laps_or_periodic_writes(
    tmp_path,
):
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    capture_root = tmp_path / "captures"
    service = CaptureService(capture_root)
    coordinator = SessionCaptureCoordinator(service, database.catalog, capture_root)
    store = StateStore()
    # Archive normalization precedes live-state handling: deliberately stale.
    await store.update(track_id=22, session_type="Race", mode_profile="race")
    assembler = SessionAssembler()
    protocol = F1DatagramProtocol(
        store,
        session_assembler=assembler,
        on_session_key_change=coordinator.observe_session,
    )
    await coordinator.start()
    expected = []
    try:
        assert service.submit(b"startup", ("127.0.0.1", 20777))
        for uid, track, length in [(123, 10, 7004), (456, 11, 5793), (123, 0, 5278)]:
            protocol._archive_event(
                SessionEvent(
                    EventStamp(uid, 1, 1, 0.1, 1, 1),
                    track_id=track,
                    layout_signature=f"f1:2026:{track}:{length}",
                    session_type=1,
                    packet_format=2026,
                )
            )
            key = assembler.session.id
            expected.append((key, uid, assembler.session.restart_epoch, track, length))
            assert service.submit(key.encode(), ("127.0.0.1", 20777))
        # Do not yield to a watchdog, save a lap or wait for a rotation here.
        assert rows(database, "recorded_sessions") == []
    finally:
        await coordinator.stop()

    assert [item[2] for item in expected] == [0, 0, 1]
    sessions = {row["id"]: row for row in rows(database, "recorded_sessions")}
    assert set(sessions) == {item[0] for item in expected}
    captures = rows(database, "raw_captures")
    assert len(captures) == 4
    assert all(row["clean_close"] == 1 for row in captures)
    for key, uid, epoch, track, length in expected:
        session = sessions[key]
        assert session["game_session_uid"] == str(uid)
        assert session["restart_epoch"] == epoch
        assert session["track_id"] == track
        assert session["track_layout_signature"] == f"f1:2026:{track}:{length}"
        assert session["session_type"] == "Practice 1"
        assert session["status"] == "incomplete"
        assert session["ended_at"] is None
        capture = next(item for item in captures if item["session_id"] == key)
        assert [
            frame.data
            for frame in CaptureReader(capture_root / capture["relative_path"])
        ] == [key.encode()]
    assert rows(database, "recorded_laps") == rows(database, "session_cars") == []
    assert coordinator.snapshot().last_error is None
    assert service.snapshot().queue_drops == service.snapshot().write_errors == 0


@pytest.mark.asyncio
async def test_capture_seed_never_overwrites_complete_or_richer_session(tmp_path):
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    await database.upsert_session(
        {
            "session_uid": 123,
            "track_id": 0,
            "track_length_m": 5278,
            "packet_format": 2026,
            "session_type": "Sprint",
            "mode_profile": "race",
            "capture_mode": "full_fidelity",
            "final_classification": {"position": 1},
        }
    )
    before = rows(database, "recorded_sessions")
    key = session_id(123)
    report = capture_report(tmp_path, key, context())
    await database.catalog.register_raw_capture(key, "capture.pwcap", report)
    assert rows(database, "recorded_sessions") == before
    assert rows(database, "raw_captures")[0]["session_id"] == key


@pytest.mark.asyncio
async def test_recovery_uses_frozen_identity_without_live_state(tmp_path):
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    uid = (1 << 64) - 7
    key = session_id(uid, 2)
    report = capture_report(tmp_path, key, context(uid, 2, 0))
    # Startup recovery has no active live identity and passes None.
    await database.catalog.register_raw_capture(None, "capture.pwcap", report)
    await database.catalog.register_raw_capture(None, "capture.pwcap", report)
    session = rows(database, "recorded_sessions")[0]
    assert session["id"] == key
    assert session["legacy_session_uid"] == -7
    assert session["track_id"] == 0
    assert session["status"] == "incomplete"
    assert len(rows(database, "raw_captures")) == 1


@pytest.mark.asyncio
async def test_missing_boundary_metadata_is_unknown_not_current_track(tmp_path):
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    key = session_id(123)
    report = capture_report(
        tmp_path, key, {"game_session_uid": "123", "restart_epoch": 0}
    )
    await database.catalog.register_raw_capture(key, "capture.pwcap", report)
    session = rows(database, "recorded_sessions")[0]
    assert session["track_id"] is session["track_layout_signature"] is None
    assert session["packet_format"] is session["raw_session_type_id"] is None
    assert session["session_type"] == "Unknown"
    assert session["mode_profile"] == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["header", "argument", "uid", "epoch"])
async def test_mismatched_capture_identity_cannot_create_or_attach_parent(
    tmp_path, mismatch
):
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    key, header_key, origin = session_id(123), session_id(123), context()
    if mismatch == "header":
        header_key = session_id(456)
    elif mismatch == "argument":
        key = session_id(456)
    elif mismatch == "uid":
        origin["game_session_uid"] = "0"
    else:
        origin["restart_epoch"] = -1
    report = capture_report(tmp_path, header_key, origin)
    with pytest.raises(ValueError, match="capture session identity"):
        await database.catalog.register_raw_capture(key, "capture.pwcap", report)
    assert rows(database, "recorded_sessions") == rows(database, "raw_captures") == []


@pytest.mark.asyncio
async def test_parent_and_capture_registration_roll_back_together(tmp_path):
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    key = session_id(123)
    report = capture_report(tmp_path, key, context())
    # The second statement fails after the parent insert, exercising the
    # transaction rather than merely rejecting malformed context beforehand.
    with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
        await database.catalog.register_raw_capture(
            key, "capture.pwcap", report, privacy_mode=None
        )
    assert rows(database, "recorded_sessions") == rows(database, "raw_captures") == []


@pytest.mark.asyncio
async def test_deferred_rotation_keeps_its_own_frozen_context(tmp_path, monkeypatch):
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    capture_root = tmp_path / "captures"
    service = CaptureService(capture_root)
    coordinator = SessionCaptureCoordinator(
        service, database.catalog, capture_root, queue_size=1
    )
    entered, release = asyncio.Event(), asyncio.Event()
    original = database.catalog.register_raw_capture

    async def paused(*args, **kwargs):
        if not entered.is_set():
            entered.set()
            await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(database.catalog, "register_raw_capture", paused)
    await coordinator.start()
    try:
        coordinator.observe_session(session_id(1), context(1, track=10))
        await asyncio.wait_for(entered.wait(), 5)
        coordinator.observe_session(session_id(2), context(2, track=11))
        mutable_origin = context(3, track=0)
        coordinator.observe_session(session_id(3), mutable_origin)
        mutable_origin["track_id"] = 22
    finally:
        release.set()
        await coordinator.stop()
    sessions = {row["id"]: row for row in rows(database, "recorded_sessions")}
    assert sessions[session_id(1)]["track_id"] == 10
    assert sessions[session_id(2)]["track_id"] == 11
    assert sessions[session_id(3)]["track_id"] == 0
    assert coordinator.snapshot().rotation_drops == 1
    assert coordinator.snapshot().last_error is None
