# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Crusoe's pricing page after its 22-23 October 2026 redesign: the GPU table by column.

The vendored recipe (`tci.vendor.computable.sources.crusoe`) reads a table built from
rows, one `prixing-item` container per chip holding its heading, its two spec tags and
both price cells. On 23 October that recipe failed with "zero GPU rows": the page now
renders the same table as three parallel Webflow lists. The first lists the chips, one
item per chip carrying its heading and tags. The second lists the On-demand cells and
the third the Current-spot cells, one `gpu-row` item per chip in the same order. A price
cell no longer sits inside its chip's row, so the only thing joining "$3.90" to "NVIDIA
H100" is position.

Position alone is how a price ends up against the wrong chip, so it is checked rather
than trusted. Every "Contact sales" cell links through `data-wf-cms-context` to the CMS
item of the chip it belongs to. Each such cell must name the chip at its own position in
the chip list, or the page is refused. A priced cell carries no item id, so it is placed
by the anchors around it, and a column needs at least two anchors before anything in it
is read. On 23 October the On-demand column had five (Vera Rubin, GB300, GB200, B200,
MI355X), all on their own chips. The spot column's twelve "Contact sales" cells link to
twelve other items in the same collection, none of them a chip in the list, so its order
cannot be checked. The on-demand column must pair or the page is refused; a spot column
that does not pair is skipped with a partial error. Nothing is lost by that today, since
every spot cell read "Contact sales".

Every other check is the vendored recipe's own, kept as strict: the section fence
between "GPU instances pricing" and "CPU instances pricing", the GPU model | On-demand |
Current spot header order inside it, the exact `$D.DD/GPU-hr` pin on the whole cell text
(now split over three paragraphs, "$3.90", "/", "GPU-hr", which are rejoined before the
pin is applied), and at least four priced on-demand chips. The observations are built
with the vendored `observation()` so the adapter treats them exactly like the recipe's.

The vendored module stays byte-identical to upstream. This reader is tried only after it
raises, so if Crusoe restores the row layout the recipe takes over again unchanged.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import unquote

from tci.vendor.computable.observation import observation
from tci.vendor.computable.sources import crusoe as recipe

_LIST_SPLIT = '<div class="w-dyn-list">'
_CHIP_ITEM_RE = re.compile(r'<div data-sort-name="[^"]*"[^>]*role="listitem"[^>]*>')
_CELL_ITEM = '<div role="listitem" class="gpu-row w-dyn-item">'
_CELL_RE = re.compile(r'<div[^>]*class="pricing-rich[^"]*"[^>]*>(.*?)</div>', re.DOTALL)
_CMS_RE = re.compile(r'data-wf-cms-context="([^"]+)"')
_SLASH_RE = re.compile(r"\s*/\s*")

_MIN_ANCHORS = 2


def _item_id(fragment: str) -> str | None:
    match = _CMS_RE.search(fragment)
    if not match:
        return None
    try:
        context = json.loads(unquote(match.group(1)))
        return str(context[0]["itemId"])
    except (ValueError, LookupError, TypeError):
        return None


def _chips(chip_list: str) -> list[tuple[str, str, str, str | None]]:
    """(heading, memory tag, form-factor tag, CMS item id) per chip, in page order."""
    chips = []
    for item in _CHIP_ITEM_RE.split(chip_list)[1:]:
        headings = recipe._HEADING_RE.findall(item)
        if len(headings) != 1:
            raise RuntimeError(
                f"crusoe: chip item with {len(headings)} headings -- chip list reshaped; "
                "refusing to extract"
            )
        tags = [recipe._text(t) for t in recipe._TAG_RE.findall(item)]
        if len(tags) != 2:
            raise RuntimeError(
                f"crusoe: chip {headings[0].strip()!r} has {len(tags)} spec tags, expected "
                "2 -- row identity (A100 SXM vs PCIe) rides on them; refusing to extract"
            )
        chips.append((headings[0].strip(), tags[0], tags[1], _item_id(item)))
    return chips


def _column(cell_list: str, chips: list[tuple[str, str, str, str | None]],
            column: str) -> list[str]:
    """The tag-stripped text of each cell, after checking every anchor sits on its chip."""
    items = cell_list.split(_CELL_ITEM)[1:]
    if len(items) != len(chips):
        raise RuntimeError(
            f"crusoe: {column} column has {len(items)} cells for {len(chips)} chips -- "
            "cannot pair by position; refusing to extract"
        )
    texts, anchors = [], 0
    for i, item in enumerate(items):
        # Priced cells are now `pricing-rich is-dp is-sl`, so the recipe's class pin
        # (`pricing-rich w-richtext`) only matches the Contact-sales cells.
        cells = _CELL_RE.findall(item)
        if len(cells) != 1:
            raise RuntimeError(
                f"crusoe: {column} cell {i} holds {len(cells)} price cells, expected 1"
            )
        anchor = _item_id(item)
        if anchor is not None:
            if anchor != chips[i][3]:
                raise RuntimeError(
                    f"crusoe: {column} cell {i} links to CMS item {anchor} but the chip at "
                    f"that position is {chips[i][0]!r} ({chips[i][3]}) -- columns out of "
                    "step; refusing to pair prices by position"
                )
            anchors += 1
        texts.append(_SLASH_RE.sub("/", recipe._text(cells[0])))
    if anchors < _MIN_ANCHORS:
        raise RuntimeError(
            f"crusoe: {column} column has {anchors} anchored cells (need >= "
            f"{_MIN_ANCHORS}) -- position cannot be checked; refusing to extract"
        )
    return texts


def parse_columns(html: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Pure parse of the column layout -> (observations, partial_errors)."""
    section = recipe._gpu_section(html)
    lists = section.split(_LIST_SPLIT)[1:]
    chip_lists = [part for part in lists if _CHIP_ITEM_RE.search(part)]
    cell_lists = [part for part in lists if _CELL_ITEM in part]
    if len(chip_lists) != 1 or len(cell_lists) != len(recipe._COLUMNS):
        raise RuntimeError(
            f"crusoe: {len(chip_lists)} chip lists and {len(cell_lists)} price lists in "
            f"the GPU section, expected 1 and {len(recipe._COLUMNS)}; refusing to extract"
        )
    chips = _chips(chip_lists[0])
    partial_errors: list[str] = []
    # On-demand has to pair or the page is refused: it is the only column TCI uses. A
    # spot column that does not pair is dropped whole, never read by position.
    columns: list[list[str] | None] = []
    for part, name in zip(cell_lists, recipe._COLUMNS, strict=True):
        try:
            columns.append(_column(part, chips, name))
        except RuntimeError as exc:
            if name == recipe._COLUMNS[0]:
                raise
            partial_errors.append(f"{exc}; column skipped")
            columns.append(None)

    rows: list[dict[str, Any]] = []
    priced_on_demand = 0
    for i, (heading, memory_tag, form_tag, _item) in enumerate(chips):
        if not recipe._VENDOR_RE.match(heading):
            partial_errors.append(f"chip {heading!r}: no NVIDIA/AMD prefix; skipped")
            continue
        identifier = " ".join(t for t in (heading, memory_tag, form_tag) if t)
        mem_match = recipe._MEMORY_TAG_RE.match(memory_tag)
        for texts, column, tier in zip(columns, recipe._COLUMNS, recipe._TIERS, strict=True):
            if texts is None:
                continue
            where = f"chip {identifier!r}, {column} column"
            kind, value = recipe._classify_cell(texts[i], where)
            if kind != "price":
                partial_errors.append(f"{where}: unpriced cell {value!r} -- skipped")
                continue
            if tier == "on-demand":
                priced_on_demand += 1
            obs = observation(
                sku_identifier=identifier,
                price_per_gpu_hr=value,
                raw_value=texts[i],
                tier=tier,
                region=recipe._REGION,
                notes=(
                    f"GPU instances table, {column} column, paired to its chip by "
                    "position and checked against the Contact-sales CMS anchors"
                ),
                extra={
                    "column": column,
                    "memory_tag": memory_tag,
                    "form_factor_tag": form_tag or None,
                    "layout": "columns-2026-10",
                },
            )
            if mem_match:
                obs["memory_gb_label"] = int(mem_match.group(1))
            rows.append(obs)
    if priced_on_demand < recipe._MIN_PRICED_ON_DEMAND:
        raise RuntimeError(
            f"crusoe: only {priced_on_demand} priced on-demand chips (expect >= "
            f"{recipe._MIN_PRICED_ON_DEMAND}) -- refusing to record a thin capture"
        )
    return rows, partial_errors
