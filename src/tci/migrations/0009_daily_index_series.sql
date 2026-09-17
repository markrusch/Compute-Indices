-- An index on (series, date) for the read paths that start from a series, not a date.
--
-- WHY. `daily_index` is keyed PRIMARY KEY (date, series, revision), so SQLite's implicit
-- index is ordered by date first. Every published read starts from a series instead:
-- "the history of EU-CRI-H100", "the last print before this date", "the value on or
-- before". `series` is not a leading column of that key, so each of those scans the
-- whole table and sorts. It is unnoticeable at today's size and gets linearly worse for
-- the life of the index, which is meant to be measured in years. The table only ever
-- grows, because corrections are appended as revisions and nothing is deleted.
--
-- The column order is (series, date) rather than (date, series): it serves the
-- series-then-date reads the PK cannot, and leaves date-first reads to the PK.
-- Revision is deliberately not a third column. The head-revision resolution in
-- tci.series_read groups rather than seeks, so carrying revision here would widen
-- every entry to speed up nothing.

CREATE INDEX IF NOT EXISTS idx_daily_index_series_date ON daily_index (series, date);

-- The same shape, for the audit set behind a print: constituents are always read for
-- one (date, series) at one revision, and are read per print by the site build.
CREATE INDEX IF NOT EXISTS idx_constituents_series_date ON constituents (series, date);
