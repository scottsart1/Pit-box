# Strategy refinement: scenarios and acceptance criteria

Baseline: released v4.9.8, `daa029ad41ce29c101254e54a53ade5dbaf8b852`.
Written before implementation on 2026-09-13. This is a specification, not a claim that the cases pass.

## Decision contract

The planner must first distinguish what is known, estimated, and unavailable. It must offer physically executable tyre plans, satisfy the game's applicable rules, and explain unavoidable failure. Among executable plans it should price remaining race time, traffic and uncertainty consistently. It must not convert an uncertain forecast, unknown inventory, or an old commitment into a confident fact.

Expected outcomes below use hard constraints, arithmetic, and counterfactual comparisons. An exact box lap is required only when independent inputs make one uniquely preferable. Closely matched plans may differ within a declared decision margin. A low-probability outcome is not itself a bug; a mispriced, impossible, inconsistent, or unsupported claim is.

Game facts take precedence over assumptions imported from Formula 1. A normal 25% or 50% game race is not automatically a shortened FIA race. We must not infer reduced points, closed pit lanes, compulsory stop counts, or a red-flag tyre-change entitlement from telemetry that does not establish them.

## Dry racing, pit costs, and finite tyres

| ID | Scenario and concrete inputs | Expected observation |
|---|---|---|
| D01 | P4, 10 laps left, legal hard tyre can finish; fresh tyre saves 0.8 s/lap, stop loses 22 s. | No speculative stop: the maximum 8 s recovery cannot repay 22 s. |
| D02 | Same field; 25 laps left, fresh tyres save 2 s/lap with sustainable wear, 20 s stop. | A stop is evaluated as a real alternative; independent integration establishes benefit before traffic. |
| D03 | Current tyre at 92% on one wheel, 8 laps left; other wheels at 45%. | Limiting wheel governs feasibility; average wear must not hide failure. |
| D04 | 12 laps left; every available tyre lasts at most 5 laps; first fitted tyre has 2 laps left. | A feasible multi-stop plan must be searchable despite the short remaining distance. |
| D05 | Already used HARD and MEDIUM; current MEDIUM at 75%, a distinct new MEDIUM and a worn HARD available. | New MEDIUM is a valid one-stop candidate. Same compound does not mean same physical set. |
| D06 | Exactly one unused HARD set; candidate M-H-M-H, with long H stints. | Never charge both H stints as the same fresh set. Allocate physical sets or explicitly reject unsupported reuse. |
| D07 | Two HARD sets: 0% and 45% wear; candidate uses both. | Price the second set's actual wear and remaining life; do not duplicate the best set. |
| D08 | Tyre-set packet absent versus packet present with every spare unavailable. | Unknown inventory and known exhausted inventory differ. An exhausted inventory must not invent fresh tyres. |
| D09 | Fitted set is listed as available by the packet; another set of that compound is not. | Do not treat the currently fitted set as a separate fresh replacement. |
| D10 | Dry Race has used only HARD; 5 laps remain, usable MEDIUM exists. | Require an executable compound change; distinguish legality from pace benefit. |
| D11 | Same state, no eligible spare. | State that no known legal finish is possible. Do not advertise a normal compliant plan. |
| D12 | Sprint has only used HARD; sustainable 8-lap remainder. | Do not impose the Race two-compound requirement. |
| D13 | Wet tyre was actually used earlier, now dry versus rain merely forecast. | Actual use can waive the compound requirement; a prediction alone cannot. |
| D14 | P2 rejoins a train of 6 cars at 0.6 s gaps, 0.2 s/lap faster, 6 laps left; compare easy and difficult passing tracks. | Avoid promising recovery of all lost positions; harder passing cannot improve that recovery. |
| D15 | Same gaps and tyres but rival pace improves by 1 s/lap. | Our projected finishing position must not improve solely because rivals became faster. |
| D16 | All driver names and indices are permuted with player identity/gaps preserved. | Physical outcomes remain unchanged; index order is not pace evidence. |
| D17 | Add a retired car or distant noncompetitive car to the field. | No artificial traffic loss or position gain from a nonexistent competitor. |
| D18 | Compare +5 s green pit loss while holding all evidence fixed. | Cost of the same one-stop plan rises 5 s; two-stop plan rises 10 s. |
| D19 | Actual pit-lane duration is 27 s, parallel track traversal 9 s. | Net loss is 18 s if both are measured; do not label raw lane duration as measured net loss. |
| D20 | Current SC gives 11 s loss versus green 24 s; plan stops now and again 15 laps later. | Discount the current opportunity; do not assume the later stop also occurs under this SC. |
| D21 | Current red flag permits a free change; next planned change is during green racing. | Only the suspension change is free; future racing stops keep their cost. |
| D22 | SC/VSC ending phase while status packet still says full/virtual. | Ending signal reduces the opportunity; stale broad status must not override the more specific phase. |
| D23 | SC with 2 laps remaining, safe/legal tyres, compact field. | Account for a possible neutralised finish and irrecoverable track-position loss. |
| D24 | Pit entry just passed, then fresh emergency data arrives. | Earliest executable racing stop is next lap; no instruction to enter a passed pit lane. |
| D25 | Extra stationary repair time or known penalty is added to the same stop. | Additional cost is counted once and explained; tyre changes do not repair fuel shortage. |
| D26 | Rival tyre has 1 lap left; 35 laps remain and each later set lasts 10 laps. | Rival age resets at every predicted stop, not only the first. Four predicted stop costs and four age resets agree. |
| D27 | Rejoin P12; six cars ahead must pit within 5 laps; identical pace and no on-track passing. | Their stops can restore six positions without overtakes. Passing difficulty must not erase pit-cycle gains. |
| D28 | Rival 1.2 s ahead; our new-tyre laps 91+89 s versus their old 92+92 s; add 5 s rejoin traffic. | Undercut margin changes from +2.8 to -2.2 s. Tool verdict and main planner use compatible assumptions. |
| D29 | Rival 0.5 s ahead pits; their out-lap 93 s, our in-lap 90 s versus 94 s. | One-lap overcut margin reverses from +2.5 to -1.5 s; name the assumed rival response. |
| D30 | Two same-compound sets: fresh set is 2 s/lap slower, 5%-worn set is quicker and both can finish. | Keep nondominated alternatives; lowest initial wear is not automatically the cheapest tyre set. |
| D31 | 36 laps remain; fitted set has 3 usable laps, three spares have 11 each. | Search an early three-stop plan; a fixed earliest stop four laps away cannot hide the only feasible finish. |

## Weather, learning, and observed evidence

| ID | Scenario and concrete inputs | Expected observation |
|---|---|---|
| W01 | Actual light rain and identical pace/temperatures; forecast probability changes 20% to 100%. | Current physical wetness is unchanged. Future occurrence weights and uncertainty may change. |
| W02 | Dry track, 20% chance of heavy rain in 5 minutes. | Model a dry/rain mixture, not heavy rain at one-fifth intensity. |
| W03 | 90 s lap, rain starts after 80 s versus after 10 s. | Earlier rain incurs more first-lap exposure; future conditions are not charged for a whole preceding lap. |
| W04 | Rain starts after the finish; current slicks are safe. | No unnecessary wet stop for conditions outside race horizon. |
| W05 | Drying surface remains wet after weather label changes to clear. | Preserve surface memory; do not switch to slicks from the sky label alone. |
| W06 | Light rain label unchanged but a dry line and field pace improve. | Surface evidence can change the crossover with appropriately limited confidence. |
| W07 | One 20 s spin with one abnormal sector among otherwise 90 s laps. | Do not infer that the whole circuit became wetter or tyres degraded catastrophically. |
| W08 | Entire field does 130 s laps under SC instead of normal 90 s. | Neutralisation is not rain evidence. |
| W09 | Latest lap completed on SOFT; INTER is fitted immediately afterwards. | Interpret the completed lap with its actual compound, not the new set. |
| W10 | Only a trapped car slows by 3 s; clear-air cars remain steady. | Traffic-confounded pace must not overwhelm weather evidence. |
| W11 | Drivers on both slicks and inters; similar skill, dry baselines known. | Compound-normalised comparisons inform wetness; raw lap order alone does not. |
| W12 | Contradictory grip feedback and sensor evidence, followed by later clean evidence. | Explain conflict; feedback decays rather than permanently rewriting the model. |
| W13 | 90 s baseline + 0.12 s/age degradation; fuel decreases 2 kg/lap at 0.030 s/kg. | Recover approximately 0.12 s/lap after fuel correction, not the raw 0.06 slope. |
| W14 | Equal tyre behaviour in two stints with different starting fuel and baseline pace. | Fit separate runs; no artificial degradation from cross-stint intercepts. |
| W15 | Append time-trial, qualifying, pit, cold out-lap, invalid lap, SC lap and tyre-reset samples. | Excluded observations do not become race degradation evidence. |
| W16 | Progressive wetting under one unchanged weather label makes each lap slower. | Do not claim confidently measured dry degradation from that confounded slope. |
| W17 | Player has 8 clean MEDIUM laps but has never used SOFT. | SOFT inference remains labelled as inference with lower confidence. |
| W18 | Replace an unobserved future stint with 12 clean representative observations. | Supported uncertainty may narrow; evidence on another compound must not falsely provide that certainty. |
| W19 | Negative, nonfinite or reset wear and a single 30-point wear jump. | Reject impossible learning data; keep output finite and bounded. |
| W20 | Train on selected tracks/drivers/seeds; test an unseen family with different degradation curvature. | Report holdout error/regret; synthetic fit is not real-race calibration. |
| W21 | Forecast received at session time 300 s says rain in 5 min; no update, recompute at 480 and 570 s. | Arrival remains 600 s: 2 min and 30 s away. Recomputations must not postpone the same forecast. |
| W22 | Identical degradation but tyres stay cold through ages 2 and 3; excess times 3, 1.8, 0.8, 0.1, 0 s. | Warm-up is not negative degradation; reveal insufficient evidence where temperatures cannot be attributed to laps. |
| W23 | Duplicate all observations from one stint, then compare with distinct independent stints. | Row count alone is not independent evidence; no live/history double counting. |
| W24 | Current live wear rises persistently from historical 2 to 4 points/lap over 1, 3, 6 and 10 new clean laps. | Adapt progressively; one lap cannot erase history, but sustained evidence cannot be ignored. |

## Sequential decisions, output contracts, and robustness

| ID | Scenario and concrete inputs | Expected observation |
|---|---|---|
| R01 | Same ordered race packets replayed at 0.5x, 1x, and accelerated speed. | Event-equivalent decision checkpoints agree; host CPU speed should not decide the race. |
| R02 | Nearly tied plans alternate by 0.2 s around a committed stop. | Avoid call oscillation; update the held plan's entire projected outcome atomically. |
| R03 | Hold a dry stop plan, then severe actual wetness, tyre failure, or legal deadline appears. | New feasibility/safety evidence breaks the hold and gives the reason. |
| R04 | Driver requests a legal same-compound replacement or explicit multi-stop plan. | Preserve the actual stops and evaluate them; do not discard them as duplicate names. |
| R05 | Driver locks an impossible/unavailable set or an illegal finish. | Explain noncompliance/feasibility rather than calling the request safely honored. |
| R06 | Session UID changes, passes through zero, or same UID restarts after time rollback. | No old race plan, weather, rival projection or tyre history leaks into the new epoch. |
| R07 | Telemetry stops mid-lap then resumes; duplicate and reordered packets injected. | No fresh-looking urgent call from stale data; recover without double-counting history. |
| R08 | Race, Sprint, qualifying, practice and time trial with identical lap numbers. | Only applicable sessions receive race strategy; Sprint scoring differs from Race. |
| R09 | Sprint P1/P7/P8/P9 versus Race P1/P7/P10. | Points 8/2/1/0 versus 25/6/1 consistently in modal, expected and championship outputs. |
| R10 | Normal 25%/50% game race versus independently known curtailed event. | Do not apply FIA reduced-distance points merely from selected game race length. Unknown curtailed scoring is disclosed. |
| R11 | Plan rejoins P12 but projects a P5 finish. | Championship output scores P5, not pit exit P12. |
| R12 | Every returned probability distribution, all percentile/mean aliases, and all selected/held plans. | Probabilities sum to 1 within rounding; mean and expected points agree with that distribution; percentiles are ordered. |
| R13 | Well-learned current HARD followed by an unobserved 20-lap SOFT stint. | The learned HARD must not shrink uncertainty for the unknown SOFT stint. Zero-stop plans have no pit-event variance. |
| R14 | Equivalent plans are generated in a different order. | Reproducible comparison; sampling noise must not create arbitrary plan-order winners. |
| R15 | Full grid, long race, many sets/overrides; repeat recomputation and replay. | Bounded planner work, no unbounded retained state, valid JSON, responsiveness measured before/after. |
| R16 | Missing distance, unknown track, no pace history, partial grid. | Conservative labelled assumptions and lower confidence; no invented measurements. |
| R17 | Final lap, checkered flag, retirement, red flag, formation and resumed racing. | Distinguish possible future action from an already finished or suspended session. |

## Evaluation and release gates

1. Record reproducible baseline inputs and actual outputs for suspected failures before modifying the implementation. Keep existing GitHub issue numbers attached to known defects; separate new findings.
2. Use temporary databases, loopback ports and synthetic captures. Never read or migrate the user's live database. Existing QA captures may be replayed read-only and are still synthetic.
3. Add targeted regression tests for confirmed failures, plus counterfactual/metamorphic tests that do not merely restate implementation choices.
4. Compare baseline and candidate on independently specified arithmetic/finite-resource cases. For closed-loop simulation, the scoring model must not call the production stint simulator. Use shared event seeds and reserved holdout cases; report median/tail regret, impossible-plan counts, decision churn and runtime. Report limitations where the model lacks observable information.
5. Run full project tests, adversarial/random telemetry scenarios, app/UDP/capture/restart checks, and the Windows installed-artifact gate against the actual release candidate. Do not call a Linux source run a Windows installer test.
6. Stop publication on any unresolved regression, impossible recommended plan, contradictory output, data-retention failure, or unpassed required build gate. Publish installer before website and verify the production file digest against the smoke-tested asset.
7. Maintain an explicit coverage ledger: passed, failed, changed and retested, already covered, or not verifiable here. This scenario inventory does not claim exhaustive coverage of every race or real-world prediction accuracy.

## Evidence to add during execution

- Primary protocol/rule sources and distinctions from game assumptions.
- Baseline reproductions and predeclared experiment seeds.
- Implemented changes, metrics, remaining limits, and release evidence.
