# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Committed-cost curves and the forwards bootstrapped from them.

A research output beside the index, not a series in it, and not in the calculation path of
any print. Built from stored observations by a fixed rule, so any past date's curve follows
from the database. Pure functions: no I/O, no database, no config, so every number here is
testable against a hand-worked example.

THREE OBJECTS, AND WHY THEY ARE KEPT APART. `K(m)` is what a named seller charges per
GPU-hour to lock m months starting today. `PHI(m1,m2)` is the marginal cost per GPU-hour of
the period between two tenors, bootstrapped from K. `S*(m)` is the fixed rate on a swap
against the spot index, which is a claim about future spot and is NOT what a bootstrap of a
rate card produces. They are related by `S*(m) = PHI(m) + pi(m)`, where the term premium
`pi` covers the optionality the buyer surrenders, counterparty credit and vendor lock-in,
against whatever capacity certainty is worth. `pi` is large and, on public rate cards,
unmeasured. Vendor products routinely label the bootstrap a forward spot curve. This module
will not: `Curve.kind` carries the distinction and `build` refuses to make an `S*` curve out
of administered prices.

CUMULATIVE COST IS THE PRIMITIVE. A GPU-hour is a flow. You pay per hour of delivery,
nothing is reinvested and nothing compounds, so the cost of a contract is rate times hours
and the cost of two consecutive contracts is the sum of two such products:

    h(m) = m * HOURS_PER_MONTH
    C(m) = h(m) * K(m)
    PHI(m1, m2) = (C(m2) - C(m1)) / (h(m2) - h(m1))

Interpolating on C rather than on K is what makes the curve roll-consistent. Interpolate a
9-month rate halfway between the 6- and 12-month rates instead and the implied forwards
depend on which knots you happened to have, which is not a property a published number can
have.

Spot does not enter C. The cost of locking zero months is zero whatever on-demand costs, so
PHI(0, m1) is simply K(m1), the price of the shortest contract the seller sells. Spot is the
anchor for the ratio K(m)/spot, which is what gets pooled, and the ceiling above which a
"commitment" is not a discount.

NO-ARBITRAGE IS ONE CONDITION ON C: it must be non-decreasing, equivalently every forward
must be non-negative. If C(24) < C(12) a buyer takes the 24-month contract, uses twelve
months, abandons the tail and has bought a year below the one-year price. That needs only
free disposal, which a reservation has. It does not need resale, which a reservation does
not have, and that is also why an interpolated rate between two knots is a convention and
not a replication price. A violation gaps its segment and is reported. It is never
projected away: monotone regression would produce a complete curve by restating a seller's
published price as a number that seller does not charge.

WHY THE CHIP-INVARIANCE TEST EXISTS. A term price is forward-looking only if somebody set it
while looking forward, and most of these were not. On 15 September 2026 Verda published
0.9200 of on-demand at twelve months on all six of A100, A100-40GB, B200, B300, H100 and
H200, and Latitude published 0.3500 on a B300, an H100 and an RTX PRO 6000. A schedule that
cannot tell a Blackwell from a workstation card carries no information about any particular
chip's future price; bootstrap it and the result is today's spot times a constant.
`classify` finds these automatically rather than by anyone's judgement, and the tag it
assigns is what gates a seller out of an `S*` curve.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from statistics import median
from typing import Literal

# 365.25 * 24 / 12 = 730.5. A convention for converting a tenor to hours delivered, not a
# fact about any month; it cancels out of every ratio and every forward rate, and matters
# only to the dollar value of C.
HOURS_PER_MONTH = 730.0

MIN_PROVIDERS = 3          # pooled curves only; a seller's own rate card needs no second
MAX_SPAN_MONTHS = 6        # a forward from a wider gap publishes, flagged
MAX_TENOR_MONTHS = 60      # never extrapolate; this only bounds what may be read in

# Half a percentage point of discount between two chip generations is noise, not a view.
CHIP_INVARIANCE_TOL = 0.005

PriceFormation = Literal[
    "administered_uniform",          # one schedule across chip generations
    "administered_differentiated",   # a rate card that varies by chip
    "administered_untested",         # too few chips observed to run the test
    "market_quoted",                 # a broker or OTC quote for a chip and tenor
    "transacted",                    # a deal that was actually done
]
CurveKind = Literal["committed", "forward_spot"]

# Only a price somebody set against a specific chip, in a market, can carry a view about
# that chip's future. Everything else builds a committed-cost curve and stops there.
FORWARD_SPOT_FORMATIONS: tuple[PriceFormation, ...] = ("market_quoted", "transacted")


@dataclass(frozen=True)
class CurveKey:
    """What a curve is of. `provider` is None for a pooled curve.

    `region_block` is a field with one value today. The US leg and the per-block series
    are the reason it exists now rather than later: adding them should be a value, not a
    change of shape.
    """

    gpu_model: str
    provider: str | None = None
    segment: str | None = None
    region_block: str = "EU"

    def __str__(self) -> str:
        who = self.provider or f"pooled:{self.segment or 'all'}"
        return f"{self.region_block}/{self.gpu_model}/{who}"


@dataclass(frozen=True)
class Knot:
    """One tenor on one curve. m = 0 is the spot anchor and carries zero hours and cost."""

    tenor_months: int
    rate: float                      # $/GPU-hour, native currency
    price_formation: PriceFormation
    observed: bool = True

    @property
    def hours(self) -> float:
        return self.tenor_months * HOURS_PER_MONTH

    @property
    def cum_cost(self) -> float:
        return self.hours * self.rate


@dataclass(frozen=True)
class Dropped:
    tenor_months: int
    rate: float
    reason: str


@dataclass(frozen=True)
class Curve:
    key: CurveKey
    date: str
    currency: str
    knots: tuple[Knot, ...]          # sorted by tenor; knots[0] is the spot anchor at m = 0
    kind: CurveKind = "committed"
    dropped: tuple[Dropped, ...] = ()

    @property
    def spot(self) -> float:
        return self.knots[0].rate

    @property
    def max_tenor_months(self) -> int:
        return self.knots[-1].tenor_months

    @property
    def tenors(self) -> tuple[int, ...]:
        """The committed tenors, without the spot anchor."""
        return tuple(k.tenor_months for k in self.knots[1:])

    def ratio(self, tenor_months: int) -> float | None:
        """K(m) / spot at an observed tenor. The shape, with the level divided out."""
        for k in self.knots[1:]:
            if k.tenor_months == tenor_months:
                return k.rate / self.spot
        return None


@dataclass(frozen=True)
class Forward:
    """PHI(m1, m2): the rate for the period between two knots.

    A break-even, not a forecast. It is the average spot rate over (m1, m2] at which
    locking to m2 and locking to m1 then rolling cost the same. Read as a forecast of spot
    it is biased low, by the value of the optionality a committed buyer gives up.
    """

    m1: int
    m2: int
    rate: float
    span_months: int
    wide_span: bool


@dataclass(frozen=True)
class Violation:
    m1: int
    m2: int
    reason: str
    detail: str


def classify(
    ratios_by_model: dict[str, dict[int, float]], tol: float = CHIP_INVARIANCE_TOL
) -> PriceFormation:
    """Whether one seller's schedule distinguishes between chips.

    `ratios_by_model` is {gpu_model: {tenor_months: K(m)/spot}} for a single seller.
    A schedule identical across chip generations at every tenor it prices is a commercial
    policy, and its bootstrap carries no chip-specific information. With fewer than two
    chips at every tenor the test cannot run, which is reported rather than assumed away:
    a single-chip seller is untested, not uniform.
    """
    tenors: set[int] = set()
    for per_tenor in ratios_by_model.values():
        tenors |= set(per_tenor)
    testable = False
    for tenor in sorted(tenors):
        seen = [r[tenor] for r in ratios_by_model.values() if tenor in r]
        if len(seen) < 2:
            continue
        testable = True
        if max(seen) - min(seen) > tol:
            return "administered_differentiated"
    return "administered_uniform" if testable else "administered_untested"


def build(
    key: CurveKey,
    date: str,
    spot: float,
    points: Iterable[tuple[int, float]],
    *,
    currency: str = "USD",
    price_formation: PriceFormation = "administered_untested",
    kind: CurveKind = "committed",
    max_tenor_months: int = MAX_TENOR_MONTHS,
) -> Curve | None:
    """A curve from a spot anchor and (tenor, rate) pairs at observed tenors.

    None when nothing survives, so a caller cannot publish a curve that is only its spot
    anchor. Points at or above spot are dropped with a reason rather than admitted: a
    "commitment" priced at exactly on-demand (OVHcloud's H200 monthly plan, ratio 1.0000
    on 15 September) is the same product billed monthly, and it would otherwise contribute
    a knot stating a term discount of zero as though it were one.
    """
    if kind == "forward_spot" and price_formation not in FORWARD_SPOT_FORMATIONS:
        raise ValueError(
            f"{key}: a forward spot curve cannot be built from {price_formation!r} prices."
            " S* = PHI + pi, and an administered rate card measures neither term."
        )
    if spot <= 0:
        return None
    kept: dict[int, list[float]] = {}
    dropped: list[Dropped] = []
    for tenor, rate in points:
        if tenor <= 0:
            dropped.append(Dropped(tenor, rate, "tenor is not a future period"))
        elif tenor > max_tenor_months:
            dropped.append(Dropped(tenor, rate, f"tenor beyond {max_tenor_months} months"))
        elif rate <= 0:
            dropped.append(Dropped(tenor, rate, "non-positive rate"))
        elif rate >= spot:
            dropped.append(Dropped(tenor, rate, "committed price at or above on-demand"))
        else:
            kept.setdefault(tenor, []).append(rate)
    if not kept:
        return None
    # Several configurations at one tenor contribute their median, as in tci.term, so a
    # seller listing eight node sizes gets one knot at twelve months, not eight.
    knots = (Knot(0, spot, price_formation),) + tuple(
        Knot(t, median(kept[t]), price_formation) for t in sorted(kept)
    )
    return Curve(key=key, date=date, currency=currency, knots=knots, kind=kind,
                 dropped=tuple(dropped))


def forwards(
    curve: Curve, max_span_months: int = MAX_SPAN_MONTHS
) -> tuple[list[Forward], list[Violation]]:
    """Bootstrap PHI between consecutive knots, and the segments that cannot carry one.

    A negative forward is an arbitrage violation (see the module docstring) and its segment
    publishes nothing. One bad segment gaps itself and leaves the rest of the curve alone,
    because each forward depends on exactly two knots and no others.
    """
    out: list[Forward] = []
    bad: list[Violation] = []
    for a, b in zip(curve.knots, curve.knots[1:], strict=False):
        rate = (b.cum_cost - a.cum_cost) / (b.hours - a.hours)
        span = b.tenor_months - a.tenor_months
        if rate < 0:
            bad.append(Violation(
                a.tenor_months, b.tenor_months, "negative implied forward",
                f"C({b.tenor_months}) = {b.cum_cost:.2f} is below C({a.tenor_months})"
                f" = {a.cum_cost:.2f}: locking the longer contract and abandoning the tail"
                " buys the shorter period below its own price",
            ))
            continue
        out.append(Forward(a.tenor_months, b.tenor_months, rate, span, span > max_span_months))
    return out, bad


def rate_at(curve: Curve, tenor_months: float) -> float | None:
    """K(m) between the shortest and longest committed tenors, by flat-forward interpolation.

    Linear on C, which is what keeps the implied forwards independent of the knots chosen.
    Outside that range this returns None, at both ends. Past the longest tenor, because
    extrapolating a term price is inventing one. Before the shortest, because the only thing
    bracketing it is spot, and spot is not a cost: interpolating from C(0) = 0 would quote
    Azure a three-month reservation at its twelve-month price, and Azure sells no
    three-month reservation. A buyer who needs three months there is rolling on-demand.
    """
    committed = curve.knots[1:]
    if not committed:
        return None
    if tenor_months < committed[0].tenor_months or tenor_months > curve.max_tenor_months:
        return None
    hours = tenor_months * HOURS_PER_MONTH
    for a, b in zip(committed, committed[1:], strict=False):
        if a.hours <= hours <= b.hours:
            w = (hours - a.hours) / (b.hours - a.hours)
            return (a.cum_cost + w * (b.cum_cost - a.cum_cost)) / hours
    return committed[0].rate  # a single committed knot, asked for at exactly its tenor


@dataclass(frozen=True)
class PooledPoint:
    """One tenor of a pooled shape curve: the median of one ratio per seller."""

    tenor_months: int
    n_providers: int
    published: bool
    median_ratio: float | None
    min_ratio: float | None
    max_ratio: float | None
    providers: tuple[str, ...]


def pool(
    curves: Sequence[Curve], min_providers: int = MIN_PROVIDERS
) -> list[PooledPoint]:
    """Pool the shape, never the level: the cross-seller median of K(m)/spot per tenor.

    Pooling levels breaks the moment the seller mix differs between tenors, which it does.
    On 15 September twelve months had four sellers and twenty-four had two, so a pooled
    level would fall between them because Azure left the sample, and the curve would read
    that composition change as a term discount. A ratio is within one seller, so a seller
    arriving or leaving changes who votes on the shape and cannot move the level.

    Every curve must be of the same GPU model and segment. Pooling across a segment
    boundary averages a hyperscaler reservation with a neocloud commitment at a 4.8x level
    gap, and the resulting number describes neither.
    """
    if not curves:
        return []
    models = {c.key.gpu_model for c in curves}
    segments = {c.key.segment for c in curves}
    if len(models) > 1 or len(segments) > 1:
        raise ValueError(f"pool: mixed keys, models={sorted(models)} segments={segments}")
    names = [c.key.provider for c in curves]
    if None in names or len(set(names)) != len(names):
        raise ValueError("pool: every curve must be one named seller, each seller once")
    per_tenor: dict[int, dict[str, float]] = {}
    for c in curves:
        assert c.key.provider is not None
        for k in c.knots[1:]:
            per_tenor.setdefault(k.tenor_months, {})[c.key.provider] = k.rate / c.spot
    out: list[PooledPoint] = []
    for tenor in sorted(per_tenor):
        by_provider = per_tenor[tenor]
        ratios = list(by_provider.values())
        ok = len(ratios) >= min_providers
        out.append(PooledPoint(
            tenor_months=tenor, n_providers=len(ratios), published=ok,
            median_ratio=round(median(ratios), 4) if ok else None,
            min_ratio=round(min(ratios), 4) if ok else None,
            max_ratio=round(max(ratios), 4) if ok else None,
            # Public rate cards: naming the sellers behind a suppressed point discloses
            # nothing that is not already on their own pages.
            providers=tuple(sorted(by_provider)),
        ))
    return out


def apply_level(
    points: Sequence[PooledPoint],
    spot_index: float,
    key: CurveKey,
    date: str,
    *,
    currency: str = "USD",
    price_formation: PriceFormation = "administered_untested",
) -> Curve | None:
    """The aggregate curve: the published index level times the pooled discount shape.

    Level from the index, shape from the discount curve, sourced independently. The
    separation is worth having for itself, because "compute got cheaper" and "commitment
    got cheaper" are different events with different causes, and it is what lets a quality
    adjustment move the level without touching the shape.

    The result still has to go through `forwards`, and can still gap a segment, but for
    one reason only. With the same sellers at both tenors the median cannot break NA-1: if
    every seller has 24 x r24 >= 12 x r12, every order statistic of r24 dominates the same
    order statistic of r12 / 2, the median included. A pooled violation therefore means
    different sellers voted at different tenors, which makes the arbitrage check on an
    aggregate a composition detector as much as a pricing one.
    """
    usable = [(p.tenor_months, p.median_ratio * spot_index)
              for p in points if p.published and p.median_ratio is not None]
    if not usable or spot_index <= 0:
        return None
    return build(key, date, spot_index, usable, currency=currency,
                 price_formation=price_formation, kind="committed")
