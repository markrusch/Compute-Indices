# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Vast.ai public search API (no auth) — executable marketplace asks.

Collection scope (documented in SOURCES.md): datacenter-verified hosts only
(verification == 'verified' AND hosting_type == 1); community/hobbyist inventory is
outside the index universe entirely. Offers are stored globally — EU/EEA, minimum
GPU count, and price-band filters are applied in the calculation path (normalise.py)
so per-day exclusions stay auditable; non-EU rows feed the future US-proxy series.

Field semantics verified against a live response on 2026-07-18:
dph_total is the whole-instance $/hr (divide by num_gpus); geolocation is
'Region, CC'; hosting_type 1 = datacenter.
"""

from __future__ import annotations

import json
import logging

import requests

from tci.collectors.base import TIMEOUT_SECONDS
from tci.db import utc_now_iso
from tci.models import Observation

log = logging.getLogger("tci.collectors.vast_ai")

URL = "https://console.vast.ai/api/v0/bundles/"

# No gpu_name filter. The endpoint returns a bounded universe (~60-130 offers), and
# fetching it whole means a GPU model this collector does not yet map still shows up in
# the logs instead of being invisible server-side. New silicon reaches this marketplace
# before it reaches a rate card, and the first place that should be noticed is here.
QUERY = {
    "rentable": {"eq": True},
    "type": "on-demand",
    "limit": 1000,
    "order": [["dph_total", "asc"]],
}

# vast.ai gpu_name -> (canonical variant, intra-node bus).
#
# WHAT IS DELIBERATELY ABSENT: "B200" and "H200" are offered here and are NOT mapped.
# `B200_SXM` and `H200_SXM` are the reference variants of published classes in
# factors.yaml, so mapping them would add a constituent to a published series, which is a
# methodology change under GOVERNANCE.md §1 and needs a version bump and one
# publication's notice. It is announced in config/notices.yaml rather than slipped in
# through a collector edit.
#
# Everything below maps to a variant that is NOT a configured reference variant, so
# normalise.py drops it at `variants.get(gpu_model)` and no published print can move.
# The rows accumulate against the day a consumer/workstation class is defined.
GPU_MODEL_MAP = {
    # Already mapped, already constituents. Unchanged.
    "H100 SXM": ("H100_SXM", "NVLink"),
    "H100 NVL": ("H100_NVL_94GB", "NVL"),
    "A100 SXM4": ("A100_SXM", "NVLink"),
    # Added 2026-09-08. None is a configured reference variant.
    "A100 PCIE": ("A100_PCIE", "PCIe"),
    "L40S": ("L40S", "PCIe"),
    "L40": ("L40", "PCIe"),
    "RTX 6000Ada": ("RTX_6000_ADA", "PCIe"),
    "RTX PRO 6000 WS": ("RTX_PRO_6000_WS", "PCIe"),
    "RTX 5090": ("RTX_5090", "PCIe"),
    "RTX 4090": ("RTX_4090", "PCIe"),
    "RTX 4080": ("RTX_4080", "PCIe"),
    "RTX 3090": ("RTX_3090", "PCIe"),
    "RTX 3080": ("RTX_3080", "PCIe"),
    "RTX 3060": ("RTX_3060", "PCIe"),
    "A6000": ("A6000", "PCIe"),
    "A40": ("A40", "PCIe"),
}

# raw_json keeps the pricing-relevant subset of the ~100-field offer (audit without bloat).
#
# The second block was added 2026-09-08 and is the reason Research Note 2026-04 needed a
# correction. That note reported that no source in the panel discloses a fabric, a power
# envelope or a thermal limit. The claim was true of what the index had STORED and false
# of what this source publishes: vast.ai returns measured throughput, memory and PCIe
# bandwidth, a power ceiling and a temperature ceiling per offer, and this tuple was
# discarding all of it. These fields are audit data only; nothing here is read by the
# calculation path.
RAW_FIELDS = (
    # pricing and identity
    "id", "ask_contract_id", "machine_id", "host_id", "gpu_name", "num_gpus",
    "dph_total", "dph_base", "discounted_dph_total", "min_bid", "geolocation",
    "geolocode", "verification", "hosting_type", "rentable", "rented", "reliability",
    "gpu_ram", "bw_nvlink", "duration", "static_ip",
    # delivered capability, as measured and published by the venue
    "dlperf", "dlperf_per_dphtotal", "total_flops", "flops_per_dphtotal",
    "gpu_mem_bw", "pcie_bw", "gpu_lanes", "pci_gen", "compute_cap", "gpu_arch",
    # the power and thermal envelope the datasheet cannot express
    "gpu_max_power", "gpu_max_temp",
    # storage and network path: the checkpoint-I/O axis
    "disk_bw", "nw_disk_avg_bw", "inet_down", "inet_up",
    # reliability and multi-node grouping
    "reliability2", "expected_reliability", "cluster_id", "score",
)


def _country(geolocation: str | None) -> str | None:
    if not geolocation or "," not in geolocation:
        return None
    code = geolocation.rsplit(",", 1)[-1].strip().upper()
    return code if len(code) == 2 else None


class VastAiCollector:
    name = "vast_ai"

    def collect(self, session: requests.Session) -> list[Observation]:
        resp = session.post(URL, json=QUERY, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
        offers = resp.json()["offers"]
        ts = utc_now_iso()
        out: list[Observation] = []
        unmapped: set[str] = set()
        for offer in offers:
            if offer.get("verification") != "verified" or offer.get("hosting_type") != 1:
                continue
            name = offer.get("gpu_name", "")
            model_map = GPU_MODEL_MAP.get(name)
            num_gpus = offer.get("num_gpus") or 0
            dph_total = offer.get("dph_total")
            if model_map is None:
                if name:
                    unmapped.add(name)
                continue
            if num_gpus < 1 or not dph_total:
                continue
            gpu_model, interconnect = model_map
            raw = json.dumps({k: offer.get(k) for k in RAW_FIELDS})
            common = {
                "ts_utc": ts,
                "source": self.name,
                "provider": "vast.ai",
                "gpu_model": gpu_model,
                "gpu_count": num_gpus,
                "region": offer.get("geolocation"),
                "country": _country(offer.get("geolocation")),
                "interconnect": interconnect,
                "raw_json": raw,
            }
            out.append(
                Observation(
                    price_usd_per_gpu_hr=float(dph_total) / num_gpus,
                    tier="executable",
                    term="on_demand",
                    **common,
                )
            )
            # The bid price for the same machine. This is the interruptible leg of a
            # two-sided quote, and storing it alongside the ask is what makes the
            # spread between them observable rather than inferred. `interruptible` is
            # in factors.filters.exclude_tiers and is dropped by normalise.py before any
            # print, so this cannot move a published number.
            min_bid = offer.get("min_bid")
            if min_bid and float(min_bid) > 0 and float(min_bid) != float(dph_total):
                out.append(
                    Observation(
                        price_usd_per_gpu_hr=float(min_bid) / num_gpus,
                        tier="interruptible",
                        term="on_demand",
                        **common,
                    )
                )
        if unmapped:
            # Not an error. New silicon appears on a marketplace before it appears on a
            # rate card, and this line is the earliest signal the index gets that a model
            # it does not price is being offered.
            log.info("vast_ai: unmapped gpu_name values seen: %s", ", ".join(sorted(unmapped)))
        log.info("vast_ai: %d/%d offers kept (datacenter-verified)", len(out), len(offers))
        return out
