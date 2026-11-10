# Your Pit Box 5.6.0 — engineering assessment and build specification

An audit of the race-strategy core at `1f35a7e` (5.6.0), with every defect
reproduced where it could be, and a build specification for the next release
cycle. It is written to be implemented from directly: each item names the
file and line, says what happens and why, gives the change step by step, and
lists the tests that prove it.

| Part | File | Covers |
| --- | --- | --- |
| 1 | [01-weather.md](01-weather.md) | Wet-weather model, the rain call and its radio path |
| 2 | [02-rivals.md](02-rivals.md) | Rival stop prediction, pit-cycle projection, rival radio calls |
| 3 | [03-strategy-core.md](03-strategy-core.md) | Plan ranking, what-if, circuit tables, settings that do nothing |
| 4 | [04-performance.md](04-performance.md) | Measured hot paths and what to do about each |
| 5 | [05-features.md](05-features.md) | The feature programme: screens, data contracts, radio protocol |
| 6 | [06-reference-patch.md](06-reference-patch.md) | A prototype of the first-wave fixes (`reference.patch`), what it was run against, and the six tests it moves |

## How to use this document

1. Work in the order given in [Delivery order](#delivery-order). Each step is
   one pull request. Later steps assume earlier ones.
2. Before changing anything, run the two probes and keep their output:

   ```bash
   python -m tools.rain_onset_probe            # wet-model timing
   python -m tools.strategy_probe              # rivals, what-if, radio guard
   ```

   Both print what the engine does today. With `--assert-within 2` and
   `--assert` they become gates that fail today and must pass when the
   matching items are done. The item that turns each line green is named in
   the item's **Done when**.
3. After every step run `python -m pytest tests -q`. The baseline at
   `1f35a7e` is **3338 passed, 4 skipped** in about four minutes.
   `distribution/tests` needs Windows packaging tools and is expected to fail
   on Linux.
4. Where an item says an existing test pins the old behaviour, change that
   test in the same pull request and say why in the test's docstring. Do not
   delete a test to get green.
5. Screens follow the board organisation in `CLAUDE.md`: DRIVE holds only what
   a driver reads mid-race, planning lives on STRATEGY, pit-lane admin on
   CONNECTION. Nothing here redesigns DRIVE. New panels go on STRATEGY and in
   the Driver Dashboard widget set, and reuse the tokens in `static/css/v42.css`.

### Conventions used in every item

- **Where** — `path:line` at `1f35a7e`. Lines move; the named function is the
  anchor.
- **Severity** — `S1` a wrong or missing call reaches the driver and can cost
  the race; `S2` a number or sentence the driver or the model reads is
  materially wrong; `S3` latent, cosmetic or a setting that does nothing.
- **Evidence** — `reproduced` (a probe or script printed it), `read` (follows
  from the code as written), or `to verify` (depends on game behaviour that
  could not be observed here; the item says how to check).
- **Change** — numbered steps. Constants are given with values. Where a value
  is a judgement it says so and names the data that should replace it.
- **Tests** — test names to add, with arrange / act / assert.
- **Done when** — the observable result.

## What was examined

Read in full: `rain.py`, `strategy.py`, `proactive.py`, `tyre_learning.py`,
`tyre_inventory.py`, and the session, lap, status, damage, history and
tyre-set handlers in `udp.py`. Read in part, following the strategy and radio
paths: `state.py`, `analysis.py`, `brain.py`, `tools.py`, `engineering.py`,
`database.py`, `config.py`, `app.py`, `api/live.py`, `static/index.html`,
`static/js/strategy.js`. The packet layouts were read from the installed
`f1-packets` 2026.1.1 to establish which fields arrive and are not used.

Not examined: voice capture and playback (`voice.py`, `audio.py`,
`realtime.py`), provider clients, networking and transfers, Android, the
Windows installer, Session Analysis and Lap Lab. Nothing in this document is a
finding about those.

## Headline findings

Three results explain most of what a driver would describe as "the strategy
is slow in the rain and does not see what the others are doing".

1. **A forecast of 50% or less stops the intermediate call from ever being
   made, however long it has been raining.** The projection treats the
   game's rain percentage as the chance that the rain exists at each future
   sample and never conditions it on the rain that is falling now. With
   light rain observed, every car more than 15% off dry pace and the
   surface past the crossover for seven laps, the model still answers "stay
   out". At 70% and
   100% it calls the stop, withdraws it one lap later, and calls it again.
   ([WX-01](01-weather.md#wx-01), [WX-02](01-weather.md#wx-02); reproduced by
   `tools.rain_onset_probe`.)

2. **When the engine does want the stop, the radio can say the opposite.**
   The wet-weather radio trigger fires once, on the first tick the track is
   judged wet. If that tick's verdict is "watching", the later change to
   "box now" raises no weather call at all. The remaining path, the ordinary
   strategy call, passes through a guard that replaces "Box lap 30 for
   inters" with "Stay out — boxing costs you 7 places and gains nothing" in a
   reproduced case where the stop is 37 s quicker to the flag.
   ([WX-03](01-weather.md#wx-03), [WX-04](01-weather.md#wx-04); both
   reproduced by `tools.strategy_probe`, parts 3 and 5.)

3. **Rivals are never expected to make the stop the rules force on them.**
   Rival stops are predicted from a fixed stint length per compound (17, 27
   and 42 laps). In a 14- or 20-lap race nobody reaches it, so nineteen
   rivals who each owe a second compound are projected to run to the flag,
   the player is projected last whatever they do, and the engine calls the
   stop for the penultimate lap. ([RV-01](02-rivals.md#rv-01),
   [ST-01](03-strategy-core.md#st-01); reproduced by `tools.strategy_probe`.)

The rule behind the penultimate-lap call — among plans level on position,
prefer the latest stop whatever it costs in time — also accounts for most
of the time lost on the repository's own held-out benchmark. A prototype
that limits it to plans within 3 s of the quickest takes the
median regret on the 18 held-out worlds of `tools/strategy_validation.py`
from 31.1 s to 11.4 s, and the 90th percentile from 69.1 s to 23.4 s, with
no world worse ([ST-01](03-strategy-core.md#st-01),
[06-reference-patch.md](06-reference-patch.md)).

## Register of findings

### Weather ([01-weather.md](01-weather.md))

| ID | Sev | Finding | Evidence |
| --- | --- | --- | --- |
| WX-01 | S1 | Forecast chance is not conditioned on rain already falling; at ≤50% the stop is never called | reproduced |
| WX-02 | S1 | A completed lap's pace is compared with the surface *now*; the call is withdrawn a lap after it is made | reproduced |
| WX-03 | S1 | The weather radio trigger is a one-shot latch; "watching" → "box now" raises no call | reproduced |
| WX-04 | S1 | The radio's stay-out guard overrules a weather stop; the finish projection under-credits a wet-tyre advantage | reproduced |
| WX-05 | S2 | The spoken reason names a dry compound as "the tyre for it" on a wet track and blames race distance | reproduced |
| WX-06 | S2 | Evidence filters drop the laps that carry the signal; a local yellow blocks the whole field channel | read |
| WX-07 | S2 | Rivals' in- and out-laps are read as wet pace | read |
| WX-08 | S2 | Evidence arrives once a lap; sector times, tyre surface temperatures and race-control grip status are received and unused | read |
| WX-09 | S2 | "Box now" for the weather waits on a model round trip and carries a 34-word rules footnote | read |
| WX-10 | S2 | What-if ignores the weather: the engine's own inter stop prices 361 s *slower* than itself | reproduced |
| WX-11 | S2 | "It's raining", "it's wet" and "I need inters" are not recognised as surface reports | read |
| WX-12 | S3 | The engineer's weather tool ignores the wet model and applies a 60% rule of its own | read |
| WX-13 | S3 | `rain_next_15_pct` is one sample, spoken as "within fifteen minutes" | read |
| WX-14 | S3 | Dry benchmark drifts with fuel and tyre age; model constants are uncalibrated; dead value | read |

### Rivals ([02-rivals.md](02-rivals.md))

| ID | Sev | Finding | Evidence |
| --- | --- | --- | --- |
| RV-01 | S1 | A rival's mandatory compound change is not modelled | reproduced |
| RV-02 | S2 | Stop timing is a fixed table: no circuit severity, measured wear or race evidence; stops in the final laps; no compound change or cold tyre after the stop | read |
| RV-03 | S2 | Rivals never change tyres for the weather in the projection | reproduced |
| RV-04 | S2 | Tyre-set packets for every car but the player's are discarded | read |
| RV-05 | S2 | Rival stops are announced from the stop counter, for every car; coalescing keeps the highest car index; "undercut threat" reaches 25 s | read |
| RV-06 | S3 | The strategic-rivals view reads plan keys that do not exist; sprint races are excluded | reproduced |
| RV-07 | S2 | Rejoin position ignores the lap of the stop, cars in the pit lane and cars about to stop | read |
| RV-08 | S3 | The "catching" call uses an 8-second sample and a 0.15 s threshold no other path uses | read |

### Strategy core ([03-strategy-core.md](03-strategy-core.md))

| ID | Sev | Finding | Evidence |
| --- | --- | --- | --- |
| ST-01 | S1 | Among plans level on position and points the latest first stop wins whatever it costs in time | reproduced |
| ST-02 | S2 | Positions recoverable after a stop: fixed overtaking tax and retention, rounded then floored | reproduced |
| ST-03 | S2 | The two-compound rule is applied to every race length | to verify |
| ST-04 | S2 | Shanghai and the three reverse layouts have no pit-loss, severity or overtaking entry; pit loss is never measured | reproduced |
| ST-05 | S3 | `compute_target` takes its median from safety-car and pit laps and from the car ahead's last lap | read |
| ST-06 | S3 | Seven different wear thresholds across four modules | read |
| ST-07 | S3 | Settings that are documented and do nothing | reproduced |
| ST-08 | S3 | Monte Carlo adds noise to one deterministic race; no safety car, weather or rival scenario | read |

### Performance ([04-performance.md](04-performance.md))

| ID | Finding | Measured |
| --- | --- | --- |
| PF-01 | Finish projection recomputes per-plan what is constant per compute | 44% of a 320 ms compute |
| PF-02 | The live frame resends the unchanged strategy four times a second | 103 KB frame, 65% strategy |
| PF-03 | Tyre history is re-queried and refitted on every recompute under the global database lock | 9–28 ms per call |
| PF-04 | Recompute is triggered by tyre wear to 0.1% | derived: a recompute every 1–3 s |

### Feature programme ([05-features.md](05-features.md))

| ID | Feature |
| --- | --- |
| F-01 | Weather wall: nowcast, forecast timeline, crossover clock, tyre cost table |
| F-02 | Fast surface channel: sector pace, tyre surface temperature, race-control grip status |
| F-03 | Weather call protocol: heads-up → stand by → box → stand down |
| F-04 | Rival strategy model and pit-window board |
| F-05 | Undercut and cover calculator, with stop detection at pit entry |
| F-06 | Rejoin map: where the car comes out, lap by lap |
| F-07 | Race simulator: safety-car, weather and rival scenarios behind every plan |
| F-08 | Safety-car readiness: the call computed before the car comes out |
| F-09 | Self-calibrating circuit model: pit loss, AI stop wear, severity, overtaking |
| F-10 | Forecast reliability log and wet-model calibration |
| F-11 | Strategy debrief: every call, what it was based on, what it cost |
| F-12 | Strategy replay harness: a recorded race as a regression test |

## Delivery order

Each row is one pull request. "Gate" is the command that must pass.

| Step | Items | Why here | Gate |
| --- | --- | --- | --- |
| 1 | WX-01, WX-02, WX-05 | The wet model's own verdict has to be right before anything downstream is touched. A prototype that passes the gate and the whole suite is in [06-reference-patch.md](06-reference-patch.md). | `python -m tools.rain_onset_probe --assert-within 2` |
| 2 | WX-03, WX-04, WX-09, F-03 | Makes the corrected verdict reach the driver once, promptly, and uncontradicted. | `python -m tools.strategy_probe --assert` (parts 3 and 5); new tests in `tests/test_weather_call_protocol.py` |
| 3 | WX-10, RV-06, ST-07 | Small, independent, visible on the Strategy page. | `python -m tools.strategy_probe --assert` (parts 2 and 4) |
| 4 | RV-01, RV-02, RV-03, RV-04, F-04 | The rival model. Largest single change to the finish projection. | `python -m tools.strategy_probe --assert` (part 1) |
| 5 | ST-01, ST-02, RV-07, F-06 | Ranking and rejoin, which only mean something once rivals stop when they should. | `python -m tools.strategy_probe --assert` (all) |
| 6 | WX-06, WX-07, WX-08, WX-11, F-02 | Faster and cleaner wet evidence. | new tests in `tests/test_rain_fast_channel.py` |
| 7 | PF-01 … PF-04 | Headroom for steps 8–10. | timings in [04-performance.md](04-performance.md) |
| 8 | RV-05, RV-08, F-05 | Rival radio calls that are about the right car. | new tests in `tests/test_rival_calls.py` |
| 9 | F-01, WX-12, WX-13 | The weather wall and the tool that reads it. | UI smoke `tools/strategy-layout-smoke.cjs` extended |
| 10 | ST-03, ST-04, ST-05, ST-06, F-09, F-10 | Calibration from recorded data. | new tests in `tests/test_circuit_calibration.py` |
| 11 | ST-08, F-07, F-08 | Scenario simulation on top of a sound deterministic core. | new tests in `tests/test_race_simulator.py` |
| 12 | F-11, F-12 | Audit and regression tooling for everything above. | a recorded wet race replays to the expected calls |

Steps 1–3 are small and change no data model. Steps 4–5 change what the
finish projection means and should ship together in one release. Steps 6
onward are independent of each other except where the table says otherwise.

## Rules that apply to every step

- The model never interprets raw telemetry. Every new number in this
  document is computed in Python and handed to the model as a fact, through
  the existing tool allow-list.
- A value that is not known is reported as unknown. None of the new models
  may fill a missing rival reading with zero or a default without labelling
  the result as estimated and lowering its confidence.
- A call that names a stop is said once per change of content. Every new
  radio call in Part 5 defines the content that makes it a new call.
- Every new constant is declared at module level with a comment that says
  whether it is measured, taken from the game, or a judgement, and what data
  would replace a judgement.
- Screens are English-keyed through `i18n.t(...)`. Add new keys to the three
  catalogs in `static/i18n/` with `tools/i18n_extract.py`.
