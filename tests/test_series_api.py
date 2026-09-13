# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""site/data/v1 — the versioned read interface, and the recipe the Data page documents.

L3.5's acceptance line is that a stranger can pull the headline for a date range with
curl and verify a digest using only what the Data page documents. These tests are that
sentence, executed: the range comes out of one file, a gap survives it as a null rather
than a missing row, and the digest recomputes with nothing but the standard library.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tci import db, reproduce
from tci.outputs import webdata

REPO_ROOT = Path(__file__).resolve().parents[1]
API = REPO_ROOT / "site" / "data" / "v1"


def _recipe_digest(entry: dict) -> str:
    """Exactly what the Data page tells a reader to run.

    `jq -jcS 'del(.digest)' | sha256sum` is compact, key-sorted, no trailing newline. Its
    output matches this only while every published value is ASCII — jq emits UTF-8 and
    does not escape, so a non-ASCII provider name would silently break the documented
    command while the published digest stayed correct. `test_published_content_is_ascii`
    is what stops that shipping.
    """
    body = {k: v for k, v in entry.items() if k != "digest"}
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(blob.encode("ascii")).hexdigest()


def test_the_documented_recipe_reproduces_every_published_digest() -> None:
    checked = 0
    for path in sorted((REPO_ROOT / "site" / "data" / "prints").glob("????-??-??.json")):
        for entry in json.loads(path.read_text(encoding="utf-8"))["series"].values():
            assert _recipe_digest(entry) == entry["digest"], path.name
            checked += 1
    assert checked > 100, "too few prints checked for this to mean anything"


def test_published_content_is_ascii() -> None:
    """Non-ASCII would break the documented jq command without breaking the digest."""
    for path in sorted((REPO_ROOT / "site" / "data" / "prints").glob("????-??-??.json")):
        path.read_text(encoding="utf-8").encode("ascii")


def test_a_date_range_comes_out_of_one_file_with_its_gaps_intact() -> None:
    payload = json.loads((API / "series" / "EU-CRI-H100.json").read_text(encoding="utf-8"))
    window = [p for p in payload["points"] if "2026-09-05" <= p["date"] <= "2026-09-12"]
    assert len(window) >= 5
    # A session that did not print is a row with a null value and a reason, never an
    # absent row: a consumer must not be able to interpolate over a gap without seeing it.
    gaps = [p for p in window if p["value_usd"] is None]
    assert gaps, "the window used here is expected to contain gaps"
    assert all(p["flags"] for p in gaps), "a gap has to carry its reason"


def test_the_series_files_agree_with_the_print_files() -> None:
    for path in sorted((API / "series").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for point in payload["points"]:
            pf = REPO_ROOT / "site" / "data" / "prints" / f"{point['date']}.json"
            published = json.loads(pf.read_text(encoding="utf-8"))["series"]
            assert point["digest"] == published[payload["series"]]["digest"], (
                f"{path.name} {point['date']}: two surfaces, two digests"
            )


def test_the_catalogue_names_every_series_file() -> None:
    index = json.loads((API / "index.json").read_text(encoding="utf-8"))
    assert index["schema_version"] == webdata.API_VERSION
    for entry in index["series"]:
        assert (API / entry["href"]).exists(), entry["href"]
    on_disk = {p.stem for p in (API / "series").glob("*.json")}
    assert {e["series"] for e in index["series"]} == on_disk


def test_a_tampered_series_file_fails_the_published_check(tmp_path: Path) -> None:
    """The v1 files republish digests, so they get checked rather than trusted."""
    src = sorted((API / "series").glob("*.json"))[0]
    payload = json.loads(src.read_text(encoding="utf-8"))
    payload["points"][0]["digest"] = "sha256:" + "0" * 64
    (tmp_path / src.name).write_text(json.dumps(payload))

    report = reproduce.check_published(
        db.connect(), prints_dir=tmp_path / "none", latest_path=tmp_path / "none.json",
        api_series_dir=tmp_path,
    )
    assert any(r.status == "MISMATCH" for r in report.results)


def test_writing_the_api_is_deterministic(tmp_path: Path) -> None:
    """The daily run rewrites every file; identical content must give an identical byte
    stream or every session commits a diff of noise."""
    conn = db.connect()
    first = {p.name: p.read_bytes() for p in webdata.write_series_api(conn, tmp_path / "a")}
    second = {p.name: p.read_bytes() for p in webdata.write_series_api(conn, tmp_path / "b")}
    # index.json carries generated_at and is expected to differ.
    del first["index.json"], second["index.json"]
    assert first == second
