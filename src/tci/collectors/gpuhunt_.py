# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Hyperscaler list prices via the open-source gpuhunt package (dstack catalogs).

Scope: aws / azure / gcp only (other gpuhunt providers are covered directly or are
out of scope), 8-GPU H100 instances only (the unit is an 8-GPU NVLink node price),
on-demand (spot=False). Catalog item price is per instance-hour -> divide by count.
Regions are mapped to countries below; unmapped regions yield country=None and are
excluded by the normaliser — the safe default for new/unknown regions.

The collector is global and always has been: the package downloads whole catalogs and
queries them locally, so the request cost does not change with how many regions we keep.
Only EU/EEA rows reach a published print (normalise.py filters on country); everything
else accumulates against the shadow blocks in config/regions.yaml.
"""

from __future__ import annotations

import json
import logging
from importlib.metadata import PackageNotFoundError, version

import requests

from tci.db import utc_now_iso
from tci.models import Observation

log = logging.getLogger("tci.collectors.gpuhunt")

PROVIDERS = ("aws", "azure", "gcp")

# Region -> ISO country. An unmapped region yields country=None, which the normaliser
# drops, so this table decides what the pipeline can ever use. It was EU/EEA plus a few US
# regions until 2026-09-07, and the first run of the source-discovery process measured what
# that cost: 1,518 stored rows — Singapore, Tokyo, Seoul, Mumbai, Sydney, London, Montreal,
# Sao Paulo — collected, paid for, and made unusable for want of a dictionary entry. The
# catalog is downloaded whole, so every line below costs zero extra requests.
#
# Countries outside the EEA are excluded from every published series by the country filter
# in normalise.py. They accumulate against config/regions.yaml's shadow blocks, which is
# the point: a regional index needs 180 days of history before its first print is worth
# anything, and that clock only starts once the rows are being kept properly.
#
# Deliberately NOT mapped, and each for its own reason:
#   us-gov-*, *-gov-*     GovCloud sells to a different buyer on a different price ladder
#   us-east-1-atl-1 etc.  AWS Local Zones carry a location premium over the parent region
#   cn-*, china*          operated by separate legal entities on separate price lists
REGION_COUNTRY = {
    # -- EU / EEA: the published population --------------------------------------
    # aws
    "eu-west-1": "IE", "eu-west-3": "FR", "eu-central-1": "DE", "eu-north-1": "SE",
    "eu-south-1": "IT", "eu-south-2": "ES",
    # azure
    "westeurope": "NL", "northeurope": "IE", "germanywestcentral": "DE",
    "francecentral": "FR", "swedencentral": "SE", "italynorth": "IT",
    "spaincentral": "ES", "polandcentral": "PL",
    "norwayeast": "NO", "norwaywest": "NO",
    # gcp (zone suffixes stripped before lookup)
    "europe-west1": "BE", "europe-west3": "DE", "europe-west4": "NL",
    "europe-north1": "FI", "europe-west9": "FR", "europe-southwest1": "ES",
    "europe-west8": "IT", "europe-west12": "IT", "europe-central2": "PL",
    "europe-west10": "DE",

    # -- US ----------------------------------------------------------------------
    "us-east-1": "US", "us-east-2": "US", "us-west-1": "US", "us-west-2": "US",
    "eastus": "US", "eastus2": "US", "westus3": "US", "southcentralus": "US",
    "us-east4": "US", "us-east5": "US", "us-central1": "US", "us-west1": "US",
    "us-west4": "US",

    # -- UK ----------------------------------------------------------------------
    "eu-west-2": "GB", "uksouth": "GB", "ukwest": "GB", "europe-west2": "GB",

    # -- Switzerland -------------------------------------------------------------
    "eu-central-2": "CH", "switzerlandnorth": "CH", "switzerlandwest": "CH",
    "europe-west6": "CH",

    # -- Canada ------------------------------------------------------------------
    "ca-central-1": "CA", "ca-west-1": "CA", "canadacentral": "CA", "canadaeast": "CA",
    "northamerica-northeast1": "CA", "northamerica-northeast2": "CA",

    # -- Latin America -----------------------------------------------------------
    "sa-east-1": "BR", "brazilsouth": "BR", "southamerica-east1": "BR",
    "southamerica-west1": "CL",

    # -- Asia-Pacific, north-east ------------------------------------------------
    "ap-northeast-1": "JP", "ap-northeast-3": "JP", "japaneast": "JP", "japanwest": "JP",
    "asia-northeast1": "JP", "asia-northeast2": "JP",
    "ap-northeast-2": "KR", "koreacentral": "KR", "koreasouth": "KR",
    "asia-northeast3": "KR",
    "ap-east-1": "HK", "eastasia": "HK", "asia-east2": "HK",
    "asia-east1": "TW",

    # -- Asia-Pacific, south-east ------------------------------------------------
    "ap-southeast-1": "SG", "southeastasia": "SG", "asia-southeast1": "SG",
    "ap-southeast-3": "ID", "asia-southeast2": "ID",

    # -- South Asia --------------------------------------------------------------
    "ap-south-1": "IN", "ap-south-2": "IN", "centralindia": "IN", "southindia": "IN",
    "westindia": "IN", "asia-south1": "IN", "asia-south2": "IN",

    # -- Oceania -----------------------------------------------------------------
    "ap-southeast-2": "AU", "ap-southeast-4": "AU", "australiaeast": "AU",
    "australiasoutheast": "AU", "australia-southeast1": "AU", "australia-southeast2": "AU",

    # -- Middle East and Africa --------------------------------------------------
    "il-central-1": "IL", "israelcentral": "IL", "me-west1": "IL",
    "me-central-1": "AE", "uaenorth": "AE",
    "me-south-1": "BH",
    "qatarcentral": "QA", "me-central1": "QA",
    "af-south-1": "ZA", "southafricanorth": "ZA", "africa-south1": "ZA",
}


def _country(provider: str, location: str) -> str | None:
    loc = location.lower()
    if provider == "gcp" and loc.count("-") == 2:
        loc = loc.rsplit("-", 1)[0]  # zone -> region (europe-west4-c -> europe-west4)
    return REGION_COUNTRY.get(loc)


def _gpuhunt_version() -> str:
    try:
        return version("gpuhunt")
    except PackageNotFoundError:
        return "unknown"


class GpuHuntCollector:
    name = "gpuhunt"

    def collect(self, session: requests.Session) -> list[Observation]:
        # session unused: gpuhunt fetches dstack's published catalog files itself
        import gpuhunt

        items = gpuhunt.query(gpu_name=["H100"], provider=list(PROVIDERS), spot=False)
        return self.to_observations(items)

    def to_observations(self, items: list) -> list[Observation]:
        ts = utc_now_iso()
        pkg_version = _gpuhunt_version()
        out: list[Observation] = []
        for item in items:
            gpu_count = int(item.gpu_count or 0)
            if gpu_count < 8:
                continue  # unit is an 8-GPU node; smaller instances never match
            country = _country(item.provider, item.location or "")
            out.append(
                Observation(
                    ts_utc=ts,
                    source=self.name,
                    provider=item.provider,
                    gpu_model="H100_SXM",  # 8x hyperscaler H100 nodes are SXM/HGX
                    gpu_count=gpu_count,
                    price_usd_per_gpu_hr=float(item.price) / gpu_count,
                    region=item.location,
                    country=country,
                    interconnect="NVLink",
                    tier="list",
                    term="on_demand",
                    raw_json=json.dumps(
                        {
                            "instance_name": item.instance_name,
                            "location": item.location,
                            "price_instance_hr": item.price,
                            "gpu_name": item.gpu_name,
                            "gpu_memory": getattr(item, "gpu_memory", None),
                            "gpuhunt_version": pkg_version,
                        }
                    ),
                )
            )
        log.info("gpuhunt: %d 8-GPU H100 instances (all regions)", len(out))
        return out
