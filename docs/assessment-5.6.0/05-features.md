# Part 5 — Feature programme

Twelve features. Each one turns something the engine already computes, or
could compute from packets it already receives, into a decision the driver
can see and hear. They are ordered by dependency, not by size.

Rules for every screen in this part:

- **DRIVE is not redesigned.** Nothing here adds a card to it. Where DRIVE
  is touched, it is the text of an element that already exists.
- New panels go on **STRATEGY** as `<section class="panel-surface
  strategy-main-col">` blocks with the existing `.section-heading`,
  `.eyebrow`, `h2`, `canvas.analysis-canvas`, `details.race-details` and
  `.field-help` classes, rendered from `static/js/strategy.js`.
  Calibration and logs go on **CONNECTION**.
- Tokens are the ones in `static/css/v42.css`: background `#070b0f`, cards
  `#121b24` with `#263542` hairlines, green `#49d17d`, blue `#3f86ff`, red
  `#ff6969`, amber `#ffc15c`, 11px uppercase labels at `.12em` tracking,
  900-weight heroes, 44px minimum hit targets.
- Colour never carries a state alone. Every pill has a word; every chart
  has the data table the Session Analysis charts already have.
- Slow data is fetched, not streamed. Panels poll their own endpoint every
  5–10 s while visible, the way `refreshRivals` does, so the 4 Hz frame does
  not grow ([PF-02](04-performance.md#pf-02)).

Contents: [F-01](#f-01) · [F-02](#f-02) · [F-03](#f-03) · [F-04](#f-04) ·
[F-05](#f-05) · [F-06](#f-06) · [F-07](#f-07) · [F-08](#f-08) ·
[F-09](#f-09) · [F-10](#f-10) · [F-11](#f-11) · [F-12](#f-12)

---

## F-01

**Weather wall.** Depends on WX-01, WX-02.

**Why.** The whole wet model reaches the driver today as one line of text
on DRIVE ("Overcast · rain at 15 min 65% · track damp") and one sentence of
reason. The model holds a surface estimate with four sources, a forecast, a
projection to the flag and a cost for every tyre. A driver deciding whether
to trust "stay out" needs to see those.

**One function, three readers.** `StrategyEngine.weather_picture(state,
plan) -> dict`, pure, built from the published `weather_crossover` and
`state.weather_forecast`. Read by the tool `get_weather_picture`
([WX-12](01-weather.md#wx-12)), by `GET /api/strategy/weather`, and by the
radio protocol (F-03).

```jsonc
{
  "available": true,
  "relevant": true,              // wet now, or wet in any scenario, or a sample at 20%+, or a driver report inside 4 laps
  "sky": {"now": "Light rain", "since_lap": 11, "track_temp_c": 26, "air_temp_c": 19},
  "surface": {
    "wetness": 0.41, "word": "wet", "trend": "wetting",
    "sources": {"surface_model": 0.43, "lap_pace": 0.40, "sector_pace": 0.42, "driver": null},
    "confidence": 0.90, "field_cars": 8,
    "crossovers": {"slick_inter": 0.35, "inter_wet": 0.75},
    "flags": {"field_surface_temp_drop_c": 14.0, "race_control_low_grip": true}
  },
  "forecast": [
    {"in_min": 5, "in_laps": 3.3, "sky": "Light rain",
     "chance_pct": 50, "chance_given_now_pct": 90, "track_temp_c": 25}
  ],
  "projection": {
    "first_lap": 12,
    "mean": [0.41, 0.46, 0.49],
    "wet_case": [0.41, 0.47, 0.51], "dry_case": [0.41, 0.40, 0.34],
    "p_wet_at_flag": 0.80
  },
  "tyres": [
    {"compound": "MEDIUM", "fitted": true,  "loss_now_s_per_lap": 4.1, "cost_to_flag_s": 118.0},
    {"compound": "INTER",  "fitted": false, "loss_now_s_per_lap": 0.9, "cost_to_flag_s": 31.0,
     "stop_cost_s": 24.5, "net_vs_staying_s": -62.5, "box_lap": 13}
  ],
  "call": {"level": 4, "label": "box", "compound": "INTER", "box_lap": 13, "direction": "to_wet",
           "shows_s": 62.5, "needs_s": 6.0, "confirmed": true,
           "confirmed_by": ["surface_model", "sector_pace"], "reason": "…"},
  "crossover_clock": {"to": "INTER", "laps": 0, "basis": "surface at the crossover"},
  "calibration": "judgement"
}
```

Where each field comes from:

| Field | Source |
| --- | --- |
| `sky.since_lap` | first lap of the trailing run of completed laps with the current label |
| `surface.*` | `weather_crossover.wetness`, `.trend`, `.wetness_source`, plus WX-02 and WX-08 additions |
| `forecast[].in_laps` | `time_offset_min × 60 / base_lap_s` |
| `forecast[].chance_given_now_pct` | `rain.conditioned_rain_probability` (WX-01) |
| `projection.wet_case`, `dry_case` | the scenarios with the highest and lowest final wetness; `mean` is `weather_crossover.trajectory` in full |
| `projection.p_wet_at_flag` | summed probability of scenarios ending at or above the slick crossover |
| `tyres[].loss_now_s_per_lap` | `expected_lap_penalties[compound][0] × dry_base_lap_s` |
| `tyres[].cost_to_flag_s`, `net_vs_staying_s` | `weather_crossover.options` |
| `call` | `ProactiveEngineer._weather_call` plus `margin_s`, `uncertainty_s`, `confirmed` |
| `crossover_clock.laps` | index of the first lap at which `mean` crosses the crossover in the direction that matters for the fitted tyre |

**Screen.** STRATEGY, a new section directly under the current call. When
`relevant` is false it is a closed `<details>` whose summary reads "Dry. No
rain forecast inside the race."

```text
TRACK CONDITIONS                                              [ BOX · INTERS · LAP 13 ]
┌ SURFACE ───────────────────────────┐ ┌ NEXT 30 MINUTES ───────────────────────────────┐
│ WET                          0.41  │ │        now    +5     +10    +15    +30         │
│ ████████████▌░░░░░░░░░░░░░░░░░░░░  │ │ sky    rain   rain   rain   rain   cloud       │
│ dry   damp  │ wet          │ flood │ │ game   50%    50%    60%    40%    20%         │
│             S/I            I/W     │ │ now →  —      90%    92%    74%    32%         │
│ model .43 · pace .40 · sectors .42 │ │ track  26°    25°    24°    24°    25°         │
│ 8 cars · wetting                   │ └────────────────────────────────────────────────┘
└────────────────────────────────────┘
┌ SURFACE TO THE FLAG ──────────────────────────────────────────────────────────────────┐
│ canvas: band between dry_case and wet_case, mean line, horizontal lines at the two    │
│ crossovers, vertical marker on the box lap, x = lap                                   │
└───────────────────────────────────────────────────────────────────────────────────────┘
TYRE        NOW          TO THE FLAG    STOP      NET AGAINST STAYING
MEDIUM  ●   +4.1 s/lap   118 s          —         —
INTER       +0.9 s/lap    31 s          24.5 s    −62 s   ← box lap 13
WET         +6.2 s/lap   171 s          24.5 s    +78 s
Confirmed by surface model and sector pace · needs 6 s, shows 62 s
The tyre curves are estimates that have not yet been fitted to recorded wet races.
```

- The call pill is green for none and watch, amber for ask and stand by,
  red for box. It always carries the word.
- The surface bar is blue; the two crossover ticks are labelled.
- The "now →" row is the chance given what is falling now. Its label on
  screen is "given the sky now"; a tooltip explains the difference from the
  game's figure in one sentence.
- The last line is shown while `calibration == "judgement"` and removed
  when F-10 has fitted the curves.
- Below 720 px the two top boxes stack and the forecast table scrolls
  horizontally inside its box, as the Strategy plan table does.

**DRIVE.** No new element. `static/index.html:957` sets `$('weather')`.
When `call.level >= 3` lead with the call — "BOX lap 13 · inters — track
wet" — and add the existing `warn` tone class. Otherwise unchanged.

**Driver Dashboard.** `static/driver-dashboard/model.mjs:96` adds
`surfaceWord` and `weatherCall` from `s.strategy.weather_crossover`;
`static/driver-dashboard/render.mjs:50`, the conditions widget, adds one
line under the sky name:
the surface word, and the call when there is one.

**Tests.**

- `tests/test_weather_picture.py`: `test_the_picture_is_built_from_the_published_crossover`
  (every numeric field equals its source), `test_a_dry_race_is_not_relevant`,
  `test_the_clock_counts_laps_to_the_crossover`,
  `test_the_picture_and_the_radio_agree_on_the_call`.
- `tools/strategy-layout-smoke.cjs`: add the section at 390, 820 and 1440
  px; assert no horizontal page scroll and that the pill text is one of the
  five labels.
- i18n: run `tools/i18n_extract.py`; the new keys appear in `fr`, `de`,
  `es`.

---

## F-02

**Fast surface channel.** The model work is [WX-08](01-weather.md#wx-08).
This item is what surrounds it.

1. **Record it.** Add to the per-lap record the player keeps
   (`state.py:1260`): `surface_estimate` (the published wetness at the
   line), `field_sector_ratio` (median), `field_surface_temp_drop_c`,
   `race_control_low_grip`. Four numbers a lap. F-10 fits against them.
2. **Race-control status.** `active_aero_track_status` and the `DRSD`
   reason are stored today and read by nothing. Their meaning in the wet is
   to verify: in a capture of a race that starts dry and turns wet, print
   both fields against the weather label each second. If either changes
   with the surface, publish `race_control_low_grip` from it and use it in
   the heads-up (F-03). If neither does, remove the flag from the contract.
3. **Tyre surface temperature.** Per running car, the median of the four
   `tyre_surface_temps_c` over the last 10 s against the same car's median
   over its previous dry lap. Publish the field median of the difference.
   Show it on the wall. Do not act on it until F-10 has measured what a
   drop means.
4. **Forecast temperature trend.** Parse `track_temperature_change` and
   `air_temperature_change` in `udp.py:1258` into each forecast sample
   (`track_temp_trend`, `air_temp_trend`). The published UDP specification
   gives the enum as 0 up, 1 down, 2 no change; confirm it against a capture
   before showing an arrow beside the temperature in the forecast table.

**Done when.** The four per-lap values are in the session export and the
verification note for step 2 is written.

---

## F-03

**Weather call protocol.** Depends on WX-03, WX-09.

One stream of calls about the surface, each said once per change, with the
driver able to answer.

| Level | Said when | Priority | Words |
| --- | --- | --- | --- |
| 1 heads-up, wetting | the sky turns to rain, or the surface passes 0.15, or the driver reports rain, or the chance given now is 60%+ inside three laps | CRITICAL, narrated | "Rain starting. Track is {surface}. Slicks are still the tyre; I'll call the stop." |
| 1 heads-up, drying | on wet tyres, trend drying, crossover clock ≤ 5 laps | CRITICAL, narrated | "Track is drying. Inters are still the tyre; slicks in about {laps} laps." |
| 2 ask | the model's `should_ask_driver` | CRITICAL, template if the narration loses the question | existing |
| 3 stand by | a stop is worth making on a named later lap, or this lap's stop is awaiting confirmation | CRITICAL, narrated | "Stand by for {tyre}, lap {lap}. {reason}" — when awaiting confirmation, end with "How is the grip?" so the reply window opens |
| 4 box | the stop is this lap and confirmed | **FLASH** | "Box this lap for {tyre}. {reason}" |
| reminder | level 4, 12% of a lap before pit entry, not yet in the lane | FLASH | "Box, box. {Tyre} on." |
| stand down | level fell from 3 or 4 without a tyre change | FLASH | "Cancel the stop. Stay out on the {current}s. {reason}" |

Rules:

1. **A new call needs new content.** The signature is `(level, compound,
   box_lap, direction)`. Level 1 is said once per direction per spell of
   weather.
2. **The strategy engine publishes the heads-up condition**, so the radio
   does not re-derive it: `weather_crossover["heads_up"]` is set in
   `_weather_crossover` when the declared sky is wet and the surface is
   below the crossover, or a sample within three laps has
   `chance_given_now ≥ 0.6`, or the driver's report is `spitting`.
3. **A refusal is respected.** When the driver declines a level-4 call
   (`_strategy_refusal`), record `weather_declined = {"lap", "compound",
   "wetness", "margin_s"}` in the store. Level 4 for the same compound is
   not said again until one of: two laps have passed **and** the margin has
   grown by half; the surface has risen by 0.10; the compound changed. It is
   never said more than three times for one spell of weather. The Strategy
   page keeps showing the call throughout.
4. **A report is acted on at once.** `_capture_conditions_feedback` already
   writes `driver_grip_feedback`, which is in the recompute signature. Add:
   after writing it, `await self.strategy.recompute()` so the answer to "How
   is the grip?" changes the call within the same exchange, and reply with
   the consequence: "Copy, damp. Box this lap for inters."
5. **Nothing else contradicts it.** While the level is 3 or 4,
   `_strategy_guard_override` returns `None` ([WX-04](01-weather.md#wx-04)),
   and `progress_update` and `tyre_wear` carry the weather stop as their
   strategy line, not the dry plan.
6. **Every call is recorded with its picture.** `save_proactive_call`
   stores the event payload; add the `call` and `surface` blocks of the
   weather picture so F-11 can show what the engine knew.

**Tests.** `tests/test_weather_call_protocol.py`, in addition to WX-03's:
`test_the_heads_up_is_said_once_a_spell`,
`test_a_declined_stop_is_not_repeated_every_lap`,
`test_a_declined_stop_returns_when_the_track_gets_worse`,
`test_a_grip_report_changes_the_call_in_the_same_exchange`,
`test_a_progress_update_carries_the_weather_stop`.

**Done when.** F-12's wet replay produces exactly this sequence and no
other weather sentence: heads-up, stand by, box, reminder; then, after the
stop, nothing until the drying heads-up.

---

## F-04

**Rival strategy model and pit-window board.** The model is
[the rival model](02-rivals.md#the-rival-model). This is its screen.

**Data.** `GET /api/strategy/rivals`, extended; `refreshRivals` already
polls it every 10 s.

```jsonc
{
  "available": true,
  "field": {
    "cars_owing_a_stop": 7,
    "windows": [{"compound": "MEDIUM", "stop_number": 1, "stopped": 6, "of": 9,
                 "median_lap": 12, "p25": 11, "p75": 14, "stop_wear_pct": 61.0}]
  },
  "rivals": [
    {"driver": "HAMILTON", "position": 6, "gap_to_player_s": 2.3,
     "tyre": {"compound": "MEDIUM", "age": 23, "wear_max_pct": 78.0, "wear_rate_pct_per_lap": 3.3},
     "stops_made": 1, "stop_owed": false,
     "stop_window": {"earliest": 24, "expected": 25, "latest": 26},
     "p_stop_next_lap": 0.35, "p_stop_within_3": 0.90,
     "next_compound": {"compound": "SOFT", "confidence": "medium"},
     "after_stops": {"position": 7, "gap_s": 4.0},
     "basis": ["wear", "field"], "confidence": "high",
     "relevance": 5, "in_cover_range": true}
  ]
}
```

**Screen.** STRATEGY, replacing the list under "Who am I racing?"
(`strategicRivals`).

```text
WHO AM I RACING?                              window open: MEDIUM laps 11–14 · 6 of 9 stopped
POS  DRIVER     TYRE  AGE  WEAR  STOPS  NEXT STOP               FITS   GAP     AFTER STOPS
P4   PIASTRI    H     30   61%   1      to the flag             —      −1.2    P4  −3.1
P5   YOU        M     19   74%   1      L36                     S              P5
P6   HAMILTON   M     23   78%   1      L24–26  ▮▮▮▯            S      +2.3    P7  +4.0   in range
P7   SAINZ      H     20   44%   0      L31–35  ▮▯▯▯  OWED      M      +5.6    P6  −0.8
```

- The bar is `p_stop_within_3` in four steps. `OWED` is an amber pill.
  "in range" is the cover range of F-05.
- `WEAR` reads "hidden" for a restricted car, never 0%.
- A row whose `confidence` is `low` shows its window in muted text with
  "typical tyre life" beneath it. A window from the table alone is never
  shown as a plan.
- Tapping a row opens the cover or undercut calculation for that car
  (F-05) in the "Try a different stop" result area.
- Rows: the five most relevant rivals plus the car directly ahead and
  behind. "Show the field" expands to everyone.

**Timeline.** `drawTimeline` (`static/js/strategy.js:408`) already marks
projected rival stops. Draw each listed rival's window as a 3px bar from
`earliest` to `latest` with a tick at `expected`, in the next compound's
colour, on a lane under the plans.

**DRIVE.** Optional, off by default, under the existing Dashboard-cards
toggles as "Rival pit hints": a 6px amber dot on a timing-tower row when
`stop_owed`, a hollow ring when `p_stop_within_3 ≥ 0.5`. No layout change.

**Tests.** `tests/test_tools_v42_services.py`:
`test_the_rivals_endpoint_carries_the_model`. A new
`tools/strategy-rivals-ui-smoke.cjs` (jsdom): a restricted rival renders
"hidden"; a low-confidence row renders "typical tyre life"; an owed stop
renders the pill.

---

## F-05

**Undercut and cover calculator.** Depends on RV-05, F-04.

**The calculation.** A car `g` seconds behind stops. It rejoins `g + L`
behind. Each lap `j` after that it runs on new tyres while the player runs
on old ones, and gains `dⱼ = player_old_lapⱼ − rival_new_lapⱼ`. If the
player stops at the end of lap `k`, both have paid `L`:

```text
margin_k = g − Σ_{j=1..k} dⱼ          positive: the player rejoins ahead
```

`StrategyEngine.evaluate_cover(driver) -> dict`:

1. `player_old_lapⱼ` — the stay-out stint's `lap_times_s[j]` from the
   current plan.
2. `rival_new_lapⱼ` — `_simulate_stint` for the rival's next compound (the
   rival model's, or the one observed once it has left the pits), age 0,
   with the cold-tyre penalty, on the rival's own pace reference
   (`_rival_pace_reference`), exactly as `_cut_rival_laps` builds its
   hypothetical.
3. Return `{"driver", "gap_s", "options": [{"box_in_laps": k, "margin_s",
   "holds": margin_s >= 0.5} for k in 1..3], "cover_by": <largest k that
   holds, or null>, "cover_range_s": Σ_{j=1..2} dⱼ + 1.0 clamped to
   1.5–6.0, "assumptions": […]}`.
4. The mirror, for a car ahead that stops (`evaluate_overcut` exists;
   reuse it): how many laps of clear air the player has and the pace needed.

**Relevance.** `_rival_relevance(driver, state) -> int`: 3 when within two
positions of the player; 2 when behind inside `cover_range_s`; 2 when ahead
inside it; 2 when the driver's name appears in the text of an active
standing instruction ("tell me when Norris stops"); 1 for the team-mate
(`is_teammate`). A stop is radio at 2 or more.

**Calls.** Each once per rival per stop.

| Event | Words |
| --- | --- |
| behind, in range, cover holds at 1 | "{Name} is in the pits, {gap} behind. Box next lap and you stay ahead by {margin}." |
| behind, in range, cover does not hold | "{Name} is in the pits, {gap} behind. You can't cover it; stay out and run your race." |
| behind, out of range | nothing on the radio |
| ahead, in range | "{Name} ahead is in. Clear air now: {laps} laps to make {seconds}." |
| the window opens | "The window is open: {n} cars on {compound}s have stopped, laps {a} to {b}. Ours is lap {box_lap}." — once per stint, when three cars on the player's compound and stop number have stopped and the player has not |

The first two replace the generic "push for up to two clean laps". The
verdict is the engine's arithmetic; the model may reword it and may not
change the number (`grounding.unsupported` already enforces that).

**Screen.** The result area of "Try a different stop" on STRATEGY, opened
from a rival row: three rows, one per `box_in_laps`, margin and "holds" or
"loses the place".

**Tests.** `tests/test_rival_calls.py`:
`test_cover_margin_is_gap_minus_what_fresh_tyres_gain`,
`test_a_cover_that_holds_is_called_with_its_margin`,
`test_a_cover_that_fails_says_so`,
`test_the_window_call_is_made_once_a_stint`.

---

## F-06

**Rejoin map.** Depends on RV-07.

**Why.** "Box lap 14" and "box lap 15" can differ by a second of race time
and by five seconds of clear air. The cumulative table RV-07 builds answers
"where do I come out" for every lap; this shows it.

**Data.** `strategy["rejoin_map"]`, fifteen rows from the current lap,
built once per compute from RV-07's `player_cum` and `rival_cum`:

```jsonc
[{"box_lap": 14, "rejoin_position": 11,
  "car_ahead": "TSUNODA", "gap_ahead_s": 2.8,
  "car_behind": "ALBON",  "gap_behind_s": 2.1,
  "clear_air": true}]                       // gap ahead of 2.0 s or more
```

For box lap `L` with `k = laps_before_stop(L)`: the player's time is
`player_cum[k] + pit_cost`; each rival's is `gap + rival_cum[k]`; sort; the
neighbours give the two gaps. Publish it in the slow lane only.

**Screen.** STRATEGY, a strip under the stint timeline.

```text
WHERE YOU COME OUT
STOP ON LAP    12     13     14     15     16     17     18
REJOIN         P14    P13    P11    P9     P9     P9     P10
CAR AHEAD      0.4    0.9    2.8    3.5    1.1    0.6    0.3
CAR BEHIND     0.3    1.2    2.1    4.0    2.2    0.9    0.2
               traffic       └──── clear air ────┘       traffic
```

The recommended lap's column is outlined. Clear-air columns carry a green
top border **and** the word. Tapping a column runs the what-if for that lap
(WX-10) and the timeline's existing move-the-stop gesture.

**Radio.** The box call gains a clause when the map has it: "Box this lap
for hards. You come out behind Tsunoda in clear air." Tool:
`get_rejoin_map`, for "where do I come out if I box now?".

**Tests.** `tests/test_strategy.py`:
`test_the_rejoin_map_names_the_cars_either_side`,
`test_a_rival_that_stops_first_is_behind_on_the_map`,
`test_clear_air_needs_two_seconds`.

---

## F-07

**Race simulator.** Replaces the noise in [ST-08](03-strategy-core.md#st-08).
Depends on the rival model and WX-01.

**What a draw is.** One possible race from here to the flag. Draws are
shared by every plan (the common-random-numbers rule the current code
already follows, seeded on session, epochs and lap).

| Sampled | From | Stage |
| --- | --- | --- |
| tyre and pit noise | as today | 1 |
| weather | one rank `u ~ U(0,1)` selects a scenario from `project_wetness_scenarios`, whose scenarios are ordered by rank | 1 |
| each rival's stop laps | the rival model's stop distribution | 1 |
| safety car | per-lap hazard for the circuit (F-09; prior 0.02, street circuits 0.04), three laps long; VSC at half the hazard, one to two laps | 1 |
| field compression at a restart | gaps to cars within 20 s shrink to one second per position | 2 |
| the plan's own reaction | a planned stop within three laps after a safety car comes out is taken under it; a weather scenario that crosses the crossover moves the stop to that lap | 2 |

**What a plan gets.** For each draw, its time and position against the
sampled field. From all draws: the position distribution it has today,
plus `p_win`, `p_podium`, `p_points`, and two sensitivities —
`safety_car_swing` (mean places gained in draws with a safety car minus
without) and `rain_swing`. The risk appetite then chooses on exposure to
real events, which is what the three words mean to a driver.

**Stage 2 changes the ranking's foundation.** With the plan's own reaction
simulated, a later stop is credited for the option it keeps open in exactly
the draws where the option is worth something. At that point the later-stop
tie-break of ST-01 can be reduced to a 1 s tolerance; do not remove it
before stage 2 is validated on recorded races (F-12).

**Cost.** Vectorise: a `(draws × rivals)` matrix of finish times is built
once per compute; each plan contributes one vector of `draws` times. With
320 draws, 20 rivals and 60 shortlisted plans this is of the order of the
current Monte Carlo. Keep the 60-plan shortlist.

**Screen.** In the plan table on STRATEGY, three small bars per plan: win,
podium, points; and under the recommended plan two sentences from the
sensitivities: "A safety car gains this plan one place on average. Rain
costs it two."

**Tests.** `tests/test_race_simulator.py`:
`test_every_plan_sees_the_same_races`,
`test_a_stop_that_is_still_to_come_gains_from_a_safety_car`,
`test_a_plan_on_slicks_loses_in_the_wet_draws`,
`test_no_safety_car_hazard_reproduces_the_current_distribution` (hazard 0,
one weather scenario, rivals at their expected laps: the position
distribution matches the current implementation's within sampling error).

---

## F-08

**Safety-car readiness.**

**Why.** The best moment to decide what to do under a safety car is before
it comes out. The engine can answer "if one came out this lap, would I
box?" every lap, and the driver can know the answer in advance.

**How.** Once per lap, after the lap's recompute, run two isolated computes
(`_compute_isolated`, as what-if does) on a copy of the state with
`race_control_phase` set to `"safety_car"` and to `"vsc"`. Publish

```jsonc
"if_neutralised": {
  "lap": 23,
  "safety_car": {"instruction": "Box this lap for hards.", "box_lap": 23, "fit_compound": "HARD",
                 "gain_vs_green_plan_s": 11.2, "position_change": 1},
  "vsc":        {"instruction": "Stay out.", "gain_vs_green_plan_s": 0.0, "position_change": 0}
}
```

Two extra computes a lap, not per recompute. After PF-01 that is about
0.3 s of worker time a lap.

**Screen.** STRATEGY, one line in the current-call block under "Changes
if": "If a safety car comes out this lap: box for hards, about 11 s
better." DRIVE: the same sentence appended to the existing
`strategyChange` element; no new element.

**Radio.** When the safety car does come out, the FLASH call can carry the
prepared instruction in the same breath, before the live recompute
finishes, provided `if_neutralised.lap` is the current lap and the pit
entry has not been passed. The live recompute then confirms or corrects it
as an ordinary strategy change.

**Tests.** `tests/test_strategy.py`:
`test_the_prepared_safety_car_call_matches_the_live_one` — compute
`if_neutralised`, then set the phase and recompute: same signature.
`test_readiness_is_computed_once_a_lap`.

---

## F-09

**Self-calibrating circuit model.** Addresses
[ST-04](03-strategy-core.md#st-04).

Every recorded race measures four things the planner currently takes from
hand-written tables.

| Quantity | Measured from | Rule |
| --- | --- | --- |
| Pit loss | each rival stop under green: `in_lap + out_lap − 2 × median(that car's clean laps within three laps either side)` | drop stops with a stationary time over 6 s (repairs, penalties) and any lap under a flag; median over 8 or more stops |
| AI stop wear | `pit_events[].wear_at_entry` by compound | median over 5 or more |
| Tyre severity | field median wear a lap by compound ÷ `DEFAULT_WEAR_PER_LAP` | median over 30 or more car-laps, restricted cars excluded |
| Safety-car hazard | safety-car periods ÷ green laps, per circuit | needs three races; otherwise the prior |

Overtaking difficulty is not in the table: counting passes that were made
on track, as opposed to in the pit cycle, needs F-12's replay to validate.
It stays a hand-written prior.

**Storage.** No schema change. One preference per circuit through the
existing `load_preference` / `save_preference`, key
`circuit_model:{track_id}`:

```jsonc
{"pit_loss_s": {"value": 21.4, "n": 23, "updated": "2026-11-10"},
 "stop_wear_pct": {"MEDIUM": {"value": 61.0, "n": 14}},
 "severity": {"value": 1.12, "n": 310},
 "safety_car_per_lap": {"value": 0.018, "races": 4}}
```

Written at final classification (`on_final_classification`) by a new
`circuit_model.py::learn_from_session(state)`, which reads `drivers[].
lap_history`, `pit_events` and `wear_history` from the last snapshot.

**Use.** `_base_pit_loss`, `TRACK_TYRE_SEVERITY` lookups and the rival
model's `stop_wear` read the measured value when it has its minimum count,
else the table. Every published figure names its source:
`base_pit_loss_source: "measured, 23 stops"` in place of
`"circuit_prior"`. A measured value more than 30% from its prior is not
used and is reported instead; that is a measurement error until someone
looks.

**Screen.** CONNECTION (calibration is pit-lane admin): a "Circuit model"
panel listing, for the current circuit and any other with data, each
quantity, its value, its source and count, with "Forget this circuit".

**Tests.** `tests/test_circuit_calibration.py`:
`test_pit_loss_is_in_lap_plus_out_lap_less_two_clean_laps`,
`test_a_repair_stop_is_not_a_pit_loss`,
`test_a_stop_under_the_safety_car_is_not_a_pit_loss`,
`test_seven_stops_are_not_enough`,
`test_a_measured_value_far_from_its_prior_is_reported_not_used`,
`test_the_planner_names_where_its_pit_loss_came_from`.

---

## F-10

**Forecast reliability log and wet-model calibration.**

Two questions this document could not answer from code, both answerable
from the driver's own races.

**What does the game's rain percentage mean?** Keep, per session, a log of
every change to the forecast: `{session_time_s, sky_now, samples: [[offset,
sky, chance]]}`, appended in `handle_PacketSessionData` when the tuple
differs from the last one, capped at 600 entries, written into the session
export and into a preference `forecast_log:{session_uid}` at the end of the
session. `tools/forecast_reliability.py` reads every stored log and prints:

- for each published chance in bands of 20 points and each sample sky:
  how often the sky was wet when that sample fell due;
- persistence: the share of wet-now moments still wet 5, 10 and 15 minutes
  later, split by the present chance;
- the value of `_FORECAST_PERSISTENCE` that best fits (WX-01);
- the same split by `forecast_accuracy`, which settles whether a perfect
  forecast's sky is simply what happens.

**How wet is wet?** `tools/wet_model_fit.py` reads the per-lap surface
record of F-02 and the archived field laps of wet sessions and fits, in
this order, each only when it has the laps:

1. the soak and drying rates, from the lap-by-lap field pace ratio after a
   change of sky (10 or more transitions);
2. the slick penalty curve, from slick runners' ratios against the fitted
   surface;
3. the crossover, from laps on which both tyre classes ran (`compound_split`
   with three or more cars each side).

It prints the fitted constants beside the current ones and writes them to a
preference `wet_model:fitted`. `rain.py` reads that at import through one
function, `calibrated(name, default)`, and the wall drops its "estimates"
line when it is present. `tests/test_rain_model.py` pins the two crossovers
at 112% and 118% of dry pace; a fitted model keeps those tests by scaling,
or the tests are rewritten to pin the fitted values with the evidence in
the docstring.

**Screen.** CONNECTION, beside the circuit model: "Weather model — fitted
from N wet laps across M sessions" or "not yet fitted".

**Tests.** `tests/test_forecast_log.py`:
`test_an_unchanged_forecast_is_logged_once`,
`test_the_log_survives_the_session_export`;
`tests/test_wet_model_fit.py`, on synthetic laps generated from known
constants: the fit recovers them within 10%.

---

## F-11

**Strategy debrief.**

**Why.** After a race the driver should be able to see every call: what the
engine recommended, what it said, what the driver did, and what it cost.
The pieces are stored already — strategy snapshots (`save_strategy_snapshot`),
proactive calls with their delivery outcome (`save_proactive_call`), radio
traces (5.6.0), the lap chart and the final classification. Nothing joins
them.

**Data.** `session_analysis.py::strategy_review(session_id) -> dict`:

```jsonc
{
  "stops": [{"lap": 17, "from": "MEDIUM", "to": "HARD", "phase": "green",
             "recommended_lap_at_the_time": 15, "called_on_lap": 15, "heard": true,
             "hindsight_best_lap": 14, "cost_vs_hindsight_s": 4.2, "places": 1}],
  "weather": [{"spell": 1, "rain_from_lap": 11, "surface_crossed_on_lap": 12,
               "first_heads_up_lap": 11, "box_called_on_lap": 13, "stopped_on_lap": 13,
               "laps_late": 1, "cost_s": 5.1, "field_median_stop_lap": 13}],
  "calls": [{"lap": 15, "type": "strategy_change", "text": "Box lap 15 for hards.",
             "outcome": "delivered", "wait_s": 2.1}],
  "overrides": [{"lap": 15, "kind": "declined", "engine": "Box lap 15", "driver": "stay out"}],
  "undelivered": 3
}
```

`hindsight_best_lap` re-runs the one-stop comparison with the lap times
that were actually set — the player's and each rival's — in place of the
projection. It is a comparison of stop laps on known pace, not a claim
about what should have been known.

**Screen.** Session Analysis, the existing strategy tab ("Stints in
finishing order"): a "Calls" lane on the stint chart with a marker per
call, coloured by outcome and labelled with its word, and a table of stops
with the columns above. A stop whose hindsight cost is under 1.5 s reads
"on the money".

**Tests.** `tests/test_session_analysis.py`:
`test_a_stop_two_laps_after_the_call_is_shown_as_such`,
`test_hindsight_uses_the_laps_that_were_set`,
`test_a_call_that_was_never_spoken_is_counted`.

---

## F-12

**Strategy replay harness.**

**Why.** Every item in this document changes what the engine says during a
race. The unit tests check functions; nothing checks the sequence of calls
over a race. A recorded race is the regression test.

**What exists.** `replay.py` replays a capture into the UDP handler.
`tools/replay_demo.py` and `tools/demo_broadcast.py` synthesise a race as
real packets. `CaptureService` records one.

**Build.** `tools/strategy_replay.py`:

```bash
python -m tools.strategy_replay CAPTURE [--expect expectations.json] [--timeline out.json]
```

1. Build the stack the app builds (`StateStore`, `PitWallDatabase` on a
   temporary file, `AnalysisEngine`, `StrategyEngine`, `ProactiveEngineer`)
   with a voice stub whose `speak_text` records the text and returns `True`
   and a brain stub whose `proactive` raises, so every call is its
   template. No provider, no audio device.
2. Feed the capture at packet time (no sleeping; advance a fake clock from
   `session_time`). Patch `time.time` / `time.monotonic` in `proactive` and
   `strategy` through one injected clock; where a module reads the clock
   directly, add a `clock` parameter defaulting to the real one.
3. Record a timeline: every change of `strategy.recommended` signature,
   every change of `weather_crossover` level, every queued and delivered
   call, with lap, lap distance and session time.
4. `--expect`: a list of assertions over the timeline.

   ```jsonc
   [{"first": {"type": "weather_crossover", "level": 4}, "by_lap": 13},
    {"never": {"text_contains": "Stay out — boxing costs"}, "while": {"weather_level_at_least": 3}},
    {"count": {"type": "weather_crossover", "level": 4}, "at_most": 2},
    {"every": {"type": "rival_pitting"}, "satisfies": {"relevance_at_least": 2}}]
   ```

**Fixtures.**

1. Extend the demo generator with `--rain-from-lap N --rain "Light rain"
   --forecast-chance 50`: the sky label, the forecast samples and every
   car's lap and sector times follow `rain.py`'s own curves, and AI cars
   stop for intermediates two laps later. Commit the generated capture's
   **recipe**, not the capture; generate it in the test.
2. One real wet race and one real dry race recorded by the owner, kept
   outside the repository, referenced by checksum, run before a release.

**Tests.** `tests/test_strategy_replay.py`:
`test_a_synthetic_wet_race_is_called_in_order` (the F-03 sequence),
`test_a_synthetic_dry_race_makes_no_weather_call`,
`test_the_same_capture_gives_the_same_timeline_twice`.

**Done when.** The harness runs in the test suite in under 20 s for a
synthetic 20-lap race and its timeline is byte-identical between runs.
