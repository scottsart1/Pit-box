# Pit Wall 3.4.1 verification

## Independent review result

The 3.4.0 extraction work was reviewed at packet-handler, state, persistence,
strategy-rule and proactive-radio boundaries. The corrected build contains 97
Python test functions and collects 101 cases.

## Reproduced in this environment

The package mirror did not provide `openai`, `f1-packets`, `sounddevice` or Ruff.
Temporary import-only stubs outside the release tree were used to collect and run
code that does not depend on those wheels. No stub is included in this build.

```text
101 test cases collected
100 tests passed
1 OpenAI-SDK serialization test deselected
Python compileall passed
Python AST parse passed
Dashboard JavaScript syntax passed with Node.js
FastAPI import and /api/health smoke check passed (version 3.4.1)
Rotating file log creation and write passed
```

The missing SDK test must be rerun by `install_windows.ps1`, which installs the
real dependency set before invoking pytest.

## Defects corrected after reviewing 3.4.0

1. **Sprint mapping:** Race 2 and Race 3 were hardcoded as Sprint. The game uses
   race IDs as weekend sequence slots, so that mapping could treat the Grand Prix
   as a Sprint and waive the dry two-compound rule. Classification now uses
   `weekend_structure`: Race (15) is Sprint only when a later Race 2/3 slot is in
   the sequence; later race slots remain Grand Prix. Unknown sequences default to
   Grand Prix rules.
2. **Sector backfill wiring:** the database backfill method existed, but the
   session-history packet did not call it. The packet handler now backfills the
   player lap immediately, including the last lap of a session.
3. **Finish timestamp drift:** every periodic upsert after classification replaced
   `ended_at` with a newer time. The first non-null finish time is now preserved.
4. **Final-result durability:** final classification now invokes persistence in
   the same packet cycle; the watchdog remains a secondary safety net.
5. **Spectator sentinel:** `player_car_index=255` could index 24-car packet arrays.
   Player-only handlers now validate the index and safely ignore spectator-only
   packets.
6. **Safety-car false alert:** a positive configured delta threshold could treat
   the default zero value as a breach before Lap Data arrived. Calls now require a
   valid delta sample.
7. **Version and test-count drift:** the dashboard still displayed 3.3.1, and the
   documentation confused test functions with parametrized cases. Version labels
   and counts now match the source.

## New and retained regression coverage

`tests/test_extraction_3_4.py` contains 26 tests covering:

- weekend-structure Sprint/Grand Prix classification and safe fallback;
- Sprint Shootout classification and dry-compound behavior;
- quantified corner coaching and noise suppression;
- complete sector extraction and packet-driven database backfill;
- final-result persistence and immutable `ended_at`;
- engine, gearbox, ERS and DRS damage calls;
- safety-car delta phase, threshold and validity;
- all proactive fallback text paths;
- team/teammate extraction and spectator sentinel handling.

The broader suite retains strategy, setup, SQLite uint64 session IDs, racing-line,
wake/PTT, latency, UDP parsing, provider routing, timeout, truncation, failover,
A/B protection and shakedown coverage.

## Hardware and live-service boundary

This environment cannot reproduce a PS5, controller, Windows audio devices, or
real OpenAI/DeepSeek credentials. On the target Windows machine run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install_windows.ps1
.\.venv\Scripts\python.exe -m pytest -q
.\start_pitwall.bat
```

Then use **Test providers**, confirm live UDP packets, complete one Sprint weekend
and one normal race weekend, and verify stored sector splits after the final lap.
