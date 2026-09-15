# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""List and spot prices from dstack's published gpuhunt catalogs.

Scope: the catalogs gpuhunt ships offline — aws, azure, gcp, oci, lambdalabs, verda and
nebius — for the H100, H200, B200, B300 and A100 classes, both on-demand and spot rows.
Catalog item price is per instance-hour -> divide by the provider's own GPU count.
Regions are mapped to countries below; unmapped regions yield country=None and are
excluded by the normaliser — the safe default for new/unknown regions.

Spot rows are stored as `tier="spot"`, `term="on_demand"` — spot is a pricing tier, not
a commitment tenor, the same reading azure_retail.py already gives its own Spot meter.
`normalise.py` admits only `tier in (executable, list)`, so a spot row is structurally
incapable of reaching a print; it accumulates as audit data the way Azure's and vast.ai's
spot/bid rows already do. Until 2026-09-15 this collector queried `spot=False` and
discarded any spot row that slipped through anyway; gpuhunt's own `Catalog.query` treats
`spot=None` (the default) as "return both", confirmed 2026-09-15 by reading the installed
package's source (`gpuhunt/_internal/catalog.py`) and by a live query returning 591 spot
rows alongside 999 on-demand ones from the same call — one request either way, so this
widening adds no catalog fetch. Every row shares one `CatalogItem` shape regardless of
`spot`, so the same `variant_of()`, `collection_floor()` and `REGION_COUNTRY` logic applies
to both; no spot-specific mapping was needed.

Which of these rows can reach a print is decided by the methodology's panel, not here.
Until 2026-09-11 only aws/azure/gcp H100 rows were collected; the other four providers
and four classes were added so their history starts accumulating before any version
admits them (see config/notices.yaml).

A caveat recorded rather than hidden: gpuhunt's own upstream provider code (not this
file) fabricates the catalog for three of these four providers as every instance type
times every location it can enumerate, with no per-location availability signal at all.
Verified by reading gpuhunt's installed source, 2026-09-12:
  - verda: `itertools.product(spots, location_codes, instance_types)` -- every instance
    crossed with every one of Verda's own listed locations.
  - lambdalabs: `add_regions()` crosses every instance type with every region returned by
    Lambda's images API, with a `# TODO: we don't know which regions are actually
    available for each instance type` admission in the source.
  - oci: `_duplicate_item_in_regions()` crosses every bare-metal shape with every OCI
    commercial region worldwide, regardless of where that GPU shape is actually racked.
  - nebius is the one exception: its provider queries `list_platforms()` per region
    through Nebius's own billing/compute API, so a (platform, region) pair here reflects
    what Nebius's control plane actually reports for that region, not an enumeration.
So a Lambda or OCI or Verda row's `location` is where the provider operates at all, not
proof that specific instance type is stocked or even sold there -- for Lambda and OCI
this is the vendor's own default assumption in the absence of data, not a documented
availability fact. A Nebius row's location is closer to a real claim.

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

PROVIDERS = ("aws", "azure", "gcp", "oci", "lambdalabs", "verda", "nebius")
GPU_NAMES = ["H100", "H200", "B200", "B300", "A100"]

# The node-size floor at collection. Until v0.4.0 this collector discarded every instance
# below 8 GPUs, while the published methodology floor had been 2 since v0.3.0: the
# collection was narrower than the method (notice 2026-N1). The providers that were
# already constituents keep the old floor until that notice takes effect, so rows that
# 0.3.0-dev never saw cannot reach a print computed under it.
MIN_GPU_COUNT = 2
LEGACY_FLOOR = 8
LEGACY_FLOOR_PROVIDERS = frozenset({"aws", "azure", "gcp"})
LEGACY_FLOOR_UNTIL = "2026-09-15"  # first collection day at the published floor

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
    # oci (region-flat prices; gpuhunt lists every region a shape is sold in)
    "eu-frankfurt-1": "DE", "eu-amsterdam-1": "NL", "eu-stockholm-1": "SE",
    "eu-paris-1": "FR", "eu-marseille-1": "FR", "eu-milan-1": "IT", "eu-madrid-1": "ES",
    # lambdalabs (docs.lambda.ai, checked 2026-09-11: europe-central-1 is Germany)
    "europe-central-1": "DE",
    # verda (FIN-* Finland, ICE-* Iceland; both EEA)
    "fin-01": "FI", "fin-02": "FI", "fin-03": "FI", "ice-01": "IS",
    # nebius (docs.nebius.com, checked 2026-09-11). eu-north2 is a private region in
    # Iceland; its rows are kept, whether they are sold on-demand is the panel's question.
    "eu-north1": "FI", "eu-west1": "FR", "eu-north2": "IS", "eu-west2": "FR",
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
    "us-ashburn-1": "US", "us-phoenix-1": "US", "us-chicago-1": "US", "us-sanjose-1": "US",
    "us-east-3": "US", "us-midwest-1": "US", "us-south-1": "US", "us-south-2": "US",
    "us-south-3": "US", "us-west-3": "US", "us-north1": "US",
    "us-east-1": "US", "us-east-2": "US", "us-west-1": "US", "us-west-2": "US",
    "eastus": "US", "eastus2": "US", "westus3": "US", "southcentralus": "US",
    "us-east4": "US", "us-east5": "US", "us-central1": "US", "us-west1": "US",
    "us-west4": "US",

    # -- UK ----------------------------------------------------------------------
    "uk-london-1": "GB", "uk-cardiff-1": "GB", "uk-south1": "GB", "uk-south2": "GB",
    "eu-west-2": "GB", "uksouth": "GB", "ukwest": "GB", "europe-west2": "GB",

    # -- Switzerland -------------------------------------------------------------
    "eu-zurich-1": "CH", "eu-central-2": "CH", "switzerlandnorth": "CH", "switzerlandwest": "CH",
    "europe-west6": "CH",

    # -- Canada ------------------------------------------------------------------
    "ca-central-1": "CA", "ca-west-1": "CA", "canadacentral": "CA", "canadaeast": "CA",
    "northamerica-northeast1": "CA", "northamerica-northeast2": "CA",

    # -- Latin America -----------------------------------------------------------
    "sa-east-1": "BR", "brazilsouth": "BR", "southamerica-east1": "BR",
    "southamerica-west1": "CL",

    # -- Asia-Pacific, north-east ------------------------------------------------
    "ap-tokyo-1": "JP", "ap-osaka-1": "JP", "asia-northeast-2": "JP",
    "ap-northeast-1": "JP", "ap-northeast-3": "JP", "japaneast": "JP", "japanwest": "JP",
    "asia-northeast1": "JP", "asia-northeast2": "JP", "asia-northeast-1": "JP",
    "asia-south-1": "IN",
    "ap-northeast-2": "KR", "koreacentral": "KR", "koreasouth": "KR",
    "asia-northeast3": "KR",
    "ap-east-1": "HK", "eastasia": "HK", "asia-east2": "HK",
    "asia-east1": "TW",

    # -- Asia-Pacific, south-east ------------------------------------------------
    "ap-singapore-1": "SG", "ap-southeast-1": "SG", "southeastasia": "SG", "asia-southeast1": "SG",
    "ap-southeast-3": "ID", "asia-southeast2": "ID",

    # -- South Asia --------------------------------------------------------------
    "ap-mumbai-1": "IN", "ap-hyderabad-1": "IN",
    "ap-south-1": "IN", "ap-south-2": "IN", "centralindia": "IN", "southindia": "IN",
    "westindia": "IN", "asia-south1": "IN", "asia-south2": "IN",

    # -- Oceania -----------------------------------------------------------------
    "ap-sydney-1": "AU", "ap-melbourne-1": "AU", "australia-east-1": "AU",
    "ap-southeast-2": "AU", "ap-southeast-4": "AU", "australiaeast": "AU",
    "australiasoutheast": "AU", "australia-southeast1": "AU", "australia-southeast2": "AU",

    # -- Middle East and Africa --------------------------------------------------
    "il-central-1": "IL", "israelcentral": "IL", "me-west1": "IL", "me-west-1": "IL",
    "il-jerusalem-1": "IL", "me-dubai-1": "AE", "me-abudhabi-1": "AE",
    "af-johannesburg-1": "ZA", "sa-saopaulo-1": "BR", "sa-bogota-1": "CO",
    "ca-toronto-1": "CA", "ca-montreal-1": "CA",
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


def variant_of(provider: str, instance_name: str, gpu_name: str,
               gpu_memory: float | None, gpu_count: int) -> str | None:
    """Canonical variant for a catalog row, or None when it cannot be pinned.

    gpuhunt reports a chip family ("H100") and a memory size, not a form factor, so the
    form factor is read from the provider's own instance name. A row that no rule below
    identifies is skipped and logged, never guessed: an H100 NVL or PCIe card recorded as
    SXM would be a different product priced as the reference unit.
    """
    name = instance_name.lower()
    mem = float(gpu_memory or 0)
    family = gpu_name.upper()
    if family == "A100":
        if mem and mem < 60:
            return "A100_SXM_40GB" if "pcie" not in name else "A100_PCIE_40GB"
        return "A100_PCIE" if "pcie" in name else "A100_SXM"
    if family == "H100":
        if 90 <= mem <= 100 or "nvl" in name:
            return "H100_NVL_94GB"
        if "pcie" in name:
            return "H100_PCIE"
        if "sxm" in name:
            return "H100_SXM"
        # Platforms whose H100 instances are HGX/SXM by construction.
        if provider == "aws" and name.startswith("p5"):
            return "H100_SXM"
        if provider == "gcp" and name.startswith("a3-"):
            return "H100_SXM"
        if provider == "azure" and name.lower().startswith(("standard_nd", "nd")):
            return "H100_SXM"
        if provider == "oci" and name.startswith("bm.gpu.h100"):
            return "H100_SXM"
        if provider == "verda":
            return "H100_SXM"  # gpuhunt maps only 'H100 SXM5 80GB' descriptions to H100
        return None
    if family == "H200":
        if "nvl" in name or "pcie" in name:
            return "H200_NVL"
        return "H200_SXM"
    if family == "B200":
        return "B200_SXM"
    if family == "B300":
        return "B300_SXM"
    return None


def collection_floor(provider: str, utc_date: str) -> int:
    if provider in LEGACY_FLOOR_PROVIDERS and utc_date < LEGACY_FLOOR_UNTIL:
        return LEGACY_FLOOR
    return MIN_GPU_COUNT


class GpuHuntCollector:
    name = "gpuhunt"

    def collect(self, session: requests.Session) -> list[Observation]:
        # session unused: gpuhunt fetches dstack's published catalog files itself
        import gpuhunt

        # spot omitted (None): gpuhunt's Catalog.query returns both spot and on-demand
        # rows from the catalog it has already loaded, so this is still one request.
        items = gpuhunt.query(gpu_name=GPU_NAMES, provider=list(PROVIDERS))
        return self.to_observations(items)

    def to_observations(self, items: list, utc_date: str | None = None) -> list[Observation]:
        ts = utc_now_iso()
        day = utc_date or ts[:10]
        pkg_version = _gpuhunt_version()
        out: list[Observation] = []
        unpinned: set[str] = set()
        for item in items:
            gpu_count = int(item.gpu_count or 0)
            if gpu_count < collection_floor(item.provider, day):
                continue
            tier = "spot" if getattr(item, "spot", False) else "list"
            variant = variant_of(
                item.provider, item.instance_name or "", item.gpu_name or "",
                getattr(item, "gpu_memory", None), gpu_count,
            )
            if variant is None:
                unpinned.add(f"{item.provider}:{item.instance_name}")
                continue
            country = _country(item.provider, item.location or "")
            out.append(
                Observation(
                    ts_utc=ts,
                    source=self.name,
                    provider=item.provider,
                    gpu_model=variant,
                    gpu_count=gpu_count,
                    price_usd_per_gpu_hr=float(item.price) / gpu_count,
                    region=item.location,
                    country=country,
                    interconnect="NVLink" if variant.endswith("_SXM") else "PCIe",
                    tier=tier,
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
        if unpinned:
            log.info("gpuhunt: %d rows with no pinnable form factor, skipped: %s",
                     len(unpinned), ", ".join(sorted(unpinned)[:20]))
        log.info("gpuhunt: %d rows across %s", len(out), ", ".join(PROVIDERS))
        return out
