import types

import pytest

from pitwall.proactive import ProactiveEngineer


class _Brain:
    def __init__(self, database):
        self.database = database

    async def proactive(self, event):
        return f"Update lap {event['payload'].get('lap')}"


class _Voice:
    is_busy = False

    async def speak_text(self, text):
        return True


class _Setup:
    def __init__(self):
        self.learned = 0

    async def learn_current_session(self):
        self.learned += 1
        return True


class _Strategy:
    async def recompute(self):
        return {}


@pytest.mark.asyncio
async def test_two_lap_cadence_does_not_depend_on_exact_modulo(stack):
    store, database, _, _, _, _ = stack
    setup = _Setup()
    engineer = ProactiveEngineer(
        store,
        _Brain(database),  # type: ignore[arg-type]
        _Voice(),  # type: ignore[arg-type]
        setup,  # type: ignore[arg-type]
        _Strategy(),  # type: ignore[arg-type]
    )

    async def no_refresh(self, state):
        return state

    engineer._refresh_strategy_if_needed = types.MethodType(no_refresh, engineer)
    await store.update(
        connected=True,
        session_uid=42,
        game_paused=False,
        mode_profile="race",
        current_lap=3,
        total_laps=35,
    )
    await store.mutate(
        lambda state: (
            state.proactive.update({"enabled": True, "cadence_laps": 2}),
            state.analysis.update(
                {
                    "last_lap_analyzed": 2,
                    "progress": {"position": 8},
                    "target": {"target": "1:24.0"},
                }
            ),
        )
    )
    await engineer._reset_for_session(42)
    await engineer._detect(await store.snapshot_analysis())
    first_progress = [item for item in engineer.pending if item["type"] == "progress_update"]
    assert len(first_progress) == 1
    assert first_progress[0]["payload"]["lap"] == 2

    # If the worker does not run at exactly lap 4, lap 5 still emits the overdue
    # two-lap update rather than silently losing it.
    await store.mutate(lambda state: state.analysis.update({"last_lap_analyzed": 5}))
    await engineer._detect(await store.snapshot_analysis())
    progress = [item for item in engineer.pending if item["type"] == "progress_update"]
    # Progress events coalesce, so the newest overdue update replaces the old one.
    assert len(progress) == 1
    assert progress[0]["payload"]["lap"] == 5
    live = await store.snapshot_live()
    assert live["proactive"]["next_due_lap"] == 7
    assert setup.learned >= 2

@pytest.mark.asyncio
async def test_manual_proactive_test_queues_live_progress_call(stack):
    store, database, _, _, _, _ = stack
    engineer = ProactiveEngineer(
        store,
        _Brain(database),  # type: ignore[arg-type]
        _Voice(),  # type: ignore[arg-type]
        _Setup(),  # type: ignore[arg-type]
        _Strategy(),  # type: ignore[arg-type]
    )

    async def no_refresh(self, state):
        return state

    engineer._refresh_strategy_if_needed = types.MethodType(no_refresh, engineer)
    await store.update(
        connected=True,
        session_uid=99,
        game_paused=False,
        current_lap=6,
        total_laps=35,
        mode_profile="race",
    )
    await store.mutate(
        lambda state: state.analysis.update(
            {
                "last_lap_analyzed": 5,
                "target": {"target": "1:24.500"},
                "progress": {"position": 7},
            }
        )
    )

    result = await engineer.queue_test_update()

    assert result["ok"] is True
    assert len(engineer.pending) == 1
    assert engineer.pending[0]["critical"] is True
    assert engineer.pending[0]["payload"]["manual_test"] is True
    assert engineer.pending[0]["payload"]["lap"] == 5


@pytest.mark.asyncio
async def test_qualifying_progress_payload_uses_best_laps_not_gaps(stack):
    from pitwall.state import DriverState

    store, database, _, _, _, _ = stack
    engineer = ProactiveEngineer(
        store,
        _Brain(database),  # type: ignore[arg-type]
        _Voice(),  # type: ignore[arg-type]
        _Setup(),  # type: ignore[arg-type]
        _Strategy(),  # type: ignore[arg-type]
    )

    async def no_refresh(self, state):
        return state

    engineer._refresh_strategy_if_needed = types.MethodType(no_refresh, engineer)

    def setup(state):
        state.connected = True
        state.session_uid = 123
        state.game_paused = False
        state.mode_profile = "qualifying"
        state.current_lap = 4
        state.player_car_index = 2
        state.proactive.update({"enabled": True, "cadence_laps": 2})
        state.analysis.update(
            {
                "last_lap_analyzed": 2,
                "target": {
                    "target_ms": 88400,
                    "target": "1:28.400",
                    "theoretical": "1:28.400",
                },
            }
        )
        state.drivers[0] = DriverState(
            0,
            "Norris",
            active=True,
            position=1,
            best_lap_ms=88500,
            gap_to_player_s=-7.2,
        )
        state.drivers[2] = DriverState(
            2,
            "Sarthak",
            active=True,
            position=8,
            best_lap_ms=89700,
        )

    await store.mutate(setup)
    await engineer._reset_for_session(123)
    await engineer._detect(await store.snapshot_analysis())

    event = next(item for item in engineer.pending if item["type"] == "progress_update")
    assert event["payload"]["mode_profile"] == "qualifying"
    assert event["payload"]["gaps"] == {}
    assert event["payload"]["strategy"] == {}
    assert event["payload"]["qualifying"]["session_best"] == "1:28.500"
    assert event["payload"]["qualifying"]["target"] == "1:28.400"
