# Part 1 — Wet weather

The wet model in `src/pitwall/rain.py` is well constructed: one surface
variable, four evidence channels, every compound priced over the laps that
are left. The defects below are in how the forecast is read, how a lap time
is dated, and how the verdict travels to the radio. None of them needs the
model replaced.

Contents: [how the call is made today](#how-the-call-is-made-today) ·
[WX-01](#wx-01) · [WX-02](#wx-02) · [WX-03](#wx-03) · [WX-04](#wx-04) ·
[WX-05](#wx-05) · [WX-06](#wx-06) · [WX-07](#wx-07) · [WX-08](#wx-08) ·
[WX-09](#wx-09) · [WX-10](#wx-10) · [WX-11](#wx-11) · [WX-12](#wx-12) ·
[WX-13](#wx-13) · [WX-14](#wx-14)

## How the call is made today

Follow one rain shower from the packet to the driver's ear. Every stage adds
delay or can lose the call; the items below are numbered against it.

| # | Stage | Code | Latency or loss |
| --- | --- | --- | --- |
| 1 | Session packet sets `state.weather` and the forecast samples | `udp.py:1254` | 0.5 s (2 Hz packet) |
| 2 | A completed lap is stamped with the weather at its **end**, and with `weather_change` if the label moved during it | `state.py:1300`, `state.py:990` | one lap |
| 3 | Surface model steps once per completed lap, plus one whole step for the lap in progress | `rain.py:262` | one lap; jumps at the line (WX-02) |
| 4 | Pace evidence: the player's last clean lap and each rival's last valid lap, against a dry benchmark | `rain.py:364`, `rain.py:462` | one lap; laps filtered out (WX-06, WX-07) |
| 5 | Forecast scenarios split on the published rain chance | `rain.py:890` | suppresses the call at ≤50% (WX-01) |
| 6 | `evaluate` prices every compound and box lap; `should_change` needs margin > uncertainty and the best lap to be this lap | `rain.py:1074` | flips with stage 4 (WX-02) |
| 7 | Strategy recompute is triggered by a change in its input signature | `proactive.py:1537` | up to 0.15 s + ~0.3 s compute |
| 8 | A weather plan is forced into the recommendation when the stop is this lap | `strategy.py:3902` | — |
| 9 | Radio: `weather_crossover` is queued on the first tick the track counts as wet, once | `proactive.py:1838` | the box call is never queued (WX-03) |
| 10 | Radio: `strategy_change` carries the stop instead, through the stay-out guard | `proactive.py:2128`, `brain.py:3280` | can be reversed (WX-04) |
| 11 | Narration by the model, up to 6 s, then speech | `proactive.py:2602` | 2–6 s (WX-09) |

---

## WX-01

**The forecast chance is never conditioned on the rain that is already
falling.** Severity S1. Evidence: reproduced.

**Where.** `rain.py:890` `project_wetness_scenarios`, in particular
`probability()` at `rain.py:931` and the branch at `rain.py:957`.

**What happens.** Every future forecast sample splits the projection into a
rain outcome with probability `rain_pct / 100` and a no-rain outcome whose
surface target is `0.0`. The observed weather is used only until the first
future sample falls due, about three laps ahead. From then on a sample that
reads "Light rain, 50%" sends half the probability to a track that starts
drying, even when it is raining now and has been for eight laps.

An intermediate on a drying track is priced at up to 8.5% of a lap
(`_INTER_DRY_MISMATCH`), a slick on a wet one at 5.5% (`0.749 × 0.52⁴`). The
two halves cancel and `best_compound` stays the fitted slick.

`python -m tools.rain_onset_probe` — light rain starts on lap 11 of 40, the
surface and every lap time follow the model's own physics:

```text
scenario                                                             first call   held  slick loss
forecast 100% light rain                                             lap +0       no    -4.7 s
forecast 70% light rain                                              lap +0       no    -4.7 s
forecast 50% light rain                                              never        -     -
forecast 30% light rain                                              never        -     -
forecast 50%, approximate accuracy                                   lap +0       no    -4.7 s
forecast 50%, no field history                                       never        -     -
forecast 50%, onset lap excluded as weather_change, next lap yellow  never        -     -
forecast 50%, forecast category Overcast                             never        -     -
dry control, forecast 50%                                            false calls: 0
```

At 50%, lap 19, with the surface at 0.51 (crossover 0.35) and nineteen cars
measured at 0.51 with confidence 0.95:

```text
 19  0.516  0.511  0.518  0.51  0.95  MEDIUM  None     0.0   10.4  False
     Track is wet and drying through the stint; HARD is the tyre for it, but with
     22 lap(s) left a stop cannot repay the pit lane. Stay out and manage it.
```

"Approximate accuracy" calls the stop only because it adds a 45% scenario
that ignores the forecast altogether, which is the opposite of what a less
reliable forecast should do.

**Why.** The module's own docstring states the modelling choice: *"Use one
shared probability rank through the horizon: rain persists within a sample
rather than being independently redrawn each lap."* Under that model the
present is one more sample of the same rank. Rain falling now with a present
chance `p₀` means the rank lies below `p₀`; a future sample with chance `pᵢ ≥
p₀` is then certain, not `pᵢ`. The code builds the scenarios from the shared
rank and then forgets to place the observation on it.

`tests/test_rain_probability.py` pins that the percentage is a probability
and never an intensity, and that the published marginals are preserved. That
stays true. Every test there starts from a dry sky with no present chance,
where conditioning changes nothing.

**Change.**

1. Add to `rain.py`, above `class WetnessScenario`:

   ```python
   # Share of a forecast chance that persists with the conditions observed
   # now. EA publishes marginal chances and no correlation; the scenarios
   # below already assume one shared rank through the horizon, and the
   # present is one more sample of that rank. The remainder is treated as
   # independent of the present. A judgement: replace it with the measured
   # persistence once the forecast reliability log (F-10) has data.
   _FORECAST_PERSISTENCE = 0.8


   def conditioned_rain_probability(
       chance: float, now_chance: float | None, raining_now: bool
   ) -> float:
       """Chance of rain at a future sample, given what is falling now."""
       chance = max(0.0, min(1.0, float(chance)))
       if now_chance is None:
           return chance
       now_chance = max(0.0, min(1.0, float(now_chance)))
       if raining_now:
           dependent = 1.0 if now_chance <= 0.0 else min(1.0, chance / now_chance)
       elif now_chance >= 1.0:
           return chance
       else:
           dependent = max(0.0, (chance - now_chance) / (1.0 - now_chance))
       return _FORECAST_PERSISTENCE * dependent + (1.0 - _FORECAST_PERSISTENCE) * chance
   ```

2. In `project_wetness_scenarios`, before `def probability`, read the
   offset-zero sample's chance (`None` when there is no such sample — do
   **not** fall back to `rain_now_pct`, which is `0` when the sample is
   absent and would be read as "0% now") and `raining_now = now_target > 0.0`.
   Return `conditioned_rain_probability(pct / 100, now_chance, raining_now)`
   from `probability`. Leave the `pct is None` branch as it is.

3. Nothing else in the function changes. `boundaries` is built from
   `probability(sample)` and so follows.

Worked values with `_FORECAST_PERSISTENCE = 0.8`:

| Sky now | Present chance | Sample chance | Was | Becomes |
| --- | --- | --- | --- | --- |
| raining | 50% | 50% | 0.50 | 0.90 |
| raining | 50% | 30% | 0.30 | 0.54 |
| raining | 10% | 60% | 0.60 | 0.92 |
| raining | 80% | 20% | 0.20 | 0.24 |
| dry | 10% | 70% | 0.70 | 0.67 |
| dry | 50% | 50% | 0.50 | 0.10 |
| dry | 0% | 20% | 0.20 | 0.20 |
| dry, no present sample | — | 20% | 0.20 | 0.20 |

4. A confirmation gate, so that the corrected probability cannot call a stop
   on a label alone. The soak rate (`_SOAK_RATE_PER_LAP = 0.45`) is a
   judgement and the game's surface may wet more slowly. In `evaluate`, after
   `should_change` is computed:

   ```python
   grip = reading.get("driver_report") or {}
   split = reading.get("compound_split") or {}
   to_wet = best_compound in WET_COMPOUNDS and current not in WET_COMPOUNDS
   to_dry = best_compound in DRY_COMPOUNDS and current in WET_COMPOUNDS
   measured = reading.get("measured_wetness")
   measured_ok = float(reading.get("pace_confidence", 0.0)) >= 0.5 and measured is not None
   confirmed = change_during_suspension or not (to_wet or to_dry) or (
       (to_wet and (
           wetness >= WETNESS_SLICK_INTER - 0.02
           or (measured_ok and float(measured) >= 0.20)
           or float(split.get("wet_advantage_fraction", 0.0) or 0.0) > 0.0
           or grip.get("category") in {"damp", "soaked", "flooded"}
       ))
       or (to_dry and (
           wetness <= WETNESS_SLICK_INTER + 0.02
           or (measured_ok and float(measured) <= 0.30)
           or float(split.get("wet_advantage_fraction", 0.0) or 0.0) < 0.0
           or grip.get("category") in {"drying", "dry_line"}
       ))
   )
   if should_change and not confirmed:
       should_change = False
       upcoming = {
           "lap_offset": 1,
           "compound": best_compound,
           "gain_s": round(margin_s, 1),
           "projected_wetness": trajectory[min(1, len(trajectory) - 1)],
           "minutes_away": int(round(base_lap_s / 60.0)),
           "benefiting_laps": max(0, len(trajectory) - 2),
           "awaiting_confirmation": True,
       }
   ```

   Add `"confirmed": confirmed` to the returned dictionary. Item WX-08 adds a
   fifth confirmation, sector pace, which brings the wait down from a lap to
   about thirty seconds.

5. Do **not** make the sample's weather category outrank its percentage.
   `test_low_probability_heavy_rain_keeps_full_conditional_wetness` and
   `test_missing_chance_and_unspecified_rain_severity_have_explicit_fallbacks`
   pin that a "Heavy rain, 0%" sample produces no rain, and that was a
   deliberate decision. Whether a perfect-accuracy forecast's category is in
   fact what happens is a question for the reliability log in F-10, not for
   this change.

**Tests.** New file `tests/test_rain_conditioning.py`.

- `test_rain_falling_now_makes_an_equal_future_chance_near_certain` — state
  with `weather="Light rain"`, samples at 0 and 5 min both `rain_pct=50`.
  `project_wetness_scenarios(state, 0.5, 20, 90)`. Sum of the probability of
  scenarios whose trajectory stays above 0.35 at index 10 is `pytest.approx(0.9)`.
- `test_a_dry_sky_under_the_same_chance_discounts_it` — same samples,
  `weather="Overcast"`. Probability of scenarios that get wet is `approx(0.1)`.
- `test_no_present_sample_leaves_the_published_chance_alone` — one sample at
  3 min, 20%, dry sky: wet probability `approx(0.2)`.
- `test_rain_under_a_zero_present_chance_trusts_the_sky` — raining, present
  chance 0, future 40%: wet probability `approx(0.8 + 0.2 * 0.4)`.
- `test_a_fifty_percent_forecast_does_not_hold_a_soaked_car_out` — the
  probe's 50% scenario at lap 14: `decision["should_change"] is True`,
  `decision["best_compound"] == "INTER"`.
- `test_a_label_alone_does_not_call_the_stop` — the probe's 100% scenario at
  lap 11 with `lap_distance_m` 10% of the lap and no rival history:
  `should_change is False`, `upcoming_change["awaiting_confirmation"] is True`.
- `test_a_driver_report_confirms_the_label` — the same state plus
  `driver_grip_feedback={"lap": 11, "category": "damp", "confidence": 1.0}`:
  `should_change is True`.

Existing tests: none change for steps 1–3 (the reference patch passes the
whole suite). Step 4 may move `test_settled_rain_on_slicks_calls_the_stop`
only if that state sits below the crossover with no evidence; if it does,
give it the evidence a settled wet race has.

**Done when.** `python -m tools.rain_onset_probe --assert-within 2` exits 0.

---

## WX-02

**A completed lap's pace is compared with the surface now, so the call is
withdrawn one lap after it is made.** Severity S1. Evidence: reproduced.

**Where.** `rain.py:707` `estimate_wetness` — the blend at `rain.py:758` and
the bias at `rain.py:776`; `rain.py:296`, the step for the lap in progress.

**What happens.** In the probe's 100% scenario the surface model is right
and the call is made on lap 11. On lap 12 the first wet lap has completed. It
was run while the surface rose from 0 to 0.23, so the field's pace fits a
wetness of 0.12. The estimate takes that as the surface **now**, against a
surface model that reads 0.36:

```text
lap  true   est    surf   meas  conf  best    off  margin  needs  box
 11  0.000  0.167  0.234  0.03  0.33  INTER     0   109.6   11.1  True
 12  0.234  0.156  0.363  0.12  0.97  MEDIUM None     0.0    5.4  False   <- withdrawn
 13  0.363  0.320  0.433  0.30  0.97  INTER     0    12.8    7.0  True    <- called again
```

The estimate is dragged to 0.156, below the truth of 0.234. Worse,
`equilibrium_bias` becomes `(0.156 − 0.363) × 0.97 = −0.20` and is added to
every future target, so light rain is projected to settle at 0.32, under the
crossover, and the stop disappears. The comment above the bias says it
exists to catch "this rain is not wetting this track the way the label
says"; here it is catching nothing but the age of the measurement.

Separately, `wetness_from_history` adds one full lap of soaking for the lap
in progress whether the car is at the start line or the last corner. The
surface model therefore jumps at the line and runs up to a lap ahead of the
track.

**Change.**

1. Split the per-lap integration out of `wetness_from_history` into
   `surface_series(samples, *, track_temp_c, active_cars) -> list[float]`:
   the surface at the start of the window followed by its value at the end
   of each lap. `wetness_from_history` gains `lap_fraction: float = 1.0` and
   applies `step_wetness(series[-1], current_target, laps=lap_fraction, …)`.
2. In `estimate_wetness`, compute `lap_fraction = lap_distance_m /
   track_length_m` clamped to 0..1 when `track_length_m > 100` and
   `lap_distance_m` is finite, else `1.0` (states without a lap distance keep
   today's behaviour). Pass it through.
3. Compute `surface_when_measured = (series[-2] + series[-1]) / 2` when the
   series has two entries, else `dynamic`. Use it as the prior passed to
   `fit_wetness`.
4. Replace the blend and the bias:

   ```python
   innovation = measured - surface_when_measured
   wetness = dynamic
   if pace_confidence > 0.0:
       wetness += innovation * min(0.85, pace_confidence)
   …
   if pace_confidence >= 0.35:
       bias = innovation * min(0.85, pace_confidence) * min(1.0, pace_confidence)
   ```

   When the weather has been steady for two laps `series[-2] ≈ series[-1] ≈
   dynamic`, so a settled race behaves exactly as before. Only a transition
   changes.
5. Add `"surface_when_measured"` and `"lap_fraction"` to the returned reading
   and to `wetness_source` in `strategy.py:1431`, so the Strategy page and
   the decision log can show them.
6. `state.py` already exposes `lap_distance_m` and `track_length_m` in every
   snapshot profile; nothing to add there.

**Tests.** In `tests/test_rain_model.py`:

- `test_the_first_wet_lap_does_not_talk_the_model_out_of_the_rain` — the
  probe's 100% state at lap 12. `reading["wetness"]` is within 0.03 of
  `rain.step_wetness(0.0, 0.52, track_temp_c=28, active_cars=20)` (0.234) plus
  the fraction of lap 12 driven; `abs(reading["equilibrium_bias"]) < 0.03`.
- `test_the_lap_in_progress_soaks_by_the_distance_driven` — two identical
  states, `lap_distance_m` at 5% and 95% of `track_length_m`; the second
  `dynamic_wetness` is higher, and the first is within 0.02 of the surface at
  the end of the last completed lap.
- `test_a_settled_wet_race_reads_as_it_did` — ten laps of `"Light rain"`
  history with slick laps at the 0.52 penalty; `reading["wetness"]` equals
  the value at `1f35a7e` to three decimals (compute it once and pin it).

**Done when.** In the probe output the **held** column reads `yes` for every
scenario that calls the stop.

---

## WX-03

**The weather radio trigger is a one-shot latch.** Severity S1. Evidence:
reproduced.

**Where.** `proactive.py:1838`–`1862`.

```python
wet = bool(crossover) and (
    bool(crossover.get("worth_stopping"))
    or bool(crossover.get("ask_driver"))
    or float(crossover.get("wetness", 0.0) or 0.0) >= rain.WETNESS_SLICK_INTER
)
if wet and not self._last_weather_alert:
    self._enqueue("weather_crossover", {...}, critical=True, cooldown_s=0.0)
self._last_weather_alert = wet
```

**What happens.** `wet` is true for three different reasons and the call is
queued only on its rising edge.

- Rain starts, the surface passes 0.35, the verdict is "not worth the stop
  yet". The call is queued once and says so. Two laps later the verdict
  becomes "box now". `wet` was already true, so nothing is queued. The driver
  is never told to box by the weather call.
- A wet race on intermediates. `wet` is true throughout. The track dries and
  the verdict becomes "box for slicks". Nothing is queued.
- The verdict is withdrawn. Nothing tells the driver.

The stop still reaches the radio as a `strategy_change`
(`proactive.py:2128`), but only when the plan signature changes, the plan is
not held by the stability rule, and `_urgent_strategy` allows it — and then
as an IMPORTANT call with an 8-second spacing, without the weather reason,
through the guard described in WX-04.

`python -m tools.strategy_probe`, part 5, runs the real detector twice:
once with a published verdict of "wet, staying out", once with "box this
lap for inters".

```text
5. The weather radio trigger
   track wet, watching -> weather_crossover: Track is wet; staying out for now.
   verdict: box now    -> strategy_change: Stay out — boxing costs you 7 places and gains nothing.
   verdict: box now    -> no weather call queued
```

The driver is told the track is wet and they are staying out, and then that
boxing gains nothing. The stop the engine has called is never said.

**Change.** Replace the latch with a level and a content signature. This is
the radio half of F-03 in Part 5; the full protocol is specified there. The
minimum for this item:

1. In `proactive.py` add

   ```python
   WEATHER_NONE, WEATHER_WATCH, WEATHER_ASK, WEATHER_STANDBY, WEATHER_BOX = range(5)
   # Below the crossover but wet enough that a driver should hear about it.
   WEATHER_WATCH_WETNESS = 0.15
   ```

   and a static method `_weather_call(state) -> dict[str, Any]` returning
   `{"level", "compound", "box_lap", "direction"}`:

   - `crossover = state["strategy"].get("weather_crossover") or {}`; empty →
     `WEATHER_NONE`.
   - `direction` is `"to_wet"` when the named compound is INTER or WET and
     the fitted one is not, `"to_dry"` for the reverse, `"wet_to_wet"` for
     INTER↔WET, else `""`.
   - `WEATHER_BOX` when `worth_stopping` and `box_lap is not None` and
     `int(box_lap) <= current_lap`.
   - `WEATHER_STANDBY` when `worth_stopping` and `box_lap` is a later lap.
   - `WEATHER_ASK` when `ask_driver`.
   - `WEATHER_WATCH` when `wetness >= WEATHER_WATCH_WETNESS`.
   - otherwise `WEATHER_NONE`.

2. Replace `self._last_weather_alert: bool` with `self._last_weather_call:
   dict[str, Any] = {"level": WEATHER_NONE}` (constructor at
   `proactive.py:488`, reset at `proactive.py:821`).

3. Queue a `weather_crossover` when any of these holds, comparing the new
   call with the last one:

   - the level rose;
   - the level is STANDBY or BOX and the compound changed, or the box lap
     moved earlier, or it moved later by two laps or more;
   - the level is WATCH or ASK and the direction changed.

   Queue a `weather_stand_down` (new event type, CRITICAL, coalesced,
   `expires_s=30`) when the last call was STANDBY or BOX, the new level is
   below STANDBY, and the fitted compound has not changed since.

   Record the new call as `self._last_weather_call` only when `_enqueue`
   returned `True`, as the damage detector already does
   (`proactive.py:1969`). The current code records the state whether or not
   the call was queued, which is how a suppressed or cooled-down call is
   lost for good.

4. Add `level`, `direction` and `current_compound` to the payload. Add
   `"level"` and `"direction"` to `_NARRATION_FACTS["weather_crossover"]`
   (`proactive.py:309`).

5. `_material_signature` (`proactive.py:3116`): return `(level, compound,
   box_lap, direction)`.

6. `_event_still_relevant` (`proactive.py:1177`): a queued weather call is
   relevant only while `_weather_call(state)["level"] >= payload["level"]`
   and, for STANDBY and BOX, the compound still matches. A queued "box" that
   the engine has since withdrawn must not be spoken. For
   `weather_stand_down`: relevant while the current level is below STANDBY.

7. `_safety_critical` (`proactive.py:2573`): return `True` for
   `weather_crossover` at `WEATHER_BOX` and for `weather_stand_down`. A
   driver who silenced weather chatter still has to hear the stop.

8. `_fallback_text`: add the stand-down template —
   `f"Cancel the stop. Stay out on the {current}s. {reason}"` — and make the
   BOX template lead with the instruction (see WX-09).

9. Add `"weather_stand_down": CRITICAL` to `EVENT_PRIORITY`, add it to the
   `coalesced` set in `_enqueue` and to `SUPPRESSIBLE_SUBJECTS["weather"]`
   and `["rain"]`.

**Tests.** New file `tests/test_weather_call_protocol.py`, built on the
`ProactiveEngineer` fixtures in `tests/test_proactive.py`.

- `test_a_watch_that_becomes_a_stop_is_called_again` — detect with a
  crossover of `wetness 0.4, worth_stopping False`; then with
  `worth_stopping True, box_lap == current_lap, compound "INTER"`. Two
  `weather_crossover` events were queued, the second with `level == 4`.
- `test_the_crossover_back_to_slicks_is_called` — fitted INTER, level WATCH
  for several ticks, then `worth_stopping True, compound "MEDIUM"`. One new
  event with `direction == "to_dry"`.
- `test_a_withdrawn_stop_is_stood_down_once` — BOX delivered, then the
  crossover reports `worth_stopping False`. One `weather_stand_down`; a
  second detect pass queues nothing.
- `test_an_unchanged_stop_is_not_repeated` — BOX on ten consecutive ticks:
  one event.
- `test_a_stop_refused_by_the_cooldown_is_tried_again` — make `_enqueue`
  return `False` once; the next tick queues it.
- `test_silenced_weather_still_boxes` — standing instruction "stop telling me
  about the weather"; WATCH is suppressed, BOX is queued.
- `test_a_queued_box_is_dropped_when_the_engine_withdraws_it` — queue BOX,
  change the published crossover to `worth_stopping False`, call `_prune`:
  the queue is empty.

**Done when.** The seven tests pass, `tests/test_proactive*.py` still pass,
and `python -m tools.strategy_probe --assert` prints no failure for part 5.

---

## WX-04

**The radio's stay-out guard overrules a weather stop, and the finish
projection that feeds it under-credits a wet-tyre advantage.** Severity S1.
Evidence: reproduced.

**Where.** `brain.py:3280` `_strategy_guard_override`; `strategy.py:1810`
`_expected_positions_recovered`; `strategy.py:1892`; `strategy.py:3902`.

**What happens.** `python -m tools.strategy_probe`, part 3: lap 30 of 40,
heavy rain since lap 26, leading on hards with the compound rule already
met.

```text
engine: Box lap 30 for INTER.
the stop is 36.82 s quicker to the flag than staying out | projected P3,
   -2 place(s) against staying out, 7 lost in the lane
radio:  Stay out — boxing costs you 7 places and gains nothing.
```

Three parts of the engine give three answers.

1. `rain.evaluate`: box now, worth 31 s.
2. The plan ranking: stay out, because the stop "projects P3". The stop is
   37 s quicker to the flag, but `projected_position = max(raw_position,
   best_from_rejoin)` and `best_from_rejoin` allows only five of the seven
   places back. The recovery model charges each pass a fixed tax of `1 + 6 ×
   difficulty` seconds on top of the typical gap, keeps 60% of the pace
   advantage (`_ADVANTAGE_RETENTION`), rounds to two decimals and then
   floors — here `floor(5.99) = 5`. That model was tuned for a few tenths a
   lap on a fresher slick. A car on intermediates lapping 6.6 s quicker than
   cars on slicks in the rain does not pay 3.3 s to pass each of them.
3. `force_weather` overrides the ranking and recommends the stop. The radio
   then reads `positions_gained_vs_stay_out = −2`, `positions_lost_by_stopping
   = 7` and, because the stop is neither mandatory nor a tyre that cannot
   reach the flag, says "Stay out".

**Change.**

1. `brain.py:3320`, the forced-stop test in `_strategy_guard_override`: a
   stop the weather called is forced.

   ```python
   weather = recommended.get("weather_crossover") or {}
   stop_is_forced = (
       "mandatory" in stop_reason
       or "cannot reach" in stop_reason
       or bool(weather.get("worth_stopping"))
   )
   ```

2. `strategy.py:4128`, `stop_required_reason`: add a first branch —
   `"track conditions"` when `best.get("weather_crossover", {}).get(
   "worth_stopping")` — and the same in `_refresh_held_plan_details`
   (`strategy.py:2757`). The radio header and the Strategy page read this
   string.

3. `_expected_positions_recovered`: price each pass against the pace
   difference that makes it.

   ```python
   # Seconds a lap above which a pass costs no more than the gap: the slower
   # car cannot defend. A judgement; F-09 replaces it per circuit.
   _FREE_PASS_PACE_DELTA_S = 2.5

   ease = min(1.0, max(0.0, pace_advantage / _FREE_PASS_PACE_DELTA_S))
   overtake_tax = (1.0 + 6.0 * difficulty) * (1.0 - ease)
   retention = _ADVANTAGE_RETENTION + (1.0 - _ADVANTAGE_RETENTION) * ease
   time_budget = pace_advantage * laps_after_stop * retention
   ```

   Return the unrounded float; round only where it is published
   (`"expected_positions_recovered": round(recovered, 2)` at
   `strategy.py:1906`), and floor the unrounded value at `strategy.py:1892`
   and `strategy.py:1937`.

4. Compare with the cars actually ahead after the stop, not the whole
   field's median. In `_expected_positions_recovered` replace `nearby` with
   the `pace_s` of rivals whose `position` satisfies `current < position <=
   rejoin` — the cars the stop puts in front. Fall back to the field median
   when that set is empty.

5. In `compute`, when `force_weather` is true and the forced plan ranks
   below `automatic_best`, record the disagreement instead of hiding it:
   `best["weather_override"] = {"ranked_first": <signature of
   automatic_best>, "time_gain_s": <stay-out time − forced time>}`. The
   Strategy page shows it in the reasoning block; F-11 logs it.

**Tests.**

- `tests/test_radio_failure_recovery_5_3_2.py` or a new
  `tests/test_weather_stop_guard.py`:
  `test_the_guard_never_talks_a_driver_out_of_a_weather_stop` — a
  recommended plan with `positions_gained_vs_stay_out=-2`,
  `positions_lost_by_stopping=7`, `stop_required_reason="best projected…"`,
  `weather_crossover={"worth_stopping": True}`.
  `EngineerBrain._strategy_guard_override(plan, 30, {"compound": "HARD"}) is
  None`.
- `test_a_pace_stop_that_gains_nothing_is_still_refused` — the same plan
  without `weather_crossover`: the stay-out sentence is returned.
- `tests/test_strategy.py`:
  `test_a_six_second_advantage_recovers_the_places_it_cost` — the probe's
  state; the inter plan's `projected_finish_position == 1`.
- `test_a_two_tenths_advantage_still_pays_the_overtaking_tax` — an existing
  dry scenario from `tests/test_race_regressions_4_9_3.py` (the Las Vegas
  lap-19 stop): the projection is unchanged to the position.

**Done when.** `python -m tools.strategy_probe --assert` prints no failure
for part 3, and `tests/test_race_regressions_4_9_3.py` still passes.

---

## WX-05

**The spoken reason names a dry compound as "the tyre for it" on a wet
track.** Severity S2. Evidence: reproduced.

**Where.** `rain.py:1247`–`1250` (`pace_best`), `rain.py:1384`–`1395`,
`rain.py:1338`–`1350`.

**What happens.** `pace_best` is `min(penalties, key=…)` over the
candidates. Dry compounds share one weather penalty, so the tie is broken by
dictionary order, which is alphabetical: HARD. On mediums the function then
believes "the right tyre differs from the fitted one, and staying out is
still the call" and says:

> Track is wet and drying through the stint; HARD is the tyre for it, but
> with 22 lap(s) left a stop cannot repay the pit lane. Stay out and manage
> it.

Every clause is wrong: the track is not drying (half the scenarios dry it),
HARD is not the tyre for a wet track, and 22 laps is ample distance. The
sentence is the `reason` of the published crossover; the weather template
speaks it verbatim (`proactive.py:2379`) and DRIVE shows it.

**Change.**

1. `pace_best`: compare by weather class. Compute it as today, then
   `if pace_best in DRY_COMPOUNDS and current in DRY_COMPOUNDS: pace_best =
   current`.
2. `_explain`, the "right tyre differs" branch: give it the real reason. Pass
   `remaining_laps` and the pit loss in. Say "cannot repay the pit lane" only
   when `laps_left * best_per_lap_gain < overhead`; otherwise say what is
   true:

   ```python
   f"Track is {surface} and {direction}; {pace_best} is quicker now, but the "
   f"forecast is too uncertain to commit — it needs {uncertainty_s:.0f}s and "
   f"shows {margin_s:.0f}s. Stay out for now{tail}."
   ```

3. `direction`: read it from the next three laps of the trajectory, not the
   last lap of the race: `near = trajectory[min(2, len(trajectory) - 1)]`.
   "Drying through the stint" stays available as a second clause when the
   end of the race differs from the near term by more than 0.15.
4. Say "lap" or "laps", not "lap(s)". The radio reads it aloud.

**Tests.** In `tests/test_rain_model.py`:

- `test_a_dry_compound_is_never_named_as_the_wet_answer` — parametrised over
  fitted SOFT, MEDIUM, HARD, the probe's 50% state at laps 12–19: no reason
  contains `"HARD is the tyre"`, `"SOFT is the tyre"` or `"MEDIUM is the
  tyre"`.
- `test_the_reason_does_not_blame_distance_with_half_a_race_left` — the same
  state: `"cannot repay the pit lane"` is absent while `remaining_laps > 10`.
- `test_raining_now_is_not_described_as_drying` — light rain observed,
  forecast 50%: the reason does not contain `"drying"`.

**Done when.** The probe's verbose output has no line containing "is the
tyre for it" with a dry compound, and `--assert-within` no longer reports
it.

---

## WX-06

**The evidence filters drop the laps that carry the signal.** Severity S2.
Evidence: read.

**Where.** `rain.py:418`–`428`; `rain.py:355`–`361`; `rain.py:484`;
`state.py:965`–`1003`; `tyre_learning.py:25`.

**What happens.** `player_pace_observation` keeps a lap only when
`exclusion_reason(lap) is None`. That function was written for tyre
learning, where a doubtful lap should be left out. It returns the first
entry of `learning_exclusions`, which `_mark_learning_context_locked`
fills with:

| Tag | Set when | Effect on the wet read |
| --- | --- | --- |
| `weather_change` | the weather label changed during the lap | the lap the rain arrived on is dropped |
| `traffic` | a car within 1.5 s ahead at any point above 50 km/h | most racing laps in a pack are dropped |
| `invalid_lap` | track limits | common in the wet; the lap is dropped |
| `neutralised_lap` | safety car, VSC, red flag — **or a yellow shown to this car** | a spin two cars ahead drops the lap |

The field channel is blocked outright by `_field_pace_block_reason` when the
player's last completed lap carries `neutralised_lap`. A local yellow in one
marshal sector — the commonest event in the first wet laps — therefore
switches off the measurement of nineteen other cars for a lap.

**Change.**

1. `state.py`, `_mark_learning_context_locked`: record the kind of
   interruption without changing `learning_exclusions`. Add two keys to
   `current_lap_started`, copied into the completed lap in `transition_lap`:

   ```python
   if state.safety_car not in ("none", "") or state.red_flag_active:
       start["full_course_neutralised"] = True
   elif state.fia_flag in {"yellow", "red"}:
       start["local_yellow"] = True
   ```

   `transition_lap` writes `"full_course_neutralised": bool(start.get(…))`
   and `"local_yellow": bool(start.get(…))`. Tyre learning keeps reading
   `neutralised_lap` and is unaffected.

2. `rain.py`, `_neutralised(record)`: when the record has the key
   `full_course_neutralised`, return it (or-ed with the live `safety_car`,
   `red_flag_active` and `race_control_phase` checks); otherwise keep the
   legacy test. Older laps and saved sessions behave as before.

3. `rain.py`: add a filter of its own and use it in
   `player_pace_observation` for the recent laps (keep `exclusion_reason`
   for the **dry benchmark**, where a strict filter is right):

   ```python
   _WET_EVIDENCE_DROPS = frozenset({
       "pit_lap", "tyre_change", "telemetry_gap", "missing_telemetry",
       "history_timing_mismatch", "setup_change",
   })

   def _wet_evidence_weight(lap: dict[str, Any]) -> float:
       """0 drops the lap; below 1 keeps it as weaker evidence."""
       if (finite(lap.get("lap_time_ms")) or 0) <= 0:
           return 0.0
       if lap.get("pit_status") or lap.get("pit_lane_time_ms"):
           return 0.0
       if _neutralised(lap):
           return 0.0
       tags = set(lap.get("learning_exclusions") or [])
       if tags & _WET_EVIDENCE_DROPS:
           return 0.0
       weight = 1.0
       if "traffic" in tags:
           weight *= 0.6
       if lap.get("local_yellow"):
           weight *= 0.5
       if not lap.get("valid", True):
           weight *= 0.6
       return weight
   ```

   `weather_change` is deliberately absent: it is the signal.

   In the loop over `laps[-3:]` use `weight = _wet_evidence_weight(lap)`;
   drop at 0; keep the weight on the lap. Set `observation["samples"]` to
   `max(1, round(sum(weights)))` so `fit_wetness` and the confidence see the
   reduced evidence.

4. The sector-shape incident test and the driver-reported incident list
   stay as they are; they already catch a spin.

**Tests.** In `tests/test_rain_model.py`:

- `test_the_lap_the_rain_arrived_on_is_evidence` — the last lap carries
  `learning_exclusions=["weather_change"]` and is 4 s slow in every sector:
  `player_pace_observation(...)["ratio"]` is not `None`.
- `test_a_lap_in_traffic_counts_for_less_not_nothing` — tag `traffic`:
  `samples == 1`, and with three such laps `samples == 2`.
- `test_a_local_yellow_does_not_switch_off_the_field` — the player's last
  lap has `local_yellow=True`, `full_course_neutralised=False`,
  `learning_exclusions=["neutralised_lap"]`:
  `rain._field_pace_block_reason(state) is None`.
- `test_a_safety_car_lap_still_switches_it_off` —
  `full_course_neutralised=True`: the reason is
  `"awaiting_clean_lap_after_neutralisation"`.
- `tests/test_strategy_weather_evidence.py` must pass unchanged; its two
  neutralisation tests use `safety_car` and `race_control_phase`.

**Done when.** The probe's scenario "onset lap excluded as weather_change,
next lap yellow" shows `conf` above 0.5 on lap 13, where it now shows 0.04.

---

## WX-07

**Rivals' in- and out-laps are read as wet pace.** Severity S2. Evidence:
read.

**Where.** `rain.py:462` `field_pace_observations`.

**What happens.** A rival is skipped while `pit_lane_timer_active`. Once it
has left the pit lane, its latest valid lap is its in-lap or its out-lap, 20
seconds or more slower than its benchmark, attributed to the compound of
that lap. For a car that just changed to intermediates the in-lap is "a
slick at 125% of dry pace", which fits a wetness above 0.6. The function
keeps the middle eight readings, which protects against one or two such
cars. In a wet race the field stops within the same two laps.

**Change.** In the loop, after `latest` is chosen, skip the lap when it is
adjacent to a stop:

```python
stint_ends = {
    int(stint.get("end_lap", 0) or 0)
    for stint in driver.get("tyre_stints", []) or []
    if 0 < int(stint.get("end_lap", 0) or 0) < 255
}
stop_laps = {int(item.get("lap", 0) or 0) for item in driver.get("pit_stop_history", []) or []}
if latest_lap in stint_ends or latest_lap - 1 in stint_ends or latest_lap in stop_laps:
    # In-lap or out-lap: the pit lane, not the track.
    recent = [lap for lap in recent[:-1] if int(lap.get("lap_num", 0) or 0) not in stint_ends
              and int(lap.get("lap_num", 0) or 0) - 1 not in stint_ends]
    if not recent:
        continue
    latest = recent[-1]
    latest_lap = int(latest.get("lap_num", 0) or 0)
```

Apply the same exclusion when building `dry_laps` and `valid` for the
benchmark. `pit_stop_history` records the lap on which the car left the pit
lane (`udp.py:1618`); `tyre_stints[].end_lap` is the in-lap.

**Tests.** In `tests/test_rain_model.py`:

- `test_an_in_lap_is_not_a_wet_lap` — six rivals whose latest lap is 22 s
  slow and equals a stint's `end_lap`; six at dry pace. The fitted wetness
  with all twelve equals the fitted wetness with only the six clean cars.
- `test_an_out_lap_is_not_a_wet_lap` — the same with `latest_lap - 1` as the
  stint end.

**Done when.** Both tests pass.

---

## WX-08

**Evidence arrives once a lap; three faster signals are received and
unused.** Severity S2. Evidence: read (the packet fields are in
`f1-packets` 2026.1.1; their behaviour in the rain is to verify).

**Where.** `udp.py:1573`–`1644` (the per-car loop in `handle_PacketLapData`
does not read `sector1_time_*` / `sector2_time_*`); `udp.py:1742`–`1762`
(surface temperatures are stored per car and read by nothing in `rain.py`);
`udp.py:1435` (`active_aero_track_status` is stored and read nowhere);
`udp.py:2474` (the DRS-disabled reason is logged and read nowhere).

**What is available.**

| Signal | Packet field | Rate | What it gives |
| --- | --- | --- | --- |
| Sector times of the lap in progress, every car | `LapData.sector1_time_*`, `sector2_time_*` | 60 Hz | a pace reading a third of a lap after the surface changes, from 20 cars |
| Tyre surface temperature, every car | `CarTelemetryData.tyres_surface_temperature` | 60 Hz | falls within seconds when water reaches the tyre |
| Race-control grip status | `PacketSessionData.active_aero_track_status`; event `DRSD` reason | 2 Hz / event | the game's own declaration that the track is low-grip |
| Forecast temperature trend | `WeatherForecastSample.track_temperature_change`, `air_temperature_change` | 2 Hz | a falling track temperature ahead of rain; not parsed at all |

**Change.** This is feature F-02 in Part 5, specified there in full. The
part that belongs to the wet model:

1. `state.py`, `DriverState`: add `current_s1_ms: int = 0`,
   `current_s2_ms: int = 0`, and `surface_temp_baseline_c: float | None =
   None`. Exclude nothing from the live snapshot; they are three numbers.
2. `udp.py`, `handle_PacketLapData`, inside the per-car loop:

   ```python
   driver.current_s1_ms = _ms(int(lap.sector1_time_minutes_part), int(lap.sector1_time_ms_part))
   driver.current_s2_ms = _ms(int(lap.sector2_time_minutes_part), int(lap.sector2_time_ms_part))
   ```

3. `rain.py`: add `sector_pace_observations(state, *, dry_until_lap)`. For
   each running rival not in the pit lane, not on an in- or out-lap (WX-07),
   not `restricted`:

   - dry reference per sector = median of that car's `s1_ms` / `s2_ms` over
     its laps `2 … dry_until_lap` with `valid_flags & 1` and both sectors
     positive; need at least two.
   - for each of `current_s1_ms`, `current_s2_ms` that is positive, `ratio =
     current / reference`.
   - skip a sector that contains a marshal zone showing yellow or red
     (`state["marshal_zones"]` against `sector2_start_m` /
     `sector3_start_m`).
   - one observation per car: the mean of its available sector ratios, with
     `"source": "field_sector"`, `"samples": 1`, the compound fitted now.

   Return the middle eight by ratio, as `field_pace_observations` does.
   Return `[]` under `_field_pace_block_reason`.

4. `estimate_wetness`: fit the sector observations separately —
   `fast_measured, fast_confidence, fast_samples = fit_wetness(sector_obs,
   dynamic)` — because they describe the surface now, not half a lap ago.
   Blend after the lap-time innovation:

   ```python
   if fast_confidence > 0.0:
       wetness += (fast_measured - wetness) * min(0.6, fast_confidence)
   ```

   Publish `fast_measured_wetness`, `fast_confidence`, `fast_cars` in the
   reading.

5. `evaluate`, the confirmation gate of WX-01: add
   `fast_confidence >= 0.5 and fast_measured >= 0.20` to the wet
   confirmations and `fast_confidence >= 0.5 and fast_measured <= 0.30` to
   the dry ones.

6. `proactive.py`, `_refresh_strategy_if_needed`: add to the signature the
   count of rivals with a positive `current_s1_ms` and the count with a
   positive `current_s2_ms`, bucketed in fives, while the sky is wet or the
   present rain chance is above 20%. That triggers a recompute as the field
   posts its sectors without recomputing at 60 Hz.

7. Surface temperature and race-control status are **not** blended into the
   wetness. Their relation to the surface is not established. Publish them
   in the reading as flags — `field_surface_temp_drop_c` (median over
   running cars of baseline minus current, baseline being the car's median
   over the previous dry lap) and `race_control_low_grip` — show them on the
   weather wall (F-01), use them for the heads-up call (F-03), and log them
   in F-10 so their relation can be measured.

**Tests.** New file `tests/test_rain_fast_channel.py`.

- `test_sector_pace_reads_the_surface_before_the_lap_ends` — twelve rivals
  with five dry laps of sectors `(30, 30, 30)` s and `current_s1_ms = 32_400`
  (8% slow), no completed wet lap. `sector_pace_observations` returns eight
  observations; `estimate_wetness(...)["fast_measured_wetness"]` is between
  0.2 and 0.3.
- `test_a_yellow_sector_is_not_evidence` — a yellow marshal zone inside
  sector 1: no observations from sector 1.
- `test_a_car_in_the_pit_lane_posts_no_sector` — `pit_lane_timer_active`.
- `test_sector_pace_confirms_a_stop_the_label_alone_could_not` — the WX-01
  "label alone" state plus the twelve rivals: `should_change is True`.
- `test_restricted_cars_contribute_nothing` — `restricted=True`.

**Done when.** With the probe extended to post sector times at mid-lap
(`--mid-lap`), the first call arrives during the lap after the rain starts.

---

## WX-09

**"Box now" for the weather waits on a model round trip and carries a
24-word rules footnote.** Severity S2. Evidence: read.

**Where.** `proactive.py:195` `FLASH_EVENTS` does not include the weather
call; `proactive.py:2602` `_narrate`; `strategy.py:4077`–`4082`.

**What happens.** A CRITICAL call that is not FLASH is worded by the model
first, with a 6-second timeout, then checked for invented numbers, then
spoken. The comment above `FLASH_EVENTS` records a measured queue-to-speech
average of 31.6 s before FLASH existed. A stop for the weather is the most
time-bound call the engineer makes: the pit entry is a fixed point on the
lap.

When the plan uses a wet compound before two dry ones have been run, the
instruction gains this sentence, including on the lap of the stop with the
track at 0.68:

> This plan meets the compound requirement only if the planned
> wet/intermediate running actually happens; if it stays dry, a different
> dry compound is required.

**Change.**

1. `proactive.py:779`: `is_flash = event_type in FLASH_EVENTS or (event_type
   == "weather_crossover" and payload.get("level") == WEATHER_BOX) or
   event_type == "weather_stand_down"`.
2. BOX template, instruction first, reason second, twelve words before the
   reason:

   ```python
   f"Box this lap for {tyre}. {reason}"            # box_lap <= current lap
   f"Box lap {box} for {tyre}. {reason}"           # a later lap (STANDBY)
   ```

   `reason` is the crossover's sentence cut at its first full stop.
3. Pit-entry reminder. In `_detect`, while the level is BOX, the player is
   not in the pit lane and `lap_distance_m / track_length_m` is within
   `0.12` below `PIT_ENTRY_FRACTION.get(track_id, 0.94)`, queue
   `weather_box_reminder` once per lap (key `(current_lap,)` in a set reset
   per session): FLASH, `expires_s=8`, template `"Box, box. {Tyre} on."`.
   Move `PIT_ENTRY_FRACTION` out of `strategy.py` into a module both can
   import, or read `strategy.neutralisation.pit_entry_status` and the
   fraction through a new key `pit_entry_fraction` in `_neutralisation`.
4. `strategy.py:4078`: append the conditional-wet-use sentence only when the
   stop is not this lap **and** the surface is below the crossover:

   ```python
   if (projected_rule["conditional_on_future_wet_use"]
           and not (box_lap == current_lap or current_wetness >= rain.WETNESS_SLICK_INTER)):
   ```

   Keep the fact in `compound_rule.conditional_on_future_wet_use` for the
   Strategy page, which has room for it.

**Tests.** In `tests/test_weather_call_protocol.py`:

- `test_a_weather_box_is_spoken_from_its_template` — a BOX event has
  `flash is True`; `_narrate` returns the template without calling
  `brain.proactive` (assert the mock was not awaited).
- `test_the_box_call_leads_with_the_instruction` — the text starts with
  `"Box this lap for inters."`.
- `test_the_reminder_is_said_once_a_lap_before_pit_entry` — two detect
  passes inside the window: one event; none after `pit_status` is non-zero.
- `tests/test_rain_strategy.py`:
  `test_a_stop_on_a_wet_track_carries_no_rules_footnote` — the probe's
  heavy-rain state: `recommended["instruction"] == "Box lap 15 for INTER."`.

**Done when.** The tests pass and, on a replayed wet capture (F-12), the
time from the engine publishing `worth_stopping` for the current lap to
`save_proactive_call(..., delivered=True)` is under 2 s outside braking
zones.

---

## WX-10

**What-if ignores the weather.** Severity S2. Evidence: reproduced.

**Where.** `strategy.py:4895` `what_if`; `app.py:1665`;
`static/js/strategy.js:757`.

**What happens.** `python -m tools.strategy_probe`, part 2. Heavy rain, the
engine recommends "Box lap 15 for INTER". The what-if for that same stop:

```text
what-if INTER: +360.8 s against the recommended plan
what-if HARD : +137.0 s against the recommended plan
```

The Strategy page's "Try a different stop" row prints the first line as
"Box lap 15 for INTER: +360.8s slower than the recommended plan (estimate)."

Five separate causes, all in `what_if`:

1. `_simulate_stint` is called without `wetness_trajectory` or
   `expected_weather_penalties`, so an intermediate is priced on a dry track.
2. `delta_to_best_s` subtracts a Monte Carlo quantile of the recommended
   plan from a deterministic conservative sum of the hypothetical. The radio
   tool works around this (`tools.py:2853`); the HTTP endpoint does not.
3. `first_laps = max(1, requested_lap − current_lap + 1)`. On the grid
   (`current_lap == 0`) that is one lap more than `laps_before_stop` in
   `compute`, the phantom lap `compute` fixed at `strategy.py:3471`.
4. `neutral["effective_pit_loss_s"]` is charged for a stop on any lap. Under
   a safety car a stop ten laps away is priced at the safety-car discount.
5. Rejoin traffic, tyre inventory and the pending-penalty basis are not
   those of the ranked plans.

**Change.** Price the hypothetical with the planner, as `_evaluate_cut`
already does (`strategy.py:4673`).

1. Rewrite `what_if`:

   ```python
   state = await self.store.snapshot_analysis()
   historical = infer_unrun_compounds(
       await self.database.tyre_history_model(int(state.get("track_id", -1)), context=state)
   )
   base, _ = await self._compute_isolated(state, historical)
   …parse lap and compound exactly as today…
   query = deepcopy(state)
   query["strategy_override"] = {
       "enabled": True, "locked": True,
       "next_box_lap": requested_lap, "next_compound": compound,
       "preferred_stops": 1,
   }
   scenario_plan, _ = await self._compute_isolated(query, historical)
   plan = scenario_plan.get("recommended", {})
   matched = (
       (plan.get("box_laps") or [None])[0] == requested_lap
       and len(plan.get("compounds") or []) > 1
       and str(plan["compounds"][1]).upper() == compound
   )
   ```

   `compute` skips the driver override while `force_weather` is true
   (`strategy.py:3911`). Add a private key the what-if sets,
   `query["_what_if"] = True`, and make the two `not force_weather`
   conditions `not force_weather or state.get("_what_if")`, so a
   hypothetical is never replaced by the weather plan it is being compared
   with.

2. When `matched` is false return `{"available": False, "reason":
   scenario_plan["recommended"].get("driver_override", {}).get("warning") or
   "No executable plan matches that stop."}`.

3. Return, from the matched plan and the baseline's recommended plan, on the
   **same** basis:

   ```python
   "projected_time_s", "risk_adjusted_time_s", "projected_finish_position",
   "projected_rejoin_position", "traffic_cost_s", "projected_finish_wear_pct",
   "feasible", "legal", "compound_rule", "pending_finish_penalty_s",
   "delta_to_best_s": plan["risk_adjusted_time_s"] - best["risk_adjusted_time_s"],
   "position_delta_to_best": plan["projected_finish_position"] - best["projected_finish_position"],
   "matches_recommended": self._radio_signature(plan) == self._radio_signature(best),
   ```

   Both `risk_adjusted_time_s` values are now the same Monte Carlo quantile
   over the same seeded draws (`_monte_carlo_profile` seeds on session and
   lap).

4. `tools.py:2784`: drop the like-for-like workaround (`_stint_time_s`,
   `time_delta_vs_recommended_s`) and pass the new fields through. Keep the
   refusal for a scenario naming neither lap nor compound and the `assumed`
   notes.

5. `static/js/strategy.js:757`: when `outcome.matches_recommended`, print
   "That is the recommended plan."; otherwise print the time delta **and**
   the position delta: "Box lap 18 for HARD: +4.2 s, finishes P7 against
   P6."

**Tests.** In `tests/test_strategy.py`:

- `test_what_if_for_the_recommended_stop_is_the_recommended_plan` — any race
  state; `what_if` with the recommended lap and compound returns
  `matches_recommended is True` and `delta_to_best_s == 0`.
- `test_what_if_prices_an_intermediate_on_the_track_it_will_run_on` — the
  probe's wet state: `what_if(INTER)["delta_to_best_s"] <
  what_if(HARD)["delta_to_best_s"]`.
- `test_what_if_on_the_grid_has_no_phantom_lap` — `current_lap=0`,
  `total_laps=30`, lap 12: the matched plan's `stint_laps` sum to 30.
- `test_what_if_under_a_safety_car_charges_green_for_a_later_lap` —
  `race_control_phase="safety_car"`, a stop five laps ahead:
  `pit_stop_costs_s[0] == neutralisation["base_pit_loss_s"]`.
- Update `tests/test_tools_quality.py` where it asserts
  `time_delta_vs_recommended_s`.

**Done when.** `python -m tools.strategy_probe --assert` prints no failure
for part 2.

---

## WX-11

**"It's raining" is not recognised as a surface report.** Severity S2.
Evidence: read.

**Where.** `brain.py:786` `_GRIP_PATTERNS`; `rain.py:670`
`GRIP_REPORT_WETNESS`.

**What happens.** The driver's report is the fastest channel there is, and
the confirmation gate in WX-01 leans on it. The phrase table has no entry
for the commonest things said when rain starts: "it's raining", "it's
starting to rain", "spots of rain", "I've got drizzle", "it's wet", "the
track is wet", "I need inters", "I'm losing grip". None is captured. "It's
wet" matches nothing because the table holds only "it's properly wet", "wet
off line" and "wet everywhere".

**Change.**

1. `rain.py`, `GRIP_REPORT_WETNESS`: add `"spitting": (0.12, 0.55)` — rain
   in the air, surface still dry — and `"wet": (0.50, 0.70)`.
2. `brain.py`, `_GRIP_PATTERNS`, in this order (first match wins, so the
   stronger category comes first; keep the existing entries):

   ```python
   "wet": (
       "it's wet", "its wet", "track is wet", "track's wet", "it's really wet",
       "i need inters", "need inters", "give me inters", "put me on inters",
       "box for inters", "inters now", "too wet for slicks",
   ),
   "damp": (… existing …, "losing grip", "no grip", "getting slippery",
            "it's slippery", "struggling for grip"),
   "spitting": (
       "it's raining", "its raining", "started raining", "starting to rain",
       "rain is starting", "spots of rain", "drops of rain", "drizzle",
       "drizzling", "it's spitting", "rain on the visor",
   ),
   ```

   Place `"wet"` after `"soaked"` and before `"damp"`; `"spitting"` after
   `"damp"`.
3. A question is not a report. With the new phrases
   `_capture_conditions_feedback` would match "is it raining?". Guard the
   grip capture with the helper the strategy commands already use:
   `if utterance_mood(normalize_text(utterance)) == "question": skip`
   (`intent.py:366`). "I need inters" reads as a request or a statement and
   is kept.
4. `evaluate`'s confirmation gate (WX-01 step 4): add `"wet"` to the wet
   confirmations. `"spitting"` confirms nothing; it raises the heads-up
   (F-03).

**Tests.** In `tests/test_dialogue_regressions.py` or
`tests/test_driver_decisions_quality.py`:

- `test_rain_reports_are_captured` — parametrised over twelve phrases from
  the table; after `brain._capture_feedback(phrase)` the store's
  `driver_grip_feedback["category"]` is the expected one.
- `test_a_question_about_rain_is_not_a_report` — "is it raining at the back
  of the circuit?": `driver_grip_feedback` is unchanged.
- `tests/test_rain_model.py`: `test_the_new_reports_sit_on_the_scale` —
  `GRIP_REPORT_WETNESS["spitting"][0] < WETNESS_SLICK_INTER <
  GRIP_REPORT_WETNESS["wet"][0] < WETNESS_INTER_WET`.

**Done when.** The tests pass.

---

## WX-12

**The engineer's weather tool ignores the wet model.** Severity S3.
Evidence: read.

**Where.** `tools.py:893` `get_weather_forecast`.

**What happens.** When the driver asks "is it going to rain?" the model is
given the raw samples and a `rain_crossover_min`: the first sample at 60% or
more. The wet model's surface estimate, trend, tyre costs and verdict are
not in the result. The engineer can therefore answer "rain in ten minutes,
60%" while the proactive call says the track is already past the crossover.

**Change.** Return the unified picture, built by one function both the tool
and the weather wall (F-01) read. Add to `tools.py`:

```python
async def get_weather_picture(self) -> dict[str, Any]:
```

returning the contract in [05-features.md, F-01](05-features.md#f-01). Keep
`get_weather_forecast` as a thin alias that adds the old keys, minus
`rain_crossover_min` and `rain_crossover_basis`. Register the new tool in
the allow-list (`tools.py:4215` region) and in `realtime.py:127`, and add
"weather", "rain", "inters", "slicks", "wet", "dry line" to the routes that
select it in `brain.py`'s deterministic lookups.

**Tests.** `tests/test_tools_quality.py`:
`test_the_weather_answer_and_the_weather_call_read_one_model` — the
`surface.wetness` and `call.level` of the tool result equal those of
`state["strategy"]["weather_crossover"]` and `ProactiveEngineer._weather_call`.

---

## WX-13

**`rain_next_15_pct` is one sample.** Severity S3. Evidence: read.

**Where.** `udp.py:1273` `rain_at`; `proactive.py:2381`.

**What happens.** `rain_at(15)` returns the chance of the last sample at or
before 15 minutes. With samples of 80% at 5 minutes and 10% at 15 it returns
10. The fallback weather template says "Rain risk is 10 percent within
fifteen minutes."

**Change.** Add `rain_peak_15_pct`, `rain_peak_30_pct`, `rain_peak_60_pct`
(the maximum over samples with `0 < offset <= minutes`) to `SessionState`,
set them in `handle_PacketSessionData`, add them to the live profile
(`api/live.py:124`) and use `rain_peak_15_pct` in the template and in
`briefing.py:253`. Keep the old three fields; saved state and the API
contract depend on them.

**Tests.** `tests/test_rain_probability.py`:
`test_the_peak_within_a_window_is_the_peak` — the two-sample packet above:
`rain_next_15_pct == 10`, `rain_peak_15_pct == 80`.

---

## WX-14

**Smaller items in the wet model.** Severity S3. Evidence: read.

1. **The dry benchmark drifts.** `player_pace_observation` takes the median
   of every clean dry lap of the race (`rain.py:389`); the field takes laps
   `2 … dry_until_lap` (`rain.py:501`). Fuel burn is worth about 0.05 s a
   lap per lap (`FUEL_SECONDS_PER_KG = 0.030`), so thirty laps in, the
   benchmark is 0.7 s slower than the car's present dry pace and the first
   1% of wet loss is invisible. Use the last five clean dry laps for both
   (`dry[-5:]`, and the last five of `dry_laps`).
2. **Costs are priced on wet pace.** `_weather_crossover` passes
   `_estimate_base_lap_s` — the median of the last four laps, which are wet
   — as `base_lap_s`, and no `dry_base_lap_s`. Every cost and margin is
   inflated by the current wet loss, 15% at 0.5. Pass the dry benchmark the
   reading already computes: `base_lap_s=reading["player_pace"]["dry_base_lap_s"]`
   when its `benchmark` is `"dry_laps"`.
3. **A safety-car discount is applied to stops laps away.** `evaluate`
   charges one `overhead` for every box lap; `_weather_crossover` passes the
   neutralised loss. Pass both — `pit_loss_now_s` and `pit_loss_green_s` —
   and use the first only for `box_offset == 0`.
4. **`settled_laps` is computed and read by nothing** (`rain.py:775`). Use
   it or remove it. Its natural use is the soak-rate calibration in F-10.
5. **`_wetness_trend` reads labels, not the surface** (`rain.py:868`). With
   WX-02's series available, compare `series[-1]` with `series[-3]`.
6. **None of the curve constants is measured.** `_TRACK_PENALTY_ANCHORS`,
   the mismatch constants, the equilibria and both rates are judgements
   with the crossovers pinned at 112% and 118% of dry pace. F-10 fits them
   to recorded wet races. Until then say so where the reading is shown:
   `"calibration": "judgement"` in the reading.

**Tests.** One per numbered point, in `tests/test_rain_model.py`:
`test_the_dry_benchmark_is_recent`, `test_costs_are_priced_on_dry_pace`,
`test_a_later_stop_pays_the_green_flag_loss`,
`test_the_trend_follows_the_surface`.
