# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""The gap log and the gate replay. Neither may write anything, ever."""

from __future__ import annotations

import sqlite3

from tci import reliability
from tests.conftest import insert_run


def _print(conn: sqlite3.Connection, date: str, series: str, value: float | None,
           flags: str = "", revision: int = 1, n: int = 5) -> None:
    run_id = f"run-{date}-{series}-{revision}"
    insert_run(conn, run_id, date, "index")
    conn.execute(
        "INSERT INTO daily_index (date, series, revision, value_usd, n_sources,"
        " n_executable, flags, methodology_version, computed_at, run_id)"
        " VALUES (?, ?, ?, ?, ?, 0, ?, '0.6.0', '2026-09-12T11:00:00Z', ?)",
        (date, series, revision, value, n, flags, run_id),
    )
    conn.commit()


def test_only_gaps_appear_in_the_log(conn) -> None:
    _print(conn, "2026-09-10", "EU-CRI-H100", 3.25)
    _print(conn, "2026-09-11", "EU-CRI-H100", None, "insufficient_sources")
    gaps = reliability.gap_log(conn)
    assert [(g.date, g.series) for g in gaps] == [("2026-09-11", "EU-CRI-H100")]


def test_a_gap_reads_its_reason_in_words() -> None:
    g = reliability.Gap("2026-09-11", "EU-CRI-H100", "insufficient_sources", 1, "0.6.0")
    assert g.reason == "fewer qualifying providers than the publication gate requires"
    assert not g.corrected


def test_an_unrecognised_flag_is_shown_rather_than_guessed_at() -> None:
    """A reason nobody has written words for is still published, as the flag itself."""
    g = reliability.Gap("2026-09-11", "S", "some_new_flag", 1, "0.6.0")
    assert g.reason == "some_new_flag"


def test_a_basis_leg_gap_reads_in_words() -> None:
    """GAP_REASONS keys must match the flags basis.py actually writes (lead_gap,
    reference_gap), or a basis-series gap falls through to the raw flag string."""
    lead = reliability.Gap("2026-09-11", "EU-CRI-BASIS", "lead_gap", 1, "0.6.0")
    reference = reliability.Gap("2026-09-11", "EU-CRI-BASIS", "reference_gap", 1, "0.6.0")
    assert lead.reason == "the lead leg did not print"
    assert reference.reason == "the reference leg did not print"


def test_the_correction_marker_is_not_a_reason() -> None:
    g = reliability.Gap("2026-09-11", "S", "insufficient_sources,correction", 1, "0.6.0")
    assert g.corrected
    assert "correction" not in g.reason


def test_the_log_takes_the_latest_revision_only(conn) -> None:
    """A gap later corrected into a print must leave the log, and the reverse."""
    _print(conn, "2026-09-10", "EU-CRI-H100", None, "insufficient_sources", revision=1)
    _print(conn, "2026-09-10", "EU-CRI-H100", 3.25, "correction", revision=2)
    assert reliability.gap_log(conn) == []

    _print(conn, "2026-09-09", "EU-CRI-H100", 3.10, revision=1)
    _print(conn, "2026-09-09", "EU-CRI-H100", None, "insufficient_sources,correction", revision=2)
    assert [g.date for g in reliability.gap_log(conn)] == ["2026-09-09"]


def test_coverage_reports_the_record_beside_the_replay(conn) -> None:
    """The two columns disagree whenever the head panel differs from the one live on the
    date, which is most of the history. A reader has to be able to see which is which."""
    _print(conn, "2026-09-12", "EU-CRI-H100", 3.49, n=6)
    day = reliability._day(conn, "EU-CRI-H100", "2026-09-12", _factors())
    assert day.in_record
    assert day.printed == 3.49
    assert day.printed_n == 6


def test_coverage_marks_a_series_that_does_not_print_yet(conn) -> None:
    insert_run(conn, "r1", "2026-09-12", "vast_ai")
    day = reliability._day(conn, "EU-CRI-H100-US", "2026-09-12", _factors())
    assert not day.in_record
    assert "not computed" in reliability.render_coverage("EU-CRI-H100-US", [day], "0.6.0")


def test_render_names_the_version_it_replayed_under(conn) -> None:
    """Coverage under the head is not the print, and the page has to say so."""
    day = reliability.Day("2026-09-12", 5, ("a", "b", "c", "d", "e"), 5)
    text = reliability.render_coverage("EU-CRI-H100-US", [day], "0.6.0")
    assert "0.6.0" in text
    assert "not a print" in text


def test_a_day_below_the_gate_is_marked(conn) -> None:
    below = reliability.Day("2026-09-11", 4, ("a",), 5)
    at = reliability.Day("2026-09-12", 5, ("a",), 5)
    assert not below.meets_gate and at.meets_gate
    text = reliability.render_coverage("S", [below, at], "0.6.0")
    assert "1 of 2 sessions would have met the gate" in text


def test_reliability_writes_nothing(conn) -> None:
    """It reads the record and replays the calculation. It is never in the write path."""
    _print(conn, "2026-09-12", "EU-CRI-H100", 3.49)
    before = conn.execute("SELECT COUNT(*) FROM daily_index").fetchone()[0]
    reliability.coverage(conn, "EU-CRI-H100", days=5)
    reliability.gap_log(conn)
    assert conn.execute("SELECT COUNT(*) FROM daily_index").fetchone()[0] == before
    assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0


def _factors():
    from tci.config import load_factors

    return load_factors()
