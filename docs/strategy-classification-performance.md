# Exact-output strategy classification optimization

The fixed-rate application replay exposed a throughput risk, so the primary model was profiled before making a performance change. Across the frozen 24 model inputs, classification of simulated finish outcomes consumed 2.297 of 5.781 profiled compute seconds. The scalar loop repeatedly rebuilt the same missing-field facts and rescanned all rival penalties for each of 80–1200 outcomes in each shortlisted plan.

Commit `a7042b51c4ee860bf45553a411eb713b2431d763` compares the bounded rival field against all outcome times using NumPy arrays. Missing competitors and penalty eligibility are determined once per plan. It preserves strict finish comparisons, inclusive physical-time penalty boundaries, missing-field ordering, recovery caps, Race/Sprint points and percentile definitions. Point totals are grouped by their integer finishing-position counts, and duplicate percentile calculations are reused. Physics, sampling, uncertainty parameters, ranking, threading and release thresholds do not change.

| Profile across the same 24 worlds | Before | After |
|---|---:|---:|
| Complete model compute, cumulative | 5.780784 s | 3.604188 s |
| Finish-distribution classification, cumulative | 2.297127 s | 0.170430 s |
| Penalty-recovery calls | 347,517 | 15,677 |
| Missing-field scans | 66,376 | 3,214 |
| Distribution calls | 1,061 | 1,061 |

Classification is about 13.5 times faster under this profile; total profiled model time falls about 38%. Profiling adds overhead, so the unprofiled frozen benchmark was also rerun on the committed code after the full suite finished: P95 falls from 158.761 to 124.061 ms, and its slowest checkpoint from 187.585 to 132.617 ms. These are shared-Linux measurements, not a promise of lossless real-time ingestion.

Exactness was checked at two levels:

1. A frozen scalar reference and the optimized classifier agree on 41 cases covering Race/Sprint, no stop/one stop/three stops, zero/partial/full rival fields, 80/1200 samples, missing competitors, duplicate positions, strict ties, penalty reversals, NaN and positive/negative infinity. The existing penalty and event-cost tests also pass: 96 focused tests in total.
2. All **24 complete `compute()` JSON outputs**, serialized with sorted keys and finite JSON enforcement, have identical SHA-256 hashes before and after the change. This includes all returned plans and probability distributions, not just the selected instruction. All 30 sequential decision plans and physical total times also remain identical. The unchanged independent evaluator still reports 24/24 executable plans and zero output-contract failures.

The pre-optimization source at `f048a8f` is identical under `src/` to `a71c062`. The candidate's full model output was compared against that predecessor, then its benchmark was rerun from the clean committed `a7042b5` worktree. The exact per-world hashes and profile counts are committed in `docs/strategy-validation-results/classification-performance-a7042b5.json` (SHA-256 `90eae311ced3491d599c4e739e1362807e381980ed6346b66cb7802311049642`). The ordinary baseline comparison is `daa029a-vs-a7042b5.json`, and `latest.json` now points to this measured source.

The next required observation is the same fixed packet capture replayed at 1x through the actual app after integrating this optimization and the separate capture-retention fix. This document makes no claim that packet loss is resolved. No user database, provider or installation was accessed by these model measurements.

```bash
python -m pytest tests/test_strategy_classification_vectorization.py tests/test_strategy_finish_penalties.py tests/test_strategy_event_costs_and_scoring.py -q
python tools/strategy_validation.py --source /path/to/a7042b5-worktree --output /tmp/optimized.json
python -m tools.strategy_validation_compare /tmp/baseline-final-pair.json /tmp/optimized.json /tmp/paired.json
```
