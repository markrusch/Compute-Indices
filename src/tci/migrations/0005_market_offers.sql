-- Every offer a marketplace read returns, including the ones the index does not admit.
--
-- WHY. The vast.ai collector has always fetched community and unverified hosts: its query
-- carries no verification filter, and the datacenter-verified test is applied afterwards,
-- in `to_observations`. Everything that failed the test was discarded before storage. On
-- 12 and 13 September 2026 that left 7 and 4 H100 SXM rows from 3 and 2 hosts, against
-- ascending books of 19 and 18 offers. A within-venue price-on-performance design (Research
-- Note 2026-05) is identified off differences between machines and hosts, and the
-- collector was throwing most of them away.
--
-- WHY A SEPARATE TABLE. normalise.py does not filter on hosting type or verification; that
-- filter lives only in the collector. Writing community offers into `observations` would
-- therefore put them in front of a print, which is a methodology change. This table is read
-- by nothing in the calculation path, so storing into it cannot move a number.
--
-- `in_index_scope` records whether the offer passed the collector's datacenter-verified
-- test at collection time, so the admitted subset stays reconstructible from this table
-- alone. Same append-only guarantee as `observations`.

CREATE TABLE market_offers (
  id INTEGER PRIMARY KEY,
  run_id          TEXT NOT NULL REFERENCES runs(run_id),
  ts_utc          TEXT NOT NULL,
  source          TEXT NOT NULL,
  queried_name    TEXT NOT NULL,
  offer_id        TEXT,
  machine_id      TEXT,
  host_id         TEXT,
  gpu_model       TEXT,
  num_gpus        INTEGER,
  dph_total       REAL,
  country         TEXT,
  verification    TEXT,
  hosting_type    INTEGER,
  in_index_scope  INTEGER NOT NULL CHECK (in_index_scope IN (0, 1)),
  raw_json        TEXT NOT NULL
);
CREATE INDEX idx_market_offers_day ON market_offers(source, gpu_model, ts_utc);

CREATE TRIGGER mko_no_update BEFORE UPDATE ON market_offers
  BEGIN SELECT RAISE(ABORT, 'market_offers are immutable'); END;
CREATE TRIGGER mko_no_delete BEFORE DELETE ON market_offers
  BEGIN SELECT RAISE(ABORT, 'market_offers are immutable'); END;
