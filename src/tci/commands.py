# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Implementations of the data-facing CLI commands (daily, constituents, backfill, ...).

Database orchestration lives here; the calculation itself (index.py, normalise.py) is
pure and methodology-hashed. Observation day = runs.utc_date (the collection day).
"""

from __future__ import annotations

import argparse
import csv
import logging
import sqlite3
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from datetime import date as date_type
from pathlib import Path

from tci import config, db, intake, series_read, weights
from tci.basis import compute_basis
from tci.collectors import base
from tci.collectors.azure_retail import AzureRetailCollector
from tci.collectors.computable_sources import computable_collectors
from tci.collectors.fx import collect_fx, rate_for
from tci.collectors.gpuhunt_ import GpuHuntCollector
from tci.collectors.runpod import RunPodCollector
from tci.collectors.scaleway import ScalewayCollector
from tci.collectors.seeweb import SeewebCollector
from tci.collectors.static_yaml import StaticYamlCollector
from tci.collectors.vast_ai import VastAiCollector
from tci.collectors.vast_reserved import VastReservedCollector
from tci.index import compute_print
from tci.models import Constituent, IndexPrint
from tci.normalise import NormalisedObs, normalise_observations, unadmitted_providers

log = logging.getLogger("tci.commands")

REPO_ROOT = Path(__file__).resolve().parents[2]
CSV_PATH = REPO_ROOT / "site" / "data" / "index_history.csv"

HEADLINE = "EU-CRI-H100"
SERIES_7D = "EU-CRI-H100-7D"
COMPOSITE = "EU-CRI-COMPUTE"
SERIES_BY_CLASS = {
    "H100": "EU-CRI-H100", "H200": "EU-CRI-H200", "B200": "EU-CRI-B200",
    "B300": "EU-CRI-B300", "A100": "EU-CRI-A100", "H100P": "EU-CRI-H100-PCIE",
}


def collectors_for_daily() -> list[base.Collector]:
    # Order is irrelevant to the calculation. Collectors not named in the live version's
    # panel store rows that no print reads (see factors.yaml `panel`).
    return [
        VastAiCollector(), RunPodCollector(), GpuHuntCollector(), StaticYamlCollector(),
        ScalewayCollector(), AzureRetailCollector(), SeewebCollector(),
        *computable_collectors(),
        # Last: its spaced requests start well after vast_ai's own book has been read.
        VastReservedCollector(),
    ]


# (model class, market-segment population, extra predicate)
SeriesDef = tuple[str, frozenset[str], Callable[[NormalisedObs], bool]]


def _series_definitions(
    sovereign: frozenset[str], headline_class: str, factors: config.Factors
) -> dict[str, SeriesDef]:
    """v0.3.0 series, segregated by market segment rather than by executable/list tier.

    The constituent distribution is bimodal — measured separation 5.4 sd between the
    neocloud/marketplace cluster and the hyperscaler catalog cluster — so the headline
    draws from one side of that gap and the hyperscaler tier gets its own series, where
    it is the correct object of measurement rather than a drag on someone else's print.
    """
    hc = headline_class

    def everything(o: NormalisedObs) -> bool:
        return o.model_class == hc

    return {
        HEADLINE: (hc, factors.population_for("headline"), everything),
        "EU-CRI-H100-MKT": (hc, factors.population_for("marketplace"), everything),
        "EU-CRI-H100-NC": (hc, frozenset({"neocloud"}), everything),
        "EU-CRI-H100-HS": (hc, factors.population_for("hyperscaler"), everything),
        "EU-CRI-H100-SOV": (
            hc,
            factors.population_for("sovereign"),
            lambda o: o.model_class == hc and o.provider in sovereign,
        ),
    }


def _observations_for_date(conn: sqlite3.Connection, utc_date: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT o.* FROM observations o JOIN runs r ON o.run_id = r.run_id"
        " WHERE r.utc_date = ?",
        (utc_date,),
    ).fetchall()


def _store_intake(
    conn: sqlite3.Connection,
    utc_date: str,
    rows: Sequence[sqlite3.Row],
    factors: config.Factors,
    fx_eur_usd: float | None,
    run_id: str,
) -> None:
    """Record where every collected observation stopped, and log what stopped arriving.

    This runs inside the daily job on purpose. The two instruments the project already had
    for this class of failure - `tci.run reliability` and `tci.run sources` - are invoked
    by no workflow, so they only ever caught something when a person remembered to look.
    An instrument that depends on somebody looking is how a collector defect ran for five
    weeks and cost the headline nine prints.

    It cannot fail the run. A gap stays a gap and a print stays a print whatever the
    ledger says; this writes a record and logs a warning, and nothing here touches a
    number.
    """
    if not intake.stored(conn):
        # A database that predates migration 0010, which includes the throwaway copies
        # `reproduce` and `effect` recompute into. The ledger is an observer and must
        # never be the reason a recomputation of the published record fails.
        return
    cells = intake.ledger(rows, factors, fx_eur_usd)
    if not cells:
        return
    revision = intake.store(
        conn, utc_date, "EU_EEA", cells, factors.methodology_version, run_id
    )
    pct = intake.yield_pct(cells)
    log.info(
        "%s intake rev%d: %d observations, %d admitted (%.1f%%)",
        utc_date, revision, sum(c.n_rows for c in cells),
        intake.by_gate(cells).get("admitted", 0), pct or 0.0,
    )
    for d in intake.dropouts(conn, utc_date):
        log.warning(
            "%s intake: %s/%s %s admitted %.1f rows/session recently, none today (now %s)",
            utc_date, d.provider, d.source, d.model_class, d.was_admitted, d.gate,
        )
    for s in intake.shifts(conn, utc_date):
        log.warning(
            "%s intake: gate %s holds %d rows against a recent mean of %.1f",
            utc_date, s.gate, s.today, s.baseline,
        )


def _prev_prices(conn: sqlite3.Connection, series: str, before_date: str) -> dict[str, float]:
    return series_read.previous_included_prices(conn, series, before_date)


def _next_revision(conn: sqlite3.Connection, date: str, series: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(revision), 0) AS rev FROM daily_index"
        " WHERE date = ? AND series = ?",
        (date, series),
    ).fetchone()
    return int(row["rev"]) + 1


def _store_print(
    conn: sqlite3.Connection, print_: IndexPrint, version: str, run_id: str, flags: str = ""
) -> int:
    revision = _next_revision(conn, print_.date, print_.series)
    all_flags = ",".join(x for x in (print_.flags, flags) if x)
    with conn:
        conn.execute(
            "INSERT INTO daily_index (date, series, revision, value_usd, value_eur,"
            " fx_rate, fx_date, n_sources, n_executable, flags, methodology_version,"
            " computed_at, run_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                print_.date, print_.series, revision, print_.value_usd, print_.value_eur,
                print_.fx_rate, print_.fx_date, print_.n_sources, print_.n_executable,
                all_flags, version, db.utc_now_iso(), run_id,
            ),
        )
        conn.executemany(
            "INSERT INTO constituents (date, series, revision, provider, source, tier,"
            " price_usd, weight, included, exclusion_reason, flags)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    print_.date, print_.series, revision, c.provider, c.source, c.tier,
                    c.price_usd, c.weight, int(c.included), c.exclusion_reason, c.flags,
                )
                for c in print_.constituents
            ],
        )
    return revision


_latest_values = series_read.head_values


@dataclass(frozen=True)
class ReviewWeights:
    """One effective weight review, as stored in weight_sets."""

    effective_date: str
    window_start: str
    window_end: str
    n_days_window: int
    provider_by_class: dict[str, dict[str, weights.ReviewWeight]]
    model_shares: dict[str, float]


def _collection_dates(conn: sqlite3.Connection, start: str, end: str) -> list[str]:
    """Days in [start, end] with at least one stored observation."""
    return [
        r["utc_date"]
        for r in conn.execute(
            "SELECT DISTINCT r.utc_date AS utc_date FROM runs r"
            " JOIN observations o ON o.run_id = r.run_id"
            " WHERE r.utc_date >= ? AND r.utc_date <= ? ORDER BY r.utc_date",
            (start, end),
        )
    ]


def _load_review(
    conn: sqlite3.Connection, effective_date: str, version: str
) -> ReviewWeights | None:
    row = conn.execute(
        "SELECT MAX(revision) AS rev FROM weight_sets WHERE effective_date = ?",
        (effective_date,),
    ).fetchone()
    if row is None or row["rev"] is None:
        return None
    rows = conn.execute(
        "SELECT * FROM weight_sets WHERE effective_date = ? AND revision = ?",
        (effective_date, row["rev"]),
    ).fetchall()
    if not rows or rows[0]["methodology_version"] != version:
        return None  # recomputed under the current version as a new revision
    provider_by_class: dict[str, dict[str, weights.ReviewWeight]] = {}
    model_shares: dict[str, float] = {}
    for r in rows:
        if r["scope"] == "provider":
            provider_by_class.setdefault(r["model_class"], {})[r["key"]] = weights.ReviewWeight(
                weight=r["weight"], days_observed=r["n_days_observed"]
            )
        else:
            model_shares[r["key"]] = r["weight"]
    return ReviewWeights(
        effective_date=effective_date,
        window_start=rows[0]["window_start"],
        window_end=rows[0]["window_end"],
        n_days_window=rows[0]["n_days_window"],
        provider_by_class=provider_by_class,
        model_shares=model_shares,
    )


def _store_review(conn: sqlite3.Connection, rw: ReviewWeights, version: str) -> None:
    row = conn.execute(
        "SELECT COALESCE(MAX(revision), 0) AS rev FROM weight_sets WHERE effective_date = ?",
        (rw.effective_date,),
    ).fetchone()
    revision = int(row["rev"]) + 1
    now = db.utc_now_iso()
    entries = [
        (rw.effective_date, "provider", cls, provider, r.weight, rw.window_start,
         rw.window_end, r.days_observed, rw.n_days_window, revision, version, now)
        for cls, pset in rw.provider_by_class.items()
        for provider, r in pset.items()
    ] + [
        (rw.effective_date, "model", "", cls, share, rw.window_start,
         rw.window_end, rw.n_days_window, rw.n_days_window, revision, version, now)
        for cls, share in rw.model_shares.items()
    ]
    with conn:
        conn.executemany(
            "INSERT INTO weight_sets (effective_date, scope, model_class, key, weight,"
            " window_start, window_end, n_days_observed, n_days_window, revision,"
            " methodology_version, computed_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            entries,
        )


def _review_weights(
    conn: sqlite3.Connection, utc_date: str, factors: config.Factors
) -> ReviewWeights | None:
    """The weight review in effect for utc_date, computing and storing it when due.

    Returns None (bootstrap) while the trailing window holds fewer collection days
    than weights.review.min_history_days.
    """
    review_cfg = factors.weights.review
    effective = weights.review_effective_date(utc_date, review_cfg.anchor_weekday)
    stored = _load_review(conn, effective, factors.methodology_version)
    if stored is not None:
        return stored

    effective_d = date_type.fromisoformat(effective)
    window_end = (effective_d - timedelta(days=1)).isoformat()
    window_start = (effective_d - timedelta(days=review_cfg.window_days)).isoformat()
    days = _collection_dates(conn, window_start, window_end)
    if len(days) < review_cfg.min_history_days:
        return None

    per_class_daily: dict[str, dict[str, dict[str, tuple[str, int]]]] = {}
    class_capacity: dict[str, float] = {}
    for day in days:
        normalised = normalise_observations(_observations_for_date(conn, day), factors)
        by_class: dict[str, list[NormalisedObs]] = {}
        for o in normalised:
            by_class.setdefault(o.model_class, []).append(o)
        for cls, obs_list in by_class.items():
            summary = weights.daily_capacity(obs_list, factors)
            per_class_daily.setdefault(cls, {})[day] = summary
            class_capacity[cls] = class_capacity.get(cls, 0.0) + sum(
                cap for _, cap in summary.values()
            )

    provider_by_class = {
        cls: weights.provider_review_weights({d: daily.get(d, {}) for d in days}, factors)
        for cls, daily in per_class_daily.items()
    }
    rw = ReviewWeights(
        effective_date=effective,
        window_start=window_start,
        window_end=window_end,
        n_days_window=len(days),
        provider_by_class=provider_by_class,
        model_shares=weights.model_review_shares(class_capacity, factors.composite),
    )
    _store_review(conn, rw, factors.methodology_version)
    log.info(
        "weight review stored: effective %s, window %s..%s (%d collection days)",
        effective, window_start, window_end, len(days),
    )
    return rw


_latest_value_on = series_read.head_value


def _compute_composite(
    conn: sqlite3.Connection, utc_date: str, factors: config.Factors, rw: ReviewWeights
) -> IndexPrint:
    """EU-CRI-COMPUTE: chain-linked over class sub-indices with review basket shares.

    Links renormalise over classes with a published value on both endpoints, so a
    gapping class drops out of the day's link without gapping the composite.
    """
    shares = {cls: s for cls, s in rw.model_shares.items() if cls in SERIES_BY_CLASS}
    prev = conn.execute(
        "SELECT d.date, d.value_usd FROM daily_index d JOIN ("
        "  SELECT date, MAX(revision) AS rev FROM daily_index WHERE series = ?"
        "  GROUP BY date) m ON d.date = m.date AND d.revision = m.rev"
        " WHERE d.series = ? AND d.date < ? AND d.value_usd IS NOT NULL"
        " ORDER BY d.date DESC LIMIT 1",
        (COMPOSITE, COMPOSITE, utc_date),
    ).fetchone()
    today = {cls: _latest_value_on(conn, SERIES_BY_CLASS[cls], utc_date) for cls in shares}

    value: float | None
    if prev is None:
        linked = sorted(cls for cls, v in today.items() if v is not None)
        value = factors.composite.base_value if linked else None
        flags = "base" if linked else "no_linkable_series"
    else:
        prev_vals = {cls: _latest_value_on(conn, SERIES_BY_CLASS[cls], prev["date"])
                     for cls in shares}
        links = [
            (shares[cls], now, before)
            for cls in sorted(shares)
            if (now := today[cls]) is not None
            and (before := prev_vals[cls]) is not None
        ]
        linked = sorted(
            cls for cls in shares
            if today[cls] is not None and prev_vals[cls] is not None
        )
        if links:
            value = round(weights.chain_link(prev["value_usd"], links), 6)
            flags = ""
        else:
            value, flags = None, "no_linkable_series"

    total_linked = sum(shares[cls] for cls in linked)
    constituents = tuple(
        Constituent(
            provider=cls, source="composite", tier="index",
            price_usd=today_val if (today_val := today[cls]) is not None else 0.0,
            weight=round(shares[cls] / total_linked * 100.0, 6)
            if cls in linked and total_linked > 0 else round(shares[cls], 6),
            included=cls in linked,
            exclusion_reason=None if cls in linked else "no_print",
        )
        for cls in sorted(shares)
    )
    return IndexPrint(
        date=utc_date, series=COMPOSITE, value_usd=value, value_eur=None,
        fx_rate=None, fx_date=None, n_sources=len(linked), n_executable=0,
        flags=flags, constituents=constituents,
    )


_NO_OVERRIDE = object()


def compute_all_series(
    conn: sqlite3.Connection,
    utc_date: str,
    correction: bool = False,
    fx_override: object = _NO_OVERRIDE,
) -> None:
    """Compute and store every series for one date (new revisions, never edits).

    `fx_override` is for `reproduce` only: the FX a print actually used is an input
    recorded with the print, and a recomputation must convert at that rate, not at
    whatever rate the fx table offers today (see tci.collectors.fx.rate_for).
    """
    # The version live on the print date, not the head of the succession: an announced
    # version sits in the repository before its effective date and must not touch a
    # print dated earlier.
    factors = config.load_factors(for_date=utc_date)
    sovereign = config.load_sovereign(for_date=utc_date)
    version = factors.methodology_version
    fx: tuple[float, str] | None
    if fx_override is _NO_OVERRIDE:
        fx = rate_for(conn, utc_date, strictly_before=factors.fx.strictly_before)
    else:
        fx = fx_override  # type: ignore[assignment]
    if fx is None:
        log.warning("no FX rate stored yet; EUR series will be null")

    run_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO runs (run_id, utc_date, source, started_utc, status)"
        " VALUES (?, ?, 'index', ?, 'running')",
        (run_id, utc_date, db.utc_now_iso()),
    )
    conn.commit()

    rows = _observations_for_date(conn, utc_date)
    # FX is needed during normalisation: providers quoting in EUR (Scaleway) are converted
    # at print time from their native amount, never from a rate frozen at collection.
    normalised = normalise_observations(rows, factors, fx_eur_usd=fx[0] if fx else None)
    unadmitted = unadmitted_providers(rows, factors)
    _store_intake(conn, utc_date, rows, factors, fx[0] if fx else None, run_id)
    rw = _review_weights(conn, utc_date, factors)
    headline_class = factors.headline_class

    common_extra = "correction" if correction else ""
    # v0.3.0 weights providers by tier only, so a missing weight review no longer changes
    # the calculation and the bootstrap flag no longer applies.
    series_extra = common_extra

    definitions = _series_definitions(sovereign, headline_class, factors)
    observed_classes = {o.model_class for o in normalised}
    headline_pop = factors.population_for("headline")
    for cls, series_name in SERIES_BY_CLASS.items():
        if cls != headline_class and cls in observed_classes:
            definitions[series_name] = (
                cls, headline_pop, (lambda c: (lambda o: o.model_class == c))(cls)
            )

    computed: dict[str, IndexPrint] = {}
    for series, (cls, population, predicate) in definitions.items():
        subset: list[NormalisedObs] = [o for o in normalised if predicate(o)]
        result = compute_print(
            utc_date, series, subset, factors, fx,
            population=population,
            prev_prices=_prev_prices(conn, series, utc_date),
            not_in_panel=frozenset(p for p, classes in unadmitted.items() if cls in classes),
        )
        computed[series] = result
        revision = _store_print(conn, result, version, run_id, series_extra)
        log.info(
            "%s %s rev%d: %s (n=%d%s)", utc_date, series, revision,
            result.value_usd, result.n_sources,
            f", flags={result.flags}" if result.flags else "",
        )

    # Series in other region blocks (v0.6.0+): the same unit, estimator and gate, drawn
    # from another block's countries. Defined in factors.yaml, so hash-locked.
    fx_rate = fx[0] if fx else None
    for series, rs in factors.regional_series.items():
        countries = factors.countries_of(rs.block)
        block_rows = normalise_observations(rows, factors, fx_eur_usd=fx_rate, countries=countries)
        block_unadmitted = unadmitted_providers(rows, factors, countries)
        result = compute_print(
            utc_date, series,
            [o for o in block_rows if o.model_class == rs.model_class],
            factors, fx,
            population=factors.population_for(rs.population),
            prev_prices=_prev_prices(conn, series, utc_date),
            not_in_panel=frozenset(
                p for p, classes in block_unadmitted.items() if rs.model_class in classes
            ),
        )
        computed[series] = result
        _store_print(conn, result, version, run_id, series_extra)
    for series, bs in factors.basis_series.items():
        result = compute_basis(
            utc_date, series, computed.get(bs.lead), computed.get(bs.reference), fx
        )
        computed[series] = result
        _store_print(conn, result, version, run_id, series_extra)

    if rw is not None:
        composite = _compute_composite(conn, utc_date, factors, rw)
        _store_print(conn, composite, version, run_id, common_extra)
        log.info(
            "%s %s: %s (linked=%d%s)", utc_date, COMPOSITE, composite.value_usd,
            composite.n_sources,
            f", flags={composite.flags}" if composite.flags else "",
        )

    # 7-day mean of the headline (>=4 non-null of the trailing window)
    window = [
        (datetime.strptime(utc_date, "%Y-%m-%d") - timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(factors.aggregation.smoothing_days)
    ]
    values = [v for v in _latest_values(conn, HEADLINE, window) if v is not None]
    if len(values) >= 4:
        mean = round(sum(values) / len(values), 6)
        eur = round(mean / fx[0], 6) if fx else None
        smoothed = IndexPrint(
            date=utc_date, series=SERIES_7D, value_usd=mean, value_eur=eur,
            fx_rate=fx[0] if fx else None, fx_date=fx[1] if fx else None,
            n_sources=len(values), n_executable=0, flags="", constituents=(),
        )
    else:
        smoothed = IndexPrint(
            date=utc_date, series=SERIES_7D, value_usd=None, value_eur=None,
            fx_rate=fx[0] if fx else None, fx_date=fx[1] if fx else None,
            n_sources=len(values), n_executable=0, flags="insufficient_history",
            constituents=(),
        )
    _store_print(conn, smoothed, version, run_id, common_extra)

    with conn:
        conn.execute(
            "UPDATE runs SET status = 'ok', finished_utc = ? WHERE run_id = ?",
            (db.utc_now_iso(), run_id),
        )


def export_csv(conn: sqlite3.Connection, path: Path | None = None) -> Path:
    """Full history, latest revision per (date, series)."""
    target = path or CSV_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(
        "SELECT d.* FROM daily_index d JOIN ("
        "  SELECT date, series, MAX(revision) AS rev FROM daily_index GROUP BY date, series"
        ") m ON d.date = m.date AND d.series = m.series AND d.revision = m.rev"
        " ORDER BY d.date, d.series"
    ).fetchall()
    with open(target, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(
            ["date", "series", "value_usd", "value_eur", "fx_rate", "fx_date",
             "n_sources", "n_executable", "flags", "methodology_version", "revision"]
        )
        for r in rows:
            writer.writerow(
                [r["date"], r["series"], r["value_usd"], r["value_eur"], r["fx_rate"],
                 r["fx_date"], r["n_sources"], r["n_executable"], r["flags"],
                 r["methodology_version"], r["revision"]]
            )
    return target


def cmd_daily(args: argparse.Namespace) -> int:
    utc_date = args.date or datetime.now(UTC).strftime("%Y-%m-%d")
    conn = db.connect()
    db.migrate(conn)

    session = base.make_session()
    collect_fx(conn, session)  # fail-soft; calc falls back to last stored rate
    from tci.collectors.rates import collect_rates

    collect_rates(conn, session, utc_date)  # overlay only; restates prepaid term prices
    from tci.collectors.entsoe import collect_overlay

    collect_overlay(conn, session, utc_date)  # overlay only; skips without token

    # term.html research table only, never a print input (CLAUDE.md). refresh() is
    # already fail-soft on its own (network/shape/file failures all leave
    # config/term_schedules.yaml untouched and log a warning); this try/except is a second,
    # belt-and-braces layer so an unexpected error in wiring it up here still cannot take
    # the rest of the daily run down with it. Only run for today's own date: a --date
    # override recomputing an earlier day must not stamp a live fetch as verified in the
    # past (the same reasoning `backfill` uses to never re-collect).
    if utc_date == datetime.now(UTC).strftime("%Y-%m-%d"):
        try:
            from tci.collectors.term_schedule_refresh import refresh as refresh_term_schedules

            refresh_term_schedules(session=session, today=utc_date)
        except Exception:
            log.exception(
                "term schedule refresh failed unexpectedly; term.html falls back to the"
                " stored last_verified date"
            )

    statuses = {
        c.name: base.run_collector(conn, c, utc_date, session) for c in collectors_for_daily()
    }
    log.info("collector statuses: %s", statuses)
    if all(s == "failed" for s in statuses.values()):
        log.error("every collector failed — aborting without computing")
        return 1

    compute_all_series(conn, utc_date)
    export_csv(conn)
    _record_forward(conn, utc_date)
    return 0 if _maybe_outputs(conn, utc_date) else 1


def _record_forward(conn: sqlite3.Connection, utc_date: str) -> None:
    """The forward estimate ledger (tci.forward_data), after the prints, before outputs.

    Research beside the index on the same terms as the term and curve tables: a failure here
    is recorded in `runs` and costs the estimate, never the day's prints or site.
    """
    from tci import forward_data

    try:
        written = forward_data.record_forward(conn, utc_date)
        log.info("forward: %d ledger rows written", written)
    except Exception as exc:  # noqa: BLE001
        log.exception("forward estimate not recorded; prints and outputs unaffected")
        forward_data.note_failure(conn, utc_date, exc)


def _maybe_outputs(conn: sqlite3.Connection, utc_date: str) -> bool:
    """Charts + post + dashboard data + the published site.

    Never blocks the day's collection or print: by the time this runs, the observations
    and the print are stored, and those are the irreplaceable half. A crash here is
    caught so the caller still commits them.

    It does fail the run, though. Until 2026-09-12 the exception was logged and
    `cmd_daily` returned 0 regardless, which is the worst of both worlds: the database
    gains a print, `site/data/prints/` never does, the workflow goes green, and the
    digest check walks only the files that exist and so reports nothing wrong. The
    published site silently falls a day behind the record it claims to publish. A
    non-zero exit here goes red while the commit step — which runs on failure too —
    still saves the data.
    """
    from tci.outputs import charts, post, site, webdata

    try:
        charts.generate_all(conn)
        post.generate_post(conn)
        webdata.generate(conn)
        site.generate(conn)
    except Exception as exc:
        log.exception("output generation failed; the print is stored but not published")
        # CI logs age out and are not public. `runs.notes` is what the workflow's
        # diagnostic step reads and what survives in the committed database.
        with conn:
            conn.execute(
                "INSERT INTO runs (run_id, utc_date, source, started_utc, finished_utc,"
                " status, notes) VALUES (?, ?, 'outputs', ?, ?, 'failed', ?)",
                (str(uuid.uuid4()), utc_date, db.utc_now_iso(), db.utc_now_iso(),
                 f"{type(exc).__name__}: {exc}"[:500]),
            )
        return False
    return True


def cmd_constituents(args: argparse.Namespace) -> int:
    conn = db.connect()
    row = conn.execute(
        "SELECT MAX(revision) AS rev FROM daily_index WHERE date = ? AND series = ?",
        (args.date, args.series),
    ).fetchone()
    if row is None or row["rev"] is None:
        print(f"no print for {args.date} {args.series}")
        return 1
    head = conn.execute(
        "SELECT * FROM daily_index WHERE date = ? AND series = ? AND revision = ?",
        (args.date, args.series, row["rev"]),
    ).fetchone()
    print(
        f"{args.series} {args.date} rev{head['revision']}"
        f" value_usd={head['value_usd']} value_eur={head['value_eur']}"
        f" n_sources={head['n_sources']} flags={head['flags'] or '-'}"
        f" methodology={head['methodology_version']}"
    )
    cols = f"{'provider':<16}{'source':<14}{'tier':<12}{'price_usd':>10}{'weight':>8}  status"
    print(cols)
    print("-" * len(cols))
    for c in conn.execute(
        "SELECT * FROM constituents WHERE date = ? AND series = ? AND revision = ?"
        " ORDER BY included DESC, price_usd",
        (args.date, args.series, row["rev"]),
    ):
        status = "included" if c["included"] else f"EXCLUDED ({c['exclusion_reason']})"
        if c["included"] and c["exclusion_reason"]:
            status += f" ({c['exclusion_reason']})"
        if c["flags"]:
            status += f" [{c['flags']}]"
        print(
            f"{c['provider']:<16}{c['source']:<14}{c['tier']:<12}"
            f"{c['price_usd']:>10.4f}{c['weight']:>8.1f}  {status}"
        )
    return 0


def cmd_weights(args: argparse.Namespace) -> int:
    """Show (computing and storing if due) the weight review in effect for a date."""
    conn = db.connect()
    db.migrate(conn)
    utc_date = args.date or datetime.now(UTC).strftime("%Y-%m-%d")
    factors = config.load_factors(for_date=utc_date)
    effective = weights.review_effective_date(
        utc_date, factors.weights.review.anchor_weekday
    )
    rw = _review_weights(conn, utc_date, factors)
    if rw is None:
        print(
            f"no weight review in effect for {utc_date} (review date {effective}):"
            f" fewer than {factors.weights.review.min_history_days} collection days in the"
            f" trailing {factors.weights.review.window_days}-day window."
            " Bootstrap weighting (same-day capacity) applies; prints are flagged"
            " 'bootstrap_weights'."
        )
        return 0
    print(
        f"weight review effective {rw.effective_date}"
        f" (window {rw.window_start}..{rw.window_end},"
        f" {rw.n_days_window} collection days)"
    )
    for cls in sorted(rw.provider_by_class):
        pset = rw.provider_by_class[cls]
        total = sum(r.weight for r in pset.values())
        print(f"\n{cls} provider review weights (shares shown pre-concentration-cap):")
        header = f"{'provider':<20}{'weight':>10}{'share':>9}{'days':>6}"
        print(header)
        print("-" * len(header))
        for provider, r in sorted(pset.items(), key=lambda kv: -kv[1].weight):
            print(
                f"{provider:<20}{r.weight:>10.2f}{r.weight / total * 100:>8.1f}%"
                f"{r.days_observed:>6}"
            )
    print(f"\nmodel basket shares ({COMPOSITE}):")
    for cls, share in sorted(rw.model_shares.items(), key=lambda kv: -kv[1]):
        print(f"  {cls:<8}{share:>7.2f}%")
    return 0


def cmd_backfill(args: argparse.Namespace) -> int:
    conn = db.connect()
    db.migrate(conn)
    start = datetime.strptime(args.date_from, "%Y-%m-%d")
    end = datetime.strptime(args.date_to, "%Y-%m-%d")
    if end < start:
        raise SystemExit("--to before --from")
    day = start
    while day <= end:
        utc_date = day.strftime("%Y-%m-%d")
        compute_all_series(conn, utc_date, correction=True)
        day += timedelta(days=1)
    export_csv(conn)
    return 0


def cmd_post(args: argparse.Namespace) -> int:
    from tci.outputs import charts, post

    conn = db.connect()
    charts.generate_all(conn)
    print(post.generate_post(conn))
    return 0


def dispatch(args: argparse.Namespace) -> int:
    if args.command == "daily":
        return cmd_daily(args)
    if args.command == "constituents":
        return cmd_constituents(args)
    if args.command == "backfill":
        return cmd_backfill(args)
    if args.command == "weights":
        return cmd_weights(args)
    if args.command == "post":
        return cmd_post(args)
    if args.command == "validate":
        from tci.validate import run_validate

        return run_validate(db.connect())
    raise SystemExit(f"command {args.command!r} is not implemented yet")
