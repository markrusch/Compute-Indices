# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Seeweb pricing-page collector. Fixture-driven, no live network.

The fixture (tests/fixtures/seeweb_cloud_server_gpu.html) is a trimmed excerpt of
https://www.seeweb.it/en/products/cloud-server-gpu captured live 2026-09-15, keeping the
H200, H100 and A100 card blocks verbatim so the parser is exercised against real markup,
not an invented shape.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests
import responses

from tci.collectors import base
from tci.collectors.seeweb import URL, SeewebCollector

FIXTURE = (Path(__file__).parent / "fixtures" / "seeweb_cloud_server_gpu.html").read_text(
    encoding="utf-8"
)


def _rows() -> list:
    return SeewebCollector().parse(FIXTURE)


# --- on-demand + term parsing ----------------------------------------------------------

def test_parses_on_demand_and_all_three_committed_prices() -> None:
    rows = _rows()
    by_term = {o.term: o for o in rows}
    assert set(by_term) == {"on_demand", "commit_3mo", "commit_6mo", "reserved_1yr"}
    assert by_term["on_demand"].price_usd_per_gpu_hr == 1.89
    assert by_term["commit_3mo"].price_usd_per_gpu_hr == 1.80
    assert by_term["commit_6mo"].price_usd_per_gpu_hr == 1.70
    assert by_term["reserved_1yr"].price_usd_per_gpu_hr == 1.61


def test_picks_the_h100_card_not_the_neighbouring_h200_or_a100() -> None:
    """H200 sorts immediately before H100 in the fixture (as on the live page), and A100
    right after -- proof the match is on the card's own name, not position or a loose
    substring match against "H100" somewhere else on the page."""
    rows = _rows()
    prices = {round(o.price_usd_per_gpu_hr, 2) for o in rows}
    assert 2.60 not in prices and 2.47 not in prices  # H200's on-demand and 3mo rates
    assert 1.15 not in prices and 1.09 not in prices  # A100's on-demand and 3mo rates


def test_gpu_model_country_interconnect_and_count() -> None:
    for o in _rows():
        assert o.gpu_model == "H100_SXM"
        assert o.country == "IT"
        assert o.interconnect == "NVLink"
        # The largest size on the card's own selector, not its 1-GPU default: a 1-GPU
        # row fails the 2-GPU node-size floor and could never enter a print.
        assert o.gpu_count == 8
        assert json.loads(o.raw_json)["node_sizes_offered"] == [1, 2, 4, 8]
        assert o.provider == "seeweb"
        assert o.source == "seeweb"


# --- tenor vocabulary --------------------------------------------------------------

def test_tenor_labels_match_the_shared_vocabulary() -> None:
    """Same tenor strings as term.py's TENOR_MONTHS and computable_sources.py's
    TERM_BY_MONTHS -- 3 months -> commit_3mo, 6 -> commit_6mo, 12 -> reserved_1yr."""
    from tci.term import TENOR_MONTHS

    committed_terms = {o.term for o in _rows() if o.term != "on_demand"}
    assert committed_terms == {"commit_3mo", "commit_6mo", "reserved_1yr"}
    assert TENOR_MONTHS["commit_3mo"] == 3
    assert TENOR_MONTHS["commit_6mo"] == 6
    assert TENOR_MONTHS["reserved_1yr"] == 12


def test_all_rows_are_tier_list_including_the_term_rows() -> None:
    """Rate-card prices, not executable quotes -- matching how azure_retail.py records
    its Reservation meters as tier=list rather than inventing a new tier."""
    assert all(o.tier == "list" for o in _rows())


# --- EUR / native-currency convention -----------------------------------------------

def test_raw_json_carries_native_eur_not_a_baked_in_fx_rate() -> None:
    for o in _rows():
        raw = json.loads(o.raw_json)
        assert raw["currency"] == "EUR"
        # Same stopgap as static_yaml.py/scaleway.py: price_usd_per_gpu_hr actually
        # holds the native (EUR) amount; normalise.py does the FX conversion at print
        # time, never here.
        assert raw["price_native_per_gpu_hr"] == o.price_usd_per_gpu_hr
        assert "fx" not in raw and "usd" not in json.dumps(raw).lower()


# --- fail loud, not fail soft and guess ---------------------------------------------

def test_raises_when_no_h100_card_is_found() -> None:
    reshaped = FIXTURE.replace(
        '<span class="cardname">NVIDIA H100</span>', '<span class="cardname">NVIDIA H100X</span>'
    )
    assert reshaped != FIXTURE, "fixture no longer carries the cardname this test edits"
    with pytest.raises(RuntimeError, match="NVIDIA H100"):
        SeewebCollector().parse(reshaped)


def test_raises_when_the_on_demand_price_markup_changes() -> None:
    reshaped = FIXTURE.replace(
        '<p class="hourly"><span>1.89</span> &euro;</p>',
        '<p class="hourly-price"><span>1.89</span> &euro;</p>',
        1,
    )
    assert reshaped != FIXTURE, "fixture no longer carries the markup this test edits"
    with pytest.raises(RuntimeError, match="on-demand"):
        SeewebCollector().parse(reshaped)


def test_raises_when_a_committed_price_markup_changes() -> None:
    # Scoped to the H100 card specifically (via its own 1.61 price) — H200's card carries
    # the same class name earlier in the fixture, and a plain replace(..., 1) would edit
    # that one instead, leaving the H100 reshape untested.
    reshaped = FIXTURE.replace(
        'class="hourly_12mnths">12 mths: <span>1.61</span>',
        'class="hourly-12mnths">12 mths: <span>1.61</span>',
        1,
    )
    assert reshaped != FIXTURE, "fixture no longer carries the markup this test edits"
    with pytest.raises(RuntimeError, match="reserved_1yr"):
        SeewebCollector().parse(reshaped)


def test_raises_when_the_h100_card_loses_its_gpu_count_selector() -> None:
    card = FIXTURE.index('<span class="cardname">NVIDIA H100</span>')
    select = FIXTURE.index('name="card-number"', card)
    reshaped = FIXTURE[:select] + 'name="gpu-number"' + FIXTURE[select + len('name="card-number"'):]
    with pytest.raises(RuntimeError, match="GPU-count selector"):
        SeewebCollector().parse(reshaped)


def test_raises_when_no_gpu_cards_exist_at_all() -> None:
    with pytest.raises(RuntimeError, match="no GPU card blocks"):
        SeewebCollector().parse("<html><body>nothing here</body></html>")


def test_never_emits_a_partial_row_on_a_reshaped_page() -> None:
    """A collector that can find the card but not every price must raise, not emit the
    prices it did find plus a gap -- a partial rate card is not a safe thing to store."""
    # Scoped to the H100 card via its own 1.70 price -- see the comment in
    # test_raises_when_a_committed_price_markup_changes above.
    reshaped = FIXTURE.replace(
        'class="hourly_6mnths">6 mths: <span>1.70</span>',
        'class="hourly-6mnths">6 mths: <span>1.70</span>',
        1,
    )
    assert reshaped != FIXTURE, "fixture no longer carries the markup this test edits"
    with pytest.raises(RuntimeError):
        SeewebCollector().parse(reshaped)


# --- end-to-end collect() over HTTP --------------------------------------------------

@responses.activate
def test_collect_makes_one_request_and_returns_four_rows() -> None:
    responses.add(responses.GET, URL, body=FIXTURE, status=200)
    out = SeewebCollector().collect(base.make_session())
    assert len(out) == 4
    assert len(responses.calls) == 1


@responses.activate
def test_collect_propagates_an_http_error_rather_than_guessing() -> None:
    responses.add(responses.GET, URL, status=503)
    with pytest.raises(requests.RequestException):
        SeewebCollector().collect(base.make_session())
