# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""The explicit panel: collected is not admitted."""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from tci.commands import compute_all_series
from tci.config import load_factors
from tci.normalise import normalise_observations, unadmitted_providers
from tests.conftest import insert_run


def row(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "provider": "vast.ai", "source": "vast_ai", "tier": "executable",
        "gpu_model": "H100_SXM", "gpu_count": 8, "price_usd_per_gpu_hr": 2.5,
        "country": "NL", "term": "on_demand", "raw_json": "{}",
    }
    base.update(kw)
    return base


def test_unknown_provider_is_not_admitted() -> None:
    f = load_factors(for_date="2026-09-15")
    assert normalise_observations([row(provider="newcloud", source="gpuhunt")], f) == []
    assert unadmitted_providers([row(provider="newcloud", source="gpuhunt")], f) == {
        "newcloud": {"H100"}
    }


def test_known_provider_through_an_unlisted_collector_is_not_admitted() -> None:
    """Nebius through gpuhunt is shadow under 0.4.0 and a constituent under 0.5.0."""
    r = row(provider="nebius", source="gpuhunt", tier="list", country="FI")
    assert normalise_observations([r], load_factors(for_date="2026-09-21")) == []
    assert len(normalise_observations([r], load_factors(for_date="2026-09-22"))) == 1


def test_a_class_is_admitted_per_provider() -> None:
    h200 = row(gpu_model="H200_SXM")
    assert normalise_observations([h200], load_factors(for_date="2026-09-21")) == []
    assert len(normalise_observations([h200], load_factors(for_date="2026-09-22"))) == 1


def test_datacrunch_and_verda_never_vote_together() -> None:
    """One company, one vote: the static entry leaves the day the feed enters."""
    old, new = load_factors(for_date="2026-09-21"), load_factors(for_date="2026-09-22")
    assert old.admits("datacrunch", "static_yaml", "H100")
    assert not old.admits("verda", "gpuhunt", "H100")
    assert new.admits("verda", "gpuhunt", "H100")
    assert not new.admits("datacrunch", "static_yaml", "H100")


def test_every_panel_segment_is_a_known_segment() -> None:
    for day in ("2026-07-18", "2026-09-15", "2026-09-22"):
        f = load_factors(for_date=day)
        assert f.panel is not None
        segments = {e.segment for e in f.panel.values()}
        assert segments <= {"marketplace", "neocloud", "hyperscaler"}


@pytest.mark.parametrize("day", ["2026-09-14", "2026-09-22"])
def test_shadow_rows_appear_in_the_audit_set_and_move_nothing(
    conn: sqlite3.Connection, day: str
) -> None:
    """A shadow provider is listed as not_in_panel and the value is unchanged by it."""
    run = insert_run(conn, utc_date=day)
    base_rows = [
        ("vast.ai", "vast_ai", "executable", 2.27), ("runpod", "runpod", "executable", 3.49),
        ("scaleway", "scaleway", "list", 3.70), ("seeweb", "static_yaml", "list", 2.16),
        ("nebius", "static_yaml", "list", 3.85), ("datacrunch", "static_yaml", "list", 3.25),
    ]

    def insert(provider: str, source: str, tier: str, price: float, rid: str) -> None:
        conn.execute(
            "INSERT INTO observations (run_id, ts_utc, source, provider, gpu_model, gpu_count,"
            " price_usd_per_gpu_hr, region, country, interconnect, tier, term, raw_json)"
            " VALUES (?, ?, ?, ?, 'H100_SXM', 8, ?, NULL, 'NL', NULL, ?, 'on_demand', ?)",
            (rid, f"{day}T11:00:00Z", source, provider, price, tier,
             '{"last_verified": "2026-09-01"}'),
        )

    for p, s, t, price in base_rows:
        insert(p, s, t, price, run)
    conn.execute("INSERT INTO fx (date, eur_usd, source) VALUES ('2026-09-01', 1.16, 't')")
    compute_all_series(conn, day)
    before = conn.execute(
        "SELECT value_usd FROM daily_index WHERE date=? AND series='EU-CRI-H100'", (day,)
    ).fetchone()["value_usd"]

    insert("coreweave", "coreweave", "list", 9.99, run)  # never on any panel
    compute_all_series(conn, day)
    head = conn.execute(
        "SELECT * FROM daily_index WHERE date=? AND series='EU-CRI-H100'"
        " ORDER BY revision DESC LIMIT 1", (day,)
    ).fetchone()
    cons = {
        c["provider"]: c for c in conn.execute(
            "SELECT * FROM constituents WHERE date=? AND series='EU-CRI-H100' AND revision=?",
            (day, head["revision"]),
        )
    }
    assert cons["coreweave"]["exclusion_reason"] == "not_in_panel"
    assert not cons["coreweave"]["included"]
    # Under 0.5.0 datacrunch is shadow too, so compare values only within one version.
    assert head["value_usd"] == before


# From v0.7.0 every price that can reach a print is read by a collector on a schedule.
# Before it, seeweb's constituent price lived in a hand-edited file and was only as current
# as the last time someone opened the page, and three panel lines pointed at files that
# never held a price. Nothing about the calculation noticed either.
FIRST_FULLY_AUTOMATED = "0.7.0"
HAND_MAINTAINED = {"static_yaml"}


def _versions_from(first: str) -> list[Any]:
    from tci.config import load_succession

    versions = load_succession()
    names = [v.version for v in versions]
    return versions[names.index(first):]


def test_every_panel_source_from_v070_is_a_collector_the_daily_run_executes() -> None:
    from tci.commands import collectors_for_daily

    run = {c.name for c in collectors_for_daily()}
    for v in _versions_from(FIRST_FULLY_AUTOMATED):
        panel = load_factors(for_date=v.effective_from).panel
        assert panel is not None, v.version
        for provider, entry in panel.items():
            for source in entry.sources:
                assert source not in HAND_MAINTAINED, (
                    f"{v.version}: {provider} is read through {source}, a hand-maintained "
                    "file; admit an automated collector instead")
                assert source in run, (
                    f"{v.version}: {provider} is read through {source}, which the daily "
                    "run does not execute, so it can never contribute")
