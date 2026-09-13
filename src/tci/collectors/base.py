# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Collector infrastructure: polite HTTP, fail-soft execution, per-day idempotency.

Rules (SOURCES.md): 1 request per source per day, honest User-Agent, 30s timeout,
and a collector failure is logged and skipped — it never blocks the run and never
fabricates data.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from typing import Protocol

import requests

from tci import USER_AGENT
from tci.db import utc_now_iso
from tci.models import MarketOffer, Observation

log = logging.getLogger("tci.collectors")

TIMEOUT_SECONDS = 30


class Collector(Protocol):
    name: str

    def collect(self, session: requests.Session) -> list[Observation]: ...


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    return session


def has_ok_run(conn: sqlite3.Connection, source: str, utc_date: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM runs WHERE utc_date = ? AND source = ? AND status = 'ok' LIMIT 1",
        (utc_date, source),
    ).fetchone()
    return row is not None


def run_collector(
    conn: sqlite3.Connection,
    collector: Collector,
    utc_date: str,
    session: requests.Session | None = None,
) -> str:
    """Run one collector fail-soft; returns the resulting run status.

    Skips (idempotency) if an 'ok' run already exists for (source, utc_date).
    Observations are inserted in the same transaction as the run row update.
    """
    if has_ok_run(conn, collector.name, utc_date):
        log.info("%s: already collected for %s, skipping", collector.name, utc_date)
        return "skipped"

    run_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO runs (run_id, utc_date, source, started_utc, status)"
        " VALUES (?, ?, ?, ?, 'running')",
        (run_id, utc_date, collector.name, utc_now_iso()),
    )
    conn.commit()
    # Fetch AND persist inside one fail-soft boundary.
    #
    # Until 2026-09-11 only the fetch was guarded, and the insert sat outside it. A
    # collector could therefore return data the schema refused, and the resulting
    # IntegrityError escaped this function and killed the whole daily run: no index
    # computed, no site regenerated, nothing committed, for every other source too. That
    # is exactly what happened when the tenor collectors began emitting tier='spot'
    # against a CHECK that allowed only executable and list. Four sessions went dark for
    # one collector's bad row.
    #
    # A source that cannot be collected, parsed OR stored is a source that did not report
    # today. That is a gap, the index already knows how to publish gaps, and it is not a
    # reason to stop publishing the sources that did report.
    try:
        observations = collector.collect(session or make_session())
        with conn:
            conn.executemany(
                "INSERT INTO observations (run_id, ts_utc, source, provider, gpu_model,"
                " gpu_count, price_usd_per_gpu_hr, region, country, interconnect, tier,"
                " term, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        run_id, o.ts_utc, o.source, o.provider, o.gpu_model, o.gpu_count,
                        o.price_usd_per_gpu_hr, o.region, o.country, o.interconnect,
                        o.tier, o.term, o.raw_json,
                    )
                    for o in observations
                ],
            )
    except Exception as exc:
        log.exception("%s: run failed (fail-soft, continuing)", collector.name)
        with conn:
            conn.execute(
                "UPDATE runs SET status = 'failed', finished_utc = ?, notes = ?"
                " WHERE run_id = ?",
                # The reason is stored, not just logged. CI logs age out and are not
                # public; `runs` is committed, so a failure stays diagnosable later.
                (utc_now_iso(), f"{type(exc).__name__}: {exc}"[:500], run_id),
            )
        return "failed"

    notes = f"{len(observations)} observations"
    book: list[MarketOffer] = getattr(collector, "offer_book", None) or []
    if book:
        notes += "; " + _store_offer_book(conn, collector.name, run_id, book)

    with conn:
        conn.execute(
            "UPDATE runs SET status = 'ok', finished_utc = ?, notes = ? WHERE run_id = ?",
            (utc_now_iso(), notes, run_id),
        )
    log.info("%s: %d observations", collector.name, len(observations))
    return "ok"


def _store_offer_book(
    conn: sqlite3.Connection, name: str, run_id: str, book: list[MarketOffer]
) -> str:
    """Store the full offer book a collector read, after its prices are already committed.

    Deliberately outside the observations transaction and fail-soft on its own. The book
    is research data that no print reads; a schema problem here must cost the book, not
    the day's prices. The outcome goes into `runs.notes` either way, so a lost book is
    visible from the committed database rather than only from a CI log.
    """
    try:
        with conn:
            conn.executemany(
                "INSERT INTO market_offers (run_id, ts_utc, source, queried_name, offer_id,"
                " machine_id, host_id, gpu_model, num_gpus, dph_total, country,"
                " verification, hosting_type, in_index_scope, raw_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        run_id, b.ts_utc, b.source, b.queried_name, b.offer_id,
                        b.machine_id, b.host_id, b.gpu_model, b.num_gpus, b.dph_total,
                        b.country, b.verification, b.hosting_type, int(b.in_index_scope),
                        b.raw_json,
                    )
                    for b in book
                ],
            )
    except Exception as exc:
        log.exception("%s: offer book not stored (prices unaffected)", name)
        return f"offer book not stored: {type(exc).__name__}: {exc}"[:300]
    return f"{len(book)} market offers"
