"""Market-price statements: every day listed, no gap filled, and checkable after the fact."""

from __future__ import annotations

import copy
import sqlite3
from statistics import mean

import pytest

from tci import db, statement


@pytest.fixture(scope="module")
def record() -> sqlite3.Connection:
    return db.connect_readonly()


@pytest.fixture(scope="module")
def september(record: sqlite3.Connection) -> dict:
    return statement.build(record, "EU-CRI-H100", "2026-09-01", "2026-09-30")


def test_every_calendar_day_is_listed(september: dict) -> None:
    dates = [s["date"] for s in september["sessions"]]
    assert dates[0] == "2026-09-01" and dates[-1] == "2026-09-30" and len(dates) == 30
    s = september["summary"]
    assert s["published"] + s["gapped"] + s["not_computed"] == 30


def test_gaps_stay_out_of_every_average(september: dict) -> None:
    printed = [x["value_eur"] for x in september["sessions"] if x["value_usd"] is not None]
    assert september["summary"]["eur"]["mean"] == pytest.approx(mean(printed), abs=1e-4)
    gaps = [x for x in september["sessions"] if x["status"] == "gap"]
    assert gaps and all(x["value_usd"] is None and x["flags"] for x in gaps)


def test_a_statement_verifies_against_the_record(record: sqlite3.Connection,
                                                 september: dict) -> None:
    assert statement.verify(record, september).ok


def test_a_changed_value_fails_even_with_a_recomputed_digest(
    record: sqlite3.Connection, september: dict,
) -> None:
    forged = copy.deepcopy(september)
    first = next(x for x in forged["sessions"] if x["value_usd"] is not None)
    first["value_eur"] = 1.0
    forged["digest"] = statement.digest_of(forged)
    check = statement.verify(record, forged)
    assert not check.ok
    assert any("value_eur differs" in p for p in check.problems)


def test_an_edited_file_fails_its_own_digest(record: sqlite3.Connection,
                                             september: dict) -> None:
    edited = copy.deepcopy(september)
    edited["measures"] = "something else"
    assert "the statement's digest does not match its content" in \
        statement.verify(record, edited).problems


def test_derived_series_are_refused(record: sqlite3.Connection) -> None:
    with pytest.raises(statement.StatementError):
        statement.build(record, "EU-CRI-H100-BASIS-US", "2026-10-01", "2026-10-05")


def test_only_closed_months_are_published(record: sqlite3.Connection) -> None:
    months = statement.completed_months(record, "EU-CRI-H100", "2026-10-11")
    assert months[-1] == "2026-09"
    assert "2026-10" not in months
    assert statement.month_bounds("2026-02") == ("2026-02-01", "2026-02-28")
