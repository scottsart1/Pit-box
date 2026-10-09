"""The session record keeps every car's final classification and the lap chart.

Only the player's result used to be stored, and the game's lap-by-lap positions
lived in memory only, so Session Analysis could not show the game's own
finishing order for the field.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from f1.packets import PacketFinalClassificationData, PacketHeader, PacketLapPositionsData

from pitwall.database import PitWallDatabase
from pitwall.state import StateStore
from pitwall.udp import F1DatagramProtocol, final_classification_payload


def _header(packet_id: int, frame: int, uid: int = 4_242, player: int = 1) -> PacketHeader:
    header = PacketHeader()
    header.packet_format = 2026
    header.game_year = 26
    header.packet_version = 1
    header.packet_id = packet_id
    header.session_uid = uid
    header.player_car_index = player
    header.frame_identifier = frame
    header.overall_frame_identifier = frame
    header.session_time = frame / 10
    return header


def _classification(uid: int = 4_242) -> PacketFinalClassificationData:
    packet = PacketFinalClassificationData()
    packet.header = _header(8, 900, uid)
    packet.num_cars = 3
    rows = [
        # position, laps, status, reason, race time, penalties
        (2, 31, 3, 2, 3466.558, 0),
        (1, 31, 3, 2, 3466.302, 5),
        (3, 2, 4, 3, 215.914, 0),
    ]
    for index, (position, laps, status, reason, race_time, penalties) in enumerate(rows):
        item = packet.classification_data[index]
        item.position = position
        item.num_laps = laps
        item.grid_position = index + 1
        item.points = 10 - position
        item.num_pit_stops = 1
        item.result_status = status
        item.result_reason = reason
        item.best_lap_time_in_ms = 91_000 + index
        item.total_race_time = race_time
        item.penalties_time = penalties
        item.num_penalties = 1 if penalties else 0
        item.num_tyre_stints = 2
        item.tyre_stints_actual[0], item.tyre_stints_actual[1] = 16, 18
        item.tyre_stints_visual[0], item.tyre_stints_visual[1] = 16, 18
        item.tyre_stints_end_laps[0], item.tyre_stints_end_laps[1] = 12, 255
    return packet


def _lap_chart(laps: list[list[int]], frame: int, uid: int = 4_242) -> PacketLapPositionsData:
    packet = PacketLapPositionsData()
    packet.header = _header(15, frame, uid)
    packet.lap_start = 0
    packet.num_laps = len(laps)
    flat = packet.position_for_vehicle_idx
    for offset, row in enumerate(laps):
        for car, position in enumerate(row):
            flat[offset * 24 + car] = position
    return packet


def _queued(store: StateStore) -> list[dict]:
    events = []
    while not store.event_queue.empty():
        events.append(store.event_queue.get_nowait())
        store.event_queue.task_done()
    return events


def test_the_payload_lists_every_classified_car_with_status_time_and_penalties() -> None:
    payload = final_classification_payload(_classification(), 1)
    assert payload["player_car_index"] == 1
    assert [car["car_index"] for car in payload["cars"]] == [0, 1, 2]
    winner = payload["cars"][1]
    assert winner["position"] == 1 and winner["laps"] == 31 and winner["result_status"] == 3
    assert winner["total_race_time_s"] == pytest.approx(3466.302)
    assert winner["penalties_s"] == 5 and winner["penalties"] == 1
    retired = payload["cars"][2]
    assert (retired["position"], retired["laps"], retired["result_status"], retired["result_reason"]) == (3, 2, 4, 3)
    assert winner["tyre_stints"] == [
        {"actual": 16, "visual": 16, "end_lap": 12},
        {"actual": 18, "visual": 18, "end_lap": 255},
    ]


def test_a_packet_with_only_the_fields_older_code_sent_still_produces_a_payload() -> None:
    from types import SimpleNamespace

    result = SimpleNamespace(position=2, num_laps=44, grid_position=4, points=18, num_pit_stops=1,
                             best_lap_time_in_ms=91_000, total_race_time=5_000.0, penalties_time=0)
    packet = SimpleNamespace(header=SimpleNamespace(player_car_index=0), classification_data=[result])
    payload = final_classification_payload(packet, 0)
    assert payload["cars"][0]["position"] == 2
    assert payload["cars"][0]["result_status"] == 0
    assert payload["cars"][0]["tyre_stints"] == []


@pytest.mark.asyncio
async def test_the_classification_is_saved_once_and_kept_out_of_the_live_events_log() -> None:
    store = StateStore()
    await store.update(session_uid=4_242)
    protocol = F1DatagramProtocol(store)
    packet = _classification()
    await protocol.handle_PacketFinalClassificationData(packet)
    # The game repeats the packet while the results screen is up.
    await protocol.handle_PacketFinalClassificationData(packet)
    events = _queued(store)
    kinds = [event["event_type"] for event in events]
    assert kinds.count("FCLS") == 1 and kinds.count("CHQF") == 1
    saved = next(event for event in events if event["event_type"] == "FCLS")
    assert len(saved["payload"]["cars"]) == 3
    log = (await store.snapshot_analysis())["events_log"]
    assert [event["type"] for event in log] == ["CHQF"]


@pytest.mark.asyncio
async def test_the_lap_chart_is_saved_when_it_gains_a_lap_not_on_every_repeat() -> None:
    store = StateStore()
    await store.update(session_uid=4_242, active_cars=3)
    protocol = F1DatagramProtocol(store)
    grid = [1, 2, 3]
    await protocol.handle_PacketLapPositionsData(_lap_chart([grid], 10))
    await protocol.handle_PacketLapPositionsData(_lap_chart([grid], 11))
    await protocol.handle_PacketLapPositionsData(_lap_chart([grid, [2, 1, 0]], 20))
    await protocol.handle_PacketLapPositionsData(_lap_chart([grid, [2, 1, 3]], 21))
    await protocol.handle_PacketLapPositionsData(_lap_chart([grid, [2, 1, 3], [1, 2, 0]], 30))
    events = [event for event in _queued(store) if event["event_type"] == "LPOS"]
    assert [len(event["payload"]["positions"]) for event in events] == [1, 2, 3]
    assert events[-1]["payload"] == {"lap_start": 0, "positions": [[1, 2, 3], [2, 1, 3], [1, 2, 0]]}
    assert (await store.snapshot_analysis())["events_log"] == []


@pytest.mark.asyncio
async def test_a_flashback_that_rewinds_the_lap_chart_is_saved_again() -> None:
    store = StateStore()
    await store.update(session_uid=4_242, active_cars=2)
    protocol = F1DatagramProtocol(store)
    await protocol.handle_PacketLapPositionsData(_lap_chart([[1, 2], [1, 2], [2, 1]], 10))
    await protocol.handle_PacketLapPositionsData(_lap_chart([[1, 2], [1, 2]], 11))
    await protocol.handle_PacketLapPositionsData(_lap_chart([[1, 2], [1, 2], [1, 2]], 12))
    events = [event for event in _queued(store) if event["event_type"] == "LPOS"]
    assert [event["payload"]["positions"][-1] for event in events] == [[2, 1], [1, 2], [1, 2]]


@pytest.mark.asyncio
async def test_both_records_reach_the_session_events_table(tmp_path: Path) -> None:
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    store = StateStore()
    await store.update(session_uid=4_242, track_id=12, active_cars=3)
    protocol = F1DatagramProtocol(store)
    await protocol.handle_PacketLapPositionsData(_lap_chart([[1, 2, 3]], 10))
    await protocol.handle_PacketFinalClassificationData(_classification())
    for event in _queued(store):
        await database.save_queued_session_event(event)
    rows = await asyncio.to_thread(_saved_events, database.path)
    assert [kind for kind, _ in rows] == ["LPOS", "CHQF", "FCLS"]
    assert json.loads(rows[2][1])["cars"][1]["position"] == 1


def _saved_events(path: Path) -> list[tuple[str, str]]:
    import sqlite3

    with sqlite3.connect(path) as db:
        return db.execute("SELECT event_type, payload_json FROM session_events ORDER BY id").fetchall()
