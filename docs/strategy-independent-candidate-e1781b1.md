# Preliminary independent candidate comparison

Candidate snapshot: `e1781b1`, evaluated in a detached worktree. Baseline: released `daa029a`. Both reports use evaluator SHA-256 `e4a84bf83900a571ad44c135898e6214b331e4462f799b301a2582aa4e18f1f4`, with the same frozen manifest, worlds, ordinary observations and historical summaries. This is preliminary; later production corrections require another run.

| Measure | Released baseline | Candidate e1781b1 |
|---|---:|---:|
| Physically impossible recommended plans | 4 / 24 | 0 / 24 |
| Unconditional finish instruction on an infeasible plan | 4 | 0 |
| Reported-feasible but physically impossible | 0 | 0 |
| Common executable worlds: improved / unchanged / worse | — | 4 / 16 / 0 |
| Median regret on the same 20 executable worlds | 24.280 s | 24.280 s |
| Finite-set worlds with an executable finish | 0 / 4 | 4 / 4 |
| Linear family P90 regret | 12.970 s | 10.794 s |
| Quadratic family P90 regret | 60.087 s | 49.315 s |
| P95 model runtime | 39.553 ms | 127.060 ms |
| Closed-loop physical failures | 0 / 2 | 0 / 2 |
| Future model recommendation changes before execution | 0 | 1 |

The clear gain is the finite-resource decision contract. All four formerly impossible recommendations become physically executable two- or three-stop plans with unique available sets. Their regret against the frozen oracle lower bound is 0, 0, 0 and 0.187889 s. The first 18-lap case now uses stint lengths 1+5+6+6, fitted MEDIUM followed by the distinct SOFT, HARD and MEDIUM spares. It says to box on the current lap and correctly accounts for that lap on the current tyre.

Of the 20 worlds executable under both revisions, four improve by 3.025–11.186 s and 16 are unchanged. No common-world physical time regression appeared. The paired median is unchanged; the candidate's lower overall median (13.946 s across 24 finite results) also includes four newly executable low-regret worlds and should not be advertised as a uniform pace improvement.

Nonlinear weather and wear-cliff results are unchanged. Weather median regret remains 72.032 s, with P90 94.145 s, against an oracle that knows the exact future and uses independently chosen physics. That is an explicit limitation, not real-race calibration or a reason to retune the withheld worlds.

The two sequential runs each reach the finish, with identical physical total time under baseline and candidate. The linear run has one additional change to its future stop instruction before execution. This harness calls model `compute()` and does not invoke the radio stability layer; it therefore cannot claim increased driver-facing chatter. The full radio replay checks must assess that separately.

Model P95 runtime rises by roughly 3.2 times in this small shared-Linux sample. The measured 127 ms remains below the product's 350 ms proactive interval in these worlds, but it is not a bound for full grids, long races, unknown inventory or an installed Windows system. The performance increase should remain visible in the release assessment.

Evidence artifacts:

- `strategy-validation-baseline-adapter-corrected-v2.json`: SHA-256 `cb4c4611f6a540e730192a3e40aa5f39a92d84b40e7cf715754704d348e91db2`.
- `strategy-validation-candidate-e1781b1-adapter-corrected-v2.json`: SHA-256 `01abd20b3324fe8772fa648e556809c32f6e57a92f02620065289c85ddad702f`.

The earlier preliminary candidate artifact without the end-of-lap output correction is superseded. Its three apparent physical failures were evaluator translation errors, not product defects. See the baseline report's correction history. Nineteen evaluator tests, including both adapter regressions, passed.
