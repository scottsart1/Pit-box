-- Site/Worker settings, read on demand by src/worker.js::readSetting.
--
-- installer_needs_code: "1" while the installer in R2 is a build from before
-- the free edition, which still asks for an activation code on first start.
-- The website then shows the shared code next to the Download button.
-- release_windows.ps1 sets it to "0" straight after uploading a free-edition
-- installer; the default below is for a fresh database.
--
-- universal_code: the one seeded code reserved as that shared code (set by
-- hand when the bridge was switched on; absent means no shared code).
--
-- Safe to apply repeatedly:
--   cd distribution/activation-server
--   npx wrangler d1 execute pitwall-licenses --remote --file migrations/0003_settings.sql
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT OR IGNORE INTO settings (key, value) VALUES ('installer_needs_code', '0');
