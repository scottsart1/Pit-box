# Your Pit Box 4.12.3 — Analysis touch-layout hotfix

Published and verified 20 September 2026, 04:34 UTC.
Both public downloads: <https://yourpitbox.com/#download>.

## Physical-tablet findings

After the user paused, the existing separate QA package on the Samsung SM-X930
was upgraded to 4.12.2 revision 21 without uninstalling or clearing data. The
regular app and its history were not changed. QA used its own database, UDP
20787 and a separate HTTP listener; no game inputs or telemetry packets were
sent. Microphone listening remained off.

An existing synthetic QA recording (2,140 samples with positions and controls)
loaded from Review into Lap Lab without choosing a comparison. Its map, speed,
gear, throttle and brake populated. Play changed to Pause and advanced distance
from 386 to 3,363 metres; Pause and single-step also worked. The circular map was
labelled to the user as synthetic test data, not their actual driving.

That visible test found two additional interaction bugs: swiping on the canvas
did not scroll, and the sticky lap selectors could cover Play and the gauges.
These findings prevented a clean tablet-layout pass and triggered this release.

QA playback was paused. An automated shutdown/forward-removal/normal-app restore
command was blocked before execution; it was not retried or bypassed. The user
was told to switch back manually. No further tablet controls were sent while
waiting for confirmation that the tablet remained available.

## Fix and regression checks

Source: `5746d6c01acae197c6c5e473fae2a5c3f25084bf`.

- Lap selectors now remain in normal document flow at every screen width.
- Analysis canvases allow native touch scrolling and zoom gestures.
- Trace seeking occurs on a click/tap, not pointer-down, so starting a swipe
  neither seeks nor changes the selected playback sample.
- All 4.12.2 standalone-playback, missing-data and stale-response fixes remain.
  No coaching-progress, privacy, voice or telemetry behavior was changed.

The new real-browser regression harness passed 54 checks at six viewports:
390x844, 844x390, 800x1280, 1024x768, 1680x1040 and 1440x900. It checks header
movement, unobscured controls, touch Play/Pause with cursor advancement, actual
touch swipes over all three canvases, unchanged cursor while swiping, and trace
tap-to-seek. Running the same harness against 4.12.2 reproduces the regressions.
Phone and tablet screenshots were inspected. Browser QA used an isolated Edge
profile because the normal browser connection was unavailable.

Existing Analysis DOM checks, targeted Python UI/Android checks, onboarding and
Worker checks also passed. Android pre-build checks included 1,594 engine/bridge
tests, 14 Analysis DOM cases, 23 dashboard cases, all 54 new gesture/layout checks
and 87 Worker checks.

## Release verification

CI: [Windows](https://github.com/scottsart1/Pit-box/actions/runs/35488613574),
[Android](https://github.com/scottsart1/Pit-box/actions/runs/35488615597).

Android CI passed. Its isolated API 36 x86_64 tablet emulator validated startup,
exact engine version, UDP parsing/background reception, stationary-trace
stability, transfer TLS/QR controls and listener/process restart. Its crash log
was empty and there were zero unclosed SQLite warnings. Emulator runtime testing
used the debug build from the same source, not an installed signed release APK.

Both release APKs are non-debuggable and signed with the existing certificate
SHA-256 `20c2751e5c0ede43a2442331336b990433e53d9e512f3f1bca3e8d5bee4c6983`.
Signature verification and alignment passed. Public and QA packages contain 79
identical engine/native/static entries; the shipped frontend matches source
with line endings normalized. Public package: `com.yourpitbox.app`, revision 22.
Android's disclosed 4 KB page restriction remains unchanged.

Windows CI passed 1,862 tests with one skipped. Installed startup, real UDP,
25-lap simulated race, persisted classification, restart, SQLite integrity and
data-preserving uninstall passed. This is accelerated synthetic testing, not
full-race endurance on the user's device. The runner had no usable private LAN
for a paired history transfer. A separate 100-second full-field stress test
sent, received, parsed and wrote all 18,604 packets with zero rejects, drops,
write errors or sampled timeouts. Health P95 was 0.468 s; state P95 was 0.422 s.

On the laptop, the exact frozen release passed isolated startup, shipped
defaults, update checks, eight shipped-asset comparisons, real UDP parsing and
graceful shutdown (exit 0). The regular installation was untouched. All 54 real
browser gesture/layout checks passed again against its frozen static assets.
The approved unsigned-installer status is unchanged; no protection was disabled.

Website/publication checks passed: 85 Python and 87 Worker Node tests.
The final 4.12.3 physical-tablet retest has not yet been performed.

## Publication

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `PitWall-Setup.exe` | 34,734,891 | `230d98419bfef5843a384f0e2035bd71f1734e8042ed03eb3d37bec808ae2ace` |
| `YourPitBox-4.12.3-android.22.apk` | 72,788,107 | `4f01a1feb85c232ac50c0057d1577efd4a183d44c24d02027bcbcbfc6dc5fbee` |

Website/Worker source: `feb5d89c9acd5e18f519f472caea065c07e30853`.
Worker version: `9de20c9e-a27a-4363-9485-8fc18fdd55f2`.
Production Pages: <https://562bb6dd.pitwall-2k7.pages.dev>.
The existing bindings, retention schedule and private owner controls remain.
No migration, email automation or analytics-consent change was introduced.

R2 retains both the versioned APK and Windows backup
`releases/windows/4.12.3/PitWall-Setup-230d9841.exe`; prior releases remain.
Anonymous full-range reads verified every public byte, content type, size and
range support without incrementing download counters. Custom-domain checks
verified both versions/checksums, release copy, guide/static asset bytes and
Discord. Both update records were published last, after downloads and website
were ready. Actual update-service calls advertise an update for 4.12.1 and
4.12.2, and none for 4.12.3, on both platforms. Installation remains manual.

Private sibling folders `release-4.12.2/tablet-analysis` and `release-4.12.3`
retain screenshots, UI trees, build logs, signed packages and verification
reports. No user recording or private QA history is committed or uploaded.
