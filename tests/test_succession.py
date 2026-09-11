# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Methodology succession: which version computes a print is decided by its date."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

from tci import methodology
from tci.config import CONFIG_DIR, load_factors, load_succession, version_for

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_succession_is_ordered_and_ends_at_the_head() -> None:
    succ = load_succession()
    dates = [date.fromisoformat(e.effective_from) for e in succ]
    assert dates == sorted(dates) and len(set(dates)) == len(dates)
    assert succ[-1].params_dir == CONFIG_DIR, "the newest version must be the head (config/)"
    assert succ[-1].version == load_factors().methodology_version
    for entry in succ[:-1]:
        assert entry.params_dir != CONFIG_DIR and entry.params_dir.is_dir(), entry.version
        assert load_factors(entry.params_dir).methodology_version == entry.version


def test_no_version_takes_effect_before_the_day_after_its_notice() -> None:
    """GOVERNANCE.md §1: one publication's notice before the first print under it."""
    notices = {
        n["id"]: n
        for n in yaml.safe_load((REPO_ROOT / "config" / "notices.yaml").read_text())["notices"]
    }
    for entry in load_succession():
        if entry.notice is None:
            continue
        notice = notices[entry.notice]
        announced = date.fromisoformat(str(notice["announced"]))
        assert date.fromisoformat(entry.effective_from) >= announced + timedelta(days=1)
        assert str(notice["effective"]) == entry.effective_from
        assert str(notice["version"]) == entry.version


@pytest.mark.parametrize(
    ("day", "version"),
    [("2026-07-18", "0.3.0-dev"), ("2026-09-14", "0.3.0-dev"), ("2026-09-15", "0.4.0"),
     ("2026-09-21", "0.4.0"), ("2026-09-22", "0.5.0"), ("2026-09-30", "0.5.0"),
     ("2026-10-01", "0.6.0")],
)
def test_the_version_live_on_a_date(day: str, version: str) -> None:
    assert version_for(day).version == version
    assert load_factors(for_date=day).methodology_version == version


def test_nothing_is_in_effect_before_the_first_version() -> None:
    with pytest.raises(ValueError):
        version_for("2026-07-17")


def test_frozen_snapshots_match_the_lock() -> None:
    lock = methodology.read_lock()
    assert lock is not None
    recorded = {e["version"]: e["params_hash"] for e in lock["succession"]}
    for entry in methodology.succession_record():
        assert recorded.get(entry["version"]) == entry["params_hash"], (
            f"parameters of {entry['version']} differ from the lock"
        )


def test_the_lock_refuses_an_edit_to_a_frozen_snapshot(tmp_path: Path) -> None:
    import shutil

    root = tmp_path
    shutil.copytree(REPO_ROOT / "config", root / "config")
    (root / "src" / "tci").mkdir(parents=True)
    for rel in methodology.HASHED_FILES:
        if rel.startswith("src/"):
            shutil.copy(REPO_ROOT / rel, root / rel)
    shutil.copy(REPO_ROOT / "METHODOLOGY.lock", root / "METHODOLOGY.lock")
    methodology.update_lock(root)  # baseline, unchanged
    frozen = root / "config" / "methodology" / "0.3.0-dev" / "factors.yaml"
    frozen.write_text(frozen.read_text().replace("min_providers: 5", "min_providers: 3"))
    with pytest.raises(SystemExit, match="frozen parameters"):
        methodology.update_lock(root)
