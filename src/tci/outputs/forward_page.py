# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""site/forward.html: the forward estimate, the cost to lock, and the record that scores them.

Rendered from `tci.forward_data.forward_tables`. Every number is baked in at build time, the
charts are inline SVG, and the page works with scripting disabled. A failure anywhere in
here renders a page saying the estimate could not be built this run; it never takes the rest
of the site with it.

Helpers come from `tci.outputs.site`, imported when the page renders rather than when this
module loads, because site.py imports this module.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("tci.outputs.forward_page")

HORIZON_LABEL = {30: "1 month", 91: "3 months", 182: "6 months", 365: "12 months"}
KIND_LABEL = {
    "listed": "vast.ai listed ask",
    "reserved": "vast.ai reserved, prepaid, restated as paid monthly",
    "rate_card": "rate card, whole contract charged",
}
DESCRIPTION = (
    "The expected average of the TCI EU H100 index over the next one to twelve months, what "
    "an EU buyer can lock the same compute for today, and the record that scores both."
)


def _h(days: int) -> str:
    return HORIZON_LABEL.get(days, f"{days} days")


def render(ctx: Any) -> str:
    from tci.outputs import site as s

    try:
        from tci import forward_data

        body = _body(s, forward_data.forward_tables(ctx.conn))
    except Exception:  # noqa: BLE001
        log.exception("site: forward page not built")
        body = _page(s, '<span class="chip chip--warning"><span>Not built today</span></span>',
                     '<div class="gapnote">' + s._icon("warn", 14) + "<p>The forward estimate "
                     "could not be built from the stored record on this run. The index and "
                     "every other page are unaffected.</p></div>")
    return s._shell(ctx, title=f"Forward — {s.BRAND}", description=DESCRIPTION,
                    current="forward.html", body=body)


def _by(rows: list[dict], component: str) -> dict[int, dict]:
    return {r["horizon_days"]: r for r in rows if r["component"] == component}


def _money(s: Any, v: float | None) -> str:
    return s._num(v) if v is None else f"${s._num(v)}"


# --- charts ------------------------------------------------------------------------------


def _segments(points: list[tuple[float, float] | None]) -> list[str]:
    out: list[str] = []
    run: list[tuple[float, float]] = []
    for p in points + [None]:
        if p is None:
            if len(run) > 1:
                out.append("M" + " L".join(f"{x} {y}" for x, y in run))
            run = []
        else:
            run.append(p)
    return out


def _horizon_chart(s: Any, horizons: list[int], mean: list[float | None],
                   low: list[float | None], high: list[float | None], *, what: str) -> str:
    """Values at a handful of horizons, straight segments between them, a truncated axis.

    Four points joined by straight lines: a smoothed curve would draw values between horizons
    that nothing estimated.
    """
    values = [v for v in mean + low + high if v is not None]
    if not values:
        return ""
    lo, hi, step = s._bounds(values)
    # A price axis never runs below zero, however far the padding would take it.
    lo = max(lo, 0.0)
    n = len(horizons)
    left, right = s.PL + 56, s.PR - 56
    dx = (right - left) / max(1, n - 1)

    def x(i: int) -> float:
        return round(left + i * dx, 1)

    def y(v: float) -> float:
        return round(s.PB - (v - lo) / (hi - lo) * (s.PB - s.PT), 1)

    grid, ticks = [], []
    t = lo
    while t <= hi + step / 2:
        gy = y(t)
        grid.append(f'<line class="ch-grid" x1="{s.PL}" y1="{gy}" x2="{s.PR}" y2="{gy}"/>')
        ticks.append(f'<text class="ch-tick" x="826" y="{gy + 4}">${t:,.2f}</text>')
        t += step

    def pts(vs: list[float | None]) -> list[tuple[float, float] | None]:
        return [None if v is None else (x(i), y(v)) for i, v in enumerate(vs)]

    band = "".join(
        f'<path d="{d}" fill="none" stroke="var(--chart-deemph)" stroke-width="1.5"'
        ' stroke-linejoin="round" stroke-linecap="round"/>'
        for series in (low, high) for d in _segments(pts(series))
    )
    # pathLength="1": see the note beside the same attribute in site.py's history chart.
    line = "".join(
        f'<path class="ch-line" pathLength="1" d="{d}"/>' for d in _segments(pts(mean))
    )
    first = next((i for i, v in enumerate(mean) if v is not None), None)
    markers = []
    for i, v in enumerate(mean):
        if v is None:
            continue
        cls = "ch-marker" if i == first else ""
        fill = "" if cls else ' fill="var(--chart-line)"'
        markers.append(f'<circle class="ch-marker-ring" cx="{x(i)}" cy="{y(v)}" r="6"/>'
                       f'<circle class="{cls}" cx="{x(i)}" cy="{y(v)}" r="4"{fill}/>')
    xlabels = "".join(f'<text class="ch-tick" x="{x(i)}" y="288" text-anchor="middle">'
                      f"{_h(h)}</text>" for i, h in enumerate(horizons))
    hits = []
    for i, v in enumerate(mean):
        if v is None:
            continue
        px, py = x(i), y(v)
        tx = px - 184 if px > s.PR - 210 else px + 12
        ty = max(s.PT, min(py - 30, s.PB - 66))
        band_txt = (f"10th to 90th ${low[i]:,.2f} to ${high[i]:,.2f}"
                    if low[i] is not None and high[i] is not None else what)
        hits.append(
            f'<g class="hp" tabindex="0" role="img" aria-label="{s._e(_h(horizons[i]))}:'
            f' {what} ${v:,.2f} per GPU-hour">'
            f'<line class="hp-cross" x1="{px}" y1="{s.PT}" x2="{px}" y2="{s.PB}"/>'
            f'<circle class="hp-dot" cx="{px}" cy="{py}" r="4.5"/>'
            f'<g class="hp-tip" transform="translate({round(tx, 1)} {round(ty, 1)})">'
            f'<rect class="hp-box" width="172" height="62" rx="3"/>'
            f'<line class="hp-key" x1="11" y1="18" x2="25" y2="18"/>'
            f'<text class="hp-val" x="31" y="22">${v:,.2f}</text>'
            f'<text class="hp-lab" x="11" y="37">{s._e(band_txt)}</text>'
            f'<text class="hp-lab" x="11" y="52">{s._e(_h(horizons[i]))}</text></g>'
            f'<rect class="hp-hit" x="{round(px - 28, 1)}" y="{s.PT}" width="56"'
            f' height="{s.PB - s.PT}"/></g>'
        )
    summary = f"{what} at " + ", ".join(
        f"{_h(h)} ${v:,.2f}" for h, v in zip(horizons, mean, strict=True) if v is not None)
    return (
        '<div class="scroll-x"><svg class="chart" viewBox="0 0 880 300" role="group"'
        f' aria-label="{s._e(summary)}">'
        f'<g aria-hidden="true">{"".join(grid)}'
        f'<line class="ch-axis" x1="{s.PL}" y1="{s.PB}" x2="{s.PR}" y2="{s.PB}"/></g>'
        f"{band}{line}{''.join(markers)}"
        f'<g aria-hidden="true">{"".join(ticks)}{xlabels}</g>'
        f'<g class="hp-layer">{"".join(hits)}</g></svg></div>'
    )


def _legend() -> str:
    def key(stroke: str, width: str) -> str:
        return (f'<svg width="22" height="8" aria-hidden="true"><line x1="1" y1="4" x2="21" y2="4"'
                f' stroke="{stroke}" stroke-width="{width}" stroke-linecap="round"/></svg>')

    mean_key, band_key = key("var(--chart-line)", "2"), key("var(--chart-deemph)", "1.5")
    return (f'<p class="ledger__d" style="margin-top:var(--space-3)">{mean_key} Expected mean'
            f" &#183; {band_key} 10th and 90th percentiles of the window mean</p>")


# --- sections ----------------------------------------------------------------------------


def _section(sid: str, title: str, dek: str, inner: str) -> str:
    return (f'<section class="section" aria-labelledby="{sid}"><div class="section__head"><div>'
            f'<h2 class="section__h" id="{sid}">{title}</h2>'
            + (f'<p class="section__dek">{dek}</p>' if dek else "")
            + f"</div></div>{inner}</section>")


def _table(head: list[tuple[str, bool]], rows: list[list[str]], caption: str) -> str:
    right_cls = ' class="ta-r"'
    th = "".join(f'<th scope="col"{right_cls if right else ""}>{label}</th>'
                 for label, right in head)
    body = "".join("<tr>" + "".join(cells) + "</tr>" for cells in rows)
    return (f'<div class="card card__body--flush"><div class="scroll-x"><table class="grid">'
            f'<caption class="vh">{caption}</caption><thead><tr>{th}</tr></thead>'
            f"<tbody>{body}</tbody></table></div></div>")


def _num_cell(s: Any, v: float | None, dp: int = 2) -> str:
    if v is None:
        return '<td class="ta-r u">&#8212;</td>'
    return f'<td class="ta-r num">{s._num(v, dp)}</td>'


def _diff_cell(s: Any, diff: float | None) -> str:
    if diff is None:
        return '<td class="ta-r u">&#8212;</td>'
    sign = "+" if diff >= 0 else "&#8722;"
    return f'<td class="ta-r num">{sign}{s._num(abs(diff))}</td>'


def _gap_reasons(s: Any, rows: dict[int, dict], horizons: list[int]) -> str:
    items = "".join(f"<li>{s._e(_h(h))}: {s._e(rows[h]['detail']['gap'])}</li>"
                    for h in horizons if h in rows and "gap" in rows[h]["detail"])
    return f'<div class="md"><ul>{items}</ul></div>' if items else ""


def _page(s: Any, status: str, inner: str, links: bool = False) -> str:
    meta_links = ('<span><a href="data/forward/latest.json">latest.json</a></span>'
                  '<span><a href="data/forward/history.csv">history.csv</a></span>'
                  if links else "")
    return f"""<main class="wrap" id="main">
  <div class="pagehead">
    <div class="eyebrow">Forward</div>
    <h1 class="pagehead__h pagehead__h--display">What the index is expected to average, and
    what locking the same compute costs today.</h1>
    <p class="pagehead__dek">For the next one, three, six and twelve months: the expected
    mean of the TCI EU H100 print, with its range, and the cheapest price an EU buyer can
    secure the same unit at today. A research output beside the index, estimated from the
    stored record as it stood on the day, and not a reference price for any financial
    instrument.</p>
    <div class="pagehead__meta"><span>{status}</span>{meta_links}</div>
  </div>
  {inner}
</main>"""


def _body(s: Any, tables: dict[str, Any] | None) -> str:
    if tables is None:
        return _page(s, '<span class="chip chip--warning"><span>Not yet estimated</span></span>',
                     '<div class="gapnote">' + s._icon("warn", 14) + "<p>No forward estimate "
                     "has been recorded yet.</p></div>")
    horizons: list[int] = tables["parameters"]["horizons_days"]
    latest = tables["latest"]
    m, t, lk = _by(latest, "M"), _by(latest, "T"), _by(latest, "L")
    as_of = tables["as_of"]
    when = s._e(s._human_date(as_of))
    status = (f'<span class="chip"><span>Estimated {when}</span></span>'
              f'<span class="u">method {s._e(tables["method_version"])}</span>')

    def col(rows: dict[int, dict], key: str) -> list[float | None]:
        return [rows.get(h, {}).get(key) for h in horizons]

    mean = col(m, "value_usd")
    if any(v is not None for v in mean):
        chart = _horizon_chart(s, horizons, mean, col(m, "p10"), col(m, "p90"),
                               what="Expected mean") + _legend()
    else:
        chart = ('<div class="gapnote">' + s._icon("warn", 14) + "<p>No estimate on "
                 f"{s._e(s._human_date(as_of))}. The reasons, by window, are below. Nothing is "
                 "carried forward from an earlier day.</p></div>")
    rows = []
    for h in horizons:
        mr, tr, lr = m.get(h, {}), t.get(h, {}), lk.get(h, {})
        mv, lv = mr.get("value_usd"), lr.get("value_usd")
        diff = None if mv is None or lv is None else lv - mv
        rows.append([
            f'<th scope="row">{s._e(_h(h))}</th>',
            _num_cell(s, mv), _num_cell(s, mr.get("p10")), _num_cell(s, mr.get("p50")),
            _num_cell(s, mr.get("p90")), _num_cell(s, tr.get("value_usd")), _num_cell(s, lv),
            _diff_cell(s, diff),
        ])
    table = _table(
        [("Window", False), ("Expected mean", True), ("10th", True), ("Median", True),
         ("90th", True), ("Term-implied", True), ("Cheapest lock", True),
         ("Lock minus expected", True)],
        rows, "Expected window mean, its percentiles, the term-implied figure and the cheapest "
              "lock, USD per GPU-hour")
    expected = _section(
        "s-expected", "Expected index over the window",
        "USD per GPU-hour. The mean of the published print over the window, as expected on the "
        "estimate date, with the 10th and 90th percentiles of that mean.",
        f'<div class="card"><div class="card__body">{chart}'
        f'<details class="tableview" open><summary>Table view</summary>{table}</details>'
        f"</div></div>{_gap_reasons(s, m, horizons)}")

    lock_mean = col(lk, "value_usd")
    lock_chart = _horizon_chart(s, horizons, lock_mean, [None] * len(horizons),
                                [None] * len(horizons), what="Cheapest lock")
    lock_rows = []
    for h in horizons:
        r = lk.get(h)
        if r is None or r["value_usd"] is None:
            reason = (r or {}).get("detail", {}).get("gap", "not estimated")
            lock_rows.append([f'<th scope="row">{s._e(_h(h))}</th>',
                              '<td class="ta-r u">&#8212;</td>',
                              f'<td class="u" colspan="4">{s._e(reason)}</td>'])
            continue
        d = r["detail"]
        lock_rows.append([
            f'<th scope="row">{s._e(_h(h))}</th>', _num_cell(s, r["value_usd"]),
            f"<td>{s._e(KIND_LABEL.get(d['kind'], d['kind']))}</td>",
            f"<td>{s._e(d['seller'])}</td>",
            _num_cell(s, d.get("covers_days"), 0),
            f'<td class="ta-r num">{s._e(r["n_inputs"])}</td>',
        ])
    lock = _section(
        "s-lock", "Cost to lock today",
        "USD per GPU-hour actually used. The cheapest way an EU buyer could secure H100 SXM "
        "compute under the index's unit today: EU/EEA, at least two GPUs. A contract longer "
        "than the window is charged in full. It is a price to act on. It neither forecasts nor "
        "bounds the index, because a median of offers cannot be reproduced by renting one of "
        "them.",
        f'<div class="card"><div class="card__body">{lock_chart}</div></div>'
        + _table([("Window", False), ("Per used GPU-hour", True), ("How", False),
                  ("Seller", False), ("Covers, days", True), ("Offers counted", True)],
                 lock_rows, "Cheapest lockable cost by window"))

    term_items = []
    for h in horizons:
        r = t.get(h)
        if r is None:
            continue
        sellers = r["detail"].get("sellers") or []
        named = ", ".join(f"{s._e(name)} ({s._e(form.replace('_', ' '))})"
                          for name, form in sellers) or "none"
        state = (f"${s._num(r['value_usd'])}" if r["value_usd"] is not None
                 else s._e(r["detail"].get("gap", "not estimated")))
        term_items.append(f"<li><strong>{s._e(_h(h))}</strong>: {state}. Sellers at this tenor: "
                          f"{named}.</li>")
    term = _section(
        "s-term", "Term-implied, beside the estimate",
        "The expected level scaled by the median discount of at least three independent EU "
        "sellers in the index population at exactly that tenor. Shown for comparison and never "
        "used to form the estimate. A schedule that prices every chip alike is not counted.",
        f'<div class="md"><ul>{"".join(term_items)}</ul></div>')

    cal_rows = []
    for c in tables["calibration"]:
        scored = c["bias"] is not None
        cal_rows.append([
            f'<th scope="row">{s._e(_h(c["horizon_days"]))}</th>',
            f"<td>{'backfilled' if c['backfilled'] else 'as made'}</td>",
            f'<td class="ta-r num">{c["n_closed"]}</td>',
            f'<td class="ta-r num">{c["n_nonoverlapping"]} of {c["required"]}</td>',
            _num_cell(s, c["bias"]) if scored else '<td class="ta-r u">&#8212;</td>',
            _num_cell(s, c["rmse"]) if scored else '<td class="ta-r u">&#8212;</td>',
            (f'<td class="ta-r num">{round(c["coverage"] * 100)}%</td>' if scored
             else '<td class="ta-r u">&#8212;</td>'),
        ])
    record = _section(
        "s-record", "Track record",
        "Estimates are stored as they are made and never revised in place. A window is scored "
        "once it has closed, only if at least half its days printed, and only windows that do "
        "not overlap count, so a year-long horizon adds one independent observation a year. "
        f"Rows marked backfilled were computed after their date for "
        f"{tables['n_backfilled_dates']} earlier sessions, from data stored on each date. They "
        "are shown apart and are not a track record.",
        _table([("Window", False), ("Estimates", False), ("Closed", True),
                ("Independent", True), ("Bias", True), ("RMSE", True),
                ("Inside 10th to 90th", True)],
               cal_rows, "Scored windows by horizon"))

    method = _section("s-method", "How the estimate is made", "", """<div class="md">
<p>A GPU-hour cannot be stored, so today's price does not fix a forward price the way carry
fixes one for a metal. The forward of a good that cannot be stored is an expectation, and that
is what is estimated here: the price level is treated as a martingale, so with no drift the
record can measure, the expected level over any window is today's.</p>
<p>The print is a weighted median of a handful of prices and moves in steps. It is modelled
as a level plus measurement noise, fitted by maximum likelihood on each estimate date, so a
one-day spike moves the estimate a little rather than all the way.</p>
<p>Where a window runs past an announced change of methodology, the days after the change are
estimated from the index recomputed under the new rules from the same stored prices. A version
counts only once its notice was published.</p>
<p>No estimate is published on a day the recomputed index did not print, and no earlier value
is carried forward.</p>
<p>Prepaid prices are restated as paid monthly at the government curve of their currency before
they are compared with anything.</p>
</div>""")

    return _page(s, status, expected + lock + term + record + method, links=True)
