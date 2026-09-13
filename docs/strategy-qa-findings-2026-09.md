# Strategy QA findings and release record

Baseline: v4.9.8, `daa029ad41ce29c101254e54a53ade5dbaf8b852`.
All experiments in this review use synthetic telemetry, synthetic physical worlds, or read-only captures produced by earlier synthetic QA. No user database, personal telemetry, credentials, or live provider was used.

The 72-case scenario specification was committed before implementation (`c3229ed`). The coverage ledger distinguishes tested behavior from partially supported or unavailable evidence. Neither scenario count nor Monte Carlo sample count establishes real-game prediction accuracy.

## New reproduced findings

| Finding | Observed failure and expected behavior | Regression evidence |
|---|---|---|
| Finite physical tyres | Fitted tyres were offered as spares; repeated stints reused one fresh set; MEDIUM-to-MEDIUM replacements disappeared. Each future stop now reserves a distinct available set with its reported wear and remaining life. | `test_strategy_inventory.py` |
| Search feasibility | A 12-lap remainder with lives 2+5+5 and a 36-lap remainder with lives 3+11+11+11 have executable finishes that the released search omitted. | Inventory tests and independent exact finite-set oracle |
| Exact driver requests | A safe explicit three-stop schedule between sampled search laps vanished from consideration. Requested schedules now receive a bounded exact evaluation; unsafe requests retain wear, life or inventory explanations. | `test_race_plan_ranking.py` |
| Proposed grid tyre | A proposed SOFT start was assigned to the fitted MEDIUM physical set, then the only SOFT was reused at a later stop. | Inventory/grid reservation tests |
| Player pace reference | Five future laps from `90 + 0.1*age` at ages 11–15 total 456.5 seconds. The release predicts 460.25 by charging observed tyre age again. | `test_strategy_pace_reference.py` |
| Rival pace reference | A median of earlier laps was priced as current-age pace; new tyres inherited old-stint observations. The matching linear rival oracle improves from 455.0 to 456.5 seconds. | `test_strategy_rival_pace_reference.py` |
| Physical-set pace delta | A packet delta of −1 second against the fitted set was added after that age advantage was already modeled: first fresh lap 89.1 rather than 90.1 seconds. | `test_strategy_pace_reference.py`; set-pace scenario addendum |
| Pit-event pricing | A present neutralisation discounted later stops too; red-flag free changes also made subsequent racing stops free. Ending events could be hidden by an older broad status. | `test_strategy_event_costs_and_scoring.py` |
| Pit-loss evidence | Raw lane time was treated as net loss without a matching main-track traversal measurement. The corrected result retains an explicitly estimated circuit prior. | Event-cost tests |
| Field and pit cycles | A P18 player with two observed rivals became P3 when the roster was absent. Later rival pit stops were treated as positions requiring overtakes. Retired cars increased pit-exit losses. | Event-cost/field tests |
| Weather chronology | Rain arriving late in a lap affected the whole lap; recomputation restarted forecast countdowns; another session's forecast could contaminate the current race. | `test_strategy_weather_evidence.py` |
| Weather/learning evidence | Neutralised laps were read as rain evidence; completed laps took the newly fitted compound; progressive wetting was learned as dry tyre degradation. | Weather-evidence tests, persisted wet-wear learning/reopen test |
| Uncertainty | Learned current tyres falsely reduced uncertainty for an unobserved future compound; no-stop uncertainty changed with pit loss; different plan labels received different random draws. | `test_strategy_uncertainty.py` |
| Held recommendations | An infeasible displayed plan could be re-held, dropping its warning and regaining confidence. Host-clock delays also changed holding behavior between equivalent replay speeds. | `test_strategy_hold_feasibility.py`, event-clock tests |
| Championship and UI | An illegal P1 plan still showed 25 earnable points; infeasible plans displayed ordinary finish predictions. Adoption could accept feasible-but-illegal plans. | `test_championship_projection_validity.py`, actual shipped JS DOM tests |
| Observed versus planned use | A future wet stint appeared as an already earned waiver; SOFT→SOFT→WET was spoken as an immediate second-compound change for SOFT. | Event-cost/scoring tests and observed-rule DOM test |
| Pending finish penalties | Classification did not consistently include the current unserved time penalty. Player/rival clocks, probabilities and what-if results now add it once, without treating a classification gain as an on-track pass. | `test_strategy_finish_penalties.py` |
| Cut-call arithmetic | Undercut and overcut calls lacked a shared response window tied to feasible physical stints. Independent arithmetic now verifies signed gap, stop loss, fresh/old laps and traffic, with explicit rival-response assumptions. | `test_strategy_cut_tools.py` |
| Classification runtime | Repeated sample-by-rival calculations dominated avoidable strategy work. Vectorization preserves every complete output in the 24-world comparison and scalar boundary semantics. | `test_strategy_classification_vectorization.py`; performance report |
| Capture rotation | During awaited catalog registration, all 202 test datagrams were rejected silently while queue-drop and write-error counters stayed zero; the no-rotation control persisted 208/208. | Deterministic barrier probe and runtime blocker report; final fix evidence recorded separately |

Exact first-failure values, fixture assumptions and remaining limitations are in the individual scenario addenda. These are local candidate fixes until a tested release is published.

## Existing issues kept separate

- [#37](https://github.com/scottsart1/Pit-box/issues/37): Sprint points now use 8–1 consistently; ordinary short game races retain the Race table. The telemetry does not establish every curtailed-event scoring bracket.
- [#36](https://github.com/scottsart1/Pit-box/issues/36): v4.9.8 already corrected atomic projection copying. This review adds the distinct feasibility/qualification hold checks above.
- [#38](https://github.com/scottsart1/Pit-box/issues/38): the released rain-probability fix is retained; this review addresses timing and evidence separately.
- [#39](https://github.com/scottsart1/Pit-box/issues/39): implausible corner metrics remain an existing upstream limitation for defence/coaching. This strategy review does not claim that issue resolved.
- Archive/lifecycle #33–35 and radio/diagnostic #40–41 were reviewed for overlap; no duplicate issue was created for them.

GitHub issue search was refreshed during this review. Creating the strategy branch and a new findings issue both returned HTTP403, `Resource not accessible by integration`. This file preserves the findings for publication; no remote issue number or pushed branch is claimed.

## Independent evaluation

Use `strategy-independent-validation-final.md` and the committed comparison JSON. The same evaluator, frozen worlds, observations and exact oracles are applied to both baseline and candidate. Two adapter mistakes were corrected openly and both revisions rerun: EA remaining-life versus maximum-stint semantics, and end-of-lap boxing versus beginning-of-lap oracle actions.

Verified primary model result at `a71c062`: all 24 candidate recommendations are physically executable, versus 20 baseline recommendations. In the 20 jointly executable worlds, four improve and 16 are unchanged. The paired median regret is unchanged; feasibility gains must not be presented as broad pace calibration. Weather, nonlinear wear, unobserved rival response and removed-set refitting retain explicit limitations. The later classification optimization preserves all 24 complete output hashes and all 30 sequential decisions; runtime is measured separately.

## App/storage gate

The same fixed synthetic capture reproduced SQLite corruption in both v4.9.8 and the candidate on workspace overlay storage, including a separate stock Python/SQLite runtime. Two candidate replays at recorded speed and one at half speed passed on tmpfs. These controls support a storage-environment association, not a proven cause, a persistence fix, or evidence of Windows behavior. Tmpfs is not a durable production remedy.

Independent packet counts also exposed reception deficits despite zero reported handler drops: the slower control parsed 29,294 of 31,930 sent frames. A deterministic probe separately proved the capture-rotation gap. Reception, parser errors and capture retention are therefore measured independently; a passing live display or zero reported drops is not a lossless-delivery claim. Detailed input hashes, controls, forensic results and limitations are in `strategy-sqlite-runtime-blocker-20260913.md`.

The environment reports Python 3.12.14 and SQLite 3.53.1. [SQLite's corruption guide](https://www.sqlite.org/howtocorrupt.html) informed controlled checks for raw file access, locking and concurrent maintenance; it does not diagnose these particular files.

## Publication requirements

The candidate must pass final integration tests, actual app UDP/capture/session/reopen checks, database integrity, and the Windows installed-artifact workflow. The workflow now enables a sustained 25-lap telemetry phase, retained strategy snapshots, complete SQLite integrity checks, restart/read-back and uninstall data retention. Its portable tests and Linux source-helper pass validate the gate's contract, not the Windows artifact. No Linux source run substitutes for the Windows install/start/UDP/persist/restart/uninstall/data-retention gate. GitHub currently denies writes, so the required Windows run and publication cannot be initiated through this integration. Publishing remains blocked until the observed failures and access/gate requirements are resolved.
