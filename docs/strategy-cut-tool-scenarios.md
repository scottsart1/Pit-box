# Companion undercut/overcut scenarios

Written before implementation, extending D28/D29 at integrating revision
`c8bedbd`. These tools compare explicit short response windows, not guaranteed
race outcomes. A future rival stop, their spare availability, their compound
choice, and their rejoin traffic are ordinarily unobserved and must remain
conditional assumptions.

## Numerical oracles

1. **D28, two recovery laps.** Target starts 1.2 seconds ahead. Our fresh laps
   total 91 + 89 = 180 seconds; their old-tyre laps total 92 + 92 = 184 seconds.
   Equal pit losses cancel. With equal current in-laps and no extra traffic,
   the expected post-response advantage is -1.2 + 184 - 180 = **2.8 seconds**.
   Adding five seconds of our rejoin traffic changes it to **-2.2 seconds**.
2. **D29, one extra lap.** Target starts 0.5 seconds ahead, pits first, and has
   a 93-second out-lap while we stay out for a 90-second in-lap. Equal current
   in-laps and pit losses cancel: -0.5 + 93 - 90 = **2.5 seconds**. If our
   extra lap takes 94 seconds, the answer becomes **-1.5 seconds**.
3. **Gap and cost direction.** Increasing the target's lead by one second
   reduces either margin by exactly one second. Increasing our pit loss by
   five seconds reduces the margin by five; increasing the rival's loss by
   five improves it by five. A target already behind retains a signed gap;
   it is not silently converted into a target ahead.
4. **One time origin.** The live gap belongs to the current checkpoint. If
   the comparison includes the current in-lap, include it for both cars.
   Do not count old-tyre degradation or fresh-tyre improvement twice on top
   of lap times that already contain those effects.

## Executability and shared assumptions

- The player's proposed stop must come from a freshly evaluated, legal,
  feasible main-planner candidate at the requested lap. Its actual physical
  set, lap predictions and pit costs supply the tactical comparison.
- A query is a hypothetical evaluation. It cannot overwrite the live
  strategy, driver override, held call, candidate pool or saved database.
- Known exhausted stock, an unreachable current pit entry, missing/retired
  targets, a completed race and insufficient response distance must produce
  an unavailable explanation instead of a positive tactical verdict.
- Unknown spare stock is an explicit condition; it cannot support a confident
  executable instruction. No real rival future set is invented as observed.
- Report each car's modeled laps, pit costs, traffic assumptions, signed gap,
  exact margin equation and response timing. A positive margin is conditional
  on that response, not a calibrated success probability.
- Preserve legacy margin keys where meaningful, while making the full-window
  net gap explicit. In the overcut case, the old per-extra-lap key describes
  this one-extra-lap comparison, not a reusable linear gain for arbitrary laps.

## Acceptance

The four arithmetic directions must pass independent supplied-lap oracles.
Public tool tests must prove they use a valid main-plan candidate, respond to
traffic/gap changes, retain low confidence for unobserved rival responses,
respect inventory and entry constraints, and leave live state unchanged.
Focused strategy, existing dialogue/tool and inventory tests must stay green.
Real-game rival behavior and out-lap calibration remain unverified here.

## Observed defects and verification

Before the change, increasing the overcut target's lead from 0.5 to 5.5
seconds changed the returned margin from -0.33 to -0.63 seconds. The old
expression used a small clean-air adjustment but omitted the actual live gap
and rival out-lap from its time comparison. An undercut query also returned
`available: true` when telemetry listed only the fitted set and no spare.
The initial 12 regression tests failed before the implementation.

The public tools now select the best legal, feasible main-plan candidate at
the requested stop lap using a separate strategy instance. Their own laps,
physical sets, stop cost and rejoin penalty come from that candidate. Rival
laps use their own age-matched observed pace with the shared stint model and
its cold-tyre cost. The rival's response timing and same-compound replacement
remain explicitly hypothetical. Current reported wear approximates wear at
the observed pace reference, so that existing wear loss is not added twice;
unreported wear is assumed zero and fuel differences remain unknown.

`tests/test_strategy_cut_tools.py` supplies 26 targeted cases: the four exact
elapsed-time oracles, gap/cost signs, public traffic and five-second lead
changes, known exhausted and unknown stock, a real main-candidate extraction,
live-state isolation, passed pit entry, stale telemetry, insufficient distance,
non-race/non-green/wet states, completed or partly served stops, and observed
rival wear counted once. Together with the existing features, dialogue,
inventory and rival-reference tests, **124 tests passed**. Ruff passed for
both changed Python files. These are deterministic synthetic regression
checks, not a calibrated field success rate.

The response window includes the current in-lap for both cars, as a full-lap
approximation shared with the main planner. The undercut then includes two
fresh laps against two rival old-tyre laps, with the rival pitting at the end
of the second; the overcut includes one additional old-tyre lap against the
rival's first out-lap. Each car's single stop cost is counted exactly once.
The tools decline wet/changing or neutralised conditions and an already
ongoing stop, where those short fixed windows lack the required shared
assumptions. They return conditional margins at low confidence and no
success probability; real-game tyre warm-up, fuel, rival spare inventory,
response choices and rejoin traffic still require observational validation.
