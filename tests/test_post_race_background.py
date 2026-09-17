"""Result durability must not depend on optional provider or speaker latency."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from pitwall import app as application
from pitwall.briefing import BriefingEngine
from pitwall.udp import F1DatagramProtocol


@pytest_asyncio.fixture
async def debrief_app(stack, monkeypatch):
    store, database, _, setup, analysis, tools = stack
    await store.update(session_uid=12345, restart_epoch=0, track_id=10,
                       track_name="Spa", session_type="Race", mode_profile="race")
    engine = BriefingEngine(store, database, analysis, setup, tools)
    narrator = AsyncMock(return_value="Race complete: P2.")
    speaker = AsyncMock()
    monkeypatch.setattr(application, "store", store)
    monkeypatch.setattr(application, "database", database)
    monkeypatch.setattr(application, "briefing", engine)
    monkeypatch.setattr(application, "brain", SimpleNamespace(narrate_briefing=narrator))
    monkeypatch.setattr(application, "voice", SimpleNamespace(speak_text=speaker))
    monkeypatch.setattr(application, "post_race_tasks", set())
    yield application, store, database, narrator, speaker
    await application._stop_post_race_debriefs()


def final_packet():
    return SimpleNamespace(header=SimpleNamespace(player_car_index=0, session_uid=12345),
        classification_data=[SimpleNamespace(position=2, num_laps=44, grid_position=4,
            points=18, num_pit_stops=1, best_lap_time_in_ms=91000,
            total_race_time=5000.0, penalties_time=0)])


@pytest.mark.asyncio
async def test_final_result_is_durable_while_slow_narration_does_not_block_packets(debrief_app):
    app, store, database, narrator, _ = debrief_app
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow(*args):
        entered.set()
        await release.wait()
        return "Finished."

    narrator.side_effect = slow
    protocol = F1DatagramProtocol(store, on_final_classification=app._persist_finished_session)
    await asyncio.wait_for(protocol.handle_PacketFinalClassificationData(final_packet()), 2)
    await asyncio.wait_for(entered.wait(), 2)
    assert (await database.history_query(limit=5))["sessions"][0]["result_position"] == 2
    assert (await database.history_query(limit=5))["sessions"][0]["ended_at"] is not None
    # A later packet can update live data before the provider is released.
    await asyncio.wait_for(protocol.handle_PacketTimeTrialData(SimpleNamespace(
        player_session_best_data_set=SimpleNamespace(lap_time_in_ms=0),
        personal_best_data_set=SimpleNamespace(lap_time_in_ms=0),
        rival_data_set=SimpleNamespace(lap_time_in_ms=0))), 1)
    await protocol.handle_PacketFinalClassificationData(final_packet())
    assert narrator.await_count == 1  # Repeated final packets do not queue speech.
    release.set()
    await asyncio.gather(*tuple(app.post_race_tasks))


@pytest.mark.asyncio
@pytest.mark.parametrize("next_uid,next_epoch", [(99999, 0), (12345, 1)])
async def test_late_debrief_stays_with_original_race_without_new_session_audio(
    debrief_app, next_uid, next_epoch,
):
    app, store, database, narrator, speaker = debrief_app
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow(*args):
        entered.set()
        await release.wait()
        return "Original race."

    narrator.side_effect = slow
    await store.update(final_classification={"position": 2})
    await app._persist_finished_session()
    await asyncio.wait_for(entered.wait(), 2)
    await store.update(session_uid=next_uid, restart_epoch=next_epoch,
                       track_id=3, briefings={"pre_session": {"text": "New session"}})
    release.set()
    await asyncio.gather(*tuple(app.post_race_tasks))
    speaker.assert_not_awaited()
    assert (await store.peek("briefings"))["briefings"] == {"pre_session": {"text": "New session"}}
    with database._connect() as db:
        row = db.execute("SELECT session_uid, track_id, text FROM briefings").fetchone()
    assert tuple(row) == (12345, 10, "Original race.")


@pytest.mark.asyncio
async def test_debrief_timeout_keeps_result_and_does_not_leak_tasks(debrief_app, monkeypatch):
    app, store, database, narrator, speaker = debrief_app
    monkeypatch.setattr(app, "POST_RACE_TIMEOUT_S", .02)
    narrator.side_effect = lambda *args: None

    async def slow(*args):
        await asyncio.Event().wait()

    narrator.side_effect = slow
    await store.update(final_classification={"position": 2})
    await app._persist_finished_session()
    await asyncio.wait_for(asyncio.gather(*tuple(app.post_race_tasks)), 1)
    assert not app.post_race_tasks
    speaker.assert_not_awaited()
    assert (await database.history_query(limit=5))["sessions"][0]["result_position"] == 2


@pytest.mark.asyncio
async def test_shutdown_cancels_pending_optional_speech(debrief_app):
    app, store, _, _, speaker = debrief_app
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def slow(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    speaker.side_effect = slow
    await store.update(final_classification={"position": 2})
    await app._persist_finished_session()
    await asyncio.wait_for(entered.wait(), 2)
    await asyncio.wait_for(app._stop_post_race_debriefs(), 1)
    assert cancelled.is_set()
    assert not app.post_race_tasks


@pytest.mark.asyncio
async def test_debrief_facts_use_captured_finish_not_current_session(debrief_app, monkeypatch):
    app, store, _, _, _ = debrief_app
    await store.update(final_classification={"position": 2, "grid_position": 4, "points": 18})
    origin = await store.snapshot_analysis()
    await store.update(session_uid=99999, final_classification={"position": 20})
    monkeypatch.setattr(store, "snapshot_analysis", AsyncMock(side_effect=AssertionError("live state read")))
    payload = await app.briefing.post_race(state=origin)
    assert payload["finish_position"] == 2
    assert payload["net_places"] == 2
    assert payload["points"] == 18
