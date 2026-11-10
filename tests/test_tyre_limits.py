"""One set of tyre wear thresholds for the planner, the radio and DRIVE.

A soft's operational limit (80%) sat below the radio's "hard safety limit"
(82%), so a soft stint the planner had already declared infeasible had not
yet reached the threshold that made the radio insist.
"""

from __future__ import annotations

import pytest

from pitwall import analysis, brain, proactive, strategy, tyre_limits
from pitwall.proactive import ProactiveEngineer


def test_a_soft_is_safety_critical_at_its_own_limit():
    assert tyre_limits.safety_limit_pct("SOFT") == 80.0
    assert tyre_limits.safety_limit_pct("MEDIUM") == 82.0
    assert tyre_limits.safety_limit_pct("unknown") == 82.0
    assert ProactiveEngineer._safety_critical("tyre_wear", {"wear_fl_fr_rl_rr": [80.5], "compound": "SOFT"})
    assert not ProactiveEngineer._safety_critical("tyre_wear", {"wear_fl_fr_rl_rr": [80.5], "compound": "MEDIUM"})


def test_every_module_reads_the_same_limits():
    assert strategy.OPERATIONAL_WEAR_LIMIT is tyre_limits.OPERATIONAL_WEAR_LIMIT
    assert strategy.safety_limit_pct is tyre_limits.safety_limit_pct
    assert proactive.safety_limit_pct is tyre_limits.safety_limit_pct
    assert brain.safety_limit_pct is tyre_limits.safety_limit_pct
    assert analysis.WEAR_PENALTY_STEEP_PEAK_PCT == tyre_limits.WEAR_PENALTY_STEEP_PEAK_PCT


@pytest.mark.parametrize("compound,expected", [("SOFT", 80.0), ("MEDIUM", 85.0), ("HARD", 88.0), ("WET", 90.0)])
def test_operational_limits_are_unchanged(compound, expected):
    assert tyre_limits.operational_limit_pct(compound) == expected


def test_the_pace_drop_lap_is_where_the_planner_s_penalty_turns_steep():
    """DRIVE's pace-drop lap: wear 40% at 5% a lap reaches 70% six laps on."""
    state = {"current_lap": 10, "tyre": {"compound": "MEDIUM", "wear": [40.0] * 4, "age_laps": 8},
             "completed_laps": []}
    result = analysis.AnalysisEngine.compute_degradation(state)
    per_lap = result["current_wear_per_lap_pct"]
    assert result["projected_cliff_lap"] == 10 + int((70.0 - 40.0) // per_lap)
