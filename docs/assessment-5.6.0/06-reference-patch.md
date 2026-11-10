# Part 6 — Reference patch

[`reference.patch`](reference.patch) is a prototype of the first-wave fixes,
written to check that the changes specified in Parts 1–3 do what those
parts say and to find out which existing tests they move. It applies to
`1f35a7e`:

```bash
git apply --check docs/assessment-5.6.0/reference.patch
git apply docs/assessment-5.6.0/reference.patch
```

It is a starting point, not the finished change. It adds no tests, touches
no screen, and implements the minimum of each item. Where the patch and an
item's **Change** section differ, the item is the specification.

## What it contains

| File | Item | What the patch does | What the item still asks for |
| --- | --- | --- | --- |
| `rain.py` | [WX-01](01-weather.md#wx-01) steps 1–3 | `conditioned_rain_probability`; `project_wetness_scenarios` conditions every future sample on the offset-zero chance and the observed sky | the tests |
| `rain.py` | [WX-01](01-weather.md#wx-01) step 4 | the confirmation gate in `evaluate`; publishes `confirmed` | the WX-08 sector confirmation; `"wet"` report (WX-11) |
| `rain.py` | [WX-02](01-weather.md#wx-02) | `surface_series`; `lap_fraction`; the innovation against `surface_when_measured`; bias from the innovation | steps 5–6: publish the two new reading fields |
| `rain.py` | [WX-05](01-weather.md#wx-05) step 1 | `_pace_best`: a dry compound is never the answer to another dry compound | steps 2–4: the sentence itself |
| `brain.py` | [WX-04](01-weather.md#wx-04) step 1 | a stop the weather called is a forced stop for the stay-out guard | steps 2–5: the reason string, the recovery model, the cars-ahead reference, the recorded disagreement |
| `strategy.py` | [WX-10](01-weather.md#wx-10) steps 1–3 | `what_if` prices the hypothetical with the planner; `_what_if` keeps the weather plan from replacing it | steps 4–5: `tools.py` and the Strategy page |
| `strategy.py` | [RV-01](02-rivals.md#rv-01) steps 1–2 | `_rival_owed_stop_offset`; no stop in the last two laps | the split into `_rival_compounds_used` / `_rival_owes_compound`; `stop_owed` on projections; `predict_rival_strategy`; the screen |
| `strategy.py`, `config.py` | [ST-01](03-strategy-core.md#st-01) steps 1–2 | `strategy_later_stop_tolerance_s`; later first stop only inside the tolerance | step 3 (expected position outside the points); step 4 (the two descriptions) |
| `engineering.py` | [RV-06](02-rivals.md#rv-06) | the two keys that exist; sprint | `strategy.js`; the tests |

## What it was run against

At `1f35a7e`, before the patch:

```text
python -m pytest tests -q                         3338 passed, 4 skipped
python -m tools.rain_onset_probe --assert-within 2    8 scenarios fail
python -m tools.strategy_probe --assert               10 failures
```

With only the `rain.py` changes for WX-01 steps 1–3 and WX-02:

```text
python -m pytest tests -q                         3338 passed, 4 skipped
python -m tools.rain_onset_probe --assert-within 2    exit 0, every scenario "lap +0", held
```

With the whole patch:

```text
python -m pytest tests -q                         3332 passed, 6 failed, 4 skipped
python -m tools.rain_onset_probe --assert-within 2    exit 0
python -m tools.strategy_probe --assert               1 failure: part 5 (WX-03 is not in the patch)
```

`tools.rain_onset_probe` with the whole patch — the confirmation gate moves
the call from the lap the label changes to the lap after it, which by the
model's own costs loses nothing (the slick is still the quicker tyre on the
first wet lap):

```text
scenario                                                             first call   held  slick loss
forecast 100% light rain                                             lap +1       yes   -5.8 s
forecast 70% light rain                                              lap +1       yes   -5.8 s
forecast 50% light rain                                              lap +1       yes   -5.8 s
forecast 30% light rain                                              lap +1       yes   -5.8 s
forecast 50%, approximate accuracy                                   lap +1       yes   -5.8 s
forecast 50%, no field history                                       lap +1       yes   -5.8 s
forecast 50%, onset lap excluded as weather_change, next lap yellow  lap +1       yes   -5.8 s
forecast 50%, forecast category Overcast                             lap +1       yes   -5.8 s
dry control, forecast 50%                                            false calls: 0
```

`tools.strategy_probe` with the whole patch:

```text
1. Rivals who still owe a second dry compound
   25% distance   14 laps | rival stops projected [1] | call: A second compound is mandatory — box lap 11 for SOFT. | projected P20
   35% distance   20 laps | rival stops projected [1] | call: A second compound is mandatory — box lap 15 for SOFT. | projected P20
   50% distance   29 laps | rival stops projected [1] | call: A second compound is mandatory — box lap 20 for SOFT. | projected P20
   100% distance  57 laps | rival stops projected [2] | call: Box lap 24 for MEDIUM.                              | projected P20
2. What-if on a wet track
   what-if INTER: +0.0 s against the recommended plan
   what-if HARD : +202.3 s against the recommended plan
3. The radio's stay-out guard against a weather stop
   engine: Box lap 30 for INTER.
   the stop is 61.1 s quicker to the flag than staying out | projected P1
   radio:  Box this lap for inters.
4. Strategic rivals view
   player_projected_position: 1
5. The weather radio trigger
   track wet, watching -> weather_crossover: Track is wet; staying out for now.
   verdict: box now    -> strategy_change: Box this lap for inters.
   verdict: box now    -> no weather call queued
FAIL: no weather call is queued when a watched track becomes a stop
```

Three things in that output are **not** fixed by the patch and show why the
delivery order pairs the items it does:

- **Part 5 still queues no weather call.** The guard no longer reverses the
  strategy line, so the driver is at least told to box, but as an ordinary
  strategy change: narrated, behind the 8-second spacing, without the
  reason. The latch itself is [WX-03](01-weather.md#wx-03).

- **Part 1 still projects P20.** Rivals now stop on lap 6 or 7, but the
  player's rejoin position is still counted from the gaps on lap 4
  ([RV-07](02-rivals.md#rv-07)), so a stop on lap 11 is still projected to
  drop behind cars that will have stopped by then. RV-01 without RV-07
  corrects the rivals and not the player.
- **Part 1's stop laps (11 of 14, 15 of 20) are later than the field's.**
  With every stopping plan level at P20 the ranking falls through to the
  tolerance rule, which picks the latest stop within 3 s of the quickest.
  Once RV-07 makes the positions differ, position decides.

## Tests moved by the prototype

Six of 3,338. None is a regression; each pins the behaviour an item
changes, or uses a fixture whose recommended plan is different afterwards.

| Test | Moved by | Why | What to do |
| --- | --- | --- | --- |
| `tests/test_strategy_what_if_fields.py::test_explicit_lap_and_compound_are_used_when_the_text_names_neither` | WX-10 | The fixture has no soft set available and asks what-if for softs. The planner cannot fit a set that is not there, so the answer is now "unavailable". | The test is about `lap` and `compound` travelling as values. Ask for `compound="MEDIUM"` (a spare exists; the plan is illegal under the compound rule and still priced, `feasible is False`). Add `test_what_if_for_a_compound_with_no_set_says_so`. |
| `tests/test_tools_quality.py::test_strategy_view_explains_why_a_faster_plan_ranks_lower` | RV-01 | It asserts the Hungaroring demo has a quicker plan ranked below the recommendation at the same projected position. With rivals making their owed stops the shown plans contain no such pair (the failing assertion is the empty list); it fails with the old tie-break restored too, so it is the rival change. | Keep the property, change the state: build two plans level on position and inside `strategy_later_stop_tolerance_s`, and assert `plan_comparison_basis` states the tolerance. |
| `tests/test_strategy_stability_projection.py::test_recompute_holds_radio_call_but_refreshes_the_whole_projection` | ST-01 | It needs a recompute whose new winner is not materially quicker, so that the call is held. Under the old rule a later stop won on lap alone; now the quicker plan wins by more than `strategy_change_min_gain_s` and is not held. | Construct the second state so the candidate gains less than `strategy_change_min_gain_s`. The assertions about the refreshed projection stay as they are. |
| `tests/test_strategy_finish_penalties.py::test_held_radio_instruction_refreshes_current_penalty_metadata` | ST-01 | The same: its hold no longer occurs. | The same. |
| `tests/test_grounding_eval_quality.py::test_the_harness_reports_a_box_lap_the_model_was_never_shown` | ST-01 | The scripted reply says "Box lap 58" and the test expects 58 to be reported as a number the model was never shown. With the changed recommendation it is no longer reported (the failing assertion is `row["ungrounded"] == ["58"]`). | Choose the lap from the plan: the first lap in `range(1, total_laps)` that appears in no shown plan. |
| `tests/test_strategy_event_costs_and_scoring.py::test_forecast_wet_plan_keeps_waiver_conditional_and_radio_honest` | ST-01 | A frozen fuzz scenario (303), recorded as `SOFT → SOFT → WET`. The recommendation in that state changes; it still fits a wet compound, but `compound_rule["conditional_on_future_wet_use"]` is now false, which is the failing assertion. | Freeze the property, not the plan: take the first plan in `plans` whose dry compounds number fewer than two and that fits a wet compound, and assert `conditional_on_future_wet_use` and the "only if" wording on **that** plan through `_compound_rule` and the instruction builder. |

To reproduce the attribution: apply the patch, then run the five strategy
tests with `PITWALL_STRATEGY_LATER_STOP_TOLERANCE_S=1000000`, which restores
the old tie-break. Four pass; the Hungaroring test still fails, which is the
rival change.

## The repository's own harnesses, before and after

`python -m tools.strategy_fuzz --scenarios 120 --seed 7` — arithmetic,
consistency and legality checks on randomised races: 120 scenarios, 0
errors, 0 warnings at `1f35a7e`, and the same with the whole patch.

`python tools/strategy_validation.py --source <tree> --output <file>` — the
independent benchmark against an exact oracle, 24 frozen worlds, identical
evaluator and manifest checksums for every run:

| Group | Worlds | Median regret, before → after | 90th percentile, before → after |
| --- | --- | --- | --- |
| all | 24 | 13.946 s → 4.726 s | 61.531 s → 17.793 s |
| training | 6 | 0.0 → 0.0 | 1.434 → 1.434 |
| held-out | 18 | 31.094 → 11.373 | 69.139 → 23.411 |
| linear | 4 | 1.434 → 1.434 | 10.794 → 3.139 |
| finite sets | 4 | 0.0 → 0.0 | 0.132 → 0.132 |
| traffic | 4 | 3.574 → 0.429 | 9.141 → 1.451 |
| quadratic | 4 | 37.882 → 24.947 | 49.315 → 36.624 |
| wear cliff | 4 | 24.28 → 11.373 | 31.868 → 13.003 |
| weather | 4 | 72.032 → 16.5 | 94.145 → 17.793 |

Physically impossible recommendations 0 → 0, contract failures 0 → 0, 95th
percentile runtime 166 ms → 166 ms, closed-loop failures 0 → 0. No world's
regret rises by more than 0.5 s.

Attribution, from three further runs: the wet-model, what-if and guard
changes alone leave every figure at its baseline (the benchmark's worlds do
not exercise the forecast); the whole patch with
`PITWALL_STRATEGY_LATER_STOP_TOLERANCE_S=1000000` also reproduces the
baseline exactly. The improvement is ST-01.

## What to check after rewriting it properly

1. Both probes with their gates.
2. The whole suite, with the six tests above rewritten in the same pull
   request as the item that moves them.
3. `python -m tools.strategy_fuzz --scenarios 400 --seed 7`: still a clean
   pass.
4. `python tools/strategy_validation.py` for the baseline and the candidate
   trees; the figures above or better, no world worse. Record the comparison
   under `docs/strategy-validation-results/` as earlier strategy changes
   did.
