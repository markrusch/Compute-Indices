# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Published commitment discounts (tci.term): pairing, exclusions, suppression."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tci import term


def _row(provider: str, term_: str, price: float, *, product: str | None = "p1",
         gpu: str = "H100_SXM", tier: str = "list", windows: bool = False,
         region: str = "r1", currency: str = "USD") -> dict[str, Any]:
    extra: dict[str, Any] = {"plan": product} if product else {}
    if windows:
        extra["os_family"] = "windows"
    raw = {"extra": extra, "currency": currency, "price_native_per_gpu_hr": price}
    return {"provider": provider, "source": "s", "gpu_model": gpu, "gpu_count": 8,
            "region": region, "country": "NL", "tier": tier, "term": term_,
            "price_usd_per_gpu_hr": price, "raw_json": json.dumps(raw)}


def test_ratio_is_committed_over_same_product_on_demand() -> None:
    rows = [_row("a", "on_demand", 4.0), _row("a", "reserved_1yr", 3.0)]
    (t,) = term.seller_terms(rows)
    assert t.tenor_months == 12 and t.ratio == 0.75


def test_different_products_are_never_paired() -> None:
    # The Azure case: an ND96is_noIB reservation must not meet the ND96isr on-demand price.
    rows = [_row("a", "on_demand", 4.0, product="isr"),
            _row("a", "reserved_1yr", 3.0, product="noIB")]
    assert term.seller_terms(rows) == []


def test_windows_licensed_rows_are_dropped() -> None:
    rows = [_row("a", "on_demand", 1.0), _row("a", "commit_1mo", 1.9, windows=True)]
    assert term.seller_terms(rows) == []


def test_a_committed_price_above_on_demand_is_excluded_with_a_reason() -> None:
    excluded: list[term.Excluded] = []
    rows = [_row("a", "on_demand", 1.0), _row("a", "commit_1mo", 1.3)]
    assert term.seller_terms(rows, excluded) == []
    assert excluded[0].reason == "committed price above on-demand"


def test_spot_and_unspecified_tenors_never_enter_a_ratio() -> None:
    rows = [_row("a", "on_demand", 4.0), _row("a", "reserved_1yr", 3.0, tier="spot"),
            _row("a", "reserved_unspecified", 2.0)]
    assert term.seller_terms(rows) == []


def test_ovh_plan_codes_pair_across_billing_suffixes() -> None:
    assert term.product_of({"extra": {"plan_code": "h100-380.consumption"}}) == "h100-380"
    assert term.product_of({"extra": {"plan_code": "h100-380.monthly.postpaid"}}) == "h100-380"
    assert term.product_of({"armSkuName": "Standard_ND96isr_H100_v5"}) == (
        "Standard_ND96isr_H100_v5")


def _terms(ratios: dict[str, float]) -> list[term.SellerTerm]:
    return [
        term.SellerTerm(provider=p, source="s", gpu_model="H100_SXM", gpu_count=8,
                        region=None, country=None, currency="USD", tenor_months=12,
                        on_demand=4.0, committed=4.0 * r)
        for p, r in ratios.items()
    ]


def test_a_cell_needs_three_sellers() -> None:
    seg = {"a": "neocloud", "b": "neocloud", "c": "neocloud"}
    (two,) = term.cells(_terms({"a": 0.9, "b": 0.8}), seg)
    assert not two.published and two.median_ratio is None and two.n_sellers == 2
    (three,) = term.cells(_terms({"a": 0.9, "b": 0.8, "c": 0.7}), seg)
    assert three.published and three.median_ratio == 0.8


def test_segments_are_not_pooled() -> None:
    seg = {"a": "neocloud", "b": "neocloud", "c": "hyperscaler"}
    cells = term.cells(_terms({"a": 0.9, "b": 0.8, "c": 0.44}), seg)
    assert {(c.segment, c.n_sellers, c.published) for c in cells} == {
        ("neocloud", 2, False), ("hyperscaler", 1, False)}


def test_a_seller_with_many_configurations_has_one_voice() -> None:
    many = [t for _ in range(8) for t in _terms({"a": 0.5})]
    cells = term.cells(many + _terms({"b": 0.9, "c": 0.9}),
                       {"a": "n", "b": "n", "c": "n"})
    assert cells[0].median_ratio == 0.9


def test_schedule_restates_one_sellers_rate_card() -> None:
    (row,) = term.schedule(_terms({"a": 0.64}) + _terms({"a": 0.64}))
    assert row.n_configs == 2 and round(row.discount, 2) == 0.36


def test_a_published_schedule_applies_only_to_gpus_priced_that_day(tmp_path: Path) -> None:
    cfg = tmp_path / "s.yaml"
    cfg.write_text(
        "max_age_days: 90\nschedules:\n  - provider: verda\n    url: u\n"
        "    applies_to: self-service\n    last_verified: 2026-09-11\n"
        "    discounts: {1: 0.02, 24: 0.25}\n", encoding="utf-8")
    (sched,), stale = term.load_schedules(cfg, "2026-09-20")
    assert stale == []
    rows = [_row("verda", "on_demand", 2.0, gpu="H100_SXM"),
            _row("other", "on_demand", 9.0, gpu="B200_SXM")]
    terms = term.schedule_terms(sched, rows)
    assert {(t.gpu_model, t.tenor_months, round(t.ratio, 2)) for t in terms} == {
        ("H100_SXM", 1, 0.98), ("H100_SXM", 24, 0.75)}
    _, stale = term.load_schedules(cfg, "2026-12-31")
    assert stale == ["verda"]


def test_the_shipped_schedules_file_loads() -> None:
    # `last_verified` is refreshed daily by tci.collectors.term_schedule_refresh, so this
    # pins to today (always >= last_verified) rather than a date that ages past the file.
    path = Path(__file__).resolve().parents[1] / "config" / "term_schedules.yaml"
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    fresh, _ = term.load_schedules(path, today)
    assert any(s.provider == "verda" and s.discounts[24] == 0.25 for s in fresh)
