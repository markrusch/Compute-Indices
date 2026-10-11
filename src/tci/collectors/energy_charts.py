# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Day-ahead power prices from Energy-Charts (Fraunhofer ISE): overlay only, never an input.

WHY A SECOND SOURCE. `entsoe.py` needs a personal ENTSO-E token and skips without one. No
token was ever set, so `overlay_power` held no rows at all on 11 October 2026 while the
dashboard's Sources panel said power prices were published beside the index. Energy-Charts
serves the same day-ahead auction results through a public API with no key. The ENTSO-E
collector stays, and when it has stored a zone for the day this one leaves that row alone.

WHAT IS READ. One GET per bidding zone per day:
`https://api.energy-charts.info/price?bzn=<zone>&start=<date>&end=<date>`. The response
carries `unix_seconds` and `price` arrays (EUR/MWh, quarter-hourly since the market moved
to 15-minute products) covering the local delivery day, plus `unit` and `license_info`.
Fixtures for NL, DE-LU and FR were captured on 11 October 2026 for 9 October.

THE LICENCE DECIDES WHAT IS STORED. Each response states its own licence. On 11 October
2026 NL, DE-LU and FR said "CC BY 4.0 ... from Bundesnetzagentur | SMARD.de"; SE3 said its
data was "for private and internal use only" and that external use is "expressly
prohibited". A zone is stored only when its response says CC BY 4.0, so a licence change
upstream stops storage instead of quietly republishing data TCI may not publish. SE3 is not
requested at all, and its fixture was not kept.

WHAT IS STORED. The arithmetic mean of the day's prices per zone, in `overlay_power`, a
table no print reads. The rows feed the rent-against-running-cost table (tci.costfloor)
and nothing else.
"""

from __future__ import annotations

import json
import logging
import sqlite3

import requests

log = logging.getLogger("tci.collectors.energy_charts")

URL = "https://api.energy-charts.info/price"
NAME = "energy_charts"
# Zone keys match entsoe.ZONES, so a row from either collector means the same thing.
ZONES = ("NL", "DE-LU", "FR")
LICENCE_MARK = "CC BY 4.0"
# A delivery day is 92, 96 or 100 quarter-hours (24 hourly points before the switch).
# Fewer than this many non-null points is a partial day, and a partial mean is not stored.
MIN_POINTS = 20


class PriceError(ValueError):
    """A response that cannot be read as one full day of prices for one zone."""


def day_average(payload: dict) -> tuple[float, int, str]:
    """(mean EUR/MWh, points used, licence text) from one API response."""
    licence = str(payload.get("license_info") or "")
    if LICENCE_MARK not in licence:
        raise PriceError(f"licence is not {LICENCE_MARK}: {licence[:80]!r}")
    unit = str(payload.get("unit") or "")
    if unit.replace(" ", "").upper() != "EUR/MWH":
        raise PriceError(f"unexpected unit {unit!r}")
    prices = payload.get("price")
    stamps = payload.get("unix_seconds")
    if not isinstance(prices, list) or not isinstance(stamps, list) or len(prices) != len(stamps):
        raise PriceError("price and unix_seconds are missing or of different lengths")
    values = [float(p) for p in prices if p is not None]
    if len(values) < MIN_POINTS or len(values) < 0.9 * len(prices):
        raise PriceError(f"{len(values)} of {len(prices)} points carry a price")
    return round(sum(values) / len(values), 4), len(values), licence


# Shorter than the price collectors' 30 s: three zones of an overlay are not worth a
# minute and a half of a stalled daily run.
TIMEOUT_SECONDS = 10


def collect(conn: sqlite3.Connection, session: requests.Session, utc_date: str) -> int:
    """Store each zone's day-ahead mean for `utc_date`. Fail-soft; returns zones stored."""
    stored = 0
    for zone in ZONES:
        try:
            resp = session.get(
                URL, params={"bzn": zone, "start": utc_date, "end": utc_date},
                timeout=TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            avg, n, licence = day_average(resp.json())
        except Exception as exc:  # noqa: BLE001 - one zone failing costs that zone only
            log.warning("energy_charts %s %s: not stored (%s)", zone, utc_date, exc)
            continue
        with conn:
            cur = conn.execute(
                "INSERT INTO overlay_power (date, zone, avg_price_eur_mwh, raw_json)"
                " VALUES (?, ?, ?, ?) ON CONFLICT(date, zone) DO NOTHING",
                (utc_date, zone, avg, json.dumps(
                    {"source": NAME, "bzn": zone, "points": n, "licence": licence},
                    sort_keys=True,
                )),
            )
        stored += cur.rowcount
    log.info("energy_charts: %d/%d zones stored for %s", stored, len(ZONES), utc_date)
    return stored
