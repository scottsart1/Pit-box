# Your Pit Box 4.10.3 — release QA

The owner requested build, test and public website deployment on 17 September
2026. This record supplements, not replaces, the retained
[4.10.2 failure record](release-qa-2026-09-17.md).

## Scope and identity

- Shared engine and Android release source: `f9c4930916a34b70fb07cea3d44c99761e30b7d1`.
- Windows rerun: `23031724f704b4078c995a2db3064e6d36c6814a`; only the test
  sender's pacing and retention of diagnostic artifacts changed after the
  Android build. Application code and assets are identical between those commits.
- Android: `4.10.3-android.16`, package `com.yourpitbox.app`, non-debuggable.
- Physical tablet testing uses a separately signed release-mode QA variant,
  `com.yourpitbox.app.qa`. Its 61 application asset/native-library entries are
  byte-identical to the public APK. Manifest identity and label intentionally differ.
- Existing normal Windows/Android installations, saved sessions and backups are
  untouched. QA used separate data, localhost/QA telemetry ports and no provider
  API keys. No security feature or firewall was disabled.

## Fixes and regression evidence

Strategy computation now runs on an isolated worker off the telemetry loop.
Concurrent recomputes coalesce. Results are committed only if the current race
context still matches; restarts, timeline changes, tyre changes and changed user
plans invalidate obsolete results. Shutdown waits for owned work. The receiver
requests a 4 MiB socket buffer; independent sent/received counts remain mandatory.

- Local full Python suite: **1,824 passed, 1 skipped**; two dependency deprecation
  warnings. The original test spy was updated to observe the isolated worker,
  preserving its numerical/scenario-cost assertions.
- Worker/website Node suite: **47 passed**; website Python suite: **74 passed**.
- Local usage, historical trace and transfer DOM interaction smokes passed.
- Real local source process: default-off, explicit enable, asynchronous delivery
  to a loopback collector, payload allowlist, opt-out clearing, persisted decline
  and graceful shutdown passed.
- Source Windows counted test: all **18,604** packets received/parsed/recorded;
  no drops, recording errors or 3-second response timeouts. 107.0 seconds,
  173.87 packets/s; health P95 1.406 s, maximum 2.094 s. This run loaded the
  corrected engine before its version constant was bumped; it is source evidence,
  not a claim that a released 4.10.2 binary contained the fix.

## Android evidence

[Android CI 35286954570](https://github.com/scottsart1/Pit-box/actions/runs/35286954570)
passed 1,567 shared/Android Python tests, all 47 Node tests, native wheel checks,
debug and non-debug release packaging, and Android 16 x86_64 emulator startup,
UDP, restart, background reception and UI-tree-driven transfer-control checks.
Emulator screenshots were inspected; the optional reporting prompt is readable
and offers both enable and decline. The historical trace is labelled separately
from current stationary controls. This is not physical ARM64 visual QA.

Signed physical ARM64 QA on Samsung SM-X930, Android 16, 4 KiB pages:

- 5,400/5,400 physical Wi-Fi datagrams received and recorded over 31.469 seconds;
  no drops/errors, maximum sampled health response 94 ms.
- Graceful shutdown/restart, persisted decline, fixture session readback and
  transfer TLS/QR identity persistence passed. No app-scoped fatal exception or
  unclosed-SQLite warning observed.
- The tablet remained PIN-locked. Physical screen interaction was not claimed.
- Public APK: 72,704,641 bytes, SHA-256
  `32e01417f55ea04c490b228397d9724dea04d3a25b6ccdcecacda627eecc3c61`.
- APK v2/v3 signature verified against the existing permanent release certificate
  SHA-256 `20c2751e5c0ede43a2442331336b990433e53d9e512f3f1bca3e8d5bee4c6983`.

## Windows packaged gate and retained sender failure

First 4.10.3 Windows CI run 35286952403 passed the full suite and actual installer
lifecycle, including its 25-lap synthetic stress fixture. The counted test received,
parsed and recorded all **18,604** packets, without timeouts or recording errors;
SQLite integrity/finalization and shutdown passed. However, the sender took
123.922 seconds (150.13 packets/s), violating the unchanged 115-second offered-load
gate. **That run is retained as failed**, not reclassified as a pass.

Per-frame relative sleeps accumulated Windows scheduler overshoot. The rerun
schedules against one absolute emission clock. No packet-count, response-time,
recording or elapsed-time gate was relaxed. Installer/runtime artifacts are now
retained for diagnosis on failure, but failed checks still block publication.

## Staged reporting service

A separate QA Worker and D1 database were used, never the production customer
database. Fourteen live HTTP checks passed: method handling, denied missing/wrong
owner keys, bounded/strict payload validation, explicit consent version, duplicate
idempotency, stable platform identity, separate Android/Windows aggregation and
private no-store report responses. Database checks confirmed hashed installation
identities and exactly six daily flags for the two initial fixtures. A subsequent
test of the actual native HTTP client also passed opt-in delivery and opt-out.
No synthetic event was sent to production. Retention and rate-limit failure
behavior passed the Node suite.

## Public boundaries

- Sharing remains off until explicitly enabled; no recordings, audio, messages,
  API keys or hardware identifiers are included.
- Reporting measures opted-in installations, not all customers or unique people.
  Downloads are separate unlinked starts; historical requests remain separate.
- Transfers and UDP forwarding remain beta. No new 107-session migration or
  long-duration Doze/full-real-race endurance result is claimed.
- Windows remains an explicitly disclosed unsigned direct download; the trusted
  signature requirement for GitHub public release attachments is unchanged.
- Android supports the tested 4 KiB memory-page configurations, not 16 KiB pages.
- The local browser runtime failed initialization. DOM, emulator and HTTP checks
  do not imply a local Windows browser visual pass.

## Rollback baseline (before publication)

- Worker version: `7db7138e-e243-4f0b-90ac-b5a0c99b9952`.
- Pages deployment: `b943dd2b-3fec-4660-976b-c09815d0f7b8`.
- Retained Windows 4.10.1 R2 object:
  `releases/windows/4.10.1/PitWall-Setup-64c804f0.exe`.
- Retained Android R2 object: `YourPitBox-4.10.1-android.14.apk`.
- D1 pre-migration bookmark:
  `000002ab-00000000-000050e9-8ffb387e5630bb6eeabcd798f7cc3aeb`.
  A routine app rollback does not require database rollback: the migration is
  additive, and rewinding customer data after new activity would be destructive.

**4.10.3 is withheld.** Windows rerun
[35288254703](https://github.com/scottsart1/Pit-box/actions/runs/35288254703)
passed all gates after the pacing correction, but the additional physical Android
tests below exposed another shared-engine bottleneck. Version 4.10.4/revision 17
contains its fix and must pass new packaged/device tests before publication.

### Additional physical Android full-field failure retained

An extra 100-second, 20-car Wi-Fi test while the tablet was screen-off failed:
18,604 packets sent, 15,566 observed received, 15,565 parsed in the final network
sample; the separately sampled recorder counter was 15,572. There were four
3-second health timeouts late in the run. These snapshots were not atomic and
are not evidence that more packets were recorded than delivered. No internal
queue-drop or write-error counter was raised. Offered load was 176.58 packets/s.
The evidence remains at `qa-candidate-4.10.3/android/full-field.json`.

Read-only Android diagnostics then showed `mState=IDLE`, `mScreenOn=false`,
`mForceIdle=false`, an active foreground service and a held partial wake lock.
The QA package was not battery-exempt. A wake-key request did not bring the
device out of that state, so the subsequent run labelled `awake` is **not** an
awake-device test. Any result is interpreted with that limitation, not relabelled
as foreground evidence. No PIN was guessed or lock screen bypassed.

Android's [Doze documentation](https://developer.android.com/training/monitoring-device-state/doze-standby)
describes network suspension and ignored wake locks in this mode. A bounded,
temporary exemption for the QA package is being used to separate this platform
restriction from engine responsiveness; it does not establish unrestricted
screen-off reliability. The website now explicitly recommends keeping the app
visible and screen awake during racing, with background reception best-effort.

With the temporary exemption, all health requests stayed within 3 seconds, but
only 17,608/18,604 packets arrived/parsed/were recorded. The live kernel socket
entry for port 20787 showed exactly **996 receive-buffer drops**, matching the
missing datagrams. Internal application queue-drop counters remained zero.
The temporary exemption was removed after testing; the QA app was stopped.

Code inspection and a synthetic 60,000-sample CPU profile identified the routine
`/api/health` call to `SessionAssembler.quality_report`: it repeatedly rescanned
all retained samples, consuming about 1.2 seconds under cProfile (over three
million calls). The new bounded `health_summary` returns detached counters and
session metadata, explicitly marking detailed groups omitted. Full quality
calculation remains available in the assembler, unchanged. Health polling must
not be allowed to starve reception. New tests forbid scanning field samples from
the health path and check metadata fidelity, detached counters and closed state.
