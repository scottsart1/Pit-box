"""Lap Lab follow-ups found by checking 4.13.0 against a real history.

Flag context judged the field and the player by different rules: a yellow
anywhere on track marked every rival's lap, while the player's laps were
never marked at all. The Library showed no quality and no circuit name for
any of 164 sessions, and a changed reference left the old comparison's
status on screen. Each is pinned here.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from f1.packets import (
    PacketCarStatusData,
    PacketHeader,
    PacketLapData,
    PacketParticipantsData,
    PacketSessionData,
)

from pitwall import database as database_module
from pitwall.catalog import track_name
from pitwall.database import PitWallDatabase
from pitwall.field_service import FieldAnalysisService
from pitwall.migrations import MIGRATIONS
from pitwall.session_assembler import LapEvent
from pitwall.state import StateStore
from pitwall.udp import F1DatagramProtocol

ROOT = Path(__file__).parents[1]
WORKSPACES = (ROOT / "static" / "js" / "workspaces.js").read_text(encoding="utf-8")

YELLOW = 3
RED = 4


def _header(packet_id: int) -> PacketHeader:
    value = PacketHeader()
    value.packet_format = 2026
    value.game_year = 26
    value.packet_version = 1
    value.packet_id = packet_id
    value.session_uid = 4242
    value.player_car_index = 0
    return value


def _participants(count: int) -> PacketParticipantsData:
    packet = PacketParticipantsData()
    packet.header = _header(4)
    packet.num_active_cars = count
    for index in range(count):
        packet.participants[index].your_telemetry = 1
        packet.participants[index].name = f"CAR{index}".encode()
    return packet


def _session(zone_flags: tuple[int, ...], *, safety_car: int = 0) -> PacketSessionData:
    packet = PacketSessionData()
    packet.header = _header(1)
    packet.track_id = 13
    packet.track_length = 5807
    packet.session_type = 15
    packet.safety_car_status = safety_car
    packet.num_marshal_zones = len(zone_flags)
    for index, flag in enumerate(zone_flags):
        packet.marshal_zones[index].zone_start = index / len(zone_flags)
        packet.marshal_zones[index].zone_flag = flag
    return packet


def _lap_data(lap_number: int, count: int) -> PacketLapData:
    packet = PacketLapData()
    packet.header = _header(2)
    for index in range(count):
        packet.lap_data[index].current_lap_num = lap_number
        packet.lap_data[index].last_lap_time_in_ms = 90_000
        packet.lap_data[index].lap_distance = 100.0
    return packet


def _car_status(flags: tuple[int, ...]) -> PacketCarStatusData:
    packet = PacketCarStatusData()
    packet.header = _header(7)
    for index, flag in enumerate(flags):
        packet.car_status_data[index].vehicle_fia_flags = flag
        packet.car_status_data[index].visual_tyre_compound = 17
    return packet


async def _lap_flags(*during_lap: object, cars: int = 3) -> dict[int, bool]:
    """Run one lap for every car and return each car's recorded flag context."""
    protocol = F1DatagramProtocol(StateStore())
    events: list[object] = []
    protocol._archive_event = events.append  # type: ignore[method-assign]
    for packet in (_participants(cars), _session((0, 0, 0, 0)), _lap_data(1, cars)):
        protocol._normalise_for_archive(packet, None)
    for packet in during_lap:
        protocol._normalise_for_archive(packet, None)
        # Lap data keeps flowing while the flag is out.
        protocol._normalise_for_archive(_lap_data(1, cars), None)
    protocol._normalise_for_archive(_session((0, 0, 0, 0)), None)
    protocol._normalise_for_archive(_lap_data(2, cars), None)
    return {
        event.car_index: bool(event.context["flag_context"])
        for event in events
        if isinstance(event, LapEvent)
    }


@pytest.mark.asyncio
async def test_a_yellow_elsewhere_does_not_flag_a_rivals_lap() -> None:
    """A fifth of the field's laps in a real history were flag context: any
    flagged zone, anywhere, marked every car's lap. In a recorded race most
    cars showed no flag of their own while a yellow was out elsewhere."""
    flags = await _lap_flags(
        _session((0, YELLOW, 1, 0)),
        _car_status((0, YELLOW, 0)),
    )
    assert flags == {0: False, 1: True, 2: False}


@pytest.mark.asyncio
async def test_the_green_after_a_yellow_is_not_flag_context() -> None:
    """The game shows green for a few seconds where a yellow has cleared."""
    flags = await _lap_flags(_session((0, 1, 0, 0)), _car_status((0, 0, 0)))
    assert flags == {0: False, 1: False, 2: False}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("zones", "safety_car"),
    [((0, 0, 0, 0), 1), ((0, 0, 0, 0), 2), ((RED, RED, RED, RED), 0)],
    ids=["safety car", "virtual safety car", "red flag"],
)
async def test_a_neutralisation_flags_every_cars_lap(
    zones: tuple[int, ...], safety_car: int
) -> None:
    flags = await _lap_flags(_session(zones, safety_car=safety_car), _car_status((0, 0, 0)))
    assert flags == {0: True, 1: True, 2: True}


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", [YELLOW, RED])
@pytest.mark.parametrize("first_lap", [0, 1])
async def test_a_cars_active_flag_is_carried_into_the_next_lap(
    flag: int, first_lap: int
) -> None:
    """Status packets can straddle the start line or precede the first lap.

    A flag remains active until the next status packet clears it, so the new
    lap must remember it even when no further yellow/red packet arrives.
    """
    protocol = F1DatagramProtocol(StateStore())
    events: list[object] = []
    protocol._archive_event = events.append  # type: ignore[method-assign]
    for packet in (
        _participants(3),
        _session((0, 0, 0, 0)),
        _lap_data(first_lap, 3),
        _car_status((0, flag, 0)),
        _lap_data(first_lap + 1, 3),
        _car_status((0, 0, 0)),
        _lap_data(first_lap + 2, 3),
        _lap_data(first_lap + 3, 3),
    ):
        protocol._normalise_for_archive(packet, None)

    rival_laps = {
        event.completed_lap_number: bool(event.context["flag_context"])
        for event in events
        if isinstance(event, LapEvent) and event.car_index == 1
    }
    assert rival_laps[first_lap + 1] is True
    assert rival_laps[first_lap + 2] is False
    if first_lap:
        assert rival_laps[first_lap] is True
    assert all(
        not event.context["flag_context"]
        for event in events
        if isinstance(event, LapEvent) and event.car_index != 1
    )


def _player_lap(lap_number: int, *, valid: bool = True) -> dict[str, object]:
    return {
        "session_uid": 7_777,
        "restart_epoch": 0,
        "timeline_epoch": 0,
        "player_car_index": 0,
        "packet_format": 2026,
        "track_id": 13,
        "track_name": "Suzuka",
        "track_length_m": 5807,
        "session_type": "Race",
        "mode_profile": "race",
        "lap_num": lap_number,
        "lap_time_ms": 92_000,
        "valid": valid,
        "compound": "MEDIUM",
        "tyre_age_end": lap_number,
        "weather": "Clear",
        "learning_exclusions": [],
        "trace": [
            {"t": d / 60.0, "d": float(d), "speed": 216, "throttle": 1.0, "brake": 0.0,
             "steer": 0.0, "gear": 7}
            for d in range(0, 5800, 50)
        ],
    }


@pytest.mark.asyncio
async def test_a_player_lap_under_a_yellow_is_catalogued_as_flag_context(
    tmp_path: Path,
) -> None:
    """The player's laps never carried flag context, so a lap slowed for a
    yellow was compared with a rival's clean lap as like for like."""
    store = StateStore()
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    await store.update(session_uid=7_777, track_id=13, track_name="Suzuka",
                       mode_profile="race", session_type="Race")
    await store.transition_lap(1, 0, False, 5, 0, 0)
    await store.add_trace_point({"t": 1.0, "d": 10.0, "speed": 200})
    clean = await store.transition_lap(2, 92_000, False, 5, 0, 0)
    await store.update(fia_flag="yellow")
    await store.update(fia_flag="none")
    await store.add_trace_point({"t": 93.0, "d": 10.0, "speed": 200})
    yellow = await store.transition_lap(3, 95_000, False, 5, 0, 0)
    assert clean["flag_context"] is False
    assert yellow["flag_context"] is True

    await database.upsert_session(clean)
    for lap in (clean, yellow):
        await database.save_lap(lap, [])
    with sqlite3.connect(database.path) as db:
        stored = dict(db.execute("SELECT lap_number, flag_context FROM recorded_laps"))
    assert stored == {1: 0, 2: 1}


@pytest.mark.asyncio
async def test_importing_a_legacy_lap_flags_neutralisation_not_invalidity(
    tmp_path: Path,
) -> None:
    """Imported laps were flag context whenever they were invalid - a
    track-limits lap is not a yellow - and never when neutralised."""
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    invalid = _player_lap(1, valid=False)
    neutralised = _player_lap(2) | {"learning_exclusions": ["neutralised_lap"]}
    await database.upsert_session(invalid)
    for lap in (invalid, neutralised):
        await database.save_lap(lap, [])
    with sqlite3.connect(database.path) as db:
        db.execute("DELETE FROM recorded_laps")
    await database.catalog.sync_legacy()
    with sqlite3.connect(database.path) as db:
        stored = dict(db.execute("SELECT lap_number, flag_context FROM recorded_laps"))
    assert stored == {1: 0, 2: 1}


@pytest.mark.asyncio
async def test_catalogued_player_laps_are_repaired_from_the_legacy_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a real history of 1,148 player laps, 35 neutralised laps read as
    clean and 13 invalid laps as flagged. The field's laps are left alone."""
    path = tmp_path / "pitwall.sqlite3"
    monkeypatch.setattr(
        database_module,
        "MIGRATIONS",
        tuple(item for item in MIGRATIONS if item.version != 4904),
    )
    database = PitWallDatabase(path)
    await database.initialize()
    clean = _player_lap(1)
    invalid = _player_lap(2, valid=False)
    neutralised = _player_lap(3) | {"learning_exclusions": ["neutralised_lap"]}
    await database.upsert_session(clean)
    for lap in (clean, invalid, neutralised):
        await database.save_lap(lap, [])
    with sqlite3.connect(path) as db:
        # As 4.13.0 left them: the live laps unflagged, the imported invalid
        # lap flagged, and a rival lap flagged by the old track-wide rule.
        db.execute("UPDATE recorded_laps SET flag_context=0")
        db.execute("UPDATE recorded_laps SET flag_context=1 WHERE lap_number=2")
        session_key = db.execute("SELECT id FROM recorded_sessions").fetchone()[0]
        db.execute(
            "INSERT INTO session_cars(id, session_id, car_index, is_player) VALUES ('rival', ?, 5, 0)",
            (session_key,),
        )
        db.execute(
            """
            INSERT INTO recorded_laps(id, session_car_id, lap_number, valid,
                                      flag_context, created_at)
            VALUES ('rival-lap', 'rival', 1, 1, 1, 't')
            """
        )

    monkeypatch.undo()
    await PitWallDatabase(path).initialize()

    with sqlite3.connect(path) as db:
        player = dict(
            db.execute(
                """
                SELECT l.lap_number, l.flag_context FROM recorded_laps l
                JOIN session_cars c ON c.id=l.session_car_id WHERE c.is_player=1
                """
            )
        )
        rival = db.execute(
            "SELECT flag_context FROM recorded_laps WHERE id='rival-lap'"
        ).fetchone()[0]
    assert player == {1: 0, 2: 0, 3: 1}
    assert rival == 1


@pytest.mark.asyncio
async def test_the_library_shows_quality_and_the_circuit_for_every_session(
    tmp_path: Path,
) -> None:
    """Every one of 164 real sessions read "Unavailable" for quality and
    "Race · Track 13" for its name in the Library, while Session Review
    showed the laps' mean quality for the same session."""
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    first, second = _player_lap(1), _player_lap(2, valid=False)
    await database.upsert_session(first)
    for lap in (first, second):
        await database.save_lap(lap, [])

    page = await database.catalog.list_sessions()
    [session] = page["items"]
    review = await database.catalog.get_quality(session["id"])
    field = await FieldAnalysisService(database.path).summary(session["id"])
    detail = await database.catalog.get_session(session["id"])

    assert session["quality_score"] == pytest.approx(review["quality_score"])
    assert field["session"]["quality_score"] == pytest.approx(review["quality_score"])
    assert review["quality_score"] is not None
    assert session["track_name"] == detail["track_name"] == "Suzuka"
    assert "session.track_name" in WORKSPACES[
        WORKSPACES.index("function sessionLabel(session)"):
        WORKSPACES.index("function lapLabel(lap)")
    ]


def test_an_unknown_track_has_no_name_rather_than_a_wrong_one() -> None:
    assert track_name(13) == "Suzuka"
    assert track_name(-1) is None
    assert track_name(None) is None


def test_changing_the_reference_clears_the_previous_comparisons_status() -> None:
    """After one comparison, choosing another reference blanked the delta but
    left "Comparison ready" above it - or, mid-comparison, "Aligning laps"
    for a result that would now be thrown away."""
    start = WORKSPACES.index('byId("referenceLapSelect")?.addEventListener("change"')
    handler = WORKSPACES[start:WORKSPACES.index('byId("createComparison")', start)]
    assert 'setNotice("lapLabStatus"' in handler
    assert "Reference changed" in handler
