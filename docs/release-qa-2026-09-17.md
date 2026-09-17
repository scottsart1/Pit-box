# Release QA — 17 September 2026

This is a local validation record, not authorization or evidence of a new
deployment. Public downloads, the Worker/database, and installed versions were
not replaced. User recordings and backups were not deleted.

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

## Remaining release work

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
