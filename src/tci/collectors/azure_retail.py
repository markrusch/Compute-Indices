# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Azure Retail Prices API (no auth) — EEA list prices for mapped ND/NC H100/A100/H200 SKUs.

Public, unauthenticated pricing feed (prices.azure.com); one OData query per EEA region,
paginated via NextPageLink and capped at MAX_PAGES_PER_REGION so a stuck region can't
balloon the request count. The API exposes no GPU-count field, so only SKUs hand-mapped
in SKU_MAP (verified against Microsoft's own VM-size docs) are emitted — everything else
is skipped rather than guessed. In particular Standard_ND128isr_NDR_GB200_v6 is
deliberately excluded: its accelerator-per-node count is unverified, and a wrong
denominator would silently corrupt a per-GPU series.

Verified against a live response on 2026-08-15. A region that errors is logged and
skipped; it never takes the other regions down with it.

switzerlandnorth and uksouth are deliberately NOT queried — neither is in the EEA.

TENOR AND TIER (added 2026-09-08). Azure publishes four price points for the same node,
and until now this collector kept one of them. It now records all four, because the
spread between them is the only observed term structure available to this index:

    Consumption, base meter        -> tier=list,  term=on_demand
    Consumption, "Spot" meter      -> tier=spot,  term=on_demand
    Consumption, "Low Priority"    -> tier=interruptible, term=on_demand
    Reservation, reservationTerm   -> tier=list,  term=reserved_1yr | _3yr | _5yr

None of the new rows can reach a published print. `normalise.py` admits only
`term == reference_unit.term` (on_demand) and only `tier in (executable, list)`, so the
reserved and spot rows are stored, auditable and structurally excluded. Admitting any of
them would be a methodology change under GOVERNANCE.md §1 and would need a notice.

THE RESERVATION UNIT IS NOT AN HOURLY RATE. Azure returns the whole-term upfront total
and still labels `unitOfMeasure` as "1 Hour", which is wrong in their feed and would
overstate a reserved GPU-hour by four orders of magnitude if taken at face value. The
conversion here is `retailPrice / term_hours / gpu_count`, which assumes the reservation
covers the node continuously for the full term. That is how an Azure reserved instance
works. The untouched upfront figure is kept in raw_json so the derivation can be checked
against the source rather than trusted.

Sanity check against a live response for Standard_ND96isr_H100_v5 in westeurope on
2026-09-08: on-demand $127.816/hr node, or $15.98/GPU-hr; 1-year reservation $716,588
upfront, or $10.23/GPU-hr, a 36% discount; 3-year $7.01; 5-year $6.39. Those discounts
sit where Azure's published reserved-instance discounts sit, which is the check that the
upfront reading is the right one.
"""

from __future__ import annotations

import json
import logging

import requests

from tci.collectors.base import TIMEOUT_SECONDS
from tci.db import utc_now_iso
from tci.models import Observation

log = logging.getLogger("tci.collectors.azure_retail")

NAME = "azure_retail"

URL = "https://prices.azure.com/api/retail/prices"

MAX_PAGES_PER_REGION = 20  # raised from 8: adding Reservation rows roughly doubles a region

REGIONS = (
    "westeurope", "northeurope", "swedencentral", "francecentral",
    "germanywestcentral", "norwayeast", "norwaywest", "italynorth",
    "polandcentral", "spaincentral",
)

REGION_COUNTRY = {
    "westeurope": "NL", "northeurope": "IE", "swedencentral": "SE",
    "francecentral": "FR", "germanywestcentral": "DE", "norwayeast": "NO",
    "norwaywest": "NO", "italynorth": "IT", "polandcentral": "PL",
    "spaincentral": "ES",
}

# armSkuName -> (canonical gpu variant, GPUs per node). Verified present in a live
# response on 2026-08-15; do not add a SKU here without confirming its GPU count from
# Microsoft's VM-size documentation -- a guessed count corrupts the per-GPU series.
SKU_MAP: dict[str, tuple[str, int]] = {
    "Standard_ND96isr_H100_v5": ("H100_SXM", 8),
    "Standard_ND96is_noIB_H100_v5": ("H100_SXM", 8),
    "Standard_ND96is_H100_v5": ("H100_SXM", 8),
    "Standard_ND96isr_H200_v5": ("H200_SXM", 8),
    "Standard_ND96asr_A100_v4": ("A100_SXM", 8),
    "Standard_ND96amsr_A100_v4": ("A100_SXM", 8),
    "Standard_ND96ams_A100_v4": ("A100_SXM", 8),
    "Standard_NC40ads_H100_v5": ("H100_PCIE", 1),
    "Standard_NC80adis_H100_v5": ("H100_PCIE", 2),
    "Standard_NC24ads_A100_v4": ("A100_PCIE", 1),
    "Standard_NC48ads_A100_v4": ("A100_PCIE", 2),
    "Standard_NC96ads_A100_v4": ("A100_PCIE", 4),
}


# Hours a reservation covers, used to turn Azure's whole-term upfront total into a
# per-GPU-hour equivalent. A reservation holds the node continuously, so the divisor is
# calendar hours over the term. 8760 = 365 days; Azure prices the term, not the leap year.
TERM_HOURS: dict[str, int] = {
    "1 Year": 8_760,
    "3 Years": 3 * 8_760,
    "5 Years": 5 * 8_760,
}

# reservationTerm -> the value stored in observations.term.
TERM_LABEL: dict[str, str] = {
    "1 Year": "reserved_1yr",
    "3 Years": "reserved_3yr",
    "5 Years": "reserved_5yr",
}


def _odata_filter(region: str) -> str:
    # DevTestConsumption is deliberately absent: it is a discount attached to a
    # subscription type, not a price the market can transact at.
    return (
        f"serviceName eq 'Virtual Machines' and armRegionName eq '{region}'"
        " and (priceType eq 'Consumption' or priceType eq 'Reservation')"
    )


def _interconnect(sku: str) -> str:
    if "noIB" in sku:
        return "Ethernet"
    if "isr" in sku:
        return "InfiniBand"
    return "PCIe"


def _fetch_region(session: requests.Session, region: str) -> list[dict]:
    """All Items for one region, following NextPageLink up to MAX_PAGES_PER_REGION."""
    items: list[dict] = []
    resp = session.get(
        URL, params={"$filter": _odata_filter(region)}, timeout=TIMEOUT_SECONDS
    )
    resp.raise_for_status()
    payload = resp.json()
    items.extend(payload.get("Items", []))
    next_link = payload.get("NextPageLink")
    pages = 1
    while next_link and pages < MAX_PAGES_PER_REGION:
        resp = session.get(next_link, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
        payload = resp.json()
        items.extend(payload.get("Items", []))
        next_link = payload.get("NextPageLink")
        pages += 1
    if next_link:
        # The cap exists so a stuck region cannot balloon the request count, but hitting
        # it means this region was truncated and some SKUs may be missing from the panel
        # for the day. That used to happen silently. Now it is on the record, because a
        # missing constituent that nobody logged is indistinguishable from one that was
        # never offered.
        log.warning(
            "azure_retail: region %s truncated at %d pages, %d items; SKUs may be missing",
            region, MAX_PAGES_PER_REGION, len(items),
        )
    return items


def _tenor(item: dict) -> tuple[str, str, int] | None:
    """(tier, term, divisor_hours) for one price row, or None if it is not priced here.

    The divisor is 1 for anything already quoted hourly and the term length for a
    reservation, whose retailPrice is a whole-term upfront total wearing an hourly label.
    """
    meter_name = item.get("meterName") or ""
    if item.get("type") == "Reservation":
        term = TERM_LABEL.get(str(item.get("reservationTerm")))
        hours = TERM_HOURS.get(str(item.get("reservationTerm")))
        # An unrecognised term is skipped rather than guessed: without knowing the length
        # there is no divisor, and a wrong divisor is a fabricated price.
        return ("list", term, hours) if term and hours else None
    if item.get("type") != "Consumption":
        return None
    if "Spot" in meter_name:
        return ("spot", "on_demand", 1)
    if "Low Priority" in meter_name:
        return ("interruptible", "on_demand", 1)
    return ("list", "on_demand", 1)


def _to_observation(item: dict, region: str, ts: str) -> Observation | None:
    meter_name = item.get("meterName") or ""
    tenor = _tenor(item)
    if tenor is None:
        return None
    tier, term, divisor_hours = tenor
    if item.get("unitOfMeasure") != "1 Hour":
        return None
    # Azure publishes every GPU SKU twice: a base/Linux meter and a "... Windows" meter
    # carrying a bundled Windows Server licence, ~23% dearer (e.g. DE NC24ads_A100_v4 at
    # 4.7750 Linux vs 5.8790 Windows on 2026-09-04). An OS licence is not part of the
    # unit -- METHODOLOGY.md §1 prices a GPU-hour ex-VAT, excluding storage and egress --
    # so admitting both put a ~23% non-compute spread inside what looks like one cell.
    # Measured 2026-09-04: 68 of 69 (country, model, SKU) cells carried both meters.
    # The Linux meter is sometimes suffixed "Linux" and sometimes unsuffixed, so the
    # test is on the Windows form, which is always explicit.
    if "Windows" in (item.get("productName") or ""):
        return None
    sku = item.get("armSkuName") or ""
    mapped = SKU_MAP.get(sku)
    if mapped is None:
        return None
    retail_price = item.get("retailPrice")
    if retail_price is None:
        return None  # never invent a price
    gpu_model, gpu_count = mapped
    return Observation(
        ts_utc=ts,
        source=NAME,
        provider="azure",
        gpu_model=gpu_model,
        gpu_count=gpu_count,
        price_usd_per_gpu_hr=float(retail_price) / divisor_hours / gpu_count,
        region=item.get("armRegionName") or region,
        country=REGION_COUNTRY.get(region),
        interconnect=_interconnect(sku),
        tier=tier,
        term=term,
        raw_json=json.dumps(
            {
                "armSkuName": sku,
                "armRegionName": item.get("armRegionName"),
                # The figure exactly as returned, before any division. For a reservation
                # this is the whole-term upfront total, and keeping it is what lets a
                # reader check the conversion instead of taking it on trust.
                "retailPrice": retail_price,
                "unitOfMeasure": item.get("unitOfMeasure"),
                "reservationTerm": item.get("reservationTerm"),
                "divisorHours": divisor_hours,
                "meterName": meter_name,
                "productName": item.get("productName"),
                "currencyCode": item.get("currencyCode"),
                "type": item.get("type"),
            }
        ),
    )


class AzureRetailCollector:
    name = NAME

    def collect(self, session: requests.Session) -> list[Observation]:
        ts = utc_now_iso()
        out: list[Observation] = []
        for region in REGIONS:
            try:
                items = _fetch_region(session, region)
            except requests.RequestException as exc:
                log.warning("azure_retail: region %s failed (%s), skipping", region, exc)
                continue
            for item in items:
                obs = _to_observation(item, region, ts)
                if obs is not None:
                    out.append(obs)
        log.info("azure_retail: %d observations across %d regions", len(out), len(REGIONS))
        return out
