# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from tci import db


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = db.connect(tmp_path / "test.db")
    db.migrate(c)
    return c


def insert_run(c: sqlite3.Connection, run_id: str = "r1", utc_date: str = "2026-07-18",
               source: str = "test") -> str:
    c.execute(
        "INSERT INTO runs (run_id, utc_date, source, started_utc, status) "
        "VALUES (?, ?, ?, ?, 'ok')",
        (run_id, utc_date, source, db.utc_now_iso()),
    )
    return run_id


@pytest.fixture()
def unpanelled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Load parameter sets with the explicit panel switched off.

    For tests of calculation mechanics that use invented provider names. The panel's own
    behaviour is tested separately in tests/test_panel.py; everywhere else it would
    exclude the synthetic providers as not_in_panel and the test would stop testing what
    it was written for.
    """
    from dataclasses import replace

    from tci import config

    original = config.load_factors

    def load(*args: object, **kwargs: object) -> config.Factors:
        return replace(original(*args, **kwargs), panel=None)  # type: ignore[arg-type]

    monkeypatch.setattr(config, "load_factors", load)


def _record_snapshot(path: Path) -> tuple[str, str]:
    """The committed database as content, not as one file.

    `data/eucri.db` is in WAL mode, so a committed write lands in `eucri.db-wal` and the
    main file's bytes do not change until a checkpoint — which sqlite runs when the last
    connection closes, i.e. after pytest has finished. Hashing only the main file inside
    the test session therefore reads a pre-checkpoint snapshot and sees nothing, which is
    how the first version of this guard passed a test that was demonstrably writing.
    `-shm` is deliberately not hashed: a pure read changes it. Neither is an EMPTY `-wal`,
    which sqlite creates on the first connection whether or not anything is written — a
    read-only test would otherwise be reported as a writer the moment it opened the file.
    An empty WAL carries no committed change, and a real write fills it (12 KB for a
    single inserted row) or is checkpointed into the main file, which is hashed.
    """
    wal = path.with_name(path.name + "-wal")
    wal_bytes = wal.read_bytes() if wal.exists() else b""
    return (
        hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "",
        hashlib.sha256(wal_bytes).hexdigest() if wal_bytes else "",
    )


@pytest.fixture(autouse=True, scope="session")
def _committed_database_is_read_only() -> Iterator[None]:
    """No test may modify `data/eucri.db`.

    Several tests read the committed database on purpose — `test_reproduce` has to, since
    the record is the thing under test. Reading it means calling `db.connect()` with no
    path, and one keystroke separates that from a connection a test then writes to. The
    database holds live prices that cannot honestly be re-collected, and the next daily
    run would commit a stray write before anyone read the diff.

    Written after a test added during the 2026-09-12 audit inserted a `runs` row into the
    real file. It was caught by `git status`, which is not a control.
    """
    path = Path(__file__).resolve().parents[1] / "data" / "eucri.db"
    before = _record_snapshot(path)
    yield
    assert _record_snapshot(path) == before, (
        f"a test modified the committed database ({path}). Use the `conn` fixture, which "
        "builds a fresh one under tmp_path, rather than db.connect() with no argument."
    )
