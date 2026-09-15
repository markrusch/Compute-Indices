# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""vast.ai reserved (prepaid) quotes for H100 SXM at 30, 90 and 180 days.

WHY A SECOND vast.ai COLLECTOR. vast.ai prices a reserved rental only when the search names a
duration. The ordinary on-demand book stored by `vast_ai` carries `discounted_dph_total`
equal to `dph_total` on every offer, and `discount_rate` of zero or null. Asked on 15
September 2026 for reserved H100 SXM with at least 30, 90 and 180 days, the same endpoint
returned a verified Czech host (214845) at 0.9751, 0.9626 and 0.9573 of its on-demand price.
Seven of the eight other offers quoted no discount at all. These are prices one host set for
one chip and tenor, which no rate card supplies, and they are what the forward estimate's
term diagnostic can use.

WHAT IS STORED. Every offer each query returns, in `term_quotes` with the requested duration,
and `in_index_scope` from the same datacenter-verified test the `vast_ai` collector applies.
No `observations` rows: a prepaid quote is not an on-demand price, and nothing in the
calculation path reads `term_quotes`. Whether a quote is a term price at all (a discount
strictly below on-demand) is decided where it is used, so the record keeps the zeros.

REQUESTS. Three a day, one per duration, H100 SXM only. The daily run calls this collector
after `vast_ai`, which has just made nine to eighteen requests at 0.75-second spacing; on 15
September a third unspaced request returned HTTP 429. So this collector waits eight seconds
before every request, including the first, honours a 429's Retry-After once (capped at 60
seconds), and records a duration it still could not read as a partial failure rather than
retrying further.

FAIL LOUD ON AN EMPTY BOOK. Three durations and not one offer between them is a changed API
or a filter that stopped matching, not a quiet market, so the run is recorded as failed.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable

import requests

from tci.collectors.base import TIMEOUT_SECONDS
from tci.collectors.vast_ai import (
    GPU_MODEL_MAP,
    RAW_FIELDS,
    URL,
    _country,
    _str_or_none,
    chip_query,
    in_index_scope,
)
from tci.db import utc_now_iso
from tci.models import Observation, TermQuote

log = logging.getLogger("tci.collectors.vast_reserved")

CHIPS: tuple[str, ...] = ("H100 SXM",)
DURATIONS_DAYS: tuple[int, ...] = (30, 90, 180)
SPACING_SECONDS = 8.0
MAX_RETRY_AFTER_SECONDS = 60.0
QUERY_LIMIT = 64
EXTRA_FIELDS = ("discount_rate", "discounted_hourly", "end_date")


def reserved_query(gpu_name: str, days: int) -> dict:
    query = chip_query(gpu_name)
    query.update({"type": "reserved", "duration": {"gte": days * 86400}, "limit": QUERY_LIMIT})
    return query


def to_term_quotes(offers: list[dict], ts: str, queried: str, days: int) -> list[TermQuote]:
    out: list[TermQuote] = []
    for offer in offers:
        raw = {k: offer.get(k) for k in RAW_FIELDS + EXTRA_FIELDS}
        raw["queried_gpu_name"] = queried
        raw["requested_days"] = days
        model_map = GPU_MODEL_MAP.get(offer.get("gpu_name", ""))
        hosting_type = offer.get("hosting_type")
        dph = offer.get("dph_total")
        discounted = offer.get("discounted_dph_total")
        duration = offer.get("duration")
        out.append(TermQuote(
            ts_utc=ts, source=VastReservedCollector.name, queried_name=queried,
            requested_days=days,
            offer_id=_str_or_none(offer.get("id")),
            machine_id=_str_or_none(offer.get("machine_id")),
            host_id=_str_or_none(offer.get("host_id")),
            gpu_model=model_map[0] if model_map else None,
            num_gpus=offer.get("num_gpus"),
            country=_country(offer.get("geolocation")),
            verification=offer.get("verification"),
            hosting_type=hosting_type if isinstance(hosting_type, int) else None,
            dph_total=None if dph is None else float(dph),
            discounted_dph_total=None if discounted is None else float(discounted),
            max_duration_days=None if duration is None else float(duration) / 86400.0,
            in_index_scope=in_index_scope(offer),
            raw_json=json.dumps(raw),
        ))
    return out


class VastReservedCollector:
    name = "vast_reserved"

    def __init__(self, spacing_seconds: float = SPACING_SECONDS,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.spacing_seconds = spacing_seconds
        self.sleep = sleep
        self.term_quotes: list[TermQuote] = []

    def _post(self, session: requests.Session, query: dict) -> list[dict]:
        resp = session.post(URL, json=query, timeout=TIMEOUT_SECONDS)
        if resp.status_code == 429:
            try:
                wait = float(resp.headers.get("Retry-After", self.spacing_seconds))
            except ValueError:
                wait = self.spacing_seconds
            self.sleep(min(max(wait, 0.0), MAX_RETRY_AFTER_SECONDS))
            resp = session.post(URL, json=query, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
        wanted = query["gpu_name"]["in"][0]
        return [o for o in resp.json().get("offers") or [] if o.get("gpu_name") == wanted]

    def collect(self, session: requests.Session) -> list[Observation]:
        ts = utc_now_iso()
        self.term_quotes = []
        failures: list[str] = []
        read = 0
        for gpu_name in CHIPS:
            for days in DURATIONS_DAYS:
                self.sleep(self.spacing_seconds)
                try:
                    offers = self._post(session, reserved_query(gpu_name, days))
                except (requests.RequestException, ValueError) as exc:
                    failures.append(f"{gpu_name} {days}d: {type(exc).__name__}")
                    continue
                read += len(offers)
                self.term_quotes.extend(to_term_quotes(offers, ts, gpu_name, days))
        if read == 0:
            raise RuntimeError(f"vast_reserved: no offers read for any duration {failures}")
        if failures:
            log.warning("vast_reserved: partial read, failed: %s", failures)
        log.info("vast_reserved: %d quotes", len(self.term_quotes))
        return []
