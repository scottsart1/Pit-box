-- Additive only; no change to subscribers, licenses or anonymous usage.
CREATE TABLE IF NOT EXISTS app_accounts (
  id TEXT PRIMARY KEY, email_hash TEXT NOT NULL UNIQUE, pin_hash TEXT NOT NULL,
  pin_salt TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS account_devices (
  account_id TEXT NOT NULL REFERENCES app_accounts(id) ON DELETE CASCADE,
  device_hash TEXT NOT NULL, generation INTEGER NOT NULL,
  PRIMARY KEY(account_id, device_hash)
);
CREATE TABLE IF NOT EXISTS account_challenges (
  id TEXT PRIMARY KEY, email_hash TEXT NOT NULL, device_hash TEXT NOT NULL,
  code_hash TEXT NOT NULL, expires_at INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS account_challenge_expiry ON account_challenges(expires_at);
CREATE TABLE IF NOT EXISTS account_limits (id TEXT PRIMARY KEY, count INTEGER NOT NULL, expires_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS account_limit_expiry ON account_limits(expires_at);
CREATE TABLE IF NOT EXISTS account_starts (
  launch_id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES app_accounts(id) ON DELETE CASCADE,
  started_at INTEGER NOT NULL, platform TEXT NOT NULL, version TEXT NOT NULL, consent_version INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS account_starts_time ON account_starts(started_at);
CREATE INDEX IF NOT EXISTS account_starts_account_time ON account_starts(account_id, started_at);
