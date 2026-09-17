# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Reading published prints: the one place that resolves a revision.

`daily_index` is append-only, so a correction is stored as a new revision and the
published print for a (date, series) is the row at MAX(revision), whatever earlier
revisions say. A read that resolves the revision any other way republishes a value the
index already withdrew. METHODOLOGY.md §4 forbids that in the strongest words it uses
anywhere: a gap stays a gap, no fallback waterfall, no fabrication.

Easy to state, and four call sites still got it wrong, because the resolution was
written inline at each of them. Two SQL shapes look alike and are not:

    (right) join against an unconditional `MAX(revision)`, THEN discard NULLs, so a
            withdrawn day drops out of the answer;
    (wrong) filter `value_usd IS NOT NULL` BEFORE taking MAX(revision), which picks the
            newest SURVIVING revision instead of the newest revision, and hands back
            the value the correction retracted.

The wrong shape agrees with the right one on every day nobody ever corrected, which is
nearly all of them. It diverges only on the days the revision machinery exists for: 24
(date, series) keys in the shipped database. So the resolution lives here once, it is
tested here against the withdrawal case, and every read path calls into it.

Two questions both sound like "the latest value", and the bug was answering the first
with the second:

    head_value, current_print      what does the index say about THIS date? NULL is a
                                   real answer, meaning the day gapped.
    value_on_or_before,            what was the last date the index actually printed a
    previous_published             number? A comparison basis. It skips gapped days,
                                   but only days whose HEAD revision gapped, never a
                                   day resurrected from an older revision.

`site.py` had this right and said so in its docstrings. `webdata.py`, `post.py` and
`commands.py` each kept a private copy and two of the three were wrong. What follows is
what `site.py` shipped, moved down a layer so every reader can reach it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

# The subquery that resolves a revision, written once. It groups by (date, series) with
# no predicate on value_usd: the head revision wins even when the head is a gap. Callers
# that want only published numbers add `AND d.value_usd IS NOT NULL` to the OUTER query,
# which is what makes a withdrawal disappear instead of falling back a revision.
_HEADS = (
    "SELECT date, series, MAX(revision) AS rev FROM daily_index"
    " WHERE series = ? GROUP BY date, series"
)
_JOIN_HEADS = (
    f" JOIN ({_HEADS}) m ON d.date = m.date AND d.series = m.series AND d.revision = m.rev"
)


@dataclass(frozen=True)
class Point:
    """One session on the published curve. `value` None means the print gapped."""

    date: str
    value: float | None
    value_eur: float | None = None
    flags: str = ""


def series_history(
    conn: sqlite3.Connection, series: str, *, since: str | None = None
) -> list[Point]:
    """Published history for one series, one row per date, latest revision only.

    Gapped days are KEPT, with `value` None, because a chart has to draw the gap
    rather than close over it. Callers wanting only printed numbers filter on `value`.
    """
    sql = (
        "SELECT d.date, d.value_usd, d.value_eur, d.flags FROM daily_index d"
        + _JOIN_HEADS
        + " WHERE d.series = ?"
    )
    params: list[object] = [series, series]
    if since is not None:
        sql += " AND d.date >= ?"
        params.append(since)
    sql += " ORDER BY d.date"
    return [
        Point(r["date"], r["value_usd"], r["value_eur"], r["flags"] or "")
        for r in conn.execute(sql, params)
    ]


def latest_print(conn: sqlite3.Connection, series: str) -> sqlite3.Row | None:
    """The newest row for a series whatever its date: history's head, not today's."""
    return conn.execute(
        "SELECT * FROM daily_index WHERE series = ? ORDER BY date DESC, revision DESC LIMIT 1",
        (series,),
    ).fetchone()


def current_print(conn: sqlite3.Connection, series: str, date: str) -> sqlite3.Row | None:
    """The print for THIS session, or None — never an older one dressed as current.

    `latest_print` returns a series' newest row whatever its date, which is right for
    history but wrong for a live surface: a series that stops being computed keeps
    rendering its last good value forever. That is exactly what happened to the retired
    `EU-CRI-H100-CLOUD`, which sat in the ticker showing 3.85 from 2026-08-15 under
    methodology 0.2.0-dev — for a while the only number on a ticker where every live
    series was honestly gapped. A stale value presented as current is the one failure
    mode this project cannot afford, so the live surfaces ask for the session's row by
    date and get nothing if it does not exist.
    """
    row = latest_print(conn, series)
    return row if row is not None and row["date"] == date else None


def head_print(conn: sqlite3.Connection, series: str, date: str) -> sqlite3.Row | None:
    """The published print for exactly this (date, series): the row at MAX(revision).

    Returns the row even when its `value_usd` is NULL. A withdrawn day is a fact about
    the date, and the caller is entitled to see the flags that explain it. None means
    the index never wrote this date at all.
    """
    return conn.execute(
        "SELECT * FROM daily_index WHERE date = ? AND series = ?"
        " ORDER BY revision DESC LIMIT 1",
        (date, series),
    ).fetchone()


def head_value(conn: sqlite3.Connection, series: str, date: str) -> float | None:
    """The published value for exactly this (date, series); None if gapped or absent.

    None is deliberately ambiguous between "gapped" and "never computed" because every
    caller treats them alike: neither is a number you may print. Use `head_print` when
    the difference matters.
    """
    row = head_print(conn, series, date)
    return row["value_usd"] if row is not None else None


def head_values(
    conn: sqlite3.Connection, series: str, dates: list[str]
) -> list[float | None]:
    """`head_value` across a window, in the order given, in one query.

    The smoothing window asks for seven dates at once; asking seven times was seven
    round trips to answer a question one GROUP BY covers.
    """
    if not dates:
        return []
    marks = ",".join("?" for _ in dates)
    rows = conn.execute(
        "SELECT d.date, d.value_usd FROM daily_index d"
        + _JOIN_HEADS
        + f" WHERE d.series = ? AND d.date IN ({marks})",
        [series, series, *dates],
    ).fetchall()
    found = {r["date"]: r["value_usd"] for r in rows}
    return [found.get(d) for d in dates]


def previous_published(
    conn: sqlite3.Connection, series: str, before: str
) -> tuple[str, float] | None:
    """The last (date, value) STRICTLY before `before` whose head revision printed."""
    row = conn.execute(
        "SELECT d.date, d.value_usd FROM daily_index d"
        + _JOIN_HEADS
        + " WHERE d.series = ? AND d.date < ? AND d.value_usd IS NOT NULL"
        " ORDER BY d.date DESC LIMIT 1",
        (series, series, before),
    ).fetchone()
    return (row["date"], row["value_usd"]) if row else None


def value_on_or_before(conn: sqlite3.Connection, series: str, date: str) -> float | None:
    """The last published value at or before DATE, for a fixed-horizon comparison.

    A 30-day delta on a series that gaps as often as this one cannot ask for "the value
    exactly 30 days ago" — most sessions have none. It asks for the most recent print up
    to that date instead, which is a comparison against a value that was genuinely
    published, never an interpolation onto a day the index said nothing.
    """
    row = conn.execute(
        "SELECT d.value_usd FROM daily_index d"
        + _JOIN_HEADS
        + " WHERE d.series = ? AND d.date <= ? AND d.value_usd IS NOT NULL"
        " ORDER BY d.date DESC LIMIT 1",
        (series, series, date),
    ).fetchone()
    return row["value_usd"] if row else None


def head_revision(conn: sqlite3.Connection, series: str, date: str) -> int | None:
    """The revision number of the published print for (date, series)."""
    row = conn.execute(
        "SELECT MAX(revision) AS rev FROM daily_index WHERE date = ? AND series = ?",
        (date, series),
    ).fetchone()
    return None if row is None or row["rev"] is None else int(row["rev"])


def constituents_for(
    conn: sqlite3.Connection, series: str, date: str
) -> list[sqlite3.Row]:
    """The audit set behind the published print for (date, series)."""
    rev = head_revision(conn, series, date)
    if rev is None:
        return []
    return list(
        conn.execute(
            "SELECT * FROM constituents WHERE date = ? AND series = ? AND revision = ?"
            " ORDER BY included DESC, price_usd",
            (date, series, rev),
        )
    )


def previous_included_prices(
    conn: sqlite3.Connection, series: str, before: str
) -> dict[str, float]:
    """Provider -> price from the last print before `before` that actually stands.

    This is the baseline the jump flag is measured against (index.compute_print), so it
    has to come from a session the index still stands behind. Reading it from a day that
    was later withdrawn measures today's move against a number the publisher retracted:
    it invents jumps where the retracted value was far from today's, and hides real ones
    where it happened to be close. `previous_published` picks the date by head revision;
    the constituents are then read at THAT date's head revision, so basis and audit set
    come from the same print rather than from two different revisions of it.
    """
    prev = previous_published(conn, series, before)
    if prev is None:
        return {}
    date, _ = prev
    rows = constituents_for(conn, series, date)
    return {r["provider"]: r["price_usd"] for r in rows if r["included"]}
