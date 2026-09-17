# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""The forward estimate's knowledge-time inputs and its ledger (tci.forward_data).

Run against a copy of the committed record rather than a synthetic one, because the property
that matters most, that a recomputed anchor reproduces the published print, is only worth
anything on the prints that were actually published.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from tci import config, db, forward_data

REPO = Path(__file__).resolve().parents[1]

# One hash per version of config/forward.yaml. A parameter changed without a new version fails
# here, which is what stops a parameter being tuned after looking at how estimates scored.
FORWARD_LOCK = {"1.0.0": "4b8640d50d57"}


def test_parameters_cannot_change_without_the_version() -> None:
    _, method_version = forward_data.load_params()
    version, _, sha = method_version.partition("+")
    assert FORWARD_LOCK.get(version) == sha, (
        "config/forward.yaml changed: bump its version and record the new hash in FORWARD_LOCK")


def test_a_version_counts_only_once_its_notice_was_announced() -> None:
    before = {v for _, v, _ in forward_data.known_versions("2026-09-07")}
    after = {v for _, v, _ in forward_data.known_versions("2026-09-11")}
    assert "0.3.0-dev" in before and "0.4.0" not in before
    assert {"0.4.0", "0.5.0", "0.6.0"} <= after


@pytest.fixture(scope="module")
def record(tmp_path_factory: pytest.TempPathFactory) -> sqlite3.Connection:
    dst = tmp_path_factory.mktemp("forward") / "eucri.db"
    shutil.copy(REPO / "data" / "eucri.db", dst)
    conn = db.connect(dst)
    db.migrate(conn)
    # daily_index and observations come from the real committed record on purpose (see the
    # module docstring), but forward_estimates does not: a live daily run always records
    # forward for the date it just printed (commands.py's `_record_forward`), so the real
    # committed ledger is - by construction, every day - already caught up through its own
    # latest print. Copying that forward_estimates as-is would leave this suite's backfill
    # tests with no gap left to backfill, forever, from the first day this ever ran against
    # a repo where the daily job had actually completed once. DROP + recreate rather than
    # DELETE: forward_estimates is append-only (fwd_no_delete), and that guarantee is about
    # the real repo's ledger, not a throwaway copy this test then writes fresh rows into.
    migration = (REPO / "src" / "tci" / "migrations" / "0008_forward_estimates.sql").read_text(
        encoding="utf-8")
    conn.executescript("DROP TABLE forward_estimates;\n" + migration)
    conn.commit()
    return conn


def test_the_recomputed_anchor_reproduces_every_published_print(
        record: sqlite3.Connection) -> None:
    pf = forward_data.ProForma(record)
    published = record.execute(
        "SELECT d.date, d.value_usd, d.methodology_version FROM daily_index d JOIN"
        " (SELECT date, MAX(revision) rev FROM daily_index WHERE series = 'EU-CRI-H100'"
        " GROUP BY date) m ON d.date = m.date AND d.revision = m.rev"
        " WHERE d.series = 'EU-CRI-H100' AND d.value_usd IS NOT NULL").fetchall()
    assert len(published) >= 20
    for row in published:
        entry = config.version_for(row["date"])
        assert entry.version == row["methodology_version"]
        assert pf.value(row["date"], entry.effective_from) == pytest.approx(
            row["value_usd"], abs=1e-6), row["date"]


@pytest.fixture(scope="module")
def latest_date(record: sqlite3.Connection) -> str:
    """The most recent published EU-CRI-H100 print in the copied record.

    Not a fixed literal: this suite runs against the real committed data/eucri.db (see the
    module docstring), which a live daily run extends every day it prints. A hardcoded date
    here previously matched only the day this test was written (2026-09-15) and started
    failing the first time these tests ran against a later day's real commit — `as_of` in
    forward_tables() is always the latest live date in the table, not whatever date this
    suite happens to name.
    """
    row = record.execute(
        "SELECT MAX(date) AS d FROM daily_index WHERE series = 'EU-CRI-H100'"
        " AND value_usd IS NOT NULL"
    ).fetchone()
    assert row["d"], "no published EU-CRI-H100 print in data/eucri.db"
    return row["d"]


@pytest.fixture(scope="module")
def recorded(
    record: sqlite3.Connection, latest_date: str
) -> tuple[sqlite3.Connection, int, str]:
    return record, forward_data.record_forward(record, latest_date), latest_date


def test_earlier_dates_are_backfilled_and_flagged(
        recorded: tuple[sqlite3.Connection, int, str]) -> None:
    conn, written, date = recorded
    assert written > 0
    flags = dict(conn.execute(
        "SELECT date, MAX(backfilled) FROM forward_estimates GROUP BY date").fetchall())
    assert flags[date] == 0
    assert len(flags) > 20 and all(v == 1 for d, v in flags.items() if d < date)


def test_every_row_is_either_a_value_or_a_stated_gap(
        recorded: tuple[sqlite3.Connection, int, str]) -> None:
    conn, _, _ = recorded
    for value, detail in conn.execute("SELECT value_usd, detail FROM forward_estimates"):
        assert (value is None) == ('"gap"' in detail), detail


def test_a_rerun_writes_nothing(recorded: tuple[sqlite3.Connection, int, str]) -> None:
    conn, _, date = recorded
    assert forward_data.record_forward(conn, date) == 0


def test_the_ledger_is_append_only(recorded: tuple[sqlite3.Connection, int, str]) -> None:
    conn, _, _ = recorded
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        conn.execute("UPDATE forward_estimates SET value_usd = 0")


def test_published_tables_keep_backfill_out_of_the_history(
        recorded: tuple[sqlite3.Connection, int, str]) -> None:
    conn, _, date = recorded
    tables = forward_data.forward_tables(conn)
    assert tables is not None and tables["as_of"] == date
    assert tables["history"] and all(not r["backfilled"] for r in tables["history"])
    assert tables["n_backfilled_dates"] > 20
    assert {("M", 30), ("T", 30), ("L", 30)} <= {(r["component"], r["horizon_days"])
                                                 for r in tables["latest"]}
    live = [c for c in tables["calibration"] if not c["backfilled"]]
    assert live and all(c["bias"] is None for c in live)


def test_later_revisions_and_later_days_do_not_move_an_estimate(
        recorded: tuple[sqlite3.Connection, int, str]) -> None:
    """The leak a `date <= t` filter on daily_index would have: a revision computed later."""
    conn, _, _ = recorded
    t = "2026-09-12"
    params, _ = forward_data.load_params()

    def snapshot() -> list[tuple]:
        return [(r.component, r.horizon_days, r.value, r.p10, r.p90, r.n_inputs, r.detail)
                for r in forward_data.estimate(conn, t, params)]

    before = snapshot()
    rev = conn.execute("SELECT MAX(revision) FROM daily_index WHERE date = ?"
                       " AND series = 'EU-CRI-H100'", (t,)).fetchone()[0] or 0
    conn.execute("INSERT INTO runs (run_id, utc_date, source, started_utc, status)"
                 " VALUES ('later', '2026-09-16', 'index', '2026-09-16T11:00:00Z', 'ok')")
    conn.execute(
        "INSERT INTO daily_index (date, series, revision, value_usd, value_eur, fx_rate, fx_date,"
        " n_sources, n_executable, flags, methodology_version, computed_at, run_id) VALUES"
        " (?, 'EU-CRI-H100', ?, 9.99, NULL, NULL, NULL, 6, 1, 'correction', '0.3.0-dev',"
        " '2026-09-16T11:00:00Z', 'later')", (t, rev + 1))
    conn.execute("INSERT INTO runs (run_id, utc_date, source, started_utc, status)"
                 " VALUES ('later-obs', '2026-09-16', 'vast_ai', '2026-09-16T11:00:00Z', 'ok')")
    conn.execute(
        "INSERT INTO observations (run_id, ts_utc, source, provider, gpu_model, gpu_count,"
        " price_usd_per_gpu_hr, region, country, interconnect, tier, term, raw_json) VALUES"
        " ('later-obs', '2026-09-16T11:00:00Z', 'vast_ai', 'vast.ai', 'H100_SXM', 8, 0.5, 'NL',"
        " 'NL', 'NVLink', 'executable', 'on_demand', '{\"duration\": 99999999}')")
    conn.commit()
    assert snapshot() == before
