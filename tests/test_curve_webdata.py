# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""The published curve tables (outputs.webdata.curve_tables, write_curve).

Built on the real parameter set for each date, because what the panel admits is the thing
under test. The seller names that must be on the panel are chosen from that parameter set
rather than hard-coded, so a panel change moves the fixture and not the assertions.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tci import db
from tci.config import load_factors
from tci.outputs import webdata
from tests.conftest import insert_run

D = "2026-09-15"


@pytest.fixture(autouse=True)
def _no_published_schedules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The shipped schedules file changes when a seller's page does. Nothing here is about it.
    path = tmp_path / "term_schedules.yaml"
    path.write_text("max_age_days: 90\nschedules: []\n", encoding="utf-8")
    monkeypatch.setattr(webdata, "TERM_SCHEDULES", path)


def _obs(conn: sqlite3.Connection, run_id: str, provider: str, source: str, gpu: str,
         term_: str, price: float, *, currency: str = "USD") -> None:
    raw = {"extra": {"plan": f"{gpu}-p"}, "currency": currency,
           "price_native_per_gpu_hr": price}
    conn.execute(
        "INSERT INTO observations (run_id, ts_utc, source, provider, gpu_model, gpu_count,"
        " price_usd_per_gpu_hr, region, country, interconnect, tier, term, raw_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, "2026-09-15T11:00:00+00:00", source, provider, gpu, 8, price, "r", "NL",
         None, "list", term_, json.dumps(raw)),
    )


def _print(conn: sqlite3.Connection, date: str, series: str, value: float | None,
           run_id: str) -> None:
    conn.execute(
        "INSERT INTO daily_index (date, series, revision, value_usd, value_eur, fx_rate,"
        " fx_date, n_sources, n_executable, flags, methodology_version, computed_at, run_id)"
        " VALUES (?, ?, 1, ?, NULL, NULL, NULL, 5, 1, '', '0.4.0', ?, ?)",
        (date, series, value, db.utc_now_iso(), run_id),
    )


def _neocloud_h100_panel(date: str) -> list[tuple[str, str]]:
    """Three neocloud sellers the panel live on `date` admits to H100, with their source."""
    factors = load_factors(for_date=date)
    assert factors.panel is not None
    out: list[tuple[str, str]] = []
    for provider, entry in sorted(factors.panel.items()):
        if entry.segment != "neocloud":
            continue
        for source, classes in sorted(entry.sources.items()):
            if "H100" in classes:
                out.append((provider, source))
                break
    assert len(out) >= 3, "the fixture needs three neocloud H100 panel sellers"
    return out[:3]


def _day(conn: sqlite3.Connection, date: str, run_id: str, *,
         level: float | None = 3.49, level_date: str | None = None) -> None:
    insert_run(conn, run_id, date)
    for term_, price in (("on_demand", 14.3793), ("reserved_1yr", 9.2028),
                         ("reserved_3yr", 6.3125), ("reserved_5yr", 5.7517)):
        _obs(conn, run_id, "azure", "azure_retail", "H100_SXM", term_, price)
    # Civo: off the panel, and its schedule distinguishes an H100 from an L40S.
    for gpu, od, r12 in (("H100_SXM", 2.99, 2.6901), ("L40S", 1.00, 0.80)):
        _obs(conn, run_id, "civo", "civo", gpu, "on_demand", od)
        _obs(conn, run_id, "civo", "civo", gpu, "reserved_1yr", r12)
    # Latitude: one schedule for a B300 and an H100.
    for gpu, od in (("B300_SXM", 4.00), ("H100_UNSPEC", 3.37)):
        _obs(conn, run_id, "latitude", "latitude", gpu, "on_demand", od)
        _obs(conn, run_id, "latitude", "latitude", gpu, "commit_1mo", od * 0.50)
        _obs(conn, run_id, "latitude", "latitude", gpu, "reserved_1yr", od * 0.35)
    # OVHcloud's H200 monthly plan at exactly on-demand.
    _obs(conn, run_id, "ovhcloud", "ovh", "H200_SXM", "on_demand", 4.00)
    _obs(conn, run_id, "ovhcloud", "ovh", "H200_SXM", "commit_1mo", 4.00)
    for (provider, source), spot in zip(_neocloud_h100_panel(date), (3.00, 3.20, 2.90),
                                        strict=True):
        _obs(conn, run_id, provider, source, "H100_SXM", "on_demand", spot)
        _obs(conn, run_id, provider, source, "H100_SXM", "reserved_1yr", spot * 0.90)
        _obs(conn, run_id, provider, source, "H100_SXM", "reserved_2yr", spot * 0.85)
    if level is not None or level_date is not None:
        _print(conn, level_date or date, "EU-CRI-H100-NC", level, run_id)
    conn.commit()


def _seller(tables: dict[str, Any], provider: str, gpu: str) -> dict[str, Any]:
    (c,) = [c for c in tables["sellers"] if c["provider"] == provider and c["gpu_model"] == gpu]
    return c


def _neocloud_h100(tables: dict[str, Any]) -> dict[str, Any]:
    (p,) = [p for p in tables["pooled"]
            if p["gpu_model"] == "H100_SXM" and p["segment"] == "neocloud"]
    return p


def test_a_seller_curve_carries_its_forwards_spans_and_formation(conn: sqlite3.Connection) -> None:
    _day(conn, D, "r1")
    azure = _seller(webdata.curve_tables(conn, D), "azure", "H100_SXM")
    fwd = {(f["m1"], f["m2"]): f for f in azure["forwards"]}
    assert fwd[(12, 36)]["rate"] == pytest.approx(4.8674, abs=1e-4)
    assert fwd[(36, 60)]["rate"] == pytest.approx(4.9105, abs=1e-4)
    assert fwd[(12, 36)]["span_months"] == 24 and fwd[(12, 36)]["wide_span"] is True
    assert all({"observed", "price_formation"} <= set(k) for k in azure["knots"])
    assert azure["knots"][0] == {**azure["knots"][0], "tenor_months": 0, "ratio": 1.0}


def test_formation_is_tagged_from_the_data(conn: sqlite3.Connection) -> None:
    _day(conn, D, "r1")
    tags = webdata.curve_tables(conn, D)["price_formation"]
    assert tags["latitude"] == "administered_uniform"
    assert tags["civo"] == "administered_differentiated"
    assert tags["azure"] == "administered_untested"   # one chip in this fixture


def test_a_commitment_at_on_demand_builds_no_curve_and_says_so(
        conn: sqlite3.Connection) -> None:
    _day(conn, D, "r1")
    tables = webdata.curve_tables(conn, D)
    assert not [c for c in tables["sellers"] if c["provider"] == "ovhcloud"]
    assert {"provider": "ovhcloud", "gpu_model": "H200_SXM", "currency": "USD",
            "reason": "no committed price below on-demand"} in tables["unbuilt"]


def test_three_panel_sellers_publish_an_aggregate_at_the_same_day_level(
        conn: sqlite3.Connection) -> None:
    _day(conn, D, "r1")
    p = _neocloud_h100(webdata.curve_tables(conn, D))
    assert p["status"] == "published" and p["level_series"] == "EU-CRI-H100-NC"
    rates = {k["tenor_months"]: k["rate"] for k in p["curve"]["knots"]}
    assert rates == {0: 3.49, 12: pytest.approx(3.49 * 0.90, abs=1e-4),
                     24: pytest.approx(3.49 * 0.85, abs=1e-4)}


def test_an_off_panel_seller_never_votes_on_the_aggregate(conn: sqlite3.Connection) -> None:
    assert "civo" not in (load_factors(for_date=D).panel or {})
    _day(conn, D, "r1")
    p = _neocloud_h100(webdata.curve_tables(conn, D))
    assert all("civo" not in pt["providers"] for pt in p["points"])
    assert all(pt["n_providers"] == 3 for pt in p["points"])


def test_the_aggregate_level_is_never_carried_forward(conn: sqlite3.Connection) -> None:
    # The print exists, one session earlier. Using it would show an old print as current.
    _day(conn, D, "r1", level_date="2026-09-14")
    p = _neocloud_h100(webdata.curve_tables(conn, D))
    assert p["curve"] is None and p["level_usd"] is None
    assert p["status"] == f"EU-CRI-H100-NC did not print on {D}"


def test_a_gapped_print_gaps_the_aggregate(conn: sqlite3.Connection) -> None:
    _day(conn, D, "r1", level=None, level_date=D)
    p = _neocloud_h100(webdata.curve_tables(conn, D))
    assert p["curve"] is None and p["status"] == f"EU-CRI-H100-NC did not print on {D}"


def test_rate_cards_never_publish_a_forward_spot_curve(conn: sqlite3.Connection) -> None:
    _day(conn, D, "r1")
    fs = webdata.curve_tables(conn, D)["forward_spot"]
    assert fs["published"] is False and fs["sellers_market_quoted"] == 0


def test_csv_rows_describe_the_segment_ending_at_each_tenor(conn: sqlite3.Connection) -> None:
    _day(conn, D, "r1")
    rows = [r.split(",") for r in webdata.curve_rows(webdata.curve_tables(conn, D))]
    header = webdata.CURVE_CSV_HEADER.split(",")
    azure = [dict(zip(header, r, strict=True)) for r in rows
             if r[2] == "H100_SXM" and r[3] == "azure"]
    by_tenor = {r["tenor_months"]: r for r in azure}
    assert by_tenor["0"]["forward_from"] == "" and by_tenor["0"]["forward_rate"] == ""
    assert by_tenor["36"]["forward_from"] == "12" and by_tenor["36"]["wide_span"] == "1"


def test_history_rebuilds_a_past_date_unchanged(conn: sqlite3.Connection,
                                                tmp_path: Path) -> None:
    _day(conn, "2026-09-14", "r14")
    _day(conn, D, "r15")
    webdata.write_curve(conn, tmp_path / "a")
    before = [ln for ln in (tmp_path / "a" / "history.csv").read_text().splitlines()
              if ln.startswith("2026-09-14,")]
    assert before == webdata.curve_rows(webdata.curve_tables(conn, "2026-09-14"))

    _day(conn, "2026-09-16", "r16")
    path = webdata.write_curve(conn, tmp_path / "b")
    after = [ln for ln in (tmp_path / "b" / "history.csv").read_text().splitlines()
             if ln.startswith("2026-09-14,")]
    assert after == before
    assert path is not None and json.loads(path.read_text())["date"] == "2026-09-16"


def test_no_term_prices_no_files(conn: sqlite3.Connection, tmp_path: Path) -> None:
    assert webdata.write_curve(conn, tmp_path / "c") is None
    assert not (tmp_path / "c").exists()
