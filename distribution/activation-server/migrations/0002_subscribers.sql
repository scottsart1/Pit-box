-- Update-notification list for the free edition.
--
-- The site's Download button offers an optional email field; a submitted
-- address lands here so the owner can mail buyers-turned-downloaders about
-- new releases. Nothing else is stored with it: no name, no IP, no device.
-- Duplicates are ignored by the Worker (INSERT OR IGNORE), and an address is
-- removed by hand on request.
--
-- Idempotent on purpose so the release script can apply it every time:
--   cd distribution/activation-server
--   npx wrangler d1 execute pitwall-licenses --remote --file migrations/0002_subscribers.sql
CREATE TABLE IF NOT EXISTS subscribers (
  email      TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  source     TEXT
);
