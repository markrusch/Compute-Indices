# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Load the intraday log into the database's intraday tables (migration 0011).

The log in data/intraday/ is what the hourly job read, hour by hour, verified against its
hashes. This copies it into SQLite so the reads can be queried beside the fixing's own:

    SELECT at_utc, provider, price_usd_per_gpu_hr, n FROM intraday_prices
    WHERE source = 'vast_ai' AND gpu_model = 'H100_SXM' AND country = 'NL'
    ORDER BY at_utc;

It is kept apart from tci.intraday on purpose: that module must never be able to write to
the record (tests/test_intraday.py reads its source for INSERT/UPDATE/DELETE and fails on
any). This one writes only to the five intraday tables, never to anything a print reads.

Idempotent by sweep id. A sweep already loaded is skipped whole; a new one is loaded in one
transaction with its reads, its books and any row content not seen before, so a crash
mid-load leaves the sweep absent rather than half-present, and the next load completes it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from collections import Counter
from dataclasses import dataclass

from tci import intraday
from tci.db import utc_now_iso

log = logging.getLogger("tci.intraday_db")

TABLES = ("intraday_sweeps", "intraday_reads", "intraday_books", "intraday_book_rows",
          "intraday_rows")


@dataclass(frozen=True)
class LoadResult:
    sweeps: int  # sweeps loaded by this call
    reads: int
    new_rows: int  # distinct row contents not seen before
    skipped: int  # sweeps already in the database
    problems: dict[str, str]  # log segments that did not fully verify


def available(conn: sqlite3.Connection) -> bool:
    """False on a database that predates migration 0011, which a load must not crash on."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'intraday_sweeps'"
    ).fetchone()
    return row is not None


def _row_hash(source: str, row: intraday.Row) -> str:
    canonical = json.dumps([source, *row], separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def _row_id(conn: sqlite3.Connection, source: str, row: intraday.Row) -> tuple[int, bool]:
    h = _row_hash(source, row)
    found = conn.execute("SELECT row_id FROM intraday_rows WHERE row_hash = ?", (h,)).fetchone()
    if found is not None:
        return int(found[0]), False
    cur = conn.execute(
        "INSERT INTO intraday_rows (row_hash, source, provider, gpu_model, gpu_count,"
        " price_usd_per_gpu_hr, region, country, interconnect, tier, term, raw_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (h, source, *row),
    )
    return int(cur.lastrowid or 0), True


def _store_book(conn: sqlite3.Connection, source: str, book: str,
                rows: tuple[intraday.Row, ...]) -> int:
    """The book's rows, once per (source, book). Returns how many row contents were new."""
    if conn.execute("SELECT 1 FROM intraday_books WHERE source = ? AND book = ?",
                    (source, book)).fetchone():
        return 0
    conn.execute("INSERT INTO intraday_books (source, book, n_rows) VALUES (?, ?, ?)",
                 (source, book, len(rows)))
    new = 0
    counts: Counter[int] = Counter()
    for row in rows:
        row_id, created = _row_id(conn, source, row)
        counts[row_id] += 1
        new += int(created)
    conn.executemany(
        "INSERT INTO intraday_book_rows (source, book, row_id, n) VALUES (?, ?, ?, ?)",
        [(source, book, row_id, n) for row_id, n in sorted(counts.items())],
    )
    return new


def load(conn: sqlite3.Connection, store: intraday.Store | None = None,
         days: list[str] | None = None) -> LoadResult:
    """Load every verified sweep in the log that the database does not hold yet."""
    store = store or intraday.Store()
    loaded = conn.execute("SELECT sweep_id FROM intraday_sweeps").fetchall()
    have = {r[0] for r in loaded}
    sweeps = reads = new_rows = skipped = 0
    for day in days if days is not None else store.days():
        for path in store.segments(day):
            seg = store.load_segment(path)
            for sw in seg.sweeps:
                if sw.id in have:
                    skipped += 1
                    continue
                with conn:  # one transaction per sweep: present whole or not at all
                    conn.execute(
                        "INSERT INTO intraday_sweeps (sweep_id, started_utc, at_utc, segment,"
                        " ingested_utc) VALUES (?, ?, ?, ?, ?)",
                        (sw.id, intraday._iso(sw.started), intraday._iso(sw.at),
                         f"{path.parent.name}/{path.name}", utc_now_iso()),
                    )
                    for source, r in sorted(sw.sources.items()):
                        if r.status == "ok" and r.book is not None:
                            new_rows += _store_book(conn, source, r.book, seg.books[r.book][1])
                        conn.execute(
                            "INSERT INTO intraday_reads (sweep_id, source, status, at_utc,"
                            " book, n_rows, dropped, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (sw.id, source, r.status, intraday._iso(r.at),
                             r.book if r.status == "ok" else None, r.n, r.dropped, r.error),
                        )
                        reads += 1
                have.add(sw.id)
                sweeps += 1
    return LoadResult(sweeps, reads, new_rows, skipped, dict(store.problems))


def book_rows(conn: sqlite3.Connection, source: str, book: str) -> tuple[intraday.Row, ...]:
    """A stored book read back as log rows, for checking a load against the log's hash."""
    out: list[intraday.Row] = []
    for r in conn.execute(
        "SELECT x.*, br.n FROM intraday_book_rows br JOIN intraday_rows x"
        " ON x.row_id = br.row_id WHERE br.source = ? AND br.book = ?", (source, book)
    ):
        row: intraday.Row = (
            r["provider"], r["gpu_model"], r["gpu_count"], r["price_usd_per_gpu_hr"],
            r["region"], r["country"], r["interconnect"], r["tier"], r["term"], r["raw_json"],
        )
        out.extend([row] * int(r["n"]))
    return tuple(out)
