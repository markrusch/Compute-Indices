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
    c = _cell(panel.aggregate(store, WINDOW, 1.12))
    assert not c.published and c.value_usd is None and c.panellists == 2


def test_three_give_a_median_and_five_a_trimmed_mean(store: sqlite3.Connection) -> None:
    mids = [(2.0, 2.2), (2.4, 2.6), (2.9, 3.1)]
    for i, (lo, hi) in enumerate(mids):
        _join(store, f"B{i}")
        panel.ingest(store, f"B{i}", [_assess(low=lo, high=hi)], "x", ON_TIME)
    c = _cell(panel.aggregate(store, WINDOW, 1.12))
    assert c.published and c.value_usd == 2.5 and c.p25_usd is None
    for i, (lo, hi) in enumerate([(1.0, 1.2), (9.0, 9.4)], start=3):
        _join(store, f"B{i}")
        panel.ingest(store, f"B{i}", [_assess(low=lo, high=hi)], "x", ON_TIME)
    c = _cell(panel.aggregate(store, WINDOW, 1.12))
    assert c.value_usd == pytest.approx((2.1 + 2.5 + 3.0) / 3, abs=0.01)
    assert c.p25_usd is not None and c.method and "trimmed" in c.method


def test_a_late_assessment_is_stored_and_not_counted(store: sqlite3.Connection) -> None:
    for i in range(3):
        _join(store, f"B{i}")
    panel.ingest(store, "B0", [_assess()], "x", ON_TIME)
    panel.ingest(store, "B1", [_assess()], "x", ON_TIME)
    panel.ingest(store, "B2", [_assess()], "x", LATE)
    c = _cell(panel.aggregate(store, WINDOW, 1.12))
    assert not c.published and c.late == 1 and c.panellists == 2


def test_sellers_cannot_be_half_the_panel(store: sqlite3.Connection) -> None:
    for name, kind in (("S1", "seller"), ("S2", "seller"), ("B1", "broker"), ("B2", "buyer")):
        _join(store, name, kind)
        panel.ingest(store, name, [_assess()], "x", ON_TIME)
    c = _cell(panel.aggregate(store, WINDOW, 1.12))
    assert not c.published and "sellers" in (c.reason or "")


def test_eur_is_converted_and_never_guessed(store: sqlite3.Connection) -> None:
    for i in range(3):
        _join(store, f"B{i}")
        panel.ingest(store, f"B{i}", [_assess(low=2.0, high=2.0, currency="EUR")], "x",
                     ON_TIME)
    assert _cell(panel.aggregate(store, WINDOW, 1.1)).value_usd == 2.2
    assert not _cell(panel.aggregate(store, WINDOW, None)).published


def test_nothing_published_names_a_panellist(store: sqlite3.Connection) -> None:
    for i in range(3):
        _join(store, f"Secret-Firm-{i}")
        panel.ingest(store, f"Secret-Firm-{i}", [_assess()], "x", ON_TIME)
    text = panel.to_json(panel.publishable(panel.aggregate(store, WINDOW, 1.12), WINDOW))
    assert "Secret-Firm" not in text
    assert "2.4" not in text.replace("2.40", "")  # no single input survives


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
