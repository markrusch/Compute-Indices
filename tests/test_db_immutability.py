# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Append-only guarantees are enforced by the database, not by convention."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tci import db
from tests.conftest import insert_run


def _insert_observation(conn: sqlite3.Connection) -> None:
    insert_run(conn)
    conn.execute(
        "INSERT INTO observations (run_id, ts_utc, source, provider, gpu_model, gpu_count,"
        " price_usd_per_gpu_hr, country, tier, raw_json)"
        " VALUES ('r1', ?, 'test', 'prov', 'H100_SXM', 8, 2.0, 'NL', 'executable', '{}')",
        (db.utc_now_iso(),),
    )


def test_observations_reject_update(conn: sqlite3.Connection) -> None:
    _insert_observation(conn)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE observations SET price_usd_per_gpu_hr = 0")


def test_observations_reject_delete(conn: sqlite3.Connection) -> None:
    _insert_observation(conn)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("DELETE FROM observations")


def test_daily_index_reject_update_and_delete(conn: sqlite3.Connection) -> None:
    insert_run(conn)
    conn.execute(
        "INSERT INTO daily_index (date, series, revision, value_usd, n_sources,"
        " n_executable, methodology_version, computed_at, run_id)"
        " VALUES ('2026-07-18', 'EU-CRI-H100', 1, 2.0, 6, 2, '0.1.0-dev', ?, 'r1')",
        (db.utc_now_iso(),),
    )
    with pytest.raises(sqlite3.IntegrityError, match="revision"):
        conn.execute("UPDATE daily_index SET value_usd = 99")
    with pytest.raises(sqlite3.IntegrityError, match="revision"):
        conn.execute("DELETE FROM daily_index")


def test_migrations_are_idempotent(conn: sqlite3.Connection) -> None:
    assert db.migrate(conn) == []  # conftest already migrated; second pass is a no-op


def test_a_failed_migration_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`executescript` inside `with conn:` committed every statement before the failure.

    This one creates a table, drops the observations update guard, then fails. None of
    it may survive: no table, the guard still in place, and no schema_migrations row, so
    the next run tries the migration again from the start.
    """
    c = db.connect(tmp_path / "m.db")
    shipped = db._migration_files()
    bad = (
        "CREATE TABLE half_applied (x INTEGER);\n"
        "DROP TRIGGER obs_no_update;\n"
        "SELECT no_such_column FROM half_applied;\n"
    )
    monkeypatch.setattr(db, "_migration_files", lambda: [*shipped, (99, "0099_bad.sql", bad)])

    with pytest.raises(sqlite3.OperationalError):
        db.migrate(c)

    names = {r[0] for r in c.execute("SELECT name FROM sqlite_master")}
    assert "half_applied" not in names
    assert "obs_no_update" in names
    assert c.execute("SELECT COUNT(*) FROM schema_migrations WHERE id = 99").fetchone()[0] == 0
    # The shipped migrations before it committed one by one and stay applied.
    assert c.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == len(shipped)
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_migrating_from_empty_restores_foreign_keys(tmp_path: Path) -> None:
    """0003 and 0004 need foreign keys off for their table rebuilds; they must come back on."""
    c = db.connect(tmp_path / "fresh.db")
    assert db.migrate(c)
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert not c.in_transaction
