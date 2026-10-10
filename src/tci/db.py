# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""SQLite connection and migrations.

Raw tables (observations, daily_index) are append-only, enforced by triggers
created in the migrations — corrections happen as new revisions, never edits.
"""

from __future__ import annotations

import functools
import os
import re
import sqlite3
import subprocess
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "eucri.db"

_MIGRATION_RE = re.compile(r"^(\d{4})_.+\.sql$")


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def connect_readonly(db_path: Path | None = None) -> sqlite3.Connection:
    """A connection that cannot write, for jobs that only read the record."""
    path = (db_path or DEFAULT_DB_PATH).resolve()
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


_REPO_ROOT = Path(__file__).resolve().parents[2]


@functools.lru_cache(maxsize=1)
def code_sha() -> str | None:
    """The commit the running code came from, for `runs.git_sha`.

    The column has existed since migration 0001 and was never filled: none of the 791 runs
    in the record up to 10 October 2026 says which code collected or computed it. That
    mattered the day v0.11.1 showed a print can depend on the interpreter: `reproduce`
    can only claim that today's code gives yesterday's numbers, not that yesterday's
    numbers came from a given commit. A tree with uncommitted changes under src/ or
    config/ is marked `-dirty`, because then the commit alone does not describe the code
    that ran. Outside a git checkout it falls back to Actions' GITHUB_SHA, then to None.
    It never raises: provenance is an observer and must not cost a run.
    """
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(_REPO_ROOT), *args],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()

    try:
        sha = git("rev-parse", "HEAD")
        dirty = git("status", "--porcelain", "--untracked-files=no", "--", "src", "config")
        return f"{sha}-dirty" if dirty else sha
    except (OSError, subprocess.SubprocessError):
        return os.environ.get("GITHUB_SHA") or None


def _migration_files() -> list[tuple[int, str, str]]:
    """Return (number, name, sql) for bundled migrations, sorted."""
    out = []
    for entry in resources.files("tci.migrations").iterdir():
        m = _MIGRATION_RE.match(entry.name)
        if m:
            out.append((int(m.group(1)), entry.name, entry.read_text(encoding="utf-8")))
    return sorted(out)


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Apply pending migrations; returns the names applied. Idempotent."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, applied_utc TEXT NOT NULL)"
    )
    done = {row["name"] for row in conn.execute("SELECT name FROM schema_migrations")}
    applied = []
    for number, name, sql in _migration_files():
        if name in done:
            continue
        _apply_migration(conn, number, name, sql)
        applied.append(name)
    return applied


def _apply_migration(conn: sqlite3.Connection, number: int, name: str, sql: str) -> None:
    """Apply one migration and record it, all or nothing.

    This used to run `executescript` inside `with conn:`, which looks transactional and
    is not: `executescript` commits whatever is pending and then runs each statement in
    autocommit. A migration that failed halfway kept the statements before the failure
    and had no `schema_migrations` row. 0003 and 0004 rebuild `observations` by drop and
    recreate, so a failure between the drop of the old triggers and the creation of the
    new ones would have left the table without its append-only guard.

    The script now runs inside an explicit BEGIN and is rolled back on any error.
    Foreign keys are switched off around the transaction rather than inside it, because
    SQLite ignores `PRAGMA foreign_keys` within a transaction, which makes the pragmas
    in 0003 and 0004 no-ops here; their table rebuilds need it off. `foreign_key_check`
    before COMMIT is what SQLite's own table-rebuild procedure uses to stop a migration
    committing a broken reference.
    """
    fk_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.executescript("BEGIN;\n" + sql)
        broken = conn.execute("PRAGMA foreign_key_check").fetchall()
        if broken:
            raise sqlite3.IntegrityError(
                f"{name}: {len(broken)} foreign key violation(s), first in {broken[0][0]}"
            )
        conn.execute(
            "INSERT INTO schema_migrations (id, name, applied_utc) VALUES (?, ?, ?)",
            (number, name, utc_now_iso()),
        )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.execute(f"PRAGMA foreign_keys = {'ON' if fk_on else 'OFF'}")
