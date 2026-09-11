# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Every collector's output must survive a real database insert.

THE GAP THIS CLOSES. The collector tests assert on the `Observation` objects a collector
returns. The normalise tests assert on dicts. Neither ever wrote a row. So when the tenor
work made the collectors emit tier='spot' against a schema that allowed only
('executable','list'), the full suite stayed green and the daily run died in production
for four consecutive sessions.

The bug was not subtle and not hard to catch. Nothing was looking at the seam between
what a collector produces and what the database accepts, so these tests look there, using
the real migrated schema and the real fixtures rather than a stand-in.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
import responses

from tci import db
from tci.collectors import base
from tci.collectors.azure_retail import URL as AZURE_URL
from tci.collectors.azure_retail import AzureRetailCollector
from tci.collectors.runpod import URL as RUNPOD_URL
from tci.collectors.runpod import RunPodCollector
from tci.collectors.vast_ai import URL as VAST_URL
from tci.collectors.vast_ai import VastAiCollector
from tci.models import Observation

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    """A real database at the current schema, not an in-memory approximation."""
    c = db.connect(tmp_path / "t.db")
    db.migrate(c)
    return c


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class _Static:
    """A collector returning a fixed list, to exercise persistence on its own."""

    def __init__(self, name: str, rows: list[Observation]) -> None:
        self.name = name
        self._rows = rows

    def collect(self, session: object) -> list[Observation]:
        return self._rows


def _obs(**over: object) -> Observation:
    base_kwargs: dict = {
        "ts_utc": db.utc_now_iso(), "source": "s", "provider": "p",
        "gpu_model": "H100_SXM", "gpu_count": 8, "price_usd_per_gpu_hr": 3.0,
        "region": "r", "country": "NL", "interconnect": "NVLink",
        "tier": "list", "term": "on_demand", "raw_json": "{}",
    }
    base_kwargs.update(over)
    return Observation(**base_kwargs)


# ---------------------------------------------------------------------------
# the seam: collector output -> database
# ---------------------------------------------------------------------------


@responses.activate
@pytest.mark.parametrize(
    ("collector", "url", "method", "fixture"),
    [
        (VastAiCollector(spacing_seconds=0), VAST_URL, responses.POST, "vast_bundles.json"),
        (RunPodCollector(), RUNPOD_URL, responses.POST, "runpod_gputypes.json"),
        (AzureRetailCollector(), AZURE_URL, responses.GET, "azure_westeurope.json"),
    ],
)
def test_collector_output_is_storable(
    conn: sqlite3.Connection, collector: object, url: str, method: object, fixture: str
) -> None:
    """Run a collector against its fixture and persist the result for real.

    `run_collector` swallows failures by design, so asserting it returned "ok" is the
    assertion: a schema rejection shows up as "failed" and a stored reason.
    """
    responses.add(method, url, json=_fixture(fixture), status=200)

    status = base.run_collector(conn, collector, "2026-09-11")

    reason = conn.execute(
        "SELECT status, notes FROM runs WHERE source = ?", (collector.name,)
    ).fetchone()
    assert status == "ok", f"{collector.name} could not be stored: {reason['notes']}"
    n = conn.execute(
        "SELECT COUNT(*) c FROM observations WHERE source = ?", (collector.name,)
    ).fetchone()["c"]
    assert n > 0, f"{collector.name} stored nothing"


def test_every_tier_and_term_the_collectors_emit_is_accepted(
    conn: sqlite3.Connection,
) -> None:
    """The vocabulary the schema allows must cover what the collectors actually produce.

    This is the direct regression test for the four dark sessions: tier='spot' was being
    emitted against a CHECK that permitted only executable and list.
    """
    for tier in ("executable", "list", "spot", "interruptible", "community"):
        assert base.run_collector(
            conn, _Static(f"t_{tier}", [_obs(tier=tier, source=f"t_{tier}")]), "2026-09-11"
        ) == "ok", f"tier {tier!r} was rejected by the schema"

    for term in ("on_demand", "commit_1mo", "commit_3mo", "commit_6mo", "reserved_1yr",
                 "reserved_2yr", "reserved_3yr", "reserved_5yr", "reserved_unspecified"):
        assert base.run_collector(
            conn, _Static(f"m_{term}", [_obs(term=term, source=f"m_{term}")]), "2026-09-11"
        ) == "ok", f"term {term!r} was rejected by the schema"


def test_the_vocabulary_is_still_closed(conn: sqlite3.Connection) -> None:
    """Widening the constraint must not have turned it into a free-text column.

    The constraint earns its place by catching a typo at write time, when it is one row,
    rather than in a research query months later. A padded or mis-cased value is exactly
    the kind of thing that otherwise sits in the audit trail unnoticed.
    """
    for bad in ("Spot", "list ", "reserved", "executable\n"):
        assert base.run_collector(
            conn, _Static("bad_tier", [_obs(tier=bad)]), "2026-09-11"
        ) == "failed", f"schema accepted junk tier {bad!r}"
    for bad in ("reserved_4yr", "1_month", "ON_DEMAND", "commit_1mo "):
        assert base.run_collector(
            conn, _Static("bad_term", [_obs(term=bad)]), "2026-09-11"
        ) == "failed", f"schema accepted junk term {bad!r}"


# ---------------------------------------------------------------------------
# failure isolation
# ---------------------------------------------------------------------------


def test_one_unstorable_collector_does_not_stop_the_others(
    conn: sqlite3.Connection,
) -> None:
    """The property whose absence cost four sessions.

    A collector that cannot be stored has to fail alone. Before this, the insert sat
    outside the fail-soft boundary, so one bad row aborted the whole daily run: no index
    computed, no site regenerated, nothing committed, for every other source too.
    """
    bad = _Static("bad", [_obs(source="bad", tier="NOT_A_TIER")])
    good = _Static("good", [_obs(source="good")])

    assert base.run_collector(conn, bad, "2026-09-11") == "failed"
    assert base.run_collector(conn, good, "2026-09-11") == "ok"

    stored = {
        r["source"] for r in conn.execute("SELECT DISTINCT source FROM observations")
    }
    assert stored == {"good"}, "a failed collector left rows behind, or blocked a good one"


def test_a_failed_run_records_why(conn: sqlite3.Connection) -> None:
    """CI logs age out and are not public; `runs` is committed and stays diagnosable.

    Four days of failures were invisible from the repository alone, because a failed run
    stored no reason. It does now.
    """
    base.run_collector(conn, _Static("bad", [_obs(tier="NOT_A_TIER")]), "2026-09-11")
    row = conn.execute("SELECT status, notes FROM runs WHERE source = 'bad'").fetchone()
    assert row["status"] == "failed"
    assert row["notes"], "a failed run recorded no reason"
    assert "CHECK constraint" in row["notes"] or "IntegrityError" in row["notes"]


def test_a_failed_collector_stores_nothing_at_all(conn: sqlite3.Connection) -> None:
    """Partial writes would be worse than none: the batch is one transaction."""
    rows = [_obs(source="mixed"), _obs(source="mixed", tier="NOT_A_TIER")]
    assert base.run_collector(conn, _Static("mixed", rows), "2026-09-11") == "failed"
    n = conn.execute(
        "SELECT COUNT(*) c FROM observations WHERE source = 'mixed'"
    ).fetchone()["c"]
    assert n == 0, "a rejected batch left rows behind"
