# Your Pit Box 4.12.4 — Analysis session-status follow-up

Published and verified 20 September 2026, 05:05 UTC.
Public Windows and Android downloads: <https://yourpitbox.com/#download>.

## Scope

Source: `147d0549396a6f073e058e022c3e37f30219b584`.

The user explicitly requested continuation of the physical-tablet checks after
the laptop was locked. The Samsung SM-X930 remained reachable through Android
debugging, so the laptop did not need to be unlocked. Only the separate QA app
was upgraded or controlled; the regular package and its history were untouched.

The signed 4.12.3 revision 22 QA build passed saved-session Review, standalone
map/trace/gauges, advancing playback, Play/Pause, previous/next, touch scrolling
over all three canvases, tap-to-seek, unobscured controls and loading a second
saved session. Solo analysis populated ten measured segments. The recordings
were pre-existing synthetic QA fixtures, not the user's actual racing history.

Switching sessions correctly cleared traces and gauges, reset distance to zero
and disabled playback, but left its old ready message visible. This release
adds a single neutral notice reset to the shared lap-reset function. It also
clears stale error/loading messages; late responses cannot restore old status.
All prior playback, missing-data and scrolling fixes are retained. No privacy,
voice, defaults, telemetry, coaching-progress or storage behavior was changed.

## Regression checks

Three expected failures reproduced the stale notices before the fix. All 16
Analysis DOM tests passed afterward, covering immediate and completed session
switches, empty selection, success/error states and late trace responses.
Release preparation passed 160 targeted Python and 87 Worker/browser Node tests.

CI: [Windows](https://github.com/scottsart1/Pit-box/actions/runs/35490014516),
[Android](https://github.com/scottsart1/Pit-box/actions/runs/35490015923).

Windows CI passed 1,862 tests with one skipped. Installed startup, accelerated
25-lap race, restart, SQLite integrity and data-preserving uninstall passed.
A separate 100-second, 20-car test sent/received/parsed/wrote all 18,604 packets
with zero rejects, drops, write errors or sampled timeouts. Health/state P95
were 0.313/0.328 s. This is not full-race endurance; the runner lacked a private
LAN for paired history transfer. The exact frozen executable also passed local
isolated startup, defaults, eight served-asset comparisons, updates, UDP and
graceful shutdown (exit 0). All 54 frozen-asset browser gesture checks passed.

Android CI passed 1,594 Python, 16 Analysis DOM, 23 dashboard, 54 touch-layout and
87 Worker checks. Its isolated debug APK on the API 36 x86_64 tablet emulator
passed startup/version, UDP/background reception, stationary trace stability,
transfer TLS/QR controls and restart. The crash buffer was empty, with zero
unclosed SQLite warnings. Public and QA release APK signatures/alignment passed;
they share 79 identical engine/native/static entries and match frontend source.
The existing signing certificate is preserved:
`20c2751e5c0ede43a2442331336b990433e53d9e512f3f1bca3e8d5bee4c6983`.

## Final signed-APK physical test

The signed `com.yourpitbox.app.qa` 4.12.4 revision 23 package was installed as a
data-preserving upgrade on the SM-X930. Its SHA-256 was
`91b3c068c62daaf92a1e25e57ef01102ec36ecb282a6e5bba15dc33664e9cafc`.
Its process log confirms isolated UDP 20787 and HTTP 43519. No microphone or
game inputs were used. All five existing QA sessions remained available.

- A Review row loaded standalone map, trace and gauges without a comparison.
- Play advanced from 1 to 76 to 1,204 m with changing speed and gear.
- Switching sessions while playing stopped playback, reset to 0 m, cleared all
  six gauges and disabled the controls. The corrected neutral notice appeared
  and remained stable. A second session's selected lap then loaded normally.
- Map swipes scrolled the lap selectors away. A trace swipe moved the page
  390 px without changing the paused 1 m cursor. A deliberate tap sought to
  2,908 m; Play remained unobscured and advanced to 2,993 m.
- Pause held at 3,286 m; next/previous stepped to 3,297 and back to 3,286 m.
- Screenshots confirmed the map/trace drawings and accessible controls. An
  independent read-only UI-tree audit matched these observations. The scoped
  process log contained no fatal exception or Python traceback.

QA was left paused. The regular tablet app/history and laptop installation were
not changed. The laptop remained locked throughout. This test covers saved
synthetic-session analysis, not a new physical full race or microphone test.

## Publication

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `PitWall-Setup.exe` | 34,733,855 | `7f8bfe652f0a13b94ffac66dbae209724f35fdc616cb13d1d3e9d069c1a2747d` |
| `YourPitBox-4.12.4-android.23.apk` | 72,788,107 | `98d10da68d45e5b3cdf7385f5e6d87c97176f03c7deb2e5944f948a9c57e1d1f` |

Website/Worker source: `f78120060a54d41fa78f1680075d919d3167fdab`.
Worker version: `5c88bb9a-38b1-4e38-a758-211a625c85b2`.
Production Pages: <https://53d0ef2c.pitwall-2k7.pages.dev>.
Anonymous full-range reads verified every public artifact byte, size, content
type and range support without incrementing download counters. Custom-domain
checks verified versions, hashes, release copy, guide, static bytes and Discord.
Update records were published last; real update-service calls report an update
for 4.12.2/4.12.3 and none for 4.12.4 on both platforms. Installation is manual.
The versioned Windows backup is
`releases/windows/4.12.4/PitWall-Setup-7f8bfe65.exe`; old releases remain intact.

The existing owner controls, download
counters, retention schedule and update-only notification behavior are retained.
The unsigned Windows status and Android 4 KB page restriction remain disclosed.
No security protection was disabled and no analytics-consent change was made.

Private sibling folders `release-4.12.3/tablet-analysis` and `release-4.12.4`
retain UI trees, screenshots, signed artifacts and test evidence. No private
recordings or copied history are committed or uploaded.
