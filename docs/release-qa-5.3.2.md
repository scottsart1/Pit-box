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

The cross-session UDP fixture records separate Spa Practice 1/2 sessions,
verifies five matching pairs with the expected **-0.600 s** sector-two gain,
then switches to Monza and rejects late packets from both retired sessions.
Focused tests cover session/group ownership, notes and exclusions, mode/track
mismatch, material layout differences over 15 m, unknown compatibility and
delayed UI responses. No existing recording is modified by these fixtures.

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

Drive/Strategy checks passed at three viewports, including retained critical
warnings and compound evidence. Setup checks passed at three viewports with all
20 values, optional source details, unavailable wet clearing, 44px footer and
navigation into Connection diagnostics. Strategy layout checks covered 20 cases
across ten viewport sizes. The native emulator suite adds installed WebView
interaction and DocumentsUI save verification; its results must be recorded
separately from browser acceptance.

The prior release remains documented separately in
[5.3.1 verification](release-qa-5.3.1.md).
