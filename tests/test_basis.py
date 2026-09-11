# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""The US reference block and the EU-US basis (v0.6.0)."""

from __future__ import annotations

import sqlite3

import pytest

from tci.basis import compute_basis
from tci.commands import compute_all_series
from tci.config import load_factors
from tci.models import IndexPrint
from tci.normalise import normalise_observations
from tests.conftest import insert_run


def _print(series: str, value: float | None, flags: str = "") -> IndexPrint:
    return IndexPrint(date="2026-10-01", series=series, value_usd=value, value_eur=None,
                      fx_rate=None, fx_date=None, n_sources=5, n_executable=1,
                      flags=flags, constituents=())


def test_basis_is_lead_minus_reference() -> None:
    b = compute_basis("2026-10-01", "B", _print("EU", 3.49), _print("US", 2.99),
                      (1.16, "2026-09-30"))
    assert b.value_usd == 0.5 and b.value_eur == round(0.5 / 1.16, 6)
    legs = {c.provider: c for c in b.constituents}
    assert legs["EU"].weight == 100.0 and legs["US"].weight == -100.0


@pytest.mark.parametrize(("eu", "us", "flag"), [
    (None, 2.99, "lead_gap"), (3.49, None, "reference_gap"), (None, None, "lead_gap"),
])
def test_basis_gaps_when_a_leg_gaps(eu: float | None, us: float | None, flag: str) -> None:
    b = compute_basis("2026-10-01", "B", _print("EU", eu, "insufficient_sources"),
                      _print("US", us, "insufficient_sources"), None)
    assert b.value_usd is None and flag in b.flags


def test_us_block_uses_the_same_unit_definition() -> None:
    f = load_factors(for_date="2026-10-01")
    row = {"provider": "vast.ai", "source": "vast_ai", "tier": "executable",
           "gpu_model": "H100_SXM", "gpu_count": 8, "price_usd_per_gpu_hr": 2.2,
           "country": "US", "term": "on_demand", "raw_json": "{}"}
    us = f.countries_of("US")
    assert len(normalise_observations([row], f, countries=us)) == 1
    assert normalise_observations([row], f) == []  # not EU/EEA
    one_gpu = dict(row, gpu_count=1)
    assert normalise_observations([one_gpu], f, countries=us) == []  # same node floor


def test_no_basis_series_before_its_version() -> None:
    assert load_factors(for_date="2026-09-30").basis_series == {}


def _insert(conn: sqlite3.Connection, run: str, day: str, provider: str, source: str,
            tier: str, price: float, country: str) -> None:
    conn.execute(
        "INSERT INTO observations (run_id, ts_utc, source, provider, gpu_model, gpu_count,"
        " price_usd_per_gpu_hr, region, country, interconnect, tier, term, raw_json)"
        " VALUES (?, ?, ?, ?, 'H100_SXM', 8, ?, NULL, ?, NULL, ?, 'on_demand', ?)",
        (run, f"{day}T11:00:00Z", source, provider, price, country, tier,
         '{"last_verified": "2026-09-25"}'),
    )


def test_daily_run_prints_us_leg_and_basis(conn: sqlite3.Connection) -> None:
    day = "2026-10-01"
    run = insert_run(conn, utc_date=day)
    eu = [("vast.ai", "vast_ai", "executable", 2.27), ("runpod", "runpod", "executable", 3.49),
          ("verda", "gpuhunt", "list", 3.25), ("nebius", "gpuhunt", "list", 3.85),
          ("seeweb", "static_yaml", "list", 2.16), ("lambdalabs", "gpuhunt", "list", 3.99)]
    us = [("vast.ai", "vast_ai", "executable", 1.95), ("runpod", "runpod", "executable", 3.49),
          ("lambdalabs", "gpuhunt", "list", 3.99), ("digitalocean", "digitalocean", "list", 4.41),
          ("voltagepark", "voltagepark", "list", 1.99)]
    for p, s, t, price in eu:
        _insert(conn, run, day, p, s, t, price, "NL")
    for p, s, t, price in us:
        _insert(conn, run, day, p, s, t, price, "US")
    conn.execute("INSERT INTO fx (date, eur_usd, source) VALUES ('2026-09-30', 1.16, 't')")
    compute_all_series(conn, day)

    def value(series: str) -> float | None:
        return conn.execute(
            "SELECT value_usd FROM daily_index WHERE date=? AND series=?"
            " ORDER BY revision DESC LIMIT 1", (day, series)).fetchone()["value_usd"]

    eu_v, us_v, basis = value("EU-CRI-H100"), value("EU-CRI-H100-US"), value(
        "EU-CRI-H100-BASIS-US")
    assert eu_v is not None and us_v is not None
    assert basis == round(eu_v - us_v, 6)
    # The US leg never sees EU rows and vice versa.
    cons = {r["provider"] for r in conn.execute(
        "SELECT provider FROM constituents WHERE date=? AND series='EU-CRI-H100-US'"
        " AND included=1", (day,))}
    assert "voltagepark" in cons and "verda" not in cons
