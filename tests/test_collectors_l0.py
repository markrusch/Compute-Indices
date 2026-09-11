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
from tci.vendor.computable.http import current_user_agent

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


def test_tenor_ranges_and_floors_are_not_given_a_tenor(collected: dict[str, list]) -> None:
    lam = [o for o in collected["lambda_pricing"] if o.term != "on_demand"]
    assert lam and {o.term for o in lam} == {"reserved_unspecified"}
    hyp = [o for o in collected["hyperstack"] if o.term != "on_demand"]
    assert hyp and {o.term for o in hyp} == {"reserved_unspecified"}


def test_display_currency_duplicates_are_dropped(collected: dict[str, list]) -> None:
    assert all(json.loads(o.raw_json)["currency"] in ("USD", "EUR")
               for rows in collected.values() for o in rows)


def test_unlabelled_form_factor_is_never_priced_as_sxm(collected: dict[str, list]) -> None:
    hyp = collected["hyperstack"]
    assert "H100_UNSPEC" in {o.gpu_model for o in hyp}


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
    assert {(o.provider, o.gpu_count) for o in before} == {("aws", 8), ("lambdalabs", 2)}
    assert {(o.provider, o.gpu_count) for o in after} == {
        ("aws", 8), ("gcp", 4), ("lambdalabs", 2)}  # spot rows never collected as on-demand


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
