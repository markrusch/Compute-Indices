# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Scaleway's public Product Catalog API (no auth): EU list prices, zone by zone.

Collection scope (SOURCES.md): scaleway.com/pricing renders GPU prices via JS only, so
Scaleway was first carried as a static entry (config/providers/scaleway.yaml) that never
had a price. This collector reads the numbers from Scaleway's own public API instead, one
GET per zone. The null static entry was retired on 2026-09-24.

WHERE THE PRICE IS. Until 28 October 2026 it was `hourly_price` in the Instance API
(`/instance/v1/zones/{zone}/products/servers`). From 29 October that field read 0.0 for
every one of the 50 server types in every zone, GPU or not, and the collector stored
$0.00 for each of them for nine days: the price band kept them out of every print, so
Scaleway simply vanished from the panel. The prices had moved to the Product Catalog
(`/product-catalog/v2alpha1/public-catalog/products`), which quotes them per MINUTE
(`unit_of_measure`), in EUR, as `units` plus `nanos`. Converted, they are the same
figures the Instance API had last given: H100-SXM-4-80G at EUR 0.2129/min is EUR 3.19 per
GPU-hour, H100-2-80G at EUR 0.0955/min is EUR 2.865 (both checked 2026-11-06 against the
26 October rows).

A price that is not positive is never recorded, and a run that finds no priced GPU at
all raises, so the failure shows in `runs` instead of as a successful run of zeros.
Retired products (`status: retired`, e.g. H100-1-M) are skipped: they cannot be bought.

Schema (verified 2026-11-06): one product per (offer, zone); the GPU count is
`properties.hardware.gpu.count`, the offer name `properties.instance.offer_id`. The price
is per INSTANCE, not per GPU and not USD. Dividing by the GPU count gives the EUR-per-GPU
native price. FX conversion is normalise.py's job: price_usd_per_gpu_hr carries the
EUR-per-GPU figure verbatim, and raw_json records the native currency and per-GPU amount
so that stays auditable.

Nine zones, one source, one logical "1 request per source per day" budget, but a single
zone failing must not take the other eight with it, so each zone is fetched and parsed
on its own; failures are logged and skipped. A zone's whole catalog fits in one page
(171 products for fr-par-2); a longer one is paged rather than truncated.
"""

from __future__ import annotations

import json
import logging

import requests

from tci.collectors.base import TIMEOUT_SECONDS
from tci.db import utc_now_iso
from tci.models import Observation

log = logging.getLogger("tci.collectors.scaleway")

URL = "https://api.scaleway.com/product-catalog/v2alpha1/public-catalog/products"
PAGE_SIZE = 1000
MINUTES = {"minute": 60.0, "hour": 1.0}  # unit_of_measure -> multiplier to per hour

ZONES = (
    "fr-par-1", "fr-par-2", "fr-par-3",
    "nl-ams-1", "nl-ams-2", "nl-ams-3",
    "pl-waw-1", "pl-waw-2", "pl-waw-3",
)

COUNTRY_BY_ZONE_PREFIX = {"fr": "FR", "nl": "NL", "pl": "PL"}


def _map_variant(instance_name: str) -> str | None:
    """Instance name -> canonical GPU variant; consumer/render types return None (skip)."""
    n = instance_name.upper()
    if n.startswith("H100"):
        return "H100_SXM" if "SXM" in n else "H100_PCIE"
    if n.startswith("B300"):
        return "B300_SXM" if "SXM" in n else None  # only the SXM B300 shape is mapped
    if n.startswith("L40S"):
        return "L40S"
    if n.startswith("L4"):
        return "L4"
    if n.startswith("H200"):
        return "H200_SXM"
    if n.startswith("A100"):
        return "A100_SXM" if "SXM" in n else "A100_PCIE"
    return None  # RENDER-*, GPU-3070-*, and other consumer/render types


class ScalewayCollector:
    name = "scaleway"

    def collect(self, session: requests.Session) -> list[Observation]:
        ts = utc_now_iso()
        out: list[Observation] = []
        for zone in ZONES:
            try:
                products = _zone_products(session, zone)
            except (requests.RequestException, ValueError) as exc:
                log.warning("scaleway: zone %s failed (%s), skipping", zone, exc)
                continue
            out.extend(self._zone_observations(zone, products, ts))
        if not out:
            raise RuntimeError(
                "scaleway: no priced GPU instance in any zone -- the catalog moved or "
                "zeroed its prices again; refusing to report a successful empty run"
            )
        log.info("scaleway: %d observations across %d zones", len(out), len(ZONES))
        return out

    def _zone_observations(
        self, zone: str, products: list[dict], ts: str
    ) -> list[Observation]:
        country = COUNTRY_BY_ZONE_PREFIX.get(zone.split("-")[0])
        out: list[Observation] = []
        for product in products:
            if product.get("product_category") != "Instance":
                continue
            if product.get("status") == "retired":
                continue
            props = product.get("properties") or {}
            gpu = ((props.get("hardware") or {}).get("gpu") or {}).get("count")
            instance_name = (props.get("instance") or {}).get("offer_id") or product.get(
                "product", "")
            if not gpu:
                continue  # non-GPU instance
            gpu_model = _map_variant(instance_name)
            if gpu_model is None:
                continue
            retail = (product.get("price") or {}).get("retail_price") or {}
            unit = (product.get("unit_of_measure") or {})
            per_hour = MINUTES.get(str(unit.get("unit")))
            size = unit.get("size") or 1
            if retail.get("currency_code") != "EUR" or per_hour is None:
                log.warning("scaleway: %s in %s priced in %s per %s %s; skipped, not guessed",
                            instance_name, zone, retail.get("currency_code"), size,
                            unit.get("unit"))
                continue
            price_per_unit = (retail.get("units") or 0) + (retail.get("nanos") or 0) / 1e9
            if price_per_unit <= 0:
                log.warning("scaleway: %s in %s has no price (%s); skipped, never recorded "
                            "as zero", instance_name, zone, price_per_unit)
                continue
            hourly_price = price_per_unit * per_hour / size
            price_native_per_gpu_hr = hourly_price / gpu
            out.append(
                Observation(
                    ts_utc=ts,
                    source=self.name,
                    provider="scaleway",
                    gpu_model=gpu_model,
                    gpu_count=int(gpu),
                    # EUR-per-GPU, not USD -- see module docstring; FX is normalise.py's job
                    price_usd_per_gpu_hr=price_native_per_gpu_hr,
                    region=zone,
                    country=country,
                    interconnect="NVLink" if "SXM" in instance_name.upper() else "PCIe",
                    tier="list",
                    term="on_demand",
                    raw_json=json.dumps(
                        {
                            "instance_name": instance_name,
                            "zone": zone,
                            "sku": product.get("sku"),
                            "price_per_unit": price_per_unit,
                            "unit": f"{size} {unit.get('unit')}",
                            "hourly_price": hourly_price,
                            "gpu": gpu,
                            "currency": "EUR",
                            "price_native_per_gpu_hr": price_native_per_gpu_hr,
                        }
                    ),
                )
            )
        return out


def _zone_products(session: requests.Session, zone: str) -> list[dict]:
    """Every instance product the catalog lists for one zone, across pages."""
    products: list[dict] = []
    page = 1
    while True:
        params: dict[str, str | int] = {"product_types": "instance", "zone": zone,
                                        "page_size": PAGE_SIZE, "page": page}
        resp = session.get(URL, params=params, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
        body = resp.json()
        batch = body.get("products") or []
        products.extend(batch)
        if not batch or len(products) >= int(body.get("total_count") or 0):
            return products
        page += 1
