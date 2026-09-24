# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Manually verified price entries for EU neoclouds without stable public APIs.

Each config/providers/*.yaml carries a `last_verified` date. Entries are warned at
staleness.warn_days; the index calculation excludes them at staleness.exclude_days
(exclusion happens in index.py so the raw observation is still recorded honestly).
A null price means the provider is skipped entirely — a price is never invented.

A yaml may carry `currency` (default USD) alongside `price_usd_per_gpu_hr`, which then
holds the *native* quoted amount rather than a USD one (seeweb quotes EUR) — same
stopgap field-naming as `collectors/scaleway.py`. Both currency and the native amount are
passed through in raw_json so normalise.py converts at print time with that day's ECB
rate, per its own module docstring: conversion never gets frozen into a stored
observation, because a rate looked up once by hand goes stale the moment EUR/USD moves.

ONLY WHAT THE PANEL STILL READS. An entry is emitted only while the methodology version
in effect on the collection date admits `static_yaml` for that provider. Until 24
September 2026 every priced file was collected whether or not any version could use it,
so datacrunch and nebius kept producing hand-maintained rows for days after v0.5.0 had
replaced both with catalogue feeds. v0.7.0 (effective 2026-10-08, notice 2026-N4) admits
static_yaml for no provider, and from that date this collector emits nothing without
anyone having to remember to switch it off. config/providers/ can then be deleted.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime

import requests

from tci.config import load_factors, load_static_providers
from tci.db import utc_now_iso
from tci.models import Observation

log = logging.getLogger("tci.collectors.static_yaml")


class StaticYamlCollector:
    name = "static_yaml"

    def __init__(self, today: date | None = None) -> None:
        self._today = today

    def admitted(self, on_date: date) -> set[str] | None:
        """Providers the version live on `on_date` reads through this collector.

        None for a parameter set without an explicit panel (before v0.4.0), where every
        collected row was a candidate.
        """
        panel = load_factors(for_date=on_date.isoformat()).panel
        if panel is None:
            return None
        return {p for p, entry in panel.items() if self.name in entry.sources}

    def collect(self, session: requests.Session) -> list[Observation]:
        today = self._today or datetime.now(UTC).date()
        factors = load_factors(for_date=today.isoformat())
        admitted = self.admitted(today)
        out: list[Observation] = []
        for p in load_static_providers():
            if admitted is not None and p.provider not in admitted:
                log.info("%s: not read through static_yaml by the panel on %s, skipped",
                         p.provider, today)
                continue
            if p.price_usd_per_gpu_hr is None:
                log.warning("%s: no verified price, skipped", p.provider)
                continue
            if p.last_verified:
                age = (today - datetime.strptime(p.last_verified, "%Y-%m-%d").date()).days
                if age > factors.staleness.warn_days:
                    log.warning(
                        "%s: last_verified %s is %d days old (excluded from index at %d)",
                        p.provider, p.last_verified, age, factors.staleness.exclude_days,
                    )
            currency = str(p.extra.get("currency") or "USD").upper()
            price_native = float(p.price_usd_per_gpu_hr)
            out.append(
                Observation(
                    ts_utc=utc_now_iso(),
                    source=self.name,
                    provider=p.provider,
                    gpu_model=p.gpu_model,
                    gpu_count=p.gpu_count,
                    price_usd_per_gpu_hr=price_native,
                    region=None,
                    country=p.country,
                    interconnect="NVLink",
                    tier="list",
                    term="on_demand",
                    raw_json=json.dumps(
                        {"url": p.url, "last_verified": p.last_verified,
                         "config_notes": p.config_notes,
                         "currency": currency,
                         "price_native_per_gpu_hr": price_native}
                    ),
                )
            )
        return out
