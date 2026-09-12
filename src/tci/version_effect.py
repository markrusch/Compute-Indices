# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""What one methodology version would have printed against what another would have, same day.

Every notice has to quantify its expected effect on the level before the change takes
effect, and `config/notices.yaml` refuses "may affect the level" as an answer. Notice
2026-N2 goes further and commits to a figure after the fact: "the first print under v0.5.0
will state its size against a v0.4.0 recomputation of the same day". Nothing could produce
that number. `reproduce` recomputes a date under the version that was live on it, which is
the right rule for checking the record and the wrong one for measuring a transition, and
there was no second version to compare against.

This computes both legs on one day's stored observations and subtracts them. The step a
reader sees between two sessions mixes the version change with whatever the market and the
panel did overnight; this holds the day fixed, so what is left is the version.

**It is why the comparison matters on 22 September in particular.** N2 quantified v0.5.0 as
taking the headline from $3.25 to $3.49, measured on the 7 September panel. The headline
reached $3.49 on 12 September under v0.3.0-dev for an unrelated reason — vast.ai came back
and the weighted median landed on RunPod's price — so the step visible in the published
series on the day is going to be close to nothing, and the honest number is this one rather
than that difference.

Nothing here writes. Both legs run against in-memory copies of the database, the real file
is opened read-only, and no revision is stored: a recomputation under a version that was
never live on a date is an analysis, not a print, and must never be able to become one.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from tci import config


@dataclass(frozen=True)
class Effect:
    series: str
    date: str
    before: float | None
    after: float | None
    before_n: int
    after_n: int
    before_flags: str
    after_flags: str

    NOT_COMPUTED = "not computed under this version"

    @property
    def moved(self) -> bool:
        return self.before != self.after

    @property
    def introduced(self) -> bool:
        """The later version computes a series the earlier one did not define at all.

        Distinct from a value moving. Both legs read None, so `moved` is False, and a row
        marked as unchanged when the series did not previously exist would be misread.
        """
        return self.before_flags == self.NOT_COMPUTED

    @property
    def delta(self) -> float | None:
        if self.before is None or self.after is None:
            return None
        return self.after - self.before

    @property
    def pct(self) -> float | None:
        if self.before in (None, 0) or self.after is None:
            return None
        assert self.before is not None
        return (self.after - self.before) / self.before * 100.0

    def describe(self) -> str:
        def leg(value: float | None, n: int, flags: str) -> str:
            if value is None:
                return f"gap ({flags or 'no reason recorded'}, n={n})"
            return f"${value:.4f} (n={n})"

        line = f"{leg(self.before, self.before_n, self.before_flags)} -> " \
               f"{leg(self.after, self.after_n, self.after_flags)}"
        if self.pct is not None and self.delta is not None:
            line += f"  {self.delta:+.4f} ({self.pct:+.2f}%)"
        return line


def versions() -> dict[str, config.SuccessionEntry]:
    return {entry.version: entry for entry in config.load_succession()}


def compare(
    conn: sqlite3.Connection, date: str, before: str, after: str,
    series: str | None = None,
) -> list[Effect]:
    """Recompute `date` under two versions and report the difference per series.

    Both legs use the FX the day's prints were published with, so the comparison cannot
    pick up a currency move that neither version caused.
    """
    known = versions()
    for name in (before, after):
        if name not in known:
            raise KeyError(f"unknown methodology version {name!r}; have {sorted(known)}")

    left = _prints_under(conn, date, known[before])
    right = _prints_under(conn, date, known[after])

    out = []
    for name in sorted(set(left) | set(right)):
        if series and name != series:
            continue
        a, b = left.get(name), right.get(name)
        out.append(Effect(
            series=name, date=date,
            before=a["value_usd"] if a else None,
            after=b["value_usd"] if b else None,
            before_n=a["n_sources"] if a else 0,
            after_n=b["n_sources"] if b else 0,
            before_flags=(a["flags"] or "") if a else Effect.NOT_COMPUTED,
            after_flags=(b["flags"] or "") if b else Effect.NOT_COMPUTED,
        ))
    return out


def _prints_under(
    conn: sqlite3.Connection, date: str, entry: config.SuccessionEntry
) -> dict[str, sqlite3.Row]:
    """Every series for `date`, computed under one version's frozen parameters.

    The version is forced by pointing `load_factors` at that version's parameter
    directory, which is the same directory the succession would have selected had the
    date fallen in its window. `compute_all_series` reads the version off the date, so it
    is patched for the duration of this call and restored after — the alternative is a
    version argument threaded through the calculation path, which would be a change to
    hash-locked code for the benefit of an analysis that is not in it.
    """
    from tci import commands
    from tci.reproduce import _memory_copy, _recorded_fx

    mem = _memory_copy(conn)
    real_factors, real_sovereign = config.load_factors, config.load_sovereign
    level = logging.getLogger("tci").level
    logging.getLogger("tci").setLevel(logging.WARNING)
    try:
        config.load_factors = lambda *a, **k: real_factors(
            config_dir=entry.params_dir
        )
        config.load_sovereign = lambda *a, **k: real_sovereign(
            config_dir=entry.params_dir
        )
        commands.compute_all_series(
            mem, date, correction=True, fx_override=_recorded_fx(conn, date)
        )
        rows = mem.execute(
            "SELECT d.* FROM daily_index d JOIN (SELECT series, MAX(revision) rev"
            " FROM daily_index WHERE date = ? GROUP BY series) m"
            " ON d.series = m.series AND d.revision = m.rev WHERE d.date = ?",
            (date, date),
        ).fetchall()
        return {r["series"]: r for r in rows}
    finally:
        config.load_factors = real_factors
        config.load_sovereign = real_sovereign
        logging.getLogger("tci").setLevel(level)
        mem.close()


def render(effects: list[Effect], date: str, before: str, after: str) -> str:
    lines = [
        f"{date} recomputed under v{before} and under v{after}, on the same stored",
        "observations and the same recorded FX. Neither leg is a print and neither is stored.",
        "",
    ]
    moved = [e for e in effects if e.moved]
    new_series = [e for e in effects if e.introduced]
    width = max((len(e.series) for e in effects), default=10)
    for e in effects:
        mark = "+" if e.introduced else (" " if e.moved else "=")
        lines.append(f"{mark} {e.series:<{width}}  {e.describe()}")
    lines.append("")
    if moved or new_series:
        tail = f"; {len(new_series)} are new in v{after}" if new_series else ""
        lines.append(f"{len(moved)} of {len(effects)} series change value{tail}")
    else:
        lines.append(f"no series differs between v{before} and v{after} on this day")
    return "\n".join(lines)
