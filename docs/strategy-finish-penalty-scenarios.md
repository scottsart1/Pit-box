# D25: pending finish penalties, defined before implementation

The official [EA 2026 UDP structures](https://forums.ea.com/t5/s/tghpe58374/attachments/tghpe58374/f1-games-game-info-hub-en/61/8/2026%20Season%20Pack%20Telemetry%20Output%20Structures%20%281%29.txt)
describe LapData `m_penalties` as "Accumulated time penalties in seconds to be
added". Separate fields describe unserved drive-through and stop-go counts,
the actual stop timer, and whether the next stop should serve a penalty. Final
classification's total penalty history is a different quantity. Strategy must
use the current live counter once; it must not add historical penalties or
elapsed stop time again.

1. **Player five-second penalty.** Keep all current gaps, completed laps,
   future tyre estimates and candidate schedules identical. Changing the
   player's current counter from zero to five must add exactly five seconds
   to every candidate's projected and conservative finish total. Zero, one,
   two and three future stops do not change that increment. Stint lap arrays,
   pit costs, physical pace advantages and tyre feasibility stay identical.
2. **Equal future clocks.** Five identical future laps at 90 seconds each
   cost 450 seconds. A rival one second behind projects 451. With no penalties
   the player remains ahead; a five-second player penalty gives 455 versus
   451, and a five-second rival penalty gives 450 versus 456. Equal penalties
   preserve relative classification. Actual compute tests may use the already
   matched linear-stint fixture instead of flat laps, but must retain this
   independently specified five-second difference.
3. **Pending counter clears.** Recompute with zero after the counter clears:
   the future five seconds disappear. A previously served stop or a historical
   penalty event must not retain another five-second addition. A player row
   mirroring the top-level counter must not cause ten seconds to be charged.
4. **Classification is different from overtaking.** A rival can finish ahead
   on the road and behind after its penalty. The stop-plan recovery cap must
   permit this classification gain without inventing an on-track pass or
   changing pit-rejoin position. Count that gain separately. The Monte Carlo
   distribution must apply the same rule on each sampled finish clock.
5. **Uncertainty uses the same axis.** A fixed five-second player penalty
   shifts every shared-draw finish sample and every time quantile by five,
   leaving variance and evidence unchanged. Rival penalties alter finishing
   probabilities through their classification time, not through tyre pace.
6. **Partial or malformed telemetry.** Missing, negative, nonnumeric,
   nonfinite and out-of-protocol penalty values cannot make a forecast NaN or
   manufacture a time advantage. Apply no invented duration, report the
   missing/invalid penalty evidence, and keep all resulting times finite.
   Missing player penalty evidence may use its own identified driver row;
   explicit top-level zero is authoritative even if that row is stale.
7. **Unknown repair/service duration.** The circuit pit-loss prior models a
   normal stop. Damage percentage, past repair duration and unserved penalty
   counts do not establish a future repair or service duration. Disclose this
   limitation; do not invent a duration or absorb it into tyre degradation.

Zero-penalty physics and deterministic lap arrays must remain unchanged. These
tests validate future classification arithmetic; the model still cannot know
an unreported future repair duration or whether an unserved drive-through will
be completed before disqualification.

## Observed result and validation

At baseline `97688ba`, actual `compute` projected 456.5 seconds both before
and after setting the player's live counter to five seconds. The real rival
projector likewise returned 456.25 seconds with either zero or five seconds
reported. Both observed increments were zero against the five-second oracle.

The implementation adds the validated live counter once to candidate finish
totals, including their feedback-free counterparts, and once to rival finish
totals. Physical lap arrays and pit costs remain separate. The reported
before-penalty clock keeps physical pace and pit-cycle comparisons on their
original basis; penalty gains bypass an overtaking cap only when the rival
would finish ahead physically and behind after both cars' penalties. The
sampled classification distribution applies that condition to every draw.

Each plan and rival exposes the pending seconds, source/status, and
before-penalty finish time. Missing or invalid telemetry is disclosed and does
not invent a duration. The what-if comparison includes the same pending
seconds on both compared plans. A held radio instruction refreshes its penalty
metadata and numerical rationale from the current candidate.

Validation: **23 new penalty tests pass**, including actual zero-through-three
stop candidate searches, exact shared-draw translations, equal-field finish
probabilities, cleared counters, nine malformed/partial values, classification
caps, normal-stop limitations, what-if and held-call flows. The full selected
strategy, rain, pit-call and feature suites pass: **340 tests in 42.52 seconds**.
Ruff and whitespace checks pass. No lap estimator, tyre physics, pit-loss
constant, database write path or user data was changed.
