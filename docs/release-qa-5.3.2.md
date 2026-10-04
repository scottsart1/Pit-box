# Your Pit Box 5.3.2 release verification

Status: final artifacts are published. Production upgrade, saved-history
reconciliation, served assets and one real text-provider request are verified.
Production native navigation, Settings reachability and visual review passed.
Earlier candidate records below are retained as development history, not
acceptance of the final artifacts.

## Changes under test

- Clearer Setup Lab controls, six groups of settings and optional source details.
- Driver-focused Drive and Strategy summaries with expandable forecast detail.
- Structured radio failure recovery, accurate timeout classification and bounded
  diagnostic history that survives a later successful request.
- Final OpenAI reasoning rounds synthesize existing tool evidence within the
  existing request/time budget instead of requesting and discarding more tools.
- Per-compound sample retention, recorded versus eligible history counts and
  explicit model provenance in strategy details and radio context.
- Wrong-compound live pace fits are withheld; flashbacks clear cached plans.
- Test Engineer supports same-track saved-session comparisons with independent
  run/group selections, notes and exclusions, and guarded asynchronous updates.
- Library session-family filters include numbered/short practice, qualifying
  and race variants; Race includes Sprint. Phone dashboard clipping is fixed.
- Very short session recordings register their exact session parent atomically,
  including immediate shutdown and rapid track changes before periodic saves.
- Field Stints labels count stints correctly; Analysis, Field and trace tabs
  and Library actions have 44px touch targets.

## Live diagnostic investigation

Read-only checks on the Samsung SM-X930 running production
`5.3.1-android.33` confirmed a live race and the transcribe/reason/speak pipeline.
The retained Python logcat interval contained successful OpenAI responses; it
did not contain the original reported failure. No provider settings, session
records or production application lifecycle were changed for diagnosis.

A local failure reproduction identified a misleading retry loop: after two
provider failures, the 20-second circuit cooldown prevented new requests but
the spoken fallback still instructed the driver to repeat the question. The
radio changes address that confirmed recovery defect. A separate tool-loop
reproduction confirmed that a final-round tool request could discard already
collected evidence; the final request now requires an answer using that evidence.
The original live service
failure remains unconfirmed and must not be represented as diagnosed.

The driver reported that dashboard telemetry kept updating during the failures.
The later ADB disconnect is a separate observation, not proof of their cause.

## Tyre evidence scope

Earlier medium samples were truncated when later hard laps filled the global
live sample window. Filtering by compound before applying the window preserves
both. Saved player-lap counts and actual eligible fitting samples are reported
separately, with exclusions and inferred compound sources. Confidence thresholds
are unchanged; a weak future stint can still lower overall plan confidence.
Legacy history is matched by track, and does not establish matching car,
formula or game version. Detailed historical model evidence is available after
a race strategy is computed; practice and qualifying early-return strategies
do not populate that model summary. No production database migration or rewrite
is required.

## Physical background QA

The isolated package `com.yourpitbox.app.qa` was verified as
`5.3.1-android.33-qa`. An empty virtual display was created with own content,
trusted display, own focus, disabled top-focus stealing and content destruction
on removal. Device diagnostics confirmed the flags before launching QA there.
After launch, the production activity retained top focus on display 0 and
its original process. No native taps or synthetic telemetry were sent.

Wireless ADB disconnected before subsequent native checks. A bounded reconnect
attempt timed out. Native Setup Lab, Test Engineer and Android report-export
acceptance remain incomplete. A successful hidden-display launch is not a
native feature pass. Further checks must verify device identity and current
foreground state before continuing.

The tablet later became available for foreground production testing, but USB
ADB still listed no device and the advertised wireless endpoint refused a
connection. Production testing is authorized and remains limited by connection
availability. No new production installation or native pass is claimed.

The user subsequently restarted wireless debugging and the Samsung reconnected
at its newly advertised endpoint. Device model, serial, Android 16 and both
revision-33 packages were reverified. Production was idle with no connected
telemetry. A private pre-update snapshot records **151 sessions and 26,155 lap
rows**, with OpenAI still configured. No synthetic data was sent to production.

The signed private QA revision-34 candidate from Android `37175056672` was
installed in place under `com.yourpitbox.app.qa`. A hardware-pinned read-only
accessibility helper supplies actual window/node bounds; it refuses production
windows. Physical Setup acceptance passed all Stable/Rotation, Race/Quali/Mixed,
six-group/last-value reachability, optional sources, wet clearing and Test
Engineer handoff checks. Physical Test Engineer checks exposed a real nested
lap-list scroll trap which prevented reaching Save groups. The list now allows
vertical scroll chaining; a browser regression reproduced the failure before
the fix and passed at all three viewport sizes afterward. The prior Windows
and Android binaries are superseded by this product change and will be rebuilt.
Diagnostic continuation on the private older APK verified group persistence,
reported traffic exclusion and both comparison modes; it is not a clean full
native acceptance pass. The diagnostic continuation also verified the second
saved Spa session's -0.600 s comparison without changing B's notes/groups.
Android DocumentsUI saved the report to Downloads; binary-safe readback exactly
matched all 6,798 API bytes, SHA-256
`4984754867cb112be5e5acabd0a08ee000cfb361899a47f7c8a7ab244e55712f`.
The final APK still requires a fresh uninterrupted native run with the scroll
fix. Native harness repairs cover settled package-specific trees, scoped drawer
selection, filename EditText identity and binary-safe export readback; 97
focused native and Android project tests pass.

External live diagnostics are retained under
`LOCALAPPDATA/YourPitBoxRelease/5.3.2/live-radio`. No recordings or credentials
are checked into this repository.

## Regression and artifact results

The first Windows candidate [37171956793](https://github.com/scottsart1/Pit-box/actions/runs/37171956793)
passed its full suite, installer lifecycle, 25-lap race and counted UDP gates.
It was superseded before publication by cross-session comparisons and the
broader UI fixes. Its source was `9a223763`.

The corresponding Android run [37171954022](https://github.com/scottsart1/Pit-box/actions/runs/37171954022)
passed **1,930 Python tests**, browser acceptance, JavaScript checks and all APK
builds. Its native test failed before the first Setup dropdown because the
test locator treated the horizontal tab strip as a page viewport. Retained XML
showed the valid visible dropdown; the corrected locator is regression-tested
against that geometry. This interrupted native flow is not a feature pass.
Final native acceptance must use the corrected helper and final source.

Superseded candidate evidence is retained for Windows
[37174930304](https://github.com/scottsart1/Pit-box/actions/runs/37174930304)
at `5a7b9c6df92f5d0a953c1a672101cba90e580461`, and Android
[37175056672](https://github.com/scottsart1/Pit-box/actions/runs/37175056672)
at `8bed2167dd92d89141c8ccbd1ec97ee2c9790140`. The latter changes only the
browser test's Library refresh wait. Application, static assets, Android
project, version and Windows packaging directories are identical between them.
Earlier intermediate runs were cancelled before promotion as the UI audit
identified the remaining stint-count and touch-target fixes.

Windows `37174930304` completed successfully: **2,259 passed, one skipped,
one dependency warning**, in 762.36 seconds. All 11 installed-artifact checks
passed, including the 25-lap classified race, persistence/readback, finalized
capture, integrity, restart and uninstall/data retention. Counted telemetry
sent, received, parsed and recorded **18,604 packets** across 20 cars and 100
simulated seconds, with zero rejected packets, queue drops or write errors and
no sampled request timeouts. Health p95/max were 0.157/1.266 seconds; state
p95/max were 0.234/0.594 seconds. Installed transfer management was verified;
paired history copy and TLS/QR were not tested on the Windows runner because
it had no usable private LAN.

The exact downloaded Windows runtime then passed local UDP and browser checks
for comparisons, groups/notes, Setup and all **72 workspace cases**. It started
in 8.094 seconds and shut down cleanly in 10.609 seconds with exit code zero.
Cold verification found both compared sessions' notes intact, the complete
20-value selected setup intact, and all four clean capture files (7,365 packets)
present under their own catalog/header session IDs. SQLite integrity and
foreign-key checks passed; temporary data and owned processes were removed.
That superseded installer was staged locally and was not publicly promoted:
35,019,409 bytes, SHA-256
`f9c3af4dafac1c385be3e368e7060752f86f934f987b0fc31bb73934a5ec7877`.

The next Android candidate [37173645207](https://github.com/scottsart1/Pit-box/actions/runs/37173645207)
passed **1,973 Python tests** and its earlier browser gates, but stopped in
engineering acceptance because the fixture queried the catalog before its
header-only Monza session was persisted. The fixture now waits for that exact
UID's saved row, retaining the original assertions. No APK/native pass applies
to that run. Its log also exposed a real short-session raw-capture catalog
registration error; that defect is repaired in the next candidate. The
corresponding Windows run was cancelled after its full suite/build, before
artifact promotion, because the source needed that additional repair.

Android `37175056672` passed **1,987 Python tests**, all browser gates and all
three APK builds. Native testing then showed a visible dropdown which stock
UIAutomator omitted from its active-window XML. The CI helper now retrieves
all interactive accessibility windows, preserving tree-derived tap bounds and
emulator/package guards. Java-to-DEX compilation and **74 focused tests** pass;
Android `37176479652` subsequently passed 1,990 Python tests and all browser
checks, but its native summary was only partially tested despite workflow
success: the dedicated hierarchy process crashed because API 36 expected an
initialized main Looper. The helper now prepares it and treats incomplete,
malformed or empty promised XML as a hard failure. That run is not release
acceptance. The new native runtime run must still complete. The equivalent hardware-pinned
physical helper has already exercised the complete Setup flow successfully.

The capture repair passed **69 focused tests**, including real UDP immediate
shutdown, rapid A-to-B-to-A restart, metadata preservation, recovery, deferred
rotation and transaction rollback. Session identity and observed context are
frozen at the recording boundary. A missing parent is inserted in the same
transaction as its raw capture, marked incomplete, and never overwrites an
existing richer row. When the boundary contains no Session metadata, the
fallback preserves unknown values rather than copying another circuit's state.

The cross-session UDP fixture records separate Spa Practice 1/2 sessions,
verifies five matching pairs with the expected **-0.600 s** sector-two gain,
then switches to Monza and rejects late packets from both retired sessions.
Focused tests cover session/group ownership, notes and exclusions, mode/track
mismatch, material layout differences over 15 m, unknown compatibility and
delayed UI responses. Game version, car/formula and car performance remain unverified comparison
limits even when laps can be matched. No existing recording is modified by
these fixtures.

Local full regression: **2,171 passed, two skipped**, with one stale website
copy assertion subsequently corrected. The website/release subset then passed
**89 tests**. Later compound-switch and flashback fixes passed **40 focused
tests**. JavaScript and Worker regressions passed **155 tests**. Final CI runs,
signed artifact verification and publication are pending.

The subsequent full cross-session regression run passed **2,230 tests**, with
two platform skips and one dependency deprecation warning, in 909.58 seconds.
Additional layout/compatibility guards passed their focused suites. The final
combined source must still pass CI before artifact publication.

Fresh browser acceptance passed all three engineering, groups/notes and Setup
viewport cases plus **69 all-workspace cases** at 1280, 800 and 390px widths,
with no page exceptions or HTTP 5xx responses. Coverage includes recorded-data
navigation, back/forward, Settings save/reload and padded toggle activation,
Driver Dashboard controls/persistence and frame geometry, Library filtering,
Session Review/Field/Test Engineer handoffs, lap playback and cancelling Quit.
The same complete UDP/browser acceptance passed again after the short-session
capture repair, including all 69 workspace cases. Its server log has one
Windows asyncio connection-reset callback from a closed connection; browser
pages reported no exceptions or HTTP 5xx and no capture-registration error.
Final workspace acceptance passed **72 cases** across the same three widths,
including delayed first-use consent, the corrected stint count, 44px Analysis,
Field and trace tabs and Library actions. No page exceptions or HTTP 5xx were
reported. The native helper's popup/viewport cases passed **38 focused tests**.

Drive/Strategy checks passed at three viewports, including retained critical
warnings and compound evidence. Setup checks passed at three viewports with all
20 values, optional source details, unavailable wet clearing, 44px footer and
navigation into Connection diagnostics. Strategy layout checks covered 20 cases
across ten viewport sizes. The native emulator suite adds installed WebView
interaction and DocumentsUI save verification; its results must be recorded
separately from browser acceptance.

The prior release remains documented separately in
[5.3.1 verification](release-qa-5.3.1.md).


## Final tablet review follow-up

The physical review reached all seven workspaces and all 21 Settings controls
without changing Settings. The private QA app retained 4,581 log lines with no
matched fatal exception, traceback, database-lock or error patterns. This is
bounded evidence, not proof that no fault can occur later.

Candidate `502ec3edbf38df695fb08cb51584deb3e46bd766` passed Windows
[37178136743](https://github.com/scottsart1/Pit-box/actions/runs/37178136743):
2,285 tests passed, one skipped, one warning, plus installed lifecycle and
counted 20-car telemetry checks. All 18,604 sent packets were received, parsed
and recorded with zero drops/write errors. The local staging pipeline was
stopped before retrieval because the subsequent tablet review found another
product defect; this installer is superseded and was not promoted.

Its Android [37178133058](https://github.com/scottsart1/Pit-box/actions/runs/37178133058)
passed 2,013 tests, all browser suites and complete native Setup, groups,
notes, both comparison modes, cross-session B and DocumentsUI export. The
6,796-byte exported report exactly matched API bytes; the crash log was empty.
This validates the repaired native harness. The isolated signed QA package was
installed on the Samsung to verify physical scrolling; production PID 27140
remained unchanged. This candidate is also superseded by the Drive fix below.

After the fixture changed from Spa to Monza, Drive still displayed the earlier
green `Hold P2` objective above an unavailable-position message. The objective
headline/tone now clear on unavailable or failed responses. Objective, race-flow
and rival requests are scoped to the current session/track/epoch and request;
old responses and errors cannot restore another session's values. Their cached
lap/sector keys reset on a session change, including a same-lap transition.
The targeted suite passed 89 Node tests (21 new Objective/race-flow/rival
cases) and six Python UI contracts. Browser tests at 1280, 800 and 390px
reproduced the old headline and delayed replies before the patch, then verified
the corrected results with no page exceptions. The corrected source still
needs fresh final builds and artifact validation.

The physical `502ec3e` candidate passed Setup, ordinary lap-list scrolling to
Save groups, persisted custom groups, notes, strict/descriptive comparisons and
cross-session B. Its export check exposed transient zero-sized accessibility
page bounds: the harness now waits for valid page geometry before accepting a
child control. The subsequent Android DocumentsUI export passed, with 6,798
bytes exactly matching the API report (SHA-256
`9af40ca347ece3523358c853a5ac6098523fe2cc39fbf8038871d4da8af90a6c`).
The geometry guard and Android project suites passed 99 focused tests. Final
artifact acceptance must still repeat the complete flow without interruption.

Windows [37179332829](https://github.com/scottsart1/Pit-box/actions/runs/37179332829)
stopped before packaging: 2,284 tests passed, one failed and one skipped. The
failure exposed a clock precision edge: an exact 20-second cooldown could be
represented as `20.000000000000004` and announced as 21 seconds. Speech now
normalizes sub-microsecond floating-point noise before rounding upward; genuine
fractional waits still round up. Deterministic clock and recovery coverage
reproduced the defect before repair, then 82 provider/radio tests passed.
Provider suppression remained correct throughout. Both final artifacts need
rebuilding with this repair; no failed candidate was promoted.

## Final packaged candidates

Both final builds use `79333ab6a2e464ef1cb8cd59bc216ccb6876501d`.
Windows [37180166206](https://github.com/scottsart1/Pit-box/actions/runs/37180166206)
passed **2,294 tests**, one platform skip and one dependency warning. Its 11
installed-artifact checks include a 25-lap run, final classification, graceful
shutdown, restart, database integrity and uninstall preserving data. The
separate 20-car, 100-second counted test received, parsed and recorded every one
of **18,604** emitted packets, with zero rejected packets, queue drops, write
errors or request timeouts. State response p95/max was 0.203/0.360 seconds;
health response p95/max was 0.204/1.328 seconds.

The downloaded frozen Windows executable also passed Setup, grouping/notes,
comparisons and all **72 workspace cases** across three viewport sizes. Saved
groups, notes and setup values survived graceful shutdown; SQLite integrity,
recording ownership and clean recording footers passed. Its four local capture
files contained zero packets, so that local run is **not** evidence of nonempty
raw recording. The local cause is unconfirmed; CI's counted test above supplies
the packet-write proof. The local disk had less than the normal 2 GiB recording
threshold; that guard was not changed. CI had no usable private LAN, so its
transfer management checks do not establish paired TLS/QR history transfer.

Android [37180163462](https://github.com/scottsart1/Pit-box/actions/runs/37180163462)
passed **2,022 tests**, one dependency warning, all browser suites and full
native Setup, groups, notes, strict/descriptive and cross-session comparisons.
DocumentsUI saved a 6,796-byte report exactly matching the API response (SHA-256
`2c4b8c16da84e5226fcd97ee4f77c7e74b4ebdaa1a2834b6baa754aac01a5312`).
The crash log was empty, with no unclosed SQLite warnings. The runtime archive
digest matched GitHub's artifact digest. Both production and QA APKs were then
signed with the existing release certificate and verified for package identity,
signature and 16 KiB alignment.

Physical final-candidate inspection found that installing successive private QA
APKs with the same version name/code retains the previously extracted dashboard.
Both final APKs embed the exact committed dashboard; the initial QA HTTP server
still served an earlier candidate's dashboard. Those same-revision physical
checks therefore do not certify the final dashboard. The isolated synthetic QA
data was reset and all 29 static files plus the dashboard route matched the
final APK exactly before final physical acceptance. Production's 5.3.1 revision 33 to 5.3.2 revision 34 upgrade changes
the extraction stamp and must independently verify its served assets afterward.

The fresh Samsung QA installation passed Setup references and all 20 values,
optional source disclosure, wet clearing and handoff, custom lap groups, saved
traffic exclusions, strict/descriptive comparisons and cross-session B. The
same-session result was -0.300 s and the cross-session result -0.600 s. Android
Downloads saved all 6,796 report bytes exactly (SHA-256
`174eef1f36443c1052ea393fbfad3ec3df360d3b390f0a54b8c483aa4835e75b`).
The actual Monza view cleared the prior objective. All seven workspaces and all
21 Settings labels were reachable, with Settings unchanged. Independent visual
review of 13 settled screenshots found no material clipping or access blocker.
Physical coverage was assembled after harness retries for a transient Samsung
keyboard-only accessibility tree and an already-open details section; it is
not represented as one uninterrupted fresh run. The final CI emulator flow did
complete uninterrupted. Production received no synthetic telemetry.

Final artifacts:

| Platform | File | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| Windows | `PitWall-Setup.exe` | 35,012,692 | `6893890d90799b167ef6fb8e1c61c2518afbc0a023871a67fff627c74e5b14bc` |
| Android | `YourPitBox-5.3.2-android.34.apk` | 73,161,097 | `41f5cb7ffc8eff59893bb8098fbc6561be32fc30fed02ad005ebb9782afc5107` |

## Production upgrade and history preservation

The Samsung production package was upgraded in place from 5.3.1 revision 33 to
**5.3.2 revision 34**, using the signed APK from Android `37180163462` at source
`79333ab6a2e464ef1cb8cd59bc216ccb6876501d`. The earlier backend completed its
dashboard-requested shutdown before installation. The installed APK digest
matches the final Android artifact above. All **30 served routes** (29 static
files and the dashboard) then matched the signed APK's bytes, including the
session-response guards. No synthetic telemetry was sent to production.

The before/after comparison retained all **151 session IDs and 26,155 complete
lap rows**, including each row's session scope and duplicate multiplicity.
Canonical per-session multisets, the compatibility lap dictionary and provider
readiness matched. The strict metadata comparison detected three changed fields
across two sessions; these were retained and reconciled rather than ignored:

- A zero-lap idle recording became incomplete and received an end timestamp.
  The startup log records one stale session recovery at that exact timestamp.
- A completed practice's reported storage grew by **29,380,496 bytes**, exactly
  matching its sole finalized raw capture: **188,595 packets**, clean closure
  and no recovery flag. Its 98 lap rows and trace metadata were unchanged, all
  98 trace manifests remained ready, and its status remained complete.

All other session metadata, except normal `updated_at` refreshes, was unchanged.
The original snapshots and strict assertions were preserved. This verifies the
exported history and the explained lifecycle changes; it does not claim a
byte-for-byte comparison of every database table and raw file. The private
`production-history-metadata-audit.json` records the exact differences and
receipt digests, and the upgrade receipt records
`passed_with_explained_lifecycle_metadata_changes`.

One real production engineer **text** request returned HTTP 200 in **15.313
seconds** with the answer “No fresh live telemetry. The current track and lap
are unavailable.” The app was disconnected with lap zero, and the reply did
not reuse an earlier circuit. OpenAI diagnostics retained zero failures, no
cooldown and no recent failure entries; Settings were unchanged. This check
does not test the microphone, speech recognition, speech playback or Realtime,
and does not establish the cause or resolution of the earlier intermittent
radio failures.

The final production package also passed native navigation across all **seven
workspaces**. All **21 Settings labels** were reached in three inspection/scroll
iterations, then the app returned to Drive. Settings and provider readiness
remained unchanged; no credentials were edited, and this navigation check
triggered no voice, exports, synthetic telemetry or lifecycle changes. Optional
onboarding was dismissed with Skip setup before the checks. This verifies page
access and label reachability, not every action or live driving behavior.
Independent review of all eight retained screenshots found no material clipping,
stale-context contradiction or readability blocker. The production Setup image
shows its empty state; reference values were exercised separately in QA. The
Driver Dashboard identifies its simulated sample race. A scan of the retained
production process log found no traceback, fatal exception, application-not-
responding or SQLite corruption/locking signatures; this bounded log check does
not establish behavior outside the recorded interval.

## Public distribution verification

The release Worker deployment is
`a6a3a6b7-ca3b-4fcf-b402-5f0cd964439f`. The website deployment from `fcacbeb` is
[a322b018.pitwall-2k7.pages.dev](https://a322b018.pitwall-2k7.pages.dev).
Both production download responses were read in full and matched the final
Windows and Android sizes and SHA-256 digests above. The installer endpoint
also reported that no download code was required.

All **19 public website routes** passed comparison with the built site,
including the guide, download flow, Driver Dashboard and new Setup screenshot.
HTML comparisons accounted for Cloudflare's email-address transformation.
All **six update checks** passed: Windows and Android installations at 5.3.0
and 5.3.1 were offered 5.3.2, while 5.3.2 installations reported no newer
release. Update metadata matched the published artifact sizes and digests.
Private `production-downloads.json`, `production-site.json` and
`production-updates.json` receipts retain these results.
