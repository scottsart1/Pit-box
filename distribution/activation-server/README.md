# Pit Wall activation server

A single Cloudflare Worker + D1 database. Its only job is to atomically claim a
code for a device at first activation and return the code's pre-signed
entitlement. It holds no private key and signs nothing.

## Deploy (free tier)

```bash
npm install -g wrangler
wrangler login

# 1. Create the D1 database, then paste its id into wrangler.toml
wrangler d1 create pitwall-licenses

# 2. Create the schema
wrangler d1 execute pitwall-licenses --file schema.sql

# 3. Seed a batch of codes (generated offline; seed file is gitignored)
wrangler d1 execute pitwall-licenses --file ../ledger/seed_codes_<stamp>.sql

# 4. Ship it
wrangler deploy
```

The deployed URL (e.g. `https://pitwall-activation.<you>.workers.dev/activate`)
is what the packaged app's first-run screen calls.

## Contract

`POST /activate` with `{ "code": "PITW-...", "device_hash": "<64 hex>" }`

- `200 { entitlement, signature }` — claimed for this device (or re-activation
  on the same device).
- `404 { code: "code_not_found" }` — unknown code.
- `409 { code: "code_already_claimed" }` — used on another device.
- `400 { code: "bad_request" }` — malformed input.

`GET /health` → `200 { ok: true }`.

## Cost

At hobby volume this stays inside Cloudflare's free tier (Workers: 100k
requests/day; D1: millions of reads, 100k writes/day). Each sale is one write.
