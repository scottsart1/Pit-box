# Strategy uncertainty and decision-clock observations

Specification: `docs/strategy-scenarios-2026-09.md`, frozen at `c3229ed` before
implementation. Baseline implementation is the v4.9.8 source at that commit.
All observations below used synthetic inputs and isolated test databases.

## Reproductions before changing production code

The uncertainty probe used session UID 2026, lap 10, green racing, player P5,
no rejoin traffic, 1,200 Monte Carlo draws and a 2,700-second central projection.
The first stint contained ten 90-second HARD laps with 30 wear and 30 pace
observations. The following SOFT stint contained twenty 90-second laps with
no observations. A one-stop plan used a 22-second pit loss.

| Probe | Baseline observation | Refined observation |
|---|---|---|
| Unknown SOFT versus 30 wear/pace observations on SOFT | Both report 0.56 s uncertainty and 60 evidence samples | 1.14 s versus 0.54 s; evidence 0 versus 30 |
| Zero-stop plan, irrelevant pit loss changed from 22 to 220 s | Uncertainty rises from 0.56 to 3.27 s | Both 0.43 s; every sampled outcome identical |
| Same physical projection, box-lap label changed 15 to 16 | p75 changes from 2700.34 to 2700.36 s | Both 2700.73 s; every sampled outcome identical |
| Five-second switch confirmation, host times 100/100.01/100.02 | false/false/false | Race times 300/302/306 produce false/false/true |
| Same race progression, host times 100/120/160 | false/true/true | Race times 300/302/306 produce false/false/true |

The clock probe initially called the existing confirmation path with the same
candidate at each host timestamp. The revised regression also traverses the
published recommendation stabilizer and a real parsed packet's header into
state. It verifies paused race time cannot confirm a switch and a changed
timeline cannot retain the abandoned branch's hold.

## Interpretation and changes

Each stint now contributes its own heuristic tyre variance, using the smaller
of its wear and pace observation counts. Counts are not added because those
measurements can come from the same laps. The least-supported nonempty stint
determines the reported evidence floor. This does not estimate the number of
independent runs or establish empirical forecast calibration.

Candidates use shared race/epoch/lap random draws, including a stable prefix
when the draw count changes. Planned pit events alone contribute pit variance;
per-stop costs are used when supplied, so a free present change does not erase
uncertainty from a future racing stop. Independent stop variances accumulate
as variances rather than multiplying one standard deviation by stop count.

Output explicitly reports `calibrated: false`,
`uncertainty_basis: heuristic_per_stint_model`, simulation draw count, per-stint
evidence counts and variance components. These are model estimates. Neither
the distribution widths nor finishing probabilities have been calibrated to
real held-out race outcomes by this work.

The packet header's `session_time` is retained as `session_time_s`; confirmation
uses it whenever available. Sources without that clock retain a monotonic
fallback. An epoch change or backwards clock restarts pending confirmation.

## Verification

- Before the fixes, the new contract/regression file had nine failures and
  two passes. Some failures were the intentionally added provenance and
  packet-clock API contracts; the numeric defects were separately reproduced
  through the existing interfaces as listed above.
- After implementation, 95 tests passed across the new uncertainty tests,
  strategy, adversarial strategy, benchmark, rain strategy, held-projection,
  race-regression and state suites. This includes twelve new tests.
- In an alternating 1,000-call comparison at 1,200 draws on the same runtime,
  baseline profile median/p95 was 0.191/0.310 ms and candidate was
  0.199/0.319 ms. This measures the profile helper, not whole-app latency.
- Diff whitespace checks and lint for the modified strategy/state and new
  tests passed. Three existing UDP lint findings outside the changed line
  were left untouched.

Full integration, end-to-end UDP replay, independent strategy evaluation and
the release installer gate remain the integrating task's responsibility.
