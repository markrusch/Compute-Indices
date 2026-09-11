# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Contributed term prices (tci.contrib): validation, privacy, suppression, corrections."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tci import contrib

HEADER = ",".join(contrib.FIELDS)


def _csv(tmp_path: Path, *rows: str, name: str = "c.csv") -> Path:
    p = tmp_path / name
    p.write_text(HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return p


def _line(price: float = 2.4, gpus: int = 64, kind: str = "firm_quote", tenor: int = 12,
          notes: str = "") -> str:
    return f"2026-09-30,seller,{kind},H100_SXM,{gpus},{tenor},{price},USD,EU_EEA,,monthly,{notes}"


@pytest.fixture()
def store(tmp_path: Path) -> sqlite3.Connection:
    return contrib.connect_private(tmp_path / "private")


def _ingest(store: sqlite3.Connection, tmp_path: Path, who: str, *lines: str) -> None:
    rows, digest = contrib.read_file(_csv(tmp_path, *lines, name=f"{who}.csv"))
    contrib.ingest(store, who, rows, digest, "2026-09-30T00:00:00Z")


def test_the_template_is_well_formed_but_its_example_row_is_refused() -> None:
    template = Path(__file__).resolve().parents[1] / "contrib" / "template.csv"
    with pytest.raises(contrib.ContributionError, match="example row"):
        contrib.read_file(template)


def test_one_bad_row_rejects_the_file_and_names_line_and_field(tmp_path: Path) -> None:
    with pytest.raises(contrib.ContributionError, match="line 3: tenor_months"):
        contrib.read_file(_csv(tmp_path, _line(), _line(tenor=7)))


def test_a_node_price_entered_per_gpu_is_refused(tmp_path: Path) -> None:
    with pytest.raises(contrib.ContributionError, match="outside"):
        contrib.read_file(_csv(tmp_path, _line(price=180.0)))


def test_unknown_columns_are_refused(tmp_path: Path) -> None:
    p = tmp_path / "x.csv"
    p.write_text(HEADER + ",customer_name\n" + _line() + ",acme\n", encoding="utf-8")
    with pytest.raises(contrib.ContributionError, match="unknown columns"):
        contrib.read_file(p)


def test_the_private_store_can_never_live_in_the_repository() -> None:
    with pytest.raises(contrib.ContributionError, match="inside the repository"):
        contrib.private_db_path(contrib.REPO_ROOT / "data")


def test_contributions_are_immutable(store: sqlite3.Connection, tmp_path: Path) -> None:
    _ingest(store, tmp_path, "c1", _line())
    with pytest.raises(sqlite3.IntegrityError):
        store.execute("UPDATE contributions SET price_per_gpu_hour = 1")
    with pytest.raises(sqlite3.IntegrityError):
        store.execute("DELETE FROM contributions")


def test_two_contributors_publish_a_count_and_no_price(store: sqlite3.Connection,
                                                       tmp_path: Path) -> None:
    _ingest(store, tmp_path, "c1", _line(2.4))
    _ingest(store, tmp_path, "c2", _line(2.6))
    (cell,) = contrib.aggregate(store, "2026-09-01", "2026-09-30")
    assert not cell.published and cell.median is None and cell.n_contributors == 2
    out = json.loads(contrib.publishable_json([cell], "2026-09-01", "2026-09-30"))
    assert "median" not in out["cells"][0] and "c1" not in json.dumps(out)


def test_three_contributors_publish_a_median_but_no_range(store: sqlite3.Connection,
                                                          tmp_path: Path) -> None:
    for who, price in (("c1", 2.2), ("c2", 2.4), ("c3", 2.6)):
        _ingest(store, tmp_path, who, _line(price))
    (cell,) = contrib.aggregate(store, "2026-09-01", "2026-09-30")
    assert cell.published and cell.median == 2.4
    assert cell.p25 is None and cell.p75 is None  # quartiles of three would reveal all three


def test_five_contributors_publish_the_range(store: sqlite3.Connection,
                                             tmp_path: Path) -> None:
    for i, price in enumerate((2.0, 2.2, 2.4, 2.6, 2.8)):
        _ingest(store, tmp_path, f"c{i}", _line(price))
    (cell,) = contrib.aggregate(store, "2026-09-01", "2026-09-30")
    assert (cell.p25, cell.median, cell.p75) == (2.2, 2.4, 2.6)


def test_a_dominant_contributor_suppresses_the_cell(store: sqlite3.Connection,
                                                    tmp_path: Path) -> None:
    _ingest(store, tmp_path, "big", _line(2.0, gpus=1024))
    _ingest(store, tmp_path, "c2", _line(2.4, gpus=64))
    _ingest(store, tmp_path, "c3", _line(2.6, gpus=64))
    (cell,) = contrib.aggregate(store, "2026-09-01", "2026-09-30")
    assert not cell.published and "more than half" in (cell.reason or "")


def test_many_small_quotes_from_one_party_get_one_voice(store: sqlite3.Connection,
                                                        tmp_path: Path) -> None:
    _ingest(store, tmp_path, "spam", *[_line(1.0, gpus=8) for _ in range(10)])
    _ingest(store, tmp_path, "c2", _line(2.4, gpus=64))
    _ingest(store, tmp_path, "c3", _line(2.6, gpus=64))
    (cell,) = contrib.aggregate(store, "2026-09-01", "2026-09-30")
    assert cell.published and cell.median == 2.4 and cell.n_quotes == 12


def test_indicative_only_cells_are_suppressed(store: sqlite3.Connection,
                                              tmp_path: Path) -> None:
    for who in ("c1", "c2", "c3"):
        _ingest(store, tmp_path, who, _line(kind="indicative"))
    (cell,) = contrib.aggregate(store, "2026-09-01", "2026-09-30")
    assert cell.reason == "indicative quotes only"


def test_a_correction_replaces_the_row_and_keeps_it(store: sqlite3.Connection,
                                                    tmp_path: Path) -> None:
    for who, price in (("c1", 2.2), ("c2", 2.4), ("c3", 2.6)):
        _ingest(store, tmp_path, who, _line(price))
    rows, digest = contrib.read_file(_csv(tmp_path, _line(2.9), name="fix.csv"))
    with pytest.raises(contrib.ContributionError, match="only by the contributor"):
        contrib.ingest(store, "c2", rows, digest, "t", supersedes=1)
    contrib.ingest(store, "c1", rows, digest, "t", supersedes=1)
    with pytest.raises(contrib.ContributionError, match="already superseded"):
        contrib.ingest(store, "c1", rows, digest, "t", supersedes=1)
    (cell,) = contrib.aggregate(store, "2026-09-01", "2026-09-30")
    assert cell.median == 2.6 and store.execute("SELECT COUNT(*) FROM contributions"
                                                ).fetchone()[0] == 4
