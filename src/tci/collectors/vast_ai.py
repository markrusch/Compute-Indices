# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Vast.ai public search API (no auth) — executable marketplace asks.

Collection scope (documented in SOURCES.md): datacenter-verified hosts only
(verification == 'verified' AND hosting_type == 1); community/hobbyist inventory is
outside the index universe entirely. Offers are stored globally — EU/EEA, minimum
GPU count, and price-band filters are applied in the calculation path (normalise.py)
so per-day exclusions stay auditable; non-EU rows feed the US reference block.

Field semantics verified against a live response on 2026-07-18:
dph_total is the whole-instance $/hr (divide by num_gpus); geolocation is
'Region, CC'; hosting_type 1 = datacenter.

Request budget: one POST per chip in CHIPS, plus one more for a chip whose ascending
book comes back full, spaced REQUEST_SPACING_SECONDS apart. That is 9-18 requests a
day where the source policy used to say one; the reason is the server's page clamp,
explained above CHIPS, and SOURCES.md records the exception.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests

from tci.collectors.base import TIMEOUT_SECONDS
from tci.db import utc_now_iso
from tci.models import Observation

log = logging.getLogger("tci.collectors.vast_ai")

URL = "https://console.vast.ai/api/v0/bundles/"

# One query per chip, not one query for the whole marketplace.
#
# WHY. On 2026-09-08 the per-chip `gpu_name` filter was removed so that unmapped silicon
# would show up in the logs. The endpoint clamps every response (Computable measured the
# clamp on 2026-08-22: limit=400 returned 64 offers), and the query was ordered by
# dph_total ascending, so what came back was the cheapest slice of the whole marketplace,
# which is consumer cards. On 2026-09-11, the first run after that change to store
# anything, vast.ai contributed two rows, both RTX 3060s in the US; the headline lost its
# only executable constituent and gapped at 4 of 5 providers. Nothing failed loudly,
# because an empty book is a legal answer.
#
# The shape below is the one Computable hardened live (their observatory/sources/vast.py,
# Apache-2.0): one chip per request, ascending by instance total, and a second
# descending read only when the ascending one comes back full. Ascending order truncates the LARGEST
# instance totals first, which are exactly the cheap-per-GPU 8x nodes, so a full
# ascending book is never trusted alone.
#
# FETCH_LIMIT must stay below the server's own clamp. If it were above it, a truncated
# book could never be detected: the response would always look shorter than the limit.
FETCH_LIMIT = 50
REQUEST_SPACING_SECONDS = 0.75

# Datacenter chips queried, in order. Each is one request (two if its book is full).
CHIPS: tuple[str, ...] = (
    "H100 SXM", "H100 NVL", "H100 PCIE", "A100 SXM4", "A100 PCIE",
    "H200", "H200 NVL", "B200", "B300",
)


def chip_query(gpu_name: str, order: str = "asc") -> dict:
    # `in` with one element rather than `eq`: `in` is the operator this collector ran live
    # against this POST endpoint from 2026-07-18 to 2026-09-07. Computable's proven `eq`
    # form uses the GET variant of the same endpoint; the two are equivalent in intent, and
    # this keeps the request shape the one already proven on this transport.
    return {
        "gpu_name": {"in": [gpu_name]},
        "rentable": {"eq": True},
        "type": "on-demand",
        "order": [["dph_total", order]],
        "limit": FETCH_LIMIT,
    }


# vast.ai gpu_name -> (canonical variant, intra-node bus).
#
# Mapping a chip to a reference variant does NOT admit it to a print. Admission is
# decided by the methodology's explicit panel (factors.yaml `panel`), which names the
# classes each provider may contribute to. H200, B200, B300 and H100 PCIe are mapped
# here so their history starts accumulating now; they reach a published series only
# when a methodology version admits vast.ai to those classes, after a notice.
GPU_MODEL_MAP = {
    "H100 SXM": ("H100_SXM", "NVLink"),
    "H100 NVL": ("H100_NVL_94GB", "NVL"),
    "H100 PCIE": ("H100_PCIE", "PCIe"),
    "A100 SXM4": ("A100_SXM", "NVLink"),
    "A100 PCIE": ("A100_PCIE", "PCIe"),
    "H200": ("H200_SXM", "NVLink"),
    "H200 NVL": ("H200_NVL", "NVL"),
    "B200": ("B200_SXM", "NVLink"),
    "B300": ("B300_SXM", "NVLink"),
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


def merge_books(asc: list[dict], desc: list[dict]) -> list[dict]:
    """Union of the two reads, deduplicated by offer id, ascending read first."""
    seen: set = set()
    out: list[dict] = []
    for offer in [*asc, *desc]:
        key = offer.get("id")
        if key in seen:
            continue
        seen.add(key)
        out.append(offer)
    return out


def book_stats(asc: list[dict], desc: list[dict] | None) -> dict:
    """What was read for one chip, stored with every row so a thin book is visible.

    `possibly_truncated` is True when both reads came back full and did not overlap:
    offers priced between the two ends may exist that neither read returned.
    """
    asc_ids = {o.get("id") for o in asc}
    desc_ids = {o.get("id") for o in desc or []}
    return {
        "asc_count": len(asc),
        "desc_count": None if desc is None else len(desc),
        "fetch_limit": FETCH_LIMIT,
        "possibly_truncated": bool(
            desc is not None and len(desc) >= FETCH_LIMIT and not (asc_ids & desc_ids)
        ),
    }


class VastAiCollector:
    name = "vast_ai"

    def __init__(self, spacing_seconds: float = REQUEST_SPACING_SECONDS) -> None:
        self.spacing_seconds = spacing_seconds

    def _read(self, session: requests.Session, gpu_name: str, order: str) -> list[dict]:
        resp = session.post(URL, json=chip_query(gpu_name, order), timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
        offers = resp.json().get("offers") or []
        # Identity pin: keep only offers the server itself labels with the queried chip.
        # If the eq filter ever stops discriminating, the wrong silicon must not be
        # recorded under this chip's name.
        return [o for o in offers if o.get("gpu_name") == gpu_name]

    def collect(self, session: requests.Session) -> list[Observation]:
        ts = utc_now_iso()
        out: list[Observation] = []
        failures: list[str] = []
        total_read = 0
        for i, gpu_name in enumerate(CHIPS):
            if i and self.spacing_seconds:
                time.sleep(self.spacing_seconds)
            try:
                asc = self._read(session, gpu_name, "asc")
                desc = None
                if len(asc) >= FETCH_LIMIT:
                    time.sleep(self.spacing_seconds)
                    desc = self._read(session, gpu_name, "desc")
            except (requests.RequestException, ValueError) as exc:
                # One chip failing is a partial read, not a failed source.
                failures.append(f"{gpu_name}: {type(exc).__name__}")
                continue
            offers = merge_books(asc, desc or [])
            total_read += len(offers)
            stats = book_stats(asc, desc)
            if stats["possibly_truncated"]:
                log.warning("vast_ai: %s book possibly truncated (%s)", gpu_name, stats)
            out.extend(self.to_observations(offers, ts, gpu_name, stats))
        if total_read == 0:
            # Nine datacenter chips and not one offer is not a quiet market, it is a
            # changed API or a filter that stopped matching. Fail loudly so the run is
            # recorded as failed with a reason, instead of 'ok, 0 observations'.
            raise RuntimeError(f"vast_ai: zero offers read across all chips {failures}")
        if failures:
            log.warning("vast_ai: partial read, failed chips: %s", failures)
        log.info("vast_ai: %d rows from %d offers across %d chips", len(out), total_read,
                 len(CHIPS))
        return out

    def to_observations(
        self, offers: list[dict], ts: str, queried: str, stats: dict
    ) -> list[Observation]:
        out: list[Observation] = []
        for offer in offers:
            if offer.get("verification") != "verified" or offer.get("hosting_type") != 1:
                continue
            model_map = GPU_MODEL_MAP.get(offer.get("gpu_name", ""))
            num_gpus = offer.get("num_gpus") or 0
            dph_total = offer.get("dph_total")
            if model_map is None or num_gpus < 1 or not dph_total:
                continue
            gpu_model, interconnect = model_map
            raw = {k: offer.get(k) for k in RAW_FIELDS}
            raw["queried_gpu_name"] = queried
            raw["book"] = stats
            common: dict[str, Any] = {
                "ts_utc": ts,
                "source": self.name,
                "provider": "vast.ai",
                "gpu_model": gpu_model,
                "gpu_count": num_gpus,
                "region": offer.get("geolocation"),
                "country": _country(offer.get("geolocation")),
                "interconnect": interconnect,
                "raw_json": json.dumps(raw),
            }
            out.append(
                Observation(
                    price_usd_per_gpu_hr=float(dph_total) / num_gpus,
                    tier="executable",
                    term="on_demand",
                    **common,
                )
            )
            # The bid price for the same machine: the interruptible leg of a two-sided
            # quote. `interruptible` is excluded by normalise.py before any print, so this
            # widens the audit trail without touching a number.
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
        return out
