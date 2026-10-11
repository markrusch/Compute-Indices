"""Rent against running cost: sourced inputs, honest ranges, and power that falls back openly."""

from __future__ import annotations

import sqlite3

import pytest

from tci import costfloor, db


@pytest.fixture(scope="module")
def record() -> sqlite3.Connection:
    return db.connect_readonly()


def test_every_input_carries_a_source_and_a_read_date() -> None:
    cfg = costfloor.load_inputs()
    entries = [*cfg["it_kw_per_gpu"].values(), cfg["pue"]["low"], cfg["pue"]["high"],
               cfg["colocation_eur_kw_month"], cfg["other_usd_per_gpu_hour"],
               cfg["power"]["fallback_eur_mwh"]]
    for e in entries:
        assert str(e["url"]).startswith("https://")
        assert e["source"] and len(str(e["read"])) == 10
    assert set(cfg["it_kw_per_gpu"]) == set(costfloor.CLASSES)
    assert set(cfg["other_usd_per_gpu_hour"]["spares"]) == set(costfloor.CLASSES)


def test_the_arithmetic(record: sqlite3.Connection) -> None:
    t = costfloor.table(record, "2026-10-11")
    assert t is not None
    cfg = costfloor.load_inputs()
    h100 = next(r for r in t.rows if r.cls == "H100")
    kw = cfg["it_kw_per_gpu"]["H100"]["kw"]
    price = t.power.eur_mwh
    assert h100.power.low == pytest.approx(kw * 1.2 * price / 1000, abs=1e-4)
    assert h100.power.high == pytest.approx(kw * 1.54 * price / 1000, abs=1e-4)
    assert h100.colocation.low == pytest.approx(kw * 165 / 730, abs=1e-4)
    assert h100.other.high == pytest.approx((0.145 + 0.1375) / t.fx, abs=1e-4)
    b200 = next(r for r in t.rows if r.cls == "B200")
    assert b200.other.high > h100.other.high
    assert h100.cost.low == pytest.approx(
        h100.power.low + h100.colocation.low + h100.other.low, abs=2e-4)
    for r in t.rows:
        assert r.cost.low < r.cost.high
        ratio = r.ratio(r.eu)
        if ratio is not None:
            assert ratio.low <= ratio.high


def test_a_thin_class_shows_no_rent(record: sqlite3.Connection) -> None:
    t = costfloor.table(record, "2026-10-11")
    assert t is not None
    for r in t.rows:
        if r.eu.sellers < 3:
            assert r.eu.eur is None and r.ratio(r.eu) is None


def test_power_falls_back_openly_until_enough_days_are_stored(
    conn: sqlite3.Connection,
) -> None:
    cfg = costfloor.load_inputs()
    pb = costfloor.power_basis(conn, "2026-10-11", cfg)
    assert pb.days == 0 and pb.eur_mwh == cfg["power"]["fallback_eur_mwh"]["value"]
    for day in range(5, 11):
        for zone in ("NL", "DE-LU"):
            conn.execute("INSERT INTO overlay_power VALUES (?, ?, 100.0, '{}')",
                         (f"2026-10-{day:02d}", zone))
    conn.commit()
    pb = costfloor.power_basis(conn, "2026-10-11", cfg)
    assert pb.days == 6 and pb.eur_mwh == 100.0
    assert costfloor.attribution(pb) is None  # rows of unknown origin earn no credit line


def test_the_credit_line_follows_the_rows(conn: sqlite3.Connection) -> None:
    cfg = costfloor.load_inputs()
    for day in range(5, 11):
        conn.execute("INSERT INTO overlay_power VALUES (?, 'NL', 90.0, ?)",
                     (f"2026-10-{day:02d}", '{"eic": "x", "doc": "A44"}'))
    conn.commit()
    pb = costfloor.power_basis(conn, "2026-10-11", cfg)
    assert costfloor.attribution(pb) == "ENTSO-E Transparency Platform"
    conn.execute("INSERT INTO overlay_power VALUES ('2026-10-11', 'FR', 90.0, ?)",
                 ('{"source": "energy_charts", "licence": "CC BY 4.0"}',))
    conn.commit()
    credit = costfloor.attribution(costfloor.power_basis(conn, "2026-10-11", cfg)) or ""
    assert "CC BY 4.0" in credit and "ENTSO-E" in credit
