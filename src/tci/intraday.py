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
in `data/intraday/YYYY-MM/YYYY-MM-DD[.N].jsonl`: one folder per month, one or more segments
per UTC day, appended and never rewritten.

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
existing hash and cost nothing), and is verified against its hash when read back. The
sweep line comes last and is what makes its books count; a runner killed between the two
leaves orphan books that nothing refers to, never a half-recorded sweep.

WHEN A FILE IS DAMAGED. A segment is trusted up to its first line that does not parse or
verify, and nothing after it is used. The problem is recorded (`Store.problems`, the
`verify` command, the page) rather than raised, because one bad file must cost the reads in
it and not every later sweep: under the first version of this store, a single torn line in
today's file made every remaining sweep of the day fail on load. The writer never appends
to a segment that has a problem, or that has reached `max_segment_bytes`; it opens the
next one, `.2`, `.3`, each self-contained, so a damaged file stays as it was found.

A row keeps exactly the columns the calculation reads (see `CALC_RAW_KEYS` for the part of
`raw_json` it needs). Anything else a collector records is in the daily `observations`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import statistics
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
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
    """The store on disk does not verify. Raised in strict mode, never repaired."""


class StoreBusy(RuntimeError):
    """Another sweep holds the store's lock."""


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
    workers: int = 4
    source_timeout_seconds: float = 300.0
    sweep_budget_seconds: float = 900.0
    max_backoff_hours: float = 24.0
    max_segment_bytes: int = 8_000_000
    fixing_guard: tuple[str, str] | None = ("10:40", "13:00")  # HH:MM UTC, [start, end)

    @property
    def longest_age(self) -> timedelta:
        return timedelta(hours=max(c.max_age_hours for c in self.sources.values()))


def _at(day: str, hhmm: str) -> datetime:
    return datetime.strptime(f"{day} {hhmm}", "%Y-%m-%d %H:%M").replace(tzinfo=UTC)


def _guard(raw: dict[str, Any] | None) -> tuple[str, str] | None:
    if not raw:
        return None
    start, end = str(raw["start"]), str(raw["end"])
    if not _at("2000-01-01", start) < _at("2000-01-01", end):
        raise ValueError("intraday.yaml: fixing_guard must end after it starts")
    return start, end


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
    cfg = IntradayConfig(
        due_slack_minutes=int(raw.get("due_slack_minutes", 0)),
        sources=sources,
        series=tuple(raw.get("series") or ()),
        settlement=window,
        workers=int(raw.get("workers", 4)),
        source_timeout_seconds=float(raw.get("source_timeout_seconds", 300)),
        sweep_budget_seconds=float(raw.get("sweep_budget_seconds", 900)),
        max_backoff_hours=float(raw.get("max_backoff_hours", 24)),
        max_segment_bytes=int(raw.get("max_segment_bytes", 8_000_000)),
        fixing_guard=_guard(raw.get("fixing_guard", {"start": "10:40", "end": "13:00"})),
    )
    if cfg.workers < 1 or cfg.source_timeout_seconds <= 0 or cfg.sweep_budget_seconds <= 0:
        raise ValueError("intraday.yaml: workers and both time limits must be positive")
    if cfg.max_segment_bytes < 1024:
        raise ValueError("intraday.yaml: max_segment_bytes is too small to hold one read")
    return cfg


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
    dropped: int = 0  # malformed rows the collector returned and the store refused


@dataclass(frozen=True)
class Sweep:
    id: str
    started: datetime
    at: datetime
    sources: dict[str, SourceRead]


@dataclass
class DayLog:
    """The verified content of one segment, or of a whole day's segments merged."""

    # book hash -> (source, rows)
    books: dict[str, tuple[str, tuple[Row, ...]]] = field(default_factory=dict)
    sweeps: list[Sweep] = field(default_factory=list)
    latest_book: dict[str, str] = field(default_factory=dict)  # source -> last hash written
    problem: str | None = None  # why reading stopped early, if it did


_SEGMENT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:\.(\d+))?\.jsonl$")


class Store:
    """Append-only JSONL, one folder per month and one or more segments per UTC day.

    `append` is the only writer. Reading never raises on a damaged segment unless asked to
    (`strict=True`, which is what `tci.run intraday verify` uses); it keeps what verified
    and records the rest in `problems`.
    """

    def __init__(self, root: Path | None = None, max_segment_bytes: int = 8_000_000) -> None:
        self.root = root or STORE_DIR
        self.max_segment_bytes = max_segment_bytes
        self._segments: dict[Path, DayLog] = {}
        self.problems: dict[str, str] = {}  # segment file name -> what is wrong with it

    # --- layout -----------------------------------------------------------------------

    def day_dir(self, day: str) -> Path:
        return self.root / day[:7]

    def path_for(self, day: str, segment: int = 1) -> Path:
        name = f"{day}.jsonl" if segment == 1 else f"{day}.{segment}.jsonl"
        return self.day_dir(day) / name

    def segments(self, day: str) -> list[Path]:
        folder = self.day_dir(day)
        if not folder.exists():
            return []
        found = []
        for p in folder.glob(f"{day}*.jsonl"):
            m = _SEGMENT_RE.match(p.name)
            if m and m.group(1) == day:
                found.append((int(m.group(2) or 1), p))
        return [p for _, p in sorted(found)]

    def days(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted({
            m.group(1) for p in self.root.rglob("*.jsonl")
            if (m := _SEGMENT_RE.match(p.name))
        })

    # --- reading ----------------------------------------------------------------------

    def load_segment(self, path: Path) -> DayLog:
        if path in self._segments:
            return self._segments[path]
        log_ = DayLog()
        if path.exists():
            lines = path.read_text(encoding="utf-8").split("\n")
            for i, line in enumerate(lines):
                if not line.strip():
                    continue
                where = f"{path.name}:{i + 1}"
                try:
                    rec = json.loads(line)
                    self._apply(log_, rec, where)
                except (json.JSONDecodeError, IntradayStoreError, KeyError, TypeError,
                        ValueError) as exc:
                    # A runner killed mid-write leaves a torn final line and nothing after
                    # it; that is the one case that is not a problem, because the sweep
                    # line it belonged to never landed. Anything else stops the read here.
                    torn = isinstance(exc, json.JSONDecodeError) and all(
                        not rest.strip() for rest in lines[i + 1:])
                    log_.problem = f"torn final line {where}" if torn else f"{where}: {exc}"
                    break
            # A file that does not end in a newline has a torn tail even if every complete
            # line verified; appending after it would glue the next line onto the fragment.
            if log_.problem is None and lines and lines[-1] != "":
                log_.problem = f"torn tail of {path.name}: no newline at end of file"
        if log_.problem is not None:
            self.problems[path.name] = log_.problem
            level = logging.INFO if log_.problem.startswith("torn") else logging.ERROR
            log.log(level, "intraday store: %s (reads before it are kept)", log_.problem)
        self._segments[path] = log_
        return log_

    def load(self, day: str, strict: bool = False) -> DayLog:
        """Every verified sweep of a day, across its segments, oldest first."""
        merged = DayLog()
        for path in self.segments(day):
            seg = self.load_segment(path)
            if strict and seg.problem is not None and not seg.problem.startswith("torn"):
                raise IntradayStoreError(seg.problem)
            merged.books.update(seg.books)
            merged.sweeps.extend(seg.sweeps)
            merged.problem = merged.problem or seg.problem
        merged.sweeps.sort(key=lambda sw: sw.at)
        return merged

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
                    dropped=int(s.get("dropped") or 0),
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

    # --- writing ----------------------------------------------------------------------

    @contextmanager
    def lock(self, stale_after: timedelta = timedelta(hours=2)) -> Iterator[None]:
        """One sweep at a time, on any platform. The workflow's concurrency group already
        serialises CI; this covers a manual sweep started beside it. A lock older than
        `stale_after` belonged to a process that died holding it and is taken over."""
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / ".lock"
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
            except FileNotFoundError:
                age = float("inf")
            if age < stale_after.total_seconds():
                raise StoreBusy(f"{path} is held ({age:.0f}s old)") from None
            log.warning("intraday: taking over a stale lock (%.0fs old)", age)
            path.unlink(missing_ok=True)
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, f"{os.getpid()} {_iso(datetime.now(UTC))}\n".encode())
            yield
        finally:
            os.close(fd)
            path.unlink(missing_ok=True)

    def _writable_segment(self, day: str) -> tuple[Path, DayLog]:
        existing = self.segments(day)
        if existing:
            last = existing[-1]
            seg = self.load_segment(last)
            if seg.problem is None and last.stat().st_size < self.max_segment_bytes:
                return last, seg
            reason = seg.problem or f"reached {self.max_segment_bytes} bytes"
            log.warning("intraday: starting a new segment after %s (%s)", last.name, reason)
            m = _SEGMENT_RE.match(last.name)
            number = int(m.group(2) or 1) + 1 if m else len(existing) + 1
        else:
            number = 1
        path = self.path_for(day, number)
        return path, self._segments.setdefault(path, DayLog())

    def append(self, sweep: Sweep, books: dict[str, list[Row]]) -> Path:
        """Write the books this sweep read, then the sweep line that makes them count."""
        day = sweep.started.strftime("%Y-%m-%d")
        path, log_ = self._writable_segment(day)
        lines: list[dict[str, Any]] = []
        for source in sorted(books):
            rows = books[source]
            h = book_hash(rows)
            # An identical book already in this segment is referred to, not written again.
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
                    ("n", s.n), ("error", s.error), ("dropped", s.dropped or None),
                ) if v is not None}
                for name, s in sorted(sweep.sources.items())
            },
        })
        path.parent.mkdir(parents=True, exist_ok=True)
        # One write of the whole batch, in append mode: existing content is never
        # rewritten, and a crash can tear at most the tail of this batch.
        payload = "".join(json.dumps(line, separators=(",", ":")) + "\n" for line in lines)
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        log_.sweeps.append(sweep)
        return path

    @staticmethod
    def _book_line(log_: DayLog, source: str, h: str, rows: list[Row]) -> dict[str, Any]:
        new = Counter(_row_key(r) for r in rows)
        base_hash = log_.latest_book.get(source)
        add: list[str]
        drop: list[str]
        if base_hash is not None and base_hash in log_.books:
            old = Counter(_row_key(r) for r in log_.books[base_hash][1])
            add = sorted((new - old).elements())
            drop = sorted((old - new).elements())
            if len(add) + len(drop) >= len(rows):
                base_hash, add, drop = None, sorted(new.elements()), []
        else:
            base_hash, add, drop = None, sorted(new.elements()), []
        return {
            "v": FORMAT_VERSION, "kind": "book", "source": source, "book": h,
            "base": base_hash, "add": [json.loads(k) for k in add],
            "drop": [json.loads(k) for k in drop],
        }


def restore_log(saved: Path, root: Path | None = None) -> list[Path]:
    """Put a saved copy of the log back over a fresh checkout, refusing to lose a byte.

    The workflow's answer to a rejected push: it copies the log aside, resets to the new
    main, and calls this. This job is the only writer to the log and its files only grow,
    so the checkout's copy of every file must be a prefix of the saved one; anything else
    means someone else wrote to the log, and it is refused rather than overwritten.

    In Python rather than the workflow's shell because the shell version globbed
    `$SAVE/*.jsonl`, which matched nothing once segments moved into month folders: a
    rejected push would have restored no file, and the reset would have thrown the hour's
    reads away without an error.
    """
    root = root or STORE_DIR
    restored: list[Path] = []
    for src in sorted(saved.rglob("*.jsonl")):
        rel = src.relative_to(saved)
        dest = root / rel
        new = src.read_bytes()
        if dest.exists():
            old = dest.read_bytes()
            if not new.startswith(old):
                raise IntradayStoreError(
                    f"{rel}: the checkout's copy is not a prefix of the saved one")
            if old == new:
                continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(new)
        restored.append(dest)
    return restored


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

    Fail-soft: the database is the fixing's, not ours. If it is missing, locked or from a
    schema this code does not know, the intraday path loses the fixing's reads and says so
    in the log; it does not stop.
    """
    wanted = sorted(set(sources))
    if not wanted:
        return []
    marks = ",".join("?" for _ in wanted)
    out = []
    try:
        runs = conn.execute(
            f"SELECT run_id, source, finished_utc FROM runs WHERE status = 'ok'"
            f" AND finished_utc >= ? AND finished_utc <= ? AND source IN ({marks})",
            (_iso(start), _iso(end), *wanted),
        ).fetchall()
        for r in runs:
            obs = conn.execute(
                "SELECT * FROM observations WHERE run_id = ?", (r["run_id"],)
            ).fetchall()
            rows = tuple(sorted((row_of(o) for o in obs), key=_row_key))
            out.append(Read(r["source"], parse_iso(r["finished_utc"]), rows, "fixing"))
    except (sqlite3.Error, ValueError, KeyError, IndexError) as exc:
        log.warning("intraday: the fixing's reads could not be loaded (%s); continuing"
                    " without them", exc)
        return []
    return out


def all_reads(store: Store, conn: sqlite3.Connection | None, cfg: IntradayConfig,
              start: datetime, end: datetime) -> list[Read]:
    reads = [r for r in intraday_reads(store, start, end) if r.source in cfg.sources]
    if conn is not None:
        reads += fixing_reads(conn, start, end, cfg.sources)
    return sorted(reads, key=lambda r: (r.at, r.source, r.origin))


# ==========================================================================
# scheduling: cadence, and backoff for a source that keeps failing
# ==========================================================================


@dataclass(frozen=True)
class SourceState:
    last_attempt: datetime | None
    last_ok: datetime | None
    failures: int  # consecutive, since the last successful read
    last_error: str | None = None

    @property
    def rate_limited(self) -> bool:
        """The last failure was the source saying slow down, not a fault."""
        return bool(self.failures and self.last_error and _RATE_LIMITED.search(self.last_error))


# What a refusal on volume looks like in the error text each transport produces:
# requests' HTTPError ("429 Client Error: Too Many Requests"), the vendored Computable
# transport ("HTTP 429"), or a body that says so in words.
_RATE_LIMITED = re.compile(r"\b429\b|too many requests|rate.?limit|throttl", re.I)


def _attempts(store: Store, conn: sqlite3.Connection | None, cfg: IntradayConfig,
              start: datetime, end: datetime) -> list[tuple[datetime, str, bool, str | None]]:
    """(when, source, ok, error) for every attempt on a configured source, both origins.

    Reads only `runs` from the database, not observations: scheduling needs to know when a
    source was asked and how it answered, not what it said.
    """
    out: list[tuple[datetime, str, bool, str | None]] = []
    for day in _days_between(start, end):
        for sw in store.load(day).sweeps:
            for name, s in sw.sources.items():
                if name in cfg.sources and start <= s.at <= end:
                    out.append((s.at, name, s.status == "ok", s.error))
    if conn is not None:
        wanted = sorted(cfg.sources)
        marks = ",".join("?" for _ in wanted)
        try:
            for r in conn.execute(
                f"SELECT source, status, finished_utc, notes FROM runs"
                f" WHERE status IN ('ok', 'failed') AND finished_utc >= ?"
                f" AND finished_utc <= ? AND source IN ({marks})",
                (_iso(start), _iso(end), *wanted),
            ):
                out.append((parse_iso(r["finished_utc"]), r["source"], r["status"] == "ok",
                            r["notes"] if r["status"] != "ok" else None))
        except (sqlite3.Error, ValueError) as exc:
            log.warning("intraday: the fixing's run log could not be read (%s)", exc)
    return sorted(out, key=lambda a: a[0])


def source_states(store: Store, conn: sqlite3.Connection | None, cfg: IntradayConfig,
                  now: datetime) -> dict[str, SourceState]:
    """Where each source stands: last asked, last answered, and how many misses since.

    The fixing counts. Without it the 12:00 sweep would read vast.ai minutes after a late
    11:00 run did, which is a second request for the same book.
    """
    lookback = timedelta(hours=max(cfg.max_backoff_hours, 24) * 2)
    states: dict[str, SourceState] = {}
    for at, name, ok, error in _attempts(store, conn, cfg, now - lookback, now):
        prev = states.get(name, SourceState(None, None, 0))
        states[name] = (SourceState(at, at, 0) if ok
                        else SourceState(at, prev.last_ok, prev.failures + 1, error))
    return states


def next_due(cfg: IntradayConfig, name: str, state: SourceState | None) -> datetime | None:
    """When a source may next be asked; None means now. The wait doubles per failure."""
    if state is None or state.last_attempt is None:
        return None
    every = cfg.sources[name].every_hours
    if state.rate_limited:
        # A source that has told us to slow down is not asked again by this job for the
        # full backoff cap. The fixing still reads it at 11:00, and that read, if it
        # succeeds, resets the state. The hourly job must never be the reason the fixing's
        # own request is refused.
        return state.last_attempt + timedelta(hours=max(cfg.max_backoff_hours, every))
    # One miss is retried at the normal cadence (a single 502 is not a pattern); from the
    # second in a row the wait doubles.
    doublings = min(max(state.failures - 1, 0), 16)
    hours = min(every * 2 ** doublings, max(cfg.max_backoff_hours, every))
    return (state.last_attempt + timedelta(hours=hours)
            - timedelta(minutes=cfg.due_slack_minutes))


def fixing_guard_holds(cfg: IntradayConfig, conn: sqlite3.Connection | None, now: datetime
                       ) -> set[str]:
    """Sources this job must not ask right now, so as not to crowd the 11:00 fixing.

    Inside the guard window a source is held back until the fixing has asked it today.
    The window runs well past 11:00 because the daily job is scheduled then, not run then:
    GitHub starts scheduled jobs 10 to 30 minutes late on a normal day. Measured on
    26 September 2026, vast.ai allows 30 requests in a window of a few seconds and one
    read takes 9, Azure's retail feed answers with a 60-second retry-after and one read
    takes 91 requests. An hourly read landing in the same minutes as the fixing's is the
    one way this job could get the fixing refused, and this rules it out.

    Without the record, it cannot know what the fixing has done, so inside the window it
    holds everything back.
    """
    if cfg.fixing_guard is None:
        return set()
    day = now.strftime("%Y-%m-%d")
    start, end = (_at(day, t) for t in cfg.fixing_guard)
    if not start <= now < end:
        return set()
    if conn is None:
        return set(cfg.sources)
    wanted = sorted(cfg.sources)
    marks = ",".join("?" for _ in wanted)
    try:
        asked = {r[0] for r in conn.execute(
            f"SELECT DISTINCT source FROM runs WHERE utc_date = ? AND status IN"
            f" ('ok', 'failed') AND source IN ({marks})", (day, *wanted))}
    except sqlite3.Error as exc:
        log.warning("intraday: fixing guard cannot read the run log (%s); holding all", exc)
        return set(cfg.sources)
    return set(cfg.sources) - asked


def due_sources(cfg: IntradayConfig, states: dict[str, SourceState], now: datetime
                ) -> list[str]:
    return [
        name for name in cfg.sources
        if (t := next_due(cfg, name, states.get(name))) is None or now >= t
    ]


def sweep_collectors(names: Iterable[str]) -> list[base.Collector]:
    """Fresh instances of the daily collectors named in the config, in the daily order."""
    from tci.commands import collectors_for_daily

    wanted = set(names)
    return [c for c in collectors_for_daily() if c.name in wanted]


# ==========================================================================
# the sweep: parallel, time-boxed, fail-soft per source
# ==========================================================================


def clean_rows(observations: Iterable[Any]) -> tuple[list[Row], int]:
    """Rows the store can hold, and how many were refused.

    A malformed row (no price, a price that is not a finite number) is dropped and
    counted rather than failing the read: `json` would write NaN as a bare token that
    other readers reject, and one bad row is no reason to lose a source's other rows.
    """
    rows: list[Row] = []
    dropped = 0
    for o in observations:
        try:
            row = row_of(o)
        except (TypeError, ValueError, KeyError, AttributeError):
            dropped += 1
            continue
        if not math.isfinite(row[3]):
            dropped += 1
            continue
        rows.append(row)
    return rows, dropped


def _collect_parallel(
    collectors: Sequence[base.Collector],
    cfg: IntradayConfig,
    now: Callable[[], datetime],
    session_for: Callable[[], Any],
) -> dict[str, tuple[SourceRead, list[Row]]]:
    """Read every collector, `cfg.workers` at a time, each inside its own time limit.

    Daemon threads, deliberately. A thread cannot be killed from outside, so a read that
    overruns is abandoned: its slot is handed to the next source, its result is ignored if
    it ever arrives, and because the thread is a daemon it cannot keep the process alive
    after the sweep has written what it has. concurrent.futures would join every worker at
    exit, so one hung socket would hold the sweep open until the runner was killed, and a
    killed runner commits nothing.
    """
    slots = threading.Semaphore(cfg.workers)
    mu = threading.Lock()
    started: dict[str, float] = {}
    results: dict[str, tuple[SourceRead, list[Row]]] = {}
    abandoned: set[str] = set()

    def work(c: base.Collector) -> None:
        slots.acquire()
        with mu:
            if c.name in abandoned:
                slots.release()
                return
            started[c.name] = time.monotonic()
        try:
            rows, dropped = clean_rows(c.collect(session_for()))
            incomplete = getattr(c, "incomplete", None) or []
            if incomplete:
                # Part of the surface refused (Azure: 9 of 10 regions answered 429 from a
                # GitHub runner on 26 September). Stored as ok, a partial catalog would
                # replace the last complete read and move the replay by composition. As a
                # failure, the last complete read stands within max_age, and the reason
                # (429 included) drives the backoff.
                raise RuntimeError(
                    f"incomplete read, {len(rows)} rows: {'; '.join(incomplete)}"[:280])
            outcome = (SourceRead("ok", now(), book=book_hash(rows), n=len(rows),
                                  dropped=dropped), rows)
            if dropped:
                log.warning("intraday: %s returned %d malformed rows, dropped", c.name,
                            dropped)
        except BaseException as exc:  # noqa: BLE001 - fail-soft, reason recorded
            log.warning("intraday: %s failed (fail-soft, continuing): %s: %s", c.name,
                        type(exc).__name__, exc)
            outcome = (SourceRead("failed", now(),
                                  error=f"{type(exc).__name__}: {exc}"[:300]), [])
        with mu:
            if c.name in abandoned:
                return  # the watchdog already recorded it and freed the slot
            results[c.name] = outcome
            slots.release()

    for c in collectors:
        threading.Thread(target=work, args=(c,), name=f"intraday-{c.name}",
                         daemon=True).start()

    budget_end = time.monotonic() + cfg.sweep_budget_seconds
    while True:
        with mu:
            pending = [c.name for c in collectors
                       if c.name not in results and c.name not in abandoned]
            if not pending:
                break
            clock = time.monotonic()
            for name in pending:
                t0 = started.get(name)
                if t0 is not None and clock - t0 > cfg.source_timeout_seconds:
                    reason = f"timed out after {cfg.source_timeout_seconds:g}s"
                elif clock > budget_end:
                    reason = (f"sweep budget of {cfg.sweep_budget_seconds:g}s spent before"
                              f" it {'finished' if t0 is not None else 'started'}")
                else:
                    continue
                log.warning("intraday: %s abandoned: %s", name, reason)
                abandoned.add(name)
                results[name] = (SourceRead("failed", now(), error=reason), [])
                if t0 is not None:
                    slots.release()  # a waiting source may start in its place
        time.sleep(0.05)
    return results


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

    A source that raises or overruns is recorded as failed with its reason, exactly as the
    daily run records it in `runs.notes`; the others are still read. Nothing is written
    when no source was due, so an idle hour costs neither a request nor a line. Raises
    StoreBusy if another sweep holds the store.
    """
    with store.lock():
        started = now()
        pool = list(collectors) if collectors is not None else sweep_collectors(cfg.sources)
        pool = [c for c in pool if c.name in cfg.sources]
        if only is not None:
            keep = set(only)
            pool = [c for c in pool if c.name in keep]
        if force:
            due = set(cfg.sources)
        else:
            try:
                due = set(due_sources(cfg, source_states(store, conn, cfg, started),
                                      started))
            except Exception:  # noqa: BLE001
                # Not knowing when a source was last read is resolved by reading it: the
                # cost is one extra request, against an hour of nothing.
                log.exception("intraday: scheduling state unreadable; treating all as due")
                due = set(cfg.sources)
        held = fixing_guard_holds(cfg, conn, started)
        if held:
            due -= held
            log.info("intraday: fixing guard holds back %s until the 11:00 run has read"
                     " them", ", ".join(sorted(held & set(cfg.sources))))
        run = [c for c in pool if c.name in due]
        not_due = tuple(sorted(c.name for c in pool if c.name not in due))
        if not run:
            log.info("intraday: nothing due (%s)", ", ".join(not_due) or "no sources")
            return SweepResult(None, None, not_due)

        session_for: Callable[[], Any] = (
            (lambda: session) if session is not None else base.make_session)
        outcomes = _collect_parallel(run, cfg, now, session_for)
        reads = {name: r for name, (r, _rows) in outcomes.items()}
        books = {name: rows for name, (r, rows) in outcomes.items() if r.status == "ok"}
        for name, r in sorted(reads.items()):
            log.info("intraday: %s %s%s", name, r.status,
                     f" {r.n} rows" if r.status == "ok" else f" ({r.error})")
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
            fx = None
            if self.conn is not None:
                try:
                    fx = rate_for(self.conn, date, strictly_before=factors.fx.strictly_before)
                except sqlite3.Error as exc:
                    # Without a rate normalise.py drops EUR-quoted rows rather than guess
                    # one, the same outcome the fixing has on a day with no rate stored.
                    log.warning("intraday: no FX for %s (%s); EUR rows left out", date, exc)
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
    """Every reconstructed point in [start, end], oldest first.

    A point whose calculation raises becomes a gap with its reason, not an exception: one
    bad read must cost one point on the chart, not the chart. The fixing handles a
    source it cannot parse the same way, by publishing without it.
    """
    reads = all_reads(store, conn, cfg, start - cfg.longest_age, end)
    calc = _Calc(conn)
    out = []
    for t, origin in event_times(store, reads, start, end):
        state = state_at(reads, t, cfg)
        try:
            out.append(compute_snapshot(state, t, origin, cfg, calc))
        except Exception as exc:  # noqa: BLE001
            log.exception("intraday: the value at %s could not be computed", _iso(t))
            reason = f"not_computed: {type(exc).__name__}"
            out.append(Snapshot(
                at=t, origin=origin,
                values={series: (None, None, 0, reason) for series in cfg.series},
                ages={s: int((t - r.at).total_seconds() // 60)
                      for s, r in sorted(state.items())},
                missing=tuple(sorted(set(cfg.sources) - set(state))),
            ))
    return out


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
