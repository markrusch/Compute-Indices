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

from tci import DISCLAIMER, __version__, series_read
from tci.commands import COMPOSITE, SERIES_BY_CLASS
from tci.config import CONFIG_DIR, load_factors, load_static_providers
from tci.db import utc_now_iso
from tci.reproduce import PRINTS_DIR, canonical_print, constituents_of, digest, print_digest

log = logging.getLogger("tci.outputs.webdata")

REPO_ROOT = Path(__file__).resolve().parents[3]
OUT_PATH = REPO_ROOT / "site" / "data" / "latest.json"
# The versioned read interface. A breaking change to the shape goes to v2 and leaves
# v1 in place, because the whole promise is that somebody can build against it.
API_VERSION = "1"
API_DIR = REPO_ROOT / "site" / "data" / f"v{API_VERSION}"

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


# Revision resolution is tci.series_read's job, not this module's. The three readers
# below were spelled inline here and two of them filtered `value_usd IS NOT NULL` before
# resolving MAX(revision), which republished withdrawn prints into latest.json, the
# public JSON feed, while the HTML site next to it correctly showed the gap.
_latest_print = series_read.latest_print
_value_on = series_read.value_on_or_before
_prior_print = series_read.previous_published


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


CURVE_DIR = REPO_ROOT / "site" / "data" / "curve"

# The index series drawn from exactly one segment, by GPU model. A pooled discount shape is
# applied only to a level from the same population: the H100 headline draws on marketplace
# and neocloud together, and a neocloud shape on it would state one population's discount
# at another population's price. A (model, segment) with no series here gets no aggregate,
# and says why.
LEVEL_SERIES: dict[tuple[str, str], str] = {
    ("H100_SXM", "neocloud"): "EU-CRI-H100-NC",
    ("H100_SXM", "hyperscaler"): "EU-CRI-H100-HS",
    ("H100_SXM", "marketplace"): "EU-CRI-H100-MKT",
}

CURVE_CSV_HEADER = (
    "date,curve,gpu_model,provider,segment,currency,price_formation,tenor_months,rate,ratio,"
    "forward_from,forward_rate,span_months,wide_span,violation"
)


def _print_on(conn: sqlite3.Connection, series: str, date: str) -> float | None:
    """The print for exactly `date`, at its latest revision, or None.

    Not `_value_on`, which walks back to the last non-null print. An aggregate curve whose
    level was borrowed from an earlier session would show an old print as current. A
    withdrawn or gapped latest revision is None here as well.
    """
    row = conn.execute(
        "SELECT value_usd FROM daily_index WHERE series = ? AND date = ?"
        " ORDER BY revision DESC LIMIT 1",
        (series, date),
    ).fetchone()
    return None if row is None or row["value_usd"] is None else float(row["value_usd"])


def _curve_json(c: Any) -> dict[str, Any]:
    from tci import curve

    fwd, bad = curve.forwards(c)
    return {
        "gpu_model": c.key.gpu_model, "provider": c.key.provider, "segment": c.key.segment,
        "region_block": c.key.region_block, "currency": c.currency, "kind": c.kind,
        "spot": round(c.spot, 4),
        "knots": [
            {"tenor_months": k.tenor_months, "rate": round(k.rate, 4),
             "ratio": round(k.rate / c.spot, 4), "cum_cost": round(k.cum_cost, 2),
             "observed": k.observed, "price_formation": k.price_formation}
            for k in c.knots
        ],
        "forwards": [
            {"m1": f.m1, "m2": f.m2, "rate": round(f.rate, 4), "span_months": f.span_months,
             "wide_span": f.wide_span}
            for f in fwd
        ],
        "violations": [dict(v.__dict__) for v in bad],
        "dropped": [dict(d.__dict__) for d in c.dropped],
    }


def _currency_choice(counts: dict[str, int]) -> str:
    """One currency per seller for pooling: most configurations, USD on a tie."""
    return sorted(counts, key=lambda cur: (-counts[cur], cur != "USD", cur))[0]


def curve_tables(conn: sqlite3.Connection, date: str) -> dict[str, Any]:
    """Committed-cost curves for one collection date (tci.curve), as published.

    A seller's knot at tenor m is its median on-demand price times its median discount at
    m, so every ratio on a curve is the ratio term.html publishes for that seller. One curve
    per (seller, GPU model, currency): OVHcloud quotes some GPUs in EUR and in USD, and a
    curve is never converted. The pooled leg takes only (seller, source, class) triples the
    panel live on `date` admits, one currency per seller, and a level from the one series
    drawn from that segment alone, on that date only.
    """
    from statistics import median

    from tci import curve, term

    factors = load_factors(for_date=date)
    segment_of = {**TERM_SEGMENT_FALLBACK, **factors.segments}
    class_of = {v: name for name, mc in factors.model_classes.items() for v in mc.variants}
    rows = _term_rows(conn, date)
    pairs = term.seller_terms(rows)
    schedules, _stale = term.load_schedules(TERM_SCHEDULES, date)
    for sched in schedules:
        pairs += term.schedule_terms(sched, rows)

    ratios: dict[str, dict[str, dict[int, float]]] = {}
    for r in term.schedule(pairs):
        if r.median_ratio < 1.0:
            ratios.setdefault(r.provider, {}).setdefault(r.gpu_model, {})[r.tenor_months] = (
                r.median_ratio)
    formation = {p: curve.classify(by_model) for p, by_model in ratios.items()}

    def build(provider: str, gpu_model: str, currency: str, terms: list[Any]) -> Any:
        spot = median(t.on_demand for t in terms)
        return curve.build(
            curve.CurveKey(gpu_model, provider, segment_of.get(provider, "unclassified")),
            date, spot, [(t.tenor_months, spot * t.ratio) for t in terms],
            currency=currency,
            price_formation=formation.get(provider, "administered_untested"),
        )

    groups: dict[tuple[str, str, str], list[Any]] = {}
    for t in pairs:
        groups.setdefault((t.provider, t.gpu_model, t.currency), []).append(t)
    sellers: list[Any] = []
    unbuilt: list[dict[str, Any]] = []
    for (provider, gpu_model, currency), terms in sorted(groups.items()):
        c = build(provider, gpu_model, currency, terms)
        if c is None:
            unbuilt.append({"provider": provider, "gpu_model": gpu_model, "currency": currency,
                            "reason": "no committed price below on-demand"})
        else:
            sellers.append(c)

    admitted: dict[tuple[str, str], dict[str, list[Any]]] = {}
    for t in pairs:
        cls = class_of.get(t.gpu_model)
        if cls is None or not factors.admits(t.provider, t.source, cls):
            continue
        admitted.setdefault((t.provider, t.gpu_model), {}).setdefault(t.currency, []).append(t)
    by_cell: dict[tuple[str, str], list[Any]] = {}
    for (provider, gpu_model), by_currency in sorted(admitted.items()):
        cur = _currency_choice({k: len(v) for k, v in by_currency.items()})
        c = build(provider, gpu_model, cur, by_currency[cur])
        if c is not None:
            by_cell.setdefault((gpu_model, c.key.segment), []).append(c)

    pooled: list[dict[str, Any]] = []
    for (gpu_model, segment), cs in sorted(by_cell.items()):
        points = curve.pool(cs)
        series = LEVEL_SERIES.get((gpu_model, segment))
        level = _print_on(conn, series, date) if series else None
        agg = None
        if not any(p.published for p in points):
            status = f"no tenor has {curve.MIN_PROVIDERS} panel sellers in this segment"
        elif series is None:
            status = "no index series is drawn from this segment alone for this GPU"
        elif level is None:
            status = f"{series} did not print on {date}"
        else:
            agg = curve.apply_level(points, level, curve.CurveKey(gpu_model, None, segment),
                                    date)
            status = "published" if agg is not None else "no usable pooled tenor"
        agg_json = _curve_json(agg) if agg is not None else None
        if agg_json is not None:
            for v in agg_json["violations"]:
                v["detail"] += ("; in a pooled curve this can only mean different sellers"
                                " voted at the two tenors")
        pooled.append({
            "gpu_model": gpu_model, "segment": segment, "model_class": class_of.get(gpu_model),
            "status": status, "level_series": series, "level_usd": level,
            "points": [dict(p.__dict__) | {"providers": list(p.providers)} for p in points],
            "curve": agg_json,
        })

    return {
        "date": date,
        "note": (
            "Committed-cost curves: what a named seller charges per GPU-hour to lock each"
            " tenor, and the forwards bootstrapped from them. Commitment prices, not a"
            " forecast of spot; a forward is the break-even rate between locking and rolling."
            " price_formation 'administered_uniform' is a schedule identical across chip"
            " generations and carries no chip-specific information. A pooled curve is the"
            " index level for its segment times the median seller discount, shown only with"
            f" at least {curve.MIN_PROVIDERS} panel sellers. A research table, not an index"
            " series."
        ),
        "parameters": {
            "hours_per_month": curve.HOURS_PER_MONTH, "interpolation": "flat_forward",
            "min_providers": curve.MIN_PROVIDERS, "max_span_months": curve.MAX_SPAN_MONTHS,
            "max_tenor_months": curve.MAX_TENOR_MONTHS,
            "chip_invariance_tol": curve.CHIP_INVARIANCE_TOL,
        },
        "price_formation": dict(sorted(formation.items())),
        "sellers": [_curve_json(c) for c in sellers],
        "unbuilt": unbuilt,
        "pooled": pooled,
        "forward_spot": {
            "published": False,
            "sellers_market_quoted": sum(
                1 for v in formation.values() if v in curve.FORWARD_SPOT_FORMATIONS),
            "reason": (
                "S* = PHI + pi. A forward spot curve needs market-quoted or transacted term"
                " prices and a measured term premium, and this table holds neither."
            ),
        },
    }


def curve_rows(tables: dict[str, Any]) -> list[str]:
    """history.csv lines for one date's curve tables: one row per curve per tenor.

    The forward columns describe the segment ending at that tenor, so every row stands on
    its own and the spot anchor's row carries none.
    """
    date = tables["date"]
    out: list[str] = []

    def emit(kind: str, c: dict[str, Any]) -> None:
        into = {f["m2"]: f for f in c["forwards"]}
        bad = {v["m2"]: v for v in c["violations"]}
        for k in c["knots"]:
            f = into.get(k["tenor_months"])
            v = bad.get(k["tenor_months"])
            start = f["m1"] if f else (v["m1"] if v else "")
            out.append(",".join(str(x) for x in (
                date, kind, c["gpu_model"], c["provider"] or "", c["segment"] or "",
                c["currency"], k["price_formation"], k["tenor_months"], k["rate"], k["ratio"],
                start, f["rate"] if f else "", f["span_months"] if f else "",
                int(f["wide_span"]) if f else "", v["reason"] if v else "",
            )))

    for c in tables["sellers"]:
        emit("seller", c)
    for p in tables["pooled"]:
        if p["curve"] is not None:
            emit("pooled", p["curve"])
    return out


def write_curve(conn: sqlite3.Connection, out_dir: Path | None = None) -> Path | None:
    """site/data/curve/latest.json and history.csv: committed-cost curves (tci.curve).

    Rebuilt in full from stored observations on every run, as write_term is, so history.csv
    is a strike ledger running from the first day a term price was stored rather than from
    the day this function first ran. A research table beside the index, not a series in it.
    """
    target = out_dir or CURVE_DIR
    dates = term_dates(conn)
    if not dates:
        return None
    target.mkdir(parents=True, exist_ok=True)
    lines = [CURVE_CSV_HEADER]
    latest: dict[str, Any] = {}
    for date in dates:
        latest = curve_tables(conn, date)
        lines += curve_rows(latest)
    (target / "history.csv").write_text("\n".join(lines) + "\n", encoding="utf-8",
                                        newline="\n")
    path = target / "latest.json"
    path.write_text(json.dumps(latest, indent=1, sort_keys=True) + "\n", encoding="utf-8",
                    newline="\n")
    return path



def write_series_api(conn: sqlite3.Connection, out_dir: Path | None = None) -> list[Path]:
    """One file per series holding its whole history, plus a catalogue naming them all.

    `latest.json` gives today across every series and the per-date print files give one
    date across every series. Neither gives one series across dates, which is what anyone
    building on this actually wants, and assembling it meant fetching a file per session.

    Each row carries the same digest the print file publishes for that (date, series), so
    a value taken from here can be verified without also fetching the print file, and the
    two can never disagree - they are computed by the same function from the same row.
    `reproduce --published` checks these files too; publishing a digest nothing verifies
    would be worse than publishing none.
    """
    target = out_dir or API_DIR
    (target / "series").mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    names = [r[0] for r in conn.execute("SELECT DISTINCT series FROM daily_index ORDER BY series")]
    catalogue: list[dict[str, Any]] = []
    for series in names:
        points = []
        for head in conn.execute(
            "SELECT d.* FROM daily_index d JOIN (SELECT date, MAX(revision) rev"
            " FROM daily_index WHERE series = ? GROUP BY date) m"
            " ON d.date = m.date AND d.revision = m.rev WHERE d.series = ?"
            " ORDER BY d.date",
            (series, series),
        ):
            pd = print_digest(conn, head["date"], series)
            points.append({
                "date": head["date"],
                "revision": head["revision"],
                "value_usd": head["value_usd"],
                "value_eur": head["value_eur"],
                "fx_rate": head["fx_rate"],
                "fx_date": head["fx_date"],
                "n_sources": head["n_sources"],
                "n_executable": head["n_executable"],
                # A gap is a row with a null value and a reason, never an absent row.
                # Dropping it would let a consumer interpolate across it without knowing.
                "flags": head["flags"] or "",
                "methodology_version": head["methodology_version"],
                "digest": pd[1] if pd else None,
            })
        path = target / "series" / f"{series}.json"
        path.write_text(
            json.dumps({
                "schema_version": API_VERSION,
                "series": series,
                "unit": "USD per GPU-hour" if series != COMPOSITE else "index, base 100",
                "note": "A null value_usd is a session that did not print; flags carry the"
                        " reason. Never interpolate across one. Digest = sha256 over the"
                        " canonical print; verify with python -m tci.run reproduce --published.",
                "points": points,
            }, indent=1, sort_keys=True) + "\n",
            encoding="utf-8", newline="\n",
        )
        written.append(path)
        catalogue.append({
            "series": series,
            "href": f"series/{series}.json",
            "first_date": points[0]["date"] if points else None,
            "last_date": points[-1]["date"] if points else None,
            "n_sessions": len(points),
            "n_printed": sum(1 for p in points if p["value_usd"] is not None),
        })

    index_path = target / "index.json"
    index_path.write_text(
        json.dumps({
            "schema_version": API_VERSION,
            "generated_at": utc_now_iso(),
            "methodology_version": load_factors(for_date=utc_now_iso()[:10]).methodology_version,
            "disclaimer": DISCLAIMER,
            "licence": "CC BY 4.0 for non-commercial use; see DATA-TERMS.md",
            "series": catalogue,
            "also": {
                "latest": "../latest.json",
                "prints": "../prints/YYYY-MM-DD.json",
                "csv": "../index_history.csv",
            },
        }, indent=1, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n",
    )
    written.append(index_path)
    return written


FORWARD_DIR = REPO_ROOT / "site" / "data" / "forward"
FORWARD_CSV_HEADER = (
    "date,component,horizon_days,revision,value_usd,p10,p50,p90,n_inputs,method_version"
)


def write_forward(conn: sqlite3.Connection, out_dir: Path | None = None) -> Path | None:
    """site/data/forward/latest.json, history.csv and calibration.json (tci.forward_data).

    Read from the ledger, never recomputed here: the published history is what was estimated
    on each date. history.csv carries estimates made on their own date only; backfilled rows
    stay in the database and in latest.json's count, and never in the history.
    """
    from tci import forward_data

    tables = forward_data.forward_tables(conn)
    if tables is None:
        return None
    target = out_dir or FORWARD_DIR
    target.mkdir(parents=True, exist_ok=True)
    latest = {k: v for k, v in tables.items() if k != "history"}
    latest["note"] = (
        "Forward estimate of the EU-CRI-H100 print. M: expected mean of the published print"
        " over the window (horizon_days), with percentiles of that mean. T: term-implied"
        " diagnostic, never used to form M. L: cheapest lockable EU cost per used GPU-hour, a"
        " price and not a bound on the index. value_usd null is a gap with its reason in"
        " detail.gap. Research output, not a reference price for any financial instrument."
    )
    lines = [FORWARD_CSV_HEADER]
    for r in tables["history"]:
        lines.append(",".join("" if v is None else str(v) for v in (
            r["date"], r["component"], r["horizon_days"], r["revision"], r["value_usd"],
            r["p10"], r["p50"], r["p90"], r["n_inputs"], r["method_version"])))
    (target / "history.csv").write_text("\n".join(lines) + "\n", encoding="utf-8",
                                        newline="\n")
    (target / "calibration.json").write_text(
        json.dumps(tables["calibration"], indent=1, sort_keys=True) + "\n", encoding="utf-8",
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
    write_series_api(conn)
    # The term table is research beside the index. A failure in it is logged and must
    # never stop the day's prints and site from being written.
    try:
        write_term(conn)
    except Exception:  # noqa: BLE001
        log.exception("webdata: term table not written")
    # The curves are research beside the index on the same terms as the term table.
    try:
        write_curve(conn)
    except Exception:  # noqa: BLE001
        log.exception("webdata: curve tables not written")
    # The forward estimate: research beside the index on the same terms.
    try:
        write_forward(conn)
    except Exception:  # noqa: BLE001
        log.exception("webdata: forward estimate not written")
    log.info("webdata: %s", OUT_PATH)
    return OUT_PATH
