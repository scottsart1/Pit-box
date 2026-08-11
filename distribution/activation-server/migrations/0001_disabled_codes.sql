-- Retire codes from the ledger workbook.
--
-- `disabled` makes the spreadsheet enforceable: the daily sync sets it from
-- the workbook's Status column, and the Worker refuses a disabled code for
-- activation, re-activation and download. Before this, a Replaced code's
-- original device could still re-activate forever, so one purchase could end
-- up as two working installs (the old machine plus the replacement's).
--
-- Apply once against the live database:
--   cd distribution/activation-server
--   npx wrangler d1 execute pitwall-licenses --remote --file migrations/0001_disabled_codes.sql
ALTER TABLE codes ADD COLUMN disabled INTEGER NOT NULL DEFAULT 0;
ALTER TABLE codes ADD COLUMN disabled_reason TEXT;
