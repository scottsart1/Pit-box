# Your Pit Box 5.1.0 — setup scope and race radio

Version **5.1.0** is published for Windows and Android at
[yourpitbox.com](https://yourpitbox.com), with update notices live for both.

Setup Lab offers Minimum, Moderate and Radical changes. Minimum keeps the
current setup and caps the final per-setting adjustments. Moderate starts
halfway between the current setup and the circuit foundation. Radical starts
from the circuit foundation. All three then consider personal handling,
corner evidence and stored runs. Fuel load is preserved as a separate race
decision. Recommendations for another selected circuit exclude live setup and
handling from the wrong track.

The pace review reports recent clean laps and, where available, a same-compound
field median using matching lap numbers from the rivals' current stint. It
states that fuel, traffic and tyre age can differ and does not attribute every
pace deficit to setup. The broader garage recommendations are separated from
permitted front-wing changes during a pit stop.

The engineer's direct-answer route now requires a complete supported request.
Questions about future field stops, qualifications and comparisons reach the
reasoning model, which can combine existing validated tools. Rival predictions
can include all 23 opponents, distinguish observed completed stops from uncertain
future stops, and report missing or unsuitable evidence. No arbitrary generated
code is executed.

Frequent proactive checks use bounded snapshots without race histories or
trace curves. The UDP consumer yields on a time budget as well as a packet
count. Corner and racing-line calculations run outside the event loop. Health
polls copy only the fields they return. Scheduling delay and evaluation duration
are available in proactive diagnostics.

The connection watchdog also avoids graph copies. The live graph carries only
the plotted channels, with a corrected sample bound; full recordings retain
the motion and slip data. Repeated identity-only observations avoid rebuilding
participant identities. The assembler shares unchanged, privately owned sample
metadata and publishes read-only finalized rows without copying each row again.

Android now retains the last 20 network availability/loss events and reports
thermal status. Code inspection found no Bluetooth toggles, Wi-Fi disable calls
or process-wide network rebinding. This does not establish the cause of the
reported physical radio disconnections. No physical tablet is currently connected
to ADB; a successful emulator or localhost test cannot prove those drops fixed.

The proposed dashboard-agent work is documented separately in
[engineer-dashboard-agent-proposal.md](engineer-dashboard-agent-proposal.md).
It is not implemented in this release.

## Validation

- Initial local full run: 1,928 passed, two failed, two skipped. Both failures
  were supported radio requests affected by stricter routing. The corrected
  focused run passed all 30 tests.
- Setup UI exercised at 1280 × 800 and 800 × 1280 in an isolated source app.
- A synthetic 24-car, 60-lap snapshot benchmark measured 35.87 ms for the
  analysis snapshot versus 2.15 ms for the compact radio snapshot on this PC.
  This is a microbenchmark, not a tablet race or end-to-end voice measurement.
- Initial counted 60 Hz source tests failed: one final health timeout and one
  receive-count deficit (12,703 of 18,604). Profiling led to the identity,
  graph, watchdog and retained-metadata improvements above. Intermediate runs
  improved reception but still failed; their failures were not waived.
- Final local counted source run: **18,604 sent, received, parsed and written**
  in a 100-second, 20-car replay (about 186 datagrams/s). No queue drops, write
  errors or request timeouts. Health p95 0.421 s / max 2.984 s; state p95 0.469 s
  / max 1.328 s. All 19 rival laps persisted; shutdown and database integrity
  passed. This bounded test is not a full-race or physical-tablet endurance test.
- A separate parser/proactive integration test delivered a penalty call to a
  recording voice sink while 60 Hz frames continued. It exercised detection,
  queuing and delivery with retained rival history; it did not test a speaker.
- Final live stored-key model probe used both field-stop tools and correctly
  reported 22 likely / one unknown opponent, answered the cars-ahead question,
  and declined to invent a rival's radio message. Times were 9.86, 4.94 and
  4.52 seconds. Earlier probes hit route deadlines; this does not guarantee a
  response under all network/model conditions.
- Additional focused runs passed 135 tests (one platform skip), 44 networking
  and identity tests, and 28 assembler/setup/radio tests.

No personal library was used for synthetic telemetry. No credentials or private
recordings are included in this document.

## Release builds

Both workflows passed against source commit
`03263cd3fb7c2f08cf2be35926039ca21c001435`:

- [Windows run 36812193621](https://github.com/scottsart1/Pit-box/actions/runs/36812193621):
  **1,933 tests passed, one skipped**. The installed artifact passed startup,
  UDP, a 25-lap accelerated replay, strategy/classification persistence,
  restart, uninstall and retained-data checks. Its separate 100-second,
  20-car, 60 Hz run received, parsed and recorded all **18,604** datagrams,
  with zero queue drops, write errors or polling timeouts. Health p95 was
  0.453 s (max 1.954 s); state p95 was 0.281 s (max 0.531 s).
- [Android run 36812188231](https://github.com/scottsart1/Pit-box/actions/runs/36812188231):
  **1,663 shared engine/bridge tests passed**, plus interface, browser and
  Worker checks. Android 16 emulator startup, background UDP reception,
  restart, transfer management, fullscreen/keyboard and SQLite lifecycle
  checks passed. No unclosed SQLite warnings were found. This runtime run
  uses the isolated debug package, not a physical production installation.
- The downloaded Windows frozen runtime also passed an isolated local
  four-lap replay, persistence and restart check. Transfer TLS/QR identity and
  the saved usage-reporting decline survived restart. Synthetic data went to
  a new temporary directory, without installing over the user's app.
- All 28 Windows static assets match the tested source after normalizing
  Windows checkout line endings. The signed Android APK passes ZIP integrity,
  package/version and alignment checks; all 28 static assets and 82 embedded
  Python modules match the tested source.
- Final website/publication checks: **87 Python tests and 87 Node tests passed**.
  The website was visually checked at desktop and phone widths, with no
  horizontal overflow. Setup Lab was checked in tablet portrait and landscape.

## Artifacts

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| Windows `PitWall-Setup.exe` | 34,898,407 | `f39fab39ba1b066fbeeea47c847b94e1fc38dd5154818cb6e81dd05d91f1d143` |
| Android `YourPitBox-5.1.0-android.30.apk` | 73,021,756 | `e3f2c24ffa23877c861b706428d3f5537913ecc35faf8aced932dd135118cb14` |

Android is the production `com.yourpitbox.app` package, versionCode **30**,
signed with the existing production certificate:
`20c2751e5c0ede43a2442331336b990433e53d9e512f3f1bca3e8d5bee4c6983`.
Windows remains unsigned, as disclosed on the download page.

The tablet advertised a wireless-debug endpoint but connection attempts failed.
No physical tablet upgrade or Wi-Fi/Bluetooth endurance test is claimed for
this release. The dashboard-agent proposal remains a second phase.

## Production publication — 1 October 2026

- Website/artifact metadata commit: `ebe6836`.
- Pages deployment: `https://06e34161.pitwall-2k7.pages.dev`.
- Activation/download Worker version: `7eb18444-6ff7-4c59-b599-6b5d97bb12f6`.
  The Android route now selects the signed revision 30 APK.
- Both complete public downloads matched the artifact sizes and SHA-256
  hashes above. The Windows endpoint still reports that no activation code
  is needed.
- Eighteen public website routes matched the built files, permitting only
  Cloudflare's email-protection transformation and text line endings.
- Both immutable release notices were published only after the download and
  website checks. The actual update-service client passed six production
  checks: Windows and Android at versions 4.14.0, 5.0.0 and 5.1.0. Older
  versions offer 5.1.0; current versions report no newer release. Both
  manifests contain the verified artifact hashes and sizes.

This publication does not claim an upgrade of either of the user's installed
devices. The production files are available for their normal manual update.
