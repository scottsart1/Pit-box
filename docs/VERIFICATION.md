# Pit Wall 3.5.1 verification

## Completed in this environment

The full dependency set (`openai`, `f1-packets`, `sounddevice`, Ruff, Node.js)
was installed, so the whole suite ran with nothing stubbed or deselected.

```text
144 automated tests passed
Ruff static checks passed
Python compileall passed
FastAPI startup and /api/health passed (version 3.5.1)
/overlay, /api/export/laps.csv, /api/export/session.json served (200)
/api/debrief returns 404 with no active session (correct)
Dashboard and overlay JavaScript passed node --check
```

The tool surface grew from 33 to 42 tools.

## Post-review corrections

An independent review of the 3.5 feature branch found nine defects, all now
fixed and covered by regression tests:

1. **Rival gap direction reversed.** `gap_to_player_s` is negative for a car
   ahead and positive for a car behind; the rival-pace and rival-prediction
   detectors had the sign inverted, so they watched the wrong car.
2. **Rival-pace anchor reset every tick.** The gap-history sample was
   overwritten on every poll, so the comparison window never reached 8s and the
   call could never fire. The anchor is now held until the window elapses.
3. **Lobby ready count.** `readyStatus` 2 is spectating, not ready; only 1 now
   counts as ready, and spectators are reported separately.
4. **Blown/seized engine.** These flags now raise an immediate `engine_failure`
   call rather than being ignored.
5. **Cross-component wear suppression.** A single shared alert band hid a second
   PU component crossing a threshold; bands are now tracked per component.
6. **Stale live-delta reference.** The reference lap is cleared on a session
   change so a new session never compares against the old track's best.
7. **Progress trend truncation.** The query returned the oldest N sessions;
   it now returns the most recent N, presented oldest-to-newest.
8. **False P0 race-start.** The start call now requires a real grid slot
   (grid_position > 0) instead of emitting "P0" before the packet arrives.
9. **Custom persona ordering.** User persona text can no longer be the final
   instruction; a safety anchor re-asserting the non-negotiable rules is always
   appended last.

## What 3.5 adds and how it was checked

Each feature is driven through its deterministic layer in
`tests/test_features_3_5.py` (43 tests) so the numbers are pinned independently
of any language model.

**Analytics (items 12–14).** Sector bests compose the theoretical best from the
independent per-sector minima and ignore pre-3.4 zero-sector rows; the progress
trend orders sessions and signs improvement; setup correlation ranks stored runs
by score.

**Strategy depth (items 6–9).** The cold-tyre penalty applies only to fresh
stints (verified against a continuing stint that shows no out-lap spike); the
rival predictor flags a car behind on dying tyres and leaves a car ahead on
fresh hards unflagged; pace mode recommends saving on a fuel deficit and pushing
on a surplus; the championship view scores plans by the F1 points of their
projected finish.

**Proactive & real-time (items 1–5, 15).** The live-delta reference interpolates
time-into-lap and clamps at the lap boundaries; race-start fires once on green
and never in qualifying; component wear escalates by 10-point band; energy-low
fires only while attacking on a low battery; rival-pace flags a closing car
behind; the safety-car restart fires on the transition into an ending phase.
Every new event type has specific fallback text.

**Surfaces & personalisation (items 16–20).** `compose_persona` folds a call
sign, custom persona and verbosity into the base persona while keeping the
safety-critical instructions; verbosity is validated; the CSV/JSON export routes
carry attachment headers; the overlay is transparent by default and uses
`wss://` under HTTPS.

**Debrief & bigger bets (items 11, 25, 26).** The debrief aggregates pace,
consistency, spread, compounds and top losses; the collision event decodes
severity and player involvement; the lobby handler counts players and ready
state; practice focus returns heuristic guidance only in practice sessions.

## Deferred

Item 24 (a local offline STT/TTS + wake-word pipeline) is not implemented: it
requires downloading speech models and evaluating real microphone/speaker
latency, which this build environment cannot provide. The cloud pipeline and the
provider router are unchanged.

## Hardware and live-service boundary

This environment cannot reproduce a PS5, controller, Windows audio devices, or
real OpenAI/DeepSeek credentials. On the target machine, confirm live UDP
packets, run the dashboard provider shakedown, and complete one race and one
qualifying session. The live delta, race-start, restart and energy calls in
particular should be observed once against real telemetry.

## Reproduction on Windows

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install_windows.ps1
.\.venv\Scripts\python.exe -m pytest -q
.\start_pitwall.bat
```
