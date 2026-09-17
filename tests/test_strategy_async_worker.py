import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pitwall.state import StateStore
from pitwall.strategy import StrategyEngine


async def engine_fixture(monkeypatch):
    store = StateStore()
    await store.update(session_uid=101, track_id=11, current_lap=1, total_laps=25,
                       mode_profile="race")
    database = SimpleNamespace(tyre_history_model=AsyncMock(return_value={"compounds": {}}),
                               save_strategy_snapshot=AsyncMock())
    engine = StrategyEngine(store, database)
    started, release = threading.Event(), threading.Event()
    calls = []

    def compute(worker, state, history):
        calls.append((threading.get_ident(), StrategyEngine._live_context(state)))
        if len(calls) == 1:
            started.set()
            assert release.wait(3), "Test must release the worker"
        worker._candidate_pool = [{"uid": state["session_uid"]}]
        return {"available": False, "source_context": StrategyEngine._live_context(state)}

    monkeypatch.setattr(StrategyEngine, "compute", compute)
    return engine, store, database, started, release, calls


async def await_started(started):
    assert await asyncio.wait_for(asyncio.to_thread(started.wait, 2), timeout=2.5)


@pytest.mark.asyncio
async def test_live_requests_coalesce_and_do_not_block_loop(monkeypatch):
    engine, store, db, started, release, calls = await engine_fixture(monkeypatch)
    jobs = [asyncio.create_task(engine.recompute()) for _ in range(10)]
    try:
        await await_started(started)
        # These loop turns must happen while compute remains blocked in a thread.
        for _ in range(5):
            await asyncio.sleep(.01)
            assert not jobs[0].done()
        assert len(calls) == 1
        assert calls[0][0] != threading.get_ident()
    finally:
        release.set()
    results = await asyncio.gather(*jobs)
    assert all(result == results[0] for result in results)
    db.save_strategy_snapshot.assert_awaited_once()
    assert engine._candidate_pool == [{"uid": 101}]
    await engine.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"session_uid": 202}, {"restart_epoch": 1}, {"timeline_epoch": 1},
    {"current_lap": 2}, {"strategy_override": {"enabled": True, "next_box_lap": 4}},
    {"rain_now_pct": 70}, {"strategy_risk_appetite": "aggressive"},
])
async def test_stale_result_is_never_published_or_persisted(monkeypatch, change):
    engine, store, db, started, release, calls = await engine_fixture(monkeypatch)
    job = asyncio.create_task(engine.recompute())
    try:
        await await_started(started)
        await store.update(**change)
        expected = StrategyEngine._live_context(await store.snapshot_analysis())
    finally:
        release.set()
    result = await job
    assert len(calls) == 2
    assert result["source_context"] == expected
    assert (await store.peek("strategy"))["strategy"]["source_context"] == expected
    db.save_strategy_snapshot.assert_awaited_once()
    assert db.save_strategy_snapshot.await_args.args[1]["source_context"] == expected
    await engine.stop()


@pytest.mark.asyncio
async def test_changed_tyre_discards_old_worker_pool(monkeypatch):
    engine, store, db, started, release, calls = await engine_fixture(monkeypatch)
    job = asyncio.create_task(engine.recompute())
    try:
        await await_started(started)
        await store.mutate(lambda state: setattr(state.tyre, "compound", "HARD"))
    finally:
        release.set()
    result = await job
    assert len(calls) == 2
    assert result["source_context"][-2] == "HARD"
    db.save_strategy_snapshot.assert_awaited_once()
    await engine.stop()


@pytest.mark.asyncio
async def test_cancelling_one_waiter_keeps_shared_result(monkeypatch):
    engine, store, db, started, release, calls = await engine_fixture(monkeypatch)
    first = asyncio.create_task(engine.recompute())
    second = asyncio.create_task(engine.recompute())
    try:
        await await_started(started)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not second.done()
    finally:
        release.set()
    await second
    assert len(calls) == 1
    db.save_strategy_snapshot.assert_awaited_once()
    await engine.stop()


@pytest.mark.asyncio
async def test_stop_waits_for_worker_and_prevents_late_publication(monkeypatch):
    engine, store, db, started, release, calls = await engine_fixture(monkeypatch)
    before = (await store.peek("strategy"))["strategy"]
    job = asyncio.create_task(engine.recompute())
    try:
        await await_started(started)
        closing = asyncio.create_task(engine.stop())
        await asyncio.sleep(.03)
        assert not closing.done()
    finally:
        release.set()
    await closing
    assert await job == before
    assert (await store.peek("strategy"))["strategy"] == before
    db.save_strategy_snapshot.assert_not_awaited()
    assert engine._candidate_pool == []
    engine.start()
    await engine.recompute()
    assert len(calls) == 2
    await engine.stop()


@pytest.mark.asyncio
async def test_cancelled_hypothetical_worker_keeps_gate_until_finished(monkeypatch):
    engine, store, db, started, release, calls = await engine_fixture(monkeypatch)
    state = await store.snapshot_analysis()
    first = asyncio.create_task(engine._compute_isolated(state, {}))
    try:
        await await_started(started)
        first.cancel()
        second = asyncio.create_task(engine._compute_isolated(state, {}))
        await asyncio.sleep(.04)
        assert len(calls) == 1
        assert not first.done() and not second.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    await second
    assert len(calls) == 2
    assert engine._candidate_pool == []
    db.save_strategy_snapshot.assert_not_awaited()
    await engine.stop()


@pytest.mark.asyncio
async def test_worker_failure_does_not_poison_next_refresh(monkeypatch):
    store = StateStore()
    db = SimpleNamespace(tyre_history_model=AsyncMock(return_value={"compounds": {}}),
                         save_strategy_snapshot=AsyncMock())
    engine = StrategyEngine(store, db)

    def fail(*args):
        raise ValueError("synthetic worker failure")

    monkeypatch.setattr(StrategyEngine, "compute", fail)
    with pytest.raises(ValueError, match="synthetic worker failure"):
        await engine.recompute()
    monkeypatch.setattr(StrategyEngine, "compute", lambda *args: {"available": False})
    assert await engine.recompute() == {"available": False}
    db.save_strategy_snapshot.assert_awaited_once()
    await engine.stop()
