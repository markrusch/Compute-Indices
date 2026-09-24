# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Adapters from Computable's vendored collector recipes to TCI observations.

The recipes (tci/vendor/computable/sources/) fetch and parse a provider's public price
surface and return Computable's observation dicts. This module turns each dict into a
TCI Observation, and it is where every judgement TCI makes about those rows lives:

- VARIANT. Computable's catalog names a chip family ("H100") and splits out the PCIe and
  NVL form factors. TCI's reference variants need the form factor, so a generic "H100"
  is read as SXM only when the provider's own label says SXM or HGX; otherwise it is
  stored as H100_UNSPEC, which no class admits. A PCIe card priced as the SXM reference
  unit would be a different product under the headline's name.
- COUNTRY. Most of these surfaces publish one price for every region. A country is
  assigned only where the surface itself names one (Latitude's location groups, OVH's
  subsidiary catalogue) or, for CoreWeave specifically, a continent label mapped to one
  representative country on Mark's explicit instruction (2026-09-12) -- see
  `_coreweave_country` for what that approximation does and does not claim. Everything
  else is stored with country=None, which keeps it out of every regional series until a
  methodology version says where it is deliverable.
- TIER AND TERM. On-demand rows are list prices (tier 'list', term 'on_demand'); spot rows
  are tier 'spot'; committed rows carry their tenor in `term`. A committed price whose
  tenor is a range or a floor ("2 weeks – 1 year", "starting from") is stored as
  'reserved_unspecified', never assigned a tenor it does not have.

None of these rows reaches a print unless the methodology's panel names the provider,
this collector, and the class.
"""

from __future__ import annotations

import importlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

import requests

from tci import USER_AGENT
from tci.db import utc_now_iso
from tci.models import Observation
from tci.vendor.computable.catalog import load_sku_catalog, match_sku
from tci.vendor.computable.http import user_agent_scope
from tci.vendor.computable.observation import result as result_

log = logging.getLogger("tci.collectors.computable")

TIMEOUT_SECONDS = 30.0

_CATALOG = load_sku_catalog()

# Commitment length in months -> TCI term.
TERM_BY_MONTHS = {1: "commit_1mo", 3: "commit_3mo", 6: "commit_6mo", 12: "reserved_1yr",
                  24: "reserved_2yr", 36: "reserved_3yr", 60: "reserved_5yr"}


def sku_of(sku_identifier: str) -> str | None:
    entry = match_sku(_CATALOG, sku_identifier)
    return str(entry["sku"]) if entry else None


def variant_of(sku: str | None, label: str) -> str | None:
    """TCI variant for a Computable sku plus the provider's label (see module docstring)."""
    if sku is None:
        return None
    text = label.upper()
    explicit_sxm = "SXM" in text or "HGX" in text
    if sku == "H100_PCIE":
        return "H100_PCIE"
    if sku == "H100_NVL":
        return "H100_NVL_94GB"
    if sku == "H100":
        return "H100_SXM" if explicit_sxm else "H100_UNSPEC"
    if sku == "H200_NVL":
        return "H200_NVL"
    if sku == "H200":
        return "H200_SXM" if explicit_sxm else "H200_UNSPEC"
    if sku == "B200":
        return "B200_SXM"  # B200 ships only as HGX/SXM boards
    if sku == "B300":
        return "B300_SXM"  # likewise
    if sku == "A100":
        if "40GB" in text.replace(" ", ""):
            return "A100_40GB"
        if "PCIE" in text:
            return "A100_PCIE"
        return "A100_SXM" if "SXM" in text else "A100_UNSPEC"
    return sku  # a non-reference part (L40S, GB200, ...): stored under its catalog name


def term_of(obs: dict[str, Any]) -> tuple[str, str] | None:
    """(tier, term) for a Computable observation, or None to skip it."""
    tier = obs.get("tier")
    extra = obs.get("extra") or {}
    if tier == "on-demand":
        return "list", "on_demand"
    if tier in ("spot", "preemptible"):
        return "spot", "on_demand"
    if tier == "monthly-commit":
        return "list", "commit_1mo"
    if tier in ("reserved", "committed"):
        months = extra.get("commitment_months")
        if isinstance(months, int) and months in TERM_BY_MONTHS:
            return "list", TERM_BY_MONTHS[months]
        return "list", "reserved_unspecified"
    return None  # serverless, from-floor, instant-cluster: not a GPU-hour rental rate


@dataclass(frozen=True)
class Placement:
    """One extra row for a region-flat price: where it is deliverable, on what node."""

    region: str
    country: str
    gpu_count: int
    evidence: dict[str, Any]


@dataclass
class ComputableSource:
    """One vendored recipe exposed as a TCI collector."""

    name: str
    module_name: str
    provider: str
    country_of: Callable[[dict[str, Any]], str | None] = lambda obs: None
    variant_override: Callable[[dict[str, Any], str | None], str | None] | None = None
    gpu_count_of: Callable[[dict[str, Any]], int | None] | None = None
    # For a region-flat price sold in several named regions: one row per region, each
    # with its country. Returns [(region, country)]; None keeps the single row.
    regions_of: Callable[[str], list[tuple[str, str]] | None] | None = None
    currencies: tuple[str, ...] = ("USD", "EUR")
    # Bypasses `self.module.collect()` for a source whose recipe deliberately covers
    # less of the page than TCI wants, and TCI's own reading needs the raw body the
    # recipe never surfaces. Takes the collector's timeout, returns a Computable
    # result dict (same shape `self.module.collect()` returns) built from exactly one
    # fetch. See `_fetch_latitude` for the one current use.
    fetch_override: Callable[[float], dict[str, Any]] | None = None
    # For a region-flat price whose seller also publishes where it has stock: extra rows,
    # one per datacentre showing a deployable VM of that model today. Takes the recipe's
    # whole result (the stock lives beside the prices, not on them) and returns a function
    # from (variant, label) to placements. See `_hyperstack_placements`.
    stock_placements: Callable[
        [dict[str, Any]], Callable[[str, str], list[Placement]]
    ] | None = None
    _module: ModuleType | None = field(default=None, repr=False)

    @property
    def module(self) -> ModuleType:
        if self._module is None:
            self._module = importlib.import_module(
                f"tci.vendor.computable.sources.{self.module_name}"
            )
        return self._module

    def collect(self, session: requests.Session) -> list[Observation]:
        # session unused: the recipe fetches through its own hardened transport. TCI's
        # User-Agent replaces Computable's for every request it makes.
        with user_agent_scope(USER_AGENT):
            result = (
                self.fetch_override(TIMEOUT_SECONDS) if self.fetch_override
                else self.module.collect(timeout=TIMEOUT_SECONDS)
            )
        partial = result.get("partial_errors") or []
        if partial:
            log.info("%s: %d partial errors from the recipe: %s", self.name, len(partial),
                     "; ".join(str(p) for p in partial[:5]))
        return self.to_observations(result)

    def to_observations(self, result: dict[str, Any]) -> list[Observation]:
        ts = utc_now_iso()
        out: list[Observation] = []
        placed_by_stock = self.stock_placements(result) if self.stock_placements else None
        for obs in result.get("observations") or []:
            currency = str(obs.get("currency") or "")
            if currency not in self.currencies:
                continue  # e.g. Latitude's BRL column: the same price in another display currency
            mapped = term_of(obs)
            if mapped is None:
                continue
            tier, term = mapped
            label = str(obs.get("sku_identifier") or "")
            sku = sku_of(label)
            variant = (
                self.variant_override(obs, sku) if self.variant_override else None
            ) or variant_of(sku, label)
            if variant is None:
                continue
            gpu_count = (
                self.gpu_count_of(obs) if self.gpu_count_of else None
            ) or int(obs.get("gpu_count_basis") or 1)
            native = float(obs["price_native_per_gpu_hr"])
            raw = {
                "sku_identifier": label,
                "computable_sku": sku,
                "raw_value": obs.get("raw_value"),
                "raw_unit": obs.get("raw_unit"),
                "gpu_count_basis": obs.get("gpu_count_basis"),
                "computable_tier": obs.get("tier"),
                "region_as_published": obs.get("region"),
                "notes": obs.get("notes"),
                "extra": obs.get("extra"),
                "currency": currency,
                "price_native_per_gpu_hr": native,
                "recipe": f"computable/{self.module_name}",
            }
            regional = self.regions_of(variant) if self.regions_of else None
            placements: list[tuple[str | None, str | None]] = (
                [(r, c) for r, c in regional]
                if regional
                else [(str(obs.get("region") or "") or None, self.country_of(obs))]
            )
            rows: list[tuple[str | None, str | None, int, dict[str, Any]]] = [
                (region, country, gpu_count, raw) for region, country in placements
            ]
            if placed_by_stock and tier == "list" and term == "on_demand":
                rows += [
                    (pl.region, pl.country, pl.gpu_count, {**raw, "stock": pl.evidence})
                    for pl in placed_by_stock(variant, label)
                ]
            for region, country, count, raw_row in rows:
                out.append(
                    Observation(
                        ts_utc=ts,
                        source=self.name,
                        provider=self.provider,
                        gpu_model=variant,
                        gpu_count=count,
                        # Native amount, as scaleway.py does: for EUR rows normalise.py
                        # converts at print time from raw_json, never at collection.
                        price_usd_per_gpu_hr=native,
                        region=region,
                        country=country,
                        interconnect=None,
                        tier=tier,
                        term=term,
                        raw_json=json.dumps(raw_row, default=str),
                    )
                )
        log.info("%s: %d rows", self.name, len(out))
        return out


# ------------------------------------------------------------------ per-source rules

# Latitude publishes location groups by country name.
_LATITUDE_COUNTRY = {
    "United States": "US", "Netherlands": "NL", "Germany": "DE", "United Kingdom": "GB",
    "Australia": "AU", "Japan": "JP", "Brazil": "BR", "Mexico": "MX", "Chile": "CL",
    "Argentina": "AR", "Colombia": "CO", "Singapore": "SG",
}


def _latitude_country(obs: dict[str, Any]) -> str | None:
    return _LATITUDE_COUNTRY.get(str(obs.get("region") or ""))


def _fetch_latitude(timeout: float) -> dict[str, Any]:
    """One fetch of latitude.sh/pricing, read twice: once by the unmodified vendored
    recipe (hour + month), once by TCI's own reading of the same body for the year field
    it discards (roadmap L5.3; see `tci.collectors.latitude_annual`).

    A failure in the annual reading is caught here and downgraded to a partial_error: it
    is TCI's own code, reading a field the vendored recipe's own contract does not cover,
    and a bug or reshape in it must not cost the day's ordinary hour/month rows, which the
    vendored parser has already produced successfully by the time this runs.

    Fetches through `latitude_module.fetch` — the vendored module's OWN name for the
    shared transport, not a second import of it — so that patching `latitude.fetch` (what
    every test in this suite already does to stay offline) covers this path too. A
    separately-imported `fetch` reference here would be invisible to that patch and would
    reach the live network on every test run: found live, by watching this exact call
    reach latitude.sh during `pytest`.
    """
    from tci.collectors.latitude_annual import parse_prepaid_annual
    from tci.vendor.computable.sources import latitude as latitude_module

    html = latitude_module.fetch(latitude_module.URL, timeout=timeout)
    rows, partial_errors = latitude_module.parse_latitude(html)
    try:
        annual_rows, annual_errors = parse_prepaid_annual(html)
    except Exception as exc:  # noqa: BLE001 — TCI's own addition must never sink the source
        log.warning("latitude: annual-price reading failed (%s: %s); hour/month unaffected",
                   type(exc).__name__, exc)
        annual_rows, annual_errors = [], [f"annual price reading failed: {exc}"]
    rows = rows + annual_rows
    partial_errors = list(partial_errors) + annual_errors
    return result_(
        latitude_module.SOURCE_ID,
        method="html-regex",
        url=latitude_module.URL,
        observations=rows,
        partial_errors=partial_errors or None,
    )


def _ovh_country(obs: dict[str, Any]) -> str | None:
    # OVHcloud prices by subsidiary catalogue, not by region. The FR catalogue is the one
    # its French public-cloud regions bill against; OVH states Gravelines (France) carries
    # the widest GPU range (ovhcloud.com/en/public-cloud/prices, read 2026-09-11). The US
    # catalogue bills its US regions. Convention recorded, not a per-region observation.
    region = str(obs.get("region") or "")
    if region.startswith("FR"):
        return "FR"
    if region.startswith("US"):
        return "US"
    return None


def _ovh_variant(obs: dict[str, Any], sku: str | None) -> str | None:
    # OVHcloud's H100 instances are "80 GB HBM2e - PCIe 5.0"; its H200 instances are
    # "141 GB HBM3 - NVLink" 8-GPU nodes (ovhcloud.com/en/public-cloud/gpu, read
    # 2026-09-11). The catalogue labels both only "H100"/"H200".
    if sku == "H100":
        return "H100_PCIE"
    if sku == "H200":
        return "H200_SXM"
    return None


def _digitalocean_variant(obs: dict[str, Any], sku: str | None) -> str | None:
    # The pricing page says only "NVIDIA H100". DigitalOcean's own announcement of the
    # Amsterdam launch describes the part as "NVIDIA HGX H100" (digitalocean.com/blog/
    # now-available-gpu-droplets-nvidia-h100s-ams, 2025-10-07; read 2026-09-11). HGX
    # boards carry SXM5 modules, so H100 rows are the SXM reference variant. H200 has no
    # such statement and stays H200_UNSPEC.
    if sku == "H100":
        return "H100_SXM"
    return None


# Where DigitalOcean sells each GPU plan, from docs.digitalocean.com/products/droplets/
# details/gpu-availability (read 2026-09-11): "NVIDIA H100 GPU Droplets are available in
# New York (NYC2), Amsterdam (AMS3), and Toronto (TOR1)." The price is the same in each,
# so the collector records one row per region. Re-check at each source review: a region
# added or withdrawn here changes where the price is deliverable.
DIGITALOCEAN_REGIONS: dict[str, list[tuple[str, str]]] = {
    "H100_SXM": [("AMS3", "NL"), ("NYC2", "US"), ("TOR1", "CA")],
}


def _digitalocean_regions(variant: str) -> list[tuple[str, str]] | None:
    return DIGITALOCEAN_REGIONS.get(variant)


# CoreWeave's pricing page states only two labels, "NORTH AMERICA" and "EUROPE" -- a
# continent, not a country, on either side. Mapped to a single representative country per
# side ONLY because Mark, who sets this project's population definitions, decided the
# approximation is acceptable for TCI's EU-vs-US framing and asked for it directly
# (2026-09-12): "EU is Europe, so that is safe" for the Europe side, with an explicit
# instruction to record that the US is not technically North America.
#
# What "EUROPE" actually covers, checked live 2026-09-12 (CoreWeave's own announcements):
# UK (London, two sites, CoreWeave's EU HQ), Sweden (Stockholm, with Conapto), Norway
# (Kristiansand), and Spain (announced). The UK is NOT in `eu_eea_countries` -- it left
# the EU and was never EEA -- so "EUROPE" is not a synonym for "EU/EEA" here: an unknown
# share of the rows this label covers may be UK capacity that the methodology's EU/EEA
# population is defined to exclude. Norway is used as the representative code because it
# is a confirmed CoreWeave location and genuinely is EEA (v0.4.0), not because any given
# priced row is known to sit there -- it is a block marker, not a location claim, exactly
# like OVH's subsidiary-catalogue convention below.
#
# What "NORTH AMERICA" actually covers: CoreWeave does not state US vs Canada, and "US" is
# used as the representative code (Mark's instruction) though the label also covers any
# Canadian capacity CoreWeave has, which this cannot distinguish from US capacity.
#
# CoreWeave is not on the panel (SOURCES.md: shadow). This mapping lets its rows carry a
# population-eligible country while they accumulate history; it moves no print, since
# admission to a class still requires a governed methodology version.
_COREWEAVE_COUNTRY = {"EUROPE": "NO", "NORTH AMERICA": "US"}


def _coreweave_country(obs: dict[str, Any]) -> str | None:
    return _COREWEAVE_COUNTRY.get(str(obs.get("region") or ""))


# Hyperstack publishes one price list for every region and, separately, a public stock
# feed per datacentre (the vendored recipe reads both; see its module docstring). The two
# use different names for the same flavour: marketing labels on the price page, flavour
# families in the feed. The feed is also the only first-hand statement of form factor:
# of its three H100 families, two are PCIe ("H100-80G-PCIe", "H100-80G-PCIe-NVLink") and
# one is SXM5, matching the page's three H100 labels "NVIDIA H100", "NVIDIA H100 NVLink"
# and "NVIDIA H100 SXM" one for one. The A100 labels line up the same way. Until 24
# September 2026 the unqualified labels were stored as H100_UNSPEC and A100_UNSPEC,
# which no class admits. The NVLink-bridged PCIe cards stay out of every class: a bridge
# is not the reference PCIe product and nobody has measured what it is worth.
#
# label -> (TCI variant, stock-feed model). Checked against the captured feed and page in
# tests/fixtures/computable/hyperstack/ (live, 2026-08-22 and 2026-08-25).
HYPERSTACK_FLAVOURS: dict[str, tuple[str, str]] = {
    "NVIDIA H100": ("H100_PCIE", "H100-80G-PCIe"),
    "NVIDIA H100 NVLink": ("H100_PCIE_NVLINK", "H100-80G-PCIe-NVLink"),
    "NVIDIA H100 SXM": ("H100_SXM", "H100-80G-SXM5"),
    "NVIDIA H200 SXM": ("H200_SXM", "H200-141G-SXM5"),
    "NVIDIA B200": ("B200_SXM", "B200-SXM"),
    "NVIDIA B300": ("B300_SXM", "B300-SXM"),
    "NVIDIA A100": ("A100_PCIE", "A100-80G-PCIe"),
    "NVIDIA A100 NVLink": ("A100_PCIE_NVLINK", "A100-80G-PCIe-NVLink"),
    "NVIDIA A100 SXM": ("A100_SXM", "A100-80G-SXM4"),
}

# The feed's region names are the country. A region not listed here places nothing
# until someone has read where it is.
HYPERSTACK_REGION_COUNTRY = {"NORWAY-1": "NO", "CANADA-1": "CA", "US-1": "US"}


def _hyperstack_variant(obs: dict[str, Any], sku: str | None) -> str | None:
    flavour = HYPERSTACK_FLAVOURS.get(str(obs.get("sku_identifier") or ""))
    return flavour[0] if flavour else None


def _largest_deployable(configurations: dict[str, Any]) -> int | None:
    """The biggest node the feed says can be deployed right now, or None.

    `configurations` counts deployable VMs per size ("1x".."10x"). An explicit count
    above zero is the only evidence of stock: the feed's `available` field is a
    saturating floor string, and a model missing from a region is not a zero.
    """
    sizes = []
    for size, vms in configurations.items():
        count = size[:-1] if size.endswith("x") else ""
        if count.isdigit() and isinstance(vms, int) and vms > 0:
            sizes.append(int(count))
    return max(sizes) if sizes else None


def _hyperstack_placements(result: dict[str, Any]) -> Callable[[str, str], list[Placement]]:
    """Extra rows for Hyperstack's flat price, one per datacentre with stock today.

    The same rule the index already applies to RunPod's flat price in the United States
    (notice 2026-N3): a row is recorded in a country only on a day the seller's own
    stock feed shows that model deployable there, at the largest node it can deploy.
    Hyperstack prices every size at one rate per GPU (the page's spec columns are
    per-GPU shares), so the node size changes the weight, not the price.
    """
    stock = ((result.get("book_stats") or {}).get("gpu_stock") or {})
    regions: dict[str, Any] = stock.get("regions") or {}
    fetched_at = stock.get("worker_fetched_at")

    def place(variant: str, label: str) -> list[Placement]:
        flavour = HYPERSTACK_FLAVOURS.get(label)
        if flavour is None:
            return []
        out = []
        for region, models in sorted(regions.items()):
            country = HYPERSTACK_REGION_COUNTRY.get(region)
            if country is None:
                continue
            for m in models:
                if m.get("model") != flavour[1]:
                    continue
                size = _largest_deployable(m.get("configurations") or {})
                if size is not None:
                    out.append(Placement(region, country, size, {
                        "model": m["model"], "available": m.get("available"),
                        "configurations": m.get("configurations"),
                        "worker_fetched_at": fetched_at,
                    }))
        return out

    return place


def _voltagepark_country(obs: dict[str, Any]) -> str | None:
    # "Voltage Park owns high-performance GPU clusters in Texas, Virginia, Washington, and
    # Utah." (voltagepark.com/neocloud, read 2026-09-11). Its location API returns opaque
    # ids, so the country is the provider's own statement that it operates only in the US.
    return "US"


def _voltagepark_count(obs: dict[str, Any]) -> int | None:
    # Priced per GPU but sold as 8-GPU nodes ("(8x per node)" in the recipe's notes).
    return 8 if "8x per node" in str(obs.get("notes") or "") else None


def computable_collectors() -> list[ComputableSource]:
    return [
        ComputableSource("ovh", "ovh", "ovhcloud", country_of=_ovh_country,
                         variant_override=_ovh_variant),
        ComputableSource("civo", "civo", "civo"),
        ComputableSource("coreweave", "coreweave", "coreweave",
                         country_of=_coreweave_country),
        ComputableSource("voltagepark", "voltagepark", "voltagepark",
                         country_of=_voltagepark_country, gpu_count_of=_voltagepark_count),
        ComputableSource("digitalocean", "digitalocean", "digitalocean",
                         variant_override=_digitalocean_variant,
                         regions_of=_digitalocean_regions),
        ComputableSource("latitude", "latitude", "latitude", country_of=_latitude_country,
                         fetch_override=_fetch_latitude),
        ComputableSource("hyperstack", "hyperstack", "hyperstack",
                         variant_override=_hyperstack_variant,
                         stock_placements=_hyperstack_placements),
        ComputableSource("crusoe", "crusoe", "crusoe"),
        ComputableSource("lambda_pricing", "lambda_", "lambdalabs"),
    ]
