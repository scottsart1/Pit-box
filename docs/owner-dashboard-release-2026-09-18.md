# Private owner dashboard — production receipt

Published 2026-09-18 at approximately 01:03 UTC (September 17 evening in
America/New_York). Dashboard: <https://yourpitbox.com/owner.html>.

## Scope and source

- Dashboard implementation: `9b4e52fc1d7fbb7d2e1bddb4071de74765836232`.
- Previously verified 4.10.4 release: `efc27e56f1a495247c1d34d0a8c62d2c52315a84`.
- Both commits pushed to GitHub `main` using ordinary fast-forward pushes.
- No application, consent/default, Android, desktop or binary changes. No new
  app release, tracking fields, website-visitor tracking or schema migration.
- Existing subscriber records are read only. No test subscribers, marketing
  messages or synthetic production usage events were created.

## Delivered

The dashboard combines live Windows/Android download starts, a separate recovered
historical file-request snapshot, newsletter subscribers, signup sources, opted-in
app activity, feature use, app versions and exact-day retention. These independent
signals are not presented as a linked person-level conversion funnel. Unavailable
data is distinguished from real zero counts.

Copy all emails loads every page of a bounded snapshot, deduplicates and verifies
completeness before writing newline-separated addresses. It does not merely copy
the visible table. Clipboard refusal provides a selectable plain-text fallback.
Export is limited to 50,000 subscribers per browser operation.

Existing collection operates independently of this page. The page refreshes every
60 seconds while visible and unlocked, and locks after 15 minutes without user
interaction or 5 minutes hidden. Locking clears page data and pending requests;
it does not clear explicitly copied system-clipboard content.

## Access boundary

- A dedicated random 256-bit `OWNER_DASHBOARD_TOKEN` is required server-side on
  every owner API read. The older aggregate-report key cannot read subscriber
  addresses. The private owner key grants no write access.
- The key and data remain in page memory, not URLs or browser storage. Requests
  and responses are non-cacheable. Page headers prevent framing and third-party
  scripts; unlisted/noindex status is not relied on for authentication.
- The owner credential and instructions are in
  `%LOCALAPPDATA%/YourPitBoxRelease/owner-dashboard/dashboard-access.txt` on the
  owner's laptop. The directory has explicit ACLs for that Windows user and
  SYSTEM only; a DPAPI-encrypted copy is also retained there. No key or subscriber
  addresses are included in Git or this receipt.
- This is bearer-key authentication, not identity-provider SSO. Anyone given the
  key gains the same read access; keep it private and rotate it if disclosed.

## Production versions

- Worker `pitwall-activation`: `e8c0a0b4-e112-4237-9a4d-b99adc4c3a59`, verified
  serving 100% of production traffic through the Cloudflare metadata API.
- Pages project `pitwall`, production branch `main`:
  `02435594-d1d3-46b2-a22d-d340691f4525`.
- Immutable Pages URL: <https://02435594.pitwall-2k7.pages.dev>.
- Owner limiter namespace `4103`; existing usage limiter `4102` is unchanged.
- Existing D1 database and R2 bucket are reused; no new cloud data stores.

## Verification

- Node Worker/page-logic suite: **59 passed**.
- Python website suite: **77 passed**.
- Website build validation, JavaScript syntax and Git whitespace checks passed.
- Production owner overview returned complete genuine data with private headers.
- All subscriber pages verified in memory with unique addresses, consistent
  snapshot/count and only the intended email/date/source fields. No addresses or
  credentials were saved in verification evidence.
- Missing, incorrect and aggregate-only keys returned 401 on both owner routes;
  an unauthorized browser origin returned 403.
- Served owner JavaScript/CSS matched the built assets exactly. Canonical owner
  page security headers were verified over HTTPS.
- Historical download counts matched the predeployment baseline exactly; live
  totals did not decrease. Eleven existing website pages/assets still matched
  their intended contents, allowing known Cloudflare HTML injection.
- Unchanged public download metadata: Windows HTTP 200 / 34,684,777 bytes;
  Android HTTP 200 / 72,704,641 bytes. Windows still requires no activation code.
- Verification used unit/VM tests and production HTTP checks, not a full visual
  browser click-through. No claim of visual browser verification is made.

Local evidence remains outside Git under `owner-dashboard-release-20260918`
(`before.json`, `after.json`) and `website-deployment-evidence`
(`verification-powershell-20260918T010358Z.json`) in the working session directory.

## Recovery

Before this change, production Pages was
`4882dbb0-4a70-4e25-8621-1a318744f9e7` and the Worker version was
`53941911-5257-4f73-a053-4566b1723bf6`. The intermediate Worker version
`224a3c7f-6d75-4872-b763-918f2eb5ce26` contains the same prior code with the new
owner secret provisioned, before owner routes were deployed. These are reference
points for rollback; alternatively redeploy the previous release source. Restore
both website and Worker code together. No database rollback or deletion of
subscriber/download/usage data is necessary. The owner dashboard becomes
unavailable while the established public downloads and aggregate reports remain.
