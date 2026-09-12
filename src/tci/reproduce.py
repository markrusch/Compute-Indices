# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Recompute published prints from stored observations and check them, end to end.

Two links in the chain are checked, and a reader can run both from a clone:

1. observations -> prints. Every stored print is recomputed from the stored observations
   under the methodology version live on its date (config/methodology/succession.yaml),
   on an in-memory copy of the database, and compared field by field with the latest
   stored revision: value, EUR companion, FX, provider and executable counts, flags, the
   version recorded, and the full constituent audit set.
2. prints -> publication. Every per-date file under site/data/prints/ and the series in
   site/data/latest.json carry a digest of the print they publish. The digest is
   recomputed from the database and compared.

Nothing here is in the calculation path. It reads the database and runs the calculation
code; it never writes to data/eucri.db.

The retired series EU-CRI-H100-CLOUD (last computed under 0.2.0-dev, retired in v0.3.0)
is reported as RETIRED rather than MATCH: the code that computed it no longer exists, and
claiming to reproduce it would be a claim nobody checked.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
PRINTS_DIR = REPO_ROOT / "site" / "data" / "prints"
LATEST_PATH = REPO_ROOT / "site" / "data" / "latest.json"

RETIRED_SERIES = frozenset({"EU-CRI-H100-CLOUD"})
IGNORED_FLAGS = frozenset({"correction"})  # added by a recomputation itself


@dataclass
class Result:
    date: str
    series: str
    status: str  # MATCH | MISMATCH | RETIRED | MISSING
    detail: str = ""
    published: float | None = None
    derived: float | None = None


@dataclass
class Report:
    results: list[Result] = field(default_factory=list)

    @property
    def mismatches(self) -> list[Result]:
        return [r for r in self.results if r.status in ("MISMATCH", "MISSING")]

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.results:
            out[r.status] = out.get(r.status, 0) + 1
        return out


# ---------------------------------------------------------------------------- digests

def _num(x: float | None) -> str | None:
    return None if x is None else format(float(x), ".6f")


def canonical_print(head: sqlite3.Row | dict, constituents: list) -> dict[str, Any]:
    """The published content of one print, in a form that hashes the same everywhere.

    Numbers are fixed to six decimals as strings, so the digest cannot depend on how a
    platform prints a float. Constituents are sorted by provider, then source.
    """
    cons = sorted(
        (
            {
                "provider": c["provider"],
                "source": c["source"],
                "tier": c["tier"],
                "price_usd": _num(c["price_usd"]),
                "weight": _num(c["weight"]),
                "included": bool(c["included"]),
                "exclusion_reason": c["exclusion_reason"],
                "flags": c["flags"] or "",
            }
            for c in constituents
        ),
        key=lambda c: (c["provider"], c["source"] or ""),
    )
    return {
        "date": head["date"],
        "series": head["series"],
        "revision": int(head["revision"]),
        "value_usd": _num(head["value_usd"]),
        "value_eur": _num(head["value_eur"]),
        "fx_rate": _num(head["fx_rate"]),
        "fx_date": head["fx_date"],
        "n_sources": int(head["n_sources"]),
        "n_executable": int(head["n_executable"]),
        "flags": head["flags"] or "",
        "methodology_version": head["methodology_version"],
        "constituents": cons,
    }


def digest(canonical: dict[str, Any]) -> str:
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(blob.encode("ascii")).hexdigest()


def latest_revision(conn: sqlite3.Connection, date: str, series: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM daily_index WHERE date = ? AND series = ?"
        " ORDER BY revision DESC LIMIT 1",
        (date, series),
    ).fetchone()


def constituents_of(conn: sqlite3.Connection, date: str, series: str, revision: int) -> list:
    return conn.execute(
        "SELECT * FROM constituents WHERE date = ? AND series = ? AND revision = ?",
        (date, series, revision),
    ).fetchall()


def print_digest(conn: sqlite3.Connection, date: str, series: str) -> tuple[dict, str] | None:
    head = latest_revision(conn, date, series)
    if head is None:
        return None
    canon = canonical_print(head, constituents_of(conn, date, series, head["revision"]))
    return canon, digest(canon)


# ------------------------------------------------------------------ observations -> prints

def _memory_copy(conn: sqlite3.Connection) -> sqlite3.Connection:
    mem = sqlite3.connect(":memory:")
    conn.backup(mem)
    mem.row_factory = sqlite3.Row
    return mem


def _comparable(canon: dict[str, Any]) -> dict[str, Any]:
    out = dict(canon)
    out.pop("revision")
    out["flags"] = ",".join(
        sorted(f for f in (canon["flags"] or "").split(",") if f and f not in IGNORED_FLAGS)
    )
    return out


def _diff(a: dict[str, Any], b: dict[str, Any]) -> str:
    keys = [k for k in a if a.get(k) != b.get(k)]
    parts = []
    for k in keys:
        if k == "constituents":
            parts.append("constituents differ")
        else:
            parts.append(f"{k}: published {a.get(k)!r} derived {b.get(k)!r}")
    return "; ".join(parts)


def print_dates(conn: sqlite3.Connection, start: str | None, end: str | None) -> list[str]:
    q = "SELECT DISTINCT date FROM daily_index WHERE 1=1"
    args: list[str] = []
    if start:
        q += " AND date >= ?"
        args.append(start)
    if end:
        q += " AND date <= ?"
        args.append(end)
    return [r[0] for r in conn.execute(q + " ORDER BY date", args)]


def reproduce_prints(
    conn: sqlite3.Connection,
    start: str | None = None,
    end: str | None = None,
    series: str | None = None,
) -> Report:
    """Recompute every stored print in [start, end] and compare with what was stored."""
    from tci.commands import compute_all_series  # local: commands imports outputs lazily

    conn.row_factory = sqlite3.Row
    report = Report()
    mem = _memory_copy(conn)
    previous_level = logging.getLogger("tci").level
    logging.getLogger("tci").setLevel(logging.WARNING)
    try:
        for date in print_dates(conn, start, end):
            stored_series = [
                r[0] for r in conn.execute(
                    "SELECT DISTINCT series FROM daily_index WHERE date = ?", (date,)
                )
            ]
            before = {s: print_digest(conn, date, s) for s in stored_series}
            compute_all_series(mem, date, correction=True, fx_override=_recorded_fx(conn, date))
            for s in sorted(stored_series):
                if series and s != series:
                    continue
                published = before[s]
                assert published is not None
                pub_canon = published[0]
                if s in RETIRED_SERIES:
                    report.results.append(Result(
                        date, s, "RETIRED",
                        f"retired series, {pub_canon['methodology_version']}",
                    ))
                    continue
                derived = print_digest(mem, date, s)
                if derived is None or derived[0]["revision"] == pub_canon["revision"]:
                    report.results.append(Result(date, s, "MISSING",
                                                 "current code does not compute this series"))
                    continue
                a, b = _comparable(pub_canon), _comparable(derived[0])
                status = "MATCH" if a == b else "MISMATCH"
                report.results.append(
                    Result(date, s, status, "" if status == "MATCH" else _diff(a, b),
                           published=_float(pub_canon["value_usd"]),
                           derived=_float(derived[0]["value_usd"]))
                )
    finally:
        logging.getLogger("tci").setLevel(previous_level)
        mem.close()
    return report


def _recorded_fx(conn: sqlite3.Connection, date: str) -> tuple[float, str] | None:
    """The FX the day's prints were published with (an input recorded on each print)."""
    row = conn.execute(
        "SELECT fx_rate, fx_date FROM daily_index WHERE date = ? AND fx_rate IS NOT NULL"
        " ORDER BY revision DESC LIMIT 1",
        (date,),
    ).fetchone()
    return (row["fx_rate"], row["fx_date"]) if row else None


def _float(x: str | None) -> float | None:
    return None if x is None else float(x)


# -------------------------------------------------------------------- prints -> files

def check_published(
    conn: sqlite3.Connection,
    prints_dir: Path | None = None,
    latest_path: Path | None = None,
) -> Report:
    """Compare the digests in the published files with digests recomputed from the DB.

    Both directions are checked. Walking the files alone would pass a run in which the
    site was never regenerated: `cmd_daily` computes the print, stores it, and then calls
    output generation inside a try/except that logs and returns, so a crash in
    `webdata.generate` leaves the database a day ahead of `site/data/prints/` with the
    exit status still 0. Nothing in the file-side loop notices a date it was never handed,
    and `latest.json` keeps matching the older print it still names. So the database is
    enumerated too, and a stored print with no published file or no entry in its file is
    a MISSING — the site quietly falling behind the record is exactly the drift these
    digests exist to make impossible.
    """
    conn.row_factory = sqlite3.Row
    report = Report()
    pdir = prints_dir or PRINTS_DIR
    seen: set[tuple[str, str]] = set()
    for path in sorted(pdir.glob("????-??-??.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for s, entry in sorted(payload.get("series", {}).items()):
            seen.add((payload["date"], s))
            derived = print_digest(conn, payload["date"], s)
            if derived is None:
                report.results.append(
                    Result(payload["date"], s, "MISSING", f"{path.name}: no print in DB")
                )
            elif derived[1] != entry.get("digest"):
                report.results.append(Result(payload["date"], s, "MISMATCH",
                                             f"{path.name}: digest differs from the database"))
            else:
                report.results.append(Result(payload["date"], s, "MATCH", path.name))
    for date, s in _stored_prints(conn):
        if (date, s) not in seen:
            report.results.append(
                Result(date, s, "MISSING",
                       f"{date}.json: print is in the database but was never published")
            )
    lpath = latest_path or LATEST_PATH
    if lpath.exists():
        latest = json.loads(lpath.read_text(encoding="utf-8"))
        for s, entry in sorted((latest.get("series") or {}).items()):
            if "digest" not in entry:
                continue
            derived = print_digest(conn, entry["date"], s)
            ok = derived is not None and derived[1] == entry["digest"]
            report.results.append(Result(entry["date"], s, "MATCH" if ok else "MISMATCH",
                                         "latest.json"))
    return report


def _stored_prints(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """Every (date, series) that has a print in the database, in publication order."""
    return [
        (r["date"], r["series"])
        for r in conn.execute(
            "SELECT DISTINCT date, series FROM daily_index ORDER BY date, series"
        )
    ]


def print_report(report: Report, verbose: bool = False) -> None:
    for r in report.results:
        if verbose or r.status != "MATCH":
            extra = f"  {r.detail}" if r.detail else ""
            print(f"{r.date} {r.series:<22} {r.status}{extra}")
    counts = report.counts()
    summary = ", ".join(f"{n} {k}" for k, n in sorted(counts.items()))
    print(f"summary: {len(report.results)} checked: {summary}")
