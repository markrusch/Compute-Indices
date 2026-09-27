# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""v0.10.0 (notice 2026-N7): country-less rows in GLOBAL, panel entries limited to some
blocks, and the Together AI collector that supplies one of those rows."""

from __future__ import annotations

from pathlib import Path

import pytest

from tci import db
from tci.collectors.together import TogetherCollector, parse
from tci.config import load_factors, load_succession
from tci.normalise import normalise_observations, unadmitted_providers

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "together" / "pricing-2026-09-27.html"
V010 = "2026-10-05"


def _row(provider: str, source: str, country: str | None, price: float = 3.0,
         count: int = 8) -> dict:
    return {"provider": provider, "source": source, "tier": "list", "gpu_model": "H100_SXM",
            "gpu_count": count, "price_usd_per_gpu_hr": price, "country": country,
            "term": "on_demand", "raw_json": "{}"}


# --- the collector ------------------------------------------------------------------------


def test_together_reads_the_visible_on_demand_column() -> None:
    page = FIXTURE.read_text(encoding="utf-8")
    got = {variant: price for _label, variant, price in parse(page)}
    assert got == {"H100_SXM": 3.99, "H200_SXM": 5.99, "B200_SXM": 8.19, "B300_SXM": 9.99}


def test_together_never_reads_the_preemptible_column_beside_it() -> None:
    """Preemptible H100 is $1.99. Moving the ON-Demand header must move what is read."""
    page = FIXTURE.read_text(encoding="utf-8")
    at = page.find("On-demand hourly rates and reserved capacity")
    head, tail = page[:at], page[at:]
    tail = tail.replace(">Preemptible Compute<", ">X<", 1)
    tail = tail.replace(">ON-Demand<", ">Preemptible Compute<", 1)
    tail = tail.replace(">X<", ">ON-Demand<", 1)
    got = {v: p for _l, v, p in parse(head + tail)}
    assert got["H100_SXM"] == 1.99  # the column follows its header, not its position


@pytest.mark.parametrize("breakage", [
    "On-demand hourly rates and reserved capacity", "All prices are per GPU per hour",
    ">ON-Demand<",
])
def test_together_raises_rather_than_guess_when_the_page_changes(breakage: str) -> None:
    page = FIXTURE.read_text(encoding="utf-8").replace(breakage, "something else")
    with pytest.raises(RuntimeError):
        parse(page)


def test_together_rows_carry_no_country_and_an_eight_gpu_node() -> None:
    obs = TogetherCollector().observations(FIXTURE.read_text(encoding="utf-8"))
    h100 = {o.tier: o for o in obs if o.gpu_model == "H100_SXM"}
    assert set(h100) == {"list", "spot"}  # on-demand, and preemptible from v0.11.0
    for tier, price in (("list", 3.99), ("spot", 1.99)):
        o = h100[tier]
        assert (o.provider, o.source, o.country, o.gpu_count, o.term, o.price_usd_per_gpu_hr) == (
            "together", "together", None, 8, "on_demand", price)


def test_together_still_reads_on_demand_when_the_preemptible_column_goes() -> None:
    page = FIXTURE.read_text(encoding="utf-8").replace(">Preemptible Compute<", ">Other<", 1)
    obs = TogetherCollector().observations(page)
    assert {o.tier for o in obs} == {"list"}


# --- the unplaced rule --------------------------------------------------------------------


def test_a_country_less_row_counts_only_where_the_version_names_its_provider() -> None:
    f = load_factors(for_date=V010)
    world = f.countries_of("GLOBAL")
    civo = _row("civo", "civo", None, 2.99)
    assert f.unplaced_in("GLOBAL") == {"civo", "together"}
    assert f.unplaced_in("US") == frozenset() and f.unplaced_in("EU_EEA") == frozenset()
    got = normalise_observations([civo], f, countries=world,
                                 unplaced=f.unplaced_in("GLOBAL"), block_id="GLOBAL")
    assert [(o.provider, o.country) for o in got] == [("civo", "")]
    # Not in any regional block, and not for a provider the list does not name.
    assert normalise_observations([civo], f, countries=f.countries_of("US"),
                                  unplaced=f.unplaced_in("US"), block_id="US") == []
    crusoe = _row("crusoe", "crusoe", None)
    assert normalise_observations([crusoe], f, countries=world,
                                  unplaced=f.unplaced_in("GLOBAL"), block_id="GLOBAL") == []


def test_no_provider_that_places_its_own_rows_is_ever_listed_as_unplaced() -> None:
    """Hyperstack places rows from its stock feed; its unplaced row beside them would count
    the same hour twice."""
    for entry in load_succession():
        f = load_factors(for_date=str(entry.effective_from))
        for block, providers in f.unplaced.items():
            assert "hyperstack" not in providers, (entry.version, block)


def test_unplaced_providers_never_carry_a_country_in_the_store() -> None:
    f = load_factors(for_date=V010)
    listed = sorted(f.unplaced_in("GLOBAL"))
    conn = db.connect_readonly(REPO_ROOT / "data" / "eucri.db")
    placed = conn.execute(
        f"SELECT provider, country, COUNT(*) FROM observations WHERE provider IN"
        f" ({','.join('?' * len(listed))}) AND country IS NOT NULL GROUP BY 1, 2",
        listed).fetchall()
    assert placed == [], [tuple(r) for r in placed]


def test_nothing_changes_before_the_version() -> None:
    f = load_factors(for_date="2026-10-04")
    assert f.unplaced == {}
    assert "coreweave" not in (f.panel or {}) and "together" not in (f.panel or {})


# --- a panel entry limited to some blocks -------------------------------------------------


@pytest.mark.parametrize(("block", "country", "counts"), [
    ("EU_EEA", "NO", False), ("GLOBAL", "NO", True), ("US", "US", True), ("GLOBAL", "US", True),
])
def test_coreweave_is_priced_in_us_and_global_only(block: str, country: str,
                                                    counts: bool) -> None:
    f = load_factors(for_date=V010)
    row = _row("coreweave", "coreweave", country, 6.155)
    got = normalise_observations([row], f, countries=f.countries_of(block), block_id=block)
    assert bool(got) is counts
    # Where it does not count, the audit names it as outside the panel, not as missing.
    missing = unadmitted_providers([row], f, f.countries_of(block), block_id=block)
    assert ("coreweave" in missing) is (not counts)


def test_the_default_block_is_eu_eea_so_every_existing_caller_is_unchanged() -> None:
    f = load_factors(for_date=V010)
    row = _row("coreweave", "coreweave", "NO", 6.155)
    assert normalise_observations([row], f) == []
    assert f.admits("coreweave", "coreweave", "H100") is False
    assert f.admits("coreweave", "coreweave", "H100", "US") is True


def test_intake_agrees_with_normalise_on_a_limited_entry() -> None:
    from tci import intake

    f = load_factors(for_date=V010)
    row = _row("coreweave", "coreweave", "NO", 6.155)
    eu = intake.classify(row, f, None, f.eu_eea_countries)
    world = intake.classify(row, f, None, f.countries_of("GLOBAL"), "GLOBAL")
    assert eu.gate == "not_in_panel" and world.gate == "admitted"
    civo = intake.classify(_row("civo", "civo", None, 2.99), f, None,
                           f.countries_of("GLOBAL"), "GLOBAL")
    assert civo.gate == "admitted"
