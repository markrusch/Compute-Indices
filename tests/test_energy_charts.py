"""Day-ahead power from Energy-Charts: stored only under the licence it says it carries."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
import requests
import responses

from tci.collectors import energy_charts

FIXTURES = Path(__file__).parent / "fixtures" / "energy_charts"


def _fixture(zone: str) -> dict:
    return json.loads((FIXTURES / f"price_{zone}_2026-10-09.json").read_text(encoding="utf-8"))


def _serve(payloads: dict[str, object], status: int = 200) -> None:
    def callback(request: requests.PreparedRequest) -> tuple[int, dict, str]:
        zone = request.params["bzn"]  # type: ignore[attr-defined]
        return status, {}, json.dumps(payloads[zone])

    responses.add_callback(responses.GET, energy_charts.URL, callback=callback)


def _rows(conn: sqlite3.Connection) -> dict[str, float]:
    return {r[0]: r[1] for r in conn.execute(
        "SELECT zone, avg_price_eur_mwh FROM overlay_power WHERE date = '2026-10-09'")}


@responses.activate
def test_stores_the_day_mean_for_each_cc_by_zone(conn: sqlite3.Connection) -> None:
    _serve({z: _fixture(z) for z in energy_charts.ZONES})
    assert energy_charts.collect(conn, requests.Session(), "2026-10-09") == 3
    rows = _rows(conn)
    nl = _fixture("NL")["price"]
    assert rows["NL"] == pytest.approx(sum(nl) / len(nl), abs=1e-4)
    assert set(rows) == {"NL", "DE-LU", "FR"}
    raw = json.loads(conn.execute(
        "SELECT raw_json FROM overlay_power WHERE zone = 'NL'").fetchone()[0])
    assert raw["source"] == "energy_charts" and "CC BY 4.0" in raw["licence"]


@responses.activate
def test_a_zone_under_another_licence_is_not_stored(conn: sqlite3.Connection) -> None:
    restricted = dict(_fixture("NL"))
    restricted["license_info"] = "The data provided herein is for private and internal use only."
    _serve({"NL": restricted, "DE-LU": _fixture("DE-LU"), "FR": _fixture("FR")})
    assert energy_charts.collect(conn, requests.Session(), "2026-10-09") == 2
    assert "NL" not in _rows(conn)


@responses.activate
def test_an_entsoe_row_is_never_overwritten(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO overlay_power (date, zone, avg_price_eur_mwh, raw_json)"
                 " VALUES ('2026-10-09', 'NL', 1.0, '{\"doc\": \"A44\"}')")
    conn.commit()
    _serve({z: _fixture(z) for z in energy_charts.ZONES})
    energy_charts.collect(conn, requests.Session(), "2026-10-09")
    assert _rows(conn)["NL"] == 1.0


@responses.activate
def test_failures_cost_the_zone_and_never_raise(conn: sqlite3.Connection) -> None:
    partial = dict(_fixture("FR"))
    partial["price"] = [None] * 90 + partial["price"][90:]
    _serve({"NL": {"oops": True}, "DE-LU": _fixture("DE-LU"), "FR": partial})
    assert energy_charts.collect(conn, requests.Session(), "2026-10-09") == 1
    assert set(_rows(conn)) == {"DE-LU"}


@responses.activate
def test_a_server_error_stores_nothing(conn: sqlite3.Connection) -> None:
    _serve({z: {} for z in energy_charts.ZONES}, status=500)
    assert energy_charts.collect(conn, requests.Session(), "2026-10-09") == 0
