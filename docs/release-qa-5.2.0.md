# Your Pit Box 5.2.0 — Test Engineer and session isolation

Windows 5.2.0 and Android 5.2.0 revision 31 are published at
[yourpitbox.com](https://yourpitbox.com/#download). The website guide explains
[where to find Test Engineer](https://yourpitbox.com/guide.html#test-engineer).
Release checks completed on 2 October 2026 US Eastern / 3 October UTC.

## Behavior

- Setup Lab links to Analysis → Test Engineer. Session Review's **Review runs /
  export report** opens the selected saved session. Drive shows the latest
  automatic stint debrief; Strategy shows strategically relevant rivals.
- Runs separate pit exits, tyre sets, setup changes and replay timelines.
  Each run retains its compound, setup, clean pace, consistency, observed pace
  trend, temperature range, handling indicators, exclusions and suggested next
  test. Objectives and conclusions can be saved per run or whole session.
- A/B comparison pairs each eligible lap at most once. It requires the same
  compound and weather, tyre age within one lap, fuel within 3 kg and air/track
  temperatures within 3°C. It rejects missing evidence, incomplete coverage,
  invalid/pit/neutralised/traffic laps and conservative pace outliers. At least
  three pairs are required for a verdict. Sector deltas use official splits;
  unavailable sectors are not invented. Negative B-minus-A means B was quicker.
- Returning to the pits generates a measured debrief without a model request.
  It is spoken when proactive radio is enabled and the voice pipeline is free.
  A busy radio does not accumulate a queue of old garage debriefs.
- Strategic rivals are ranked by projected finish proximity on the same time
  axis as the selected plan, including estimated remaining stops, tyre age and
  pending penalties. Forecast confidence and assumed gaps remain visible.
- Text and JSON reports include runs, setup changes, incidents, test notes and
  conclusions. Android uses the system file picker. Rendering notes uses DOM
  text, and export filenames do not reflect arbitrary request text.
- A replaced session, restart or flashback cancels pending engineer/wake/voice
  work, stops playback, closes Realtime and clears live analysis, briefings,
  queued audio and radio context. Delayed packets from retired UIDs are ignored.
  Frozen historical lap writes may finish, but cannot update the new session.
- Official sector history arriving during asynchronous lap analysis now survives
  persistence; zero placeholders cannot overwrite already received splits.

Observed pace trend includes fuel and driving effects; it does not isolate tyre
degradation. Matched observational laps do not prove setup causation. Handling
flags are evidence, not a diagnosis of a setup fault. Older recordings remain
readable but may lack the condition/setup/traffic evidence required for A/B.

## Build identity

Application source: `15fbbb9c0349706821c51b0383de4c89a7a2c0aa`.
Website and download metadata: `fecfe94`.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| Windows `PitWall-Setup.exe` | 34,934,459 | `086dc311aa1c6fac8d958d396a49d51641b36491d61738a4787158fb52cb11e2` |
| Android `YourPitBox-5.2.0-android.31.apk` | 73,062,793 | `0fd3a64b6e65e1b80cacacdc94d2f830feec7dcd3688bd42e128497264eda410` |

Windows retains the existing unsigned distribution and is labelled accordingly
on the download page. Android uses the existing production certificate SHA-256
`20c2751e5c0ede43a2442331336b990433e53d9e512f3f1bca3e8d5bee4c6983`.
Signature and 16 KB alignment checks passed. The separately signed QA package
is not a public download.

## Automated acceptance

- [Windows CI](https://github.com/scottsart1/Pit-box/actions/runs/37079125011):
  1,964 tests passed, one skipped. Installed-artifact startup, UDP, persistence
  and shutdown checks passed. The packaged 100-second 20-car / 60 Hz test sent,
  received, parsed and wrote all 18,604 packets, with no request timeouts,
  queue drops or write errors. All 19 rival laps persisted. Health p95 was
  0.156 s (maximum 1.297 s); state p95 0.157 s (maximum 0.500 s).
- [Android CI](https://github.com/scottsart1/Pit-box/actions/runs/37079117766):
  1,694 shared-engine and Android checks passed. Android 16 emulator startup,
  UDP, restart and background checks passed; production and QA release-mode
  APKs were built from the same source.
- Real UDP Test Engineer acceptance creates two practice stints with one wing
  change, returns to the pits twice, compares four matched pairs and verifies
  a 0.300-second gain entirely in sector 2. It then changes Spa to Monza and
  sends a delayed Spa packet; no previous analysis, radio or briefing returns.
- Chromium acceptance at 1280×800, 800×1280 and 390×844 passed navigation,
  comparison, notes persistence, text download and horizontal-overflow checks.
  This test also runs in Android CI.
- After the release metadata changes, 114 website/Android project checks and
  all 87 download-Worker tests passed. The site builder's checks passed.

Early failures remain part of the evidence. The first full local run still
expected the old release number; later CI found four wake-cue test doubles
missing the new boundary event. Those fixtures were corrected and both final
CI suites passed. The real UDP test found the sector-history race described
above; a regression test now reproduces the ordering.

Local source-only Windows load attempts also exceeded the three-second response
bound. One final source run retained every packet and passed database integrity
and shutdown, but had two health timeouts; it is not a performance pass. A
diagnostic cProfile run added substantial overhead and exceeded shutdown time.
These failures are retained separately from the successful packaged CI test.
A subsequent test of the downloaded 5.2.0 Windows executable on the local PC
also retained all 18,604 packets and 19 rival laps, with no queue drops or write
errors, and passed integrity/shutdown. One health request at about 90 seconds
exceeded three seconds, so this local packaged run is also a responsiveness
failure. Its successful health samples had p95 0.391 s and maximum 2.656 s;
state p95 was 0.187 s. The CI pass does not override this local result.
An identical local control run of the previous 5.1.0 packaged executable also
failed the response bound, with two health timeouts at about 88 and 94 seconds.
It retained every packet and passed integrity/shutdown. This reproduces the
condition in the previous release on this PC; it does not establish its cause
or resolve the local responsiveness limitation.

## Physical Galaxy Tab S11 Ultra acceptance

Device: Samsung SM-X930, Android 16, connected through wireless adb. Synthetic
telemetry was sent only to `com.yourpitbox.app.qa`, using its own UDP port and
private database.

- The signed release-mode QA upgrade passed the same two-run UDP, sector
  comparison, automatic pit-debrief and track-transition checks as source.
- Native touch navigation from Setup Lab reached Test Engineer. Saved Spa
  selection and A/B comparison displayed the expected result and exclusions.
  Scrolling kept the comparison and its controls accessible.
- **Export session report** opened Android's document picker. A text report
  saved into Downloads matched the API response byte for byte (2,667 bytes).
  SHA-256: `2bb3b965b5b092a83848415a5059fb75bd3876e8671f0aef3e75216f156b2d5c`.
- A 100-second, 20-car, 60 Hz Wi-Fi replay received and parsed all 18,604 sent
  packets. There were no request timeouts or receiver/capture queue drops or
  write errors. Health p95 was 0.109 s (maximum 1.375 s); state p95 0.094 s
  (maximum 0.188 s). Three native speech operations completed during reception.
- A real provider request in flight was cancelled by a subsequent track
  transition. The API returned 409 and the new session had empty radio and
  briefing state. The temporary QA API credential was then removed and the
  original proactive setting restored.
- The normal `com.yourpitbox.app` was upgraded in place to revision 31. Schema
  migrated from 4904 to 5200. All **139 sessions and 24,706 lap records** were
  retained; all session metadata apart from catalog refresh timestamps matched.
  Normalised full lap records had the identical pre/post SHA-256
  `c9b016f8320411004ad750afdaffaa8c6a88d5568316c208262d64b122169459`.
  The regular app's voice credential remained configured.

Speech completion is not human confirmation of audibility. These bounded tests
do not establish full-race endurance or Bluetooth-headset reliability. The
previously reproduced combined Wi-Fi/Bluetooth failure documented in
[5.1.0 verification](release-qa-5.1.0.md) remains under investigation; this
release does not claim to fix it. The public guide retains that limitation.

## Production readback

The download objects were uploaded before the Worker and website deployment.
Complete public Windows and Android downloads matched the byte counts and
hashes above; the Windows route still requires no activation code.

Worker version: `a378f853-5ada-41c7-9e8f-415d95c02224`.
Pages deployment: `https://ff57f4b8.pitwall-2k7.pages.dev`.
Eighteen public website routes matched the built files, allowing only
Cloudflare's email-protection transformation and text line endings.
Both release notices were published after the download and website checks.
The actual update client passed six production checks: Windows and Android
at versions 5.0.0, 5.1.0 and 5.2.0. Older versions offer 5.2.0; the current
version reports no newer release. Both notices contain the verified hashes.
Raw device logs, history inventories, credentials and synthetic captures remain
outside source control.
