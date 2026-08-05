import asyncio
import types

import pytest
from f1.packets import PacketHeader, PacketLapData

from pitwall.brain import EngineerBrain
from pitwall.briefing import BriefingEngine
from pitwall.proactive import ProactiveEngineer
from pitwall.strategy import StrategyEngine
from pitwall.udp import F1DatagramProtocol


class _Brain:
    def __init__(self, database):
        self.database = database

    async def proactive(self, event):
        return str(event.get("payload", {}).get("instruction", "update"))


class _Voice:
    is_busy = False
    realtime_active = False

    async def speak_text(self, text):
        return True


class _Setup:
    async def learn_current_session(self):
        return False


class _Strategy:
    async def recompute(self):
        return {}


def _texas_state(state):
    state.connected = True
    state.session_uid = 3801
    state.session_type = "Race"
    state.mode_profile = "race"
    state.current_lap = 21
    state.total_laps = 28
    state.track_id = 15
    state.track_name = "Texas"
    state.player_position = 10
    state.player_car_index = 9
    state.active_cars = 20
    state.tyre.compound = "MEDIUM"
    state.tyre.age_laps = 10
    state.tyre.wear = [40, 41, 42, 42]
    state.tyre_sets = [
        {"compound": "SOFT", "available": True, "wear_pct": 0, "usable_life_laps": 10},
        {"compound": "HARD", "available": True, "wear_pct": 0, "usable_life_laps": 20},
    ]
    for index, driver in enumerate(state.drivers[:20]):
        driver.active = True
        driver.position = index + 1
        driver.is_player = index == 9
        driver.name = f"Driver {index + 1}"
        driver.tyre_compound = "MEDIUM"
        driver.tyre_age = 12
        driver.gap_to_player_s = (index - 9) * 2.1
        driver.lap_history = [
            {
                "lap_num": lap,
                "lap_ms": 97_000 + index * 15,
                "valid_flags": 1,
                "s1_ms": 30_000,
                "s2_ms": 35_000,
                "s3_ms": 32_000 + index * 15,
            }
            for lap in range(16, 21)
        ]
    state.drivers[9].tyre_stints = [
        {"compound": "HARD", "start_lap": 1, "end_lap": 10},
        {"compound": "MEDIUM", "start_lap": 11, "end_lap": 255},
    ]


@pytest.mark.asyncio
async def test_texas_p10_track_position_beats_a_faster_p19_rejoin(stack):
    """The lap-21 failure must never rank elapsed time above classification."""
    store, _database, strategy, _setup, _analysis, _tools = stack
    await store.mutate(_texas_state)

    result = await strategy.recompute()
    recommendation = result["recommended"]
    stopping = next(plan for plan in result["plans"] if plan["stops_remaining"])

    assert recommendation["stops_remaining"] == 0
    assert recommendation["projected_finish_position"] <= 10
    assert stopping["projected_rejoin_position"] >= 19
    assert stopping["positions_lost_by_stopping"] >= 9
    assert stopping["projected_finish_position"] > recommendation["projected_finish_position"]
    assert result["assumptions"][0].startswith("Plans are ranked by projected finishing position")


@pytest.mark.asyncio
async def test_refused_call_creates_five_lap_hold_and_suppresses_strategy_change(stack):
    store, database, strategy, _setup, _analysis, tools = stack
    await store.mutate(_texas_state)
    brain = EngineerBrain(store, tools, database)
    assert await brain._fast_answer("I'm not boxing; I'm staying out") is None
    hold = (await store.snapshot_analysis())["strategy_hold"]
    assert hold["active"] is True
    assert hold["until_lap"] == 26

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
    await engineer._reset_for_session(3801)
    for lap in range(21, 26):
        await store.update(current_lap=lap)
        await strategy.recompute()
        await engineer._detect(await store.snapshot_analysis())
        assert not [event for event in engineer.pending if event["type"] == "strategy_change"]

    await store.update(current_lap=26)
    await strategy.recompute()
    await engineer._detect(await store.snapshot_analysis())
    released = [event for event in engineer.pending if event["type"] == "strategy_change"]
    assert len(released) == 1
    assert "five-lap" in released[0]["payload"]["hold_released_reason"]


@pytest.mark.asyncio
async def test_material_safety_car_change_releases_hold_once(stack):
    store, database, _strategy, _setup, _analysis, _tools = stack
    await store.mutate(_texas_state)
    await store.update(
        strategy_hold={
            "active": True,
            "until_lap": 26,
            "set_at_lap": 21,
            "baseline": {
                "race_control_phase": "green",
                "wet": False,
                "damage": {},
                "max_wear_pct": 42,
                "compound": "MEDIUM",
            },
        }
    )
    engineer = ProactiveEngineer(
        store,
        _Brain(database),  # type: ignore[arg-type]
        _Voice(),  # type: ignore[arg-type]
        _Setup(),  # type: ignore[arg-type]
        _Strategy(),  # type: ignore[arg-type]
    )
    await store.update(current_lap=22, race_control_phase="safety_car")
    _state, reason = await engineer._evaluate_strategy_hold(await store.snapshot_analysis())
    assert reason == "race control changed to safety car"
    _state, second_reason = await engineer._evaluate_strategy_hold(await store.snapshot_analysis())
    assert second_reason is None


def test_driver_feedback_is_bounded_decays_and_surfaces_conflict():
    state = {
        "current_lap": 12,
        "track_id": 15,
        "mode_profile": "race",
        "tyre": {"compound": "MEDIUM", "wear": [40, 41, 40, 39]},
        "driver_tyre_feedback": {
            "lap": 12,
            "category": "tyres_gone",
            "confidence": 1.0,
        },
    }
    adjustment = StrategyEngine._driver_feedback_adjustment(state, "MEDIUM")
    assert adjustment["wear_factor"] == 1.25
    assert adjustment["deg_factor"] == 1.25
    state["current_lap"] = 16
    faded = StrategyEngine._driver_feedback_adjustment(state, "MEDIUM")
    assert 1.0 < faded["wear_factor"] < 1.1
    state["current_lap"] = 17
    assert StrategyEngine._driver_feedback_adjustment(state, "MEDIUM")["active"] is False


@pytest.mark.asyncio
async def test_tyres_gone_conflict_is_explicit_and_cannot_invert_position_primary(stack):
    store, _database, strategy, _setup, _analysis, _tools = stack
    await store.mutate(_texas_state)
    await store.update(
        driver_tyre_feedback={
            "lap": 21,
            "category": "tyres_gone",
            "confidence": 1.0,
        }
    )
    result = await strategy.recompute()
    assert result["model_summary"]["driver_feedback_factor"] <= 1.25
    assert result["model_summary"]["driver_feedback_deg_factor"] <= 1.25
    assert result["model_summary"]["feedback_conflict"] is True
    assert result["recommended"]["stops_remaining"] == 0


@pytest.mark.asyncio
async def test_cold_pre_session_brief_never_fabricates_personal_history(stack):
    store, database, _strategy, setup, analysis, tools = stack
    engine = BriefingEngine(store, database, analysis, setup, tools)

    payload = await engine.pre_session("race", track_id=15)

    assert payload["telemetry_connected"] is False
    assert payload["historical_lap_count"] == 0
    assert payload["personal_data_available"] is False
    assert payload["weather"]["available"] is False
    assert all(plan["projected_time_s"] is None for plan in payload["leading_strategies"])
    assert all(plan["projection_confidence"] == "low" for plan in payload["leading_strategies"])
    assert "Null values" in payload["projection_notice"]
    assert len(payload["session_goals"]) == 3


@pytest.mark.asyncio
async def test_qualifying_in_lap_debrief_triggers_exactly_once(stack):
    store, _database, _strategy, _setup, _analysis, _tools = stack
    await store.update(
        session_uid=3802,
        session_type="Qualifying",
        mode_profile="qualifying",
        pit_status=0,
    )
    calls = 0

    async def debrief() -> None:
        nonlocal calls
        calls += 1

    protocol = F1DatagramProtocol(store, on_qualifying_lap=debrief)
    header = PacketHeader()
    header.packet_format = 2026
    header.game_year = 25
    header.packet_version = 1
    header.packet_id = 2
    header.session_uid = 3802
    header.player_car_index = 0
    packet = PacketLapData()
    packet.header = header
    player = packet.lap_data[0]
    player.current_lap_num = 4
    player.last_lap_time_in_ms = 90_000
    player.car_position = 1
    player.pit_status = 1
    player.driver_status = 2

    await protocol._handle(packet)
    await protocol._handle(packet)
    await asyncio.sleep(0.35)

    assert calls == 1
