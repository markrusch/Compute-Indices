# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Market-price statements: one series over one period, every session listed, verifiable.

A statement is the thing a public compute operator, an auditor or a tax adviser can attach
to a file when they have to show what a GPU-hour cost in the market over a period. It
carries every session in the period, including the ones that gapped, each with the digest
TCI published for that print, and a digest over the whole statement. Anyone holding the
file and a clone of the repository can check both with `python -m tci.run statement verify`.

WHY. EU State aid rules for research infrastructure require that the price charged for its
use "shall correspond to a market price" (Regulation 651/2014, Art. 26(6)). EuroHPC's AI
Factories sell pay-per-use access to industry and publish no price. A market price for an
EU-sited on-demand GPU-hour is exactly what the headline measures, and a statement turns a
daily series into evidence a file can hold.

WHAT IT DOES NOT CLAIM. It states what the series printed. It is not a valuation, it
says nothing about any particular contract, and whether a statement meets a legal test is
for whoever relies on it to decide. The series is the on-demand posted price for small
blocks of GPUs; a multi-year commitment for a large cluster is a different product, and
the statement says so.

NO GAP IS FILLED. A session that gapped is listed with its reason and left out of every
average; a date the index did not compute at all is listed as such. The averages state how
many sessions they cover.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date as date_type
from datetime import timedelta
from pathlib import Path
from statistics import mean, median
from typing import Any

from tci import config
from tci.reproduce import print_digest
from tci.series_read import head_print

KIND = "tci-market-price-statement"
FORMAT = 1
REPO_URL = "https://github.com/markrusch/Compute-Indices"
SITE_URL = "https://thecomputeindices.com"
# Series a statement may be issued for: those computed from offers, with their own audit
# set. A basis or a smoothed series is derived from other prints and is stated from those.
STATED_SERIES = ("EU-CRI-H100", "EU-CRI-H100-SPOT", "EU-CRI-H100-US", "EU-CRI-H100-GLOBAL",
                 "EU-CRI-H100-NC", "EU-CRI-H200", "EU-CRI-B200", "EU-CRI-A100")
PUBLISHED_SERIES = ("EU-CRI-H100", "EU-CRI-H100-SPOT")

DISCLAIMER = (
    "This statement reports what a TCI series printed over the period. It is not a"
    " valuation, an offer or legal or tax advice, and it says nothing about the terms of any"
    " particular contract. TCI is a research publication and is not administered as a"
    " benchmark for use in financial instruments."
)


class StatementError(ValueError):
    pass


def _published_name(series: str) -> str:
    return series.replace("EU-CRI", "TCI-CRI", 1)


def _days(start: str, end: str) -> list[str]:
    a, b = date_type.fromisoformat(start), date_type.fromisoformat(end)
    if b < a:
        raise StatementError("the period ends before it starts")
    return [(a + timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


def _class_of(series: str, factors: config.Factors) -> str:
    from tci.commands import SERIES_BY_CLASS

    rs = factors.regional_series.get(series)
    if rs is not None:
        return rs.model_class
    by_series = {v: k for k, v in SERIES_BY_CLASS.items()}
    return by_series.get(series, factors.headline_class)


def _what(series: str, factors: config.Factors) -> str:
    cls = _class_of(series, factors)
    variant = factors.model_classes[cls].reference_variant if cls in factors.model_classes \
        else factors.reference_unit.gpu_model
    gpu = variant.replace("_", " ")
    if series == "EU-CRI-H100-SPOT":
        kind = "the price sellers publish for interruptible (spot) capacity"
    else:
        kind = "the on-demand price sellers publish, with no term commitment"
    where = {
        "EU-CRI-H100-US": "located in the United States",
        "EU-CRI-H100-GLOBAL": "located in any country",
    }.get(series, "located in the EU/EEA")
    return (
        f"One GPU-hour of an NVIDIA {gpu}, {where}, "
        f"in nodes of {factors.filters.min_gpu_count} GPUs or more: {kind}, as a weighted "
        "median over sellers' qualifying offers, per GPU-hour, excluding VAT. It is not "
        "the price of a multi-year commitment for a large cluster."
    )


def _canonical(payload: dict[str, Any]) -> bytes:
    body = {k: v for k, v in payload.items() if k != "digest"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def digest_of(payload: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(payload)).hexdigest()


def _summary(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    printed = [s for s in sessions if s["value_usd"] is not None]

    def stats(key: str) -> dict[str, float] | None:
        vals = [float(s[key]) for s in printed if s[key] is not None]
        if not vals:
            return None
        return {"mean": round(mean(vals), 4), "median": round(median(vals), 4),
                "min": round(min(vals), 4), "max": round(max(vals), 4)}

    months: dict[str, list[dict[str, Any]]] = {}
    for s in sessions:
        months.setdefault(s["date"][:7], []).append(s)
    return {
        "calendar_days": len(sessions),
        "published": len(printed),
        "gapped": sum(1 for s in sessions if s["status"] == "gap"),
        "not_computed": sum(1 for s in sessions if s["status"] == "not_computed"),
        "eur": stats("value_eur"),
        "usd": stats("value_usd"),
        "months": [
            {
                "month": m,
                "calendar_days": len(ss),
                "published": sum(1 for s in ss if s["value_usd"] is not None),
                "mean_eur": (round(mean(v), 4) if (v := [float(s["value_eur"]) for s in ss
                             if s["value_eur"] is not None]) else None),
                "mean_usd": (round(mean(v2), 4) if (v2 := [float(s["value_usd"]) for s in ss
                             if s["value_usd"] is not None]) else None),
            }
            for m, ss in sorted(months.items())
        ],
    }


def build(conn: sqlite3.Connection, series: str, start: str, end: str) -> dict[str, Any]:
    """The statement for `series` over [start, end], every calendar day listed."""
    if series not in STATED_SERIES:
        raise StatementError(f"no statement is issued for {series}; one of {STATED_SERIES}")
    conn.row_factory = sqlite3.Row
    sessions: list[dict[str, Any]] = []
    versions: set[str] = set()
    for d in _days(start, end):
        head = head_print(conn, series, d)
        if head is None:
            sessions.append({"date": d, "status": "not_computed", "value_usd": None,
                             "value_eur": None, "fx_rate": None, "fx_date": None,
                             "revision": None, "methodology_version": None, "n_sources": None,
                             "flags": "", "digest": None})
            continue
        pd = print_digest(conn, d, series)
        versions.add(str(head["methodology_version"]))
        sessions.append({
            "date": d,
            "status": "published" if head["value_usd"] is not None else "gap",
            "value_usd": head["value_usd"],
            "value_eur": head["value_eur"],
            "fx_rate": head["fx_rate"],
            "fx_date": head["fx_date"],
            "revision": int(head["revision"]),
            "methodology_version": head["methodology_version"],
            "n_sources": int(head["n_sources"]),
            "flags": head["flags"] or "",
            "digest": pd[1] if pd else None,
        })
    factors = config.load_factors(for_date=end)
    payload: dict[str, Any] = {
        "kind": KIND,
        "format": FORMAT,
        "series": _published_name(series),
        "series_key": series,
        "period": {"from": start, "to": end},
        "measures": _what(series, factors),
        "currency_note": (
            "EUR values are the USD print converted at the ECB reference rate the print"
            " recorded (fx_rate, USD per EUR, published on fx_date)."
        ),
        "summary": _summary(sessions),
        "methodology_versions": sorted(versions),
        "sessions": sessions,
        "verify": {
            "repository": REPO_URL,
            "statement": "python -m tci.run statement verify --file <this file>",
            "prints": "python -m tci.run reproduce --published",
            "print_files": f"{SITE_URL}/data/prints/<date>.json",
        },
        "disclaimer": DISCLAIMER,
    }
    payload["digest"] = digest_of(payload)
    return payload


@dataclass(frozen=True)
class Check:
    ok: bool
    problems: tuple[str, ...]


def verify(conn: sqlite3.Connection, payload: dict[str, Any]) -> Check:
    """Check a statement against itself and against the record it claims to come from.

    Three things are checked: the statement digest matches its content, every session's
    print digest matches the database, and the summary is what the sessions add up to.
    A statement issued before a correction fails the second check for the corrected date,
    which is the point: it no longer describes the record.
    """
    problems: list[str] = []
    if payload.get("kind") != KIND:
        return Check(False, ("not a TCI market-price statement",))
    if payload.get("digest") != digest_of(payload):
        problems.append("the statement's digest does not match its content")
    series = str(payload.get("series_key"))
    for s in payload.get("sessions", []):
        if s.get("status") == "not_computed":
            if head_print(conn, series, s["date"]) is not None:
                problems.append(f"{s['date']}: listed as not computed, but a print exists")
            continue
        pd = print_digest(conn, s["date"], series)
        head = head_print(conn, series, s["date"])
        if pd is None or head is None:
            problems.append(f"{s['date']}: no print in the database")
            continue
        if pd[1] != s.get("digest"):
            problems.append(f"{s['date']}: print digest differs from the database")
        # The digest above vouches for the print, not for the numbers this file repeats
        # beside it; those are checked against the same row.
        for key in ("value_usd", "value_eur", "fx_rate", "revision", "methodology_version"):
            if head[key] != s.get(key):
                problems.append(f"{s['date']}: {key} differs from the database")
    if payload.get("summary") != _summary(list(payload.get("sessions", []))):
        problems.append("the summary is not what the listed sessions add up to")
    return Check(not problems, tuple(problems))


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def markdown(payload: dict[str, Any]) -> str:
    """A human-readable rendering. The JSON is the statement; this is a cover sheet."""
    s = payload["summary"]
    eur = s["eur"] or {}
    lines = [
        f"# Market-price statement: {payload['series']}",
        "",
        f"Period {payload['period']['from']} to {payload['period']['to']}."
        f" Digest `{payload['digest']}`.",
        "",
        payload["measures"],
        "",
        f"{s['published']} of {s['calendar_days']} calendar days published a value;"
        f" {_count(s['gapped'], 'gapped', 'gapped')} and"
        f" {_count(s['not_computed'], 'was not computed', 'were not computed')}. Gaps are"
        " listed and left out of every average.",
        "",
    ]
    if eur:
        lines += [
            "| | EUR per GPU-hour |", "|---|---|",
            f"| Mean of published sessions | {eur['mean']:.4f} |",
            f"| Median | {eur['median']:.4f} |",
            f"| Lowest | {eur['min']:.4f} |",
            f"| Highest | {eur['max']:.4f} |", "",
        ]
    lines += ["| Date | Status | USD | EUR | Methodology | Flags |", "|---|---|---|---|---|---|"]
    for x in payload["sessions"]:
        usd = f"{x['value_usd']:.4f}" if x["value_usd"] is not None else "—"
        e = f"{x['value_eur']:.4f}" if x["value_eur"] is not None else "—"
        lines.append(f"| {x['date']} | {x['status']} | {usd} | {e} |"
                     f" {x['methodology_version'] or '—'} | {x['flags'] or ''} |")
    lines += ["", f"Verify: `{payload['verify']['statement']}` in a clone of"
              f" {payload['verify']['repository']}.", "", payload["disclaimer"], ""]
    return "\n".join(lines)


def completed_months(conn: sqlite3.Connection, series: str, through: str) -> list[str]:
    """YYYY-MM months that ended before `through` and hold at least one print."""
    first = conn.execute(
        "SELECT MIN(date) FROM daily_index WHERE series = ?", (series,)
    ).fetchone()[0]
    if first is None:
        return []
    out: list[str] = []
    y, m = int(first[:4]), int(first[5:7])
    while True:
        month = f"{y:04d}-{m:02d}"
        nxt = date_type(y + (m == 12), (m % 12) + 1, 1)
        if nxt.isoformat() > through:
            break
        out.append(month)
        y, m = nxt.year, nxt.month
    return out


def month_bounds(month: str) -> tuple[str, str]:
    y, m = int(month[:4]), int(month[5:7])
    nxt = date_type(y + (m == 12), (m % 12) + 1, 1)
    return f"{month}-01", (nxt - timedelta(days=1)).isoformat()


def write_monthly(conn: sqlite3.Connection, out_dir: Path, through: str) -> list[Path]:
    """One statement per completed month per published series, and an index of them."""
    written: list[Path] = []
    index: list[dict[str, Any]] = []
    for series in PUBLISHED_SERIES:
        for month in completed_months(conn, series, through):
            start, end = month_bounds(month)
            payload = build(conn, series, start, end)
            path = out_dir / month / f"{_published_name(series)}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n",
                            encoding="utf-8", newline="\n")
            written.append(path)
            s = payload["summary"]
            index.append({
                "series": payload["series"], "month": month,
                "path": f"data/statements/{month}/{path.name}",
                "published": s["published"], "calendar_days": s["calendar_days"],
                "mean_eur": (s["eur"] or {}).get("mean"), "digest": payload["digest"],
            })
    idx = out_dir / "index.json"
    idx.parent.mkdir(parents=True, exist_ok=True)
    idx.write_text(json.dumps({"statements": index}, indent=1, sort_keys=True) + "\n",
                   encoding="utf-8", newline="\n")
    written.append(idx)
    return written


def read(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
