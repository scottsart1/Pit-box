# Part 4 — Performance

Everything here was measured on one machine (Linux container, Python 3.13)
with synthetic race states, so the absolute figures will differ on a
driver's PC running the game beside the app. The ratios will not. The
planner runs in a worker thread, but it is pure Python and holds the
interpreter lock while it runs: every millisecond it spends is a
millisecond the UDP consumer and the web server share with it.

Reproduce the figures with the commands in each item. The feature programme
in Part 5 adds work to the planner (a rival model, a rejoin table, scenario
simulation), so this headroom is needed before those land, not after.

Contents: [PF-01](#pf-01) · [PF-02](#pf-02) · [PF-03](#pf-03) ·
[PF-04](#pf-04)

---

## PF-01

**The finish projection recomputes, per plan, what is constant for the
whole compute.**

**Where.** `strategy.py:1864` `_annotate_finish_projection` and the helpers
it calls.

**Measured.** A 57-lap race at lap 12, twenty cars, 2,540 enumerated plans.
`StrategyEngine.compute`: 321 ms wall. Under `cProfile` (1.24 s total):

| Function | Calls | Cumulative | Share |
| --- | --- | --- | --- |
| `_annotate_finish_projection` | 2,540 | 0.548 s | 44% |
| `_pit_cycle_positions_recovered` | **5,079** | 0.293 s | 24% |
| `make_plan` | 2,540 | 0.341 s | 27% |
| `simulate` (cache wrapper) | 9,670 | 0.232 s | 19% |
| `_simulate_stint` (real simulations) | 226 | 0.133 s | 11% |
| `_expected_positions_recovered` | 2,540 | 0.191 s | 15% |
| `TyreInventory.allocate` | 2,540 | 0.099 s | 8% |
| `_rejoin_position` | 2,539 | 0.064 s | 5% |
| `_compound_rule` | 2,544 | 0.059 s | 5% |
| `_projection_field_size` | 2,590 | 0.037 s | 3% |

The stint simulation — the actual model — is 11%. The rest is bookkeeping
around it.

**Causes and changes.**

1. **`_pit_cycle_positions_recovered` runs twice per plan.** Once inside
   `_expected_positions_recovered` (`strategy.py:1824`) and again for the
   published field (`strategy.py:1908`). Compute it once in
   `_annotate_finish_projection` and pass it in:

   ```python
   pit_cycle = self._pit_cycle_positions_recovered(plan, state, rival_projections)
   recovered = self._expected_positions_recovered(
       plan, state, rival_projections, difficulty, pit_cycle=pit_cycle
   )
   …
   "pit_cycle_positions_recovered": pit_cycle,
   ```

   Expected saving: about 12% of the compute.

2. **Per-compute constants are rebuilt for every plan.** None of these
   depends on the plan:

   | Value | Rebuilt in |
   | --- | --- |
   | cars ahead with no projection | `_unobserved_cars_ahead` |
   | field size and its source | `_projection_field_size` |
   | sorted rival finish times | the generator at `strategy.py:1876` |
   | median rival pace | `_expected_positions_recovered` |
   | typical adjacent gap | `_typical_adjacent_gap_s` |
   | whether any rival projection is low or medium confidence | `strategy.py:1915` |
   | compounds the player has used | `_used_compounds`, via `_compound_rule` |

   Build them once in `compute`, immediately after `rival_projections`, into
   a small frozen dataclass and pass that to `_annotate_finish_projection`:

   ```python
   @dataclass(frozen=True, slots=True)
   class _FieldContext:
       player_position: int
       unobserved_ahead: int
       field_size: int
       field_size_source: str
       finish_times: tuple[float, ...]            # sorted ascending
       median_pace_s: float | None
       typical_gap_s: float
       worst_confidence: str                      # "low" | "medium" | "high"
       observed_rivals: int
   ```

   The raw position becomes `1 + unobserved_ahead + bisect.bisect_left(
   finish_times, projected_time)` instead of a 20-term generator per plan.

3. **`_compound_rule(state, future)` runs per plan** and calls
   `_used_compounds(state)` each time. In `compute` keep
   `rule_cache: dict[tuple[str, ...], dict[str, Any]]` keyed on
   `tuple(compounds_in_plan[1:])`; there are at most a few dozen distinct
   keys.

4. **`_rejoin_position` runs per plan** with one of two pit costs. Memoise
   on `round(pit_cost, 2)`; [RV-07](02-rivals.md#rv-07) replaces it with a
   table keyed on the box lap anyway.

5. **Infeasible plans are annotated and then discarded.** When at least one
   plan is both feasible and legal, `deterministic` is sorted from
   `feasible` only (`strategy.py:3740`). Annotate `feasible`, the stay-out
   anchor `plans[0]`, the weather plan, the driver-requested plans and the
   best plan of each shape; skip the rest. Compute the set first, annotate
   second.

**Target.** The same state in under 160 ms. Do not gate on wall time in
the test suite; gate on the structure:

- `tests/test_strategy_performance.py::test_the_pit_cycle_is_counted_once_a_plan`
  — wrap `StrategyEngine._pit_cycle_positions_recovered` with a counter; after
  one `compute`, calls ≤ number of annotated plans.
- `test_the_compound_rule_is_evaluated_once_per_distinct_schedule` — the
  same with `_used_compounds`: calls ≤ 40 for the 57-lap state.
- `test_the_projection_is_unchanged` — for ten seeded scenarios from
  `tools/strategy_fuzz.build_scenario`, the recommended plan and the top five
  `(box_laps, compounds, projected_finish_position, risk_adjusted_time_s)`
  are identical before and after. Capture the expected values from
  `1f35a7e` in a JSON fixture first.

Reproduce:

```bash
python - <<'EOF'
import asyncio, cProfile, pstats, sys
sys.path.insert(0, "src"); sys.path.insert(0, ".")
from tools.strategy_probe import build_field, new_engine, race_basics
from pitwall.strategy import infer_unrun_compounds

async def main():
    store, engine = await new_engine()
    def setup(state):
        race_basics(state, total_laps=57, current_lap=12, position=10)
        state.weather = "Clear"; state.tyre.compound = "MEDIUM"
        state.tyre.age_laps = 11; state.tyre.wear = [33.0] * 4
        state.tyre_sets = [{"compound": c, "available": True, "index": i}
                           for i, c in enumerate(("SOFT", "MEDIUM", "HARD", "HARD"))]
        state.drivers = build_field(current_lap=12, player_position=10,
                                    compound="MEDIUM", tyre_age=11)
    await store.mutate(setup)
    state = await store.snapshot_analysis()
    history = infer_unrun_compounds(await engine.database.tyre_history_model(3, context=state))
    profile = cProfile.Profile(); profile.enable(); engine.compute(state, history); profile.disable()
    pstats.Stats(profile).sort_stats("cumulative").print_stats(14)
asyncio.run(main())
EOF
```

---

## PF-02

**The live frame resends the unchanged strategy four times a second.**

**Where.** `app.py:1262` `websocket_state`; `state.py:765`
`_serialize_locked`, the `"live"` profile.

**Measured.** Mid-race, twenty cars, a computed strategy, 29 completed laps
with fifteen corners each: one frame is **102,682 bytes**, 401 KB/s per
connected dashboard.

| Key | Bytes | Share | Changes |
| --- | --- | --- | --- |
| `strategy` | 66,923 | 65% | on recompute, every few seconds at most |
| ↳ `rival_finish_projections` | 26,869 | 26% | includes a lap-time list per rival that no screen draws |
| ↳ `plans` (first three) | 22,805 | 22% | includes per-lap, per-wheel wear projections |
| ↳ `recommended` | 8,492 | 8% | |
| `drivers` | 27,643 | 27% | continuously |
| `recent_laps` | 10,728 | 10% | once a lap; carries every corner metric of eight laps |
| everything else | ~4,000 | 4% | continuously |

Three quarters of every frame changes at most once in several seconds. The
comment at `state.py:770` records that a 250 KB frame for two dashboards
once cost more CPU than the telemetry pipeline; the fix then was to stop
copying. The bytes are still serialised, sent, parsed and diffed by the
browser, and on Android by a phone that is also the display.

**Changes.**

1. **Version the slow part.** Add `strategy_revision: int = 0` to
   `SessionState`; increment it in the `publish` closure of
   `StrategyEngine._recompute_latest` (`strategy.py:2973`) and wherever
   `state.strategy` is otherwise replaced (`udp.py:1461` writes the game
   window into it twice a second — make that write compare first and leave
   the revision alone when the three numbers are unchanged).

2. **Let the client say what it holds.** The dashboard already acknowledges
   every frame (`app.py:1309`). Send the revisions with the
   acknowledgement — `ws.send(JSON.stringify({ack: 1, strategy: rev, laps:
   lapRev}))` in `static/index.html:1290` — and have `websocket_state` parse
   it (`json.loads` in a `try`; anything else means "send everything", which
   keeps an older client working).

3. **Send the slow part only when it moved.** `snapshot_live_json(
   strategy_revision: int | None = None, laps_revision: int | None = None)`:
   when the client's revision equals the current one, serialise
   `"strategy": {"revision": n, "unchanged": true}` and
   `"recent_laps": {"revision": m, "unchanged": true}`. In `render`, keep
   the last full `strategy` and `recent_laps` and substitute them when the
   marker arrives. `laps_revision` is `analysis.last_lap_analyzed`.

4. **Trim what no screen draws**, in the `"live"` branch of
   `_serialize_locked`:

   - from each entry of `rival_finish_projections`: `lap_times_s`,
     `pace_reference`, `finish_penalty`, `pit_stop_costs_s`;
   - from each plan's `stint_models[]`: `wheel_projection`, `lap_times_s`,
     `setup_effects`, `set_pace_reference`, `without_driver_feedback`;
   - from `recent_laps[]`: `corner_metrics`, `setup`, `incidents`,
     `traffic_evidence`.

   Check each against its readers first: `grep -n "wheel_projection\|lap_times_s\|corner_metrics" static/index.html static/js/*.js static/driver-dashboard/*.mjs`.
   The analysis and radio profiles are unchanged.

5. The second socket, `/api/v1/live/ws` (`api/live.py:509`), already has
   named subscriptions. Leave it; the Driver Dashboard reads small frames
   from it.

**Target.** A steady-state frame under 35 KB; a frame after a recompute
under 70 KB.

**Tests.** `tests/test_live_frame_size.py`:

- `test_an_unchanged_strategy_is_not_resent` — two calls to
  `snapshot_live_json` with the current revision: the second contains
  `"unchanged":true` and is under 40,000 bytes for the fixture state.
- `test_a_client_that_sends_no_revision_gets_everything`.
- `test_a_recompute_is_sent_once`.
- Extend `tools/drive-trace-ui-smoke.cjs` (jsdom) to feed a full frame
  followed by an `unchanged` frame and assert the strategy card still shows
  the instruction.

Reproduce the measurement with the state built in
`tools/strategy_probe.py` and `len(await store.snapshot_live_json())`.

---

## PF-03

**Tyre history is queried and refitted on every recompute, under the global
database lock.**

**Where.** `strategy.py:2967`; `database.py:1795` `tyre_history_model`;
`database.py:132` `_connect`.

**Measured.** With 300 stored laps for the circuit:
`tyre_history_model` takes 9–28 ms (first call 28 ms). It opens a new SQLite
connection, sets `PRAGMA journal_mode=WAL` and `foreign_keys`, runs two
queries, decodes four JSON columns for each of up to 300 rows, groups, and
fits a stint pace model and a ridge regression per compound. It holds
`PitWallDatabase._lock` throughout, so a lap being saved or a radio call
being recorded waits behind it.

The stored laps for a circuit change only when a lap is saved. The current
session's laps are excluded from the query (`session_uid != ?`), so during a
race the result changes only with the query point of the ridge fit (tyre
age, fuel, temperatures, mode, three setup values).

**Changes.**

1. Split `_tyre_history_model_sync` into `_tyre_history_rows(track_id,
   session_uid, limit)` — the SQL and JSON decoding — and
   `_tyre_history_fit(rows, context)`.
2. Cache the rows in `PitWallDatabase`:
   `self._tyre_rows_cache: dict[tuple[int, int], tuple[int, list[dict]]]`
   keyed on `(track_id, session_uid)` with a generation number. Increment
   `self._laps_generation` in `save_lap`, `reconcile_player_lap_history`,
   session delete and history import; a cache entry is valid while its
   generation matches.
3. Cache the fit on the rounded query: `(generation, compound, age,
   round(fuel, 0), track_temp, air_temp, mode, front_wing, rear_wing,
   on_throttle)`, last 32 entries.
4. `_connect`: `journal_mode=WAL` is a property of the database file, not
   of the connection. Set it once in `_initialize_sync` and drop it from
   `_connect`; keep `foreign_keys=ON`, which is per connection.
5. Reads do not need the write lock under WAL. Give read-only methods a
   separate path that does not take `self._lock`. Do this only for
   `tyre_history_model`, `get_personal_best` and `load_preference` in this
   change; they are the three called from the lap and strategy paths.

**Tests.** `tests/test_database_caching.py`:

- `test_history_rows_are_read_once_between_lap_saves` — patch
  `sqlite3.connect` with a counter; three `tyre_history_model` calls make
  one connection; a `save_lap` then one more call makes a second.
- `test_a_saved_lap_is_in_the_next_model` — the model's `laps_observed`
  rises after `save_lap` for a different session at the circuit.
- `test_a_history_read_does_not_wait_for_a_writer` — hold `_lock`; the read
  completes.

---

## PF-04

**A recompute is triggered by tyre wear to a tenth of a percent.**

**Where.** `proactive.py:1537` `_refresh_strategy_if_needed`.

**Derived, not measured.** The signature that triggers a recompute includes

```python
tuple(round(float(x), 1) for x in state.get("tyre", {}).get("wear", [])),
```

At 3% wear a lap on a 90-second lap, each wheel crosses a 0.1% step every
three seconds, and the four wheels do not cross together. The signature
also holds `last_lap_ms`, every rival's stop count and compound, the
weather and both rain percentages. In a race the planner therefore runs
about once every one to three seconds — 300 ms of interpreter time each on
a full-distance race (PF-01) — to answer a question whose inputs have
moved by a tenth of a percent of tyre.

`AnalysisEngine.process_lap` also calls `recompute` at the end of every
analysed lap (`analysis.py:260`), which is the one that matters.

**Changes.**

1. Round wear to whole percent in the signature, and add the worst wheel's
   distance to the next threshold that changes the call
   (`tyre_limits.safety_limit_pct`, ST-06) so the planner still runs the
   moment a limit is crossed:

   ```python
   tuple(int(float(x)) for x in wear),
   max(wear, default=0.0) >= safety_limit_pct(compound),
   ```

2. Add the fast weather evidence of [WX-08](01-weather.md#wx-08) step 6 —
   and only while the sky or the forecast is wet.
3. Add a floor between recomputes that were not caused by a discrete event:
   keep `self._last_recompute_at`; when the only parts of the signature that
   changed are continuous ones (wear, sector counts), skip the recompute if
   the last one was less than `settings.strategy_min_recompute_interval_s`
   (new, default `4.0`) ago. A change of lap, flag phase, weather label,
   tyre, tyre sets, driver feedback, risk appetite or any rival's stop count
   or compound recomputes immediately, as now.
4. Publish `strategy_recomputes_per_min` in `state.proactive` next to
   `evaluation_ms` (`proactive.py:3311`) so the footer and the
   troubleshooter can show it.

**Tests.** `tests/test_proactive.py`:

- `test_a_tenth_of_a_percent_of_wear_does_not_recompute` — two detect
  passes with wear `[30.0]*4` then `[30.1]*4`: `strategy.recompute` awaited
  once.
- `test_crossing_the_safety_limit_recomputes_at_once`.
- `test_a_rival_stop_recomputes_inside_the_floor`.
- `test_continuous_changes_respect_the_floor` — wear moving a whole percent
  twice within the interval: one recompute.
