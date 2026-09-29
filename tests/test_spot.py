# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""v0.11.0 (notice 2026-N8): spot series per region, the on-demand to spot spread, and
the analytics and page built on them (tci.spot, outputs/spot_page.py)."""

from __future__ import annotations

import math
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from tci import spot
from tci.commands import compute_all_series
from tci.config import load_factors
from tci.normalise import normalise_observations
from tests.conftest import insert_run

V011 = "2026-10-06"
SPOT_SERIES = ("EU-CRI-H100-SPOT", "EU-CRI-H100-SPOT-US", "EU-CRI-H100-SPOT-GLOBAL")


def _obs(conn: sqlite3.Connection, run: str, day: str, provider: str, source: str, tier: str,
         price: float, country: str | None, region: str | None = None, count: int = 8) -> None:
    conn.execute(
        "INSERT INTO observations (run_id, ts_utc, source, provider, gpu_model, gpu_count,"
        " price_usd_per_gpu_hr, region, country, interconnect, tier, term, raw_json)"
        " VALUES (?, ?, ?, ?, 'H100_SXM', ?, ?, ?, ?, NULL, ?, 'on_demand', '{}')",
        (run, f"{day}T11:00:00Z", source, provider, count, price, region, country, tier))


EU_OD = [("vast.ai", "vast_ai", "executable", 2.27), ("runpod", "runpod", "executable", 3.49),
         ("verda", "gpuhunt", "list", 3.48), ("nebius", "gpuhunt", "list", 3.85),
         ("seeweb", "seeweb", "list", 2.16), ("lambdalabs", "gpuhunt", "list", 3.99)]
EU_SPOT = [("aws", "gpuhunt", 1.31, "SE"), ("verda", "gpuhunt", 1.74, "FI"),
           ("azure", "azure_retail", 2.66, "NL"), ("gcp", "gpuhunt", 6.31, "BE")]
# Outside the EU: CoreWeave's US spot row, and Together's preemptible rate, which has no
# country and counts in GLOBAL only.
WORLD_SPOT = [("coreweave", "coreweave", 2.46, "US"), ("together", "together", 1.99, None)]


def _seed(conn: sqlite3.Connection, day: str, run_id: str = "r1") -> None:
    run = insert_run(conn, run_id=run_id, utc_date=day)
    for p, s, t, price in EU_OD:
        _obs(conn, run, day, p, s, t, price, "NL")
    for p, s, price, cc in EU_SPOT + WORLD_SPOT:
        _obs(conn, run, day, p, s, "spot", price, cc)
    # Nebius's catalogue spot row: dynamic at the source and public only as a floor, so
    # its panel entry admits on-demand tiers only (0.11.0 as amended on 29 September).
    _obs(conn, run, day, "nebius", "gpuhunt", "spot", 2.15, "FI")
    # Kept out of every spot series: a bid floor and a retired product.
    _obs(conn, run, day, "vast.ai", "vast_ai", "interruptible", 0.65, "NL", count=2)
    _obs(conn, run, day, "azure", "azure_retail", "interruptible", 3.04, "NL")
    prev = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    conn.execute("INSERT OR IGNORE INTO fx (date, eur_usd, source) VALUES (?, 1.16, 't')",
                 (prev,))


def _row(conn: sqlite3.Connection, day: str, series: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM daily_index WHERE date=? AND series=?"
                        " ORDER BY revision DESC LIMIT 1", (day, series)).fetchone()


# --- the version --------------------------------------------------------------------------


def test_spot_series_exist_only_from_their_effective_date() -> None:
    before, after = load_factors(for_date="2026-10-05"), load_factors(for_date=V011)
    for s in SPOT_SERIES:
        assert s not in before.regional_series
        rs = after.regional_series[s]
        assert rs.tiers == {"spot"} and rs.model_class == "H100"
    assert after.population_for("spot") == {"marketplace", "neocloud", "hyperscaler"}
    spreads = {k: (b.lead, b.reference) for k, b in after.basis_series.items()
               if "SPOTSPREAD" in k}
    assert spreads == {
        "EU-CRI-H100-SPOTSPREAD": ("EU-CRI-H100", "EU-CRI-H100-SPOT"),
        "EU-CRI-H100-SPOTSPREAD-US": ("EU-CRI-H100-US", "EU-CRI-H100-SPOT-US"),
        "EU-CRI-H100-SPOTSPREAD-GLOBAL": ("EU-CRI-H100-GLOBAL", "EU-CRI-H100-SPOT-GLOBAL"),
    }


def test_every_other_regional_series_still_reads_on_demand_tiers_only() -> None:
    f = load_factors(for_date=V011)
    for name, rs in f.regional_series.items():
        if name not in SPOT_SERIES:
            assert rs.tiers == {"executable", "list"}, name


def test_the_tier_filter_keeps_spot_and_on_demand_apart() -> None:
    f = load_factors(for_date=V011)
    row = {"provider": "verda", "source": "gpuhunt", "tier": "spot", "gpu_model": "H100_SXM",
           "gpu_count": 8, "price_usd_per_gpu_hr": 1.78, "country": "FI",
           "term": "on_demand", "raw_json": "{}"}
    assert normalise_observations([row], f) == []  # on-demand series: spot never enters
    got = normalise_observations([row], f, tiers=frozenset({"spot"}))
    assert [(o.provider, o.tier) for o in got] == [("verda", "spot")]
    # Nebius's panel entry admits its on-demand tiers only: its spot row never enters, and
    # its on-demand row still does.
    nebius = dict(row, provider="nebius", price_usd_per_gpu_hr=2.15)
    assert normalise_observations([nebius], f, tiers=frozenset({"spot"})) == []
    assert normalise_observations([dict(nebius, tier="list", price_usd_per_gpu_hr=3.85)], f)
    interruptible = dict(row, tier="interruptible")
    assert normalise_observations([interruptible], f, tiers=frozenset({"spot"})) == []


# --- the daily run ------------------------------------------------------------------------


def test_the_daily_run_prints_spot_and_its_spread(conn: sqlite3.Connection) -> None:
    _seed(conn, V011)
    compute_all_series(conn, V011)
    od, sp = _row(conn, V011, "EU-CRI-H100-GLOBAL"), _row(conn, V011, "EU-CRI-H100-SPOT-GLOBAL")
    assert od["value_usd"] is not None and sp["value_usd"] is not None
    assert sp["value_usd"] < od["value_usd"]
    assert _row(conn, V011, "EU-CRI-H100-SPOTSPREAD-GLOBAL")["value_usd"] == round(
        od["value_usd"] - sp["value_usd"], 6)
    cons = {r["provider"]: r["tier"] for r in conn.execute(
        "SELECT provider, tier FROM constituents WHERE date=?"
        " AND series='EU-CRI-H100-SPOT-GLOBAL' AND included=1", (V011,))}
    assert set(cons) == {p for p, *_ in EU_SPOT + WORLD_SPOT}  # Nebius's spot row is not
    assert set(cons.values()) == {"spot"}  # recorded as priced, not relabelled list
    # The EU/EEA has four spot sellers once Nebius is out: a gap with its reason, and so
    # is its spread, while the on-demand headline, Nebius included, prints as before.
    eu_spot = _row(conn, V011, "EU-CRI-H100-SPOT")
    assert eu_spot["value_usd"] is None and eu_spot["n_sources"] == 4
    assert "insufficient_sources" in eu_spot["flags"]
    assert _row(conn, V011, "EU-CRI-H100-SPOTSPREAD")["value_usd"] is None
    headline = {r[0] for r in conn.execute(
        "SELECT provider FROM constituents WHERE date=? AND series='EU-CRI-H100'"
        " AND included=1", (V011,))}
    assert "nebius" in headline
    # US has no spot seller in this fixture: a gap with its reason, and so is its spread.
    us = _row(conn, V011, "EU-CRI-H100-SPOT-US")
    assert us["value_usd"] is None and "insufficient" in us["flags"]
    assert _row(conn, V011, "EU-CRI-H100-SPOTSPREAD-US")["value_usd"] is None


def test_no_spot_print_before_the_version(conn: sqlite3.Connection) -> None:
    day = "2026-10-05"
    _seed(conn, day)
    compute_all_series(conn, day)
    for s in (*SPOT_SERIES, "EU-CRI-H100-SPOTSPREAD"):
        assert _row(conn, day, s) is None
    # The on-demand constituents are still recorded as the tiers they always were.
    tiers = {r[0] for r in conn.execute(
        "SELECT DISTINCT tier FROM constituents WHERE date=? AND series='EU-CRI-H100'"
        " AND included=1", (day,))}
    assert tiers <= {"executable", "list"}


# --- volatility ---------------------------------------------------------------------------


def test_a_log_change_is_never_taken_across_a_gap() -> None:
    d = [date(2026, 10, i) for i in range(1, 6)]
    pts = [(d[0], 2.0), (d[1], 2.2), (d[2], None), (d[3], 2.0), (d[4], 2.0)]
    ch = spot.log_changes(pts, timedelta(days=1))
    assert ch == pytest.approx([math.log(1.1), 0.0])
    # A missing calendar day is a gap too, not one long change.
    assert spot.log_changes([(d[0], 2.0), (d[2], 3.0)], timedelta(days=1)) == []


def test_volatility_needs_enough_changes_and_annualises_by_root_365() -> None:
    thin = spot.annualised([0.01, -0.01], 365, needed=5)
    assert thin.value is None and thin.n_changes == 2
    ch = [0.01, -0.01, 0.02, -0.02, 0.0, 0.01]
    v = spot.annualised(ch, 365, needed=5)
    sd = (sum((x - sum(ch) / 6) ** 2 for x in ch) / 5) ** 0.5
    assert v.per_period == pytest.approx(sd) and v.value == pytest.approx(sd * math.sqrt(365))


def test_the_spread_is_measured_in_dollars_because_it_can_reach_zero() -> None:
    vals = [spot.DayValue(f"2026-10-0{i}", v, 2, "", "print")
            for i, v in enumerate([0.5, 0.0, 0.2, 0.4, 0.1, 0.3, 0.3], start=1)]
    vol = spot.daily_vol(vals, "2026-10-07", level=True)["7d"]
    assert vol.n_changes == 6 and vol.value is not None


# --- replay and page ----------------------------------------------------------------------


def test_before_the_version_values_are_replayed_and_marked_indicative(
        conn: sqlite3.Connection) -> None:
    day = "2026-10-01"
    _seed(conn, day)
    replay = spot._Replay(conn)
    got = spot.daily(conn, "EU-CRI-H100-SPOT-GLOBAL", [day], replay)[0]
    assert got.status == "indicative" and got.value is not None
    # A replayed gap is a gap too: EU spot has four sellers, and says so.
    eu = spot.daily(conn, "EU-CRI-H100-SPOT", [day], replay)[0]
    assert eu.value is None and "insufficient_sources" in eu.flags
    # After the version with no stored print, the day stays empty: never back-filled.
    after = spot.daily(conn, "EU-CRI-H100-SPOT-GLOBAL", ["2026-10-07"], replay)[0]
    assert after.value is None and after.status == "none"


def test_seller_discounts_pair_the_same_region_and_size(conn: sqlite3.Connection) -> None:
    day = V011
    run = insert_run(conn, utc_date=day)
    # Same seller, two regions: a Finnish spot rate must not be compared with a Belgian list.
    _obs(conn, run, day, "gcp", "gpuhunt", "spot", 1.10, "FI", "europe-north1-c")
    _obs(conn, run, day, "gcp", "gpuhunt", "list", 12.18, "FI", "europe-north1-c")
    _obs(conn, run, day, "gcp", "gpuhunt", "spot", 7.69, "NL", "europe-west4-b")
    _obs(conn, run, day, "gcp", "gpuhunt", "list", 5.53, "NL", "europe-west4-b")
    table, cats, _outside = spot.sellers(conn, spot.REGIONS[0], day, spot._Replay(conn))
    gcp = next(r for r in table if r["provider"] == "gcp")
    assert gcp["pairs"] == 2 and gcp["spot_above_on_demand"] == 1
    fi, nl = 1 - 1.10 / 12.18, 1 - 7.69 / 5.53
    assert gcp["discount_pct"] == pytest.approx((fi + nl) / 2 * 100, abs=0.01)
    assert {c["segment"] for c in cats} == {"hyperscaler", "neocloud", "marketplace"}


def test_the_page_and_its_files(conn: sqlite3.Connection, tmp_path: Path) -> None:
    from tci.outputs import site, spot_page

    for i, day in enumerate(("2026-10-05", V011)):
        _seed(conn, day, run_id=f"r{i}")
        compute_all_series(conn, day)
    views = spot.build(conn, V011)
    written = spot_page.write_data(views, spot_page.datetime(2026, 10, 6, 12),
                                   out_dir=tmp_path)
    assert {p.name for p in written} == {"daily.csv", "latest.json"}
    header = (tmp_path / "daily.csv").read_text(encoding="utf-8").splitlines()[0]
    assert header == "date,region,leg,series,value_usd,n_sources,status,flags"
    html = spot_page.render(site.build_context(conn), views, built=None)
    for key in ("eu", "us", "global"):
        assert html.count(f'<div class="rpanel rpanel--{key}">') == 3  # charts, vol, sellers
    assert "Not built" not in html and "could not be built" not in html
    assert 'pathLength="1"' in html
    assert "region-global" in html


def test_spot_has_a_nav_tab_and_stays_off_the_intraday_family_section() -> None:
    from tci import intraday
    from tci.outputs import site

    assert ("spot.html", "Spot") in site.NAV
    series = intraday.load_config().series
    assert set(SPOT_SERIES) <= set(series)  # replayed hourly, for the spot page


def test_intake_agrees_that_nebius_spot_is_refused_and_its_on_demand_is_not() -> None:
    from tci import intake

    f = load_factors(for_date=V011)
    base = {"provider": "nebius", "source": "gpuhunt", "gpu_model": "H100_SXM",
            "gpu_count": 8, "country": "FI", "term": "on_demand", "raw_json": "{}"}
    spot_row = dict(base, tier="spot", price_usd_per_gpu_hr=2.15)
    od_row = dict(base, tier="list", price_usd_per_gpu_hr=3.85)
    assert intake.classify(spot_row, f, None, f.eu_eea_countries, "EU_EEA",
                           frozenset({"spot"})).gate == "not_in_panel"
    assert intake.classify(od_row, f, None, f.eu_eea_countries).gate == "admitted"
    # Frozen versions carry no tier restriction, so nothing earlier changes.
    before = load_factors(for_date="2026-10-05")
    assert all(e.tiers is None for e in (before.panel or {}).values())
