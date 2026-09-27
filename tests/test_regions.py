# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""EU, US and Global: the GLOBAL block (v0.9.0), the regional guard in the daily run,
indicative values in the intraday replay, and the region tabs on the site."""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tci import commands, db, intraday
from tci.commands import compute_all_series
from tci.config import load_factors
from tests.conftest import insert_run
from tests.test_basis import _insert
from tests.test_intraday import FakeCollector, _cfg, _obs, _sweep

REPO_ROOT = Path(__file__).resolve().parents[1]
REGIONAL = ("EU-CRI-H100", "EU-CRI-H100-US", "EU-CRI-H100-GLOBAL")


# --- the GLOBAL block ---------------------------------------------------------------------


def test_global_block_is_every_country_the_other_blocks_name() -> None:
    f = load_factors(for_date="2026-10-04")
    world = f.countries_of("GLOBAL")
    assert all(re.fullmatch(r"[A-Z]{2}", c) for c in world)
    assert len(world) == len(set(world)) >= 240
    assert set(f.eu_eea_countries) <= set(world)
    assert set(f.countries_of("US")) <= set(world)


def test_every_country_ever_observed_is_in_the_global_block() -> None:
    """A country missing from the list would silently drop its offers from GLOBAL."""
    world = set(load_factors(for_date="2026-10-04").countries_of("GLOBAL"))
    conn = db.connect_readonly(REPO_ROOT / "data" / "eucri.db")
    seen = {r[0] for r in conn.execute(
        "SELECT DISTINCT country FROM observations WHERE country IS NOT NULL AND country != ''")}
    assert seen, "the committed database has no observations"
    assert seen <= world, f"observed but not in GLOBAL: {sorted(seen - world)}"


@pytest.mark.parametrize(("day", "us", "world"), [
    ("2026-09-30", False, False), ("2026-10-01", True, False),
    ("2026-10-03", True, False), ("2026-10-04", True, True),
])
def test_each_regional_series_starts_on_its_notice_date(day: str, us: bool, world: bool) -> None:
    rs = load_factors(for_date=day).regional_series
    assert ("EU-CRI-H100-US" in rs) is us
    assert ("EU-CRI-H100-GLOBAL" in rs) is world


# --- the daily run ------------------------------------------------------------------------

EU_ROWS = [("vast.ai", "vast_ai", "executable", 2.27), ("runpod", "runpod", "executable", 3.49),
           ("verda", "gpuhunt", "list", 3.25), ("nebius", "gpuhunt", "list", 3.85),
           ("seeweb", "static_yaml", "list", 2.16), ("lambdalabs", "gpuhunt", "list", 3.99)]
US_ROWS = [("vast.ai", "vast_ai", "executable", 1.95), ("runpod", "runpod", "executable", 3.49),
           ("lambdalabs", "gpuhunt", "list", 3.99),
           ("digitalocean", "digitalocean", "list", 4.41),
           ("voltagepark", "voltagepark", "list", 1.99)]


def _seed(conn: sqlite3.Connection, day: str) -> None:
    run = insert_run(conn, utc_date=day)
    for p, s, t, price in EU_ROWS:
        _insert(conn, run, day, p, s, t, price, "NL")
    for p, s, t, price in US_ROWS:
        _insert(conn, run, day, p, s, t, price, "US")
    conn.execute("INSERT INTO fx (date, eur_usd, source) VALUES (?, 1.16, 't')",
                 ((datetime.fromisoformat(day) - timedelta(days=1)).date().isoformat(),))


def _row(conn: sqlite3.Connection, day: str, series: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM daily_index WHERE date=? AND series=?"
                        " ORDER BY revision DESC LIMIT 1", (day, series)).fetchone()


def test_global_prints_over_both_blocks_from_its_effective_date(
        conn: sqlite3.Connection) -> None:
    day = "2026-10-04"
    _seed(conn, day)
    compute_all_series(conn, day)
    row = _row(conn, day, "EU-CRI-H100-GLOBAL")
    assert row is not None and row["value_usd"] is not None
    cons = {r["provider"] for r in conn.execute(
        "SELECT provider FROM constituents WHERE date=? AND series='EU-CRI-H100-GLOBAL'"
        " AND included=1", (day,))}
    assert {"verda", "voltagepark"} <= cons  # an EU-only and a US-only seller
    for series in ("EU-CRI-H100", "EU-CRI-H100-US"):
        assert _row(conn, day, series)["value_usd"] is not None


def test_no_global_print_before_its_version(conn: sqlite3.Connection) -> None:
    day = "2026-10-03"
    _seed(conn, day)
    compute_all_series(conn, day)
    assert _row(conn, day, "EU-CRI-H100-GLOBAL") is None
    assert _row(conn, day, "EU-CRI-H100-US")["value_usd"] is not None


def test_a_regional_series_that_raises_is_stored_as_a_gap_and_costs_nothing_else(
        conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    day = "2026-10-04"
    _seed(conn, day)
    real = commands.regional_print

    def broken(rows, factors, fx, utc_date, series, rs, prev_prices=None):  # type: ignore[no-untyped-def]
        if series == "EU-CRI-H100-US":
            raise ValueError("a malformed block")
        return real(rows, factors, fx, utc_date, series, rs, prev_prices=prev_prices)

    monkeypatch.setattr(commands, "regional_print", broken)
    compute_all_series(conn, day)
    us = _row(conn, day, "EU-CRI-H100-US")
    assert us is not None and us["value_usd"] is None
    assert "computation_failed" in us["flags"]
    # The basis gaps on its missing leg; everything computed after the failure is stored.
    assert _row(conn, day, "EU-CRI-H100-BASIS-US")["value_usd"] is None
    assert _row(conn, day, "EU-CRI-H100-GLOBAL")["value_usd"] is not None
    assert _row(conn, day, "EU-CRI-H100")["value_usd"] is not None
    assert _row(conn, day, "EU-CRI-H100-7D") is not None


# --- the intraday replay ------------------------------------------------------------------


def _two_block_store(tmp_path: Path, at: datetime) -> intraday.Store:
    store = intraday.Store(tmp_path)
    book = ([_obs(f"eu{i}", 2.0 + i / 10) for i in range(5)]
            + [_obs(f"us{i}", 3.0 + i / 10, country="US") for i in range(5)])
    _sweep(store, _cfg(series=REGIONAL), [FakeCollector("fake", [book])], at)
    return store


@pytest.mark.usefixtures("unpanelled")
@pytest.mark.parametrize(("at", "us_ind", "world_ind"), [
    (datetime(2026, 9, 26, 6, 17, tzinfo=UTC), True, True),
    (datetime(2026, 10, 2, 6, 17, tzinfo=UTC), False, True),
    (datetime(2026, 10, 24, 6, 17, tzinfo=UTC), False, False),
])
def test_replay_prices_every_region_and_marks_the_ones_not_yet_in_effect(
        tmp_path: Path, at: datetime, us_ind: bool, world_ind: bool) -> None:
    store = _two_block_store(tmp_path, at)
    snap = intraday.path(store, None, _cfg(series=REGIONAL), at - timedelta(hours=1),
                         at + timedelta(hours=1))[0]
    eu, us, world = (snap.values[s] for s in REGIONAL)
    assert eu[0] == 2.2 and "indicative" not in eu[3]
    assert us[0] == 3.2 and ("indicative" in us[3]) is us_ind
    assert world[2] == 10 and ("indicative" in world[3]) is world_ind
    # Each region's constituents come from its own block.
    assert {p for p, _v, _w in snap.constituents["EU-CRI-H100-US"]} == {
        f"us{i}" for i in range(5)}


@pytest.mark.usefixtures("unpanelled")
def test_a_regional_failure_in_the_replay_costs_that_series_only(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    at = datetime(2026, 10, 24, 6, 17, tzinfo=UTC)
    store = _two_block_store(tmp_path, at)

    def broken(*_a: object, **_k: object) -> None:
        raise KeyError("GLOBAL")

    monkeypatch.setattr(commands, "regional_print", broken)
    snap = intraday.path(store, None, _cfg(series=REGIONAL), at - timedelta(hours=1),
                         at + timedelta(hours=1))[0]
    assert snap.values["EU-CRI-H100"][0] == 2.2
    for series in REGIONAL[1:]:
        assert snap.values[series][0] is None
        assert snap.values[series][3].startswith("not_computed")


def test_a_series_no_version_defines_is_a_named_gap(tmp_path: Path) -> None:
    calc = intraday._Calc(None)
    assert calc.regional("EU-CRI-H100-MARS", "2026-09-26") is None
    assert calc.regional("EU-CRI-H100-US", "2026-09-26")[2] is True
    assert calc.regional("EU-CRI-H100-US", "2026-10-01")[2] is False


def test_the_shipped_intraday_config_lists_the_regions_first() -> None:
    assert intraday.load_config().series[:3] == REGIONAL


# --- the site -----------------------------------------------------------------------------


def test_region_tabs_are_native_radios_with_eu_selected() -> None:
    from tci.outputs import site

    html = site.region_tabs()
    radios = re.findall(r'<input type="radio" id="region-(\w+)" name="region"[^>]*>', html)
    assert radios == ["eu", "us", "global"]
    assert re.search(r'id="region-eu"[^>]*checked', html)
    assert [m for m in re.findall(r">(EU|US|Global)</label>", html)] == ["EU", "US", "Global"]
    assert "<legend" in html


def test_without_has_every_region_panel_stays_visible() -> None:
    """The panels are hidden only inside @supports, so an old browser shows all three."""
    css = (REPO_ROOT / "site" / "assets" / "site.css").read_text(encoding="utf-8")
    block = css[css.index("@supports selector(:has(a))"):]
    block = block[:block.index("\n}\n")]
    assert ".rpanel { display: none; }" in block
    outside = css.replace(block, "")
    assert not re.search(r"\.rpanel\s*\{[^}]*display:\s*none", outside)
    # Indicative lines are never dashed (DESIGN.md §3).
    ind = re.search(r"\.ch-line--ind\s*\{([^}]*)\}", css)
    assert ind and "dasharray" not in ind.group(1)


def test_series_start_names_the_version_and_notice() -> None:
    from tci.outputs import site

    assert site.series_start("EU-CRI-H100-US") == ("2026-10-01", "0.6.0", "2026-N3")
    assert site.series_start("EU-CRI-H100-GLOBAL") == ("2026-10-04", "0.9.0", "2026-N6")
    assert site.series_start("EU-CRI-H100") is None


def test_indicative_stretches_are_grey_labelled_and_never_joined_to_the_official_line(
) -> None:
    from tci.outputs import intraday_page as ip
    from tci.outputs import site

    t0 = datetime(2026, 9, 30, 20, tzinfo=UTC)
    pts: list[tuple[datetime, float | None]] = [
        (t0 + timedelta(hours=h), 3.0 + h / 100) for h in range(6)]
    ind = frozenset(t for t, _ in pts[:3])
    svg = ip._time_chart(site, pts, [], t0, t0 + timedelta(hours=6), symbol="X",
                         focusable=False, indicative=ind)
    assert svg.count('class="ch-line ch-line--ind"') == 1
    assert svg.count('class="ch-line" pathLength="1"') == 1
    assert svg.count("· indicative") == 3 * 2  # the aria-label and the visible tooltip
    assert "3 indicative" in svg


def _read_only_ctx():  # type: ignore[no-untyped-def]
    from tci.outputs import site

    return site.build_context(db.connect_readonly(REPO_ROOT / "data" / "eucri.db"))


def test_dashboard_region_panels_show_prints_only() -> None:
    from tci.outputs import site

    ctx = _read_only_ctx()
    html = site._region_charts(ctx)
    panels = dict(re.findall(r'<div class="rpanel rpanel--(\w+)">(.*?)(?=<div class="rpanel|$)',
                             html, re.S))
    assert set(panels) == {"eu", "us", "global"}
    for key, series in (("us", "EU-CRI-H100-US"), ("global", "EU-CRI-H100-GLOBAL")):
        if site.latest_print(ctx.conn, series) is None:
            assert "No print yet" in panels[key]
            assert f'href="intraday.html#{key}"' in panels[key]
            assert "$" not in panels[key]  # an indicative value never reaches this page
    assert '<svg class="chart"' in panels["eu"]


def test_ticker_carries_a_region_only_once_it_has_a_print() -> None:
    from tci.outputs import site

    ctx = _read_only_ctx()
    bar = site._ticker(ctx)
    assert site._nbsp_series("EU-CRI-H100") in bar
    for series in ("EU-CRI-H100-US", "EU-CRI-H100-GLOBAL"):
        shown = site._nbsp_series(series) in bar
        assert shown is (site.current_print(ctx.conn, series, ctx.date) is not None)


def test_intraday_page_has_a_panel_per_region() -> None:
    from tci.outputs import intraday_page as ip

    ctx = _read_only_ctx()
    html = ip.render(ctx)
    for key in ("eu", "us", "global"):
        assert html.count(f'<div class="rpanel rpanel--{key}">') == 2  # charts, settlement
    assert "could not be built" not in html and "Not built" not in html
    assert "region-global" in html and "history.replaceState" in html
