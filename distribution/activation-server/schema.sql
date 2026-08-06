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
  claimed_at       TEXT                -- ISO-8601 UTC
);

CREATE INDEX IF NOT EXISTS idx_codes_claimed ON codes (claimed);
