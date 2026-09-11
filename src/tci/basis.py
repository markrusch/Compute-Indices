# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Basis series: the spread between two published prints (methodology-hashed).

value = lead - reference, in USD per GPU-hour, on a day both legs publish. The legs are
priced by the same unit definition, estimator, trim, weights and publication gate, and
differ only in the region block they draw from; that is what makes the spread a basis
rather than a comparison of two methodologies.

A basis is undefined on a day either leg gaps, and it is published as a gap with the
reason, like any other series. It is never computed from a carried-forward leg: a
spread between today's EU print and last week's US print is a number about two different
days.
"""

from __future__ import annotations

from tci.models import Constituent, IndexPrint


def compute_basis(
    date: str,
    series: str,
    lead: IndexPrint | None,
    reference: IndexPrint | None,
    fx: tuple[float, str] | None,
) -> IndexPrint:
    legs = tuple(
        Constituent(
            provider=leg.series,
            source="basis",
            tier="index",
            price_usd=leg.value_usd if leg.value_usd is not None else 0.0,
            weight=weight,
            included=leg.value_usd is not None,
            exclusion_reason=None if leg.value_usd is not None else (leg.flags or "gap"),
        )
        for leg, weight in ((lead, 100.0), (reference, -100.0))
        if leg is not None
    )
    missing = []
    if lead is None or lead.value_usd is None:
        missing.append("lead_gap")
    if reference is None or reference.value_usd is None:
        missing.append("reference_gap")
    fx_rate, fx_date = (fx[0], fx[1]) if fx else (None, None)
    if missing:
        return IndexPrint(
            date=date, series=series, value_usd=None, value_eur=None,
            fx_rate=fx_rate, fx_date=fx_date,
            n_sources=sum(1 for c in legs if c.included), n_executable=0,
            flags=",".join(missing), constituents=legs,
        )
    assert lead is not None and reference is not None
    assert lead.value_usd is not None and reference.value_usd is not None
    spread = round(lead.value_usd - reference.value_usd, 6)
    return IndexPrint(
        date=date, series=series, value_usd=spread,
        value_eur=round(spread / fx[0], 6) if fx else None,
        fx_rate=fx_rate, fx_date=fx_date, n_sources=2, n_executable=0,
        flags="", constituents=legs,
    )
