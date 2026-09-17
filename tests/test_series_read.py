# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Revision resolution: the withdrawn-print case, for every surface that reads a print.

`tests/test_site.py` already covered this rule for the HTML site, and the site was
correct because of it. The bug these tests exist for lived in the surfaces that had NO
such test: `latest.json` (the public JSON feed), the Substack post, and the jump-flag
baseline inside the calculation itself. Each had its own copy of the resolution, and two
of the three resolved MAX(revision) only over rows that survived, republishing values a
later revision had withdrawn.

The scenario below is the one the shipped database actually contains (EU-CRI-H100,
2026-08-10 through 2026-08-16): a run of sessions printed, then a correction withdrew
the later ones, leaving the last standing print several days back.
"""

from __future__ import annotations

import sqlite3

import pytest

from tci import series_read
from tci.outputs import post, webdata
from tests.conftest import insert_run
from tests.test_site import _print


@pytest.fixture()
def withdrawn(conn: sqlite3.Connection) -> sqlite3.Connection:
    """08-10 stands at 3.25; 08-11..08-15 printed at rev 1 then were withdrawn at rev 2."""
    insert_run(conn, "r1", "2026-08-10")
    _print(conn, "2026-08-10", "EU-CRI-H100", 1, 3.29)
    _print(conn, "2026-08-10", "EU-CRI-H100", 2, 3.25, flags="correction")
    for day in ("2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14", "2026-08-15"):
        _print(conn, day, "EU-CRI-H100", 1, 3.29)
        _print(conn, day, "EU-CRI-H100", 2, None, flags="insufficient_sources,correction")
    _print(conn, "2026-08-16", "EU-CRI-H100", 1, 3.40)
    return conn


# ---------------------------------------------------------------------------
# the rule itself
# ---------------------------------------------------------------------------


def test_value_on_or_before_walks_past_withdrawn_sessions(withdrawn):
    """3.29 was withdrawn on every day it appears after 08-10; it may never come back."""
    assert series_read.value_on_or_before(withdrawn, "EU-CRI-H100", "2026-08-15") == 3.25


def test_previous_published_walks_past_withdrawn_sessions(withdrawn):
    assert series_read.previous_published(withdrawn, "EU-CRI-H100", "2026-08-16") == (
        "2026-08-10", 3.25,
    )


def test_head_value_reports_a_withdrawn_session_as_a_gap(withdrawn):
    assert series_read.head_value(withdrawn, "EU-CRI-H100", "2026-08-13") is None
    assert series_read.head_value(withdrawn, "EU-CRI-H100", "2026-08-10") == 3.25


def test_head_print_keeps_the_row_so_the_reason_survives(withdrawn):
    """A gap still has to be explainable: the flags on the head revision say why."""
    row = series_read.head_print(withdrawn, "EU-CRI-H100", "2026-08-13")
    assert row is not None
    assert row["value_usd"] is None
    assert "insufficient_sources" in row["flags"]


def test_head_values_matches_head_value_across_a_window(withdrawn):
    dates = ["2026-08-10", "2026-08-13", "2026-08-16", "2026-09-01"]
    assert series_read.head_values(withdrawn, "EU-CRI-H100", dates) == [
        series_read.head_value(withdrawn, "EU-CRI-H100", d) for d in dates
    ]
    # the absent date is None, not a dropped element: callers index this against `dates`
    assert series_read.head_values(withdrawn, "EU-CRI-H100", dates)[3] is None


def test_head_values_is_empty_for_no_dates(withdrawn):
    assert series_read.head_values(withdrawn, "EU-CRI-H100", []) == []


def test_resolution_does_not_leak_across_series(conn):
    """Revision numbers are per (date, series); a join on revision alone would mix them."""
    insert_run(conn, "r1", "2026-08-01")
    _print(conn, "2026-08-01", "EU-CRI-H100", 1, 3.00)
    _print(conn, "2026-08-01", "EU-CRI-H100", 2, None, flags="insufficient_sources")
    _print(conn, "2026-08-01", "EU-CRI-A100", 1, 1.00)

    assert series_read.head_value(conn, "EU-CRI-H100", "2026-08-01") is None
    assert series_read.head_value(conn, "EU-CRI-A100", "2026-08-01") == 1.00
    assert series_read.value_on_or_before(conn, "EU-CRI-A100", "2026-08-01") == 1.00


def test_no_published_value_at_all_is_none_not_an_error(conn):
    insert_run(conn, "r1", "2026-08-01")
    _print(conn, "2026-08-01", "EU-CRI-H100", 1, None, flags="insufficient_sources")

    assert series_read.value_on_or_before(conn, "EU-CRI-H100", "2026-08-01") is None
    assert series_read.previous_published(conn, "EU-CRI-H100", "2026-08-02") is None
    assert series_read.previous_included_prices(conn, "EU-CRI-H100", "2026-08-02") == {}


# ---------------------------------------------------------------------------
# the surfaces that had no test, which is why the bug survived
# ---------------------------------------------------------------------------


def test_latest_json_percentages_are_measured_against_a_standing_print(withdrawn):
    """`wow_pct` in site/data/latest.json divided by a retracted value before this.

    webdata resolved the revision after discarding NULLs, so the denominator came from
    08-15 revision 1 (3.29), a session withdrawn at revision 2, instead of 08-10 (3.25).
    """
    assert webdata._value_on(withdrawn, "EU-CRI-H100", "2026-08-15") == 3.25
    assert webdata._prior_print(withdrawn, "EU-CRI-H100", "2026-08-16") == ("2026-08-10", 3.25)


def test_the_post_quotes_the_same_basis_as_the_site(withdrawn):
    """The newsletter cannot be corrected after sending, so it must not lead the site."""
    assert post._value_on(withdrawn, "EU-CRI-H100", "2026-08-15") == 3.25


def test_every_surface_agrees_on_the_comparison_basis(withdrawn):
    """The actual defect was DISAGREEMENT between surfaces, so assert they agree.

    Site, JSON feed and post each had their own resolution; this fails if any one of
    them grows a private copy again.
    """
    from tci.outputs import site

    basis = series_read.value_on_or_before(withdrawn, "EU-CRI-H100", "2026-08-15")
    assert webdata._value_on(withdrawn, "EU-CRI-H100", "2026-08-15") == basis
    assert post._value_on(withdrawn, "EU-CRI-H100", "2026-08-15") == basis
    assert site.value_on_or_before(withdrawn, "EU-CRI-H100", "2026-08-15") == basis


def test_jump_flag_baseline_comes_from_a_print_that_still_stands(withdrawn):
    """`previous_included_prices` feeds index.compute_print's jump flag.

    Measuring today against a retracted session invents jumps and hides real ones, so
    the baseline is read from the last print whose HEAD revision published, and the
    constituents are read at THAT print's revision.
    """
    conn = withdrawn
    # 08-10 rev 1 had a provider at 9.99; rev 2 (the standing print) has it at 3.25.
    for rev, price in ((1, 9.99), (2, 3.25)):
        conn.execute(
            "INSERT INTO constituents (date, series, revision, provider, source, tier,"
            " price_usd, weight, included) VALUES"
            " ('2026-08-10', 'EU-CRI-H100', ?, 'nebius', 'test', 'executable', ?, 1, 1)",
            (rev, price),
        )
    conn.commit()

    prices = series_read.previous_included_prices(conn, "EU-CRI-H100", "2026-08-16")

    assert prices == {"nebius": 3.25}


def test_excluded_constituents_are_not_a_jump_baseline(withdrawn):
    """A provider the print excluded contributed no price, so it anchors nothing."""
    conn = withdrawn
    conn.execute(
        "INSERT INTO constituents (date, series, revision, provider, source, tier,"
        " price_usd, weight, included, exclusion_reason) VALUES"
        " ('2026-08-10', 'EU-CRI-H100', 2, 'dropped', 'test', 'executable', 7.5, 0, 0, 'stale')"
    )
    conn.commit()

    assert "dropped" not in series_read.previous_included_prices(
        conn, "EU-CRI-H100", "2026-08-16"
    )
