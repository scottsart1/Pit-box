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

### Private download reporting

Apply `migrations/0005_download_counts.sql` before deploying the updated Worker.
The migration adds only `download_daily`; it does not rewrite licences, reviews
or subscriptions. Existing downloads keep working if a counter write fails.

The `/installer` and `/android` routes increment an atomic daily platform total
after R2 supplies an HTTP 200 full-file GET response. HEAD, failed requests,
prefetch and all Range requests are excluded. Range-only download managers are
therefore not counted. Repeated full GETs and automated requests may count again;
these are **download starts**, not completed downloads, installs or unique people.
Legacy code-gated `/file` and website page views are not included.

Only UTC day, platform, count and first/last start timestamps are stored. No
download cookies, visitor IDs, IP addresses, emails or user-agent logs are added.
There is no backfill: counts start when the updated Worker is deployed. Cache
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

The table is only ever read by hand (`wrangler d1 execute pitwall-licenses
--remote --command "SELECT email, created_at FROM subscribers"`); nothing
sends mail automatically.

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
