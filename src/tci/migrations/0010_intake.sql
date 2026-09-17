-- The intake ledger: of everything collected on a date, what reached the calculation and
-- why the rest did not.
--
-- WHY THIS TABLE EXISTS. On 2026-09-17 the pipeline stored 1,979 observations and 75
-- reached a print. Nothing in the repository recorded what happened to the other 1,904.
-- normalise.py drops a row through one of nine sequential filters and returns only the
-- survivors, so a collector whose rows all die at one gate looks exactly like a collector
-- that is working: the run succeeds, the count of stored observations goes up, and the
-- print survives on the constituents it already had.
--
-- That is not hypothetical. `eu_eea_countries` held YAML 1.1's boolean false where the
-- string "NO" belonged, and every Norwegian observation was dropped by the country filter
-- for about three weeks - 126 rows - until somebody found it by hand. canary.py would not
-- have caught it (it detects a collector returning nothing, and those rows were returned);
-- reliability.html would not have caught it (it reports prints that already gapped, which
-- is the symptom arriving after the fact).
--
-- WHY IT IS STORED rather than recomputed on demand. Recomputing shows today. The signal
-- is the change: a gate whose count jumps against its trailing window is how the Norway
-- bug would have announced itself on day one. That comparison needs yesterday's counts as
-- they were, under the methodology version live on that date, which is not what a replay
-- under today's head would produce.
--
-- `gate` is the FIRST filter the row failed, so the counts partition the day exactly and
-- sum to the rows collected. The chain is ordered, so a row counted at `not_in_panel` may
-- also have been outside the block; it is attributed to the gate that stopped it. Reading
-- one gate's count as "rows that would otherwise have printed" is therefore wrong, and the
-- published page says so.
--
-- `revision` and `methodology_version`: this is a derived per-date table, and a derived
-- table without a revision column becomes the next place a stale value hides. A recompute
-- appends a revision; readers take MAX(revision) per (date, block).
CREATE TABLE intake (
  date                TEXT NOT NULL,
  block               TEXT NOT NULL,   -- region block priced: 'EU_EEA', 'US', ...
  revision            INTEGER NOT NULL,
  source              TEXT NOT NULL,   -- the collector that stored the rows
  provider            TEXT NOT NULL,
  model_class         TEXT NOT NULL,   -- '' where the row matched no configured class
  gate                TEXT NOT NULL,   -- first filter failed, or 'admitted'
  n_rows              INTEGER NOT NULL CHECK (n_rows > 0),
  methodology_version TEXT NOT NULL,
  computed_at         TEXT NOT NULL,
  run_id              TEXT NOT NULL REFERENCES runs(run_id),
  PRIMARY KEY (date, block, revision, source, provider, model_class, gate)
);

CREATE INDEX intake_date_gate ON intake (date, gate);

CREATE TRIGGER intake_no_update BEFORE UPDATE ON intake
  BEGIN SELECT RAISE(ABORT, 'intake rows are immutable; add a revision'); END;
CREATE TRIGGER intake_no_delete BEFORE DELETE ON intake
  BEGIN SELECT RAISE(ABORT, 'intake rows are immutable; add a revision'); END;
