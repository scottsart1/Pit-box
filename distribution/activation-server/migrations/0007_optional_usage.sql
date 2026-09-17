-- Draft only: no production migration until the owner resumes deployment.
-- No race data, audio, text, emails, addresses or hardware identifiers.
CREATE TABLE IF NOT EXISTS usage_installations (
  installation_hash TEXT PRIMARY KEY,
  platform TEXT NOT NULL CHECK (platform IN ('windows', 'android')),
  version TEXT NOT NULL,
  first_day TEXT NOT NULL,
  last_day TEXT NOT NULL,
  consent_version INTEGER NOT NULL CHECK (consent_version = 1)
);
CREATE INDEX IF NOT EXISTS idx_usage_installations_last_day ON usage_installations(last_day);
CREATE INDEX IF NOT EXISTS idx_usage_installations_first_day ON usage_installations(first_day);

CREATE TABLE IF NOT EXISTS usage_daily (
  installation_hash TEXT NOT NULL REFERENCES usage_installations(installation_hash) ON DELETE CASCADE,
  day TEXT NOT NULL,
  event TEXT NOT NULL CHECK (event IN ('app_started', 'app_used', 'racing', 'engineer', 'voice', 'analysis', 'transfer')),
  PRIMARY KEY (installation_hash, day, event)
);
CREATE INDEX IF NOT EXISTS idx_usage_daily_day_event ON usage_daily(day, event);
