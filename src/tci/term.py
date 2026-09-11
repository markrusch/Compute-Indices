# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Published term discounts: what sellers charge for commitment, relative to on-demand.

This is a research table, not a benchmark series, and it is not in the calculation path of
any print. It is computed from stored observations by a fixed rule, so any day's table can
be rebuilt from the database, and it is published beside the index rather than inside it.

WHAT IS MEASURED. For each seller, variant and configuration observed on the same day both
on-demand and at a committed tenor, the ratio committed / on-demand. A ratio is taken
within one seller, one configuration and one currency, so it needs no FX and carries no
composition effect: it is the discount that seller publishes for that commitment.

WHAT IS AGGREGATED. A cell (variant, tenor) is the median of one ratio per seller, and it
is shown only when at least MIN_SELLERS sellers publish that tenor. With fewer, the cell
is suppressed and says how many sellers it has. The same floor applies to contributed
prices (tci.contrib), for a different reason: there it protects contributors; here it
stops one seller's rate card being presented as a market figure.

ONE PRODUCT ON BOTH SIDES. A ratio pairs prices of the same product, not merely the same
GPU: Azure's reservations are keyed by VM size (an ND96is_noIB reservation is not paired
with the InfiniBand ND96isr on-demand price), OVHcloud's by plan code with the billing
suffix removed, Latitude's by plan, Civo's by instance size. Windows-licensed instances
are dropped, because the licence is not compute and OVHcloud prices it into the monthly
plan differently from the hourly one. A pair where the committed price is above the
on-demand price is not a discount and is excluded with that reason; on the first
fixtures it was always a product mismatch.

WHAT IS NOT USED. Committed prices whose tenor is a range or a floor
(term 'reserved_unspecified': Lambda's cluster rows, Hyperstack's "starting from"
reservations) never enter a ratio, because a ratio needs a tenor.

WHY SEGMENTS ARE SHOWN, NOT POOLED. Hyperscaler reservations and neocloud commitments are
different products at different discounts (Azure's 3-year reservation on an ND H100 v5 is
about 56% below on-demand; Civo's 36-month commitment on an H100 is about 17% below). A
median across both would describe neither, so every row carries its seller's segment.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from statistics import median
from typing import Any

TENOR_MONTHS: dict[str, int] = {
    "commit_1mo": 1, "commit_3mo": 3, "commit_6mo": 6, "reserved_1yr": 12,
    "reserved_2yr": 24, "reserved_3yr": 36, "reserved_5yr": 60,
}
MIN_SELLERS = 3
PRICED_TIERS = ("list", "executable")


@dataclass(frozen=True)
class SellerTerm:
    """One seller's published discount for one configuration and tenor on one day."""

    provider: str
    source: str
    gpu_model: str
    gpu_count: int | None
    region: str | None
    country: str | None
    currency: str
    tenor_months: int
    on_demand: float  # native currency, per GPU-hour
    committed: float  # native currency, per GPU-hour
    product: str | None = None

    @property
    def ratio(self) -> float:
        return self.committed / self.on_demand


@dataclass(frozen=True)
class Cell:
    gpu_model: str
    tenor_months: int
    segment: str
    n_sellers: int
    published: bool
    median_ratio: float | None
    min_ratio: float | None
    max_ratio: float | None
    sellers: tuple[str, ...]


def _native(row: Any) -> tuple[float, str]:
    import json

    try:
        raw = json.loads(row["raw_json"]) or {}
    except (TypeError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    currency = str(raw.get("currency") or "USD").upper()
    native = raw.get("price_native_per_gpu_hr")
    return (float(native) if native is not None else float(row["price_usd_per_gpu_hr"]),
            currency)


_BILLING_SUFFIXES = (".consumption", ".monthly.postpaid")


def _raw(row: Any) -> dict[str, Any]:
    import json

    try:
        raw = json.loads(row["raw_json"]) or {}
    except (TypeError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _extra(raw: dict[str, Any]) -> dict[str, Any]:
    extra = raw.get("extra")
    return extra if isinstance(extra, dict) else {}


def product_of(raw: dict[str, Any]) -> str | None:
    """The seller's own product identifier, identical on the on-demand and committed rows."""
    extra = _extra(raw)
    if raw.get("armSkuName"):
        return str(raw["armSkuName"])
    code = extra.get("plan_code")
    if isinstance(code, str):
        for suffix in _BILLING_SUFFIXES:
            if code.endswith(suffix):
                return code[: -len(suffix)]
        return code
    for key in ("plan", "size_name"):
        if extra.get(key):
            return str(extra[key])
    return None


def _licensed(raw: dict[str, Any]) -> bool:
    extra = _extra(raw)
    return bool(extra.get("windows_license_included")) or extra.get("os_family") == "windows"


@dataclass(frozen=True)
class Excluded:
    provider: str
    gpu_model: str
    product: str | None
    tenor_months: int
    ratio: float
    reason: str


def seller_terms(rows: Iterable[Any], excluded: list[Excluded] | None = None
                 ) -> list[SellerTerm]:
    """Pair every committed price with the same product's same-day on-demand price.

    Pairs that are not discounts are left out of the result and, when `excluded` is
    given, appended to it with the reason.
    """
    on_demand: dict[tuple, list[float]] = {}
    committed: dict[tuple, list[float]] = {}
    for row in rows:
        if row["tier"] not in PRICED_TIERS:
            continue
        raw = _raw(row)
        if _licensed(raw):
            continue
        price, currency = _native(row)
        if price <= 0:
            continue
        key = (row["provider"], row["source"], row["gpu_model"], row["gpu_count"],
               row["region"], row["country"], currency, product_of(raw))
        term = row["term"]
        if term == "on_demand":
            on_demand.setdefault(key, []).append(price)
        elif term in TENOR_MONTHS:
            committed.setdefault(key + (TENOR_MONTHS[term],), []).append(price)
    out = []
    for ckey, prices in sorted(committed.items(), key=lambda kv: str(kv[0])):
        base = on_demand.get(ckey[:-1])
        if not base:
            continue  # no same-day on-demand price for this configuration: no ratio
        provider, source, gpu_model, gpu_count, region, country, currency, product, tenor = (
            ckey)
        t = SellerTerm(
            provider=provider, source=source, gpu_model=gpu_model, gpu_count=gpu_count,
            region=region, country=country, currency=currency, tenor_months=tenor,
            on_demand=median(base), committed=median(prices), product=product,
        )
        if t.ratio > 1.0:
            if excluded is not None:
                excluded.append(Excluded(provider, gpu_model, product, tenor, round(t.ratio, 4),
                                         "committed price above on-demand"))
            continue
        out.append(t)
    return out


def cells(
    terms: Iterable[SellerTerm], segment_of: dict[str, str], min_sellers: int = MIN_SELLERS
) -> list[Cell]:
    """One cell per (variant, tenor, segment): the median of one ratio per seller.

    A seller with several configurations at a tenor contributes the median of its own
    ratios, so a seller that lists eight node sizes does not outvote one that lists one.
    """
    per_seller: dict[tuple[str, int, str], dict[str, list[float]]] = {}
    for t in terms:
        seg = segment_of.get(t.provider, "unclassified")
        per_seller.setdefault((t.gpu_model, t.tenor_months, seg), {}).setdefault(
            t.provider, []).append(t.ratio)
    out = []
    for (gpu_model, tenor, seg), by_seller in sorted(per_seller.items()):
        ratios = [median(v) for v in by_seller.values()]
        ok = len(ratios) >= min_sellers
        out.append(
            Cell(
                gpu_model=gpu_model, tenor_months=tenor, segment=seg,
                n_sellers=len(ratios), published=ok,
                median_ratio=round(median(ratios), 4) if ok else None,
                min_ratio=round(min(ratios), 4) if ok else None,
                max_ratio=round(max(ratios), 4) if ok else None,
                # Public rate cards: naming the sellers behind a suppressed cell discloses
                # nothing that is not already on their own pages.
                sellers=tuple(sorted(by_seller)),
            )
        )
    return out


@dataclass(frozen=True)
class ScheduleRow:
    """One seller's published discount for one variant and tenor, across its products."""

    provider: str
    gpu_model: str
    tenor_months: int
    median_ratio: float
    min_ratio: float
    max_ratio: float
    n_configs: int  # product x region x currency pairs behind the figure

    @property
    def discount(self) -> float:
        return 1.0 - self.median_ratio


def schedule(terms: Iterable[SellerTerm]) -> list[ScheduleRow]:
    """Each seller's own discount schedule: the median ratio per (variant, tenor).

    This is what a seller publishes, restated as one number per tenor. Unlike a cell it
    carries no minimum count, because it describes one seller's rate card and says so.
    """
    grouped: dict[tuple[str, str, int], list[float]] = {}
    for t in terms:
        grouped.setdefault((t.provider, t.gpu_model, t.tenor_months), []).append(t.ratio)
    return [
        ScheduleRow(provider=p, gpu_model=g, tenor_months=m,
                    median_ratio=round(median(r), 4), min_ratio=round(min(r), 4),
                    max_ratio=round(max(r), 4), n_configs=len(r))
        for (p, g, m), r in sorted(grouped.items())
    ]


@dataclass(frozen=True)
class PublishedSchedule:
    """A seller's discount schedule as the seller states it, in percent off on-demand."""

    provider: str
    url: str
    applies_to: str
    last_verified: str
    discounts: dict[int, float]


def load_schedules(path: Any, on_date: str) -> tuple[list[PublishedSchedule], list[str]]:
    """Schedules fresh on `on_date`, and the providers left out as stale."""
    from datetime import date

    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    max_age = int(raw.get("max_age_days", 90))
    fresh, stale = [], []
    for e in raw.get("schedules") or []:
        verified = str(e["last_verified"])
        age = (date.fromisoformat(on_date) - date.fromisoformat(verified)).days
        if age > max_age or age < 0:
            stale.append(str(e["provider"]))
            continue
        fresh.append(PublishedSchedule(
            provider=str(e["provider"]), url=str(e["url"]), applies_to=str(e["applies_to"]),
            last_verified=verified,
            discounts={int(k): float(v) for k, v in (e.get("discounts") or {}).items()},
        ))
    return fresh, stale


def schedule_terms(sched: PublishedSchedule, rows: Iterable[Any]) -> list[SellerTerm]:
    """The schedule applied to each variant the seller priced on demand that day.

    A variant the seller did not price that day gets nothing: the discount is of an
    on-demand price, so without one there is no term price to state.
    """
    base: dict[tuple[str, str], list[float]] = {}
    for row in rows:
        if (row["provider"] != sched.provider or row["term"] != "on_demand"
                or row["tier"] not in PRICED_TIERS):
            continue
        price, currency = _native(row)
        if price > 0:
            base.setdefault((row["gpu_model"], currency), []).append(price)
    out = []
    for (gpu_model, currency), prices in sorted(base.items()):
        od = median(prices)
        for months, off in sorted(sched.discounts.items()):
            out.append(SellerTerm(
                provider=sched.provider, source="published_schedule", gpu_model=gpu_model,
                gpu_count=None, region=None, country=None, currency=currency,
                tenor_months=months, on_demand=od, committed=od * (1 - off),
                product=sched.applies_to,
            ))
    return out
