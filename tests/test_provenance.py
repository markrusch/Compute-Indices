"""Every collection and index run records the commit of the code that ran it.

`runs.git_sha` existed from migration 0001 and was never written, so none of the 791 runs
in the record up to 10 October 2026 says which code produced it. v0.11.1 showed a print
can turn on the interpreter; without the commit, nobody can say which code a published
print came from, only that today's code happens to reproduce it.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from tci import db
from tci.collectors import base
from tci.models import Observation

SHA = re.compile(r"^[0-9a-f]{40}(-dirty)?$")


class _NoRows:
    name = "provenance_probe"

    def collect(self, session: object) -> list[Observation]:
        return []


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = db.connect(tmp_path / "t.db")
    db.migrate(c)
    return c


def test_code_sha_names_a_commit_in_a_checkout() -> None:
    if not (Path(db.__file__).resolve().parents[2] / ".git").exists():
        pytest.skip("not a git checkout")
    assert SHA.match(db.code_sha() or ""), db.code_sha()


def test_a_collector_run_records_the_commit(conn: sqlite3.Connection) -> None:
    base.run_collector(conn, _NoRows(), "2026-10-10")  # type: ignore[arg-type]
    row = conn.execute(
        "SELECT git_sha FROM runs WHERE source = 'provenance_probe'"
    ).fetchone()
    assert row["git_sha"] == db.code_sha()


def test_code_sha_falls_back_to_the_actions_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_git(*a: object, **k: object) -> None:
        raise FileNotFoundError("git")

    db.code_sha.cache_clear()
    monkeypatch.setattr(db.subprocess, "run", no_git)
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    try:
        assert db.code_sha() == "a" * 40
    finally:
        db.code_sha.cache_clear()
