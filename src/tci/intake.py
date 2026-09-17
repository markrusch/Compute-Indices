# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""What happened to every observation between collection and the calculation.

`normalise.py` answers "which offers qualify". This module answers the question next to
it: of everything collected, what did not qualify, and at which rule. Those are different
questions and only one of them was instrumented.

WHY IT IS A SEPARATE MODULE AND NOT A FEW COUNTERS IN normalise.py. `normalise.py` is
hash-locked (see `tci.methodology.METHODOLOGY_FILES`). Editing it changes the methodology
hash, which the lock refuses under a released version, so adding counters there costs a
version bump, a notice and a succession entry - for a change that moves no number. The
governance is right to be that strict, so the instrument goes beside the calculation
instead of inside it.

THE COST OF THAT CHOICE, AND HOW IT IS PAID. Two copies of the same filter chain can
disagree, and a silently disagreeing audit is worse than none. So `classify` is not
trusted on its own authority: `tests/test_intake.py` replays every stored collection date
under the methodology version live on it and asserts that the rows this module calls
`admitted` are exactly the rows `normalise_observations` returns - same provider, source,
variant, node size and converted price, as a multiset. When the chain changes and this
module does not, that test fails against real data rather than against a fixture.

FIRST-FAILING-GATE. The chain is ordered, so each row is attributed to the one rule that
stopped it and the counts partition the day exactly: they sum to the rows collected. The
consequence, which the published page states and a reader will otherwise get wrong, is
that a gate's count is not the number of rows that would have printed had that rule been
looser. 828 rows stopped at the panel on 2026-09-17; many of them were also outside the
EU/EEA, or spot, or a GPU the index does not price. Admitting the panel would not have
produced 828 constituents.

Nothing here reads a print, and nothing here can change one.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from tci import db
from tci.config import Factors, load_factors
from tci.normalise import RowLike, _variant_map

# The gates, in the order normalise.py applies them. `admitted` is last because it is not
# a failure: it is the row reaching the estimator.
GATES: tuple[str, ...] = (
    "not_a_reference_variant",
    "not_in_panel",
    "term_not_reference",
    "tier_excluded",
    "outside_block",
    "below_node_floor",
    "size_undeclared",
    "currency_unsupported",
    "outside_price_band",
    "admitted",
)

# What each gate means in the words the site uses. A gate with no entry is shown as itself
# rather than guessed at.
GATE_REASONS: dict[str, str] = {
    "not_a_reference_variant": "a GPU the index does not price, or a variant that is not "
                               "the reference unit of its class",
    "not_in_panel": "the panel does not admit this provider, through this collector, in "
                    "this class",
    "term_not_reference": "a term-committed price, not the on-demand reference unit",
    "tier_excluded": "spot, interruptible or community capacity, which the unit excludes",
    "outside_block": "sold outside the region block being priced, or with no country "
                     "recorded at all",
    "below_node_floor": "fewer GPUs than the node-size floor",
    "size_undeclared": "an executable quote that did not state how many GPUs it was for",
    "currency_unsupported": "quoted in a currency the index has no rate for, so it was "
                            "never converted at a guessed one",
    "outside_price_band": "outside the price band that guards against junk listings",
    "admitted": "reached the calculation",
}

# Gates a reader should not read as recoverable volume. `not_a_reference_variant` is the
# index doing its job on a broad catalogue, not a loss.
_STRUCTURAL = frozenset({"not_a_reference_variant", "admitted"})


@dataclass(frozen=True)
class Verdict:
    """One observation and the rule that stopped it."""

    gate: str
    model_class: str  # '' where the row matched no configured class
    source: str
    provider: str


@dataclass(frozen=True)
class Cell:
    """The ledger's stored grain: one count per source, provider, class and gate."""

    source: str
    provider: str
    model_class: str
    gate: str
    n_rows: int


def classify(
    row: RowLike, factors: Factors, fx_eur_usd: float | None, block: frozenset[str]
) -> Verdict:
    """The first rule in `normalise_observations` that this row fails, or 'admitted'.

    The order of checks mirrors normalise.py line for line, deliberately. Keep them in
    step; the equivalence test in tests/test_intake.py is what notices if they drift.
    """
    source, provider = str(row["source"]), str(row["provider"])

    def verdict(gate: str, model_class: str = "") -> Verdict:
        return Verdict(gate, model_class, source, provider)

    entry = _variant_map(factors).get(row["gpu_model"])
    if entry is None:
        return verdict("not_a_reference_variant")
    model_class, factor = entry

    if not factors.admits(provider, source, model_class):
        return verdict("not_in_panel", model_class)
    if row["term"] != factors.reference_unit.term:
        return verdict("term_not_reference", model_class)
    if row["tier"] not in ("executable", "list"):
        return verdict("tier_excluded", model_class)
    if row["country"] not in block:
        return verdict("outside_block", model_class)

    gpu_count = row["gpu_count"]
    if gpu_count is not None and gpu_count < factors.filters.min_gpu_count:
        return verdict("below_node_floor", model_class)
    if row["tier"] == "executable" and gpu_count is None:
        return verdict("size_undeclared", model_class)

    raw = _raw_json(row)
    currency = str(raw.get("currency") or "USD").upper()
    native = raw.get("price_native_per_gpu_hr")
    price = float(native) if native is not None else float(row["price_usd_per_gpu_hr"])
    if currency == "EUR":
        if not fx_eur_usd:
            return verdict("currency_unsupported", model_class)
        price *= fx_eur_usd
    elif currency != "USD":
        return verdict("currency_unsupported", model_class)

    price *= factor
    band = factors.filters
    if not (band.price_floor_usd <= price <= band.price_ceiling_usd):
        return verdict("outside_price_band", model_class)

    return verdict("admitted", model_class)


def _raw_json(row: RowLike) -> dict[str, Any]:
    """As normalise.py reads it: an unparseable raw_json is not a special case."""
    try:
        parsed = json.loads(row["raw_json"])
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def ledger(
    rows: Sequence[RowLike],
    factors: Factors,
    fx_eur_usd: float | None = None,
    countries: frozenset[str] | None = None,
) -> list[Cell]:
    """Aggregate one date's observations into the ledger's stored grain."""
    block = countries if countries is not None else factors.eu_eea_countries
    counts: Counter[tuple[str, str, str, str]] = Counter()
    for row in rows:
        v = classify(row, factors, fx_eur_usd, block)
        counts[(v.source, v.provider, v.model_class, v.gate)] += 1
    return [
        Cell(source, provider, model_class, gate, n)
        for (source, provider, model_class, gate), n in sorted(counts.items())
    ]


def store(
    conn: sqlite3.Connection,
    date: str,
    block: str,
    cells: list[Cell],
    version: str,
    run_id: str,
) -> int:
    """Append a revision of one date's ledger. Returns the revision written."""
    row = conn.execute(
        "SELECT MAX(revision) FROM intake WHERE date = ? AND block = ?", (date, block)
    ).fetchone()
    revision = (row[0] + 1) if row and row[0] is not None else 1
    now = db.utc_now_iso()
    conn.executemany(
        "INSERT INTO intake (date, block, revision, source, provider, model_class, gate,"
        " n_rows, methodology_version, computed_at, run_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (date, block, revision, c.source, c.provider, c.model_class, c.gate,
             c.n_rows, version, now, run_id)
            for c in cells
        ],
    )
    conn.commit()
    return revision


def stored(conn: sqlite3.Connection) -> bool:
    """Whether this database has the ledger table yet.

    The table arrives with migration 0010, and the migration reaches a database when the
    daily run applies it. Until then the readers below return nothing rather than raising,
    so generating the site against a database that predates the migration produces a page
    computed from observations alone instead of a crash. `tests/test_pipeline_smoke.py`
    regenerates every surface without migrating first, which is exactly that case.
    """
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'intake'"
    ).fetchone() is not None


def head(conn: sqlite3.Connection, date: str, block: str = "EU_EEA") -> list[Cell]:
    """The latest revision of one date's ledger. An earlier revision is history, not data."""
    if not stored(conn):
        return []
    return [
        Cell(r["source"], r["provider"], r["model_class"], r["gate"], r["n_rows"])
        for r in conn.execute(
            "SELECT source, provider, model_class, gate, n_rows FROM intake"
            " WHERE date = ? AND block = ? AND revision ="
            " (SELECT MAX(revision) FROM intake WHERE date = ? AND block = ?)"
            " ORDER BY source, provider, model_class, gate",
            (date, block, date, block),
        )
    ]


def dates(conn: sqlite3.Connection, block: str = "EU_EEA", limit: int = 60) -> list[str]:
    """Dates with a stored ledger, oldest first."""
    if not stored(conn):
        return []
    rows = conn.execute(
        "SELECT DISTINCT date FROM intake WHERE block = ? ORDER BY date DESC LIMIT ?",
        (block, limit),
    ).fetchall()
    return sorted(r[0] for r in rows)


def by_gate(cells: list[Cell]) -> dict[str, int]:
    """Gate -> rows stopped there, in the order the chain applies them."""
    counts: Counter[str] = Counter()
    for c in cells:
        counts[c.gate] += c.n_rows
    return {g: counts[g] for g in GATES if counts[g]}


def yield_pct(cells: list[Cell]) -> float | None:
    """Admitted rows as a percentage of rows collected. None when nothing was collected."""
    total = sum(c.n_rows for c in cells)
    if not total:
        return None
    return sum(c.n_rows for c in cells if c.gate == "admitted") / total * 100.0


@dataclass(frozen=True)
class SourceYield:
    source: str
    collected: int
    admitted: int
    top_gate: str          # where most of this source's rows stop
    top_gate_rows: int

    @property
    def pct(self) -> float:
        return self.admitted / self.collected * 100.0 if self.collected else 0.0

    @property
    def structurally_blocked(self) -> bool:
        """Nothing admitted, and what stops it is not the index declining to price a GPU.

        A source in this state is collecting rows that cannot reach a print at all. That
        may be correct and permanent - a provider selling only outside the block, or
        publishing one region-uniform list price with no country to attribute it to - but
        it should be a stated position rather than something nobody noticed.
        """
        return self.admitted == 0 and self.top_gate not in _STRUCTURAL


def source_yields(cells: list[Cell]) -> list[SourceYield]:
    """Per collector: how much it brought in, how much reached the calculation, and where
    the rest stopped. Sorted by rows collected, descending."""
    collected: Counter[str] = Counter()
    admitted: Counter[str] = Counter()
    gates: dict[str, Counter[str]] = {}
    for c in cells:
        collected[c.source] += c.n_rows
        if c.gate == "admitted":
            admitted[c.source] += c.n_rows
        else:
            gates.setdefault(c.source, Counter())[c.gate] += c.n_rows
    out = []
    for source, n in collected.items():
        blocked = gates.get(source) or Counter()
        top, top_n = blocked.most_common(1)[0] if blocked else ("admitted", 0)
        out.append(SourceYield(source, n, admitted[source], top, top_n))
    return sorted(out, key=lambda s: (-s.collected, s.source))


@dataclass(frozen=True)
class Shift:
    """A gate whose count moved against its own recent history."""

    gate: str
    today: int
    baseline: float   # mean over the comparison window
    n_days: int

    @property
    def delta(self) -> float:
        return self.today - self.baseline


def shifts(
    conn: sqlite3.Connection,
    date: str,
    block: str = "EU_EEA",
    window: int = 7,
    min_rows: int = 20,
) -> list[Shift]:
    """Gates whose count today is more than double, or less than half, its recent mean.

    This is the instrument the Norway bug needed. It is deliberately a crude rule on a
    stored count rather than a model: the failure it is built for takes a gate from a
    handful of rows to hundreds overnight, and a threshold that obvious does not need
    tuning. `min_rows` keeps a gate that went from 1 row to 3 off the list.
    """
    history = [d for d in dates(conn, block) if d < date][-window:]
    if not history:
        return []
    today = by_gate(head(conn, date, block))
    past: dict[str, list[int]] = {}
    for d in history:
        counts = by_gate(head(conn, d, block))
        for gate in GATES:
            past.setdefault(gate, []).append(counts.get(gate, 0))
    out = []
    for gate in GATES:
        now = today.get(gate, 0)
        series = past.get(gate) or []
        if not series:
            continue
        baseline = sum(series) / len(series)
        if max(now, baseline) < min_rows:
            continue
        if now > baseline * 2 or now * 2 < baseline:
            out.append(Shift(gate, now, baseline, len(series)))
    return sorted(out, key=lambda s: -abs(s.delta))


@dataclass(frozen=True)
class Dropout:
    """A constituent that was reaching the calculation and today is not.

    WHY THIS EXISTS BESIDE `shifts`. `shifts` watches gate totals and needs a floor
    (`min_rows`) to stay quiet, which makes it blind to the failure that has actually cost
    this index the most prints. RunPod's secure-cloud H100 is a single row per session.
    Between 2026-08-05 and 2026-09-11 its upstream availability probe returned no stock at
    the one node size the collector asked about, so the row carried no GPU count and
    `normalise.py` dropped it for not stating its size. One row. On ten of the eleven
    sessions it happened, the headline gapped: four qualifying providers against a gate of
    five, and the fifth was sitting in the database with its price in hand.

    The published reliability page records those sessions as "fewer qualifying providers
    than the publication gate requires", which is what the calculation saw and is not the
    cause. The cause took a person reading a collector by hand to find.

    So this detector has no size threshold. One row that stops arriving is the signal.
    """

    source: str
    provider: str
    model_class: str
    gate: str            # where the rows go now
    was_admitted: float  # mean admitted per session over the window
    n_days: int


def dropouts(
    conn: sqlite3.Connection, date: str, block: str = "EU_EEA", window: int = 7
) -> list[Dropout]:
    """(source, provider, class) cells that admitted rows recently and admit none today.

    Deliberately ungated on volume: see `Dropout`. A cell that has never admitted anything
    is not a dropout - that is `SourceYield.structurally_blocked`, a different question.
    """
    history = [d for d in dates(conn, block) if d < date][-window:]
    if not history:
        return []
    Key = tuple[str, str, str]
    today_cells = head(conn, date, block)
    today_admitted: set[Key] = {
        (c.source, c.provider, c.model_class) for c in today_cells if c.gate == "admitted"
    }
    # Where today's rows for a cell ended up instead, if they arrived at all.
    today_gate: dict[Key, str] = {}
    for c in today_cells:
        key = (c.source, c.provider, c.model_class)
        if c.gate != "admitted" and key not in today_gate:
            today_gate[key] = c.gate

    past: dict[Key, list[int]] = {}
    for d in history:
        seen: dict[Key, int] = {}
        for c in head(conn, d, block):
            if c.gate == "admitted":
                key = (c.source, c.provider, c.model_class)
                seen[key] = seen.get(key, 0) + c.n_rows
        for key, n in seen.items():
            past.setdefault(key, []).append(n)

    out = []
    for key, series in past.items():
        if key in today_admitted or not series:
            continue
        source, provider, model_class = key
        out.append(
            Dropout(
                source, provider, model_class,
                today_gate.get(key, "not_collected"),
                sum(series) / len(history),
                len(series),
            )
        )
    return sorted(out, key=lambda d: (-d.was_admitted, d.source, d.provider))


def compute(
    conn: sqlite3.Connection, date: str, block: str = "EU_EEA"
) -> tuple[list[Cell], Factors]:
    """Classify one stored date without writing anything. Used by the CLI and the tests.

    The row set and the FX rate are the ones the calculation used for that date, not
    today's: the same observations joined through `runs`, and the rate recorded with the
    print rather than whatever the fx table offers now.
    """
    factors = load_factors(for_date=date)
    countries = (
        factors.eu_eea_countries if block == "EU_EEA" else factors.countries_of(block)
    )
    rows = conn.execute(
        "SELECT o.* FROM observations o JOIN runs r ON o.run_id = r.run_id"
        " WHERE r.utc_date = ?",
        (date,),
    ).fetchall()
    fx = conn.execute(
        "SELECT fx_rate FROM daily_index WHERE date = ? AND fx_rate IS NOT NULL LIMIT 1",
        (date,),
    ).fetchone()
    return ledger(rows, factors, fx["fx_rate"] if fx else None, countries), factors


def render(
    cells: list[Cell],
    date: str,
    version: str,
    shift_rows: list[Shift],
    dropout_rows: list[Dropout] | None = None,
) -> str:
    """The funnel on a terminal."""
    total = sum(c.n_rows for c in cells)
    if not total:
        return f"{date}: nothing collected"
    gates = by_gate(cells)
    pct = yield_pct(cells) or 0.0
    lines = [
        f"{date}: {total} observations collected under v{version}, "
        f"{gates.get('admitted', 0)} reached the calculation ({pct:.1f}%)",
        "",
        f"{'rows':>6}  {'share':>6}  gate",
    ]
    for gate, n in sorted(gates.items(), key=lambda kv: -kv[1]):
        lines.append(f"{n:>6}  {n / total * 100:>5.1f}%  {gate}")
    lines += ["", f"{'coll':>6}{'adm':>6}{'yield':>7}  source (where the rest stopped)"]
    for s in source_yields(cells):
        note = f"{s.top_gate} x{s.top_gate_rows}" if s.top_gate_rows else ""
        mark = "  <- nothing can reach a print" if s.structurally_blocked else ""
        lines.append(
            f"{s.collected:>6}{s.admitted:>6}{s.pct:>6.1f}%  {s.source:<16}{note}{mark}"
        )
    if shift_rows:
        lines += ["", "gates that moved against their trailing window:"]
        for sh in shift_rows:
            lines.append(
                f"  {sh.gate:<24} {sh.today} today vs {sh.baseline:.1f} mean"
                f" over {sh.n_days} sessions"
            )
    if dropout_rows:
        lines += ["", "constituents that were reaching the calculation and are not now:"]
        for dr in dropout_rows:
            lines.append(
                f"  {dr.provider}/{dr.source} {dr.model_class}: {dr.was_admitted:.1f}"
                f" rows/session over {dr.n_days} sessions, now {dr.gate}"
            )
    return "\n".join(lines)
