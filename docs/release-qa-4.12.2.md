# Your Pit Box 4.12.2 — Analysis playback hotfix

Published and verified 20 September 2026, 03:26 UTC (19 September locally).
Both public downloads: <https://yourpitbox.com/#download>.

## Fix and reproduction

Opening a saved lap previously selected references but never requested the lap's
trace. Playback, map and instruments therefore remained empty until a comparison
was created. The review-row action also left solo analysis disabled. A separate
missing-file error referenced a nonexistent record attribute and returned 500;
partial recordings could produce non-JSON numeric summaries.

- Selecting a lap now loads standalone playback, controls and recorded geometry.
  A reference is optional; no reference/delta/coaching result is fabricated.
- Session/lap changes stop playback, clear old visuals and ignore late responses.
- Optional missing motion cannot discard readable controls. Missing files are
  reported as unavailable, and summaries exclude unknown samples.
- Map paths break at missing coordinates. Cursor markers neither jump gaps nor
  extrapolate outside coverage. Approximate distance controls work on sparse data.
- The 4.12.1 Strategy fix, privacy choices, existing settings and voice behavior
  are unchanged. No persistent-coaching feature is included in this hotfix.

Source: `90d3c72eeadb9f0ad7beae9cce201d523e1fb60e`.

## Local Analysis checks

The original laptop history was opened read-only and copied into an isolated QA
database; only selected trace files were copied. Keys/network preferences were
removed from the disposable copy. Microphone and raw capture were off. The final
preview used loopback HTTP 18014 and UDP 20794, not the tablet's live receiver.

An isolated real Edge browser reproduced the old code's failure: zero trace
requests, disabled playback/solo controls and unavailable speed despite recorded
positions and controls. With the fix, the same saved lap loaded the track map,
speed trace, gauges, moving playback cursor and ten solo-analysis segments.
Desktop/phone screenshots were inspected. A second saved lap produced both map
paths and a comparison while preserving its weather/coverage caveats. Switching
sessions cleared the old playback and gauges. No browser page errors occurred.

- 14 new DOM regressions passed, including late requests, invalid recorded laps,
  missing/partial geometry, incomplete metrics and sparse transport controls.
- 72 focused Analysis/backend regressions and 38 version/Android/cache checks
  passed locally. Existing DRIVE, usage, onboarding/tour and transfer DOM smokes,
  23 dashboard tests and 17 onboarding Node tests also passed.
- The normal browser connection was unavailable; testing used a separate browser
  profile, not the user's signed-in browser. The source previews (including the
  pre-fix backend) hung during shutdown after headless WebSocket disconnection.
  Only these verified, owned QA processes were terminated. This observation is
  not claimed fixed; the exact frozen Windows release shut down normally below.

## Build and package checks

CI: [Windows](https://github.com/scottsart1/Pit-box/actions/runs/35485719357),
[Android](https://github.com/scottsart1/Pit-box/actions/runs/35485720917).

- Windows: 1,861 tests passed, one skipped. Installed startup, real UDP, 25-lap
  simulated race, restart, SQLite integrity and data-preserving uninstall passed.
  Separate 100-second full-field test sent/received/parsed/wrote all 18,604 packets;
  zero rejects, queue drops, write errors or sampled timeouts. Health P95 0.500 s;
  state P95 0.360 s. The runner had no usable private LAN for paired TLS/QR copy.
- Exact frozen Windows executable on the laptop: isolated startup, shipped
  defaults, update checker, source asset parity (including workspaces.js), actual
  UDP parser fixture and clean shutdown passed. The installed app was unchanged.
- Android pre-build: 1,593 engine/bridge tests, DOM smokes, dashboard tests,
  20 real Chromium Strategy layout cases and Worker tests passed. Emulator
  startup, stationary/background UDP, transfer controls and restart passed;
  final logs had zero unclosed SQLite warnings. This is not a physical-race test.
- Public and isolated QA APKs are signed with the existing certificate SHA-256
  `20c2751e5c0ede43a2442331336b990433e53d9e512f3f1bca3e8d5bee4c6983`.
  Public package `com.yourpitbox.app`, revision 21, non-debuggable. All 79
  engine/native/static payload entries match between public and QA packages;
  shipped frontend source matches with line endings normalized.
- Website/publication preparation: 85 Python tests and 87 Node tests passed.

The physical SM-X930 was connected only for read-only identification/version
checks. It remained on 4.12.1 revision 20. No installation, app launch, UI input,
force-stop or data clearing was performed while the user was playing. A visible
physical-tablet Analysis test is deferred until the user is paused and ready.

## Publication

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `PitWall-Setup.exe` | 34,729,155 | `3e23b027ced28cc61b88cad34a35e2061b6e09e1f32b5f8482ae349d58f4cc62` |
| `YourPitBox-4.12.2-android.21.apk` | 72,788,107 | `5e3f4968311a7ef7739e34e56b175745a63a15c3813fd286bf7756d6606dc9a4` |

Android's disclosed 4 KB page limitation and the approved unsigned Windows
installer status are unchanged. No security protection was disabled.

Website/Worker source: `3a043e7473e38de65f7e8c426a952a313db29a1f`.
Worker version: `40a86f89-5be0-4979-b034-a4e1dbfbfa32`.
Production Pages: <https://3a0bc180.pitwall-2k7.pages.dev>.
Existing bindings, retention schedule and private owner controls remain.
No migration, email automation or analytics-consent change was introduced.

R2 retains the versioned APK and Windows backup
`releases/windows/4.12.2/PitWall-Setup-3e23b027.exe`; previous releases remain.
Anonymous full-range reads verified every public byte, content type, size and
range support without incrementing download counters. Custom-domain checks
verified release copy, checksums, guide, static assets and Discord. Both update
records were published last. Actual update-service checks report an update for
4.12.1 and none for 4.12.2 on both platforms. Installation remains manual.

Sibling folders `analysis-qa-4.12.2` and `release-4.12.2` retain private snapshots,
screenshots, signed artifacts, CI logs and verification JSON. None of the copied
user history was committed or uploaded. Approximately 6.4 GiB remains free on C:.
