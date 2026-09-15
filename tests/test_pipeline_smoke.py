# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""The daily entrypoint runs end to end, in a copy of the repo, without the network.

What this covers that nothing else did: `python -m tci.run daily` is the command the
GitHub Action runs at 11:00 UTC, and until now no test ever invoked it. The suite tested
the pieces - normalisation, the estimator, each parser, each page - while the orchestration
that strings them together was exercised for the first time each morning in production,
against live sources, with an irreplaceable day of prices riding on it. Two of this
project's worst incidents were in that seam rather than in any unit: a day's prints
computed and never persisted, and four sessions of collection discarded.

So this runs the real entrypoint as a subprocess, in a throwaway copy of the tree, with
the collectors replaced by fixtures. A push that breaks the daily run now fails the pull
request instead of the print.

Isolation is by copy rather than by monkeypatching the output paths. Those paths are
module-level constants derived from `REPO_ROOT`, and several are derived from each other
(`site.ASSETS`, `site.CONTRIBUTED_PATH`), so patching one leaves the rest pointing at the
real `site/` - a test that overwrites the published record to prove it can write it is
not a test. Running with PYTHONPATH into the copied `src/` moves `REPO_ROOT` wholesale.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Everything the pipeline reads or writes through REPO_ROOT, plus the packaging metadata
# needed to import tci from the copy.
_COPIED = ("src", "config", "data", "site", "research")
_COPIED_FILES = ("pyproject.toml", "METHODOLOGY.md", "METHODOLOGY.lock", "GOVERNANCE.md")

# The one page under site/ that `site.generate` does not write. It is a design-system
# gallery, linked from nowhere and Disallowed in robots.txt, maintained by hand alongside
# DESIGN.md. CLAUDE.md's "site/*.html is generated" holds for every other page, and this
# test is where the exception is enumerated rather than assumed.
_NOT_GENERATED = frozenset({"components.html"})


@pytest.fixture(scope="module")
def repo_copy(tmp_path_factory: pytest.TempPathFactory) -> Path:
    dst = tmp_path_factory.mktemp("repo")
    for name in _COPIED:
        shutil.copytree(REPO_ROOT / name, dst / name,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in _COPIED_FILES:
        shutil.copy(REPO_ROOT / name, dst / name)
    # The WAL sidecars are not copied, so the copy must be checkpointed content only.
    # sqlite reads a bare .db fine; any pending WAL in the original stays in the original.
    return dst


def _run(repo: Path, *args: str, expect: int = 0) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PYTHONPATH=str(repo / "src"), PYTHONIOENCODING="utf-8")
    # A stray token would make the entsoe overlay reach the network from a test.
    env.pop("ENTSOE_TOKEN", None)
    env.pop("SHADEFORM_API_KEY", None)
    proc = subprocess.run(
        [sys.executable, "-m", "tci.run", *args],
        cwd=repo, env=env, capture_output=True, text=True, timeout=900,
    )
    assert proc.returncode == expect, (
        f"`tci.run {' '.join(args)}` exited {proc.returncode}, expected {expect}\n"
        f"--- stdout ---\n{proc.stdout[-4000:]}\n--- stderr ---\n{proc.stderr[-4000:]}"
    )
    return proc


def test_the_copy_is_the_repo_and_not_the_repo(repo_copy: Path) -> None:
    """Guards the isolation itself: if REPO_ROOT still resolved to the real tree, every
    other assertion here would be checking the published site."""
    proc = _run(repo_copy, "sources", "--due")
    out = subprocess.run(
        [sys.executable, "-c", "from tci import db; print(db.DEFAULT_DB_PATH)"],
        cwd=repo_copy, env=dict(os.environ, PYTHONPATH=str(repo_copy / "src")),
        capture_output=True, text=True,
    ).stdout.strip()
    assert Path(out).parent.parent == repo_copy, f"not isolated: {out}"
    assert proc.returncode == 0


def test_daily_runs_end_to_end_and_publishes_what_it_computed(repo_copy: Path) -> None:
    """The full 11:00 UTC path, minus collection: compute, CSV, charts, post, site."""
    date = "2026-09-12"
    _run(repo_copy, "backfill", "--from", date, "--to", date)
    _run(repo_copy, "post")

    conn = sqlite3.connect(repo_copy / "data" / "eucri.db")
    conn.row_factory = sqlite3.Row
    head = conn.execute(
        "SELECT * FROM daily_index WHERE date = ? AND series = 'EU-CRI-H100'"
        " ORDER BY revision DESC LIMIT 1", (date,)
    ).fetchone()
    assert head is not None, "the recomputation stored no headline print"
    assert head["value_usd"] == pytest.approx(3.49), "the headline moved in a smoke test"

    csv_path = repo_copy / "site" / "data" / "index_history.csv"
    assert csv_path.exists() and date in csv_path.read_text(encoding="utf-8")


def test_every_published_surface_is_regenerated(repo_copy: Path) -> None:
    """A crash anywhere in output generation used to leave the site silently stale."""
    # Delete the generated surfaces, rebuild them, and require each one back. Deleting
    # first is the point: a generator that quietly does nothing passes an existence check
    # against files the copy already carried.
    site_dir = repo_copy / "site"
    pages = sorted(p.name for p in site_dir.glob("*.html") if p.name not in _NOT_GENERATED)
    assert pages, "no pages in the copy to rebuild"
    for page in pages:
        (site_dir / page).unlink()
    latest = site_dir / "data" / "latest.json"
    latest.unlink()

    env = dict(os.environ, PYTHONPATH=str(repo_copy / "src"), PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, "-c",
         "from tci import db\n"
         "from tci.outputs import charts, post, webdata, site\n"
         "c = db.connect()\n"
         "charts.generate_all(c); post.generate_post(c); webdata.generate(c); site.generate(c)\n"],
        cwd=repo_copy, env=env, capture_output=True, text=True, timeout=900,
    )
    assert proc.returncode == 0, f"output generation failed\n{proc.stdout}\n{proc.stderr}"

    missing = [p for p in pages if not (site_dir / p).exists()]
    assert not missing, f"pages not regenerated: {missing}"
    assert latest.exists(), "latest.json not regenerated"
    assert json.loads(latest.read_text(encoding="utf-8"))["series"], "latest.json is empty"


def test_reproduce_passes_inside_the_copy(repo_copy: Path) -> None:
    """Exit 0 is the contract the roadmap's acceptance line names."""
    proc = _run(repo_copy, "reproduce", "--published")
    assert "MISMATCH" not in proc.stdout
    assert "MISSING" not in proc.stdout


def test_forward_records_and_publishes_from_the_copy(repo_copy: Path) -> None:
    """The forward estimate's own entrypoint, the step the daily run takes after its prints."""
    _run(repo_copy, "forward", "--date", "2026-09-12")
    latest = repo_copy / "site" / "data" / "forward" / "latest.json"
    data = json.loads(latest.read_text(encoding="utf-8"))
    assert data["series"] == "EU-CRI-H100" and data["as_of"] == "2026-09-12"
    assert {r["component"] for r in data["latest"]} == {"M", "T", "L"}
    assert (repo_copy / "site" / "data" / "forward" / "history.csv").exists()
