# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Whether the price could be bought: the stock that sellers themselves publish.

Research beside the index. It reads rows the collectors already store and writes nothing to
the database; no print reads it.

WHY. A posted price is not a price anyone can pay when the seller has nothing to rent. For
much of 2026 on-demand H100 capacity was reported sold out across sellers while rate cards
did not move, and the index's own record shows the same thing from the inside: RunPod's
8-GPU H100 stock level changed 16 times between 5 September and 11 October 2026 while its
posted price changed once. The headline already reacts to stock, but only for the two
sellers whose rows disappear when they have none (RunPod's node-size floor, vast.ai's live
offer book). The rate-card sellers never drop out, sold out or not. This module publishes
what is known about stock, seller by seller, so the asymmetry is visible instead of buried
in the median.

WHAT COUNTS. Only what a seller publishes about its own capacity, read as published:

- RunPod: a stock level (High, Medium, Low, or none) for each node size, and the list of
  datacentres reporting stock of the GPU type. One level per node size, for every region
  at once; the EU/EEA count comes from the datacentre list.
- vast.ai: the live offer book, datacentre-verified hosts only (`in_index_scope`), counted
  as offers and GPUs located in the region.
- Hyperstack: its public stock feed, through the placement rule its collector already
  applies (a row is placed in a country only when the feed shows the model deployable in a
  named datacentre there).

Every other seller publishes a price and nothing about stock, and is reported as such. None
of this is utilisation: a seller with stock "Low" may be 40% or 95% rented, and nobody
outside knows which.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date as date_type
from datetime import timedelta

from tci import config

RUNPOD_LEVELS = ("High", "Medium", "Low")
# RunPod datacentre ids name their region first. EUR-IS-3 is Iceland, which is in the EEA.
_RUNPOD_EU_PREFIXES = ("EU-", "EUR-")


@dataclass(frozen=True)
class SellerStock:
    provider: str
    publishes_stock: bool
    # "yes", "no", a RunPod level, or None when nothing is published. `node2` is stock at
    # the headline's node-size floor (2 GPUs or more), `node8` at a full 8-GPU node.
    node2: str | None
    node8: str | None
    detail: str  # one line, in words, of what the seller published that day


@dataclass(frozen=True)
class Reading:
    date: str
    gpu_model: str
    sellers: tuple[SellerStock, ...]
    # None wherever nothing was recorded that day: the vast.ai offer book is stored from
    # 14 September 2026 and RunPod's stock fields from 5 September. Before those dates the
    # answer is unknown, which is not the same as no stock.
    vast_offers: int | None  # datacentre-verified offers located in the block
    vast_gpus: int | None
    vast_largest: int | None  # GPUs in the largest single offer
    runpod_node2: str | None
    runpod_node8: str | None
    runpod_eu_datacentres: int | None
    hyperstack_countries: tuple[str, ...]

    @property
    def publishers(self) -> int:
        """Sellers that publish anything about stock at all."""
        return sum(1 for s in self.sellers if s.publishes_stock)

    @property
    def publishing(self) -> int:
        """Sellers whose stock was actually recorded that day."""
        return sum(1 for s in self.sellers if s.node2 is not None)

    @property
    def with_stock(self) -> int:
        """Sellers that published stock at the headline's node floor."""
        return sum(1 for s in self.sellers if s.node2 not in (None, "no"))

    @property
    def with_8gpu_stock(self) -> int:
        return sum(1 for s in self.sellers if s.node8 not in (None, "no"))


def _level(raw: dict, key: str) -> str | None:
    level = (raw.get(key) or {}).get("stockStatus")
    return level if level in RUNPOD_LEVELS else None


def _runpod(
    conn: sqlite3.Connection, date: str, gpu_model: str
) -> tuple[bool, str | None, str | None, int | None]:
    """(row stored, best level at 2+ GPUs, level at 8 GPUs, EU/EEA datacentres in stock)."""
    row = conn.execute(
        "SELECT o.raw_json FROM observations o JOIN runs r ON o.run_id = r.run_id"
        " WHERE r.utc_date = ? AND o.source = 'runpod' AND o.gpu_model = ?"
        " ORDER BY o.id LIMIT 1",
        (date, gpu_model),
    ).fetchone()
    if row is None:
        return False, None, None, None
    raw = json.loads(row[0])
    if not any(k in raw for k in ("c2", "c4", "c8")):
        # A row from before the collector stored per-node stock (5 September 2026).
        return False, None, None, None
    levels = [lv for lv in (_level(raw, k) for k in ("c2", "c4", "c8")) if lv]
    best = min(levels, key=RUNPOD_LEVELS.index) if levels else None
    centres = raw.get("datacentres_in_stock")
    eu = (
        sum(1 for c in centres if str(c).startswith(_RUNPOD_EU_PREFIXES))
        if isinstance(centres, list) else None
    )
    return True, best, _level(raw, "c8"), eu


def _vast(
    conn: sqlite3.Connection, date: str, gpu_model: str, countries: frozenset[str]
) -> tuple[int, int, int]:
    marks = ",".join("?" for _ in countries) or "''"
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(num_gpus), 0), COALESCE(MAX(num_gpus), 0)"
        " FROM market_offers WHERE substr(ts_utc, 1, 10) = ? AND gpu_model = ?"
        f" AND in_index_scope = 1 AND country IN ({marks})",
        (date, gpu_model, *sorted(countries)),
    ).fetchone()
    return int(row[0]), int(row[1]), int(row[2])


def _hyperstack(
    conn: sqlite3.Connection, date: str, gpu_model: str
) -> dict[str, int]:
    """country -> largest node the stock feed shows deployable there."""
    rows = conn.execute(
        "SELECT o.country, MAX(COALESCE(o.gpu_count, 0)) FROM observations o"
        " JOIN runs r ON o.run_id = r.run_id"
        " WHERE r.utc_date = ? AND o.source = 'hyperstack' AND o.gpu_model = ?"
        " AND o.country IS NOT NULL AND o.term = 'on_demand' GROUP BY o.country",
        (date, gpu_model),
    ).fetchall()
    return {str(r[0]): int(r[1]) for r in rows}


def regional_sellers(
    conn: sqlite3.Connection, date: str, gpu_model: str, cls: str,
    countries: frozenset[str], population: frozenset[str], factors: config.Factors,
    block: str = "EU_EEA", lookback_days: int = 30,
) -> list[str]:
    """Panel sellers of the class that have priced it inside the region recently.

    The panel is region-agnostic: Voltage Park, CoreWeave, Civo and Together are admitted
    to the H100 class and never price it in the EU/EEA. Listing them on an EU stock table
    would pad it with sellers that were never candidates.
    """
    since = (date_type.fromisoformat(date) - timedelta(days=lookback_days)).isoformat()
    marks = ",".join("?" for _ in countries) or "''"
    seen = {
        str(r[0]) for r in conn.execute(
            "SELECT DISTINCT o.provider FROM observations o JOIN runs r ON o.run_id = r.run_id"
            " WHERE r.utc_date BETWEEN ? AND ? AND o.gpu_model = ?"
            f" AND o.country IN ({marks}) AND o.tier IN ('list', 'executable')",
            (since, date, gpu_model, *sorted(countries)),
        )
    }
    return sorted(
        p for p, pe in (factors.panel or {}).items()
        if p in seen and pe.segment in population
        and (pe.blocks is None or block in pe.blocks)
        and any(cls in classes for classes in pe.sources.values())
    )


def _vast_book_seen(conn: sqlite3.Connection, date: str) -> bool:
    """Whether the offer book was read at all that day (an empty book is still a reading)."""
    return conn.execute(
        "SELECT 1 FROM market_offers WHERE substr(ts_utc, 1, 10) = ? LIMIT 1", (date,)
    ).fetchone() is not None


def reading(
    conn: sqlite3.Connection, date: str, gpu_model: str = "H100_SXM", block: str = "EU_EEA"
) -> Reading:
    """What each seller in the class panel published about its stock on `date`."""
    factors = config.load_factors(for_date=date)
    # Snapshots before v0.4.0 hold the boolean False where Norway belongs (YAML 1.1 read
    # the bare token NO as false), which cannot be compared with a country code.
    countries = frozenset(c for c in factors.countries_of(block) if isinstance(c, str))
    cls = next(
        (c for c, mc in factors.model_classes.items() if mc.reference_variant == gpu_model),
        None,
    )
    population = factors.population_for("headline")
    if cls is None:
        raise ValueError(f"no model class prices {gpu_model}")
    sellers = regional_sellers(
        conn, date, gpu_model, cls, countries, population, factors, block
    )
    stored, best, level8, eu_centres = _runpod(conn, date, gpu_model)
    book_seen = _vast_book_seen(conn, date)
    v_offers, v_gpus, v_largest = (
        _vast(conn, date, gpu_model, countries) if book_seen else (None, None, None)
    )
    hs = {c: n for c, n in _hyperstack(conn, date, gpu_model).items() if c in countries}
    floor = factors.filters.min_gpu_count

    out: list[SellerStock] = []
    for p in sellers:
        if p == "runpod":
            if not stored:
                out.append(SellerStock(p, True, None, None, "stock not recorded that day"))
                continue
            out.append(SellerStock(
                p, True, best or "no", level8 or "no",
                f"stock at 2+ GPUs {best or 'none'}, at 8 GPUs {level8 or 'none'}; "
                f"{eu_centres or 0} EU/EEA datacentres reporting stock of the GPU type",
            ))
        elif p == "vast.ai":
            if v_largest is None:
                out.append(SellerStock(p, True, None, None, "offer book not recorded that day"))
                continue
            out.append(SellerStock(
                p, True, "yes" if v_largest >= floor else "no",
                "yes" if v_largest >= 8 else "no",
                f"{v_offers} verified offers with {v_gpus} GPUs in the region, the largest "
                f"{v_largest} GPUs",
            ))
        elif p == "hyperstack":
            where = ", ".join(f"{c} ({n} GPUs)" for c, n in sorted(hs.items()))
            largest = max(hs.values(), default=0)
            out.append(SellerStock(
                p, True, "yes" if largest >= floor else "no", "yes" if largest >= 8 else "no",
                f"stock feed shows the model deployable in {where}" if hs
                else "stock feed shows the model deployable nowhere in the region",
            ))
        else:
            out.append(SellerStock(
                p, False, None, None, "publishes a price and nothing about stock"
            ))
    return Reading(
        date=date, gpu_model=gpu_model, sellers=tuple(out), vast_offers=v_offers,
        vast_gpus=v_gpus, vast_largest=v_largest,
        runpod_node2=(best or "no") if stored else None,
        runpod_node8=(level8 or "no") if stored else None,
        runpod_eu_datacentres=eu_centres, hyperstack_countries=tuple(sorted(hs)),
    )


def collection_dates(conn: sqlite3.Connection, since: str | None = None) -> list[str]:
    q = "SELECT DISTINCT utc_date FROM runs WHERE source = 'runpod'"
    args: tuple[str, ...] = ()
    if since:
        q += " AND utc_date >= ?"
        args = (since,)
    return [r[0] for r in conn.execute(q + " ORDER BY utc_date", args)]
