# Your Pit Box 5.7.0

5.7.0 changes how the strategy engine reads the race and what it says about
it. The stop for inters is called on the rain that is falling and said once.
Rivals are projected making the stops the rules and the weather force on
them, and the radio calls the stop of the car that matters, with the
arithmetic to cover it. Every plan is judged against possible races, with and
without a safety car or rain. After the race, Session Analysis shows every
strategy call beside your stops.

## Rain

- **The stop is called on the rain that is falling.** A forecast chance is
  now read against the weather already observed, so light rain with a 50%
  forecast no longer projects half the race drying out and cancels the call.
  A lap's pace is judged against the surface it was driven on, not the
  surface now, so the first wet lap no longer withdraws a stop a lap after
  calling it. A stop is committed once something measured agrees with the
  sky (the surface past the crossover, lap or sector pace, a split field or
  your report); until then the call is to stand by.
- **One weather call per change.** The weather call has levels: watch, ask,
  stand by and box. It is said when the level rises or what it says changes
  (the tyre, the lap, the direction). A stop the engine withdraws is stood
  down once ("Cancel the stop. Stay out on the mediums."), a "Box, box.
  Inters on." reminder comes just before the pit entry on the lap of the
  call, and a box call you heard and did not act on is said again only when
  the case for it has grown, at most three times a spell. The stay-out guard
  no longer contradicts a weather stop with "Stay out — boxing costs you …".
- **Faster, cleaner evidence.** A lap in traffic, past a local yellow or on
  which the rain arrived now counts for less instead of nothing; only a full
  safety car, VSC or red flag stops the field being measured. Rivals' in-laps
  and out-laps are skipped. The first two sectors of every car's lap in
  progress move the estimate before the lap is complete. "It's wet" and "I
  need inters" are heard as reports of a wet track; "it's raining" as rain in
  the air, which confirms nothing on its own.
- **Track conditions on STRATEGY.** Under the current call: the call, the
  surface on a bar with both crossovers, the next thirty minutes of
  forecast, the surface to the flag and every tyre's cost. DRIVE's weather
  line leads with the stop when one is called ("BOX lap 13 · inter — track
  wet"). Asked about the weather, the engineer answers from the same
  picture. "Rain within fifteen minutes" now means the highest chance in
  that window, not the last sample in it.
- **Quiet in the pit lane.** While you are in the lane making a stop, the
  weather call holds and is compared afresh at the exit, so crossing the
  line before the new tyre is reported no longer reads as "Cancel the stop".
  The drying heads-up is no longer said while the track is still getting
  wetter.

## Rivals

- **Rivals owe their stops.** A rival that has raced only one dry compound
  in a dry race is projected to stop for another, at a point in the race set
  by its tyre, sooner if its tyre life forces it. In a short race this was
  missed entirely and every stopping plan of yours was projected last. On a
  changing surface rivals box for inters, and back to slicks, when the
  projected surface says so. The Strategy page marks a rival whose second
  compound is still owed.
- **The stop that matters, at pit entry.** Rival stops are called as the car
  enters the lane, not a lap later, and only for a car that matters: within
  two places, within the range two laps of new tyres can bridge, or one you
  asked about. Others in the lane with it are named; the rest are left to
  the Strategy page. Nothing is said about rivals while you are in the lane.
- **The cover.** For a car behind: "Sainz is in the pits, 2.1 behind. Box
  next lap and you stay ahead by 0.8." or "You can't cover it; stay out and
  run your race." For a car ahead, the pace to find in clear air. Ask "can I
  cover him?" for the same arithmetic. When three cars on your tyre have
  stopped, the radio says the window is open, once a stint.
- **Catching.** "Sainz is catching" now reads the gap over about a lap, not
  eight seconds, and says how soon the car will be with you. A tow down one
  straight is no longer catching.

## Plans

- **Where you come out.** A stop's rejoin is judged on the lap it is made,
  against every rival's projected race including their own stops. STRATEGY
  shows fifteen laps of rejoin position, the cars either side and clear air
  ("Where you come out"); tapping a lap prices that stop. A box call for this
  lap or the next names the car you rejoin behind.
- **Later is not free.** Among plans level on position and points, a later
  stop wins only while it is within 3 s of the quickest.
- **What-ifs use the planner.** A hypothetical stop is priced by running the
  full planner with that stop fixed, so the weather, your tyre sets, traffic
  and the finishing position are the same on both sides of the comparison.
- **Possible races.** Each plan is judged against races drawn with and
  without a safety car or a VSC, each weather scenario, and rivals stopping a
  lap early or late. Each plan shows its chance of a win, a podium and
  points, and the recommended plan says what a safety car and rain do to it:
  "A safety car gains this plan one place on average. Rain costs it two."
- **Ready for a safety car.** Once a lap the engine works out what it would
  do if a safety car or VSC came out on that lap. STRATEGY and DRIVE show it
  ("If a safety car comes out this lap: box for hards, about 11 s better."),
  and when the flag does come out on that lap before the pit entry, the
  race-control call carries the instruction straight away.
- **Circuits.** Shanghai has its own pit loss, tyre wear and overtaking
  figures, and the reversed layouts take their circuit's pit loss and wear.
  Each finished race now teaches the circuit its pit loss, tyre wear and
  safety-car rate; a measurement is used once there is enough of it and it
  agrees with the table within 30%, and CONNECTION shows each value with its
  source and "Forget this circuit".
- **Tyre limits and the race target.** Every wear threshold the planner, the
  radio and DRIVE act on now comes from one place: a soft is critical at
  80%, where the planner already ends its stint. The race target ignores
  safety-car laps, in-laps and invalid laps, and follows the car ahead only
  when it is comparable.

## After the race

- **Every strategy call beside your stops.** Session Analysis adds a calls
  lane under the stint chart (one marker per call, coloured by whether it
  was said) and "Your stops and the calls behind them": for each stop, the
  lap recommended before it, the lap it was called and whether you heard it,
  and the best stop lap on the lap times you actually set, with what the
  difference cost in seconds and places. Within 1.5 s is on the money. Each
  spell of rain shows when the surface crossed to inters and how many laps
  later you stopped. This is a comparison on known pace, not a claim about
  what could have been known at the time.

## Performance

- A planner compute does less repeated work (about a third quicker on a
  57-lap race), stored tyre history is read once per change, and the live
  frame to the dashboard is about a third of its old size between
  recomputes. In a race the engine now also computes the safety-car and VSC
  call once a lap, two extra computes per lap.

## For operators

- On first start the database is backed up and upgraded to schema 5700,
  which stores each lap's conditions (surface estimate, field sector ratio,
  whether it was neutralised or under a local yellow). 5.6.0 cannot open the
  upgraded database, and a history transfer needs both devices on 5.7.0.
  Each finished race also keeps its forecast log and its circuit record in
  the database.
- New settings: `strategy_compound_rule` (`auto`, `always`, `never`) forces
  the two-compound rule on or off; `strategy_later_stop_tolerance_s` (3 s)
  and `strategy_min_recompute_interval_s` (4 s). `strategy_risk_quantile` now
  selects that quantile of the simulated times. `proactive_safe_throttle`,
  `ptt_debounce_ms`, `trace_chunk_seconds` and `analysis_distance_step_m`
  were read by nothing and are removed; a `.env` that still sets them is
  accepted.
- New endpoints: `GET /api/strategy/weather`,
  `GET /api/strategy/cover/{car_idx}`, `GET /api/circuit-model`,
  `DELETE /api/circuit-model/{track_id}` and
  `GET /api/v1/sessions/{id}/strategy-review`. The engineer gains
  `get_weather_picture`, `evaluate_cover` and `get_rejoin_map`.
- The live frame carries `strategy_revision`; a dashboard that acknowledges
  it receives `{"unchanged": true}` in place of a plan it already has. An
  older client still receives every frame in full.
- For development: `tools/strategy_replay.py` replays a capture or a
  generated race through the engine and the radio and checks the calls it
  makes; `tools/replay_demo.py` can generate a race with rain
  (`--rain-from-lap`); `tools/forecast_reliability.py` reports what the
  game's rain percentage has meant across stored races.

## Not yet

- The wet model is not yet fitted to stored forecast logs, and the race
  simulation does not yet move a plan's own stop to take a safety car.
- The cover arithmetic has no rival-row display yet; it is on the radio and
  through the engineer.

Windows remains an explicitly unsigned direct-download installer, and Android
keeps the existing signing identity. The engineer's radio stays in English.
