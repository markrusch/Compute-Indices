# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""The intake ledger, and the one test that makes it trustworthy.

`tci.intake` replays the filter chain in `normalise.py` in order to name which rule
stopped a row. That is a second copy of the calculation's admission logic, kept outside
the hash-locked file on purpose (see the module docstring), and a second copy that
silently disagrees with the first is worse than no audit at all.

`test_admitted_set_matches_normalise_over_the_whole_record` is what pays for that choice.
It replays every stored collection date under the methodology version live on that date
and asserts the rows the ledger calls `admitted` are exactly the rows the calculation
takes. Not a fixture - the real database, every session in it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from typing import Any

import pytest

from tci import commands, db, intake
from tci.config import load_factors
from tci.normalise import normalise_observations

# Panel off for the unit tests below: they pin gate attribution with synthetic providers,
# exactly as tests/test_normalise.py does, so that a panel change does not rewrite them.
FACTORS = replace(load_factors(), panel=None)
BLOCK = FACTORS.eu_eea_countries


def obs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "provider": "p", "source": "s", "tier": "executable", "gpu_model": "H100_SXM",
        "gpu_count": 8, "price_usd_per_gpu_hr": 2.0, "country": "NL",
        "term": "on_demand", "raw_json": "{}",
    }
    base.update(overrides)
    return base


def gate(**overrides: Any) -> str:
    return intake.classify(obs(**overrides), FACTORS, 1.17, BLOCK).gate


# ---------------------------------------------------------------------------
# gate attribution
# ---------------------------------------------------------------------------


def test_reference_row_is_admitted() -> None:
    assert gate() == "admitted"


def test_every_gate_is_reachable() -> None:
    """A gate no input can reach is a gate that cannot be trusted to mean anything.

    The panel gate is absent here because these rows run with the panel off; it is covered
    by the equivalence test against the real record, where the panel is live.
    """
    reachable = {
        gate(gpu_model="RTX_4090"),
        gate(term="commit_12mo"),
        gate(tier="spot"),
        gate(country="US"),
        gate(gpu_count=1),
        gate(gpu_count=None, tier="executable"),
        gate(raw_json='{"currency": "GBP", "price_native_per_gpu_hr": 2.0}'),
        gate(price_usd_per_gpu_hr=0.01),
        gate(),
    }
    assert reachable == set(intake.GATES) - {"not_in_panel"}


def test_term_is_checked_before_tier() -> None:
    """Attribution order is load-bearing: it is what makes the counts a partition.

    A spot row on a 12-month commitment fails both rules. normalise.py checks term first,
    so the ledger must attribute it to term, or the two disagree on a real row.
    """
    assert gate(term="commit_12mo", tier="spot") == "term_not_reference"
    assert gate(tier="spot") == "tier_excluded"


def test_country_gate_covers_a_missing_country() -> None:
    """The Norway bug's shape: a row with no country is not outside the block by accident,
    it is unattributable, and it must land somewhere a reader can see."""
    assert gate(country=None) == "outside_block"
    assert gate(country="US") == "outside_block"
    assert gate(country="Germany") == "outside_block"


def test_node_floor_and_undeclared_size_are_different_gates() -> None:
    """RunPod's defect was the second of these, and it cost nine prints. Reporting it as
    the first would have sent anyone looking at the wrong rule."""
    assert gate(gpu_count=1) == "below_node_floor"
    assert gate(gpu_count=None, tier="executable") == "size_undeclared"
    # A list price need not demonstrate its size.
    assert gate(gpu_count=None, tier="list") == "admitted"


def test_currency_gate() -> None:
    eur = obs(raw_json='{"currency": "EUR", "price_native_per_gpu_hr": 2.0}')
    assert intake.classify(eur, FACTORS, 1.17, BLOCK).gate == "admitted"
    # No rate: dropped, never converted at a guess.
    assert intake.classify(eur, FACTORS, None, BLOCK).gate == "currency_unsupported"
    gbp = obs(raw_json='{"currency": "GBP", "price_native_per_gpu_hr": 2.0}')
    assert intake.classify(gbp, FACTORS, 1.17, BLOCK).gate == "currency_unsupported"


def test_price_band() -> None:
    assert gate(price_usd_per_gpu_hr=0.01) == "outside_price_band"
    assert gate(price_usd_per_gpu_hr=99.0) == "outside_price_band"


def test_unparseable_raw_json_is_not_a_special_case() -> None:
    assert gate(raw_json="not json") == "admitted"


def test_counts_partition_the_input() -> None:
    rows = [
        obs(), obs(gpu_model="RTX_4090"), obs(tier="spot"), obs(country="US"),
        obs(gpu_count=1), obs(term="commit_12mo"), obs(price_usd_per_gpu_hr=0.01),
    ]
    cells = intake.ledger(rows, FACTORS, 1.17)
    assert sum(c.n_rows for c in cells) == len(rows)
    assert set(intake.by_gate(cells)) <= set(intake.GATES)


def test_gate_order_matches_the_gates_tuple() -> None:
    """`GATES` is the published order and `by_gate` reports in it. If the tuple and the
    chain drift apart the page lists rules in an order the calculation does not use."""
    assert intake.GATES[-1] == "admitted"
    assert set(intake.GATE_REASONS) == set(intake.GATES)


# ---------------------------------------------------------------------------
# the equivalence test
# ---------------------------------------------------------------------------


def _key(o: Any) -> tuple[Any, ...]:
    return (o.provider, o.source, o.gpu_model, o.gpu_count, round(o.price_usd, 9))


@pytest.mark.parametrize("block", ["EU_EEA"])
def test_admitted_set_matches_normalise_over_the_whole_record(block: str) -> None:
    """Every stored session: the ledger's `admitted` rows are the calculation's rows.

    This is the test that licenses `tci.intake` to exist outside normalise.py. It compares
    multisets, not counts, so a ledger that admitted the right NUMBER of wrong rows fails.
    """
    conn = db.connect()
    dates = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT substr(ts_utc, 1, 10) d FROM observations ORDER BY d"
        )
    ]
    if not dates:
        pytest.skip("no observations stored")

    for date in dates:
        factors = load_factors(for_date=date)
        countries = (
            factors.eu_eea_countries
            if block == "EU_EEA"
            else factors.countries_of(block)
        )
        rows = conn.execute(
            "SELECT o.* FROM observations o JOIN runs r ON o.run_id = r.run_id"
            " WHERE r.utc_date = ?",
            (date,),
        ).fetchall()
        fx_row = conn.execute(
            "SELECT fx_rate FROM daily_index WHERE date = ? AND fx_rate IS NOT NULL"
            " LIMIT 1",
            (date,),
        ).fetchone()
        fx = fx_row["fx_rate"] if fx_row else None

        expected = sorted(
            _key(o) for o in normalise_observations(rows, factors, fx, countries)
        )
        admitted = [
            row for row in rows
            if intake.classify(row, factors, fx, countries).gate == "admitted"
        ]
        # Re-normalise each admitted row on its own to recover the converted price the
        # calculation would have used, so the comparison is on values and not just counts.
        got = sorted(
            _key(o)
            for row in admitted
            for o in normalise_observations([row], factors, fx, countries)
        )
        assert got == expected, (
            f"{date} (v{factors.methodology_version}): the intake ledger and"
            f" normalise_observations disagree on which rows qualify."
            f" Only in normalise: {sorted(set(expected) - set(got))[:5]}."
            f" Only in intake: {sorted(set(got) - set(expected))[:5]}."
        )


def test_ledger_totals_match_rows_collected_over_the_whole_record() -> None:
    """The funnel must account for every stored row, on every session."""
    conn = db.connect()
    for date in [
        r[0] for r in conn.execute(
            "SELECT DISTINCT substr(ts_utc, 1, 10) d FROM observations ORDER BY d"
        )
    ]:
        cells, _ = intake.compute(conn, date)
        stored = conn.execute(
            "SELECT COUNT(*) FROM observations o JOIN runs r ON o.run_id = r.run_id"
            " WHERE r.utc_date = ?",
            (date,),
        ).fetchone()[0]
        assert sum(c.n_rows for c in cells) == stored, date


# ---------------------------------------------------------------------------
# storage, revisions, detectors
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    db.migrate(c)
    c.execute(
        "INSERT INTO runs (run_id, utc_date, source, started_utc, status)"
        " VALUES ('r1', '2026-09-17', 'test', '2026-09-17T00:00:00Z', 'ok')"
    )
    c.commit()
    return c


def _cells(**by_gate: int) -> list[intake.Cell]:
    return [
        intake.Cell("s", "p", "H100", gate, n) for gate, n in by_gate.items()
    ]


def test_store_appends_a_revision_and_head_reads_the_latest(
    conn: sqlite3.Connection,
) -> None:
    assert intake.store(conn, "2026-09-17", "EU_EEA", _cells(admitted=5), "0.4.0", "r1") == 1
    assert intake.store(conn, "2026-09-17", "EU_EEA", _cells(admitted=9), "0.4.0", "r1") == 2
    assert intake.by_gate(intake.head(conn, "2026-09-17")) == {"admitted": 9}


def test_intake_rows_are_immutable(conn: sqlite3.Connection) -> None:
    """A derived per-date table without this becomes the next place a stale value hides."""
    intake.store(conn, "2026-09-17", "EU_EEA", _cells(admitted=5), "0.4.0", "r1")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE intake SET n_rows = 1")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM intake")


def _seed(conn: sqlite3.Connection, date: str, cells: list[intake.Cell]) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO runs (run_id, utc_date, source, started_utc, status)"
        " VALUES (?, ?, 'test', ?, 'ok')",
        (f"run-{date}", date, f"{date}T00:00:00Z"),
    )
    conn.commit()
    intake.store(conn, date, "EU_EEA", cells, "0.4.0", f"run-{date}")


def test_dropouts_catch_a_single_row_that_stopped_qualifying(
    conn: sqlite3.Connection,
) -> None:
    """The RunPod case, reduced to its shape.

    One row per session, admitted for a week, then held at `size_undeclared`. `shifts` has
    a volume floor and cannot see this; that is precisely why `dropouts` has none. On ten
    of the eleven real sessions this happened, the headline gapped for want of a fifth
    provider that was sitting in the database with its price in hand.
    """
    for day in range(10, 17):
        _seed(conn, f"2026-09-{day}", [intake.Cell("runpod", "runpod", "H100", "admitted", 1)])
    _seed(
        conn, "2026-09-17",
        [intake.Cell("runpod", "runpod", "H100", "size_undeclared", 1)],
    )
    found = intake.dropouts(conn, "2026-09-17")
    assert [(d.provider, d.gate) for d in found] == [("runpod", "size_undeclared")]
    assert found[0].was_admitted == pytest.approx(1.0)


def test_a_cell_that_never_qualified_is_not_a_dropout(conn: sqlite3.Connection) -> None:
    """Shadow collectors store rows for weeks by design. Reporting them every session as a
    regression would bury the one session that means something."""
    for day in range(10, 18):
        _seed(conn, f"2026-09-{day}", [intake.Cell("civo", "civo", "H100", "not_in_panel", 40)])
    assert intake.dropouts(conn, "2026-09-17") == []


def test_shifts_flag_a_gate_that_jumped(conn: sqlite3.Connection) -> None:
    for day in range(10, 17):
        _seed(conn, f"2026-09-{day}", _cells(admitted=30, outside_block=30))
    _seed(conn, "2026-09-17", _cells(admitted=30, outside_block=400))
    flagged = {s.gate for s in intake.shifts(conn, "2026-09-17")}
    assert "outside_block" in flagged
    assert "admitted" not in flagged


def test_shifts_stay_quiet_on_small_numbers(conn: sqlite3.Connection) -> None:
    """A gate going from one row to three is noise, and an instrument that cries about it
    gets ignored on the day it matters."""
    for day in range(10, 17):
        _seed(conn, f"2026-09-{day}", _cells(admitted=30, outside_block=1))
    _seed(conn, "2026-09-17", _cells(admitted=30, outside_block=3))
    assert [s.gate for s in intake.shifts(conn, "2026-09-17")] == []


def test_no_history_means_no_alarms(conn: sqlite3.Connection) -> None:
    _seed(conn, "2026-09-17", _cells(admitted=30, outside_block=400))
    assert intake.shifts(conn, "2026-09-17") == []
    assert intake.dropouts(conn, "2026-09-17") == []


# ---------------------------------------------------------------------------
# yields
# ---------------------------------------------------------------------------


def test_structurally_blocked_distinguishes_a_rule_from_a_catalogue() -> None:
    """A collector whose rows are mostly GPUs the index does not price is a broad
    catalogue, not a blockage. One held at the panel or the country filter is a decision
    somebody should be making."""
    cat = intake.source_yields(
        [intake.Cell("gpuhunt", "gcp", "", "not_a_reference_variant", 500)]
    )[0]
    assert not cat.structurally_blocked

    blocked = intake.source_yields(
        [intake.Cell("seeweb", "seeweb", "H100", "not_in_panel", 4)]
    )[0]
    assert blocked.structurally_blocked

    fine = intake.source_yields(
        [
            intake.Cell("scaleway", "scaleway", "H100", "admitted", 2),
            intake.Cell("scaleway", "scaleway", "H100", "not_in_panel", 9),
        ]
    )[0]
    assert not fine.structurally_blocked


def test_readers_are_quiet_on_a_database_without_the_table() -> None:
    """A database that predates migration 0010 must still generate the site.

    The migration reaches a database when the daily run applies it, and
    `tests/test_pipeline_smoke.py` regenerates every page without migrating first. The
    page falls back to classifying observations, so the readers return nothing here
    rather than raising OperationalError.
    """
    bare = sqlite3.connect(":memory:")
    bare.row_factory = sqlite3.Row
    assert not intake.stored(bare)
    assert intake.head(bare, "2026-09-17") == []
    assert intake.dates(bare) == []
    assert intake.shifts(bare, "2026-09-17") == []
    assert intake.dropouts(bare, "2026-09-17") == []


def test_missing_names_sessions_with_observations_and_no_ledger(
    conn: sqlite3.Connection,
) -> None:
    for date in ("2026-09-15", "2026-09-16", "2026-09-17"):
        conn.execute(
            "INSERT OR IGNORE INTO runs (run_id, utc_date, source, started_utc, status)"
            " VALUES (?, ?, 'test', ?, 'ok')",
            (f"c-{date}", date, f"{date}T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO observations (run_id, ts_utc, source, provider, gpu_model,"
            " gpu_count, price_usd_per_gpu_hr, country, tier, term, raw_json)"
            " VALUES (?, ?, 's', 'p', 'H100_SXM', 8, 2.0, 'NL', 'list', 'on_demand', '{}')",
            (f"c-{date}", f"{date}T00:00:00Z"),
        )
    conn.commit()
    assert intake.missing(conn) == ["2026-09-15", "2026-09-16", "2026-09-17"]

    intake.store(conn, "2026-09-16", "EU_EEA", _cells(admitted=1), "0.4.0", "c-2026-09-16")
    assert intake.missing(conn) == ["2026-09-15", "2026-09-17"]


def test_backfill_recovers_the_record_and_is_then_a_no_op(
    conn: sqlite3.Connection,
) -> None:
    """The ledger is a function of stored observations, so a session it never recorded is
    recoverable exactly. Both detectors are blind without that history, which is why the
    daily run repairs its own gaps rather than waiting for somebody to run a command."""
    test_missing_names_sessions_with_observations_and_no_ledger(conn)
    filled = intake.backfill(conn, "EU_EEA", "c-2026-09-17")
    assert filled == ["2026-09-15", "2026-09-17"]
    assert intake.missing(conn) == []
    # Idempotent: nothing left to repair, so nothing is written and no revision is added.
    assert intake.backfill(conn, "EU_EEA", "c-2026-09-17") == []
    assert len(intake.head(conn, "2026-09-16")) == 1


def test_backfill_on_a_database_without_the_table_does_nothing() -> None:
    bare = sqlite3.connect(":memory:")
    bare.row_factory = sqlite3.Row
    assert intake.missing(bare) == []
    assert intake.backfill(bare, "EU_EEA", "r1") == []


def test_store_intake_actually_writes_on_the_happy_path(
    conn: sqlite3.Connection,
) -> None:
    """The counterpart to the two tests below, and the reason they are safe to have.

    Both of those assert that `_store_intake` does NOTHING: nothing when the ledger throws,
    nothing when the table is absent. A no-op body satisfies both, and satisfied the whole
    file — verified by replacing the function with `return` and watching 29 tests pass. An
    absence-assertion with no opposite is a test that a deleted feature passes forever.

    So this one asserts the presence: given a migrated database and rows that qualify, the
    ledger is written and the counts are the ones `intake.ledger` computes.
    """
    conn.execute(
        "INSERT INTO observations (run_id, ts_utc, source, provider, gpu_model, gpu_count,"
        " price_usd_per_gpu_hr, country, tier, term, raw_json)"
        " VALUES ('r1', '2026-09-17T00:00:00Z', 's', 'p', 'H100_SXM', 8, 2.0, 'NL',"
        " 'list', 'on_demand', '{}')"
    )
    conn.commit()
    rows = conn.execute("SELECT * FROM observations").fetchall()

    assert intake.head(conn, "2026-09-17") == []
    commands._store_intake(conn, "2026-09-17", rows, FACTORS, 1.17, "r1")

    stored_cells = intake.head(conn, "2026-09-17")
    assert stored_cells, "_store_intake wrote nothing on a database that can hold it"
    assert stored_cells == intake.ledger(rows, FACTORS, 1.17)
    assert intake.by_gate(stored_cells) == {"admitted": 1}


def test_a_failing_ledger_cannot_gap_a_print(monkeypatch: pytest.MonkeyPatch) -> None:
    """An observer bug must cost the audit record and nothing else.

    `_store_intake` is called from inside `compute_all_series`. Unguarded, an exception
    there aborts the session before any print is stored and gaps every series - the one
    failure this project cannot afford, caused by the instrument built to catch it. So the
    guarantee is tested rather than asserted in a docstring: break the ledger outright and
    require the call to return quietly.
    """
    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("ledger is broken")

    monkeypatch.setattr(intake, "stored", boom)
    commands._store_intake(
        sqlite3.connect(":memory:"), "2026-09-17", [obs()], FACTORS, 1.17, "r1",  # type: ignore[arg-type]
    )


def test_the_ledger_is_written_after_every_print() -> None:
    """Order is the other half of the guarantee, and a refactor could quietly undo it.

    Storing the ledger before the prints puts an observer in front of the calculation. The
    call therefore has to sit after the last `_store_print` in `compute_all_series`.
    """
    import inspect

    src = inspect.getsource(commands.compute_all_series)
    assert src.count("_store_intake(") == 1
    assert src.rindex("_store_print(") < src.index("_store_intake("), (
        "_store_intake must come after the last _store_print, or an observer failure"
        " can cost a session its prints"
    )


def test_a_recompute_into_an_unmigrated_database_still_succeeds() -> None:
    """`reproduce` and `effect` recompute the published record into a throwaway copy of
    the database, and that copy predates the migration. The ledger is an observer, so it
    must never be the reason a recomputation of a published print fails."""
    bare = sqlite3.connect(":memory:")
    bare.row_factory = sqlite3.Row
    rows = [obs()]
    # The write path used by compute_and_store_all, against a database with no table.
    commands._store_intake(bare, "2026-09-17", rows, FACTORS, 1.17, "run-1")  # type: ignore[arg-type]


def test_yield_pct_on_an_empty_session_is_none() -> None:
    assert intake.yield_pct([]) is None
    assert intake.render([], "2026-09-17", "0.4.0", []) == "2026-09-17: nothing collected"

def test_the_page_never_prints_a_series_name_that_does_not_exist(
    conn: sqlite3.Connection,
) -> None:
    """The watch list names a compute class, and a class is not a series.

    Building `EU-CRI-<class>` and rebranding it yields TCI-CRI-H100P, and that class is
    published as TCI-CRI-H100-PCIE. `display_series` is a pure string rebrand that raises
    on nothing, so nothing would have caught it: the page would simply have carried an
    identifier that does not exist. CLAUDE.md keeps series keys to two translation points
    for this reason.
    """
    from tci.outputs import site as site_mod

    # H100P is the class where pasting differs from the published series name.
    assert commands.SERIES_BY_CLASS["H100P"] == "EU-CRI-H100-PCIE"
    assert site_mod.display_series("EU-CRI-H100-PCIE") == "TCI-CRI-H100-PCIE"

    for day in range(10, 17):
        _seed(
            conn, f"2026-09-{day}",
            [intake.Cell("ovh", "ovhcloud", "H100P", "admitted", 1)],
        )
    _seed(
        conn, "2026-09-17",
        [intake.Cell("ovh", "ovhcloud", "H100P", "not_in_panel", 1)],
    )
    dropped = intake.dropouts(conn, "2026-09-17")
    assert [d.model_class for d in dropped] == ["H100P"]

    ctx = site_mod.SiteContext(
        conn=conn, factors=load_factors(), version="0.6.0", lock_hash="x",
        generated_at="2026-09-17T00:00:00Z", head=None, date="2026-09-17",
    )
    html = site_mod._intake_watch(ctx)
    assert "H100P" in html, "the class should still be named"
    assert "CRI-H100P" not in html, (
        "the page built a series identifier out of a class name; TCI-CRI-H100P is not a"
        " published series"
    )
