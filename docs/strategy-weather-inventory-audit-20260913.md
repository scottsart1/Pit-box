# Immediate weather and physical inventory interaction

Frozen observed input before correction, integrated revision `d5cdd8f`:
Monza Race, lap 10/27 (18 lap completions remaining), heavy rain, current
HARD physical set 0 with two remaining laps, two distinct WET spares (1 and
2) with nine remaining laps each. All tyre models have one percentage point
of wear per lap and 0.01 s/lap degradation. Player is the only active car.

Observed recommendation: one stop, box lap 10, HARD→WET, set [1],
`feasible=false`, instruction “No feasible finish is supported by the
current tyre data. Box lap 10 for WET.” The displayed conditional wet-use
qualification follows that instruction. Projected time: 1438.92 s.

The same production computation contains a feasible, legal two-stop
candidate: boxes [10,18], HARD→WET→WET, physical sets [1,2], 1+8+9 laps,
1463.85 s. The independent resource constraint is sufficient to reject the
one-stop finish: 2+9<18, whereas two distinct spares can cover the distance.
This is a selection defect, not a claim that the synthetic pace is calibrated.

The immediate-weather override selected the single-stop crossover without
testing its feasibility. Regressions extend the same resource oracle to
INTER, already-passed pit entry, and zero/one spare cases. The correction
must preserve the complete executable wet plan and must not turn exhausted
inventory into a supported finish.

Before correction, all four WET/INTER × reachable/passed-entry regressions
failed; zero/one-spare impossibility controls passed. After correction, all
six pass. The selected weather plan keeps the executable full stop schedule
and physical set identities. Its first stop obeys the same reachable-entry
bound as ordinary candidates; a red-flag immediate change consumes zero
racing laps. At most one extra complete weather candidate is reserved for
uncertainty evaluation, so this does not broaden the search.

Validation: 114 tests passed across `test_strategy_weather_inventory`,
`test_strategy_inventory`, `test_rain_strategy`, `test_strategy_adversarial`,
`test_strategy_weather_evidence`, `test_strategy_event_costs_and_scoring`,
`test_strategy_hold_feasibility`, and `test_race_plan_ranking` (27.65 s).
Ruff and whitespace checks passed. These are source regressions; no installed
Windows or real-game validation is claimed.
