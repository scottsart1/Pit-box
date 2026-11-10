# Part 2 — Rivals

What the engine believes the other nineteen cars will do decides the
projected finishing position of every plan, and the projected finishing
position is the first thing plans are ranked on. That belief is currently
one table of three numbers.

Contents: [what the engine knows about a rival today](#what-the-engine-knows-about-a-rival-today) ·
[RV-01](#rv-01) · [RV-02](#rv-02) · [RV-03](#rv-03) · [RV-04](#rv-04) ·
[RV-05](#rv-05) · [RV-06](#rv-06) · [RV-07](#rv-07) · [RV-08](#rv-08) ·
[the rival model](#the-rival-model)

## What the engine knows about a rival today

| Question | Answer today | Code |
| --- | --- | --- |
| When will this car stop? | When its tyre age reaches 17 (soft), 27 (medium) or 42 (hard) laps | `strategy.py:323`, `strategy.py:1670` |
| Does it have to stop? | Not considered | — |
| What will it fit? | The same compound again | `strategy.py:1675`–`1693` |
| What does the stop cost it? | The pit loss; no cold tyre | `strategy.py:1696` |
| Will it change for rain? | Not considered | — |
| How worn are its tyres? | Received for every car (`DriverState.tyre_wear`), not used for stop timing | `udp.py:1946` |
| What sets does it have left? | Sent by the game for every car, discarded | `udp.py:2144` |
| Is it in the pit lane now? | Known (`pit_status`, `pit_lane_timer_active`), used by one tool, not by the radio trigger | `udp.py:1608`, `proactive.py:2135` |

`TYPICAL_STINT_LAPS` is `OPERATIONAL_WEAR_LIMIT / DEFAULT_WEAR_PER_LAP` with
no circuit severity: 80 / 4.8 = 17, 85 / 3.2 = 27, 88 / 2.1 = 42.

---

## RV-01

**A rival's mandatory compound change is not modelled.** Severity S1.
Evidence: reproduced.

**Where.** `strategy.py:1620` `_project_rival_finish_times`;
`strategy.py:4412` `predict_rival_strategy`.

**What happens.** `python -m tools.strategy_probe`, part 1. Nineteen rivals
on mediums, no stops made, lap 4 of 14:

```text
25% distance   14 laps | rival stops projected [0] | {'likely': 0, 'unlikely_on_tyre_age': 19, 'unknown': 0}
               call: A second compound is mandatory — box lap 13 for SOFT. | projected P20
35% distance   20 laps | rival stops projected [0] | {'likely': 0, 'unlikely_on_tyre_age': 19, 'unknown': 0}
               call: A second compound is mandatory — box lap 19 for SOFT. | projected P20
```

The engine applies the two-compound rule to the player ("A second compound
is mandatory") and to nobody else. In a race shorter than 27 laps a medium
never reaches its table age, so every rival is projected to run to the
flag. The consequences chain:

1. Every plan that makes the player's own mandatory stop loses a pit stop's
   worth of time to nineteen cars that are assumed never to make theirs. All
   stopping plans project the same last place.
2. With position and points level across all of them, the ranking prefers
   the latest first stop ([ST-01](03-strategy-core.md#st-01)).
3. The call is "box on lap 13 of 14".

In a full-distance race the table does produce stops, but for the wrong
reason and at the wrong lap: a medium is projected to stop at age 27 and
again at age 54, three laps from the flag.

**Change.** Minimum, sufficient to turn the probe green; the full model is
[below](#the-rival-model).

1. Add to `StrategyEngine`:

   ```python
   @staticmethod
   def _rival_compounds_used(driver: dict[str, Any]) -> set[str]:
       """Compounds a rival has raced on, plus the one it is on."""
       used = {
           str(stint.get("compound") or "").upper()
           for stint in driver.get("tyre_stints") or []
           # A stint that ended on lap 0 is the set swapped on the grid.
           if int(stint.get("end_lap", 0) or 0) >= 1
       }
       used.add(str(driver.get("tyre_compound") or "").upper())
       return used - {"", "UNKNOWN"}

   @classmethod
   def _rival_owes_compound(cls, state: dict[str, Any], driver: dict[str, Any]) -> bool:
       if str(state.get("mode_profile", "")) != "race":
           return False
       if not cls._compound_rule_applies_to_event(state):      # ST-03
           return False
       used = cls._rival_compounds_used(driver)
       return not (used & WET_COMPOUNDS) and len(used & DRY_COMPOUNDS) == 1
   ```

2. In `_project_rival_finish_times`, before the lap loop, compute the lap
   offset at which an owed stop is expected when tyre life does not already
   force one:

   ```python
   owed_offset = None
   if self._rival_owes_compound(state, driver) and age + remaining <= typical:
       expected_lap = max(current + 1, round(total_laps * 0.45))
       owed_offset = max(1, min(remaining - 2, expected_lap - current + 1))
   ```

   and change the stop condition to

   ```python
   if offset > 0 and remaining - offset >= 2 and (
       future_age >= typical or (offset == owed_offset and not stop_offsets)
   ):
   ```

   `remaining - offset >= 2` removes the stop three laps from the flag. The
   0.45 is a judgement; the rival model replaces it with what this race's
   field is doing.

3. Publish `"stop_owed": bool` on each entry of `rival_finish_projections`.

4. `predict_rival_strategy`: when `_rival_owes_compound` is true,
   `another_stop` is `"owed"` (a new value beside `likely`,
   `unlikely_on_tyre_age`, `unknown`), `confidence` is `"medium"`, and
   `projected_stop_lap` is the lap from step 2. Add `"owed"` to
   `another_stop_counts`. Update the tool description in `tools.py` and the
   `note` text: an owed stop is an obligation observed from the car's own
   stint history, not a tyre-age estimate.

5. `static/js/strategy.js`, the "Who am I racing?" list: show "stop owed"
   where `estimated_stops_remaining >= 1` comes from an obligation.

**Tests.** In `tests/test_strategy.py`:

- `test_a_rival_on_its_first_dry_compound_is_projected_to_stop` — 20-lap
  race, lap 5, rivals on mediums with one ongoing stint:
  every projection has `likely_remaining_stops >= 1` and `stop_owed is True`.
- `test_a_rival_that_has_run_two_compounds_owes_nothing` — stints
  `[SOFT ended lap 6, MEDIUM ongoing]`: `stop_owed is False`.
- `test_a_grid_swap_is_not_a_compound_used` — stints `[SOFT ended lap 0,
  HARD ongoing]`: `stop_owed is True`.
- `test_a_rival_that_ran_inters_owes_nothing`.
- `test_no_rival_stops_in_the_last_two_laps` — any projection:
  `max(likely_stop_offsets_laps, default=0) <= remaining - 2`.
- `test_a_sprint_binds_nobody` — `mode_profile="sprint"`.

Existing tests that pin the old projection are listed in
[06-reference-patch.md](06-reference-patch.md#tests-moved-by-the-prototype).

**Done when.** `python -m tools.strategy_probe --assert` prints no "rivals
owing a compound are projected to make no stop".

---

## RV-02

**Stop timing is a fixed table.** Severity S2. Evidence: read.

**Where.** `strategy.py:323` `TYPICAL_STINT_LAPS`; `strategy.py:1669`–`1693`;
`strategy.py:4433`.

**What is wrong, point by point.**

1. **No circuit.** The player's own default wear is multiplied by
   `TRACK_TYRE_SEVERITY` (0.82 at Las Vegas, 1.30 at Losail). The rival
   table is not. A medium is expected to last 27 laps at both.
2. **No measurement.** `DriverState.tyre_wear` holds each rival's four wear
   percentages, refreshed with every damage packet. Stop timing never reads
   it. A rival at 70% wear after 12 laps on mediums is still projected to
   run to age 27.
3. **No evidence from this race.** When eight cars that started on mediums
   have stopped between laps 11 and 14, the ninth is still projected for
   lap 27.
4. **Stops in the final laps.** Addressed in RV-01 step 2.
5. **The same compound again.** After the projected stop the rival's lap
   time is `normalized + deg × age` on the compound it already had. A car
   going from mediums to softs is projected at medium pace; a car going to
   hards for a long final stint likewise.
6. **No cold tyre.** The player's stints pay `strategy_cold_tyre_penalty_s`
   on the out-lap and half of it on the next. A rival's do not, so every
   rival's stop is 1.65 s cheaper than the player's.
7. **`confidence` is the string `"low"`** on every rival in
   `predict_rival_strategy` whatever the evidence.

**Change.** The rival model, [below](#the-rival-model). It replaces the
table as the source of stop laps in both `_project_rival_finish_times` and
`predict_rival_strategy`, so the tool and the ranking can no longer disagree.

---

## RV-03

**Rivals never change tyres for the weather.** Severity S2. Evidence:
reproduced (the WX-04 probe: nineteen rivals on slicks at 0.5 wetness with
ten laps left are all projected to finish on them).

**Where.** `strategy.py:1620`; `strategy.py:4459`–`4464`, where
`another_stop` becomes `"unknown"` whenever the surface is wet.

**What happens.** In the rain the projection keeps every rival on its
current tyre at its current lap time. The player's weather stop is then
priced as "lose seven places to cars that will not stop", which is what the
radio guard in [WX-04](01-weather.md#wx-04) repeats to the driver. In
practice the field stops within two laps of the crossover and the places
come back in the pit cycle.

**Change.** In the rival model, `weather_response`:

1. Inputs: the published `weather_crossover` (`wetness`, `trajectory`,
   `compound_split`) and the rival's compound.
2. A rival on slicks is expected to stop for intermediates on the first lap
   at which the mean trajectory is at or above `WETNESS_SLICK_INTER`, or
   this lap when the surface is already there. A rival on intermediates is
   expected to stop for slicks on the first lap the trajectory is at or
   below `WETNESS_SLICK_INTER − 0.10` (the hysteresis every pit wall
   applies to going back to slicks).
3. The lap is not certain. Spread it: 60% that lap, 30% the next, 10% the
   one after. `_project_rival_finish_times` uses the expected lap (the
   60% lap); the race simulator (F-07) samples the distribution.
4. When `compound_split` shows cars already on the other compound, count
   them: if a third or more of the running field has changed, every
   remaining car is expected this lap or next.
5. After the stop the rival's pace is priced by `compound_pace_delta_s`
   against the wetness of each projected lap, like the player's.

**Tests.** `tests/test_rival_model.py`:
`test_a_wet_track_sends_slick_runners_to_the_pits`,
`test_a_drying_track_does_not_send_inter_runners_back_too_early`,
`test_a_field_that_is_already_changing_brings_the_rest_forward`.

**Done when.** In the WX-04 probe the inter plan's
`pit_cycle_positions_recovered` is at least 5 and its
`projected_finish_position` is 1.

---

## RV-04

**Tyre-set packets for every car but the player's are discarded.** Severity
S2. Evidence: read; the rival fields' contents are to verify.

**Where.** `udp.py:2142`:

```python
async def handle_PacketTyreSetsData(self, packet: Any) -> None:
    snapshot = await self.store.peek("player_car_index")
    if int(packet.car_idx) != int(snapshot.get("player_car_index", 0) or 0):
        return
```

**What happens.** `PacketTyreSetsData` carries a `car_idx` and is sent in
rotation through the cars. For each of a car's twenty sets it gives
compound, wear, whether it is available, the game's own remaining life
(`life_span`), usable life, the lap-time delta to the fitted set, and which
set is fitted. For the player this is the basis of the whole inventory
model. For a rival it would answer, from the game itself, "how many laps
does the set he is on have left" and "what can he still fit".

**Change.**

1. `state.py`, `DriverState`: add

   ```python
   tyre_sets: list[dict[str, Any]] = field(default_factory=list)
   fitted_tyre_set_idx: int = -1
   tyre_sets_session_time_s: float = 0.0
   ```

   and add `"tyre_sets"` to `_LIVE_DRIVER_EXCLUDED_FIELDS`; twenty
   dictionaries for twenty cars do not belong in the 4 Hz frame.

2. `udp.py`, `handle_PacketTyreSetsData`: build `sets` as today, then

   ```python
   index = int(packet.car_idx)
   if index == player_index:
       await self.store.update(tyre_sets=sets, fitted_tyre_set_idx=int(packet.fitted_idx))
   if 0 <= index < 24:
       def apply(state):
           driver = state.drivers[index]
           if not driver.active or driver.restricted:
               return
           driver.tyre_sets = sets
           driver.fitted_tyre_set_idx = int(packet.fitted_idx)
           driver.tyre_sets_session_time_s = float(packet.header.session_time)
       await self.store.mutate(apply)
   ```

3. Verify before relying on it. With a capture of an offline race
   (`CaptureService`), print for three AI cars, each lap: `fitted_idx`, the
   fitted set's `wear`, `life_span`, `usable_life`, and the car's
   `tyres_age_laps`. Establish:

   - that the sets are populated for AI cars (not all zero);
   - whether `life_span` counts down by one a lap (laps left, as the
     player's does — see the comment at `strategy.py:2398`);
   - the `life_span` at which AI cars actually stop.

   Record the result in `docs/capture-rotation-observations.md`. Until that
   is done the rival model uses the set data at weight 0 and only reports it.

**Tests.** `tests/test_extraction_3_4.py` or a new
`tests/test_rival_tyre_sets.py`:

- `test_a_rivals_tyre_sets_are_kept` — feed a `PacketTyreSetsData` with
  `car_idx=5`; `state["drivers"][5]["tyre_sets"]` has twenty entries and the
  player's `tyre_sets` is unchanged.
- `test_a_restricted_rivals_tyre_sets_are_not` — `restricted=True`: empty.
- `test_rival_tyre_sets_stay_out_of_the_live_frame` —
  `"tyre_sets" not in snapshot_live()["drivers"][5]`.

---

## RV-05

**Rival stops are announced from the stop counter, for every car, and the
wrong one survives.** Severity S2. Evidence: read.

**Where.** `proactive.py:2135`–`2163`; `proactive.py:757`–`777`.

**What happens.**

1. **Late.** The trigger is `pit_stops` rising. `pit_status` and
   `pit_lane_timer_active` are already tracked per car "so a rival's stop
   can be called as it happens" (`udp.py:1605`) and would give the call at
   pit entry, some twenty seconds earlier — the difference between covering
   on this lap and the next.
2. **Unfiltered.** Every rival's stop is queued, P19's while the player is
   P2 included. The type is IMPORTANT with an 8-second spacing, so in a pit
   window the radio fills with "Albon has boxed. We are recalculating the
   response."
3. **The wrong car.** `rival_pitted` is a coalesced type: a new one discards
   the queued one. The loop runs in car-index order, so when two cars stop
   in the same pass the higher index is kept, whichever is nearer.
4. **"Undercut threat" reaches 25 seconds.** Any car within 25 s behind that
   stops raises it, and it is CRITICAL out to 12 s. A car 12 s behind that
   stops is not an undercut threat. It rejoins a further pit loss behind;
   the threat is a car close enough for a lap of fresh tyres to bridge the
   gap, a second or three.
5. **The advice is generic.** "Push for up to two clean laps while
   protecting the limiting tyre, then reassess the cover stop" is said
   whatever the gap, the tyres or the lap. The engine can compute whether a
   cover stop next lap keeps the place.

**Change.** Feature [F-05](05-features.md#f-05) specifies the calls. The
detection change:

1. In `ProactiveEngineer.__init__` and `_reset_for_session` add
   `self._rival_in_pit: dict[int, bool] = {}`.
2. In `_detect`, before the stop-counter block, for each active rival
   compute `in_pit = bool(driver.get("pit_lane_timer_active")) or
   int(driver.get("pit_status", 0) or 0) != 0`. On a false→true edge collect
   the car into `entered`. On a true→false edge collect it into `released`
   with its new `tyre_compound`.
3. Rank `entered` by relevance (`_rival_relevance`, defined in F-05) and
   queue **one** `rival_pitting` for the most relevant car that clears the
   relevance threshold, with the other relevant cars named in
   `also: [names]`. Queue nothing for the rest; the Strategy board shows
   them.
4. Keep the stop-counter block as a fallback for a stop that was never seen
   in the lane (telemetry gap): queue `rival_pitted` only for a car not in
   `self._rival_in_pit` history for that stop number.
5. Replace the 25 s / 12 s constants with the cover calculation in F-05.

**Tests.** New file `tests/test_rival_calls.py`:

- `test_a_stop_is_called_at_pit_entry` — a rival 2 s behind goes
  `pit_status 0 → 1` with `pit_stops` unchanged: one `rival_pitting`.
- `test_a_backmarker_stopping_is_not_radio` — P19, 60 s behind, player P2:
  nothing queued.
- `test_two_cars_stopping_together_names_the_nearer` — car indexes 3 (gap
  1.8 s) and 17 (gap 14 s) in one pass: the payload's `driver` is car 3.
- `test_a_car_twelve_seconds_back_is_not_an_undercut_threat`.
- `test_a_stop_seen_at_entry_is_not_called_again_from_the_counter`.

---

## RV-06

**The strategic-rivals view reads plan keys that do not exist.** Severity
S3. Evidence: reproduced (`tools.strategy_probe` part 4 prints `None`).

**Where.** `engineering.py:629`, `engineering.py:671`–`673`;
`static/js/strategy.js:559`.

```python
"player_projected_position": plan.get(
    "expected_position", plan.get("projected_position")
),
```

A plan carries `projected_finish_position` and `expected_finish_position`.
Neither key above is set anywhere, so the field is always `None`.
`strategic_rivals` and `refreshRivals` also require `mode_profile ==
"race"`; a sprint has rivals too.

**Change.**

```python
"player_projected_position": plan.get("projected_finish_position"),
"player_expected_position": plan.get("expected_finish_position"),
```

and `state.get("mode_profile") not in {"race", "sprint"}` in
`engineering.py:629`; `!["race", "sprint"].includes(s.mode_profile)` in
`static/js/strategy.js:559`.

**Tests.** `tests/test_engineering_groups.py` or
`tests/test_tools_v42_services.py`:
`test_the_rivals_view_reports_where_the_player_is_projected` and
`test_the_rivals_view_works_in_a_sprint`.

**Done when.** `tools.strategy_probe --assert` part 4 passes.

---

## RV-07

**Rejoin position ignores the lap of the stop.** Severity S2. Evidence:
read; its effect is visible in the prototype run in
[06-reference-patch.md](06-reference-patch.md): with rivals now stopping on
lap 6, a player stop on lap 11 is still projected to rejoin last.

**Where.** `strategy.py:1514` `_rejoin_position`; `strategy.py:2447`
`_traffic_cost`; `strategy.py:3252`.

**What happens.** `_rejoin_position` counts the cars whose gap behind the
player **now** is smaller than the pit loss. It is called once per plan with
the first stop's cost and no lap. A stop on lap 30 is given the same rejoin
position as a stop on lap 12, from gaps measured on lap 11. Cars that will
have stopped before the player does, and so will be a pit loss further back,
are counted as passing. Cars in the pit lane at this moment are counted at
their pit-lane gap. The game's own estimate, `pit_stop_rejoin_position`, is
parsed (`udp.py:1366`) and only displayed.

**Change.**

1. In `compute`, after `rival_projections` is built and before plans are
   annotated, build a rejoin table once:

   ```python
   def cumulative(times: list[float]) -> list[float]:
       total, out = 0.0, [0.0]
       for value in times:
           total += value
           out.append(total)
       return out

   player_cum = cumulative(no_stop["lap_times_s"])
   rival_cum = []
   for rival in rival_projections:
       laps = cumulative(rival["lap_times_s"])
       for offset, cost in zip(rival["likely_stop_offsets_laps"], rival["pit_stop_costs_s"]):
           for index in range(offset, len(laps)):
               laps[index] += cost
       rival_cum.append((float(rival["current_gap_s"]), laps))
   ```

   `rival_projections` must be built **before** the plan loops for this, and
   it already can be: it depends on `state`, `remaining`, `base_lap_s`,
   `effective_pit_loss` and `current_wetness`, all known at
   `strategy.py:3103`. Move the call up from `strategy.py:3689`.

2. `rejoin_at(laps_before_stop: int, pit_cost: float) -> int`:

   ```python
   k = min(laps_before_stop, len(player_cum) - 1)
   mine = player_cum[k] + pit_cost
   ahead = sum(1 for gap, laps in rival_cum if gap + laps[min(k, len(laps) - 1)] < mine)
   return min(field_size, 1 + unobserved_ahead + ahead)
   ```

   Memoise on `(k, round(pit_cost, 1))`; there are at most
   `remaining × 2` distinct keys.

3. `make_plan`: `projected_rejoin = rejoin_at(laps_before_stop(box_laps[0]),
   pit_costs[0])` for stopping plans; `_traffic_cost` takes the rejoin
   position as an argument instead of computing it.

4. `positions_lost_by_stopping` becomes `rejoin − position_if_not_stopping`,
   where `position_if_not_stopping` is the same count with `mine =
   player_cum[k]`. Losing "seven places" to cars that were going to pass on
   pace anyway is not a cost of the stop.

5. Publish the game's figure beside the engine's for the current-lap stop:
   `"game_rejoin_position"` is already in `game_window`. Add
   `"rejoin_disagreement": abs(engine − game)` when both exist and the stop
   is this lap; F-11 logs it. A persistent disagreement of three places or
   more is a calibration signal for the pit loss (F-09).

**Tests.** In `tests/test_strategy.py`:

- `test_a_later_stop_rejoins_ahead_of_cars_that_have_stopped_by_then` —
  ten rivals within 20 s behind, all projected to stop on lap 8; the
  player's lap-12 plan has `projected_rejoin_position` equal to the
  player's current position (±1); the lap-6 plan loses places.
- `test_a_car_in_the_pit_lane_is_not_counted_at_its_lane_gap`.
- `test_places_lost_are_measured_against_not_stopping`.
- The 4.9.3 regressions (`tests/test_race_regressions_4_9_3.py`) describe
  real races where a stop from P5 rejoined P14. They must still project a
  rejoin outside the top ten for those states.

**Done when.** In `tools.strategy_probe` part 1 the 25% race's
recommendation projects a finish within three places of the player's
current position.

---

## RV-08

**The "catching" call uses a sample no other path uses.** Severity S3.
Evidence: read.

**Where.** `proactive.py:1057` `_detect_rival_pace`.

**What happens.** A car behind is called "catching" when its gap fell by
more than 0.15 s between two samples 8 s apart on the wall clock, inside
3 s. `TelemetryTools._gap_trend` (`tools.py:957`) exists because exactly
this kind of short window made the same rival "closing" in one answer and
"dropping back" in the next; every tool and `defence_assessment` now read a
one-lap window with a conflict flag. The unsolicited call still reads 8 s,
and uses the wall clock, so a pause or a replay at another speed changes
it.

**Change.** In `_detect_rival_pace`, for each rival behind within 3 s call
`TelemetryTools._gap_trend(driver)` and queue the call when
`trend == "closing"`, `gap_change_s_per_lap <= -0.2` and
`not trend_conflict`. Put `laps_to_contact` in the payload and say it:
"Sainz is catching, 1.8 back, three tenths a lap; with you in about six
laps." Remove `self._rival_gap_history`.

**Tests.** `tests/test_proactive.py`:
`test_the_catching_call_and_the_gap_tool_read_the_same_trend`,
`test_a_tow_down_one_straight_is_not_catching`.

---

## The rival model

New module `src/pitwall/rival_model.py`. Pure functions over snapshot
dictionaries, no I/O, no state of its own, so it can be unit-tested from
plain dictionaries and called from the planner's worker thread.

### Inputs

Per rival, from the analysis snapshot's `drivers[]` entry:

| Field | Source | Used for |
| --- | --- | --- |
| `tyre_compound`, `tyre_age` | status packet | every estimator |
| `tyre_wear` (4) | damage packet; zeros when `restricted` | wear estimator |
| `wear_history` (new) | sampled at each lap change | wear rate |
| `tyre_stints` | session history | compounds used, stint starts |
| `pit_stops`, `pit_events` (new) | lap data | stops made, wear at pit entry |
| `tyre_sets` (new, RV-04) | tyre-set packet | set life, what can be fitted |
| `lap_history` | session history | pace cliff |
| `current_lap`, `position`, `gap_to_player_s` | lap data | timing |

Race context, built once per compute by `build_context(state)`:

| Field | Meaning |
| --- | --- |
| `total_laps`, `current_lap`, `track_id`, `mode_profile` | from the session |
| `rule_applies` | the two-compound rule binds this event ([ST-03](03-strategy-core.md#st-03)) |
| `severity` | `TRACK_TYRE_SEVERITY.get(track_id, 1.0)` |
| `wetness`, `trajectory` | from the published `weather_crossover` |
| `stop_wear` | per compound, the limiting wear at which cars in **this race** entered the pits (median of `pit_events[].wear_at_entry`, needs 3); else the stored circuit figure from F-09; else the prior |
| `stint_lengths` | per (compound, stop number), the stint lengths already completed in this race by cars that started that stint on that compound: `{"n", "median", "p25", "p75"}` |
| `field_changed_share` | share of running cars already on the other weather class |

New per-car history, sampled in `udp.py` `handle_PacketLapData` where
`energy_lap_history` is appended (the lap-change branch at `udp.py:1580`):

```python
if not driver.restricted:
    driver.wear_history.append({
        "lap": int(driver.current_lap),
        "max_wear": round(max(driver.tyre_wear or [0.0]), 2),
        "compound": driver.tyre_compound,
        "tyre_age": int(driver.tyre_age),
    })
    del driver.wear_history[:-40]
```

and on the pit-status edges already computed there (`was_in_pit`):

```python
if not was_in_pit and driver.pit_lane_timer_active:
    driver.pit_events.append({
        "lap": new_driver_lap, "session_time_s": session_time_s,
        "compound_in": driver.tyre_compound, "tyre_age_in": int(driver.tyre_age),
        "wear_at_entry": round(max(driver.tyre_wear or [0.0]), 2) if not driver.restricted else None,
        "phase": state.race_control_phase,
    })
    del driver.pit_events[:-8]
elif was_in_pit and not driver.pit_lane_timer_active and driver.pit_events:
    driver.pit_events[-1].update(
        lane_time_ms=int(driver.pit_lane_time_ms),
        stationary_ms=int(driver.current_pit_stop_max_ms),
        exit_session_time_s=session_time_s,
    )
```

`compound_out` is filled by the status handler on the next status packet
(`udp.py:1860`) when the last event has no `compound_out`. Add
`wear_history: list` and `pit_events: list` to `DriverState` and to
`_LIVE_DRIVER_EXCLUDED_FIELDS`.

### Constants

```python
# No car stops for tyres by choice this close to the flag.
NO_STOP_FINAL_LAPS = 2
# Limiting wear at which the game's AI is taken to stop, until this race or
# this circuit's history says otherwise. A judgement: twenty points inside
# the operational limit the planner allows the player.
STOP_WEAR_PRIOR_PCT = {name: OPERATIONAL_WEAR_LIMIT[name] - 20.0 for name in OPERATIONAL_WEAR_LIMIT}
# Share of race distance at which an owed stop is expected with no other
# evidence, by the compound being run. A judgement.
OWED_STOP_DISTANCE_SHARE = {"SOFT": 0.35, "MEDIUM": 0.45, "HARD": 0.60}
# Hysteresis before a wet-tyre runner is expected back on slicks.
BACK_TO_SLICKS_MARGIN = 0.10
ESTIMATOR_WEIGHTS = {
    "set_life": 0.0,        # 3.0 once RV-04 step 3 has verified the field
    "wear": 2.0,
    "field": 2.0,
    "game_window": 1.0,
    "owed_distance": 1.0,
    "table": 0.5,
}
```

### Estimators

Each returns `None` or `{"lap": int, "basis": str, "weight": float}` — the
lap at the end of which the rival is expected to enter the pits.

1. **`wear`.** Needs `tyre_wear` with a positive maximum and
   `wear_history` with at least two entries on the fitted set (same
   compound, increasing `tyre_age`). Rate = median of the last five
   lap-to-lap increases of `max_wear`; fall back to `max_wear / tyre_age`
   when `tyre_age >= 3`. `laps_left = (stop_wear[compound] − max_wear) /
   rate`. Lap = `rival.current_lap + max(0, ceil(laps_left))`.
2. **`field`.** Needs `stint_lengths[(compound, stops_made + 1)]["n"] >= 3`.
   Lap = stint start lap + median stint length − 1, floored at the rival's
   current lap. The stint start is `current_lap − tyre_age`.
3. **`set_life`.** Needs `tyre_sets` with one fitted entry. Lap =
   `current_lap + min(life_span, usable_life − tyre_age) − 1`.
4. **`game_window`.** Only for a rival on the player's compound with the
   same stint start (±1 lap) and no stops: lap = `strategy.game_ideal_lap`
   when it is positive and in the future.
5. **`owed_distance`.** Only when the rival owes a compound: lap =
   `round(total_laps × OWED_STOP_DISTANCE_SHARE[compound])`.
6. **`table`.** Lap = stint start + `round(TYPICAL_STINT_LAPS[compound] /
   severity)` − 1.

### Combination

```python
def next_stop(rival, context) -> dict:
    owed = owes_compound(rival, context)
    weather = weather_response(rival, context)            # RV-03
    if weather:
        return {**weather, "owed": owed}
    candidates = [e for e in (wear(...), field(...), set_life(...), game_window(...),
                              owed_distance(...) if owed else None, table(...))
                  if e and e["weight"] > 0]
    last_lap = context["total_laps"] - NO_STOP_FINAL_LAPS
    reach_flag = all(e["lap"] > last_lap for e in candidates if e["basis"] != "owed_distance")
    if not owed and reach_flag:
        return {"expected_lap": None, "owed": False, "basis": [e["basis"] for e in candidates],
                "confidence": confidence(candidates)}
    laps = [(min(last_lap, max(rival_current_lap, e["lap"])), e["weight"]) for e in candidates]
    expected = weighted_median(laps)
    return {
        "expected_lap": expected,
        "earliest_lap": min(lap for lap, _ in laps),
        "latest_lap": max(lap for lap, _ in laps),
        "owed": owed,
        "basis": [e["basis"] for e in candidates],
        "confidence": confidence(candidates),
    }
```

`confidence`: `"high"` when `wear` and `field` are both present and within
two laps of each other; `"medium"` when either is present, or the stop is
owed; `"low"` otherwise.

`p_stop_within(next_stop, laps)`: `0.02` when `expected_lap is None`;
otherwise a logistic in laps centred on `expected_lap` with scale
`max(1.0, (latest_lap − earliest_lap) / 4)`.

### Next compound

```python
def next_compound(rival, context, stop_lap) -> dict:
```

1. Weather response decides it when active (INTER; WET at or above
   `WETNESS_INTER_WET`; the hardest unused dry compound that covers the
   remainder when going back to slicks).
2. Otherwise the candidates are the dry compounds, restricted to those with
   an available set when `tyre_sets` is known, and to compounds not yet
   used when a compound is owed.
3. Choose the softest candidate whose life `TYPICAL_STINT_LAPS / severity`
   covers `total_laps − stop_lap`; if none covers it, the hardest.
4. `confidence` is `"medium"` when the candidate set has one member,
   `"low"` otherwise.

### Whole-race projection

```python
def project(rival, context) -> dict:
    """{"stops": [{"lap", "compound", "basis"}], "owed", "confidence", "next": {...}}"""
```

Apply `next_stop` and `next_compound`, then repeat from the new stint
(fresh tyre, age 0, the `table` and `field` estimators only) until no
further stop is needed or three stops are projected.

### Integration

1. `_project_rival_finish_times`: build `context = rival_model.build_context(
   state, weather_crossover)` once; for each rival `plan =
   rival_model.project(driver, context)`; convert stop laps to offsets
   (`lap − current_lap + 1`). In the lap loop, at a stop: set the compound,
   reset the age, add `settings.strategy_cold_tyre_penalty_s` to that lap
   and half to the next, and from then on add
   `compound_pace_delta_s(new, old, base_lap_s, wetness_at(offset))` and use
   the new compound's `deg`. Publish on each projection: `stop_plan`,
   `stop_owed`, `next_compound`, `stop_window`, `basis`, and keep
   `likely_remaining_stops` and `likely_stop_offsets_laps` for existing
   readers.
2. `predict_rival_strategy`: return, per rival, the existing keys plus
   `stop_owed`, `stop_window {earliest, expected, latest}`,
   `p_stop_next_lap`, `p_stop_within_3`, `next_compound`, `basis`,
   `confidence`. `undercut_threat` becomes: behind, `p_stop_within(…, 2) >=
   0.5`, and inside the cover range of F-05.
3. `tools.py` `get_race_flow`: add `cars_owing_a_stop` and the field's
   stint-length summary.
4. The model's output is evidence for the engineer under the existing tool
   safety rules: every figure it gives the model is labelled with its
   `basis` and `confidence`, and an `expected_lap` from `table` alone is
   described as "typical tyre life", never as a plan.

### Tests

New file `tests/test_rival_model.py`. Each builds plain dictionaries.

| Test | Arrange | Assert |
| --- | --- | --- |
| `test_measured_wear_times_the_stop` | medium, age 10, max wear 40, history rising 4 a lap, stop wear 65 | `expected_lap == current + 7` (±1), basis contains `wear` |
| `test_the_field_times_the_stop_when_wear_is_hidden` | restricted rival; five cars' medium first stints ended at 11–14 | expected within that range, basis `field` |
| `test_an_owed_stop_has_a_latest_lap` | hard, age 3, 20-lap race | `latest_lap <= 18`, `owed is True` |
| `test_no_stop_when_the_tyre_reaches_the_flag_and_nothing_is_owed` | two compounds used, hard age 5, 8 laps left | `expected_lap is None` |
| `test_severity_shortens_the_table` | Losail against Las Vegas, table only | earlier at Losail |
| `test_the_next_tyre_is_one_not_yet_used_when_owed` | started medium, 25 laps left | `next_compound["compound"] == "HARD"` |
| `test_a_short_run_to_the_flag_takes_softs` | started medium, 8 laps left | `"SOFT"` |
| `test_available_sets_limit_the_choice` | `tyre_sets` with no soft available | never `"SOFT"` |
| `test_probability_rises_through_the_window` | expected 14, window 12–16 | `p(…, lap 11) < 0.2 < p(…, 14) < p(…, 17) > 0.9` |
| `test_missing_everything_is_low_confidence_not_a_crash` | compound `UNKNOWN`, no histories | `confidence == "low"`, no exception |
| `test_a_car_in_the_pit_lane_is_between_stints` | `pit_lane_timer_active` | the stop in progress is counted as made |

**Done when.** `python -m tools.strategy_probe --assert` passes, and on a
recorded race (F-12) the median absolute error of `expected_lap` against
the laps rivals actually stopped, measured five laps before each stop, is
three laps or fewer. Report the figure in the pull request; it is the
number this model exists to improve.
