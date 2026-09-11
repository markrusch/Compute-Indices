# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Contributed term prices: intake, validation, private storage, and publishable aggregates.

Public rate cards say little about term pricing. Across every public source TCI collects,
as of September 2026, no committed tenor of any GPU is published by three or more sellers
(see tci.term). The prices that matter to a lender or a buyer are in signed contracts and
firm quotes, and only the parties to them hold them. This module is how they can be
contributed without being exposed.

Rules, each enforced here rather than promised in prose:

- PRIVATE BY CONSTRUCTION. Contributions are stored in an SQLite database outside the
  repository (TCI_PRIVATE_DIR, default ~/.tci-private). `private_db_path` refuses any path
  inside the repository, so a contributed price cannot be committed by accident, and
  nothing in this module writes a contributor's identity or a single quote anywhere public.
- APPEND-ONLY. As with observations, triggers forbid UPDATE and DELETE. A correction is a
  new row that names the row it supersedes.
- ONE PRICE PER CONTRIBUTOR. Each contributor's quotes in a cell are reduced to their own
  median before anything is pooled, so a party that splits one deal into ten small
  quotes gets one voice, not ten.
- A MEDIAN NEEDS THREE CONTRIBUTORS, A RANGE NEEDS FIVE, and no single contributor may
  carry more than half of the cell's GPU volume. Below the thresholds the cell is
  published as a count, never as a value: a median of two prices reveals both to either
  contributor, and the quartiles of three prices, published beside their median, reveal
  all three.
- KIND IS KEPT. An executed contract, a firm quote and an indicative quote are different
  evidence. Every aggregate states how many of each it contains, and indicative quotes are
  never the only evidence behind a published cell.
- NOT REPRODUCIBLE FROM PUBLIC DATA, AND SAYS SO. Everything else TCI publishes can be
  recomputed from the repository. Contributed cells cannot, by design; each carries that
  statement, and an auditor can verify them only against the private store.

The aggregates are research outputs. Nothing here enters an index print.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import date as date_type
from pathlib import Path
from statistics import median
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

KINDS = ("executed", "firm_quote", "indicative")
ROLES = ("seller", "buyer", "broker")
CURRENCIES = ("USD", "EUR")
TENORS = (1, 3, 6, 12, 18, 24, 36, 48, 60)
GPU_MODELS = ("H100_SXM", "H100_PCIE", "H200_SXM", "B200_SXM", "B300_SXM", "GB200",
              "GB300", "A100_SXM", "MI300X")
REGIONS = ("EU_EEA", "UK", "CH", "US", "OTHER")
MIN_CONTRIBUTORS = 3
MIN_CONTRIBUTORS_FOR_RANGE = 5
MAX_CONTRIBUTOR_SHARE = 0.5
PRICE_BAND = (0.25, 60.0)  # per GPU-hour, native currency: a junk-entry guard only

FIELDS = (
    "as_of", "role", "kind", "gpu_model", "gpus", "tenor_months", "price_per_gpu_hour",
    "currency", "region", "start_date", "payment", "notes",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS contributions (
  id INTEGER PRIMARY KEY,
  received_utc TEXT NOT NULL,
  contributor TEXT NOT NULL,          -- pseudonym; the key to a name is kept offline
  as_of TEXT NOT NULL,                -- the date the price applies to
  role TEXT NOT NULL CHECK (role IN ('seller','buyer','broker')),
  kind TEXT NOT NULL CHECK (kind IN ('executed','firm_quote','indicative')),
  gpu_model TEXT NOT NULL,
  gpus INTEGER NOT NULL CHECK (gpus >= 1),
  tenor_months INTEGER NOT NULL,
  price_per_gpu_hour REAL NOT NULL,
  currency TEXT NOT NULL CHECK (currency IN ('USD','EUR')),
  region TEXT NOT NULL,
  start_date TEXT,
  payment TEXT,
  notes TEXT,
  supersedes INTEGER REFERENCES contributions(id),
  source_sha256 TEXT NOT NULL         -- digest of the submitted file, for the audit trail
);
CREATE TRIGGER IF NOT EXISTS contrib_no_update BEFORE UPDATE ON contributions
  BEGIN SELECT RAISE(ABORT, 'contributions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS contrib_no_delete BEFORE DELETE ON contributions
  BEGIN SELECT RAISE(ABORT, 'contributions are immutable'); END;
"""


class ContributionError(ValueError):
    """A submitted row that fails validation. The message names the row and the field."""


@dataclass(frozen=True)
class Contribution:
    as_of: str
    role: str
    kind: str
    gpu_model: str
    gpus: int
    tenor_months: int
    price_per_gpu_hour: float
    currency: str
    region: str
    start_date: str | None = None
    payment: str | None = None
    notes: str | None = None


def _date(value: str, where: str) -> str:
    try:
        return date_type.fromisoformat(value.strip()).isoformat()
    except ValueError as exc:
        raise ContributionError(f"{where}: not a YYYY-MM-DD date: {value!r}") from exc


def parse_row(raw: dict[str, str], line: int) -> Contribution:
    """Validate one submitted row. Every rule failing names the line and the field."""
    where = f"line {line}"
    missing = [f for f in FIELDS[:9] if not str(raw.get(f, "")).strip()]
    if missing:
        raise ContributionError(f"{where}: missing {', '.join(missing)}")
    role, kind = raw["role"].strip(), raw["kind"].strip()
    if role not in ROLES:
        raise ContributionError(f"{where}: role must be one of {ROLES}, got {role!r}")
    if kind not in KINDS:
        raise ContributionError(f"{where}: kind must be one of {KINDS}, got {kind!r}")
    gpu_model = raw["gpu_model"].strip()
    if gpu_model not in GPU_MODELS:
        raise ContributionError(f"{where}: gpu_model must be one of {GPU_MODELS}")
    try:
        gpus = int(raw["gpus"])
        tenor = int(raw["tenor_months"])
        price = float(raw["price_per_gpu_hour"])
    except ValueError as exc:
        raise ContributionError(f"{where}: gpus, tenor_months and price must be numbers") from exc
    if gpus < 1:
        raise ContributionError(f"{where}: gpus must be at least 1")
    if tenor not in TENORS:
        raise ContributionError(f"{where}: tenor_months must be one of {TENORS}")
    currency = raw["currency"].strip().upper()
    if currency not in CURRENCIES:
        raise ContributionError(f"{where}: currency must be USD or EUR")
    if not PRICE_BAND[0] <= price <= PRICE_BAND[1]:
        raise ContributionError(
            f"{where}: price {price} per GPU-hour is outside {PRICE_BAND}; a total contract"
            " value or a node price entered as a per-GPU price is the usual cause"
        )
    region = raw["region"].strip()
    if region not in REGIONS:
        raise ContributionError(f"{where}: region must be one of {REGIONS}")
    notes = (raw.get("notes") or "").strip()
    if "EXAMPLE ROW" in notes.upper():
        raise ContributionError(f"{where}: this is the template's example row; delete it")
    start = raw.get("start_date", "").strip()
    return Contribution(
        as_of=_date(raw["as_of"], where), role=role, kind=kind, gpu_model=gpu_model,
        gpus=gpus, tenor_months=tenor, price_per_gpu_hour=price, currency=currency,
        region=region, start_date=_date(start, where) if start else None,
        payment=(raw.get("payment") or "").strip() or None,
        notes=(raw.get("notes") or "").strip() or None,
    )


def read_file(path: Path) -> tuple[list[Contribution], str]:
    """Parse a submitted CSV. All-or-nothing: one bad row rejects the file."""
    blob = path.read_bytes()
    text = blob.decode("utf-8-sig")
    reader = csv.DictReader(text.splitlines())
    header = [h.strip() for h in (reader.fieldnames or [])]
    unknown = [h for h in header if h not in FIELDS]
    if unknown:
        raise ContributionError(f"unknown columns: {', '.join(unknown)}")
    rows = [parse_row({k.strip(): (v or "") for k, v in r.items()}, i + 2)
            for i, r in enumerate(reader)]
    if not rows:
        raise ContributionError("the file has no rows")
    return rows, hashlib.sha256(blob).hexdigest()


def private_db_path(private_dir: Path | None = None) -> Path:
    """The private store. Refuses any location inside the repository."""
    base = Path(private_dir or os.environ.get("TCI_PRIVATE_DIR") or Path.home() / ".tci-private")
    base = base.expanduser().resolve()
    repo = REPO_ROOT.resolve()
    if base == repo or repo in base.parents:
        raise ContributionError(
            f"refusing a private store inside the repository ({base}): contributed prices"
            " must never be committable"
        )
    base.mkdir(parents=True, exist_ok=True)
    return base / "contributions.db"


def connect_private(private_dir: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(private_db_path(private_dir))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def ingest(conn: sqlite3.Connection, contributor: str, rows: list[Contribution],
           source_sha256: str, received_utc: str, supersedes: int | None = None) -> int:
    """Store validated rows. A correction is one row that names the row it replaces.

    Only the contributor who submitted a row can supersede it, and a row can be superseded
    once; the original stays in the store either way.
    """
    if not contributor or not contributor.replace("-", "").isalnum():
        raise ContributionError("contributor must be a pseudonym of letters, digits and '-'")
    if supersedes is not None:
        if len(rows) != 1:
            raise ContributionError("a correction file must hold exactly one row")
        old = conn.execute("SELECT contributor FROM contributions WHERE id = ?",
                           (supersedes,)).fetchone()
        if old is None:
            raise ContributionError(f"no contribution with id {supersedes}")
        if old["contributor"] != contributor:
            raise ContributionError("a row can be corrected only by the contributor who sent it")
        if conn.execute("SELECT 1 FROM contributions WHERE supersedes = ?",
                        (supersedes,)).fetchone():
            raise ContributionError(f"contribution {supersedes} is already superseded")
    with conn:
        conn.executemany(
            "INSERT INTO contributions (received_utc, contributor, as_of, role, kind,"
            " gpu_model, gpus, tenor_months, price_per_gpu_hour, currency, region,"
            " start_date, payment, notes, supersedes, source_sha256)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (received_utc, contributor, r.as_of, r.role, r.kind, r.gpu_model, r.gpus,
                 r.tenor_months, r.price_per_gpu_hour, r.currency, r.region, r.start_date,
                 r.payment, r.notes, supersedes, source_sha256)
                for r in rows
            ],
        )
    return len(rows)


@dataclass(frozen=True)
class ContributedCell:
    gpu_model: str
    tenor_months: int
    region: str
    currency: str
    published: bool
    reason: str | None
    n_contributors: int
    n_quotes: int
    kinds: dict[str, int] = field(default_factory=dict)
    median: float | None = None
    p25: float | None = None
    p75: float | None = None


def _quantile(sorted_vals: list[float], q: float) -> float:
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def aggregate(conn: sqlite3.Connection, start: str, end: str) -> list[ContributedCell]:
    """Publishable cells for contributions with as_of in [start, end].

    A superseded row is replaced by the row that supersedes it. Within a cell, each
    contributor's volume is its total GPUs; a contributor above MAX_CONTRIBUTOR_SHARE of
    the cell's volume suppresses the cell even with three or more contributors, because
    the published median would then mostly be one party's price. The pooled statistics
    are taken over one price per contributor (the median of its own quotes).
    """
    rows = conn.execute(
        "SELECT * FROM contributions WHERE as_of BETWEEN ? AND ?"
        " AND id NOT IN (SELECT supersedes FROM contributions WHERE supersedes IS NOT NULL)",
        (start, end),
    ).fetchall()
    groups: dict[tuple, list[Any]] = {}
    for r in rows:
        groups.setdefault((r["gpu_model"], r["tenor_months"], r["region"], r["currency"]),
                          []).append(r)
    out = []
    for (gpu_model, tenor, region, currency), members in sorted(groups.items()):
        volume: dict[str, int] = {}
        kinds: dict[str, int] = {}
        for m in members:
            volume[m["contributor"]] = volume.get(m["contributor"], 0) + int(m["gpus"])
            kinds[m["kind"]] = kinds.get(m["kind"], 0) + 1
        total = sum(volume.values())
        n = len(volume)
        reason = None
        if n < MIN_CONTRIBUTORS:
            reason = f"fewer than {MIN_CONTRIBUTORS} contributors"
        elif max(volume.values()) / total > MAX_CONTRIBUTOR_SHARE:
            reason = "one contributor holds more than half the volume"
        elif kinds.get("indicative", 0) == len(members):
            reason = "indicative quotes only"
        own: dict[str, list[float]] = {}
        for m in members:
            own.setdefault(m["contributor"], []).append(float(m["price_per_gpu_hour"]))
        prices = sorted(median(v) for v in own.values())
        published = reason is None
        ranged = published and n >= MIN_CONTRIBUTORS_FOR_RANGE
        out.append(
            ContributedCell(
                gpu_model=gpu_model, tenor_months=tenor, region=region, currency=currency,
                published=published, reason=reason, n_contributors=n, n_quotes=len(members),
                kinds=dict(sorted(kinds.items())) if published else {},
                median=round(median(prices), 4) if published else None,
                p25=round(_quantile(prices, 0.25), 4) if ranged else None,
                p75=round(_quantile(prices, 0.75), 4) if ranged else None,
            )
        )
    return out


def publishable_json(cells: list[ContributedCell], start: str, end: str) -> str:
    """The only thing that leaves the private store: aggregates and counts, no identities."""
    return json.dumps(
        {
            "window": {"from": start, "to": end},
            "note": (
                "Aggregates of contributed term prices, one price per contributor. A median"
                f" is published only with at least {MIN_CONTRIBUTORS} contributors and no"
                f" contributor above {int(MAX_CONTRIBUTOR_SHARE * 100)}% of the cell's GPU"
                f" volume; quartiles only with at least {MIN_CONTRIBUTORS_FOR_RANGE}."
                " Contributed cells"
                " are not reproducible from public data; they can be audited only against"
                " the private store."
            ),
            "cells": [
                {k: v for k, v in c.__dict__.items() if not (not c.published and k in (
                    "median", "p25", "p75", "kinds"))}
                for c in cells
            ],
        },
        indent=1,
        sort_keys=True,
    )
