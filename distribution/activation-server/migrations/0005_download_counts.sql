-- Aggregate successful download starts, not people or completed installs.
-- Additive and safe to apply again. No visitor identifiers or raw events.
CREATE TABLE IF NOT EXISTS download_daily (
  day TEXT NOT NULL,
  platform TEXT NOT NULL CHECK (platform IN ('windows', 'android')),
  starts INTEGER NOT NULL DEFAULT 0 CHECK (starts >= 0),
  first_started_at TEXT NOT NULL,
  last_started_at TEXT NOT NULL,
  PRIMARY KEY (day, platform)
);
