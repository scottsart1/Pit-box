"""What the engineer reads from its tools must be the decision, in words.

Each case here is a tool result that once handed the model something it
could not use, or would misread and repeat to the driver as fact.
"""

from __future__ import annotations

import json
import sys
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
    assert view["faster_plan_awaiting_confirmation"]["delta_vs_recommended_s"] == -3.0
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
    # Like-for-like baseline of the recommended plan: two stints and one stop.
    baseline = 500.0 * 2 + 21.0

    async def engine_what_if(scenario, **_):
        lap = 56 if "this lap" in scenario else int(scenario.split()[2])
        return {
            "available": True, "scenario": f"Box lap {lap} for SOFT", "box_lap": lap,
            "compound": "SOFT", "projected_time_s": 1015.123456, "risk_adjusted_time_s": 1016.0,
            # Engine delta against the ranked plan's traffic-inclusive time.
            "delta_to_best_s": -538.08, "projected_finish_wear_pct": 75.1, "feasible": True,
            "pending_finish_penalty_s": 0.0,
            "finish_penalty": {"pending_s": 0.0, "basis": "x" * 120},
            "compound_rule": {"compliant": True, "change_outstanding": False,
                              "eligible_next_compounds": ["MEDIUM"], "note": "y" * 120},
        }

    monkeypatch.setattr(strategy, "what_if", engine_what_if)

    early = await tools.call("what_if", {"scenario": "box this lap for softs"})
    assert "delta_to_best_s" not in early
    assert early["time_delta_vs_recommended_s"] == round(1016.0 - baseline, 2)
    assert early["ranked_equivalent"] is None
    assert early["recommended_plan"]["instruction"] == "Box lap 62 for SOFT."
    assert "does not replace the recommended call" in early["basis"]
    assert early["legal"] is True and early["feasible"] is True

    ranked_stop = await tools.call("what_if", {"scenario": "box lap 60 for softs"})
    assert ranked_stop["ranked_equivalent"] == {
        "rank": 2, "is_recommended": False, "feasible": True, "legal": True,
        "projected_finish_position": 7, "projected_rejoin_position": 9, "traffic_cost_s": 1.0,
    }
    same_stop = await tools.call("what_if", {"scenario": "box lap 62 for softs"})
    assert same_stop["ranked_equivalent"]["is_recommended"] is True


# ------------------------------------------------------------- realtime bound


def test_realtime_bound_keeps_small_fields_of_a_wide_payload():
    """Many large keys defeat list and string trimming; the call must survive."""
    wide = {f"section_{index}": {f"field_{k}": "v" * 700 for k in range(6)} for index in range(8)}
    wide["instruction"] = "Box lap 20 for hards."
    decoded = json.loads(RealtimeRadio._tool_output(wide))
    assert decoded["instruction"] == "Box lap 20 for hards."
    assert decoded["truncated"] is True and decoded["omitted_fields"]
    assert len(json.dumps(decoded)) <= 16_000
