# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Collectors added 2026-09-11: gpuhunt's wider catalogue, the vendored Computable recipes,
RunPod's datacentre stock. Fixture-only, no live network."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest
import requests
import responses

from tci import USER_AGENT
from tci.collectors import base
from tci.collectors.computable_sources import computable_collectors, term_of, variant_of
from tci.collectors.gpuhunt_ import GpuHuntCollector, _country, collection_floor
from tci.collectors.gpuhunt_ import variant_of as gh_variant
from tci.collectors.runpod import URL as RUNPOD_URL
from tci.collectors.runpod import RunPodCollector, parse_datacentre_stock
from tci.models import Observation
from tci.vendor.computable.http import current_user_agent
from tci.vendor.computable.sources.coreweave import parse_coreweave

FIX = Path(__file__).parent / "fixtures" / "computable"
ROUTES = {
    "lambda.ai/pricing": "lambda/pricing.html",
    "hyperstack.cloud/gpu-pricing": "hyperstack/gpu-pricing.html",
    "gpu-stock.hyperstack": "hyperstack/gpu-stock.json",
    "latitude.sh/pricing": "latitude/pricing_excerpt.html",
    "coreweave.com/pricing": "coreweave/pricing.html",
    "bare-metal/locations": "voltagepark/locations.json",
    "instant-deploy-presets": "voltagepark/instant_deploy_presets.json",
    "virtual-machines/instant/locations": "voltagepark/vm_instant_locations.json",
    "digitalocean.com": "digitalocean/droplets_pricing.html",
    "civo.com/pricing": "civo/pricing_excerpt.html",
    "civo.com/ai/cloud-gpu": "civo/cloud_gpu_hub_excerpt.html",
    "crusoe.ai": "crusoe/pricing.html",
    "api.ovh.com/1.0/order/catalog": "ovh/order_catalog_public_cloud_fr.json",
    "api.us.ovhcloud.com/1.0/order/catalog": "ovh/order_catalog_public_cloud_us.json",
    "api.ovh.com/v1/dedicated": "ovh/dedicated_availabilities_eu.json",
    "api.us.ovhcloud.com/1.0/dedicated": "ovh/dedicated_availabilities_us.json",
}
USER_AGENTS_SEEN: list[str] = []


def _fake_fetch(url: str, *args: object, **kwargs: object) -> str:
    USER_AGENTS_SEEN.append(current_user_agent())
    for key, rel in ROUTES.items():
        if key in url:
            return (FIX / rel).read_text(encoding="utf-8")
    raise AssertionError(f"no fixture for {url}")


@pytest.fixture()
def collected(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    out = {}
    for c in computable_collectors():
        monkeypatch.setattr(c.module, "fetch", _fake_fetch)
        out[c.name] = c.collect(base.make_session())
    return out


# ------------------------------------------------------------------- vendored recipes

def test_every_recipe_parses_its_fixture(collected: dict[str, list]) -> None:
    for name, rows in collected.items():
        assert rows, f"{name} produced no rows from its fixture"


def test_requests_carry_tcis_user_agent(collected: dict[str, list]) -> None:
    assert USER_AGENTS_SEEN and set(USER_AGENTS_SEEN) == {USER_AGENT}


def test_ovh_h100_is_pcie_in_eur_and_french(collected: dict[str, list]) -> None:
    h100 = [o for o in collected["ovh"] if o.gpu_model == "H100_PCIE" and o.country == "FR"]
    assert h100, "OVH's H100 must be priced as PCIe, never as the SXM reference unit"
    assert not [o for o in collected["ovh"] if o.gpu_model == "H100_SXM"]
    raw = json.loads(h100[0].raw_json)
    assert raw["currency"] == "EUR" and raw["price_native_per_gpu_hr"] > 0
    assert {o.term for o in h100} >= {"on_demand", "commit_1mo"}


def test_civo_publishes_a_term_structure(collected: dict[str, list]) -> None:
    terms = {o.term for o in collected["civo"] if o.gpu_model == "H100_SXM"}
    assert terms == {"on_demand", "commit_6mo", "reserved_1yr", "reserved_2yr",
                     "reserved_3yr"}
    assert all(o.country is None for o in collected["civo"])  # the page names no region


def test_coreweave_continent_labels_map_to_one_representative_country_each(
    collected: dict[str, list]
) -> None:
    """CoreWeave states only 'NORTH AMERICA' / 'EUROPE', never a country. Mapping either
    to a specific code is an approximation authorised directly by Mark (2026-09-12) for
    TCI's EU-vs-US framing, not a claim the source itself makes -- see
    `computable_sources._coreweave_country`. This only checks the mapping is wired and
    total: every collected row gets one of the two countries, never a guess at a third."""
    from tci.collectors.computable_sources import _COREWEAVE_COUNTRY

    rows = collected["coreweave"]
    assert rows, "fixture produced no coreweave rows"
    seen = {(o.region, o.country) for o in rows}
    assert seen, "no rows to check"
    for region, country in seen:
        assert country == _COREWEAVE_COUNTRY[region]
    assert {r for r, _c in seen} <= {"NORTH AMERICA", "EUROPE"}


def test_tenor_ranges_and_floors_are_not_given_a_tenor(collected: dict[str, list]) -> None:
    lam = [o for o in collected["lambda_pricing"] if o.term != "on_demand"]
    assert lam and {o.term for o in lam} == {"reserved_unspecified"}
    hyp = [o for o in collected["hyperstack"] if o.term != "on_demand"]
    assert hyp and {o.term for o in hyp} == {"reserved_unspecified"}


def test_latitude_prepaid_annual_is_recorded_as_a_12_month_commitment(
    collected: dict[str, list]
) -> None:
    """L5.3: the vendored recipe's own loop discards the year price into `_year_s`. TCI's
    adapter (latitude_annual.py) reads the same already-fetched body a second time and
    turns it into a reserved_1yr row, without editing the vendored file."""
    annual = [o for o in collected["latitude"] if o.term == "reserved_1yr"]
    assert annual, "latitude produced no prepaid-annual rows from its fixture"
    for o in annual:
        assert o.tier == "list"
        assert json.loads(o.raw_json)["extra"]["commitment_months"] == 12
        # A deeper discount than the monthly tier, for the same plan/region/currency.
        monthly = [m for m in collected["latitude"]
                  if m.term == "commit_1mo" and m.gpu_model == o.gpu_model
                  and m.region == o.region]
        if monthly:
            assert o.price_usd_per_gpu_hr < monthly[0].price_usd_per_gpu_hr


def test_latitude_annual_reading_does_not_reach_the_network_in_tests(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression this guards: `_fetch_latitude` first called a `fetch` imported
    directly into computable_sources.py, invisible to `monkeypatch.setattr(module,
    "fetch", ...)`, which is how every test in this file stays offline. That version
    reached the live network on every test run instead of the fixture."""
    from tci.collectors.computable_sources import _fetch_latitude
    from tci.vendor.computable.sources import latitude as latitude_module

    monkeypatch.setattr(latitude_module, "fetch", _fake_fetch)
    result = _fetch_latitude(timeout=5.0)
    assert result["observations"]
    assert any(o["tier"] == "reserved" for o in result["observations"])


def test_latitude_reads_the_camel_case_keys_it_switched_to_on_22_september(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Latitude renamed its embedded plan keys to camelCase, and the vendored parser
    raised on every session from 22 September. The fixture predates the rename, so it is
    rewritten into the new shape here and must yield exactly what the old shape did."""
    from tci.collectors.computable_sources import LATITUDE_KEY_RENAMES, _fetch_latitude
    from tci.vendor.computable.sources import latitude as latitude_module

    def rows(fetch: object) -> list[tuple]:
        monkeypatch.setattr(latitude_module, "fetch", fetch)
        return sorted(
            (o["sku_identifier"], o["region"], o["tier"], o["currency"],
             o["price_native_per_gpu_hr"])
            for o in _fetch_latitude(timeout=5.0)["observations"]
        )

    def camel_case_fetch(url: str, *a: object, **k: object) -> str:
        body = _fake_fetch(url)
        for camel, snake in LATITUDE_KEY_RENAMES.items():
            body = body.replace(f'"{snake}', f'"{camel}')
        return body

    camel_page = camel_case_fetch(latitude_module.URL)
    assert "vramPerGpu" in camel_page and "vram_per_gpu" not in camel_page
    with pytest.raises(RuntimeError, match="gpu spec"):  # the failure seen live
        latitude_module.parse_latitude(camel_page)

    before = rows(_fake_fetch)
    assert before and rows(camel_case_fetch) == before


def test_display_currency_duplicates_are_dropped(collected: dict[str, list]) -> None:
    assert all(json.loads(o.raw_json)["currency"] in ("USD", "EUR")
               for rows in collected.values() for o in rows)


def _sku(o: Observation) -> str:
    return json.loads(o.raw_json)["sku_identifier"]


def test_unlabelled_form_factor_is_never_priced_as_sxm(collected: dict[str, list]) -> None:
    """Hyperstack's plain "NVIDIA H100" is the PCIe card by its own stock feed, which
    names that flavour H100-80G-PCIe; only the label saying SXM is the reference unit."""
    by_label = {_sku(o): o.gpu_model for o in collected["hyperstack"]}
    assert by_label["NVIDIA H100"] == "H100_PCIE"
    assert by_label["NVIDIA H100 NVLink"] == "H100_PCIE_NVLINK"  # bridged: in no class
    assert by_label["NVIDIA H100 SXM"] == "H100_SXM"
    assert by_label["NVIDIA A100"] == "A100_PCIE"
    assert by_label["NVIDIA A100 SXM"] == "A100_SXM"
    assert not {m for m in by_label.values() if m.endswith("UNSPEC")}


def _hyperstack_on_demand(collected: dict[str, list]) -> list[Observation]:
    return [o for o in collected["hyperstack"] if o.tier == "list" and o.term == "on_demand"]


def test_hyperstack_flat_price_is_placed_only_where_its_stock_feed_shows_a_node(
    collected: dict[str, list]
) -> None:
    """The captured feed (2026-08-25) has H100 SXM deployable in CANADA-1 as 2 x 8-GPU
    VMs, A100 SXM in US-1 as 2 x 8, and NORWAY-1 carrying only an RTX A4000."""
    placed = sorted((o.gpu_model, o.region, o.country, o.gpu_count)
                    for o in _hyperstack_on_demand(collected) if o.country)
    assert placed == [
        ("A100_PCIE", "CANADA-1", "CA", 4),
        ("A100_SXM", "US-1", "US", 8),
        ("H100_PCIE", "CANADA-1", "CA", 1),
        ("H100_SXM", "CANADA-1", "CA", 8),
    ]


def test_hyperstack_zero_stock_places_nothing(collected: dict[str, list]) -> None:
    """H200 SXM, B200 and B300 are listed in CANADA-1 with every configuration at 0.
    Listed is not deployable, so they keep only their unplaced row."""
    rows = [o for o in _hyperstack_on_demand(collected)
            if o.gpu_model in ("H200_SXM", "B200_SXM", "B300_SXM")]
    assert rows and all(o.country is None and o.gpu_count == 1 for o in rows)


def test_hyperstack_keeps_its_unplaced_row_and_records_the_stock_it_used(
    collected: dict[str, list]
) -> None:
    h100 = [o for o in _hyperstack_on_demand(collected) if o.gpu_model == "H100_SXM"]
    assert sorted((o.region, o.country) for o in h100) == [
        ("CANADA-1", "CA"), ("EU-heavy", None)]
    assert len({o.price_usd_per_gpu_hr for o in h100}) == 1  # region-flat
    placed = next(o for o in h100 if o.country)
    stock = json.loads(placed.raw_json)["stock"]
    assert stock["model"] == "H100-80G-SXM5"
    assert stock["configurations"]["8x"] == 2
    assert stock["worker_fetched_at"] == "2026-08-25T20:15:59.101Z"


def test_hyperstack_prices_survive_a_dead_stock_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The feed is not a contractual API. Without it nothing is placed, and nothing
    else is lost."""
    src = next(c for c in computable_collectors() if c.name == "hyperstack")

    def no_stock(url: str, *a: object, **k: object) -> str:
        if "gpu-stock" in url:
            raise requests.ConnectionError("stock worker down")
        return _fake_fetch(url)

    monkeypatch.setattr(src.module, "fetch", no_stock)
    rows = src.collect(base.make_session())
    assert rows and all(o.country is None for o in rows)
    assert "H100_SXM" in {o.gpu_model for o in rows}


def test_digitalocean_h100_is_one_row_per_region_it_is_sold_in(
    collected: dict[str, list]
) -> None:
    h100 = [o for o in collected["digitalocean"] if o.gpu_model == "H100_SXM"
            and o.term == "on_demand" and o.tier == "list" and o.gpu_count == 8]
    assert sorted((o.region, o.country) for o in h100) == [
        ("AMS3", "NL"), ("NYC2", "US"), ("TOR1", "CA")]
    assert len({o.price_usd_per_gpu_hr for o in h100}) == 1  # region-flat
    assert {o.gpu_model for o in collected["digitalocean"] if "H200" in o.gpu_model} == {
        "H200_UNSPEC"}


def test_every_row_is_storable(conn: sqlite3.Connection, collected: dict[str, list]) -> None:
    class Replay:
        def __init__(self, name: str, rows: list) -> None:
            self.name, self.rows = name, rows

        def collect(self, session: requests.Session) -> list:
            return self.rows

    for name, rows in collected.items():
        assert base.run_collector(conn, Replay(name, rows), "2026-09-11") == "ok", name


@pytest.mark.parametrize(
    ("sku", "label", "variant"),
    [("H100", "NVIDIA HGX H100", "H100_SXM"), ("H100", "NVIDIA H100", "H100_UNSPEC"),
     ("H100_PCIE", "H100 PCIe", "H100_PCIE"), ("H100_NVL", "H100 NVL", "H100_NVL_94GB"),
     ("H200", "NVIDIA H200 SXM", "H200_SXM"), ("B200", "NVIDIA B200", "B200_SXM"),
     ("A100", "A100 40GB", "A100_40GB"), ("A100", "A100 SXM", "A100_SXM"),
     (None, "whatever", None)],
)
def test_variant_rules(sku: str | None, label: str, variant: str | None) -> None:
    assert variant_of(sku, label) == variant


def test_term_rules() -> None:
    assert term_of({"tier": "on-demand"}) == ("list", "on_demand")
    assert term_of({"tier": "monthly-commit"}) == ("list", "commit_1mo")
    assert term_of({"tier": "reserved", "extra": {"commitment_months": 36}}) == (
        "list", "reserved_3yr")
    assert term_of({"tier": "reserved", "extra": {"commitment_months": 9}}) == (
        "list", "reserved_unspecified")
    assert term_of({"tier": "serverless"}) is None


# ------------------------------------------------------------------- gpuhunt


@dataclass
class _Item:
    provider: str
    instance_name: str
    location: str
    price: float
    gpu_count: int
    gpu_name: str
    gpu_memory: float | None = 80.0
    spot: bool = False


@pytest.mark.parametrize(
    ("provider", "name", "gpu", "mem", "variant"),
    [("lambdalabs", "gpu_8x_h100_sxm5", "H100", 80, "H100_SXM"),
     ("lambdalabs", "gpu_1x_h100_pcie", "H100", 80, "H100_PCIE"),
     ("lambdalabs", "gpu_8x_h100", "H100", 80, None),          # no form factor: skipped
     ("nebius", "gpu-h100-sxm 8gpu-128vcpu-1600gb", "H100", 80, "H100_SXM"),
     ("oci", "BM.GPU.H100.8", "H100", 80, "H100_SXM"),
     ("verda", "8H100.80S.176V", "H100", 80, "H100_SXM"),
     ("azure", "NC80adis_H100_v5", "H100", 94, "H100_NVL_94GB"),
     ("azure", "ND96isr_H100_v5", "H100", 80, "H100_SXM"),
     ("oci", "BM.GPU4.8", "A100", 40, "A100_SXM_40GB"),
     ("verda", "8B200.180S", "B200", 180, "B200_SXM")],
)
def test_gpuhunt_form_factor_is_pinned_from_the_instance_name(
    provider: str, name: str, gpu: str, mem: float, variant: str | None
) -> None:
    assert gh_variant(provider, name, gpu, mem, 8) == variant


def test_gpuhunt_legacy_floor_holds_until_the_notice_takes_effect() -> None:
    assert collection_floor("aws", "2026-09-14") == 8
    assert collection_floor("aws", "2026-09-15") == 2
    assert collection_floor("lambdalabs", "2026-09-12") == 2  # never a constituent before


def test_gpuhunt_new_regions_resolve() -> None:
    assert _country("verda", "FIN-01") == "FI"
    assert _country("nebius", "eu-north1") == "FI"
    assert _country("lambdalabs", "europe-central-1") == "DE"
    assert _country("oci", "eu-frankfurt-1") == "DE"
    assert _country("oci", "uk-london-1") == "GB"
    assert _country("oci", "eu-zurich-1") == "CH"


def test_gpuhunt_rows_before_and_after_the_floor_change() -> None:
    items = [_Item("aws", "p5.48xlarge", "eu-north-1", 63.86, 8, "H100"),
             _Item("gcp", "a3-highgpu-4g", "europe-west4-b", 44.0, 4, "H100"),
             _Item("lambdalabs", "gpu_2x_h100_sxm5", "europe-central-1", 8.38, 2, "H100"),
             _Item("verda", "8H100.80S.176V", "FIN-01", 26.0, 8, "H100", spot=True)]
    before = GpuHuntCollector().to_observations(items, utc_date="2026-09-14")
    after = GpuHuntCollector().to_observations(items, utc_date="2026-09-15")
    # verda's spot row clears the (unchanged, non-legacy) floor on both dates and is
    # stored as tier="spot", not dropped -- only the floor gates which providers appear.
    assert {(o.provider, o.gpu_count) for o in before} == {
        ("aws", 8), ("lambdalabs", 2), ("verda", 8)}
    assert {(o.provider, o.gpu_count) for o in after} == {
        ("aws", 8), ("gcp", 4), ("lambdalabs", 2), ("verda", 8)}


def test_gpuhunt_spot_rows_are_stored_with_tier_spot_not_dropped() -> None:
    """The safety property this depends on lives in normalise.py, not here: tier='spot'
    is structurally excluded from every print (test_normalise.py checks that generically).
    This test only proves the collector still stores the row instead of discarding it."""
    items = [_Item("aws", "p5.48xlarge", "eu-north-1", 63.86, 8, "H100", spot=False),
             _Item("aws", "p5.48xlarge", "eu-north-1", 38.32, 8, "H100", spot=True)]
    out = GpuHuntCollector().to_observations(items, utc_date="2026-09-15")
    by_tier = {o.tier: o for o in out}
    assert set(by_tier) == {"list", "spot"}
    spot = by_tier["spot"]
    assert spot.term == "on_demand"  # spot is a tier, not a commitment tenor
    assert spot.gpu_model == by_tier["list"].gpu_model == "H100_SXM"
    assert spot.country == by_tier["list"].country == "SE"
    assert abs(spot.price_usd_per_gpu_hr - 38.32 / 8) < 1e-9


# ------------------------------------------------------------------- runpod


def test_runpod_datacentre_stock_parse() -> None:
    payload = {"data": {"dataCenters": [
        {"id": "EU-RO-1", "listed": True, "gpuAvailability": [
            {"gpuTypeId": "NVIDIA H100 80GB HBM3", "available": True, "stockStatus": "High"}]},
        {"id": "US-TX-3", "listed": True, "gpuAvailability": [
            {"gpuTypeId": "NVIDIA H100 80GB HBM3", "available": True, "stockStatus": "Low"},
            {"gpuTypeId": "NVIDIA A100-SXM4-80GB", "available": False, "stockStatus": None}]},
        {"id": "EU-XX-9", "listed": False, "gpuAvailability": [
            {"gpuTypeId": "NVIDIA H100 80GB HBM3", "available": True, "stockStatus": "High"}]},
    ]}}
    assert parse_datacentre_stock(payload) == {"NVIDIA H100 80GB HBM3": ["EU-RO-1", "US-TX-3"]}


@responses.activate
def test_runpod_prices_survive_a_failed_stock_query() -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures" / "runpod_gputypes.json").read_text())
    responses.add(responses.POST, RUNPOD_URL, json=fixture, status=200)
    responses.add(responses.POST, RUNPOD_URL, json={"errors": [{"message": "no"}]}, status=200)
    out = RunPodCollector().collect(base.make_session())
    assert out and all(json.loads(o.raw_json)["datacentres_in_stock"] is None for o in out)


# --------------------------------------------------------------- coreweave


def test_coreweave_survives_the_json_ld_block_added_on_12_september_2026() -> None:
    """The reshape that broke the collector, kept as a fixture so it cannot break again.

    CoreWeave added a schema.org OfferCatalog block to the head of its pricing page whose
    `name` fields repeat "On-demand GPU instances" and "On-demand CPU instances" as plain
    text before the real headings. The recipe bounded the GPU section by counting bare
    occurrences of those strings — deliberately, because the CPU tables below reuse the
    same row markup — so both anchors counted twice and it refused the page outright,
    which is what it is supposed to do when a page reshapes under it.

    The fixture is `pricing.html` as captured on 2026-09-11 with that block inserted
    verbatim from the live page, so the anchor counts are the 2/2 that failed. Anchoring
    on the h2 tag rather than the bare string is what fixes it, and the fix must not
    weaken the guarantee: a reshape of the heading itself still has to raise.
    """
    fixture = FIX / "coreweave" / "pricing-2026-09-12-jsonld.html"
    html = fixture.read_text(encoding="utf-8")
    assert html.count("On-demand GPU instances") == 2, "fixture no longer reproduces the break"
    assert html.count("On-demand CPU instances") == 2

    rows = parse_coreweave(html)[0]
    baseline = parse_coreweave((FIX / "coreweave" / "pricing.html").read_text(encoding="utf-8"))[0]
    assert rows, "the JSON-LD block still stops the page being read"
    assert len(rows) == len(baseline), "the SEO block changed which rows are read"

    # The fence around the CPU tables is the reason the count check exists at all.
    assert all(r["region"] in ("NORTH AMERICA", "EUROPE") for r in rows)


def test_coreweave_still_refuses_a_page_whose_headings_have_changed() -> None:
    """Fail-closed, not fail-loose. The fix moved the anchor; it did not relax it."""
    html = (FIX / "coreweave" / "pricing.html").read_text(encoding="utf-8")
    reshaped = html.replace('class="heading-32-20">On-demand GPU instances</h2>',
                            'class="heading-40-24">On-demand GPU instances</h2>')
    assert reshaped != html, "the fixture no longer carries the heading this test edits"
    with pytest.raises(RuntimeError, match="section heading"):
        parse_coreweave(reshaped)
