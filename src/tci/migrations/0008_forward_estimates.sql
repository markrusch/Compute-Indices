-- The forward estimate ledger: every estimate as it was made, never as it would be made now.
--
-- WHY STORED AT ALL, when the estimate is a deterministic function of stored observations.
-- Because the function changes. A forecast track record rebuilt with today's code scores
-- today's model against the past with hindsight, and the published record of what was
-- forecast on a date would move every time the method did. Stored rows cannot move; a
-- changed method writes rows under a new `method_version`, and calibration is reported per
-- version.
--
-- `component`: M the martingale estimate of the anchor's window mean (published as S*),
-- T the term-implied diagnostic, L the lockable cost per used GPU-hour. `value_usd` NULL is a
-- gap, with the reason in `detail`. `backfilled` rows were computed after their date from data
-- knowable on it: pseudo-out-of-sample, never a track record.

CREATE TABLE forward_estimates (
  date           TEXT NOT NULL,
  series         TEXT NOT NULL,
  component      TEXT NOT NULL CHECK (component IN ('M', 'T', 'L')),
  horizon_days   INTEGER NOT NULL,
  revision       INTEGER NOT NULL,
  value_usd      REAL,
  p10            REAL,
  p50            REAL,
  p90            REAL,
  n_inputs       INTEGER NOT NULL DEFAULT 0,
  detail         TEXT NOT NULL DEFAULT '{}',
  inputs_digest  TEXT NOT NULL,
  method_version TEXT NOT NULL,
  backfilled     INTEGER NOT NULL CHECK (backfilled IN (0, 1)),
  computed_at    TEXT NOT NULL,
  PRIMARY KEY (date, series, component, horizon_days, revision)
);

CREATE TRIGGER fwd_no_update BEFORE UPDATE ON forward_estimates
  BEGIN SELECT RAISE(ABORT, 'forward_estimates are immutable'); END;
CREATE TRIGGER fwd_no_delete BEFORE DELETE ON forward_estimates
  BEGIN SELECT RAISE(ABORT, 'forward_estimates are immutable'); END;
