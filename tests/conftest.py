# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
from __future__ import annotations

import sqlite3
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
