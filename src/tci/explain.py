# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Why a print moved, which seller's price it landed on, and what the EU-US basis is made of.

An observer, like `reproduce` and `intake`: it reads stored observations and prints, runs
the calculation code in memory, and writes nothing to the database. Nothing here is in the
calculation path or the methodology lock, and no print depends on it.

WHY IT EXISTS. On 24 September 2026 the headline rose 8.2%, from $3.49 to $3.78/GPU-hr,
and on 26 September it came back. RunPod and vast.ai had no qualifying stock on the 24th
and 25th, so their offers left the median and it landed higher up the book: exits moved
the print by +$0.30, price changes by sellers present on both days by -$0.02. The print
was right; a reader had no way to tell that move from a price rise. And on 27 of the 47
sessions the headline printed up to 11 October, RunPod had an offer exactly at the print.
Both facts were recoverable from the database and published nowhere.

WHAT A MOVE IS SPLIT INTO. A weighted median has no additive decomposition, so the split is
a path, taken in a fixed order, with every step priced under the rules live on the later
date:

1. methodology: the earlier print recomputed under the later date's rules, minus the
   earlier print as published. Zero unless a version took effect in between.
2. exits: the earlier day's book without the sellers that are absent on the later day.
3. price: the later day's offers from the sellers present on both days, against step 2.
   A change in a seller's offer set (a node size appearing or going out of stock) counts
   here, as does a EUR-quoted price moving with the exchange rate.
4. entries: the later day's full book, against step 3.

The four steps sum to the published move exactly. Another order would give other numbers
for the steps and the same total, which is why the order is stated wherever the split is
shown. If any intermediate book falls below the publication gate it has no value, and the
move is published as a total with the lists of sellers that left, joined and repriced.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from statistics import median

from tci import config
from tci.config import Factors
from tci.index import compute_print, provider_offers
from tci.normalise import NormalisedObs, normalise_observations

# Prices are stored to six decimals; anything closer than this is the same price.
_SAME = 1e-6
# A seller whose audit price moved by less than this did not reprice.
_REPRICE = 0.005


@dataclass(frozen=True)
class Book:
    """The offers one print is computed from, and the rules that price them."""

    date: str
    series: str
    factors: Factors
    fx: tuple[float, str] | None
    population: frozenset[str] | None
    offers: tuple[NormalisedObs, ...]

    def value(self, keep: frozenset[str] | None = None) -> float | None:
        """The print over this book, or over the sellers in `keep` only."""
        offers = [o for o in self.offers if keep is None or o.provider in keep]
        return compute_print(
            self.date, self.series, offers, self.factors, self.fx, population=self.population
        ).value_usd

    def providers(self) -> frozenset[str]:
        """Sellers with at least one offer that survives into the calculation."""
        kept, _ = provider_offers(list(self.offers), self.factors, self.date, self.population)
        return frozenset(kept)


def recorded_fx(conn: sqlite3.Connection, date: str) -> tuple[float, str] | None:
    """The FX the day's prints were published with (an input recorded on each print)."""
    row = conn.execute(
        "SELECT fx_rate, fx_date FROM daily_index WHERE date = ? AND fx_rate IS NOT NULL"
        " ORDER BY revision DESC LIMIT 1",
        (date,),
    ).fetchone()
    return (float(row[0]), str(row[1])) if row else None


def _rows(conn: sqlite3.Connection, date: str) -> list[sqlite3.Row]:
    # The same rows, in the same order, that compute_all_series reads.
    from tci.commands import _observations_for_date

    return _observations_for_date(conn, date)


def book(
    conn: sqlite3.Connection, date: str, series: str, *, rules_date: str | None = None
) -> Book | None:
    """Rebuild the book behind (date, series), priced under the rules live on `rules_date`.

    `rules_date` defaults to `date`, which reproduces the print as computed, with the FX the
    print recorded. Under another date's rules the FX is the one those rules would choose
    for `date`: the FX rule is a methodology parameter (v0.5.0 made it strictly T-1), so a
    change to it belongs in the methodology step. Basis, 7-day, composite and any series
    the rules do not define return None: they are not computed from offers.
    """
    from tci.collectors.fx import rate_for
    from tci.commands import print_definitions

    conn.row_factory = sqlite3.Row
    on = rules_date or date
    factors = config.load_factors(for_date=on)
    if on == date:
        fx = recorded_fx(conn, date)
    else:
        fx = rate_for(conn, date, strictly_before=factors.fx.strictly_before)
    rate = fx[0] if fx else None
    rows = _rows(conn, date)
    rs = factors.regional_series.get(series)
    if rs is not None:
        block = normalise_observations(
            rows, factors, fx_eur_usd=rate, countries=factors.countries_of(rs.block),
            unplaced=factors.unplaced_in(rs.block), block_id=rs.block, tiers=rs.tiers,
        )
        offers = tuple(o for o in block if o.model_class == rs.model_class)
        population = factors.population_for(rs.population)
    else:
        normalised = normalise_observations(rows, factors, fx_eur_usd=rate)
        definitions = print_definitions(
            factors, config.load_sovereign(for_date=on), {o.model_class for o in normalised}
        )
        if series not in definitions:
            return None
        _, population, predicate = definitions[series]
        offers = tuple(o for o in normalised if predicate(o))
    return Book(date, series, factors, fx, population, offers)


def median_setters(b: Book) -> tuple[str, ...]:
    """Sellers with an offer priced exactly at the print.

    The weighted median returns one offer's (possibly trimmed) price, and a trim bound is
    itself an offer's price, so a published value always belongs to at least one seller.
    Two sellers posting the same price both set it.
    """
    value = b.value()
    if value is None:
        return ()
    kept, _ = provider_offers(list(b.offers), b.factors, b.date, b.population)
    return tuple(sorted(
        p for p, offers in kept.items() if any(abs(o.price_usd - value) < _SAME for o in offers)
    ))


@dataclass(frozen=True)
class Step:
    name: str  # methodology | exits | price | entries
    delta: float | None


@dataclass(frozen=True)
class Move:
    series: str
    date: str
    prev_date: str
    value: float
    prev_value: float
    steps: tuple[Step, ...]
    exited: tuple[str, ...]
    entered: tuple[str, ...]
    repriced: tuple[tuple[str, float, float], ...]  # (seller, earlier, later)
    setters: tuple[str, ...]
    split: bool  # False when an intermediate book fell below the gate
    notes: tuple[str, ...] = field(default=())

    @property
    def total(self) -> float:
        return round(self.value - self.prev_value, 6)


def _audit_prices(conn: sqlite3.Connection, series: str, date: str) -> dict[str, float]:
    from tci.series_read import constituents_for

    return {
        r["provider"]: float(r["price_usd"])
        for r in constituents_for(conn, series, date)
        if r["included"] or r["exclusion_reason"] == "insufficient_sources"
    }


def explain_move(conn: sqlite3.Connection, series: str, date: str) -> Move | None:
    """The move from the last published print before `date` to the print on `date`."""
    from tci.series_read import head_print, previous_published

    conn.row_factory = sqlite3.Row
    head = head_print(conn, series, date)
    if head is None or head["value_usd"] is None:
        return None
    prev = previous_published(conn, series, date)
    if prev is None:
        return None
    prev_date, prev_value = prev
    later = book(conn, date, series)
    earlier = book(conn, prev_date, series, rules_date=date)
    earlier_as_printed = book(conn, prev_date, series)
    if later is None or earlier is None or earlier_as_printed is None:
        return None

    notes: list[str] = []
    value = float(head["value_usd"])
    # Both ends must reproduce from stored rows with today's code. If the later print does
    # not, a split built on a different number would not sum to the published move. If
    # the earlier one does not, whatever made it drift would be booked as "methodology".
    # Either is reproduce's finding to report, not this module's to absorb.
    for bk, published in ((later, value), (earlier_as_printed, float(prev_value))):
        v = bk.value()
        if v is None or abs(v - published) > _SAME:
            return None

    p_earlier, p_later = earlier.providers(), later.providers()
    both = p_earlier & p_later
    v_a = earlier.value()
    v_b = earlier.value(both)
    v_c = later.value(both)
    chain = [prev_value, v_a, v_b, v_c, value]
    names = ("methodology", "exits", "price", "entries")
    split = all(v is not None for v in chain)
    steps = tuple(
        Step(n, round(chain[i + 1] - chain[i], 6) if split else None)  # type: ignore[operator]
        for i, n in enumerate(names)
    )
    if not split:
        notes.append("an intermediate book fell below the publication gate")

    a, b = _audit_prices(conn, series, prev_date), _audit_prices(conn, series, date)
    repriced = tuple(
        (p, a[p], b[p]) for p in sorted(both) if p in a and p in b and abs(a[p] - b[p]) >= _REPRICE
    )
    return Move(
        series=series, date=date, prev_date=prev_date, value=value,
        prev_value=float(prev_value), steps=steps,
        exited=tuple(sorted(p_earlier - p_later)), entered=tuple(sorted(p_later - p_earlier)),
        repriced=repriced, setters=median_setters(later), split=split, notes=tuple(notes),
    )


def setter_history(
    conn: sqlite3.Connection, series: str, dates: list[str]
) -> dict[str, tuple[str, ...]]:
    """date -> the sellers that set the published print, for every date that printed."""
    from tci.series_read import head_print

    out: dict[str, tuple[str, ...]] = {}
    for d in dates:
        head = head_print(conn, series, d)
        if head is None or head["value_usd"] is None:
            continue
        b = book(conn, d, series)
        if b is None:
            continue
        v = b.value()
        if v is None or abs(v - float(head["value_usd"])) > _SAME:
            continue
        out[d] = median_setters(b)
    return out


# ---------------------------------------------------------------------- the EU-US basis

@dataclass(frozen=True)
class BasisMakeup:
    """What one day's basis print is made of, from the two legs' audit sets."""

    date: str
    basis: float | None
    both: tuple[tuple[str, float, float], ...]  # sellers in both legs, (seller, lead, ref)
    identical: int  # of those, how many charge the same price in both regions
    like_for_like: float | None  # median of the within-seller differences
    lead_setters: tuple[str, ...]
    reference_setters: tuple[str, ...]

    @property
    def shared_setter(self) -> bool:
        return bool(set(self.lead_setters) & set(self.reference_setters))


def basis_makeup(
    conn: sqlite3.Connection, date: str, basis_series: str, lead: str, reference: str
) -> BasisMakeup | None:
    """The sellers both legs share, what each charges on each side, and who set each leg.

    A median minus a median does not split into a price part and a mix part, so this does
    not try. It states two things a reader of a basis needs: the like-for-like difference
    (what the sellers present in both regions charge on each side) and whether one seller's
    single price set both legs, in which case the basis is zero by construction.
    """
    from tci.series_read import constituents_for, head_print

    conn.row_factory = sqlite3.Row
    head = head_print(conn, basis_series, date)
    if head is None:
        return None

    def included(series: str) -> dict[str, float]:
        return {
            r["provider"]: float(r["price_usd"])
            for r in constituents_for(conn, series, date) if r["included"]
        }

    a, b = included(lead), included(reference)
    both = tuple((p, a[p], b[p]) for p in sorted(a.keys() & b.keys()))
    diffs = [x - y for _, x, y in both]

    def setters(series: str) -> tuple[str, ...]:
        bk = book(conn, date, series)
        return median_setters(bk) if bk is not None else ()

    return BasisMakeup(
        date=date,
        basis=head["value_usd"],
        both=both,
        identical=sum(1 for d in diffs if abs(d) < _REPRICE),
        like_for_like=round(median(diffs), 6) if diffs else None,
        lead_setters=setters(lead) if head["value_usd"] is not None else (),
        reference_setters=setters(reference) if head["value_usd"] is not None else (),
    )


@dataclass(frozen=True)
class ReplaySession:
    """One session of the basis replayed with region-flat sellers taken out of both legs."""

    date: str
    us_sellers: tuple[str, ...]  # qualifying in the US leg under the replay's rules
    region_flat: tuple[str, ...]  # in both legs at the same price
    us_left: int  # US sellers left once the region-flat ones are dropped
    us_value: float | None  # the US leg over those alone; None where it falls below the gate


def basis_replay(
    conn: sqlite3.Connection, start: str, end: str, rules_date: str,
    lead: str = "EU-CRI-H100", reference: str = "EU-CRI-H100-US",
) -> list[ReplaySession]:
    """Would a basis over sellers that price the regions differently ever have printed?

    The question the basis-composition finding left: if the sellers that charge one price
    everywhere are taken out of both legs, what remains of the US leg? Every session from
    `start` to `end` with a collection is rebuilt under the rules live on `rules_date`.
    The CHANGELOG entry of 11 October 2026 quotes this over 12 September to 11 October.
    """
    from datetime import date as date_type
    from datetime import timedelta

    def qualifying(b: Book) -> dict[str, float]:
        p = compute_print(b.date, b.series, list(b.offers), b.factors, b.fx,
                          population=b.population)
        return {c.provider: c.price_usd for c in p.constituents
                if c.included or c.exclusion_reason == "insufficient_sources"}

    conn.row_factory = sqlite3.Row
    out: list[ReplaySession] = []
    d, last = date_type.fromisoformat(start), date_type.fromisoformat(end)
    while d <= last:
        day = d.isoformat()
        d += timedelta(days=1)
        if conn.execute("SELECT 1 FROM runs WHERE utc_date = ? AND source <> 'index' LIMIT 1",
                        (day,)).fetchone() is None:
            continue
        eu, us = book(conn, day, lead, rules_date=rules_date), book(conn, day, reference,
                                                                     rules_date=rules_date)
        if eu is None or us is None:
            continue
        a, b = qualifying(eu), qualifying(us)
        flat = {p for p in a.keys() & b.keys() if abs(a[p] - b[p]) < _REPRICE}
        keep = frozenset(b) - flat
        out.append(ReplaySession(day, tuple(sorted(b)), tuple(sorted(flat)), len(keep),
                                 us.value(keep)))
    return out
