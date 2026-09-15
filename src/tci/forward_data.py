# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""The forward estimate's inputs as they were knowable on the estimate date, and its ledger.

`tci.forward` does the arithmetic; this module decides what the arithmetic is allowed to see.
Every read here is bounded by the estimate date `t`, and the bounds are the point:

- The anchor is never read from `daily_index`. A print can be revised after its date (on 16
  August 2026 two July prints were withdrawn and two August prints revised), so a filter on
  `date <= t` with MAX(revision) hands a past estimate a value computed later. The anchor is
  recomputed instead from each day's stored observations, which are collected on their own
  date and never change.
- A methodology version counts on `t` only if its notice had been announced by `t`. The
  backfill must not know in August about a version announced in September.
- Rates are read by the date they were fetched, not only the date they describe.

WHY A PRO-FORMA ANCHOR. The index definition changes on dates already announced: v0.5.0 on 22
September 2026 admits DigitalOcean, Verda, Nebius and Lambda to the panel. A 30-day window
starting on 15 September is therefore a forecast of an index that, for most of the window,
is computed under different rules from the ones behind today's print. Each version the
window needs gets its own series, computed in memory from past observations under that
version's parameters through the same normalise and compute_print path the print uses, and
never stored.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from tci import config, curve, forward, term
from tci.collectors.fx import rate_for
from tci.db import utc_now_iso

log = logging.getLogger("tci.forward_data")

FORWARD_CONFIG = config.CONFIG_DIR / "forward.yaml"
NOTICES_PATH = config.CONFIG_DIR / "notices.yaml"

# The headline's reference variant: the only chip whose term prices and lockable offers are
# comparable with the anchor's unit.
REFERENCE_VARIANT = "H100_SXM"
HORIZON_TENOR_MONTHS = {days: months for months, days in forward.TENOR_DAYS.items()}
RESERVED_TENOR_MONTHS = {30: 1, 90: 3, 180: 6}


def load_params(path: Path | None = None) -> tuple[forward.Params, str]:
    """The parameters and `<version>+<sha256>` of the file they came from.

    Line endings are normalised before hashing, so a checkout on Windows and one on the Linux
    runner record the same method version for the same file.
    """
    text = (path or FORWARD_CONFIG).read_text(encoding="utf-8").replace("\r\n", "\n")
    params = forward.Params.from_mapping(yaml.safe_load(text))
    return params, f"{params.version}+{hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]}"


def known_versions(on_date: str) -> list[tuple[int, str, str]]:
    """(first day in effect, version, effective_from) for versions knowable on `on_date`."""
    raw = yaml.safe_load(NOTICES_PATH.read_text(encoding="utf-8")) or {}
    announced = {str(n["id"]): str(n["announced"]) for n in raw.get("notices") or []}
    out = []
    for entry in config.load_succession():
        notice = getattr(entry, "notice", None)
        if notice and announced.get(str(notice), "9999-12-31") > on_date:
            continue
        out.append((forward.day(entry.effective_from), entry.version, entry.effective_from))
    return sorted(out)


class ProForma:
    """EU-CRI-H100 recomputed from one day's observations under one version's parameters.

    A value depends only on the observation date and the version, never on the estimate date,
    so it is computed once per pair and reused across every estimate that needs it.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._values: dict[tuple[str, str], float | None] = {}
        self._factors: dict[str, config.Factors] = {}
        self._dates: list[str] | None = None

    def dates(self) -> list[str]:
        if self._dates is None:
            self._dates = [r[0] for r in self.conn.execute(
                "SELECT DISTINCT r.utc_date FROM runs r JOIN observations o ON o.run_id = r.run_id"
                " ORDER BY r.utc_date")]
        return self._dates

    def factors(self, version_from: str) -> config.Factors:
        if version_from not in self._factors:
            self._factors[version_from] = config.load_factors(for_date=version_from)
        return self._factors[version_from]

    def value(self, obs_date: str, version_from: str) -> float | None:
        key = (obs_date, version_from)
        if key not in self._values:
            self._values[key] = self._compute(obs_date, version_from)
        return self._values[key]

    def _compute(self, obs_date: str, version_from: str) -> float | None:
        from tci import commands
        from tci.index import compute_print
        from tci.normalise import normalise_observations

        factors = self.factors(version_from)
        sovereign = config.load_sovereign(for_date=version_from)
        fx = rate_for(self.conn, obs_date, strictly_before=factors.fx.strictly_before)
        rows = commands._observations_for_date(self.conn, obs_date)
        normalised = normalise_observations(rows, factors, fx_eur_usd=fx[0] if fx else None)
        _, population, predicate = commands._series_definitions(
            sovereign, factors.headline_class, factors)[commands.HEADLINE]
        result = compute_print(obs_date, commands.HEADLINE,
                               [o for o in normalised if predicate(o)], factors, fx,
                               population=population)
        return result.value_usd


@dataclass
class Row:
    component: str
    horizon_days: int
    value: float | None
    p10: float | None = None
    p50: float | None = None
    p90: float | None = None
    n_inputs: int = 0
    detail: dict[str, Any] = field(default_factory=dict)


def _r6(x: float | None) -> float | None:
    return None if x is None else round(x, 6)


# --- M: the martingale estimate ------------------------------------------------------------


def martingale_rows(pf: ProForma, t: str, params: forward.Params) -> list[Row]:
    versions = known_versions(t)
    schedule = [(first, effective_from) for first, _, effective_from in versions]
    names = {effective_from: v for _, v, effective_from in versions}
    t_day = forward.day(t)
    dates = [d for d in pf.dates() if d <= t]
    fits: dict[str, forward.LevelFit | str] = {}

    def fit_for(version_from: str) -> forward.LevelFit | str:
        name = names[version_from]
        points = [(forward.day(d), math.log(v)) for d in dates
                  if (v := pf.value(d, version_from)) is not None and v > 0]
        if not points or points[-1][0] != t_day:
            return f"no pro-forma print under {name} on {t}"
        if len(points) < params.min_prints:
            return (f"{len(points)} pro-forma prints under {name}, fewer than the "
                    f"{params.min_prints} the fit needs")
        fit = forward.fit_level(points, params.grid_log10_q, params.grid_log10_r)
        return fit if fit is not None else f"no fit under {name}"

    rows = []
    for h in params.horizons_days:
        try:
            split = forward.version_days(t_day, h, schedule)
        except ValueError as exc:
            rows.append(Row("M", h, None, detail={"gap": str(exc)}))
            continue
        parts: list[tuple[int, forward.WindowForecast]] = []
        detail: dict[str, Any] = {"versions": {}}
        gap = None
        for version_from, n_days in split.items():
            if version_from not in fits:
                fits[version_from] = fit_for(version_from)
            fit = fits[version_from]
            if isinstance(fit, str):
                gap = fit
                break
            parts.append((n_days, forward.window_forecast(fit, h, params.quantiles)))
            detail["versions"][names[version_from]] = {
                "days": n_days, "prints": fit.n, "q": _r6(fit.q * 1e6), "r": _r6(fit.r * 1e6),
                "level_usd": _r6(math.exp(fit.level)),
            }
        if gap is not None:
            rows.append(Row("M", h, None, detail={"gap": gap}))
            continue
        wf = parts[0][1] if len(parts) == 1 else forward.combine(parts, params.quantiles)
        lo, mid, hi = (wf.at(p) for p in params.quantiles)
        detail["q_r_units"] = "variance per day x 1e6, log price"
        rows.append(Row("M", h, _r6(wf.mean), _r6(lo), _r6(mid), _r6(hi),
                        sum(f.n for f in fits.values() if not isinstance(f, str)), detail))
    return rows


# --- shared as-of reads ----------------------------------------------------------------------


def rate_curve(conn: sqlite3.Connection, series: str, t: str) -> dict[int, float]:
    """tenor days -> annual rate (decimal), the latest observation fetched by `t`."""
    out: dict[int, float] = {}
    for r in conn.execute(
        "SELECT tenor_days, rate_pct FROM overlay_rates WHERE series = ? AND obs_date <= ?"
        " AND substr(fetched_utc, 1, 10) <= ? ORDER BY obs_date, fetched_utc",
        (series, t, t),
    ):
        out[r["tenor_days"]] = r["rate_pct"] / 100.0
    return out


def _reserved_quotes(conn: sqlite3.Connection, t: str, factors: config.Factors) -> list[Any]:
    eu = set(factors.eu_eea_countries)
    rows = conn.execute(
        "SELECT q.* FROM term_quotes q JOIN runs r ON q.run_id = r.run_id"
        " WHERE r.utc_date = ? AND q.gpu_model = ? AND q.in_index_scope = 1",
        (t, REFERENCE_VARIANT),
    ).fetchall()
    # A quote is a term price only if it is below the same offer's on-demand price: a null
    # or zero discount means the host offers no commitment price at all.
    return [q for q in rows
            if q["country"] in eu and (q["num_gpus"] or 0) >= factors.filters.min_gpu_count
            and q["dph_total"] and q["discounted_dph_total"]
            and 0 < q["discounted_dph_total"] < q["dph_total"]]


# --- T: the term-implied diagnostic ----------------------------------------------------------


def term_rows(conn: sqlite3.Connection, t: str, m_rows: list[Row], params: forward.Params,
              factors: config.Factors, usd: dict[int, float]) -> list[Row]:
    from tci import commands

    obs = commands._observations_for_date(conn, t)
    pairs = term.seller_terms(obs)
    by_provider: dict[str, dict[str, dict[int, float]]] = {}
    for row in term.schedule(pairs):
        if row.median_ratio < 1.0:
            by_provider.setdefault(row.provider, {}).setdefault(row.gpu_model, {})[
                row.tenor_months] = row.median_ratio
    formation = {p: curve.classify(v) for p, v in by_provider.items()}
    population = factors.population_for("headline")
    eu = set(factors.eu_eea_countries)
    panel = factors.panel or {}

    ratios: list[forward.SellerRatio] = []
    for s in pairs:
        entry = panel.get(s.provider)
        if (s.gpu_model != REFERENCE_VARIANT or s.country not in eu or entry is None
                or entry.segment not in population):
            continue
        ratios.append(forward.SellerRatio(s.provider, s.tenor_months, s.ratio,
                                          formation.get(s.provider, "administered_untested"),
                                          s.source))
    for q in _reserved_quotes(conn, t, factors):
        rate = forward.rate_at(usd, q["requested_days"])
        months = RESERVED_TENOR_MONTHS.get(q["requested_days"])
        if rate is None or months is None:
            continue
        restated = forward.arrears_equivalent(q["discounted_dph_total"], q["requested_days"],
                                              rate + params.prepaid_credit_spread)
        ratios.append(forward.SellerRatio(f"vast.ai:{q['host_id']}", months,
                                          restated / q["dph_total"], "market_quoted",
                                          "vast_reserved"))

    means = {r.horizon_days: r for r in m_rows}
    out = []
    for h in params.horizons_days:
        months = HORIZON_TENOR_MONTHS.get(h)
        m = means.get(h)
        if months is None:
            out.append(Row("T", h, None, detail={"gap": "no contract tenor matches this window"}))
            continue
        at_tenor = sorted({(s.seller, s.formation) for s in ratios if s.tenor_months == months})
        if m is None or m.value is None:
            out.append(Row("T", h, None, detail={"gap": "no martingale estimate to scale",
                                                 "sellers": [list(x) for x in at_tenor]}))
            continue
        ti = forward.term_implied(m.value, ratios, months, params.term_min_sellers,
                                  params.term_admit)
        detail: dict[str, Any] = {"tenor_months": months, "sellers": [list(x) for x in at_tenor]}
        if ti.value is None:
            detail["gap"] = (f"{ti.n_sellers} of {params.term_min_sellers} eligible sellers at "
                             f"{months} month{'s' if months != 1 else ''}")
        out.append(Row("T", h, _r6(ti.value), n_inputs=ti.n_sellers, detail=detail))
    return out


# --- L: the lockable cost --------------------------------------------------------------------


def lock_offers(conn: sqlite3.Connection, t: str, params: forward.Params,
                factors: config.Factors, usd: dict[int, float]) -> list[forward.LockOffer]:
    from tci import commands

    eu = set(factors.eu_eea_countries)
    min_gpus = factors.filters.min_gpu_count
    fx = rate_for(conn, t, strictly_before=factors.fx.strictly_before)
    offers: list[forward.LockOffer] = []
    for r in commands._observations_for_date(conn, t):
        if r["gpu_model"] != REFERENCE_VARIANT or r["country"] not in eu:
            continue
        if r["gpu_count"] is not None and r["gpu_count"] < min_gpus:
            continue
        raw = term._raw(r)
        if term._licensed(raw):
            continue
        if r["source"] == "vast_ai" and r["tier"] == "executable" and r["term"] == "on_demand":
            duration = raw.get("duration")
            if duration:
                offers.append(forward.LockOffer("listed", f"vast.ai:{raw.get('host_id')}",
                                                float(r["price_usd_per_gpu_hr"]),
                                                float(duration) / 86400.0, 0.0))
        elif r["tier"] == "list" and r["term"] in term.TENOR_MONTHS:
            price, currency = term._native(r)
            if currency == "EUR":
                if fx is None:
                    continue
                price *= fx[0]
            elif currency != "USD":
                continue
            days = round(term.TENOR_MONTHS[r["term"]] * 365 / 12)
            offers.append(forward.LockOffer("rate_card", r["provider"], price, days, days))
    for q in _reserved_quotes(conn, t, factors):
        rate = forward.rate_at(usd, q["requested_days"])
        if rate is None:
            continue
        per_gpu = q["discounted_dph_total"] / q["num_gpus"]
        restated = forward.arrears_equivalent(per_gpu, q["requested_days"],
                                              rate + params.prepaid_credit_spread)
        offers.append(forward.LockOffer("reserved", f"vast.ai:{q['host_id']}", restated,
                                        q["requested_days"], q["requested_days"]))
    return offers


def lockable_rows(offers: list[forward.LockOffer], params: forward.Params) -> list[Row]:
    rows = []
    for h in params.horizons_days:
        lk = forward.lockable(offers, h)
        if lk.value is None or lk.cheapest is None:
            rows.append(Row("L", h, None, detail={"gap": f"no EU/EEA offer covers {h} days"}))
            continue
        c = lk.cheapest
        rows.append(Row("L", h, _r6(lk.value), n_inputs=lk.n_offers, detail={
            "kind": c.kind, "seller": c.seller, "price_usd": _r6(c.price_usd),
            "covers_days": _r6(c.covers_days), "commit_days": _r6(c.commit_days),
        }))
    return rows


# --- the ledger ------------------------------------------------------------------------------


def estimate(conn: sqlite3.Connection, t: str, params: forward.Params,
             pf: ProForma | None = None) -> list[Row]:
    """Every row for one estimate date, from data knowable on that date."""
    pf = pf or ProForma(conn)
    factors = config.load_factors(for_date=t)
    usd = rate_curve(conn, "UST-PAR", t)
    m = martingale_rows(pf, t, params)
    return m + term_rows(conn, t, m, params, factors, usd) + lockable_rows(
        lock_offers(conn, t, params, factors, usd), params)


def write_rows(conn: sqlite3.Connection, t: str, series: str, rows: list[Row],
               method_version: str, backfilled: bool) -> int:
    """Append what changed. Same digest and version as the latest revision: nothing written."""
    now = utc_now_iso()
    written = 0
    with conn:
        for r in rows:
            payload = {"value": r.value, "p10": r.p10, "p50": r.p50, "p90": r.p90,
                       "n": r.n_inputs, "detail": r.detail}
            dg = forward.digest(payload)
            last = conn.execute(
                "SELECT revision, inputs_digest, method_version FROM forward_estimates"
                " WHERE date = ? AND series = ? AND component = ? AND horizon_days = ?"
                " ORDER BY revision DESC LIMIT 1",
                (t, series, r.component, r.horizon_days),
            ).fetchone()
            if last is not None and last["inputs_digest"] == dg and (
                    last["method_version"] == method_version):
                continue
            conn.execute(
                "INSERT INTO forward_estimates (date, series, component, horizon_days, revision,"
                " value_usd, p10, p50, p90, n_inputs, detail, inputs_digest, method_version,"
                " backfilled, computed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (t, series, r.component, r.horizon_days,
                 1 if last is None else last["revision"] + 1,
                 r.value, r.p10, r.p50, r.p90, r.n_inputs,
                 json.dumps(r.detail, sort_keys=True), dg, method_version, int(backfilled),
                 now),
            )
            written += 1
    return written


def record_forward(conn: sqlite3.Connection, utc_date: str) -> int:
    """Estimate `utc_date`, and backfill any earlier stored date that has no estimate yet.

    A date estimated after the fact, whether on the first run or after a day the daily job
    failed, is written with `backfilled = 1`: computed from data knowable on its date, but not
    on its date, so never part of the track record.
    """
    params, method_version = load_params()
    series = params.anchor_series
    pf = ProForma(conn)
    have = {r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM forward_estimates WHERE series = ?", (series,))}
    written = 0
    for d in pf.dates():
        if d < utc_date and d not in have:
            written += write_rows(conn, d, series, estimate(conn, d, params, pf), method_version,
                                  backfilled=True)
    if utc_date in pf.dates():
        written += write_rows(conn, utc_date, series, estimate(conn, utc_date, params, pf),
                              method_version, backfilled=False)
    return written


def note_failure(conn: sqlite3.Connection, utc_date: str, exc: BaseException) -> None:
    now = utc_now_iso()
    with conn:
        conn.execute(
            "INSERT INTO runs (run_id, utc_date, source, started_utc, finished_utc, status, notes)"
            " VALUES (?, ?, 'forward', ?, ?, 'failed', ?)",
            (str(uuid.uuid4()), utc_date, now, now, f"{type(exc).__name__}: {exc}"[:500]),
        )


# --- reading the ledger back -----------------------------------------------------------------


def _latest(conn: sqlite3.Connection, series: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT f.* FROM forward_estimates f JOIN (SELECT date, component, horizon_days,"
        " MAX(revision) rev FROM forward_estimates WHERE series = ?"
        " GROUP BY date, component, horizon_days) m ON f.date = m.date"
        " AND f.component = m.component AND f.horizon_days = m.horizon_days"
        " AND f.revision = m.rev WHERE f.series = ? ORDER BY f.date, f.component, f.horizon_days",
        (series, series),
    ).fetchall()


def published_prints(conn: sqlite3.Connection, series: str) -> dict[int, float]:
    """Realised values: the published print, latest revision, for scoring closed windows."""
    rows = conn.execute(
        "SELECT d.date, d.value_usd FROM daily_index d JOIN (SELECT date, MAX(revision) rev"
        " FROM daily_index WHERE series = ? GROUP BY date) m ON d.date = m.date"
        " AND d.revision = m.rev WHERE d.series = ? AND d.value_usd IS NOT NULL",
        (series, series),
    ).fetchall()
    return {forward.day(r["date"]): r["value_usd"] for r in rows}


def forward_tables(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """What the page and the data files publish: the latest estimate, history, calibration."""
    params, method_version = load_params()
    series = params.anchor_series
    rows = _latest(conn, series)
    if not rows:
        return None
    live_dates = sorted({r["date"] for r in rows if not r["backfilled"]})
    as_of = live_dates[-1] if live_dates else max(r["date"] for r in rows)

    def as_dict(r: sqlite3.Row) -> dict[str, Any]:
        return {"date": r["date"], "component": r["component"], "horizon_days": r["horizon_days"],
                "revision": r["revision"], "value_usd": r["value_usd"], "p10": r["p10"],
                "p50": r["p50"], "p90": r["p90"], "n_inputs": r["n_inputs"],
                "detail": json.loads(r["detail"]), "method_version": r["method_version"],
                "backfilled": bool(r["backfilled"])}

    prints = published_prints(conn, series)
    last_print_day = max(prints) if prints else None
    calibration = []
    for backfilled in (False, True):
        for h in params.horizons_days:
            forecasts, realised = [], {}
            for r in rows:
                if (r["component"] != "M" or r["horizon_days"] != h or bool(r["backfilled"])
                        != backfilled or r["value_usd"] is None):
                    continue
                origin = forward.day(r["date"])
                if last_print_day is None or origin + h > last_print_day:
                    continue
                forecasts.append((origin, r["value_usd"], r["p10"], r["p90"]))
                rv = forward.realised_mean(prints, origin, h, params.min_printed_share)
                if rv.value is not None:
                    realised[origin] = rv.value
            cal = forward.calibrate(forecasts, realised, h, params.min_nonoverlapping_windows)
            calibration.append({"backfilled": backfilled, **cal.__dict__})

    return {
        "series": series,
        "as_of": as_of,
        "method_version": method_version,
        "parameters": {"horizons_days": list(params.horizons_days),
                       "min_prints": params.min_prints, "quantiles": list(params.quantiles),
                       "term_min_sellers": params.term_min_sellers,
                       "min_printed_share": params.min_printed_share,
                       "min_nonoverlapping_windows": params.min_nonoverlapping_windows},
        "latest": [as_dict(r) for r in rows if r["date"] == as_of],
        "history": [as_dict(r) for r in rows if not r["backfilled"]],
        "n_backfilled_dates": len({r["date"] for r in rows if r["backfilled"]}),
        "calibration": calibration,
    }
