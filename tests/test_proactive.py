import types

import pytest

from pitwall.proactive import ProactiveEngineer


class _Brain:
    def __init__(self, database):
        self.database = database

    async def proactive(self, event):
        return f"Update lap {event['payload'].get('lap')}"

    async def record_spoken_call(self, text):
        return None


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

@pytest.mark.asyncio
async def test_rival_pitting_from_behind_queues_undercut_threat(stack):
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
        state.session_uid = 444
        state.game_paused = False
        state.mode_profile = "race"
        state.current_lap = 12
        state.total_laps = 35
        state.player_car_index = 0
        state.proactive["enabled"] = True
        state.drivers[0] = DriverState(
            0, "Sarthak", active=True, position=6, pit_stops=0, gap_to_player_s=0.0
        )
        state.drivers[1] = DriverState(
            1, "Lawson", active=True, position=7, pit_stops=0, gap_to_player_s=8.4
        )

    await store.mutate(setup)
    await engineer._reset_for_session(444)
    await engineer._detect(await store.snapshot_analysis())

    await store.mutate(lambda state: setattr(state.drivers[1], "pit_stops", 1))
    await engineer._detect(await store.snapshot_analysis())

    threats = [item for item in engineer.pending if item["type"] == "undercut_threat"]
    assert len(threats) == 1
    assert threats[0]["payload"]["driver"] == "Lawson"
    assert threats[0]["payload"]["gap_to_player_s"] == pytest.approx(8.4)


@pytest.mark.asyncio
async def test_qualifying_invalid_lap_queues_deleted_lap_call(stack):
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
        session_uid=555,
        game_paused=False,
        mode_profile="qualifying",
        current_lap=3,
        current_lap_invalid=False,
    )
    await engineer._reset_for_session(555)
    await engineer._detect(await store.snapshot_analysis())

    await store.update(current_lap_invalid=True, sector=2)
    await engineer._detect(await store.snapshot_analysis())

    deleted = [item for item in engineer.pending if item["type"] == "lap_deleted"]
    assert len(deleted) == 1
    assert deleted[0]["payload"]["lap"] == 3


def test_progress_fallback_never_speaks_a_raw_finding_dict():
    """Verbatim from the 2026-08-10 Las Vegas session.

    top_opportunity is sometimes the full finding dict, and the fallback read
    "Lap 8: target 1:33.425. {'start_m': 5331.0, ...}" over the radio. The
    dashboard already guarded for this shape; speech must too.
    """
    finding = {
        "start_m": 5331.0, "end_m": 5354.5, "center_m": 5342.8,
        "average_deviation_m": 1.45, "side": "right",
        "instruction": "Around 5343 m, tighten the line on the right.",
    }
    event = {
        "type": "progress_update",
        "payload": {
            "lap": 8,
            "target": {"target": "1:33.425"},
            "racing_line": {"top_opportunity": finding},
            "strategy": {},
        },
    }
    text = ProactiveEngineer._fallback_text(event, {})
    assert "{" not in text and "start_m" not in text
    assert "tighten the line" in text

    # A dict with no instruction degrades to the generic line, never the repr.
    event["payload"]["racing_line"]["top_opportunity"] = {"start_m": 1.0}
    text = ProactiveEngineer._fallback_text(event, {})
    assert "{" not in text
    assert "keep the lap clean" in text


def test_near_identical_consecutive_calls_are_swallowed(stack):
    """Three rewordings of "Practice complete, P8" went out in 30 seconds.

    Different event types converged on the same content, which the per-type
    cooldown cannot see. The spoken text itself is the last line of defence.
    """
    import time as _time

    store, database, _, _, _, _ = stack
    engineer = ProactiveEngineer(store, _Brain(database), _Voice(), _Setup(), _Strategy())
    engineer._last_spoken_text = (
        "Practice complete, P8; the qualifying reference is set. "
        "Engine damage is at 23%, non-critical."
    )
    engineer._last_spoken_at = _time.monotonic()
    assert engineer._is_near_repeat(
        "Practice complete, P8, and the qualifying reference is set. "
        "No repeatable corner loss identified."
    ) is True
    assert engineer._is_near_repeat(
        "Box this lap for mediums; rear wear is the limit."
    ) is False
    # Outside the horizon the same words are a fresh update, not a repeat.
    engineer._last_spoken_at = _time.monotonic() - 300
    assert engineer._is_near_repeat(
        "Practice complete, P8; the qualifying reference is set."
    ) is False


def test_driver_check_rearms_when_the_window_moves(stack):
    """Asked at lap 2 about a lap-5 window; the real stop became lap 12.

    One check per stint per WINDOW: the same window never re-asks, a window
    that moved four laps or more is a new conversation.
    """
    store, database, _, _, _, _ = stack
    engineer = ProactiveEngineer(store, _Brain(database), _Voice(), _Setup(), _Strategy())

    def state_for(box_lap, current_lap):
        return {
            "mode_profile": "race",
            "race_control_phase": "green",
            "ptt_pressed": False,
            "current_lap": current_lap,
            "strategy": {"recommended": {"box_lap": box_lap}, "plans": []},
            "tyre": {"compound": "HARD", "age_laps": current_lap - 1, "wear": [10.0]},
            "analysis": {"deg_model": {}},
        }

    engineer._detect_driver_check(state_for(box_lap=5, current_lap=2))
    assert len(engineer.pending) == 1, "first window asks"
    engineer.pending.clear()

    engineer._detect_driver_check(state_for(box_lap=6, current_lap=3))
    assert len(engineer.pending) == 0, "a one-lap slide is the same conversation"

    engineer._detect_driver_check(state_for(box_lap=12, current_lap=10))
    assert len(engineer.pending) == 1, "a moved window re-asks before the real stop"


@pytest.mark.asyncio
async def test_a_spoken_question_opens_the_reply_window(stack):
    """When the engineer asks, the driver must not need the wake phrase.

    Reported from Las Vegas: "Tyre state: holding or going away?" was asked,
    and the driver then had to say "Mark" to be heard. After any delivered
    call that ends with a question mark, the reply window opens.
    """

    class _QuestionBrain(_Brain):
        async def proactive(self, event):
            return "Tyre state: holding or going away?"

    class _ListeningVoice(_Voice):
        def __init__(self):
            self.reply_windows = []

        async def open_reply_window(self, reason="engineer asked a question"):
            self.reply_windows.append(reason)

    store, database, _, _, _, _ = stack
    voice = _ListeningVoice()
    engineer = ProactiveEngineer(store, _QuestionBrain(database), voice, _Setup(), _Strategy())

    def apply(state):
        state.connected = True
        state.speed_kph = 40  # slow enough to be a safe speaking window
        state.current_lap = 3
        state.mode_profile = "race"
        # The check stays relevant only while the box window is 1-3 laps out.
        state.strategy = {"recommended": {"box_lap": 5, "fit_compound": "MEDIUM"}}
    await store.mutate(apply)

    engineer._enqueue("driver_check", {"laps_to_window": 2, "box_lap": 5}, critical=True)
    await engineer._deliver(await store.snapshot_analysis())

    assert voice.reply_windows == ["engineer asked a question"]

    # A statement does not open the window.
    class _StatementBrain(_Brain):
        async def proactive(self, event):
            return "Box this lap for mediums."

    def move_window(state):
        state.strategy = {"recommended": {"box_lap": 5, "fit_compound": "MEDIUM"}}
    await store.mutate(move_window)
    voice = _ListeningVoice()
    engineer = ProactiveEngineer(store, _StatementBrain(database), voice, _Setup(), _Strategy())
    engineer._enqueue("driver_check", {"laps_to_window": 2, "box_lap": 5}, critical=True)
    await engineer._deliver(await store.snapshot_analysis())
    assert voice.reply_windows == []
