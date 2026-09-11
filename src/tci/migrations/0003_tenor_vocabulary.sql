-- Widen the observations tier vocabulary, and give `term` the constraint it never had.
--
-- WHY. From 2026-09-08 the collectors record the wider market around the reference unit:
-- Azure reservations at three terms, Azure spot and low-priority meters, and vast.ai bid
-- prices. The original CHECK allowed tier IN ('executable','list') only, so every daily
-- run from 2026-09-08 died on an IntegrityError before computing anything, and the index
-- went dark for four sessions. This migration is the fix.
--
-- WHAT IS NOT CHANGING. Index eligibility does not live in this schema and never did.
-- normalise.py admits only `term == reference_unit.term` and `tier IN (executable, list)`,
-- and tests/test_normalise.py pins that. Widening the column changes what may be STORED,
-- not what may be PRICED.
--
-- WHY STILL A CLOSED LIST. The constraint's real value is catching a typo'd or
-- whitespace-padded value at write time, when it is one row, rather than in a research
-- query months later. That value is preserved by enumerating a larger vocabulary; it
-- would be lost by dropping the constraint. `term` had no constraint at all, which is how
-- 'reserved_1yr' inserted cleanly while tier failed -- an asymmetry that would eventually
-- have let a typo'd tenor sit unnoticed in the audit trail.
--
-- SQLite cannot alter a CHECK in place, so this is the documented table-rebuild. The
-- immutability triggers are dropped and recreated around it; DROP TABLE does not fire
-- them, and the data is copied rather than mutated, so the append-only guarantee holds
-- across the rebuild.

PRAGMA foreign_keys = OFF;

DROP TRIGGER obs_no_update;
DROP TRIGGER obs_no_delete;

CREATE TABLE observations_new (
  id INTEGER PRIMARY KEY,
  run_id  TEXT NOT NULL REFERENCES runs(run_id),
  ts_utc  TEXT NOT NULL,
  source  TEXT NOT NULL,
  provider TEXT NOT NULL,
  gpu_model TEXT NOT NULL,
  gpu_count INTEGER,
  price_usd_per_gpu_hr REAL NOT NULL,
  region TEXT,
  country TEXT,
  interconnect TEXT,
  -- executable / list  : a price the index may consider (normalise.py decides)
  -- spot / interruptible / community : real market prices on a different delivery
  --   promise, stored for context and never eligible for a print
  tier TEXT NOT NULL CHECK (
    tier IN ('executable','list','spot','interruptible','community')
  ),
  -- on_demand is the reference unit. The reserved_* tenors are Azure's published
  -- 1/3/5-year reservations, converted from a whole-term upfront total to a per-GPU-hour
  -- equivalent by the collector.
  term TEXT NOT NULL DEFAULT 'on_demand' CHECK (
    term IN ('on_demand','reserved_1yr','reserved_3yr','reserved_5yr')
  ),
  raw_json TEXT NOT NULL
);

INSERT INTO observations_new
  (id, run_id, ts_utc, source, provider, gpu_model, gpu_count,
   price_usd_per_gpu_hr, region, country, interconnect, tier, term, raw_json)
SELECT
   id, run_id, ts_utc, source, provider, gpu_model, gpu_count,
   price_usd_per_gpu_hr, region, country, interconnect, tier, term, raw_json
FROM observations;

DROP TABLE observations;
ALTER TABLE observations_new RENAME TO observations;

CREATE TRIGGER obs_no_update BEFORE UPDATE ON observations
  BEGIN SELECT RAISE(ABORT, 'observations are immutable'); END;
CREATE TRIGGER obs_no_delete BEFORE DELETE ON observations
  BEGIN SELECT RAISE(ABORT, 'observations are immutable'); END;

PRAGMA foreign_keys = ON;
