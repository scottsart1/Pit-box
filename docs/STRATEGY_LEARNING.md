# Personal tyre learning

The race strategy engine uses deterministic tyre and pace estimates. The
language model explains those estimates; changing an AI provider does not
repair biased tyre evidence.

## Evidence used for race strategy

- Historical evidence is selected by circuit. Time trials and qualifying are
  excluded before the history limit is applied, so a long time-trial session
  cannot crowd out practice and race data.
- Invalid laps, pit laps, recorded yellow/red flags and safety-car interruptions
  are excluded. Interruptions are retained for the whole lap even when it ends
  under green, and saved in SQLite for later sessions.
- Wear requires four finite corner readings within 0–100%. Set resets,
  unchanged packets and implausible increments do not become zero-wear tyres.
- Pace requires at least three usable laps spanning two tyre-age laps in a run,
  with fuel readings. Standing starts and cold first laps are excluded. Pace
  can still be learned when wear packets are missing.

## Pace and wear estimates

Pace is corrected using the existing **0.030 seconds/kg fuel prior** before
robust slope fitting. Comparisons stay within each run: session, tyre age reset,
lap gaps, compound, weather and setup changes separate runs. Each run has its
own intercept; the median of usable run slopes forms the compound estimate.
Noisy or implausible fits remain unavailable, and the strategy falls back to
historical or inferred evidence and then track estimates.

This avoids asking ridge regression to distinguish tyre age from fuel mass
inside a stint, where the two are strongly correlated. In a synthetic pair of
runs with a true degradation of +0.20 seconds/lap and different baseline pace,
the previous live fit returned −0.10; the stint fit returns +0.20.

The condition regression remains available for wear. Queries are bounded to
observed feature ranges, poorly fitting predictions are rejected, and accepted
predictions are blended with the measured median. The adjustment is applied to
the actual per-wheel simulation, along with any active driver tyre report.
Compound inference already includes personal wear; the driver style factor is
not applied a second time.

Live wear needs three usable observations to start replacing its prior and
reaches full live weighting at six. Live pace starts at three usable laps and
reaches full weighting at eight. The active session is excluded from historical
learning during live recomputation, preventing the same laps from being counted
twice. The pre-race planner includes completed practice runs even before the
game advances to a new session.

## Degradation learned from the field

Personal evidence is not the only evidence in a race. Every other car is
running the same compounds on the same surface in the same conditions, and
`field_learning.py` estimates a per-compound degradation slope from their
session-history laps.

The fit is a two-way fixed-effects regression over all dry compounds at once:
a car effect absorbs each driver's intrinsic pace, and a lap effect absorbs
everything the field shares on a given lap — fuel load, track evolution, air
and track temperature, a safety car, a shower. No rival fuel telemetry is
needed, because fuel mass is common to the field at a given lap and the lap
effect takes it.

**Degradation is identified by tyre age resetting at pit stops.** Inside one
stint, tyre age advances exactly one lap at a time, so for a car that runs a
compound once, its age is precisely a car effect plus a lap effect and the
fixed effects annihilate it. The stop is what breaks that: age returns to zero
while the lap counter continues, and a field that has stopped on different laps
carries a sawtooth no car-plus-lap decomposition can reproduce. Before the
first round of stops nothing is identified however many cars and laps exist,
and the model reports `age_not_separable_from_lap` rather than a number.
`retained_age_variance` is the fraction of the age regressor surviving both
sets of fixed effects, and it is the published measure of how much the field
has actually supplied.

Excluded before fitting: out-laps and in-laps, laps the player recorded as
neutralised, invalid laps, restricted and retired cars, wet compounds, laps
with no identifiable stint, and laps run within 2.5 s of the car ahead, which
measure aerodynamic wake rather than tyre life. Gaps are reconstructed from
cumulative elapsed time; a car being lapped is close to traffic this
reconstruction cannot see, and those laps remain a known confounder.

The player's own car is excluded from the field. Its laps are already the
personal estimate, and combining two sources that share observations would
report a confidence neither earned. Standard errors are clustered by car,
because ten laps from one driver are one driver's evidence.

Estimates are combined by precision rather than by a first-match cascade, so
sparse personal evidence is nudged by the field instead of either overriding it
or being ignored until a lap counter crosses a threshold. A field estimate is
never offered as tighter than `SIGMA_FLOOR`, which is the standing admission
that the confounders above are not in its standard error. Where the field has
identified nothing, every estimate is exactly what it was before this existed.

The same fit yields compound *contrasts*, which replace the hand-written
`_DEFAULT_DEG_STEP_RATIO` extrapolation for compounds nobody has run with a
measurement. Rival stop laps are still estimated from typical stint length and
are deliberately not learned from observed stops: a rival's stop lap is a
decision, not a tyre limit, and learning from it would mix strategy into
physics.

## What the desktop shows

Confidence is based on the least-supported non-empty stint in the recommended
plan, requiring both pace and wear evidence. Eight measured laps on mediums do
not give an untested hard final stint high confidence. The Strategy screen shows
supporting laps and the source of the final stint's wear and pace estimates.
Unknown live degradation is displayed as unavailable rather than zero.

The comparison table refreshes when time, wear or points change even if the
stop laps and compounds stay the same. Pre-race planning starts a new dry-race
scenario with fresh sets; previous compound usage, neutralisations, rival gaps,
penalties, tyre inventory and live fits do not carry into that scenario.

## Data compatibility and limits

The additive, versioned database migration preserves existing laps and uses the
existing backup-before-migration path. Older builds cannot read the newer schema;
use the pre-migration backup if a rollback is necessary.

Legacy laps retain useful learning, but interruptions that older builds never
recorded cannot be reconstructed. There is no new driver identity, game-version,
assist-setting or car-performance partition in the legacy lap table. Traffic,
damage, changing track grip and driving consistency can still affect pace.
The fixed fuel coefficient and long-stint wear growth remain modelling priors,
not individually calibrated physical measurements. Confidence is an evidence
grade, not a statistically calibrated chance that a strategy will win.

Field degradation is verified against synthetic fields whose true slope is
stated, including fields carrying a shared fuel burn, an improving track, a
safety car and a 4.5 s spread of car pace. Those checks establish that the
estimator recovers what it is given and refuses what it cannot identify; they
do not establish accuracy against the game.

The next validation priority is held-out real race and practice captures: compare
predicted versus actual stint wear, lap pace and stop outcomes, split by circuit,
compound and session. Synthetic checks establish regression correctness, not
real-world predictive accuracy.

## Verification

- `python -m pytest tests -q` covers the rules above; `tests/test_tyre_learning.py`
  holds the learning and desktop regressions, including a synthetic pair of
  runs where the previous cross-stint fit returned the wrong sign.
- `python -m tools.strategy_fuzz --scenarios 200 --seed 17` exercises the
  strategy engine on generated scenarios and must report no errors or warnings.
- The Strategy board regression executes the shipped JavaScript module with
  Node and is skipped when Node is unavailable; Node is not a runtime
  dependency of the app.
- Native Windows packaging, visual browser inspection and a live PS5/wheel
  session are not covered by these checks. Synthetic checks establish
  regression correctness, not real-world predictive accuracy; held-out real
  race and practice captures are the next validation step.
