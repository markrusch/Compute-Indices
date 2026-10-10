"""The dashboard's Sources panel lists exactly the collectors the daily run uses.

The panel says "every collector the index reads". Until 10 October 2026 its list in
config/source_links.yaml was kept by hand, and it had drifted: it described the retired
hand-kept rate cards as a live source and left out eight collectors whose rows were in
that day's print. A list that depends on someone remembering is the failure this test
replaces.
"""

from __future__ import annotations

from tci.commands import collectors_for_daily
from tci.outputs.webdata import sources_panel


def test_every_daily_collector_is_listed_once_in_run_order() -> None:
    expected = [c.name for c in collectors_for_daily()]
    listed = [s["source"] for s in sources_panel()]
    assert listed == expected


def test_every_listed_collector_says_where_it_reads() -> None:
    for s in sources_panel():
        assert s["label"], s["source"]
        assert s["endpoint"], s["source"]
        assert str(s["url"] or "").startswith("https://"), s["source"]
