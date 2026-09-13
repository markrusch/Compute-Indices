# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Collector parsing against recorded fixtures + fail-soft behaviour. No live network."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import requests
import responses

from tci.collectors import base
from tci.collectors.static_yaml import StaticYamlCollector
from tci.collectors.vast_ai import CHIPS, FETCH_LIMIT, GPU_MODEL_MAP, VastAiCollector
from tci.collectors.vast_ai import URL as VAST_URL
from tci.models import Observation

FIXTURE = Path(__file__).parent / "fixtures" / "vast_bundles.json"


def _vast_callback(data: dict, calls: list[dict]):
    """Serve the fixture per chip, as the live endpoint does with a gpu_name eq filter."""

    def callback(request):  # type: ignore[no-untyped-def]
        body = json.loads(request.body)
        calls.append(body)
        name = body["gpu_name"]["in"][0]
        book = [o for o in data["offers"] if o.get("gpu_name") == name]
        book.sort(key=lambda o: o["dph_total"], reverse=body["order"][0][1] == "desc")
        return (200, {}, json.dumps({"offers": book[: body["limit"]]}))

    return callback


@responses.activate
def test_vast_parses_fixture() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    calls: list[dict] = []
    responses.add_callback(responses.POST, VAST_URL, callback=_vast_callback(data, calls))

    out = VastAiCollector(spacing_seconds=0).collect(base.make_session())

    expected = [
        o for o in data["offers"]
        if o.get("verification") == "verified" and o.get("hosting_type") == 1
        and o.get("gpu_name") in GPU_MODEL_MAP and (o.get("num_gpus") or 0) >= 1
        and o.get("dph_total")
    ]
    # One ask row per qualifying offer, plus a bid row wherever min_bid differs from the
    # ask. The bid rows carry tier="interruptible" and are dropped by normalise.py, so
    # they widen the audit trail without touching a print.
    asks = [o for o in out if o.tier == "executable"]
    bids = [o for o in out if o.tier == "interruptible"]
    assert len(asks) == len(expected) > 0
    assert len(bids) <= len(asks)
    for o in out:
        assert o.provider == "vast.ai" and o.tier in ("executable", "interruptible")
        assert o.price_usd_per_gpu_hr > 0
        assert o.country is None or len(o.country) == 2
        raw = json.loads(o.raw_json)
        assert raw["verification"] == "verified" and raw["hosting_type"] == 1
        assert raw["book"]["fetch_limit"] == FETCH_LIMIT
    # per-GPU price = dph_total / num_gpus (verified against the recorded NL 8x node)
    node = next(o for o in asks if o.gpu_count == 8 and o.country == "NL")
    raw = json.loads(node.raw_json)
    assert abs(node.price_usd_per_gpu_hr - raw["dph_total"] / 8) < 1e-9


@responses.activate
def test_vast_every_query_names_one_chip() -> None:
    """Regression, 2026-09-08: an unfiltered query ordered by price returned only the
    cheapest offers on the marketplace, consumer cards, and no H100 reached the index.
    Every request must carry a single-chip gpu_name filter and a limit below the server's
    clamp."""
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    calls: list[dict] = []
    responses.add_callback(responses.POST, VAST_URL, callback=_vast_callback(data, calls))
    VastAiCollector(spacing_seconds=0).collect(base.make_session())
    assert [c["gpu_name"]["in"][0] for c in calls if c["order"][0][1] == "asc"] == list(CHIPS)
    assert all(c["limit"] == FETCH_LIMIT < 64 for c in calls)
    assert "H100 SXM" in CHIPS


@responses.activate
def test_vast_full_book_is_read_from_both_ends() -> None:
    """A full ascending book triggers the descending read, and rows from both are kept."""
    template = json.loads(FIXTURE.read_text(encoding="utf-8"))["offers"][0]
    offers = []
    for i in range(2 * FETCH_LIMIT + 10):
        o = dict(template, id=10_000 + i, gpu_name="H100 SXM", num_gpus=8,
                 dph_total=16.0 + i * 0.1, verification="verified", hosting_type=1,
                 geolocation="Noord-Holland, NL", min_bid=None)
        offers.append(o)
    calls: list[dict] = []
    responses.add_callback(
        responses.POST, VAST_URL, callback=_vast_callback({"offers": offers}, calls)
    )
    out = VastAiCollector(spacing_seconds=0).collect(base.make_session())
    h100 = [o for o in out if o.gpu_model == "H100_SXM"]
    assert len(h100) == 2 * FETCH_LIMIT  # 50 cheapest + 50 dearest, 10 in the middle unseen
    raw = json.loads(h100[0].raw_json)
    assert raw["book"]["possibly_truncated"] is True
    assert sum(1 for c in calls if c["gpu_name"]["in"] == ["H100 SXM"]) == 2


@responses.activate
def test_vast_identity_pin_drops_mislabelled_offers() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    wrong = [dict(o, gpu_name="RTX 3060") for o in data["offers"]]
    responses.add(responses.POST, VAST_URL, json={"offers": wrong}, status=200)
    import pytest

    with pytest.raises(RuntimeError, match="zero offers"):
        VastAiCollector(spacing_seconds=0).collect(base.make_session())


def test_static_yaml_emits_only_priced_entries() -> None:
    out = StaticYamlCollector().collect(base.make_session())
    providers = {o.provider for o in out}
    assert "nebius" in providers and "seeweb" in providers
    assert all(o.tier == "list" and o.price_usd_per_gpu_hr > 0 for o in out)
    assert all(json.loads(o.raw_json).get("last_verified") for o in out)


def test_static_yaml_passes_through_native_currency() -> None:
    """seeweb quotes EUR; the raw price must reach normalise.py unconverted, with a
    currency tag, so print-time FX is used instead of a rate baked into the yaml."""
    out = StaticYamlCollector().collect(base.make_session())
    by_provider = {o.provider: o for o in out}

    seeweb = by_provider["seeweb"]
    raw = json.loads(seeweb.raw_json)
    assert raw["currency"] == "EUR"
    assert raw["price_native_per_gpu_hr"] == seeweb.price_usd_per_gpu_hr == 1.89

    nebius = by_provider["nebius"]
    assert json.loads(nebius.raw_json).get("currency", "USD") == "USD"


class _BoomCollector:
    name = "boom"

    def collect(self, session: requests.Session) -> list[Observation]:
        raise RuntimeError("source exploded")


class _OkCollector:
    name = "okc"

    def __init__(self) -> None:
        self.calls = 0

    def collect(self, session: requests.Session) -> list[Observation]:
        self.calls += 1
        return []


def test_fail_soft_records_failure_and_run_continues(conn: sqlite3.Connection) -> None:
    status1 = base.run_collector(conn, _BoomCollector(), "2026-07-18")
    status2 = base.run_collector(conn, _OkCollector(), "2026-07-18")
    assert (status1, status2) == ("failed", "ok")
    rows = {
        r["source"]: r["status"]
        for r in conn.execute("SELECT source, status FROM runs")
    }
    assert rows == {"boom": "failed", "okc": "ok"}


def test_failed_collector_is_retried_next_run(conn: sqlite3.Connection) -> None:
    base.run_collector(conn, _BoomCollector(), "2026-07-18")
    # a failure must not satisfy idempotency — the next daily run retries the source
    status = base.run_collector(conn, _OkCollector(), "2026-07-18")
    assert status == "ok"
    boom_again = base.run_collector(conn, _BoomCollector(), "2026-07-18")
    assert boom_again == "failed"
