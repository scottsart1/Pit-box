# Independent strategy benchmark: released baseline

Measured revision: `daa029ad41ce29c101254e54a53ade5dbaf8b852` (v4.9.8), clean source tree.
Manifest frozen in commit `39eeaa4` before executing the released model. The evaluator's physical arithmetic and dynamic program use no production simulation or lap-cost function.

## Actual baseline result

| Family | Worlds | Physically impossible recommendations | Median finite regret | P90 finite regret |
|---|---:|---:|---:|---:|
| Linear degradation | 4 | 0 | 1.434 s | 10.794 s |
| Finite tyre inventory | 4 | 4 | unavailable | unavailable |
| Rejoin traffic | 4 | 0 | 2.545 s | 8.524 s |
| Unseen quadratic degradation | 4 | 0 | 37.882 s | 49.315 s |
| Unseen wear cliff | 4 | 0 | 17.368 s | 24.224 s |
| Future weather | 4 | 0 | 72.032 s | 94.145 s |

Across the 20 physically executable outputs, median regret was 17.935 s and P90 66.247 s. The four impossible outputs remain explicitly counted; they are not assigned a flattering finite score. Six training worlds had median finite regret 0 s; 18 withheld worlds had median finite regret 30.359 s. All nonlinear/weather families are withheld even when paired with a training seed.

These numbers compare the chosen strategy with the exact best strategy in a deliberately small generated world. The oracle knows its actual future weather, nonlinear tyre response and traffic clearance. The production model receives normal state and linear historical summaries; it does not receive that hidden truth. This information advantage makes regret useful for diagnosing sensitivity, but it does **not** establish real-game prediction accuracy or make every second of regret a defect.

The definite defect is narrower and directly observable: all four finite-inventory cases emitted `feasible: false` together with the instruction **“Stay out to the finish.”** The fitted set has only two usable laps, but 12, 15 or 18 laps remain. The independent exhaustive search finds a physically executable two- or three-stop finish. For example, case `2026091301-finite_sets` can stop after 1, 6 and 12 remaining laps using three distinct available sets. Staying out exhausts the fitted tyre on remaining lap 3. This is both a missed feasible finish and a contradictory driver instruction. The engine did not falsely label these four plans feasible; that distinction is recorded separately.

Two sequential runs re-evaluated the actual model on the wear and fitted set resulting from each previous lap. They completed 30 checkpoints without physical failure or a future recommendation change before execution. Their finite regret was 14.190192 s (linear) and 13.055792 s (wear cliff). This exercises lap-by-lap model decisions; it does not test the wall-clock radio hold, database learning, UDP handling or the user interface.

The baseline's P95 single-checkpoint model runtime was 44.084 ms in this shared Linux environment. This is a small-sample responsiveness measurement, not a worst-case bound or a Windows claim.

## Harness validation and reproduction

Seventeen evaluator tests passed. They verify exact stop arithmetic, actual-lap weather exposure, hard physical wear/life and unique-set constraints, a necessary two-stop finish over six laps, and agreement of dynamic programming with an independent exhaustive enumeration for every family. They also check explicit versus legacy set allocation, no silent omission of impossible cases, scoring consistency and actual closed-loop set execution. Ruff passed for the added files.

Run the same evaluator file against each revision in a new process:

```bash
python tools/strategy_validation.py --source /path/to/released-worktree --output /tmp/strategy-baseline.json
python tools/strategy_validation.py --source /path/to/candidate-worktree --output /tmp/strategy-candidate.json
python -m pytest tests/test_strategy_validation.py -q
```

`--only finite_sets` reproduces the resource failures. `--skip-closed-loop` omits sequential runs. Every report contains all generated worlds, ordinary observations, historical summaries, emitted recommendations, oracle stop assignments, failure reasons, runtime, revision and evaluator/manifest digests. The program exits successfully when it produces a report; findings inside the report must be inspected by the release gate.

Baseline evidence file: `strategy-validation-baseline.json`, SHA-256 `dde04f50e921db7415528e47a100a3e7447126f074f3328e9b802c4bf672acad`.
Manifest SHA-256: `e64e4bc36c829a07073d746ba7e4822221be02794a19e357344207a6a8bd51fe`.
Evaluator SHA-256: `a22eabea73ea0bcdf136df741ee3922a3caccdf1454bf15bf8e76930373f94a2`.

No user database, account, provider, capture or installation was accessed. Candidate results must use these unchanged worlds and scoring rules; failed heldout cases are evidence to disclose, not a reason to alter the manifest or tune hidden-world parameters.
