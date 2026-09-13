# Strategy scenario coverage ledger

This accounts for all **72 scenarios** in the specification frozen at
`c3229ed`: D01–D31, W01–W24, and R01–R17. It is a coverage review, **not a
claim that all 72 scenarios passed** or that the model is calibrated to real
races. The ledger includes the integrated player/rival/set pace references,
championship validity, exact driver overrides, conditional cut tools and
pending-penalty corrections through `a71c062`. Execution results identify
their own tested revisions in the reports linked below.

**Covered** means a targeted automated test exercises the stated behavior or
its explicit mathematical invariant, and the responsible worker reports a
passing focused suite. It does not mean every possible input is covered.
**Partial** means the cited evidence addresses only part of the sequence,
boundary, or output contract. **Unsupported** means the required observation
or modeling capability is absent; nearby tests cannot establish the promised
behavior. Final build, installed-app, and real-game checks are recorded
separately as **deferred/pending**, rather than being counted as model passes.

The test references below are repository-relative paths. Function names are
included where a broad file name would otherwise overstate coverage.

## Dry racing and finite tyres

| ID | Coverage | Actual evidence and remaining boundary |
|---|---|---|
| D01 | Partial | `tests/test_strategy_validation.py::test_oracle_exact_stop_arithmetic` and the independent linear worlds establish payback arithmetic. The exact ten-lap, 8-second gain versus 22-second pit-loss case has not yet been recorded through production `compute`. |
| D02 | Partial | Independent linear worlds and `tests/test_strategy_pace_reference.py::test_actual_compute_extrapolates_known_linear_stint` validate cost components. The exact 25-lap, 50-second gain versus 20-second loss comparison is not a recorded production oracle. |
| D03 | Partial | `tests/test_strategy.py::test_spa_high_personal_wear_rejects_long_soft_finish` checks the limiting per-wheel wear model; adversarial tests bound wear and expose the limiting corner. The exact 92% single-wheel versus 45% other-wheel emergency sequence is not a separate recorded test. |
| D04 | Covered | `tests/test_strategy_inventory.py::test_short_horizon_can_need_two_stops`; independent oracle also finds necessary short-distance multi-stop finishes. |
| D05 | Covered | `tests/test_strategy_inventory.py::test_same_compound_replacement_survives_other_available_compounds` and `test_driver_plan_can_request_a_distinct_set_of_the_same_compound`. |
| D06 | Covered | `tests/test_strategy_inventory.py::test_inventory_ids_are_not_reused_as_fresh_tyres`; independent scorer rejects duplicated physical-set identities. Refitting a removed set later is explicitly unsupported, not modeled as fresh stock. |
| D07 | Covered | `tests/test_strategy_inventory.py::test_two_medium_spares_keep_the_second_sets_reported_wear` verifies distinct IDs, 0%/35% initial wear, and unchanged result after record-order reversal. Same invariant applies to two HARD sets. |
| D08 | Covered | `tests/test_strategy_inventory.py::test_exhausted_inventory_does_not_invent_fresh_sets` and `test_unknown_stock_is_explicitly_conditional`; held-plan qualification regressions preserve the unknown-stock caveat. |
| D09 | Covered | `tests/test_strategy_inventory.py::test_fitted_available_set_is_not_a_fresh_spare`; proposed-grid set reservation has separate regression coverage. |
| D10 | Covered | `tests/test_strategy.py::test_dry_race_requires_two_distinct_compounds` and `tests/test_strategy_adversarial.py::test_illegal_stay_out_never_beats_a_legal_plan`. |
| D11 | Covered | `tests/test_strategy_adversarial.py::test_unservable_compound_rule_is_spoken_not_hidden`. `tests/test_championship_projection_validity.py` in `ccb710c` additionally preserves the illegal-finish caveat through compute, state, and the public championship tool. |
| D12 | Covered | `tests/test_extraction_3_4.py::test_two_compound_rule_does_not_apply_to_a_sprint`; Sprint classification and points have separate parameterized tests. |
| D13 | Covered | `tests/test_strategy.py::test_wet_use_waives_dry_compound_requirement`, grid-swap regressions in `test_race_regressions_4_9_3.py`, and probability/observed-weather separation in `test_rain_probability.py`. |
| D14 | Covered | `tests/test_pit_call_sanity.py::test_recovery_is_bounded_by_what_the_time_advantage_can_buy` compares easy/hard passing and shorter remainders; no-advantage and maximum-recovery tests prevent free overtakes. Traffic response remains a heuristic, not calibrated passing physics. |
| D15 | Covered | `test_strategy_event_costs_and_scoring.py::test_renaming_and_permuting_the_field_does_not_change_strategy` improves every rival by one second and checks each matched candidate cannot gain finishing positions. Equal-field age-reference oracles are in `test_strategy_rival_pace_reference.py`. |
| D16 | Covered | The field permutation test changes names, indices, player identity and array order through actual compute, preserving stop choices, clocks, points and distributions. |
| D17 | Partial | `tests/test_strategy_event_costs_and_scoring.py::test_retired_cars_do_not_cost_rejoin_positions` covers retired/DNF/disqualified cars. Adding a distant noncompetitive classified car is not independently tested across all ranking/probability outputs. |
| D18 | Covered | `test_strategy_event_costs_and_scoring.py::test_five_seconds_more_pit_loss_is_counted_once_per_stop` compares actual matched zero/one/two-stop candidates and separates changed traffic from the exact +5 seconds per stop. |
| D19 | Partial | `tests/test_strategy_event_costs_and_scoring.py::test_raw_lane_duration_is_not_a_measured_net_pit_loss` verifies honest fallback when main-track traversal is absent. The planner currently uses labeled circuit priors; it does not independently measure the stipulated 27−9-second traversal pair. |
| D20 | Covered | `tests/test_strategy_event_costs_and_scoring.py::test_only_current_stop_receives_safety_car_discount` checks actual two-stop candidate totals: current reachable SC discount, later green loss. |
| D21 | Covered | The parameterized current-stop ledger test includes actual red-flag two-stop candidates: zero seconds for the suspension change and 24 seconds for a later green stop. Inventory/zero-lap removal tests also pass. Actual game entitlement remains an assumption requiring game validation. |
| D22 | Covered | `tests/test_strategy_event_costs_and_scoring.py::test_specific_ending_event_outranks_lingering_general_status` covers SC/VSC ending against lingering full/virtual status. |
| D23 | Covered | `tests/test_strategy_benchmarks_2026.py::test_britain_late_sc_protects_track_position` verifies the safe/legal late-neutralisation case. The likelihood of a neutralised finish remains an assumption. |
| D24 | Partial | `test_strategy_weather_inventory.py` now verifies actual compute for urgent WET/INTER changes with entry reachable or already passed, preserving a complete feasible schedule. No recorded game sequence straddles the physical entry threshold through UDP and the UI; circuit thresholds remain approximations. |
| D25 | Partial | `tests/test_strategy_finish_penalties.py` covers current unserved time penalties once in player/rival clocks, probabilities, held outputs and what-if comparisons. Cleared counters and classification gains are separate from physical passes. Unknown repair, drive-through and stop-go service durations remain explicitly unmodeled. |
| D26 | Covered | `tests/test_strategy_event_costs_and_scoring.py::test_rival_age_resets_after_every_projected_stop` uses an independently written age sequence and counts every pit cost. Rival response and fresh-set pace remain estimates. |
| D27 | Covered | `tests/test_strategy_event_costs_and_scoring.py::test_pit_cycle_recovery_does_not_require_on_track_overtakes` verifies six later rival stops restore positions even at maximal passing difficulty. This is conditional on those rival stops occurring. |
| D28 | Covered | `tests/test_strategy_cut_tools.py` exercises actual shared response-window arithmetic: undercut +2.8 seconds becomes −2.2 with five seconds of traffic. The tool consumes feasible main-plan physical stints and discloses a low-confidence rival-response assumption; real-game response calibration remains open. |
| D29 | Covered | `tests/test_strategy_cut_tools.py` verifies overcut +2.5 seconds becomes −1.5 with the slower extra lap, and changes second-for-second with target gap. Guards decline unsupported weather, neutralisation, stock and partial-stop situations. Rival out-lap behavior remains an estimate. |
| D30 | Covered | `test_strategy_inventory.py::test_nondominated_faster_worn_set_is_preserved` retains faster usable sets. Physical-set pace oracles in `test_strategy_pace_reference.py` validate fitted-reference deltas without duplicating compound, age or wear cost. Unknown spare age and game-estimator accuracy remain disclosed. |
| D31 | Covered | `tests/test_strategy_inventory.py::test_three_stop_search_includes_urgent_first_stop` covers the early three-stop resource requirement. Longer-horizon two/three-stop search remains explicitly sampled; this is not an exhaustive optimizer claim. |

## Weather and learning

| ID | Coverage | Actual evidence and remaining boundary |
|---|---|---|
| W01 | Covered | `tests/test_rain_probability.py::test_probability_cannot_change_weather_equilibrium_or_tyre` and `test_current_reading_and_stop_ignore_all_forecast_probability_fields`. |
| W02 | Covered | `tests/test_rain_probability.py::test_low_probability_heavy_rain_keeps_full_conditional_wetness` and `test_costs_weight_heavy_rain_and_dry_outcomes_after_pricing_each`. |
| W03 | Covered | `tests/test_strategy_weather_evidence.py::test_rain_in_last_six_seconds_does_not_wet_an_entire_two_minute_lap` verifies partial first-lap exposure with independent timing. |
| W04 | Covered | `tests/test_strategy_weather_evidence.py::test_weather_arriving_at_the_finish_cannot_change_the_race` and beyond-finish tests in `test_rain_probability.py`/`test_rain_model.py`. |
| W05 | Covered | Surface drying-rate/memory tests in `test_rain_model.py` and `test_rain_probability.py::test_changing_future_chances_preserves_marginals_and_surface_memory`; dry-reference guard rejects clear sky over a still-wet surface. Physical drying rates remain heuristic. |
| W06 | Covered | `tests/test_rain_model.py::test_a_drying_track_reaches_for_slicks_while_it_is_still_spitting`, plus dry-line driver-report tests in `test_rain_radio.py`. |
| W07 | Covered | `tests/test_rain_model.py::test_a_lap_lost_in_one_sector_is_a_mistake_not_rain` and `test_rain_strategy.py::test_a_reported_mistake_keeps_a_slow_lap_out_of_the_weather_read`. |
| W08 | Covered | `tests/test_strategy_weather_evidence.py::test_recorded_interruption_is_not_rain_even_after_green_returns` and `test_field_neutralisation_cannot_outvote_dry_weather`. Older unobserved rival interruptions cannot be reconstructed. |
| W09 | Covered | `tests/test_strategy_weather_evidence.py::test_a_completed_lap_keeps_its_compound_after_a_pit_stop`. |
| W10 | Partial | Sector-outlier and neutralisation gates exclude some confounding. They do not directly prove that an isolated traffic-trapped car cannot affect the weather estimate; correlated traffic trains are an explicit remaining limitation in the weather report. |
| W11 | Covered | `tests/test_rain_model.py::test_a_split_field_measures_the_crossover_instead_of_guessing_it` and the real strategy path in `test_rain_strategy.py::test_the_field_on_wets_going_quicker_is_what_calls_the_stop`. Skill-normalization assumptions are synthetic. |
| W12 | Covered | Marginal-call, driver-report, and stale-report tests in `test_rain_model.py`; `test_rain_radio.py` verifies capture of track reports separately from tyre complaints. This tests bounded use/decay, not a learned reliability estimate for driver reports. |
| W13 | Covered | `tests/test_tyre_learning.py::test_fuel_burn_does_not_hide_degradation` recovers a known 0.20 rather than raw 0.14 slope using the explicit 0.030 s/kg prior. The same arithmetic invariant covers the specification's 0.12/0.06 example. |
| W14 | Covered | `tests/test_tyre_learning.py::test_separate_runs_do_not_create_cross_stint_pace_slopes`. |
| W15 | Covered | Parameterized exclusions in `tests/test_tyre_learning.py::test_unusable_laps_cannot_teach_zero_or_corrupt_wear`; history ignores time trials; neutralisation/pit flags survive persistence. Cold first out-lap exclusion is covered, but prolonged warm-up is tracked separately as W22. |
| W16 | Covered | `tests/test_strategy_weather_evidence.py::test_wetting_under_unchanged_sky_label_does_not_become_intrinsic_degradation` and persisted wet-pace exclusion/wear-retention test. |
| W17 | Covered | Unrun-compound inference tests in `test_strategy.py`, `test_tyre_learning.py::test_unmeasured_final_compound_cannot_inherit_high_confidence`, and `test_strategy_uncertainty.py::test_unobserved_future_stint_cannot_borrow_current_stint_certainty`. |
| W18 | Covered | `tests/test_strategy_uncertainty.py` compares unknown versus observed future-stint evidence and separates Monte Carlo draws from observations. Narrowing uncertainty is a heuristic model response, not empirical calibration. |
| W19 | Partial | Parameterized invalid/nonfinite/reset/out-of-range wear exclusions and adversarial finite-output tests cover most corruption. The specific single 30-point jump has a rejection rule but no separately recorded regression in the reviewed suite. |
| W20 | Covered | Frozen `docs/strategy-validation-manifest.json`, independent nonlinear/weather worlds, oracle/exhaustive-agreement tests, and baseline report. Candidate comparisons must use identical corrected adapters/worlds; real driver/track holdout accuracy remains deferred. |
| W21 | Covered | `tests/test_strategy_weather_evidence.py::test_forecast_ages_in_session_time_instead_of_restarting_its_countdown` and `test_udp_forecast_preserves_capture_time_and_ignores_other_sessions`. |
| W22 | Unsupported | `tests/test_features_3_5.py::test_cold_tyre_penalty_only_hits_fresh_stints` verifies a fixed fresh-stint cost, not temperature-attributed warm-up through ages 2/3. No validated historical temperature-based exclusion establishes this extended sequence. |
| W23 | Partial | `tests/test_strategy_pace_reference.py::test_duplicate_observation_is_not_three_independent_laps` protects the observed reference. `test_tyre_learning.py::test_history_recovers_true_pace_and_ignores_newer_time_trials` excludes the live session from historical evidence. Duplicating every row through all wear and slope-learning paths is not fully exercised. |
| W24 | Covered | `tests/test_tyre_learning.py::test_single_live_sample_cannot_replace_established_history` and `test_live_learning_blends_then_adapts_to_persistent_change`. |

## Sequential decisions and output contracts

| ID | Coverage | Actual evidence and remaining boundary |
|---|---|---|
| R01 | Partial | `tests/test_strategy_uncertainty.py` verifies race-time confirmation at radically different host speeds, parsed-header propagation, pause, and rollback. Replay foundations test speed/pause/step. Whole-application 0.5×/1×/accelerated checkpoint equality is not yet an attached result. |
| R02 | Covered | `tests/test_strategy_stability_projection.py` observes recompute→state→tools→SQLite coherence while a call is held; `test_race_regressions_4_9_3.py` verifies sustained-winner confirmation and shortlist-independent holds. |
| R03 | Partial | `tests/test_strategy_hold_feasibility.py` prevents illegal/infeasible holds and loss of warnings; wet-stop and high-wear tests cover fresh computation. The complete held-call→new emergency→reachable pit action sequence has not been recorded across every trigger. |
| R04 | Covered | Same-compound physical replacements in `test_strategy_inventory.py`, complete-plan validation in `test_race_plan.py`, and actual planned-stop rebasing in `test_race_plan_ranking.py`. |
| R05 | Covered | `tests/test_race_plan_ranking.py` verifies exact unsampled safe three-stop requests, unavailable repeated physical stock and an unsafe long SOFT finish. Unsafe requests receive wear/life/inventory explanations; safe exact schedules are honored. UI adoption also requires feasibility and legality. |
| R06 | Partial | `test_reliability.py::test_new_session_uid_resets_stale_session_state_but_keeps_ptt`, same-UID restart tests in `test_state_v42.py`, assembler flashback invalidation, and clock epoch tests cover components. UID→0→same UID and every race-local cache together are not one observed regression. |
| R07 | Partial | Stale suppression, packet duplicate/reorder/flashback and archive invalidation tests pass. Capture rotation now preserves all directly submitted frames in the paired barrier probe. The fixed-input app run at `eebfac4` records every parsed frame on tmpfs, but receives only 25,470/31,930 input frames despite zero reported drops. Individual packet freshness, parser errors, transport loss and recording are separate measures; full reconnection behavior remains incomplete. |
| R08 | Partial | Qualifying exclusion, Sprint mode extraction, and race legality/scoring are tested. An otherwise-identical five-mode full-planner matrix, including practice/time trial and transitions, is not separately recorded. |
| R09 | Covered | `tests/test_strategy_event_costs_and_scoring.py::test_classification_and_distribution_use_session_points` covers specified Sprint/Race positions. Championship expected/modal points have additional tests. |
| R10 | Covered | `tests/test_strategy_event_costs_and_scoring.py::test_normal_game_distance_does_not_reduce_race_points` verifies normal game distances retain Race scoring. Actual curtailed-event scoring is not inferred and remains unsupported without authoritative event evidence. |
| R11 | Covered | `tests/test_strategy_event_costs_and_scoring.py::test_championship_uses_finish_not_rejoin_and_keeps_expected_value`. `ccb710c` corrects the older fixture to distinct rejoin P8/finish P3 and propagates invalid-plan caveats. |
| R12 | Partial | Distribution/points contract validation in `tools/strategy_validation.py`, held-snapshot arithmetic tests, and uncertainty tests cover means/aliases and selected plans. Final all-candidate fuzz/application checks and invalid-plan/public-tool integration must be attached for the release revision. Probabilities remain uncalibrated model estimates. |
| R13 | Covered | `tests/test_strategy_uncertainty.py` verifies per-stint evidence, missing pace versus wear-only counts, no zero-stop pit variance, per-stop costs, and explicit uncalibrated provenance. |
| R14 | Partial | Common-draw candidate-label comparisons, sample-prefix reproducibility, and order-independent world generation are tested. Complete enumeration/shortlist order permutations through all ranking/override paths are not a recorded production test. |
| R15 | Partial | Inventory worker measured a 78-lap/24-car/20-set stress case; reference extraction runs once per compute. Fuzz and source-app harnesses exist. Final repeated-run latency, queue loss, memory growth, and persistence behavior require the integrating run's evidence; helper timing alone is insufficient. |
| R16 | Partial | Unknown inventory, unknown active-car count including P18 with a partial roster, missing pace, and unmatched reference context have targeted tests. Unknown circuit and distance together across all instructions/outputs are not one complete verified sequence. |
| R17 | Partial | Last-lap wet-stop/no-stop, red-flag replacement, retirement suppression/queue clearing, and garage guardrails are tested. Checkered, formation, suspension, and resumed-racing transitions together need an observed app/game sequence. |

## Evidence provenance and release gates

Focused worker reports are in `docs/strategy-uncertainty-observations.md`,
`docs/strategy-weather-validation-20260913.md`, and
`docs/strategy-pace-reference-scenarios.md`. Their test counts refer to their
own listed revisions and focused runs, not the final combined product. The
independent benchmark definition and baseline are in
`docs/strategy-validation-manifest.json` and
`docs/strategy-independent-baseline-2026-09.md`. An adapter correction does not
justify changing physical worlds or hidden seeds after observing a failure.

The following gates apply regardless of the table's coverage labels:

1. The corrected championship output and matched player/rival pace references
   are integrated. Their regressions pass in the complete 1,529-test run at
   `35b33c6`, including the equal-field oracle that exposed a ranking reversal.
2. Independent evaluation at `a71c062` finds 24/24 executable recommendations,
   versus 20/24 in v4.9.8, with no contract violations or candidate regression
   among the 20 common feasible worlds. This is synthetic validation, not
   real-race calibration; all worlds, including remaining regret, are retained.
3. Attach full-suite, same-evaluator candidate-versus-baseline, bounded fuzz,
   isolated app/UDP/capture/restart, latency, and data-preservation evidence for
   the final candidate. The presence of `tools/strategy_app_smoke.py` is not
   evidence that its latest run passed.
4. Run the Windows installed-artifact gate against the actual release
   candidate. Earlier v4.9.8 installer results do not validate this model
   revision. Keep installer-first/site-second publication and production hash
   verification as separate release actions.
5. Keep clean-Windows first-run UI, live game pit-entry timing, audio/provider
   narration, real-game red-flag behavior, and actual driver forecast accuracy
   explicitly deferred unless observed. No synthetic test establishes these.

The partial/unsupported rows identify limits on the requested scope; they are
not automatically confirmed defects. Conditional undercut/overcut arithmetic
now has explicit numerical oracles, while its rival-response assumptions remain
uncalibrated. Repair-duration pricing, extended temperature-attributed warm-up
learning and whole-app replay equivalence are not fully validated.

## Integration evidence

- `strategy-independent-validation-final.md` records all 24 physical worlds,
  the two sequential runs, exact evaluator/input/output identities and timing.
- `strategy-qa-findings-2026-09.md` records reproduced defects, overlap with
  existing GitHub issues and publication status.
- `strategy-sqlite-runtime-blocker-20260913.md` records byte-identical source
  replays, failed overlay storage controls in both release and candidate,
  passing tmpfs controls and independently measured reception/capture gaps.
- The Windows workflow now includes sustained telemetry and full database
  integrity checks. Its portable tests and source-helper run do not establish
  that the Windows installed artifact passed.

This ledger is not a release approval. GitHub denied branch/issue creation with
HTTP403, and no candidate Windows artifact or production deployment is claimed.
