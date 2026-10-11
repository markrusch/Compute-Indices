# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Rent against running cost, per GPU generation: how far each sits above switch-off.

Research beside the index. It reads stored observations, stored power prices and
config/cost_floor.yaml, writes nothing to the database, and no print reads it.

WHY. Lenders ask what a GPU will re-lease for when its contract ends, and auditors ask
whether a five- or six-year book life is credible. Both questions come down to one number
TCI's inputs can support without a model of anyone's balance sheet: the rent a GPU earns
against the cash it costs to keep it running. Power, colocation and the small operating
lines are that cash cost; depreciation and financing are not, because once the hardware is
bought they no longer decide whether to keep it on. A GPU whose rent falls to its running
cost is at the end of its economic life whatever its book life says.

Power is where this table meets the energy market. It is a small share of the rent (about
3% of the H100 headline at a 2025 German day-ahead price) and a large share of the running
cost, so it moves the floor and barely moves the rent.

RENTS. The on-demand prices the index already collects, under the rules live on the date:
the same admission, node floor and price band as the headline population, one price per
seller (its lowest qualifying offer), and the median over sellers. With fewer than
`min_sellers` the class shows no rent for that region. EU/EEA first; every country beside
it, because the EU/EEA A100, H200 and B200 populations are thin.

EVERY INPUT IS A RANGE. PUE, colocation rent and the operating lines are given as low and
high; the cost and the ratio are published as ranges. No midpoint is published, because no
midpoint was measured.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date as date_type
from datetime import timedelta
from pathlib import Path
from statistics import median
from typing import Any

from tci import config
from tci.normalise import normalise_observations

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "cost_floor.yaml"
CLASSES = ("A100", "H100", "H200", "B200")


@dataclass(frozen=True)
class Range:
    low: float
    high: float

    def __add__(self, other: Range) -> Range:
        return Range(self.low + other.low, self.high + other.high)

    def rounded(self, dp: int = 4) -> Range:
        return Range(round(self.low, dp), round(self.high, dp))


@dataclass(frozen=True)
class Rent:
    eur: float | None  # median over sellers, EUR per GPU-hour
    sellers: int


@dataclass(frozen=True)
class ClassRow:
    cls: str
    kw: float
    power: Range
    colocation: Range
    other: Range
    cost: Range
    eu: Rent
    world: Rent

    def ratio(self, rent: Rent) -> Range | None:
        """Rent over running cost: low against the high cost, high against the low."""
        if rent.eur is None:
            return None
        return Range(rent.eur / self.cost.high, rent.eur / self.cost.low).rounded(2)


@dataclass(frozen=True)
class PowerBasis:
    eur_mwh: float
    days: int  # stored days averaged; 0 means the configured fallback was used
    zones: tuple[str, ...]
    source: str


@dataclass(frozen=True)
class Table:
    date: str
    fx: float  # USD per EUR, from the day's prints
    power: PowerBasis
    rows: tuple[ClassRow, ...]
    config_version: str


def load_inputs(path: Path | None = None) -> dict[str, Any]:
    return config.read_yaml(path or CONFIG_PATH)


def power_basis(conn: sqlite3.Connection, date: str, cfg: dict[str, Any]) -> PowerBasis:
    """Trailing mean of stored day-ahead prices across the configured zones, or the fallback."""
    p = cfg["power"]
    zones = tuple(str(z) for z in p["zones"])
    start = (date_type.fromisoformat(date) - timedelta(days=int(p["window_days"]) - 1)).isoformat()
    marks = ",".join("?" for _ in zones)
    rows = conn.execute(
        "SELECT date, AVG(avg_price_eur_mwh) FROM overlay_power"
        f" WHERE date BETWEEN ? AND ? AND zone IN ({marks}) GROUP BY date",
        (start, date, *zones),
    ).fetchall()
    if len(rows) >= int(p["min_days"]):
        mean = sum(float(r[1]) for r in rows) / len(rows)
        return PowerBasis(round(mean, 2), len(rows), zones,
                          f"day-ahead mean of {', '.join(zones)} over {len(rows)} days")
    fb = p["fallback_eur_mwh"]
    return PowerBasis(float(fb["value"]), 0, zones, str(fb["source"]))


def _fx(conn: sqlite3.Connection, date: str) -> float | None:
    row = conn.execute(
        "SELECT fx_rate FROM daily_index WHERE date <= ? AND fx_rate IS NOT NULL"
        " ORDER BY date DESC, revision DESC LIMIT 1",
        (date,),
    ).fetchone()
    return float(row[0]) if row else None


def rents(
    conn: sqlite3.Connection, date: str, block: str, min_sellers: int
) -> dict[str, Rent]:
    """class -> median over sellers of each seller's lowest on-demand price, in EUR."""
    from tci.explain import _rows

    factors = config.load_factors(for_date=date)
    fx = _fx(conn, date)
    if fx is None:
        return {c: Rent(None, 0) for c in CLASSES}
    population = factors.population_for("headline")
    kwargs: dict[str, Any] = {}
    if block != "EU_EEA" and block not in factors.blocks:
        # The block did not exist under the rules live on that date (GLOBAL from v0.9.0).
        return {c: Rent(None, 0) for c in CLASSES}
    if block != "EU_EEA":
        kwargs = {"countries": factors.countries_of(block), "unplaced": factors.unplaced_in(block),
                  "block_id": block}
    obs = normalise_observations(_rows(conn, date), factors, fx_eur_usd=fx, **kwargs)
    out: dict[str, Rent] = {}
    for cls in CLASSES:
        lows: dict[str, float] = {}
        for o in obs:
            if o.model_class == cls and o.segment in population:
                lows[o.provider] = min(lows.get(o.provider, o.price_usd), o.price_usd)
        n = len(lows)
        out[cls] = Rent(round(median(lows.values()) / fx, 4) if n >= min_sellers else None, n)
    return out


def table(conn: sqlite3.Connection, date: str, cfg: dict[str, Any] | None = None) -> Table | None:
    cfg = cfg or load_inputs()
    fx = _fx(conn, date)
    if fx is None:
        return None
    pb = power_basis(conn, date, cfg)
    pue_lo, pue_hi = float(cfg["pue"]["low"]["value"]), float(cfg["pue"]["high"]["value"])
    colo = cfg["colocation_eur_kw_month"]
    hours = float(cfg["hours_per_month"])
    other = cfg["other_usd_per_gpu_hour"]
    other_r = Range(float(other["low"]) / fx, float(other["high"]) / fx)
    eu = rents(conn, date, "EU_EEA", int(cfg["min_sellers"]))
    world = rents(conn, date, "GLOBAL", int(cfg["min_sellers"]))
    rows = []
    for cls in CLASSES:
        kw = float(cfg["it_kw_per_gpu"][cls]["kw"])
        power = Range(kw * pue_lo * pb.eur_mwh / 1000.0, kw * pue_hi * pb.eur_mwh / 1000.0)
        colocation = Range(kw * float(colo["low"]) / hours, kw * float(colo["high"]) / hours)
        cost = power + colocation + other_r
        rows.append(ClassRow(
            cls=cls, kw=kw, power=power.rounded(), colocation=colocation.rounded(),
            other=other_r.rounded(), cost=cost.rounded(), eu=eu[cls], world=world[cls],
        ))
    return Table(date=date, fx=fx, power=pb, rows=tuple(rows),
                 config_version=str(cfg["version"]))


def as_json(t: Table, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or load_inputs()

    def rng(r: Range | None) -> dict[str, float] | None:
        return None if r is None else {"low": r.low, "high": r.high}

    return {
        "date": t.date,
        "unit": "EUR per GPU-hour",
        "fx_usd_per_eur": t.fx,
        "inputs_version": t.config_version,
        "power": {"eur_mwh": t.power.eur_mwh, "days": t.power.days,
                  "zones": list(t.power.zones), "basis": t.power.source,
                  # Required by CC BY 4.0 wherever the stored day-ahead prices are used.
                  "attribution": ("Day-ahead prices: Bundesnetzagentur | SMARD.de, via"
                                  " Energy-Charts (Fraunhofer ISE), CC BY 4.0"
                                  if t.power.days else None)},
        "classes": [
            {
                "class": r.cls, "it_kw_per_gpu": r.kw,
                "running_cost": rng(r.cost), "power": rng(r.power),
                "colocation": rng(r.colocation), "other": rng(r.other),
                "rent_eu": r.eu.eur, "sellers_eu": r.eu.sellers,
                "rent_to_cost_eu": rng(r.ratio(r.eu)),
                "rent_world": r.world.eur, "sellers_world": r.world.sellers,
                "rent_to_cost_world": rng(r.ratio(r.world)),
            }
            for r in t.rows
        ],
        "sources": {
            "it_kw_per_gpu": {c: cfg["it_kw_per_gpu"][c]["url"] for c in CLASSES},
            "pue": [cfg["pue"]["low"]["url"], cfg["pue"]["high"]["url"]],
            "colocation": cfg["colocation_eur_kw_month"]["url"],
            "other": cfg["other_usd_per_gpu_hour"]["url"],
            "power_fallback": cfg["power"]["fallback_eur_mwh"]["url"],
        },
        "excludes": "depreciation, financing, staff, grid fees and levies on power",
    }
