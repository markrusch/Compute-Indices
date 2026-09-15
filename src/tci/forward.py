# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""The forward spot estimate: what the index is expected to average over a coming window.

A research output beside the index. Pure functions, no I/O: `tci.forward_data` assembles the
inputs from the database as they were knowable on the estimate date, and this module does
the arithmetic, so every number can be checked against a hand-worked example.

WHAT KIND OF ESTIMATE. A GPU-hour cannot be stored, so spot does not pin a forward by
cash-and-carry the way it does for a metal. The forward of a non-storable is an
expectation, a martingale in the estimate date. With no traded forward on this index, the
estimate here is that expectation under a stated model, and nothing more is claimed for it.

WHY A LOCAL LEVEL AND NOT A RANDOM WALK ON THE PRINT. The headline is a weighted median of a
handful of rate cards. On the record to 15 September 2026 it took one of four values on most
days, the lag-1 autocorrelation of its daily log changes was -0.32, and one print (3.49 on 12
September, back to 3.25 the next day) supplied 56% of its realised variance. Treating the
last print as the martingale would have forecast that one print forward for a year. The
print is modelled instead as a level plus measurement noise; the filtered level is the part
that persists, and it is what gets carried forward.

WHY NO DRIFT. The prints available to estimate one span 59 days, and the resulting standard
error is larger than any drift worth arguing about. A prior would decide the answer, so the
drift is zero and says so.

WHY THE LOCKABLE COST IS NOT A BOUND ON THE INDEX. Free disposal bounds what a buyer need
pay: nobody pays more for a window than the cheapest contract that covers it. It does not
bound the index. The index is a median of offers, a median cannot be replicated by renting
one offer, and an offer nobody has rented can be repriced by its host tomorrow. The two
numbers answer two different hedging questions and are published as such.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date as _date
from statistics import NormalDist, median
from typing import Any

# Contract tenor in months -> the window length in days it is matched to. A term price is
# used only at exactly its own tenor; nothing is interpolated between tenors.
TENOR_DAYS: dict[int, int] = {1: 30, 3: 91, 6: 182, 12: 365}


@dataclass(frozen=True)
class Params:
    version: str
    anchor_series: str
    horizons_days: tuple[int, ...]
    min_prints: int
    grid_log10_q: tuple[float, float, int]
    grid_log10_r: tuple[float, float, int]
    quantiles: tuple[float, ...]
    term_min_sellers: int
    term_admit: frozenset[str]
    prepaid_credit_spread: float
    min_printed_share: float
    min_nonoverlapping_windows: int

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> Params:
        def grid(key: str) -> tuple[float, float, int]:
            lo, hi, n = raw[key]
            return float(lo), float(hi), int(n)

        return cls(
            version=str(raw["version"]),
            anchor_series=str(raw["anchor_series"]),
            horizons_days=tuple(int(h) for h in raw["horizons_days"]),
            min_prints=int(raw["min_prints"]),
            grid_log10_q=grid("grid_log10_q"),
            grid_log10_r=grid("grid_log10_r"),
            quantiles=tuple(float(p) for p in raw["quantiles"]),
            term_min_sellers=int(raw["term"]["min_sellers"]),
            term_admit=frozenset(str(f) for f in raw["term"]["admit_formations"]),
            prepaid_credit_spread=float(raw["prepaid_credit_spread"]),
            min_printed_share=float(raw["calibration"]["min_printed_share"]),
            min_nonoverlapping_windows=int(raw["calibration"]["min_nonoverlapping_windows"]),
        )


def day(iso: str) -> int:
    """A calendar date as an integer day count, so gaps are plain subtraction."""
    return _date.fromisoformat(iso).toordinal()


def iso(ordinal: int) -> str:
    return _date.fromordinal(ordinal).isoformat()


def digest(obj: Any) -> str:
    """A short, stable fingerprint of an estimate's inputs, for idempotent ledger writes."""
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# --- the local level ---------------------------------------------------------------------


def _linspace(lo: float, hi: float, n: int) -> list[float]:
    if n < 2:
        return [lo]
    step = (hi - lo) / (n - 1)
    return [lo + i * step for i in range(n)]


def kalman(points: Sequence[tuple[int, float]], q: float, r: float) -> tuple[float, float, float]:
    """Filter a local level through dated log values; return (level, variance, loglik).

    `points` are (day, log value) with strictly increasing days. A gap of g days adds g times
    the daily level variance before the next observation, which is how a missing print is
    handled without inventing one. The first observation initialises the level diffusely, so
    it contributes no likelihood.
    """
    (first_day, first_value), rest = points[0], points[1:]
    level, variance, loglik = first_value, r, 0.0
    prev = first_day
    for d, y in rest:
        predicted = variance + q * (d - prev)
        total = predicted + r
        innovation = y - level
        loglik -= 0.5 * (math.log(2.0 * math.pi * total) + innovation * innovation / total)
        gain = predicted / total
        level += gain * innovation
        variance = predicted * (1.0 - gain)
        prev = d
    return level, variance, loglik


@dataclass(frozen=True)
class LevelFit:
    q: float
    r: float
    level: float        # filtered log level at the last observation
    variance: float     # posterior variance of that level
    n: int
    last_day: int
    loglik: float


def fit_level(
    points: Sequence[tuple[int, float]],
    grid_q: tuple[float, float, int],
    grid_r: tuple[float, float, int],
) -> LevelFit | None:
    """Maximum likelihood over a fixed grid of (q, r). Deterministic: ties keep the first."""
    if len(points) < 2:
        return None
    best: tuple[float, float, float, float, float] | None = None
    for lq in _linspace(*grid_q):
        for lr in _linspace(*grid_r):
            q, r = 10.0 ** lq, 10.0 ** lr
            level, variance, loglik = kalman(points, q, r)
            if best is None or loglik > best[0] + 1e-12:
                best = (loglik, q, r, level, variance)
    assert best is not None
    loglik, q, r, level, variance = best
    return LevelFit(q=q, r=r, level=level, variance=variance, n=len(points),
                    last_day=points[-1][0], loglik=loglik)


# --- the window mean ---------------------------------------------------------------------


@dataclass(frozen=True)
class WindowForecast:
    mean: float
    log_variance: float
    quantiles: tuple[tuple[float, float], ...]

    def at(self, p: float) -> float:
        return dict(self.quantiles)[p]


def _lognormal(mean: float, log_variance: float, probs: Sequence[float]) -> WindowForecast:
    nd = NormalDist()
    sd = math.sqrt(log_variance)
    qs = tuple((p, mean * math.exp(-log_variance / 2.0 + nd.inv_cdf(p) * sd)) for p in probs)
    return WindowForecast(mean=mean, log_variance=log_variance, quantiles=qs)


def window_forecast(fit: LevelFit, horizon_days: int, probs: Sequence[float]) -> WindowForecast:
    """The mean of the anchor over the next `horizon_days` days, as a distribution.

    Mean: exp(level + P/2), the expected price level today, carried unchanged because the
    price level is taken to be a martingale. Log-variance of the window mean: today's level
    uncertainty P, plus the variance of the average of a random walk over h days,
    q(h+1)(2h+1)/(6h), plus measurement noise averaged over h prints, r/h. The window mean is
    then taken as lognormal with that mean and variance.
    """
    h = horizon_days
    walk = fit.q * (h + 1) * (2 * h + 1) / (6.0 * h)
    log_variance = fit.variance + walk + fit.r / h
    return _lognormal(math.exp(fit.level + fit.variance / 2.0), log_variance, probs)


def combine(parts: Sequence[tuple[int, WindowForecast]], probs: Sequence[float]) -> WindowForecast:
    """Day-weighted combination when a window spans methodology versions."""
    total = sum(n for n, _ in parts)
    mean = sum(n * f.mean for n, f in parts) / total
    log_variance = sum(n * f.log_variance for n, f in parts) / total
    return _lognormal(mean, log_variance, probs)


def version_days(
    start_day: int, horizon_days: int, schedule: Sequence[tuple[int, str]]
) -> dict[str, int]:
    """How many days of (start, start + h] fall under each version.

    `schedule` is (first day in effect, version), sorted, and must include a version in
    effect on `start_day + 1`.
    """
    out: dict[str, int] = {}
    for d in range(start_day + 1, start_day + horizon_days + 1):
        live = [v for effective, v in schedule if effective <= d]
        if not live:
            raise ValueError(f"no version in effect on {iso(d)}")
        out[live[-1]] = out.get(live[-1], 0) + 1
    return out


# --- the term-implied diagnostic ---------------------------------------------------------


@dataclass(frozen=True)
class SellerRatio:
    seller: str              # a provider, or "vast.ai:<host>" for one marketplace host
    tenor_months: int
    ratio: float             # pay-as-delivered committed price / on-demand price
    formation: str
    source: str


@dataclass(frozen=True)
class TermImplied:
    value: float | None
    n_sellers: int
    sellers: tuple[str, ...]


def term_implied(
    mean: float,
    ratios: Iterable[SellerRatio],
    tenor_months: int,
    min_sellers: int,
    admit: frozenset[str],
) -> TermImplied:
    """The window mean implied by term prices at exactly one tenor, or a gap with the count.

    One vote per seller: a seller with several configurations contributes their median.
    Ratios at or above one are not term discounts and are not counted.
    """
    per_seller: dict[str, list[float]] = {}
    for s in ratios:
        if s.tenor_months == tenor_months and s.formation in admit and 0.0 < s.ratio < 1.0:
            per_seller.setdefault(s.seller, []).append(s.ratio)
    sellers = tuple(sorted(per_seller))
    if len(sellers) < min_sellers:
        return TermImplied(None, len(sellers), sellers)
    return TermImplied(mean * median(median(v) for v in per_seller.values()),
                       len(sellers), sellers)


# --- the lockable cost -------------------------------------------------------------------


@dataclass(frozen=True)
class LockOffer:
    kind: str            # "listed" | "reserved" | "rate_card"
    seller: str
    price_usd: float     # per GPU-hour, pay-as-delivered
    covers_days: float   # the longest period the price is available for
    commit_days: float   # the period that must be paid for, used or not (0 for a listed ask)


def cost_per_used_hour(offer: LockOffer, horizon_days: int) -> float | None:
    """What securing `horizon_days` through this offer costs per hour actually used.

    Free disposal: a commitment longer than the need is paid in full and the tail wasted, so
    a 12-month contract bought for a 30-day need costs twelve months over one.
    """
    if offer.covers_days < horizon_days:
        return None
    return offer.price_usd * max(offer.commit_days, horizon_days) / horizon_days


@dataclass(frozen=True)
class Lockable:
    value: float | None
    cheapest: LockOffer | None
    n_offers: int


def lockable(offers: Iterable[LockOffer], horizon_days: int) -> Lockable:
    best: tuple[float, LockOffer] | None = None
    n = 0
    for o in offers:
        cost = cost_per_used_hour(o, horizon_days)
        if cost is None:
            continue
        n += 1
        if best is None or (cost, o.kind, o.seller) < (best[0], best[1].kind, best[1].seller):
            best = (cost, o)
    if best is None:
        return Lockable(None, None, 0)
    return Lockable(best[0], best[1], n)


def arrears_equivalent(prepaid_price: float, days: float, annual_rate: float) -> float:
    """A prepaid hourly price restated as the pay-as-delivered price of equal present value.

    Delivered evenly over `days` and paid as delivered, a unit hourly price is worth about
    days x (1 - rate x days / 730) today at simple interest. Paying p x days up front is
    therefore the same as paying p / (1 - rate x days / 730) as delivered. Prepaying costs the
    interest, so the restated price is higher than the prepaid one.
    """
    return prepaid_price / (1.0 - annual_rate * days / 730.0)


def rate_at(curve: Mapping[int, float], days: int) -> float | None:
    """Annual rate (decimal) at a tenor, linear between quoted tenors, flat beyond the ends."""
    if not curve:
        return None
    tenors = sorted(curve)
    if days <= tenors[0]:
        return curve[tenors[0]]
    if days >= tenors[-1]:
        return curve[tenors[-1]]
    for a, b in zip(tenors, tenors[1:], strict=False):
        if a <= days <= b:
            w = (days - a) / (b - a)
            return curve[a] + w * (curve[b] - curve[a])
    return None  # pragma: no cover - unreachable with sorted tenors


# --- calibration -------------------------------------------------------------------------


@dataclass(frozen=True)
class Realised:
    value: float | None
    printed_share: float
    n_prints: int


def realised_mean(
    prints: Mapping[int, float], start_day: int, horizon_days: int, min_share: float
) -> Realised:
    """The mean of the published prints in (start, start + h], if enough of them printed.

    `prints` holds only days that printed. A window with too few prints stays unscored; its
    missing days are not filled.
    """
    values = [prints[d] for d in range(start_day + 1, start_day + horizon_days + 1)
              if d in prints]
    share = len(values) / horizon_days
    value = sum(values) / len(values) if values and share >= min_share else None
    return Realised(value, share, len(values))


@dataclass(frozen=True)
class Calibration:
    horizon_days: int
    n_closed: int
    n_nonoverlapping: int
    required: int
    bias: float | None
    rmse: float | None
    coverage: float | None


def calibrate(
    forecasts: Sequence[tuple[int, float, float, float]],
    realised: Mapping[int, float],
    horizon_days: int,
    required: int,
) -> Calibration:
    """Score forecasts (origin day, mean, P10, P90) on non-overlapping closed windows.

    Daily origins at a 365-day horizon share almost all of their window, so twenty of them
    are about one independent observation. Only windows that do not overlap an earlier chosen
    one are scored, taken greedily from the earliest, and nothing is reported below the
    required count.
    """
    closed = sorted((o, m, lo, hi, realised[o]) for o, m, lo, hi in forecasts if o in realised)
    chosen: list[tuple[int, float, float, float, float]] = []
    next_allowed: int | None = None
    for row in closed:
        if next_allowed is None or row[0] >= next_allowed:
            chosen.append(row)
            next_allowed = row[0] + horizon_days
    if len(chosen) < required:
        return Calibration(horizon_days, len(closed), len(chosen), required, None, None, None)
    errors = [r - m for _, m, _, _, r in chosen]
    return Calibration(
        horizon_days, len(closed), len(chosen), required,
        bias=sum(errors) / len(errors),
        rmse=math.sqrt(sum(e * e for e in errors) / len(errors)),
        coverage=sum(1 for _, _, lo, hi, r in chosen if lo <= r <= hi) / len(chosen),
    )
