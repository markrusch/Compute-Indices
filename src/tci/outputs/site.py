# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Static site generation: the published TCI pages, rendered from the DB + the docs.

WHY a generator and not hand-maintained HTML: the dashboard has to be correct the morning
after every daily run, and a page whose numbers are typed by hand is a page that will one
day disagree with `daily_index`. Everything numeric here is read out of SQLite at build
time and baked into the markup, so the pages are complete with JavaScript disabled.

Design contract (site/assets/tokens.css, and the TCI brand guide):
  * no external requests — tokens.css and site.css are inlined, the two typefaces are
    self-hosted from site/assets/fonts, every graphic is inline SVG or DOM;
  * ONE theme. TCI's ground (#0B0C0D) is the only page background the brand allows, so
    there is no light palette and no theme toggle (see tokens.css, "THEMING CONTRACT");
  * two reds, never inverted: wine fills large shapes, bright red marks small ones;
  * charts are hand-rolled SVG, monochrome by default, the bright red on the current
    value only;
  * a gap is published as a gap. No interpolation across a missing print, no stale value
    dressed as live.

NAMING (2026-09-06). The brand is TCI — The Compute Indices — and the ticker family is
`TCI-CRI-*`. The DATABASE still stores the original `EU-CRI-*` series keys, and so do
`site/data/latest.json` and `index_history.csv`: renaming stored identifiers is a
governed event (old->new mapping, effective date, version bump — GOVERNANCE.md §1), not
a reskin. `display_series()` is the single boundary where a stored key becomes a
published name, so exactly one function has to change when that governed rename lands.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime, timedelta
from html import escape
from math import ceil, floor
from pathlib import Path

import yaml

from tci import DISCLAIMER
from tci.commands import COMPOSITE, HEADLINE, SERIES_7D
from tci.config import Factors, load_factors, load_sovereign
from tci.db import utc_now_iso
from tci.outputs import markdown
from tci.outputs.webdata import provider_links, sources_panel

log = logging.getLogger("tci.outputs.site")

REPO_ROOT = Path(__file__).resolve().parents[3]
SITE_DIR = REPO_ROOT / "site"
ASSETS = SITE_DIR / "assets"
RESEARCH_SRC = REPO_ROOT / "research"

WINDOW_DAYS = 30

# ---- brand ---------------------------------------------------------------
BRAND = "TCI"
BRAND_FULL = "The Compute Indices"
BRAND_LINE = (
    "TCI publishes reproducible reference prices for AI and HPC compute — "
    "methodology-driven, vendor-neutral, and rebuildable from public sources by anyone."
)
CONTACT_EMAIL = "rusch.mh@gmail.com"
# Still the pre-rebrand Substack address, and still the one that works. Substack keeps
# the old subdomain redirecting after a rename, but this points at whatever the
# publication answers on today rather than at a name it might take later: a dead link in
# the footer of a site about not publishing stale values would be its own small joke.
# Change this line, and only this line, once the publication moves.
NEWSLETTER_URL = "https://computeindex.substack.com"
REPO_URL = "https://github.com/markrusch/Compute-Indices"

# The canonical home. Every page carries a <link rel="canonical"> pointing here, and the
# social-card URLs are absolute against it, because Open Graph consumers do not resolve
# relative paths. Until 2026-09-07 the site lived at two host-shaped URLs and had neither
# a canonical nor a card, so a shared link rendered as a bare grey URL and the two mirrors
# competed with each other for the same content in search results.
#
# This is the ONLY absolute self-reference in the generated markup. Every other link stays
# relative so the pages still work unchanged at markrusch.github.io/Compute-Indices/ and
# from a local file:// checkout.
SITE_URL = "https://thecomputeindices.com"
OG_IMAGE = "assets/og-card.png"  # relative in the repo, absolutised for the meta tags

# The stored series prefix and its published equivalent. See the module docstring:
# the DB is not renamed here, only what the reader sees.
SERIES_PREFIX_STORED = "EU-CRI"
SERIES_PREFIX_PUBLISHED = "TCI-CRI"

# The daily cut-off, stated wherever the site claims a schedule. 11:00 UTC is the real
# one (it is why the EUR leg is structurally T-1: the ECB publishes ~14:00 UTC).
CUTOFF_UTC = "11:00 UTC"

NAV: tuple[tuple[str, str], ...] = (
    ("index.html", "Indices"),
    ("methodology.html", "Methodology"),
    ("data.html", "Data"),
    ("research.html", "Research"),
    ("governance.html", "Governance"),
)

# Ticker order: the headline first, then its companions. Presentational only.
# EU-CRI-H100-CLOUD was retired in v0.3.0 (see CHANGELOG) but stayed in this tuple, so
# the ticker kept rendering its final 0.2.0-dev value. Removed 2026-09-04 and not
# replaced: the remaining sub-population series draw on segments smaller than
# aggregation.min_providers and so cannot print (see the note in TILES).
TICKER = (HEADLINE, SERIES_7D, "EU-CRI-H100-MKT", COMPOSITE)

# The landing page's "headline series, today" table: the headline plus one row per GPU
# class the index prices. Class series only — segment cuts of H100 belong in the tiles,
# where their gap reasons have room to be explained.
HEADLINE_TABLE = (
    HEADLINE, "EU-CRI-H200", "EU-CRI-B200", "EU-CRI-B300", "EU-CRI-A100",
    "EU-CRI-H100-PCIE",
)

# The sub-index tiles, in publication order.
# NOTE (2026-09-04): EU-CRI-H100-MKT and EU-CRI-H100-HS draw on the marketplace (2
# providers) and hyperscaler (3) segments, against aggregation.min_providers = 5, so
# they cannot clear the gate and have never published a value. EU-CRI-H100-NC and
# EU-CRI-H100-SOV have not either. They render as honest gaps, but a tile that can
# never print is a governance question, not a rendering one — it is on the 0.4.0 list.
TILES = (
    "EU-CRI-H200", "EU-CRI-B300", "EU-CRI-A100", "EU-CRI-H100-PCIE",
    "EU-CRI-H100-SOV", "EU-CRI-H100-MKT", "EU-CRI-H100-NC", "EU-CRI-H100-HS",
)

SERIES_LABEL: dict[str, str] = {
    HEADLINE: "H100 SXM 80GB, on-demand, EU/EEA",
    SERIES_7D: "7-day mean of the headline",
    "EU-CRI-H100-MKT": "Marketplace segment only",
    "EU-CRI-H100-NC": "Neocloud segment only",
    "EU-CRI-H100-HS": "Hyperscaler catalog segment",
    "EU-CRI-H100-SOV": "EU/EEA-headquartered operators",
    "EU-CRI-H100-PCIE": "H100 PCIe — priced as its own class",
    "EU-CRI-H200": "H200 SXM 141GB",
    "EU-CRI-B300": "B300 SXM",
    "EU-CRI-A100": "A100 SXM 80GB",
    COMPOSITE: "Chain-linked class composite (level, not $/hr)",
}

# Why a print gapped, in words. Colour is never the only channel and neither is a flag
# string: every gap on the page says what the gate was.
FLAG_TEXT: dict[str, str] = {
    "insufficient_sources": "below the provider gate",
    "insufficient_offers": "below the offer gate",
    "insufficient_history": "too few days in the window",
    "no_linkable_series": "no class linked on both endpoints",
    "no_executable_input": "list prices only, no executable quote",
    "bootstrap_weights": "bootstrap weighting",
    "correction": "revised",
    "base": "base value",
    "stale": "source older than the staleness limit",
    "no_print": "no print computed for this session",
}

# Constituent exclusion reasons, as short flag codes. A truncated word is not a flag.
EXCLUSION_CODE: dict[str, str] = {
    "out_of_population": "OOP",
    "insufficient_sources": "GATE",
    "insufficient_offers": "GATE",
    "stale": "STALE",
    "no_print": "NOPRINT",
    "excluded": "EXCL",
}

ICON = {
    "up": '<path d="M8 13V3.6M3.9 7.6 8 3.3l4.1 4.3" fill="none" stroke="currentColor"'
          ' stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/>',
    "down": '<path d="M8 3v9.4M3.9 8.4 8 12.7l4.1-4.3" fill="none" stroke="currentColor"'
            ' stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/>',
    "flat": '<path d="M3 8h10" fill="none" stroke="currentColor" stroke-width="1.9"'
            ' stroke-linecap="round"/>',
    "check": '<path d="M2.5 8.5 6.2 12.2 13.5 4.4" fill="none" stroke="currentColor"'
             ' stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"/>',
    "warn": '<path d="M8 1.6 15.2 14H0.8Z" fill="none" stroke="currentColor"'
            ' stroke-width="1.6" stroke-linejoin="round"/><path d="M8 6v3.4"'
            ' stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>'
            '<circle cx="8" cy="11.9" r="1" fill="currentColor"/>',
    "lock": '<rect x="2.8" y="6.9" width="10.4" height="7.3" rx="1" fill="none"'
            ' stroke="currentColor" stroke-width="1.5"/><path d="M5.4 6.9V4.8a2.6 2.6 0'
            ' 0 1 5.2 0v2.1" fill="none" stroke="currentColor" stroke-width="1.5"/>',
    "ext": '<path d="M6.5 3H3.2v9.8H13V9.5M9.4 2.6H13.4V6.6M13.2 2.8 7.4 8.6" fill="none"'
           ' stroke="currentColor" stroke-width="1.5" stroke-linecap="round"'
           ' stroke-linejoin="round"/>',
    "gap": '<path d="M2.5 8h3M10.5 8h3" fill="none" stroke="currentColor"'
           ' stroke-width="1.8" stroke-linecap="round"/>',
}

# The wordmark is DOM, not an image: "TC" set in Outfit 800, then the "I" built as a bar
# with the bright red dot above it — the one place bright red is a fixed brand element.
# Building it from spans means it scales with the type, needs no asset request, and stays
# crisp at any DPI. --brand-h drives every dimension (brand guide §01: clearspace = the
# height of the "T", minimum 18px cap height).
def _wordmark(height: int = 24, *, name: bool = True) -> str:
    lockup = (
        f'<span class="brand__mark" style="--brand-h:{height}px" aria-hidden="true">'
        '<span class="brand__tc">TC</span>'
        '<span class="brand__i"><span class="brand__dot"></span>'
        '<span class="brand__bar"></span></span></span>'
    )
    label = f'<span class="brand__name">{_e(BRAND_FULL)}</span>' if name else ""
    return lockup + label


# The mark, flattened to a favicon: the ground, the bar, the dot. Same construction,
# same two colours, no external request.
FAVICON = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'"
    "%3E%3Crect width='32' height='32' rx='7' fill='%230b0c0d'/%3E"
    "%3Crect x='6' y='9' width='7' height='16' rx='1' fill='%23f5f5f6'/%3E"
    "%3Crect x='17' y='16' width='7' height='9' rx='1' fill='%23f5f5f6'/%3E"
    "%3Ccircle cx='20.5' cy='10.5' r='3.5' fill='%23ff0000'/%3E%3C/svg%3E"
)


# ==========================================================================
# small helpers
# ==========================================================================


def _e(value: object) -> str:
    return escape(str(value), quote=True)


def display_series(series: str) -> str:
    """The published name of a stored series key — the ONE place the rename happens.

    `daily_index.series` still holds `EU-CRI-H100`, and so do the published CSV and
    latest.json, because renaming a stored identifier is a governed event that owes
    readers an old->new mapping and an effective date (GOVERNANCE.md §1). Until that
    lands, the site publishes the TCI name and the data files publish the stored one;
    the Data page says so in as many words rather than leaving a reader to notice.
    """
    return series.replace(SERIES_PREFIX_STORED, SERIES_PREFIX_PUBLISHED)


def _nbsp_series(series: str) -> str:
    """A ticker with non-breaking hyphens, so `TCI-CRI-H100` never wraps mid-symbol."""
    return _e(display_series(series)).replace("-", "&#8209;")


def _rebrand(prose: str) -> str:
    """Brand-substitute prose for display, without editing the source it came from.

    Two substitutions, in this order, because order is the whole trick: a hyphenated
    `EU-CRI-H100` is an identifier and becomes `TCI-CRI-H100`, while a bare `EU-CRI` is
    the brand and becomes `TCI`. Doing the bare one first would turn every ticker into
    `TCI-H100`.

    Used for `DISCLAIMER` — which also ships inside latest.json, so the constant itself
    is left alone — and, via `_rebrand_doc`, for the Markdown the pages embed. Same
    boundary `display_series` draws for identifiers: rename what the reader sees, leave
    the stored artefact to the governed rename.
    """
    return prose.replace(
        f"{SERIES_PREFIX_STORED}-", f"{SERIES_PREFIX_PUBLISHED}-"
    ).replace(SERIES_PREFIX_STORED, BRAND)


_FENCE_RE = re.compile(r"(^```.*?^```|`[^`\n]+`)", re.M | re.S)


def _rebrand_doc(markdown_text: str) -> str:
    """Rebrand a Markdown document's PROSE, leaving code untouched.

    The embedded documents are the reason this is not a plain string replace. Prose that
    says "EU-CRI is a price-transparency benchmark" must read TCI under a TCI masthead —
    a page whose chrome and body disagree about the name of the thing is exactly the
    half-finished feel the rebrand is meant to remove. But the same documents also print
    commands a reader is meant to paste:

        python -m tci.run constituents --series EU-CRI-H100

    and that argument is a database key, not a brand. Renaming it would hand out a
    command that returns nothing. So fenced blocks and inline-code spans are held out
    and everything between them is rebranded.
    """
    return "".join(
        part if _FENCE_RE.fullmatch(part) else _rebrand(part)
        for part in _FENCE_RE.split(markdown_text)
    )


def _icon(name: str, size: int = 12, cls: str = "ico") -> str:
    return (
        f'<svg class="{cls}" viewBox="0 0 16 16" width="{size}" height="{size}"'
        f' aria-hidden="true" focusable="false">{ICON[name]}</svg>'
    )


def _num(value: float | None, dp: int = 2, dash: str = "&#8212;") -> str:
    """Fixed-decimal so a live cell never reflows, and a missing value is never a zero."""
    return dash if value is None else f"{value:,.{dp}f}"


def _pct(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return (new - old) / old * 100.0


def _delta(pct: float | None, dp: int = 2) -> str:
    """Sign + arrow + colour. Three channels, so colour is never load-bearing alone."""
    if pct is None:
        return '<span class="delta delta--flat"><span class="u">n/a</span></span>'
    if abs(pct) < 0.005:
        return (
            f'<span class="delta delta--flat">{_icon("flat")}'
            f'<span class="num">&#177;0.00%</span></span>'
        )
    kind, arrow = ("up", "up") if pct > 0 else ("down", "down")
    sign = "+" if pct > 0 else "&#8722;"
    return (
        f'<span class="delta delta--{kind}">{_icon(arrow)}'
        f'<span class="num">{sign}{abs(pct):.{dp}f}%</span></span>'
    )


def _human_date(iso: str) -> str:
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%d %b %Y").lstrip("0")


def _flag_words(flags: str | None) -> str:
    parts = [f.strip() for f in (flags or "").split(",") if f.strip()]
    return " · ".join(FLAG_TEXT.get(f, f.replace("_", " ")) for f in parts)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ==========================================================================
# data access — always the latest revision of a (date, series)
# ==========================================================================


@dataclass(frozen=True)
class Point:
    """One session on the published curve. `value` None means the print gapped."""

    date: str
    value: float | None
    value_eur: float | None = None
    flags: str = ""


def series_history(
    conn: sqlite3.Connection, series: str, *, since: str | None = None
) -> list[Point]:
    """Published history for one series, one row per date, latest revision only.

    A correction is stored as a NEW revision rather than an edit (db triggers enforce
    append-only), so any read that forgets `MAX(revision)` silently republishes a value
    that was already withdrawn. Every read path on the site goes through here.
    """
    sql = (
        "SELECT d.date, d.value_usd, d.value_eur, d.flags FROM daily_index d JOIN ("
        "  SELECT date, series, MAX(revision) AS rev FROM daily_index"
        "  WHERE series = ? GROUP BY date, series"
        ") m ON d.date = m.date AND d.series = m.series AND d.revision = m.rev"
        " WHERE d.series = ?"
    )
    params: list[object] = [series, series]
    if since is not None:
        sql += " AND d.date >= ?"
        params.append(since)
    sql += " ORDER BY d.date"
    return [
        Point(r["date"], r["value_usd"], r["value_eur"], r["flags"] or "")
        for r in conn.execute(sql, params)
    ]


def latest_print(conn: sqlite3.Connection, series: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM daily_index WHERE series = ? ORDER BY date DESC, revision DESC LIMIT 1",
        (series,),
    ).fetchone()


def current_print(conn: sqlite3.Connection, series: str, date: str) -> sqlite3.Row | None:
    """The print for THIS session, or None — never an older one dressed as current.

    `latest_print` returns a series' newest row whatever its date, which is right for
    history but wrong for a live surface: a series that stops being computed keeps
    rendering its last good value forever. That is exactly what happened to the retired
    `EU-CRI-H100-CLOUD`, which sat in the ticker showing 3.85 from 2026-08-15 under
    methodology 0.2.0-dev — for a while the only number on a ticker where every live
    series was honestly gapped. A stale value presented as current is the one failure
    mode this project cannot afford, so the live surfaces ask for the session's row by
    date and get nothing if it does not exist.
    """
    row = latest_print(conn, series)
    return row if row is not None and row["date"] == date else None


def previous_published(
    conn: sqlite3.Connection, series: str, before: str
) -> tuple[str, float] | None:
    row = conn.execute(
        "SELECT d.date, d.value_usd FROM daily_index d JOIN ("
        "  SELECT date, MAX(revision) AS rev FROM daily_index WHERE series = ? GROUP BY date"
        ") m ON d.date = m.date AND d.revision = m.rev"
        " WHERE d.series = ? AND d.date < ? AND d.value_usd IS NOT NULL"
        " ORDER BY d.date DESC LIMIT 1",
        (series, series, before),
    ).fetchone()
    return (row["date"], row["value_usd"]) if row else None


def value_on_or_before(
    conn: sqlite3.Connection, series: str, date: str
) -> float | None:
    """The last published value at or before DATE, for a fixed-horizon comparison.

    A 30-day delta on a series that gaps as often as this one cannot ask for "the value
    exactly 30 days ago" — most sessions have none. It asks for the most recent print up
    to that date instead, which is a comparison against a value that was genuinely
    published, never an interpolation onto a day the index said nothing.
    """
    row = conn.execute(
        "SELECT d.value_usd FROM daily_index d JOIN ("
        "  SELECT date, MAX(revision) AS rev FROM daily_index WHERE series = ? GROUP BY date"
        ") m ON d.date = m.date AND d.revision = m.rev"
        " WHERE d.series = ? AND d.date <= ? AND d.value_usd IS NOT NULL"
        " ORDER BY d.date DESC LIMIT 1",
        (series, series, date),
    ).fetchone()
    return row["value_usd"] if row else None


def constituents_for(conn: sqlite3.Connection, series: str, date: str) -> list[sqlite3.Row]:
    rev = conn.execute(
        "SELECT MAX(revision) AS rev FROM daily_index WHERE date = ? AND series = ?",
        (date, series),
    ).fetchone()
    if rev is None or rev["rev"] is None:
        return []
    return list(
        conn.execute(
            "SELECT * FROM constituents WHERE date = ? AND series = ? AND revision = ?"
            " ORDER BY included DESC, price_usd",
            (date, series, rev["rev"]),
        )
    )


def _window(end: str, days: int = WINDOW_DAYS) -> list[str]:
    last = date_type.fromisoformat(end)
    return [(last - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]


def _windowed(points: list[Point], dates: list[str]) -> list[Point]:
    """Align a sparse series onto a full calendar window; absent days become gaps."""
    by_date = {p.date: p for p in points}
    return [by_date.get(d, Point(d, None, None, "no_print")) for d in dates]


# ==========================================================================
# charts — hand-rolled SVG, no libraries
# ==========================================================================

PL, PR, PT, PB = 8.0, 816.0, 16.0, 268.0  # plot box; tick text lives in the 816..880 gutter


def _nice_step(raw: float) -> float:
    from math import log10

    if raw <= 0:
        return 1.0
    mag = 10 ** floor(log10(raw))
    for mult in (1, 2, 2.5, 5, 10):
        if raw <= mult * mag:
            return mult * mag
    return 10 * mag


def _bounds(values: list[float]) -> tuple[float, float, float]:
    """(lo, hi, step) for a truncated axis. Truncated, so the chart is a LINE, never an area."""
    lo, hi = min(values), max(values)
    if hi == lo:
        pad = max(abs(hi) * 0.02, 0.05)
        lo, hi = lo - pad, hi + pad
    else:
        pad = (hi - lo) * 0.30
        lo, hi = lo - pad, hi + pad
    step = _nice_step((hi - lo) / 4)
    return floor(lo / step) * step, ceil(hi / step) * step, step


def _monotone_path(pts: list[tuple[float, float]]) -> str:
    """SVG path through PTS as a cubic Hermite spline, tangents chosen by the
    Fritsch-Carlson monotone rule (the same constraint D3's curveMonotoneX and
    matplotlib's PCHIP enforce). Passes through every real point exactly and
    never overshoots past two consecutive points' values — so a run of real
    prints reads as a smooth line instead of a jagged polyline, without ever
    implying a high or low between two prints that didn't happen."""
    n = len(pts)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    if n == 2:
        return f"M{xs[0]} {ys[0]} L{xs[1]} {ys[1]}"
    d = [(ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]) for i in range(n - 1)]
    m = [0.0] * n
    m[0], m[-1] = d[0], d[-1]
    for i in range(1, n - 1):
        if d[i - 1] == 0 or d[i] == 0 or (d[i - 1] > 0) != (d[i] > 0):
            m[i] = 0.0
        else:
            m[i] = (d[i - 1] + d[i]) / 2
    for i in range(n - 1):
        if d[i] == 0:
            m[i] = m[i + 1] = 0.0
            continue
        a, b = m[i] / d[i], m[i + 1] / d[i]
        if a < 0:
            m[i], a = 0.0, 0.0
        if b < 0:
            m[i + 1], b = 0.0, 0.0
        s = a * a + b * b
        if s > 9:
            tau = 3.0 / s**0.5
            m[i], m[i + 1] = tau * a * d[i], tau * b * d[i]
    parts = [f"M{xs[0]} {ys[0]}"]
    for i in range(n - 1):
        dx = xs[i + 1] - xs[i]
        c1x, c1y = round(xs[i] + dx / 3, 1), round(ys[i] + m[i] * dx / 3, 1)
        c2x, c2y = round(xs[i + 1] - dx / 3, 1), round(ys[i + 1] - m[i + 1] * dx / 3, 1)
        parts.append(f"C{c1x} {c1y},{c2x} {c2y},{xs[i + 1]} {ys[i + 1]}")
    return "".join(parts)


def line_chart(points: list[Point], *, symbol: str, ccy: str = "$", dp: int = 2) -> str:
    """A single-series time-series chart. Greyscale line, accent only on the current value.

    Gaps are drawn as gaps: the path breaks, and each missing session gets a hollow tick
    on the baseline. Nothing is interpolated across a session the index did not publish.
    """
    n = len(points)
    vals = [p.value for p in points if p.value is not None]
    if not vals:
        return (
            '<div class="gapnote">' + _icon("warn", 14)
            + f"<p>No published value for <strong>{_e(symbol)}</strong> anywhere in the last"
            f" {n} sessions. The series is gapped, not flat — see the table view below.</p></div>"
        )
    lo, hi, step = _bounds(vals)
    dx = (PR - PL) / max(1, n - 1)

    def x(i: int) -> float:
        return round(PL + i * dx, 1)

    def y(v: float) -> float:
        return round(PB - (v - lo) / (hi - lo) * (PB - PT), 1)

    grid, ticks = [], []
    t = lo
    while t <= hi + step / 2:
        gy = y(t)
        grid.append(f'<line class="ch-grid" x1="{PL}" y1="{gy}" x2="{PR}" y2="{gy}"/>')
        ticks.append(f'<text class="ch-tick" x="826" y="{gy + 4}">{ccy}{t:,.{dp}f}</text>')
        t += step

    # Segments join only calendar-adjacent published sessions.
    segments: list[list[tuple[float, float]]] = []
    run: list[tuple[float, float]] = []
    for i, p in enumerate(points):
        if p.value is None:
            if len(run) > 1:
                segments.append(run)
            run = []
        else:
            run.append((x(i), y(p.value)))
    if len(run) > 1:
        segments.append(run)
    paths = "".join(f'<path class="ch-line" d="{_monotone_path(seg)}"/>' for seg in segments)
    # An isolated print (both neighbours gapped) would otherwise draw nothing at all.
    isolated = "".join(
        f'<circle class="ch-marker-ring" cx="{x(i)}" cy="{y(p.value)}" r="4.5"/>'
        f'<circle cx="{x(i)}" cy="{y(p.value)}" r="2.6" fill="var(--chart-line)"/>'
        for i, p in enumerate(points)
        if p.value is not None
        and (i == 0 or points[i - 1].value is None)
        and (i == n - 1 or points[i + 1].value is None)
    )
    gapmarks = "".join(
        f'<line class="ch-gapmark" x1="{x(i)}" y1="{PB - 4}" x2="{x(i)}" y2="{PB + 4}"/>'
        for i, p in enumerate(points)
        if p.value is None
    )

    published_idx = [(i, p.value) for i, p in enumerate(points) if p.value is not None]
    last_i, last_v = published_idx[-1]
    low_i, low_v = min(published_idx, key=lambda iv: iv[1])
    ly = y(last_v)
    labels = [
        f'<text class="ch-cur" x="{min(x(last_i) - 8, PR - 8)}" y="{ly - 12}"'
        f' text-anchor="end">{ccy}{last_v:,.{dp}f}</text>'
    ]
    if low_i != last_i:
        anchor = "start" if low_i < n / 2 else "end"
        low_y = y(low_v)
        # The current-value guide rule (drawn below, at y=ly) runs the full plot width.
        # When the low ties or nearly ties the current value, the default +20 label
        # offset lands right on that rule — push it further clear in that case.
        label_y = low_y + (30 if abs(low_y - ly) < 12 else 20)
        labels.append(
            f'<text class="ch-note" x="{x(low_i) + (8 if anchor == "start" else -8)}"'
            f' y="{label_y}" text-anchor="{anchor}">'
            f"{n}-day low {ccy}{low_v:,.{dp}f}</text>"
        )

    hits = []
    hw = min(28.0, dx)
    for i, p in enumerate(points):
        if p.value is None:
            continue
        px, py = x(i), y(p.value)
        flip = px > PR - 190
        tx = px - 164 if flip else px + 12
        ty = max(PT, min(py - 24, PB - 50))
        hits.append(
            f'<g class="hp" tabindex="0" role="img" aria-label="{_e(_human_date(p.date))}:'
            f' {ccy}{p.value:,.{dp}f} per GPU-hour">'
            f'<line class="hp-cross" x1="{px}" y1="{PT}" x2="{px}" y2="{PB}"/>'
            f'<circle class="hp-dot" cx="{px}" cy="{py}" r="4.5"/>'
            f'<g class="hp-tip" transform="translate({round(tx, 1)} {round(ty, 1)})">'
            f'<rect class="hp-box" width="152" height="46" rx="3"/>'
            f'<line class="hp-key" x1="11" y1="18" x2="25" y2="18"/>'
            f'<text class="hp-val" x="31" y="22">{ccy}{p.value:,.{dp}f}</text>'
            f'<text class="hp-lab" x="11" y="37">{_e(_human_date(p.date))}</text></g>'
            f'<rect class="hp-hit" x="{round(px - hw / 2, 1)}" y="{PT}" width="{round(hw, 1)}"'
            f' height="{PB - PT}"/></g>'
        )

    xlabels = []
    for i in (0, n // 4, n // 2, (3 * n) // 4, n - 1):
        anchor = "start" if i == 0 else "end" if i == n - 1 else "middle"
        stamp = datetime.strptime(points[i].date, "%Y-%m-%d").strftime("%d %b").lstrip("0")
        xlabels.append(
            f'<text class="ch-tick" x="{x(i)}" y="288" text-anchor="{anchor}">{stamp}</text>'
        )

    gapped = sum(1 for p in points if p.value is None)
    summary = (
        f"{symbol}, {n} sessions to {_human_date(points[-1].date)}."
        f" {n - gapped} published, {gapped} gapped."
        f" Latest {ccy}{last_v:,.{dp}f}."
    )
    return (
        '<div class="scroll-x"><svg class="chart" viewBox="0 0 880 300" role="group"'
        f' aria-label="{_e(summary)}">'
        f'<g aria-hidden="true">{"".join(grid)}'
        f'<line class="ch-axis" x1="{PL}" y1="{PB}" x2="{PR}" y2="{PB}"/></g>'
        f"{paths}{isolated}"
        f'<g aria-hidden="true">{gapmarks}{"".join(ticks)}{"".join(xlabels)}'
        f'{"".join(labels)}</g>'
        f'<line class="ch-marker-rule" x1="{PL}" y1="{ly}" x2="{x(last_i)}" y2="{ly}"/>'
        f'<circle class="ch-marker-ring" cx="{x(last_i)}" cy="{ly}" r="6"/>'
        f'<circle class="ch-marker" cx="{x(last_i)}" cy="{ly}" r="4"/>'
        f'<g class="hp-layer">{"".join(hits)}</g>'
        "</svg></div>"
    )


def sparkline(points: list[Point]) -> str:
    """A 96x28 trend for a tile. Rendered only when at least two sessions published."""
    vals = [(i, p.value) for i, p in enumerate(points) if p.value is not None]
    if len(vals) < 2:
        return ""
    lo = min(v for _, v in vals)
    hi = max(v for _, v in vals)
    span = (hi - lo) or 1.0
    n = max(1, len(points) - 1)

    def sx(i: int) -> float:
        return round(4 + i / n * 88, 1)

    def sy(v: float) -> float:
        return round(24 - (v - lo) / span * 20, 1)

    d = _monotone_path([(sx(i), sy(v)) for i, v in vals])
    ex, ey = sx(vals[-1][0]), sy(vals[-1][1])
    return (
        '<svg class="spark" viewBox="0 0 96 28" width="96" height="28" aria-hidden="true"'
        f' focusable="false"><path class="spark__line" d="{d}"/>'
        f'<circle class="spark__ring" cx="{ex}" cy="{ey}" r="4.5"/>'
        f'<circle class="spark__dot" cx="{ex}" cy="{ey}" r="2.8"/></svg>'
    )


# ==========================================================================
# page shell
# ==========================================================================

# Marks the document as scripted, before body paints. The one-shot fade-in CSS
# (.js-boot body, see site.css) only fires when this class is present, so with scripting
# off the class is never added and the page renders at full opacity immediately —
# nothing to skip, nothing to wait for.
#
# v1.0 also read a stored theme preference here. TCI ships one theme (tokens.css,
# "THEMING CONTRACT"), so there is nothing to restore and no flash to guard against.
_BOOT_HEAD = "document.documentElement.classList.add('js-boot');"

# Vercel Web Analytics. The one <script src> on the page, and it is deliberate.
#
# WHY IT DOES NOT BREAK THE SELF-CONTAINMENT RULE: the path is same-origin. Vercel serves
# the page, so Vercel already sees every visitor's request; this hands nothing to a party
# that was not already in the path. The rule the site actually claims -- no visitor's IP
# reaches a THIRD party -- holds unchanged, and the test that enforces it now allows
# exactly this one path and nothing else. Vercel Web Analytics sets no cookies and does
# not fingerprint.
#
# On GitHub Pages the file does not exist and the request 404s. That is intended: the two
# mirrors stay byte-identical, and the mirror simply collects nothing. Analytics also has
# to be switched on in the Vercel project before the endpoint exists at all -- until then
# this tag is inert on both hosts.
_ANALYTICS = '<script defer src="/_vercel/insights/script.js"></script>'

# Same-origin refresh: re-stamps the ticker and the hero if the pipeline has published
# since the page was served. Pure enhancement — every value is already in the markup.
_REFRESH_JS = """(function(){
var stamp=document.getElementById('asof'),hero=document.getElementById('hero-usd');
if(!stamp||!hero)return;
var baked=stamp.getAttribute('data-generated');
function paint(d){
 var s=d.series&&d.series['EU-CRI-H100'];if(!s||s.value_usd==null)return;
 var v=s.value_usd.toFixed(2);
 if(v!==hero.textContent){hero.textContent=v;hero.classList.remove('is-ticked');
  void hero.offsetWidth;hero.classList.add('is-ticked');}
 var eur=document.getElementById('hero-eur');
 if(eur&&s.value_eur!=null)eur.textContent=s.value_eur.toFixed(2);
 stamp.textContent='AS OF '+d.generated_at.replace('T',' ').replace('Z',' UTC');
 stamp.setAttribute('data-generated',d.generated_at);}
function poll(){fetch('data/latest.json',{cache:'no-store'}).then(function(r){
 return r.ok?r.json():null;}).then(function(d){
 if(d&&d.generated_at&&d.generated_at!==baked){baked=d.generated_at;paint(d);}
 }).catch(function(){});}
document.addEventListener('visibilitychange',function(){
 if(document.visibilityState==='visible')poll();});
setInterval(poll,300000);})();"""

# The hero wave follows the cursor. Pure enhancement: without it the CSS-only wave
# still draws in and breathes on hover (site.css, "SCRIPTING OFF"), and nothing on the
# page depends on this running.
#
# Two ideas keep it smooth. First, the script writes only --mx and --amp on the
# container and lets CSS compute each bar's falloff, so a frame is one style pass over
# two properties rather than 48 inline transforms. Second, the tracked position is
# eased toward the raw pointer (a 0.16 lerp) instead of following it exactly, so a fast
# flick across the hero draws a crest that trails and settles rather than teleporting.
#
# Listeners sit on .hero, not on the graphic, so the wave also answers a cursor resting
# over the headline. Under prefers-reduced-motion the script returns before touching
# anything, leaving the static wave.
_WAVE_JS = """(function(){
var wave=document.querySelector('.hero__wave');if(!wave)return;
var hero=wave.closest?wave.closest('.hero'):wave.parentNode;if(!hero)return;
var mq=window.matchMedia&&window.matchMedia('(prefers-reduced-motion: reduce)');
if(mq&&mq.matches)return;
wave.classList.add('is-live','is-intro');
requestAnimationFrame(function(){wave.style.setProperty('--in','1');});
setTimeout(function(){wave.classList.remove('is-intro');},2600);
var box=null,tx=0.5,cx=0.5,ta=0,ca=0,raf=0;
function drop(){box=null;}
function step(){
 raf=0;
 cx+=(tx-cx)*0.16;ca+=(ta-ca)*0.09;
 wave.style.setProperty('--mx',cx.toFixed(4));
 wave.style.setProperty('--amp',ca.toFixed(4));
 if(Math.abs(tx-cx)>0.0004||Math.abs(ta-ca)>0.002)run();}
function run(){if(!raf)raf=requestAnimationFrame(step);}
function move(e){
 if(!box)box=wave.getBoundingClientRect();
 if(!box.width)return;
 var x=(e.clientX-box.left)/box.width;
 tx=x<-0.25?-0.25:(x>1.25?1.25:x);ta=1;run();}
function out(){ta=0;run();}
hero.addEventListener('pointermove',move,{passive:true});
hero.addEventListener('pointerleave',out,{passive:true});
window.addEventListener('resize',drop,{passive:true});
window.addEventListener('scroll',drop,{passive:true});})();"""


def _css(prefix: str = "") -> str:
    """The whole stylesheet, inlined, with font URLs resolved for this page's depth.

    tokens.css writes `url("{FONTS}outfit-latin.woff2")`. Because the sheet is inlined
    into the page rather than linked, that URL resolves against the PAGE — so a note at
    research/x.html needs `../assets/fonts/`, not `assets/fonts/`. Substituting here is
    what keeps the fonts same-origin (no off-origin request, nothing for a third party
    to log) without hard-coding a site root that GitHub Pages does not serve from.
    """
    css = _read(ASSETS / "tokens.css") + "\n" + _read(ASSETS / "site.css")
    return css.replace("{FONTS}", f"{prefix}assets/fonts/")


def _masthead(ctx: SiteContext, current: str, prefix: str) -> str:
    links = "".join(
        f'<a href="{prefix}{href}"'
        + (' aria-current="page"' if href == current else "")
        + f">{_e(label)}</a>"
        for href, label in NAV
    )
    return f"""<header class="masthead">
{_ticker(ctx)}
  <div class="masthead__bar">
    <div class="wrap masthead__inner">
      <a class="brand" href="{prefix}index.html">{_wordmark(24)}</a>
      <input type="checkbox" id="navtoggle" class="navtoggle">
      <label class="navbtn" for="navtoggle">
        <span class="navbtn__bars" aria-hidden="true"></span>
        <span>Menu</span>
      </label>
      <nav class="nav" aria-label="Primary">{links}
        <a href="mailto:{_e(CONTACT_EMAIL)}">Contact</a>
        <a class="nav__cta" href="{prefix}index.html#indices">View Indices</a>
      </nav>
    </div>
  </div>
</header>"""


def _footer(ctx: SiteContext, prefix: str) -> str:
    """Brand guide footer: four link columns, then the legal block.

    The mockup's fourth column carries a one-line disclaimer. The full "Important
    information" block underneath it is not decoration — it is the not-for-settlement
    position, the licence position and the print stamp — so it stays on every page.
    """
    return f"""<footer class="footer">
  <div class="wrap footer__inner">
    <div class="footer__cols">
      <div class="footer__brand">
        <span class="brand">{_wordmark(20)}</span>
        <p class="footer__tag">Reproducible reference prices for renting AI compute. Every
        print is recomputable from public sources using the published code.</p>
      </div>
      <nav class="footer__nav" aria-label="Footer, product">
        <h4>Product</h4>
        <a href="{prefix}index.html">Indices</a>
        <a href="{prefix}methodology.html">Methodology v{_e(ctx.version)}</a>
        <a href="{prefix}governance.html">Governance</a>
        <a href="{prefix}notices.html">Methodology notices</a>
      </nav>
      <nav class="footer__nav" aria-label="Footer, data">
        <h4>Data</h4>
        <a href="{prefix}data.html">Downloads &amp; terms</a>
        <a href="{prefix}data/index_history.csv">index_history.csv</a>
        <a href="{prefix}data/latest.json">latest.json</a>
      </nav>
      <nav class="footer__nav" aria-label="Footer, company">
        <h4>Company</h4>
        <a href="{prefix}research.html">Research</a>
        <a href="{_e(REPO_URL)}" rel="noopener">GitHub</a>
        <a href="mailto:{_e(CONTACT_EMAIL)}">Contact</a>
      </nav>
    </div>
    <div class="disclaimer">
      <h4 class="disclaimer__h">Important information</h4>
      <p>{_e(_rebrand(DISCLAIMER))}</p>
      <p>{_e(BRAND)} is a <strong>price-transparency benchmark, not a settlement
      benchmark</strong>. It is not transaction-based, is not administered by an authorised
      benchmark administrator, and must not be referenced in a financial contract. Values are
      derived from third-party public price surfaces believed to be reliable but are
      <strong>not independently verified</strong>; observed prices may differ materially from
      prices actually obtainable. Past values are not indicative of future values. A session
      with too few qualifying providers is <strong>published as a gap</strong> — never
      back-filled, never carried forward.</p>
      <p>&#169; 2026 Mark Rusch. The <strong>software</strong> that computes this index is
      open source under the Apache&#160;License&#160;2.0 and the <strong>methodology</strong>
      is published under CC&#160;BY&#160;4.0, so any print here can be reproduced and
      checked independently. The <strong>index data</strong> is published under separate
      terms: free to use for research, journalism and other non-commercial purposes with
      attribution. &#8220;{_e(BRAND)}&#8221; and &#8220;{SERIES_PREFIX_PUBLISHED}&#8221; are
      used as the identity of this benchmark family and its published values; a fork is
      welcome and must carry its own name.</p>
      <p class="disclaimer__meta num">{SERIES_PREFIX_PUBLISHED}&#8209;M v{_e(ctx.version)}
      &#183; methodology hash sha256:{_e(ctx.lock_hash[:12])}&#8230; &#183; generated
      {_e(ctx.generated_at)} &#183; administrator Mark Rusch &#183; USD primary, EUR companion
      at the ECB reference rate dated on or before the print date.</p>
    </div>
  </div>
</footer>"""


def _abs(path: str) -> str:
    """A site-root-relative path as an absolute URL on the canonical domain.

    `index.html` collapses to the bare domain, because that is the URL people visit and
    share. Declaring a canonical of /index.html while every inbound link points at / is
    how a site ends up competing with itself for its own home page.
    """
    rel = path.lstrip("/")
    if rel == "index.html":
        return f"{SITE_URL}/"
    return f"{SITE_URL}/{rel}"


def _structured_data(ctx: SiteContext, canonical: str, *, dataset: bool) -> str:
    """JSON-LD: who administers this and, on the dashboard, what the dataset is.

    Search engines and citation tools read this; a human never sees it. It is generated
    rather than hand-written so the version, the licence and the cut-off cannot drift
    from what the rest of the page says.
    """
    org: dict[str, object] = {
        "@type": "Organization",
        "@id": f"{SITE_URL}/#administrator",
        "name": BRAND_FULL,
        "alternateName": BRAND,
        "url": SITE_URL,
        "email": CONTACT_EMAIL,
        "founder": {"@type": "Person", "name": "Mark Rusch"},
        "description": BRAND_LINE,
    }
    graph: list[dict[str, object]] = [
        org,
        {
            "@type": "WebSite",
            "@id": f"{SITE_URL}/#website",
            "url": SITE_URL,
            "name": BRAND_FULL,
            "publisher": {"@id": f"{SITE_URL}/#administrator"},
            "inLanguage": "en",
        },
        {"@type": "WebPage", "url": _abs(canonical), "isPartOf": {"@id": f"{SITE_URL}/#website"}},
    ]
    if dataset:
        graph.append(
            {
                "@type": "Dataset",
                "@id": f"{SITE_URL}/#dataset",
                "name": f"{display_series(HEADLINE)} — {BRAND_FULL}",
                "description": (
                    "Daily reference price for one NVIDIA H100 SXM 80GB GPU-hour, on-demand,"
                    " per-GPU, ex-VAT, delivered from an EU/EEA data centre. USD primary with"
                    " a EUR companion at the ECB reference rate."
                ),
                "creator": {"@id": f"{SITE_URL}/#administrator"},
                "license": "https://creativecommons.org/licenses/by/4.0/",
                "isAccessibleForFree": True,
                "temporalCoverage": f"2026-07-18/{ctx.date}",
                "measurementTechnique": (
                    f"Capacity-weighted median over offers, methodology v{ctx.version}"
                ),
                "variableMeasured": "USD per GPU-hour",
                "distribution": [
                    {
                        "@type": "DataDownload",
                        "encodingFormat": "text/csv",
                        "contentUrl": _abs("data/index_history.csv"),
                    },
                    {
                        "@type": "DataDownload",
                        "encodingFormat": "application/json",
                        "contentUrl": _abs("data/latest.json"),
                    },
                ],
            }
        )
    payload = json.dumps({"@context": "https://schema.org", "@graph": graph}, indent=None)
    # A literal "</script>" inside the block would end the element early; escaping the
    # angle bracket is the standard defence and leaves the JSON valid.
    return f'<script type="application/ld+json">{payload.replace("<", chr(92) + "u003c")}</script>'


def _shell(
    ctx: SiteContext,
    *,
    title: str,
    description: str,
    current: str,
    body: str,
    prefix: str = "",
    extra_js: str = "",
    canonical: str | None = None,
    dataset: bool = False,
    noindex: bool = False,
) -> str:
    scripts = f"<script>{extra_js}</script>" if extra_js else ""
    # Every page's nav href is also its path, except a research note, which lives one
    # directory down and marks the research index as current.
    href = canonical if canonical is not None else current
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)}</title>
<meta name="description" content="{_e(description)}">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="#0b0c0d">
{'<meta name="robots" content="noindex">' if noindex else ""}
<link rel="canonical" href="{_e(_abs(href))}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{_e(BRAND_FULL)}">
<meta property="og:title" content="{_e(title)}">
<meta property="og:description" content="{_e(description)}">
<meta property="og:url" content="{_e(_abs(href))}">
<meta property="og:image" content="{_e(_abs(OG_IMAGE))}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{_e(title)}">
<meta name="twitter:description" content="{_e(description)}">
<meta name="twitter:image" content="{_e(_abs(OG_IMAGE))}">
<link rel="alternate" type="application/atom+xml" title="{_e(BRAND)} Research"
 href="{prefix}feed.xml">
<link rel="icon" href="{FAVICON}">
{_structured_data(ctx, href, dataset=dataset)}
{_ANALYTICS}
<script>{_BOOT_HEAD}</script>
<style>
{_css(prefix)}
</style>
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
{_masthead(ctx, current, prefix)}
{body}
{_footer(ctx, prefix)}
{scripts}
</body>
</html>
"""


# ==========================================================================
# build context
# ==========================================================================


@dataclass
class SiteContext:
    conn: sqlite3.Connection
    factors: Factors
    version: str
    lock_hash: str
    generated_at: str
    head: sqlite3.Row | None
    date: str


def _lock_hash() -> str:
    path = REPO_ROOT / "METHODOLOGY.lock"
    if not path.exists():
        return "unlocked"
    data = yaml.safe_load(_read(path)) or {}
    return str((data.get("current") or {}).get("hash", "unlocked"))


# ==========================================================================
# dashboard sections
# ==========================================================================


def _ticker(ctx: SiteContext) -> str:
    """The scrolling ticker band, on every page (brand guide: shared header partial).

    Two identical runs translated by exactly -50% make the loop seamless. Each run
    repeats the series list until it is comfortably wider than a desktop viewport —
    a run narrower than the screen leaves a bald patch at the right edge on every
    cycle, which is the one way a marquee looks broken rather than deliberate.

    The duplicate run is aria-hidden, so a screen reader gets each value once. The
    as-of stamp is pinned outside the marquee: it is the cell the refresh script
    rewrites, and a timestamp that scrolls away is a timestamp nobody reads.
    """
    items: list[str] = []
    for series in TICKER:
        row = current_print(ctx.conn, series, ctx.date)
        if row is None:
            continue
        if row["value_usd"] is None:
            value = ('<span class="tickerbar__gap">&#8212;&#8194;GAP</span>')
        else:
            dp = 2 if series != COMPOSITE else 1
            unit = "" if series == COMPOSITE else "$"
            value = (
                f'<span class="tickerbar__val num">{unit}'
                f'{_num(row["value_usd"], dp)}</span>'
            )
        items.append(
            '<span class="tickerbar__item">'
            '<span class="tickerbar__dot" aria-hidden="true"></span>'
            f'<span class="tickerbar__sym">{_nbsp_series(series)}</span>{value}</span>'
        )
    stamp = (
        f'<div class="tickerbar__stamp num" id="asof" aria-live="polite"'
        f' data-generated="{_e(ctx.generated_at)}">'
        f'AS OF {_e(ctx.generated_at.replace("T", " ").replace("Z", " UTC"))}</div>'
    )
    if not items:
        # No series resolved for this session. The band still carries the stamp — an
        # empty ticker that says when it was built beats a ticker that is simply absent.
        return f'<div class="tickerbar">{stamp}</div>'

    # ~200px per item; two runs of >=10 keep the track wider than a 1920px viewport.
    reps = max(1, -(-10 // len(items)))
    run = "".join(items * reps)
    return (
        '<div class="tickerbar">'
        '<div class="tickerbar__marquee" role="region"'
        f' aria-label="{_e(BRAND)} series, last published values">'
        f'<div class="tickerbar__track">'
        f'<div class="tickerbar__run">{run}</div>'
        f'<div class="tickerbar__run" aria-hidden="true">{run}</div>'
        "</div></div>"
        f"{stamp}</div>"
    )


# --- landing-page components ----------------------------------------------


def _wave() -> str:
    """The hero's wave graphic — brand furniture, not a chart.

    Deliberately abstract: no axis, no scale, no readable value, and aria-hidden, so it
    can never be mistaken for a price series. The shape is the brand guide's own curve
    (two summed sines), evaluated here rather than in JavaScript so the graphic is
    present with scripting disabled. It draws in once on load (tci-rise, staggered) and
    breathes only while hovered (tci-wave) — motion that answers the reader instead of
    looping at them, and silenced entirely by prefers-reduced-motion.
    """
    from math import pi, sin

    n = 48
    cols = []
    for i in range(n):
        t = i / (n - 1)
        height = max(8, round(90 + sin(t * pi * 2.2 + 0.5) * 60 + sin(t * pi * 5) * 16))
        hi = 0.3 < t < 0.7 and i % 3 == 0
        cols.append(
            f'<span class="wave__col{" wave__col--hi" if hi else ""}"'
            # --i is this column's position across the wave, 0..1. It is the only
            # per-column input the cursor maths needs: the falloff against the
            # pointer is computed in CSS, so a frame writes two properties on the
            # container instead of ninety-six on the columns.
            f' style="--i:{t:.4f};--h:{height}px;--dot:{4 if hi else 2.5}px;'
            f'--d:{i * 0.03:.2f}s;--dw:{i * 0.035:.2f}s;'
            f'--wd:{1.3 + (i % 5) * 0.15:.2f}s">'
            '<span class="wave__dot"></span><span class="wave__bar"></span></span>'
        )
    return f'<div class="hero__wave" aria-hidden="true">{"".join(cols)}</div>'


def _hero() -> str:
    return f"""<section class="hero">
  {_wave()}
  <div class="hero__inner">
    <h1 class="hero__h">Independent. Transparent.<br>Built for the compute
    market<span class="hero__stop">.</span></h1>
    <p class="hero__dek">{_e(BRAND_LINE)}</p>
    <div class="hero__cta">
      <a class="btn btn--primary" href="#indices">View Indices</a>
      <a class="btn btn--ghost" href="methodology.html">Our Methodology</a>
    </div>
  </div>
</section>"""


def _series_table(ctx: SiteContext) -> str:
    """Today's print for every class series, in the brand guide's flat ruled register.

    Every row is this session's row or nothing: `current_print` refuses to hand back an
    older print dressed as today's. A series that gapped keeps its row and says why.
    """
    region = ctx.factors.reference_unit.location.replace("_", "/")
    rows = []
    for series in HEADLINE_TABLE:
        row = current_print(ctx.conn, series, ctx.date)
        if row is None:
            price = '<td class="ta-r u">&#8212;</td>'
            delta = '<td class="ta-r u">not computed</td>'
        elif row["value_usd"] is None:
            price = '<td class="ta-r u">&#8212;</td>'
            delta = (
                f'<td class="ta-r u">gap &#183; '
                f'{_e(_flag_words(row["flags"]) or "not computed")}</td>'
            )
        else:
            month_ago = (
                date_type.fromisoformat(row["date"]) - timedelta(days=WINDOW_DAYS)
            ).isoformat()
            base = value_on_or_before(ctx.conn, series, month_ago)
            price = f'<td class="ta-r">${_num(row["value_usd"])}</td>'
            delta = f'<td class="ta-r">{_delta(_pct(row["value_usd"], base), 1)}</td>'
        rows.append(
            f'<tr><th scope="row">{_nbsp_series(series)}</th>'
            f'<td class="stable__reg">{_e(region)}</td>{price}{delta}</tr>'
        )
    return f"""<div class="scroll-x"><table class="stable">
  <caption class="vh">{_e(BRAND)} class series, print for {_e(ctx.date)}</caption>
  <thead><tr>
    <th scope="col">Series</th><th scope="col">Region</th>
    <th scope="col" class="ta-r">$/GPU&#8209;hr</th>
    <th scope="col" class="ta-r">{WINDOW_DAYS}d &#916;</th>
  </tr></thead>
  <tbody>{"".join(rows)}</tbody>
</table></div>"""


def _pillars(ctx: SiteContext) -> str:
    gate = ctx.factors.aggregation.min_providers
    cap = f"{ctx.factors.weights.max_weight_share_pct:,.0f}"
    cards = (
        (
            "dot", "Reproducible",
            "Every parameter is a visible config value, and the five files that can change "
            "a print are hash-locked. Every print can be rebuilt from public sources by "
            "anyone with the repo.",
        ),
        (
            "sq", "Vendor-neutral",
            "A weighted median over offers from marketplaces, neoclouds and sovereign "
            f"operators, gated at {gate} providers, with a {cap}% concentration cap — "
            "no single source sets the price.",
        ),
        (
            "rule", "Open methodology",
            "Design follows the IOSCO Principles for Financial Benchmarks as voluntary best "
            "practice — published, versioned, and change-controlled, with the full "
            "constituent set behind every print.",
        ),
    )
    return '<div class="pillars">' + "".join(
        f'<article class="pillar"><span class="pillar__ico pillar__ico--{kind}"'
        f' aria-hidden="true"><i></i></span>'
        f'<h3 class="pillar__t">{_e(title)}</h3>'
        f'<p class="pillar__d">{_e(body)}</p></article>'
        for kind, title, body in cards
    ) + "</div>"


def _family(ctx: SiteContext) -> str:
    """The index family. Only the first one exists; the other says so plainly.

    A roadmap card is fine; a roadmap card that reads like a shipped product is the same
    failure as a stale print dressed as live, so the status label leads and the copy for
    an unbuilt index never implies a number.

    TCI-ERI (energy) was listed here until 2026-09-07 and was withdrawn. The day-ahead
    power collector exists and has never landed a row, so the card was advertising an
    intention rather than a pipeline. The roadmap is compute and storage only.
    """
    classes = len(ctx.factors.model_classes)
    cards = (
        (
            True, "Live", f"{SERIES_PREFIX_PUBLISHED}",
            f"Compute Reference Index — GPU-hour pricing across {classes} hardware classes, "
            "published daily with its full constituent set.",
        ),
        (
            False, "Planned", "TCI-SRI",
            "Storage Reference Index — reference pricing for high-throughput storage, "
            "the other metered cost on a training run. No data is collected for this yet.",
        ),
    )
    return '<div class="famcards">' + "".join(
        f'<article class="fam{" fam--live" if live else ""}">'
        f'<span class="fam__status">{_e(status)}</span>'
        f'<h3 class="fam__sym">{_e(sym).replace("-", "&#8209;")}</h3>'
        f'<p class="fam__desc">{_e(desc)}</p></article>'
        for live, status, sym, desc in cards
    ) + "</div>"


def _data_teaser(ctx: SiteContext) -> str:
    """The data section's terminal block — a command that actually works.

    The mockup curls a `tci.dev/api/v1/...` endpoint. No such host or API exists, so the
    block shows the real published JSON and the real stored series key, and its output is
    generated from this session's row rather than typed. A fabricated example response on
    a benchmark site is the same class of error as a fabricated print.
    """
    head = ctx.head
    if head is not None and head["value_usd"] is not None:
        out = (
            f'{{"date":"{head["date"]}","value_usd":{head["value_usd"]:.2f},'
            f'"flags":""}}'
        )
    elif head is not None:
        out = (
            f'{{"date":"{head["date"]}","value_usd":null,'
            f'"flags":"{_e(head["flags"] or "")}"}}'
        )
    else:
        out = '{"date":null,"value_usd":null}'
    return f"""<div class="teaser">
  <div>
    <h2 class="teaser__h" id="data">Full history. CSV and JSON.</h2>
    <p class="teaser__d">Every observation, every print, every constituent set — the
    candidates that were rejected as well as the ones that counted, and the reason for
    each. Downloadable and versioned since day one.</p>
    <div class="teaser__cta">
      <a class="btn btn--primary" href="data/index_history.csv">Download CSV</a>
      <a class="btn btn--ghost" href="data.html">Data &amp; terms</a>
    </div>
  </div>
  <div class="term">
    <div class="term__chrome" aria-hidden="true"><i></i><i></i><i></i>
      <span class="term__name">latest.json</span></div>
    <div class="term__body">
      <div class="term__c"># today's print, straight from the published JSON</div>
      <div>curl -s ./data/latest.json | jq '.series["{HEADLINE}"]'</div>
      <div class="term__hi">{_e(out)}</div>
    </div>
  </div>
</div>"""


def _research_strip(notes: list[Note], limit: int = 3) -> str:
    rows = "".join(
        f'<a class="linklist__row" href="research/{_e(n.slug)}.html">'
        f'<span class="linklist__t">{_e(n.title)}</span>'
        f'<span class="linklist__when num">{_e(n.date or "unscheduled")}</span></a>'
        for n in notes[:limit]
    )
    return f'<div class="linklist">{rows}</div>'


def _print_card(ctx: SiteContext) -> str:
    head = ctx.head
    assert head is not None
    ru = ctx.factors.reference_unit
    desc = (
        f"NVIDIA {ru.gpu_model.replace('_', ' ')} 80GB, {ru.term.replace('_', '-')},"
        f" {ru.location.replace('_', '/')} regions. Weighted median over offers,"
        f" per GPU-hour, ex-VAT."
    )
    gate = ctx.factors.aggregation.min_providers
    published = head["value_usd"] is not None

    if published:
        prev = previous_published(ctx.conn, HEADLINE, head["date"])
        pct = _pct(head["value_usd"], prev[1] if prev else None)
        gap_days = (
            (date_type.fromisoformat(head["date"]) - date_type.fromisoformat(prev[0])).days
            if prev
            else 0
        )
        vs = (
            f'<span class="print__vs">vs {_e(_human_date(prev[0]))} print'
            f"{f' ({gap_days} sessions back)' if gap_days > 1 else ''}</span>"
            if prev
            else '<span class="print__vs">first published print</span>'
        )
        abs_move = (
            f'<span class="print__abs num">{"+" if head["value_usd"] >= prev[1] else "&#8722;"}'
            f'{abs(head["value_usd"] - prev[1]):,.2f}</span>'
            if prev
            else ""
        )
        figure = (
            '<div class="print__figure" aria-live="polite">'
            '<span class="print__ccy ccy-usd" aria-hidden="true">$</span>'
            '<span class="print__ccy ccy-eur" aria-hidden="true">&#8364;</span>'
            f'<span class="print__value num ccy-usd" id="hero-usd">'
            f'{_num(head["value_usd"])}</span>'
            f'<span class="print__value num ccy-eur" id="hero-eur">'
            f'{_num(head["value_eur"])}</span>'
            '<span class="print__unit">/GPU&#8209;hr</span></div>'
        )
        row = f'<div class="print__row">{_delta(pct)}{abs_move}{vs}</div>'
        status = (
            f'<span class="chip chip--good">{_icon("check")}<span>Index live &#183;'
            f' {head["n_sources"]} of {gate} providers</span></span>'
        )
    else:
        figure = (
            '<div class="print__figure" aria-live="polite">'
            '<span class="print__value num" id="hero-usd"'
            ' aria-label="No value published">&#8212;&#8212;</span>'
            '<span class="print__unit">no print this session</span></div>'
        )
        last = previous_published(ctx.conn, HEADLINE, head["date"])
        row = (
            '<div class="print__row"><span class="print__vs">Last published '
            f'{_e(_human_date(last[0]))} at ${_num(last[1])}. Not carried forward.</span></div>'
            if last
            else '<div class="print__row"><span class="print__vs">No print published yet.'
            "</span></div>"
        )
        status = (
            f'<span class="chip chip--warning">{_icon("warn")}<span>Gapped &#183;'
            f' {_e(_flag_words(head["flags"]))} ({head["n_sources"]} of {gate} providers)'
            "</span></span>"
        )

    chips = (
        f'<div class="print__chips">{status}'
        f'<span class="chip chip--neutral">{_icon("lock")}<span>Hash&#8209;locked'
        f' <span class="num">v{_e(ctx.version)}</span></span></span>'
        f'<span class="chip chip--neutral"><span>{head["n_executable"]} executable'
        f" input{'s' if head['n_executable'] != 1 else ''}</span></span></div>"
    )

    ccy_toggle = (
        '<fieldset class="seg seg--ccy"><legend class="vh">Quote currency</legend>'
        '<input type="radio" id="ccy-usd" name="ccy" value="usd" checked>'
        '<label for="ccy-usd">USD</label>'
        '<input type="radio" id="ccy-eur" name="ccy" value="eur">'
        '<label for="ccy-eur">EUR</label></fieldset>'
        if published
        else ""
    )

    fx = (
        f'{_num(head["fx_rate"], 4)} @ {_e(head["fx_date"])}'
        if head["fx_rate"]
        else "&#8212;"
    )
    eur = f'&#8364;{_num(head["value_eur"], 6)}' if head["value_eur"] is not None else "&#8212;"
    return f"""<section class="print" aria-labelledby="print-h">
  <div class="print__main">
    <div class="print__head">
      <div>
        <div class="eyebrow">Headline index</div>
        <h2 class="print__sym" id="print-h">{_nbsp_series(HEADLINE)}</h2>
        <p class="print__desc">{_e(desc)}</p>
      </div>
      {ccy_toggle}
    </div>
    {figure}
    {row}
    {chips}
  </div>
  <div class="print__meta">
    <dl class="metagrid">
      <div><dt>Print date</dt><dd class="num">{_e(head["date"])}</dd></div>
      <div><dt>EUR companion</dt><dd class="num">{eur}</dd></div>
      <div><dt>ECB EUR/USD</dt><dd class="num">{fx}</dd></div>
      <div><dt>Providers</dt><dd class="num">{head["n_sources"]} in panel</dd></div>
      <div><dt>Estimator</dt><dd>{_e(ctx.factors.aggregation.estimator.replace("_", " "))}</dd>
      </div>
      <div><dt>Methodology</dt><dd><a href="methodology.html">
        {SERIES_PREFIX_PUBLISHED}&#8209;M v{_e(ctx.version)}</a></dd></div>
    </dl>
  </div>
</section>"""


def _tiles(ctx: SiteContext) -> str:
    dates = _window(ctx.date, 14)
    out = []
    for series in TILES:
        row = current_print(ctx.conn, series, ctx.date)
        label = SERIES_LABEL.get(series, series)
        head = (
            f'<div class="tile__head"><span class="tile__sym">{_nbsp_series(series)}</span>'
            f'<span class="tile__note">{_e(label)}</span></div>'
        )
        if row is None:
            out.append(
                f'<article class="tile tile--gap">{head}'
                f'<div class="tile__body"><div class="tile__gap">'
                f'<span class="tile__dash">&#8212;&#8212;</span></div></div>'
                f'<div class="tile__foot"><span class="chip chip--neutral">'
                f"<span>Not yet computed</span></span></div></article>"
            )
            continue
        history = _windowed(series_history(ctx.conn, series, since=dates[0]), dates)
        if row["value_usd"] is not None:
            prev = previous_published(ctx.conn, series, row["date"])
            body = (
                f'<div class="tile__num"><span class="tile__ccy" aria-hidden="true">$</span>'
                f'<span class="tile__val num">{_num(row["value_usd"])}</span></div>'
                f"{sparkline(history)}"
            )
            foot = _delta(_pct(row["value_usd"], prev[1] if prev else None))
            cls = "tile"
        else:
            last = previous_published(ctx.conn, series, row["date"])
            last_txt = (
                f"last ${_num(last[1])} on {_e(_human_date(last[0]))}"
                if last
                else "never published"
            )
            body = (
                f'<div class="tile__gap"><span class="tile__dash">&#8212;&#8212;</span>'
                f'<span class="tile__last">{last_txt}</span></div>{sparkline(history)}'
            )
            foot = (
                f'<span class="chip chip--warning">{_icon("gap")}'
                f'<span>{_e(_flag_words(row["flags"]) or "gapped")}</span></span>'
            )
            cls = "tile tile--gap"
        out.append(
            f'<article class="{cls}">{head}'
            f'<div class="tile__body">{body}</div>'
            f'<div class="tile__foot">{foot}</div></article>'
        )
    return '<div class="tiles">' + "".join(out) + "</div>"


def _chart_card(ctx: SiteContext) -> str:
    dates = _window(ctx.date, WINDOW_DAYS)
    points = _windowed(series_history(ctx.conn, HEADLINE, since=dates[0]), dates)
    vals = [p.value for p in points if p.value is not None]
    published = len(vals)
    meta = (
        f"Last ${_num(vals[-1])} &#183; high ${_num(max(vals))} &#183; low ${_num(min(vals))}"
        if vals
        else "No published print in the window"
    )
    rows = "".join(
        f'<tr><td class="num">{_e(_human_date(p.date))}</td>'
        + (
            f'<td class="num ta-r">{_num(p.value)}</td><td>published</td>'
            if p.value is not None
            else '<td class="num ta-r u">&#8212;</td><td>gap &#183; '
            + _e(_flag_words(p.flags) or "not computed")
            + "</td>"
        )
        + "</tr>"
        for p in points
    )
    return f"""<div class="card">
  <div class="card__head">
    <div><h3 class="card__title">{_nbsp_series(HEADLINE)}</h3>
    <p class="card__sub">USD per GPU-hour &#183; {WINDOW_DAYS} sessions to
    {_e(_human_date(ctx.date))} &#183; {published} published, {WINDOW_DAYS - published}
    gapped</p></div>
    <span class="card__meta num">{meta}</span>
  </div>
  <div class="card__body">
    {line_chart(points, symbol=display_series(HEADLINE))}
    <p class="ledger__d" style="margin-top:var(--space-4)">A gapped session is drawn as a
    hairline tick on the baseline and the line breaks across it. Nothing is interpolated:
    the index publishes a gap rather than a value it cannot defend.</p>
    <details class="tableview"><summary>Table view &#8212; {WINDOW_DAYS} sessions</summary>
      <div class="tableview__scroll"><table><caption class="vh">Every session in the window,
      published or gapped</caption><thead><tr><th scope="col">Session</th>
      <th scope="col" class="ta-r">USD/GPU-hr</th><th scope="col">State</th></tr></thead>
      <tbody>{rows}</tbody></table></div>
    </details>
  </div>
</div>"""


def _tier_badge(provider: str, tier: str, factors: Factors) -> tuple[str, str]:
    """L1/L2/L3 is ORDINAL: executable quote > neocloud list > hyperscaler catalog."""
    if tier == "executable":
        return "L1", "Executable marketplace quote"
    if factors.segment_of(provider) == "hyperscaler":
        return "L3", "Hyperscaler catalog list price"
    return "L2", "Published neocloud list price"


def _constituents_card(ctx: SiteContext) -> str:
    rows = constituents_for(ctx.conn, HEADLINE, ctx.date)
    if not rows:
        return (
            '<div class="card"><div class="card__body"><div class="gapnote">'
            + _icon("warn", 14)
            + "<p>No constituent set stored for this session.</p></div></div></div>"
        )
    head = ctx.head
    assert head is not None
    links = provider_links()
    sovereign = load_sovereign()
    max_w = max((r["weight"] for r in rows if r["included"]), default=1.0) or 1.0
    body = []
    for c in rows:
        tier, tier_title = _tier_badge(c["provider"], c["tier"], ctx.factors)
        segment = ctx.factors.segment_of(c["provider"])
        flags = []
        if c["exclusion_reason"] == "trimmed" and c["included"]:
            flags.append(("TRIM", "Clamped to the k-th order statistic by the trim"))
        if "weight_capped" in (c["flags"] or ""):
            flags.append(("CAP", "Weight limited by the 25% concentration cap"))
        if "jump" in (c["flags"] or ""):
            flags.append(("JUMP", "Moved more than the jump threshold day-over-day"))
        if c["provider"] in sovereign:
            flags.append(("SOV", "EU/EEA-headquartered operator"))
        if not c["included"]:
            reason = c["exclusion_reason"] or "excluded"
            flags.append(
                (
                    EXCLUSION_CODE.get(reason, reason.upper().replace("_", " ")[:10]),
                    f"Excluded: {reason.replace('_', ' ')}",
                )
            )
        flag_html = "".join(
            f'<span class="flag" title="{_e(t)}">{_e(f)}</span>' for f, t in flags
        ) or '<span class="u">&#8212;</span>'
        price = (
            f'<td class="ta-r num strong">{_num(c["price_usd"], 4)}</td>'
            if c["price_usd"]
            else '<td class="ta-r u">&#8212;</td>'
        )
        weight = (
            f'<td class="ta-r"><span class="wcell"><span class="num">'
            f'{_num(c["weight"], 1)}%</span><span class="meter" aria-hidden="true">'
            f'<span class="meter__fill" style="width:{c["weight"] / max_w * 100:.1f}%">'
            f"</span></span></span></td>"
            if c["included"]
            else '<td class="ta-r num u">0.0%</td>'
        )
        url = (links.get(c["provider"]) or {}).get("url")
        name = _e(c["source"]) if c["source"] else "page"
        src = (
            f'<td class="ta-r"><a class="srclink" href="{_e(url)}" rel="noopener">'
            f'{name}{_icon("ext")}</a></td>'
            if url
            else f'<td class="ta-r u">{name}</td>'
        )
        body.append(
            f'<tr><th scope="row" class="grid__name">{_e(c["provider"])}</th>'
            f'<td class="u">{_e(segment)}</td>'
            f'<td class="ta-c"><span class="badge badge--{tier.lower()}"'
            f' title="{_e(tier_title)}">{tier}</span></td>'
            f"{price}{weight}"
            f'<td class="grid__flags">{flag_html}</td>{src}</tr>'
        )
    k = ctx.factors.aggregation.trim_for(sum(1 for r in rows if r["included"]))
    total = sum(r["weight"] for r in rows if r["included"])
    value_cell = (
        f'<td class="ta-r num strong">{_num(head["value_usd"], 4)}</td>'
        if head["value_usd"] is not None
        else '<td class="ta-r u">no print</td>'
    )
    return f"""<div class="card card__body--flush">
<div class="scroll-x">
<table class="grid">
  <caption class="vh">{_e(display_series(HEADLINE))} constituents at the
  {_e(ctx.date)} print</caption>
  <thead><tr>
    <th scope="col">Provider</th>
    <th scope="col">Segment</th>
    <th scope="col" class="ta-c">Tier</th>
    <th scope="col" class="ta-r">Price <span class="u">USD/GPU&#8209;hr</span></th>
    <th scope="col" class="ta-r">Weight</th>
    <th scope="col">Flags</th>
    <th scope="col" class="ta-r">Source</th>
  </tr></thead>
  <tbody>{"".join(body)}</tbody>
  <tfoot><tr>
    <th scope="row" colspan="3">Weighted median over offers</th>
    {value_cell}
    <td class="ta-r num strong">{_num(total, 1)}%</td>
    <td colspan="2" class="u">Trim k={k} each end &#183; 25% concentration cap</td>
  </tr></tfoot>
</table>
</div></div>"""


def _quality_card(ctx: SiteContext) -> str:
    head = ctx.head
    assert head is not None
    agg = ctx.factors.aggregation
    rows = constituents_for(ctx.conn, HEADLINE, ctx.date)
    included = [r for r in rows if r["included"]]
    exec_weight = sum(r["weight"] for r in included if r["tier"] == "executable")
    total_weight = sum(r["weight"] for r in included) or 1.0
    exec_share = exec_weight / total_weight * 100.0
    n = head["n_sources"] or 0
    passes = head["value_usd"] is not None
    dates = _window(ctx.date, WINDOW_DAYS)
    hist = _windowed(series_history(ctx.conn, HEADLINE, since=dates[0]), dates)
    published = sum(1 for p in hist if p.value is not None)
    capped = any("weight_capped" in (r["flags"] or "") for r in rows)
    fx_age = (
        (date_type.fromisoformat(ctx.date) - date_type.fromisoformat(head["fx_date"])).days
        if head["fx_date"]
        else None
    )
    gate_chip = (
        f'<span class="chip chip--good">{_icon("check")}<span>Gate met</span></span>'
        if passes
        else f'<span class="chip chip--warning">{_icon("warn")}<span>Gate not met</span></span>'
    )
    cap_pct = f"{ctx.factors.weights.max_weight_share_pct:,.0f}"
    cap_words = "bound on this print" if capped else "did not bind on this print"
    fx_vintage = f"T&#8722;{fx_age}" if fx_age is not None else "&#8212;"
    return f"""<div class="card" id="quality">
  <div class="card__head">
    <div><h3 class="card__title">Data quality</h3>
    <p class="card__sub">Everything that decides whether this session publishes at all</p>
    </div>
    <span class="card__meta">{gate_chip}</span>
  </div>
  <div class="qgrid">
    <div class="qstat">
      <span class="qstat__k">Publication gate</span>
      <span class="qstat__v">{n}<span class="u"> / {agg.min_providers}</span></span>
      <span class="qstat__n">Qualifying providers against the minimum. Below it the value is
      null and the session is flagged, never estimated.</span>
    </div>
    <div class="qstat">
      <span class="qstat__k">Executable share</span>
      <span class="qstat__v">{exec_share:,.1f}<span class="u">%</span></span>
      <span class="qbar" aria-hidden="true"><i style="width:{min(exec_share, 100):.1f}%"></i>
      </span>
      <span class="qstat__n">Share of index weight from executable marketplace quotes;
      the rest is published list price.</span>
    </div>
    <div class="qstat">
      <span class="qstat__k">Session coverage</span>
      <span class="qstat__v">{published}<span class="u"> / {WINDOW_DAYS}</span></span>
      <span class="qbar" aria-hidden="true">
      <i style="width:{published / WINDOW_DAYS * 100:.1f}%"></i></span>
      <span class="qstat__n">Sessions in the last {WINDOW_DAYS} days that cleared the gate.
      The remainder are published as gaps.</span>
    </div>
    <div class="qstat">
      <span class="qstat__k">FX vintage</span>
      <span class="qstat__v">{fx_vintage}</span>
      <span class="qstat__n">ECB reference rate {_num(head["fx_rate"], 4)} dated
      {_e(head["fx_date"] or "n/a")}. The ECB publishes after the cut-off, so the EUR leg is
      T&#8722;1 by construction.</span>
    </div>
    <div class="qstat">
      <span class="qstat__k">Concentration cap</span>
      <span class="qstat__v">{"BOUND" if capped else "SLACK"}</span>
      <span class="qstat__n">The {cap_pct}% per-provider cap {cap_words}; it is
      mathematically inert at n=4 and binds only from n=5.</span>
    </div>
    <div class="qstat">
      <span class="qstat__k">Trim</span>
      <span class="qstat__v">k={agg.trim_for(len(included))}</span>
      <span class="qstat__n">Count-based: the k highest and k lowest offers are clamped to the
      k-th order statistic. Percentile winsorising is inert at this panel size.</span>
    </div>
  </div>
</div>"""


def _sources_card(ctx: SiteContext) -> str:
    rows = "".join(
        f'<div class="srcs__row"><div><span class="srcs__name">{_e(s["source"])}</span>'
        f'<span class="srcs__label">{_e(s["label"] or "")}</span></div>'
        f'<div class="srcs__ep">{_e(s["endpoint"] or "")}</div>'
        + (
            f'<a class="srclink" href="{_e(s["url"])}" rel="noopener">Site{_icon("ext")}</a>'
            if s.get("url")
            else '<span class="u">&#8212;</span>'
        )
        + "</div>"
        for s in sources_panel()
    )
    return f"""<div class="card" id="sources">
  <div class="card__head">
    <div><h3 class="card__title">Sources</h3>
    <p class="card__sub">Every collector the index reads. Overlay data (power prices) is
    published beside the index and never enters the calculation.</p></div>
  </div>
  <div class="srcs">{rows}</div>
</div>"""


def _model_mix_entries(ctx: SiteContext) -> tuple[str, int, list[sqlite3.Row]] | None:
    """The standing weight review's current row set, scope='model' — shared by the
    full ledger (`_weights_card`) and the compact summary bar (`_model_mix_bar`) so
    the two can never drift onto different revisions of the same basket."""
    row = ctx.conn.execute(
        "SELECT effective_date FROM weight_sets WHERE effective_date <= ?"
        " ORDER BY effective_date DESC LIMIT 1",
        (ctx.date,),
    ).fetchone()
    if row is None:
        return None
    effective = row["effective_date"]
    rev = ctx.conn.execute(
        "SELECT MAX(revision) AS rev FROM weight_sets WHERE effective_date = ?", (effective,)
    ).fetchone()
    entries = list(
        ctx.conn.execute(
            "SELECT * FROM weight_sets WHERE effective_date = ? AND revision = ?"
            " AND scope = 'model' ORDER BY weight DESC",
            (effective, rev["rev"]),
        )
    )
    if not entries:
        return None
    return effective, int(rev["rev"]), entries


def _weights_card(ctx: SiteContext) -> str:
    """The standing weight review — a data update on a fixed schedule, not a discretion."""
    found = _model_mix_entries(ctx)
    if found is None:
        return ""
    effective, revn, entries = found
    total = sum(e["weight"] for e in entries) or 1.0
    body = "".join(
        f'<li class="ledger__row"><span class="ledger__n num">{i:02d}</span>'
        f'<div class="ledger__body"><h4 class="ledger__t">{_e(e["key"])} class share</h4>'
        f'<p class="ledger__d">Share of total observed qualifying capacity over the review '
        f'window, floored at {ctx.factors.composite.min_class_share_pct:.0f}% and capped at '
        f'{ctx.factors.composite.max_class_share_pct:.0f}%.</p></div>'
        f'<span class="ledger__v num">{e["weight"] / total * 100:,.1f}%</span></li>'
        for i, e in enumerate(entries, start=1)
    )
    window = f'{entries[0]["window_start"]} to {entries[0]["window_end"]}'
    return f"""<div class="card">
  <div class="card__head">
    <div><h3 class="card__title">Composite basket, effective {_e(effective)}</h3>
    <p class="card__sub">Window {_e(window)} &#183; {entries[0]["n_days_window"]} collection
    days &#183; recomputed by a fixed published formula, stored append-only</p></div>
    <span class="card__meta num">rev {revn}</span>
  </div>
  <div class="card__body"><ol class="ledger">{body}</ol></div>
</div>"""


def _model_mix_bar(ctx: SiteContext) -> str:
    """A compact composition summary of the same basket `_weights_card` itemises in
    full below it — a stacked bar, not a replacement for the ledger's review-window
    and formula detail. Uses the categorical palette (tokens.css §6c): colour here
    identifies a GPU class across a genuinely multi-part whole, the documented case
    for reaching past the single-hue chart default."""
    found = _model_mix_entries(ctx)
    if found is None:
        return ""
    effective, _revn, entries = found
    total = sum(e["weight"] for e in entries) or 1.0
    shares = [(str(e["key"]), e["weight"] / total * 100) for e in entries]
    # The palette has 8 fixed slots and no ninth — DESIGN.md/tokens.css §6c is explicit
    # that a ninth series folds into "Other" rather than reusing a slot's colour.
    if len(shares) > 8:
        shares, rest = shares[:7], shares[7:]
        shares.append(("Other", sum(pct for _k, pct in rest)))
    segs = "".join(
        f'<span class="mixbar__seg" style="inline-size:{pct:.4f}%;'
        f'background:var(--series-{i + 1})"></span>'
        for i, (_key, pct) in enumerate(shares)
    )
    legend = "".join(
        f'<li><i style="background:var(--series-{i + 1})"></i>'
        f"<span>{_e(key)}</span><span class=\"num\">{pct:,.1f}%</span></li>"
        for i, (key, pct) in enumerate(shares)
    )
    label = ", ".join(f"{key} {pct:.1f}%" for key, pct in shares)
    return f"""<div class="card">
  <div class="card__head">
    <div><h3 class="card__title">Model mix</h3>
    <p class="card__sub">Composite weight by GPU class, effective {_e(effective)} &#183; same
    basket as the ledger below</p></div>
  </div>
  <div class="card__body">
    <div class="mixbar" role="img"
    aria-label="Composite weight by GPU class: {_e(label)}">{segs}</div>
    <ul class="mixlegend">{legend}</ul>
  </div>
</div>"""


# ==========================================================================
# pages
# ==========================================================================


def _dashboard(ctx: SiteContext, notes: list[Note]) -> str:
    title = f"{BRAND} — {BRAND_FULL}"
    description = (
        f"{BRAND}: daily, reproducible reference prices for renting AI compute. Weighted "
        "median over offers, published with its full constituent set and a gap wherever "
        "the panel is too thin to price."
    )
    if ctx.head is None:
        body = (
            '<main id="main"><div class="wrap">'
            '<section class="section"><h1 class="section__h">' + _e(BRAND_FULL) + "</h1>"
            '<div class="gapnote">' + _icon("warn", 14)
            + "<p>No print has been computed yet. Run the daily pipeline.</p></div>"
            "</section></div></main>"
        )
        return _shell(
            ctx, title=title, description=description, current="index.html", body=body,
            dataset=True,
        )

    # The pulsing dot means "there is a price here right now". On a session that gapped
    # there is not, and a live badge over a column of dashes is the same lie as a stale
    # value dressed as current — so the badge states which of the two it is.
    if ctx.head is not None and ctx.head["value_usd"] is not None:
        live = (
            '<div class="live-label"><span class="live-dot" aria-hidden="true"></span>'
            f'<span>Live &#183; updated daily {_e(CUTOFF_UTC)}</span></div>'
        )
    else:
        live = (
            '<div class="live-label">'
            f'<span>No headline print this session &#183; next {_e(CUTOFF_UTC)}</span>'
            "</div>"
        )
    body = f"""<main id="main">
  <div class="wrap">
    {_hero()}
  </div>
{_notice_banner(_load_notices())}

  <div class="wrap">
  <section class="section" id="indices" aria-labelledby="s-today">
    <div class="section__head"><div>
      <h2 class="section__h" id="s-today">Headline series &#8212; today</h2>
      <p class="section__dek">One row per class the index prices, for the
      {_e(_human_date(ctx.date))} session. A class below the provider gate keeps its row and
      states the gate it missed; the last good value is never promoted into today's slot.
      </p></div>
      <div class="section__link">{live}</div></div>
    {_series_table(ctx)}
  </section>

  <section class="section" aria-labelledby="print-h">
    {_print_card(ctx)}
  </section>

  <section class="section" aria-labelledby="s-sub">
    <div class="section__head"><div>
      <h2 class="section__h" id="s-sub">Sub-indices</h2>
      <p class="section__dek">The segment and variant cuts of the headline. A series below
      the provider gate keeps its slot and shows a gap with the reason.</p></div></div>
    {_tiles(ctx)}
  </section>

  <section class="section" aria-labelledby="s-chart">
    <div class="section__head"><div>
      <h2 class="section__h" id="s-chart">Headline, {WINDOW_DAYS} sessions</h2>
      <p class="section__dek">Truncated y-axis, so this is a line and never an area fill.
      Hover or tab through the plot for the crosshair readout; it is CSS-only and works with
      scripting disabled.</p></div></div>
    {_chart_card(ctx)}
  </section>

  <section class="section" aria-labelledby="s-cons" id="constituents">
    <div class="section__head"><div>
      <h2 class="section__h" id="s-cons">Constituents at the {_e(ctx.date)} print</h2>
      <p class="section__dek">Every candidate the index saw, including the ones it rejected
      and why. Tier is ordinal: L1 executable quote, L2 neocloud list, L3 hyperscaler
      catalog.</p></div>
      <a class="section__link" href="methodology.html#3-aggregation-exact-algorithm">
      How the median is taken</a></div>
    {_constituents_card(ctx)}
  </section>

  <section class="section" aria-labelledby="s-q">
    <div class="section__head"><div>
      <h2 class="section__h" id="s-q">Data quality and sources</h2>
      <p class="section__dek">The publication gate, the executable share, and the collectors
      behind them. A gap is credible; a fabricated print is fatal.</p></div></div>
    <div class="stack">
      {_quality_card(ctx)}
      {_model_mix_bar(ctx)}
      {_weights_card(ctx)}
      {_sources_card(ctx)}
    </div>
  </section>

  <section class="section" id="methodology" aria-labelledby="s-pillars">
    <div class="section__head"><div>
      <h2 class="section__h" id="s-pillars">How the number is made</h2>
      <p class="section__dek">Three commitments, each one checkable against the repository
      rather than taken on trust.</p></div>
      <a class="section__link" href="methodology.html">Full methodology</a></div>
    {_pillars(ctx)}
  </section>

  <section class="section" aria-labelledby="s-family">
    <div class="section__head"><div>
      <h2 class="section__h" id="s-family">A family of indices</h2>
      <p class="section__dek">One index is published today. The rest are stated as what they
      are — in development or planned — and carry no numbers until they do.</p></div></div>
    {_family(ctx)}
  </section>

  <section class="section" aria-labelledby="data">
    {_data_teaser(ctx)}
  </section>

  <section class="section" aria-labelledby="s-research">
    <div class="section__head"><div>
      <h2 class="section__h" id="s-research">Latest research</h2>
      <p class="section__dek">Notes on what the index measures and what it cannot.</p>
      </div><a class="section__link" href="research.html">All notes</a></div>
    {_research_strip(notes)}
  </section>
  </div>
</main>"""
    return _shell(
        ctx,
        title=f"{title} — {ctx.date}",
        description=description,
        current="index.html",
        dataset=True,
        body=body,
        extra_js=_REFRESH_JS + "\n" + _WAVE_JS,
    )


def _toc(headings: list[markdown.Heading], top: int = 3) -> str:
    """Two levels of contents, starting at `top`.

    Defaults to 3 because every embedded document is rendered with heading_offset=1 (the
    page already owns its single h1), so the document's own `##` sections land on h3.
    """
    sub = ' class="is-sub"'
    items = "".join(
        f"<li{sub if h.level > top else ''}>"
        f'<a href="#{h.slug}">{_e(h.text)}</a></li>'
        for h in headings
        if top <= h.level <= top + 1
    )
    if not items:
        return ""
    return (
        '<nav class="toc" aria-label="On this page">'
        '<h2 class="toc__h">On this page</h2>'
        f'<ol class="toc__list">{items}</ol></nav>'
    )


def _parameter_ledger(ctx: SiteContext) -> str:
    """The construction, as numbered ledger rows with right-aligned tabular values.

    Every value is read from config/factors.yaml at build time, so the page cannot drift
    from the parameters the calculation actually used.
    """
    f = ctx.factors
    agg, w = f.aggregation, f.weights
    trim = " · ".join(f"n&#8805;{r.min_n}&#8594;k={r.k}" for r in agg.trim_k if r.min_n)
    rows: list[tuple[str, str, str]] = [
        (
            "Reference unit",
            f"One {f.reference_unit.gpu_model.replace('_', ' ')} GPU-hour, "
            f"{f.reference_unit.term.replace('_', '-')}, delivered from the "
            f"{f.reference_unit.location.replace('_', '/')}, per GPU, ex-VAT, excluding "
            "storage and metered egress. A class prices its reference variant only.",
            f"{len(f.model_classes)} classes",
        ),
        (
            "Unit filters",
            f"Offers below {f.filters.min_gpu_count} GPUs are excluded rather than "
            "normalised: the per-GPU discount saturates at 2 GPUs, while a 1-GPU offer "
            "carries a small-order premium. Sanity band "
            f"${f.filters.price_floor_usd:,.2f}&#8211;${f.filters.price_ceiling_usd:,.2f}. "
            f"Excluded tiers: {', '.join(f.filters.exclude_tiers)}.",
            f"&#8805;{f.filters.min_gpu_count} GPU",
        ),
        (
            "Market segment",
            "The constituent distribution is bimodal, so series never average across the "
            "neocloud/hyperscaler gap. The headline draws from "
            f"{', '.join(sorted(f.population_for('headline')))}.",
            f"{len(set(f.segments.values()))} segments",
        ),
        (
            "Staleness",
            f"Manually verified static entries warn at {f.staleness.warn_days} days and are "
            f"excluded at {f.staleness.exclude_days}.",
            f"{f.staleness.exclude_days} d",
        ),
        (
            "Provider weight",
            "A provider with any executable offer is weighted "
            f"{w.executable_multiplier:,.0f}&#215; a list-only provider. Capacity does not "
            "enter at provider level: it is unobservable for every list source.",
            f"&#215;{w.executable_multiplier:,.1f}",
        ),
        (
            "Concentration cap",
            "No provider may exceed this share of total weight; the excess is redistributed "
            "pro-rata to a fixed point. Mathematically inert at n=4; binds from n=5. Every "
            "print publishes whether it bound.",
            f"{w.max_weight_share_pct:,.0f}%",
        ),
        (
            "Offer spread",
            "Each provider's share is spread across its own offers in proportion to observed "
            f"capacity, capped at {w.capacity_cap} GPUs, defaulting to "
            f"{w.default_capacity} where capacity is unobservable.",
            f"cap {w.capacity_cap}",
        ),
        (
            "Trim",
            "Count-based: clamp the k highest and k lowest offer prices to the k-th order "
            "statistic. Percentile winsorising is inert at this panel size — at n=6 both "
            "p5/p95 and p10/p90 resolve to (min, max) and clamp nothing.",
            trim,
        ),
        (
            "Estimator",
            f"{agg.estimator.replace('_', ' ').capitalize()} over {agg.unit}s: the first "
            "price at which cumulative weight reaches 50%. It always lands on a price "
            "someone actually quoted.",
            f"over {agg.unit}s",
        ),
        (
            "Publication gate",
            f"Fewer than {agg.min_providers} qualifying providers or fewer than "
            f"{agg.min_offers} qualifying offers publishes no value, flagged "
            "<code class=\"inline\">insufficient_sources</code> / "
            "<code class=\"inline\">insufficient_offers</code>. There is no fallback "
            "waterfall.",
            f"&#8805;{agg.min_providers} / &#8805;{agg.min_offers}",
        ),
        (
            "EUR companion",
            "USD value divided by the most recent ECB reference rate dated on or before the "
            f"print date, refused beyond {f.fx.max_age_days} days old. The ECB publishes "
            "after the cut-off, so the EUR leg is T&#8722;1 by construction.",
            f"&#8804;{f.fx.max_age_days} d",
        ),
        (
            "Smoothing companion",
            f"The headline's {agg.smoothing_days}-day mean, requiring at least 4 non-null "
            "days. Published beside the headline, never as it.",
            f"{agg.smoothing_days} d",
        ),
        (
            "Composite",
            f"Chain-linked over class sub-indices from a base of {f.composite.base_value:,.0f}, "
            f"class shares floored at {f.composite.min_class_share_pct:,.0f}% and capped at "
            f"{f.composite.max_class_share_pct:,.0f}%. A class that gaps drops out of that "
            "day's link.",
            f"base {f.composite.base_value:,.0f}",
        ),
        (
            "Quality flag",
            f"A constituent moving more than {f.jump_flag_pct:,.0f}% day-over-day is flagged "
            "for review and is never silently excluded.",
            f"{f.jump_flag_pct:,.0f}%",
        ),
        (
            "Revisions",
            "Observations and prints are append-only, enforced by database triggers. An error "
            "is corrected as a new revision flagged <code class=\"inline\">correction</code>; "
            "prior revisions stay queryable forever.",
            "append-only",
        ),
    ]
    body = "".join(
        f'<li class="ledger__row"><span class="ledger__n num">{i:02d}</span>'
        f'<div class="ledger__body"><h4 class="ledger__t">{title}</h4>'
        f'<p class="ledger__d">{desc}</p></div>'
        f'<span class="ledger__v num">{value}</span></li>'
        for i, (title, desc, value) in enumerate(rows, start=1)
    )
    return (
        '<ol class="ledger">'
        '<li class="ledger__row ledger__row--head"><span>#</span><span>Step</span>'
        '<span class="ledger__v">Parameter</span></li>' + body + "</ol>"
    )


def _reference_definition(ctx: SiteContext) -> str:
    """The unit, as a key/value table read from config — never retyped prose."""
    ru = ctx.factors.reference_unit
    f = ctx.factors
    rows = (
        ("GPU model", ru.gpu_model.replace("_", " ")),
        ("Term", ru.term.replace("_", "-").capitalize() + ", no commitment"),
        ("Location", ru.location.replace("_", "/") + " data centre"),
        ("Node size", f"&#8805;{f.filters.min_gpu_count} GPUs; sub-node offers excluded"),
        ("Unit", "Per-GPU-hour, ex-VAT, excluding storage and metered egress"),
        ("Currency", "USD primary, EUR companion at the ECB reference rate (T&#8722;1)"),
    )
    body = "".join(f"<tr><td>{_e(k)}</td><td>{v}</td></tr>" for k, v in rows)
    return f'<table class="kv"><tbody>{body}</tbody></table>'


def _class_table(ctx: SiteContext) -> str:
    rows = "".join(
        f'<tr><td>{_e(name)}</td>'
        f'<td class="u">{_e(cls.reference_variant.replace("_", " "))}</td></tr>'
        for name, cls in ctx.factors.model_classes.items()
    )
    return (
        '<table class="kv"><thead><tr><td>Class</td><td>Reference variant</td></tr></thead>'
        f"<tbody>{rows}</tbody></table>"
    )


def _segment_cards(ctx: SiteContext) -> str:
    """Market segments and their members, straight from `factors.segments`.

    The mockup lists the providers as copy. Reading them from config instead means a
    provider added or delisted tomorrow cannot leave this card telling a stale story.
    """
    by_segment: dict[str, list[str]] = {}
    for provider, segment in sorted(ctx.factors.segments.items()):
        by_segment.setdefault(segment, []).append(provider)
    note = {
        "marketplace": "Executable quotes with a demonstrated node size.",
        "neocloud": "Published list prices from specialist operators.",
        "hyperscaler": "Catalog list prices. Published as their own series.",
    }
    cards = "".join(
        f'<article class="segcard"><h4 class="segcard__t">{_e(seg.capitalize())}</h4>'
        f'<p class="segcard__l">{_e(", ".join(members))}</p>'
        + (f'<p class="segcard__l">{_e(note[seg])}</p>' if seg in note else "")
        + "</article>"
        for seg, members in sorted(by_segment.items())
    )
    return f'<div class="segcards">{cards}</div>'


def _aggregation_term(ctx: SiteContext) -> str:
    """The aggregation rules as a terminal block — the config file, not a paraphrase."""
    agg = ctx.factors.aggregation
    trim = ", ".join(f"k={r.k} at n&#8805;{r.min_n}" for r in agg.trim_k if r.min_n)
    return f"""<div class="term">
  <div class="term__chrome" aria-hidden="true"><i></i><i></i><i></i>
    <span class="term__name">config/factors.yaml</span></div>
  <div class="term__body">
    <div>unit: <span class="term__hi">{_e(agg.unit)}</span>  <span class="term__c">
    # weighted over offers, not one price per provider</span></div>
    <div>estimator: {_e(agg.estimator)}</div>
    <div>min_providers: {agg.min_providers}  <span class="term__c">
    # below this: value=null, flag=insufficient_sources</span></div>
    <div>min_offers: {agg.min_offers}</div>
    <div>trim: count-based ({trim})</div>
    <div>smoothing_days: {agg.smoothing_days}  <span class="term__c">
    # headline companion series only</span></div>
  </div>
</div>"""


def _methodology(ctx: SiteContext) -> str:
    doc = markdown.render(_rebrand_doc(_read(REPO_ROOT / "METHODOLOGY.md")), heading_offset=1)
    lock = ctx.lock_hash
    ru = ctx.factors.reference_unit
    head_pop = ", ".join(sorted(ctx.factors.population_for("headline")))
    body = f"""<main class="wrap" id="main">
  <div class="pagehead">
    <div class="vbadges">
      <span class="vbadge">v{_e(ctx.version)}</span>
      <span class="vbadge">iosco-aligned</span>
      <span class="vbadge">reproducible</span>
    </div>
    <h1 class="pagehead__h pagehead__h--display">The reference price, defined
    precisely.</h1>
    <p class="pagehead__dek"><strong>{_nbsp_series(HEADLINE)}</strong> is the headline
    series: one NVIDIA {_e(ru.gpu_model.replace("_", " "))} GPU-hour, on-demand, per GPU,
    ex-VAT, from an {_e(ru.location.replace("_", "/"))} data centre. Companion series cover
    market segments, additional GPU generations, and a chain-linked composite that follows
    the market across hardware cycles. Everything below is read from
    <code class="inline">config/factors.yaml</code> at build time, so no table on this page
    can drift from the calculation. Series identifiers quoted as code below are the
    <a href="data.html#s-ids">stored keys</a>, which the published files still use.</p>
    <div class="pagehead__meta">
      <span>Version <span class="num">v{_e(ctx.version)}</span></span>
      <span id="lock">Lock <span class="num">sha256:{_e(lock[:16])}&#8230;</span></span>
      <span>Print date <span class="num">{_e(ctx.date)}</span></span>
      <span><a href="governance.html">Change procedure</a></span>
    </div>
  </div>

  <section class="section" aria-labelledby="s-refdef">
    <div class="section__head"><div>
      <h2 class="section__h" id="s-refdef">Reference definition</h2></div></div>
    {_reference_definition(ctx)}
  </section>

  <section class="section" aria-labelledby="s-classes">
    <div class="section__head"><div>
      <h2 class="section__h" id="s-classes">Compute classes</h2>
      <p class="section__dek">A class prices its reference variant only. No assumed
      cross-variant normalisation factor enters the calculation path — a variant re-enters a
      class only once a factor is measured from same-venue, same-day, same-SKU pairs.</p>
      </div></div>
    {_class_table(ctx)}
  </section>

  <section class="section" aria-labelledby="s-segments">
    <div class="section__head"><div>
      <h2 class="section__h" id="s-segments">Market segments</h2>
      <p class="section__dek">The headline draws from {_e(head_pop)} only. The constituent
      distribution is bimodal — the hyperscaler catalog sits 5.4 standard deviations away —
      so it is published as its own series rather than averaged in.</p></div></div>
    {_segment_cards(ctx)}
  </section>

  <section class="section" aria-labelledby="s-agg">
    <div class="section__head"><div>
      <h2 class="section__h" id="s-agg">How the print is computed</h2>
      <p class="section__dek">Every offer is weighted by GPU count rather than counted once
      per provider: a median over six providers is a step function, a median over many
      weighted offers is locally smooth. Below the provider floor the value is published as
      a gap, never fabricated.</p></div></div>
    {_aggregation_term(ctx)}
  </section>

  <div class="doc">
    {_toc(doc.headings)}
    <div>
      <section aria-labelledby="ledger-h" style="margin-bottom:var(--space-8)">
        <div class="card">
          <div class="card__head">
            <div><h2 class="card__title" id="ledger-h">{SERIES_PREFIX_PUBLISHED}&#8209;M
            &#183; construction</h2>
            <p class="card__sub">Hash-locked. A change to any row requires a version bump, a
            CHANGELOG entry, and one publication's notice.</p></div>
            <span class="card__meta num">v{_e(ctx.version)} &#183;
            sha256:{_e(lock[:8])}&#8230;</span>
          </div>
          <div class="card__body">{_parameter_ledger(ctx)}</div>
        </div>
      </section>
      <section aria-labelledby="audit-h" style="margin-bottom:var(--space-8)">
        <div class="card">
          <div class="card__head"><div>
            <h2 class="card__title" id="audit-h">Audit and governance hooks</h2>
            <p class="card__sub">How a third party checks this print without asking
            us</p></div></div>
          <div class="card__body stack">
            <pre class="code"><code># the full constituent set behind any print
python -m tci.run constituents --date {_e(ctx.date)} --series EU-CRI-H100

# the weight review in effect on a date, recomputed from stored observations
python -m tci.run weights --date {_e(ctx.date)}

# regenerate this document and the lock from config
python -m tci.run docs</code></pre>
            <ol class="ledger">
              <li class="ledger__row"><span class="ledger__n num">A1</span>
                <div class="ledger__body"><h4 class="ledger__t">Methodology lock</h4>
                <p class="ledger__d">A sha256 over factors.yaml, sovereign.yaml, index.py,
                normalise.py and weights.py. CI fails whenever the working tree stops matching
                it.<span class="ledger__k">sha256:{_e(lock)}</span></p></div>
                <span class="ledger__v num">v{_e(ctx.version)}</span></li>
              <li class="ledger__row"><span class="ledger__n num">A2</span>
                <div class="ledger__body"><h4 class="ledger__t">Machine-readable prints</h4>
                <p class="ledger__d">The full published history, latest revision per date and
                series, plus today's snapshot with its constituent set.</p></div>
                <span class="ledger__v num"><a href="data/index_history.csv">CSV</a> &#183;
                <a href="data/latest.json">JSON</a></span></li>
              <li class="ledger__row"><span class="ledger__n num">A3</span>
                <div class="ledger__body"><h4 class="ledger__t">Complaints</h4>
                <p class="ledger__d">Any print may be challenged. Acknowledged within 7 days;
                the outcome is published with the next print, whether it is a correction or a
                rationale for no change.</p></div>
                <span class="ledger__v num"><a href="governance.html#6-complaints">P13</a>
                </span></li>
            </ol>
          </div>
        </div>
      </section>
      <div class="md">{doc.html}</div>
    </div>
  </div>
</main>"""
    return _shell(
        ctx,
        title=f"Methodology v{ctx.version} — {BRAND}",
        description=(
            f"The exact {SERIES_PREFIX_PUBLISHED} construction: unit definition, market "
            "segmentation, weighting, trim, weighted median over offers, and the "
            "publication gate."
        ),
        current="methodology.html",
        body=body,
    )


_PRECOND_HEAD = re.compile(r"^#+\s*Settlement-grade preconditions", re.M)
_PRECOND_ITEM = re.compile(r"^(\d+)\.\s+(.+?)(?=\n\d+\.\s|\n\n)", re.M | re.S)
_PRECOND_STATUS = re.compile(r"none of ([\d,\s]+?(?:or\s*\d+)?) is met", re.I)


def _preconditions(ctx: SiteContext) -> str:
    """The settlement-grade checklist, PARSED out of GOVERNANCE.md — never retyped.

    The mockup hardcodes the seven conditions. Duplicating a governance list in the
    generator is precisely the drift this project exists to avoid: the document is the
    authority, and a summary that quietly disagrees with it is worse than no summary. So
    the list and the met/unmet line are read from the document at build time, and if its
    shape ever changes the component simply does not render — the full text is on the
    same page either way.
    """
    text = _read(REPO_ROOT / "GOVERNANCE.md")
    start = _PRECOND_HEAD.search(text)
    if start is None:
        return ""
    block = text[start.end():]
    end = block.find("\n## ")          # stop at the next top-level section
    items = _PRECOND_ITEM.findall(block if end < 0 else block[:end])
    if not items:
        return ""
    # The document names the conditions it considers UNMET. It does not anywhere assert
    # that a condition is met, so neither does this table: anything the document is
    # silent about renders as "not stated", never as met by inference. Reading a
    # governance page's silence as a positive claim is how a checklist starts lying.
    unmet_match = _PRECOND_STATUS.search(block)
    unmet = (
        {int(n) for n in re.findall(r"\d+", unmet_match.group(1))}
        if unmet_match
        else set(range(1, len(items) + 1))
    )
    rows = []
    for num, raw in items:
        pill = (
            '<span class="pill">not yet met</span>' if int(num) in unmet
            else '<span class="pill">not stated</span>'
        )
        rows.append(
            f'<tr><td>{_e(" ".join(raw.split())).rstrip(".")}</td><td>{pill}</td></tr>'
        )
    return f"""<table class="preconds">
  <caption class="vh">Settlement-grade preconditions as of methodology
  v{_e(ctx.version)}</caption>
  <tbody>{"".join(rows)}</tbody>
</table>"""


def _policy_grid() -> str:
    """Navigational summaries of the policies set out in full further down the page."""
    cards = (
        ("Methodology changes (IOSCO P12)",
         "Version bump, CHANGELOG entry, regenerated lock, and one publication's notice "
         "before the change affects a print. Every print records the version it was "
         "computed under."),
        ("Corrections (IOSCO P13)",
         "Prints are never edited or deleted — database triggers forbid it. An erroneous "
         "print is superseded by a new revision flagged “correction”, no later "
         "than the next publication."),
        ("Audit trail (IOSCO P16)",
         "Every print stores its full constituent set — every candidate, price, weight and "
         "exclusion reason. Reproducible with a single CLI command against any print date."),
        ("Conflicts of interest (IOSCO P4–P5)",
         "The calculation path contains no expert judgement: every parameter is a published "
         "config value. Any author position on an observed venue is disclosed where "
         "relevant."),
        ("Data sufficiency (IOSCO P6–P7)",
         "Below the minimum provider count the print is null and flagged "
         "“insufficient_sources”. A gap is published — never fabricated."),
        ("Review and cessation",
         "Reviewed annually or on structural market change. If the index can no longer be "
         "produced credibly, cessation is announced with 30 days' notice; the history "
         "stays public."),
    )
    return '<div class="policygrid">' + "".join(
        f'<article class="policy"><h3 class="policy__t">{title}</h3>'
        f'<p class="policy__d">{_e(body)}</p></article>'
        for title, body in cards
    ) + "</div>"


@dataclass(frozen=True)
class Notice:
    """One announced change to the methodology, as published under GOVERNANCE.md §1."""

    id: str
    title: str
    announced: str
    effective: str
    version: str
    status: str
    summary: str
    effect_on_level: str
    body: str

    @property
    def pending(self) -> bool:
        """Announced, effective date not yet reached. These raise the dashboard banner."""
        return self.status == "announced"


def _load_notices() -> list[Notice]:
    """The notice register, newest first.

    GOVERNANCE.md §1 step 5 requires one publication's notice before the first print
    under a new methodology version. That requirement had no public surface until this
    existed: a change could satisfy every other step and still leave a reader to find out
    from a changelog afterwards.

    The register is not hash-locked and never enters the calculation path. It states what
    a change is intended to do; METHODOLOGY.lock is what proves what the code does.
    """
    path = REPO_ROOT / "config" / "notices.yaml"
    if not path.exists():
        return []
    raw = yaml.safe_load(_read(path)) or {}
    out = [
        Notice(
            id=str(n.get("id", "")),
            title=str(n.get("title", "")),
            announced=str(n.get("announced", "")),
            effective=str(n.get("effective", "")),
            version=str(n.get("version", "")),
            status=str(n.get("status", "announced")),
            summary=str(n.get("summary", "")).strip(),
            effect_on_level=str(n.get("effect_on_level", "")).strip(),
            body=str(n.get("body", "")).strip(),
        )
        for n in (raw.get("notices") or [])
    ]
    return sorted(out, key=lambda n: (n.announced, n.id), reverse=True)


_NOTICE_STATUS = {
    "announced": ("chip--warning", "Takes effect"),
    "in_effect": ("chip--good", "In effect since"),
    "withdrawn": ("chip--neutral", "Withdrawn, was to take effect"),
}


def _notice_banner(notices: list[Notice], prefix: str = "") -> str:
    """A standing banner on the dashboard while any change is announced but not yet live.

    Notice is worth nothing if it is only reachable from a page nobody visits, so it sits
    above the print it is going to change, on the page everyone lands on.
    """
    pending = [n for n in notices if n.pending]
    if not pending:
        return ""
    items = "".join(
        f"<p><strong>{_e(n.title)}</strong> Takes effect {_e(_human_date(n.effective))}"
        f" with methodology v{_e(n.version)}. {_e(n.summary)}</p>"
        for n in pending
    )
    plural = "changes" if len(pending) > 1 else "change"
    return f"""<div class="wrap"><div class="callout--brand notice-banner" role="region"
  aria-labelledby="notice-h">
  <span class="eyebrow" id="notice-h">Announced {plural} to the methodology</span>
  {items}
  <p class="notice-banner__more"><a href="{prefix}notices.html">Read the full notice</a></p>
</div></div>"""


def _notices(ctx: SiteContext) -> str:
    notices = _load_notices()
    if not notices:
        cards = (
            '<div class="slot"><h3 class="slot__h">No notices</h3>'
            "<p>No change to the methodology is currently announced. Every change that "
            "would alter a published print appears here before the first print computed "
            "under it.</p></div>"
        )
    else:
        blocks = []
        for n in notices:
            cls, verb = _NOTICE_STATUS.get(n.status, _NOTICE_STATUS["announced"])
            blocks.append(
                f"""<article class="card" id="{_e(n.id)}">
  <div class="card__head"><div>
    <h2 class="card__title">{_e(n.title)}</h2>
    <p class="card__sub">Notice {_e(n.id)} &#183; announced
      {_e(_human_date(n.announced))} &#183; methodology v{_e(n.version)}</p>
  </div>
  <span class="card__meta"><span class="chip {cls}">
    <span>{verb} {_e(_human_date(n.effective))}</span></span></span>
  </div>
  <div class="card__body">
    <p class="dek">{_e(n.summary)}</p>
    <div class="md">{markdown.render(_rebrand_doc(n.body)).html}</div>
    <div class="fnstrip" style="margin-top:var(--space-6)">
      <h4 class="fnstrip__h">Expected effect on the level</h4>
      <p style="margin:0;font-size:var(--text-xs);color:var(--ink-2);
        line-height:var(--leading-prose)">{_e(n.effect_on_level)}</p>
    </div>
  </div>
</article>"""
            )
        cards = '<div class="stack">' + "".join(blocks) + "</div>"

    body = f"""<main id="main"><div class="wrap">
  <section class="pagehead">
    <div class="eyebrow">Governance</div>
    <h1 class="pagehead__h">Methodology notices</h1>
    <p class="pagehead__dek">Every change that would alter a published print is announced
    here before the first print computed under it, as required by
    <a href="governance.html">GOVERNANCE.md &#167;1</a>. Prints keep the methodology
    version they were computed under, so a transition is auditable after the fact rather
    than only announced before it.</p>
  </section>
  <section class="section">{cards}</section>
  <section class="section">
    <div class="section__head"><div>
      <h2 class="section__h">What a notice does not cover</h2>
      <p class="section__dek">Scheduled weight reviews run a fixed published formula on a
      fixed schedule with no discretion. They are data updates, not methodology changes,
      and are not announced here. Corrections to an individual print are handled under the
      correction policy and published as a new revision, never as an edit.</p>
    </div></div>
  </section>
</div></main>"""
    return _shell(
        ctx,
        title=f"Methodology notices — {BRAND}",
        description=(
            "Changes to the TCI methodology, announced before the first print computed "
            "under them."
        ),
        current="governance.html",
        canonical="notices.html",
        body=body,
    )


def _governance(ctx: SiteContext) -> str:
    doc = markdown.render(_rebrand_doc(_read(REPO_ROOT / "GOVERNANCE.md")), heading_offset=1)
    preconds = _preconditions(ctx)
    preconds_section = (
        f"""<section class="section" aria-labelledby="s-pre">
    <div class="section__head"><div>
      <h2 class="section__h" id="s-pre">Settlement-grade preconditions</h2>
      <p class="section__dek">{_e(BRAND)} is a price-transparency benchmark, not a
      settlement benchmark, and will not be represented as one until all of these hold.
      Published so the claim can be checked rather than trusted.</p></div></div>
    {preconds}
  </section>"""
        if preconds
        else ""
    )
    body = f"""<main class="wrap" id="main">
  <div class="pagehead">
    <div class="eyebrow">IOSCO principles, voluntary</div>
    <h1 class="pagehead__h pagehead__h--display">Governance</h1>
    <p class="pagehead__dek">Administrator and author: <strong>Mark Rusch</strong>,
    Amsterdam. Who administers the index, how a change to it is made, and what happens when
    a print is wrong. {_e(BRAND)} is a research publication: it is not licensed for use in
    financial instruments, and any request to hard-wire it into a financial contract will be
    refused.</p>
    <div class="pagehead__meta">
      <span>Administrator Mark Rusch</span>
      <span>Methodology <span class="num">v{_e(ctx.version)}</span></span>
      <span>Lock <span class="num">sha256:{_e(ctx.lock_hash[:16])}&#8230;</span></span>
    </div>
  </div>

  <section class="section" aria-labelledby="s-scope">
    <div class="callout--brand">
      <span class="eyebrow" id="s-scope">Regulatory scope</span>
      <p>Regulation (EU) 2025/914 narrows the EU Benchmarks Regulation to critical,
      significant and climate-transition benchmarks, and {_e(BRAND)} falls outside Titles
      II&#8211;VI as a non-significant benchmark. Whether prices scraped from public rate
      cards constitute &#8220;contributed input data&#8221; under the new Article 2(1c) is
      an open question on which the administrator expresses no view; legal advice will be
      obtained before any contractual use. The precise position is set out below.</p>
    </div>
  </section>

  {preconds_section}

  <section class="section" aria-labelledby="s-policy">
    <div class="section__head"><div>
      <h2 class="section__h" id="s-policy">Policy summary</h2>
      <p class="section__dek">The short form. Each one is set out in full in the document
      below, which is the authority wherever the two differ.</p></div></div>
    {_policy_grid()}
  </section>

  <section class="section" aria-labelledby="s-complaints">
    <div class="section__head"><div>
      <h2 class="section__h" id="s-complaints">Complaints</h2></div></div>
    <p class="dek">Complaints or challenges to any print:
    <a href="mailto:{_e(CONTACT_EMAIL)}">{_e(CONTACT_EMAIL)}</a>. Acknowledged within 7
    days; the outcome — a correction or a rationale for no change — is published with the
    next print.</p>
  </section>

  <div class="doc">
    {_toc(doc.headings)}
    <div class="md">{doc.html}</div>
  </div>
</main>"""
    return _shell(
        ctx,
        title=f"Governance — {BRAND}",
        description=(
            f"{BRAND} governance: methodology change procedure, correction policy, audit "
            "trail, conflicts of interest, complaints, and cessation."
        ),
        current="governance.html",
        body=body,
    )


# ---- data -----------------------------------------------------------------


def _data(ctx: SiteContext) -> str:
    """Downloads, the terms in short, and how to cite a value.

    The one thing this page must be straight about: the site publishes TCI names while the
    files still carry the stored EU-CRI keys, because renaming a published identifier is a
    governed event and not a styling decision. Saying so here costs a paragraph; letting a
    reader discover it inside a CSV costs the citation.
    """
    head = ctx.head
    stamp = (
        f'{_e(display_series(HEADLINE))}, {_e(ctx.date)}: '
        + (
            f"${_num(head['value_usd'])}/GPU-hr"
            if head is not None and head["value_usd"] is not None
            else "no print (gap)"
        )
        + f" (v{_e(ctx.version)}, lock sha256:{_e(ctx.lock_hash[:12])}&#8230;)"
    )
    cards = (
        ("Index history", "Full daily history of every published series, latest revision "
         "per date.", "data/index_history.csv", "Download CSV"),
        ("Latest print", "latest.json — current values across all series, with flags and "
         "the FX leg.", "data/latest.json", "View JSON"),
        ("Constituent audit", "Every candidate provider per print, included or not, its "
         "weight, and why. Published inside latest.json.", "data/latest.json",
         "View audit set"),
        ("Source code", "The generator, the collectors and the calculation — Apache-2.0, "
         "so any print here can be rebuilt independently.", REPO_URL, "Open repository"),
    )
    dl = "".join(
        f'<article class="dl"><h3 class="dl__t">{_e(t)}</h3><p class="dl__d">{_e(d)}</p>'
        f'<a class="btn btn--ghost btn--sm" href="{_e(href)}">{_e(action)}</a></article>'
        for t, d, href, action in cards
    )
    body = f"""<main class="wrap" id="main">
  <div class="pagehead">
    <h1 class="pagehead__h pagehead__h--display">Full history. Every observation. No black
    box.</h1>
    <p class="pagehead__dek">Raw observations, daily prints, and the full constituent audit
    set behind each one — downloadable, versioned since day one, and reproducible from
    public sources using the published code.</p>
    <div class="pagehead__meta">
      <span>As of <span class="num">{_e(ctx.date)}</span></span>
      <span>Methodology <span class="num">v{_e(ctx.version)}</span></span>
      <span>Updated daily <span class="num">{_e(CUTOFF_UTC)}</span></span>
    </div>
  </div>

  <section class="section" aria-labelledby="s-dl">
    <h2 class="vh" id="s-dl">Downloads</h2>
    <div class="dlcards">{dl}</div>
  </section>

  <section class="section" aria-labelledby="s-ids">
    <div class="section__head"><div>
      <h2 class="section__h" id="s-ids">Series identifiers</h2>
      <p class="section__dek">The site publishes the
      <code class="inline">{SERIES_PREFIX_PUBLISHED}&#8209;*</code> names. The data files
      still carry the original <code class="inline">{SERIES_PREFIX_STORED}&#8209;*</code>
      keys, and will until the rename is executed as a governed change with a published
      old&#8594;new mapping and an effective date. Until then, read
      <code class="inline">{HEADLINE}</code> in the files as
      <code class="inline">{_e(display_series(HEADLINE))}</code> on this site: same series,
      same history, same values.</p></div></div>
    <div class="term">
      <div class="term__chrome" aria-hidden="true"><i></i><i></i><i></i>
        <span class="term__name">latest.json</span></div>
      <div class="term__body">
        <div class="term__c"># the headline print, and the constituents behind it</div>
        <div>curl -s ./data/latest.json | jq '.series["{HEADLINE}"]'</div>
        <div>curl -s ./data/latest.json | jq '.constituents["{HEADLINE}"]'</div>
      </div>
    </div>
  </section>

  <section class="section" aria-labelledby="s-terms">
    <div class="section__head"><div>
      <h2 class="section__h" id="s-terms">Terms of use, in short</h2>
      <p class="section__dek">The full text is in
      <code class="inline">DATA-TERMS.md</code>; this is a summary, and the document
      governs.</p></div></div>
    <table class="terms">
      <tbody>
        <tr><th scope="row">Non-commercial</th><td>Free, no permission needed — research,
        teaching, journalism, verification, critique, reproducibility packages. Attribution
        required. Verification is never restricted: if you believe a print is wrong, you may
        publish everything needed to demonstrate it.</td></tr>
        <tr><th scope="row">Commercial</th><td>Paid products, terminals, resale, or
        financial-instrument use require permission — contact
        <a href="mailto:{_e(CONTACT_EMAIL)}">{_e(CONTACT_EMAIL)}</a>. Terms are being
        finalised; enquiries are welcome now.</td></tr>
        <tr><th scope="row">Never permitted</th><td>Use as a reference price in a financial
        instrument or contract. That is a governance restriction, not a commercial one, and
        it is not for sale at any price.</td></tr>
        <tr><th scope="row">Software &amp; docs</th><td>The code is Apache&#160;2.0 and the
        methodology is CC&#160;BY&#160;4.0, so every print here can be recomputed and
        checked independently.</td></tr>
      </tbody>
    </table>
  </section>

  <section class="section" aria-labelledby="s-cite">
    <div class="section__head"><div>
      <h2 class="section__h" id="s-cite">How to cite a value</h2>
      <p class="section__dek">Cite the print date, the methodology version and the lock
      hash, so the claim is checkable rather than merely attributed.</p></div></div>
    <div class="cite">Source: {_e(BRAND)} ({_e(BRAND_FULL)}), Mark Rusch &#183;
    {stamp}</div>
  </section>
</main>"""
    return _shell(
        ctx,
        title=f"Data & downloads — {BRAND}",
        description=(
            f"{BRAND} data: full CSV history, latest.json, the constituent audit set behind "
            "every print, terms of use, and the citation format."
        ),
        current="data.html",
        body=body,
    )


# ---- research -------------------------------------------------------------


@dataclass(frozen=True)
class Note:
    """One research note discovered on disk (or a planned one, with source None)."""

    slug: str
    title: str
    dek: str
    date: str
    status: str
    source: Path | None


PLANNED_NOTES: tuple[Note, ...] = (
    Note(
        slug="composition-vs-price",
        title="Composition, not price: what actually moves a European compute benchmark",
        dek=(
            "The headline moved 1.2% between its last two published prints. Almost none of "
            "that was a price change — it was a change in who was in the panel. This note "
            "decomposes the print into a price effect and a composition effect, and argues "
            "that for a panel this thin the composition effect is the story."
        ),
        date="",
        status="In preparation",
        source=None,
    ),
)


def _front_matter(text: str) -> tuple[dict, str]:
    """Optional `---` YAML block at the top of a note. PyYAML is already a dependency."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("\n---", 2)
    if len(parts) < 2:
        return {}, text
    meta = yaml.safe_load(parts[0].lstrip("-\n")) or {}
    return (meta if isinstance(meta, dict) else {}), parts[1].lstrip("\n")


_MONTHS = (
    "january february march april may june july august september october november december"
).split()
_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_LONG_DATE_RE = re.compile(r"\b(\d{1,2})\s+([A-Z][a-z]+)\s+(\d{4})\b")


def _sniff_date(text: str) -> str:
    """A note's publication date from its own byline — so authors need no front matter."""
    head = "\n".join(text.split("\n")[:12])
    iso = _ISO_RE.search(head)
    if iso:
        return iso.group(0)
    long_form = _LONG_DATE_RE.search(head)
    if long_form and long_form.group(2).lower() in _MONTHS:
        day, month, year = long_form.groups()
        return f"{year}-{_MONTHS.index(month.lower()) + 1:02d}-{int(day):02d}"
    return ""


def _discover_notes() -> list[Note]:
    found: list[Note] = []
    if RESEARCH_SRC.exists():
        for path in sorted(RESEARCH_SRC.glob("*.md")):
            meta, body = _front_matter(_read(path))
            doc = markdown.render(_rebrand_doc(body))
            found.append(
                Note(
                    slug=str(meta.get("slug") or path.stem),
                    title=str(meta.get("title") or doc.title or path.stem),
                    dek=str(meta.get("dek") or doc.lead)[:400],
                    date=str(meta.get("date") or _sniff_date(body)),
                    status=str(meta.get("status") or "Published"),
                    source=path,
                )
            )
    have = {n.slug for n in found}
    found += [n for n in PLANNED_NOTES if n.slug not in have]
    return sorted(found, key=lambda n: (n.date or "0000", n.slug), reverse=True)


def _research_index(ctx: SiteContext, notes: list[Note]) -> str:
    rows = []
    for n in notes:
        chip = (
            f'<span class="chip chip--neutral"><span>{_e(n.status)}</span></span>'
            if n.source is None
            else f'<span class="chip chip--good">{_icon("check")}'
            f"<span>{_e(n.status)}</span></span>"
        )
        when = _human_date(n.date) if _ISO_RE.fullmatch(n.date) else (n.date or "unscheduled")
        rows.append(
            f'<article class="note"><div class="note__when num">'
            f"{_e(when)}</div><div>"
            f'<h3 class="note__t"><a href="research/{_e(n.slug)}.html">{_e(n.title)}</a></h3>'
            f'<p class="note__dek">{_e(n.dek)}</p>'
            f'<div class="note__foot">{chip}'
            f'<span class="u">{_e(BRAND)} Research &#183; methodology '
            f'v{_e(ctx.version)}</span>'
            f"</div></div></article>"
        )
    body = f"""<main class="wrap" id="main">
  <div class="pagehead">
    <div class="eyebrow">{_e(BRAND)} Research</div>
    <h1 class="pagehead__h pagehead__h--display">Research</h1>
    <p class="pagehead__dek">The newsletter behind the index: notes on what it measures and
    what it cannot. Each one is reproducible from the published history in
    <code class="inline">site/data/index_history.csv</code> and the stored constituent sets;
    where a note makes a numeric claim, the query that produced it is printed with it.</p>
    <div class="pagehead__meta">
      <span><span class="num">{len(notes)}</span> notes</span>
      <span>Methodology <span class="num">v{_e(ctx.version)}</span></span>
      <span>As of <span class="num">{_e(ctx.date)}</span></span>
    </div>
  </div>
  <div class="section">
    <div class="notes">{"".join(rows)}</div>
  </div>
  <section class="section" aria-labelledby="s-sub">
    <div class="subscribe">
      <div class="subscribe__t" id="s-sub">Get the next print note</div>
      <p class="subscribe__d">One email every two to three weeks, plus a short print note
      whenever something breaks or moves. The archive is public; the index is published here
      whether you subscribe or not.</p>
      <div class="subscribe__cta">
        <a class="btn btn--primary" href="{_e(NEWSLETTER_URL)}"
        rel="noopener">Subscribe on Substack</a>
        <a class="btn btn--ghost" href="{_e(REPO_URL)}" rel="noopener">Watch the repo</a>
      </div>
    </div>
  </section>
</main>"""
    return _shell(
        ctx,
        title=f"Research — {BRAND}",
        description=(
            f"{BRAND} research notes: what the index measures, what moves it, and what it "
            "cannot yet say."
        ),
        current="research.html",
        body=body,
    )


def _strip_note_masthead(text: str) -> str:
    """Drop a note's own title block; the page header already publishes it.

    House format is `# Title`, a bold dek, a byline, then a `---` rule before the body.
    Everything above that rule is reproduced in `.pagehead`, so rendering it again gives
    the reader the headline twice. If the note is not in that shape, only the duplicate
    `# Title` line is removed.
    """
    lines = text.split("\n")
    start = next((i for i, ln in enumerate(lines) if ln.strip()), 0)
    if not lines[start].startswith("# "):
        return text
    rule = next(
        (i for i, ln in enumerate(lines[start : start + 10], start) if ln.strip() == "---"),
        None,
    )
    return "\n".join(lines[(rule + 1) if rule is not None else (start + 1) :]).lstrip("\n")


def _research_note(ctx: SiteContext, note: Note) -> str:
    if note.source is not None:
        _, raw = _front_matter(_read(note.source))
        doc = markdown.render(_rebrand_doc(_strip_note_masthead(raw)), heading_offset=1)
        content = f'<div class="md">{doc.html}</div>'
        toc = _toc(doc.headings)
    else:
        toc = ""
        content = f"""<div class="slot">
  <h2 class="slot__h">Content slot &#8212; awaiting copy</h2>
  <p><strong>This note has not been written yet.</strong></p>
  <p>This page is the rendered shell for
  <code class="inline">research/{_e(note.slug)}.md</code>. The generator renders that file
  through the in-repo Markdown renderer the moment it exists: headings become the table of
  contents on the left, pipe tables become dense scrollable grids with right-aligned
  numerals, fenced code becomes an inset well, and <code class="inline">[^1]</code>
  footnotes become the note strip at the foot of the page.</p>
  <p>Nothing on this page is fabricated in the meantime. There is no placeholder chart and no
  sample number &#8212; the same rule the index applies to a gapped session applies to a note
  that has not been written.</p>
</div>"""
    body = f"""<main class="wrap" id="main">
  <div class="pagehead">
    <div class="eyebrow"><a class="link-quiet" href="../research.html">{_e(BRAND)}
    Research</a></div>
    <h1 class="pagehead__h pagehead__h--display">{_e(note.title)}</h1>
    <p class="pagehead__dek">{_e(note.dek)}</p>
    <div class="pagehead__meta">
      <span class="num">{_e(_human_date(note.date) if _ISO_RE.fullmatch(note.date)
                          else note.date or "Unscheduled")}</span>
      <span>Methodology <span class="num">v{_e(ctx.version)}</span></span>
      <span>{_e(note.status)}</span>
      <span>Mark Rusch</span>
    </div>
  </div>
  <div class="doc">
    {toc}
    <div>{content}</div>
  </div>
</main>"""
    return _shell(
        ctx,
        title=f"{note.title} — {BRAND} Research",
        description=note.dek[:200],
        current="research.html",
        canonical=f"research/{note.slug}.html",
        body=body,
        prefix="../",
    )


# ==========================================================================
# entry point
# ==========================================================================


def _not_found(ctx: SiteContext) -> str:
    """The 404. Both hosts serve site/404.html for an unmatched path automatically.

    Until now an unmatched path fell through to the host's own default, which on Vercel
    is an unstyled white page -- the single worst place for the site to drop its theme,
    because a reader who mistypes a URL sees a page that looks like it belongs to nobody.

    It is noindex: a 404 that gets indexed competes with the pages that do exist. Links
    out are relative, so the page also works from the Pages mirror and a local checkout.
    """
    body = """<main class="wrap" id="main">
  <section class="pagehead">
    <div class="eyebrow">Error 404</div>
    <h1 class="pagehead__h pagehead__h--display">That page is not here<span
      style="color:var(--red)">.</span></h1>
    <p class="pagehead__dek">The address may be mistyped, or it may point at something
    that moved. Nothing published is ever deleted, so if you followed a link to a print
    or a research note it still exists somewhere below.</p>
    <div class="hero__cta" style="margin-top:var(--space-7)">
      <a class="btn btn--primary" href="index.html">Today&#8217;s print</a>
      <a class="btn btn--ghost" href="research.html">Research</a>
      <a class="btn btn--ghost" href="data.html">Data &amp; downloads</a>
    </div>
  </section>
</main>"""
    return _shell(
        ctx,
        title=f"Page not found — {BRAND}",
        description=(
            "That page is not here. Every published print and research note remains "
            "available from the index."
        ),
        current="",
        body=body,
        noindex=True,
    )


def _write_feed(ctx: SiteContext, notes: list[Note]) -> Path:
    """An Atom feed of the research notes.

    The research is the part of this project most likely to be read by someone who never
    opens the dashboard, and until now there was no way to follow it without checking the
    page by hand. A feed is the open-standard answer to that: no account, no tracking, no
    platform in the middle, and it costs one file.

    Only published notes with a real date go in. A planned note has no page to link to,
    and a feed entry pointing at a slot that says "not written yet" is the same failure
    as a tile showing a stale price.
    """
    published = [n for n in notes if n.source is not None and n.date]
    entries = []
    for n in published:
        url = _abs(f"research/{n.slug}.html")
        entries.append(
            "  <entry>\n"
            f"    <title>{_e(n.title)}</title>\n"
            f'    <link href="{_e(url)}"/>\n'
            f"    <id>{_e(url)}</id>\n"
            f"    <updated>{_e(n.date)}T00:00:00Z</updated>\n"
            f"    <summary>{_e(n.dek)}</summary>\n"
            "    <author><name>Mark Rusch</name></author>\n"
            "  </entry>"
        )
    # The feed's own timestamp is the newest note, not the build time. The site
    # regenerates daily whether or not the research changed, and a feed that claims to
    # have changed every day trains readers to ignore it.
    updated = (published[0].date if published else ctx.date) + "T00:00:00Z"
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<feed xmlns="http://www.w3.org/2005/Atom">\n'
        f"  <title>{_e(BRAND_FULL)} — Research</title>\n"
        f'  <link href="{_e(_abs("research.html"))}"/>\n'
        f'  <link rel="self" type="application/atom+xml" href="{_e(_abs("feed.xml"))}"/>\n'
        f"  <id>{_e(_abs('research.html'))}</id>\n"
        f"  <updated>{_e(updated)}</updated>\n"
        "  <author><name>Mark Rusch</name></author>\n"
        f"  <subtitle>{_e(BRAND_LINE)}</subtitle>\n"
        + "\n".join(entries)
        + "\n</feed>\n"
    )
    path = SITE_DIR / "feed.xml"
    path.write_text(xml, encoding="utf-8", newline="\n")
    return path


def generate(conn: sqlite3.Connection) -> list[Path]:
    """Render every page into site/. Returns the paths written, newest content first."""
    factors = load_factors()
    head = latest_print(conn, HEADLINE)
    now = utc_now_iso()
    ctx = SiteContext(
        conn=conn,
        factors=factors,
        version=factors.methodology_version,
        lock_hash=_lock_hash(),
        generated_at=now,
        head=head,
        date=head["date"] if head else now[:10],
    )
    notes = _discover_notes()

    pages: list[tuple[Path, str]] = [
        (SITE_DIR / "index.html", _dashboard(ctx, notes)),
        (SITE_DIR / "methodology.html", _methodology(ctx)),
        (SITE_DIR / "data.html", _data(ctx)),
        (SITE_DIR / "governance.html", _governance(ctx)),
        (SITE_DIR / "notices.html", _notices(ctx)),
        (SITE_DIR / "research.html", _research_index(ctx, notes)),
    ]
    pages += [
        (SITE_DIR / "research" / f"{n.slug}.html", _research_note(ctx, n)) for n in notes
    ]

    written = []
    for path, html_text in pages:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html_text, encoding="utf-8", newline="\n")
        written.append(path)

    # The sitemap is built before 404.html joins the list: a 404 must never be
    # advertised to a crawler as a page worth indexing.
    written.append(_write_sitemap(ctx, [p for p, _ in pages]))
    written.append(_write_feed(ctx, notes))

    not_found = SITE_DIR / "404.html"
    not_found.write_text(_not_found(ctx), encoding="utf-8", newline="\n")
    written.append(not_found)
    written.append(_write_robots())
    log.info("site: %d files -> %s", len(written), SITE_DIR)
    return written


def _write_sitemap(ctx: SiteContext, pages: list[Path]) -> Path:
    """A sitemap over the canonical domain, generated from what was actually written.

    Built from the page list rather than a hand-kept constant, so a new page cannot be
    published and then quietly left out of the index. `lastmod` is the print date, not
    the build timestamp: the site is regenerated daily whether or not anything changed,
    and telling a crawler that every page changed every day is how you get ignored.
    """
    urls = []
    for path in pages:
        rel = path.relative_to(SITE_DIR).as_posix()
        # The dashboard is the home page and outranks the rest; notes sit below the
        # top-level sections.
        priority = "1.0" if rel == "index.html" else "0.5" if "/" in rel else "0.8"
        urls.append(
            f"  <url><loc>{_e(_abs(rel))}</loc>"
            f"<lastmod>{_e(ctx.date)}</lastmod>"
            f"<priority>{priority}</priority></url>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls)
        + "\n</urlset>\n"
    )
    path = SITE_DIR / "sitemap.xml"
    path.write_text(xml, encoding="utf-8", newline="\n")
    return path


def _write_robots() -> Path:
    """Everything is public and crawlable; the only job here is to name the sitemap.

    `components.html` is excluded because it is a 164 KB design-system gallery that is
    linked from nowhere and would otherwise be the largest page a crawler indexes.
    """
    path = SITE_DIR / "robots.txt"
    path.write_text(
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /components.html\n"
        f"\nSitemap: {_abs('sitemap.xml')}\n",
        encoding="utf-8",
        newline="\n",
    )
    return path
