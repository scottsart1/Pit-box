# NASCAR product delivery

Independent package: `products/nascar`. No imports from the F1 engine, shared
settings, update channel, application ID, installer identity or history database.

## Product scope

- Race room, stage and fuel strategy, pit-service comparisons, handling workshop,
  long-run analysis, evidence-aware conversational crew chief and local radio.
- Windows app and a separate Android companion. Browser access on the local PC.
- NASCAR 26 has no external data API confirmed by the published game documentation
  checked on 2026-10-01. Do not label the adapter contract as a native game feed.
- Local Windows OCR with user-calibrated HUD regions is the practical observation
  path. Manual observations and CSV lap imports also work. A replay is explicitly
  synthetic and isolated from driver sessions.
- Critical calls use local calculations; cloud reasoning is on demand. No model
  executes arbitrary code or controls the game. Missing opponent fuel, eligibility
  or geometry must stay unknown.

## Candidate status — 2026-10-01

Version 0.1.0 is a working development candidate. The Windows installer and
separate Android application build successfully. Local domain/API tests, browser
journeys, actual provider tool calls and a frozen Windows capture/OCR smoke test
have passed. See [VALIDATION.md](VALIDATION.md) for results and their limits.

The NASCAR build workflow produces candidate artifacts only. It cannot publish
to the F1 download or update endpoints. The code, installer identity, Android
package, local database, credentials and companion certificate are independent.

## Release gates

1. Domain tests: green/yellow fuel, pit closure, overtime, lap deficits, missing
   evidence, session resets, replay isolation, setup scope and long-run analysis.
2. API/security/persistence tests; bounded high-rate ingest and concurrent radio.
3. Real browser journeys on desktop and tablet layouts.
4. Windows frozen build and separate Android build, launch and voice checks.
5. Actual NASCAR 26 HUD calibration and a race recording on a licensed game.
   This needs game access; a synthetic fixture cannot satisfy it.

Gates 1–3 pass locally. Gate 4 passes for compilation, frozen Windows execution
and Android installation; physical tablet pairing and voice checks are pending
an unlocked device. Gate 5 requires access to the target game and is open.

No production release or verified native NASCAR 26 integration is claimed.
The next integration test must establish the actual HUD, capture route and game
settings before defining supported features for that platform.
