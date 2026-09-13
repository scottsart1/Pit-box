"""Observed R01/R13/R14 failures and independent uncertainty invariants."""

from copy import deepcopy

import numpy as np
import pytest
from f1.packets import PacketHeader, PacketMotionData

from pitwall.config import settings
from pitwall.state import StateStore
from pitwall.strategy import StrategyEngine
from pitwall.udp import F1DatagramProtocol


def _state(**updates):
    return {"session_uid": 2026, "current_lap": 10, "player_position": 5,
            "race_control_phase": "green", **updates}


def _plan(future_evidence=0):
    return {
        "box_laps": [15], "compounds": ["HARD", "SOFT"],
        "stops_remaining": 1, "projected_time_s": 2700.0,
        "projected_rejoin_position": 5, "feasible": True,
        "stint_models": [
            {"lap_times_s": [90.0] * 10, "wear_sample_size": 30, "deg_sample_size": 30},
            {"lap_times_s": [90.0] * 20, "wear_sample_size": future_evidence,
             "deg_sample_size": future_evidence},
        ],
    }


def _profile(plan, pit_loss=22, samples=1200):
    return StrategyEngine._monte_carlo_profile(plan, _state(), pit_loss, samples)


def test_unobserved_future_stint_cannot_borrow_current_stint_certainty():
    unknown, learned = _profile(_plan()), _profile(_plan(30))
    assert unknown["uncertainty_s"] > learned["uncertainty_s"] * 1.5
    assert unknown["evidence_samples"] == 0
    assert learned["evidence_samples"] == 30


def test_wear_observations_alone_cannot_supply_missing_pace_evidence():
    wear_only = _plan()
    wear_only["stint_models"][1]["wear_sample_size"] = 1000
    assert np.array_equal(_profile(wear_only)["_outcome_times_s"],
                          _profile(_plan())["_outcome_times_s"])


def test_zero_stop_outcomes_are_independent_of_unused_pit_lane_loss():
    plan = _plan(30)
    plan.update(stops_remaining=0, box_laps=[], compounds=["HARD"])
    assert np.array_equal(_profile(plan, 22)["_outcome_times_s"],
                          _profile(plan, 220)["_outcome_times_s"])


def test_a_real_stop_keeps_pit_event_uncertainty():
    assert _profile(_plan(30), 220)["uncertainty_s"] > _profile(_plan(30), 22)["uncertainty_s"]


def test_candidates_share_race_draws_instead_of_arbitrary_label_noise():
    first = _plan()
    second = deepcopy(first)
    second.update(box_laps=[16], compounds=["MEDIUM", "HARD"])
    second["projected_time_s"] += 5
    a, b = _profile(first), _profile(second)
    np.testing.assert_allclose(b["_outcome_times_s"] - a["_outcome_times_s"], 5, atol=1e-10)
    # Order and a different Monte Carlo sample budget must not change the
    # matching underlying draws used for the same physical comparison.
    assert np.array_equal(_profile(first)["_outcome_times_s"], a["_outcome_times_s"])
    assert np.array_equal(_profile(first, samples=80)["_outcome_times_s"],
                          a["_outcome_times_s"][:80])


def test_uncertainty_is_an_uncalibrated_model_estimate_not_extra_observations():
    small = _profile(_plan(), samples=80)
    large = _profile(_plan(), samples=1200)
    assert small["evidence_samples"] == large["evidence_samples"] == 0
    assert small["simulation_samples"] == 80 and large["simulation_samples"] == 1200
    assert small["calibrated"] is False
    assert small["uncertainty_basis"] == "heuristic_per_stint_model"


def test_per_stop_costs_price_current_opportunity_and_later_green_stop_separately():
    plan = _plan(30)
    plan.update(stops_remaining=2, pit_stop_costs_s=[0, 24])
    supplied = _profile(plan, 0)
    plan["pit_stop_costs_s"] = [0, 0]
    both_free = _profile(plan, 0)
    assert supplied["pit_uncertainty_s"] > both_free["pit_uncertainty_s"]


def test_confirmation_uses_elapsed_race_time_not_replay_cpu_speed(monkeypatch):
    monkeypatch.setattr(settings, "strategy_switch_confirm_s", 5.0)
    signature = (18, "HARD", 1)
    event_times = [300.0, 302.0, 306.0]
    outcomes = []
    for host_times in ([100.0, 100.01, 100.02], [100.0, 120.0, 160.0]):
        engine = StrategyEngine(None, None)
        results = []
        for event_time, host_time in zip(event_times, host_times):
            monkeypatch.setattr("pitwall.strategy.time.monotonic", lambda t=host_time: t)
            results.append(engine._switch_confirmed(signature, _state(session_time_s=event_time)))
        outcomes.append(results)
    assert outcomes == [[False, False, True], [False, False, True]]


def test_paused_or_rewound_race_does_not_complete_a_pending_switch(monkeypatch):
    monkeypatch.setattr(settings, "strategy_switch_confirm_s", 5.0)
    signature = (18, "HARD", 1)
    engine = StrategyEngine(None, None)
    assert not engine._switch_confirmed(signature, _state(session_time_s=300.0))
    assert not engine._switch_confirmed(signature, _state(session_time_s=300.0))
    assert not engine._switch_confirmed(signature, _state(session_time_s=20.0, timeline_epoch=1))
    assert not engine._switch_confirmed(signature, _state(session_time_s=24.0, timeline_epoch=1))
    assert engine._switch_confirmed(signature, _state(session_time_s=26.0, timeline_epoch=1))


def test_no_packet_clock_retains_explicit_monotonic_fallback(monkeypatch):
    monkeypatch.setattr(settings, "strategy_switch_confirm_s", 5.0)
    engine = StrategyEngine(None, None)
    monkeypatch.setattr("pitwall.strategy.time.monotonic", lambda: 100.0)
    assert not engine._switch_confirmed((18, "HARD", 1), _state())
    monkeypatch.setattr("pitwall.strategy.time.monotonic", lambda: 106.0)
    assert engine._switch_confirmed((18, "HARD", 1), _state())


@pytest.mark.asyncio
async def test_published_hold_uses_packet_time_and_cannot_cross_a_flashback(stack, monkeypatch):
    store, _, engine, *_ = stack
    monkeypatch.setattr(settings, "strategy_switch_confirm_s", 5.0)
    monkeypatch.setattr(settings, "strategy_min_hold_laps", 0)
    monkeypatch.setattr(settings, "strategy_change_min_gain_s", 1.0)
    await store.update(session_uid=2026, current_lap=16,
                       tyre={"compound": "HARD", "wear": [40] * 4})
    state = await store.snapshot_analysis()
    previous = {"recommended": {
        "box_lap": 19, "fit_compound": "MEDIUM", "stops_remaining": 1,
        "committed_at_lap": 13, "instruction": "Box lap 19 for MEDIUM.",
        "source_compound": "HARD", "neutralisation_phase": "green",
    }}

    def candidate():
        return {
            "neutralisation": {"phase": "green"},
            "recommended": {"box_lap": 17, "fit_compound": "MEDIUM",
                            "stops_remaining": 1, "risk_adjusted_time_s": 1805},
            "plans": [{"box_laps": [lap], "compounds": ["HARD", "MEDIUM"],
                       "stops_remaining": 1, "feasible": True, "legal": True,
                       "risk_adjusted_time_s": cost}
                      for lap, cost in ((17, 1805), (19, 1810))],
        }

    # An accelerated replay advances six race seconds with no host-clock
    # advance. The held instruction changes only after the race-time window.
    monkeypatch.setattr("pitwall.strategy.time.monotonic", lambda: 100.0)
    state["session_time_s"] = 300.0
    held = engine._stabilize_radio_plan(state, previous, candidate())
    assert held["recommended"]["box_lap"] == 19
    assert held["recommended"]["session_epoch"] == [2026, 0, 0]
    state["session_time_s"] = 306.0
    confirmed = engine._stabilize_radio_plan(state, held, candidate())
    assert confirmed["recommended"]["box_lap"] == 17
    assert confirmed["stability"]["held"] is False

    # An instruction observed on the abandoned branch cannot be held merely
    # because its radio signature is still available on the new branch.
    state.update(session_time_s=250.0, timeline_epoch=1)
    after_rewind = engine._stabilize_radio_plan(state, held, candidate())
    assert after_rewind["recommended"]["box_lap"] == 17
    assert after_rewind["stability"]["reason"] == "race timeline changed"


@pytest.mark.asyncio
async def test_real_packet_header_supplies_strategy_clock_and_survives_restart():
    store = StateStore()
    protocol = F1DatagramProtocol(store)
    packet = PacketMotionData()
    packet.header = PacketHeader()
    packet.header.packet_format = 2026
    packet.header.game_year = 26
    packet.header.session_uid = 123
    packet.header.session_time = 302.5
    await protocol._handle(packet)
    assert (await store.snapshot_analysis())["session_time_s"] == 302.5
    packet.header.session_time = 1.25
    await protocol._handle(packet)
    await store.synchronize_session_epoch(123, 1, 0)
    assert (await store.snapshot_analysis())["session_time_s"] == 1.25
