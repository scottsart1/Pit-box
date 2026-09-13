# Strategy 4.10.0: final validation record

**Candidate prepared; publication blocked.** The refined model passes its
recorded automated checks, but application reception and persistence failures
remain unresolved. The candidate has not been installed on a Windows runner
or deployed to users. No existing user data or provider credentials were used.

## Scope and source identity

The 72 written scenarios were frozen before implementation at `c3229ed`.
The ledger accounts for 51 covered, 20 partial and one unsupported scenario.
These labels describe evidence for particular behaviors, not proof of all
possible races. All experiments use synthetic physical worlds, generated UDP
or read-only recordings of earlier synthetic QA.

- Released comparison: `daa029ad41ce29c101254e54a53ade5dbaf8b852`, v4.9.8.
- Final model/application source: `eebfac4bec11331b2bda39365ecfc15d77bf20e8`.
- Strengthened final-classification gate: `762846f6d91ce05bbd0720da21493328b6481ce1`.
- Full-suite and final fuzz source: `d0231fe1577f28e3f2340f5ac1aa59496f7064f6`.
  Only documentation changed during the final fuzz runs. The handoff manifest
  identifies the final documentation-inclusive commit and tree.

## Model and regression results

| Check | Observed result |
|---|---|
| Complete project suite, including packaging tests | **1,592 passed** in 77.33 s; two existing Starlette/httpx/AnyIO deprecation warnings |
| Random synthetic scenarios | **1,000**, seed `2026091392`; zero errors or warnings |
| Adversarial synthetic scenarios | **1,000**, seed `20260913`; zero errors or warnings |
| Independent frozen physical worlds | **24/24 executable**, versus 20/24 released; zero candidate output-contract violations |
| Same 20 executable worlds | Four physical-time improvements, 16 unchanged, none worse |
| Independent sequential evaluation | Two runs, 30 decision checkpoints, zero physical failures; physical totals unchanged |
| Complete output equivalence after optimization | All 24 complete JSON outputs match the pre-optimization primary model; dedicated boundary tests cover ties, missing fields and penalties |
| Final measured model computation | P95 **123.615 ms**, maximum 129.557 ms in the frozen benchmark |
| New Python files and patch hygiene | Ruff passed on all 20 added Python files; final diff whitespace check passed |

The model corrects physical tyre allocation, same-compound replacement,
urgent and exact requested stop schedules, observed pace references, pending
finish penalties, current-only pit opportunities, field/points projections,
weather chronology, confidence qualifications and held-plan consistency.
Conditional undercut/overcut arithmetic has separate numerical oracles. A final
cross-feature audit also corrected an emergency weather override that replaced
a feasible two-stop wet plan with an infeasible one-stop and used a passed entry.

The independent benchmark does not cover every changed branch. Dedicated
regressions establish driver overrides, penalties and the severe-weather
finite-inventory interaction. The paired median regret remains 24.280 seconds;
weather and wear-cliff regret do not improve. The oracle knows generated future
conditions and has a disclosed timing advantage. These results do not establish
real-race calibration or calibrated finish probabilities.

Earlier failed tests, corrected evaluator adapters and harness defects remain
documented in `strategy-test-failure-dispositions.md`. Their original logs are
retained; no failed physical world was removed to improve the reported score.

## Application observations

The fixed input contains **31,930 frames** spanning 99.589909 seconds, SHA-256
`c713ee4391e5f22755c16c240a4ae7a75bfc5c5aa599d6c6bc625f98d88cc436`.
It contains 11 packet IDs and no final-classification packet. Its recorded timing
is bursty: 320.61 frames/s on average, up to 182 in a 100 ms bin and 706 in a
one-second bin. These are measured input properties, not an established product
throughput limit.

| Fixed 1× input on final source | Tmpfs control | Workspace overlay control |
|---|---:|---:|
| Parsed / recorded frames | 25,470 / 25,470 | 25,203 / 25,203 |
| Sent frames absent before recording | 6,460 (20.23%) | 6,727 (21.07%) |
| Reported parser drops | 0 | 0 |
| Observed live strategy snapshots | 177 | 179 |
| Live strategy contract violations | 0 | 0 |
| HTTP request latency P95 / max | 0.7838 / 1.3615 s | 0.7021 / 1.8204 s |
| Final full SQLite integrity | Passed | **Failed: 12 error rows** |
| Relaunch and retained catalog | Passed | Correctly blocked after integrity failure |

The passing control also preserves 25 strategy snapshots covering laps 1–25,
including held calls, with zero persisted contract violations. Its sentinel and
catalog survive restart. Readable rows in the failed database are not evidence
that its persisted history is complete.

Capture rotation is independently fixed: a deterministic barrier experiment
improved from **6/208** recorded datagrams to **208/208**, retaining all 202
offered during catalog registration. Both final app controls record every parsed
frame. Overflow remains bounded and counted; raw session attribution before the
normalization callback remains an explicit limitation.

The separate generated-telemetry helper on source `762846f` passes a stronger
finish contract: 25 laps, exactly the emitted P7 classification (six points and
one stop), 34 persisted strategy snapshots, completed session read-back after
restart and full integrity after both shutdowns. It observes 49,232 additional
received packets, with combined health/state request P95 1.5762 s and maximum
2.3240 s. It does not independently count every sent packet and is **Linux
source-helper evidence, not a Windows installer result**. The launcher and
diagnostics are included in the handoff.

## Why this is not deployed

1. **Persistence:** the same fixed capture corrupts both released and candidate
   applications on workspace overlay storage, also with a different stock
   Python/SQLite runtime. Tmpfs passes support a storage-environment association,
   not a proven root cause or production fix. Tmpfs is not durable user storage.
2. **Reception and responsiveness:** zero reported drops concealed a roughly
   20% fixed-input reception deficit. Capture is now complete relative to parsed
   input, but upstream loss and sustained-load latency remain unresolved.
3. **Supported-platform validation:** the actual Windows install, frozen startup,
   sustained UDP, classified finish, persistence, restart, uninstall and retention
   gate has not run for 4.10.0. Clean-Windows rendering, real game behavior,
   audio/provider narration and empirical forecast accuracy remain unobserved.
4. **Publishing access:** GitHub branch and issue creation returned HTTP403,
   `Resource not accessible by integration`. Findings are logged locally and
   separated from existing issues; no remote branch, new issue number, release
   artifact or deployment is claimed.

## Reproduction

Use the bundle or patch from the handoff, and install `.[dev]` in an isolated
environment. The runtime manifest records the exact installed distributions.

```bash
python -m pytest -q
python -m tools.strategy_fuzz --scenarios 1000 --seed 2026091392 --json random.json
python -m tools.strategy_fuzz --scenarios 1000 --seed 20260913 --adversarial --json adversarial.json
python tools/strategy_validation.py --source /path/to/checkout --output candidate.json
python tools/strategy_app_smoke.py --capture /path/to/fixed-synthetic-telemetry.pwcap --speed 1 --output-parent /path/to/fresh-qa-parent
```

Keep the independent baseline, manifest and evaluator unchanged. The supplied
post-shutdown audit tool copies evidence before SQL and compares original frame
bytes with multiplicities. The publication sequence and remaining gates are in
`strategy-release-handoff.md`. A prepared candidate is not a release approval.
