# Independent validation of candidate 3ed73b9

The candidate passes the bounded physical-feasibility and output-contract checks in the frozen independent benchmark. This result covers the primary strategy model; it is not approval of the complete release or evidence of real-game calibration.

Evaluated source commits, each from a clean detached worktree:

- Released baseline: `daa029ad41ce29c101254e54a53ade5dbaf8b852`.
- Candidate: `3ed73b95863a8db289a11ed189f65b82ae3b576a`.

The same evaluator generated 24 worlds from the predeclared four seeds and six families, plus two sequential runs. The comparison verifies identical physical worlds, observations, history summaries, oracle results and evaluator/manifest hashes before reporting any change. Earlier adapter mistakes and their repairs are documented in `strategy-independent-baseline-2026-09.md`; this run uses both corrected adapters.

## Physical results and paired comparisons

| Measure | Baseline | Candidate |
|---|---:|---:|
| Executable recommended plans | 20 / 24 | 24 / 24 |
| Impossible recommended plans | 4 | 0 |
| Claimed-feasible but physically impossible | 0 | 0 |
| Contradictory unconditional finish instructions | 4 | 0 |
| Newly executable finite-inventory cases | — | 4 |
| Improved / unchanged / worse, same 20 executable worlds | — | 4 / 16 / 0 |
| Paired median regret, same 20 worlds | 24.280 s | 24.280 s |
| Paired P90 regret, same 20 worlds | 66.503 s | 66.247 s |
| Closed-loop physical failures | 0 / 2 | 0 / 2 |
| Closed-loop future recommendation changes before execution | 0 | 1 |
| P95 model computation time | 40.737 ms | 131.936 ms |

All four finite-inventory cases now finish using unique physical spare sets. Three match the independent oracle's best time; the fourth is 0.187889 s above its lower bound. They previously returned `feasible: false` with “Stay out to the finish.” For the 18-lap case with only two fitted laps left and spare lifetimes of 5, 6 and 6 laps, the candidate uses 1+5+6+6 laps and explicitly allocates all three distinct spares.

Four already executable worlds improve by 3.025–11.186 s; none becomes slower. The same-world median is unchanged. The all-finite candidate median of 13.946 s includes four newly executable low-regret cases, while the baseline median of 24.280 s excludes its four impossible cases. Those unequal populations must not be used to claim a uniform percentage improvement in strategy accuracy.

| Predeclared split | Common executable worlds | Improved / unchanged / worse | Newly executable | Paired median regret, before → after | Paired P90, before → after |
|---|---:|---:|---:|---:|---:|
| Training seeds and families | 4 | 0 / 4 / 0 | 2 | 0 → 0 s | 2.008 → 2.008 s |
| Withheld seeds or families | 16 | 4 / 12 / 0 | 2 | 34.217 → 34.217 s | 72.174 → 72.032 s |

There are six training worlds and 18 withheld worlds in total. All quadratic, cliff and weather families were withheld, including when generated with a training seed. No generated world, oracle, scoring rule, split or acceptance condition was changed after seeing candidate results.

## What remains uncertain

Weather and wear-cliff physical decisions are unchanged. Weather median regret remains 72.032 s and P90 94.145 s. This reflects a deliberately independent world whose oracle knows the future conditions and tyre response; the planner receives ordinary weather evidence and linear historical summaries. It does not prove that those time losses would occur in F1, and it does not establish that the weather model is calibrated.

The frozen oracle can change tyres before the current remaining lap, whereas a normal production “box this lap” call completes that lap first. Output translation now correctly respects this difference. The oracle's extra timing opportunity remains part of the disclosed lower-bound advantage, rather than silently changing the frozen benchmark.

The two lap-by-lap runs reach the finish with the same physical total time under both versions. The linear run changes one future stop instruction before execution; the cliff run does not. This evaluator calls `compute()` and excludes the radio stability layer, so that change is not evidence of extra spoken calls. Learning through SQLite, UDP packet order, captures, audio and installed Windows behavior require their separate gates.

Runtime rose by approximately 3.24 times. Baseline and candidate ran sequentially after the full test suite finished; a separate isolated app check may have been active. These are shared-Linux measurements from short races, not worst-case guarantees. The measured 132 ms P95 is below a 350 ms proactive interval for these inputs; full-grid and long-race responsiveness still require separate checks.

Nineteen independent evaluator tests pass, covering exact arithmetic, agreement with exhaustive enumeration, finite-set execution, scoring contracts and both adapter boundaries. The paired report generator rejects changed worlds, observations, oracle answers or evaluator versions. Its actual paired output was checked against these full reports.

## Reproduction and committed evidence

```bash
python tools/strategy_validation.py --source /path/to/released-worktree --output /tmp/baseline.json
python tools/strategy_validation.py --source /path/to/candidate-worktree --output /tmp/candidate.json
python -m tools.strategy_validation_compare /tmp/baseline.json /tmp/candidate.json /tmp/paired.json
python -m pytest tests/test_strategy_validation.py -q
```

Committed machine evidence: `docs/strategy-validation-results/daa029a-vs-3ed73b9.json`. It contains every per-world input fingerprint, both emitted plans, physical scores and failure reasons, oracle stop assignments, paired statistics and sequential decisions. All data is synthetic.

| Evidence | SHA-256 |
|---|---|
| Frozen manifest | `e64e4bc36c829a07073d746ba7e4822221be02794a19e357344207a6a8bd51fe` |
| Evaluator | `e4a84bf83900a571ad44c135898e6214b331e4462f799b301a2582aa4e18f1f4` |
| Full baseline artifact | `d727e231c3b263987ece7fb326ce89f6f3ff8203254994b3de52ae94df22790e` |
| Full candidate artifact | `ba3ead62e850e6012449ed5bbf60ecc6320b2dedc0c95e048b9622c4007eed35` |
| Committed paired evidence | `81b9be5276aa8391efcce1d5e945b6e570885fce3cdb214023946d1bb373bbaa` |

The full baseline was rerun for the sequential timing pair and has identical physical decisions to the corrected-v2 baseline. No user database, installation, account or provider was accessed by this benchmark. Any later primary-model commit requires another candidate run before these results can be attributed to it.
