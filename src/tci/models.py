# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Shared dataclasses."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Observation:
    """One raw price observation, exactly as collected (persisted immutably)."""

    ts_utc: str
    source: str
    provider: str
    gpu_model: str
    gpu_count: int | None
    price_usd_per_gpu_hr: float
    region: str | None
    country: str | None
    interconnect: str | None
    tier: str  # 'executable' | 'list'
    term: str
    raw_json: str


@dataclass(frozen=True)
class MarketOffer:
    """One offer as a marketplace returned it, admitted to the index or not.

    Stored in `market_offers`, which nothing in the calculation path reads. It exists so a
    within-venue analysis can see the whole book the collector read, rather than only the
    datacenter-verified subset that becomes `observations`.
    """

    ts_utc: str
    source: str
    queried_name: str
    offer_id: str | None
    machine_id: str | None
    host_id: str | None
    gpu_model: str | None
    num_gpus: int | None
    dph_total: float | None
    country: str | None
    verification: str | None
    hosting_type: int | None
    in_index_scope: bool
    raw_json: str


@dataclass(frozen=True)
class Constituent:
    """A provider's contribution to one print (audit row).

    For included constituents of a published print, `weight` is the final print share
    (all included shares sum to 100, post concentration cap). For excluded candidates
    and gapped prints it is the raw pre-cap weight.
    """

    provider: str
    source: str
    tier: str
    price_usd: float
    weight: float
    included: bool
    exclusion_reason: str | None = None
    flags: str = ""


@dataclass(frozen=True)
class IndexPrint:
    date: str
    series: str
    value_usd: float | None
    value_eur: float | None
    fx_rate: float | None
    fx_date: str | None
    n_sources: int
    n_executable: int
    flags: str
    constituents: tuple[Constituent, ...]
