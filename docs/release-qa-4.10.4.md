# Your Pit Box 4.10.4 — release verification

Status: release gates passed; publication verification is recorded below when complete.
The owner explicitly requested building, testing and public website deployment.

Source: `029eb130430af8210b0596936767646af6b91a83`.
Windows/shared version 4.10.4; Android revision 17.

## Why this additional build

See the retained [4.10.3 findings](release-qa-4.10.3.md), including every failed
packet test. Moving strategy computation off the receive loop fixed the original
Windows loss. A heavier physical Android test then exposed excessive diagnostic
work: routine health polling recalculated detailed field quality over all retained
full-field samples. The Android socket reported exactly the missing packets as
receive-buffer drops, even when application queue-drop counters stayed at zero.

Routine health responses now use bounded assembler metadata and detached counters,
with `group_details_included: false`. Detailed in-process `quality_report()`
behavior remains unchanged. Tests ensure routine health cannot scan sample fields,
metadata is accurate, counters cannot drift/mutate live state, and shutdown/empty
states are represented correctly. The 46 affected assembler, app, live-API and
Android-project tests passed before dispatching new release builds.

In a 60,000-sample synthetic microbenchmark, the full report took about 0.58 s
without a profiler, versus 0.000025 s per summary call. cProfile separately
attributed over three million calls to the full report. Microbenchmarks are not
end-to-end UI latency claims.

## Local source-process load test

`counted-windows-qa-w8nl1w6e/diagnostics/summary.json` in the parent workspace:

- 20-car Monza fixture, 100 simulated seconds, crossing lap 2.
- **18,604 sent, received, parsed and recorded**, no rejects, internal drops or
  recording errors. Independent send counts are compared with the receiver.
- 100.032 seconds elapsed, 185.98 packets/s.
- No 3-second health/state request timeouts. Health P95 0.250 s, max 1.125 s;
  state P95 0.141 s, max 0.969 s.
- Graceful shutdown, finalized recording and SQLite integrity passed with the
  normal 2 GiB free-space reserve. User data was not used.
- This is source-process evidence. Packaged-app verification is recorded below
  only after the actual installer/APKs pass.

## Release gates

- [Windows CI 35289369234](https://github.com/scottsart1/Pit-box/actions/runs/35289369234):
  complete regressions, actual install/startup, synthetic 25-lap race, SQLite,
  restart/readback, data-preserving uninstall, independently counted full-field UDP.
- [Android CI 35289371556](https://github.com/scottsart1/Pit-box/actions/runs/35289371556):
  shared/Android regressions, native libraries, standard non-debug release and
  isolated release QA packaging, emulator startup/UDP/restart/background/UI.
- Physical tablet QA uses only `com.yourpitbox.app.qa`, retaining both legacy
  packages and all real histories. No production-signed package is forced over
  a differently signed installation.
- Live reporting service tests use a separate QA Worker/database. No synthetic
  usage event is written to production. Existing private report credentials are
  preserved, not published in URLs or source.

## Packaged release evidence

Windows CI passed **1,827 Python tests, 1 skip**; Android CI passed **1,570
Python tests** and **47 Node tests**. The final website and Worker references
passed **74 website Python tests** and **47 Node tests** locally.

The actual frozen Windows 4.10.4 runtime passed the laptop full-field fixture:
`counted-windows-qa-bj3hzgve/diagnostics/summary.json`. All 18,604 independently
sent packets were received, parsed and recorded in 100.031 seconds (185.98/s),
with no internal drops, recording errors or 3-second request timeouts. Health
P95 was 0.344 s, max 2.094 s; state P95 0.250 s, max 0.688 s. Normal 2 GiB
recording reserve, finalized capture, SQLite integrity and shutdown passed.

The separate laptop frozen-runtime lifecycle test also passed:
`packaged-qa-921azwya/diagnostics/summary.json`. It completed the accelerated
25-lap Monza fixture in 169.687 seconds, received the expected final
classification, persisted the completed race and strategy, and passed full
SQLite integrity. Combined health/state poll P95 was 2.046 s, max 2.594 s.
After graceful shutdown/restart the recorded session, TLS transfer identity
and declined usage setting survived. No installation ID or report queue was
created while sharing was declined. This test used isolated data and did not
replace the owner's installed app. Installer/uninstaller lifecycle was separately
verified in CI. Paired large-history copying remains outside this smoke test.

The physical Samsung SM-X930 (Android 16, 4 KiB pages) ran the signed,
non-debuggable `com.yourpitbox.app.qa` revision 17. Both existing user apps and
their histories were preserved. `qa-candidate-4.10.4/android/full-field.json`
records all 18,604 packets received, parsed and recorded with zero kernel socket
drops, internal drops or request timeouts; max health response 0.516 s. Its
requested-condition label says locked, but the observed screen was **on and
ACTIVE**: this is foreground/awake evidence, not a locked/Doze reliability pass.
The separate background test received all 5,400 packets over 30 simulated
seconds, max health 1.031 s, without claiming deep-Doze coverage.

Physical UI screenshots and hierarchy evidence cover the live dashboard,
Settings with sharing unchecked, and expanded privacy disclosure. Restart
retained decline, fixture history and TLS transfer identity. QA was gracefully
stopped and its temporary ADB forwarding removed after testing. No provider
key/audio was used, and no fixture usage event was sent to production.

The standard public and isolated QA APKs have identical 61 engine/native payload
entries. Both verify with APK v2/v3 signatures and the existing release certificate
SHA-256 `20c2751e5c0ede43a2442331336b990433e53d9e512f3f1bca3e8d5bee4c6983`.

| Public artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `PitWall-Setup.exe` | 34,684,777 | `b2e62d08e93144706cf7c23ecd19045ecef62c3857a4b59efb27d8cbe55a36e8` |
| `YourPitBox-4.10.4-android.17.apk` | 72,704,641 | `bdcb25fab226be79beff48352e3e939761a0c59f9c6a63f824cd5cec8ceba89d` |

## Disclosed boundaries

Windows remains unsigned, as authorized for direct website distribution. Android
supports tested 4 KiB page-size configurations, not 16 KiB devices. Transfers and
UDP forwarding remain beta. Screen-off/background Android reception is best-effort;
keep the app visible and screen awake during racing. A short test is not a real
full-race/long-duration Doze guarantee. Provider billing/audio quality is not tested
without a configured key. The laptop browser runtime was unavailable; DOM, HTTP
and emulator checks are distinguished from local browser/physical-tablet visual QA.

Usage reports require explicit opt-in and contain bounded daily flags, not race
recordings, audio, messages, keys or hardware identifiers. Installations are not
unique people; separate download totals are not a linked conversion funnel.

Rollback baseline and staged service evidence are in the 4.10.3 preparation record.
Final artifact hashes, device checks, deployed versions and public verification
will be added after those actions actually complete.
