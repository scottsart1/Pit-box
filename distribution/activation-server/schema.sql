-- Activation database (Cloudflare D1 / SQLite).
--
-- One row per minted code. The pre-signed entitlement lives here so the app
-- can fetch it at activation and then verify it OFFLINE against the embedded
-- public key. The server holds signatures but NEVER the private key, so a
-- compromised database cannot forge new valid codes.
--
-- Single global use is enforced by an atomic conditional UPDATE on `claimed`
-- (see worker.js): UPDATE ... WHERE code_id=? AND claimed=0. D1 is strongly
-- consistent per database, so two simultaneous activations cannot both win.

CREATE TABLE IF NOT EXISTS codes (
  code_id          TEXT PRIMARY KEY,
  entitlement_json TEXT NOT NULL,
  signature        TEXT NOT NULL,      -- base64 Ed25519 signature
  claimed          INTEGER NOT NULL DEFAULT 0,
  claimed_device   TEXT,               -- SHA-256 device hash of the first claim
  claimed_at       TEXT,               -- ISO-8601 UTC
  -- Set by the daily ledger sync from the workbook's Status column (see
  -- distribution/tools/sync_ledger_status.py). A disabled code is refused for
  -- activation, re-activation AND download; without it a Replaced code's
  -- original device could re-activate forever. Existing databases get these
  -- via migrations/0001_disabled_codes.sql.
  disabled         INTEGER NOT NULL DEFAULT 0,
  disabled_reason  TEXT                -- e.g. "ledger status: Replaced"
);

CREATE INDEX IF NOT EXISTS idx_codes_claimed ON codes (claimed);

-- Reviews posted from the website. Shown only once approved by hand; the
-- email is for a reply and never published. Existing databases get this via
-- migrations/0004_reviews.sql, which also documents how to moderate.
CREATE TABLE IF NOT EXISTS reviews (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  name         TEXT NOT NULL,
  rating       INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
  body         TEXT NOT NULL,
  email        TEXT,
  created_at   TEXT NOT NULL,
  address_hash TEXT,               -- truncated one-way hash, for the daily cap
  approved     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_reviews_approved ON reviews (approved, created_at);
