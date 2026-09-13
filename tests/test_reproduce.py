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


def test_a_print_the_site_never_published_is_caught(tmp_path: Path) -> None:
    """The failure this guards: output generation crashes, the run still exits 0.

    `cmd_daily` stores the print and then calls `_maybe_outputs`, which logs any
    exception and returns. The database then holds a date that `site/data/prints/`
    does not, and walking the files alone can never see it — the missing file simply
    isn't iterated, and `latest.json` still matches the older print it names.
    """
    published = sorted((REPO_ROOT / "site" / "data" / "prints").glob("????-??-??.json"))
    for src in published[:-1]:
        shutil.copy(src, tmp_path / src.name)
    withheld = published[-1].stem  # the newest date never reaches the site

    report = reproduce.check_published(db.connect(), prints_dir=tmp_path,
                                       latest_path=tmp_path / "none.json")
    missing = [r for r in report.results if r.status == "MISSING"]
    assert missing, "a stored print with no published file has to be reported"
    assert {r.date for r in missing} == {withheld}
    assert report.mismatches, "MISSING has to fail the check, not just annotate it"


def test_a_series_dropped_from_an_otherwise_current_file_is_caught(tmp_path: Path) -> None:
    """A whole file is the loud version. One series quietly absent is the quiet one."""
    for src in (REPO_ROOT / "site" / "data" / "prints").glob("????-??-??.json"):
        shutil.copy(src, tmp_path / src.name)
    newest = sorted(tmp_path.glob("????-??-??.json"))[-1]
    payload = json.loads(newest.read_text())
    dropped = sorted(payload["series"])[0]
    del payload["series"][dropped]
    newest.write_text(json.dumps(payload))

    report = reproduce.check_published(db.connect(), prints_dir=tmp_path,
                                       latest_path=tmp_path / "none.json")
    assert [(r.date, r.series) for r in report.results if r.status == "MISSING"] == [
        (newest.stem, dropped)
    ]


def test_a_failed_site_build_fails_the_daily_run(monkeypatch, conn) -> None:
    """Green build, stale site, clean digest check — the combination this rules out."""
    from tci import commands
    from tci.outputs import charts

    monkeypatch.setattr(charts, "generate_all", _raise_disk_full)

    assert commands._maybe_outputs(conn, "2026-09-12") is False, (
        "a crash in output generation has to be reported to the caller"
    )
    note = conn.execute(
        "SELECT notes FROM runs WHERE source = 'outputs' AND utc_date = '2026-09-12'"
    ).fetchone()
    assert note is not None and "OSError" in note["notes"]


def test_a_clean_site_build_leaves_the_run_green(monkeypatch, conn) -> None:
    from tci import commands
    from tci.outputs import charts, post, site, webdata

    for mod, fn in ((charts, "generate_all"), (post, "generate_post"),
                    (webdata, "generate"), (site, "generate")):
        monkeypatch.setattr(mod, fn, lambda *a, **k: None)

    assert commands._maybe_outputs(conn, "2026-09-12") is True
    assert conn.execute("SELECT COUNT(*) FROM runs WHERE source = 'outputs'").fetchone()[0] == 0


def _raise_disk_full(*_a, **_k):
    raise OSError("no space left on device")
