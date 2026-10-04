# Your Pit Box 5.3.2 release verification

Status: development and verification in progress. This document does not certify
publication, signed artifacts or completed native acceptance yet.

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
