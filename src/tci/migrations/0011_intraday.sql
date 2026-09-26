-- Intraday prices: every read the hourly sweep made, queryable beside the fixing's own.
--
-- WHY SEPARATE TABLES, AND NOT `observations`. `commands._observations_for_date` takes every
-- observation whose run is dated that day. An hourly read stored there would enter the 11:00
-- print the moment it was written, without a version or a notice. Nothing in the calculation
-- path reads these tables.
--
-- WHO WRITES THEM. The daily run, from the hourly job's append-only log in data/intraday/.
-- This database has one writer and it is committed once a day; if the hourly job committed
-- it too, two jobs would race on a 17 MB binary file git cannot merge, 24 times a day. The
-- log is the record as it was read, hour by hour; these tables are the same reads loaded for
-- SQL, at most a day behind (`tci.run intraday ingest` brings a local copy up to date). A
-- load is idempotent by sweep id, so the daily job's reset-and-rerun on a push race cannot
-- load a sweep twice.
--
-- WHY FOUR TABLES FOR ONE KIND OF ROW. A marketplace offer usually sits at the same price for
-- hours, and a catalog of 1,297 rows is usually identical from one hour, and one day, to the
-- next. Storing every read in full would add about 2,000 rows an hour. Instead a row's
-- content is stored once (`intraday_rows`), a read's full set of rows once per distinct
-- content (`intraday_books`, keyed by the same hash the log verifies), and each read points
-- at its book. The `intraday_prices` view joins them back into one row per offer per read.
--
-- Append-only like every other raw table: a correction is a later read, never an edit.

CREATE TABLE intraday_sweeps (
  sweep_id     TEXT PRIMARY KEY,
  started_utc  TEXT NOT NULL,
  at_utc       TEXT NOT NULL,           -- when the sweep finished: the time its point is plotted
  segment      TEXT NOT NULL,           -- the log file it was loaded from
  ingested_utc TEXT NOT NULL
);

CREATE TABLE intraday_rows (
  row_id               INTEGER PRIMARY KEY,
  row_hash             TEXT NOT NULL UNIQUE,   -- sha256 of (source, canonical row)
  source               TEXT NOT NULL,
  provider             TEXT NOT NULL,
  gpu_model            TEXT NOT NULL,
  gpu_count            INTEGER,
  price_usd_per_gpu_hr REAL NOT NULL,
  region               TEXT,
  country              TEXT,
  interconnect         TEXT,
  tier                 TEXT NOT NULL,
  term                 TEXT NOT NULL,
  raw_json             TEXT NOT NULL            -- only the keys the calculation reads
);

CREATE TABLE intraday_books (
  source TEXT NOT NULL,
  book   TEXT NOT NULL,                  -- the log's book hash
  n_rows INTEGER NOT NULL,
  PRIMARY KEY (source, book)
);

CREATE TABLE intraday_book_rows (
  source TEXT NOT NULL,
  book   TEXT NOT NULL,
  row_id INTEGER NOT NULL REFERENCES intraday_rows(row_id),
  n      INTEGER NOT NULL CHECK (n > 0),  -- identical offers are two offers, not one
  PRIMARY KEY (source, book, row_id),
  FOREIGN KEY (source, book) REFERENCES intraday_books(source, book)
) WITHOUT ROWID;

CREATE TABLE intraday_reads (
  sweep_id TEXT NOT NULL REFERENCES intraday_sweeps(sweep_id),
  source   TEXT NOT NULL,
  status   TEXT NOT NULL CHECK (status IN ('ok', 'failed')),
  at_utc   TEXT NOT NULL,
  book     TEXT,                          -- NULL for a failed read
  n_rows   INTEGER NOT NULL,
  dropped  INTEGER NOT NULL DEFAULT 0,    -- malformed rows the store refused
  error    TEXT,
  PRIMARY KEY (sweep_id, source),
  CHECK ((status = 'ok') = (book IS NOT NULL))
);

CREATE INDEX intraday_reads_at ON intraday_reads (at_utc);
CREATE INDEX intraday_reads_source_at ON intraday_reads (source, at_utc);

-- One row per offer per successful read. `n` is how many identical offers the read held.
CREATE VIEW intraday_prices AS
SELECT r.sweep_id, r.at_utc, r.source, x.provider, x.gpu_model, x.gpu_count,
       x.price_usd_per_gpu_hr, x.region, x.country, x.interconnect, x.tier, x.term,
       x.raw_json, br.n
FROM intraday_reads r
JOIN intraday_book_rows br ON br.source = r.source AND br.book = r.book
JOIN intraday_rows x ON x.row_id = br.row_id
WHERE r.status = 'ok';

CREATE TRIGGER intraday_sweeps_no_update BEFORE UPDATE ON intraday_sweeps
  BEGIN SELECT RAISE(ABORT, 'intraday rows are immutable'); END;
CREATE TRIGGER intraday_sweeps_no_delete BEFORE DELETE ON intraday_sweeps
  BEGIN SELECT RAISE(ABORT, 'intraday rows are immutable'); END;
CREATE TRIGGER intraday_rows_no_update BEFORE UPDATE ON intraday_rows
  BEGIN SELECT RAISE(ABORT, 'intraday rows are immutable'); END;
CREATE TRIGGER intraday_rows_no_delete BEFORE DELETE ON intraday_rows
  BEGIN SELECT RAISE(ABORT, 'intraday rows are immutable'); END;
CREATE TRIGGER intraday_books_no_update BEFORE UPDATE ON intraday_books
  BEGIN SELECT RAISE(ABORT, 'intraday rows are immutable'); END;
CREATE TRIGGER intraday_books_no_delete BEFORE DELETE ON intraday_books
  BEGIN SELECT RAISE(ABORT, 'intraday rows are immutable'); END;
CREATE TRIGGER intraday_book_rows_no_update BEFORE UPDATE ON intraday_book_rows
  BEGIN SELECT RAISE(ABORT, 'intraday rows are immutable'); END;
CREATE TRIGGER intraday_book_rows_no_delete BEFORE DELETE ON intraday_book_rows
  BEGIN SELECT RAISE(ABORT, 'intraday rows are immutable'); END;
CREATE TRIGGER intraday_reads_no_update BEFORE UPDATE ON intraday_reads
  BEGIN SELECT RAISE(ABORT, 'intraday rows are immutable'); END;
CREATE TRIGGER intraday_reads_no_delete BEFORE DELETE ON intraday_reads
  BEGIN SELECT RAISE(ABORT, 'intraday rows are immutable'); END;
