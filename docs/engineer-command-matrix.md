# Engineer response validation

Run each command through the real `/api/ask` route on desktop and tablet while
synthetic UDP is running, then repeat the freshness cases by withholding only
the named packet group. Save the response, request, contemporaneous state and
tool results. A provider response passes only when every factual claim agrees
with that evidence; exact wording may vary. Deterministic coverage is in
`tests/test_engineer_command_matrix.py`; paid provider narration needs separate
execution and review.

## Command and expected response

| Case | Command | Expected evidence and response |
| --- | --- | --- |
| Pit plan | `Give me the full pit strategy and its best alternative.` | Recommended stop laps and compounds exactly match `get_pit_strategy`; distinguish the alternative, legality, confidence and change condition. |
| Commit | `Box lap 12 for hards.` | Acknowledge driver constraint, set next lap 12 and HARD, then recompute; never claim the game itself was changed. |
| Clear | `Clear my strategy override.` | Disable both the next-stop constraint and whole-race plan; immediate automatic ranking and response agree. |
| Pre-race agreement | Open proposal, then `Yes, lock it in.` | Save the discussed complete proposal; no unsolicited change to its compounds or stops. |
| Tyres | `What tyres am I on?` | Correct fitted compound, age, limiting wheel and wear; unavailable fields remain unknown. |
| Temperatures | `Give me tyre temperatures in Fahrenheit.` | Convert all four Celsius readings consistently and retain the requested unit. No temperature target for activating an overtaking aid. |
| Pace/fuel | `Do I need to save fuel, and what pace will that cost?` | Negative margin means saving; include the current lap in distance remaining. Cost is a configured planning estimate, not a measured loss. Do not advise a richer fuel mix. |
| Energy | `Battery.` | Current store percentage; 2026 Overtake Mode eligibility/activation from packet 16, independently of ERS deployment mode. Missing flags mean unknown. |
| Aero | `Active aero.` | Mode 0 means Cornering Mode and mode 1 means Straight Line Mode; report observed availability independently of Overtake Mode. |
| Legacy energy | `With low battery, can I use DRS?` | DRS availability depends on its observed eligibility; low ERS charge does not disable an aerodynamic device. |
| Attack | `Can I attack the car ahead next lap?` | Measured gap, trend, tyres and battery; a current subsecond gap alone does not guarantee future Overtake Mode eligibility. Restricted rival values are unknown, not zero. |
| Defence | `How should I defend against the car behind?` | Measured rival gap and energy; realistic exit/positioning advice with no invented corner map or unseen rival boost. |
| Weather | `Weather forecast.` | Current conditions plus actual forecast horizon; absent forecast is unknown, not zero rain risk. Probability is not a confirmed start time or tyre crossover. |
| Flags | `What flags or penalties do I need to react to?` | Actual race-control phase, FIA flag and penalties; distinguish red flag, SC and VSC, including unserved penalties. |
| Red flag | `Red flag: can I change tyres, and what is the best restart strategy and alternative?` | Refresh strategy after confirmed suspension. State tyre-change opportunity without normal pit-lane loss; quote `red_flag_restart.primary` and distinct feasible `alternative`. Preserve inventory uncertainty and do not deny the change because the game is paused. |
| Red flag, unknown inventory | Same red-flag request with no tyre-set packet | Explain the opportunity and conditional plan. Never claim an unobserved fresh hard/medium/soft set is available. |
| Red flag, worn same compound | Request restart strategy while currently on worn mediums | Distinguish a fresh medium physical set from the fitted medium. Do not suppress a suspension change merely because compound names match. |
| Setup | `Review my setup for rear instability on exit.` | Current/saved setup evidence, bounded differential advice and its conditions; never say a setup was applied in game. Published setup reference is not a proven personal gain. |
| History | `Compare my hard and medium practice runs.` | Use saved run/group IDs, lap counts, conditions, recorded notes and compatibility. Missing current degradation fit does not mean no saved practice data. |
| Session best | `What is my best valid recorded lap so far, and is that from this session?` | Use `session_best_valid_lap` from all available current-session game history, with lap-validity bit 0, before limiting the returned recent window. Identify the lap and session; an earlier best must remain visible when it is outside the requested window. |
| Incomplete history | Same session-best request with earlier or latest completed laps missing | Say full-session coverage is incomplete. `best_valid_observed_lap` is only the best among observed history; do not promote it or the recent-window minimum to the full-session best. A new UID must not inherit the previous session's history. |
| Confirmed result | `What is my final classification, and is the result confirmed?` or `Final position.` after packet 8 | Answer directly from the current session's `final_classification`, including recorded position, completed laps, points, pit stops, best lap and penalties when present. The confirmed result remains valid with stale, disconnected or paused live telemetry; live gaps do not qualify or replace it. |
| Strategy after finish | `Should I box now?` or `Give me the full pit strategy and its best alternative.` after packet 8 | State the confirmed finish and that no further racing pit stop or live strategy alternative is needed, even with fresh telemetry. The strategy tool exposes the result with no actionable recommendation; hypothetical and historical questions retain their scope. |
| Result not received | Same final-result request before packet 8 or after a new UID | Explicitly say no final classification has been received for this session. Live P1, the final scheduled lap and the previous session's result do not establish a confirmed result. Hypothetical, rival and other-session questions retain their own scope. |
| Notes | `Note that lap 7 was compromised by traffic.` | Persist exact affected lap and qualitative source; only say saved after tool success. A reported traffic cause is not telemetry proof. |
| Stale group | Continue packet 6 but stop packet 7 beyond disconnect threshold; ask `Fuel status.` | Socket may remain connected. Response says fuel telemetry stale; no current fuel margin or new fuel-saving recommendation. |
| Missing group | No packet 16, fresh packet 7; ask `Battery.` | Report battery; Overtake Mode and Active Aero fields are unknown, not confirmed unavailable. |
| Paused | Pause known session; ask `Fuel status.` | Explicitly identify last confirmed paused value, with no live pace/attack command. Red-flag suspension strategy remains available. |
| Session switch | New UID receives only session packet; ask `Fuel status.` | Old session numbers cannot appear; current fuel unknown until new status arrives. In-flight old-session answers are discarded. |
| Output format | Any above | Brief driver-facing prose, consistent units, no raw JSON, raw enum numbers, tool names, leaked reasoning or unrelated pit calls. |

## Terminology sources

The game is **F1 25: 2026 Season Pack**; `2026` remains the correct UDP format
identifier. Current game guidance names **Overtake Mode**, **Active Aero**,
**Cornering Mode** and **Straight Line Mode**. See [EA game guidance](https://www.ea.com/games/f1/f1-25/news/f1-25-2026-season-pack-tips-and-tricks).

The [EA UDP specification](https://forums.ea.com/blog/f1-games-game-info-hub-en/ea-sports%E2%84%A2-f1%C2%AE25-2026-season-pack-udp-specification/12187347)
defines separate eligibility flags in Car Telemetry 2 (packet 16). The 2026 ERS
deployment enum 3 is **Boost**, while Active Aero enum 0/1 means corner/straight.
Do not relabel a battery deployment setting as proof that Overtake Mode is active.
