# 2026 Strategy Regression Benchmarks

Pit Wall does **not** pretend to possess the teams' private degradation models, race simulations, or complete historical telemetry. These tests convert publicly reported 2026 strategic turning points into decision invariants that the local simulator must satisfy.

## Why scenario invariants

Copying a winner's stop laps into a game would be poor engineering: the player's tyre wear, setup, race distance, AI pace, weather and safety-car timing differ. Instead, the benchmark suite asks whether the engine behaves correctly when the same strategic mechanism is present.

| Scenario | Required behavior |
|---|---|
| Australia, lap-11 VSC | The identical stop plan is materially cheaper under VSC than under green. |
| Japan, full Safety Car | Full-SC pit loss is lower than VSC and the engine may advance the preferred stop. |
| Britain, late Safety Car | Losing track position with only a few laps left is heavily penalised because the race may not restart. |
| Red flag | A suspension tyre change has zero ordinary drive-through loss, while tyre legality remains enforced. |
| High personal degradation | The engine chooses additional stops when per-wheel wear makes a long stint unsafe or slower. |

The machine-readable definitions live in `data/strategy_benchmarks_2026.json`. Tests use synthetic game telemetry with explicit assumptions, so failures are reproducible and do not depend on internet access.

## Model layers under test

1. **Rules:** dry-compound legality, wet waiver, pit-entry availability.
2. **Tyres:** per-wheel wear, fuel-corrected degradation, setup multipliers, set life and cliff risk.
3. **Race control:** green, VSC, full SC, ending phases and red flag.
4. **Traffic:** projected rejoin and late-SC classification risk.
5. **Uncertainty:** Monte Carlo p25/p50/p75/p90 outcomes and evidence-based confidence.
6. **Learning:** current stint first, then personal track history, then bounded track/setup defaults.

These tests are guardrails. They do not guarantee an optimal strategy against every AI field, but they prevent the most damaging classes of advice: illegal finishes, expired windows, impossible tyre life, blind late-SC stops, and strategy based only on fresh-tyre nominal pace.
