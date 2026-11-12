-- Local profiles use random identifiers. No names, email addresses or PINs.
CREATE TABLE IF NOT EXISTS driver_profiles (
  id TEXT PRIMARY KEY, device_hash TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS profile_sign_ins (
  event_id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES driver_profiles(id) ON DELETE CASCADE,
  occurred_at INTEGER NOT NULL, received_at INTEGER NOT NULL, version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS profile_sign_ins_time ON profile_sign_ins(occurred_at);
CREATE INDEX IF NOT EXISTS profile_sign_ins_account_time ON profile_sign_ins(account_id, occurred_at);
