# Pit Wall 3.4.0 verification

## Completed in this environment

Unlike the 3.3.1 packaging run, this environment had the full dependency set
installed (`openai`, `f1-packets`, `sounddevice`, Ruff), so the whole suite ran
with no stubs and nothing deselected:

```text
95 automated tests passed
Ruff static checks passed
Python compileall passed
FastAPI startup and /api/health passed (reports version 3.4.0)
Rotating file log created and written
```

The repository contained 75 test functions before this release; 3.4 adds 20, all
in `tests/test_extraction_3_4.py`.

## New 3.4.0 coverage

1. Race 2 / Race 3 classify as `sprint`, not `race`.
2. The two-compound dry rule does not apply to a sprint.
3. All five Sprint Shootout session types classify as `qualifying`, including the
   truncated "One-Shot Sprint Shoot" label.
4. The label fallback classifier recognises the truncated shootout label.
5. Every known session type id resolves to a mode.
6. Reference deltas are signed correctly against the personal-best corner.
7. Corner instructions quote measured metres and km/h.
8. Corner instructions still work when no personal-best corner matched.
9. Noise-level deltas are suppressed rather than spoken as "0 metres".
10. Sector extraction requires a complete split.
11. Lap summaries carry sectors into `timing_fields`.
12. Sector backfill fills rows saved before the history packet arrived, and does
    not overwrite a complete split.
13. Backfill ignores incomplete splits.
14. `ended_at` and `result_position` are written on final classification and are
    not erased by a later write without classification data.
15. The damage signature covers engine, gearbox and DRS/ERS faults.
16. The damage relevance filter accepts power-unit and fault events.
17. Safety-car delta relevance tracks both race-control phase and delta.
18. Every one of the 18 proactive event types has specific fallback text.
19. Damage fallback text distinguishes power-unit from aero damage.
20. Participants populate `team_id`, and teammate detection matches the player's
    team.

## Verified end to end beyond unit tests

Two production paths were driven directly rather than only asserted in isolation.

**Quantified coaching.** A synthetic reference lap plus two slower laps produced:

```text
corner: cause='early brake' loss=0.678s brake_delta=-80.0m apex_delta=-30.0km/h
spoken: "At Corner 1 @ 640 m, brake 80 metres later while protecting apex speed."
```

Previously the same corner produced only "release the brake point a little
later", because the deltas were computed and then discarded.

**Sector persistence.** Driving the real order of events — lap completes, then
the session-history packet arrives with the split — persisted
`(s1, s2, s3) = (31000, 36000, 38000)`, summing exactly to the 105000 ms lap
time. Before 3.4 these columns were always written as zero.

## Existing behavior retained

The broader suite continues to cover deterministic and Monte Carlo strategy
planning, dry-compound legality, SC/VSC/red-flag distinctions, setup foundations
and learning, SQLite history and unsigned session UID persistence, racing-line
analysis, proactive radio cadence and relevance, wake phrase and L3/UDP Action
behavior, latency routing and the dashboard state rail, telemetry tool schemas,
and provider routing, failover, A/B evidence freezing and shakedown stages.

## Hardware boundary

The container cannot reproduce the PS5, controller, Windows microphone or
speakers. The safety-car delta sign convention in particular is taken from the
telemetry field's documented meaning and should be confirmed in one live
neutralised session: the call should fire while running too fast under a VSC and
stay silent when compliant. `PITWALL_PROACTIVE_SC_DELTA_MIN_S` tunes the
threshold without a code change.

No schema change was made in this release, so existing databases are reused
without migration. Adding columns would require a `PRAGMA user_version`
migration mechanism, which does not exist yet.

## Reproduction on Windows

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install_windows.ps1
.\.venv\Scripts\python.exe -m pytest -q
.\start_pitwall.bat
```

Then confirm `%USERPROFILE%\PitWallData\pitwall.log` is being written, and run
one session to check that sector times appear on stored laps and that corner
coaching quotes numbers.
