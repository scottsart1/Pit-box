-- Public metadata for the in-app update flag. No subscriber/email tables.
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
