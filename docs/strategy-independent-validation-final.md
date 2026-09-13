# Final primary strategy model validation

Candidate **4.10.0**, commit `a7042b51c4ee860bf45553a411eb713b2431d763`, was evaluated from a clean worktree. The released baseline is `daa029ad41ce29c101254e54a53ade5dbaf8b852`. This assessment covers the primary strategy model; deployment still requires the separate test-suite, application and installed-artifact gates.

The unchanged independent evaluator ran the frozen 24 physical worlds and two sequential runs again. **Every physical score, emitted recommendation, contract result, regret value and sequential action is identical to the previously assessed candidate `3ed73b9`.** The later driver-request, penalty and companion-tool changes did not alter these worlds, which contain no override and no pending penalty. Dedicated tests establish those added behaviors. This measured candidate also contains a classification-loop optimization: all 24 complete `compute()` JSON results are hash-identical to its predecessor `a71c062`. See `strategy-classification-performance.md` for the scalar-equivalence checks and profiler evidence.

| Primary-model measure | Released baseline | Final candidate |
|---|---:|---:|
| Physically executable recommended plans | 20 / 24 | 24 / 24 |
| Impossible plans | 4 | 0 |
| Claimed-feasible but physically impossible | 0 | 0 |
| Output contract failures | 4 | 0 |
| Same 20 executable worlds: improved / unchanged / worse | — | 4 / 16 / 0 |
| Paired median regret | 24.280 s | 24.280 s |
| Paired P90 regret | 66.503 s | 66.247 s |
| Sequential runs / decision checkpoints | 2 / 30 | 2 / 30 |
| Sequential physical failures | 0 | 0 |
| Future model plan changes before execution | 0 | 1 |
| P95 computation time | 40.737 ms | 124.061 ms |

The strict no-harm comparison finds no newly impossible plan and no increase in physical time among the 20 plans executable under both versions, with only a one-microsecond floating-point comparison tolerance. Four finite-inventory cases become executable, with regret 0, 0, 0 and 0.187889 s. They are counted separately from the paired comparison.

The six training worlds include four previously executable cases, all unchanged, and two newly executable cases. The 18 withheld worlds include 16 previously executable cases—four improve and 12 are unchanged—and two newly executable cases. Paired withheld median regret remains 34.217 s; its P90 moves from 72.174 to 72.032 s. These counts show a concrete feasibility improvement and several timing improvements, not a uniformly better or calibrated model.

Weather and wear-cliff results remain unchanged. The independent oracle knows future generated conditions and nonlinear physical response and can change tyres at offset zero, while normal production calls stop at the end of the current lap. Its score is therefore a disclosed lower bound with information and timing advantages. No result establishes real-game or real-driver prediction accuracy.

The two sequential physical totals remain identical to baseline. One future plan changes before execution in the candidate's linear run. This harness excludes the radio hold and does not establish extra spoken chatter. UDP, SQLite learning, actual captures, audio and Windows installation are outside this model benchmark.

The unprofiled rerun followed completion of the full test suite. Its slowest checkpoint was 132.617 ms and P95 was 124.061 ms, compared with 187.585 ms maximum and 158.761 ms P95 before the classification optimization. The released baseline P95 was 40.737 ms. These shared-Linux observations show a measurable reduction in candidate computation cost, but are not a full-grid, long-race or Windows worst-case bound. The separate responsiveness and fixed-capture packet-retention gates remain necessary.

The original scenario manifest, generated worlds, oracle, observations and evaluator were unchanged. Nineteen evaluator tests passed during validation of this exact evaluator. Adapter correction history and the detailed experimental limitations remain in `strategy-independent-baseline-2026-09.md`; the earlier `strategy-independent-validation-3ed73b9.md` report is retained for history.

Current committed evidence is `docs/strategy-validation-results/daa029a-vs-a7042b5.json`; `docs/strategy-validation-results/latest.json` identifies this measured candidate. The paired JSON contains all 24 input fingerprints, both emitted plans, independent scores, oracle assignments and all sequential decisions. Earlier candidate artifacts remain as historical evidence. All evidence is synthetic.

| Evidence | SHA-256 |
|---|---|
| Frozen manifest | `e64e4bc36c829a07073d746ba7e4822221be02794a19e357344207a6a8bd51fe` |
| Evaluator | `e4a84bf83900a571ad44c135898e6214b331e4462f799b301a2582aa4e18f1f4` |
| Full baseline artifact | `d727e231c3b263987ece7fb326ce89f6f3ff8203254994b3de52ae94df22790e` |
| Full final-candidate artifact | `0d3f1cf91d035ca6d027cf5db854f7f2321db9204492dcfc5ab3eb5f66fcdfc5` |
| Committed final paired evidence | `66772c2f1438730d03b19e6e7959a100566ebf9077cedcae74bcbeaf858fbae1` |

Reproduce with the commands in the preceding candidate report, using the exact commits above. The comparison utility refuses differing input worlds, observations, history, oracle results, evaluator hashes or manifests. A subsequent primary-model change requires another candidate run before inheriting this assessment.
