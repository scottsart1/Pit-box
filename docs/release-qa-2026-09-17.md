# Release QA — 17 September 2026

This is a local validation record, not authorization or evidence of a new
deployment. Public downloads, the Worker/database, and normal installed versions
were not replaced. A separately named Android QA app was installed with its own
private data. User recordings and backups were not deleted.

## Current candidate verdict — 4.10.2

**Built and tested, but not approved for a new public release.** Both CI workflows
passed. Local Windows testing nevertheless reproduced a responsiveness timeout
and independently measured UDP loss that the internal queue-drop counter did not
report. A green workflow is not sufficient evidence to dismiss these results.

The owner authorized development-branch pushes and build-only CI. Candidate source
is `8cd828f8f71874c5ad505980c28890ff66724409` on
`codex/physical-device-validation`. No production deployment or migration ran.

| Candidate check | Evidence and result |
| --- | --- |
| Windows build and complete regression suite | [CI 35283045877](https://github.com/scottsart1/Pit-box/actions/runs/35283045877): 1,808 passed, 1 platform-dependent skip, 2 dependency warnings |
| Windows installer lifecycle on disposable runner | Silent install, frozen launch, UDP, 25-lap synthetic race, finalized recording, full SQLite integrity, restart/readback and data-preserving uninstall passed |
| Android build and regression checks | [CI 35283048553](https://github.com/scottsart1/Pit-box/actions/runs/35283048553): 1,551 Python tests, 47 Node tests, real UDP/transfer integration and three DOM checks passed |
| Android emulator | API 36 x86_64 debug APK: startup, UDP, restart, background reception, transfer TLS/QR and UI-tree-driven Connection/Transfer controls passed |
| Actual Windows candidate on owner's laptop | `packaged-qa-y0w4tz6y/diagnostics/summary.json`: frozen start/restart, default-off usage choice, persisted decline, real UDP, finalized recording, database integrity, history reopen and stable transfer TLS identity passed |
| Physical signed Android QA candidate | Samsung SM-X930 / Android 16 / 4 KiB pages; isolated non-debuggable `com.yourpitbox.app.qa`, version `4.10.2-android.15-qa`, installed and launched |
| Physical Wi-Fi while tablet PIN-locked | `qa-candidate-4.10.2/android/background-telemetry.json`: all 5,400 sent datagrams received, parsed and written; zero reported capture drops/errors; stationary trace stayed at two points; health P95 79 ms, max 94 ms |
| Physical Android process restart | `qa-candidate-4.10.2/android/restart-transfer.json`: graceful backend shutdown, process restart, persisted decline, saved fixture API readback and transfer certificate identity passed; no app-scoped fatal/unclosed-SQLite warnings observed |

Evidence directories above are retained in the parent workspace, not distributed
as application assets. The Windows laptop check kept the normal **2 GiB** capture
reserve. Its real installed 4.10.0 app and real history were not used by the tests.
The Android background test was a bounded, synthetic stationary three-packet
fixture, not a full-field race or long-term Doze test. No API keys were copied
into either QA environment, and no live provider/audio-response test was performed.

The tablet was PIN-locked. Decline/restart was tested through its own forwarded
loopback API, **not by clicking the consent controls on the physical tablet**.
The owner was asked to unlock it; no PIN was requested or guessed. Emulator
screenshots show the new prompt and stationary-history label, but do not replace
the remaining physical release-mode UI checks. The Windows browser/computer
runtime still fails before JavaScript initialization, so no Windows visual pass
is claimed.

### Local Windows failures retained

1. `packaged-qa-7cr35s_4/diagnostics/`: the unchanged 3-second health-request bound
   failed during the 25x synthetic race, after 45.922 seconds. Last observed lap
   was 9. Successful combined health/state samples reached 3.625 seconds; this
   excludes the timed-out request. The server subsequently shut down gracefully.
2. `counted-windows-qa-uyrmv0f1/diagnostics/`: 18,604 independently counted sends,
   but only 14,796 received/parsed/recorded, with zero internal queue-drop and
   capture-error counters. All **received** packets were recorded; missing packets
   were upstream of the application's receive counter. Shutdown, capture
   finalization and SQLite integrity passed. This fixture crossed a lap boundary
   with 20 cars. Its first emitter used Windows `Event.wait`, which stretched
   100 simulated seconds to 188.188 seconds (about 98.9 sends/s), so it does not
   prove the intended 186-packet/s rate. The missing packets still make it a
   failed test, not a pass. A follow-up uses Python's higher-resolution sleep
   and additionally checks actual elapsed emission time.
3. `counted-windows-qa-ws4mt3sn/diagnostics/`: the corrected emitter sent 18,604
   datagrams in 107.937 seconds (172.36/s measured); only 13,212 were received,
   parsed and recorded. There were 5,392 missing datagrams (about 29.0%) and
   another health response exceeded 3 seconds near the lap boundary. Internal
   queue-drop/write-error counters remained zero. Database integrity, finalized
   capture and graceful shutdown still passed. This is a failed reception and
   responsiveness check; successful recording of received data does not repair
   the missing samples. The helper is parent-workspace
   `test-windows-counted-qa.py`; it uses a fresh isolated data directory each run
   and the exact retained frozen candidate, not a source-server substitute.

CI's completed 25-lap race had combined health/state latency P95 3.25 seconds and
maximum 4.36 seconds. It does not independently count all emitted packets and
must not be described as lossless. The local failure establishes a release
concern even though its CI counterpart completed successfully.

### Narrowed performance lead, not a shipped fix

`qa-candidate-4.10.2/strategy-source-diagnostic.json` records a deterministic
source reproducer using the retained synthetic lap-9 state and an empty historical
model. Three **unprofiled** calls to `StrategyEngine.compute` took 1.3134, 1.1997
and 1.0921 seconds. A separate profiled call spent substantial time annotating
6,956 candidate plans and calculating pit-cycle positions. Profiling overhead
is not included in the three timings above.

Code inspection confirms `StrategyEngine.recompute` calls `compute` synchronously
inside an async method. Periodic strategy refreshes and lap analysis can invoke
this on the same event loop as the HTTP endpoints and UDP transport. The UDP
receive counter is incremented only when the callback runs; its zero internal
drop count cannot certify delivery before that callback. Event-loop stalls and
OS receive-buffer overflow are therefore a plausible explanation, **not a proven
complete root cause from a frozen-process stack capture**. No performance fix or
test-threshold relaxation was applied to the shipped candidate.

The next implementation should keep heavy planning away from receive/HTTP work,
bound concurrent recomputations, reject stale-session results, and preserve
strategy correctness. Re-run the counted field/lap-boundary fixture, full race,
restart/persistence and both platforms' CI before replacing any public artifact.

### Retained candidate artifacts

All paths are under parent-workspace `qa-candidate-4.10.2/`. They are **not live
website downloads**. The Windows installer remains unsigned. Both Android
candidates passed APK v2/v3 signature verification with the existing permanent
release certificate, SHA-256
`20c2751e5c0ede43a2442331336b990433e53d9e512f3f1bca3e8d5bee4c6983`.

| File | SHA-256 |
| --- | --- |
| `windows-ci/PitWall-Setup.exe` | `431a337ccf670a1c748bc87c8beacc1d6506d1a507d81fb3a33111216fb188e7` |
| `YourPitBox-4.10.2-android.15.apk` | `8380fe1b4c6974b853d2aea4fc30b12d9ca482609e61c9531ba976dd88e8ee5f` |
| `YourPitBox-4.10.2-android.15-qa.apk` | `066c9fa8631e92d1866b694d79df8a604fd9ba5c3eb3ee0c30eb5e5fb8910918` |

Only the QA APK was installed. Neither `com.yourpitbox.app.debug` nor the legacy,
differently signed `com.yourpitbox.app` was uninstalled, cleared or upgraded.
The standard release-package candidate was verified as a file, not installed.
Android's known 4 KiB-only native-library compatibility limitation is unchanged.

At the end of this pass, the isolated Android backend acknowledged shutdown and
its ADB API forward was removed. Wake listening and usage sharing were off,
usage choice was saved with zero pending days, and transfer sharing was stopped.
The QA icon remains installed separately; no ongoing telemetry emitter was left
running. The normal Windows installation, both legacy tablet apps, saved history
and rollback backups remain unchanged. Public distribution stays paused pending
the failed Windows reception/responsiveness gate and remaining visual checks.

## Prior baseline record

The sections below describe earlier baseline checks and constraints, before the
candidate builds above. Their failures and narrower scope are retained for audit.

## Versions and scope

- Uncommitted reporting draft based on `8381456929adfef936d0d6089d6cf620c6cd4135`.
- Actual frozen Windows 4.10.1 executable from the retained release build tested
  using new temporary data directories, private ports and no provider keys.
  The laptop's installed 4.10.0 application was not upgraded.
- Samsung SM-X930: Android 16, 4 KiB memory pages, existing debug package
  `com.yourpitbox.app.debug`, version `4.10.0-android.13-debug`.
- Permanent-signed release APK `4.10.1-android.14` verified as a file, not installed
  over either existing differently signed tablet package.
- No new reporting-draft Windows installer or Android APK has been built yet.

## Completed checks

| Area | Evidence | Result |
| --- | --- | --- |
| Complete source/distribution regression suite | `pytest tests distribution/tests -q -p no:cacheprovider --tb=short` | 1,801 passed, 1 platform-dependent skip, 2 dependency deprecation warnings |
| Website and reporting behavior | `node --test distribution/activation-server/tests/*.mjs` | 47 passed |
| Dashboard DOM | Usage consent, transfer controls and stationary/historical trace scripts | All passed |
| Draft reporting, real Windows source process | `tools/usage-reporting-smoke.py` | Startup, explicit choice, local delivery, opt-out identity clearing, persisted opt-out on restart and shutdown passed |
| Packaged Windows baseline | `packaged-qa-15zjvhx_/diagnostics/summary.json` outside repo | Startup, actual UDP decoding, transfer TLS/QR, recording, clean shutdown, restart, history reopen, stable transfer identity passed |
| Tablet baseline | ADB API, UI tree and screenshot observations | Healthy backend, dashboard visible, Settings navigation, background backend health; no app-scoped AndroidRuntime error output observed |
| Android release signing | Android SDK `apksigner verify --verbose --print-certs` | Valid APK v2/v3 signatures, permanent certificate matched |
| Public artifact routes | HEAD requests only, no download counter increments | Windows and Android HTTP 200, expected sizes/types, byte-range support |
| Website source validation | `python -m distribution.website.build_site --check` | Passed |

All reporting-test traffic used mocks or a loopback fixture, not production.
The tablet application was shut down after checking it; the ADB forward was
removed. No synthetic race was injected into the tablet's real history.

## Storage constraint

The laptop started with approximately 250 MB free. Four redundant generated
build ZIPs were removed only after every archived file's SHA-256 matched the
retained extracted copy, recovering 219,727,141 bytes. These archives can be
recreated. No recordings, backups, unique APKs or rollback installers were removed.

The default Windows recording test correctly hit the 2 GiB free-space reserve;
there was no finalized raw capture. The bounded isolated rerun used a **0.1 GiB
test-process-only reserve** and passed recording/restart/integrity checks.
Neither installed app settings nor the production default were changed.
This does not establish normal recording readiness on a nearly full laptop.

## Stress evidence and harness correction

The first 25-lap, 25x synthetic-race attempt timed out on a 3-second health
request while the full regression suite was also active. Retained evidence:
`packaged-qa-z196x52c/diagnostics/` outside the repository. This failed run must
not be omitted from a performance assessment.

The subsequent isolated attempt exposed a **test-oracle defect**: it rejected
the projected wet-tyre waiver even though the app separately reported observed
wet use as false and its instruction explicitly said the plan was valid “only
if” the future wet running occurred. The test now checks the observed rule and
the conditional instruction. Five new regressions cover the actual strategy
helper's output, false/missing observed evidence and unqualified instructions.
The affected installer/strategy suite passed 63 tests after the initial correction.
No strategy production code was changed to obtain this result.

The corrected-gate run passed the full synthetic race and persistence lifecycle:
`packaged-qa-46o_dl8l/diagnostics/summary.json` outside the repository. It completed
25 laps in 113.375 seconds, received the expected P7/six-point classification,
retained strategies and finalized capture, passed SQLite `integrity_check`,
restarted, reopened the basic fixture session through the API, and rechecked
the race classification in the persisted database after the second shutdown.
Transfer identity survived the process restart. The actual installer and
uninstaller were not run on the user's laptop.

The app reported 6,718 additional parsed packets and zero internal queue drops.
This is **not proof of lossless UDP reception**: the harness does not independently
count every emitted datagram. Combined sequential health/state request latency
was 2.266 seconds at P95 and 3.609 seconds maximum, so this successful completion
does not establish consistently responsive operation under stress. The earlier
timeout remains relevant. The final test-oracle regressions, linked to the
actual strategy helper, passed all 31 installer-smoke unit tests.

## Work outstanding at the baseline checkpoint

- Build and test actual reporting-draft installers/APKs; obtain permission for
  development-branch push and artifact-only CI jobs while public deployment
  remains paused.
- Use a separately identifiable QA Android package or another clean device;
  do not uninstall the differently signed legacy apps and lose their history.
- Complete new-build Android consent/restart/telemetry checks and Windows
  installer lifecycle checks on a disposable runner.
- Bound stress responsiveness with representative-rate repeat tests and
  independent sent/received counts; obtain adequate laptop storage.
- Browser visual QA remains blocked: browser runtime failed before JavaScript
  execution with a kernel-assets path error. DOM tests are not visual QA.
- Retain Android's disclosed 4 KiB-only native-library limitation until a
  separately validated 16 KiB-compatible build exists. This tablet's 4 KiB
  page size does not prove compatibility with 16 KiB devices.
- Complete staged Worker migration/reporting checks and matched website/app
  deployment only when deployment is resumed. Large history transfer remains beta.
