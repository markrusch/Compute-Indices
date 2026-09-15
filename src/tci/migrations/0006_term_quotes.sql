-- Term quotes a marketplace returns when asked for a committed duration.
--
-- WHY. vast.ai prices a reserved (prepaid) rental only when the search names a duration:
-- the ordinary on-demand book carries `discounted_dph_total` equal to `dph_total` on every
-- one of the 169 offers stored by 15 September 2026, while the same chip queried with
-- `type: reserved` and a 90-day duration returned a Czech host at 0.9626 of on-demand. These
-- are prices a host set for a specific chip and tenor, which is what the forward estimate's
-- term diagnostic needs and what no rate card supplies.
--
-- WHY A SEPARATE TABLE. `observations` is read by normalise.py, and `market_offers` holds the
-- on-demand book under the same queried names. A reserved quote carries a requested duration
-- neither table has a column for. Nothing in the calculation path reads this table.

CREATE TABLE term_quotes (
  id INTEGER PRIMARY KEY,
  run_id              TEXT NOT NULL REFERENCES runs(run_id),
  ts_utc              TEXT NOT NULL,
  source              TEXT NOT NULL,
  queried_name        TEXT NOT NULL,
  requested_days      INTEGER NOT NULL,
  offer_id            TEXT,
  machine_id          TEXT,
  host_id             TEXT,
  gpu_model           TEXT,
  num_gpus            INTEGER,
  country             TEXT,
  verification        TEXT,
  hosting_type        INTEGER,
  dph_total           REAL,
  discounted_dph_total REAL,
  max_duration_days   REAL,
  in_index_scope      INTEGER NOT NULL CHECK (in_index_scope IN (0, 1)),
  raw_json            TEXT NOT NULL
);
CREATE INDEX idx_term_quotes_day ON term_quotes(source, gpu_model, ts_utc);

CREATE TRIGGER tq_no_update BEFORE UPDATE ON term_quotes
  BEGIN SELECT RAISE(ABORT, 'term_quotes are immutable'); END;
CREATE TRIGGER tq_no_delete BEFORE DELETE ON term_quotes
  BEGIN SELECT RAISE(ABORT, 'term_quotes are immutable'); END;
