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
  subsidiary catalogue); everything else is stored with country=None, which keeps it out
  of every regional series until a methodology version says where it is deliverable.
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
            result = self.module.collect(timeout=TIMEOUT_SECONDS)
        partial = result.get("partial_errors") or []
        if partial:
            log.info("%s: %d partial errors from the recipe: %s", self.name, len(partial),
                     "; ".join(str(p) for p in partial[:5]))
        return self.to_observations(result)

    def to_observations(self, result: dict[str, Any]) -> list[Observation]:
        ts = utc_now_iso()
        out: list[Observation] = []
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
            for region, country in placements:
                out.append(
                    Observation(
                        ts_utc=ts,
                        source=self.name,
                        provider=self.provider,
                        gpu_model=variant,
                        gpu_count=gpu_count,
                        # Native amount, as scaleway.py does: for EUR rows normalise.py
                        # converts at print time from raw_json, never at collection.
                        price_usd_per_gpu_hr=native,
                        region=region,
                        country=country,
                        interconnect=None,
                        tier=tier,
                        term=term,
                        raw_json=json.dumps(raw, default=str),
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
        ComputableSource("coreweave", "coreweave", "coreweave"),
        ComputableSource("voltagepark", "voltagepark", "voltagepark",
                         country_of=_voltagepark_country, gpu_count_of=_voltagepark_count),
        ComputableSource("digitalocean", "digitalocean", "digitalocean",
                         variant_override=_digitalocean_variant,
                         regions_of=_digitalocean_regions),
        ComputableSource("latitude", "latitude", "latitude", country_of=_latitude_country),
        ComputableSource("hyperstack", "hyperstack", "hyperstack"),
        ComputableSource("crusoe", "crusoe", "crusoe"),
        ComputableSource("lambda_pricing", "lambda_", "lambdalabs"),
    ]
