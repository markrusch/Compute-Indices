# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""site/data/latest.json — the data contract for the static dashboard (site/index.html).

Never part of the calculation path: pure read-and-serialise of what daily_index,
constituents and weight_sets already hold. Fail-soft like charts/post (see
commands._maybe_outputs); the daily run must never fail because the dashboard export did.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from tci import DISCLAIMER, __version__
from tci.commands import COMPOSITE, SERIES_BY_CLASS
from tci.config import CONFIG_DIR, load_factors, load_static_providers
from tci.db import utc_now_iso
from tci.reproduce import PRINTS_DIR, canonical_print, constituents_of, digest, print_digest

log = logging.getLogger("tci.outputs.webdata")

REPO_ROOT = Path(__file__).resolve().parents[3]
OUT_PATH = REPO_ROOT / "site" / "data" / "latest.json"

# Every series the pipeline can publish. The dashboard renders a tile per sub-index and
# must be able to show a GAP honestly, so a series with no value still belongs in the
# snapshot — omitting it would make a gapped series indistinguishable from one that was
# never computed.
# EU-CRI-H100-CLOUD is deliberately absent: retired in v0.3.0 and no longer computed.
# Listing it here republished its final 0.2.0-dev value in latest.json indefinitely.
ALL_SERIES = [
    "EU-CRI-H100", "EU-CRI-H100-7D", "EU-CRI-H100-SOV", "EU-CRI-H100-MKT",
    "EU-CRI-H100-NC", "EU-CRI-H100-HS", "EU-CRI-H100-PCIE",
    "EU-CRI-H200", "EU-CRI-B200", "EU-CRI-B300", "EU-CRI-A100", COMPOSITE,
    # v0.6.0: the US reference leg and the EU-US basis (value = EU minus US, $/GPU-hr).
    "EU-CRI-H100-US", "EU-CRI-H100-BASIS-US",
]


def _latest_print(conn: sqlite3.Connection, series: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM daily_index WHERE series = ? ORDER BY date DESC, revision DESC LIMIT 1",
        (series,),
    ).fetchone()


def _value_on(conn: sqlite3.Connection, series: str, date: str) -> float | None:
    row = conn.execute(
        "SELECT value_usd FROM daily_index WHERE series = ? AND date <= ?"
        " AND value_usd IS NOT NULL ORDER BY date DESC, revision DESC LIMIT 1",
        (series, date),
    ).fetchone()
    return row["value_usd"] if row else None


def _prior_print(
    conn: sqlite3.Connection, series: str, before_date: str
) -> tuple[str, float] | None:
    """The most recent (date, value_usd) strictly before before_date, for the print-meta tile."""
    row = conn.execute(
        "SELECT date, value_usd FROM daily_index WHERE series = ? AND date < ?"
        " AND value_usd IS NOT NULL ORDER BY date DESC, revision DESC LIMIT 1",
        (series, before_date),
    ).fetchone()
    return (row["date"], row["value_usd"]) if row else None


def _pct(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return round((new - old) / old * 100.0, 4)


def _load_source_links() -> dict:
    path = CONFIG_DIR / "source_links.yaml"
    if not path.exists():
        return {"providers": {}, "collectors": {}}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def provider_links() -> dict[str, dict]:
    """provider -> {url, note}. Static-yaml providers' own url wins (authoritative)."""
    links = _load_source_links()
    out: dict[str, dict] = {
        name: {"url": entry.get("url"), "note": entry.get("note")}
        for name, entry in (links.get("providers") or {}).items()
    }
    for p in load_static_providers():
        if p.url:
            out[p.provider] = {"url": p.url, "note": p.config_notes or None}
    return out


def sources_panel() -> list[dict]:
    """Collector-level catalogue for the dashboard's Sources panel."""
    links = _load_source_links()
    return [
        {"source": name, "label": c.get("label"), "url": c.get("url"),
         "endpoint": c.get("endpoint")}
        for name, c in (links.get("collectors") or {}).items()
    ]


def _series_snapshot(conn: sqlite3.Connection) -> dict:
    out: dict = {}
    for series in ALL_SERIES:
        row = _latest_print(conn, series)
        if row is None:
            continue
        pd = print_digest(conn, row["date"], series)
        entry = {
            "date": row["date"],
            "revision": row["revision"],
            "digest": pd[1] if pd else None,
            "value_usd": row["value_usd"],
            "value_eur": row["value_eur"],
            "fx_rate": row["fx_rate"],
            "fx_date": row["fx_date"],
            "n_sources": row["n_sources"],
            "n_executable": row["n_executable"],
            "flags": row["flags"],
        }
        if row["value_usd"] is not None:
            d = datetime.strptime(row["date"], "%Y-%m-%d")
            wow_date = (d - timedelta(days=7)).strftime("%Y-%m-%d")
            mom_date = (d - timedelta(days=30)).strftime("%Y-%m-%d")
            entry["wow_pct"] = _pct(row["value_usd"], _value_on(conn, series, wow_date))
            entry["mom_pct"] = _pct(row["value_usd"], _value_on(conn, series, mom_date))
            prior = _prior_print(conn, series, row["date"])
            entry["prev_date"] = prior[0] if prior else None
            entry["prev_value_usd"] = prior[1] if prior else None
        out[series] = entry
    return out


def _constituents(conn: sqlite3.Connection, series: str, date: str) -> list[dict]:
    row = conn.execute(
        "SELECT MAX(revision) AS rev FROM daily_index WHERE date = ? AND series = ?",
        (date, series),
    ).fetchone()
    if row is None or row["rev"] is None:
        return []
    cons = conn.execute(
        "SELECT * FROM constituents WHERE date = ? AND series = ? AND revision = ?"
        " ORDER BY included DESC, price_usd",
        (date, series, row["rev"]),
    ).fetchall()
    links = provider_links()
    return [
        {
            "provider": c["provider"], "source": c["source"], "tier": c["tier"],
            "price_usd": c["price_usd"], "weight": round(c["weight"], 4),
            "included": bool(c["included"]), "exclusion_reason": c["exclusion_reason"],
            "flags": c["flags"],
            "url": (links.get(c["provider"]) or {}).get("url"),
            "note": (links.get(c["provider"]) or {}).get("note"),
        }
        for c in cons
    ]


def _weight_review(conn: sqlite3.Connection, on_date: str) -> dict | None:
    """Most recent review with effective_date <= on_date, all classes, latest revision."""
    row = conn.execute(
        "SELECT effective_date FROM weight_sets WHERE effective_date <= ?"
        " ORDER BY effective_date DESC LIMIT 1",
        (on_date,),
    ).fetchone()
    if row is None:
        return None
    effective = row["effective_date"]
    rev_row = conn.execute(
        "SELECT MAX(revision) AS rev FROM weight_sets WHERE effective_date = ?",
        (effective,),
    ).fetchone()
    rows = conn.execute(
        "SELECT * FROM weight_sets WHERE effective_date = ? AND revision = ?",
        (effective, rev_row["rev"]),
    ).fetchall()
    if not rows:
        return None
    providers: dict[str, list[dict]] = {}
    model_shares: dict[str, float] = {}
    for r in rows:
        if r["scope"] == "provider":
            providers.setdefault(r["model_class"], []).append(
                {"provider": r["key"], "weight": round(r["weight"], 4),
                 "days_observed": r["n_days_observed"]}
            )
        else:
            model_shares[r["key"]] = round(r["weight"], 4)
    for pset in providers.values():
        pset.sort(key=lambda p: -p["weight"])
    return {
        "effective_date": effective,
        "window_start": rows[0]["window_start"],
        "window_end": rows[0]["window_end"],
        "n_days_window": rows[0]["n_days_window"],
        "providers": providers,
        "model_shares": model_shares,
    }


def write_prints(conn: sqlite3.Connection, out_dir: Path | None = None) -> list[Path]:
    """One JSON file per print date: every series' latest revision, its full constituent
    audit set, and a digest of exactly that content.

    The digest is what makes the file checkable: `python -m tci.run reproduce --published`
    recomputes it from the database, and the database from stored observations, so a
    reader can confirm that what the site shows is what the calculation produced.
    Retired series are written too, so the published record is complete.
    """
    target = out_dir or PRINTS_DIR
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    dates = [r[0] for r in conn.execute("SELECT DISTINCT date FROM daily_index ORDER BY date")]
    for date in dates:
        series_out: dict = {}
        heads = conn.execute(
            "SELECT d.* FROM daily_index d JOIN (SELECT series, MAX(revision) AS rev"
            " FROM daily_index WHERE date = ? GROUP BY series) m"
            " ON d.series = m.series AND d.revision = m.rev WHERE d.date = ?"
            " ORDER BY d.series",
            (date, date),
        ).fetchall()
        for head in heads:
            canon = canonical_print(
                head, constituents_of(conn, date, head["series"], head["revision"])
            )
            series_out[head["series"]] = {**canon, "digest": digest(canon)}
        payload = {
            "date": date,
            "note": "Digest = sha256 over the canonical print (sorted keys, numbers as"
                    " 6-decimal strings). Verify: python -m tci.run reproduce --published",
            "series": series_out,
        }
        path = target / f"{date}.json"
        path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n",
                        encoding="utf-8", newline="\n")
        written.append(path)
    return written


TERM_DIR = REPO_ROOT / "site" / "data" / "term"
TERM_SCHEDULES = CONFIG_DIR / "term_schedules.yaml"

# Segments for sellers that publish term prices but are not on the panel (Civo, Latitude,
# Hyperstack ...). The panel's own segments win wherever a seller is on it. Shown so a
# hyperscaler reservation is never read beside a neocloud commitment as the same product.
TERM_SEGMENT_FALLBACK = {
    "civo": "neocloud", "latitude": "neocloud", "hyperstack": "neocloud",
    "crusoe": "neocloud", "coreweave": "neocloud", "voltagepark": "neocloud",
    "digitalocean": "neocloud", "lambdalabs": "neocloud", "ovhcloud": "neocloud",
    "oci": "hyperscaler", "azure": "hyperscaler", "aws": "hyperscaler", "gcp": "hyperscaler",
}


def _term_rows(conn: sqlite3.Connection, date: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT o.* FROM observations o JOIN runs r ON o.run_id = r.run_id"
        " WHERE r.utc_date = ?",
        (date,),
    ).fetchall()


def term_tables(conn: sqlite3.Connection, date: str) -> dict[str, Any]:
    """The term tables for one collection date, as published (tci.term)."""
    from tci import term

    segment_of = {**TERM_SEGMENT_FALLBACK, **load_factors().segments}
    excluded: list[term.Excluded] = []
    rows = _term_rows(conn, date)
    pairs = term.seller_terms(rows, excluded)
    schedules, stale = term.load_schedules(TERM_SCHEDULES, date)
    for sched in schedules:
        pairs += term.schedule_terms(sched, rows)
    return {
        "date": date,
        "note": (
            "Published commitment discounts. A ratio is committed / same-day on-demand price"
            " for one seller's same product, region and currency. `schedule` restates each"
            " seller's rate card as one median ratio per variant and tenor. A `cells` entry"
            " pools sellers and is shown only with at least"
            f" {term.MIN_SELLERS} sellers. A research table, not an index series."
        ),
        "schedule": [
            r.__dict__ | {"segment": segment_of.get(r.provider, "unclassified")}
            for r in term.schedule(pairs)
        ],
        "cells": [c.__dict__ | {"sellers": list(c.sellers)}
                  for c in term.cells(pairs, segment_of)],
        "excluded": [e.__dict__ for e in excluded],
        "published_schedules": [
            sc.__dict__ | {"segment": segment_of.get(sc.provider, "unclassified"),
                           "discounts": {str(k): v for k, v in sc.discounts.items()}}
            for sc in schedules
        ],
        "stale_schedules": stale,
        "pairs": [
            {
                "provider": t.provider, "source": t.source, "product": t.product,
                "gpu_model": t.gpu_model, "gpus": t.gpu_count, "region": t.region,
                "country": t.country, "currency": t.currency,
                "tenor_months": t.tenor_months, "on_demand": round(t.on_demand, 4),
                "committed": round(t.committed, 4), "ratio": round(t.ratio, 4),
            }
            for t in pairs
        ],
    }


def term_dates(conn: sqlite3.Connection) -> list[str]:
    return [
        r[0] for r in conn.execute(
            "SELECT DISTINCT r.utc_date FROM runs r JOIN observations o ON o.run_id = r.run_id"
            " WHERE o.term != 'on_demand' ORDER BY r.utc_date"
        )
    ]


def write_term(conn: sqlite3.Connection, out_dir: Path | None = None) -> Path | None:
    """site/data/term/latest.json and history.csv: published commitment discounts.

    A research table beside the index, not a series in it. Rebuilt in full from stored
    observations on every run, so any past day's table follows from the database by the
    same rule.
    """
    target = out_dir or TERM_DIR
    dates = term_dates(conn)
    if not dates:
        return None
    target.mkdir(parents=True, exist_ok=True)
    lines = ["date,provider,segment,gpu_model,tenor_months,median_ratio,min_ratio,"
             "max_ratio,n_configs"]
    latest: dict[str, Any] = {}
    for date in dates:
        latest = term_tables(conn, date)
        for r in latest["schedule"]:
            lines.append(
                f"{date},{r['provider']},{r['segment']},{r['gpu_model']},{r['tenor_months']},"
                f"{r['median_ratio']},{r['min_ratio']},{r['max_ratio']},{r['n_configs']}"
            )
    (target / "history.csv").write_text("\n".join(lines) + "\n", encoding="utf-8",
                                        newline="\n")
    path = target / "latest.json"
    path.write_text(json.dumps(latest, indent=1, sort_keys=True) + "\n", encoding="utf-8",
                    newline="\n")
    return path


def generate(conn: sqlite3.Connection) -> Path:
    # The version live today, which is what today's print was computed under. The head
    # of the succession can be an announced version whose effective date is still ahead.
    factors = load_factors(for_date=utc_now_iso()[:10])
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    head = _latest_print(conn, "EU-CRI-H100")
    payload: dict = {
        "generated_at": utc_now_iso(),
        "tci_version": __version__,
        "methodology_version": factors.methodology_version,
        "disclaimer": DISCLAIMER,
        "date": head["date"] if head else None,
        "series": _series_snapshot(conn),
        "constituents": {},
        "weight_review": None,
        "sources": sources_panel(),
    }
    if head is not None:
        for series in SERIES_BY_CLASS.values():
            cons = _constituents(conn, series, head["date"])
            if cons:
                payload["constituents"][series] = cons
        payload["weight_review"] = _weight_review(conn, head["date"])

    OUT_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    write_prints(conn)
    # The term table is research beside the index. A failure in it is logged and must
    # never stop the day's prints and site from being written.
    try:
        write_term(conn)
    except Exception:  # noqa: BLE001
        log.exception("webdata: term table not written")
    log.info("webdata: %s", OUT_PATH)
    return OUT_PATH
