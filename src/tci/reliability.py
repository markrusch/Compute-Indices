# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Two questions the record can answer about itself: where it gapped, and where it nearly did.

`gap_log` reads stored prints. Every session that published null, with the reason the
calculation recorded, in the words the site uses. A benchmark that publishes its own
failures is worth more than one that quietly has none, and the list has to come from the
database rather than from anyone's memory of a bad morning.

`coverage` answers the other question, for a series that does not print yet. A series
whose methodology version is not yet effective computes nothing, so there is no print
history to watch it with - and the decision about whether to let it go live is due before
it has any. So the unit definition is replayed over stored observations, through the same
`normalise_observations` and `provider_offers` the calculation uses, and the count of
qualifying providers per date is reported against the gate.

That count is coverage, not a print, and is labelled so everywhere it surfaces. It says
how many sellers would have qualified on a day, which is what a gate decision needs. It is
not a value, it is not published, and replaying it changes nothing: this module writes
nothing, anywhere.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from tci.config import Factors, load_factors
from tci.index import provider_offers
from tci.normalise import normalise_observations

# The words the site and the notices use for each recorded flag. A flag with no entry
# here is shown as itself rather than guessed at.
GAP_REASONS = {
    "insufficient_sources": "fewer qualifying providers than the publication gate requires",
    "insufficient_history": "not enough consecutive prints to average over",
    "no_linkable_series": "no series it could be chained to printed that day",
    "no_reference": "the reference leg did not print",
    "no_lead": "the lead leg did not print",
}


@dataclass(frozen=True)
class Gap:
    date: str
    series: str
    flags: str
    revision: int
    methodology_version: str

    @property
    def reason(self) -> str:
        named = [GAP_REASONS.get(f, f) for f in self.flags.split(",") if f and f != "correction"]
        return "; ".join(named) or "no reason recorded"

    @property
    def corrected(self) -> bool:
        return "correction" in self.flags


@dataclass(frozen=True)
class Day:
    date: str
    n_qualifying: int
    providers: tuple[str, ...]
    gate: int
    # What the record actually holds for this series on this date, which for a series
    # whose version is not yet effective is nothing. Carried so the replay can never be
    # mistaken for the print: the two differ whenever the head panel differs from the
    # panel that was live on the date, which is most of the history.
    printed: float | None = None
    printed_n: int | None = None
    printed_version: str = ""

    @property
    def meets_gate(self) -> bool:
        return self.n_qualifying >= self.gate

    @property
    def in_record(self) -> bool:
        return self.printed_version != ""


def gap_log(conn: sqlite3.Connection, series: str | None = None) -> list[Gap]:
    """Every published gap, newest first, at the latest revision of each (date, series)."""
    sql = (
        "SELECT d.date, d.series, COALESCE(d.flags, '') flags, d.revision,"
        " d.methodology_version FROM daily_index d JOIN ("
        "  SELECT date, series, MAX(revision) rev FROM daily_index GROUP BY date, series"
        ") m ON d.date = m.date AND d.series = m.series AND d.revision = m.rev"
        " WHERE d.value_usd IS NULL"
    )
    params: list[str] = []
    if series:
        sql += " AND d.series = ?"
        params.append(series)
    sql += " ORDER BY d.date DESC, d.series"
    return [
        Gap(r["date"], r["series"], r["flags"], r["revision"], r["methodology_version"])
        for r in conn.execute(sql, params)
    ]


def coverage(conn: sqlite3.Connection, series: str, days: int = 21) -> list[Day]:
    """How many providers would have qualified for `series` on each of the last `days`.

    Uses the head of the succession, not the version live on each date: the question is
    whether the series as configured would print, and for a series announced but not yet
    effective the version live on those dates does not define it at all.
    """
    factors = load_factors()
    dates = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT substr(ts_utc, 1, 10) d FROM observations"
            " ORDER BY d DESC LIMIT ?", (days,)
        )
    ]
    return [_day(conn, series, date, factors) for date in sorted(dates)]


def _day(conn: sqlite3.Connection, series: str, date: str, factors: Factors) -> Day:
    rs = factors.regional_series.get(series)
    countries = factors.countries_of(rs.block) if rs else factors.eu_eea_countries
    model_class = rs.model_class if rs else factors.headline_class
    population = factors.population_for(rs.population if rs else "headline")

    rows = conn.execute(
        "SELECT o.* FROM observations o JOIN runs r ON o.run_id = r.run_id"
        " WHERE substr(o.ts_utc, 1, 10) = ? AND r.status = 'ok'", (date,)
    ).fetchall()
    fx = conn.execute(
        "SELECT fx_rate FROM daily_index WHERE date = ? AND fx_rate IS NOT NULL LIMIT 1",
        (date,),
    ).fetchone()
    normalised = normalise_observations(
        rows, factors, fx_eur_usd=fx["fx_rate"] if fx else None, countries=countries
    )
    kept, _ = provider_offers(
        [o for o in normalised if o.model_class == model_class], factors, date, population
    )
    stored = conn.execute(
        "SELECT value_usd, n_sources, methodology_version FROM daily_index"
        " WHERE date = ? AND series = ? ORDER BY revision DESC LIMIT 1", (date, series)
    ).fetchone()
    return Day(
        date, len(kept), tuple(sorted(kept)), factors.aggregation.min_providers,
        printed=stored["value_usd"] if stored else None,
        printed_n=stored["n_sources"] if stored else None,
        printed_version=stored["methodology_version"] if stored else "",
    )


def render_coverage(series: str, days: list[Day], head_version: str) -> str:
    """Replay beside record. The two columns answer different questions, and the gap
    between them is the point: the replay uses the head of the succession, the record
    used whatever version was live on the date. For most of the history those panels are
    not the same, so a replay reading lower than the published print is expected and is
    not evidence that the print was wrong."""
    gate = days[0].gate if days else 0
    lines = [
        f"{series}: providers qualifying per session, against a gate of {gate}",
        f"replayed from stored observations under {head_version}, the head of the"
        " succession - coverage, not a print, and nothing is published from it",
        "",
        f"{'date':<12}{'replay':>7}  {'record':>16}  providers qualifying in the replay",
    ]
    for d in days:
        mark = " " if d.meets_gate else "<"
        if not d.in_record:
            record = "not computed"
        elif d.printed is None:
            record = f"gap (n={d.printed_n})"
        else:
            record = f"${d.printed:.2f} (n={d.printed_n})"
        lines.append(
            f"{d.date:<12}{d.n_qualifying:>6}{mark}  {record:>16}  "
            f"{', '.join(d.providers) or '-'}"
        )
    met = sum(1 for d in days if d.meets_gate)
    lines.append("")
    lines.append(f"{met} of {len(days)} sessions would have met the gate under {head_version}")
    return "\n".join(lines)


def render_gaps(gaps: list[Gap], limit: int = 40) -> str:
    if not gaps:
        return "no gaps in the record"
    width = max(len(g.series) for g in gaps)
    lines = [f"{len(gaps)} gaps in the record, newest first", ""]
    for g in gaps[:limit]:
        note = " (corrected)" if g.corrected else ""
        lines.append(f"{g.date}  {g.series:<{width}}  {g.reason}{note}")
    if len(gaps) > limit:
        lines.append(f"... and {len(gaps) - limit} more")
    return "\n".join(lines)
