# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Latitude.sh's prepaid-annual price — TCI's own adapter over the vendored recipe.

Roadmap L5.3: "Record Latitude.sh's prepaid annual price (the vendored recipe ignores it;
TCI can read it in its own adapter without touching vendored code)."

Latitude's own flight-JSON pricing map publishes an {hour, month, year} triple per plan,
per region, per currency. The vendored recipe (`tci/vendor/computable/sources/latitude.py`)
already turns `hour` into an on-demand row and `month` into a `tier="monthly-commit"` row
(TCI's `commit_1mo`), and discards `year` by binding it to `_year_s` with a leading
underscore, right there in its own loop. That is a choice about what one recipe's
*contract* covers, not evidence the field is unreadable — so it is read here, by a second,
independent pass over the same already-fetched page, reusing the vendored module's own
regex constants (import only; nothing in `vendor/computable/` is edited, per CLAUDE.md's
vendoring invariant).

WHAT THE NUMBER MEANS. Checked against the live page 2026-09-12: `month` is consistently
`hour * 365` to within rounding (i.e., roughly a flat 50% saving against the 730-hour
month `HOURS_PER_MONTH` already assumes elsewhere in the vendored recipe), and `year` is
consistently `month * 12 * 0.70` to four decimal places across every row checked — a
materially deeper discount than the monthly figure, which is the sane direction (commit
longer, pay less). Converting `year` to a per-GPU-hour equivalent uses the SAME
`HOURS_PER_MONTH` convention the vendored recipe already established for `month`,
extended to a year (`HOURS_PER_MONTH * 12` = 8760 hours), rather than inventing a second,
unrelated hours-per-year constant. `term.py` records the tenor as `reserved_1yr`, which is
the label its own `TERM_BY_MONTHS` maps `commitment_months=12` to.

ONE FETCH, NOT TWO. `latitude` is a shadow source already fetched once daily. This module
does not fetch: `collect_prepaid_annual` takes the SAME html body the ordinary collector
already downloaded this run and finds the year price in it, so a second capability never
costs a second request to latitude.sh (SOURCES.md's "one request per source per day").
The wiring in `computable_sources.py` does one `fetch()`, hands the body to both this
module's parser and the vendored one, and merges the two observation lists before they
reach `to_observations()` — everything downstream of that (variant, tier/term mapping,
country, raw_json) is the ordinary, unmodified Computable pipeline.
"""

from __future__ import annotations

from typing import Any

from tci.vendor.computable.observation import observation
from tci.vendor.computable.sources.latitude import (
    _BLOB_WINDOW,
    _CURRENCY_TRIPLE_RE,
    _GPU_ANY_RE,
    _GPU_SPEC_RE,
    _OS_LIST_MARKER,
    _PLAN_PAIR_RE,
    _PRICING_KEY,
    _REGION_START_RE,
    _num,
)
from tci.vendor.computable.sources.latitude import HOURS_PER_MONTH as _HOURS_PER_MONTH

HOURS_PER_YEAR = _HOURS_PER_MONTH * 12  # 8760h: the same convention, carried one step on


def parse_prepaid_annual(html: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Every plan/region/currency's prepaid-annual price, as a 12-month committed
    observation. Structurally the minimum subset of `parse_latitude`'s own windowing
    needed to reach the currency triple: plan pair -> GPU spec -> region block -> pricing
    map. Availability metadata (stock level, per-site lists, interconnect) is the ordinary
    recipe's business, not repeated here — this is priced years, nothing else.

    Fails the same way the vendored parser does on a reshape it cannot read: raises rather
    than guessing at a GPU plan's spec, and records everything else (a missing region, an
    unreadable currency block, a null/zero year) as a skip or a partial error, never a
    fabricated print.
    """
    pairs = list(_PLAN_PAIR_RE.finditer(html))
    if not pairs:
        raise RuntimeError(
            "latitude (annual): no bare-metal plan blobs found — page shape changed"
        )
    rows: list[dict[str, Any]] = []
    partial_errors: list[str] = []
    seen: dict[tuple[str, str, str], str] = {}  # (slug, region, currency) -> raw year

    for i, m in enumerate(pairs):
        slug, plan_name = m.group(1), m.group(2)
        end = (
            pairs[i + 1].start() if i + 1 < len(pairs)
            else min(m.start() + _BLOB_WINDOW, len(html))
        )
        window = html[m.start():end]
        cut = window.find(_OS_LIST_MARKER)
        if cut != -1:
            window = window[:cut]
        plain = window.replace('\\"', '"')
        if '"gpu":' not in plain:
            continue  # not a GPU plan blob

        gpu_any = _GPU_ANY_RE.search(plain)
        if not gpu_any or gpu_any.group(1) in ("null", "{}"):
            continue  # CPU-only, or genuinely absent — the ordinary recipe's job to flag
        gpu = _GPU_SPEC_RE.search(plain)
        if not gpu:
            raise RuntimeError(
                f"latitude (annual): plan {slug} has a non-empty gpu spec this parser "
                "cannot read — field order/shape changed; refusing to silently drop it"
            )
        count = int(gpu.group(1))
        gpu_type = gpu.group(2)
        if count < 1:
            continue  # per-GPU normalisation impossible; the ordinary recipe already notes it

        region_starts = list(_REGION_START_RE.finditer(plain))
        for j, rm in enumerate(region_starts):
            seg_end = (
                region_starts[j + 1].start() if j + 1 < len(region_starts) else len(plain)
            )
            seg = plain[rm.start():seg_end]
            region_name = rm.group(1)
            pricing_at = seg.find(_PRICING_KEY)
            if pricing_at == -1:
                continue  # no pricing map in this segment — the ordinary recipe's business

            map_start = pricing_at + len(_PRICING_KEY) - 1
            depth = 0
            map_end = -1
            for k in range(map_start, len(seg)):
                ch = seg[k]
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        map_end = k + 1
                        break
            if map_end == -1:
                partial_errors.append(
                    f"{slug}/{region_name}: pricing map never closes — annual price skipped"
                )
                continue

            triples = _CURRENCY_TRIPLE_RE.findall(seg[map_start:map_end])
            for cur, _hour_s, _month_s, year_s in triples:
                year = _num(year_s)
                if year is None:
                    continue  # this currency/region does not offer the plan — not a $0 print
                key = (slug, region_name, cur)
                if seen.get(key) == year_s:
                    continue  # duplicated flight payload, identical print
                if key in seen:
                    partial_errors.append(
                        f"{slug}/{region_name}/{cur}: conflicting annual prints "
                        f"({seen[key]} vs {year_s}) — kept the first, ambiguity noted"
                    )
                    continue
                seen[key] = year_s
                per_gpu = year / HOURS_PER_YEAR / count
                obs = observation(
                    sku_identifier=gpu_type,
                    price_per_gpu_hr=per_gpu,
                    currency=cur,
                    raw_value=year_s,
                    raw_unit=f"{cur.lower()}_per_node_year",
                    gpu_count_basis=count,
                    tier="reserved",
                    region=region_name,
                    notes=(
                        f"{slug} {count}x {gpu_type} bare metal, prepaid annual "
                        f"({cur} {year_s}/node/year, {HOURS_PER_YEAR:.0f}h/year convention "
                        "— TCI's own reading of the year field the vendored recipe leaves "
                        "unrecorded, not part of the Computable recipe's own contract)"
                    ),
                    extra={
                        "plan": slug,
                        "plan_name": plan_name,
                        "commitment_months": 12,
                        "tci_adapter": "latitude_annual",
                    },
                )
                rows.append(obs)
    return rows, partial_errors
