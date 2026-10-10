# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Exact weighted-median semantics (METHODOLOGY.md §3.6): lower weighted median."""

from __future__ import annotations

import pytest

from tci.index import weighted_median


def test_single_constituent() -> None:
    assert weighted_median([(2.5, 8.0)]) == 2.5


def test_equal_weights_odd() -> None:
    pairs = [(1.0, 1.0), (2.0, 1.0), (3.0, 1.0)]
    assert weighted_median(pairs) == 2.0


def test_equal_weights_even_takes_lower() -> None:
    # cumulative reaches exactly 50% at the 2nd of 4 -> lower median, no interpolation
    pairs = [(1.0, 1.0), (2.0, 1.0), (3.0, 1.0), (4.0, 1.0)]
    assert weighted_median(pairs) == 2.0


def test_heavy_constituent_dominates() -> None:
    pairs = [(1.5, 10.0), (2.0, 1.0), (3.0, 1.0)]
    assert weighted_median(pairs) == 1.5


def test_exact_50_percent_boundary() -> None:
    # first constituent holds exactly half the total weight -> it is the median (>= rule)
    pairs = [(1.0, 5.0), (2.0, 3.0), (3.0, 2.0)]
    assert weighted_median(pairs) == 1.0


def test_input_order_irrelevant() -> None:
    pairs = [(3.0, 1.0), (1.0, 1.0), (2.0, 1.0)]
    assert weighted_median(pairs) == 2.0


def test_empty_raises() -> None:
    with pytest.raises(ValueError):
        weighted_median([])


def _eight_october_h100_offers() -> list[tuple[float, float]]:
    """The EU H100 offer book of 8 October 2026, weighted as compute_print weights it.

    Seven providers on equal shares, RunPod doubled as executable, so 100 splits into
    12.5s and a 25. The weight below Scaleway's 2-GPU offer is 50 exactly, which makes
    the print a tie between $3.70 and Verda's $3.85.
    """
    book = {  # provider: (share, [(price, gpus), ...])
        "digitalocean": (12.5, [(4.41, 8)]),
        "lambdalabs": (12.5, [(4.19, 2), (4.09, 4), (3.99, 8)]),
        "nebius": (12.5, [(4.5, 8)]),
        "runpod": (25.0, [(3.49, 8)]),
        "scaleway": (12.5, [(3.699475, 2), (3.568537, 4)]),
        "seeweb": (12.5, [(2.112453, 8)]),
        "verda": (12.5, [(3.85, 2)] * 3 + [(3.85, 4)] * 3 + [(3.85, 8)] * 3),
    }
    pairs = []
    for share, offers in book.values():
        total_cap = float(sum(g for _, g in offers))
        pairs.extend((price, share * (g / total_cap)) for price, g in offers)
    return pairs


def test_a_tie_resolves_the_same_on_every_python(monkeypatch: pytest.MonkeyPatch) -> None:
    # Python 3.12 made built-in sum() compensated. Under that arithmetic the old code moved
    # this print from the published $3.70 to $3.85; CI runs 3.11 and could not see it.
    # Swapping sum() for math.fsum here is what 3.12+ does to the old code.
    import builtins
    import math

    pairs = _eight_october_h100_offers()
    assert weighted_median(pairs) == 3.699475
    monkeypatch.setattr(builtins, "sum", lambda it, start=0: start + math.fsum(it))
    assert weighted_median(pairs) == 3.699475
