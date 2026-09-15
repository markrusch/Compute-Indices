# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Automated refresh of config/term_schedules.yaml's Verda row.

Covers tci.collectors.term_schedule_refresh. Fixture-only, no live network.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import responses

from tci import USER_AGENT
from tci.collectors.term_schedule_refresh import (
    URL,
    ScheduleShapeError,
    parse_schedule,
    refresh,
)

FIX = Path(__file__).parent / "fixtures" / "term_schedule"
GOOD_HTML = (FIX / "verda_pricing.html").read_text(encoding="utf-8")
RESHAPED_HTML = (FIX / "verda_pricing_reshaped.html").read_text(encoding="utf-8")

LIVE_DISCOUNTS = {1: 0.02, 3: 0.03, 6: 0.04, 12: 0.08, 24: 0.25}

# A small stand-in for config/term_schedules.yaml: same shape (header comment, a ruled-out
# provider block, the schedules list, the verda entry with its own comment), small enough
# to keep the test readable. What matters is that everything outside the verda entry's
# four data lines survives a refresh unchanged.
CONFIG_HEADER = """\
max_age_days: 90

# Roadmap L5.3 review, 2026-09-12: every other panel provider checked for a schedule of
# this shape. None qualified.
#
#   - RunPod: "Savings Plans" named but no published percentage.

schedules:
"""

CONFIG_FOOTER = """
"""


def _config(last_verified: str, discounts: dict[int, float]) -> str:
    discounts_str = "{" + ", ".join(f"{k}: {v}" for k, v in sorted(discounts.items())) + "}"
    entry = f"""\
  - provider: verda
    url: https://verda.com/pricing
    applies_to: self-service GPU instances
    last_verified: {last_verified}
    # "Commitment discount" (read on the live page, {last_verified}).
    discounts: {discounts_str}
"""
    return CONFIG_HEADER + entry + CONFIG_FOOTER


def _write(tmp_path: Path, last_verified: str, discounts: dict[int, float]) -> Path:
    p = tmp_path / "term_schedules.yaml"
    p.write_text(_config(last_verified, discounts), encoding="utf-8")
    return p


# ---- parse_schedule: the strict inner parser --------------------------------------------


def test_parse_schedule_matches_the_live_table_shape() -> None:
    assert parse_schedule(GOOD_HTML) == LIVE_DISCOUNTS


def test_parse_schedule_rejects_a_reshaped_table() -> None:
    with pytest.raises(ScheduleShapeError):
        parse_schedule(RESHAPED_HTML)


def test_parse_schedule_rejects_a_page_with_no_reserved_table() -> None:
    with pytest.raises(ScheduleShapeError):
        parse_schedule("<html><body>nothing here</body></html>")


# ---- refresh(): the fail-soft outer boundary ---------------------------------------------


@responses.activate
def test_refresh_fetches_once_and_updates_an_unchanged_table(tmp_path: Path) -> None:
    """A routine day where the page hasn't moved still bumps last_verified."""
    responses.add(responses.GET, URL, body=GOOD_HTML, status=200)
    path = _write(tmp_path, "2026-06-01", LIVE_DISCOUNTS)

    changed = refresh(config_path=path, today="2026-09-15")

    assert changed is True
    assert len(responses.calls) == 1
    assert responses.calls[0].request.headers["User-Agent"] == USER_AGENT
    text = path.read_text(encoding="utf-8")
    assert "last_verified: 2026-09-15" in text
    assert "discounts: {1: 0.02, 3: 0.03, 6: 0.04, 12: 0.08, 24: 0.25}" in text
    # everything outside the verda entry survives untouched
    assert "Roadmap L5.3 review" in text
    assert "RunPod" in text


@responses.activate
def test_refresh_updates_discounts_that_actually_changed(tmp_path: Path) -> None:
    old_discounts = {1: 0.01, 3: 0.02, 6: 0.03, 12: 0.07, 24: 0.20}
    responses.add(responses.GET, URL, body=GOOD_HTML, status=200)
    path = _write(tmp_path, "2026-06-01", old_discounts)

    changed = refresh(config_path=path, today="2026-09-15")

    assert changed is True
    text = path.read_text(encoding="utf-8")
    assert "discounts: {1: 0.02, 3: 0.03, 6: 0.04, 12: 0.08, 24: 0.25}" in text
    assert "last_verified: 2026-09-15" in text


@responses.activate
def test_refresh_leaves_the_file_untouched_on_a_reshaped_page(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A page whose shape no longer matches never partially-updates the file: the write
    either happens in full or not at all, and the existing last_verified stands (aging
    toward term.py's 90-day exclusion, same as if the human review had been skipped)."""
    responses.add(responses.GET, URL, body=RESHAPED_HTML, status=200)
    path = _write(tmp_path, "2026-06-01", LIVE_DISCOUNTS)
    before = path.read_text(encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="tci.collectors.term_schedule_refresh"):
        changed = refresh(config_path=path, today="2026-09-15")

    assert changed is False
    after = path.read_text(encoding="utf-8")
    assert after == before
    assert "last_verified: 2026-06-01" in after
    assert any("could not refresh" in r.message for r in caplog.records)


@responses.activate
def test_refresh_never_raises_on_a_network_failure(tmp_path: Path) -> None:
    responses.add(responses.GET, URL, status=503)
    path = _write(tmp_path, "2026-06-01", LIVE_DISCOUNTS)
    before = path.read_text(encoding="utf-8")

    changed = refresh(config_path=path, today="2026-09-15")

    assert changed is False
    assert path.read_text(encoding="utf-8") == before


@responses.activate
def test_refresh_skips_the_fetch_when_already_verified_today(tmp_path: Path) -> None:
    """No route is registered with `responses`, so any HTTP attempt would raise —
    proving the skip happens before the request, honouring 'one request per source per
    day' even when `daily` runs twice."""
    path = _write(tmp_path, "2026-09-15", LIVE_DISCOUNTS)

    changed = refresh(config_path=path, today="2026-09-15")

    assert changed is False
    assert len(responses.calls) == 0


@responses.activate
def test_refresh_returns_cleanly_rather_than_raising_into_the_daily_run(
    tmp_path: Path,
) -> None:
    """The exact guarantee cmd_daily depends on: whatever goes wrong, refresh() returns
    a bool and never propagates an exception."""
    responses.add(responses.GET, URL, body="<html>not the pricing table</html>", status=200)
    path = _write(tmp_path, "2026-06-01", LIVE_DISCOUNTS)

    result = refresh(config_path=path, today="2026-09-15")  # must not raise

    assert result is False
