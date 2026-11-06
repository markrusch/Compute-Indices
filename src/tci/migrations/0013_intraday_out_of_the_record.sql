-- The intraday tables leave the record. They move to data/intraday.db, which is not
-- committed and is rebuilt from the log by `tci.run intraday ingest` (tci.intraday_db).
--
-- WHY. GitHub refuses any file over 100 MB. On 27 October 2026 the daily run's copy of
-- this database reached it, and every daily push from then until 6 November was rejected:
-- eleven sessions were collected and computed on the runner and then thrown away, because
-- a collector reports today's price and a past day cannot be collected again. On 26
-- October these five tables held 43 MB of the 103.5 MB file, growing about 2 MB a day,
-- and every byte of it is a copy of data/intraday/, which is committed already and is the
-- record of what the hourly job read.
--
-- WHAT IS LOST. Nothing. Nothing in the calculation path ever read these tables (see
-- 0011), and a load from the log reproduces them exactly: books are keyed by the hash the
-- log verifies, and the load is idempotent by sweep id.
--
-- Dropping a table drops its triggers and indexes with it. The file only shrinks once it
-- is vacuumed, which cannot run inside a migration's transaction; `db.migrate` vacuums
-- after applying this one.

DROP VIEW intraday_prices;
DROP TABLE intraday_reads;
DROP TABLE intraday_book_rows;
DROP TABLE intraday_books;
DROP TABLE intraday_rows;
DROP TABLE intraday_sweeps;
