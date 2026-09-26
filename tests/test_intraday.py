# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Intraday sweeps: the store keeps what was read, and nothing it holds reaches a print.

Four properties carry the design, and each has a test here:

- the store round-trips exactly, refuses an edited file, and only ever grows;
- the reconstruction is the fixing's own calculation: replayed over the fixing's own reads
  it reproduces the published print, to the digit, on every day those reads were close
  enough together to count;
- a read older than its source's limit drops out, and a window without enough values has
  no settlement value;
- nothing in the intraday path writes to `observations`, so the 11:00 print is untouched.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tci import db, intraday, series_read
from tci.models import Observation

REPO_ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 9, 20, 6, 17, tzinfo=UTC)


def _obs(provider: str, price: float, *, source: str = "fake", count: int | None = 8,
         country: str = "DE", model: str = "H100_SXM", tier: str = "executable",
         raw: dict | None = None) -> Observation:
    return Observation(
        ts_utc="2026-09-20T06:17:00Z", source=source, provider=provider, gpu_model=model,
        gpu_count=count, price_usd_per_gpu_hr=price, region="eu-1", country=country,
        interconnect="NVLink", tier=tier, term="on_demand",
        raw_json=json.dumps(raw or {"noise": "not kept"}),
    )


class FakeCollector:
    def __init__(self, name: str, books: list[list[Observation]] | None = None,
                 fail: bool = False) -> None:
        self.name = name
        self.books = list(books or [])
        self.fail = fail
        self.calls = 0

    def collect(self, session: object) -> list[Observation]:
        self.calls += 1
        if self.fail:
            raise ConnectionError("HTTP 502 from upstream")
        return self.books.pop(0) if len(self.books) > 1 else self.books[0]


def _cfg(sources: dict[str, tuple[float, float]] | None = None,
         series: tuple[str, ...] = ("EU-CRI-H100",), min_points: int = 2
         ) -> intraday.IntradayConfig:
    return intraday.IntradayConfig(
        due_slack_minutes=20,
        sources={k: intraday.Cadence(*v) for k, v in (sources or {"fake": (1, 3)}).items()},
        series=series,
        settlement=intraday.SettlementWindow("08:00", "11:00", "mean", min_points),
        # Off in the mechanics tests, which sweep straight through the late morning; the
        # guard has its own tests below.
        fixing_guard=None,
    )


class Clock:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t


def _sweep(store: intraday.Store, cfg: intraday.IntradayConfig, collectors: list,
           at: datetime, conn: sqlite3.Connection | None = None, force: bool = False
           ) -> intraday.SweepResult:
    return intraday.run_sweep(store, conn, cfg, collectors=collectors, now=Clock(at),
                              force=force, session=object())  # type: ignore[arg-type]


# --- configuration ------------------------------------------------------------------------


def test_the_shipped_config_loads_and_names_only_real_collectors() -> None:
    from tci.commands import collectors_for_daily

    cfg = intraday.load_config()
    names = {c.name for c in collectors_for_daily()}
    assert set(cfg.sources) <= names, f"unknown sources: {set(cfg.sources) - names}"
    # Every source a published print can read is swept, or the intraday line would be a
    # different panel from the fixing and the comparison would mean nothing.
    from tci import config

    factors = config.load_factors()
    panel_sources = {s for e in (factors.panel or {}).values() for s in e.sources}
    assert panel_sources <= set(cfg.sources), panel_sources - set(cfg.sources)
    assert cfg.series[0] == "EU-CRI-H100"


def test_a_read_that_expires_before_the_next_is_due_is_refused(tmp_path: Path) -> None:
    p = tmp_path / "intraday.yaml"
    p.write_text("sources: {x: {every_hours: 6, max_age_hours: 2}}\n"
                 "settlement: {start: '08:00', end: '11:00'}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="max_age_hours"):
        intraday.load_config(p)


def test_calc_raw_keys_cover_every_raw_key_the_normaliser_reads() -> None:
    """A raw_json key normalise.py reads but the store drops would change the intraday
    value silently - e.g. Scaleway's EUR price would be read as USD."""
    source = (REPO_ROOT / "src" / "tci" / "normalise.py").read_text(encoding="utf-8")
    read = set(re.findall(r"raw\.get\(\s*[\"'](\w+)[\"']", source))
    assert read, "found no raw_json reads in normalise.py; has the pattern changed?"
    assert read <= set(intraday.CALC_RAW_KEYS), read - set(intraday.CALC_RAW_KEYS)


# --- the store ----------------------------------------------------------------------------


def test_the_store_round_trips_and_stores_differences(tmp_path: Path) -> None:
    cfg = _cfg()
    base = [_obs(f"p{i}", 2.0 + i / 10) for i in range(40)]
    moved = base[:-1] + [_obs("p39", 9.99)]
    c = FakeCollector("fake", [base, base, moved])
    store = intraday.Store(tmp_path)
    for h in range(3):
        _sweep(store, cfg, [c], T0 + timedelta(hours=h))

    lines = [json.loads(x) for x in store.path_for("2026-09-20").read_text().splitlines()]
    books = [x for x in lines if x["kind"] == "book"]
    # First read in full; the identical second read is only a reference; the third is a
    # one-row difference from the first, not forty rows again.
    assert len(books) == 2
    assert books[0]["base"] is None and len(books[0]["add"]) == 40
    assert books[1]["base"] == books[0]["book"]
    assert len(books[1]["add"]) == 1 and len(books[1]["drop"]) == 1

    fresh = intraday.Store(tmp_path).load("2026-09-20")
    reads = [fresh.books[s.sources["fake"].book][1] for s in fresh.sweeps]
    assert sorted(reads[2]) == sorted(intraday.row_of(o) for o in moved)
    assert reads[0] == reads[1]


def test_duplicate_rows_are_kept_as_duplicates(tmp_path: Path) -> None:
    """Two identical offers are two offers: a set would halve their weight."""
    twin = [_obs("vast.ai", 2.5), _obs("vast.ai", 2.5), _obs("vast.ai", 2.7)]
    store = intraday.Store(tmp_path)
    _sweep(store, _cfg(), [FakeCollector("fake", [twin])], T0)
    log_ = intraday.Store(tmp_path).load("2026-09-20")
    rows = log_.books[log_.sweeps[0].sources["fake"].book][1]
    assert len(rows) == 3


def test_only_the_calculation_part_of_raw_json_is_kept(tmp_path: Path) -> None:
    row = intraday.row_of(_obs("scaleway", 2.1, raw={
        "currency": "EUR", "price_native_per_gpu_hr": 1.9, "offer_url": "https://x"}))
    assert json.loads(row[9]) == {"currency": "EUR", "price_native_per_gpu_hr": 1.9}
    assert intraday.row_of(_obs("x", 2.0))[9] == ""


def test_an_edited_store_is_refused_in_strict_mode_and_left_out_otherwise(
        tmp_path: Path) -> None:
    store = intraday.Store(tmp_path)
    c = FakeCollector("fake", [[_obs("a", 2.0)], [_obs("a", 2.0), _obs("b", 3.0)]])
    _sweep(store, _cfg(), [c], T0)
    _sweep(store, _cfg(), [c], T0 + timedelta(hours=1))
    path = store.path_for("2026-09-20")
    path.write_text(path.read_text().replace("3.0", "2.9"), encoding="utf-8")
    with pytest.raises(intraday.IntradayStoreError, match="does not verify"):
        intraday.Store(tmp_path).load("2026-09-20", strict=True)
    # Tolerant (the default for every reader but `verify`): the sweep before the edit is
    # kept, the edited one and everything after it is not, and the reason is recorded.
    fresh = intraday.Store(tmp_path)
    assert len(fresh.load("2026-09-20").sweeps) == 1
    assert "does not verify" in fresh.problems[path.name]


def test_a_torn_last_line_is_dropped_but_a_bad_middle_line_is_not(tmp_path: Path) -> None:
    store = intraday.Store(tmp_path)
    c = FakeCollector("fake", [[_obs("a", 2.0)]])
    _sweep(store, _cfg(), [c], T0)
    path = store.path_for("2026-09-20")
    good = path.read_text()
    path.write_text(good + '{"v":1,"kind":"book","sou', encoding="utf-8")
    assert len(intraday.Store(tmp_path).load("2026-09-20", strict=True).sweeps) == 1
    path.write_text('{"v":1,"kind":"bo\n' + good, encoding="utf-8")
    with pytest.raises(intraday.IntradayStoreError):
        intraday.Store(tmp_path).load("2026-09-20", strict=True)


def test_a_damaged_segment_is_never_written_after(tmp_path: Path) -> None:
    """Under the first version of the store, one torn line made every later sweep of the
    day fail. Now the writer opens the next segment and the day carries on."""
    store = intraday.Store(tmp_path)
    c = FakeCollector("fake", [[_obs("a", 2.0)], [_obs("a", 2.1)], [_obs("a", 2.2)]])
    _sweep(store, _cfg(), [c], T0)
    first = store.path_for("2026-09-20")
    damaged = first.read_text() + '{"v":1,"kind":"sweep","id"'
    first.write_text(damaged, encoding="utf-8")

    fresh = intraday.Store(tmp_path)
    for h in (1, 2):
        result = _sweep(fresh, _cfg(), [c], T0 + timedelta(hours=h))
        assert result.path == fresh.path_for("2026-09-20", 2)
    assert first.read_text() == damaged, "the damaged segment was written to"
    day = intraday.Store(tmp_path).load("2026-09-20", strict=True)
    assert [sw.sources["fake"].n for sw in day.sweeps] == [1, 1, 1]
    assert [sw.at for sw in day.sweeps] == sorted(sw.at for sw in day.sweeps)


def test_segments_rotate_at_their_size_cap_and_stay_self_contained(tmp_path: Path) -> None:
    store = intraday.Store(tmp_path, max_segment_bytes=1024)
    books = [[_obs(f"p{i}", 2.0 + h / 100 + i / 10) for i in range(8)] for h in range(6)]
    c = FakeCollector("fake", books)
    for h in range(6):
        _sweep(store, _cfg(), [c], T0 + timedelta(hours=h))
    segs = store.segments("2026-09-20")
    assert len(segs) > 1
    assert all(p.parent.name == "2026-09" for p in segs)
    for p in segs:  # each verifies on its own: no delta refers across a segment boundary
        assert intraday.Store(tmp_path).load_segment(p).problem is None
    assert len(intraday.Store(tmp_path).load("2026-09-20", strict=True).sweeps) == 6


def test_appending_never_rewrites_what_is_already_there(tmp_path: Path) -> None:
    store = intraday.Store(tmp_path)
    c = FakeCollector("fake", [[_obs("a", 2.0)], [_obs("a", 2.1)], [_obs("a", 2.2)]])
    path = store.path_for("2026-09-20")
    before = b""
    for h in range(3):
        _sweep(store, _cfg(), [c], T0 + timedelta(hours=h))
        now = path.read_bytes()
        assert now.startswith(before) and len(now) > len(before)
        before = now


# --- scheduling and fail-soft sweeps ------------------------------------------------------


def test_a_source_is_read_only_when_due(tmp_path: Path) -> None:
    cfg = _cfg({"fast": (1, 3), "slow": (6, 13)})
    fast = FakeCollector("fast", [[_obs("a", 2.0)]])
    slow = FakeCollector("slow", [[_obs("b", 3.0)]])
    store = intraday.Store(tmp_path)
    for h in range(7):
        _sweep(store, cfg, [fast, slow], T0 + timedelta(hours=h, minutes=(-8 if h % 2 else 0)))
    assert fast.calls == 7
    assert slow.calls == 2  # 06:17 and 12:17, not the five hours between


def test_nothing_due_writes_nothing(tmp_path: Path) -> None:
    store = intraday.Store(tmp_path)
    c = FakeCollector("fake", [[_obs("a", 2.0)]])
    _sweep(store, _cfg(), [c], T0)
    size = store.path_for("2026-09-20").stat().st_size
    result = _sweep(store, _cfg(), [c], T0 + timedelta(minutes=10))
    assert result.sweep is None and result.not_due == ("fake",)
    assert store.path_for("2026-09-20").stat().st_size == size and c.calls == 1


def test_the_fixing_counts_as_a_read(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """The sweep after the 11:00 run must not ask the same page again minutes later."""
    conn.execute(
        "INSERT INTO runs (run_id, utc_date, source, started_utc, finished_utc, status)"
        " VALUES ('d1', '2026-09-20', 'fake', '2026-09-20T11:09:00Z', '2026-09-20T11:10:00Z',"
        " 'ok')")
    conn.commit()
    c = FakeCollector("fake", [[_obs("a", 2.0)]])
    result = _sweep(intraday.Store(tmp_path), _cfg(), [c],
                    datetime(2026, 9, 20, 11, 17, tzinfo=UTC), conn=conn)
    assert result.sweep is None and c.calls == 0


def test_one_failing_source_is_recorded_and_the_rest_still_read(tmp_path: Path) -> None:
    cfg = _cfg({"good": (1, 3), "bad": (1, 3)})
    store = intraday.Store(tmp_path)
    result = _sweep(store, cfg, [FakeCollector("bad", fail=True),
                                 FakeCollector("good", [[_obs("a", 2.0)]])], T0)
    assert result.sweep is not None
    assert result.sweep.sources["good"].status == "ok"
    bad = intraday.Store(tmp_path).load("2026-09-20").sweeps[0].sources["bad"]
    assert bad.status == "failed" and "502" in (bad.error or "")


class SlowCollector:
    def __init__(self, name: str, seconds: float) -> None:
        self.name = name
        self.seconds = seconds

    def collect(self, session: object) -> list[Observation]:
        import time

        time.sleep(self.seconds)
        return [_obs("late", 2.0)]


def test_a_hung_source_is_abandoned_and_the_rest_are_written(tmp_path: Path) -> None:
    from dataclasses import replace

    cfg = replace(_cfg({"slow": (1, 3), "fast": (1, 3)}), source_timeout_seconds=0.3)
    store = intraday.Store(tmp_path)
    import time

    t0 = time.monotonic()
    result = _sweep(store, cfg, [SlowCollector("slow", 30),
                                 FakeCollector("fast", [[_obs("a", 2.0)]])], T0)
    assert time.monotonic() - t0 < 5, "the sweep waited for the hung source"
    assert result.sweep is not None
    assert result.sweep.sources["fast"].status == "ok"
    slow = result.sweep.sources["slow"]
    assert slow.status == "failed" and "timed out" in (slow.error or "")


def test_the_sweep_budget_bounds_the_whole_sweep(tmp_path: Path) -> None:
    from dataclasses import replace

    cfg = replace(_cfg({"a": (1, 3), "b": (1, 3), "c": (1, 3)}), workers=1,
                  source_timeout_seconds=60, sweep_budget_seconds=0.5)
    import time

    t0 = time.monotonic()
    result = _sweep(intraday.Store(tmp_path), cfg,
                    [SlowCollector("a", 30), SlowCollector("b", 30), SlowCollector("c", 30)],
                    T0)
    assert time.monotonic() - t0 < 5
    assert result.sweep is not None
    errors = {n: r.error for n, r in result.sweep.sources.items()}
    assert "finished" in (errors["a"] or "")  # it was running when the budget ran out
    assert "started" in (errors["b"] or "") and "started" in (errors["c"] or "")


def test_sources_are_read_in_parallel(tmp_path: Path) -> None:
    from dataclasses import replace

    names = {f"s{i}": (1.0, 3.0) for i in range(4)}
    cfg = replace(_cfg(names), workers=4)
    import time

    t0 = time.monotonic()
    result = _sweep(intraday.Store(tmp_path), cfg,
                    [SlowCollector(n, 0.4) for n in names], T0)
    assert time.monotonic() - t0 < 1.2, "four 0.4s reads took as long as running in series"
    assert result.sweep is not None
    assert all(r.status == "ok" for r in result.sweep.sources.values())


def test_malformed_rows_are_dropped_and_counted_not_fatal(tmp_path: Path) -> None:
    bad = [_obs("a", 2.0), _obs("b", float("nan")), _obs("c", float("inf"))]
    result = _sweep(intraday.Store(tmp_path), _cfg(), [FakeCollector("fake", [bad])], T0)
    assert result.sweep is not None
    read = result.sweep.sources["fake"]
    assert read.status == "ok" and read.n == 1 and read.dropped == 2
    text = intraday.Store(tmp_path).path_for("2026-09-20").read_text()
    assert "NaN" not in text and "Infinity" not in text


def test_a_failing_source_backs_off_and_one_success_resets_it(tmp_path: Path) -> None:
    cfg = _cfg({"flaky": (1, 3)})
    c = FakeCollector("flaky", fail=True)
    store = intraday.Store(tmp_path)
    for h in range(12):
        _sweep(store, cfg, [c], T0 + timedelta(hours=h))
    # Asked at 0h, then 1h, 2h and 4h apart: 0, 1, 3, 7 - not twelve times.
    assert c.calls == 4
    states = intraday.source_states(store, None, cfg, T0 + timedelta(hours=12))
    assert states["flaky"].failures == 4 and "502" in (states["flaky"].last_error or "")

    c.fail, c.books = False, [[_obs("a", 2.0)]]
    _sweep(store, cfg, [c], T0 + timedelta(hours=15))
    assert c.calls == 5
    for h in (16, 17):
        _sweep(store, cfg, [c], T0 + timedelta(hours=h))
    assert c.calls == 7, "a success did not reset the backoff to the plain cadence"


def test_backoff_is_capped() -> None:
    cfg = _cfg({"x": (1, 3)})
    state = intraday.SourceState(T0, None, 40)
    due = intraday.next_due(cfg, "x", state)
    assert due is not None and due - T0 <= timedelta(hours=24)


def test_a_second_sweep_does_not_run_beside_the_first(tmp_path: Path) -> None:
    store = intraday.Store(tmp_path)
    with store.lock(), pytest.raises(intraday.StoreBusy):
        _sweep(intraday.Store(tmp_path), _cfg(), [FakeCollector("fake", [[_obs("a", 2)]])],
               T0)
    # Released afterwards, and a lock left by a dead process is taken over once stale.
    assert not (tmp_path / ".lock").exists()
    (tmp_path / ".lock").write_text("12345 2026-01-01T00:00:00Z\n")
    import os

    os.utime(tmp_path / ".lock", (0, 0))
    assert _sweep(store, _cfg(), [FakeCollector("fake", [[_obs("a", 2)]])], T0).sweep


def test_unreadable_scheduling_state_means_read_everything(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        raise sqlite3.DatabaseError("database disk image is malformed")

    monkeypatch.setattr(intraday, "source_states", broken)
    c = FakeCollector("fake", [[_obs("a", 2.0)]])
    assert _sweep(intraday.Store(tmp_path), _cfg(), [c], T0).sweep is not None
    assert c.calls == 1


def test_a_broken_record_costs_the_fixing_reads_not_the_path(tmp_path: Path) -> None:
    broken = sqlite3.connect(":memory:")  # no tables at all
    broken.row_factory = sqlite3.Row
    assert intraday.fixing_reads(broken, T0, T0 + timedelta(hours=1), ["fake"]) == []
    store = intraday.Store(tmp_path)
    _sweep(store, _cfg(), [FakeCollector("fake", [[_obs("a", 2.0)]])], T0, conn=broken)
    assert intraday.path(store, broken, _cfg(), T0 - timedelta(hours=1),
                         T0 + timedelta(hours=1))


@pytest.mark.usefixtures("unpanelled")
def test_a_point_that_cannot_be_computed_is_a_gap_not_a_crash(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _panel_store(tmp_path, [[2.0, 2.2, 2.4, 2.6, 2.8]] * 3)
    real = intraday.compute_snapshot
    calls = {"n": 0}

    def flaky(*args: Any, **kwargs: Any) -> intraday.Snapshot:
        calls["n"] += 1
        if calls["n"] == 2:
            raise ZeroDivisionError("bad read")
        return real(*args, **kwargs)

    monkeypatch.setattr(intraday, "compute_snapshot", flaky)
    snaps = intraday.path(store, None, _cfg(), T0, T0 + timedelta(hours=3))
    assert [s.values["EU-CRI-H100"][0] for s in snaps] == [2.4, None, 2.4]
    assert snaps[1].values["EU-CRI-H100"][3] == "not_computed: ZeroDivisionError"


def test_the_daily_site_build_survives_an_intraday_failure(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The 11:00 run renders this page with every other. Whatever goes wrong in here must
    cost this page at most, and never the fixing's site."""
    from tci.outputs import intraday_page, site

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("store unreadable")

    conn = db.connect_readonly(REPO_ROOT / "data" / "eucri.db")
    ctx = site.build_context(conn)
    monkeypatch.setattr(intraday_page, "build", boom)
    assert "could not be built" in site._intraday(ctx)  # the page says so
    monkeypatch.setattr(intraday_page, "render", boom)
    assert site._intraday(ctx) == ""  # generate() then keeps the last good page


def _guarded(sources: dict[str, tuple[float, float]] | None = None) -> intraday.IntradayConfig:
    from dataclasses import replace

    return replace(_cfg(sources), fixing_guard=("10:40", "13:00"))


def test_the_fixing_guard_holds_sources_until_the_fixing_has_asked_them(
        tmp_path: Path, conn: sqlite3.Connection) -> None:
    """GitHub starts the 11:00 job late. An hourly sweep at 11:17 must not reach vast.ai
    before it does."""
    cfg = _guarded({"vast_ai": (1, 3), "runpod": (1, 3)})
    vast = FakeCollector("vast_ai", [[_obs("a", 2.0)]])
    runpod = FakeCollector("runpod", [[_obs("b", 2.0)]])
    store = intraday.Store(tmp_path)
    at = datetime(2026, 9, 20, 11, 17, tzinfo=UTC)

    result = _sweep(store, cfg, [vast, runpod], at, conn=conn)
    assert result.sweep is None and vast.calls == runpod.calls == 0

    # The fixing has now asked runpod (and failed on vast.ai, which still counts as asked).
    for run_id, source, status in (("d1", "runpod", "ok"), ("d2", "vast_ai", "failed")):
        conn.execute(
            "INSERT INTO runs (run_id, utc_date, source, started_utc, finished_utc, status)"
            " VALUES (?, '2026-09-20', ?, '2026-09-20T11:24:00Z', '2026-09-20T11:25:00Z', ?)",
            (run_id, source, status))
    conn.commit()
    # Once the fixing has asked a source, the guard lets it go and the ordinary cadence
    # decides: 22 minutes after the fixing's reads, neither is due yet.
    assert intraday.fixing_guard_holds(cfg, conn, at + timedelta(minutes=30)) == set()
    assert _sweep(store, cfg, [vast, runpod], at + timedelta(minutes=30), conn=conn).sweep is None
    _sweep(store, cfg, [vast, runpod], at + timedelta(hours=1), conn=conn)
    assert vast.calls == runpod.calls == 1


def test_the_fixing_guard_holds_everything_without_the_record(tmp_path: Path) -> None:
    cfg = _guarded()
    c = FakeCollector("fake", [[_obs("a", 2.0)]])
    assert _sweep(intraday.Store(tmp_path), cfg, [c],
                  datetime(2026, 9, 20, 10, 45, tzinfo=UTC)).sweep is None
    # Outside the window it reads normally, record or not.
    assert _sweep(intraday.Store(tmp_path), cfg, [c],
                  datetime(2026, 9, 20, 10, 17, tzinfo=UTC)).sweep is not None
    assert c.calls == 1


def test_the_shipped_guard_covers_the_fixing() -> None:
    cfg = intraday.load_config()
    assert cfg.fixing_guard is not None
    start, end = cfg.fixing_guard
    assert start < "11:00" < end and end >= "12:00", cfg.fixing_guard


def test_a_rate_limited_source_is_left_to_the_fixing(tmp_path: Path) -> None:
    class Limited(FakeCollector):
        def collect(self, session: object) -> list[Observation]:
            self.calls += 1
            raise RuntimeError("429 Client Error: Too Many Requests for url: https://x")

    cfg = _cfg({"vast_ai": (1, 3)})
    c = Limited("vast_ai")
    store = intraday.Store(tmp_path)
    for h in range(20):
        _sweep(store, cfg, [c], T0 + timedelta(hours=h))
    assert c.calls == 1, "asked again after a 429"
    state = intraday.source_states(store, None, cfg, T0 + timedelta(hours=20))["vast_ai"]
    assert state.rate_limited
    assert not intraday.SourceState(T0, None, 1, "HTTP 502 Bad Gateway").rate_limited


def test_a_sweep_never_writes_to_the_record(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """Structural: the intraday module has no way into `observations` or `daily_index`."""
    source = (REPO_ROOT / "src" / "tci" / "intraday.py").read_text(encoding="utf-8")
    assert not re.search(r"\b(INSERT|UPDATE|DELETE)\b", source)
    before = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    _sweep(intraday.Store(tmp_path), _cfg(), [FakeCollector("fake", [[_obs("a", 2.0)]])],
           T0, conn=conn, force=True)
    assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == before
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


# --- reconstruction -----------------------------------------------------------------------


def _panel_store(tmp_path: Path, prices_by_hour: list[list[float]]) -> intraday.Store:
    """Five invented providers, one book per hour, for the unpanelled calculation."""
    store = intraday.Store(tmp_path)
    cfg = _cfg()
    for h, prices in enumerate(prices_by_hour):
        book = [_obs(f"prov{i}", p) for i, p in enumerate(prices)]
        _sweep(store, cfg, [FakeCollector("fake", [book])], T0 + timedelta(hours=h))
    return store


@pytest.mark.usefixtures("unpanelled")
def test_the_path_moves_with_the_reads(tmp_path: Path) -> None:
    hours = [[2.0, 2.2, 2.4, 2.6, 2.8], [3.0, 3.2, 3.4, 3.6, 3.8], [2.0, 2.2, 2.4, 2.6, 2.8]]
    store = _panel_store(tmp_path, hours)
    snaps = intraday.path(store, None, _cfg(), T0 - timedelta(hours=1), T0 + timedelta(hours=5))
    assert [s.values["EU-CRI-H100"][0] for s in snaps] == [2.4, 3.4, 2.4]
    assert all(s.origin == "intraday" for s in snaps)


@pytest.mark.usefixtures("unpanelled")
def test_a_stale_read_drops_out_and_the_value_gaps(tmp_path: Path) -> None:
    store = _panel_store(tmp_path, [[2.0, 2.2, 2.4, 2.6, 2.8]])
    cfg = _cfg()
    reads = intraday.all_reads(store, None, cfg, T0 - timedelta(hours=4), T0 + timedelta(hours=6))
    assert intraday.state_at(reads, T0 + timedelta(hours=2), cfg)
    assert not intraday.state_at(reads, T0 + timedelta(hours=3, minutes=1), cfg)
    # Four providers is below the gate: a gap, with the fixing's own reason.
    store2 = _panel_store(tmp_path / "b", [[2.0, 2.2, 2.4, 2.6]])
    snap = intraday.path(store2, None, cfg, T0, T0 + timedelta(hours=1))[0]
    assert snap.values["EU-CRI-H100"][0] is None
    assert snap.values["EU-CRI-H100"][3] == "insufficient_sources"


def test_replaying_the_fixing_reproduces_the_published_prints(tmp_path: Path) -> None:
    """The reconstruction is the fixing's calculation, not an approximation of it.

    Over the committed record, the value reconstructed at the moment each day's fixing
    collection finished equals the published print for every configured series, whenever
    that collection was quick enough for every read to be inside its limit. A day whose
    collection was spread over hours (a catch-up run) is left out on exactly that ground.
    """
    conn = db.connect_readonly(REPO_ROOT / "data" / "eucri.db")
    cfg = intraday.load_config()
    # Anchored to the record, not the clock, so the test means the same thing next month.
    last = conn.execute("SELECT MAX(finished_utc) FROM runs WHERE status = 'ok'").fetchone()[0]
    end = intraday.parse_iso(last) + timedelta(minutes=1)
    snaps = intraday.path(intraday.Store(tmp_path), conn, cfg, end - timedelta(days=21), end)
    fixings = [s for s in snaps if s.origin == "fixing"]
    compared = 0
    for s in fixings:
        day = s.at.strftime("%Y-%m-%d")
        spread = conn.execute(
            "SELECT MIN(finished_utc), MAX(finished_utc) FROM runs WHERE status = 'ok'"
            " AND utc_date = ? AND source NOT IN ('index', 'intake', 'outputs', 'forward')",
            (day,)).fetchone()
        if spread[0] is None or (
                intraday.parse_iso(spread[1]) - intraday.parse_iso(spread[0])
                > timedelta(hours=3)):
            continue
        for series, (usd, _eur, _n, _flags) in s.values.items():
            assert usd == series_read.head_value(conn, series, day), (day, series)
            compared += 1
    assert compared >= 5 * len(cfg.series), f"too few fixings compared ({compared})"


# --- settlement ---------------------------------------------------------------------------


def _snap(hh: int, mm: int, v: float | None) -> intraday.Snapshot:
    return intraday.Snapshot(
        at=datetime(2026, 9, 20, hh, mm, tzinfo=UTC), origin="intraday",
        values={"S": (v, None, 5, "" if v is not None else "insufficient_sources")},
        ages={}, missing=(),
    )


def test_settlement_averages_inside_the_window_only() -> None:
    snaps = [_snap(7, 17, 9.0), _snap(8, 17, 2.0), _snap(9, 17, 3.0), _snap(10, 59, 4.0),
             _snap(11, 0, 9.0)]
    w = intraday.SettlementWindow("08:00", "11:00", "mean", 2)
    st = intraday.settle(snaps, "2026-09-20", "S", w, fixing_usd=3.1)
    assert st.value_usd == 3.0 and st.n_points == 3 and st.fixing_usd == 3.1
    med = intraday.settle(snaps + [_snap(9, 30, 10.0)], "2026-09-20", "S",
                          intraday.SettlementWindow("08:00", "11:00", "median", 2))
    assert med.value_usd == 3.5


def test_a_thin_window_has_no_value_and_says_why() -> None:
    snaps = [_snap(8, 17, 2.0), _snap(9, 17, None), _snap(10, 17, None)]
    st = intraday.settle(snaps, "2026-09-20", "S",
                         intraday.SettlementWindow("08:00", "11:00", "mean", 2))
    assert st.value_usd is None
    assert st.reason == "1 published value(s) in the window, 2 required"


# --- outputs ------------------------------------------------------------------------------


@pytest.mark.usefixtures("unpanelled")
def test_the_page_and_data_files_are_built(tmp_path: Path) -> None:
    from tci.outputs import intraday_page, site

    hours = [[2.0 + h / 100, 2.2, 2.4, 2.6, 2.8] for h in range(30)]
    hours[5] = [2.0, 2.2]  # one gapped read
    store = _panel_store(tmp_path / "store", hours)
    conn = db.connect_readonly(REPO_ROOT / "data" / "eucri.db")
    built = intraday_page.build(conn, store, _cfg(), now=T0 + timedelta(hours=30))
    files = intraday_page.write_data(built, tmp_path / "out")
    rows = (tmp_path / "out" / "path.csv").read_text().splitlines()
    assert rows[0].startswith("at_utc,origin,series,value_usd")
    assert any(",insufficient_sources," in r for r in rows)
    latest = json.loads(files[1].read_text())
    assert latest["latest"]["values"]["EU-CRI-H100"]["value_usd"] is not None

    html = intraday_page.render(site.build_context(conn), built)
    assert "Not built" not in html
    assert "TCI-CRI-H100" in html and "EU-CRI-H100 " not in html  # published names only
    assert 'class="ch-gapmark"' in html  # the gapped read is marked, not bridged
    assert "<script src" not in html.replace('<script defer src="/_vercel', "")
    assert 'href="data/intraday/path.csv"' in html


def test_the_chart_breaks_at_a_missed_sweep() -> None:
    from tci.outputs import intraday_page, site

    t = T0
    pts = [(t, 2.0), (t + timedelta(hours=1), 2.1), (t + timedelta(hours=5), 2.2),
           (t + timedelta(hours=6), 2.3)]
    svg = intraday_page._time_chart(site, pts, [], t, t + timedelta(hours=7),
                                    symbol="X", focusable=True)
    assert svg.count('class="ch-line"') == 2  # a four-hour hole is not drawn across
