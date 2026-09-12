# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Run every collector against its live source, store nothing, and report what broke.

The daily run is fail-soft by design: a source that reshapes its page yields nothing, the
run continues, and the print either survives on the remaining constituents or gaps with a
reason. That is the right behaviour at 11:00 UTC and a poor way to find out. CoreWeave
added a JSON-LD block to its pricing page on 12 September 2026 and the first anyone knew
of it was a failed collector in the morning's committed database.

This is the same collection against the same sources, into a throwaway database, reporting
per source how many rows came back and which of the reference GPU models were among them.
Nothing it does can reach `data/eucri.db`, the index, or the site.

It is deliberately not on a schedule. SOURCES.md commits TCI to one request per source per
day, and a canary running beside the daily job would quietly make that two. It runs when a
change is proposed to collector code and when somebody asks for it.

Exit status: 0 when every source expected to report did; 1 when one did not. "Expected" is
`config/source_registry.yaml` status, not a guess — a source already known to be down is
not a new failure, and a source that has never returned a row is not evidence of a break.
"""

from __future__ import annotations

import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tci import db
from tci.collectors import base


@dataclass(frozen=True)
class SourceResult:
    source: str
    status: str  # ok | failed | empty
    rows: int
    models: tuple[str, ...]
    note: str

    @property
    def broken(self) -> bool:
        return self.status in ("failed", "empty")


def run(expected: frozenset[str] | None = None) -> list[SourceResult]:
    """Collect from every live source into a temporary database. Never touches the record."""
    from tci.commands import collectors_for_daily

    utc_date = datetime.now(UTC).strftime("%Y-%m-%d")
    with tempfile.TemporaryDirectory(prefix="tci-canary-") as tmp:
        conn = db.connect(Path(tmp) / "canary.db")
        db.migrate(conn)
        session = base.make_session()
        results = []
        for collector in collectors_for_daily():
            status = base.run_collector(conn, collector, utc_date, session)
            results.append(_summarise(conn, collector.name, status))
        conn.close()
    if expected is not None:
        results = [r for r in results if r.source in expected]
    return results


def _summarise(conn: sqlite3.Connection, source: str, status: str) -> SourceResult:
    row = conn.execute(
        "SELECT COUNT(*) n, COALESCE(GROUP_CONCAT(DISTINCT gpu_model), '') m"
        " FROM observations WHERE source = ?", (source,)
    ).fetchone()
    note = conn.execute(
        "SELECT COALESCE(notes, '') FROM runs WHERE source = ? ORDER BY started_utc DESC"
        " LIMIT 1", (source,)
    ).fetchone()[0]
    rows = row["n"]
    models = tuple(sorted(m for m in row["m"].split(",") if m))
    # A collector that returns cleanly and hands back nothing is the failure mode a
    # fail-soft pipeline hides best: no exception, no log line, just a source that has
    # silently stopped contributing. It counts as broken here.
    if status == "ok" and rows == 0:
        return SourceResult(source, "empty", 0, (), "collected cleanly, returned no rows")
    return SourceResult(source, status, rows, models, note)


def render(results: list[SourceResult]) -> str:
    width = max((len(r.source) for r in results), default=10)
    lines = [f"{'source':<{width}}  {'status':<8}{'rows':>6}  detail"]
    lines.append("-" * (width + 26))
    for r in sorted(results, key=lambda x: (not x.broken, x.source)):
        detail = r.note if r.broken else ", ".join(r.models[:6])
        lines.append(f"{r.source:<{width}}  {r.status:<8}{r.rows:>6}  {detail[:90]}")
    broken = [r.source for r in results if r.broken]
    lines.append("")
    lines.append(
        f"{len(results)} sources, {len(broken)} not reporting"
        + (f": {', '.join(broken)}" if broken else "")
    )
    return "\n".join(lines)
