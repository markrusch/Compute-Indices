# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Seeweb's public GPU pricing page — on-demand plus its published 3/6/12-month
commitment rate for the NVIDIA H100 SXM configuration.

WHY A SEPARATE COLLECTOR. Seeweb's on-demand price is already carried by hand in
config/providers/seeweb.yaml (StaticYamlCollector), because the page was originally
screened as one more JS-only neocloud pricing surface not worth automating. It is not:
https://www.seeweb.it/en/products/cloud-server-gpu is server-rendered HTML, confirmed by
a live fetch on 2026-09-15 with no JavaScript required, and it states three more prices
the static entry has never carried — a 3-, 6- and 12-month committed rate on the same
H100 SXM card. This collector reads all four numbers from the one page. It is additive:
config/providers/seeweb.yaml and static_yaml.py are untouched, and this collector stores
its rows under `source="seeweb"`, a source name factors.yaml's panel does not list for
the `seeweb` provider (only `static_yaml` is), so nothing here reaches a print until a
methodology version admits it — see CLAUDE.md's "the panel decides admission".

WHAT IS MEASURED. Exactly the NVIDIA H100 SXM card (`cardname` "NVIDIA H100", the only
card of that name; Seeweb also sells an H200 card the same page labels "NVIDIA H200",
which this collector does not touch). Four prices per day:

    on_demand    tier=list, term=on_demand      -- the card's headline hourly EUR rate
    commit_3mo   tier=list, term=commit_3mo     -- "3 mths" committed rate
    commit_6mo   tier=list, term=commit_6mo     -- "6 mths" committed rate
    reserved_1yr tier=list, term=reserved_1yr   -- "12 mths" committed rate

All four are still list prices, not executable quotes -- a rate card, not something this
collector can transact against -- so all four carry tier="list", matching how
azure_retail.py records its Reservation meters (tier=list, term=reserved_1yr etc.) rather
than inventing a new tier for a committed rate. Tenor labels are the vocabulary already
shared by term.py's TENOR_MONTHS and computable_sources.py's TERM_BY_MONTHS: no new tenor
name is introduced here.

GPU COUNT. Recorded as 1, the page's default GPU-count selector state. The static yaml
entry's own config_notes record that Seeweb's H100 pricing is confirmed linear per-GPU
(an 8x SXM config lists at EUR 15.12/hr = EUR 1.89/GPU-hr, the same rate as 1x), so 1 is
not a small-order guess -- it is the priced unit the page actually renders before any
selector interaction, at the same per-GPU rate every other node size resolves to.

NATIVE CURRENCY, NOT A BAKED-IN RATE. Same stopgap field-naming as static_yaml.py and
scaleway.py: `price_usd_per_gpu_hr` on Observation actually holds the EUR amount, and
`currency: "EUR"` plus `price_native_per_gpu_hr` in raw_json is what normalise.py reads
to convert at print time with that day's ECB rate. No FX rate is ever computed here.

FAIL LOUD, NOT FAIL SOFT AND GUESS. Same philosophy as vast_ai.py (raises when zero
offers come back) and latitude_annual.py (raises rather than guessing at an unreadable
plan spec). If the H100 SXM card cannot be found by name, or any of the four prices
cannot be found inside it, this raises RuntimeError instead of emitting a partial or
interpolated row -- the exception is caught by collectors.base.run_collector, same as
every other collector, and the day is recorded as a failed run with a reason rather than
a quietly wrong price.

ONE REQUEST PER DAY. A single GET of the pricing page reads all four numbers; there is
no second request for the term prices (SOURCES.md: "1 request per source per day").

Parsing is regex-based over the raw HTML rather than a DOM library, matching the house
style already used for latitude_annual.py and the vendored Computable recipes. The page
renders each GPU as its own `<div id="gpuN" class="cardType...">` block in card order
(gpu1..gpu10 as read 2026-09-15, not in numeric id order); this collector windows the
page between consecutive `id="gpuN"` markers and searches inside the window whose
`<span class="cardname">` text is exactly "NVIDIA H100", so a reordering or a renumbering
of the cards does not matter -- only the card's own name and its own prices do.
"""

from __future__ import annotations

import json
import logging
import re

import requests

from tci.collectors.base import TIMEOUT_SECONDS
from tci.db import utc_now_iso
from tci.models import Observation

log = logging.getLogger("tci.collectors.seeweb")

URL = "https://www.seeweb.it/en/products/cloud-server-gpu"

CARD_NAME = "NVIDIA H100"
GPU_MODEL = "H100_SXM"
COUNTRY = "IT"
# The page's default GPU-count selector state; see module docstring "GPU COUNT".
GPU_COUNT = 1

_CARD_START_RE = re.compile(r'<div id="gpu\d+"')
_CARDNAME_RE = re.compile(r'<span class="cardname">([^<]+)</span>')
_HOURLY_RE = re.compile(r'<p class="hourly"><span>([\d.]+)</span>')

# Committed-rate spans keyed by their own CSS class -> the TCI tenor it becomes.
# Same tenor vocabulary as term.py's TENOR_MONTHS / computable_sources.py's
# TERM_BY_MONTHS -- no new tenor name is introduced here.
_TERM_PATTERNS: dict[str, re.Pattern[str]] = {
    "commit_3mo": re.compile(r'class="hourly_3mnths">[^<]*<span>([\d.]+)</span>'),
    "commit_6mo": re.compile(r'class="hourly_6mnths">[^<]*<span>([\d.]+)</span>'),
    "reserved_1yr": re.compile(r'class="hourly_12mnths">[^<]*<span>([\d.]+)</span>'),
}


def _h100_card(html: str) -> str:
    """The HTML slice for the card named exactly "NVIDIA H100", or raise.

    Windows the page between consecutive `id="gpuN"` markers (see module docstring) so
    the search is robust to the cards being reordered or renumbered; it is not robust to
    the page dropping the H100 card, its cardname span, or the id="gpuN" markers
    entirely -- any of which is exactly the kind of reshape that must fail loudly rather
    than silently emit nothing or an interpolated guess.
    """
    starts = [m.start() for m in _CARD_START_RE.finditer(html)]
    if not starts:
        raise RuntimeError(
            "seeweb: no GPU card blocks (id=\"gpuN\") found on the pricing page — "
            "page shape changed"
        )
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(html)
        window = html[start:end]
        name = _CARDNAME_RE.search(window)
        if name and name.group(1).strip() == CARD_NAME:
            return window
    raise RuntimeError(
        f"seeweb: no card named {CARD_NAME!r} found among {len(starts)} GPU cards — "
        "page shape changed (or Seeweb dropped the H100 SXM configuration)"
    )


def _price(card: str, pattern: re.Pattern[str], label: str) -> float:
    m = pattern.search(card)
    if not m:
        raise RuntimeError(
            f"seeweb: could not find the {label} price inside the {CARD_NAME!r} card — "
            "page shape changed"
        )
    return float(m.group(1))


class SeewebCollector:
    name = "seeweb"

    def collect(self, session: requests.Session) -> list[Observation]:
        resp = session.get(URL, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
        return self.parse(resp.text)

    def parse(self, html: str) -> list[Observation]:
        card = _h100_card(html)
        on_demand = _price(card, _HOURLY_RE, "on-demand")
        committed = {
            term: _price(card, pattern, term) for term, pattern in _TERM_PATTERNS.items()
        }

        ts = utc_now_iso()

        def observation(term: str, price_eur: float) -> Observation:
            return Observation(
                ts_utc=ts,
                source=self.name,
                provider="seeweb",
                gpu_model=GPU_MODEL,
                gpu_count=GPU_COUNT,
                # EUR, not USD -- see module docstring "NATIVE CURRENCY". normalise.py
                # converts at print time from raw_json, never at collection.
                price_usd_per_gpu_hr=price_eur,
                region=None,
                country=COUNTRY,
                interconnect="NVLink",
                tier="list",
                term=term,
                raw_json=json.dumps(
                    {
                        "url": URL,
                        "card": CARD_NAME,
                        "currency": "EUR",
                        "price_native_per_gpu_hr": price_eur,
                    }
                ),
            )

        out = [observation("on_demand", on_demand)]
        out.extend(observation(term, price) for term, price in committed.items())
        log.info(
            "seeweb: %d observations (on-demand EUR %.2f/GPU-hr, 3/6/12mo EUR %.2f/%.2f/%.2f)",
            len(out), on_demand, committed["commit_3mo"], committed["commit_6mo"],
            committed["reserved_1yr"],
        )
        return out
