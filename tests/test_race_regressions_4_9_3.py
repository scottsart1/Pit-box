"""What the Suzuka and Sakhir weekends taught the engineer (4.9.3).

Every test here reproduces something that was actually said, or wrongly not
said, over the radio in a real race: "Unknown is not a tyre I know" four times
to a driver asking about hards, a no-stop recommendation that would have been
a disqualification, a plan that flipped several times a lap, calls for a car
whose driver had retired, and a 463-metre brake-point correction.
"""

from __future__ import annotations

import time
import types

import pytest

from pitwall.analysis import AnalysisEngine
from pitwall.brain import EngineerBrain
from pitwall.config import settings
from pitwall.intent import has_negation
from pitwall.prerace import PreRacePlanner
from pitwall.proactive import ProactiveEngineer
from pitwall.race_plan import PlanError, describe_plan, normalise_plan
from pitwall.racing_line import compare_lines
from pitwall.strategy import StrategyEngine

# ---------------------------------------------------------------------------
# "stop" is a noun in strategy talk
# ---------------------------------------------------------------------------


def test_a_stop_count_is_not_a_negation() -> None:
    for plain in (
        "I would actually prefer the soft two-stop strategy",
        "let's do the one stop",
        "I'll take a pit stop on lap 18",
    ):
        assert not has_negation(plain), plain
    for negated in (
        "stop telling me about the gearbox",
        "stop going on about the tyres",
        "I'm not boxing",
    ):
        assert has_negation(negated), negated


def test_a_stated_preference_for_a_two_stop_locks_it() -> None:
    action = EngineerBrain._strategy_override_action(
        "I would actually prefer the soft two-stop strategy", 9
    )
    assert action is not None
    assert action["next_compound"] == "SOFT"
    assert action["preferred_stops"] == 2
    assert action["locked"] is True


def test_a_question_about_a_tyre_is_not_a_lock() -> None:
    for question in (
        "should we box for hard tyres right now and take advantage of the yellow flag",
        "do you feel that we should now shift over to hards and complete the race on that",
        "what do you think about the overcut strategy at this point in time",
    ):
        assert EngineerBrain._strategy_override_action(question, 10) is None, question


def test_a_decision_to_switch_still_locks() -> None:
    action = EngineerBrain._strategy_override_action("let's shift over to hards now", 10)
    assert action is not None
    assert action["next_compound"] == "HARD"
    assert action["next_box_lap"] == 10


# ---------------------------------------------------------------------------
# Placeholder tyres are never spoken as tyre names
# ---------------------------------------------------------------------------


def test_a_placeholder_tyre_is_never_answered_as_a_tyre_name() -> None:
    with pytest.raises(PlanError) as refused:
        normalise_plan({"compounds": ["UNKNOWN", "SOFT"], "box_laps": [12]}, total_laps=29)
    assert "Unknown is not a tyre" not in str(refused.value)
    assert "which tyre" in str(refused.value)
    # A genuinely unknown word is still refused by name.
    with pytest.raises(PlanError, match="Purple is not a tyre I know"):
        normalise_plan({"compounds": ["PURPLE", "SOFT"], "box_laps": [12]}, total_laps=29)


def test_describe_plan_never_says_unknowns() -> None:
    text = describe_plan(
        {"compounds": ["UNKNOWN", "MEDIUM", "SOFT"], "box_laps": [10, 15]}
    )
    assert "unknown" not in text.lower()
    assert "start on the fitted tyre" in text
    assert describe_plan({"compounds": ["UNKNOWN"], "box_laps": []}) == (
        "No stop, the fitted tyre to the flag."
    )


# ---------------------------------------------------------------------------
# The grid discussion does not own the race
# ---------------------------------------------------------------------------


def _on_the_grid(state, *, current_lap=0, total_laps=29, compound="MEDIUM"):
    state.session_type = "Race"
    state.mode_profile = "race"
    state.current_lap = current_lap
    state.total_laps = total_laps
    state.player_position = 8
    state.active_cars = 20
    state.track_id = 3
    state.player_car_index = 0
    state.tyre.compound = compound
    state.tyre.age_laps = 0
    state.tyre.wear = [0.0, 0.0, 0.0, 0.0]
    state.tyre_sets = [
        {"compound": "SOFT", "available": True},
        {"compound": "MEDIUM", "available": True},
        {"compound": "HARD", "available": True},
    ]
    player = state.drivers[0]
    player.car_idx = 0
    player.is_player = True
    player.pit_stops = 0
    player.tyre_compound = compound


@pytest.mark.asyncio
async def test_an_open_grid_discussion_lapses_once_the_race_is_running(stack) -> None:
    store, _, strategy, *_ = stack
    await store.mutate(_on_the_grid)
    planner = PreRacePlanner(store, strategy)
    briefing = await planner.propose()
    assert briefing["phase"] == "proposed"

    # Lap 10, safety car, and the driver never answered the grid proposal.
    await store.update(current_lap=10)
    turn = await planner.try_respond(
        "should we box for hard tyres right now and take advantage of the yellow flag"
    )
    assert turn is None, "the race owns this sentence, not the plan"
    briefing = await planner.snapshot()
    assert briefing["phase"] == "idle"
    assert "unknown" not in briefing["spoken"].lower()
    # And it stays closed for the next question too.
    assert await planner.try_respond("what do you think about the overcut strategy") is None


@pytest.mark.asyncio
async def test_the_panel_cannot_negotiate_a_plan_mid_race(stack) -> None:
    store, _, strategy, *_ = stack
    await store.mutate(lambda s: _on_the_grid(s, current_lap=12))
    planner = PreRacePlanner(store, strategy)
    briefing = await planner.respond("make it a one stop")
    assert briefing["phase"] == "idle"
    assert briefing["committed"] is False
    assert "under way" in briefing["spoken"]


@pytest.mark.asyncio
async def test_a_stale_unknown_shape_is_recomputed_before_it_is_proposed(stack) -> None:
    store, _, strategy, *_ = stack
    await store.mutate(_on_the_grid)
    # The first computation of a race often runs before the compound has
    # arrived; make the cached plan look exactly like that.
    stale = dict(await strategy.recompute())
    stale["shapes"] = [
        dict(shape, compounds=["UNKNOWN"] + list(shape["compounds"])[1:])
        for shape in stale["shapes"]
    ]
    stale["recommended"] = dict(stale["recommended"], compounds=["UNKNOWN", "SOFT"])
    await store.update(strategy=stale)

    planner = PreRacePlanner(store, strategy)
    briefing = await planner.propose()
    assert briefing["phase"] == "proposed"
    assert briefing["proposal"]["compounds"][0] == "MEDIUM"
    assert "unknown" not in briefing["spoken"].lower()


@pytest.mark.asyncio
async def test_now_does_not_move_the_first_stop_to_the_grid(stack) -> None:
    store, _, strategy, *_ = stack
    await store.mutate(_on_the_grid)
    planner = PreRacePlanner(store, strategy)
    briefing = await planner.propose()
    before = list(briefing["proposal"]["box_laps"])
    assert before, "the proposal needs a stop for this test to mean anything"

    briefing = await planner.respond("box for hards right now")
    assert briefing["proposal"]["box_laps"] == before
    assert "moved to lap 1" not in briefing["spoken"]


# ---------------------------------------------------------------------------
# Only raced compounds count for the two-compound rule
# ---------------------------------------------------------------------------


def _race_state(**overrides):
    state = {
        "current_lap": 1,
        "player_car_index": 0,
        "mode_profile": "race",
        "tyre": {"compound": "HARD"},
        "completed_laps": [],
        "drivers": [
            {
                "car_idx": 0,
                "tyre_stints": [
                    # Sat on the qualifying softs until the grid, then hards.
                    {"start_lap": 1, "end_lap": 0, "compound": "SOFT", "actual_compound": 16},
                    {"start_lap": 1, "end_lap": 255, "compound": "HARD", "actual_compound": 18},
                ],
            }
        ],
    }
    state.update(overrides)
    return state


def test_the_grid_swap_does_not_count_as_a_raced_compound() -> None:
    assert StrategyEngine._used_compounds(_race_state()) == ["HARD"]
    rule = StrategyEngine._compound_rule(_race_state())
    assert rule["change_outstanding"] is True
    assert rule["eligible_next_compounds"] == ["MEDIUM", "SOFT"]


def test_a_lap_zero_summary_does_not_count_either() -> None:
    state = _race_state(
        current_lap=2,
        completed_laps=[
            {"lap_num": 0, "compound": "SOFT"},
            {"lap_num": 1, "compound": "HARD"},
        ],
    )
    assert StrategyEngine._used_compounds(state) == ["HARD"]


def test_a_real_first_lap_stint_counts() -> None:
    state = _race_state(
        current_lap=3,
        completed_laps=[
            {"lap_num": 1, "compound": "SOFT"},
            {"lap_num": 2, "compound": "HARD"},
        ],
        drivers=[
            {
                "car_idx": 0,
                "tyre_stints": [
                    {"start_lap": 1, "end_lap": 1, "compound": "SOFT"},
                    {"start_lap": 2, "end_lap": 255, "compound": "HARD"},
                ],
            }
        ],
    )
    assert StrategyEngine._used_compounds(state) == ["SOFT", "HARD"]
    assert StrategyEngine._compound_rule(state)["compliant"] is True


def test_a_finished_stint_is_trusted_once_the_race_has_passed_it() -> None:
    # No lap records at all (the app joined mid-race): the stint history is
    # all there is, and a stint that ended laps ago was certainly raced.
    state = _race_state(
        current_lap=12,
        completed_laps=[],
        drivers=[
            {
                "car_idx": 0,
                "tyre_stints": [
                    {"start_lap": 1, "end_lap": 9, "compound": "MEDIUM"},
                    {"start_lap": 10, "end_lap": 255, "compound": "HARD"},
                ],
            }
        ],
    )
    assert StrategyEngine._used_compounds(state) == ["MEDIUM", "HARD"]


# ---------------------------------------------------------------------------
# The spoken call does not flap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_held_plan_outside_the_top_five_is_still_held(stack) -> None:
    store, _, strategy, *_ = stack
    await store.update(
        current_lap=14,
        tyre={
            "compound": "MEDIUM",
            "age_laps": 12,
            "wear": [35, 36, 40, 41],
            "inner_temps_c": [92, 100, 106, 110],
        },
    )
    state = await store.snapshot_analysis()
    previous = {
        "neutralisation": {"phase": "green"},
        "recommended": {
            "box_lap": 18, "fit_compound": "HARD", "stops_remaining": 1,
            "risk_adjusted_time_s": 1801.0, "instruction": "Box lap 18 for HARD.",
            "committed_at_lap": 13, "source_compound": "MEDIUM",
            "neutralisation_phase": "green",
        },
    }
    candidate = {
        "neutralisation": {"phase": "green"},
        "recommended": {
            "box_lap": 18, "fit_compound": "SOFT", "stops_remaining": 2,
            "risk_adjusted_time_s": 1800.4, "instruction": "Box lap 18 for SOFT.",
        },
        # The dashboard's top five are all flavours of the two-stop...
        "plans": [
            {
                "box_laps": [18, 23], "compounds": ["MEDIUM", "SOFT", "MEDIUM"],
                "stops_remaining": 2, "risk_adjusted_time_s": 1800.4,
                "feasible": True, "legal": True,
            },
        ],
    }
    # ...but the held one-stop survived the shortlist.
    strategy._candidate_pool = [
        {
            "box_laps": [18], "compounds": ["MEDIUM", "HARD"],
            "stops_remaining": 1, "risk_adjusted_time_s": 1801.0,
            "feasible": True, "legal": True,
        }
    ]
    result = strategy._stabilize_radio_plan(state, previous, candidate)
    assert result["stability"]["held"] is True
    assert result["recommended"]["fit_compound"] == "HARD"
    assert result["recommended"]["stops_remaining"] == 1


@pytest.mark.asyncio
async def test_a_faster_plan_has_to_stay_faster_before_it_is_spoken(stack) -> None:
    store, _, strategy, *_ = stack
    await store.update(
        current_lap=16,
        tyre={
            "compound": "HARD",
            "age_laps": 14,
            "wear": [40, 42, 46, 48],
            "inner_temps_c": [92, 100, 106, 110],
        },
    )
    state = await store.snapshot_analysis()
    previous = {
        "neutralisation": {"phase": "green"},
        "recommended": {
            "box_lap": 19, "fit_compound": "MEDIUM", "stops_remaining": 1,
            "risk_adjusted_time_s": 1810.0, "instruction": "Box lap 19 for MEDIUM.",
            "committed_at_lap": 13, "source_compound": "HARD",
            "neutralisation_phase": "green",
        },
    }

    def candidate():
        return {
            "neutralisation": {"phase": "green"},
            "recommended": {
                "box_lap": 17, "fit_compound": "MEDIUM", "stops_remaining": 1,
                "risk_adjusted_time_s": 1805.0, "instruction": "Box lap 17 for MEDIUM.",
            },
            "plans": [
                {"box_laps": [17], "compounds": ["HARD", "MEDIUM"], "stops_remaining": 1, "risk_adjusted_time_s": 1805.0, "feasible": True, "legal": True},
                {"box_laps": [19], "compounds": ["HARD", "MEDIUM"], "stops_remaining": 1, "risk_adjusted_time_s": 1810.0, "feasible": True, "legal": True},
            ],
        }

    first = strategy._stabilize_radio_plan(state, previous, candidate())
    assert first["stability"]["held"] is True
    assert first["stability"]["reason"] == "faster plan awaiting confirmation"
    assert first["recommended"]["box_lap"] == 19

    # The same faster plan, still winning once the window has passed.
    signature, first_seen = strategy._pending_switch
    strategy._pending_switch = (
        signature,
        first_seen - float(settings.strategy_switch_confirm_s) - 1.0,
    )
    second = strategy._stabilize_radio_plan(state, previous, candidate())
    assert second["stability"]["held"] is False
    assert second["recommended"]["box_lap"] == 17

    # A different winner in between starts the clock again.
    strategy._pending_switch = None
    other = candidate()
    other["recommended"]["box_lap"] = 18
    other["plans"][0]["box_laps"] = [18]
    strategy._stabilize_radio_plan(state, previous, other)
    third = strategy._stabilize_radio_plan(state, previous, candidate())
    assert third["stability"]["held"] is True


# ---------------------------------------------------------------------------
# The radio: retirement, the garage, and stale advice
# ---------------------------------------------------------------------------


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
    async def learn_current_session(self):
        return True


class _Strategy:
    async def recompute(self):
        return {}


def _engineer(stack) -> ProactiveEngineer:
    store, database, *_ = stack
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
    return engineer


def test_nothing_is_relevant_once_the_driver_has_retired() -> None:
    state = {
        "session_uid": 7,
        "player_car_index": 0,
        "mode_profile": "race",
        "drivers": [{"car_idx": 0, "result_status": 7}],
    }
    event = {"type": "rival_pace", "session_uid": 7, "expires_at": time.time() + 60, "payload": {}}
    assert ProactiveEngineer._event_still_relevant(event, state) is False
    assert ProactiveEngineer._relevance_reason(event, state) == "driver is out of the race"
    state["drivers"][0]["result_status"] = 2  # active again
    assert ProactiveEngineer._event_still_relevant(event, state) is True
    # Classified at the flag counts in a race, not in practice.
    state["drivers"][0]["result_status"] = 3
    assert ProactiveEngineer._player_out_of_race(state) is True
    state["mode_profile"] = "practice"
    assert ProactiveEngineer._player_out_of_race(state) is False


def test_a_strategy_change_that_has_moved_on_is_not_spoken() -> None:
    state = {
        "session_uid": 7,
        "strategy_hold": {},
        "strategy": {"recommended": {"box_lap": 18, "fit_compound": "SOFT", "stops_remaining": 2}},
    }
    event = {
        "type": "strategy_change", "session_uid": 7, "expires_at": time.time() + 60,
        "payload": {"box_lap": 14, "fit_compound": "SOFT", "stops_remaining": 2},
    }
    assert ProactiveEngineer._event_still_relevant(event, state) is False
    event["payload"]["box_lap"] = 18
    assert ProactiveEngineer._event_still_relevant(event, state) is True


def test_a_penalty_undone_by_a_flashback_is_not_announced() -> None:
    state = {"session_uid": 7, "penalties_s": 0, "corner_cutting_warnings": 1}
    penalty = {"type": "penalty", "session_uid": 7, "expires_at": time.time() + 60, "payload": {"penalties_s": 10}}
    warning = {"type": "warning", "session_uid": 7, "expires_at": time.time() + 60, "payload": {"corner_cutting_warnings": 2}}
    assert ProactiveEngineer._event_still_relevant(penalty, state) is False
    assert ProactiveEngineer._event_still_relevant(warning, state) is False
    state.update(penalties_s=10, corner_cutting_warnings=2)
    assert ProactiveEngineer._event_still_relevant(penalty, state) is True
    assert ProactiveEngineer._event_still_relevant(warning, state) is True


@pytest.mark.asyncio
async def test_queued_strategy_advice_is_refreshed_when_spoken(stack) -> None:
    engineer = _engineer(stack)
    current = {
        "instruction": "Stay out to the finish.",
        "box_lap": None, "fit_compound": None, "stops_remaining": 0,
    }
    state = {
        "mode_profile": "race", "current_lap": 11, "race_control_phase": "safety_car",
        "tyre": {"wear": [10, 10, 10, 10]},
        "strategy": {"recommended": current},
    }
    progress = {"type": "progress_update", "payload": {"lap": 11, "strategy": {"instruction": "Box lap 18 for HARD.", "box_lap": 18}}}
    engineer._refresh_payload(progress, state)
    assert progress["payload"]["strategy"]["instruction"] == "Stay out to the finish."

    rival = {"type": "rival_pitted", "payload": {"driver": "Alonso", "strategy": {"instruction": "Box lap 18 for SOFT."}}}
    engineer._refresh_payload(rival, state)
    assert rival["payload"]["strategy"]["instruction"] == "Stay out to the finish."

    # Advice the driver had declined stays declined.
    quiet = {"type": "race_control", "payload": {"to": "safety_car", "strategy": {}}}
    engineer._refresh_payload(quiet, state)
    assert quiet["payload"]["strategy"] == {}


@pytest.mark.asyncio
async def test_damage_and_penalties_are_not_called_from_the_garage(stack) -> None:
    store, *_ = stack
    engineer = _engineer(stack)
    engineer._session_uid = 42
    await store.update(
        connected=True, session_uid=42, game_paused=False, mode_profile="practice",
        current_lap=1, speed_kph=0,
        # Inherited from the previous session's crash.
        penalties_s=40, damage={"front_right_wing": 100, "floor": 30},
    )

    def in_garage(state):
        state.proactive.update({"enabled": True})
        player = state.drivers[0]
        player.car_idx = 0
        player.is_player = True
        player.status = "garage"

    await store.mutate(in_garage)
    await engineer._detect(await store.snapshot_analysis())
    assert not [e for e in engineer.pending if e["type"] in {"damage", "penalty", "fuel_warning"}]

    # Out of the garage the car is fresh; nothing inherited is called.
    await store.update(penalties_s=0, damage={"front_right_wing": 0, "floor": 0}, speed_kph=120)
    await store.mutate(lambda s: setattr(s.drivers[0], "status", "on track"))
    await engineer._detect(await store.snapshot_analysis())
    assert not [e for e in engineer.pending if e["type"] in {"damage", "penalty"}]

    # A real hit later is reported.
    await store.update(damage={"front_right_wing": 45, "floor": 0})
    await engineer._detect(await store.snapshot_analysis())
    assert [e for e in engineer.pending if e["type"] == "damage"]


@pytest.mark.asyncio
async def test_damage_reporting_rearms_after_a_repair(stack) -> None:
    store, *_ = stack
    engineer = _engineer(stack)
    engineer._session_uid = 42
    await store.update(
        connected=True, session_uid=42, game_paused=False, mode_profile="race",
        current_lap=5, total_laps=30, speed_kph=200,
        damage={"front_right_wing": 45},
    )
    await store.mutate(lambda s: (s.proactive.update({"enabled": True}), setattr(s.drivers[0], "status", "on track")))
    await engineer._detect(await store.snapshot_analysis())
    assert len([e for e in engineer.pending if e["type"] == "damage"]) == 1
    engineer.pending.clear()

    # Wing changed at the stop, then hit again two laps later.
    await store.update(damage={"front_right_wing": 0}, current_lap=7)
    await engineer._detect(await store.snapshot_analysis())
    engineer._cooldowns.clear()
    await store.update(damage={"front_right_wing": 30}, current_lap=9)
    await engineer._detect(await store.snapshot_analysis())
    assert [e for e in engineer.pending if e["type"] == "damage"], "the second hit is news"


@pytest.mark.asyncio
async def test_the_queue_is_cleared_when_the_driver_retires(stack) -> None:
    store, *_ = stack
    engineer = _engineer(stack)
    engineer._session_uid = 42
    await store.update(
        connected=True, session_uid=42, game_paused=False, mode_profile="race",
        current_lap=7, total_laps=27,
    )
    await store.mutate(lambda s: s.proactive.update({"enabled": True}))
    engineer._enqueue("rival_pace", {"driver": "Bortoleto", "gap_to_player_s": 1.2}, cooldown_s=0.0)
    assert engineer.pending

    await store.mutate(lambda s: setattr(s.drivers[0], "result_status", 7))
    await engineer._detect(await store.snapshot_analysis())
    assert not engineer.pending
    assert engineer._discarded[-1]["delivery_outcome"] == "driver is out of the race"


# ---------------------------------------------------------------------------
# Coaching numbers are bounded
# ---------------------------------------------------------------------------


def test_an_absurd_brake_point_delta_is_not_spoken() -> None:
    absurd = AnalysisEngine.corner_instruction(
        {"cause": "early brake", "name": "Corner 2", "brake_point_delta_m": 463.0}
    )
    assert "463" not in absurd
    assert "Corner 2" in absurd
    plausible = AnalysisEngine.corner_instruction(
        {"cause": "early brake", "name": "Corner 2", "brake_point_delta_m": 14.0}
    )
    assert "14 metres later" in plausible


def _trace(offset_start: float = 10_000, offset_end: float = -1, lateral_m: float = 0.0):
    points = []
    for index in range(401):
        distance = float(index * 5)
        offset = lateral_m if offset_start <= distance <= offset_end else 0.0
        points.append(
            {
                "d": distance, "t": index / 20, "x": distance, "z": offset,
                "speed": 220.0 if offset == 0 else 210.0,
                "brake": 0.0 if offset == 0 else 0.25,
                "throttle": 1.0 if offset == 0 else 0.72,
            }
        )
    return points


def test_a_reference_hundreds_of_metres_away_is_not_a_line_to_match() -> None:
    misaligned = compare_lines(_trace(500, 800, 388.6), _trace(), threshold_m=1.0)
    assert misaligned["available"] is True
    assert misaligned["zones"] == []
    assert misaligned["top_opportunity"] is None
    # A real line difference is still reported.
    real = compare_lines(_trace(500, 800, 2.4), _trace(), threshold_m=1.0)
    assert real["zones"]
