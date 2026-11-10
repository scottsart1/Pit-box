"""The planner, the history read, the live frame and the recompute trigger.

None of these may change an answer; each did work it did not need to:

* the finish projection rebuilt per plan what is constant per compute, and
  counted the pit cycle twice for every plan;
* the tyre history was queried, decoded and refitted on every recompute,
  though the stored laps change only when one is saved;
* the live frame resent the unchanged strategy four times a second, with
  per-lap detail no screen draws;
* tyre wear to a tenth of a percent triggered a full recompute.

Wall time is not asserted; the structure that bounds it is.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from pitwall.database import PitWallDatabase
from pitwall.state import StateStore
from pitwall.strategy import StrategyEngine, infer_unrun_compounds

FIXTURE = Path(__file__).parent / "fixtures" / "strategy_projection_baseline.json"


async def _race_57(store):
    from tools.strategy_probe import build_field, race_basics

    def setup(state):
        race_basics(state, total_laps=57, current_lap=12, position=10)
        state.weather = "Clear"
        state.tyre.compound = "MEDIUM"
        state.tyre.age_laps = 11
        state.tyre.wear = [33.0] * 4
        state.tyre_sets = [{"compound": c, "available": True, "index": i}
                           for i, c in enumerate(("SOFT", "MEDIUM", "HARD", "HARD"))]
        state.drivers = build_field(current_lap=12, player_position=10, compound="MEDIUM", tyre_age=11)

    await store.mutate(setup)
    return await store.snapshot_analysis()


# --- PF-01: per-compute constants -----------------------------------------------------


@pytest.mark.asyncio
async def test_the_pit_cycle_is_counted_once_a_plan(stack, monkeypatch):
    store, database, strategy, *_ = stack
    state = await _race_57(store)
    history = infer_unrun_compounds(await database.tyre_history_model(3, context=state))
    counts = {"cycle": 0, "annotated": 0}
    cycle = StrategyEngine._pit_cycle_positions_recovered
    annotate = StrategyEngine._annotate_finish_projection

    def counted_cycle(*args, **kwargs):
        counts["cycle"] += 1
        return cycle(*args, **kwargs)

    def counted_annotate(self, *args, **kwargs):
        counts["annotated"] += 1
        return annotate(self, *args, **kwargs)

    monkeypatch.setattr(StrategyEngine, "_pit_cycle_positions_recovered", staticmethod(counted_cycle))
    monkeypatch.setattr(StrategyEngine, "_annotate_finish_projection", counted_annotate)
    strategy.compute(state, history)
    assert counts["annotated"] > 1000
    assert counts["cycle"] <= counts["annotated"]


@pytest.mark.asyncio
async def test_the_compound_rule_is_evaluated_once_per_distinct_schedule(stack, monkeypatch):
    store, database, strategy, *_ = stack
    state = await _race_57(store)
    history = infer_unrun_compounds(await database.tyre_history_model(3, context=state))
    calls = {"used": 0}
    used = StrategyEngine._used_compounds

    def counted(*args, **kwargs):
        calls["used"] += 1
        return used(*args, **kwargs)

    monkeypatch.setattr(StrategyEngine, "_used_compounds", staticmethod(counted))
    result = strategy.compute(state, history)
    assert len(result["plans"]) > 0
    assert calls["used"] <= 40


@pytest.mark.asyncio
async def test_the_projection_is_unchanged():
    """Ten seeded races: the same call and the same top five as before the work.

    Rewrite the fixture (``python -m tools.strategy_projection_snapshot
    --write``) only for an intended change to the model, and say which in the
    commit: Shanghai's tyre severity entry moved scenario 3's times, not its call;
    the race simulator's safety cars moved scenario 10's expected finish, and
    with it the better of two adjacent laps outside the points.
    """
    from tools.strategy_projection_snapshot import snapshot

    assert await snapshot() == json.loads(FIXTURE.read_text(encoding="utf-8"))


# --- PF-03: the tyre history ----------------------------------------------------------


def _insert_laps(path: Path, count: int, *, session: int = 1000, start: int = 0) -> None:
    connection = sqlite3.connect(path)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(laps)")}
    for number in range(start, start + count):
        row = {
            "session_uid": session + number // 30, "track_id": 3, "track_name": "X", "session_type": "Race",
            "lap_num": number % 30 + 1, "lap_time_ms": 90_000 + number, "valid": 1,
            "compound": ("SOFT", "MEDIUM", "HARD")[number % 3], "pit_status": 0,
            "tyre_age_start": number % 20, "tyre_age_end": number % 20 + 1,
            "wear_start_json": json.dumps([number % 20 * 3.0] * 4),
            "wear_end_json": json.dumps([(number % 20 + 1) * 3.0] * 4),
            "fuel_start_kg": 50 - number % 30, "fuel_end_kg": 49 - number % 30,
            "track_temp_c": 30, "air_temp_c": 22, "setup_json": "{}", "trace_json": "[]",
            "learning_exclusions_json": "[]", "created_at": number, "mode_profile": "race",
        }
        row = {key: value for key, value in row.items() if key in columns}
        connection.execute(
            f"INSERT INTO laps({','.join(row)}) VALUES({','.join('?' * len(row))})", list(row.values())
        )
    connection.commit()
    connection.close()


CONTEXT = {"session_uid": 1, "tyre": {"age_laps": 5}, "fuel_kg": 40.2, "track_temp_c": 30,
           "air_temp_c": 22, "mode_profile": "race"}


async def _database_with_laps(count=60) -> PitWallDatabase:
    database = PitWallDatabase(Path(tempfile.mkdtemp()) / "history.sqlite3")
    await database.initialize()
    _insert_laps(database.path, count)
    return database


@pytest.mark.asyncio
async def test_history_rows_are_decoded_and_fitted_once_between_lap_saves(monkeypatch):
    database = await _database_with_laps()
    fits = {"count": 0}
    fit = PitWallDatabase._tyre_history_fit

    def counted(self, *args, **kwargs):
        fits["count"] += 1
        return fit(self, *args, **kwargs)

    monkeypatch.setattr(PitWallDatabase, "_tyre_history_fit", counted)
    first = await database.tyre_history_model(3, context=CONTEXT)
    for fuel in (40.3, 39.8):
        assert await database.tyre_history_model(3, context={**CONTEXT, "fuel_kg": fuel}) == first
    assert fits["count"] == 1
    _insert_laps(database.path, 3, session=5000, start=500)
    later = await database.tyre_history_model(3, context=CONTEXT)
    assert fits["count"] == 2
    observed = sum(item.get("laps_observed", 0) for item in later["compounds"].values())
    assert observed > sum(item.get("laps_observed", 0) for item in first["compounds"].values())


@pytest.mark.asyncio
async def test_a_lap_invalidated_on_another_connection_reaches_the_model():
    """The session catalogue invalidates laps on its own connection."""
    database = await _database_with_laps()
    before = await database.tyre_history_model(3, context=CONTEXT)
    connection = sqlite3.connect(database.path)
    connection.execute("UPDATE laps SET valid=0 WHERE compound='SOFT'")
    connection.commit()
    connection.close()
    after = await database.tyre_history_model(3, context=CONTEXT)
    assert "SOFT" in before["compounds"] and "SOFT" not in after["compounds"]


@pytest.mark.asyncio
async def test_a_returned_model_cannot_change_the_cached_one():
    database = await _database_with_laps()
    first = await database.tyre_history_model(3, context=CONTEXT)
    first["compounds"].clear()
    assert (await database.tyre_history_model(3, context=CONTEXT))["compounds"]


# --- PF-02: the live frame ------------------------------------------------------------


async def _store_with_strategy():
    from tools.strategy_probe import build_field, new_engine, race_basics

    store, engine = await new_engine()

    def setup(state):
        race_basics(state, total_laps=57, current_lap=30, position=10)
        state.weather = "Clear"
        state.drivers = build_field(current_lap=30, player_position=10, compound="MEDIUM", tyre_age=11)

    await store.mutate(setup)
    await engine.recompute()
    return store, engine


@pytest.mark.asyncio
async def test_an_unchanged_strategy_is_not_resent():
    store, _ = await _store_with_strategy()
    full = json.loads(await store.snapshot_live_json())
    revision = full["strategy_revision"]
    assert full["strategy"]["recommended"]
    again = await store.snapshot_live_json(revision)
    assert json.loads(again)["strategy"] == {"revision": revision, "unchanged": True}
    assert len(again) < 40_000


@pytest.mark.asyncio
async def test_a_client_that_sends_no_revision_gets_everything():
    store, _ = await _store_with_strategy()
    revision = json.loads(await store.snapshot_live_json())["strategy_revision"]
    for held in (None, revision - 1, revision + 1):
        assert json.loads(await store.snapshot_live_json(held))["strategy"].get("recommended")


@pytest.mark.asyncio
async def test_a_recompute_is_sent_once():
    store, engine = await _store_with_strategy()
    revision = json.loads(await store.snapshot_live_json())["strategy_revision"]
    await store.mutate(lambda state: setattr(state, "current_lap", 31))
    await engine.recompute()
    moved = json.loads(await store.snapshot_live_json(revision))
    assert moved["strategy_revision"] == revision + 1
    assert moved["strategy"].get("recommended")
    assert json.loads(await store.snapshot_live_json(revision + 1))["strategy"]["unchanged"] is True


@pytest.mark.asyncio
async def test_the_live_frame_leaves_out_what_no_screen_draws():
    store, _ = await _store_with_strategy()
    strategy = json.loads(await store.snapshot_live_json())["strategy"]
    for rival in strategy["rival_finish_projections"]:
        assert "lap_times_s" not in rival and "pace_reference" not in rival
    for stint in strategy["recommended"].get("stint_models", []):
        assert "wheel_projection" not in stint and "lap_times_s" not in stint
        assert "usable_life_laps" in stint
    # The engine and the tools keep all of it.
    full = (await store.snapshot_analysis())["strategy"]
    assert all("lap_times_s" in rival for rival in full["rival_finish_projections"])


@pytest.mark.parametrize("text,expected", [
    (".", None), ('{"ack": 1, "strategy": 4}', 4), ('{"ack": 1, "strategy": null}', None),
    ('{"strategy": true}', None), ('{"strategy": "4"}', None), ("[4]", None),
])
def test_the_acknowledgement_names_the_strategy_held(text, expected):
    from pitwall.app import _held_strategy_revision

    assert _held_strategy_revision(text) == expected


# --- PF-04: when the planner runs -----------------------------------------------------


class _CountingStrategy:
    def __init__(self):
        self.recomputes = 0

    async def recompute(self):
        self.recomputes += 1
        return {}


async def _trigger():
    from pitwall.proactive import ProactiveEngineer

    class _Brain:
        database = None

    store = StateStore()
    strategy = _CountingStrategy()
    engineer = ProactiveEngineer(store, _Brain(), None, None, strategy)  # type: ignore[arg-type]
    return engineer, strategy


def _radio(wear=30.0, *, lap=10, compound="MEDIUM", stops=0):
    return {
        "current_lap": lap, "mode_profile": "race", "weather": "Clear",
        "tyre": {"compound": compound, "age_laps": 9, "wear": [wear] * 4},
        "drivers": [{"car_idx": 1, "pit_stops": stops, "tyre_compound": "MEDIUM"}],
    }


@pytest.mark.asyncio
async def test_a_tenth_of_a_percent_of_wear_does_not_recompute():
    engineer, strategy = await _trigger()
    await engineer._refresh_strategy_if_needed(_radio(30.0))
    await engineer._refresh_strategy_if_needed(_radio(30.1))
    assert strategy.recomputes == 1


@pytest.mark.asyncio
async def test_crossing_the_safety_limit_recomputes_at_once():
    engineer, strategy = await _trigger()
    await engineer._refresh_strategy_if_needed(_radio(84.6))
    await engineer._refresh_strategy_if_needed(_radio(85.0))
    assert strategy.recomputes == 2


@pytest.mark.asyncio
async def test_a_rival_stop_recomputes_inside_the_floor():
    engineer, strategy = await _trigger()
    await engineer._refresh_strategy_if_needed(_radio())
    await engineer._refresh_strategy_if_needed(_radio(stops=1))
    await engineer._refresh_strategy_if_needed(_radio(stops=1, lap=11))
    assert strategy.recomputes == 3


@pytest.mark.asyncio
async def test_continuous_changes_respect_the_floor(monkeypatch):
    from pitwall.proactive import time as proactive_time

    clock = {"now": 1000.0}
    monkeypatch.setattr(proactive_time, "monotonic", lambda: clock["now"])
    engineer, strategy = await _trigger()
    await engineer._refresh_strategy_if_needed(_radio(30.0))
    clock["now"] += 1.0
    await engineer._refresh_strategy_if_needed(_radio(31.0))
    clock["now"] += 1.0
    await engineer._refresh_strategy_if_needed(_radio(32.0))
    assert strategy.recomputes == 1
    # Past the floor, the change that was waiting is taken.
    clock["now"] += 3.0
    await engineer._refresh_strategy_if_needed(_radio(32.0))
    assert strategy.recomputes == 2
    assert engineer._recomputes_per_min() == 2
