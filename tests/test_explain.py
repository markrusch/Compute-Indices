"""Why a print moved, and whose price it landed on: read from the record, never written to it.

These run against the committed database because the claims the site publishes are about
that record. A split that does not add up to the published move, or a "set by" seller with
no offer at the printed price, would be a false statement on a live page.
"""

from __future__ import annotations

import sqlite3

import pytest

from tci import db, explain
from tci.series_read import series_history

HEADLINE = "EU-CRI-H100"


@pytest.fixture(scope="module")
def record() -> sqlite3.Connection:
    return db.connect_readonly()


def _printed(conn: sqlite3.Connection, since: str) -> list[str]:
    return [p.date for p in series_history(conn, HEADLINE, since=since) if p.value is not None]


def test_every_split_adds_up_to_the_published_move(record: sqlite3.Connection) -> None:
    checked = 0
    for date in _printed(record, "2026-09-01"):
        m = explain.explain_move(record, HEADLINE, date)
        if m is None or not m.split:
            continue
        assert sum(s.delta or 0.0 for s in m.steps) == pytest.approx(m.total, abs=1e-6), date
        checked += 1
    assert checked >= 20


def test_a_stock_out_is_published_as_exits_not_as_a_price_rise(
    record: sqlite3.Connection,
) -> None:
    # 24 September 2026: +8.2% with no seller changing a price. RunPod and vast.ai had no
    # qualifying stock and left the book; the explain module's docstring cites this day.
    m = explain.explain_move(record, HEADLINE, "2026-09-24")
    assert m is not None and m.split
    assert m.exited == ("runpod", "vast.ai")
    steps = {s.name: s.delta for s in m.steps}
    assert steps["exits"] == pytest.approx(0.304138, abs=1e-6)
    assert abs(steps["price"] or 0.0) < 0.02
    assert m.total == pytest.approx(0.286927, abs=1e-6)


def test_a_repricing_is_published_as_price(record: sqlite3.Connection) -> None:
    # 9 October 2026: RunPod moved its posted H100 price from $3.49 to $3.99.
    m = explain.explain_move(record, HEADLINE, "2026-10-09")
    assert m is not None
    assert ("runpod", 3.49, 3.99) in m.repriced
    assert {s.name: s.delta for s in m.steps}["price"] == pytest.approx(m.total, abs=1e-6)


def test_the_print_always_belongs_to_a_seller(record: sqlite3.Connection) -> None:
    for date in _printed(record, "2026-09-12"):
        b = explain.book(record, date, HEADLINE)
        assert b is not None
        assert explain.median_setters(b), f"{date}: no seller has an offer at the print"


def test_a_gap_has_no_move(record: sqlite3.Connection) -> None:
    gapped = [p.date for p in series_history(record, HEADLINE, since="2026-09-01")
              if p.value is None]
    assert gapped
    assert explain.explain_move(record, HEADLINE, gapped[0]) is None


def test_basis_makeup_shows_one_seller_setting_both_legs(record: sqlite3.Connection) -> None:
    mk = explain.basis_makeup(record, "2026-10-11", "EU-CRI-H100-BASIS-US", HEADLINE,
                              "EU-CRI-H100-US")
    assert mk is not None and mk.basis == 0.0
    assert mk.shared_setter
    assert mk.like_for_like == 0.0
    assert mk.identical == len(mk.both) >= 3
