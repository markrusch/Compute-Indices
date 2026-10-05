"""IBM Cloud collector: the Global Catalog's deployment pricing for the 8x H100 profile.

The fixture is the live response of 5 October 2026, cut down to the two meters the
collector reads plus the reservation meter it must skip.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tci.collectors.ibm_cloud import parse

FIXTURE = Path(__file__).parent / "fixtures" / "ibm_cloud" / "deployment-pricing-2026-10-05.json"
TS = "2026-10-05T11:00:00Z"


@pytest.fixture()
def payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_one_on_demand_and_one_spot_row_per_region(payload: dict) -> None:
    rows = parse(payload, TS)
    assert len(rows) == 18  # nine regions, two meters each
    assert {o.tier for o in rows} == {"list", "spot"}
    assert all(o.gpu_model == "H100_SXM" and o.gpu_count == 8 for o in rows)
    assert all(o.term == "on_demand" and o.provider == "ibm" for o in rows)


def test_frankfurt_is_the_instance_price_divided_by_eight(payload: dict) -> None:
    rows = {(o.region, o.tier): o for o in parse(payload, TS)}
    de = rows[("eu-de", "list")]
    assert de.country == "DE"
    assert de.price_usd_per_gpu_hr == pytest.approx(99.60 / 8)
    assert json.loads(de.raw_json)["price_instance_hr"] == pytest.approx(99.60)
    assert rows[("eu-es", "list")].country == "ES"
    assert rows[("eu-gb", "list")].country == "GB"  # London: stored, never EU/EEA
    assert rows[("eu-de", "spot")].price_usd_per_gpu_hr == pytest.approx(24.8965 / 8)


def test_the_reservation_meter_is_never_read(payload: dict) -> None:
    metrics = {json.loads(o.raw_json)["metric_id"] for o in parse(payload, TS)}
    assert not any("reservation" in m for m in metrics)


def test_an_unknown_region_is_stored_without_a_country(payload: dict) -> None:
    p = copy.deepcopy(payload)
    p["resources"][0]["deployment_region"] = "xx-new"
    rows = [o for o in parse(p, TS) if o.region == "xx-new"]
    assert rows and all(o.country is None for o in rows)


def test_a_zero_price_is_skipped_not_stored(payload: dict) -> None:
    p = copy.deepcopy(payload)
    for m in p["resources"][3]["metrics"]:
        for a in m["amounts"]:
            for price in a["prices"]:
                price["price"] = 0
    assert "eu-de" not in {o.region for o in parse(p, TS)}


def test_a_catalog_without_the_meters_fails_the_day(payload: dict) -> None:
    p = copy.deepcopy(payload)
    for r in p["resources"]:
        r["metrics"] = [m for m in r["metrics"] if "reservation" in m["metric_id"]]
    with pytest.raises(ValueError):
        parse(p, TS)
