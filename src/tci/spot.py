# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Spot against on-demand, per region: daily values, spread, volatility, seller discounts.

Beside the pipeline, like `intraday.py`, and read-only. Nothing here computes or stores a
print. The daily values are the stored prints where they exist; for a day before a series
took effect they are replayed from that day's stored observations, under the first version
that defines the series, and flagged indicative, exactly as the intraday page does. The
replay calls `commands.regional_print`, the fixing's own function, so the two cannot drift.

Volatility is the standard deviation of day-over-day log changes, shown per day and
annualised by sqrt(365) because compute is rented every day of the year. The spread is a
difference that can reach zero, where a log change is undefined, so its volatility is the
standard deviation of its daily change in dollars instead. A change is taken only between two
consecutive calendar days that both have a value: a gap is never bridged, so a series that
gapped in between contributes fewer changes and says so, rather than one large change
spread over the missing days. Below a minimum count the window reads "not enough history"
instead of a number computed from three points.

The hourly figure uses the intraday replay, with the same rule: two reads further apart
than the intraday chart's join limit are not a change.
"""

from __future__ import annotations

import logging
import math
import sqlite3
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date as date_type
from datetime import datetime, timedelta
from typing import Any

from tci import series_read

log = logging.getLogger("tci.spot")

HOURS_PER_YEAR = 24 * 365
MAX_HOURLY_GAP = timedelta(hours=2.5)  # the intraday chart's join limit


@dataclass(frozen=True)
class Region:
    key: str
    label: str
    long: str
    on_demand: str
    spot: str
    spread: str


REGIONS: tuple[Region, ...] = (
    Region("eu", "EU", "EU/EEA", "EU-CRI-H100", "EU-CRI-H100-SPOT", "EU-CRI-H100-SPOTSPREAD"),
    Region("us", "US", "United States", "EU-CRI-H100-US", "EU-CRI-H100-SPOT-US",
           "EU-CRI-H100-SPOTSPREAD-US"),
    Region("global", "Global", "Every country", "EU-CRI-H100-GLOBAL", "EU-CRI-H100-SPOT-GLOBAL",
           "EU-CRI-H100-SPOTSPREAD-GLOBAL"),
)
BLOCK = {"eu": "EU_EEA", "us": "US", "global": "GLOBAL"}

# (window in calendar days, fewest changes that window may be computed from)
WINDOWS: tuple[tuple[int, int], ...] = ((7, 5), (30, 20))
HOURLY_MIN_CHANGES = 48


@dataclass(frozen=True)
class DayValue:
    date: str
    value: float | None
    n: int
    flags: str
    status: str  # 'print' | 'indicative' | 'gap' | 'none'


@dataclass(frozen=True)
class Vol:
    value: float | None  # annualised: a fraction for a price (0.12 = 12%), USD for a spread
    n_changes: int
    needed: int
    per_period: float | None = None  # the same, per day (or per hour), not annualised


@dataclass
class RegionView:
    region: Region
    on_demand: list[DayValue]
    spot: list[DayValue]
    spread: list[DayValue]
    vol: dict[str, dict[str, Vol]] = field(default_factory=dict)  # leg -> window -> Vol
    sellers: list[dict[str, Any]] = field(default_factory=list)
    categories: list[dict[str, Any]] = field(default_factory=list)
    outside: list[dict[str, Any]] = field(default_factory=list)


def first_spot_date(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT MIN(r.utc_date) FROM observations o JOIN runs r ON o.run_id = r.run_id"
        " WHERE o.tier = 'spot' AND r.status = 'ok'").fetchone()
    return row[0] if row and row[0] else None


def _dates(start: str, end: str) -> list[str]:
    d0, d1 = date_type.fromisoformat(start), date_type.fromisoformat(end)
    return [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]


class _Replay:
    """Values for days before a series took effect, from that day's stored rows."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        from tci import intraday

        self.conn = conn
        self.calc = intraday._Calc(conn)
        self._rows: dict[str, list[sqlite3.Row]] = {}

    def rows(self, day: str) -> list[sqlite3.Row]:
        if day not in self._rows:
            self._rows[day] = self.conn.execute(
                "SELECT o.* FROM observations o JOIN runs r ON o.run_id = r.run_id"
                " WHERE r.utc_date = ? AND r.status = 'ok' ORDER BY o.id", (day,)).fetchall()
        return self._rows[day]

    def value(self, series: str, day: str) -> DayValue:
        from tci.commands import regional_print

        found = self.calc.regional(series, day)
        rows = self.rows(day)
        if found is None or not rows:
            return DayValue(day, None, 0, "", "none")
        factors, rs, indicative = found
        fx = self.calc.inputs(day)[2]
        p = regional_print(rows, factors, fx, day, series, rs)
        status = "indicative" if indicative else ("print" if p.value_usd is not None else "gap")
        return DayValue(day, p.value_usd, p.n_sources, p.flags, status)


def daily(conn: sqlite3.Connection, series: str, dates: Sequence[str],
          replay: _Replay) -> list[DayValue]:
    """The stored print for each date; otherwise, before the series took effect, a replay
    flagged indicative. A date after it took effect with no print stays empty: that is a
    day the index did not publish, and the page must not fill it."""
    out = []
    for day in dates:
        row = series_read.head_print(conn, series, day)
        if row is not None:
            value = row["value_usd"]
            out.append(DayValue(day, value, row["n_sources"], row["flags"] or "",
                                "print" if value is not None else "gap"))
            continue
        try:
            found = replay.calc.regional(series, day)
        except Exception:  # noqa: BLE001 - a failed lookup costs the point, not the page
            log.exception("spot: %s on %s not resolved", series, day)
            found = None
        if found is not None and found[2]:
            try:
                out.append(replay.value(series, day))
            except Exception:  # noqa: BLE001
                log.exception("spot: %s on %s not replayed", series, day)
                out.append(DayValue(day, None, 0, "not_computed", "gap"))
        else:
            out.append(DayValue(day, None, 0, "", "none"))
    return out


def spread(lead: list[DayValue], reference: list[DayValue]) -> list[DayValue]:
    """Lead minus reference per day, where both have a value. Indicative if either is."""
    out = []
    for a, b in zip(lead, reference, strict=True):
        if a.value is None or b.value is None:
            out.append(DayValue(a.date, None, 0, "", "gap" if "print" in (a.status, b.status)
                                else "none"))
            continue
        status = "indicative" if "indicative" in (a.status, b.status) else "print"
        out.append(DayValue(a.date, round(a.value - b.value, 6), 2, "", status))
    return out


def log_changes(points: Sequence[tuple[Any, float | None]], max_gap: Any) -> list[float]:
    """Log changes between consecutive points no further apart than `max_gap`, both valued."""
    out = []
    for (t0, v0), (t1, v1) in zip(points, points[1:], strict=False):
        if v0 is None or v1 is None or v0 <= 0 or v1 <= 0 or (t1 - t0) > max_gap:
            continue
        out.append(math.log(v1 / v0))
    return out


def abs_changes(points: Sequence[tuple[Any, float | None]], max_gap: Any) -> list[float]:
    """Changes in level between consecutive valued points, for a series that can be zero."""
    return [v1 - v0 for (t0, v0), (t1, v1) in zip(points, points[1:], strict=False)
            if v0 is not None and v1 is not None and (t1 - t0) <= max_gap]


def annualised(changes: Sequence[float], periods_per_year: float, needed: int) -> Vol:
    if len(changes) < needed or len(changes) < 2:
        return Vol(None, len(changes), needed)
    sd = statistics.stdev(changes)
    return Vol(sd * math.sqrt(periods_per_year), len(changes), needed, sd)


def daily_vol(values: Sequence[DayValue], end: str, level: bool = False) -> dict[str, Vol]:
    """Per window. `level` measures changes in dollars (the spread) instead of log changes."""
    out = {}
    end_d = date_type.fromisoformat(end)
    changes = abs_changes if level else log_changes
    for days, needed in WINDOWS:
        start = end_d - timedelta(days=days)
        pts = [(date_type.fromisoformat(v.date), v.value) for v in values
               if start <= date_type.fromisoformat(v.date) <= end_d]
        out[f"{days}d"] = annualised(changes(pts, timedelta(days=1)), 365, needed)
    return out


def hourly_vol(points: Sequence[tuple[datetime, float | None]]) -> Vol:
    return annualised(log_changes(points, MAX_HOURLY_GAP), HOURS_PER_YEAR, HOURLY_MIN_CHANGES)


def _median(xs: Sequence[float]) -> float | None:
    return round(statistics.median(xs), 6) if xs else None


def _key(row: Any) -> str:
    return str(row["region"] or row["country"] or "")


def sellers(conn: sqlite3.Connection, region: Region, day: str, replay: _Replay) -> tuple[
        list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Each seller's own discount, the segment breakdown, and the prices kept out.

    Under the version that defines the spot series for this region (the live one on
    `day`, or the first later one). A seller's discount compares its spot and on-demand
    prices for the same region and node size only, then takes the median across those
    pairs: medians over different zones compared a Finnish spot rate with a Dutch list
    rate and read as a discount neither market offers. Where the source lists spot above
    on-demand for a pair, the row says so rather than drop it; Google Cloud's catalogue
    does that in europe-west4 and europe-west1 on 26 September 2026.

    The index spread compares two markets. This table is the other question a buyer asks:
    what one provider charges less for the same hour if it may take it back.
    """
    from tci.normalise import ON_DEMAND_TIERS

    found = replay.calc.regional(region.spot, day)
    if found is None:
        return [], [], []
    factors, rs, _indicative = found
    block = rs.block
    countries = factors.countries_of(block)
    unplaced = factors.unplaced_in(block)
    mc = factors.model_classes[rs.model_class]
    floor = factors.filters.min_gpu_count
    lo, hi = factors.filters.price_floor_usd, factors.filters.price_ceiling_usd
    population = factors.population_for(rs.population)

    def admitted(row: Any, tiers: frozenset[str]) -> bool:
        country = row["country"]
        return (row["gpu_model"] == mc.reference_variant and row["term"] == "on_demand"
                and row["tier"] in tiers
                and (country in countries or (country is None and row["provider"] in unplaced))
                and (row["gpu_count"] is None or row["gpu_count"] >= floor)
                and factors.admits(row["provider"], row["source"], rs.model_class, block)
                and "EUR" not in str(row["raw_json"] or "")
                and lo <= row["price_usd_per_gpu_hr"] <= hi)

    rows = replay.rows(day)
    cells: dict[tuple[str, str, str, Any], dict[str, list[float]]] = {}
    for row in rows:
        for tag, tiers in (("spot", rs.tiers), ("od", ON_DEMAND_TIERS)):
            if admitted(row, tiers):
                k = (row["provider"], row["source"], _key(row), row["gpu_count"])
                cells.setdefault(k, {"spot": [], "od": []})[tag].append(
                    float(row["price_usd_per_gpu_hr"]))
    by: dict[str, dict[str, Any]] = {}
    for (provider, _src, _where, _n), c in cells.items():
        d = by.setdefault(provider, {"pairs": [], "spot": [], "od": []})
        d["spot"] += c["spot"]
        d["od"] += c["od"]
        if c["spot"] and c["od"]:
            d["pairs"].append((statistics.median(c["spot"]), statistics.median(c["od"])))
    table = []
    for provider, d in sorted(by.items()):
        if not d["spot"]:
            continue
        segment = factors.segment_of(provider)
        discounts = [1 - s / od for s, od in d["pairs"] if od > 0]
        table.append({
            "provider": provider, "segment": segment,
            "spot_usd": _median(d["spot"]), "on_demand_usd": _median(d["od"]),
            "pairs": len(d["pairs"]),
            "discount_pct": round(statistics.median(discounts) * 100, 2) if discounts else None,
            "spot_above_on_demand": sum(1 for s, od in d["pairs"] if s > od),
            "in_spot_population": segment in population,
        })
    cats = []
    for seg in ("hyperscaler", "neocloud", "marketplace"):
        prices = [r["spot_usd"] for r in table if r["segment"] == seg and r["spot_usd"]]
        cats.append({"segment": seg, "sellers": len(prices), "median_spot_usd": _median(prices)})
    # Stored interruptible prices the unit leaves out: a reference, never an input.
    why = {"azure": "Low Priority, a retired product", "vast.ai": "min_bid, a bid floor"}
    outside = []
    for provider in sorted({r["provider"] for r in rows
                            if admitted(r, frozenset({"interruptible"}))}):
        prices = [float(r["price_usd_per_gpu_hr"]) for r in rows
                  if r["provider"] == provider and admitted(r, frozenset({"interruptible"}))]
        outside.append({"provider": provider, "tier": "interruptible",
                        "why": why.get(provider, "stored as interruptible"),
                        "median_usd": _median(prices), "offers": len(prices)})
    return table, cats, outside


def build(conn: sqlite3.Connection, end: str, hourly: dict[str, list[tuple[datetime,
          float | None]]] | None = None) -> list[RegionView]:
    """Everything the spot page and its data files show, one RegionView per region.

    Each region is built on its own, so a failure costs that region and not the page.
    """
    # The window ends on the last day anything was collected. Today, before the 11:00 run,
    # has nothing yet; ending on it would draw an empty day as though it were a gap.
    last = conn.execute(
        "SELECT MAX(r.utc_date) FROM observations o JOIN runs r ON o.run_id = r.run_id"
        " WHERE r.status = 'ok' AND r.utc_date <= ?", (end,)).fetchone()
    end = last[0] if last and last[0] else end
    start = first_spot_date(conn) or end
    dates = _dates(start, end)
    replay = _Replay(conn)
    views = []
    for region in REGIONS:
        try:
            od = daily(conn, region.on_demand, dates, replay)
            sp = daily(conn, region.spot, dates, replay)
            spr = spread(od, sp)
            view = RegionView(region, od, sp, spr)
            view.vol = {"on_demand": daily_vol(od, end), "spot": daily_vol(sp, end),
                        "spread": daily_vol(spr, end, level=True)}
            for leg, series in (("on_demand", region.on_demand), ("spot", region.spot)):
                pts = (hourly or {}).get(series)
                if pts is not None:
                    view.vol[leg]["hourly"] = hourly_vol(pts)
            latest = next((v.date for v in reversed(sp) if v.value is not None), end)
            view.sellers, view.categories, view.outside = sellers(conn, region, latest, replay)
        except Exception:  # noqa: BLE001
            log.exception("spot: region %s not built", region.key)
            view = RegionView(region, [], [], [])
        views.append(view)
    return views
