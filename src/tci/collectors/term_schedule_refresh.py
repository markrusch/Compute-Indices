# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Automated refresh of the Verda row in config/term_schedules.yaml.

That file is a research input only (see its own header comment): it is read by the term
table at term.html and is never part of METHODOLOGY.lock or the calculation path. Until
now its one entry was kept fresh by a human re-reading https://verda.com/pricing every
quarter and hand-editing the date and numbers. This module does the same read, on the
same cadence the daily pipeline already runs, and leaves the human step needed only when
the page's shape changes enough that automation should not guess.

ONE NEW REQUEST, NOT A REUSE. gpuhunt's `verda` catalog (config/source_registry.yaml,
id: verda_catalog) reads a downloaded price list, not this marketing page, and says
nothing about commitment discounts. `fetch_html` is a genuinely new GET, one per day
(SOURCES.md's "1 request per source per day"; `refresh` also skips the fetch entirely
once `last_verified` already reads today, so a second `daily` run the same day costs
nothing).

FAIL-SOFT OUTSIDE, STRICT INSIDE. `refresh` never raises: a network failure, an
unreadable file, or a page that no longer matches the exact five-row table checked live
on 2026-09-15 all end the same way — a logged warning and config/term_schedules.yaml left
byte-for-byte untouched. That is the same outcome as if the quarterly human review had
simply been skipped: `term.py:load_schedules` already excludes an entry once
`last_verified` is more than `max_age_days` old, so a stale date is a gap, not a wrong
number, exactly as CLAUDE.md's "a gap stays a gap" requires. `parse_schedule` itself is
strict for the same reason a wrong reading would be worse than no reading: it raises
`ScheduleShapeError` on anything it cannot match with confidence rather than returning a
partial or guessed table.

TEXT SURGERY, NOT A YAML DUMP. config/term_schedules.yaml carries about forty lines of
prose recording every other panel provider checked for this shape and ruled out — exactly
the kind of documentation CLAUDE.md says must read as though a person wrote it. PyYAML
(the project's only YAML dependency; see pyproject.toml) has no comment-preserving writer,
and a round-trip library is not among its dependencies, so `_update_entry_text` edits only
the four lines that belong to the `verda` entry — `last_verified`, the two-line comment
above `discounts`, and `discounts` itself — by regex, and returns the rest of the file
unchanged, comments included.
"""

from __future__ import annotations

import logging
import re
import textwrap
from datetime import UTC, datetime
from pathlib import Path

import requests
import yaml

from tci.collectors.base import TIMEOUT_SECONDS, make_session

log = logging.getLogger("tci.collectors.term_schedule_refresh")

URL = "https://verda.com/pricing"
PROVIDER = "verda"
REPO_ROOT = Path(__file__).resolve().parents[3]
TERM_SCHEDULES_PATH = REPO_ROOT / "config" / "term_schedules.yaml"

# Wording -> tenor months, in the exact order the live "Reserved" table publishes them
# (checked 2026-09-15). Deliberately an allowlist: this file states, as the seller's own
# fact, a schedule the site's copy asserts — not a number read around a reshape.
EXPECTED_ROWS: tuple[tuple[str, int], ...] = (
    ("1 month", 1),
    ("3 months", 3),
    ("6 months", 6),
    ("1 year", 12),
    ("2 years", 24),
)
_WORDING_BY_MONTHS = {months: wording for wording, months in EXPECTED_ROWS}

_HEADING_RE = re.compile(r'id="reserved"[^>]*>.*?</h3>', re.DOTALL)
_TABLE_RE = re.compile(r"<table\b.*?</table>", re.DOTALL)
_ROW_RE = re.compile(r"<tr\b.*?</tr>", re.DOTALL)
_CELL_RE = re.compile(r"<t[hd]\b.*?>(.*?)</t[hd]>", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_DISCOUNT_RE = re.compile(r"^(\d+(?:\.\d+)?)%\s+off\s+on-demand$")


class ScheduleShapeError(ValueError):
    """The live page (or the stored config file) does not match the shape this module
    trusts. Raised by the strict inner functions; caught at the `refresh` boundary."""


def _cell_text(cell_html: str) -> str:
    return _TAG_RE.sub("", cell_html).strip()


def parse_schedule(html: str) -> dict[int, float]:
    """Parse the 'Reserved' commitment-discount table into {months: fraction}.

    Raises `ScheduleShapeError` on any deviation from the exact table checked live on
    2026-09-15: a missing heading or table, a wrong row count, reworded commitment
    labels, or a discount cell that is not literally "N% off on-demand". Raising here,
    rather than returning an empty or partial dict, is what lets `refresh` tell "nothing
    changed" apart from "don't trust what this read."
    """
    heading = _HEADING_RE.search(html)
    if not heading:
        raise ScheduleShapeError("no 'Reserved' heading found on the pricing page")
    table = _TABLE_RE.search(html, heading.end())
    if not table:
        raise ScheduleShapeError("no <table> found after the 'Reserved' heading")
    rows = _ROW_RE.findall(table.group(0))
    if len(rows) != len(EXPECTED_ROWS) + 1:  # +1 for the header row
        raise ScheduleShapeError(
            f"expected {len(EXPECTED_ROWS) + 1} table rows (1 header + "
            f"{len(EXPECTED_ROWS)} data rows), found {len(rows)}"
        )
    header_cells = [_cell_text(c) for c in _CELL_RE.findall(rows[0])]
    if header_cells != ["Commitment", "Discount"]:
        raise ScheduleShapeError(f"unexpected header cells: {header_cells!r}")

    discounts: dict[int, float] = {}
    for (expected_wording, months), row in zip(EXPECTED_ROWS, rows[1:], strict=True):
        cells = [_cell_text(c) for c in _CELL_RE.findall(row)]
        if len(cells) != 2:
            raise ScheduleShapeError(f"row {cells!r} does not have exactly 2 cells")
        commitment, discount_text = cells
        if commitment != expected_wording:
            raise ScheduleShapeError(
                f"expected commitment row {expected_wording!r}, found {commitment!r}"
            )
        m = _DISCOUNT_RE.match(discount_text)
        if not m:
            raise ScheduleShapeError(
                f"{expected_wording}: discount cell {discount_text!r} is not "
                "'N% off on-demand'"
            )
        discounts[months] = round(float(m.group(1)) / 100, 6)
    return discounts


def fetch_html(session: requests.Session | None = None) -> str:
    """One GET request to the Verda pricing page (SOURCES.md: 1 request per source per
    day). The page is server-rendered — no JS needed to read the table."""
    resp = (session or make_session()).get(URL, timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp.text


def _current_entry(config_text: str) -> tuple[str, dict[int, float]] | None:
    """The verda entry's stored (last_verified, discounts), read via ordinary YAML.

    Used only to decide whether a write is needed and what changed; the write itself
    edits `config_text` in place so every comment survives (see module docstring).
    """
    raw = yaml.safe_load(config_text) or {}
    for e in raw.get("schedules") or []:
        if e.get("provider") == PROVIDER:
            return (
                str(e["last_verified"]),
                {int(k): float(v) for k, v in (e.get("discounts") or {}).items()},
            )
    return None


def _format_discounts(discounts: dict[int, float]) -> str:
    parts = []
    for months in sorted(discounts):
        s = f"{discounts[months]:.4f}".rstrip("0").rstrip(".")
        parts.append(f"{months}: {s}")
    return "{" + ", ".join(parts) + "}"


def _format_comment(discounts: dict[int, float], on_date: str) -> str:
    parts = [
        f"{_WORDING_BY_MONTHS[months]} {discounts[months] * 100:g}% off on-demand"
        for months in sorted(discounts)
    ]
    body = f'"Commitment discount: {", ".join(parts)}" (read on the live page, {on_date}).'
    lines = textwrap.wrap(body, width=94, break_on_hyphens=False, break_long_words=False)
    return "\n".join(f"    # {line}" for line in lines)


_ENTRY_START_RE_TEMPLATE = r"^(  - provider: {}\n)"
_NEXT_ENTRY_RE = re.compile(r"^  - provider: ", re.MULTILINE)
_LAST_VERIFIED_RE = re.compile(r"^(    last_verified: )\S+", re.MULTILINE)
_COMMENT_BLOCK_RE = re.compile(r"(?:^    #.*\n)+", re.MULTILINE)
_DISCOUNTS_LINE_RE = re.compile(r"^(    discounts: )\{[^\n}]*\}", re.MULTILINE)


def _update_entry_text(config_text: str, on_date: str, discounts: dict[int, float]) -> str:
    """Rewrite only the `verda` entry's `last_verified`, comment and `discounts` lines.

    Everything before and after that entry — including the ~40 lines of documented
    provider rejections above `schedules:` — passes through untouched.
    """
    start_re = re.compile(_ENTRY_START_RE_TEMPLATE.format(re.escape(PROVIDER)), re.MULTILINE)
    start_m = start_re.search(config_text)
    if not start_m:
        raise ScheduleShapeError(
            f"no '  - provider: {PROVIDER}' entry found in config/term_schedules.yaml"
        )
    next_m = _NEXT_ENTRY_RE.search(config_text, start_m.end())
    end = next_m.start() if next_m else len(config_text)
    entry = config_text[start_m.start():end]

    entry, n = _LAST_VERIFIED_RE.subn(lambda m: m.group(1) + on_date, entry, count=1)
    if n != 1:
        raise ScheduleShapeError("verda entry has no 'last_verified:' line to update")

    new_comment = _format_comment(discounts, on_date) + "\n"
    comment_m = _COMMENT_BLOCK_RE.search(entry)
    if comment_m:
        entry = entry[:comment_m.start()] + new_comment + entry[comment_m.end():]
    else:
        entry = _DISCOUNTS_LINE_RE.sub(lambda m: new_comment + m.group(0), entry, count=1)

    entry, n = _DISCOUNTS_LINE_RE.subn(
        lambda m: m.group(1) + _format_discounts(discounts), entry, count=1
    )
    if n != 1:
        raise ScheduleShapeError("verda entry has no 'discounts:' line to update")

    return config_text[:start_m.start()] + entry + config_text[end:]


def refresh(
    config_path: Path | None = None,
    session: requests.Session | None = None,
    today: str | None = None,
) -> bool:
    """Fetch, strictly parse, and refresh the Verda row. Returns True if the file changed.

    Fail-soft at this boundary, on purpose: any failure (network, shape, file I/O) is
    logged as a warning and the function returns False with config/term_schedules.yaml
    left exactly as it was. That preserves the existing safety net for every day this
    cannot confirm a number — `term.py`'s staleness gate is unchanged, so a `last_verified`
    that cannot be refreshed simply keeps aging toward its 90-day exclusion, the same as if
    the quarterly human review had been skipped. See CLAUDE.md's "a gap stays a gap."
    """
    path = config_path or TERM_SCHEDULES_PATH
    on_date = today or datetime.now(UTC).strftime("%Y-%m-%d")
    try:
        config_text = path.read_text(encoding="utf-8")
        current = _current_entry(config_text)
        if current is not None and current[0] == on_date:
            log.info(
                "term_schedule_refresh: %s already verified today (%s), skipping fetch",
                PROVIDER, on_date,
            )
            return False
        html = fetch_html(session)
        discounts = parse_schedule(html)
        new_text = _update_entry_text(config_text, on_date, discounts)
        path.write_text(new_text, encoding="utf-8")
    except Exception:
        log.warning(
            "term_schedule_refresh: could not refresh %s — config/term_schedules.yaml"
            " left untouched, last_verified keeps aging toward the 90-day cutoff",
            PROVIDER, exc_info=True,
        )
        return False

    changed = current is None or current[1] != discounts
    log.info(
        "term_schedule_refresh: %s last_verified -> %s%s",
        PROVIDER, on_date, " (discounts changed)" if changed else " (discounts unchanged)",
    )
    return True
