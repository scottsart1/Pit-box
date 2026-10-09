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
  the newest timeline epoch of each lap (the same rule as `FieldAnalysisService`).
  `started_game_ms`/`ended_game_ms` are empty for field laps, so elapsed race
  time is the **cumulative sum of lap times** (lap 1 is timed from the start in
  the F1 games, matching FastF1's convention). A car with a missing lap time has
  no cumulative time from that lap on; it is reported, not interpolated.
- **Finishing order is derived**, not official: laps completed, then cumulative
  time. The full-field final classification is not stored. Labelled as derived.
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
| 3 | `session_analysis.py` + tests against the real race | pending |
| 4 | API route + tests | pending |
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
