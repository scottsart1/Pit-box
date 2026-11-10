# Part 3 — Strategy core

The planner enumerates one-, two- and three-stop plans, simulates every
stint, projects a finishing position against the field and ranks. The
enumeration and the stint model are sound. The findings here are in how the
ranked plans are ordered, in tables and thresholds that have drifted apart,
and in settings that are read by nothing.

Contents: [ST-01](#st-01) · [ST-02](#st-02) · [ST-03](#st-03) ·
[ST-04](#st-04) · [ST-05](#st-05) · [ST-06](#st-06) · [ST-07](#st-07) ·
[ST-08](#st-08)

---

## ST-01

**Among plans level on position and points, the latest first stop wins
whatever it costs in time.** Severity S1. Evidence: reproduced.

**Where.** `strategy.py:3846` `ranking_key`, the term at
`strategy.py:3891`.

```python
return (
    not plan.get("legal", True),
    not plan.get("feasible", False),
    <projected finish position without driver feedback>,
    <projected finish position>,
    appetite_position,
    -float(appetite_points),
    0 if red_flag_change else -first_stop,        # <- before time
    float(plan.get("risk_adjusted_time_s", 1e9)),
    int(plan.get("stops_remaining", 9)),
)
```

**What happens.** The comment explains the intent: when the model scores two
plans as the same finishing position, the time between them "is below what
the model can cash in", and keeping track position has option value. That
is right when the two plans are a few seconds apart. The key applies it at
any distance.

Level position is common, not exceptional:

- Outside the points every plan has `points_expected == 0.0`, so the only
  thing separating plans is the integer projected position. From P12 with
  a compressed field, most stopping plans share one.
- When rivals are projected not to stop ([RV-01](02-rivals.md#rv-01)) every
  stopping plan projects the same last place.

`python -m tools.strategy_probe`, part 1:

```text
25% distance   14 laps | call: A second compound is mandatory — box lap 13 for SOFT.
35% distance   20 laps | call: A second compound is mandatory — box lap 19 for SOFT.
50% distance   29 laps | call: A second compound is mandatory — box lap 26 for SOFT.
```

One lap on new softs, at the cost of thirteen on worn mediums.

**Measured on the repository's own benchmark.** `tools/strategy_validation.py`
scores the engine's recommendation against an exact oracle in 24 frozen
worlds (`docs/strategy-validation-manifest.json`). Regret is the race time
lost to the oracle's plan. Run against `1f35a7e` and against the prototype
of this change in [06-reference-patch.md](06-reference-patch.md), same
evaluator and manifest checksums:

| Worlds | Median regret | 90th percentile |
| --- | --- | --- |
| all 24 | 13.9 s → **4.7 s** | 61.5 s → **17.8 s** |
| held-out (18) | 31.1 s → **11.4 s** | 69.1 s → **23.4 s** |
| weather (4) | 72.0 s → **16.5 s** | 94.1 s → **17.8 s** |
| wear cliff (4) | 24.3 s → **11.4 s** | 31.9 s → **13.0 s** |
| traffic (4) | 3.6 s → **0.4 s** | 9.1 s → **1.5 s** |
| quadratic (4) | 37.9 s → **24.9 s** | 49.3 s → **36.6 s** |
| training (6), finite sets (4) | unchanged | unchanged |

No world is worse by more than half a second, there are no contract
failures, and the runtime is unchanged. Setting
`PITWALL_STRATEGY_LATER_STOP_TOLERANCE_S=1000000`, which restores the old
tie-break inside the prototype, reproduces the baseline figures exactly: the
whole improvement is this one rule.

**Change.**

1. `config.py`: add

   ```python
   # How much projected race time a later first stop may cost and still be
   # preferred for the track position it keeps. Beyond it the quicker plan
   # wins. 3 s is about two cars' spacing in a train.
   strategy_later_stop_tolerance_s: float = 3.0
   ```

   and the matching line in `.env.example`.

2. In `compute`, split the key into a primary part and the tie-break, and
   let the later stop win only inside the tolerance of the best time in its
   group:

   ```python
   group_best_time: dict[tuple[Any, ...], float] = {}

   def primary_key(plan) -> tuple[Any, ...]:
       return (… the first six terms, unchanged …)

   def ranking_key(plan) -> tuple[Any, ...]:
       primary = primary_key(plan)
       time_s = float(plan.get("risk_adjusted_time_s", 1e9))
       within = time_s - group_best_time.get(primary, time_s) <= settings.strategy_later_stop_tolerance_s
       return (
           *primary,
           0 if red_flag_change or not within else -first_stop,
           time_s,
           int(plan.get("stops_remaining", 9)),
       )
   ```

   Fill `group_best_time` once, after the Monte Carlo loop has set every
   shortlisted plan's `risk_adjusted_time_s` and before the first use of
   `ranking_key` (`strategy.py:3897`):

   ```python
   for plan in shortlisted:
       key = primary_key(plan)
       group_best_time[key] = min(group_best_time.get(key, math.inf),
                                  float(plan.get("risk_adjusted_time_s", 1e9)))
   ```

   A plan outside the tolerance gets `0`, which sorts after every negative
   `-first_stop`, and then by time. `ranking_key` is also passed to
   `_race_shapes`, `_closest_to_plan`, `_red_flag_restart_advice` and the
   override selection; all of them run after the table is filled.

3. Break position ties outside the points with the distribution, before
   time. `points_expected` is 0 there, but `expected_finish_position` (the
   Monte Carlo mean, `strategy.py:1997`) is not. In `primary_key`, for the
   balanced appetite, replace `-float(appetite_points)` with the pair
   `(-float(appetite_points), round(float(plan.get("expected_finish_position",
   appetite_position)) * 2) / 2)`. Half-position resolution keeps Monte
   Carlo jitter from reordering plans every tick.

4. Update the first sentence of `assumptions` (`strategy.py:4314`) and
   `plan_comparison_basis` in the pit-strategy tool view (`tools.py`) to say
   what the rule now is.

**Known consequence, to decide before merging.** In the Hungaroring demo
race the lap-60 soft stop is about 4.6 s quicker than the lap-62 stop the
engine recommends today (the docstring of `TelemetryTools.what_if` records
it). With a 3 s tolerance the recommendation moves to lap 60. That is what
this change is for, but it reverses a call the current rule made on
purpose. If the later stop should still win there, set the tolerance to
5 s; the probe's penultimate-lap calls are 20 s and more from the best
plan and are fixed at either value.

**Tests.** In `tests/test_race_plan_ranking.py`:

- `test_a_later_stop_is_preferred_when_it_costs_almost_nothing` — two
  shortlisted plans, same position and points, first stops on laps 12 and
  16, times 1500.0 and 1501.5: lap 16 ranks first.
- `test_a_later_stop_is_not_preferred_when_it_costs_ten_seconds` — times
  1500.0 and 1510.0: lap 12 ranks first.
- `test_the_tolerance_is_a_setting` — with
  `strategy_later_stop_tolerance_s = 15`, lap 16 ranks first again.
- `test_outside_the_points_the_expected_position_separates_plans`.
- `tests/test_race_regressions_4_9_3.py` must still pass. Its Las Vegas
  case — "box lap 6 for softs from P8 with a projected rejoin of P22" — is
  the reason the later-stop rule exists. That plan and the stay-long plan
  were level on position; check the regression's times. If the early stop
  is more than 3 s quicker in that state, the regression is protected by
  the position recovery model (ST-02) and the rejoin projection
  ([RV-07](02-rivals.md#rv-07)), not by this tie-break, and those two items
  must land in the same release as this one.

**Done when.** `python -m tools.strategy_probe --assert` prints no "first
stop called for lap N of M".

---

## ST-02

**Positions recoverable after a stop use one fixed tax and one fixed
retention.** Severity S2. Evidence: reproduced in
[WX-04](01-weather.md#wx-04).

**Where.** `strategy.py:1810` `_expected_positions_recovered`;
`strategy.py:194`–`201`; `strategy.py:1892`; `strategy.py:1937`.

**What happens.** The number of places a stop can win back is

```text
pit_cycle + pace_advantage × laps_after_stop × 0.60 / (typical_gap + 1 + 6 × difficulty)
```

capped at the places lost, rounded to two decimals, then floored. Four
things are wrong with it.

1. The reference pace is the **median of the whole field**, not of the cars
   the stop puts in front. A stop from P3 that rejoins P9 has to pass the
   cars in P4–P9, who are faster than the field median.
2. The overtaking tax does not depend on the pace difference. Passing a car
   0.2 s a lap slower and one 6 s a lap slower cost the same 2.3–6.7 s.
3. `typical_gap` is the median spacing of the whole field, used for every
   pass. The cars between the rejoin and the pre-stop position have known
   gaps.
4. `round(…, 2)` before `floor(… + 1e-9)` turns 5.994 into 5.99 into 5.

**Change.** Steps 3 and 4 of WX-04 cover points 1, 2 and 4. For point 3,
walk the actual gaps:

```python
# Cars the stop puts ahead, nearest first, with the gap to each after the stop.
ahead = sorted(
    (rival for rival in rival_projections
     if current < int(rival.get("position", 0)) <= rejoin
     and finite(rival.get("current_gap_s")) is not None),
    key=lambda rival: int(rival["position"]), reverse=True,
)
budget = pace_advantage * laps_after_stop * retention
recovered = float(pit_cycle)
behind_by = first_cost                      # seconds behind the pre-stop position
for rival in ahead:
    to_catch = max(0.0, behind_by - float(rival["current_gap_s"]))
    cost = to_catch + overtake_tax
    if budget < cost:
        break
    budget -= cost
    behind_by = float(rival["current_gap_s"])
    recovered += 1.0
```

`current_gap_s` is positive for a car behind the player before the stop.
After the stop the player is `first_cost` seconds back from where they were,
so the car `g` seconds behind is `first_cost − g` ahead. When
[RV-07](02-rivals.md#rv-07) lands, take the gaps at the box lap from its
cumulative table instead of the current ones.

**Tests.** In `tests/test_strategy.py`:

- `test_recovery_is_measured_against_the_cars_ahead_after_the_stop`.
- `test_a_strung_out_field_costs_more_to_recover_through_than_a_train` —
  the same pace advantage, gaps of 5 s against gaps of 1 s.
- `test_five_point_nine_nine_four_is_not_five` — a state constructed so the
  unrounded value is 5.994: `projected_finish_position` reflects five whole
  places, and with 6.001 six.

---

## ST-03

**The two-compound rule is applied to every race length.** Severity S2.
Evidence: to verify.

**Where.** `strategy.py:960`: `applies = mode_profile == "race" and not
wet_used`.

**What may be wrong.** `session_length_id` is parsed (`udp.py:1341`; 2 =
Very Short, 3 = Short, 4 = Medium, 5 = Medium Long, 6 = Long, 7 = Full) and
read by nothing in the planner. If the game does not enforce a compound
change in its shortest race, the engine tells the driver "A second compound
is mandatory — box lap 4" in a five-lap race and, when they do not, "the
result is a disqualification".

**How to verify.** Start a Very Short race and a Short race with dry
weather. For each, record from a capture: `session_length`, `total_laps`,
`pit_stop_window_ideal_lap`, `pit_stop_window_latest_lap`, and whether the
game penalises a no-stop finish. Write the result into
`docs/capture-rotation-observations.md`.

**Change, if the shortest race has no mandatory stop.**

1. Add

   ```python
   @staticmethod
   def _compound_rule_applies_to_event(state: dict[str, Any]) -> bool:
       override = str(settings.strategy_compound_rule).lower()   # auto | always | never
       if override in {"always", "never"}:
           return override == "always"
       if str(state.get("mode_profile", "idle")) != "race":
           return False
       # Very Short races carry no mandatory stop.
       return int(state.get("session_length_id", 0) or 0) != 2
   ```

   and use it in `_compound_rule` for `applies` and `observed_compliant`,
   and in `_rival_owes_compound` (RV-01).
2. `config.py`: `strategy_compound_rule: str = "auto"`, validated to the
   three values; `.env.example` entry.
3. `compound_rule["note"]`: add the case "This race length carries no
   mandatory compound change."

**Tests.** `tests/test_strategy.py`:
`test_a_very_short_race_has_no_mandatory_stop`,
`test_the_rule_can_be_forced_on_or_off`, and the existing compound-rule
tests unchanged with `session_length_id` absent (0).

---

## ST-04

**Shanghai and the three reverse layouts have no entry in any circuit
table, and pit loss is never measured.** Severity S2. Evidence: reproduced.

**Where.** `strategy.py:27` `PIT_LOSS_SECONDS`; `strategy.py:56`
`PIT_ENTRY_FRACTION`; `strategy.py:68` `TRACK_TYRE_SEVERITY`;
`strategy.py:227` `TRACK_OVERTAKING_DIFFICULTY`; `strategy.py:1270`
`_base_pit_loss`.

**What happens.** Of the 37 circuits the parser names:

| Circuit (id) | Pit loss | Severity | Overtaking | Pit entry |
| --- | --- | --- | --- | --- |
| Shanghai (2) | missing → 22.5 s | missing → 1.00 | missing → 0.60, confidence low | missing → 0.94 |
| Silverstone Reverse (39) | missing | missing | missing | missing |
| Austria Reverse (40) | missing | missing | missing | missing |
| Zandvoort Reverse (41) | missing | missing | missing | missing |
| 16 further current circuits | present | present | present | missing → 0.94 |

Shanghai is on the calendar. Setup Lab covers all four layouts above; the
planner treats them as an unknown circuit, which also forces
`finish_projection_confidence` to `"low"` for the whole race.

`_base_pit_loss` returns the table value and nothing else. Its comment is
right that the game's lane timer is time in the lane, not net loss. But net
loss is measurable from data the app already records: every rival's in-lap
and out-lap in every archived race.

**Change.**

1. Add the missing entries. These are judgements and must be commented as
   such until F-09 measures them:

   ```python
   PIT_LOSS_SECONDS[2] = 23.0          # Shanghai
   TRACK_TYRE_SEVERITY[2] = 1.10
   TRACK_OVERTAKING_DIFFICULTY[2] = 0.42
   ```

2. Reverse layouts inherit their circuit's pit loss and severity. Add

   ```python
   # A reversed layout uses the same pit lane and the same surface.
   _LAYOUT_PARENT = {39: 7, 40: 17, 41: 26}

   def _circuit_value(table: dict[int, float], track_id: int, default: float) -> float:
       return table.get(track_id, table.get(_LAYOUT_PARENT.get(track_id, -1), default))
   ```

   and use it at every `.get(track_id, …)` on the four tables (there are
   nine call sites; `grep -n "TRACK_TYRE_SEVERITY.get\|PIT_LOSS_SECONDS.get\|TRACK_OVERTAKING_DIFFICULTY.get\|PIT_ENTRY_FRACTION.get" src/pitwall`).
   Overtaking difficulty and pit entry do **not** inherit: the corners and
   the entry point are different in reverse.

3. `difficulty_known` (`strategy.py:3687`) stays `False` for a reverse
   layout so the projection confidence remains honest.

4. Measured pit loss, severity and overtaking are feature
   [F-09](05-features.md#f-09).

**Tests.** `tests/test_strategy.py`:

- `test_every_circuit_the_parser_names_has_a_pit_loss_or_a_parent` — for
  each id in `udp.TRACKS` not in the short-layout set `{21, 22, 23, 24}` and
  not in the retired set `{1, 8, 18, 25, 28}`:
  `_circuit_value(PIT_LOSS_SECONDS, id, None) is not None`.
- `test_a_reverse_layout_inherits_pit_loss_and_severity_only`.

---

## ST-05

**`compute_target` takes its median from safety-car and pit laps, and from
the car ahead's last lap.** Severity S3. Evidence: read.

**Where.** `analysis.py:561`–`633`.

**What happens.** In a race the lap target is the median of the last four
laps that are `valid` with a positive time. A safety-car lap and an in-lap
are both valid. After a three-lap safety car the target is a safety-car lap
time for the next two laps; the DRIVE card shows it, the progress call
speaks it ("Lap 14: target 2:04.3"), and brutal mode judges the driver
against it.

The target is then lowered to the car ahead's last lap minus 0.1 s. If
that car's last lap was on new softs, or it is on intermediates and the
player on slicks, the target is a time the player's car cannot do.

**Change.**

1. Filter with the rule the rest of the app uses:
   `exclusion_reason({"mode_profile": mode, **lap}) is None` in place of
   `lap.get("valid") and lap.get("lap_time_ms", 0) > 0` for the race
   branch. Keep the looser filter for `player_best`.
2. Use the car ahead only when it is comparable: same weather class of
   tyre, tyre age within eight laps of the player's, not in the pit lane,
   its last lap within 2% of its own median of the last three valid laps
   (so not an in- or out-lap), and the race is green.
3. Cap the pull: `target_ms = max(target_ms − 800, ahead_last − 100)`. A
   target more than 0.8 s under sustainable pace is not a target.
4. `basis` names what was used: `"sustainable race pace (4 clean laps)"`,
   `"pace of the car ahead, capped"`.

**Tests.** `tests/test_proactive_quality.py` or `tests/test_analysis.py`:
`test_a_safety_car_lap_is_not_race_pace`,
`test_a_car_on_new_softs_does_not_set_the_target`,
`test_the_car_ahead_can_pull_the_target_by_eight_tenths_at_most`.

---

## ST-06

**Seven wear thresholds across four modules.** Severity S3. Evidence: read.

| Value | Meaning there | Where |
| --- | --- | --- |
| 45 / 75 | driver feedback contradicts wear | `strategy.py:3113`, `3117` |
| 52 / 58 / 70 | pace penalty begins (average / peak / steep) | `strategy.py:446`–`448` |
| 62 | tyre warning re-arms | `proactive.py:1885`, `1191` |
| 65 | tyre warning; conservative time penalty begins | `proactive.py:1888`; `strategy.py:2386` |
| 72 | "cliff" lap shown on DRIVE | `analysis.py:524` |
| 80 / 85 / 88 / 88 / 90 | operational limit per compound | `strategy.py:300` |
| 82 | "hard safety limit": releases a hold, makes calls critical, lifts suppression | `proactive.py:1482`, `1532`, `1889`, `2580`; `strategy.py:2636`; `brain.py:1548` |
| 92 | stint infeasible | `strategy.py:2391` |

The DRIVE card says "CLIFF ~L36" from 72% while the plan beside it allows
the same tyre to 85% and the radio calls it critical at 82%. A soft tyre's
operational limit (80) is below the "hard safety limit" (82), so a soft
stint the planner has already declared infeasible has not yet reached the
threshold that makes the radio insist.

**Change.** New module `src/pitwall/tyre_limits.py`, imported by the four
modules, holding every value above under a name and one function:

```python
def safety_limit_pct(compound: str) -> float:
    """Wear at which a call about this tyre stops being optional."""
    return min(82.0, OPERATIONAL_WEAR_LIMIT.get(str(compound).upper(), 85.0))
```

Replace each literal `82.0` with `safety_limit_pct(compound)`. Replace the
`72` in `compute_degradation` with the wear at which the planner's own pace
penalty turns steep (`WEAR_PENALTY_STEEP_PEAK_PCT = 70.0`), and rename the
published key's label on DRIVE from "CLIFF" to "pace drop" — it is where
the model starts charging 0.06 s per percent, not a cliff the game defines.
No numeric behaviour changes except soft tyres reaching the safety limit at
80 instead of 82.

**Tests.** `tests/test_tyre_limits.py`:
`test_a_soft_is_safety_critical_at_its_own_limit`,
`test_every_module_reads_the_same_limits` (import each module, assert the
names are the same objects).

---

## ST-07

**Settings that are documented and do nothing.** Severity S3. Evidence:
reproduced (a scan of every `Settings` field against every reference in
`src/pitwall`).

| Setting | In `.env.example` | Read by | What actually applies |
| --- | --- | --- | --- |
| `proactive_safe_throttle` (0.65) | yes | nothing | `0.45`, a literal at `proactive.py:1322` |
| `ptt_debounce_ms` (150) | — | nothing | `voice.py` uses its own constant |
| `custom_llm_label` | — | nothing | — |
| `trace_chunk_seconds`, `analysis_distance_step_m` | — | a validator only | — |
| `strategy_risk_quantile` (0.75) | yes | `strategy.py:3834` | a switch: `>= 0.70` → p75, else p50. `0.9` and `0.71` are the same |
| `strategy_change_min_gain_s`, `strategy_min_hold_laps`, `strategy_switch_confirm_s` | no | the stability hold | undocumented; these are the three knobs that decide how quickly a new call replaces an old one |

**Change.**

1. `proactive.py:1322`: `float(state.get("throttle", 0)) >=
   settings.proactive_safe_throttle`, and change the default in
   `config.py:286` to `0.45` so behaviour is unchanged for everyone who has
   not set it. Update `.env.example`.
2. `strategy.py:3834`: compute the configured quantile.
   `_monte_carlo_profile` returns `_outcome_times_s`; add
   `"risk_s": round(float(np.quantile(outcomes, min(0.95, max(0.5,
   settings.strategy_risk_quantile)))), 2)` and use `plan["monte_carlo"][
   "risk_s"]`. Keep `p50_s`, `p75_s`, `p90_s` for display.
3. Remove `ptt_debounce_ms`, `custom_llm_label`, `trace_chunk_seconds` and
   `analysis_distance_step_m`, or wire them. `ptt_debounce_ms` should
   replace the literal in `voice.py:593`–`626`.
4. Add the three stability settings to `.env.example` under "Strategy and
   uncertainty", with one line each on what a larger value does.
5. Add a test that keeps this from recurring, in a new file
   `tests/test_settings_are_read.py`:
   `test_every_setting_is_read_somewhere` — for each
   field of `Settings`, assert its name appears in at least one file under
   `src/pitwall` other than `config.py`, with an explicit allow-list for
   settings read only through `getattr`.

Also stored and read by nothing: `SessionState.active_aero_track_status`
(`udp.py:1435`) — see [WX-08](01-weather.md#wx-08) for its use.

---

## ST-08

**The Monte Carlo adds noise to one deterministic race.** Severity S3.
Evidence: read.

**Where.** `strategy.py:2478` `_monte_carlo_profile`.

**What happens.** Each shortlisted plan's projected time is perturbed by
three normal draws — tyre, pit, traffic — with hand-set scales
(`"calibrated": False`). Every draw is the same race: no safety car, one
weather outcome, rivals on one fixed schedule. The position distribution,
`upside_p10_position`, `downside_p90_position` and `points_expected` that
the risk appetite reads all come from that. "Conservative" and "aggressive"
therefore choose between plans on the spread of tyre-model noise, not on
exposure to a safety car or a shower.

**Change.** Feature [F-07](05-features.md#f-07), the race simulator. Until
it lands, say what the numbers are on the Strategy page: the meta line at
`static/js/strategy.js:208` already prints "forecast range is not yet
calibrated"; add "and assumes no safety car".
