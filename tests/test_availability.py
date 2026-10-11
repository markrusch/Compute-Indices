"""Stock as sellers publish it: read honestly, and never mistaken for its absence."""

from __future__ import annotations

import sqlite3

import pytest

from tci import availability, db


@pytest.fixture(scope="module")
def record() -> sqlite3.Connection:
    return db.connect_readonly()


def test_the_eu_table_lists_only_sellers_that_price_the_eu(record: sqlite3.Connection) -> None:
    r = availability.reading(record, "2026-10-11")
    names = {s.provider for s in r.sellers}
    # Admitted to the H100 class, never priced in the EU/EEA in the window.
    assert not names & {"voltagepark", "coreweave", "civo", "together"}
    assert {"runpod", "vast.ai", "lambdalabs"} <= names


def test_a_stock_out_reads_as_no_stock(record: sqlite3.Connection) -> None:
    r = availability.reading(record, "2026-09-24")
    by = {s.provider: s for s in r.sellers}
    assert by["runpod"].node2 == "no"
    assert by["vast.ai"].node2 == "no"
    assert r.with_stock == 0


def test_before_stock_was_recorded_nothing_is_claimed(record: sqlite3.Connection) -> None:
    # RunPod's per-node stock is stored from 5 September 2026 and the vast.ai book from 14
    # September. Before that the answer is unknown, which must not read as "none".
    r = availability.reading(record, "2026-09-01")
    assert r.runpod_node2 is None and r.vast_gpus is None
    assert r.publishing == 0
    assert all(s.node2 is None for s in r.sellers)


def test_sellers_that_publish_no_stock_are_never_counted_as_in_stock(
    record: sqlite3.Connection,
) -> None:
    r = availability.reading(record, "2026-10-11")
    for s in r.sellers:
        if not s.publishes_stock:
            assert s.node2 is None and s.node8 is None
    assert r.with_stock <= r.publishing <= r.publishers


def test_runpods_one_label_counts_in_the_eu_only_with_an_eu_datacentre(
    record: sqlite3.Connection,
) -> None:
    # 9 October 2026: RunPod's all-regions label said Low at 2+ GPUs while no EU/EEA
    # datacentre reported H100 stock. Counting the label put RunPod among the EU sellers
    # with stock on the one day the page's own finding says it had none.
    r = availability.reading(record, "2026-10-09")
    rp = next(s for s in r.sellers if s.provider == "runpod")
    assert r.runpod_eu_datacentres == 0 and r.runpod_node2 not in (None, "no")
    assert rp.node2 == "no" and rp.node8 == "no"


def test_before_the_datacentre_list_runpods_eu_stock_is_unknown(
    record: sqlite3.Connection,
) -> None:
    r = availability.reading(record, "2026-09-06")
    rp = next(s for s in r.sellers if s.provider == "runpod")
    assert r.runpod_eu_datacentres is None and r.runpod_node2 is not None
    assert rp.node2 is None
