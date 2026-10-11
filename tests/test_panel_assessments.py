"""The EU term panel: its rules hold in code, and what leaves the store names nobody."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

from tci import panel

REPO = Path(__file__).resolve().parents[1]
WINDOW = "2026-W42"  # Monday 12 October to the cutoff Friday 16 October 12:00 UTC
ON_TIME = "2026-10-14T09:00:00Z"
LATE = "2026-10-16T12:30:00Z"
AFTER = "2026-10-20T09:00:00Z"  # when the window is aggregated


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    monkeypatch.setenv("TCI_PRIVATE_DIR", str(tmp_path / "private"))
    return panel.connect_private()


def _join(store: sqlite3.Connection, name: str, kind: str = "broker") -> None:
    panel.register(store, name, kind, "active", ON_TIME, "none declared", "ab" * 32)


def _assess(cell: str = "EU-H100-12M-8", low: float = 2.4, high: float = 2.6,
            currency: str = "USD", basis: str = "judgement") -> panel.Assessment:
    return panel.Assessment(WINDOW, cell, low, high, currency, basis)


def _cell(cells: list[panel.PanelCell], cell_id: str = "EU-H100-12M-8") -> panel.PanelCell:
    return next(c for c in cells if c.id == cell_id)


def _agg(store: sqlite3.Connection, fx: float | None = 1.12) -> panel.PanelCell:
    return _cell(panel.aggregate(store, WINDOW, fx, now_utc=AFTER))


def test_an_unsigned_or_inactive_panellist_cannot_submit(store: sqlite3.Connection) -> None:
    with pytest.raises(panel.PanelError, match="signed agreement"):
        panel.register(store, "B1", "broker", "active", ON_TIME)
    with pytest.raises(panel.PanelError, match="not an active panellist"):
        panel.ingest(store, "B1", [_assess()], "x", ON_TIME)
    _join(store, "B1")
    panel.register(store, "B1", "broker", "suspended", ON_TIME)
    with pytest.raises(panel.PanelError, match="not an active panellist"):
        panel.ingest(store, "B1", [_assess()], "x", ON_TIME)


def test_one_voice_per_cell_per_week(store: sqlite3.Connection) -> None:
    _join(store, "B1")
    panel.ingest(store, "B1", [_assess()], "x", ON_TIME)
    with pytest.raises(panel.PanelError, match="already assessed"):
        panel.ingest(store, "B1", [_assess(low=2.5)], "y", ON_TIME)
    first = store.execute("SELECT id FROM assessments").fetchone()[0]
    panel.ingest(store, "B1", [_assess(low=2.5, high=2.7)], "y", ON_TIME, supersedes=first)
    with pytest.raises(sqlite3.DatabaseError):
        store.execute("UPDATE assessments SET low = 1.0")


def test_below_three_a_cell_is_a_count(store: sqlite3.Connection) -> None:
    for name in ("B1", "B2"):
        _join(store, name)
        panel.ingest(store, name, [_assess()], "x", ON_TIME)
    c = _agg(store)
    assert not c.published and c.value_usd is None and c.panellists == 2


def test_three_give_a_mean_and_five_a_trimmed_mean(store: sqlite3.Connection) -> None:
    mids = [(2.0, 2.2), (2.4, 2.6), (2.9, 3.1)]
    for i, (lo, hi) in enumerate(mids):
        _join(store, f"B{i}")
        panel.ingest(store, f"B{i}", [_assess(low=lo, high=hi)], "x", ON_TIME)
    c = _agg(store)
    assert c.published and c.value_usd == 2.53
    # A median of three would have published B1's own mid, $2.50, to the cent.
    assert c.value_usd not in (2.1, 2.5, 3.0)
    for i, (lo, hi) in enumerate([(1.0, 1.2), (9.0, 9.4)], start=3):
        _join(store, f"B{i}")
        panel.ingest(store, f"B{i}", [_assess(low=lo, high=hi)], "x", ON_TIME)
    c = _agg(store)
    assert c.value_usd == pytest.approx((2.1 + 2.5 + 3.0) / 3, abs=0.01)
    assert c.method and "trimmed" in c.method


def test_a_late_assessment_is_stored_and_not_counted(store: sqlite3.Connection) -> None:
    for i in range(3):
        _join(store, f"B{i}")
    panel.ingest(store, "B0", [_assess()], "x", ON_TIME)
    panel.ingest(store, "B1", [_assess()], "x", ON_TIME)
    panel.ingest(store, "B2", [_assess()], "x", LATE)
    c = _agg(store)
    assert not c.published and c.late == 1 and c.panellists == 2


def test_sellers_cannot_be_half_the_panel(store: sqlite3.Connection) -> None:
    for name, kind in (("S1", "seller"), ("S2", "seller"), ("B1", "broker"), ("B2", "buyer")):
        _join(store, name, kind)
        panel.ingest(store, name, [_assess()], "x", ON_TIME)
    c = _agg(store)
    assert not c.published and "sellers" in (c.reason or "")


def test_eur_is_converted_and_never_guessed(store: sqlite3.Connection) -> None:
    for i in range(3):
        _join(store, f"B{i}")
        panel.ingest(store, f"B{i}", [_assess(low=2.0, high=2.0, currency="EUR")], "x",
                     ON_TIME)
    assert _agg(store, 1.1).value_usd == 2.2
    assert not _agg(store, None).published


def test_nothing_published_names_a_panellist(store: sqlite3.Connection) -> None:
    for i in range(3):
        _join(store, f"Secret-Firm-{i}")
        panel.ingest(store, f"Secret-Firm-{i}", [_assess()], "x", ON_TIME)
    text = panel.to_json(panel.publishable(
        panel.aggregate(store, WINDOW, 1.12, now_utc=AFTER), WINDOW))
    assert "Secret-Firm" not in text
    assert "2.4" not in text.replace("2.40", "")  # no single input survives


def test_a_late_correction_cannot_withdraw_an_assessment_that_counted(
    store: sqlite3.Connection,
) -> None:
    for i in range(3):
        _join(store, f"B{i}")
        panel.ingest(store, f"B{i}", [_assess()], "x", ON_TIME)
    assert _agg(store).published
    first = store.execute("SELECT id FROM assessments WHERE panellist = 'B2'").fetchone()[0]
    panel.ingest(store, "B2", [_assess(low=2.6, high=2.8)], "y", AFTER, supersedes=first)
    c = _agg(store)
    assert c.published and c.panellists == 3 and c.value_usd == 2.5 and c.late == 1


def test_one_week_has_one_spelling(store: sqlite3.Connection) -> None:
    assert panel.normalise_window("2027-W1") == panel.normalise_window("2027-W001") == "2027-W01"
    for i, spelling in enumerate(("2027-W1", "2027-W01", "2027-W001")):
        _join(store, f"B{i}")
        a = panel.Assessment(spelling, "EU-H100-12M-8", 2.4, 2.6, "USD", "judgement")
        panel.ingest(store, f"B{i}", [a], "x", "2027-01-05T09:00:00Z")
    late = panel.Assessment("2027-W1", "EU-H100-12M-8", 2.0, 2.2, "USD", "judgement")
    with pytest.raises(panel.PanelError, match="already assessed"):
        panel.ingest(store, "B0", [late], "x", "2027-01-05T10:00:00Z")
    c = _cell(panel.aggregate(store, "2027-W1", 1.12, now_utc="2027-01-11T00:00:00Z"))
    assert c.published and c.panellists == 3 and c.window == "2027-W01"
    assert panel.window_bounds("2026-W53", panel.load_config().rules) == (
        "2026-12-28T00:00:00Z", "2027-01-01T12:00:00Z")


def test_a_suspension_reaches_back_and_stays(store: sqlite3.Connection) -> None:
    for i in range(3):
        _join(store, f"B{i}")
        panel.ingest(store, f"B{i}", [_assess()], "x", ON_TIME)
    panel.register(store, "B2", "broker", "suspended", AFTER)
    assert _agg(store).panellists == 2
    # Leaving or being reinstated does not bring the earlier weeks back.
    panel.register(store, "B2", "broker", "left", "2026-10-21T09:00:00Z")
    assert _agg(store).panellists == 2
    panel.register(store, "B2", "broker", "active", "2026-10-22T09:00:00Z", None, "ab" * 32)
    assert _agg(store).panellists == 2
    # A week that opens after the reinstatement counts as normal.
    later = panel.Assessment("2026-W44", "EU-H100-12M-8", 2.4, 2.6, "USD", "judgement")
    for i in range(3):
        panel.ingest(store, f"B{i}", [later], "y", "2026-10-28T09:00:00Z")
    c = _cell(panel.aggregate(store, "2026-W44", 1.12, now_utc="2026-11-02T00:00:00Z"))
    assert c.published and c.panellists == 3


def test_no_percentile_is_published(store: sqlite3.Connection) -> None:
    # At five, the 25th and 75th percentiles are exactly the second and fourth mids.
    for i, mid in enumerate((2.1, 2.5, 2.9, 3.3, 3.7)):
        _join(store, f"B{i}")
        panel.ingest(store, f"B{i}", [_assess(low=mid - 0.1, high=mid + 0.1)], "x", ON_TIME)
    text = panel.to_json(panel.publishable(
        panel.aggregate(store, WINDOW, 1.12, now_utc=AFTER), WINDOW))
    numbers: list[float] = []

    def walk(x: object) -> None:
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, float):
            numbers.append(x)

    walk(json.loads(text))
    assert not {2.1, 2.5, 3.3, 3.7} & set(numbers)
    assert "p25" not in text and "p75" not in text


def test_no_value_exists_before_the_cutoff(store: sqlite3.Connection) -> None:
    for i in range(3):
        _join(store, f"B{i}")
        panel.ingest(store, f"B{i}", [_assess()], "x", ON_TIME)
    with pytest.raises(panel.PanelError, match="closes at"):
        panel.aggregate(store, WINDOW, 1.12, now_utc="2026-10-16T11:59:59Z")


def test_validation_refuses_what_is_not_an_assessment(tmp_path: Path) -> None:
    def check(row: str) -> None:
        f = tmp_path / "a.csv"
        f.write_text("window,cell,low,high,currency,basis,deals_seen,notes\n" + row + "\n")
        panel.read_file(f)

    check(f"{WINDOW},EU-H100-12M-8,2.40,2.70,USD,judgement,,")
    for bad, why in (
        (f"{WINDOW},EU-H100-12M-8,2.0,3.5,USD,judgement,,", "wider than"),
        (f"{WINDOW},EU-H100-12M-9,2.0,2.2,USD,judgement,,", "unknown cell"),
        (f"{WINDOW},EU-H100-12M-8,2.0,2.2,USD,transacted,,", "deals_seen"),
        ("2026-42,EU-H100-12M-8,2.0,2.2,USD,judgement,,", "ISO week"),
    ):
        with pytest.raises(panel.PanelError, match=why):
            check(bad)
    with pytest.raises(panel.PanelError, match="example row"):
        panel.read_file(REPO / "contrib" / "panel-template.csv")


def test_the_published_rules_match_the_code() -> None:
    cfg = panel.load_config()
    doc = (REPO / "TERM-PANEL.md").read_text(encoding="utf-8")
    assert cfg.rules.min_panellists == 3 and "A value needs three panellists" in doc
    assert "it is the mean of their" in doc and "never a median of three" in doc
    assert cfg.rules.trim_from == 5 and "From five, the highest and lowest" in doc
    assert cfg.rules.max_range_ratio == 1.5 and "wider than 1.5 times" in doc
    assert cfg.rules.max_seller_share == 0.5 and "fewer than half" in doc
    assert cfg.rules.cutoff_weekday == 5 and cfg.rules.cutoff_hour_utc == 12
    assert "Friday 12:00 UTC" in doc
    for c in cfg.cells:
        assert f"`{c.id}`" in doc
    assert panel.window_bounds(WINDOW, cfg.rules) == (
        "2026-10-12T00:00:00Z", "2026-10-16T12:00:00Z")
    assert re.fullmatch(r"\d{4}-W\d{2}", panel.window_of("2026-10-14"))


def test_the_empty_panel_lists_every_cell() -> None:
    data = panel.empty()
    assert [c["id"] for c in data["cells"]] == [c.id for c in panel.load_config().cells]
    assert all(not c["published"] for c in data["cells"])
    json.dumps(data)
