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
| 7b | Regression fixes, Session Analysis UI | **next** - see Remaining work 1 |
| 7c | Regression fixes, Lap Lab pickers and auto-run | pending - Remaining work 2 |
| 7d | Browser smokes locally and on a PR; screenshots | pending - Remaining work 4 |
| 8 | Squash-merge to `main`, CI builds | pending |
| 9 | Production release (5.4.0, Android revision 37) | pending |

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
2. **Lap Lab** (`workspaces.js`, Lap Lab markup):
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
3. **Docs**: replace "Analyze / reprocess" and "Compare laps" in
   `docs/SHAKEDOWN_4_2.md` and `docs/PIT_WALL_4_2.md`; check every sentence of
   `docs/release-notes-5.4.0.md` against the merged behaviour.
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
