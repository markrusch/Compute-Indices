# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""The EU term panel: weekly assessments of standard term contracts, aggregated privately.

WHY A PANEL. CoreWeave reported 98% of its first-half 2026 revenue under committed
contracts (10-Q for the period to 30 June 2026). The term price is where the volume is,
and no public source has it: as of September 2026 no committed tenor
of any GPU is published by three or more sellers (tci.term). Contributed prices
(tci.contrib) wait for parties to volunteer deals; a panel asks named market participants
for a judgement every week about a fixed contract. Freight has priced its time charters
that way for decades. The Baltic Exchange's panellists are brokers active in the routes
they assess, who submit in a fixed window and may not trade derivatives on the result.

WHAT IS ASSESSED. A cell (config/term_panel.yaml) is one standard contract: GPU, region,
tenor and block size. Each panellist states, once per ISO week, the range in which a deal
of exactly that specification would be agreed, and what the range rests on: deals done,
firm quotes, indicative quotes, or judgement.

RULES, ENFORCED HERE, NOT PROMISED:

- PRIVATE. Assessments live in the same private store as contributed prices, outside the
  repository (tci.contrib.private_db_path refuses a path inside it). Nothing that leaves
  this module names a panellist or carries one panellist's numbers.
- APPEND-ONLY. Triggers forbid UPDATE and DELETE, on assessments and on the register of
  panellists. A correction is a new row naming the one it replaces.
- ONE VOICE PER PANELLIST PER CELL PER WEEK. A second assessment in the same window must
  supersede the first.
- THE WINDOW IS FIXED. An assessment received after the window's cutoff is stored and not
  counted, and neither is a correction received after it: a late correction cannot
  withdraw an assessment that counted. No week is aggregated before its cutoff, so nobody
  can see one week's number and then shape their input to it.
- THREE OR NO VALUE. Below three panellists a cell is a count. At three or four the value
  is the mean of the mids; from five, the highest and lowest mids are dropped and the rest
  averaged. Not a median at three: the median of three mids IS one panellist's mid,
  published to the cent. And no percentile at any size: at five the 25th and 75th
  percentiles are exactly the second and fourth mids, and at nine, thirteen and so on
  they land on single mids again.
- A SUSPENSION REACHES BACK. Every assessment a panellist made in a week that opened
  before its suspension was recorded is left out, for good: being reinstated, or leaving,
  does not bring them back. Weeks that open after a reinstatement count as normal.
- NOT A SELLERS' CLUB. A cell where sellers are half the panellists or more publishes no
  value. Competing sellers pooling current prices is the arrangement competition law is
  most wary of; brokers, marketplaces and buyers are the panel this is designed for.

The output is research. Nothing here enters an index print.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from datetime import date as date_type
from pathlib import Path
from statistics import mean
from typing import Any

from tci import config
from tci.contrib import private_db_path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "term_panel.yaml"

KINDS = ("broker", "marketplace", "seller", "buyer")
STATUSES = ("active", "suspended", "left")
BASES = ("transacted", "firm_quote", "indicative", "judgement")
CURRENCIES = ("USD", "EUR")
FIELDS = ("window", "cell", "low", "high", "currency", "basis", "deals_seen", "notes")

SCHEMA = """
CREATE TABLE IF NOT EXISTS panellists (
  id INTEGER PRIMARY KEY,
  recorded_utc TEXT NOT NULL,
  panellist TEXT NOT NULL,            -- pseudonym; the key to a firm's name is kept offline
  kind TEXT NOT NULL CHECK (kind IN ('broker','marketplace','seller','buyer')),
  status TEXT NOT NULL CHECK (status IN ('active','suspended','left')),
  conflicts TEXT,                     -- the panellist's own declaration, verbatim
  agreement_sha256 TEXT               -- digest of the signed panellist agreement
);
CREATE TABLE IF NOT EXISTS assessments (
  id INTEGER PRIMARY KEY,
  received_utc TEXT NOT NULL,
  panellist TEXT NOT NULL,
  window TEXT NOT NULL,               -- ISO week, e.g. 2026-W42
  cell TEXT NOT NULL,
  low REAL NOT NULL,
  high REAL NOT NULL,
  currency TEXT NOT NULL CHECK (currency IN ('USD','EUR')),
  basis TEXT NOT NULL CHECK (basis IN ('transacted','firm_quote','indicative','judgement')),
  deals_seen INTEGER,
  notes TEXT,
  supersedes INTEGER REFERENCES assessments(id),
  source_sha256 TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS panellists_no_update BEFORE UPDATE ON panellists
  BEGIN SELECT RAISE(ABORT, 'the panel register is append-only'); END;
CREATE TRIGGER IF NOT EXISTS panellists_no_delete BEFORE DELETE ON panellists
  BEGIN SELECT RAISE(ABORT, 'the panel register is append-only'); END;
CREATE TRIGGER IF NOT EXISTS assessments_no_update BEFORE UPDATE ON assessments
  BEGIN SELECT RAISE(ABORT, 'assessments are immutable'); END;
CREATE TRIGGER IF NOT EXISTS assessments_no_delete BEFORE DELETE ON assessments
  BEGIN SELECT RAISE(ABORT, 'assessments are immutable'); END;
"""


class PanelError(ValueError):
    """A submission or register entry that breaks a rule. The message names the rule."""


# ------------------------------------------------------------------------- configuration

@dataclass(frozen=True)
class Cell:
    id: str
    gpu_model: str
    region: str
    tenor_months: int
    gpus: tuple[int, int]
    description: str


@dataclass(frozen=True)
class Rules:
    min_panellists: int
    trim_from: int
    max_seller_share: float
    max_range_ratio: float
    price_band: tuple[float, float]
    cutoff_weekday: int  # ISO weekday, 1 = Monday
    cutoff_hour_utc: int


@dataclass(frozen=True)
class PanelConfig:
    version: str
    cells: tuple[Cell, ...]
    rules: Rules

    def cell(self, cell_id: str) -> Cell | None:
        return next((c for c in self.cells if c.id == cell_id), None)


def load_config(path: Path | None = None) -> PanelConfig:
    raw = config.read_yaml(path or CONFIG_PATH)
    r, w = raw["rules"], raw["window"]
    return PanelConfig(
        version=str(raw["version"]),
        cells=tuple(
            Cell(id=str(c["id"]), gpu_model=str(c["gpu_model"]), region=str(c["region"]),
                 tenor_months=int(c["tenor_months"]),
                 gpus=(int(c["gpus"][0]), int(c["gpus"][1])),
                 description=" ".join(str(c["description"]).split()))
            for c in raw["cells"]
        ),
        rules=Rules(
            min_panellists=int(r["min_panellists"]), trim_from=int(r["trim_from"]),
            max_seller_share=float(r["max_seller_share"]),
            max_range_ratio=float(r["max_range_ratio"]),
            price_band=(float(r["price_band"][0]), float(r["price_band"][1])),
            cutoff_weekday=int(w["cutoff_weekday"]), cutoff_hour_utc=int(w["cutoff_hour_utc"]),
        ),
    )


# ------------------------------------------------------------------------------- windows

def _monday(window: str) -> date_type:
    try:
        year, week = window.strip().split("-W")
        return date_type.fromisocalendar(int(year), int(week), 1)
    except (ValueError, AttributeError) as exc:
        raise PanelError(f"window must be an ISO week like 2026-W42, got {window!r}") from exc


def normalise_window(window: str) -> str:
    """One spelling per week. Stored raw, "2027-W1" and "2027-W01" were two windows: three
    on-time panellists split into three cells of one, and one panellist could file twice
    for the same week under two spellings."""
    y, w, _ = _monday(window).isocalendar()
    return f"{y}-W{w:02d}"


def window_bounds(window: str, rules: Rules) -> tuple[str, str]:
    """(opens, cutoff) as ISO UTC timestamps for an ISO week like 2026-W42."""
    monday = _monday(window)
    cutoff_day = monday + timedelta(days=rules.cutoff_weekday - 1)
    return (f"{monday.isoformat()}T00:00:00Z",
            f"{cutoff_day.isoformat()}T{rules.cutoff_hour_utc:02d}:00:00Z")


def window_of(day: str) -> str:
    y, w, _ = date_type.fromisoformat(day).isocalendar()
    return f"{y}-W{w:02d}"


# ------------------------------------------------------------------------------- the store

def connect_private(private_dir: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(private_db_path(private_dir))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _pseudonym(name: str) -> str:
    if not name or not name.replace("-", "").isalnum():
        raise PanelError("a panellist is recorded under a pseudonym of letters, digits and '-'")
    return name


def register(conn: sqlite3.Connection, panellist: str, kind: str, status: str,
             recorded_utc: str, conflicts: str | None = None,
             agreement_sha256: str | None = None) -> None:
    """Append a status entry. The latest entry for a pseudonym is its current status."""
    _pseudonym(panellist)
    if kind not in KINDS:
        raise PanelError(f"kind must be one of {KINDS}")
    if status not in STATUSES:
        raise PanelError(f"status must be one of {STATUSES}")
    if status == "active" and not agreement_sha256:
        raise PanelError("a panellist becomes active only with a signed agreement on file")
    with conn:
        conn.execute(
            "INSERT INTO panellists (recorded_utc, panellist, kind, status, conflicts,"
            " agreement_sha256) VALUES (?, ?, ?, ?, ?, ?)",
            (recorded_utc, panellist, kind, status, conflicts, agreement_sha256),
        )


def standing(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    """pseudonym -> its latest register entry."""
    out: dict[str, sqlite3.Row] = {}
    for r in conn.execute("SELECT * FROM panellists ORDER BY id"):
        out[r["panellist"]] = r
    return out


# ------------------------------------------------------------------------- submissions

@dataclass(frozen=True)
class Assessment:
    window: str
    cell: str
    low: float
    high: float
    currency: str
    basis: str
    deals_seen: int | None = None
    notes: str | None = None


def parse_row(raw: dict[str, str], line: int, cfg: PanelConfig) -> Assessment:
    where = f"line {line}"
    missing = [f for f in FIELDS[:6] if not str(raw.get(f, "")).strip()]
    if missing:
        raise PanelError(f"{where}: missing {', '.join(missing)}")
    window = normalise_window(raw["window"])
    cell_id = raw["cell"].strip()
    if cfg.cell(cell_id) is None:
        raise PanelError(f"{where}: unknown cell {cell_id!r}; cells are "
                         f"{', '.join(c.id for c in cfg.cells)}")
    try:
        low, high = float(raw["low"]), float(raw["high"])
    except ValueError as exc:
        raise PanelError(f"{where}: low and high must be numbers") from exc
    lo_band, hi_band = cfg.rules.price_band
    if not (lo_band <= low <= high <= hi_band):
        raise PanelError(f"{where}: need {lo_band} <= low <= high <= {hi_band} per GPU-hour")
    if high / low > cfg.rules.max_range_ratio:
        raise PanelError(
            f"{where}: a range from {low} to {high} is wider than"
            f" {cfg.rules.max_range_ratio:.1f}x; narrow it or leave the cell blank"
        )
    currency = raw["currency"].strip().upper()
    if currency not in CURRENCIES:
        raise PanelError(f"{where}: currency must be USD or EUR")
    basis = raw["basis"].strip()
    if basis not in BASES:
        raise PanelError(f"{where}: basis must be one of {BASES}")
    deals = (raw.get("deals_seen") or "").strip()
    try:
        deals_seen = int(deals) if deals else None
    except ValueError as exc:
        raise PanelError(f"{where}: deals_seen must be a whole number") from exc
    if deals_seen is not None and deals_seen < 0:
        raise PanelError(f"{where}: deals_seen cannot be negative")
    if basis == "transacted" and not deals_seen:
        raise PanelError(f"{where}: a transacted basis needs deals_seen of at least 1")
    notes = (raw.get("notes") or "").strip()
    if "EXAMPLE ROW" in notes.upper():
        raise PanelError(f"{where}: this is the template's example row; delete it")
    return Assessment(window, cell_id, low, high, currency, basis, deals_seen, notes or None)


def read_file(path: Path, cfg: PanelConfig | None = None) -> tuple[list[Assessment], str]:
    """Parse a submitted CSV. All-or-nothing: one bad row rejects the file."""
    cfg = cfg or load_config()
    blob = path.read_bytes()
    reader = csv.DictReader(blob.decode("utf-8-sig").splitlines())
    header = [h.strip() for h in (reader.fieldnames or [])]
    unknown = [h for h in header if h not in FIELDS]
    if unknown:
        raise PanelError(f"unknown columns: {', '.join(unknown)}")
    rows = [parse_row({k.strip(): (v or "") for k, v in r.items()}, i + 2, cfg)
            for i, r in enumerate(reader)]
    if not rows:
        raise PanelError("the file has no rows")
    seen: set[tuple[str, str]] = set()
    for a in rows:
        if (a.window, a.cell) in seen:
            raise PanelError(f"two rows for {a.cell} in {a.window}; send one per cell")
        seen.add((a.window, a.cell))
    return rows, hashlib.sha256(blob).hexdigest()


def ingest(conn: sqlite3.Connection, panellist: str, rows: list[Assessment],
           source_sha256: str, received_utc: str, supersedes: int | None = None) -> int:
    """Store validated assessments from an active panellist."""
    _pseudonym(panellist)
    rows = [replace(a, window=normalise_window(a.window)) for a in rows]
    entry = standing(conn).get(panellist)
    if entry is None or entry["status"] != "active":
        raise PanelError(f"{panellist} is not an active panellist")
    if supersedes is not None:
        if len(rows) != 1:
            raise PanelError("a correction file must hold exactly one row")
        old = conn.execute("SELECT * FROM assessments WHERE id = ?", (supersedes,)).fetchone()
        if old is None:
            raise PanelError(f"no assessment with id {supersedes}")
        if old["panellist"] != panellist:
            raise PanelError("an assessment can be corrected only by the panellist who sent it")
        if (old["window"], old["cell"]) != (rows[0].window, rows[0].cell):
            raise PanelError("a correction must name the same window and cell")
        if conn.execute("SELECT 1 FROM assessments WHERE supersedes = ?",
                        (supersedes,)).fetchone():
            raise PanelError(f"assessment {supersedes} is already superseded")
    else:
        for a in rows:
            if _live(conn, panellist, a.window, a.cell):
                raise PanelError(
                    f"{panellist} already assessed {a.cell} in {a.window}; send a correction"
                    " that supersedes it"
                )
    with conn:
        conn.executemany(
            "INSERT INTO assessments (received_utc, panellist, window, cell, low, high,"
            " currency, basis, deals_seen, notes, supersedes, source_sha256)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(received_utc, panellist, a.window, a.cell, a.low, a.high, a.currency, a.basis,
              a.deals_seen, a.notes, supersedes, source_sha256) for a in rows],
        )
    return len(rows)


def _live(conn: sqlite3.Connection, panellist: str, window: str, cell: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM assessments WHERE panellist = ? AND window = ? AND cell = ?"
        " AND id NOT IN (SELECT supersedes FROM assessments WHERE supersedes IS NOT NULL)",
        (panellist, window, cell),
    ).fetchone() is not None


# --------------------------------------------------------------------------- aggregation

@dataclass(frozen=True)
class PanelCell:
    id: str
    description: str
    window: str
    panellists: int
    late: int
    published: bool
    reason: str | None
    value_usd: float | None = None
    value_eur: float | None = None
    method: str | None = None
    bases: dict[str, int] = field(default_factory=dict)
    kinds: dict[str, int] = field(default_factory=dict)


def aggregate(conn: sqlite3.Connection, window: str, fx_usd_per_eur: float | None,
              cfg: PanelConfig | None = None, now_utc: str | None = None) -> list[PanelCell]:
    """Every configured cell for one window: a value where the rules allow, a count where not.

    `fx_usd_per_eur` converts EUR assessments to USD; the caller passes the ECB rate dated
    strictly before the cutoff day, so the result does not depend on when this runs (the
    defect v0.5.0 fixed for prints). Without it, a cell with any EUR input publishes no
    value rather than guess. Refuses a window whose cutoff is after `now_utc`.
    """
    cfg = cfg or load_config()
    window = normalise_window(window)
    opens, cutoff = window_bounds(window, cfg.rules)
    now = now_utc or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if now < cutoff:
        raise PanelError(f"{window} closes at {cutoff}; no value exists before then")
    reg = standing(conn)
    rows = conn.execute("SELECT * FROM assessments WHERE window = ? ORDER BY id",
                        (window,)).fetchall()
    # Keyed on the register's history, not on current status: keyed on status, a
    # suspended panellist recorded as "left" or reinstated had every assessment counted
    # again, the weeks that led to the suspension included.
    suspended_at = {
        r["panellist"] for r in conn.execute(
            "SELECT panellist FROM panellists WHERE status = 'suspended' AND recorded_utc > ?",
            (opens,),
        )
    }
    rows = [r for r in rows if r["panellist"] not in suspended_at]
    # Lateness first, supersession second, and only among rows that were on time.
    # The other order let a correction received after the cutoff withdraw an assessment
    # that had counted, turning a published cell back into a count on re-aggregation.
    on_time = [r for r in rows if opens <= r["received_utc"] <= cutoff]
    replaced = {r["supersedes"] for r in on_time if r["supersedes"] is not None}
    on_time = [r for r in on_time if r["id"] not in replaced]
    late_rows = [r for r in rows if not opens <= r["received_utc"] <= cutoff]
    out: list[PanelCell] = []
    for cell in cfg.cells:
        live = [r for r in on_time if r["cell"] == cell.id]
        late = sum(1 for r in late_rows if r["cell"] == cell.id)
        mids: list[float] = []
        bases: dict[str, int] = {}
        kinds: dict[str, int] = {}
        missing_fx = False
        for r in live:
            mid = (float(r["low"]) + float(r["high"])) / 2.0
            if r["currency"] == "EUR":
                if fx_usd_per_eur is None:
                    missing_fx = True
                    continue
                mid *= fx_usd_per_eur
            mids.append(mid)
            bases[r["basis"]] = bases.get(r["basis"], 0) + 1
            kind = reg[r["panellist"]]["kind"] if r["panellist"] in reg else "unknown"
            kinds[kind] = kinds.get(kind, 0) + 1
        n = len(live)
        reason = None
        rules = cfg.rules
        if n < rules.min_panellists:
            reason = f"{n} of {rules.min_panellists} panellists needed"
        elif kinds.get("seller", 0) / n >= rules.max_seller_share:
            reason = "sellers are half the panellists or more"
        elif missing_fx:
            reason = "no exchange rate for a EUR assessment"
        if reason is not None:
            out.append(PanelCell(cell.id, cell.description, window, n, late, False, reason))
            continue
        ordered = sorted(mids)
        if n >= rules.trim_from:
            value, method = mean(ordered[1:-1]), "trimmed mean (highest and lowest mid dropped)"
        else:
            value, method = mean(ordered), "mean of mids"
        out.append(PanelCell(
            cell.id, cell.description, window, n, late, True, None,
            value_usd=round(value, 2),
            value_eur=round(value / fx_usd_per_eur, 2) if fx_usd_per_eur else None,
            method=method, bases=dict(sorted(bases.items())), kinds=dict(sorted(kinds.items())),
        ))
    return out


def publishable(cells: list[PanelCell], window: str, cfg: PanelConfig | None = None,
                generated_utc: str | None = None) -> dict[str, Any]:
    """The only thing that leaves the private store: values, counts and rules."""
    cfg = cfg or load_config()
    r = cfg.rules
    return {
        "window": window,
        "generated_utc": generated_utc or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "config_version": cfg.version,
        "rules": {
            "min_panellists": r.min_panellists, "trim_from": r.trim_from,
            "max_seller_share": r.max_seller_share,
        },
        "note": (
            "Weekly panel assessments of standard term contracts. A value is published only"
            f" with at least {r.min_panellists} panellists, sellers fewer than half of them,"
            " and every assessment received inside the window. Panel values are judgements"
            " and quotes from named market participants, recorded privately; they are not"
            " reproducible from public data and can be audited only against the private"
            " store."
        ),
        "cells": [
            {k: v for k, v in c.__dict__.items()
             if c.published or k not in ("value_usd", "value_eur",
                                         "method", "bases", "kinds")}
            for c in cells
        ],
    }


def empty(cfg: PanelConfig | None = None) -> dict[str, Any]:
    """What the site shows before any window has been aggregated: every cell, no panel."""
    cfg = cfg or load_config()
    return {
        "window": None,
        "config_version": cfg.version,
        "rules": {"min_panellists": cfg.rules.min_panellists},
        "cells": [
            {"id": c.id, "description": c.description, "panellists": 0, "published": False,
             "reason": f"0 of {cfg.rules.min_panellists} panellists needed"}
            for c in cfg.cells
        ],
    }


def to_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=1, sort_keys=True)
