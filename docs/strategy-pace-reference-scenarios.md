# Observed pace reference: scenario addendum

This extends D15, D23 and R12 of the scenario specification frozen at c3229ed.
The acceptance criteria below were written before changing the model.

## Known linear stint

Use a dry race with constant fuel and a medium tyre. The independent data
generator defines each completed lap as `90 + 0.1 * tyre_age_at_lap_end`
seconds. Give the strategy the exact 0.1 second degradation slope, a 1% wear
rate, and observations ending at ages 2 through 10. Wear stays below every
pace penalty threshold. Five laps remain, and no pit stop is necessary.

Expected future laps are 91.1, 91.2, 91.3, 91.4 and 91.5 seconds: 456.5
seconds in total. Shifting all observations by an arbitrary constant must
shift the forecast by that constant per lap, without changing degradation.

Observed at c3229ed: the model treats the recent median of 90.85 seconds as
a fresh-tyre baseline and adds degradation for ages 10 through 14 again. It
predicts 91.85 through 92.25 seconds, totaling 460.25 seconds. These values
come from the actual `_simulate_stint` and `compute` paths.

## Equal field

An identical rival starts one second behind on the same-age medium tyre.
Neither driver stops. They must retain the one-second gap in the deterministic
remaining-time projection. The old full compute predicts the player P2: its
460.25-second forecast is slower than the rival's 456.0-second forecast,
including that rival's starting gap. The rival also anchors an older median
pace to the current tyre age; fixing the player alone is insufficient. The
field test requires the separate rival-reference correction to be integrated.

## Setup already represented in observed laps

Add a fixed setup pace cost to every generated observation and record the
same setup in the laps and live state. The forecast must retain this cost
exactly once. It must not add the setup prior to the measured pace again.
If the setup changes or matching context is absent, do not claim a measured
setup correction.

## Evidence boundaries

Only a matched dry stint with usable tyre ages may identify the age-zero
reference. A previous compound, previous tyre set, weather transition,
flashback epoch, session or different setup must not silently establish the
current reference. Missing ages keep the existing fallback and explicitly
report that age normalization is unavailable. A wet stint must not receive
an invented dry intercept or fuel correction. Future fuel use is outside
this bounded change; constant-fuel data isolates the measured defect.

For a matched wear observation, reference the existing piecewise wear/cliff
prior at that observation before adding it at the future wear. This prevents
charging an already-observed wear cost twice; it does not claim a newly
calibrated physical wear-to-pace relationship.

## Release acceptance

The numeric linear-stint oracle and setup-cost oracle must pass through the
actual compute path. The result must expose its observed reference, age,
degradation adjustment, sample count and assumptions. The source state must
remain unchanged, and reference extraction must run once per compute rather
than once per enumerated strategy candidate. Existing strategy, weather and
driver-feedback tests must pass. Equal-field ranking is an integration gate.

## Implementation verification

The 20 new reference scenarios pass. The focused strategy, rain, pit-call and
cold-tyre suite passes 184 tests. The constant-fuel forecast is now 456.5
seconds, matching the independent five-lap oracle exactly. Reference
extraction runs once per compute; source snapshots are unchanged. The new
guards also reject repeated copies of one lap as independent evidence and
reject a nominally clear sky while the surface model still indicates rain
water on the track. The rival-ranking correction remains a separate
integration dependency.
