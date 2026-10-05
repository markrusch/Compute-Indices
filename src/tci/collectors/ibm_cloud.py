# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""IBM Cloud's 8x H100 virtual server price, from IBM's public Global Catalog API.

WHAT IS READ. One GET a day:
https://globalcatalog.cloud.ibm.com/api/v1/99548819-fcbc-486c-8eeb-0355a56b035e/pricing/deployment
That id is the pricing plan for the VPC profile `gx3d-160x1792x8h100`, and the deployment
endpoint returns every region the plan is sold in, nine on 5 October 2026, in one response.
The catalog is the public, documented API the IBM Cloud console and cost estimator read
(cloud.ibm.com/apidocs/resource-catalog/global-catalog). No key, no account, and the same
answer for every caller. globalcatalog.cloud.ibm.com serves no robots.txt (404).

Each region carries a list of metrics. Two are read and nothing else:
  part-is.instance-hours-gx3d-160x1792x8h100   "Virtual Server Instance Hours", the
                                                multi-tenant on-demand rate -> tier list
  part-is.spot-gx3d-160x1792x8h100             the spot rate -> tier spot
The dedicated-host meter of the same profile is priced 0 (the host is billed instead), and
the reservation meter is a commitment, so both are skipped. The OS and software add-ons
(RHEL, SUSE, Windows, SQL Server) are separate per-vCPU meters on top; the Linux instance
rate is the bare one. Amounts are given per country of the paying account; the USA row in
USD is read, because the index is quoted in USD and that row is IBM's own USD list price,
not a conversion. On 5 October the eu-de row read $99.60 per instance-hour, $12.45 per
GPU-hour; spot $24.8965.

THE PART. IBM's profile documentation (cloud.ibm.com/docs/vpc?topic=vpc-accelerated-profile-
family, read 2026-10-05) lists the gx3d profiles under "NVIDIA Hopper HGX instance
profiles" with accelerator "NVIDIA H100 SXM5 (80 GB)", and the profile name ends in 8h100:
eight of them. The profile is mapped by hand below, the Azure collector's rule for a feed
with no GPU-count field: a profile not listed is never emitted. IBM also sells
gx3d-160x1792x8h200 (plan 473375e7-f226-4deb-9214-dd68607b3d99); it is not read, because
a second request a day would break the one-request rule for a product no series uses.

REGIONS. The deployment location is IBM's region name. eu-de is Frankfurt and eu-es is
Madrid, the two EU/EEA rows; eu-gb (London) is stored against GB. A region not in the map
is stored with no country, so it reaches no block until someone has read where it is.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import requests

from tci.collectors.base import TIMEOUT_SECONDS
from tci.db import utc_now_iso
from tci.models import Observation

log = logging.getLogger("tci.collectors.ibm_cloud")

PLAN_ID = "99548819-fcbc-486c-8eeb-0355a56b035e"
URL = f"https://globalcatalog.cloud.ibm.com/api/v1/{PLAN_ID}/pricing/deployment"

# profile -> (TCI variant, GPUs per instance). Hand-mapped: see THE PART above.
PROFILES: dict[str, tuple[str, int]] = {
    "gx3d-160x1792x8h100": ("H100_SXM", 8),
}
PROFILE = "gx3d-160x1792x8h100"

# metric id -> stored tier. Every other metric on the plan is skipped.
METRICS: dict[str, str] = {
    f"part-is.instance-hours-{PROFILE}": "list",
    f"part-is.spot-{PROFILE}": "spot",
}

# IBM region -> ISO country, from IBM's region list (cloud.ibm.com/docs/overview?topic=
# overview-locations, read 2026-10-05).
REGION_COUNTRY: dict[str, str] = {
    "eu-de": "DE", "eu-es": "ES", "eu-gb": "GB",
    "us-east": "US", "us-south": "US", "ca-tor": "CA",
    "br-sao": "BR", "jp-tok": "JP", "au-syd": "AU",
}

PRICE_COUNTRY = "USA"
PRICE_CURRENCY = "USD"


def _instance_price(metric: dict[str, Any]) -> float | None:
    """The first-tier USD price per instance-hour, or None when IBM shows none."""
    for amount in metric.get("amounts") or []:
        if amount.get("country") != PRICE_COUNTRY or amount.get("currency") != PRICE_CURRENCY:
            continue
        prices = amount.get("prices") or []
        if not prices:
            return None
        price = prices[0].get("price")
        # A zero is a meter billed elsewhere (dedicated host), never a free GPU.
        return float(price) if isinstance(price, (int, float)) and price > 0 else None
    return None


def parse(payload: dict[str, Any], ts: str) -> list[Observation]:
    variant, gpus = PROFILES[PROFILE]
    out: list[Observation] = []
    for deployment in payload.get("resources") or []:
        region = str(deployment.get("deployment_region")
                     or deployment.get("deployment_location") or "")
        for metric in deployment.get("metrics") or []:
            tier = METRICS.get(str(metric.get("metric_id") or ""))
            if tier is None:
                continue
            price = _instance_price(metric)
            if price is None:
                continue
            out.append(
                Observation(
                    ts_utc=ts,
                    source="ibm_cloud",
                    provider="ibm",
                    gpu_model=variant,
                    gpu_count=gpus,
                    price_usd_per_gpu_hr=price / gpus,
                    region=region or None,
                    country=REGION_COUNTRY.get(region),
                    interconnect="NVLink",
                    tier=tier,
                    term="on_demand",
                    raw_json=json.dumps({
                        "plan_id": PLAN_ID,
                        "profile": PROFILE,
                        "deployment_id": deployment.get("deployment_id"),
                        "deployment_region": region,
                        "metric_id": metric.get("metric_id"),
                        "charge_unit": metric.get("charge_unit"),
                        "price_instance_hr": price,
                        "price_country": PRICE_COUNTRY,
                        "currency": PRICE_CURRENCY,
                        "gpus": gpus,
                        "effective_from": metric.get("effective_from"),
                    }),
                )
            )
    if not out:
        # The plan answered but carried neither meter: a reshaped catalog, not a quiet
        # market. Fail the day rather than store nothing as if nothing were for sale.
        raise ValueError("ibm_cloud: no on-demand or spot meter for the H100 profile")
    return out


class IbmCloudCollector:
    name = "ibm_cloud"

    def collect(self, session: requests.Session) -> list[Observation]:
        resp = session.get(URL, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
        rows = parse(resp.json(), utc_now_iso())
        log.info("ibm_cloud: %d rows", len(rows))
        return rows
