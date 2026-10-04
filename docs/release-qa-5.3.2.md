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

External live diagnostics are retained under
`LOCALAPPDATA/YourPitBoxRelease/5.3.2/live-radio`. No recordings or credentials
are checked into this repository.

## Regression and artifact results

Local full regression: **2,171 passed, two skipped**, with one stale website
copy assertion subsequently corrected. The website/release subset then passed
**89 tests**. Later compound-switch and flashback fixes passed **40 focused
tests**. JavaScript and Worker regressions passed **155 tests**. Final CI runs,
signed artifact verification and publication are pending.

Drive/Strategy checks passed at three viewports, including retained critical
warnings and compound evidence. Setup checks passed at three viewports with all
20 values, optional source details, unavailable wet clearing, 44px footer and
navigation into Connection diagnostics. Strategy layout checks covered 20 cases
across ten viewport sizes. The native emulator suite adds installed WebView
interaction and DocumentsUI save verification; its results must be recorded
separately from browser acceptance.

The prior release remains documented separately in
[5.3.1 verification](release-qa-5.3.1.md).
