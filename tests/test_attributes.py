# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""L4.1: what sellers declare, and the line between that and what TCI worked out.

The rule under test throughout is the one the layer exists for. A seller that publishes a
field gets `declared`. A value TCI read off a product name gets `derived` and carries the
rule. A seller that publishes nothing gets `not published` and says so. Nothing is ever
filled in from a neighbouring offer, a sibling SKU, or what the chip usually ships with.
"""

from __future__ import annotations

import json

import pytest

from tci import attributes
from tests.conftest import insert_run


def _row(source: str, raw: dict, tier: str = "list", interconnect: str | None = None,
         gpu_count: int = 8) -> dict:
    return {
        "source": source, "raw_json": json.dumps(raw), "tier": tier,
        "interconnect": interconnect, "gpu_count": gpu_count,
    }


def _get(row: dict, name: str) -> attributes.Attribute:
    return next(a for a in attributes.for_observation(row) if a.name == name)


def test_a_published_field_is_declared() -> None:
    attr = _get(_row("voltagepark", {"extra": {"network": "ethernet"}}), "interconnect")
    assert attr.value == "ethernet"
    assert attr.provenance == attributes.DECLARED
    assert attr.detail == "extra.network"


def test_a_value_read_off_the_product_name_is_derived_and_says_so() -> None:
    """The whole stored `interconnect` column is this: Azure from `isr`/`noIB`, gpuhunt
    and Scaleway from `SXM`, static entries hardcoded. None of it is anybody's statement."""
    attr = _get(_row("scaleway", {}, interconnect="NVLink"), "interconnect")
    assert attr.value == "NVLink"
    assert attr.provenance == attributes.DERIVED
    assert "not published as a field" in attr.detail


def test_a_seller_that_publishes_nothing_gets_nothing() -> None:
    attr = _get(_row("digitalocean", {}), "vcpus")
    assert attr.value is None
    assert attr.provenance == attributes.ABSENT
    assert "digitalocean" in attr.detail


def test_absent_is_its_own_provenance() -> None:
    """`declared` with a null value would say the seller declared nothing, which is a
    contradiction dressed up as a field."""
    assert attributes.ABSENT not in (attributes.DECLARED, attributes.DERIVED)
    attr = _get(_row("crusoe", {}), "local_storage")
    assert not attr.stated and attr.provenance == attributes.ABSENT


def test_values_keep_the_units_the_seller_published() -> None:
    """Lambda says "2900 GiB" and Voltage Park says 1024. Converting them to a common
    unit would make the number TCI's claim instead of the seller's."""
    lam = _get(_row("lambda_pricing", {"extra": {"ram": "2900 GiB"}}), "system_memory")
    vp = _get(_row("voltagepark", {"extra": {"ram_gb": 1024}}), "system_memory")
    assert lam.value == "2900 GiB"
    assert vp.value == "1024 GB"


def test_a_unit_conversion_on_a_published_number_is_still_declared() -> None:
    """vast.ai publishes gpu_ram in MiB. Dividing by 1024 is arithmetic on its number."""
    attr = _get(_row("vast_ai", {"gpu_ram": 81559}), "gpu_memory")
    assert attr.value == "80 GB"
    assert attr.provenance == attributes.DECLARED
    assert attr.detail == "gpu_ram"


def test_an_empty_string_is_not_a_declared_value() -> None:
    attr = _get(_row("voltagepark", {"extra": {"network": "   "}}), "interconnect")
    assert attr.value is None


def test_unparseable_raw_json_does_not_raise() -> None:
    """A stored row is whatever the collector wrote. This reads the record, so it reads
    every row in it, including the ones written before a schema settled."""
    row = {"source": "vast_ai", "raw_json": "not json", "tier": "list",
           "interconnect": None, "gpu_count": 8}
    assert len(attributes.for_observation(row)) == len(attributes.ATTRIBUTES)
    row["raw_json"] = "[1, 2, 3]"  # valid json, wrong shape
    assert len(attributes.for_observation(row)) == len(attributes.ATTRIBUTES)


def test_every_attribute_is_answered_for_every_row() -> None:
    """Silence about an attribute is not an option: each one gets a value or a reason."""
    got = attributes.for_observation(_row("digitalocean", {}))
    assert [a.name for a in got] == [n for n, _ in attributes.ATTRIBUTES]


@pytest.mark.parametrize(
    "tier,expected",
    [("executable", "bookable"), ("list", "rate card"), ("spot", "spot"),
     ("interruptible", "interruptible")],
)
def test_contract_form_names_the_tier_the_price_came_from(tier: str, expected: str) -> None:
    attr = _get(_row("runpod", {}, tier=tier), "contract_form")
    assert attr.value is not None and expected in attr.value


def test_offers_that_disagree_are_reported_as_disagreeing(conn) -> None:
    """vast.ai is a marketplace of independent hosts, so one seller's offers genuinely
    differ. Printing one of them as the seller's attribute would invent a fact about a
    named company out of an arbitrary row."""
    insert_run(conn, "r1", "2026-09-12", "vast_ai")
    for gpus in (1, 2, 4):
        conn.execute(
            "INSERT INTO observations (run_id, ts_utc, source, provider, gpu_model,"
            " gpu_count, price_usd_per_gpu_hr, region, country, interconnect, tier, term,"
            " raw_json) VALUES ('r1', '2026-09-12T11:00:00Z', 'vast_ai', 'vast.ai',"
            " 'H100_SXM', ?, 3.0, 'eu', 'NL', NULL, 'executable', 'on_demand', ?)",
            (gpus, json.dumps({"num_gpus": gpus, "gpu_ram": 81559})),
        )
    conn.commit()

    attrs = attributes.for_provider(conn, "2026-09-12", "vast.ai", "vast_ai", "H100_SXM")
    per_node = next(a for a in attrs if a.name == "gpus_per_node")
    assert "varies" in str(per_node.value)
    assert "3 distinct values" in per_node.detail
    # Where every offer agrees, the agreed value is reported rather than "varies".
    memory = next(a for a in attrs if a.name == "gpu_memory")
    assert memory.value == "80 GB"


def test_a_seller_with_no_offers_that_day_says_so(conn) -> None:
    attrs = attributes.for_provider(conn, "2026-09-12", "nobody", "nowhere")
    assert all(a.provenance == attributes.ABSENT for a in attrs)
    assert all("no stored offer" in a.detail for a in attrs)


def test_coverage_counts_only_what_sellers_publish(conn) -> None:
    """A derived value must not inflate the figure the layer exists to produce."""
    rows = [
        _row("voltagepark", {"extra": {"network": "ethernet"}}),
        _row("scaleway", {}, interconnect="NVLink"),   # derived
        _row("digitalocean", {}),                      # absent
    ]
    assert attributes.coverage(rows)["interconnect"] == (1, 3)
