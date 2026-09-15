# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""The rates and vast.ai reserved collectors, on responses captured live on 15 September 2026."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
import requests

from tci.collectors import base, rates, vast_reserved

FIX = Path(__file__).parent / "fixtures" / "forward"


def test_ecb_csv_parses_dates_tenors_and_values() -> None:
    estr = rates.parse_ecb((FIX / "ecb_estr.csv").read_text(encoding="utf-8"))
    curve = rates.parse_ecb((FIX / "ecb_yc_sr_1y.csv").read_text(encoding="utf-8"))
    assert len(estr) == 3 and {t for _, t, _ in estr} == {1}
    assert len(curve) == 3 and {t for _, t, _ in curve} == {365}
    for obs, _, value in estr + curve:
        assert len(obs) == 10 and obs[4] == "-" and 0.0 < value < 10.0


def test_treasury_csv_reads_every_short_tenor() -> None:
    rows = rates.parse_treasury((FIX / "ust_par_2026.csv").read_text(encoding="utf-8"))
    on_15th = {t: v for d, t, v in rows if d == "2026-09-15"}
    assert on_15th[365] == 4.39 and on_15th[30] == 3.93 and on_15th[1826] == 4.83
    assert 3650 not in on_15th  # tenors beyond five years are not kept


def test_rates_are_stored_once_and_a_restatement_is_a_new_row(conn: sqlite3.Connection) -> None:
    obs = [("2026-09-15", 365, 4.39)]
    assert rates.store(conn, "UST-PAR", "USD", "rates", obs) == 1
    assert rates.store(conn, "UST-PAR", "USD", "rates", obs) == 0
    assert rates.store(conn, "UST-PAR", "USD", "rates", [("2026-09-15", 365, 4.40)]) == 1
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        conn.execute("DELETE FROM overlay_rates")


def test_treasury_falls_back_to_last_year_in_early_january() -> None:
    body = (FIX / "ust_par_2026.csv").read_text(encoding="utf-8")
    asked: list[str] = []

    class Resp:
        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            pass

    class Session:
        def get(self, url: str, **_: Any) -> Resp:
            asked.append(url)
            return Resp("Date,\"1 Mo\"\n" if "/2027/" in url else body)

    out = rates._treasury(Session(), "2027-01-02")  # type: ignore[arg-type]
    assert out and "/2027/" in asked[0] and "/2026/" in asked[1]


def _offers() -> list[dict]:
    path = FIX / "vast_reserved_h100_sxm_90d.json"
    return json.loads(path.read_text(encoding="utf-8"))["offers"]


def test_every_reserved_offer_is_kept_with_its_scope_and_duration() -> None:
    quotes = vast_reserved.to_term_quotes(_offers(), "2026-09-15T12:00:00Z", "H100 SXM", 90)
    assert len(quotes) == len(_offers()) == 7
    discounted = [q for q in quotes if q.discounted_dph_total and q.dph_total
                  and q.discounted_dph_total < q.dph_total]
    assert len(discounted) == 1
    (d,) = discounted
    assert d.country == "CZ" and d.in_index_scope and d.requested_days == 90
    assert d.max_duration_days is not None and d.max_duration_days > 90
    assert all(q.gpu_model == "H100_SXM" for q in quotes)


class _FakeResp:
    def __init__(self, status: int, offers: list[dict], headers: dict | None = None) -> None:
        self.status_code = status
        self._offers = offers
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self) -> dict:
        return {"offers": self._offers}


class _FakeSession:
    def __init__(self, responses: list[_FakeResp]) -> None:
        self.responses = responses
        self.queries: list[dict] = []

    def post(self, url: str, json: dict, timeout: int) -> _FakeResp:  # noqa: A002
        self.queries.append(json)
        return self.responses.pop(0)


def test_collector_spaces_every_request_and_honours_retry_after() -> None:
    slept: list[float] = []
    offers = _offers()
    session = _FakeSession([
        _FakeResp(200, offers),
        _FakeResp(429, [], {"Retry-After": "12"}), _FakeResp(200, offers),
        _FakeResp(429, [], {"Retry-After": "not-a-number"}), _FakeResp(429, []),
    ])
    c = vast_reserved.VastReservedCollector(sleep=slept.append)
    assert c.collect(session) == []  # type: ignore[arg-type]
    # 8 s before each of three requests, 12 s for the first 429, 8 s (unparseable) for the second.
    assert slept == [8.0, 8.0, 12.0, 8.0, 8.0]
    assert [q["duration"]["gte"] // 86400 for q in session.queries] == [30, 90, 90, 180, 180]
    assert all(q["type"] == "reserved" for q in session.queries)
    assert len(c.term_quotes) == 14  # two durations read, the third failed after its retry


def test_an_empty_book_across_every_duration_fails_the_run() -> None:
    session = _FakeSession([_FakeResp(200, []) for _ in range(3)])
    with pytest.raises(RuntimeError, match="no offers"):
        collector = vast_reserved.VastReservedCollector(sleep=lambda s: None)
        collector.collect(session)  # type: ignore[arg-type]


def test_quotes_are_stored_through_run_collector(conn: sqlite3.Connection) -> None:
    session = _FakeSession([_FakeResp(200, _offers()) for _ in range(3)])
    c = vast_reserved.VastReservedCollector(sleep=lambda s: None)
    assert base.run_collector(conn, c, "2026-09-15", session) == "ok"  # type: ignore[arg-type]
    assert conn.execute("SELECT COUNT(*) FROM term_quotes").fetchone()[0] == 21
    assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
    note = conn.execute("SELECT notes FROM runs WHERE source = 'vast_reserved'").fetchone()[0]
    assert note == "0 observations; 21 term quotes"
