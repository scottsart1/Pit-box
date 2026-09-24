# Your Pit Box 4.13.2 — Flag context across lap boundaries

Windows and Android published and verified 24 September 2026, 00:07 UTC
(23 September in the release machine's local time).
Public downloads: <https://yourpitbox.com/#download>.

## Scope

Application source: `57f671a5cad8612e4c7bef0ad13adb328df579af`.

The review of 4.13.1 found that a rival's current yellow or red flag was lost
when lap accumulation reset at the start line. If the next status packet
cleared the flag, the new lap was incorrectly catalogued as clean. The
player's state retained that flag, so the two paths still disagreed.

The archive now tracks each car's current flag separately from its accumulated
lap context. It carries an active flag into the next lap, including when
status arrives before the first lap-data packet. A later clear status lets
subsequent clean laps remain clean. Session reset clears the tracked state;
other cars and the existing global safety-car/neutralisation rules are
unchanged. Four regression cases failed before the fix and passed afterward.

This release includes the 4.13.1 comparison threading, quality/circuit display,
reference-notice reset and migration 4904 fixes. Android revision 26 supersedes
the unpublished revisions 24 and 25. Public Android updates retain the
existing signing identity.

## Regression and packaged checks

- Application: 1,586 passed in two disjoint file groups (834 and 752).
- Final distribution source: 311 passed, one skipped.
- Final Worker, website and driver-dashboard source: 110 Node tests passed.
- Website build/preflight and Worker deployment dry run passed.
- [Windows CI](https://github.com/scottsart1/Pit-box/actions/runs/35934817978)
  passed 1,897 tests with one skipped. Its installed candidate passed startup,
  a 25-lap synthetic race, persisted classification, restart, SQLite integrity,
  graceful shutdown and uninstall preserving recorded data. A separate
  100-second, 20-car test received, parsed and captured all 18,604 packets with
  zero rejects, queue drops or write errors. State/health P95 were 0.312/0.422 s
  with no endpoint timeouts. This CI installer is an independent build from
  the same source; the public installer was built and checked locally.
- [Android CI](https://github.com/scottsart1/Pit-box/actions/runs/35934811363)
  passed both jobs, including engine tests, real UDP/transfer integration,
  five DOM smoke tests, dashboard tests, Chromium layout/touch checks and
  tablet-emulator startup, UDP, restart and background reception. Its isolated
  debug variant on API 36 parsed and wrote all 360 test packets with zero
  rejects, capture drops or write errors. Transfer management/UI and identity
  persistence passed; no unclosed SQLite warnings were found. A crash-buffer
  entry belonged to the UI-automation shell process, not the application.

The Android public APK is non-debug `com.yourpitbox.app`, versionCode 26,
versionName `4.13.2-android.26`, for arm64-v8a and x86_64. Its 27 static assets
and 82 compiled Python modules match the application source commit. ZIP CRC,
v2/v3 signatures, alignment and payload preservation after signing passed.
The signing certificate remains
`20c2751e5c0ede43a2442331336b990433e53d9e512f3f1bca3e8d5bee4c6983`.

## Local Windows runtime check

The local installer built successfully with packaging preflight passing. Two
20x accelerated replays did not reach final classification. Instrumented
diagnostics showed missing frames before the application receiver queue,
including 1,123 confirmed gaps in the regularly emitted motion stream, while
the parser and archive reported no rejects, queue drops or write errors.
These accelerated local runs are failures, not passing release evidence.
The controlled 2x replay passed with the same correctness assertions. The
frozen app reported 4.13.2/schema 4904 and served all 27 assets matching
source. A four-lap, 20-car race produced the exact emitted final result and
90 persisted laps (four player, 86 rival), all correctly unflagged. Library
and Field quality/circuit details, eight comparisons against four references,
comparison traces and solo analysis passed. A separate real-UDP boundary
sequence persisted rival laps 1/2 as flagged and lap 3 as clean. Restart
preserved the session/trace; SQLite integrity and both clean shutdowns passed.
The last sampled receiver queue had zero drops (high-water 111 of 2048),
zero rejected datagrams and no gaps across 3,533 motion packets. The failed
20x runs remain evidence of this local machine's accelerated-load limit.

## Publication

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `PitWall-Setup.exe` | 34,798,288 | `e95f8407777ba74e23a9ed3eff7cc95750310ec1d3ab9cf6301b0a0c8ad1121c` |
| `YourPitBox-4.13.2-android.26.apk` | 72,812,683 | `82e4e66bdec32f4934181d84ff759a801cd9f6e50f51177224e9ee22605dd802` |

Website and Android Worker pointer: `61425e8b55d325bb5336f8c52c63774007b5466d`.
Worker version: `d43d0839-0764-4eae-a860-1e2db8a45c07`.
Production Pages: <https://10f82794.pitwall-2k7.pages.dev>.

Full-range anonymous reads of `/installer` and `/android` returned 206 and
matched every byte, size, content type and filename above without incrementing
download-start counters. The Windows archive is
`releases/windows/4.13.2/PitWall-Setup-e95f8407.exe`; the Android object retains
its versioned filename. Earlier release objects remain intact.

The production domain advertises both versions and exact checksums. Its
download JavaScript and CSS match the built source byte for byte. Index and
guide HTML match after decoding Cloudflare's existing email-protection
transformation and following the guide's canonical URL redirect.

Both update records were published last through `publish_release.ps1`, after
its own full public-file and website verification. Real update-service calls
for both platforms report 4.13.2 available to 4.12.4/4.13.1 and no update for
4.13.2. Installation remains manual; no messages or emails were sent.

Private sibling `release-4.13.2` retains the build, signatures, CI diagnostics,
frozen-app evidence, public download readbacks and update-service receipts.

## Verification boundaries

The release checks use isolated synthetic data. No real racing history is
needed or uploaded. Windows remains unsigned, and Android retains its
documented 4 KB memory-page restriction. CI is not full-race endurance or a
physical console/Wi-Fi/Bluetooth/audio check. The Windows runner lacked a
usable private LAN; paired history copy was not tested. The final signed
Android APK was statically verified; emulator runtime checks used the isolated
debug variant. The user's installed applications and history were not changed.
