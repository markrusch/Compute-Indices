# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Intraday sampling: sweeps through the day, and the index reconstructed at each one.

The fixing is untouched. `tci.run daily` still collects once at 11:00 UTC into
`observations`, and every print is computed from that collection alone. This module reads
the same sources more often, writes what it reads somewhere no print looks, and replays the
fixing's own calculation over it, so the day's movement can be seen and a settlement window
can be studied before anybody proposes one.

WHERE THE READS GO, AND WHY NOT INTO `observations`. `commands._observations_for_date`
selects every observation whose run is dated that day. An hourly read stored there would
enter the fixing the moment it was written: the print would become an average of the
day's reads without a version, a notice or a line in the CHANGELOG. So intraday reads live
in `data/intraday/YYYY-MM-DD.jsonl`, one file per UTC day, appended and never rewritten.

WHY FILES AND NOT A SECOND SQLITE DATABASE. The store is committed to git every hour. A
binary database changes wholesale on every commit and git keeps each version whole; an
append-only text file changes by the lines appended and git keeps the difference. Each
file is also self-contained: a day can be read, verified and discarded without the rest.

WHAT A LINE HOLDS. Two kinds, in the order written:

  {"v":1,"kind":"book","source":S,"book":H,"base":H0|null,"add":[row...],"drop":[row...]}
  {"v":1,"kind":"sweep","id":...,"started":T,"at":T,"sources":{S:{"status":...}}}

A book is the full set of rows one read of one source produced, as a multiset. It is
written as a difference from the previous book of the same source in the same file (most
catalogs are identical from one hour to the next, so most books are a reference to an
existing hash and cost nothing), and is verified against its hash when read back. A
mismatch raises: a store that has been edited is not something to reconstruct prices from.
The sweep line comes last and is what makes its books count; a runner killed between the
two leaves orphan books that nothing refers to, never a half-recorded sweep.

A row keeps exactly the columns the calculation reads (see `CALC_RAW_KEYS` for the part of
`raw_json` it needs). Anything else a collector records is in the daily `observations`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import statistics
import uuid
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import requests
import yaml

from tci import config
from tci.collectors import base
from tci.config import CONFIG_DIR
from tci.models import Observation

log = logging.getLogger("tci.intraday")

REPO_ROOT = Path(__file__).resolve().parents[2]
STORE_DIR = REPO_ROOT / "data" / "intraday"
CONFIG_PATH = CONFIG_DIR / "intraday.yaml"
FORMAT_VERSION = 1

# The keys of `raw_json` that normalise.py reads: the quoted currency and native amount
# (Scaleway quotes EUR and is converted at print time), and a static entry's
# last_verified date (the staleness rule). tests/test_intraday.py reads normalise.py's
# source and fails if it starts reading a key that is not listed here, because a key
# dropped at this point would change the intraday value without any error.
CALC_RAW_KEYS = ("currency", "last_verified", "price_native_per_gpu_hr")

# provider, gpu_model, gpu_count, price_usd_per_gpu_hr, region, country, interconnect,
# tier, term, raw_json (the CALC_RAW_KEYS subset, canonical JSON, or "")
Row = tuple[str, str, int | None, float, str | None, str | None, str | None, str, str, str]


class IntradayStoreError(RuntimeError):
    """The store on disk does not verify. Raised, never repaired."""


# ==========================================================================
# configuration
# ==========================================================================


@dataclass(frozen=True)
class Cadence:
    every_hours: float
    max_age_hours: float


@dataclass(frozen=True)
class SettlementWindow:
    start: str  # "HH:MM" UTC, inclusive
    end: str  # "HH:MM" UTC, exclusive
    method: str  # "mean" | "median"
    min_points: int

    def bounds(self, day: str) -> tuple[datetime, datetime]:
        return _at(day, self.start), _at(day, self.end)


@dataclass(frozen=True)
class IntradayConfig:
    due_slack_minutes: int
    sources: dict[str, Cadence]
    series: tuple[str, ...]
    settlement: SettlementWindow

    @property
    def longest_age(self) -> timedelta:
        return timedelta(hours=max(c.max_age_hours for c in self.sources.values()))


def _at(day: str, hhmm: str) -> datetime:
    return datetime.strptime(f"{day} {hhmm}", "%Y-%m-%d %H:%M").replace(tzinfo=UTC)


def load_config(path: Path | None = None) -> IntradayConfig:
    raw = yaml.safe_load((path or CONFIG_PATH).read_text(encoding="utf-8"))
    sources = {
        name: Cadence(float(c["every_hours"]), float(c["max_age_hours"]))
        for name, c in (raw.get("sources") or {}).items()
    }
    if not sources:
        raise ValueError("intraday.yaml names no sources")
    for name, c in sources.items():
        # A read that expires before the next one is due leaves a hole in every cycle.
        if c.max_age_hours < c.every_hours:
            raise ValueError(f"intraday.yaml: {name} max_age_hours < every_hours")
    st = raw["settlement"]
    window = SettlementWindow(
        start=str(st["start"]), end=str(st["end"]), method=str(st.get("method", "mean")),
        min_points=int(st.get("min_points", 1)),
    )
    if window.method not in ("mean", "median"):
        raise ValueError(f"intraday.yaml: unknown settlement method {window.method!r}")
    if not _at("2000-01-01", window.start) < _at("2000-01-01", window.end):
        raise ValueError("intraday.yaml: settlement window must end after it starts")
    return IntradayConfig(
        due_slack_minutes=int(raw.get("due_slack_minutes", 0)),
        sources=sources,
        series=tuple(raw.get("series") or ()),
        settlement=window,
    )


# ==========================================================================
# rows and books
# ==========================================================================


def _slim_raw(raw_json: str | None) -> str:
    try:
        raw = json.loads(raw_json or "")
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(raw, dict):
        return ""
    kept = {k: raw[k] for k in CALC_RAW_KEYS if raw.get(k) is not None}
    return json.dumps(kept, sort_keys=True, separators=(",", ":")) if kept else ""


def row_of(o: Observation | sqlite3.Row | dict[str, Any]) -> Row:
    """The calculation's columns of one observation, from a collector or from the database."""
    def get(key: str) -> Any:
        return getattr(o, key) if isinstance(o, Observation) else o[key]

    count = get("gpu_count")
    return (
        str(get("provider")), str(get("gpu_model")),
        None if count is None else int(count),
        float(get("price_usd_per_gpu_hr")),
        get("region"), get("country"), get("interconnect"),
        str(get("tier")), str(get("term")), _slim_raw(get("raw_json")),
    )


def _row_key(row: Sequence[Any]) -> str:
    return json.dumps(list(row), separators=(",", ":"))


def _row_from_json(value: list[Any]) -> Row:
    if len(value) != 10:
        raise IntradayStoreError(f"row has {len(value)} fields, expected 10")
    p, m, n, price, region, country, ic, tier, term, raw = value
    return (p, m, None if n is None else int(n), float(price), region, country, ic,
            tier, term, raw)


def book_hash(rows: Iterable[Row]) -> str:
    canonical = "\n".join(sorted(_row_key(r) for r in rows))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def calc_rows(source: str, rows: Iterable[Row]) -> list[dict[str, Any]]:
    """Rows in the shape normalise.normalise_observations reads."""
    return [
        {
            "source": source, "provider": r[0], "gpu_model": r[1], "gpu_count": r[2],
            "price_usd_per_gpu_hr": r[3], "region": r[4], "country": r[5],
            "interconnect": r[6], "tier": r[7], "term": r[8], "raw_json": r[9] or "{}",
        }
        for r in rows
    ]


# ==========================================================================
# the store
# ==========================================================================


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


@dataclass(frozen=True)
class SourceRead:
    """One source's outcome inside one sweep."""

    status: str  # 'ok' | 'failed'
    at: datetime
    book: str | None = None
    n: int = 0
    error: str | None = None


@dataclass(frozen=True)
class Sweep:
    id: str
    started: datetime
    at: datetime
    sources: dict[str, SourceRead]


@dataclass
class DayLog:
    """One parsed, verified day file."""

    # book hash -> (source, rows)
    books: dict[str, tuple[str, tuple[Row, ...]]] = field(default_factory=dict)
    sweeps: list[Sweep] = field(default_factory=list)
    latest_book: dict[str, str] = field(default_factory=dict)  # source -> last hash written


class Store:
    """Append-only, day-partitioned JSONL. The only writer is `append`."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or STORE_DIR
        self._cache: dict[str, DayLog] = {}

    def path_for(self, day: str) -> Path:
        return self.root / f"{day}.jsonl"

    def days(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(p.stem for p in self.root.glob("*.jsonl"))

    def load(self, day: str) -> DayLog:
        if day in self._cache:
            return self._cache[day]
        log_ = DayLog()
        path = self.path_for(day)
        if path.exists():
            lines = path.read_text(encoding="utf-8").split("\n")
            for i, line in enumerate(lines):
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError as exc:
                    # A runner killed mid-write leaves a torn final line and nothing after
                    # it. That line is dropped; its sweep line never landed, so nothing
                    # referred to it. A bad line with more lines after it is not a torn
                    # write and is not skipped.
                    if all(not rest.strip() for rest in lines[i + 1:]):
                        log.warning("%s: torn final line ignored", path.name)
                        break
                    raise IntradayStoreError(f"{path.name}:{i + 1}: {exc}") from exc
                self._apply(log_, rec, f"{path.name}:{i + 1}")
        self._cache[day] = log_
        return log_

    @staticmethod
    def _apply(log_: DayLog, rec: dict[str, Any], where: str) -> None:
        if rec.get("v") != FORMAT_VERSION:
            raise IntradayStoreError(f"{where}: unknown format version {rec.get('v')!r}")
        kind = rec.get("kind")
        if kind == "book":
            base_hash = rec.get("base")
            counts: Counter[str] = Counter()
            if base_hash is not None:
                if base_hash not in log_.books:
                    raise IntradayStoreError(f"{where}: base {base_hash} not in this file")
                counts.update(_row_key(r) for r in log_.books[base_hash][1])
            counts.subtract(_row_key(_row_from_json(r)) for r in rec.get("drop") or [])
            if any(v < 0 for v in counts.values()):
                raise IntradayStoreError(f"{where}: drops a row its base does not hold")
            counts.update(_row_key(_row_from_json(r)) for r in rec.get("add") or [])
            rows = tuple(
                _row_from_json(json.loads(k)) for k in sorted(counts.elements())
            )
            if book_hash(rows) != rec["book"]:
                raise IntradayStoreError(f"{where}: book {rec['book']} does not verify")
            log_.books[rec["book"]] = (str(rec["source"]), rows)
            log_.latest_book[str(rec["source"])] = rec["book"]
        elif kind == "sweep":
            sources = {}
            for name, s in (rec.get("sources") or {}).items():
                if s.get("status") == "ok" and s.get("book") not in log_.books:
                    raise IntradayStoreError(f"{where}: {name} refers to a missing book")
                sources[name] = SourceRead(
                    status=str(s["status"]), at=parse_iso(s["at"]), book=s.get("book"),
                    n=int(s.get("n") or 0), error=s.get("error"),
                )
            log_.sweeps.append(Sweep(
                id=str(rec["id"]), started=parse_iso(rec["started"]),
                at=parse_iso(rec["at"]), sources=sources,
            ))
            for name, s in sources.items():
                if s.book is not None:
                    log_.latest_book[name] = s.book
        else:
            raise IntradayStoreError(f"{where}: unknown line kind {kind!r}")

    def append(self, sweep: Sweep, books: dict[str, list[Row]]) -> Path:
        """Write the books this sweep read, then the sweep line that makes them count."""
        day = sweep.started.strftime("%Y-%m-%d")
        log_ = self.load(day)
        lines: list[dict[str, Any]] = []
        for source in sorted(books):
            rows = books[source]
            h = book_hash(rows)
            # An identical book already in this file is referred to, not written again.
            if h not in log_.books:
                lines.append(self._book_line(log_, source, h, rows))
                log_.books[h] = (source, tuple(sorted(rows, key=_row_key)))
            log_.latest_book[source] = h
        lines.append({
            "v": FORMAT_VERSION, "kind": "sweep", "id": sweep.id,
            "started": _iso(sweep.started), "at": _iso(sweep.at),
            "sources": {
                name: {k: v for k, v in (
                    ("status", s.status), ("at", _iso(s.at)), ("book", s.book),
                    ("n", s.n), ("error", s.error),
                ) if v is not None}
                for name, s in sorted(sweep.sources.items())
            },
        })
        path = self.path_for(day)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Append mode, one write per line: existing content is never rewritten.
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            for line in lines:
                f.write(json.dumps(line, separators=(",", ":")) + "\n")
        log_.sweeps.append(sweep)
        return path

    @staticmethod
    def _book_line(log_: DayLog, source: str, h: str, rows: list[Row]) -> dict[str, Any]:
        new = Counter(_row_key(r) for r in rows)
        base_hash = log_.latest_book.get(source)
        add: list[str]
        drop: list[str]
        if base_hash is not None:
            old = Counter(_row_key(r) for r in log_.books[base_hash][1])
            add = sorted((new - old).elements())
            drop = sorted((old - new).elements())
            if len(add) + len(drop) >= len(rows):
                base_hash, add, drop = None, sorted(new.elements()), []
        else:
            add, drop = sorted(new.elements()), []
        return {
            "v": FORMAT_VERSION, "kind": "book", "source": source, "book": h,
            "base": base_hash, "add": [json.loads(k) for k in add],
            "drop": [json.loads(k) for k in drop],
        }


# ==========================================================================
# reads: intraday sweeps, and the daily fixing's own collection
# ==========================================================================


@dataclass(frozen=True)
class Read:
    """One successful read of one source, from either origin."""

    source: str
    at: datetime
    rows: tuple[Row, ...]
    origin: str  # 'intraday' | 'fixing'


def _days_between(start: datetime, end: datetime) -> list[str]:
    d, out = start.date(), []
    while d <= end.date():
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def intraday_reads(store: Store, start: datetime, end: datetime) -> list[Read]:
    out = []
    for day in _days_between(start - timedelta(days=1), end):
        log_ = store.load(day)
        for sw in log_.sweeps:
            for name, s in sw.sources.items():
                if s.status == "ok" and s.book is not None and start <= s.at <= end:
                    out.append(Read(name, s.at, log_.books[s.book][1], "intraday"))
    return out


def fixing_reads(conn: sqlite3.Connection, start: datetime, end: datetime,
                 sources: Iterable[str]) -> list[Read]:
    """The daily run's own reads, from the committed database, never re-requested.

    Timed by when each collector finished, not by the run's nominal date: a catch-up run
    for yesterday made this morning read this morning's prices.
    """
    wanted = sorted(set(sources))
    if not wanted:
        return []
    marks = ",".join("?" for _ in wanted)
    runs = conn.execute(
        f"SELECT run_id, source, finished_utc FROM runs WHERE status = 'ok'"
        f" AND finished_utc >= ? AND finished_utc <= ? AND source IN ({marks})",
        (_iso(start), _iso(end), *wanted),
    ).fetchall()
    out = []
    for r in runs:
        obs = conn.execute(
            "SELECT * FROM observations WHERE run_id = ?", (r["run_id"],)
        ).fetchall()
        rows = tuple(sorted((row_of(o) for o in obs), key=_row_key))
        out.append(Read(r["source"], parse_iso(r["finished_utc"]), rows, "fixing"))
    return out


def all_reads(store: Store, conn: sqlite3.Connection | None, cfg: IntradayConfig,
              start: datetime, end: datetime) -> list[Read]:
    reads = [r for r in intraday_reads(store, start, end) if r.source in cfg.sources]
    if conn is not None:
        reads += fixing_reads(conn, start, end, cfg.sources)
    return sorted(reads, key=lambda r: (r.at, r.source, r.origin))


# ==========================================================================
# scheduling and the sweep itself
# ==========================================================================


def due_sources(cfg: IntradayConfig, last_read: dict[str, datetime], now: datetime
                ) -> list[str]:
    slack = timedelta(minutes=cfg.due_slack_minutes)
    return [
        name for name, c in cfg.sources.items()
        if name not in last_read
        or now - last_read[name] >= timedelta(hours=c.every_hours) - slack
    ]


def last_reads(store: Store, conn: sqlite3.Connection | None, cfg: IntradayConfig,
               now: datetime) -> dict[str, datetime]:
    """When each source was last read successfully, by a sweep or by the fixing.

    The fixing counts. Without it the 11:17 sweep would read vast.ai seven minutes after
    the 11:00 run did, which is a second request for the same book.
    """
    out: dict[str, datetime] = {}
    for r in all_reads(store, conn, cfg, now - timedelta(days=1), now):
        out[r.source] = max(r.at, out.get(r.source, r.at))
    return out


def sweep_collectors(names: Iterable[str]) -> list[base.Collector]:
    """Fresh instances of the daily collectors named in the config, in the daily order."""
    from tci.commands import collectors_for_daily

    wanted = set(names)
    return [c for c in collectors_for_daily() if c.name in wanted]


@dataclass(frozen=True)
class SweepResult:
    sweep: Sweep | None
    path: Path | None
    not_due: tuple[str, ...]


def run_sweep(
    store: Store,
    conn: sqlite3.Connection | None,
    cfg: IntradayConfig,
    *,
    collectors: Sequence[base.Collector] | None = None,
    session: requests.Session | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    only: Iterable[str] | None = None,
    force: bool = False,
) -> SweepResult:
    """Read every due source once, fail-soft per source, and append one sweep.

    A source that raises is recorded as failed with its reason, exactly as the daily run
    records it in `runs.notes`; the others are still read. Nothing is written when no
    source was due, so an idle hour costs neither a request nor a line.
    """
    started = now()
    pool = list(collectors) if collectors is not None else sweep_collectors(cfg.sources)
    pool = [c for c in pool if c.name in cfg.sources]
    if only is not None:
        keep = set(only)
        pool = [c for c in pool if c.name in keep]
    due = set(cfg.sources) if force else set(
        due_sources(cfg, last_reads(store, conn, cfg, started), started)
    )
    run = [c for c in pool if c.name in due]
    not_due = tuple(sorted(c.name for c in pool if c.name not in due))
    if not run:
        log.info("intraday: nothing due (%s)", ", ".join(not_due) or "no sources")
        return SweepResult(None, None, not_due)

    http = session or base.make_session()
    reads: dict[str, SourceRead] = {}
    books: dict[str, list[Row]] = {}
    for c in run:
        try:
            rows = [row_of(o) for o in c.collect(http)]
        except Exception as exc:  # noqa: BLE001 - fail-soft, reason recorded
            log.exception("intraday: %s failed (fail-soft, continuing)", c.name)
            reason = f"{type(exc).__name__}: {exc}"[:300]
            reads[c.name] = SourceRead("failed", now(), error=reason)
            continue
        books[c.name] = rows
        reads[c.name] = SourceRead("ok", now(), book=book_hash(rows), n=len(rows))
        log.info("intraday: %s %d rows", c.name, len(rows))
    sweep = Sweep(id=str(uuid.uuid4()), started=started, at=now(), sources=reads)
    path = store.append(sweep, books)
    return SweepResult(sweep, path, not_due)


# ==========================================================================
# reconstruction: the fixing's calculation, replayed at every sweep
# ==========================================================================


@dataclass(frozen=True)
class Snapshot:
    at: datetime
    origin: str  # 'intraday' | 'fixing'
    values: dict[str, tuple[float | None, float | None, int, str]]  # series -> usd, eur, n, flags
    ages: dict[str, int]  # source -> minutes since its read
    missing: tuple[str, ...]  # configured sources with no read inside max_age


def state_at(reads: Sequence[Read], t: datetime, cfg: IntradayConfig) -> dict[str, Read]:
    """The newest read of each source at or before t, if it is within max_age."""
    state: dict[str, Read] = {}
    for r in reads:
        if r.at > t:
            continue
        if t - r.at > timedelta(hours=cfg.sources[r.source].max_age_hours):
            continue
        if r.source not in state or r.at >= state[r.source].at:
            state[r.source] = r
    return state


def event_times(store: Store, reads: Sequence[Read], start: datetime, end: datetime
                ) -> list[tuple[datetime, str]]:
    """One point per sweep that read something, and one per fixing once it finished."""
    events: dict[datetime, str] = {}
    for day in _days_between(start, end):
        for sw in store.load(day).sweeps:
            if start <= sw.at <= end and any(s.status == "ok" for s in sw.sources.values()):
                events[sw.at] = "intraday"
    fixing_end: dict[str, datetime] = {}
    for r in reads:
        if r.origin == "fixing":
            day = r.at.strftime("%Y-%m-%d")
            fixing_end[day] = max(r.at, fixing_end.get(day, r.at))
    for t in fixing_end.values():
        if start <= t <= end:
            events[t] = "fixing"
    return sorted(events.items())


class _Calc:
    """Per-date inputs the fixing would use, loaded once per date."""

    def __init__(self, conn: sqlite3.Connection | None) -> None:
        self.conn = conn
        self._by_date: dict[str, tuple[Any, frozenset[str], tuple[float, str] | None]] = {}

    def inputs(self, date: str) -> tuple[Any, frozenset[str], tuple[float, str] | None]:
        if date not in self._by_date:
            from tci.collectors.fx import rate_for

            factors = config.load_factors(for_date=date)
            sovereign = config.load_sovereign(for_date=date)
            fx = (rate_for(self.conn, date, strictly_before=factors.fx.strictly_before)
                  if self.conn is not None else None)
            self._by_date[date] = (factors, sovereign, fx)
        return self._by_date[date]


def compute_snapshot(state: dict[str, Read], t: datetime, origin: str,
                     cfg: IntradayConfig, calc: _Calc) -> Snapshot:
    from tci.commands import print_definitions
    from tci.index import compute_print
    from tci.normalise import normalise_observations

    date = t.strftime("%Y-%m-%d")
    factors, sovereign, fx = calc.inputs(date)
    rows = [row for r in state.values() for row in calc_rows(r.source, r.rows)]
    normalised = normalise_observations(rows, factors, fx_eur_usd=fx[0] if fx else None)
    definitions = print_definitions(factors, sovereign, {o.model_class for o in normalised})
    values: dict[str, tuple[float | None, float | None, int, str]] = {}
    for series in cfg.series:
        if series not in definitions:
            # The fixing does not compute a class series on a day with no offer in the
            # class; the reconstruction says the same thing, in words.
            values[series] = (None, None, 0, "no_offers_in_class")
            continue
        _cls, population, predicate = definitions[series]
        p = compute_print(date, series, [o for o in normalised if predicate(o)], factors,
                          fx, population=population)
        values[series] = (p.value_usd, p.value_eur, p.n_sources, p.flags)
    return Snapshot(
        at=t, origin=origin, values=values,
        ages={s: int((t - r.at).total_seconds() // 60) for s, r in sorted(state.items())},
        missing=tuple(sorted(set(cfg.sources) - set(state))),
    )


def path(store: Store, conn: sqlite3.Connection | None, cfg: IntradayConfig,
         start: datetime, end: datetime) -> list[Snapshot]:
    """Every reconstructed point in [start, end], oldest first."""
    reads = all_reads(store, conn, cfg, start - cfg.longest_age, end)
    calc = _Calc(conn)
    return [
        compute_snapshot(state_at(reads, t, cfg), t, origin, cfg, calc)
        for t, origin in event_times(store, reads, start, end)
    ]


# ==========================================================================
# the settlement window
# ==========================================================================


@dataclass(frozen=True)
class Settlement:
    date: str
    series: str
    value_usd: float | None
    n_points: int
    window: tuple[datetime, datetime]
    method: str
    reason: str | None  # why value_usd is null, in words
    fixing_usd: float | None


def settle(snapshots: Sequence[Snapshot], day: str, series: str,
           window: SettlementWindow, fixing_usd: float | None = None) -> Settlement:
    """Average the reconstructed values inside the window. Below min_points, a gap.

    A gapped point inside the window is not a zero and not skipped silently: it reduces
    the count, and a window that falls below `min_points` publishes nothing.
    """
    lo, hi = window.bounds(day)
    inside = [s for s in snapshots if lo <= s.at < hi and series in s.values]
    vals = [v for s in inside if (v := s.values[series][0]) is not None]
    value: float | None = None
    reason: str | None = None
    if len(vals) < window.min_points:
        reason = (f"{len(vals)} published value(s) in the window, "
                  f"{window.min_points} required")
    else:
        agg = statistics.fmean(vals) if window.method == "mean" else statistics.median(vals)
        value = round(agg, 6)
    return Settlement(day, series, value, len(vals), (lo, hi), window.method, reason,
                      fixing_usd)
