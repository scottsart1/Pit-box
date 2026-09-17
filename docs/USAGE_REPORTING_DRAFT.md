# Optional usage reporting — local draft, not deployed

Status: 17 September 2026. The owner has resumed candidate builds and testing.
Development-branch CI artifacts and an isolated tablet QA package are in scope;
public releases, Cloudflare deployments and database migrations remain paused.
Candidate version: Windows/shared engine 4.10.2, Android revision 15. The released
download artifacts remain unchanged. No production analytics events were
submitted during these tests. Never overwrite or uninstall the owner's
differently signed legacy Android apps to test the candidate.

## User choice and scope

- Sharing is **off until explicitly chosen**, including on existing installs.
- The first-run choice and Settings explain basic usage reporting. Declining
  does not limit functionality. Neither option is preselected.
- A settings write must succeed before enabling collection. Invalid/missing
  consent files fail closed. The preference is installation-local and not
  included in history transfers.
- Turning sharing off clears the local queue and random ID. Enabling again
  creates a new ID. Previously received records expire under the retention
  policy; opt-out does not promise retroactive deletion.
- No microphone data, transcripts, race/session IDs, telemetry values, API keys,
  names, emails, hardware identifiers or history files enter reporting payloads.
- A brief choice plus plain-language privacy details replaces the old,
  incompatible “nothing is sent to the developer” promise in draft terms.

## What is counted

One flag per installation, UTC date and event (not every interaction):

| Event | Meaning |
| --- | --- |
| `app_started` | Started after opting in, or sharing was explicitly enabled |
| `app_used` | Focused, visible UI with recent interaction; or qualifying driving |
| `racing` | 60 consecutive seconds of fresh, advancing, unpaused driving telemetry above 5 km/h |
| `engineer` | A nonempty engineer reply was added to local radio history |
| `voice` | A voice command/transcript reached the radio processing path |
| `analysis` | A lap comparison was successfully created or loaded |
| `transfer` | A history pull was accepted; **not** proof of a completed import |

An app launch alone is not active use. Paused, stationary, stale and frozen
telemetry are not qualifying driving. These are usage signals, not certified
race completions or proof a human was driving rather than using assists.

Downloads remain unlinked aggregate file-start counts. The owner usage report
shows them beside first reported use, active installations, driving and feature
use, but does not divide unlike rows into a fictitious conversion rate.
Installations are not people: Windows plus Android, reinstalls or resetting
sharing can count more than once. Older builds and declined sharing are not
observable. No historical usage backfill is possible from download records.

Retention is exact-day active return on UTC day 1/7/30 after first reported use,
among eligible cohorts first seen within the 90-day retained window. Fresh
cohorts are shown as not yet eligible, not failed returns. Queued offline events
may change recent counts up to 7 days later.

## Implementation and boundaries

- Shared Python engine: `src/pitwall/usage_reporting.py` and `api/usage.py`.
- Bounded, deduplicated outbox in `PitWallData/usage-reporting.json`: at most
  49 daily flags, batches of 28, local expiry after 7 days.
- Network and persistence run asynchronously outside the telemetry parser;
  HTTP requests time out, retries back off to at most once per 15 minutes.
- No usage network client/request is created while sharing is off.
- Local controls require the device's own same-origin loopback dashboard.
- Worker `POST /usage`: strict schema/allowlist, 8 KB streaming body limit,
  consent version, UUIDv4 only, dates limited to current/past 6 UTC days,
  transactional storage and idempotent daily primary keys.
- The server hashes the random installation ID before storing it. No per-ID
  records are exposed by `GET /usage-stats`, which uses the existing private
  download-report bearer secret, no-store responses and restricted CORS.
- Cloudflare rate limits are an approximate, per-edge abuse safeguard, not
  authentication or accounting. Coarse connection rate limiting is 300 requests
  per minute; shared connections can still be limited. Valid-looking events can
  be forged, so reporting is not suitable for billing or fraud proof.
- Daily flags expire after 90 calendar days; installation metadata expires
  after 90 days without activity. Daily cleanup is scheduled at 03:17 UTC.
  Reports themselves filter expired records even if scheduled cleanup is late.
- Website `usage-stats.html` uses the existing site's style and the existing
  owner key, kept only in memory and erased on lock/page exit. It links back to
  the unchanged historical download report. Failures display unavailable, not 0.

## Local verification

Run from the repository with the existing QA Python environment and Node 24:

```text
python -m pytest tests/test_usage_reporting.py -q -p no:cacheprovider
node --test distribution/activation-server/tests/*.mjs
node tools/usage-ui-smoke.cjs
python tools/usage-reporting-smoke.py
python -m distribution.website.build_site --check
```

The DOM smoke uses the existing `qa-ui/node_modules` through `NODE_PATH`.
All usage requests in tests go to mocks or an in-memory loopback receiver.
The real-app smoke uses temporary data, isolated ports, no keys and no audio;
it does not start or change the installed desktop app or touch saved sessions.

Current results:

- 265 Python tests passed; 1 pre-existing platform-dependent test skipped.
  Covers reporting plus state, startup/settings, analysis, voice/realtime,
  history/peer transfer, Android project/network and website regressions.
- 47 JavaScript tests passed, including real in-memory SQLite ingestion,
  idempotency, validation, authentication, retention and both owner reports.
- Consent-control DOM smoke passed default-off, decline, enable, disable and
  failed-save rollback behavior.
- Real shared-engine laptop smoke passed startup, default-off, explicit opt-in,
  asynchronous delivery to a loopback receiver, payload checks, opt-out clearing
  the ID, restart with opt-out retained and graceful shutdown. Exactly one
  fixture report was received locally; none went to production.
- Website draft assembled successfully (about 9.4 MB); source validation passed.
  Visual browser QA is still pending: the
  browser connection could not initialize on this host; no visual pass claimed.
- The tablet subsequently connected over wireless ADB. Its existing debug app
  passed basic dashboard/settings/background-health checks, but the new draft
  is not installed. No new signed APK or packaged Windows installer has been
  built or installed. See [the September 17 release QA record](release-qa-2026-09-17.md)
  for the complete 1,801-test suite, packaged Windows race/restart checks,
  corrected test oracle, stress-latency caveats and remaining artifact gates.

## When deployment is explicitly resumed

1. Confirm the tablet connection; inspect package/signature/version and preserve history.
   A production-signed package must not overwrite/uninstall a differently signed
   debug package. The existing transfer is paused; do not resume it implicitly.
2. Obtain adequate laptop disk space before packaging. Only about 230 MB was
   free during draft testing; no user recordings or backups were deleted.
3. Complete browser/device UI QA, actual opt-out/restart checks, and full CI.
   Confirm the Cloudflare rate-limit namespace is unused before provisioning it.
4. Allocate new app/Android versions, build trusted artifacts, retain rollback
   versions, sign Android with the existing permanent key. Never expose keys.
5. Apply additive migration `0007_optional_usage.sql`, deploy Worker with its
   rate-limit binding/cleanup schedule and existing report secret. Smoke-test
   using an isolated staging database first. Verify retention and denied access.
6. Publish matching website/privacy copy and artifact references together;
   verify served digests and both download prompts, without altering the old
   counters or presenting historical requests as unique downloads.
7. Install and test on the owner's laptop/tablet without deleting history;
   retain clear rollback steps. New public usage begins only after users opt in.

Worker API reference checked against official documentation:
[rate-limit bindings](https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/)
and [D1 batch transactions](https://developers.cloudflare.com/d1/worker-api/d1-database/).
