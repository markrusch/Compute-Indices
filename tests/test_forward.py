# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""The forward estimate's arithmetic (tci.forward), against hand-worked values."""

from __future__ import annotations

import math

import pytest

from tci import forward
from tci.forward import LockOffer, SellerRatio


def test_kalman_one_step_by_hand() -> None:
    # Level 0 with variance r = 1 after the first point. Next day, q = 0: predicted variance
    # 1, total 2, gain 0.5, so the level moves halfway to 1 and its variance halves.
    level, variance, loglik = forward.kalman([(0, 0.0), (1, 1.0)], q=0.0, r=1.0)
    assert level == pytest.approx(0.5)
    assert variance == pytest.approx(0.5)
    assert loglik == pytest.approx(-0.5 * (math.log(2 * math.pi * 2) + 1 / 2))


def test_a_gap_adds_level_variance_per_missing_day() -> None:
    near = forward.kalman([(0, 0.0), (1, 1.0)], q=0.1, r=1.0)
    far = forward.kalman([(0, 0.0), (10, 1.0)], q=0.1, r=1.0)
    # A ten-day gap leaves more room for the level to have moved, so the new print is trusted
    # more against the old level.
    assert far[0] > near[0]


def test_a_one_day_spike_is_mostly_noise() -> None:
    # The shape of 12 September 2026: a flat record, one print 7% up, back the next day.
    flat = [math.log(3.25)] * 12
    series = flat[:8] + [math.log(3.49)] + flat[8:]
    points = list(enumerate(series))
    fit = forward.fit_level(points, (-8.0, -2.0, 25), (-8.0, -1.0, 29))
    assert fit is not None
    # The filtered level moves less than a tenth of the way from the flat record to the spike,
    # and the fit attributes the spike to measurement noise rather than to the level.
    assert abs(math.exp(fit.level) - 3.25) < 0.1 * (3.49 - 3.25)
    assert fit.r > fit.q


def test_fit_is_deterministic_and_needs_two_points() -> None:
    pts = [(0, 1.0), (1, 1.1), (3, 1.05), (4, 1.2)]
    a = forward.fit_level(pts, (-6.0, -1.0, 11), (-6.0, -1.0, 11))
    b = forward.fit_level(pts, (-6.0, -1.0, 11), (-6.0, -1.0, 11))
    assert a == b
    assert forward.fit_level(pts[:1], (-6.0, -1.0, 11), (-6.0, -1.0, 11)) is None


def test_window_forecast_by_hand() -> None:
    fit = forward.LevelFit(q=1e-4, r=4e-4, level=math.log(3.0), variance=2e-4, n=20,
                           last_day=0, loglik=0.0)
    wf = forward.window_forecast(fit, 30, (0.1, 0.5, 0.9))
    # V = P + q(h+1)(2h+1)/(6h) + r/h = 2e-4 + 1e-4 x 31 x 61 / 180 + 4e-4 / 30
    v = 2e-4 + 1e-4 * 31 * 61 / 180 + 4e-4 / 30
    assert wf.log_variance == pytest.approx(v)
    # A martingale in price: the mean is today's level in dollars, whatever the horizon.
    assert wf.mean == pytest.approx(3.0 * math.exp(1e-4))
    assert wf.at(0.5) == pytest.approx(wf.mean * math.exp(-v / 2))
    assert wf.at(0.1) < wf.at(0.5) < wf.mean < wf.at(0.9)


def test_longer_windows_are_wider_but_centred_on_the_same_mean() -> None:
    fit = forward.LevelFit(q=1e-4, r=4e-4, level=math.log(3.0), variance=2e-4, n=20,
                           last_day=0, loglik=0.0)
    short = forward.window_forecast(fit, 30, (0.1, 0.9))
    long_ = forward.window_forecast(fit, 365, (0.1, 0.9))
    assert short.mean == long_.mean
    assert long_.at(0.9) - long_.at(0.1) > short.at(0.9) - short.at(0.1)


def test_version_days_split_a_window_across_announced_versions() -> None:
    schedule = [(forward.day("2026-07-18"), "a"), (forward.day("2026-09-22"), "b"),
                (forward.day("2026-10-01"), "c")]
    split = forward.version_days(forward.day("2026-09-15"), 30, schedule)
    # 16-21 September under a, 22-30 under b, 1-15 October under c.
    assert split == {"a": 6, "b": 9, "c": 15}
    with pytest.raises(ValueError):
        forward.version_days(forward.day("2026-07-01"), 5, schedule)


def test_combine_is_day_weighted() -> None:
    a = forward.WindowForecast(3.0, 0.01, ())
    b = forward.WindowForecast(4.0, 0.03, ())
    c = forward.combine([(1, a), (3, b)], (0.5,))
    assert c.mean == pytest.approx(3.75)
    assert c.log_variance == pytest.approx(0.025)


def _ratio(
    seller: str, r: float, months: int = 12, formation: str = "market_quoted"
) -> SellerRatio:
    return SellerRatio(seller, months, r, formation, "s")


def test_term_implied_needs_three_sellers_one_vote_each() -> None:
    admit = frozenset({"market_quoted", "administered_differentiated"})
    two = [_ratio("a", 0.9), _ratio("a", 0.7), _ratio("b", 0.8)]
    assert forward.term_implied(3.0, two, 12, 3, admit).value is None
    three = two + [_ratio("c", 0.85)]
    ti = forward.term_implied(3.0, three, 12, 3, admit)
    # Seller a votes once, with the median of 0.9 and 0.7 = 0.8; median of (0.8, 0.8, 0.85).
    assert ti.value == pytest.approx(3.0 * 0.8) and ti.sellers == ("a", "b", "c")


def test_term_implied_ignores_uniform_schedules_other_tenors_and_non_discounts() -> None:
    admit = frozenset({"market_quoted"})
    rs = [_ratio("a", 0.9), _ratio("b", 0.9, formation="administered_uniform"),
          _ratio("c", 1.0), _ratio("d", 0.9, months=6)]
    assert forward.term_implied(3.0, rs, 12, 1, admit).sellers == ("a",)


def test_free_disposal_charges_the_whole_contract() -> None:
    card = LockOffer("rate_card", "s", 2.0, covers_days=365, commit_days=365)
    listed = LockOffer("listed", "vast.ai:1", 2.5, covers_days=60, commit_days=0)
    short = LockOffer("listed", "vast.ai:2", 1.0, covers_days=10, commit_days=0)
    # A year at 2.00 bought for 30 days costs 2.00 x 365 / 30 = 24.33 per used hour.
    assert forward.cost_per_used_hour(card, 30) == pytest.approx(2.0 * 365 / 30)
    assert forward.cost_per_used_hour(short, 30) is None
    lk = forward.lockable([card, listed, short], 30)
    assert lk.value == pytest.approx(2.5) and lk.cheapest == listed and lk.n_offers == 2
    assert forward.lockable([card, listed, short], 365).value == pytest.approx(2.0)
    assert forward.lockable([short], 30).value is None


def test_prepaying_costs_the_interest() -> None:
    # 180 days prepaid at 4.4%: 2.00 / (1 - 0.044 x 180 / 730).
    assert forward.arrears_equivalent(2.0, 180, 0.044) == pytest.approx(
        2.0 / (1 - 0.044 * 180 / 730))
    assert forward.arrears_equivalent(2.0, 180, 0.0) == 2.0


def test_rate_interpolation_is_linear_and_flat_beyond_the_ends() -> None:
    curve = {30: 0.04, 365: 0.05}
    assert forward.rate_at(curve, 10) == 0.04
    assert forward.rate_at(curve, 400) == 0.05
    assert forward.rate_at(curve, 197) == pytest.approx(0.04 + (167 / 335) * 0.01)
    assert forward.rate_at({}, 30) is None


def test_a_window_with_too_few_prints_stays_unscored() -> None:
    prints = {d: 3.0 for d in range(1, 16)}   # the first 15 of 30 days printed
    assert forward.realised_mean(prints, 0, 30, 0.5).value == 3.0
    assert forward.realised_mean(prints, 0, 30, 0.6).value is None
    assert forward.realised_mean(prints, 0, 30, 0.6).printed_share == 0.5


def test_calibration_counts_only_non_overlapping_windows() -> None:
    forecasts = [(o, 3.0, 2.9, 3.1) for o in range(60)]
    realised = {o: 3.05 for o in range(60)}
    cal = forward.calibrate(forecasts, realised, 30, required=2)
    # Sixty daily origins at a 30-day horizon are two independent windows, origins 0 and 30.
    assert cal.n_closed == 60 and cal.n_nonoverlapping == 2
    assert cal.bias == pytest.approx(0.05) and cal.coverage == 1.0
    assert forward.calibrate(forecasts, realised, 30, required=3).bias is None
