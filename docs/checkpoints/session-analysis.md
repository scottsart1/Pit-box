# Checkpoint: Session Analysis and Lap Lab driver selection

Working document for the feature branch `feature/session-analysis`. It records
the goal, the decisions, what is finished and what remains, so that another
engineer (or another AI coding agent) can resume from the last checkpoint
without rediscovering anything. Update the status table and the log at every
checkpoint.

## Goal

1. **Session Analysis.** When the driver presses **Analyze session** (Library
   row action or the Session Review header), open a post-session analysis view
   with the visuals popular after a race (TracingInsights, Formula 1 Dashboard,
   F1Visualized): KPI row, race pace box plot, race trace, position changes,
   tyre strategy (S/M/H/I/W letters), lap times, lap-time heatmap (a cell opens
   the lap in Lap Lab), race timelapse, fastest and ideal laps, pit stops.
   Every chart has a data table, tooltips and honest availability notes.
2. **Lap Lab selection.** Pick a **driver, then a lap** for the candidate. The
   reference is suggested and compared automatically, but the driver can pick
   a reference **driver, then lap** manually, including any lap of the same
   session.
3. Robust tests (Python unit + API, Node unit, real-data checks, browser checks
   at phone/tablet/desktop widths), then merge to `main` and release to
   production (Windows + Android + site + update flags).

## Key decisions

- **Data source.** Stored `recorded_laps` rows of the active flashback branch:
  rows a flashback abandoned are flagged in place (`invalid_reason_mask` bit 2)
  and never read; then the newest timeline epoch per car identity and lap.
  Across identity revisions a row that observed the lap beats a timing-only
  copy. Laps still awaiting confirmation after a frame-only flashback are
  marked unconfirmed (shown, but kept out of pace and fastest laps).
- **Race order and gaps: segmented lap-time sums.** The game's lap timer stops
  during a red flag and the restart is timed from lights out, so each racing
  segment between suspensions is summed from its own first lap. Suspended laps
  are laps where the field runs at more than 1.8 times its usual pace (the
  lower quartile of each car's laps); with fewer than three cars a recorded
  red flag must confirm it. Each car has its own suspended and restart laps (a
  car a lap down meets the red flag earlier). A race stopped and not resumed
  is classified by countback. Penalties are not applied; the order is labelled
  derived and cross-checked with the player's official result when one exists.
- **Why not traces?** In races full traces exist only for a scoped set of cars;
  lap times exist for every car.
- **Safety cars come from race control**, never from how many cars had a flag
  on a lap (flag context is per car since 4.13.1). Lost messages are bounded
  by the field's pace; message laps are the player's laps and are moved onto
  the leaders' laps for a lapped player.
- **Unknown is unknown.** Laps known only from the game's lap history have no
  pit or flag context: they are excluded from pace (`no_context`), and a car
  whose stops could hide in them has an unknown stop count, never 0. A tyre
  change with no recorded stop and no suspension next to it is an
  `unrecorded_stop` with no loss estimate.
- **Colour** supports identity but never carries it alone: driver codes label
  every mark, tyre colours always come with letters, diverging blue/orange
  heatmap around each driver's median.
- **No new runtime dependencies**: hand-built SVG, works offline in the Android
  WebView. Never use `window.confirm`/`alert` in these views.

## Files

| Area | File |
| --- | --- |
| Analysis maths | `src/pitwall/session_analysis.py` |
| API route | `src/pitwall/api/field.py` -> `GET /api/v1/sessions/{id}/analysis` |
| Python tests | `tests/test_session_analysis.py`, `tests/test_session_analysis_regressions.py`, `tests/test_session_analysis_real_race.py` (opt-in) |
| Shared fixture | `tests/session_analysis_fixture.py` -> `tests/fixtures/session_analysis_race.json` |
| Chart maths (pure JS) | `static/js/session-analysis-model.mjs`, tests in `tests/session_analysis_model.test.mjs` |
| Chart rendering | `static/js/session-analysis.js` |
| Markup / styles | `static/index.html` (`#session-analysis`, Lap Lab pickers), `static/css/v42.css` (`.sa-*`, `.picker-*`) |
| Lap Lab logic | `static/js/workspaces.js` |

## Test data

- Real full-field race: a 31-lap Singapore replay (22 cars, red flag on laps
  4-5, safety car on laps 13-16). The recording is private;
  `tests/test_session_analysis_real_race.py` runs only when
  `PITWALL_REAL_RACE_DATA` (a data directory) and `PITWALL_REAL_RACE_EXPECTED`
  (the final classification decoded from the original capture) are set, and it
  copies the database before reading it.
- Synthetic: regenerate the shared fixture with
  `PYTHONPATH=src python tests/session_analysis_fixture.py`.

## Status

| # | Checkpoint | Status |
| --- | --- | --- |
| 1 | Research + code map | done |
| 2 | Design canvas (Race Analysis + Lap Lab pickers) | done |
| 3 | `session_analysis.py` + tests against the real race | done |
| 4 | API route + tests | done |
| 5 | Frontend view, charts, Analyze session entry points | done (first version) |
| 6 | Lap Lab driver -> lap pickers, auto + manual reference | done (first version) |
| 7a | Regression pressure tests and fixes, backend | done |
| 7b | Regression fixes, Session Analysis UI | done - merged |
| 7c | Regression fixes, Lap Lab pickers and auto-run | done - reviewed adversarially, all findings fixed |
| 7d | Browser smokes locally and on a PR; screenshots | done - local and CI |
| 8 | Squash-merge to `main`, CI builds | done - both installer workflows green |
| 9 | Production release (5.4.0, Android revision 37) | superseded - 5.4.0 was installed on a test tablet only; the public release is 5.4.1 |
| 10 | Race classification on imperfect recordings (5.4.1) | done - see below |
| 11 | Production release (5.4.1, Android revision 38) | done - see Release record |
| 12 | Website screenshots refreshed, site copy made concise | done - Session Analysis stills 23/24, home page 4,317 -> 3,249 words, guide 4,945 -> 4,375 |

## Race classification on imperfect recordings (5.4.1)

Field check of 5.4.0 against the test tablet's real history (103 races, most
recorded by versions before 5.3.4) found the derived race order unusable on
most of them:

- Older versions wrote a field car's lap row only when its full-field trace
  batch completed, so many laps are missing for most of the field at once
  (Singapore Race 2: lap 5 for 18 cars after the red flag). A single hole
  left a car unplaced, so cars with continuous records were ranked among
  themselves as if they were the whole field: four cars that stopped after
  lap 2 were shown P1-P4 "finished" while the 18 cars that ran 31 laps were
  unplaced.
- Two cars can be flagged as the player (a phantom car 0 plus the real car).
  Only the real player's rows carry legacy lap ids.
- The player's final lap and every finisher's final-lap time are often
  missing; some recordings stop before the race distance.
- The recorder bounded per-car loops by the game's active-car count, which
  drops when a car retires while every other car keeps its index: the two
  highest-index cars stopped being recorded from that lap on.

Done (branch `wf/race-classification`, then `release/5.4.0`):

- The recorder saves every car's final classification (`FCLS` session event)
  and the game's lap chart (`LPOS`), not only the player's result
  (`tests/test_session_record_classification.py`).
- The recorder keeps recording every car slot that has had a lap after a
  retirement (`tests/test_recording_after_retirements.py`). On a 10x replay of
  the Singapore capture the two highest-index cars went from 2-3 to 15-16 laps
  with telemetry context.
- `classify_race` in `session_analysis.py` orders the field from the best
  evidence: the game's classification; lap times when the record is
  sufficient (unchanged, no trace read); otherwise the game's recorded
  lap-end positions (lap chart, the player's legacy rows, the last trace
  sample), with strict finish rules (settled final-lap position; the lap
  before only when free at the finish and the car behind is still behind;
  retired cars and records that simply end are not placed; a recording that
  stops early claims no finish). Without positions, a lap-time place is kept
  only when no car with a hole could be ahead (lower bound: missing laps at
  97% of the session's fastest). `tests/test_session_analysis_imperfect_records.py`.
- Player identity from legacy lap rows; official result compared only with
  such a player. Session-event window ignores an end time equal to the start.
- Validation: on the tablet's 59 races 522 cars placed (214 before), no
  placed position contradicting an official result; both Singapore replays
  (lap-time path and classification path) equal the official classification
  for every car. Harness: `race-dataset` + `harness/` in the job scratch
  folder (not in the repo).
- UI: headline names the order's source; DNF/DSQ/NC results; timelapse
  labels use the game's positions.
- Version 5.4.1, Android revision 38; `docs/release-notes-5.4.1.md`.

### Release record (5.4.1)

- `main` at the release: CI Windows installer run 38070822153 (passed on a
  re-run; the first attempt failed a 0.2 s event-loop timing check by 1 ms)
  and Android run 38070819649, both green.
- Windows `PitWall-Setup.exe` SHA-256
  `6538bd5fd586c639ee940dc50e3d9a8b255d0287fb1ce32f959218449b226365`,
  35,296,141 bytes, identical to the CI install smoke's installer.
- Android `YourPitBox-5.4.1-android.38.apk` SHA-256
  `67719e84a9ff17d31978d2985a00375b5b7e52ffaaeecfd15258082cefcee439`,
  73,341,578 bytes, signed with the existing production certificate.
- Test tablet upgraded in place from 5.4.0: 160 sessions and 28,177 lap rows
  preserved with an identical fingerprint. With 5.4.1 on the tablet's own
  data: 520 cars placed over 59 races, Singapore Race 2's 18 placed cars all
  equal to the official classification, no player result contradicting an
  official one, no impossible output; a first open takes up to 3.5 s (traces
  read once), repeats 0.12 s. The race engineer answers on the tablet.
- Both artifacts uploaded to R2; public `/installer` and `/android` hashes
  verified; Worker deployed; website deployed with the checksums; update
  manifests published for Windows and Android.

All checkpoints are done.

## Backend contract (schema_version 2)

- `laps[].neutralised`: `"SC"`, `"VSC"` or null (field-wide, race control).
  `laps[].pit` / `laps[].flags` are null when the lap had no telemetry
  context. New lap fields: `context_observed`, `unconfirmed`,
  `driver_excluded`, `traced`.
- `pace_excluded` values: `PACE_EXCLUSION_REASONS` (`missing_time, lap_one,
  suspended, restart, safety_car, vsc, unconfirmed, driver_reported,
  no_context, pit, flags, invalid, outlier`).
- Stop kinds: `STOP_KINDS` (`pit_stop`, `tyre_change` - only next to a
  suspension - and `unrecorded_stop`).
- Driver status: `DRIVER_STATUSES` (adds `unranked` for a single recorded car,
  `running` for provisional sessions). `pit_stops` is null when unknown
  (`pit_stops_recorded` holds what was seen); `classified_at_lap` is set on
  countback; `best_lap_traced`.
- Top level: `field {cars, cars_with_laps, complete}`, `race_control
  {available, covered_from_lap, neutralisations, safety_car_laps, vsc_laps,
  red_flag_laps}`, `record_complete`, `official_result {player_position,
  derived_player_position, agrees}`, `session.provisional`,
  `session.display_name`, `session.winner_laps`, `basis.neutralisation`,
  `basis.sectors`.

## Remaining work

Each item needs a behaviour test.

1. **Session Analysis UI** (`session-analysis-model.mjs`, `session-analysis.js`,
   `.sa-*` styles):
   - Map every new enum value to driver-facing text; "Tyre change while
     suspended" only for `tyre_change`, "Stop not recorded" for
     `unrecorded_stop`; never a raw key, "undefined" or "0 stops" for an
     unknown count; retired cars show "Retired".
   - Headline: no "wins" when `session.provisional` or `!field.complete`;
     show the official result when it differs; "Fastest recorded lap" when
     `!record_complete`; safety-car text from `race_control`.
   - Lapped finishers never labelled "out" in positions or the timelapse; the
     final timelapse frame holds every classified car.
   - Clear title, KPIs, charts, tables and the thin-pace note on every session
     switch, failure, empty or partial payload; drop the remembered session on
     404 or deletion; refresh the session list after deletions.
   - Initialise when the module loads after the router already showed
     `#session-analysis` (reload or deep link).
   - Practice/qualifying: focus changes redraw lap times; pace caption from
     `basis.pace_laps`.
   - Reduced-motion timelapse keeps one cancellable timeout; stop on pagehide
     and deletion.
   - Size charts from the container width (text at least about 10-11 CSS px
     on a 390 px phone) and re-render on resize; keep pinch-zoom; the heatmap
     scrolls horizontally on touch; act only on click; tooltips at the mark,
     correct inside a scrolled heatmap; a first tap on a cell shows its value.
   - 44 px timelapse range and controls; 'W' letter contrast; race-pace
     outliers clipped to their chart.
   - Accessibility: one tab stop per chart with arrow keys, named marks, no
     `role="img"` around interactive marks, tooltip not announced on pointer
     move, data tables for lap times, heatmap and timelapse.
   - Register `session-analysis` in `tools/analysis-ui-smoke.cjs`,
     `tools/onboarding-ui-smoke.cjs` and `tools/all-workspaces-ui-smoke.cjs`.
2. **Lap Lab** - done (see the log). Kept for reference:
   - Hide abandoned laps (`invalid_reason_mask & 2`); label unconfirmed and
     untraced laps; one option per driver/lap/timeline; default laps and the
     suggested reference must have telemetry (two real-race cars have
     untraced fastest laps).
   - A late lap-trace load must not overwrite a pending or failed comparison
     status; one comparison in flight, last selection wins; no POST per key
     press while stepping through reference drivers.
   - `tools/analysis-ui-smoke.cjs` fails two cases now: "Review row opens
     single-lap playback, controls and solo analysis without Compare"
     (auto-run posts once; change the assertion deliberately: playback works
     while the comparison is pending) and "Clearing the session removes both
     ready and failed lap notices and disables playback".
   - Hand-offs from Session Analysis: last tap wins; add the analysed session
     to the session selects when the Library list lacks it; honest error for
     deleted sessions.
   - Other-session reference labels show type and date; distinct accessible
     names for the four selects; keep focus on "Use suggested"; deleting the
     session that supplies the reference resets the comparison.
3. **Docs**: guides updated; check every sentence of the Session Analysis
   section of `docs/release-notes-5.4.0.md` against the merged behaviour.
4. **Verification**: `python -m pytest tests -q`, `node --test
   tests/*.test.mjs`, every jsdom and Playwright smoke listed in
   `.github/workflows/android-apk.yml` (install `jsdom` and
   `playwright@1.58.2` into a temporary prefix, point `NODE_PATH` at it;
   Playwright smokes accept `PITBOX_BROWSER_PATH` / `PITBOX_BROWSER_CHANNEL`
   for an installed Chrome), real-browser screenshots at 390x844, 800x1280,
   1691x879 (touch) and 1440x900, then a PR so CI runs them too.
5. Squash-merge to `main`; release 5.4.0 following
   `docs/release-plan-5.3.4.md` with Android revision 37.

## Log

- **Checkpoint 1.** Mapped Analysis (Library, Session Review, Lap Lab, Field,
  History) in `static/index.html` and `static/js/workspaces.js`; references in
  `src/pitwall/comparison_service.py` (`list_references`, top 200 laps at the
  track; manual references outside that list are sent with
  `allow_caveated_reference`).
- **Checkpoints 3-4.** `session_analysis.py` and the analysis route (optional
  second argument of `create_field_router`).
- **2026 season-pack team IDs** (476-486) are not in `TEAM_NAMES`; the
  frontend uses the F1 25 team order offset by 476 (Cadillac 486) for colours
  only, never for names.
- **Checkpoints 5-6.** Chart maths, rendering, `#session-analysis` view and
  `#tab-session-analysis`; entry points dispatch `pitwall:analyze-session`;
  heatmap cells dispatch `pitwall:open-lap`. **Any new Analysis subview must
  also be added to `ANALYSIS_SUBVIEWS` in `static/js/connection.js`**
  (otherwise its tab sync hides every page; `tests/test_ui_analysis_v461.py`
  checks it). Lap Lab: new `#candidateDriverSelect`, `#referenceDriverSelect`,
  `#referenceChoice`, `#referenceUseSuggested`; `#candidateLapSelect`,
  `#referenceLapSelect` and `#createComparison` ("Compare again") kept.
- **Checkpoint 7a.** Regression pressure tests from past incidents in
  `tests/test_session_analysis_regressions.py`: abandoned flashback laps
  through the real catalog, a random-race property test, race control, unknown
  context, unrecorded stops, partial fields, lapped cars under a red flag,
  countback, lost race-control packets, lap notes, C1-C5 compounds,
  identity-revision duplicates, the player's pit out-lap, 404/409 routes,
  event-loop and query-plan checks. Field Lab and the reference list also
  ignore abandoned rows now, and `session_events` has an index on
  `(session_uid, created_at)`. On the real race the analysis matches the
  official classification for order, laps, best laps and gaps; stop counts
  match except the two untraced cars (unknown, not 0) and the two retirements
  (the game counts the retirement as a stop).
- **Checkpoint 7c.** Lap Lab (`workspaces.js`): pickers read the active branch
  like Session Analysis (no abandoned rows, newest timeline per identity, one
  row per car and lap across identity revisions, telemetry first); timing-only
  laps are labelled and never a default or a reference; the suggested reference
  is compared once the lap has played back, manual picks once the choice
  settles (350 ms), Use suggested and Compare again at once; a late lap trace
  never overwrites a comparison's status; playback keeps running and keeps its
  distance when a comparison lands or is refused; hand-offs from Session
  Analysis let the last pick win, reload a stale or failed session copy, and
  stand down when the driver picks a session; other sessions are named by type
  and date without delaying the comparison; deleting a session drops its laps
  and recompares only when it supplied the chosen reference; the server no
  longer offers abandoned or superseded laps as references. `pitwall:session-deleted`
  is dispatched for Session Analysis. Smoke: `tools/analysis-ui-smoke.cjs` (44
  cases); layout: `tools/lap-lab-layout-ui-smoke.cjs` (six sizes).
- **Comparisons and Field corners.** Comparisons are stored per pair (input
  hash); repeating a pair keeps its row and timestamp, so browsing does not
  change Field corners. The first comparison of a new pair (an automatic
  suggestion or a manual pick) becomes that lap's newest comparison, as a
  Compare click always did.
- **Running the browser smokes locally.** Install `jsdom` and
  `playwright@1.58.2` into a folder outside the repo (`npm install --prefix
  <dir> --no-save jsdom playwright@1.58.2`, with
  `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1` to use an installed Chrome), set
  `NODE_PATH=<dir>/node_modules`, and for Playwright smokes
  `PITBOX_BROWSER_CHANNEL=chrome` (or `PITBOX_BROWSER_PATH`).
