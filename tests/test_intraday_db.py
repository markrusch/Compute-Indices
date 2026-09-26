# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""The intraday tables: loaded from the log exactly, once, append-only, and never a print.

The log in data/intraday/ is the record the hourly job wrote. The tables are the same reads
in SQL, loaded by the daily run. What has to hold is that a load is lossless (every book
reads back to the hash the log verified), idempotent (the daily job's reset-and-rerun on a
push race loads nothing twice), and that nothing a print reads changes.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tci import intraday, intraday_db
from tci.models import Observation

T0 = datetime(2026, 9, 20, 6, 17, tzinfo=UTC)


def _obs(provider: str, price: float, count: int = 8, raw: dict | None = None
         ) -> Observation:
    return Observation(
        ts_utc="2026-09-20T06:17:00Z", source="fake", provider=provider,
        gpu_model="H100_SXM", gpu_count=count, price_usd_per_gpu_hr=price, region="eu-1",
        country="NL", interconnect="NVLink", tier="executable", term="on_demand",
        raw_json=json.dumps(raw or {}),
    )


class Books:
    def __init__(self, name: str, books: list[list[Observation]],
                 fail_at: frozenset[int] = frozenset()) -> None:
        self.name, self.books, self.fail_at, self.calls = name, books, fail_at, 0

    def collect(self, session: object) -> list[Observation]:
        self.calls += 1
        if self.calls - 1 in self.fail_at:
            raise ConnectionError("HTTP 502")
        return self.books[min(self.calls - 1, len(self.books) - 1)]


def _cfg(*names: str) -> intraday.IntradayConfig:
    return intraday.IntradayConfig(
        due_slack_minutes=20,
        sources={n: intraday.Cadence(1, 3) for n in names},
        series=("EU-CRI-H100",),
        settlement=intraday.SettlementWindow("08:00", "11:00", "mean", 2),
        fixing_guard=None,
    )


def _store_with_sweeps(tmp_path: Path, hours: int = 4) -> intraday.Store:
    store = intraday.Store(tmp_path / "log")
    vast = Books("vast", [
        [_obs("vast.ai", 2.0 + h / 100), _obs("vast.ai", 2.5), _obs("vast.ai", 2.5)]
        for h in range(hours)
    ])
    cat = Books("cat", [[_obs("aws", 7.36, raw={"currency": "USD"})]], fail_at=frozenset({2}))
    for h in range(hours):
        intraday.run_sweep(store, None, _cfg("vast", "cat"), collectors=[vast, cat],
                           now=lambda h=h: T0 + timedelta(hours=h),  # type: ignore[misc]
                           session=object())  # type: ignore[arg-type]
    return intraday.Store(tmp_path / "log")


def test_a_load_is_lossless(tmp_path: Path, conn: sqlite3.Connection) -> None:
    store = _store_with_sweeps(tmp_path)
    result = intraday_db.load(conn, store)
    assert result.sweeps == 4 and result.reads == 8 and result.skipped == 0

    # Every stored book reads back to exactly the rows the log verified against its hash,
    # duplicates included.
    for source, book in conn.execute("SELECT source, book FROM intraday_books"):
        assert intraday.book_hash(intraday_db.book_rows(conn, source, book)) == book

    # The view gives one row per offer per read, with identical offers counted in `n`.
    first = conn.execute(
        "SELECT price_usd_per_gpu_hr, n FROM intraday_prices WHERE source = 'vast'"
        " AND at_utc = ? ORDER BY price_usd_per_gpu_hr", ("2026-09-20T06:17:00Z",)
    ).fetchall()
    assert [(r[0], r[1]) for r in first] == [(2.0, 1), (2.5, 2)]

    failed = conn.execute(
        "SELECT status, book, error FROM intraday_reads WHERE source = 'cat'"
        " ORDER BY at_utc").fetchall()
    assert [r[0] for r in failed] == ["ok", "ok", "failed", "ok"]
    assert failed[2][1] is None and "502" in failed[2][2]


def test_unchanged_content_is_stored_once(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """The catalog read four times is one book of one row; the marketplace's unchanged
    offer at $2.50 is one row content however many hours it stays listed."""
    intraday_db.load(conn, _store_with_sweeps(tmp_path))
    assert conn.execute("SELECT COUNT(*) FROM intraday_books WHERE source = 'cat'"
                        ).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM intraday_rows WHERE source = 'vast'"
                        " AND price_usd_per_gpu_hr = 2.5").fetchone()[0] == 1


def test_a_second_load_adds_nothing_and_a_later_one_adds_only_new_sweeps(
        tmp_path: Path, conn: sqlite3.Connection) -> None:
    store = _store_with_sweeps(tmp_path, hours=2)
    intraday_db.load(conn, store)
    again = intraday_db.load(conn, intraday.Store(tmp_path / "log"))
    assert again.sweeps == 0 and again.skipped == 2

    more = intraday.Store(tmp_path / "log")
    c = Books("vast", [[_obs("vast.ai", 3.0)]])
    intraday.run_sweep(more, None, _cfg("vast"), collectors=[c],
                       now=lambda: T0 + timedelta(hours=5),
                       session=object())  # type: ignore[arg-type]
    later = intraday_db.load(conn, intraday.Store(tmp_path / "log"))
    assert later.sweeps == 1 and later.skipped == 2
    assert conn.execute("SELECT COUNT(*) FROM intraday_sweeps").fetchone()[0] == 3


def test_the_intraday_tables_are_append_only(tmp_path: Path, conn: sqlite3.Connection) -> None:
    intraday_db.load(conn, _store_with_sweeps(tmp_path))
    for table in intraday_db.TABLES:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(f"DELETE FROM {table}")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE intraday_rows SET price_usd_per_gpu_hr = 0")


def test_a_load_never_touches_what_a_print_reads(tmp_path: Path, conn: sqlite3.Connection
                                                 ) -> None:
    before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("observations", "runs", "daily_index", "constituents")}
    intraday_db.load(conn, _store_with_sweeps(tmp_path))
    after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in before}
    assert after == before


def test_a_damaged_log_loads_what_verified(tmp_path: Path, conn: sqlite3.Connection) -> None:
    store = _store_with_sweeps(tmp_path)
    path = store.path_for("2026-09-20")
    lines = path.read_text().splitlines(keepends=True)
    path.write_text("".join(lines[:-1]) + '{"v":1,"kind":"sweep",BROKEN\n', encoding="utf-8")
    result = intraday_db.load(conn, intraday.Store(tmp_path / "log"))
    assert result.sweeps == 3 and path.name in result.problems


def test_the_daily_run_survives_a_failing_load(monkeypatch: pytest.MonkeyPatch,
                                               conn: sqlite3.Connection) -> None:
    from tci import commands

    def boom(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(intraday_db, "load", boom)
    commands._load_intraday(conn)  # logged and swallowed, never raised


def test_a_database_before_migration_0011_is_skipped() -> None:
    old = sqlite3.connect(":memory:")
    assert not intraday_db.available(old)
    from tci import commands

    commands._load_intraday(old)  # no tables, no error
