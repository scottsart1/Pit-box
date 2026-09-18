-- Release metadata is public; recipient and send state are private.
-- No backfill, automatic campaign or message is created by this migration.
CREATE TABLE IF NOT EXISTS app_releases (
  id TEXT PRIMARY KEY,
  platform TEXT NOT NULL CHECK(platform IN ('windows', 'android')),
  version TEXT NOT NULL,
  sort_key TEXT NOT NULL,
  artifact_key TEXT NOT NULL,
  size INTEGER NOT NULL CHECK(size > 0),
  sha256 TEXT NOT NULL,
  notes TEXT NOT NULL,
  published_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS release_channels (
  platform TEXT PRIMARY KEY CHECK(platform IN ('windows', 'android')),
  release_id TEXT NOT NULL REFERENCES app_releases(id),
  sort_key TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS release_email_preferences (
  email TEXT PRIMARY KEY,
  token TEXT NOT NULL UNIQUE,
  unsubscribed_at TEXT
);
CREATE TABLE IF NOT EXISTS release_campaigns (
  release_id TEXT PRIMARY KEY REFERENCES app_releases(id),
  created_at TEXT NOT NULL,
  nonce TEXT NOT NULL,
  sender TEXT NOT NULL,
  footer TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS release_deliveries (
  id TEXT PRIMARY KEY,
  release_id TEXT NOT NULL REFERENCES app_releases(id),
  email TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','sending','retry','accepted','failed','uncertain','suppressed')),
  first_attempt_ms INTEGER,
  lease_until_ms INTEGER,
  payload_json TEXT,
  provider_id TEXT,
  UNIQUE(release_id, email)
);
CREATE INDEX IF NOT EXISTS idx_release_delivery_state ON release_deliveries(state, lease_until_ms);
CREATE INDEX IF NOT EXISTS idx_release_delivery_release ON release_deliveries(release_id, state);
