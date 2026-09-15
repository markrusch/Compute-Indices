-- Interest-rate curves, used only to restate a prepaid term price as the pay-as-delivered
-- rate with the same present value before it is compared with an expected rolling cost.
--
-- WHY THE KEY INCLUDES THE VALUE. The ECB and the US Treasury occasionally restate a
-- published observation. Updating in place is what the `fx` table does, and it is what an
-- append-only record cannot do: a restatement becomes a second row with its own fetch time,
-- and a knowledge-time read picks the value that had been fetched by the estimate date.
-- A rerun that fetches an unchanged value inserts nothing.

CREATE TABLE overlay_rates (
  series      TEXT NOT NULL,
  currency    TEXT NOT NULL CHECK (currency IN ('EUR', 'USD')),
  tenor_days  INTEGER NOT NULL,
  obs_date    TEXT NOT NULL,
  rate_pct    REAL NOT NULL,
  source      TEXT NOT NULL,
  fetched_utc TEXT NOT NULL,
  PRIMARY KEY (series, tenor_days, obs_date, rate_pct)
);

CREATE TRIGGER rates_no_update BEFORE UPDATE ON overlay_rates
  BEGIN SELECT RAISE(ABORT, 'overlay_rates are immutable'); END;
CREATE TRIGGER rates_no_delete BEFORE DELETE ON overlay_rates
  BEGIN SELECT RAISE(ABORT, 'overlay_rates are immutable'); END;
