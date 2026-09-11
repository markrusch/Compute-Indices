# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""RunPod public GraphQL (no auth) — secure-cloud executable per-GPU rates.

Collection scope (SOURCES.md): secure cloud only; community tier is outside the index
universe. RunPod's secure-cloud pricing is region-flat and deliverable from its EU
secure-cloud datacenters (EU-RO / EU-SE / EU-NL); observations are recorded against
EU-RO by convention (documented — the API exposes no per-region price).

Schema verified against a live response on 2026-07-18 and re-verified 2026-09-04:
gpuTypes[].securePrice is the secure-cloud on-demand $/GPU-hr, and
lowestPrice(input:{gpuCount:N}).stockStatus demonstrates availability at node size N.
If the endpoint ever requires auth, this collector is retired — no workarounds.

NODE SIZE: probed at 8, 4 and 2 GPUs in a single request (aliased fields), and the
LARGEST size that actually demonstrates stock is recorded. Until 2026-09-04 this
collector probed only gpuCount:8 — a leftover from before v0.3.0 lowered
`filters.min_gpu_count` from 8 to 2. When RunPod had no 8-GPU pod the collector
recorded gpu_count=None, and normalise.py dropped the row ("executable asks must
demonstrably state their size") even though the price was in hand. Measured over the
modern collector regime that accounted for 7 of 8 headline gaps. Verified live on
2026-09-04: H100_SXM was 8x=none, 4x=Low, 2x=Medium on a day the index gapped.

1 GPU is deliberately NOT probed. v0.3.0 measured a ~9% small-order premium on 1-GPU
offers and excludes them rather than normalising them away; probing 1 would admit
exactly what `min_gpu_count: 2` exists to keep out.
"""

from __future__ import annotations

import json
import logging

import requests

from tci.collectors.base import TIMEOUT_SECONDS
from tci.db import utc_now_iso
from tci.models import Observation

log = logging.getLogger("tci.collectors.runpod")

URL = "https://api.runpod.io/graphql"

# Descending: the largest size with stock wins. 1 is excluded on purpose — see module
# docstring. Kept in step with the aliases in QUERY below.
NODE_SIZES: tuple[int, ...] = (8, 4, 2)

QUERY = """
query {
  gpuTypes {
    id displayName memoryInGb secureCloud communityCloud
    securePrice communityPrice
    c8: lowestPrice(input: {gpuCount: 8}) {
      minimumBidPrice uninterruptablePrice stockStatus
    }
    c4: lowestPrice(input: {gpuCount: 4}) {
      minimumBidPrice uninterruptablePrice stockStatus
    }
    c2: lowestPrice(input: {gpuCount: 2}) {
      minimumBidPrice uninterruptablePrice stockStatus
    }
  }
}
"""

# A second, separate request: which datacentres report stock of each GPU type. Separate so
# that a schema change here can never take the price query down with it. Field names are
# the ones Computable reads live (observatory/sources/runpod.py, 2026-08-22).
DC_QUERY = (
    "query { dataCenters { id name listed"
    " gpuAvailability { gpuTypeId available stockStatus } } }"
)

GPU_MODEL_MAP = {
    "NVIDIA H100 80GB HBM3": ("H100_SXM", "NVLink"),
    "NVIDIA H100 NVL": ("H100_NVL_94GB", "NVL"),
    "NVIDIA A100-SXM4-80GB": ("A100_SXM", "NVLink"),
}


def demonstrated_node_size(gpu_type: dict) -> int | None:
    """Largest probed node size that RunPod reports stock for, else None.

    None is an honest "size not demonstrated", not a zero: normalise.py drops the row
    rather than letting an executable ask enter without stating its size.
    """
    for size in NODE_SIZES:
        node = gpu_type.get(f"c{size}") or {}
        if node.get("stockStatus"):
            return size
    return None


def us_datacentres(ids: list[str] | None) -> list[str]:
    """RunPod datacentre ids in the United States (ids are prefixed by location, 'US-TX-3')."""
    return [i for i in ids or [] if i.upper().startswith("US-")]


def parse_datacentre_stock(payload: dict) -> dict[str, list[str]]:
    """gpuTypeId -> sorted datacentre ids reporting that type available with stock."""
    out: dict[str, set[str]] = {}
    for dc in (payload.get("data") or {}).get("dataCenters") or []:
        if not dc.get("listed", True):
            continue
        for g in dc.get("gpuAvailability") or []:
            if g.get("available") and g.get("stockStatus"):
                out.setdefault(str(g.get("gpuTypeId")), set()).add(str(dc.get("id")))
    return {k: sorted(v) for k, v in out.items()}


class RunPodCollector:
    name = "runpod"

    def _datacentre_stock(self, session: requests.Session) -> dict[str, list[str]] | None:
        try:
            resp = session.post(URL, json={"query": DC_QUERY}, timeout=TIMEOUT_SECONDS)
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("errors"):
                return None
            return parse_datacentre_stock(payload)
        except (requests.RequestException, ValueError):
            return None

    def collect(self, session: requests.Session) -> list[Observation]:
        resp = session.post(URL, json={"query": QUERY}, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("errors"):
            raise RuntimeError(f"runpod graphql errors: {payload['errors'][:1]}")
        gpu_types = payload["data"]["gpuTypes"]
        stock_by_type = self._datacentre_stock(session)
        ts = utc_now_iso()
        out: list[Observation] = []
        for gt in gpu_types:
            model_map = GPU_MODEL_MAP.get(gt.get("id", ""))
            if model_map is None:
                continue
            if not gt.get("secureCloud") or not gt.get("securePrice"):
                continue
            gpu_model, interconnect = model_map
            node_size = demonstrated_node_size(gt)
            out.append(
                Observation(
                    ts_utc=ts,
                    source=self.name,
                    provider="runpod",
                    gpu_model=gpu_model,
                    # The largest size RunPod demonstrates stock for. Never inflated:
                    # gpu_count feeds the within-provider offer weight, so claiming 8
                    # when only 2 is available would overweight the offer.
                    gpu_count=node_size,
                    price_usd_per_gpu_hr=float(gt["securePrice"]),
                    region="secure-cloud EU (EU-RO/EU-SE/EU-NL)",
                    country="RO",
                    interconnect=interconnect,
                    tier="executable",
                    term="on_demand",
                    # Where the price is deliverable, as RunPod reports it: the datacentres
                    # with stock of this type today. Audit data only; country stays RO by
                    # convention (above) so no stored print changes meaning.
                    raw_json=json.dumps(
                        {**gt, "datacentres_in_stock": None if stock_by_type is None
                         else stock_by_type.get(gt.get("id", ""))}
                    ),
                )
            )
            # The same region-flat price, deliverable from the United States: recorded as a
            # second row ONLY when RunPod itself reports stock of this type in a US
            # datacentre today. The EU row above keeps its documented convention (country
            # RO) so no stored print changes meaning; this row feeds the US block.
            in_stock = None if stock_by_type is None else stock_by_type.get(gt.get("id", ""))
            us = us_datacentres(in_stock)
            if us:
                out.append(
                    Observation(
                        ts_utc=ts,
                        source=self.name,
                        provider="runpod",
                        gpu_model=gpu_model,
                        gpu_count=node_size,
                        price_usd_per_gpu_hr=float(gt["securePrice"]),
                        region="secure-cloud US (" + ", ".join(us) + ")",
                        country="US",
                        interconnect=interconnect,
                        tier="executable",
                        term="on_demand",
                        raw_json=json.dumps({**gt, "datacentres_in_stock": in_stock}),
                    )
                )
        sized = sum(1 for o in out if o.gpu_count is not None)
        if stock_by_type is None:
            log.warning("runpod: datacentre stock query failed; prices recorded without it")
        log.info(
            "runpod: %d gpu types kept (secure cloud), %d with a demonstrated node size",
            len(out), sized,
        )
        return out
