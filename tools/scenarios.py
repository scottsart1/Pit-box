"""Race scenarios for the synthetic broadcaster.

A scenario is the invented situation a demo is built around: which circuit,
which field, where the player is in it, and what state their car is in at the
moment the demo wants to show. Everything else — the packets, the receiver,
the parser, the strategy engine — is the real application.

Two exist:

  * ``SILVERSTONE`` reproduces the original documentation screenshots exactly.
    It is the default, so ``python -m tools.demo_broadcast`` is unchanged.
  * ``HUNGARORING`` is built for the strategy demo video: a stop-or-stay call
    that is genuinely balanced rather than a foregone conclusion. See the note
    on that scenario for why each number is what it is — several of them are
    load-bearing, and getting one wrong collapses the decision into an obvious
    answer without anything appearing to break.

Neither is a recording of a real session.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Driver:
    name: str
    team_id: int
    race_number: int
    pace_offset_s: float


@dataclass(frozen=True, slots=True)
class Scenario:
    key: str
    track_id: int
    track_length_m: int
    total_laps: int
    base_lap_s: float
    start_lap: int
    display_lap: int
    session_time_s: float
    session_time_left_s: int
    session_duration_s: int
    track_temperature: int
    air_temperature: int
    weather: int
    pit_window_ideal_lap: int
    drivers: tuple[Driver, ...]
    player_index: int
    player_compound: str
    player_tyre_age: int
    player_wear: tuple[float, float, float, float]
    player_fuel_kg: float
    player_fuel_remaining_laps: float
    # Weather forecast samples: (minutes ahead, weather id, rain %)
    forecast: tuple[tuple[int, int, int], ...]
    # The running order as (driver name, cumulative gap to the leader in
    # seconds), leader first. Named rather than indexed by grid slot: writing
    # it positionally is how two cars silently end up on the same gap, which
    # puts the player a place lower than intended and makes the wrong car the
    # one behind them. None means "spread the field randomly", which is fine
    # when no particular gap is on screen.
    running_order: tuple[tuple[str, float], ...] | None = None
    # Per-car (compound, age) overrides, keyed by field index.
    stint_overrides: dict[int, tuple[str, int]] = field(default_factory=dict)
    front_wing_damage: int = 0
    caption: str = ""
    # Hold the lap counter at `display_lap` instead of letting it run on.
    # A demo built around one decision has to stay at that decision: over a
    # ten-minute capture the race otherwise advances nine laps, so the screen
    # ends up saying "6 left" while the narration says sixteen.
    #
    # This is a backstop, not the mechanism. Pinning the lap while the car
    # keeps circulating means the live trace never resets and redraws over
    # itself into a scribble, so `time_scale` below is what actually keeps
    # the moment still.
    hold_lap: bool = False
    # Multiplier on how fast the cars cover ground once the warm-up is done.
    # Below 1.0 the lap takes longer than the capture, so no lap completes,
    # the trace stays clean and the counter stays put on its own.
    time_scale: float = 1.0


# The grid, used as plausible labels. Team ids follow the game's enum, which
# `pitwall.identity.TEAM_NAMES` resolves: 0 Mercedes, 1 Ferrari, 2 Red Bull,
# 3 Williams, 4 Aston Martin, 5 Alpine, 6 RB, 7 Haas, 8 McLaren, 9 Sauber.
#
# The pairings were previously arbitrary, which put Russell in a Williams on
# screen. They now match the real teams, because the demo shows team names and
# a wrong one is the first thing anyone who follows the sport would notice.
_GRID = (
    Driver("VERSTAPPEN", 2, 1, 0.00),    # Red Bull Racing
    Driver("NORRIS", 8, 4, 0.09),        # McLaren
    Driver("LECLERC", 1, 16, 0.14),      # Ferrari
    Driver("PIASTRI", 8, 81, 0.21),      # McLaren
    Driver("RUSSELL", 0, 63, 0.26),      # Mercedes  <- the player
    Driver("HAMILTON", 1, 44, 0.31),     # Ferrari
    Driver("SAINZ", 3, 55, 0.38),        # Williams
    Driver("ALONSO", 4, 14, 0.44),       # Aston Martin
    Driver("HULKENBERG", 9, 27, 0.52),   # Sauber
    Driver("STROLL", 4, 18, 0.58),       # Aston Martin
    Driver("TSUNODA", 2, 22, 0.64),      # Red Bull Racing
    Driver("ALBON", 3, 23, 0.71),        # Williams
    Driver("GASLY", 5, 10, 0.77),        # Alpine
    Driver("OCON", 7, 31, 0.83),         # Haas
    Driver("BEARMAN", 7, 87, 0.90),      # Haas
    Driver("LAWSON", 6, 30, 0.96),       # RB
    Driver("COLAPINTO", 6, 43, 1.03),    # RB
    Driver("BORTOLETO", 9, 5, 1.10),     # Sauber
    Driver("ANTONELLI", 0, 12, 1.16),    # Mercedes  <- the rival behind
    Driver("DOOHAN", 5, 7, 1.23),        # Alpine
)


SILVERSTONE = Scenario(
    key="silverstone",
    track_id=7,
    track_length_m=5891,
    total_laps=52,
    base_lap_s=88.6,
    start_lap=28,
    display_lap=34,
    session_time_s=3120.0,
    session_time_left_s=1680,
    session_duration_s=5400,
    track_temperature=34,
    air_temperature=21,
    weather=2,
    pit_window_ideal_lap=36,
    drivers=_GRID,
    player_index=4,
    player_compound="MEDIUM",
    player_tyre_age=19,
    player_wear=(71.0, 73.5, 78.0, 80.5),
    player_fuel_kg=34.0,
    player_fuel_remaining_laps=-0.4,
    forecast=((5, 2, 20), (15, 3, 65), (30, 3, 80)),
    front_wing_damage=4,
    caption="Silverstone, mid-race",
)


# --------------------------------------------------------------------------
# Hungaroring: the strategy demo
# --------------------------------------------------------------------------
#
# The point of this scenario is that the call is genuinely hard. Every number
# below is chosen so both options survive the strategy engine's own scrutiny:
#
#   Stay out. P4 on hards 22 laps old, around 50% worn, 16 laps left. The
#   engine rates the defence against ANTONELLI at 8.5/10 with roughly a 17%
#   chance of being passed, because Hungaroring is one of the hardest
#   circuits on the calendar to overtake at. Holding P4 to the flag is a real
#   outcome, not wishful thinking.
#
#   Box now. A stop costs 20.5s and drops the car to about P8 on rejoin.
#   Fresher rubber recovers most of that, but "most" is the whole argument:
#   the engine projects P5, one place worse than staying out, and says so.
#
# The threat behind is ANTONELLI, 3.6s back on mediums fitted 9 laps ago —
# quicker now, degrading faster, so the advantage has a shelf life. The car
# ahead is LECLERC, 6.4s up the road on a comparable hard stint.
#
# The compounds matter and were got wrong once. Mediums here are modelled at
# 3.46%/lap, so a car 22 laps into a medium stint would already be near 76%
# worn and climbing past 100% before the flag; the engine then refuses to
# consider staying out at all and the trade-off disappears. Hards run at
# 2.27%/lap, which is what makes a one-stop finishable and the call open.
#
# No rain in the forecast: the decision is about tyres, and a weather
# crossover would answer it for us.

_HUNGARORING_ORDER = (
    ("VERSTAPPEN", 0.0),
    ("NORRIS", 2.8),
    ("LECLERC", 9.7),      # P3 — 6.4s up the road: the overcut target
    ("RUSSELL", 16.1),     # P4 — the player
    ("ANTONELLI", 19.7),   # P5 — 3.6s back on fresher hards: the threat
    ("PIASTRI", 26.4),
    ("HAMILTON", 31.0),
    ("SAINZ", 35.8),
    ("ALONSO", 40.3),
    ("HULKENBERG", 45.1),
    ("STROLL", 49.6),
    ("TSUNODA", 54.2),
    ("ALBON", 58.9),
    ("GASLY", 63.4),
    ("OCON", 68.0),
    ("BEARMAN", 72.7),
    ("LAWSON", 77.1),
    ("COLAPINTO", 81.9),
    ("BORTOLETO", 86.4),
    ("DOOHAN", 91.2),
)

HUNGARORING = Scenario(
    key="hungaroring",
    track_id=9,
    track_length_m=4381,
    total_laps=70,
    base_lap_s=79.4,
    start_lap=49,
    display_lap=55,          # 15 laps left
    session_time_s=4380.0,
    session_time_left_s=1200,
    session_duration_s=6000,
    track_temperature=48,    # a hot Hungarian afternoon: degradation is the story
    air_temperature=32,
    weather=0,               # clear; the call must turn on tyres, not rain
    pit_window_ideal_lap=55,
    drivers=_GRID,
    player_index=4,          # RUSSELL
    # The player is on HARDS, and that is the whole reason the call is open.
    #
    # The strategy model prices Hungaroring mediums at 3.2%/lap x 1.08
    # severity = 3.46%/lap. A car 24 laps into a medium stint here would
    # already be near 97% worn, so an earlier draft of this scenario — old
    # mediums, moderate wear — was not merely awkward, it was physically
    # impossible in the model's own terms. The engineer correctly refused to
    # entertain a one-stop, and the trade-off collapsed into a scripted
    # box-now.
    #
    # Hards run at 2.1 x 1.08 = 2.27%/lap. 22 laps at that rate is ~50% wear,
    # which is self-consistent, and 16 more laps lands near 86% — a one-stop
    # that genuinely reaches the flag. Against that, fresh softs are worth
    # roughly a second a lap over 16 laps versus a 20.5s stop. Neither call is
    # free, which is the point.
    player_compound="HARD",
    player_tyre_age=22,
    player_wear=(47.5, 48.5, 50.0, 51.0),
    player_fuel_kg=18.0,
    player_fuel_remaining_laps=1.4,          # fuel is not the constraint here
    forecast=((5, 0, 0), (15, 0, 5), (30, 1, 10)),
    running_order=_HUNGARORING_ORDER,
    stint_overrides={
        # ANTONELLI on mediums fitted 9 laps ago: faster now, but degrading
        # half again as quickly, so his advantage has a shelf life. That is
        # what makes "hold him off" a real option rather than a hope.
        18: ("MEDIUM", 9),
        # LECLERC ahead on the same hard-tyre one-stop as the player, so the
        # overcut has a target in a comparable state.
        2: ("HARD", 24),
    },
    front_wing_damage=0,     # nothing to distract from the tyre call
    caption="Hungaroring, lap 55 of 70",
    # Real time, and laps allowed to complete. Both alternatives were tried
    # and both cost more than they saved:
    #
    #   hold_lap alone pins the counter while the car keeps circulating, so
    #   the live trace never resets and redraws into an unreadable scribble.
    #
    #   time_scale=0.3 stops laps completing at all, which keeps the trace
    #   clean but leaves the degradation and lap-target panels reading "No
    #   valid laps" and "Building…" — a demo that makes the product look like
    #   it has no data.
    #
    # Once the capture stopped recording through the model's thinking time it
    # dropped to about 145 seconds, which is under two laps of drift. The lap
    # counter ticking 55 → 57 during the video is not a defect; it is a race
    # happening.
    hold_lap=False,
    time_scale=1.0,
)


SCENARIOS = {
    SILVERSTONE.key: SILVERSTONE,
    HUNGARORING.key: HUNGARORING,
}


__all__ = ["HUNGARORING", "SCENARIOS", "SILVERSTONE", "Driver", "Scenario"]
