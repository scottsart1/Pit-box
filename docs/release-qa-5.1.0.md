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
reported physical radio disconnections. A subsequent physical-tablet test
reproduced a combined Wi-Fi/Bluetooth failure with Bluetooth audio connected.
**5.1.0 does not resolve that observed condition.** See the physical-device
results below; emulator and localhost passes do not override this failure.

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

The physical tablet was subsequently paired and upgraded from production
5.0 revision 29 to the signed 5.1.0 revision 30 APK. The dashboard-agent
proposal remains a second phase.

## Physical-device follow-up — 1 October 2026

Target: Samsung Galaxy Tab S11 Ultra (SM-X930), Android 16. The production
upgrade preserved the existing session and lap counts and the canonical
session-inventory hash. Synthetic telemetry was sent only to the separately
installed `com.yourpitbox.app.qa` package, signed with the existing certificate.
The preexisting QA library was retained. After testing, the temporary QA speech
credential was removed, the QA service stopped, and the normal production app
reopened. Its saved credential remained configured and the production library
inventory was reverified unchanged.

Each replay emitted 18,604 datagrams over 100 seconds: 20 cars, nominal 60 Hz
motion/lap/telemetry packets, plus slower supporting packets. Speech used the
native Android audio adapter and the configured live speech service.

| Run | Received / sent | Wireless result | Native speech completion |
| --- | ---: | --- | ---: |
| Initial run, Bluetooth audio connected | 12,574 / 18,604 | Wi-Fi lost; Bluetooth service crashed | 3 calls before interruption |
| App in background, Bluetooth audio disconnected | 18,008 / 18,604 | No new service crash; packet loss remains | 3 calls |
| App visible, Bluetooth audio disconnected | 18,604 / 18,604 | No new service crash in this bounded run | 3 calls |

The initial failure occurred about 68 seconds into the replay. Android recorded
a Wi-Fi disconnect at 00:51:49.870 EDT, a Bluetooth controller hardware error
at 00:51:54.340, and a Bluetooth service crash at 00:51:54.875. The crash counter
increased from four to five. Four earlier Wi-Fi disconnects were also followed
roughly four to five seconds later by Bluetooth controller errors and service
crashes. The new Wi-Fi failure occurred at reported RSSI -42 dBm; Android
reported thermal status 0. These observations establish the system-level
symptom, not which component triggered it.

The app process survived and its API recovered after Wi-Fi reconnected. All
12,574 received datagrams were parsed and written, with no application queue
drops or write errors. Before the disconnect, successful health/state requests
had p95 latencies of 0.047/0.078 seconds. The failed run is not a reception or
endurance pass.

The visible-app repeat received and wrote every emitted datagram, with no
request timeouts or application queue/write errors; health/state p95 latency
was 0.078/0.063 seconds. The background repeat lost 596 datagrams before the
app receiver and had a longest successful health request of 4.953 seconds.
It is not a 60 Hz reception pass despite the absence of a new wireless crash.

Android reported Wi-Fi operation mode 0 in the background and mode 4
(low latency) with the app visible. Android 14 and later also map the older
high-performance lock to low-latency mode, so the explicit mode selection in
5.1 did not newly enable it on this tablet. See the
[Android Wi-Fi lock documentation](https://developer.android.com/reference/android/net/wifi/WifiManager#WIFI_MODE_FULL_LOW_LATENCY).
The connected Bluetooth audio device did not reconnect after the first
failure, so these repeats cannot isolate the Wi-Fi lock as a cause or fix.

Speech completion means the native playback operation returned successfully;
human confirmation of audibility is still pending. The next comparison needs
the same Bluetooth audio device connected in both app visibility states.
USB debugging, when available, would preserve access to failure logs while
Wi-Fi is down. Raw device logs, network identifiers and credentials remain
outside the repository.

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

The Android production upgrade was subsequently verified as described above.
The Windows publication does not claim an upgrade of the installed desktop
app; its production installer is available for the normal manual update.
