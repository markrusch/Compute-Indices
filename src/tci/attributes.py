# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""What each seller declares about the thing it is selling, beside what it charges for it.

A hyperscaler H100 at $14/GPU-hr and a marketplace H100 at $2.10/GPU-hr are both an H100
GPU-hour under the unit definition, and they are plainly not the same product. The index
prices them and says nothing about the difference beyond a segment label. This module
reads the difference back out of what the sellers themselves publish.

Three rules, and the third is the one that matters:

**As published.** A value is carried in the words and units the seller used. Lambda says
"2900 GiB" of system memory and Voltage Park says 1024; they are not converted to a common
unit here, because the conversion would be TCI's claim rather than the seller's, and the
point of this layer is that it is not TCI's claim.

**Declared or derived, never blurred.** `declared` means the source publishes the value in
a field. `derived` means TCI computed it from something else by a rule, and the rule
travels with the value. The stored `interconnect` column is why this distinction exists:
every value in it is read off a SKU string — Azure from `isr`/`noIB`, gpuhunt and Scaleway
from `SXM`, static entries hardcoded — and none of it is declared by anybody. Publishing
"InfiniBand" against a named company on the strength of three characters in a product code
would be the kind of unsourced claim this project exists not to make. Latitude.sh and
Voltage Park do state a fabric, in those words, and theirs is `declared`.

**Absent is a value.** A seller that does not publish its interconnect gets `None` and the
reason, never a guess and never a blank that reads like agreement with its neighbours. Most
of the panel does not publish most of this, which is itself the finding.

Nothing here is in the calculation path. It reads `observations.raw_json` and returns
strings; no print depends on it, and no methodology version needs to change for a seller to
start or stop publishing a field.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

DECLARED = "declared"
DERIVED = "derived"
# A third state, and not a cosmetic one: `provenance=DECLARED, value=None` would say
# the seller declared nothing, which is a contradiction dressed as a field.
ABSENT = "not published"

# The attributes asked for, in the order a reader wants them. A name absent from a
# source's map is reported as not stated.
ATTRIBUTES: tuple[tuple[str, str], ...] = (
    ("interconnect", "Interconnect"),
    ("gpus_per_node", "GPUs per node"),
    ("gpu_memory", "GPU memory"),
    ("vcpus", "vCPUs"),
    ("system_memory", "System memory"),
    ("local_storage", "Local storage"),
    ("contract_form", "Contract form"),
)

Reader = Callable[[dict[str, Any]], "tuple[str | None, str]"]


@dataclass(frozen=True)
class Attribute:
    name: str
    value: str | None
    provenance: str  # DECLARED | DERIVED
    detail: str      # the field it was read from, or the rule that produced it

    @property
    def stated(self) -> bool:
        return self.value is not None


def _s(value: Any) -> str | None:
    """A published value as a string, or None. Empty and whitespace are not values."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _extra(raw: dict[str, Any]) -> dict[str, Any]:
    extra = raw.get("extra")
    return extra if isinstance(extra, dict) else {}


def _field(raw: dict[str, Any], key: str, unit: str = "") -> tuple[str | None, str]:
    value = _s(raw.get(key))
    return (f"{value}{unit}" if value else None, key)


def _extra_field(raw: dict[str, Any], key: str, unit: str = "") -> tuple[str | None, str]:
    value = _s(_extra(raw).get(key))
    return (f"{value}{unit}" if value else None, f"extra.{key}")


def _gib_to_gb(raw: dict[str, Any]) -> tuple[str | None, str]:
    """vast.ai publishes gpu_ram in MiB. Rounding a published number is arithmetic, not a
    judgement about the product."""
    value = raw.get("gpu_ram")
    if not value:
        return None, "gpu_ram"
    return f"{round(float(value) / 1024)} GB", "gpu_ram"


def _nvlink(raw: dict[str, Any]) -> tuple[str | None, str]:
    """vast.ai hosts report a measured NVLink bandwidth. The measurement is the host's."""
    value = raw.get("bw_nvlink")
    if not value:
        return None, "bw_nvlink"
    return f"NVLink, {float(value):.0f} GB/s as measured by the host", "bw_nvlink"


def _gpuhunt_memory(raw: dict[str, Any]) -> tuple[str | None, str]:
    value = raw.get("gpu_memory")
    if not value:
        return None, "gpu_memory"
    return f"{float(value):.0f} GB", "gpu_memory"


# Per source, per attribute: a reader from the parsed raw_json to (value, field name).
# Every entry is a field the source publishes. Anything a source does not publish is
# absent from its map; nothing is inferred to fill a row.
_DECLARED: dict[str, dict[str, Reader]] = {
    "vast_ai": {
        "gpus_per_node": lambda r: _field(r, "num_gpus"),
        "gpu_memory": _gib_to_gb,
        "interconnect": _nvlink,
    },
    "runpod": {
        "gpu_memory": lambda r: _field(r, "memoryInGb", " GB"),
    },
    "gpuhunt": {
        "gpu_memory": _gpuhunt_memory,
    },
    "scaleway": {
        "gpus_per_node": lambda r: _field(r, "gpu"),
    },
    "latitude": {
        "interconnect": lambda r: _extra_field(r, "interconnect"),
    },
    "voltagepark": {
        "interconnect": lambda r: _extra_field(r, "network"),
        "gpus_per_node": lambda r: _extra_field(r, "node_gpu_count"),
        "system_memory": lambda r: _extra_field(r, "ram_gb", " GB"),
    },
    "lambda_pricing": {
        "vcpus": lambda r: _extra_field(r, "vcpus"),
        "system_memory": lambda r: _extra_field(r, "ram"),
        "local_storage": lambda r: _extra_field(r, "storage"),
        "gpu_memory": lambda r: _extra_field(r, "vram_per_gpu"),
    },
    "hyperstack": {
        "gpu_memory": lambda r: _extra_field(r, "vram_gb", " GB"),
        "vcpus": lambda r: _extra_field(r, "max_pcpus_per_gpu"),
        "system_memory": lambda r: _extra_field(r, "max_ram_gb_per_gpu", " GB per GPU"),
    },
    "coreweave": {
        "gpu_memory": lambda r: _extra_field(r, "vram_gb", " GB"),
        "vcpus": lambda r: _extra_field(r, "vcpus"),
        "system_memory": lambda r: _extra_field(r, "system_ram_gb", " GB"),
        "local_storage": lambda r: _extra_field(r, "local_storage_tb", " TB"),
    },
    "civo": {
        "gpus_per_node": lambda r: _field(r, "gpu_count_basis"),
    },
    "crusoe": {
        "gpu_memory": lambda r: _extra_field(r, "memory_tag"),
    },
}

# Contract form is the one attribute every row carries, because the collector records the
# tier it read the price from. Declared in the sense that matters: the seller published
# this price as bookable now, or as a rate card.
_TIER_WORDS = {
    "executable": "bookable at this price now",
    "list": "published rate card",
    "spot": "spot, price varies and capacity is reclaimable",
    "interruptible": "interruptible, capacity is reclaimable",
}


def for_observation(row: Any) -> list[Attribute]:
    """Every attribute for one stored observation, stated or explicitly not stated."""
    try:
        raw = json.loads(row["raw_json"] or "{}")
    except (ValueError, TypeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    source = row["source"]
    declared = _DECLARED.get(source, {})

    out: list[Attribute] = []
    for name, _label in ATTRIBUTES:
        if name == "contract_form":
            out.append(Attribute(
                name, _TIER_WORDS.get(row["tier"], row["tier"]), DECLARED,
                "the tier the price was read from",
            ))
            continue
        reader = declared.get(name)
        if reader is not None:
            try:
                value, field = reader(raw)
            except (TypeError, ValueError, KeyError):
                value, field = None, "unreadable"
            if value is not None:
                out.append(Attribute(name, value, DECLARED, field))
                continue
        out.append(_fallback(name, row, source))
    return out


def _fallback(name: str, row: Any, source: str) -> Attribute:
    """What to say when the source publishes no field for this attribute.

    Two have a derived answer worth showing with its rule attached. The rest are not
    stated, and say so.
    """
    if name == "interconnect" and _s(row["interconnect"]):
        return Attribute(
            name, str(row["interconnect"]), DERIVED,
            "read from the product name, not published as a field by this seller",
        )
    if name == "gpus_per_node" and row["gpu_count"]:
        return Attribute(
            name, str(row["gpu_count"]), DERIVED,
            "the offer's GPU count as the collector recorded it",
        )
    return Attribute(name, None, ABSENT, f"not published by {source}")


def coverage(rows: list[Any]) -> dict[str, tuple[int, int]]:
    """Per attribute: how many of these rows declare it, out of how many rows.

    The number this layer exists to produce. A low one is the answer to the question, not
    a reason to start inferring.
    """
    per_row = [for_observation(row) for row in rows]
    counts: dict[str, tuple[int, int]] = {}
    for name, _label in ATTRIBUTES:
        declared = sum(
            1 for attrs in per_row
            for a in attrs
            if a.name == name and a.stated and a.provenance == DECLARED
        )
        counts[name] = (declared, len(rows))
    return counts


def for_provider(
    conn: Any, date: str, provider: str, source: str, gpu_model: str | None = None
) -> list[Attribute]:
    """The attributes behind one constituent, over every offer that seller published that day.

    A constituent is a provider, not a single offer: its price comes from a set of offers,
    and those offers can declare different things. vast.ai is a marketplace of independent
    hosts, so its NVLink bandwidth genuinely differs from machine to machine.

    Where the offers agree, the agreed value is reported. Where they do not, the answer is
    that they do not, with the count — picking one and printing it as the seller's
    attribute would be inventing a fact about a named company out of an arbitrary row.
    """
    sql = (
        "SELECT o.* FROM observations o JOIN runs r ON o.run_id = r.run_id"
        " WHERE substr(o.ts_utc, 1, 10) = ? AND o.provider = ? AND o.source = ?"
        " AND r.status = 'ok' AND o.tier IN ('executable', 'list')"
    )
    params: list[Any] = [date, provider, source]
    if gpu_model:
        sql += " AND o.gpu_model = ?"
        params.append(gpu_model)
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return [Attribute(n, None, ABSENT, "no stored offer for this seller that day")
                for n, _ in ATTRIBUTES]

    per_row = [for_observation(r) for r in rows]
    out: list[Attribute] = []
    for i, (name, _label) in enumerate(ATTRIBUTES):
        seen = {(a.value, a.provenance, a.detail) for a in (row[i] for row in per_row)}
        if len(seen) == 1:
            value, provenance, detail = seen.pop()
            out.append(Attribute(name, value, provenance, detail))
            continue
        stated = {v for v, _p, _d in seen if v is not None}
        if not stated:
            out.append(Attribute(name, None, ABSENT, f"not published by {source}"))
        else:
            out.append(Attribute(
                name, f"varies across {len(rows)} offers",
                DECLARED if any(p == DECLARED for _v, p, _d in seen) else DERIVED,
                f"{len(stated)} distinct values published that day",
            ))
    return out
