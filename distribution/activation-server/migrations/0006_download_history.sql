-- One privately imported, provenance-bearing snapshot. Re-import replaces it;
-- it never increments or backfills the separately measured download_daily.
CREATE TABLE IF NOT EXISTS download_history (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  report_json TEXT NOT NULL CHECK (json_valid(report_json))
);
