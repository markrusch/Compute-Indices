# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""The canary's own failure modes. Nothing here touches the network or the record."""

from __future__ import annotations

import sqlite3

import pytest

from tci import canary, db
from tci.collectors import base
from tci.models import Observation


class _Collector:
    def __init__(self, name: str, rows: list[Observation] | None = None,
                 raises: Exception | None = None) -> None:
        self.name = name
        self._rows = rows or []
        self._raises = raises

    def collect(self, session: object) -> list[Observation]:
        if self._raises:
            raise self._raises
        return self._rows


def _obs(source: str, model: str = "H100_SXM") -> Observation:
    return Observation(
        ts_utc="2026-09-12T11:00:00Z", source=source, provider="p", gpu_model=model,
        gpu_count=8, price_usd_per_gpu_hr=3.0, region="eu", country="NL",
        interconnect=None, tier="list", term="on_demand", raw_json="{}",
    )


def _results(conn: sqlite3.Connection, collectors: list[_Collector]) -> list[canary.SourceResult]:
    out = []
    for c in collectors:
        status = base.run_collector(conn, c, "2026-09-12", session=None)
        out.append(canary._summarise(conn, c.name, status))
    return out


def test_a_source_that_raises_is_reported_with_its_reason(conn) -> None:
    results = _results(conn, [_Collector("boom", raises=RuntimeError("page reshaped"))])
    assert results[0].status == "failed"
    assert results[0].broken
    assert "page reshaped" in results[0].note


def test_a_source_that_returns_nothing_cleanly_counts_as_broken(conn) -> None:
    """The failure a fail-soft pipeline hides best: no exception, no rows, no signal.

    `run_collector` records status 'ok' for a collector that parsed the page and matched
    zero products, which is what a silently reshaped table looks like from the outside.
    """
    results = _results(conn, [_Collector("quiet", rows=[])])
    assert results[0].status == "empty"
    assert results[0].broken
    assert results[0].rows == 0


def test_a_healthy_source_reports_its_models(conn) -> None:
    results = _results(conn, [_Collector("good", rows=[_obs("good"), _obs("good", "H200_SXM")])])
    assert results[0].status == "ok"
    assert not results[0].broken
    assert results[0].models == ("H100_SXM", "H200_SXM")


def test_render_puts_the_broken_sources_first_and_counts_them(conn) -> None:
    results = _results(conn, [
        _Collector("zz_good", rows=[_obs("zz_good")]),
        _Collector("aa_bad", raises=ValueError("gone")),
    ])
    text = canary.render(results)
    body = [ln for ln in text.splitlines() if ln and not ln.startswith("-")]
    assert body[1].startswith("aa_bad"), "a broken source has to be readable without scrolling"
    assert "1 not reporting: aa_bad" in text


def test_the_canary_never_writes_to_the_committed_database(monkeypatch) -> None:
    """It exists to be run casually, including from a clone with real data in it."""
    seen: list[object] = []
    real_connect = db.connect

    def spy(path=None):  # type: ignore[no-untyped-def]
        seen.append(path)
        return real_connect(path)

    monkeypatch.setattr(db, "connect", spy)
    monkeypatch.setattr(canary, "db", db)
    monkeypatch.setattr("tci.commands.collectors_for_daily", lambda: [_Collector("x", [])])
    canary.run()
    assert seen and all(p is not None for p in seen), (
        "canary.run() connected with no path, which is data/eucri.db"
    )
    assert all("canary" in str(p) for p in seen)


def test_filtering_to_one_source_returns_only_that_source(monkeypatch) -> None:
    monkeypatch.setattr(
        "tci.commands.collectors_for_daily",
        lambda: [_Collector("a", [_obs("a")]), _Collector("b", [_obs("b")])],
    )
    assert [r.source for r in canary.run(frozenset({"b"}))] == ["b"]


@pytest.mark.parametrize("status", ["ok", "failed"])
def test_summarise_reads_the_note_the_run_stored(conn, status: str) -> None:
    c = _Collector("s", rows=[_obs("s")] if status == "ok" else None,
                   raises=None if status == "ok" else OSError("timeout"))
    base.run_collector(conn, c, "2026-09-12", session=None)
    r = canary._summarise(conn, "s", status)
    assert r.note
