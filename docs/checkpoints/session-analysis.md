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
   F1Visualized):
   - KPI row: winner and race time, fastest lap, the player's result, laps,
     neutralised (flag) laps.
   - **Race pace**: lap-time box plot per driver (median, quartiles, whiskers),
     sorted by median, team-coloured, driver codes on the axis.
   - **Race trace**: cumulative gap to a reference driver (winner by default)
     per lap, pit markers, neutralised-lap shading.
   - **Position changes**: position by lap for every driver, end labels.
   - **Tyre strategy**: horizontal stint bars per driver by compound, in
     finishing order, S/M/H/I/W letters as secondary encoding.
   - **Lap times**: lap time by lap for focus drivers with compound markers,
     107% outlier hiding.
   - **Lap-time heatmap**: driver × lap, diverging around each driver's median;
     clicking a cell opens that lap in Lap Lab.
   - **Race timelapse**: lap-by-lap animated gaps (F1Visualized style) with
     play, pause and scrub.
   - **Fastest and ideal laps**: best lap per driver and best-sector ideal lap.
   - **Pit stops**: lap, compound change and estimated time lost.
   Every chart has a data-table view, hover tooltips, and honest availability
   notes when data is missing.
2. **Lap Lab selection.** Pick a **driver, then a lap** for the candidate. The
   reference is still suggested automatically and the comparison runs
   automatically, but the driver can switch to **manual** and pick a reference
   **driver, then lap**, including any lap of the same session.
3. Robust tests (Python unit + API, Node unit for chart maths, real-data checks,
   browser checks at phone/tablet/desktop widths), then merge to `main` and
   release to production (Windows + Android + site + update flags).

## Key decisions

- **Data source.** Everything is derived from stored `recorded_laps` rows for
  the newest timeline epoch of each lap (the same rule as `FieldAnalysisService`),
  one row per physical car (identity revisions merged by `car_index`).
- **Race order and gaps: segmented lap-time sums.** Plain cumulative sums fail
  after a red flag: the game's lap timer stops while the race is suspended (by a
  different amount per car) and the restart is timed from lights out, like lap 1.
  Suspended laps are recognised from the field's pace (median lap time on the lap
  > 1.8× each car's usual pace; safety cars are ~1.3-1.6×) and each racing
  segment between suspensions is summed from its own first lap. On the real
  Singapore replay (red flag on laps 4-5) this reproduces the official order for
  all 22 cars, laps completed, best laps and gaps within 20 ms.
- **Why not traces?** Trace session clocks give exact line crossings, but in
  races the recorder keeps full traces only for a scoped set of cars
  (`cars_in_trace_scope` in `full_field_archive.py`; the player, teammate, podium
  and grid neighbours) and the player's own traces lack `current_lap_time_s`.
  Lap times exist for every car, so they are the only complete basis. Trace
  reads also cost ~2 s on desktop per session. This was tried and removed.
- **Finishing order is derived**, not official: laps completed, then segment
  time. Penalties are not applied. The full-field final classification is not
  stored. Cars with a hole in their lap record after the last restart are not
  placed (`incomplete_record`).
- **Tyres.** Compounds come from traces; cars without traces have none after the
  first laps. Three or more laps without tyre data form an `unknown` stint, never
  stretched from a known compound, and never counted as a tyre change. The game's
  final classification stores stint end laps one lower than ours (zero-based or
  laps completed); our end lap is the last lap driven on that set.
- **Pit stops** include red-flag tyre changes (`kind: tyre_change`, no loss
  estimate), matching the game's stop count. Stops under neutralisation get no
  loss estimate.
- **Race pace laps** exclude lap 1, pit-context laps, flag-context (SC/VSC/red)
  laps, laps without a time, and (when "hide outliers" is on, default) laps
  slower than 107% of that driver's median. Track-limit-invalid laps are kept
  (they are real race laps); the exclusion counts are shown.
- **Colour.** Team colour supports identity but never carries it alone: driver
  codes label every box/bar/line end. Line charts default to emphasis (focus
  drivers coloured, the rest grey). Tyre colours always come with letters.
  Heatmap uses a diverging blue↔orange scale around each driver's median with a
  neutral midpoint.
- **No new runtime dependencies**: charts are hand-built SVG (plus canvas only
  where existing code already uses it), so they work offline in the Android
  WebView.

## Files

| Area | File |
| --- | --- |
| Analysis maths (pure) | `src/pitwall/session_analysis.py` |
| API route | `src/pitwall/api/field.py` → `GET /api/v1/sessions/{id}/analysis` |
| Python tests | `tests/test_session_analysis.py` |
| Chart maths (pure JS) | `static/js/session-analysis-model.mjs` |
| Chart rendering | `static/js/session-analysis.js` |
| JS tests | `tests/session_analysis.test.mjs` |
| Markup | `static/index.html` (`#session-analysis` view, Lap Lab pickers) |
| Styles | `static/css/v42.css` |
| Lap Lab logic | `static/js/workspaces.js` |

## Test data

- Real full-field race: the 31-lap Singapore replay recorded by the 5.3.4 test
  app (session `ses_44a380e4a94a0bc59b959b73`, 22 cars, 666 lap rows incl.
  flashback branches). A copy of that app's database and traces lives outside
  the repo at `C:\Users\Sarth\PitBoxTestData\race-7f491ba\PitWallData`. Run the
  app against it with `PITWALL_DATA_DIR` pointing there (use a scratch copy so
  migrations don't touch the original).
- Independent expectations for that race (decoded from the original capture):
  `%LOCALAPPDATA%\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\YourPitBoxReview\2026-10-08\tablet-history\full-race-expected.json`
  (final classification: player car 6 P1, 31 laps, best 91,649 ms,
  3,466.302 s) and `full-field-lap-expectations.json` (624 completed laps,
  cars 4 and 5 retired after 2 laps).

## Status

| # | Checkpoint | Status |
| --- | --- | --- |
| 1 | Research + code map | done |
| 2 | Design canvas (Race Analysis + Lap Lab pickers) | in progress |
| 3 | `session_analysis.py` + tests against the real race | done |
| 4 | API route + tests | done |
| 5 | Frontend view, charts, Analyze session entry points | pending |
| 6 | Lap Lab driver → lap pickers, auto + manual reference | pending |
| 7 | Full suites, browser checks (phone/tablet/desktop) on real data | pending |
| 8 | Merge to `main`, version bump, CI builds | pending |
| 9 | Production release (sign APK, tablet upgrade, uploads, Worker, site, update flags, verification) | pending |

## How to resume

1. `git checkout feature/session-analysis` in
   `C:\Users\Sarth\OneDrive\Documents\Pit-box-main-wake-fix-v4.1`.
2. Read the status table and the log below; continue at the first checkpoint
   that is not `done`.
3. Run `.venv/Scripts/python.exe -m pytest tests -q` and
   `node --test tests/*.test.mjs` before and after changes.
4. Production release follows `docs/release-plan-5.3.4.md` (same steps, new
   version). Release credentials live under Codex's redirected AppData:
   `%LOCALAPPDATA%\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\YourPitBoxRelease`.
   Run release scripts with `LOCALAPPDATA` pointed at
   `...\LocalCache\Local` and the Android SDK at the real
   `%LOCALAPPDATA%\Android\Sdk`.
5. The C: drive is nearly full (under 0.5 GB free). Check `df -h /c` before
   builds or replays.

## Log

- **Checkpoint 1.** Mapped Analysis (Library, Session Review, Lap Lab, Field,
  History) in `static/index.html` and `static/js/workspaces.js`; field data in
  `src/pitwall/field_service.py`; references in
  `src/pitwall/comparison_service.py` (`list_references`, top 200 laps at the
  track, compatibility classes; manual references outside that list are sent
  with `allow_caveated_reference` and incompatible ones are rejected by the
  server). Team names: `src/pitwall/identity.py` `TEAM_NAMES`.
- **Checkpoints 3-4.** `src/pitwall/session_analysis.py` and
  `GET /api/v1/sessions/{id}/analysis` (wired in `app.py`, optional second
  argument of `create_field_router`). `tests/test_session_analysis.py` (29 tests:
  red flag, safety car, lapped/retired, record hole, stints, unknown tyres,
  practice, flashback epochs, identity revisions, route). Real-data check:
  `C:\Users\Sarth\PitBoxTestData\validate_race.py` compares the replay's analysis
  with the official classification decoded from the capture; remaining
  differences are only the 4 untraced cars' stop counts (no tyre data) and one
  off-by-one stint end on the player's red-flag change.
- **2026 season-pack team IDs** (476-486) are not in `TEAM_NAMES`. Rosters
  match the F1 25 team order offset by 476 with Cadillac at 486; the frontend may
  use that mapping for colours only, never for names, until the spec confirms it.
- Regression inventory workflow (recent issues → pressure tests) is running; its
  checklist will be appended here and drives the test plan for checkpoints 5-7.
