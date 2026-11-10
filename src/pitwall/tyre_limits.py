"""Every tyre wear threshold the app acts on, in one place.

Seven thresholds across four modules disagreed: DRIVE drew a "cliff" at 72%
while the plan beside it ran the same tyre to 85% and the radio called it
critical at 82%, and a soft's operational limit (80%) sat below the "hard
safety limit" (82%), so a soft stint the planner had already declared
infeasible had not reached the threshold that made the radio insist.

Each value is a judgement unless marked otherwise; recorded stint wear is
what would replace it.
"""

from __future__ import annotations

# Operational wear limit per compound: the planner's usable life of a set.
OPERATIONAL_WEAR_LIMIT = {
    "SOFT": 80.0,
    "MEDIUM": 85.0,
    "HARD": 88.0,
    "INTER": 88.0,
    "WET": 90.0,
}
# Beyond this on any compound a call about the tyre stops being optional: it
# releases a strategy hold, makes the wear call critical and lifts a "don't
# talk about tyres" instruction.
HARD_SAFETY_LIMIT_PCT = 82.0
# The planner's lap-time penalty for wear starts at these averages and peaks,
# and turns steep (0.06 s per percent) past the last one. DRIVE's "pace drop"
# lap is where the steep part begins.
WEAR_PENALTY_AVERAGE_PCT = 52.0
WEAR_PENALTY_PEAK_PCT = 58.0
WEAR_PENALTY_STEEP_PEAK_PCT = 70.0
# The conservative stint time starts charging for wear here.
CONSERVATIVE_PENALTY_PCT = 65.0
# A stint whose peak wear reaches this is infeasible.
STINT_INFEASIBLE_PCT = 92.0
# The tyre wear call: raised from this wear, and re-armed below the lower one
# when a fresh set goes on.
WEAR_CALL_PCT = 65.0
WEAR_CALL_REARM_PCT = 62.0
# Driver feedback that a tyre is fine below, or finished above, these wears is
# read as contradicting the measurement.
FEEDBACK_CONTRADICTS_LOW_PCT = 45.0
FEEDBACK_CONTRADICTS_HIGH_PCT = 75.0


def operational_limit_pct(compound: str) -> float:
    return OPERATIONAL_WEAR_LIMIT.get(str(compound).upper(), 85.0)


def safety_limit_pct(compound: str) -> float:
    """Wear at which a call about this tyre stops being optional."""
    return min(HARD_SAFETY_LIMIT_PCT, operational_limit_pct(compound))
