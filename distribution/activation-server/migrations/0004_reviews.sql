-- Reviews posted from the website (src/worker.js::handleReviewSubmit).
--
-- Anyone can post one; nothing is shown until the owner has read it and set
-- approved = 1 by hand in D1. The optional email is for a reply and is never
-- returned by GET /reviews. address_hash is a truncated one-way hash of the
-- posting connection's address, kept only to cap how many reviews one
-- connection can post per day; it cannot be turned back into the address.
--
-- To moderate:
--   SELECT id, name, rating, body, email, created_at FROM reviews WHERE approved = 0;
--   UPDATE reviews SET approved = 1 WHERE id = <id>;
--   DELETE FROM reviews WHERE id = <id>;          -- spam
--
-- Idempotent, so the release script can apply it every time:
--   cd distribution/activation-server
--   npx wrangler d1 execute pitwall-licenses --remote --file migrations/0004_reviews.sql
CREATE TABLE IF NOT EXISTS reviews (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  name         TEXT NOT NULL,
  rating       INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
  body         TEXT NOT NULL,
  email        TEXT,
  created_at   TEXT NOT NULL,
  address_hash TEXT,
  approved     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_reviews_approved ON reviews (approved, created_at);
