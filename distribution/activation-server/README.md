# Your Pit Box activation server

A single Cloudflare Worker + D1 database + private R2 bucket. Since the free
edition (4.9) its everyday job is to stream the installer to anyone who asks
and to record optional release-news signups. It also still claims activation
codes for installs made under the paid model. It holds no private key and
signs nothing.

## Deploy (free tier)

```bash
npm install -g wrangler
wrangler login

# 1. Create the D1 database, then paste its id into wrangler.toml
wrangler d1 create pitwall-licenses

# 2. Create the schema, then apply the migrations in order
wrangler d1 execute pitwall-licenses --remote --file schema.sql
wrangler d1 execute pitwall-licenses --remote --file migrations/0001_disabled_codes.sql
wrangler d1 execute pitwall-licenses --remote --file migrations/0002_subscribers.sql

# 3. (Paid-model installs only) seed a batch of codes; the seed file is gitignored
wrangler d1 execute pitwall-licenses --remote --file ../ledger/seed_codes_<stamp>.sql

# 4. Ship it
wrangler deploy
```

`release_windows.ps1` at the repository root runs the deploy and the
`0002_subscribers` migration on every release, after the installer has been
uploaded to R2 and before the site is published.

## Contract

### Private owner dashboard

`https://yourpitbox.com/owner.html` combines download starts, separately labelled
historical file requests, newsletter subscribers, opted-in activity, feature use,
app versions and exact-day retention. Collection continues independently of the
page. The page refreshes every minute while visible/unlocked and locks after
15 minutes without interaction or 5 minutes hidden. There is no new app tracking,
website visitor tracking or app release; 4.10.4 and its privacy choice are unchanged.

The read-only `/owner/overview` and `/owner/subscribers` routes require a separate
random `OWNER_DASHBOARD_TOKEN` (at least 32 bytes of entropy), not the aggregate-only
`DOWNLOAD_REPORT_TOKEN`. Reusing the same key is rejected. The old report key cannot
read emails. `OWNER_RATE_LIMITER` is required and fails closed if missing. Keys are
compared using fixed-size SHA-256 digests; query-string keys are never accepted.
Routes allow only GET/OPTIONS, restrict browser origins to the canonical and www
site, and return `private, no-store`. Authentication is server-side on every read;
an unlisted/noindex page is not the security boundary. Anyone possessing the owner
key can access the dashboard: protect and rotate it like a password.

The owner page stores credentials/data only in memory and clears them on lock or
page exit. Its security headers prohibit framing and third-party scripts, including
hosting analytics injection. Private data is never embedded in public HTML. The
password key should be delivered through the owner's protected local credential
file, not committed, printed in logs or sent in a URL.

Subscriber pagination uses integer row cursors (not email addresses in URLs), a
fixed high-water row and bounded pages of at most 200. Copy all loads the full
snapshot, verifies completeness and copies one address per line; a failed page
never silently copies a partial list. The browser export cap is 50,000 addresses.
Clipboard refusal provides a selectable plain-text fallback. Copying sends no
mail, and copied text remains in the system clipboard after the dashboard locks.
Only release-news subscribers are included, not reviewers' contact addresses.

No schema migration is required. Existing download/usage reports and subscriber
rows are unchanged. SQL tests use a disposable in-memory database; production
verification performs read-only checks and does not create fixture subscribers.

Rollback: restore the previous Worker and Pages deployment. The owner page then
becomes unavailable; existing app downloads, reports and user data remain intact.

### Private download reporting

Apply `migrations/0005_download_counts.sql` and `0006_download_history.sql` before
deploying the updated Worker. They add `download_daily` and `download_history`;
they do not rewrite licences, reviews
or subscriptions. Existing downloads keep working if a counter write fails.

The `/installer` and `/android` routes increment an atomic daily platform total
after R2 supplies an HTTP 200 full-file GET response. HEAD, failed requests,
prefetch and all Range requests are excluded. Range-only download managers are
therefore not counted. Repeated full GETs and automated requests may count again;
these are **download starts**, not completed downloads, installs or unique people.
Legacy code-gated `/file` and website page views are not included.

Only UTC day, platform, count and first/last start timestamps are stored. No
download cookies, visitor IDs, IP addresses, emails or user-agent logs are added.
Live counts are not backfilled: they start when the counter is deployed. Cache
control is `private, no-store`, so these routes continue to reach the counter.
Writes run with `ctx.waitUntil`; a storage outage can undercount, not stop files.

`GET /download-stats` requires `Authorization: Bearer <report-key>`. Provision a
random, dedicated 32-byte key as the Worker secret `DOWNLOAD_REPORT_TOKEN` with
`wrangler secret put`; never use a Cloudflare or AI-provider token for this.
The key is read-only and exposes only aggregate counts. Missing/wrong keys get
401, and storage failures get 503 rather than a misleading zero count. Rotate
it by replacing the Worker secret and distributing the new key privately.

The owner page is `https://yourpitbox.com/download-stats.html`. It keeps the key
only in page memory, does not put it in URLs or browser storage, and clears
loaded counts when locked. It shows all-time Windows/Android/combined totals
and a 30-day UTC breakdown. Days before the first recorded start use a dash,
not a reconstructed zero. The report is not indexed; server authorization,
not an unlisted URL, protects the data. Responses are private and non-cacheable.

#### Recovered history (separate metric)

`history` in the authenticated response is either `null` (unavailable, not zero)
or a `historical_file_requests` snapshot with coverage (`from` inclusive, `until`
exclusive), source, recovery date, separate Windows/Android/combined totals and
daily rows. The page displays its entire coverage in a separate section.
The first-screen overview adds history and live starts only as clearly labelled
**mixed-method recorded activity**, not verified downloads or installations.
The source counters and database rows remain unchanged and separately visible.
Missing history leaves the combined overview unavailable, rather than implying
that the live count alone covers all history. Detailed daily tables are collapsed
so the historical summary is not buried beneath 30 rows of live-only data.

Only successful Cloudflare R2 `GetObject` analytics for the actual public
release object keys are included. Exclude uploads, metadata calls, errors,
unrelated objects and versioned release archives. These analytics may be sampled
and may include partial reads, retries, bots, developer reads and verification
downloads. R2 reads are **not** the same metric as the new route-level counter;
never add them to `download_daily` or present them as unique users/installations.

Store the validated aggregate snapshot in `download_history` row `id=1` as
`report_json`. Include `source_sha256` and queried object names in the private
import for provenance; the API returns only its allowlisted aggregate fields.
Use an atomic UPSERT replacing this singleton on re-import, not addition, so a
retry cannot double counts. Keep raw query evidence and generated import SQL
outside the public repository and assets. No credentials or visitor records are
needed in the snapshot. Import timestamps and historical bounds must not overlap
the live counting period; do not imply coverage outside successfully queried data.

The homepage and Android setup guide share the optional release-news prompt.
Android selection downloads `/android` without checking Windows activation
metadata; Windows retains `/installer-info` and `/installer`. Skip/blank email
downloads the selected platform; Cancel/Escape does not. Android signup source
is `website-download-android`; Windows preserves `website-download`. A cancelled
or skipped pending signup cannot later trigger an extra download.

For an account-authenticated alternative, the owner can inspect `download_daily`
directly through Cloudflare D1; never expose the database or account credential.

Implementation references: [Worker waitUntil](https://developers.cloudflare.com/workers/runtime-apis/context/),
[D1 prepared statements](https://developers.cloudflare.com/d1/worker-api/prepared-statements/),
[Worker secrets](https://developers.cloudflare.com/workers/configuration/secrets/).

Rollback: revert the Worker/site to the previous version, leaving the additive
table intact. Existing collected counts are preserved but recording pauses.

### Free edition

`GET /installer` → the installer as `attachment; filename="PitWall-Setup.exe"`,
streamed from the private R2 bucket. `Range` requests are honoured (206), so a
dropped download resumes. `503 { code: "not_configured" }` if the object has
not been uploaded yet.

`POST /subscribe` with `{ "email": "...", "source": "website-download" }`

- `200 { ok: true }` — stored (duplicates are kept once, silently).
- `422 { code: "bad_email" }` — not an email address.
- `503 { code: "not_ready" }` — the `subscribers` table has not been created
  yet. The website treats this as a soft failure and starts the download.

The owner dashboard reads this table through its separately authenticated,
read-only subscriber endpoint. Nothing sends mail automatically.

`GET /installer-info` → `{ "needs_code": bool, "code": "PITW-..." | null }`.
`needs_code` is true while the `settings` row `installer_needs_code` is `"1"`,
meaning the installer in R2 is a build from before the free edition that still
asks for an activation code on first start; `code` is then the shared code
(`settings.universal_code`) the site shows under its Download button.
`POST /activate` with that code returns its entitlement to any device without
claiming it. `release_windows.ps1` sets the flag to `"0"` after uploading a
free-edition installer. See `migrations/0003_settings.sql` and
`HANDOVER.md`, "The bridge".

`GET /reviews` → `{ "reviews": [{ "name", "rating", "body", "created_at" }], "count", "average" }`.
Only reviews with `approved = 1` are returned, newest first, at most 50. The
reply email and the address hash are never returned.

`POST /reviews` with `{ "name", "rating": 1-5, "body", "email"?: "...", "website": "" }`
stores a review with `approved = 0`. `website` is a honeypot: a non-empty
value is dropped silently. At most three reviews per connection per day
(429 after that). Moderation is by hand in D1; `migrations/0004_reviews.sql`
has the queries.

### Paid-model installs

`POST /activate` with `{ "code": "PITW-...", "device_hash": "<64 hex>" }`

- `200 { entitlement, signature }` — claimed for this device (or re-activation
  on the same device).
- `404 { code: "code_not_found" }` — unknown code.
- `409 { code: "code_already_claimed" }` — used on another device.
- `410 { code: "code_retired" }` — retired by the ledger sync.
- `400 { code: "bad_request" }` — malformed input.

`POST /download` with `{ "code": "PITW-..." }` and `GET /file?code=...` are
the code-gated download the site used before 4.9. They still work for anyone
holding a code; the free build never calls them.

`GET /health` → `200 { ok: true }`.

## Cost

At hobby volume this stays inside Cloudflare's free tier (Workers: 100k
requests/day; D1: millions of reads, 100k writes/day; R2: 10 GB stored and
10 GB egress a month, which is roughly 300 installer downloads). Each signup
is one write.
