# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""MLPerf Training results, joined to TCI prices, for the sellers that submitted any.

L4.2 of the roadmap: turn a price per GPU-hour into a price per unit of delivered work,
using results the vendors published themselves rather than anything TCI measured. The
join is only as good as the overlap, and the overlap is the finding.

**What the overlap turned out to be.** Across MLPerf Training v4.0, v5.0, v5.1 and v6.0,
six of TCI's panel providers have submitted: Azure, CoreWeave, Google, Lambda, Nebius and
Oracle. None of them has ever submitted an H100 system, which is the GPU the headline
prices. On H200, three submitted — Nebius, Oracle and Google — and no two of them ran the
same benchmark, so there is no pair of providers whose cost per unit of work can be
compared. What can be published is one provider's cost for the workload that provider
chose to run, which is a real number and is not a comparison.

That is a negative result, and it is published as one. It also says something the priced
data cannot: the public-results route does not answer the question this layer exists to
ask, so the measured route is not optional.

**Reproducibility.** Nothing here reaches the network at read time. `data/mlperf/
training.json` is a snapshot that pins the upstream commit of each results repository, so
every figure traces to a file at a stated revision and re-running against that revision
gives the same answer. `python -m tci.run mlperf --refresh` rebuilds it and moves the pin.

**Time to train** is `run_stop` minus `run_start` from the submitted MLLOG, over runs whose
`run_stop` carries `status: success`, reported as the median of those runs. That is TCI's
arithmetic over the published logs. It is deliberately not called the official score:
MLCommons publishes official scores itself, the scoring rule varies by benchmark, and a
number computed here should not be mistaken for one ratified there.

**What an MLPerf result is not.** A submitted system is a configuration the vendor tuned
for the benchmark, at a node count it chose, in a region it did not have to name. It is
not what a customer gets by default, and it is a point in time. The price it is multiplied
by is TCI's EU/EEA price for that seller's matching accelerator on the print date, which is
a different thing bought in a different shape. Every one of those gaps is stated on the
page rather than buried.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = REPO_ROOT / "data" / "mlperf" / "training.json"

# The rounds worth carrying. v4.0 is fetched and kept out: of TCI-priced sellers only
# Oracle and Google submitted, and neither on an accelerator TCI has an EU price for.
DEFAULT_ROUNDS = ("training_results_v5.0", "training_results_v5.1", "training_results_v6.0")

# The accelerator TCI prices, per MLPerf accelerator model name. A model that is not in
# this map is a system TCI has no comparable price for, and is reported as such rather
# than matched to something adjacent.
ACCELERATOR_TO_TCI_MODEL = {
    "NVIDIA H200-SXM5-141GB": "H200_SXM",
    "NVIDIA H100-SXM5-80GB": "H100_SXM",
    "NVIDIA Blackwell GPU (B200-SXM-180GB)": "B200_SXM",
    "NVIDIA Blackwell Ultra GPU (B300-SXM-270GB)": "B300_SXM",
}

# MLPerf submitter name -> TCI provider key. Only the sellers TCI prices; a submitter
# absent from this map is a hardware vendor or an OEM rather than somebody selling hours.
SUBMITTER_TO_PROVIDER = {
    "Nebius": "nebius",
    "Oracle": "oci",
    "CoreWeave": "coreweave",
    "Lambda": "lambdalabs",
    "Azure": "azure",
    "Google": "gcp",
    "Google_1": "gcp",
}


@dataclass(frozen=True)
class System:
    repo: str
    commit: str
    submitter: str
    # The system file's stem, which is what the results directory is named after and the
    # only unambiguous key. `system_name` is not one: Oracle files four different node
    # counts under the single name "BM.GPU.GB300.4".
    system_key: str
    system_name: str
    accelerator_model: str
    nodes: int
    accelerators_per_node: int
    status: str
    accelerator_interconnect: str
    host_networking: str

    @property
    def accelerators(self) -> int:
        return self.nodes * self.accelerators_per_node

    @property
    def provider(self) -> str | None:
        return SUBMITTER_TO_PROVIDER.get(self.submitter)

    @property
    def tci_model(self) -> str | None:
        return ACCELERATOR_TO_TCI_MODEL.get(self.accelerator_model)


@dataclass(frozen=True)
class Result:
    repo: str
    commit: str
    submitter: str
    # The system file's stem, for the same reason System carries it: `system_name` is not
    # unique within a submitter, and joining on it merged an 8-GPU run and a 512-GPU run
    # onto whichever system happened to be last in the lookup.
    system_key: str
    benchmark: str
    runs: tuple[float, ...]  # minutes, successful runs only
    logs: tuple[str, ...]    # the paths the runs came from, at `commit`

    @property
    def median_minutes(self) -> float:
        return statistics.median(self.runs)


@dataclass
class Snapshot:
    generated_at: str = ""
    repos: dict[str, str] = field(default_factory=dict)  # repo -> pinned commit sha
    systems: list[System] = field(default_factory=list)
    results: list[Result] = field(default_factory=list)


def time_to_train_minutes(log_text: str) -> float | None:
    """`run_stop` minus `run_start`, for a run the log records as successful.

    A run that did not converge carries a `run_stop` with a status other than success, and
    is not a time to train. Returning None for it keeps a failed run out of the median
    rather than averaging it in as a slow one.
    """
    start: int | None = None
    stop: int | None = None
    for line in log_text.splitlines():
        marker = line.find(":::MLLOG ")
        if marker < 0:
            continue
        try:
            event = json.loads(line[marker + len(":::MLLOG "):])
        except ValueError:
            continue
        key = event.get("key")
        if key == "run_start" and start is None:
            start = event.get("time_ms")
        elif key == "run_stop":
            if (event.get("metadata") or {}).get("status") != "success":
                return None
            stop = event.get("time_ms")
    if start is None or stop is None:
        return None
    return (stop - start) / 1000.0 / 60.0


def load(path: Path | None = None) -> Snapshot:
    raw = json.loads((path or SNAPSHOT).read_text(encoding="utf-8"))
    return Snapshot(
        generated_at=raw["generated_at"],
        repos=raw["repos"],
        systems=[System(**s) for s in raw["systems"]],
        results=[
            Result(
                repo=r["repo"], commit=r["commit"], submitter=r["submitter"],
                system_key=r["system_key"], benchmark=r["benchmark"],
                runs=tuple(r["runs"]), logs=tuple(r["logs"]),
            )
            for r in raw["results"]
        ],
    )


def save(snapshot: Snapshot, path: Path | None = None) -> Path:
    target = path or SNAPSHOT
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "note": "MLPerf Training results for sellers TCI prices, pinned to an upstream "
                "commit per repository. Rebuild: python -m tci.run mlperf --refresh",
        "source": "https://github.com/mlcommons",
        "licence": "MLCommons results are published by MLCommons; see each repository",
        "generated_at": snapshot.generated_at,
        "repos": snapshot.repos,
        "systems": [asdict(s) for s in snapshot.systems],
        "results": [asdict(r) for r in snapshot.results],
    }
    target.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n",
                      encoding="utf-8", newline="\n")
    return target


# ------------------------------------------------------------------ joining to prices

@dataclass(frozen=True)
class Row:
    """One (system, benchmark) with the price TCI holds for that seller's accelerator."""

    system: System
    result: Result
    price_usd_per_gpu_hr: float | None
    price_date: str | None
    price_countries: tuple[str, ...]

    @property
    def gpu_hours(self) -> float:
        return self.result.median_minutes / 60.0 * self.system.accelerators

    @property
    def cost_usd(self) -> float | None:
        if self.price_usd_per_gpu_hr is None:
            return None
        return self.gpu_hours * self.price_usd_per_gpu_hr


def _eu_price(
    conn: sqlite3.Connection, provider: str, model: str, date: str,
    countries: frozenset[str],
) -> tuple[float | None, tuple[str, ...]]:
    """The median EU/EEA price TCI stored for this seller's accelerator on this date.

    The median over that seller's own qualifying rows, not a panel median: the question is
    what this seller charges, not what the market does.
    """
    rows = conn.execute(
        "SELECT o.price_usd_per_gpu_hr p, o.country c FROM observations o"
        " JOIN runs r ON o.run_id = r.run_id"
        " WHERE substr(o.ts_utc, 1, 10) = ? AND o.provider = ? AND o.gpu_model = ?"
        " AND o.tier IN ('executable', 'list') AND o.term = 'on_demand'"
        " AND r.status = 'ok'",
        (date, provider, model),
    ).fetchall()
    priced = [(r["p"], r["c"]) for r in rows if r["p"] and r["c"] in countries]
    if not priced:
        return None, ()
    return statistics.median(p for p, _c in priced), tuple(sorted({c for _p, c in priced}))


def table(
    conn: sqlite3.Connection, date: str, countries: frozenset[str],
    snapshot: Snapshot | None = None,
) -> list[Row]:
    """Every submitted result by a seller TCI prices, with the price where there is one."""
    snap = snapshot or load()
    by_system = {(s.repo, s.system_key): s for s in snap.systems}
    out: list[Row] = []
    for result in snap.results:
        system = by_system.get((result.repo, result.system_key))
        if system is None or system.provider is None:
            continue
        price: float | None = None
        where: tuple[str, ...] = ()
        if system.tci_model:
            price, where = _eu_price(
                conn, system.provider, system.tci_model, date, countries
            )
        out.append(Row(system, result, price, date if price else None, where))
    return sorted(out, key=lambda r: (r.system.submitter, r.result.benchmark,
                                      r.system.accelerators))


def comparable_cells(rows: list[Row]) -> dict[tuple[str, str, str, int], list[Row]]:
    """Cells where two sellers ran the same thing, strictly enough to divide one by the other.

    Same round, same accelerator model, same benchmark, same accelerator count. The round
    has to match: MLPerf revises its benchmark suite between rounds, and a v5.1 time
    against a v6.0 time under the same benchmark name is not the same workload. The
    accelerator count has to match because time to train is not linear in it.

    Loosening any of those produces a much fuller table and a meaningless one.
    """
    cells: dict[tuple[str, str, str, int], list[Row]] = {}
    for row in rows:
        key = (row.system.repo, row.system.accelerator_model, row.result.benchmark,
               row.system.accelerators)
        cells.setdefault(key, []).append(row)
    return {
        k: sorted(v, key=lambda r: r.system.submitter)
        for k, v in sorted(cells.items())
        if len({r.system.submitter for r in v}) > 1
    }


def priced_comparisons(rows: list[Row]) -> dict[tuple[str, str, str, int], list[Row]]:
    """The comparable cells in which every seller also has an EU/EEA price.

    The only cells from which a cost per unit of delivered work can be stated for one
    seller against another. There is presently one.
    """
    return {
        k: v for k, v in comparable_cells(rows).items()
        if all(r.price_usd_per_gpu_hr is not None for r in v)
    }


def missing_submitters(providers: list[str], snapshot: Snapshot | None = None) -> list[str]:
    """Panel providers with no submission at all. They are named, never estimated."""
    snap = snapshot or load()
    submitted = {s.provider for s in snap.systems if s.provider}
    return sorted(p for p in providers if p not in submitted)


def render(rows: list[Row], date: str) -> str:
    if not rows:
        return "no MLPerf submissions by any seller TCI prices"
    lines = [
        f"MLPerf Training results by sellers TCI prices, against the {date} EU/EEA price",
        "time to train is the median of the submitter's own successful runs, computed from",
        "its published logs; it is not MLCommons' official score",
        "",
        f"{'submitter':<11}{'round':<8}{'benchmark':<18}{'GPUs':>6}{'min':>8}"
        f"{'$/GPU-hr':>10}{'run cost':>11}  accelerator",
    ]
    for r in rows:
        price = f"{r.price_usd_per_gpu_hr:.2f}" if r.price_usd_per_gpu_hr else "no price"
        cost = f"${r.cost_usd:,.0f}" if r.cost_usd is not None else "-"
        rnd = r.result.repo.replace("training_results_", "")
        lines.append(
            f"{r.system.submitter:<11}{rnd:<8}{r.result.benchmark:<18}"
            f"{r.system.accelerators:>6}{r.result.median_minutes:>8.1f}"
            f"{price:>10}{cost:>11}  {r.system.accelerator_model[:34]}"
        )
    lines.append("")
    cells = comparable_cells(rows)
    priced = priced_comparisons(rows)
    if not cells:
        lines.append(
            "no cell has two sellers in it, so no cost per unit of work is comparable "
            "between sellers from public results alone"
        )
        return "\n".join(lines)
    lines.append(
        f"{len(cells)} comparable cells (same round, accelerator, benchmark and GPU "
        f"count), of which {len(priced)} has an EU/EEA price for every seller in it:"
    )
    for (repo, acc, bench, n), members in cells.items():
        rnd = repo.replace("training_results_", "")
        lines.append(f"  {rnd} {acc} / {bench} / {n} GPUs")
        for r in members:
            price = f"${r.price_usd_per_gpu_hr:.2f}" if r.price_usd_per_gpu_hr else "no price"
            cost = f"${r.cost_usd:,.0f}" if r.cost_usd is not None else "-"
            lines.append(
                f"    {r.system.submitter:<11}{r.result.median_minutes:>7.1f} min"
                f"{price:>10}/GPU-hr   run {cost}"
            )
    return "\n".join(lines)


def fetch(repos: list[str], panel_submitters: frozenset[str]) -> Snapshot:
    """Rebuild the snapshot from MLCommons. The only function here that uses the network.

    Pins each repository's current commit so every figure downstream names a revision.
    """
    import urllib.request
    from datetime import UTC, datetime

    def _get(url: str) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": _user_agent()})
        with urllib.request.urlopen(request, timeout=60) as response:
            return bytes(response.read())

    snapshot = Snapshot(generated_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
    for repo in repos:
        head = json.loads(_get(
            f"https://api.github.com/repos/mlcommons/{repo}/commits/main"
        ))
        commit = head["sha"]
        snapshot.repos[repo] = commit
        tree = json.loads(_get(
            f"https://api.github.com/repos/mlcommons/{repo}/git/trees/{commit}?recursive=1"
        ))
        paths = [t["path"] for t in tree["tree"] if t["type"] == "blob"]
        raw_base = f"https://raw.githubusercontent.com/mlcommons/{repo}/{commit}/"

        wanted = {p for p in paths
                  if p.split("/")[0] in panel_submitters and "/systems/" in p
                  and p.endswith(".json")}
        systems: dict[str, System] = {}
        for path in sorted(wanted):
            try:
                spec = json.loads(_get(raw_base + path))
            except (ValueError, OSError):
                continue
            system = System(
                repo=repo, commit=commit, submitter=str(spec.get("submitter", "")),
                system_key=Path(path).stem,
                system_name=str(spec.get("system_name", "")),
                accelerator_model=str(spec.get("accelerator_model_name", "")),
                nodes=int(spec.get("number_of_nodes") or 0),
                accelerators_per_node=int(spec.get("accelerators_per_node") or 0),
                status=str(spec.get("status", "")),
                accelerator_interconnect=str(spec.get("accelerator_interconnect", "")),
                host_networking=str(spec.get("host_networking", "")),
            )
            # Only systems whose accelerator TCI has a price shape for are worth the
            # log fetches; the rest are recorded as systems and left without results.
            snapshot.systems.append(system)
            systems[Path(path).stem] = system

        for system in snapshot.systems:
            if system.repo != repo or system.tci_model is None:
                continue
            prefix = f"{system.submitter}/results/"
            logs = [p for p in paths if p.startswith(prefix) and p.endswith(".txt")]
            here = [s for s in snapshot.systems
                    if s.repo == repo and s.submitter == system.submitter]
            by_bench: dict[str, list[tuple[float, str]]] = {}
            for path in logs:
                parts = path.split("/")
                if len(parts) < 5 or not _same_system(parts[2], system):
                    continue
                # Fail closed on an ambiguous directory rather than pick one system.
                if sum(1 for s in here if _same_system(parts[2], s)) != 1:
                    continue
                try:
                    minutes = time_to_train_minutes(_get(raw_base + path).decode(
                        "utf-8", "replace"))
                except OSError:
                    continue
                if minutes is not None:
                    by_bench.setdefault(parts[3], []).append((minutes, path))
            for benchmark, runs in sorted(by_bench.items()):
                snapshot.results.append(Result(
                    repo=repo, commit=commit, submitter=system.submitter,
                    system_key=system.system_key, benchmark=benchmark,
                    runs=tuple(m for m, _p in sorted(runs)),
                    logs=tuple(p for _m, p in sorted(runs)),
                ))
    return snapshot


def _same_system(results_dir: str, system: System) -> bool:
    """Match a results directory to a system entry, on the system file's stem only.

    The first version of this fell back to matching a directory against any system with
    the same node count when the stem did not match. Oracle submits `64xBM.GPU.H200.8`
    and `64x...B200...` in the same round, so that fallback attached an H200 run time to a
    B200 system and multiplied it by a B200 price — three fabricated rows per benchmark,
    each one a plausible-looking number about a named company that nobody had measured.

    The stem is the key. Submitters append a framework tag to the directory
    (`nebius_soperator_8xH200_n128` -> `..._ngc25.01_nemo`) and nothing else, so an exact
    match or the stem followed by an underscore is the whole rule. A directory matching
    no system, or more than one, is skipped rather than assigned to a neighbour.
    """
    return results_dir == system.system_key or results_dir.startswith(system.system_key + "_")


def _user_agent() -> str:
    from tci import USER_AGENT

    return USER_AGENT
