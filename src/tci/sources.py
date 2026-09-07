# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""The source register, the region register, and the coverage report over both.

Neither `config/source_registry.yaml` nor `config/regions.yaml` is read by the
calculation path, so neither is hash-locked and neither can change a print. They exist
to answer two questions that the database alone cannot:

1. What have I already looked at, and why did I say no? Without a written record, a
   source rejected in July gets rediscovered in October and rejected again for the same
   reason nobody wrote down. Worse: a rejection that has since stopped being true
   ("needs an API key") never gets revisited.
2. What am I collecting but not using? The pipeline stores every offer a collector
   returns, including offers from countries no published series covers. Those rows are
   the seed corpus for a future regional index, and they are worthless if nobody knows
   they are accumulating. `coverage()` counts them per region block.

The reporting here is deliberately blunt: counts, dates, and gaps. It makes no judgement
about whether a block is ready to publish; `config/regions.yaml:gate` states the
threshold and the report says whether the numbers clear it.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from tci.config import CONFIG_DIR

# Ordered worst-to-best-known, and the report sorts by this. A frozenset would render
# the register in a different order on different runs, which is no way to read a diff.
STATUS_ORDER = ("live", "built", "candidate", "watchlist", "rejected", "retired")
SOURCE_STATUSES = frozenset(STATUS_ORDER)
BLOCK_STATUSES = frozenset({"published", "shadow", "watch", "excluded"})
TIERS = frozenset({"executable", "list", "reference"})

# Statuses whose `last_reviewed` date the review clock applies to. A rejected row is not
# on a clock -- it is re-examined by the discovery sweep, which reads `recheck`.
REVIEWED_STATUSES = frozenset({"live", "built", "candidate"})


@dataclass(frozen=True)
class RegionBlock:
    key: str
    label: str
    status: str
    countries: frozenset[str]
    series: str | None = None
    reason: str | None = None
    notes: str = ""

    @property
    def collectable(self) -> bool:
        """Whether an observation from this block may be stored at all."""
        return self.status in ("published", "shadow", "watch")


@dataclass(frozen=True)
class Regions:
    blocks: dict[str, RegionBlock]
    gate: dict[str, int]

    def block_of(self, country: str | None) -> str | None:
        if not country:
            return None
        for key, block in self.blocks.items():
            if country in block.countries:
                return key
        return None

    @property
    def countries(self) -> frozenset[str]:
        return frozenset().union(*(b.countries for b in self.blocks.values()))

    @property
    def excluded_countries(self) -> frozenset[str]:
        return frozenset().union(
            *(b.countries for b in self.blocks.values() if b.status == "excluded"),
            frozenset(),
        )


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    kind: str
    status: str
    tier: str
    blocks: tuple[str, ...]
    gpu_classes: tuple[str, ...]
    homepage: str = ""
    endpoint: str = ""
    auth: str = "none"
    collector: str | None = None
    access_basis: str = ""
    discovered: str | None = None
    discovered_via: str = ""
    last_reviewed: str | None = None
    reason: str | None = None
    recheck: str | None = None
    notes: str = ""

    def review_age_days(self, today: date) -> int | None:
        if not self.last_reviewed:
            return None
        return (today - datetime.strptime(self.last_reviewed, "%Y-%m-%d").date()).days


@dataclass(frozen=True)
class Registry:
    sources: tuple[Source, ...]
    review_interval_days: int
    scans: tuple[dict[str, Any], ...]

    def get(self, source_id: str) -> Source | None:
        return next((s for s in self.sources if s.id == source_id), None)

    def with_status(self, *statuses: str) -> tuple[Source, ...]:
        return tuple(s for s in self.sources if s.status in statuses)

    def serving(self, block: str) -> tuple[Source, ...]:
        return tuple(s for s in self.sources if block in s.blocks)

    def due_for_review(self, today: date) -> tuple[Source, ...]:
        out = []
        for s in self.sources:
            if s.status not in REVIEWED_STATUSES:
                continue
            age = s.review_age_days(today)
            if age is None or age > self.review_interval_days:
                out.append(s)
        return tuple(out)

    @property
    def last_scan(self) -> str | None:
        dates = [str(s.get("date")) for s in self.scans if s.get("date")]
        return max(dates) if dates else None


def load_regions(config_dir: Path | None = None) -> Regions:
    cfg = config_dir or CONFIG_DIR
    raw = yaml.safe_load((cfg / "regions.yaml").read_text(encoding="utf-8"))
    blocks = {}
    for key, b in raw["blocks"].items():
        for country in b["countries"]:
            # YAML 1.1 reads a bare NO as boolean false. Coercing it with str() would turn
            # Norway into the string 'FALSE' and hide the problem one level deeper, so the
            # loader refuses instead. factors.yaml carries exactly this bug today.
            if not isinstance(country, str):
                raise ValueError(
                    f"regions.yaml {key}: country {country!r} is not a string. Quote it "
                    "-- YAML reads bare NO as a boolean."
                )
        blocks[key] = RegionBlock(
            key=key,
            label=str(b["label"]),
            status=str(b["status"]),
            countries=frozenset(c.upper() for c in b["countries"]),
            series=(str(b["series"]) if b.get("series") else None),
            reason=(str(b["reason"]) if b.get("reason") else None),
            notes=str(b.get("notes") or ""),
        )
    return Regions(blocks=blocks, gate={k: int(v) for k, v in (raw.get("gate") or {}).items()})


def load_registry(config_dir: Path | None = None) -> Registry:
    cfg = config_dir or CONFIG_DIR
    raw = yaml.safe_load((cfg / "source_registry.yaml").read_text(encoding="utf-8"))
    known = set(Source.__dataclass_fields__)
    sources = []
    for entry in raw["sources"]:
        kwargs = {k: v for k, v in entry.items() if k in known}
        kwargs["blocks"] = tuple(kwargs.get("blocks") or ())
        kwargs["gpu_classes"] = tuple(kwargs.get("gpu_classes") or ())
        for datefield in ("discovered", "last_reviewed"):
            if kwargs.get(datefield) is not None:
                kwargs[datefield] = str(kwargs[datefield])
        sources.append(Source(**kwargs))
    return Registry(
        sources=tuple(sources),
        review_interval_days=int(raw.get("review_interval_days", 90)),
        scans=tuple(raw.get("scans") or ()),
    )


# ---- coverage over what has actually been stored ---------------------------


@dataclass(frozen=True)
class BlockCoverage:
    """What the database holds for one region block. Counts, not judgement."""

    block: str
    label: str
    status: str
    observations: int
    providers: int
    collection_days: int
    countries: tuple[str, ...]
    first_seen: str | None
    last_seen: str | None

    def clears(self, gate: dict[str, int]) -> bool:
        return (
            self.providers >= gate.get("min_providers", 0)
            and self.observations >= gate.get("min_offers", 0)
            and self.collection_days >= gate.get("min_collection_days", 0)
        )


def coverage(
    conn: sqlite3.Connection, regions: Regions, since: str | None = None
) -> list[BlockCoverage]:
    """Per-block observation counts. `since` is an inclusive YYYY-MM-DD lower bound."""
    sql = (
        "SELECT country, provider, substr(ts_utc, 1, 10) AS day, COUNT(*) AS n "
        "FROM observations WHERE country IS NOT NULL "
    )
    params: list[str] = []
    if since:
        sql += "AND substr(ts_utc, 1, 10) >= ? "
        params.append(since)
    sql += "GROUP BY 1, 2, 3"

    obs: dict[str, int] = defaultdict(int)
    provs: dict[str, set[str]] = defaultdict(set)
    days: dict[str, set[str]] = defaultdict(set)
    countries: dict[str, set[str]] = defaultdict(set)
    for row in conn.execute(sql, params):
        block_key = regions.block_of(row["country"])
        if block_key is None:
            continue
        obs[block_key] += int(row["n"])
        provs[block_key].add(row["provider"])
        days[block_key].add(row["day"])
        countries[block_key].add(row["country"])

    out = []
    for key, block in regions.blocks.items():
        seen = sorted(days[key])
        out.append(
            BlockCoverage(
                block=key,
                label=block.label,
                status=block.status,
                observations=obs[key],
                providers=len(provs[key]),
                collection_days=len(seen),
                countries=tuple(sorted(countries[key])),
                first_seen=seen[0] if seen else None,
                last_seen=seen[-1] if seen else None,
            )
        )
    return sorted(out, key=lambda c: (-c.observations, c.block))


def unmapped_locations(conn: sqlite3.Connection, limit: int = 40) -> list[tuple[str, str, int]]:
    """Stored rows whose country never resolved: (provider, raw location, count).

    These are prices the pipeline paid a request for, stored, and then made unusable,
    because the collector's region table had no entry for the location. They are the
    cheapest source of new coverage in the whole project: the data is already arriving.
    """
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for row in conn.execute(
        "SELECT provider, region, raw_json FROM observations WHERE country IS NULL"
    ):
        loc = row["region"]
        if not loc:
            try:
                loc = (json.loads(row["raw_json"]) or {}).get("location")
            except (json.JSONDecodeError, TypeError):
                loc = None
        counts[(row["provider"], str(loc or "(unknown)"))] += 1
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])[:limit]
    return [(p, loc, n) for (p, loc), n in ranked]


def unclassified_providers(
    conn: sqlite3.Connection, since: str | None = None
) -> list[tuple[str, int]]:
    """Provider names in the database that factors.yaml has never classified.

    This is the automatic half of new-entrant detection, and the only half that works
    while nobody is looking. A marketplace adds a host, a catalog adds a vendor, and the
    name arrives in `observations` with no segment assigned. `Factors.segment_of` then
    defaults it to 'neocloud', which puts it straight into the headline population -- the
    deliberately safe default, but only safe if someone eventually reads the name.
    """
    from tci.config import load_factors

    known = set(load_factors().segments)
    sql = "SELECT provider, COUNT(*) AS n FROM observations "
    params: list[str] = []
    if since:
        sql += "WHERE substr(ts_utc, 1, 10) >= ? "
        params.append(since)
    sql += "GROUP BY provider ORDER BY n DESC"
    return [
        (row["provider"], int(row["n"]))
        for row in conn.execute(sql, params)
        if row["provider"] not in known
    ]


def silent_live_sources(
    conn: sqlite3.Connection, registry: Registry, today: date, tolerance_days: int = 3
) -> list[tuple[str, str | None]]:
    """Live sources with no successful run recently: (source id, last ok date or None).

    A collector that quietly stopped returning rows looks exactly like a market with
    nothing to say. This is the check that tells the two apart.
    """
    cutoff = (today - timedelta(days=tolerance_days)).isoformat()
    out = []
    for source in registry.with_status("live"):
        name = source.collector or source.id
        if name == "fx":
            # collect_fx() upserts straight into the fx table and never writes a runs row,
            # so asking `runs` about it reports a healthy feed as permanently silent.
            row = conn.execute("SELECT MAX(date) AS last_ok FROM fx").fetchone()
        else:
            row = conn.execute(
                "SELECT MAX(utc_date) AS last_ok FROM runs WHERE source = ? AND status = 'ok'",
                (name,),
            ).fetchone()
        last_ok = row["last_ok"] if row else None
        if last_ok is None or last_ok < cutoff:
            out.append((source.id, last_ok))
    return out


# ---- the report ------------------------------------------------------------


def _row(cells: list[str], widths: list[int]) -> str:
    return "  ".join(c.ljust(w) for c, w in zip(cells, widths, strict=False)).rstrip()


def _gate_note(cov: BlockCoverage, gate: dict[str, int]) -> str:
    if cov.observations == 0:
        return "no data"
    missing = []
    if cov.providers < gate.get("min_providers", 0):
        missing.append(f"{cov.providers}/{gate['min_providers']} providers")
    if cov.collection_days < gate.get("min_collection_days", 0):
        missing.append(f"{cov.collection_days}/{gate['min_collection_days']} days")
    return "clears gate" if not missing else "needs " + ", ".join(missing)


def render_report(
    conn: sqlite3.Connection,
    registry: Registry,
    regions: Regions,
    today: date,
    status: str | None = None,
    block: str | None = None,
) -> str:
    """The whole register in one screen: sources, review clock, block coverage, waste."""
    lines: list[str] = [f"TCI source register  {today.isoformat()}"]
    last = registry.last_scan
    if last:
        scan_age = (today - datetime.strptime(last, "%Y-%m-%d").date()).days
        lines.append(f"last discovery sweep: {last} ({scan_age}d ago)")
    else:
        lines.append("last discovery sweep: never")

    shown = registry.sources
    if status:
        shown = tuple(s for s in shown if s.status == status)
    if block:
        shown = tuple(s for s in shown if block in s.blocks)

    lines += ["", "SOURCES", _row(["id", "status", "tier", "kind", "blocks"],
                                  [22, 10, 11, 12, 40])]
    for s in sorted(shown, key=lambda s: (STATUS_ORDER.index(s.status), s.id)):
        blocks = ", ".join(s.blocks) if s.blocks else "-"
        lines.append(_row([s.id, s.status, s.tier, s.kind, blocks[:40]],
                          [22, 10, 11, 12, 40]))

    due = registry.due_for_review(today)
    lines += ["", f"REVIEW DUE  (interval {registry.review_interval_days}d)"]
    if not due:
        lines.append("  nothing due")
    for s in due:
        age = s.review_age_days(today)
        lines.append(f"  {s.id:<22} {'never reviewed' if age is None else str(age) + 'd ago'}")

    lines += ["", "REGION BLOCKS", _row(
        ["block", "status", "obs", "prov", "days", "countries", "gate"],
        [16, 10, 8, 5, 5, 26, 28])]
    for cov in coverage(conn, regions):
        lines.append(_row(
            [cov.block, cov.status, f"{cov.observations:,}", str(cov.providers),
             str(cov.collection_days), " ".join(cov.countries)[:26] or "-",
             _gate_note(cov, regions.gate)],
            [16, 10, 8, 5, 5, 26, 28]))

    silent = silent_live_sources(conn, registry, today)
    if silent:
        lines += ["", "SILENT LIVE SOURCES  (no ok run in 3 days)"]
        for source_id, last_ok in silent:
            lines.append(f"  {source_id:<22} last ok: {last_ok or 'never'}")

    newcomers = unclassified_providers(conn)
    if newcomers:
        lines += ["", "UNCLASSIFIED PROVIDERS  (defaulting to segment 'neocloud')"]
        for provider, n in newcomers:
            lines.append(f"  {provider:<22} {n:>6,} observations")

    waste = unmapped_locations(conn)
    if waste:
        total = sum(n for _, _, n in waste)
        lines += ["", f"UNMAPPED LOCATIONS  ({total:,} stored rows with no country)"]
        for provider, loc, n in waste[:15]:
            lines.append(f"  {provider:<8} {loc:<28} {n:>6,}")
        if len(waste) > 15:
            lines.append(f"  ... and {len(waste) - 15} more locations")

    return "\n".join(lines)
