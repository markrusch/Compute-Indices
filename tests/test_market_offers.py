# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""The full vast.ai offer book is stored beside the prices, and cannot reach a print.

WHY THIS EXISTS. Until 2026-09-14 the collector discarded every community and unverified
offer before storage, which left a within-venue price-on-performance analysis (Research
Note 2026-05) with 4 to 7 H100 SXM rows a day from two or three hosts. Those offers are
now kept in `market_offers`. The properties that matter are the ones that would otherwise
fail silently: that `observations` is byte-for-byte what it was, that nothing outside the
index scope leaks into it, and that a problem storing the book never costs the prices.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
import responses

from tci import db
from tci.collectors import base
from tci.collectors.vast_ai import URL as VAST_URL
from tci.collectors.vast_ai import VastAiCollector, in_index_scope
from tci.models import MarketOffer, Observation

FIXTURE = Path(__file__).parent / "fixtures" / "vast_bundles.json"


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = db.connect(tmp_path / "t.db")
    db.migrate(c)
    return c


def _serve_fixture() -> dict:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def callback(request):  # type: ignore[no-untyped-def]
        body = json.loads(request.body)
        name = body["gpu_name"]["in"][0]
        book = [o for o in data["offers"] if o.get("gpu_name") == name]
        book.sort(key=lambda o: o["dph_total"], reverse=body["order"][0][1] == "desc")
        return (200, {}, json.dumps({"offers": book[: body["limit"]]}))

    responses.add_callback(responses.POST, VAST_URL, callback=callback)
    return data


@responses.activate
def test_the_book_keeps_every_offer_and_the_prices_keep_only_the_scope() -> None:
    data = _serve_fixture()
    collector = VastAiCollector(spacing_seconds=0)
    out = collector.collect(base.make_session())

    book = collector.offer_book
    assert len(book) == len(data["offers"]), "an offer the collector read was not kept"
    assert {b.in_index_scope for b in book} == {True, False}, "fixture no longer tests both"
    assert sum(b.in_index_scope for b in book) == sum(in_index_scope(o) for o in data["offers"])

    # Nothing outside the scope reaches the rows normalise.py reads.
    for o in out:
        raw = json.loads(o.raw_json)
        assert raw["verification"] == "verified" and raw["hosting_type"] == 1


@responses.activate
def test_storing_the_book_does_not_change_the_observations(tmp_path: Path) -> None:
    """The same fixture, persisted with and without a book, gives identical observations."""
    _serve_fixture()
    with_book = db.connect(tmp_path / "a.db")
    db.migrate(with_book)
    assert base.run_collector(with_book, VastAiCollector(spacing_seconds=0), "2026-09-13") == "ok"

    class NoBook(VastAiCollector):
        def collect(self, session):  # type: ignore[no-untyped-def]
            rows = super().collect(session)
            self.offer_book = []
            return rows

    without = db.connect(tmp_path / "b.db")
    db.migrate(without)
    assert base.run_collector(without, NoBook(spacing_seconds=0), "2026-09-13") == "ok"

    cols = ("provider, gpu_model, gpu_count, price_usd_per_gpu_hr, region, country,"
            " interconnect, tier, term")
    q = f"SELECT {cols} FROM observations ORDER BY {cols}"
    assert [tuple(r) for r in with_book.execute(q)] == [tuple(r) for r in without.execute(q)]
    assert with_book.execute("SELECT COUNT(*) FROM market_offers").fetchone()[0] > 0
    assert without.execute("SELECT COUNT(*) FROM market_offers").fetchone()[0] == 0


@responses.activate
def test_the_book_is_stored_against_the_run(conn: sqlite3.Connection) -> None:
    data = _serve_fixture()
    assert base.run_collector(conn, VastAiCollector(spacing_seconds=0), "2026-09-13") == "ok"

    run = conn.execute("SELECT run_id, notes FROM runs WHERE source = 'vast_ai'").fetchone()
    stored = conn.execute(
        "SELECT COUNT(*) n, SUM(in_index_scope) s, COUNT(DISTINCT run_id) r FROM market_offers"
    ).fetchone()
    assert stored["n"] == len(data["offers"])
    assert stored["s"] == sum(in_index_scope(o) for o in data["offers"])
    assert stored["r"] == 1
    assert f"{len(data['offers'])} market offers" in run["notes"]


def _book_row(**over: object) -> MarketOffer:
    kwargs: dict = {
        "ts_utc": db.utc_now_iso(), "source": "s", "queried_name": "H100 SXM",
        "offer_id": "1", "machine_id": "2", "host_id": "3", "gpu_model": "H100_SXM",
        "num_gpus": 1, "dph_total": 2.5, "country": "NL", "verification": "unverified",
        "hosting_type": 0, "in_index_scope": False, "raw_json": "{}",
    }
    kwargs.update(over)
    return MarketOffer(**kwargs)


class _WithBook:
    def __init__(self, name: str, book: list[MarketOffer]) -> None:
        self.name = name
        self.offer_book = book

    def collect(self, session: object) -> list[Observation]:
        return [
            Observation(
                ts_utc=db.utc_now_iso(), source=self.name, provider="p", gpu_model="H100_SXM",
                gpu_count=8, price_usd_per_gpu_hr=3.0, region="r", country="NL",
                interconnect="NVLink", tier="list", term="on_demand", raw_json="{}",
            )
        ]


def test_an_unstorable_book_costs_the_book_not_the_prices(conn: sqlite3.Connection) -> None:
    # A book row missing its NOT NULL raw_json is refused by the schema.
    bad = _WithBook("bad_book", [_book_row(source="bad_book", raw_json=None)])
    assert base.run_collector(conn, bad, "2026-09-13") == "ok"

    assert conn.execute(
        "SELECT COUNT(*) FROM observations WHERE source = 'bad_book'"
    ).fetchone()[0] == 1, "the prices were lost with the book"
    assert conn.execute("SELECT COUNT(*) FROM market_offers").fetchone()[0] == 0
    notes = conn.execute("SELECT notes FROM runs WHERE source = 'bad_book'").fetchone()[0]
    assert "offer book not stored" in notes, "a lost book left no trace in runs.notes"


def test_market_offers_are_append_only(conn: sqlite3.Connection) -> None:
    assert base.run_collector(conn, _WithBook("ok_book", [_book_row()]), "2026-09-13") == "ok"
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        conn.execute("UPDATE market_offers SET dph_total = 0")
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        conn.execute("DELETE FROM market_offers")
