# Tyre-set pace scenarios before implementation

This addendum fixes the initial pace reference for physical spare sets. EA's
[2026 UDP specification](https://forums.ea.com/blog/f1-games-game-info-hub-en/ea-sports%E2%84%A2-f1%C2%AE25-2026-season-pack-udp-specification/12187347)
defines `m_lapDeltaTime` as "Lap delta time in milliseconds compared to fitted
set". It does not define it as a residual after subtracting compound, tyre age,
or wear. `m_lifeSpan` reports remaining laps; `m_usableLife` is a compound
recommendation. Neither establishes the age of a spare.

The expected values below are defined independently of the simulator. They
extend D15/D23/R12's common pace reference to physical set selection. All toy
laps use constant fuel and setup, dry conditions, wear below the model's pace
penalty, and medium degradation of 0.1 seconds per completed tyre lap. A fresh
medium's age-zero pace is 90.0 seconds. The currently fitted medium has completed
10 laps, and its latest observed pace is therefore 91.0 seconds. Cold-tyre costs
are disabled in the first numerical tests so they cannot hide a pace offset.

1. **Fresh same-compound spare.** The packet reports -1000 ms for a fresh
   medium compared with the fitted set. Its next three lap completions must be
   90.1, 90.2, and 90.3 seconds. Removing the fitted age from the observed base
   and also adding the full -1.0-second delta would incorrectly produce 89.1,
   89.2, and 89.3. The delta is a whole initial difference, not another gain.
2. **Used spare with unknown age.** A second medium reports -500 ms. The toy
   generator knows its age is five laps, but the strategy receives only the
   actual packet's wear, remaining life and relative pace. Its next laps are
   90.6, 90.7, and 90.8 seconds. Do not infer age from life-span subtraction.
   Preserve that age is unknown even when the packet can anchor initial pace.
3. **Cross-compound replacement.** A hard's fresh base is independently set to
   90.65 seconds, and its degradation is 0.1 seconds/lap. A fresh hard reports
   -350 ms against the old medium. Its next laps are 90.75, 90.85 and 90.95.
   Adding the packet difference on top of the already modeled +0.65 compound
   difference and fresh age would double-price the fitted-to-spare transition.
4. **Wear cost also included once.** A used spare's generating pace includes
   its initial wear cost. Its packet difference therefore includes that cost.
   Charge subsequent wear changes, not its initial wear cost for a second time.
5. **Actual candidate schedules.** Run the real `compute` search with finite
   physical sets and inspect an executable same-compound replacement. Its
   complete projected time must equal independently generated current laps,
   future spare laps, configured pit loss, and configured warm-up costs once.
   Report the physical set index and packet reference used.
6. **Missing or contradictory reference.** No matched dry stint, no identifiable
   fitted set, fitted compound inconsistent with car status, ambiguous fitted
   identities, or a wet target cannot establish this calibration. Retain the
   existing model's compound/wear estimate and disclose the unused packet value
   and missing reference. Neither a negative nor a positive unreferenced packet
   delta should silently change absolute projected time.
7. **Stability.** Reordering physical sets must not change the pace attached to
   a set. Modeling a set fitted to the car must not treat its delta-to-itself as
   a spare advantage. A planned grid compound cannot reuse deltas measured
   against a different physical fitted set as if they had been re-referenced.

The bounded implementation may calibrate the spare's *initial* modeled pace to
the current fitted set plus the packet difference, only with a matched dry
reference. It must then add future changes in degradation, wear, weather and
warm-up once. Unknown spare age remains a limitation for nonlinear ageing;
the packet's initial pace estimate does not establish its age, its future
degradation rate, or the game estimator's accuracy. No fuel calibration or wet
compound calibration is introduced by this change.

## Observations and verification

Against integrated baseline `e1781b1`, the real simulator returned 89.1, 89.2,
89.3 seconds for scenario 1: 267.6 seconds against the 270.6-second oracle.
The actual `compute` candidate stopping on lap 11 projected 470.1 seconds:
91.1 on the current set, 357.0 on the spare, and 22.0 in the pit lane. The
independent total is 474.1 seconds: the four spare laps cost 361.0 seconds.

The implementation now subtracts the modeled initial fitted-to-spare difference
from the packet delta before adding a calibration adjustment. Thus, the packet
replaces that difference instead of repeating it. Each stint reports the fitted
identity, fitted reference age and modeled pace, original packet delta, modeled
initial difference, applied adjustment, and assumptions. Model-only fallbacks
report the unused packet value and a reason. Physical index and fitted status
are included in simulation cache keys.

The new tests exercise 23 numerical and missing-evidence cases. The existing
faster-worn-spare inventory test now supplies its identifiable fitted physical
set and matched dry history, so its relative-pace claim has the required basis.
With warm-up explicitly set to 2 seconds then 1 second, all four actual
same-compound one-stop candidates equal the independent lap-by-lap clock plus
the 22-second circuit pit prior. Reordering the inventory preserves the result.

The broad strategy/rain/pit-call/features run produced **297 passed and four
failures**. Each of those four failed identically on untouched `e1781b1`:
legacy red-flag wording, future-stop VSC discounts, a frozen stability box lap,
and championship points inferred from rejoin position. Root integration has
separate fixes for those earlier expectations. Ruff and whitespace checks pass.

This establishes arithmetic consistency with the packet semantics under the
stated synthetic process. It does not validate EA's pace estimator against
real driving, establish unknown spare age, or calibrate nonlinear ageing.
