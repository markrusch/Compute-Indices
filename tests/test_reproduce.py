# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Every stored print recomputes from stored observations; every published digest matches.

These run against the committed database and the committed site files, so a change to
the calculation code that would alter any published print, under any version still in
the succession, fails CI here.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from tci import db, reproduce

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_every_stored_print_reproduces() -> None:
    report = reproduce.reproduce_prints(db.connect())
    assert report.results, "no prints checked"
    assert not report.mismatches, [
        (r.date, r.series, r.detail) for r in report.mismatches[:10]
    ]


def test_every_published_digest_matches_the_database() -> None:
    report = reproduce.check_published(db.connect())
    assert report.results, "no published print files found"
    assert not report.mismatches, [(r.date, r.series, r.detail) for r in report.mismatches[:10]]


def test_a_tampered_print_file_is_caught(tmp_path: Path) -> None:
    src = sorted((REPO_ROOT / "site" / "data" / "prints").glob("*.json"))[-1]
    shutil.copy(src, tmp_path / src.name)
    payload = json.loads((tmp_path / src.name).read_text())
    series = next(iter(payload["series"]))
    payload["series"][series]["digest"] = "sha256:" + "0" * 64
    (tmp_path / src.name).write_text(json.dumps(payload))
    report = reproduce.check_published(db.connect(), prints_dir=tmp_path,
                                       latest_path=tmp_path / "none.json")
    assert any(r.status == "MISMATCH" for r in report.results)


def test_digest_is_stable_across_float_representations() -> None:
    a = {"date": "2026-09-01", "series": "S", "revision": 1, "value_usd": 3.25,
         "value_eur": None, "fx_rate": 1.1, "fx_date": "2026-08-31", "n_sources": 5,
         "n_executable": 1, "flags": "", "methodology_version": "0.3.0-dev"}
    b = dict(a, value_usd=3.2500000000000004)
    assert reproduce.digest(reproduce.canonical_print(a, [])) == reproduce.digest(
        reproduce.canonical_print(b, [])
    )
