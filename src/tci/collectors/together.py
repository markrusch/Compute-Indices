# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Together AI's GPU Clusters rates from its public pricing page.

WHAT IS READ. https://www.together.ai/pricing, one GET a day. The page carries two
on-demand tables for GPU Clusters. The first sits inside a `<div class="hide">` and is
not shown to a reader, so it is not read here: a price a visitor cannot see is not a
public price. The visible one is headed "On-demand hourly rates and reserved capacity"
and states "All prices are per GPU per hour"; its columns are Preemptible Compute,
ON-Demand and four Reserved tenors. Only the ON-Demand column is stored. On 27 September
2026 it read HGX H100 $3.99, HGX H200 $5.99, HGX B200 $8.19 per GPU-hour. Both tables
agreed on every figure that day.

The column is found by its header text rather than by position, and the table by its
heading, so a reordered page is read correctly and a reshaped one raises. A collector
that cannot find the ON-Demand column fails the day rather than reading a neighbouring
column's number: the preemptible rate beside it is half the on-demand one.

NODE SIZE. The page gives no GPU count. "HGX" is NVIDIA's multi-GPU baseboard, and
Together's GPU Clusters documentation sells capacity in 8xH100 steps ("reserve 8xH100
for 30 days ... scale to 16xH100 temporarily", docs.together.ai/docs/gpu-clusters-overview,
read 2026-09-27). Rows are recorded at 8 GPUs, the smallest unit that documentation names.
If Together starts selling single GPUs under this table, that is the fact to recheck.

NO COUNTRY. Nothing on the page, its documentation or its terms says where the clusters
are. Rows are stored with country=None, which keeps them out of every regional block.
From v0.10.0 (notice 2026-N7) the Global block admits country-less rows from the
providers `factors.yaml:unplaced` names, and Together is one of them: a single price
Together charges wherever it runs the cluster is still a price for an H100 hour somewhere.

PERMISSION. robots.txt allows every path to every crawler except Google-Extended, and
the terms of service (read 2026-09-27) contain no clause on automated access.
"""

from __future__ import annotations

import html as html_lib
import json
import logging
import re

import requests

from tci.collectors.base import TIMEOUT_SECONDS
from tci.db import utc_now_iso
from tci.models import Observation

log = logging.getLogger("tci.collectors.together")

URL = "https://www.together.ai/pricing"
PROVIDER = "together"
NODE_GPUS = 8
TABLE_HEADING = "On-demand hourly rates and reserved capacity"
PER_GPU_STATEMENT = "All prices are per GPU per hour"
ON_DEMAND_HEADER = "on-demand"

# The page's hardware label -> TCI variant. Only HGX boards; the NVL72 racks are a
# different product and carry no hourly price.
VARIANTS = {"HGX H100": "H100_SXM", "HGX H200": "H200_SXM", "HGX B200": "B200_SXM",
            "HGX B300": "B300_SXM"}

_TABLE_RE = re.compile(r"<table\b.*?</table>", re.S)
_ROW_RE = re.compile(r"<tr\b.*?</tr>", re.S)
_CELL_RE = re.compile(r"<t([hd])\b([^>]*)>(.*?)</t[hd]>", re.S)
_PRICE_RE = re.compile(r"^\$(\d+(?:\.\d+)?)$")


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _table(page: str) -> str:
    at = page.find(TABLE_HEADING)
    if at < 0:
        raise RuntimeError(f"together: heading {TABLE_HEADING!r} not found — page shape changed")
    near = _text(page[at:at + 2000])
    if PER_GPU_STATEMENT not in near:
        raise RuntimeError("together: the per-GPU statement under the heading is gone — "
                           "the unit of these prices can no longer be read from the page")
    table = _TABLE_RE.search(page, at)
    if table is None:
        raise RuntimeError("together: no table after the on-demand heading")
    return table.group(0)


def _on_demand_column(header_row: str) -> int:
    """Index of the ON-Demand column among the data cells of a body row.

    The first header row spans the hardware column over two rows and the Reserved group
    over four, so the data index of each header is the running sum of colspans.
    """
    col = 0
    for _kind, attrs, inner in _CELL_RE.findall(header_row):
        span = re.search(r'colspan="(\d+)"', attrs)
        if _text(inner).lower() == ON_DEMAND_HEADER:
            return col
        col += int(span.group(1)) if span else 1
    raise RuntimeError("together: no ON-Demand column in the table header — page shape changed")


def parse(page: str) -> list[tuple[str, str, float]]:
    """(label, variant, on-demand USD per GPU-hour) for every HGX row with a price."""
    table = _table(page)
    rows = _ROW_RE.findall(table)
    if not rows:
        raise RuntimeError("together: the on-demand table has no rows")
    col = _on_demand_column(rows[0])
    out = []
    for row in rows[1:]:
        cells = [_text(inner) for kind, _a, inner in _CELL_RE.findall(row) if kind == "d"]
        if len(cells) <= col:
            continue  # the second header row, or a malformed row
        label = re.sub(r"^NVIDIA\s+", "", cells[0])
        price = _PRICE_RE.match(cells[col])
        if label in VARIANTS and price:
            out.append((label, VARIANTS[label], float(price.group(1))))
    if not any(v == "H100_SXM" for _l, v, _p in out):
        raise RuntimeError("together: no HGX H100 on-demand price in the table")
    return out


class TogetherCollector:
    name = "together"

    def collect(self, session: requests.Session) -> list[Observation]:
        resp = session.get(URL, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
        return self.observations(resp.text)

    def observations(self, page: str) -> list[Observation]:
        ts = utc_now_iso()
        out = [
            Observation(
                ts_utc=ts, source=self.name, provider=PROVIDER, gpu_model=variant,
                gpu_count=NODE_GPUS, price_usd_per_gpu_hr=price, region="unspecified",
                country=None, interconnect=None, tier="list", term="on_demand",
                raw_json=json.dumps({"url": URL, "label": label, "column": "ON-Demand",
                                     "unit": "usd_per_gpu_hr", "node_gpus_basis":
                                     "docs.together.ai gpu-clusters-overview, 8xH100 steps"}),
            )
            for label, variant, price in parse(page)
        ]
        log.info("together: %d on-demand rows (%s)", len(out),
                 ", ".join(f"{o.gpu_model} ${o.price_usd_per_gpu_hr:.2f}" for o in out))
        return out
