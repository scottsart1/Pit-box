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
