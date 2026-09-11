# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Guards on the source register and the region register.

These files are not hash-locked, because nothing in them reaches a print. What they can
do is drift: a collector starts emitting a country no block claims, or the EU_EEA block
quietly stops matching the country list the calculation path actually uses. Both failures
are silent, and both are caught here.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime

import pytest

from tci import sources
from tci.collectors.azure_retail import REGION_COUNTRY as AZURE_REGIONS
from tci.collectors.gpuhunt_ import REGION_COUNTRY as GPUHUNT_REGIONS
from tci.commands import collectors_for_daily
from tci.config import load_factors
from tests.conftest import insert_run


@pytest.fixture(scope="module")
def registry() -> sources.Registry:
    return sources.load_registry()


@pytest.fixture(scope="module")
def regions() -> sources.Regions:
    return sources.load_regions()


def test_source_ids_are_unique(registry):
    ids = [s.id for s in registry.sources]
    assert len(ids) == len(set(ids))


def test_statuses_and_tiers_are_from_the_documented_vocabulary(registry):
    for s in registry.sources:
        assert s.status in sources.SOURCE_STATUSES, s.id
        assert s.tier in sources.TIERS, s.id


def test_a_rejection_records_why(registry):
    """The reason outlives the decision. Without it the source gets rediscovered."""
    for s in registry.with_status("rejected"):
        assert s.reason, f"{s.id} is rejected with no reason"


def test_every_block_a_source_claims_exists(registry, regions):
    for s in registry.sources:
        for block in s.blocks:
            assert block in regions.blocks, f"{s.id} claims unknown block {block}"


def test_every_running_collector_is_registered(registry):
    """A collector that reaches the daily run but not the register is invisible to review."""
    for collector in collectors_for_daily():
        matches = [s for s in registry.sources if s.collector == collector.name]
        assert matches, f"collector {collector.name} has no source_registry.yaml row"
        assert matches[0].status in ("live", "shadow"), (
            f"{collector.name} runs daily but is neither live nor shadow"
        )


def test_eu_eea_block_matches_the_calculation_path(regions):
    """regions.yaml is documentation until it agrees with the file that decides prints."""
    assert regions.blocks["EU_EEA"].countries == load_factors().eu_eea_countries


def test_norway_is_in_the_calculation_path_from_v040(regions):
    """The YAML-1.1 `NO` bug, fixed in v0.4.0 (notice 2026-N1).

    The 0.3.0-dev snapshot keeps the bug on purpose: prints stored under it were computed
    without Norway, and the snapshot has to reproduce them.
    """
    assert "NO" in load_factors(for_date="2026-09-15").eu_eea_countries
    assert "NO" not in load_factors(for_date="2026-09-14").eu_eea_countries
    assert all(isinstance(c, str) and len(c) == 2 for c in load_factors().eu_eea_countries)


def test_no_country_is_claimed_by_two_blocks(regions):
    seen: dict[str, str] = {}
    for key, block in regions.blocks.items():
        for country in block.countries:
            assert country not in seen, f"{country} in both {seen.get(country)} and {key}"
            seen[country] = key


def test_country_codes_are_iso_alpha2(regions):
    for country in regions.countries:
        assert len(country) == 2 and country.isupper() and country.isalpha(), country


def test_block_statuses_are_known(regions):
    for block in regions.blocks.values():
        assert block.status in sources.BLOCK_STATUSES, block.key
    assert any(b.status == "published" for b in regions.blocks.values())


def test_an_excluded_block_says_why(regions):
    for block in regions.blocks.values():
        if block.status == "excluded":
            assert block.reason, f"{block.key} is excluded with no reason"


def test_collector_region_maps_only_emit_countries_a_block_claims(regions):
    """The check that catches a widened region map nobody told the register about."""
    for label, mapping in (("gpuhunt", GPUHUNT_REGIONS), ("azure_retail", AZURE_REGIONS)):
        for region, country in mapping.items():
            assert regions.block_of(country), f"{label}:{region} -> {country} has no block"


def test_no_collector_can_reach_a_sanctioned_country(regions):
    """A legal exclusion is worthless if a region map quietly routes around it."""
    excluded = regions.excluded_countries
    for mapping in (GPUHUNT_REGIONS, AZURE_REGIONS):
        assert not (set(mapping.values()) & excluded)


def test_review_dates_parse_and_are_not_in_the_future(registry):
    today = datetime.now(UTC).date()
    for s in registry.sources:
        for value in (s.discovered, s.last_reviewed):
            if value is None:
                continue
            parsed = datetime.strptime(value, "%Y-%m-%d").date()
            assert parsed <= today, f"{s.id} carries a future date {value}"


def test_review_clock_flags_a_stale_row(registry):
    fresh = sources.Source(
        id="x", name="X", kind="neocloud", status="live", tier="list",
        blocks=("EU_EEA",), gpu_classes=("H100",), last_reviewed="2026-09-01",
    )
    stale = sources.Source(
        id="y", name="Y", kind="neocloud", status="live", tier="list",
        blocks=("EU_EEA",), gpu_classes=("H100",), last_reviewed="2026-01-01",
    )
    never = sources.Source(
        id="z", name="Z", kind="neocloud", status="candidate", tier="list",
        blocks=("EU_EEA",), gpu_classes=("H100",),
    )
    reg = sources.Registry(
        sources=(fresh, stale, never), review_interval_days=90, scans=()
    )
    due = {s.id for s in reg.due_for_review(date(2026, 9, 7))}
    assert due == {"y", "z"}


def _observe(c: sqlite3.Connection, country: str | None, provider: str,
             day: str = "2026-09-01", region: str | None = None) -> None:
    c.execute(
        "INSERT INTO observations (run_id, ts_utc, source, provider, gpu_model, gpu_count,"
        " price_usd_per_gpu_hr, region, country, interconnect, tier, term, raw_json)"
        " VALUES ('r1', ?, 'test', ?, 'H100_SXM', 8, 3.0, ?, ?, 'NVLink', 'list',"
        " 'on_demand', '{}')",
        (f"{day}T10:00:00Z", provider, region, country),
    )


def test_coverage_counts_shadow_blocks_separately(conn, regions):
    insert_run(conn)
    _observe(conn, "NL", "alpha")
    _observe(conn, "FR", "beta")
    _observe(conn, "US", "gamma")
    _observe(conn, "US", "delta", day="2026-09-02")
    _observe(conn, "SG", "epsilon")

    by_block = {c.block: c for c in sources.coverage(conn, regions)}
    assert by_block["EU_EEA"].observations == 2
    assert by_block["EU_EEA"].providers == 2
    assert by_block["US"].observations == 2
    assert by_block["US"].collection_days == 2
    assert by_block["APAC_SE"].observations == 1
    assert by_block["APAC_SE"].status == "shadow"
    assert by_block["MEA"].observations == 0


def test_coverage_since_bound(conn, regions):
    insert_run(conn)
    _observe(conn, "NL", "alpha", day="2026-08-01")
    _observe(conn, "NL", "alpha", day="2026-09-01")
    by_block = {c.block: c for c in sources.coverage(conn, regions, since="2026-08-15")}
    assert by_block["EU_EEA"].observations == 1


def test_unmapped_locations_surfaces_rows_with_no_country(conn):
    insert_run(conn)
    _observe(conn, None, "gcp", region="asia-southeast1-c")
    _observe(conn, None, "gcp", region="asia-southeast1-c")
    _observe(conn, None, "aws", region="ap-south-1")
    _observe(conn, "NL", "scaleway", region="fr-par-2")

    found = sources.unmapped_locations(conn)
    assert ("gcp", "asia-southeast1-c", 2) in found
    assert ("aws", "ap-south-1", 1) in found
    assert all(loc != "fr-par-2" for _, loc, _ in found)


def test_silent_source_detection_uses_the_fx_table_for_fx(conn):
    """collect_fx never writes a runs row, so the runs table always calls it silent."""
    conn.execute("INSERT INTO fx (date, eur_usd, source) VALUES ('2026-09-06', 1.1, 'ECB')")
    reg = sources.Registry(
        sources=(
            sources.Source(id="ecb_fx", name="fx", kind="reference", status="live",
                           tier="reference", blocks=(), gpu_classes=(), collector="fx"),
            sources.Source(id="ghost", name="Ghost", kind="neocloud", status="live",
                           tier="list", blocks=("EU_EEA",), gpu_classes=("H100",),
                           collector="ghost"),
        ),
        review_interval_days=90,
        scans=(),
    )
    silent = dict(sources.silent_live_sources(conn, reg, date(2026, 9, 7)))
    assert "ecb_fx" not in silent
    assert silent["ghost"] is None


def test_report_renders(conn, registry, regions):
    insert_run(conn)
    _observe(conn, "NL", "alpha")
    text = sources.render_report(conn, registry, regions, date(2026, 9, 7))
    assert "REGION BLOCKS" in text
    assert "EU_EEA" in text
    assert "UK" in text
