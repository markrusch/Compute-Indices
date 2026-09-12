# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""One day recomputed under two methodology versions.

The commitment this serves is in a published notice: 2026-N2 says the first print under
v0.5.0 "will state its size against a v0.4.0 recomputation of the same day". The number had
no implementation.

The risk it carries is the opposite of the one it answers. A recomputation under a version
that was never live on a date is an analysis, and if it could ever reach `daily_index` it
would be a print computed under rules that were not in force — the single thing the dated
succession exists to prevent. So most of what follows checks that nothing is written.
"""

from __future__ import annotations

import sqlite3

import pytest

from tci import config, db, version_effect
from tests.conftest import insert_run


def test_the_succession_versions_are_all_addressable() -> None:
    known = version_effect.versions()
    assert {"0.3.0-dev", "0.4.0", "0.5.0", "0.6.0"} <= set(known)
    for name, entry in known.items():
        assert entry.params_dir.exists(), f"{name}: {entry.params_dir} is missing"


def test_an_unknown_version_is_refused_by_name() -> None:
    with pytest.raises(KeyError, match="0.9.9"):
        version_effect.compare(db.connect(), "2026-09-12", "0.4.0", "0.9.9")


def test_neither_leg_writes_to_the_database_it_was_given(conn) -> None:
    """The whole point. A stored revision here would be a print under rules never in force.

    The session-wide guard in conftest covers `data/eucri.db`; this covers any connection,
    including the one a caller passes in.
    """
    insert_run(conn, "r1", "2026-09-12", "vast_ai")
    conn.execute(
        "INSERT INTO observations (run_id, ts_utc, source, provider, gpu_model, gpu_count,"
        " price_usd_per_gpu_hr, region, country, interconnect, tier, term, raw_json)"
        " VALUES ('r1', '2026-09-12T11:00:00Z', 'vast_ai', 'vast.ai', 'H100_SXM', 8, 3.0,"
        " 'eu', 'NL', NULL, 'executable', 'on_demand', '{}')"
    )
    conn.commit()
    before = _counts(conn)

    version_effect.compare(conn, "2026-09-12", "0.4.0", "0.5.0")

    assert _counts(conn) == before, "a version comparison wrote to the database"


def _counts(conn: sqlite3.Connection) -> tuple[int, int, int]:
    return tuple(  # type: ignore[return-value]
        conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("daily_index", "constituents", "observations")
    )


def test_the_real_loaders_are_restored_even_when_the_calculation_raises(conn) -> None:
    """`_prints_under` swaps `config.load_factors` for the duration of one call. Leaving
    the swap in place would silently pin every later calculation in the process to one
    frozen version — including, in a daily run, the one that computes the day's print."""
    real = config.load_factors
    entry = version_effect.versions()["0.4.0"]

    from tci import commands

    def boom(*_a, **_k):
        raise RuntimeError("calculation failed")

    original = commands.compute_all_series
    commands.compute_all_series = boom  # type: ignore[assignment]
    try:
        with pytest.raises(RuntimeError):
            version_effect._prints_under(conn, "2026-09-12", entry)
    finally:
        commands.compute_all_series = original  # type: ignore[assignment]

    assert config.load_factors is real


def test_both_legs_read_the_same_day(conn) -> None:
    """Holding the day fixed is the reason this exists: the step between two sessions
    mixes the version change with whatever the market did overnight."""
    effects = version_effect.compare(conn, "2026-09-12", "0.4.0", "0.5.0")
    assert all(e.date == "2026-09-12" for e in effects)


def test_a_series_the_earlier_version_did_not_define_is_marked_as_new() -> None:
    """Both legs read None, so `moved` is False. A row shown as unchanged when the series
    did not previously exist would be read as "this version changed nothing here"."""
    effect = version_effect.Effect(
        series="EU-CRI-B200", date="2026-09-12", before=None, after=None,
        before_n=0, after_n=2, before_flags=version_effect.Effect.NOT_COMPUTED,
        after_flags="insufficient_sources",
    )
    assert effect.introduced
    assert not effect.moved
    assert "+ EU-CRI-B200" in version_effect.render([effect], "2026-09-12", "0.4.0", "0.5.0")


def test_a_move_reports_its_size_and_direction() -> None:
    effect = version_effect.Effect(
        series="EU-CRI-H100", date="2026-09-12", before=3.25, after=3.49,
        before_n=5, after_n=8, before_flags="", after_flags="",
    )
    assert effect.moved
    assert effect.delta == pytest.approx(0.24)
    assert effect.pct == pytest.approx(7.3846, abs=1e-3)
    assert "+0.2400" in effect.describe()


def test_a_gap_on_either_leg_has_no_percentage() -> None:
    """A gap is not zero, and a move out of one is not a percentage change."""
    effect = version_effect.Effect(
        series="EU-CRI-H100-NC", date="2026-09-12", before=None, after=3.84,
        before_n=4, after_n=6, before_flags="insufficient_sources", after_flags="",
    )
    assert effect.pct is None and effect.delta is None
    assert "gap (insufficient_sources, n=4)" in effect.describe()


def test_the_v0_5_0_transition_on_the_stored_record() -> None:
    """The number notice 2026-N2 owes its readers, on the last day with a full panel.

    N2 quantified v0.5.0 as taking the headline from $3.25 to $3.49 on the 7 September
    panel. On 12 September's observations the version change moves the headline not at all:
    both legs print $3.49, the panel widens from six sellers to eight, and the two entrants
    land either side of the median. The step a reader will see published on 22 September is
    therefore not the step N2 described, and this is the figure that says so.
    """
    effects = version_effect.compare(db.connect(), "2026-09-12", "0.4.0", "0.5.0")
    headline = next(e for e in effects if e.series == "EU-CRI-H100")
    assert headline.before == pytest.approx(3.49)
    assert headline.after == pytest.approx(3.49)
    assert headline.before_n == 6 and headline.after_n == 8
