"""What the engineer reads from its tools must be the decision, in words.

Each case here is a tool result that once handed the model something it
could not use, or would misread and repeat to the driver as fact.
"""

from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter
from copy import deepcopy
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from f1.packets import resolve

from pitwall.brain import EngineerBrain
from pitwall.realtime import RealtimeRadio
from pitwall.tools import TelemetryTools
from pitwall.udp import F1DatagramProtocol
from tools.demo_broadcast import Race
from tools.scenarios import HUNGARORING, SILVERSTONE


async def _demo_race(stack, scenario):
    """Drive the stack with the demo broadcaster's packets up to its display lap."""
    store, _, strategy, _, analysis, tools = stack
    protocol = F1DatagramProtocol(store)
    race = Race(4, scenario)
    race.lap = scenario.start_lap
    race.distance = [distance * 0.2 for distance in race.distance]
    builders = (
        race.session_packet, race.participants_packet, race.lap_packet,
        race.motion_packet, race.telemetry_packet, race.status_packet,
        race.damage_packet,
    )
    guard = 0
    while race.lap < scenario.display_lap and guard < 4000:
        guard += 1
        race.advance(warm_up=True)
        for build in builders:
            await protocol._handle(resolve(build()), None)
    race.set_display_state()
    for _ in range(5):
        race.advance()
        race.set_display_state()
        for build in builders:
            await protocol._handle(resolve(build()), None)
    while not store.lap_queue.empty():
        await analysis.process_lap(store.lap_queue.get_nowait())
    await strategy.recompute()
    return store, tools


def _leaves(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _leaves(item)
    elif isinstance(value, list):
        for item in value:
            yield from _leaves(item)
    else:
        yield value


# --------------------------------------------------------------- strategy view


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", [SILVERSTONE, HUNGARORING], ids=lambda s: s.key)
async def test_strategy_tool_returns_a_radio_sized_decision_view(stack, scenario):
    store, tools = await _demo_race(stack, scenario)
    raw = await tools.get_pit_strategy()
    stored = deepcopy((await store.snapshot_analysis())["strategy"])

    view = await tools.call("get_pit_strategy", {})
    encoded = json.dumps(view, ensure_ascii=False)

    # The full plan is ~80k characters; the model and the radio get the call.
    assert len(json.dumps(raw)) > 60_000
    assert len(encoded) < 10_000
    radio = json.loads(RealtimeRadio._tool_output(view))
    assert "truncated" not in radio and radio == json.loads(json.dumps(view))

    # The decision itself is unchanged.
    for key in ("instruction", "box_laps", "compounds", "feasible", "legal", "call_changes_if"):
        assert view["recommended"][key] == raw["recommended"][key]
    assert len(view["plans"]) == len(raw["plans"])
    assert view["confidence"] == raw["confidence"]
    assert view["recommended"]["stints"][-1]["projected_finish_wear_fl_fr_rl_rr"]

    # The brain's strategy guards read the same facts from the view as from
    # the full plan, so a tool result never trips a spurious refresh guard.
    assert EngineerBrain._material_plan_identity(view) == EngineerBrain._material_plan_identity(raw)
    for text in ("Box this lap.", "Stay out."):
        assert EngineerBrain.qualify_strategy_text(text, view) == EngineerBrain.qualify_strategy_text(text, raw)
        assert (
            EngineerBrain._with_strategy_change_condition(text, view)
            == EngineerBrain._with_strategy_change_condition(text, raw)
        )

    # No unrounded floats and no basis string sent twice.
    assert not [v for v in _leaves(view) if isinstance(v, float) and len(repr(v).split(".")[-1]) > 3]
    repeated = Counter(v for v in _leaves(view) if isinstance(v, str) and len(v) >= 60)
    assert not [text for text, count in repeated.items() if count > 1]

    # Neither the store's plan nor the direct accessor is trimmed.
    assert (await store.snapshot_analysis())["strategy"] == stored
    assert await tools.get_pit_strategy() == raw


@pytest.mark.asyncio
async def test_strategy_view_explains_why_a_faster_plan_ranks_lower(stack):
    """At the Hungaroring the lap-60 soft stop is quicker but ranks below lap 62.

    The view must state the order the engine actually ranks by, or the
    engineer reads the negative delta as a reason to box earlier.
    """
    _, tools = await _demo_race(stack, HUNGARORING)
    view = await tools.call("get_pit_strategy", {})
    recommended = view["recommended"]
    faster_same_place = [
        plan for plan in view["plans"]
        if (plan.get("delta_vs_recommended_s") or 0) < 0
        and plan["projected_finish_position"] == recommended["projected_finish_position"]
    ]
    assert faster_same_place
    assert all(plan["box_laps"][0] < recommended["box_laps"][0] for plan in faster_same_place)
    basis = view["plan_comparison_basis"]
    assert "later first stop ranks higher" in basis
    assert "elapsed time breaks ties" not in basis


def _plan(box_laps, compounds, *, feasible=True, legal=True, risk=1500.0, position=6):
    return {
        "box_laps": box_laps, "compounds": compounds, "feasible": feasible, "legal": legal,
        "risk_adjusted_time_s": risk, "projected_finish_position": position,
        "projected_rejoin_position": position + 2, "traffic_cost_s": 1.0,
        "stops_remaining": len(box_laps), "total_pit_cost_s": 21.0 * len(box_laps),
        "pending_finish_penalty_s": 0.0,
        "stint_models": [
            {"laps": 5, "conservative_time_s": 500.0, "expected_time_s": 499.0,
             "projected_max_wear_pct": 60.0, "projected_finish_wear_fl_fr_rl_rr": [58.0, 60.0, 50.0, 52.0],
             "lap_times_s": [99.8] * 5, "wheel_projection": [[1, 2, 3, 4]] * 5,
             "setup_effects": {"baseline": "x" * 500}}
            for _ in compounds
        ],
    }


def test_strategy_view_keeps_the_guard_fields_of_every_ranked_plan():
    """Only the fifth plan is a feasible alternative; the guard must still see it."""
    recommended = {
        **_plan([20], ["MEDIUM", "HARD"]), "box_lap": 20, "fit_compound": "HARD",
        "action": "pit_stop", "instruction": "Box lap 20 for hards.",
        "call_changes_if": "The call changes for a safety car.",
        "driver_override": {"active": True, "honored": True, "warning": "",
                            "requested": {"next_box_lap": 20}, "delta_vs_automatic_s": 1.234567},
    }
    plans = [recommended] + [_plan([19 + k], ["MEDIUM", "SOFT"], feasible=False) for k in range(3)]
    plans.append(_plan([24], ["MEDIUM", "MEDIUM"], risk=1503.333333))
    plan = {
        "available": True, "confidence": "low", "recommended": recommended, "plans": plans,
        "model_summary": {"selected_stint_wear_source": "track_default",
                          "selected_stint_deg_source": "measured",
                          "compounds": {"HARD": {"wear_source": "track_default", "pace_source": "measured"}}},
        "red_flag_restart": {"active": True, "instruction": "Fit fresh hards during the suspension."},
        "raw_recommended": _plan([18], ["MEDIUM", "HARD"], risk=1497.0),
        "tyre_inventory": {"status": "unknown"},
    }
    view = TelemetryTools.strategy_decision_view(plan)

    assert EngineerBrain._material_plan_identity(view) == EngineerBrain._material_plan_identity(plan)
    assert EngineerBrain.qualify_strategy_text("Box.", view) == EngineerBrain.qualify_strategy_text("Box.", plan)
    assert view["red_flag_restart"] == plan["red_flag_restart"]
    assert view["plans"][0]["is_recommended"] is True
    assert view["plans"][4]["delta_vs_recommended_s"] == 3.33
    assert view["newly_ranked_plan_held_back"]["delta_vs_recommended_s"] == -3.0
    assert view["recommended"]["driver_override"]["delta_vs_automatic_s"] == 1.23
    assert view["tyre_inventory"] == {"status": "unknown"}
    encoded = json.dumps(view)
    assert "lap_times_s" not in encoded and "setup_effects" not in encoded


# --------------------------------------------------------- what_if and routes


def test_live_race_planning_tools_are_offered_on_every_route(stack):
    tools = stack[5]
    for route in ("fast", "normal"):
        offered = {schema["name"] for schema in tools.schemas_for_route(route)}
        # "can I box this lap for softs?", "change the wing at my stop?",
        # "is it worth gambling for the points?" carry no deep-route keyword.
        for name in ("what_if", "get_front_wing_adjustment", "get_championship_scenario",
                     "get_racing_line_analysis", "get_pit_strategy"):
            assert name in offered, (route, name)
        assert "generate_setup" not in offered
        assert "list_saved_sessions" not in offered


@pytest.mark.asyncio
async def test_what_if_is_framed_against_the_ranked_plan_it_cannot_replace(stack, monkeypatch):
    store, _, strategy, _, _, tools = stack
    recommended = {
        **_plan([62], ["HARD", "SOFT"], risk=1554.08, position=7),
        "box_lap": 62, "fit_compound": "SOFT", "action": "pit_stop",
        "instruction": "Box lap 62 for SOFT.",
    }
    ranked = [recommended, _plan([60], ["HARD", "SOFT"], risk=1550.45, position=7)]
    await store.update(current_lap=55, total_laps=70, mode_profile="race", session_type="Race",
                       strategy={"available": True, "recommended": recommended, "plans": ranked,
                                 "confidence": "low"})
    async def engine_what_if(scenario, *, lap=None, compound=None):
        # Parses like StrategyEngine.what_if: an explicit lap wins, then the
        # first number in the text, then the next lap; never a past lap.
        digits = re.search(r"(\d{1,2})", scenario)
        lap = lap if lap is not None else int(digits.group(1)) if digits else 56
        lap = max(55, lap)
        compound = next((name for name in ("SOFT", "MEDIUM", "HARD") if name.lower() in scenario.lower()), "SOFT")
        return {
            "available": True, "scenario": f"Box lap {lap} for {compound}", "box_lap": lap,
            "compound": compound, "projected_time_s": 1015.123456, "risk_adjusted_time_s": 1016.0,
            # The planner's delta against the recommended plan, like for like.
            "delta_to_best_s": 4.2, "position_delta_to_best": 1, "matches_recommended": False,
            "projected_finish_position": 8, "projected_finish_wear_pct": 75.1, "feasible": True,
            "pending_finish_penalty_s": 0.0,
            "finish_penalty": {"pending_s": 0.0, "basis": "x" * 120},
            "compound_rule": {"compliant": True, "change_outstanding": False,
                              "eligible_next_compounds": ["MEDIUM"], "note": "y" * 120},
        }

    monkeypatch.setattr(strategy, "what_if", engine_what_if)

    early = await tools.call("what_if", {"scenario": "box this lap for softs"})
    assert "delta_to_best_s" not in early
    # "This lap" is the current lap, as in the ranked plans, not the next one.
    assert early["box_lap"] == 55 and "assumed" not in early
    assert early["time_delta_vs_recommended_s"] == 4.2
    assert early["position_delta_vs_recommended"] == 1
    assert early["is_recommended"] is False
    assert early["ranked_equivalent"] is None
    assert early["recommended_plan"]["instruction"] == "Box lap 62 for SOFT."
    assert "does not replace the recommended call" in early["basis"]
    assert "prefers the later stop" in early["basis"]
    assert early["legal"] is True and early["feasible"] is True

    ranked_stop = await tools.call("what_if", {"scenario": "box lap 60 for softs"})
    assert ranked_stop["ranked_equivalent"] == {
        "rank": 2, "is_recommended": False, "feasible": True, "legal": True,
        "projected_finish_position": 7, "projected_rejoin_position": 9, "traffic_cost_s": 1.0,
        "delta_vs_recommended_s": -3.63,
    }
    same_stop = await tools.call("what_if", {"scenario": "box lap 62 for softs"})
    assert same_stop["ranked_equivalent"]["is_recommended"] is True

    assert (await tools.call("what_if", {"scenario": "box in two laps for hards"}))["box_lap"] == 57


@pytest.mark.asyncio
async def test_what_if_never_prices_a_stop_the_driver_did_not_name(stack, monkeypatch):
    store, _, strategy, _, _, tools = stack
    recommended = {
        **_plan([62], ["HARD", "SOFT"], risk=1554.08, position=7),
        "box_lap": 62, "fit_compound": "SOFT", "action": "pit_stop",
        "instruction": "Box lap 62 for SOFT.",
    }
    await store.update(current_lap=55, total_laps=70, mode_profile="race", session_type="Race",
                       strategy={"available": True, "recommended": recommended, "plans": [recommended],
                                 "confidence": "low",
                                 "neutralisation": {"phase": "green", "pit_this_lap_available": False}})
    calls = []

    async def engine_what_if(scenario, *, lap=None, compound=None):
        calls.append(scenario)
        digits = re.search(r"(\d{1,2})", scenario)
        lap = max(55, lap if lap is not None else int(digits.group(1)) if digits else 56)
        compound = next((name for name in ("SOFT", "MEDIUM", "HARD") if name.lower() in scenario.lower()), "SOFT")
        return {"available": True, "scenario": f"Box lap {lap} for {compound}", "box_lap": lap,
                "compound": compound, "risk_adjusted_time_s": 1016.0, "feasible": True,
                "compound_rule": {"compliant": True}}

    monkeypatch.setattr(strategy, "what_if", engine_what_if)

    # The engine would price "Box lap 56 for SOFT" for each of these.
    for scenario in ("stay out to the end", "can we go long on this set", "two stop"):
        refused = await tools.call("what_if", {"scenario": scenario})
        assert refused["available"] is False and "get_pit_strategy" in refused["reason"], scenario
    assert calls == []

    no_lap = await tools.call("what_if", {"scenario": "what if we pit for hards"})
    assert no_lap["assumed"] == ["The scenario named no lap; lap 56 was priced."]
    no_compound = await tools.call("what_if", {"scenario": "box lap 58"})
    assert no_compound["assumed"] == ["The scenario named no compound; SOFT was priced."]
    past = await tools.call("what_if", {"scenario": "box lap 12 for hards"})
    assert past["assumed"] == ["Lap 12 has already passed; lap 55 was priced."]

    # Pit entry for this lap has gone: the stop is priced but flagged.
    now = await tools.call("what_if", {"scenario": "box now for mediums"})
    assert now["box_lap"] == 55 and now["pit_entry_passed"] is True
    assert now["earliest_reachable_box_lap"] == 56


# ------------------------------------------------------------ session events


async def _named_field(store):
    def apply(state):
        state.connected = True
        state.player_car_index = 4
        for index, name in ((4, "PLAYER"), (6, "SAINZ"), (18, "ANTONELLI")):
            state.drivers[index].name = name
            state.drivers[index].active = True
            state.drivers[index].position = index
    await store.mutate(apply)
    # Payloads exactly as udp.handle_PacketEventData builds them.
    await store.append_event("PENA", {"vehicle_idx": 6, "type": 4, "infringement": 7,
                                      "other_vehicle_idx": 255, "places_gained": 0, "time": 5, "lap": 33})
    await store.append_event("OVTK", {"overtaking": 18, "overtaken": 4})
    await store.append_event("SCAR", {"safety_car_type": 2, "safety_car": "virtual",
                                      "event_type": 0, "event": "deployed"})
    await store.append_event("RTMT", {"vehicle_idx": 18, "reason": 8})


@pytest.mark.asyncio
async def test_session_events_name_the_driver_and_decode_the_penalty(stack):
    store, tools = stack[0], stack[5]
    await _named_field(store)

    result = await tools.call("get_session_events", {"last_n": 10, "types": "penalty"})

    assert len(result["events"]) == 1
    penalty = result["events"][0]
    assert penalty["kind"] == "penalty"
    assert penalty["driver"] != "car 6" and "SAINZ" in penalty["driver"].upper()
    assert penalty["penalty"] == "time penalty"
    assert penalty["infringement"] == "corner cutting, gained time"
    assert penalty["time_s"] == 5 and penalty["infringement_lap"] == 33
    encoded = json.dumps(result)
    assert "PENA" not in encoded and "vehicle_idx" not in encoded
    assert "time" not in penalty and penalty["seconds_ago"] >= 0  # no epoch timestamp

    everything = (await tools.call("get_session_events", {"last_n": 10, "types": "all"}))["events"]
    overtake = next(event for event in everything if event["kind"] == "overtake")
    assert overtake["other_driver"] == "you"
    assert next(event for event in everything if event["kind"] == "safety_car")["detail"] == "virtual safety car deployed"
    assert next(event for event in everything if event["kind"] == "retirement")["reason"] == "mechanical failure"

    # SCAR with type "none" comes around a red flag; it is not a safety car.
    await store.append_event("SCAR", {"safety_car_type": 0, "safety_car": "none",
                                      "event_type": 3, "event": "resume"})
    latest = (await tools.call("get_session_events", {"last_n": 1, "types": "safety_car"}))["events"][0]
    assert latest["detail"] == "resume, no safety car type reported"


@pytest.mark.asyncio
@pytest.mark.parametrize(("types", "kinds"), [
    ("penalties", ["penalty"]),
    ("Safety Car", ["safety_car"]),
    ("VSC", ["safety_car"]),
    ("overtakes, retirements", ["overtake", "retirement"]),
    ("PENA,OVTK", ["penalty", "overtake"]),
    ("race control", ["penalty", "safety_car"]),
])
async def test_session_event_filter_understands_spoken_kinds(stack, types, kinds):
    store, tools = stack[0], stack[5]
    await _named_field(store)
    result = await tools.call("get_session_events", {"last_n": 10, "types": types})
    assert [event["kind"] for event in result["events"]] == kinds
    assert "unrecognised_types" not in result


@pytest.mark.asyncio
async def test_session_event_filter_says_when_it_matched_nothing(stack):
    store, tools = stack[0], stack[5]
    await _named_field(store)
    unknown = await tools.call("get_session_events", {"last_n": 10, "types": "weather"})
    assert unknown["events"] == [] and unknown["filter_matched_nothing"] is True
    assert unknown["unrecognised_types"] == ["weather"] and "penalty" in unknown["accepted_types"]
    none_yet = await tools.call("get_session_events", {"last_n": 10, "types": "red flag"})
    assert none_yet["events"] == [] and none_yet["filter_matched_nothing"] is True
    assert "unrecognised_types" not in none_yet and none_yet["events_in_log"] == 4


@pytest.mark.asyncio
async def test_ers_deploy_mode_is_named(stack):
    store, tools = stack[0], stack[5]
    await store.update(ers_mode=2, connected=True)
    assert (await tools.get_ers_report())["deploy_mode"] == "hotlap"
    assert (await tools.get_my_car_state())["ers_mode"] == "hotlap"
    await store.update(ers_mode=9)
    assert (await tools.get_my_car_state())["ers_mode"] == "unknown mode 9"


# --------------------------------------------------------------- lap analysis


async def _session_laps(store):
    times = {28: 89_602, 29: 89_411, 30: 89_477, 31: 89_545, 32: 89_512, 33: 89_782}

    def apply(state):
        state.completed_laps = [
            {"lap_num": lap, "lap_time_ms": ms, "valid": True,
             "s1_ms": 30_000 + (lap - 29) * 10, "s2_ms": 29_000, "s3_ms": ms - 59_000 - (lap - 29) * 10,
             "corner_metrics": [{"name": "Turn 3", "loss_vs_pb_s": 0.05 * (lap - 28), "cause": "early brake"}],
             "line_summary": {"available": True, "reference": "personal_best",
                              "mean_abs_deviation_m": round(0.1 * lap, 2), "line_score": float(lap),
                              "zones": [], "top_opportunity": None}}
            for lap, ms in times.items()
        ]
        state.analysis = {
            "last_lap_analyzed": 33,
            "lap_summary": {"lap_num": 33, "lap_time_ms": 89_782, "personal_best_ms": 89_300,
                            "delta_to_pb_s": 0.482, "top_corner_losses": []},
            "racing_line": dict(state.completed_laps[-1]["line_summary"]),
            "flagged_corners": [],
        }
    await store.mutate(apply)


@pytest.mark.asyncio
async def test_lap_analysis_answers_for_the_lap_and_reference_requested(stack):
    store, tools = stack[0], stack[5]
    await _session_laps(store)

    result = await tools.call("get_lap_analysis", {"lap": "31", "compare_to": "session_best"})

    assert result["analysed_lap"] == 31
    assert result["reference"] == "session_best" and result["reference_lap"] == 29
    assert result["delta_s"] == 0.134
    assert result["comparison_basis"] == "against your current-session best (lap 29)"
    assert result["sector_deltas_s"] == {"S1": 0.02, "S2": 0.0, "S3": 0.114}
    assert "lap" not in result and "compare_to" not in result

    pb = await tools.call("get_lap_analysis", {"lap": "last", "compare_to": "personal_best"})
    assert pb["analysed_lap"] == 33 and pb["delta_s"] == 0.482
    assert pb["top_corner_losses"][0] == {"name": "Turn 3", "loss_vs_pb_s": 0.25, "cause": "early brake"}

    previous = await tools.call("get_lap_analysis", {"lap": "30", "compare_to": "previous_lap"})
    assert previous["reference_lap"] == 29 and previous["delta_s"] == 0.066


@pytest.mark.asyncio
async def test_lap_analysis_never_substitutes_another_lap(stack):
    store, tools = stack[0], stack[5]
    await _session_laps(store)
    missing = await tools.call("get_lap_analysis", {"lap": "12", "compare_to": "session_best"})
    assert missing["available"] is False and "analysed_lap" not in missing
    assert "laps 28 to 33" in missing["reason"]
    line = await tools.call("get_racing_line_analysis", {"lap": "12"})
    assert line["available"] is False and "analysed_lap" not in line


@pytest.mark.asyncio
async def test_racing_line_analysis_reports_the_requested_lap(stack):
    store, tools = stack[0], stack[5]
    await _session_laps(store)
    line = await tools.call("get_racing_line_analysis", {"lap": "29"})
    assert line["analysed_lap"] == 29 and line["line_score"] == 29.0
    latest = await tools.call("get_racing_line_analysis", {"lap": "last"})
    assert latest["analysed_lap"] == 33 and latest["line_score"] == 33.0


@pytest.mark.asyncio
async def test_tyre_condition_always_reports_each_wheel(stack):
    store, tools = stack[0], stack[5]
    await store.update(tyre={"compound": "MEDIUM", "age_laps": 24,
                             "wear": [78.0, 80.52417, 71.0, 73.5],
                             "inner_temps_c": [104.0, 105.0, 103.0, 103.0], "blisters": [0, 0, 0, 0]})
    schema = next(item for item in tools.schemas() if item["name"] == "get_tyre_condition")
    assert schema["parameters"]["properties"] == {}
    result = await tools.call("get_tyre_condition", {})
    assert result["wear"] == [78.0, 80.5, 71.0, 73.5]
    assert result["inner_temps"] == [104.0, 105.0, 103.0, 103.0]
    assert result["max_wear_pct"] == 80.5


def test_free_text_arguments_say_what_they_accept(stack):
    schemas = {item["name"]: item["parameters"]["properties"] for item in stack[5].schemas()}
    assert schemas["get_lap_analysis"]["compare_to"]["enum"] == ["personal_best", "session_best", "previous_lap"]
    assert "lap number" in schemas["get_lap_analysis"]["lap"]["description"]
    assert "penalty" in schemas["get_session_events"]["types"]["description"]
    assert "lap number" in schemas["what_if"]["scenario"]["description"]
    for name in ("get_pace_verdict", "get_rival_car_state", "evaluate_undercut", "get_driver_lap_history"):
        assert "ahead" in schemas[name]["driver"]["description"], name
    assert "ahead" in schemas["get_gap"]["target"]["description"]



@pytest.mark.asyncio
async def test_cut_tools_resolve_every_rival_reference_the_schema_offers(stack, monkeypatch):
    """The engine's cut resolver knows only ahead/behind/leader, P<n> and a surname."""
    store, _, strategy, _, _, tools = stack

    def apply(state):
        state.player_car_index = 0
        state.player_position = 6
        state.drivers[0].name = "PLAYER"
        state.drivers[0].position = 6
        mate = state.drivers[3]
        mate.name = "TEAMMATE"
        mate.active = True
        mate.position = 9
        mate.is_teammate = True
    await store.mutate(apply)
    seen = []

    async def cut(driver):
        seen.append(driver)
        return {"available": True}

    monkeypatch.setattr(strategy, "evaluate_undercut", cut)
    monkeypatch.setattr(strategy, "evaluate_overcut", cut)

    await tools.call("evaluate_undercut", {"driver": "teammate"})
    await tools.call("evaluate_overcut", {"driver": "team mate"})
    await tools.call("evaluate_undercut", {"driver": "ahead"})
    # Unresolved references pass through for the engine to refuse.
    await tools.call("evaluate_undercut", {"driver": "zzz"})
    await tools.call("evaluate_overcut", {"driver": "me"})
    assert seen == ["P9", "P9", "ahead", "zzz", "me"]

# ------------------------------------------------------------------ gap trend


async def _tow_against_the_trend(store):
    """The gap grew 1.0 -> 2.2 s over two laps; a tow took 0.25 s back in 8 s."""
    history = []
    for k in range(90):
        gap = 1.0 + 1.2 * (k / 89)
        if k >= 86:
            gap -= 0.25 * ((k - 85) / 4)
        history.append({"session_time_s": 3022.0 + 2.0 * k, "lap": 32 + k // 45, "gap_s": -round(gap, 3)})

    def apply(state):
        state.connected = True
        state.player_car_index = 0
        state.player_position = 5
        state.drivers[0].name = "PLAYER"
        state.drivers[0].position = 5
        state.drivers[0].last_lap_ms = 90_400
        rival = state.drivers[1]
        rival.name = "PIASTRI"
        rival.active = True
        rival.position = 4
        rival.last_lap_ms = 89_900
        rival.gap_history = history
        rival.gap_to_player_s = history[-1]["gap_s"]
    await store.mutate(apply)


@pytest.mark.asyncio
async def test_closing_answer_and_pace_verdict_agree_on_the_same_rival(stack):
    store, database, tools = stack[0], stack[1], stack[5]
    await _tow_against_the_trend(store)

    car = (await tools.get_cars_ahead_progress(1))["cars"][0]
    verdict = await tools.get_pace_verdict("ahead")
    answer = await EngineerBrain(store, tools, database)._fast_answer("am I closing in on the cars in front")

    assert car["trend"] == "falling_back"
    assert verdict["verdict"] == "dropping back"
    assert car["gap_change_s_per_lap"] == verdict["gap_change_s_per_lap"] > 0
    assert "not closing" in answer
    # The tow is still reported, as a short-term move against the lap trend.
    assert car["short_term_change_s"] < 0 and "shrank" in car["trend_conflict"]
    assert verdict["trend_conflict"] == car["trend_conflict"]


@pytest.mark.asyncio
async def test_pace_verdict_and_race_picture_carry_timing_freshness(stack):
    store, tools = stack[0], stack[5]
    await _tow_against_the_trend(store)
    live = await tools.get_pace_verdict("ahead")
    assert live["available"] is True and live["freshness"]["status"] == "live"

    await store.mutate(lambda state: setattr(state, "packet_group_freshness", {"2": time.time() - 30}))
    stale = await tools.get_pace_verdict("ahead")
    assert stale["available"] is False and stale["freshness"]["status"] == "stale"
    assert "verdict" not in stale
    picture = await tools.get_race_picture()
    assert picture["available"] is False and picture["freshness"]["status"] == "stale"


# ------------------------------------------------------------- realtime bound


def test_realtime_bound_keeps_small_fields_of_a_wide_payload():
    """Many large keys defeat list and string trimming; the call must survive."""
    wide = {f"section_{index}": {f"field_{k}": "v" * 700 for k in range(6)} for index in range(8)}
    wide["instruction"] = "Box lap 20 for hards."
    wide["plans"] = [{"box_laps": [20 + k]} for k in range(4)]
    decoded = json.loads(RealtimeRadio._tool_output(wide))
    assert decoded["instruction"] == "Box lap 20 for hards."
    assert decoded["truncated"] is True and decoded["omitted_fields"]
    assert len(json.dumps(decoded)) <= 16_000
    # A kept list is still cut to one entry; the note must not imply it is whole.
    assert decoded["plans"] == [{"box_laps": [20]}]
    assert "first item" in decoded["truncation_note"]
