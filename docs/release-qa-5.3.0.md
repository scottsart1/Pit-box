# Your Pit Box 5.3.0 — lap groups and evidence-aware notes

**Published and verified.** Windows 5.3.0 and Android 5.3.0 revision 32 are
available from [yourpitbox.com](https://yourpitbox.com/#download). Final CI,
artifact verification, physical-device acceptance, public download checks and
update-client readback passed. Checks and publication were recorded on
2 October 2026 US Eastern (3 October 2026 UTC).

## Behavior and evidence boundaries

- **Setup Lab → Open Test Engineer** and **Analysis → Test Engineer** open the
  same workspace. Automatic runs remain available. **Define my lap groups**
  adds named groups using exact laps, chronological ranges or suggestions for
  2–12 groups. Suggestions prioritize recorded events and lap order, without
  clustering laps by pace. Group cards collapse to keep longer sessions usable.
- Custom groups retain canonical lap identities. Repeated lap numbers after a
  flashback are distinguished by timeline. Foreign or overlapping selections
  are rejected; new laps remain unassigned until added. Suggested group IDs
  follow membership so an altered suggestion cannot silently inherit another
  group's notes. Raw recordings are not regrouped or overwritten.
- **Stint comparison — any compound** permits comparisons such as medium
  versus hard tyres. It reports selected clean median pace, condition changes,
  exclusions and saved notes. At least three clean laps in each group are
  required for a verdict. Sector differences use a common complete-sector
  cohort within each group, with its count shown separately from total clean
  laps. The descriptive difference does not isolate a tyre or setup effect.
- **Matched setup test** retains unique condition-matched pairs. Each arm
  needs one known recorded setup; mixed-setup groups cannot produce a setup
  verdict. Unknown compound/setup/weather, missing evidence, incomplete
  telemetry, within-lap temperature changes exceeding the matching bound and
  compromised laps are rejected. Missing sectors remain unavailable.
- Measured air/track temperature ranges, starting fuel, tyre age, weather and
  available traffic evidence accompany comparisons. Gap coverage, detected
  close following and a reported obstruction are separate concepts. A recorded
  gap is not proof of interference; no proximity flag does not establish that
  traffic had no effect. Group-wide and clean-cohort conditions are distinct.
- Driver reports and engineer interpretations have explicit provenance and
  lap/group scope. **Lap context and driver reports** supports creation,
  correction and removal. Only an explicit pace-exclusion choice adds a
  reported exclusion. Correcting or removing the note updates later analysis
  without altering the measured telemetry or unrelated exclusions.
- The engineer can save and retrieve this context through bounded validated
  tools. Ambiguous references require clarification. A note about an unfinished
  lap remains pending and resolves only to that lap and timeline. Spoken
  references remain attached to the lap where the request began, including when
  transcription or a provider response crosses the timing line.
- An isolated slow lap remains inside its run. The additional transient rule
  requires consecutive clean neighbours returning to similar pace, stable
  measured context and a slowdown exceeding the larger of 2.5 seconds or 3%.
  Neighbouring pace must agree within the larger of one second or 1%. The
  reason remains visible; the rule does not infer a cooldown, driving mistake
  or obstruction. Missing context or a sustained change does not satisfy this
  isolated-slow-lap rule. Existing quality and conservative outlier filters
  continue to apply.
- Mixed groups screen pace within recorded run/weather cohorts so a slower
  compound is not discarded merely for having a different baseline. A single
  pace trend is withheld across different runs, tyre sets, setups or timelines.
- Pit-entry debriefs use saved note exclusions and include their attributed
  context in the persisted payload. A reused run serial cannot select an earlier
  flashback timeline. Track/timeline changes while loading the report discard
  the pending debrief. Text and JSON exports include saved groups and lap notes.

Matched observational laps still do not prove setup causation. Pace trends
include fuel and driving effects and do not isolate tyre degradation. Older
recordings can be reviewed, but missing measurements cannot be reconstructed.

## Build identity and release checks

Verified application source:
[`02d7b7d925890c9aef7b921433e529fa26c3d6f4`](https://github.com/scottsart1/Pit-box/commit/02d7b7d925890c9aef7b921433e529fa26c3d6f4).
Source version: Windows 5.3.0 and Android 5.3.0 revision 32.

| Release check | Status |
| --- | --- |
| [Windows CI](https://github.com/scottsart1/Pit-box/actions/runs/37089712358) | Passed: 2,062 tests, one skip; installed and counted-telemetry acceptance |
| [Android CI](https://github.com/scottsart1/Pit-box/actions/runs/37088924042) | Passed: 1,790 tests, browser acceptance and emulator runtime |
| Windows installer byte count and SHA-256 | 34,986,674 bytes; `d5720fc9fd51c63c1fd6fa7324a5cde2ceddb8af4116f8e0f29ba171654308ef` |
| Windows frozen archive byte count and SHA-256 | 43,754,303 bytes; `2d2cc5cd3153e04297975244d868c79619612d7deb18a38e3fa6505142c8c0be` |
| Final frozen Windows feature and shutdown acceptance | Passed; graceful exit in 0.641 s, exit code 0, finalized captures verified |
| Production Android APK byte count and SHA-256 | 73,116,041 bytes; `20586fed7a0bc5c34f77d68125c7a61860c9d15417ae29a3b1f4de4da618ba88` |
| Android production signing identity and alignment checks | Passed; installed APK bytes also match |
| Physical tablet acceptance and in-place production upgrade | Passed; 139 sessions and 24,706 laps preserved |
| Public downloads, website and update notices | Passed: both artifact hashes, 18 website routes and six update-client cases |

The Windows distribution remains unsigned and labelled accordingly. Android
retains the production certificate SHA-256
`20c2751e5c0ede43a2442331336b990433e53d9e512f3f1bca3e8d5bee4c6983`.
Signature and 16 KB archive-alignment checks passed; the latter does not claim
support for 16 KB runtime memory pages. The separately signed QA package is
not a public download.

## Final Windows CI and frozen application acceptance

Windows CI ran against the verified source above and passed **2,062 tests with
one skip and one warning** in **429.47 seconds**. The downloaded installer and
frozen archive matched the staged byte counts and SHA-256 values in the table.

- The installed-app smoke check passed silent installation, isolated startup,
  real F1 2026 UDP ingestion, transfer-management APIs, dashboard shutdown,
  persisted session/capture validation, relaunch and history readback. Silent
  uninstall removed the application while preserving existing and recorded data.
  Transfer pairing, TLS/QR and paired history copying were not exercised on this
  Windows runner because it had no usable private LAN.
- Its accelerated **25-lap** Monza replay completed in **106.375 seconds** wall
  time, adding **32,255 packets** to a receiver total of **32,415**, with zero
  reported drops. Final classification was received, persisted and read back;
  **25 strategy snapshots** and one finalized capture were recorded. Request
  latency was **1.672 s p95**, **2.032 s maximum**.
- The separate **100-second, 20-car** counted-telemetry check sent, received,
  parsed and wrote **18,604 packets**, with zero rejected datagrams, sample
  timeouts, capture-queue drops or capture write errors. It persisted **19 rival
  laps** without archive-queue drops or write errors. Health latency was
  **0.343 s p95**, **1.578 s maximum**; state latency was **0.297 s p95**,
  **0.797 s maximum**. Graceful shutdown, database integrity and finalized
  capture checks passed. This was a bounded load check, not full-race endurance.
- The final frozen application also passed the real UDP engineering fixture and
  both browser suites at **1280×800, 800×1280 and 390×844**, including lap groups,
  note changes, comparisons, exports and delayed-save draft preservation. It
  started with an isolated database and no provider credentials in **7.656 s**.
  Dashboard shutdown completed in **0.641 s**, exited with code **0**, and left
  no owned processes running. Cold SQLite integrity, persisted groups and final
  note edits/deletions were verified after exit, along with clean footers in
  **three finalized captures containing 4,910 packets**. The temporary data root
  was removed after verification.

## Final Android CI acceptance

Android CI used the same application source and passed **1,790 engine/bridge
tests** in **140.05 seconds**, with one Starlette/httpx deprecation warning.

- The real UDP engineering fixture produced **four matched pairs**, with B
  quicker by **0.300 s**, entirely in sector 2. Pit debrief, track transition and
  rejection of a late retired-session packet passed. Grouping, manual lap
  selection, draft protection, context notes, corrections and exports passed at
  **1280×800, 800×1280 and 390×844** with no horizontal overflow. Desktop
  delayed-save race checks passed. Screenshot review confirmed readable,
  contained forms and notes at all three sizes.
- The **API 36, x86_64 Pixel C emulator** passed startup, fullscreen and keyboard
  behavior, stationary trace, process and listener restart identity, background
  UDP reception, transfer controls, and transfer TLS/QR checks. It received and
  parsed **360 datagrams**, rejecting none; receiver and forwarding queues
  reported zero drops. Crash-log size and unclosed-SQLite warning count were
  both zero. Schema remained **5200**.
- Measured emulator state-request times were **0.169 s at launch**, **0.134 s
  after restart**, and **3.945 s in the background**. Paired-device history
  copying and physical Wi-Fi were outside this emulator check. The stale
  telemetry indication after the synthetic feed stopped was expected.
- The downloaded engineering and runtime evidence archives matched their
  GitHub artifact digests. Physical-device and production-upgrade evidence is
  recorded separately below.

## Local automated acceptance

- Grouping and comparison tests cover exact group counts, deterministic
  chronological boundaries, arbitrary manual selections, stable lap identities,
  missing evidence, distinct compounds/weather, meaningful outliers, note
  corrections and compact note payloads. The earlier grouping plus 5.2 baseline
  run passed 74 checks; it is a focused development result, not a final release
  suite count.
- Final provenance-focused checks passed **42 tests**. These cover note scope,
  pending references, source attribution, timing-line/session boundaries and
  separating observed gap coverage from a reported traffic obstruction.
- **Seven** debrief/browser-voice regressions passed. They use the real temporary
  database and engineering service to verify persisted note exclusions,
  restoration after note removal, context in stored/spoken debriefs, flashback
  isolation, session changes during report loading, and browser transcription
  crossing the timing line. The originating-lap context is restored after
  successful replies, provider errors and cancellation.
- **139 Node checks** passed, including dashboard state, accessibility and
  preference behavior. **83 website tests** passed and the site builder's
  `--check` completed successfully. These are source checks, not public-site
  readback.
- Real UDP acceptance recorded two practice runs with a wing change and two
  pit-entry debriefs. Four matched pairs showed B quicker by **0.300 seconds**,
  entirely in sector 2. A Spa-to-Monza transition and a delayed packet from Spa
  left the new session's analysis, radio and briefing context clear.
- Chromium acceptance at **1280×800, 800×1280 and 390×844** passed navigation,
  matched comparison, custom grouping, individual lap selection, unsaved-draft
  protection, context notes, corrections and report downloads. No horizontal
  overflow was found. Delayed-save and edit-during-save checks also passed at
  the desktop size. Evidence includes screenshots and exported text reports.

An earlier full local app run reported **1,714 passed and one failure**: the
test process cached the page before its stylesheet version reference changed
from 5.2.0 to 5.3.0 during concurrent work. Its six-test file passed against the
finished source without another product change. The
earlier full run is not recorded as a full-suite pass. The completed final CI
runs above provide the release suite results.

The first packaged feature acceptance passed the new flows but exposed a
Windows shutdown stall after browser connections closed. A Proactor transport
reset left network cleanup waiting before application lifespan cleanup could
start. The final application bounds that network drain to ten seconds and still
awaits recording finalization separately. Fifteen main/shutdown checks passed;
the new regression uses a stalled listener and real queued capture packets,
then verifies a readable finalized capture even when cleanup exceeds the test
network deadline. The final frozen acceptance above confirmed graceful exit,
database integrity and readable finalized captures. The ten-second bound
applies to network drain; recording finalization is awaited separately.

Windows build attempt `37088928515` passed 2,062 tests and one skip, but its
installed stress check timed out reading health during the first few seconds
of load. The three-second bound was retained for a fresh-runner retry; no
product change was made. Its logs show complete graceful application shutdown.

The first final Android CI attempt passed 1,789 engine/bridge tests but failed
the new browser check while waiting for a temporary save-status message after
a concurrent refresh. The application preserved both the saved note and the
newer draft. The test now forces that refresh ordering and checks persisted
data, settled controls and draft preservation directly. The full local UDP and
three-viewport browser acceptance passed after this test-only correction.

## Live reasoning-provider exercise

An isolated source server with synthetic session data was tested using OpenAI
GPT-6 Luna through `/api/ask`. Explicit instructions saved a driver traffic report on one chosen
lap, excluded only that lap, retrieved its explanation and measured air
temperature, kept a context-only note eligible, and requested clarification
without writing when the affected lap was unspecified.

A natural report of being held up on lap 2 also saved the note without an
explicit request to use a logging tool. An initial follow-up incorrectly
described available gap observations as observed traffic. The tool vocabulary
was corrected to separate gap coverage, close-following flags and reported
interference. A repeated natural report/review then preserved the driver's
explanation while correctly stating that no close-following flag had been
recorded. Both final requests returned HTTP 200, in 6.14 and 4.30 seconds.
The temporary credential was removed after each exercise. Realtime note and
boundary behavior was unit-tested; these exercises did not test a live
Realtime speech socket.

These are bounded live-provider checks, not a guarantee of every phrasing or
provider. Physical native speech completion and device load evidence are
recorded below; these provider requests alone do not establish microphone,
speaker or Bluetooth reliability.

## Physical Galaxy Tab S11 Ultra acceptance

Device: Samsung SM-X930, Android 16, using wireless adb. Synthetic data was
sent only to the separate QA package and UDP port. The final signed QA APK was
read back from the tablet and matched its local artifact hash.

- Real UDP produced two runs and four matched pairs. Custom exact-lap groups,
  a three-group suggestion preview, persistence, descriptive/strict comparison,
  exclusion, correction, deletion and both report formats passed. Fingerprints
  of measured laps stayed identical through the note and group changes.
- Native touch navigation reached Test Engineer, selected a saved session and
  custom groups, and displayed B faster by 0.300 seconds in sector 2. The note
  editor, measured conditions and source-attributed reports were visually checked.
- Android's document picker saved an 8,049-byte report containing the groups
  and note. It matched the API report byte for byte, SHA-256
  `b4a7e4311553c59119cf25b5a6cfc3969b38629508d2ec3f8e29fb1bb75e4e09`.
- A 100-second 20-car / 60 Hz replay received and parsed all 18,604 packets.
  No request timed out, and receiver/capture queues had no drops or write errors.
  Health p95 was 0.078 s, maximum 1.000 s; state p95 was 0.141 s, maximum
  0.438 s. Three native speech operations completed during reception.
- An actual provider request in flight returned 409 after a track transition;
  the new session had empty radio and briefing context. The temporary API
  credential was removed and the QA proactive preference restored afterward.
- The final QA app exited through its native Quit flow; logs confirmed
  application shutdown completion and server exit within about 0.21 seconds.
- The regular app was upgraded in place to revision 32. Its installed APK
  matched the final signed production artifact byte for byte. All **139 sessions
  and 24,706 laps** were preserved, including session metadata apart from
  catalog update timestamps. Schema remained 5200. Normalized full lap records
  retained SHA-256
  `c9b016f8320411004ad750afdaffaa8c6a88d5568316c208262d64b122169459`.
  The existing voice credential remained configured. Setup Lab's native
  **Open Test Engineer** control opened the new workspace in the regular app.

Speech completion is not human confirmation of audibility. These bounded
checks do not establish a full-race endurance or Bluetooth-headset result.

## Production publication and readback

- Both verified artifacts were uploaded to the existing `pitwall-downloads`
  bucket before deploying the download Worker and website. Full public range
  downloads matched the byte counts, filenames and SHA-256 values above.
  The Windows download continues to require no activation code.
- Worker version: `946fd7ad-8d58-4b16-9aaa-3825065faf62`.
  Cloudflare Pages deployment:
  [`d193fc8c`](https://d193fc8c.pitwall-2k7.pages.dev).
- Readback from `https://yourpitbox.com` passed for **18 routes**, including the
  download page, guide, styles/scripts, diagnostics, owner pages and Driver
  Dashboard files. Served content matched the built files after normalizing
  only line endings and Cloudflare email protection.
- Windows and Android update notices were published as **5.3.0** only after
  public artifact and website verification. The application's actual update
  client passed **six cases**: installed versions 5.1.0 and 5.2.0 see 5.3.0 as
  available on both platforms; installed 5.3.0 sees no newer update. Every
  returned release hash and size matched its verified public artifact.

## Remaining limitations

The earlier combined Galaxy Tab Wi-Fi/Bluetooth interruption remains under
investigation and is still disclosed in the guide. The local Windows response
timeouts documented in [5.2.0 verification](release-qa-5.2.0.md) are also not
claimed fixed by this feature release. The final packaged load and device
results above are distinct from source/UI tests. No full-race endurance or
headset-reliability claim follows from these bounded checks.

Raw device logs, provider transcripts, credentials, history inventories and
synthetic captures remain outside source control.
