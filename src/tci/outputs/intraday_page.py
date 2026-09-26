# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""site/intraday.html and site/data/intraday/: the index recomputed at every hourly read.

Built from `tci.intraday.path`, which replays the fixing's own calculation over the intraday
store. Every number is baked in at build time, the charts are inline SVG, and the page works
with scripting disabled. A failure in here renders a page saying so and never takes the rest
of the site with it, the same contract as forward.html.

Two callers. `site.generate` renders this page with every other page after the 11:00 run.
The hourly intraday workflow calls `build_and_write` and touches nothing else under site/,
so an hourly commit cannot rewrite a page the fixing owns.
"""

from __future__ import annotations

import csv
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tci import intraday, series_read

log = logging.getLogger("tci.outputs.intraday_page")

WINDOW_DAYS = 14  # data files and the settlement table
CHART_DAYS = 7
MAX_JOIN_HOURS = 2.5  # two points further apart than this are not joined by a line

DESCRIPTION = (
    "The TCI EU H100 index recomputed at every hourly read of its sources, beside the "
    "published 11:00 UTC fixing, with a settlement-window average for comparison."
)


@dataclass(frozen=True)
class Built:
    now: datetime
    cfg: intraday.IntradayConfig
    snapshots: list[intraday.Snapshot]
    settlements: dict[str, list[intraday.Settlement]]  # series -> newest day first
    fixings: dict[str, list[tuple[datetime, float]]]  # series -> (fixing time, published)


def _data_dir() -> Path:
    from tci.outputs import site

    return site.SITE_DIR / "data" / "intraday"


def build(conn: sqlite3.Connection, store: intraday.Store | None = None,
          cfg: intraday.IntradayConfig | None = None, now: datetime | None = None) -> Built:
    cfg = cfg or intraday.load_config()
    store = store or intraday.Store()
    now = now or datetime.now(UTC)
    start = (now - timedelta(days=WINDOW_DAYS)).replace(hour=0, minute=0, second=0,
                                                        microsecond=0)
    snaps = intraday.path(store, conn, cfg, start, now)
    fixing_at = {s.at.strftime("%Y-%m-%d"): s.at for s in snaps if s.origin == "fixing"}
    # Settlement is scored only on days with at least one intraday sweep. A day before the
    # sweeps began has nothing to average, and listing it would fill the table with
    # windows that were never sampled rather than windows that came up short.
    days = sorted({s.at.strftime("%Y-%m-%d") for s in snaps if s.origin == "intraday"},
                  reverse=True)
    settlements: dict[str, list[intraday.Settlement]] = {}
    fixings: dict[str, list[tuple[datetime, float]]] = {}
    for series in cfg.series:
        settlements[series] = [
            intraday.settle(snaps, day, series, cfg.settlement,
                            series_read.head_value(conn, series, day))
            for day in days
        ]
        # The marker is the PUBLISHED print, read from daily_index, not the reconstruction
        # at the fixing's time. On most days they are the same number; on a day the fixing
        # was assembled from reads hours apart they are not, and the chart must show what
        # was actually published.
        fixings[series] = [
            (t, v) for day, t in sorted(fixing_at.items())
            if (v := series_read.head_value(conn, series, day)) is not None
        ]
    return Built(now, cfg, snaps, settlements, fixings)


def write_data(built: Built, out_dir: Path | None = None) -> list[Path]:
    """path.csv (every reconstructed value, trailing window) and latest.json."""
    out = out_dir or _data_dir()
    out.mkdir(parents=True, exist_ok=True)
    path_csv = out / "path.csv"
    with open(path_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["at_utc", "origin", "series", "value_usd", "value_eur", "n_sources",
                    "flags", "sources_missing"])
        for s in built.snapshots:
            for series, (usd, eur, n, flags) in s.values.items():
                w.writerow([intraday._iso(s.at), s.origin, series, usd, eur, n, flags,
                            " ".join(s.missing)])
    latest = built.snapshots[-1] if built.snapshots else None
    doc: dict[str, Any] = {
        "generated_utc": intraday._iso(built.now),
        "note": ("Research beside the index. Reconstructed from intraday reads with the "
                 "fixing's own calculation; not a print, and not for settlement."),
        "settlement_window": {
            "start_utc": built.cfg.settlement.start, "end_utc": built.cfg.settlement.end,
            "method": built.cfg.settlement.method,
            "min_points": built.cfg.settlement.min_points,
        },
        "latest": None if latest is None else {
            "at_utc": intraday._iso(latest.at), "origin": latest.origin,
            "values": {k: {"value_usd": v[0], "value_eur": v[1], "n_sources": v[2],
                           "flags": v[3]} for k, v in latest.values.items()},
            "source_age_minutes": latest.ages, "sources_missing": list(latest.missing),
        },
        "settlements": {
            series: [
                {"date": st.date, "value_usd": st.value_usd, "n_points": st.n_points,
                 "reason": st.reason, "fixing_usd": st.fixing_usd}
                for st in rows
            ]
            for series, rows in built.settlements.items()
        },
    }
    latest_json = out / "latest.json"
    latest_json.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return [path_csv, latest_json]


def build_and_write(conn: sqlite3.Connection) -> list[Path]:
    """The hourly workflow's output step: data files and this one page, nothing else."""
    from tci.outputs import site

    built = build(conn)
    written = write_data(built)
    page = site.SITE_DIR / "intraday.html"
    page.write_text(render(site.build_context(conn), built), encoding="utf-8", newline="\n")
    return [*written, page]


# --- rendering ---------------------------------------------------------------------------


def render(ctx: Any, built: Built | None = None) -> str:
    from tci.outputs import site as s

    try:
        built = built or build(ctx.conn)
        body = _body(s, built)
    except Exception:  # noqa: BLE001
        log.exception("site: intraday page not built")
        body = _page(s, '<span class="chip chip--warning"><span>Not built</span></span>',
                     '<div class="gapnote">' + s._icon("warn", 14) + "<p>The intraday path "
                     "could not be built from the stored reads on this run. The index and "
                     "every other page are unaffected.</p></div>")
    # Not in the primary nav (a ninth item wraps the masthead at desktop widths), so it
    # marks Indices as current, the way a research note marks Research, and keeps its own
    # canonical URL.
    return s._shell(ctx, title=f"Intraday — {s.BRAND}", description=DESCRIPTION,
                    current="index.html", canonical="intraday.html", body=body)


def _hhmm(t: datetime) -> str:
    return t.strftime("%H:%M")


def _when(s: Any, t: datetime) -> str:
    return f"{s._human_date(t.strftime('%Y-%m-%d'))}, {_hhmm(t)} UTC"


def _time_chart(s: Any, pts: list[tuple[datetime, float | None]],
                fixings: list[tuple[datetime, float]], start: datetime, end: datetime,
                *, symbol: str, focusable: bool) -> str:
    """Values against real time. Straight segments, broken at a gap or a missed sweep.

    Straight rather than smoothed: between two hourly reads nothing was observed, and a
    curve would draw a price path nobody quoted. Fixings are squares, the reconstruction is
    a line with round markers only at its end, so the two are told apart by shape.
    """
    vals = [v for _, v in pts if v is not None] + [v for _, v in fixings]
    if not vals:
        return ('<div class="gapnote">' + s._icon("warn", 14) + f"<p>No reconstructed value "
                f"for <strong>{s._e(symbol)}</strong> in this window. Every read in it gapped "
                "or none was made; nothing is drawn in their place.</p></div>")
    lo, hi, step = s._bounds(vals)
    lo = max(lo, 0.0)
    span = (end - start).total_seconds()

    def x(t: datetime) -> float:
        return round(s.PL + (t - start).total_seconds() / span * (s.PR - s.PL), 1)

    def y(v: float) -> float:
        return round(s.PB - (v - lo) / (hi - lo) * (s.PB - s.PT), 1)

    grid, ticks = [], []
    t = lo
    while t <= hi + step / 2:
        gy = y(t)
        grid.append(f'<line class="ch-grid" x1="{s.PL}" y1="{gy}" x2="{s.PR}" y2="{gy}"/>')
        ticks.append(f'<text class="ch-tick" x="826" y="{gy + 4}">${t:,.2f}</text>')
        t += step

    segments: list[list[tuple[float, float]]] = []
    run: list[tuple[float, float]] = []
    prev_t: datetime | None = None
    for at, v in pts:
        broken = v is None or (
            prev_t is not None and (at - prev_t) > timedelta(hours=MAX_JOIN_HOURS))
        if broken and run:
            segments.append(run)
            run = []
        if v is not None:
            run.append((x(at), y(v)))
        prev_t = at
    if run:
        segments.append(run)
    paths = "".join(
        f'<path class="ch-line" pathLength="1" d="M{" L".join(f"{a} {b}" for a, b in seg)}"/>'
        for seg in segments if len(seg) > 1
    )
    lone = "".join(
        f'<circle cx="{seg[0][0]}" cy="{seg[0][1]}" r="2.6" fill="var(--chart-line)"/>'
        for seg in segments if len(seg) == 1
    )
    gapmarks = "".join(
        f'<line class="ch-gapmark" x1="{x(at)}" y1="{s.PB - 4}" x2="{x(at)}" y2="{s.PB + 4}"/>'
        for at, v in pts if v is None
    )
    squares = "".join(
        f'<rect x="{x(at) - 6}" y="{y(v) - 6}" width="12" height="12" fill="var(--chart-ring)"/>'
        f'<rect x="{x(at) - 4}" y="{y(v) - 4}" width="8" height="8"'
        ' fill="var(--chart-line)"/>'
        for at, v in fixings if start <= at <= end
    )

    # x labels: midnights for a multi-day window, six-hourly for a single day.
    xlabels = []
    multi_day = span > 36 * 3600
    tick_t = start.replace(minute=0, second=0, microsecond=0)
    while tick_t <= end:
        if (multi_day and tick_t.hour == 0) or (not multi_day and tick_t.hour % 6 == 0):
            if tick_t >= start:
                label = (tick_t.strftime("%d %b").lstrip("0") if multi_day
                         else _hhmm(tick_t))
                xlabels.append(f'<text class="ch-tick" x="{x(tick_t)}" y="288"'
                               f' text-anchor="middle">{label}</text>')
        tick_t += timedelta(hours=1)

    published = [(at, v) for at, v in pts if v is not None]
    last = ""
    if published:
        at, v = published[-1]
        lx, ly = x(at), y(v)
        last = (f'<text class="ch-cur" x="{max(lx - 8, s.PL + 60)}" y="{ly - 12}"'
                f' text-anchor="end">${v:,.2f}</text>'
                f'<circle class="ch-marker-ring" cx="{lx}" cy="{ly}" r="6"/>'
                f'<circle class="ch-marker" cx="{lx}" cy="{ly}" r="4"/>')

    hits = []
    hw = max(4.0, min(28.0, (s.PR - s.PL) / max(1, len(pts))))
    tab = ' tabindex="0"' if focusable else ""
    for at, v in published:
        px, py = x(at), y(v)
        tx = px - 164 if px > s.PR - 190 else px + 12
        ty = max(s.PT, min(py - 24, s.PB - 50))
        hits.append(
            f'<g class="hp"{tab} role="img" aria-label="{s._e(_when(s, at))}: ${v:,.2f}">'
            f'<line class="hp-cross" x1="{px}" y1="{s.PT}" x2="{px}" y2="{s.PB}"/>'
            f'<circle class="hp-dot" cx="{px}" cy="{py}" r="4.5"/>'
            f'<g class="hp-tip" transform="translate({round(tx, 1)} {round(ty, 1)})">'
            f'<rect class="hp-box" width="152" height="46" rx="3"/>'
            f'<line class="hp-key" x1="11" y1="18" x2="25" y2="18"/>'
            f'<text class="hp-val" x="31" y="22">${v:,.2f}</text>'
            f'<text class="hp-lab" x="11" y="37">{s._e(_when(s, at))}</text></g>'
            f'<rect class="hp-hit" x="{round(px - hw / 2, 1)}" y="{s.PT}"'
            f' width="{round(hw, 1)}" height="{s.PB - s.PT}"/></g>'
        )
    n_gap = sum(1 for _, v in pts if v is None)
    summary = (f"{symbol}, {len(pts)} reads from {_when(s, start)} to {_when(s, end)}: "
               f"{len(published)} with a value, {n_gap} gapped, {len(fixings)} fixings marked.")
    return (
        '<div class="scroll-x scroll-x--recent"><svg class="chart" viewBox="0 0 880 300"'
        f' role="group" aria-label="{s._e(summary)}">'
        f'<g aria-hidden="true">{"".join(grid)}'
        f'<line class="ch-axis" x1="{s.PL}" y1="{s.PB}" x2="{s.PR}" y2="{s.PB}"/></g>'
        f'{paths}{lone}<g aria-hidden="true">{squares}{gapmarks}{"".join(ticks)}'
        f'{"".join(xlabels)}</g>{last}<g class="hp-layer">{"".join(hits)}</g></svg></div>'
    )


def _legend() -> str:
    line = ('<svg width="22" height="8" aria-hidden="true"><line x1="1" y1="4" x2="21" y2="4"'
            ' stroke="var(--chart-line)" stroke-width="2" stroke-linecap="round"/></svg>')
    square = ('<svg width="12" height="12" aria-hidden="true"><rect x="2" y="2" width="8"'
              ' height="8" fill="var(--chart-line)"/></svg>')
    return (f'<p class="ledger__d" style="margin-top:var(--space-3)">{line} Recomputed at each '
            f"read &#183; {square} 11:00 UTC fixing as published</p>")


def _series_points(built: Built, series: str, start: datetime
                   ) -> list[tuple[datetime, float | None]]:
    return [(sn.at, sn.values[series][0]) for sn in built.snapshots
            if sn.at >= start and series in sn.values]


def _section(sid: str, title: str, dek: str, inner: str) -> str:
    from tci.outputs.forward_page import _section as section

    return section(sid, title, dek, inner)


def _table(head: list[tuple[str, bool]], rows: list[list[str]], caption: str) -> str:
    from tci.outputs.forward_page import _table as table

    return table(head, rows, caption)


def _num_cell(s: Any, v: float | None, dp: int = 2) -> str:
    if v is None:
        return '<td class="ta-r u">&#8212;</td>'
    return f'<td class="ta-r num">{s._num(v, dp)}</td>'


def _page(s: Any, status: str, inner: str, links: bool = False) -> str:
    meta_links = ('<span><a href="data/intraday/latest.json">latest.json</a></span>'
                  '<span><a href="data/intraday/path.csv">path.csv</a></span>'
                  if links else "")
    return f"""<main class="wrap" id="main">
  <div class="pagehead">
    <div class="eyebrow">Intraday</div>
    <h1 class="pagehead__h pagehead__h--display">The index, recomputed at every hourly read
    of its sources.</h1>
    <p class="pagehead__dek">The published price is still the 11:00 UTC fixing, computed from
    one collection a day. The line below is the same calculation, under the same
    methodology version, replayed over reads taken through the day. It is research beside
    the index: it is not a print, it is never stored as one, and it is not a reference price
    for any financial instrument.</p>
    <div class="pagehead__meta"><span>{status}</span>{meta_links}</div>
  </div>
  {inner}
</main>"""


def _body(s: Any, built: Built) -> str:
    cfg = built.cfg
    if not built.snapshots:
        return _page(s, '<span class="chip chip--neutral"><span>No reads yet</span></span>',
                     '<div class="gapnote">' + s._icon("warn", 14) + "<p>No intraday read "
                     "has been stored yet. The first hourly sweep will start the record.</p>"
                     "</div>")
    head = cfg.series[0]
    sym = s.display_series(head)
    latest = built.snapshots[-1]
    status = (f'<span class="chip"><span>Latest read {s._e(_when(s, latest.at))}</span></span>'
              f'<span class="u">{len(built.snapshots)} reads in {WINDOW_DAYS} days</span>')

    now = built.now
    day_start = now - timedelta(hours=24)
    week_start = now - timedelta(days=CHART_DAYS)
    fix = built.fixings.get(head, [])
    charts = (
        '<div class="card"><div class="card__body">'
        '<h3 class="section__h">Last 24 hours</h3>'
        + _time_chart(s, _series_points(built, head, day_start), fix, day_start, now,
                      symbol=sym, focusable=True)
        + f'<h3 class="section__h" style="margin-top:var(--space-5)">Last {CHART_DAYS} days</h3>'
        + _time_chart(s, _series_points(built, head, week_start), fix, week_start, now,
                      symbol=sym, focusable=False)
        + _legend() + _table_view(s, built, head, day_start) + "</div></div>"
    )
    main = _section("s-path", f"{s._e(sym)} through the day",
                    "USD per GPU-hour, times in UTC. A break in the line is a read that gapped "
                    "or a sweep that did not run; nothing is drawn across it.", charts)

    others = []
    for series in cfg.series[1:]:
        pts = _series_points(built, series, week_start)
        if not any(v is not None for _, v in pts):
            continue
        name = s.display_series(series)
        others.append(
            f'<div class="card"><div class="card__body"><h3 class="section__h">{s._e(name)}'
            "</h3>" + _time_chart(s, pts, built.fixings.get(series, []), week_start, now,
                                  symbol=name, focusable=False) + "</div></div>")
    other = _section("s-family", f"The rest of the family, last {CHART_DAYS} days",
                     "Only series with at least one value in the window are drawn. A series "
                     "that gapped at every read is left out rather than drawn flat.",
                     "".join(others)) if others else ""

    return _page(s, status, main + other + _settlement(s, built) + _freshness(s, built)
                 + _method(s, cfg), links=True)


def _table_view(s: Any, built: Built, series: str, since: datetime) -> str:
    rows = []
    for sn in reversed([x for x in built.snapshots if x.at >= since]):
        usd, _eur, n, flags = sn.values.get(series, (None, None, 0, ""))
        rows.append([
            f'<th scope="row">{s._e(_when(s, sn.at))}</th>',
            f"<td>{'fixing reads' if sn.origin == 'fixing' else 'hourly sweep'}</td>",
            _num_cell(s, usd), f'<td class="ta-r num">{n}</td>',
            f"<td>{s._e(s._flag_words(flags) or '')}</td>",
        ])
    table = _table([("Read", False), ("From", False), ("USD per GPU-hour", True),
                    ("Providers", True), ("Gap reason", False)], rows,
                   "Reconstructed values, last 24 hours")
    return f'<details class="tableview"><summary>Table view</summary>{table}</details>'


def _settlement(s: Any, built: Built) -> str:
    cfg = built.cfg
    head = cfg.series[0]
    rows = []
    for st in built.settlements.get(head, []):
        diff = pct = None
        if st.value_usd is not None and st.fixing_usd:
            diff = st.value_usd - st.fixing_usd
            pct = diff / st.fixing_usd * 100.0
        rows.append([
            f'<th scope="row">{s._e(s._human_date(st.date))}</th>',
            _num_cell(s, st.value_usd), f'<td class="ta-r num">{st.n_points}</td>',
            _num_cell(s, st.fixing_usd),
            _num_cell(s, diff) if diff is None else
            f'<td class="ta-r num">{"+" if diff >= 0 else "&#8722;"}{s._num(abs(diff))}</td>',
            '<td class="ta-r u">&#8212;</td>' if pct is None else
            f'<td class="ta-r num">{"+" if pct >= 0 else "&#8722;"}{s._num(abs(pct))}%</td>',
            f'<td class="u">{s._e(st.reason or "")}</td>',
        ])
    w = cfg.settlement
    return _section(
        "s-settle", "A settlement window, against the fixing",
        f"The {w.method} of every reconstructed {s._e(s.display_series(head))} value from "
        f"{w.start} to {w.end} UTC, beside the 11:00 fixing as published. At least "
        f"{w.min_points} values are required; with fewer the window has no value, and no "
        "earlier one is carried in. Shown to measure what averaging would change. The "
        "published price remains the fixing.",
        _table([("Date", False), ("Window", True), ("Reads", True), ("Fixing", True),
                ("Window minus fixing", True), ("As % of fixing", True), ("Why empty", False)],
               rows, "Settlement window value against the published fixing, by date"))


def _freshness(s: Any, built: Built) -> str:
    latest = built.snapshots[-1]
    rows = []
    for name, c in sorted(built.cfg.sources.items()):
        age = latest.ages.get(name)
        state = "no read inside its limit" if age is None else f"{age} min"
        rows.append([
            f'<th scope="row">{s._e(name)}</th>',
            f"<td>every {c.every_hours:g} h</td>", f"<td>{c.max_age_hours:g} h</td>",
            (f'<td class="u">{s._e(state)}</td>' if age is None
             else f"<td>{s._e(state)}</td>"),
        ])
    return _section(
        "s-fresh", "Sources at the latest read",
        f"How old each source's read was at {s._e(_when(s, latest.at))}. A source past its "
        "limit drops out of the reconstruction, which can then gap exactly as the fixing "
        "would with that source missing.",
        _table([("Source", False), ("Read", False), ("Counts for up to", False),
                ("Age at latest read", False)], rows, "Source read ages"))


def _method(s: Any, cfg: intraday.IntradayConfig) -> str:
    return _section("s-how", "How the line is made", "", """<div class="md">
<p>Each source is read at the rate its price can move. Marketplace books and stock-driven
feeds are read every hour; rate cards on pricing pages every six hours. The fixing's own
11:00 read counts as a read, so the hour after it asks nobody for the same page twice.</p>
<p>At every sweep, the newest read of each source is taken, provided it is younger than that
source's limit. Those rows go through the fixing's normalisation and estimator unchanged,
under the methodology version in effect on the date, with the panel, the node-size floor,
the provider gate and the trim that version sets. A read below the provider gate is a gap
here as it would be at 11:00.</p>
<p>The reads are kept in an append-only store in the repository, one file per UTC day, so
every value on this page can be recomputed from what was read. Nothing in that store reaches
a published print.</p>
</div>""")
