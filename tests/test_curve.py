# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Committed-cost curves (tci.curve): the bootstrap, the arbitrage check, the formation gate.

The Azure and Civo figures are the H100 SXM curves as read from the database on
2026-09-15. They are copied in rather than queried so the test does not move when the
market does; the hand arithmetic is written out beside each assertion so a reader can
check it without running anything.
"""

from __future__ import annotations

import pytest

from tci import curve
from tci.curve import CurveKey

AZURE = CurveKey("H100_SXM", provider="azure", segment="hyperscaler")
CIVO = CurveKey("H100_SXM", provider="civo", segment="neocloud")


def _azure() -> curve.Curve:
    c = curve.build(AZURE, "2026-09-15", 14.3793,
                    [(12, 9.2028), (36, 6.3125), (60, 5.7517)],
                    price_formation="administered_differentiated")
    assert c is not None
    return c


def test_azure_forwards_reproduce_the_hand_worked_figures() -> None:
    fwd, bad = curve.forwards(_azure())
    assert bad == []
    by_span = {(f.m1, f.m2): f.rate for f in fwd}
    # PHI(0,12) is the twelve-month price itself: C(0) is zero whatever spot is.
    assert by_span[(0, 12)] == pytest.approx(9.2028)
    # (36 x 6.3125 - 12 x 9.2028) / 24 = (227.25 - 110.4336) / 24 = 4.86735
    assert by_span[(12, 36)] == pytest.approx(4.86735, abs=1e-9)
    # (60 x 5.7517 - 36 x 6.3125) / 24 = (345.102 - 227.25) / 24 = 4.9105
    assert by_span[(36, 60)] == pytest.approx(4.9105, abs=1e-9)


def test_hours_per_month_cancels_out_of_every_forward_rate() -> None:
    # 730 is a convention. If it moved a published forward it would be a parameter
    # somebody could argue about; it must only scale C.
    fwd, _ = curve.forwards(_azure())
    rates = [f.rate for f in fwd]
    original = curve.HOURS_PER_MONTH
    try:
        curve.HOURS_PER_MONTH = 720.0
        assert [f.rate for f in curve.forwards(_azure())[0]] == pytest.approx(rates)
    finally:
        curve.HOURS_PER_MONTH = original


def test_the_long_end_kink_is_data_not_an_artefact() -> None:
    # Azure's forward rises from 4.87 to 4.91 past three years. Flat-forward bootstrapping
    # adds nothing between knots, so the kink is in Azure's prices.
    fwd, _ = curve.forwards(_azure())
    by_span = {(f.m1, f.m2): f.rate for f in fwd}
    assert by_span[(36, 60)] > by_span[(12, 36)]


def test_wide_segments_publish_and_are_flagged() -> None:
    civo = curve.build(CIVO, "2026-09-15", 2.99,
                       [(6, 2.79), (12, 2.6901), (24, 2.5899), (36, 2.4901)])
    assert civo is not None
    fwd, _ = curve.forwards(civo)
    flags = {(f.m1, f.m2): (f.span_months, f.wide_span) for f in fwd}
    assert flags[(0, 6)] == (6, False)
    assert flags[(6, 12)] == (6, False)
    assert flags[(12, 24)] == (12, True)
    az ={(f.m1, f.m2): f.wide_span for f in curve.forwards(_azure())[0]}
    assert az == {(0, 12): True, (12, 36): True, (36, 60): True}


def test_a_negative_forward_gaps_its_segment_and_only_its_segment() -> None:
    # C(12) = 12 x 730 x 5.00 = 43,800; C(24) = 24 x 730 x 2.00 = 35,040. Buying 24 months
    # and abandoning the second year would buy the first year below its own price.
    c = curve.build(AZURE, "d", 10.0, [(12, 5.0), (24, 2.0), (36, 1.9)])
    assert c is not None
    fwd, bad = curve.forwards(c)
    assert [(v.m1, v.m2) for v in bad] == [(12, 24)]
    assert "abandoning the tail" in bad[0].detail
    spans = {(f.m1, f.m2) for f in fwd}
    assert (0, 12) in spans and (24, 36) in spans and (12, 24) not in spans


def test_a_commitment_priced_at_on_demand_is_dropped_with_a_reason() -> None:
    # OVHcloud's H200 monthly plan, 15 September: ratio exactly 1.0000.
    c = curve.build(CurveKey("H200_SXM", "ovhcloud"), "d", 4.00, [(1, 4.00), (12, 3.0)])
    assert c is not None
    assert c.tenors == (12,)
    assert [(d.tenor_months, d.reason) for d in c.dropped] == [
        (1, "committed price at or above on-demand")]


def test_a_curve_that_is_only_its_spot_anchor_is_not_a_curve() -> None:
    assert curve.build(AZURE, "d", 4.0, [(1, 4.0), (12, 4.5)]) is None
    assert curve.build(AZURE, "d", 4.0, []) is None
    assert curve.build(AZURE, "d", 0.0, [(12, 3.0)]) is None


def test_nothing_is_read_past_the_tenor_bound() -> None:
    c = curve.build(AZURE, "d", 10.0, [(12, 8.0), (84, 5.0)])
    assert c is not None and c.tenors == (12,)
    assert c.dropped[0].reason == "tenor beyond 60 months"


def test_several_configurations_at_one_tenor_give_one_median_knot() -> None:
    c = curve.build(AZURE, "d", 10.0, [(12, 6.0), (12, 7.0), (12, 9.0)])
    assert c is not None
    assert [(k.tenor_months, k.rate) for k in c.knots] == [(0, 10.0), (12, 7.0)]


def test_rate_at_is_flat_forward_inside_the_range() -> None:
    c = _azure()
    assert curve.rate_at(c, 12) == pytest.approx(9.2028)
    assert curve.rate_at(c, 36) == pytest.approx(6.3125)
    # C(24)/730 = 110.4336 + (12/24) x 116.8164 = 168.8418, over 24 months = 7.035075
    k24 = curve.rate_at(c, 24)
    assert k24 == pytest.approx(7.035075, abs=1e-9)
    # Roll consistency: the forward off the interpolated point is the segment's forward.
    assert (24 * k24 - 12 * 9.2028) / 12 == pytest.approx(4.86735, abs=1e-9)


def test_rate_at_refuses_both_ends() -> None:
    c = _azure()
    assert curve.rate_at(c, 3) is None     # Azure sells no three-month reservation
    assert curve.rate_at(c, 61) is None    # and nothing past five years
    assert curve.rate_at(c, 0) is None


def test_rate_at_on_a_single_knot_curve() -> None:
    c = curve.build(CurveKey("H100_PCIE", "ovhcloud"), "d", 2.80, [(1, 2.6611)])
    assert c is not None
    assert curve.rate_at(c, 1) == pytest.approx(2.6611)
    assert curve.rate_at(c, 2) is None


# --- price formation -------------------------------------------------------------------

def test_verda_and_latitude_schedules_are_chip_invariant() -> None:
    verda = {m: {1: 0.98, 3: 0.97, 6: 0.96, 12: 0.92, 24: 0.75}
             for m in ("A100_SXM", "A100_SXM_40GB", "B200_SXM", "B300_SXM", "H100_SXM",
                       "H200_SXM")}
    latitude = {m: {1: 0.50, 12: 0.35} for m in ("B300_SXM", "H100_UNSPEC", "RTX_PRO_6000")}
    assert curve.classify(verda) == "administered_uniform"
    assert curve.classify(latitude) == "administered_uniform"


def test_azure_and_civo_schedules_distinguish_chips() -> None:
    azure = {"A100_SXM": {12: 0.6400, 36: 0.4400}, "H100_PCIE": {12: 0.7500, 36: 0.5500},
             "H100_SXM": {12: 0.6400, 36: 0.4390, 60: 0.4000},
             "H200_SXM": {12: 0.5486, 36: 0.4979}}
    civo = {"A100_40GB": {6: 0.9083, 12: 0.8165}, "H100_SXM": {6: 0.9331, 12: 0.8997},
            "H200_SXM": {6: 0.9427, 12: 0.9140}}
    assert curve.classify(azure) == "administered_differentiated"
    assert curve.classify(civo) == "administered_differentiated"


def test_a_single_chip_seller_is_untested_not_uniform() -> None:
    # One chip is trivially "identical to itself". Calling that uniform would tag a seller
    # as a rate card on no evidence.
    assert curve.classify({"H100_SXM": {1: 0.95, 12: 0.80}}) == "administered_untested"
    assert curve.classify({"H100_SXM": {1: 0.95}, "L4": {12: 0.8}}) == "administered_untested"


def test_half_a_point_across_chips_is_noise() -> None:
    assert curve.classify({"a": {12: 0.920}, "b": {12: 0.924}}) == "administered_uniform"
    assert curve.classify({"a": {12: 0.920}, "b": {12: 0.930}}) == (
        "administered_differentiated")


@pytest.mark.parametrize("formation", ["administered_uniform", "administered_differentiated",
                                       "administered_untested"])
def test_a_forward_spot_curve_cannot_be_built_from_a_rate_card(formation: str) -> None:
    with pytest.raises(ValueError, match="S\\* = PHI \\+ pi"):
        curve.build(AZURE, "d", 10.0, [(12, 8.0)], price_formation=formation,  # type: ignore[arg-type]
                    kind="forward_spot")


@pytest.mark.parametrize("formation", ["market_quoted", "transacted"])
def test_a_forward_spot_curve_can_be_built_from_market_prices(formation: str) -> None:
    c = curve.build(AZURE, "d", 10.0, [(12, 8.0)], price_formation=formation,  # type: ignore[arg-type]
                    kind="forward_spot")
    assert c is not None and c.kind == "forward_spot"


# --- pooling ---------------------------------------------------------------------------

def _seller(name: str, spot: float, ratios: dict[int, float],
            segment: str = "neocloud") -> curve.Curve:
    c = curve.build(CurveKey("H100_SXM", name, segment), "d", spot,
                    [(m, spot * r) for m, r in ratios.items()])
    assert c is not None
    return c


def test_pooling_takes_the_shape_so_a_level_gap_cannot_move_it() -> None:
    # Same discount schedule at a 4.8x level gap: the pooled shape is the schedule.
    cheap = _seller("a", 2.99, {12: 0.90})
    dear = _seller("b", 14.38, {12: 0.90})
    mid = _seller("c", 6.00, {12: 0.90})
    (p,) = curve.pool([cheap, dear, mid])
    assert p.published and p.median_ratio == 0.90


def test_a_seller_leaving_a_tenor_changes_the_vote_not_the_level() -> None:
    # The composition failure: pool levels and the 24-month point falls because the dear
    # seller has no 24-month price. Pool ratios and it cannot.
    sellers = [_seller("dear", 14.38, {12: 0.90}), _seller("b", 3.00, {12: 0.90, 24: 0.85}),
               _seller("c", 3.10, {12: 0.90, 24: 0.85}), _seller("d", 2.95, {12: 0.9, 24: 0.85})]
    pts = {p.tenor_months: p for p in curve.pool(sellers)}
    assert pts[12].n_providers == 4 and pts[24].n_providers == 3
    assert pts[24].median_ratio == 0.85


def test_below_the_floor_a_pooled_point_is_suppressed_but_names_its_sellers() -> None:
    # 15 September, H100 SXM neocloud at twelve months: Civo and Verda. One short.
    pts = curve.pool([_seller("civo", 2.99, {12: 0.8997}), _seller("verda", 3.27, {12: 0.92})])
    (p,) = pts
    assert not p.published and p.median_ratio is None
    assert p.n_providers == 2 and p.providers == ("civo", "verda")


def test_pooling_refuses_to_cross_a_segment_or_a_model() -> None:
    with pytest.raises(ValueError, match="mixed keys"):
        curve.pool([_seller("a", 3.0, {12: 0.9}), _seller("b", 14.0, {12: 0.64}, "hyperscaler")])
    other = curve.build(CurveKey("A100_SXM", "c", "neocloud"), "d", 2.0, [(12, 1.8)])
    assert other is not None
    with pytest.raises(ValueError, match="mixed keys"):
        curve.pool([_seller("a", 3.0, {12: 0.9}), other])


def test_pooling_refuses_a_seller_counted_twice() -> None:
    with pytest.raises(ValueError, match="each seller once"):
        curve.pool([_seller("a", 3.0, {12: 0.9}), _seller("a", 3.1, {12: 0.8})])


def test_the_aggregate_is_the_index_level_times_the_pooled_shape() -> None:
    sellers = [_seller(n, s, {12: 0.90, 24: 0.80}) for n, s in
               (("a", 2.99), ("b", 3.27), ("c", 3.10))]
    key = CurveKey("H100_SXM", segment="neocloud")
    agg = curve.apply_level(curve.pool(sellers), 3.49, key, "d")
    assert agg is not None and agg.spot == 3.49
    assert [(k.tenor_months, round(k.rate, 4)) for k in agg.knots[1:]] == [
        (12, round(3.49 * 0.90, 4)), (24, round(3.49 * 0.80, 4))]


def test_no_aggregate_when_nothing_clears_the_floor() -> None:
    pts = curve.pool([_seller("civo", 2.99, {12: 0.8997}), _seller("verda", 3.27, {12: 0.92})])
    assert curve.apply_level(pts, 3.49, CurveKey("H100_SXM", segment="neocloud"), "d") is None


def test_with_the_same_sellers_at_every_tenor_pooling_stays_arbitrage_free() -> None:
    # If every seller has 24 x r24 >= 12 x r12, each order statistic of r24 dominates the
    # same order statistic of r12 / 2, the median included. Pooling a fixed set of
    # arbitrage-free sellers cannot manufacture a violation.
    sellers = [_seller("a", 10.0, {12: 0.60, 24: 0.31}), _seller("b", 10.0, {12: 0.90, 24: 0.46}),
               _seller("c", 10.0, {12: 0.95, 24: 0.48})]
    assert all(curve.forwards(s)[1] == [] for s in sellers)
    agg = curve.apply_level(curve.pool(sellers), 10.0, CurveKey("H100_SXM", segment="neocloud"),
                            "d")
    assert agg is not None and curve.forwards(agg)[1] == []


def test_a_change_of_sellers_between_tenors_can_break_the_pooled_curve() -> None:
    # The only way a pooled violation arises: different sellers vote at different tenors.
    # Twelve months is a, b, c (median 0.95); twenty-four is c, d, e (median 0.40), and
    # 24 x 0.40 = 9.6 < 12 x 0.95 = 11.4, though no seller alone violates anything.
    sellers = [_seller("a", 10.0, {12: 0.95}), _seller("b", 10.0, {12: 0.95}),
               _seller("c", 10.0, {12: 0.60, 24: 0.31}), _seller("d", 10.0, {24: 0.40}),
               _seller("e", 10.0, {24: 0.42})]
    assert all(curve.forwards(s)[1] == [] for s in sellers)
    agg = curve.apply_level(curve.pool(sellers), 10.0, CurveKey("H100_SXM", segment="neocloud"),
                            "d")
    assert agg is not None
    _, bad = curve.forwards(agg)
    assert [(v.m1, v.m2) for v in bad] == [(12, 24)]
