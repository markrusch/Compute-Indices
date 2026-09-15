# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Interest-rate curves: ECB €STR and AAA euro-area spot curve, US Treasury par curve.

Stored in `overlay_rates`, a table the index calculation never reads. The forward estimate
uses them for one thing: restating a prepaid term price as the pay-as-delivered price with
the same present value, in the price's own currency, before comparing it with an expected
rolling cost. A USD price discounted off a euro curve would have put a 1.4 point rate gap
into a comparison whose only observed discount on 15 September 2026 was 2.5%.

THREE REQUESTS A DAY. The ECB's SDMX API returns several series of one dataset in a single
request when the dimension values are joined with "+", so the six curve tenors are one
request. `lastNObservations=5` refetches the last week, and `INSERT OR IGNORE` on
(series, tenor, date, value) makes the refetch free and turns a restated value into a
second row rather than an edit.

THE TREASURY URL NAMES A YEAR. The daily par-curve CSV lives under a path for one calendar
year, so the year is taken from the run date, and in early January, before the new year has
rows, the previous year's file is read instead.

THE ECB HOST AND TLS. Plain urllib on a Windows developer machine failed certificate
verification against data-api.ecb.europa.eu on 15 September 2026; `requests` with its bundled
CA store succeeded. The collector uses the shared `requests` session like every other one.
"""

from __future__ import annotations

import csv
import io
import logging
import sqlite3
from datetime import datetime

import requests

from tci.collectors.base import TIMEOUT_SECONDS
from tci.db import utc_now_iso

log = logging.getLogger("tci.collectors.rates")

ECB_BASE = "https://data-api.ecb.europa.eu/service/data"
ECB_ESTR = f"{ECB_BASE}/EST/B.EU000A2X2A25.WT"
ECB_CURVE = f"{ECB_BASE}/YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_3M+SR_6M+SR_1Y+SR_2Y+SR_3Y+SR_5Y"
ECB_PARAMS = {"format": "csvdata", "lastNObservations": "5"}
UST_URL = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
           "daily-treasury-rates.csv/{year}/all")

ECB_TENOR_DAYS = {"WT": 1, "SR_3M": 91, "SR_6M": 182, "SR_1Y": 365, "SR_2Y": 730,
                  "SR_3Y": 1095, "SR_5Y": 1826}
UST_TENOR_DAYS = {"1 Mo": 30, "1.5 Month": 45, "2 Mo": 61, "3 Mo": 91, "4 Mo": 122,
                  "6 Mo": 182, "1 Yr": 365, "2 Yr": 730, "3 Yr": 1095, "5 Yr": 1826}

Rate = tuple[str, int, float]   # (observation date, tenor in days, rate in percent)


def parse_ecb(text: str) -> list[Rate]:
    """ECB SDMX csvdata: one row per observation, the tenor is the last segment of KEY."""
    out: list[Rate] = []
    for rec in csv.DictReader(io.StringIO(text)):
        period, value, key = rec.get("TIME_PERIOD"), rec.get("OBS_VALUE"), rec.get("KEY") or ""
        tenor = ECB_TENOR_DAYS.get(key.rsplit(".", 1)[-1])
        if not period or not value or tenor is None:
            continue
        out.append((period, tenor, float(value)))
    return out


def parse_treasury(text: str) -> list[Rate]:
    """Treasury par-curve CSV: one row per date (MM/DD/YYYY), one column per tenor."""
    out: list[Rate] = []
    for rec in csv.DictReader(io.StringIO(text)):
        raw_date = rec.get("Date")
        if not raw_date:
            continue
        obs = datetime.strptime(raw_date, "%m/%d/%Y").strftime("%Y-%m-%d")
        for column, tenor in UST_TENOR_DAYS.items():
            value = rec.get(column)
            if value:
                out.append((obs, tenor, float(value)))
    return out


def store(conn: sqlite3.Connection, series: str, currency: str, source: str,
          rates: list[Rate]) -> int:
    """Insert what is new; return how many rows were added."""
    fetched = utc_now_iso()
    with conn:
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO overlay_rates (series, currency, tenor_days, obs_date,"
            " rate_pct, source, fetched_utc) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(series, currency, tenor, obs, value, source, fetched)
             for obs, tenor, value in rates],
        )
        return conn.total_changes - before


def _treasury(session: requests.Session, utc_date: str) -> list[Rate]:
    year = int(utc_date[:4])
    for y in (year, year - 1):
        resp = session.get(
            UST_URL.format(year=y),
            params={"type": "daily_treasury_yield_curve", "field_tdr_date_value": str(y),
                    "_format": "csv"},
            timeout=TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        rows = parse_treasury(resp.text)
        if rows:
            return rows
    return []


def collect_rates(conn: sqlite3.Connection, session: requests.Session,
                  utc_date: str) -> dict[str, int]:
    """Fetch the three curves. Fail-soft per request: returns rows added, -1 on failure."""
    added: dict[str, int] = {}
    jobs = (
        ("ECB-ESTR", "EUR", lambda: parse_ecb(_get(session, ECB_ESTR))),
        ("ECB-AAA-SPOT", "EUR", lambda: parse_ecb(_get(session, ECB_CURVE))),
        ("UST-PAR", "USD", lambda: _treasury(session, utc_date)),
    )
    for series, currency, fetch in jobs:
        try:
            rates = fetch()
            added[series] = store(conn, series, currency, "rates", rates)
        except Exception:
            log.exception("rates: %s not collected (fail-soft)", series)
            added[series] = -1
    log.info("rates: %s", added)
    return added


def _get(session: requests.Session, url: str) -> str:
    resp = session.get(url, params=ECB_PARAMS, timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp.text
