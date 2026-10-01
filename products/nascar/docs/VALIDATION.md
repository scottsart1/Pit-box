# NASCAR development-build validation

2026-10-01. These results describe bounded tests, not a full NASCAR 26 race.

## Completed

- 71 NASCAR tests pass: domain math, independent green/yellow learning, pit access,
  overtime, lap deficits, field uncertainty, stale evidence, CSV validation,
  session persistence, replay isolation, authentication, credentials, bounded
  high-rate ingestion and read-only model tools.
- Final review added coverage for continuing fuel observations after flag evidence
  expires, pit entry until an explicit exit, fresh low-fuel alerts with unconfirmed
  race control, lapped-player pit targets, saved garage control choices in spoken
  reviews, malformed scenarios, ragged CSV input and multiple network invitations.
- Rounded whole-percent fuel observations retain zero-change laps: alternating
  0/1% caution laps learn 0.5%, rather than the former incorrect 1%. All-zero
  readings cannot establish free fuel. Disabled overtime cannot be reported as
  an overtime attempt when the configured distance has been reached.
- The unchanged F1 application suite passes: 1,613 tests. No F1 runtime, website,
  installer identity, Android app ID or update endpoint was modified.
- A paced one-minute API test accepted 3,600 / 3,600 observations at 60 Hz with
  40 opponent records. No request failures. Ingest p95 8.31 ms; dashboard p95
  8.70 ms; local field-brief p95 4.56 ms. A proactive urgent fuel call was generated.
  This is localhost JSON ingestion, not evidence of NASCAR 26 telemetry export
  or real Wi-Fi packet reception.
- Frozen Windows executable: launch, static assets, fuel calculation, capture of
  an explicitly created synthetic Windows HUD, actual Windows OCR recognition,
  clean shutdown and restart persistence pass. Reopened observations remain stale.
- Actual OpenAI requests: field pit-stop question uses `field_strategy` and
  reports unknown fuel/intent; a caution plus saving question uses `fuel_scenario`;
  a radical handling request uses `handling_review` and discusses a full baseline
  comparison. Test keys were read into memory only, never saved into this product.
- Browser journeys: sample race, create weekend, manual observations, closed-pit
  display, scenario calculation and radical setup review. Desktop, 412-pixel
  phone and 1024-pixel tablet breakpoints checked; no page-wide horizontal overflow
  or browser console errors. Mobile navigation corrected during visual QA.
- CSV file-picker import and downloaded JSON export checked in the browser.
  Six imported fixture laps retained their two stints; the excluded caution lap
  did not affect clean pace. Continuing the latest imported stint after resume
  is covered by a regression test.
- Android debug and release variants compile. Separate QA app installed and
  activity launched on the connected Samsung tablet.
- The Android release APK has a verified publisher signature and package
  `com.yourpitbox.nascar`, version 0.1.0 / code 1. The independent Windows
  installer completed successfully; the installed executable passed the same
  launch, calculation, actual OCR and restart checks.
- [Independent cloud build run](https://github.com/scottsart1/Pit-box/actions/runs/36831831498)
  passed for both Windows and Android. Windows also ran its tests and frozen
  OCR smoke check. The first Android job requested the removed legacy SDK
  `tools` package; using `platform-tools` fixed the build environment.

## Open release gates

- **Real game access:** no NASCAR 26 installation was found on the development
  PC. PS Remote Play is installed; this does not establish ownership or access
  to NASCAR 26. Platform and game access remain unconfirmed.
- **Actual game observation:** calibrate and record a full NASCAR 26 session,
  including fuel display units, tire display semantics, race-control visibility,
  stage transitions, pit service and overtime. OCR of synthetic text does not
  prove correct capture of the game's DirectX/Remote Play surface or HUD fonts.
- **Tablet visual/voice checks:** the tablet is behind a secure lock screen.
  Installation/launch are confirmed, but pairing, layout, speech recognition,
  TTS completion and human audibility need an unlocked-device test.
- No collision spotter, native NASCAR SDK, hidden opponent fuel, automatic setup
  application or verified game-menu control is claimed.

This is a working development candidate, not a production release. Installer
and APK distribution must retain that status until the real-session gates pass.
