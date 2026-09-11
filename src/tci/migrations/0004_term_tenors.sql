-- Widen the `term` vocabulary to the tenors public rate cards actually publish.
--
-- WHY. 0003 admitted Azure's 1/3/5-year reservations. The sources added on 2026-09-11
-- publish other tenors: Civo sells 6-, 12-, 24- and 36-month commitments; OVHcloud and
-- Latitude.sh publish a one-month commitment price; Hyperstack and Lambda publish
-- "reserved" prices whose tenor is a range or a floor rather than a term. Each needs a
-- storable value, and the last kind must be stored as what it is (tenor unspecified)
-- rather than forced into a tenor it does not have.
--
-- WHAT IS NOT CHANGING. As with 0003: eligibility for a print lives in normalise.py, which
-- admits only term == reference_unit.term (on_demand). This widens what may be STORED.
-- The list stays closed so a typo'd tenor fails at write time.
--
-- Same documented table rebuild as 0003: triggers dropped and recreated around a copy, so
-- no row is mutated and the append-only guarantee holds across the rebuild.

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
  tier TEXT NOT NULL CHECK (
    tier IN ('executable','list','spot','interruptible','community')
  ),
  -- on_demand is the reference unit. commit_* and reserved_* are term-committed prices
  -- normalised to a per-GPU-hour equivalent by the collector (tenor in the name).
  -- reserved_unspecified: a published committed price whose tenor is a range or a
  -- "starting from" floor; stored for the record, never used where a tenor is needed.
  term TEXT NOT NULL DEFAULT 'on_demand' CHECK (
    term IN ('on_demand','commit_1mo','commit_3mo','commit_6mo','reserved_1yr',
             'reserved_2yr','reserved_3yr','reserved_5yr','reserved_unspecified')
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
