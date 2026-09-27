# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""site/spot.html and site/data/spot/: spot against on-demand, by region.

Everything on the page comes from `tci.spot`, which reads stored prints and, for the days
before the spot series took effect, replays them from stored observations and labels
them indicative. The page never shows an indicative value without saying so, never draws
a line across a gap, and never promotes an earlier day's value into today's slot.

Two callers, like the intraday page: `site.generate` after the 11:00 run, and the hourly
job through `intraday_page.build_and_write`, so the hourly spot line is as fresh as the
intraday one. A failure renders a page that says so and never takes the site with it.
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tci import spot

log = logging.getLogger("tci.outputs.spot_page")

DESCRIPTION = (
    "Spot H100 prices beside on-demand for the EU, the US and every country: the spread, "
    "each seller's own discount, and how much each price moves."
)
HOURLY_DAYS = 7


def _data_dir() -> Path:
    from tci.outputs import site

    return site.SITE_DIR / "data" / "spot"


def _hourly(built: Any) -> dict[str, list[tuple[datetime, float | None]]]:
    """Series -> (read time, value) over the last HOURLY_DAYS, from the intraday replay."""
    if built is None:
        return {}
    since = built.now - timedelta(days=HOURLY_DAYS)
    out: dict[str, list[tuple[datetime, float | None]]] = {}
    for sn in built.snapshots:
        if sn.at < since:
            continue
        for series, v in sn.values.items():
            out.setdefault(series, []).append((sn.at, v[0]))
    return out


def _indicative_times(built: Any, series: str) -> frozenset[datetime]:
    if built is None:
        return frozenset()
    return frozenset(sn.at for sn in built.snapshots
                     if series in sn.values and "indicative" in sn.values[series][3])


def build(conn: Any, built: Any = None, now: datetime | None = None) -> list[spot.RegionView]:
    now = now or (built.now if built is not None else datetime.now(UTC))
    return spot.build(conn, now.strftime("%Y-%m-%d"), _hourly(built))


def write_data(views: list[spot.RegionView], now: datetime,
               out_dir: Path | None = None) -> list[Path]:
    """daily.csv (every day, every leg, with its status) and latest.json (the decision view)."""
    out = out_dir or _data_dir()
    out.mkdir(parents=True, exist_ok=True)
    daily_csv = out / "daily.csv"
    with open(daily_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["date", "region", "leg", "series", "value_usd", "n_sources", "status",
                    "flags"])
        for v in views:
            for leg, series, values in (("on_demand", v.region.on_demand, v.on_demand),
                                        ("spot", v.region.spot, v.spot),
                                        ("spread", v.region.spread, v.spread)):
                for d in values:
                    w.writerow([d.date, v.region.key, leg, series, d.value, d.n, d.status,
                                d.flags])
    doc: dict[str, Any] = {
        "generated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": ("Values with status 'indicative' predate the spot series (v0.11.0, "
                 "effective 2026-10-06) and are replayed, never published as prints. "
                 "Volatility: standard deviation of daily log changes (spread: of daily "
                 "USD changes), per day and annualised by sqrt(365); never across a gap."),
        "regions": {},
    }
    for v in views:
        doc["regions"][v.region.key] = {
            "series": {"on_demand": v.region.on_demand, "spot": v.region.spot,
                       "spread": v.region.spread},
            "latest": _latest(v),
            "volatility": {leg: {win: {"annualised": x.value, "per_period": x.per_period,
                                       "changes": x.n_changes, "needed": x.needed}
                                 for win, x in wins.items()}
                           for leg, wins in v.vol.items()},
            "sellers": v.sellers, "segments": v.categories, "outside_index": v.outside,
        }
    latest_json = out / "latest.json"
    latest_json.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return [daily_csv, latest_json]


def _last(values: list[spot.DayValue]) -> spot.DayValue | None:
    return values[-1] if values else None


def _latest(v: spot.RegionView) -> dict[str, Any]:
    od, sp, spr = _last(v.on_demand), _last(v.spot), _last(v.spread)
    out: dict[str, Any] = {"date": sp.date if sp else None}
    for name, d in (("on_demand", od), ("spot", sp), ("spread", spr)):
        out[name] = None if d is None else {"value_usd": d.value, "status": d.status,
                                            "flags": d.flags, "n_sources": d.n}
    disc = _discount(od, sp)
    out["discount_pct"] = None if disc is None else round(disc * 100, 2)
    out["breakeven_overhead_pct"] = (None if disc is None or disc >= 1
                                     else round(disc / (1 - disc) * 100, 2))
    return out


def _discount(od: spot.DayValue | None, sp: spot.DayValue | None) -> float | None:
    if od is None or sp is None or od.value is None or sp.value is None or od.value <= 0:
        return None
    return 1 - sp.value / od.value


# --- rendering ---------------------------------------------------------------------------


def render(ctx: Any, views: list[spot.RegionView] | None = None, built: Any = None) -> str:
    from tci.outputs import site as s

    try:
        if built is None:
            from tci.outputs import intraday_page

            try:
                built = intraday_page.build(ctx.conn)
            except Exception:  # noqa: BLE001 - the hourly line is a courtesy, not the page
                log.exception("spot page: intraday replay not built")
                built = None
        views = views if views is not None else build(ctx.conn, built)
        body = _body(s, views, built)
    except Exception:  # noqa: BLE001
        log.exception("site: spot page not built")
        body = _page(s, '<span class="chip chip--warning"><span>Not built</span></span>',
                     '<div class="gapnote">' + s._icon("warn", 14) + "<p>The spot page could "
                     "not be built from the stored prices on this run. The index and every "
                     "other page are unaffected.</p></div>")
    return s._shell(ctx, title=f"Spot — {s.BRAND}", description=DESCRIPTION,
                    current="spot.html", body=body, extra_js=s.REGION_JS)


def _page(s: Any, status: str, inner: str, links: bool = False) -> str:
    meta = ('<span><a href="data/spot/daily.csv">daily.csv</a></span>'
            '<span><a href="data/spot/latest.json">latest.json</a></span>' if links else "")
    return f"""<main class="wrap" id="main">
  <div class="pagehead">
    <div class="eyebrow">Spot</div>
    <h1 class="pagehead__h pagehead__h--display">Spot H100 prices beside on-demand, by
    region.</h1>
    <p class="pagehead__dek">A spot hour is cheaper because the seller may take it back.
    This page prices that trade: the spot series for the EU, the US and every country, the
    spread to on-demand, what each seller charges for the same hour both ways, and how much
    each price moves. The spot series print from 6 October 2026 (methodology v0.11.0,
    notice 2026-N8); earlier values are the same calculation replayed on stored prices and
    are marked indicative.</p>
    <div class="pagehead__meta"><span>{status}</span>{meta}</div>
  </div>
  {inner}
</main>"""


def _status_word(d: spot.DayValue | None, s: Any) -> str:
    if d is None or d.status == "none":
        return "no value"
    if d.value is None:
        return "gap: " + (s._flag_words(d.flags) or "not computed")
    return "indicative" if d.status == "indicative" else "print"


def _tile(s: Any, label: str, value: str, note: str, gap: bool = False) -> str:
    cls = "tile tile--gap" if gap else "tile"
    return (f'<article class="{cls}"><div class="tile__sym">{s._e(label)}</div>'
            f'<div class="tile__body"><div class="tile__num"><span class="tile__val num">'
            f"{value}</span></div></div>"
            f'<div class="tile__foot"><span class="tile__note">{s._e(note)}</span></div>'
            "</article>")


def _tiles(s: Any, v: spot.RegionView) -> str:
    od, sp, spr = _last(v.on_demand), _last(v.spot), _last(v.spread)
    disc = _discount(od, sp)

    def money(d: spot.DayValue | None) -> str:
        return "&#8212;" if d is None or d.value is None else f"${s._num(d.value)}"

    when = s._e(s._human_date(sp.date)) if sp else ""
    tiles = [
        _tile(s, "Spot", money(sp), f"{when}, {_status_word(sp, s)}",
              sp is None or sp.value is None),
        _tile(s, "On-demand", money(od), f"{when}, {_status_word(od, s)}",
              od is None or od.value is None),
        _tile(s, "On-demand minus spot", money(spr), "USD per GPU-hour",
              spr is None or spr.value is None),
        _tile(s, "Spot discount", "&#8212;" if disc is None else f"{s._num(disc * 100, 1)}%",
              "below on-demand", disc is None),
        _tile(s, "Break-even overhead",
              "&#8212;" if disc is None or disc >= 1 else f"{s._num(disc / (1 - disc) * 100, 0)}%",
              "extra GPU-hours an interruption may cost before spot stops being cheaper",
              disc is None),
    ]
    return '<div class="tiles spot-tiles">' + "".join(tiles) + "</div>"


def _lines_chart(s: Any, dates: list[str], lines: list[tuple[str, str, list[float | None]]],
                 *, marker: str | None, unit: str, symbol: str) -> str:
    """Daily lines against one axis, broken at every missing day, labelled at their ends.

    Each line is named in words at its last point and in the legend, so colour is never the
    only thing telling them apart. `marker` draws a rule at the first governed print.
    """
    vals = [v for _l, _c, vs in lines for v in vs if v is not None]
    if not vals or len(dates) < 2:
        return ('<div class="gapnote">' + s._icon("warn", 14) + f"<p>No value for "
                f"<strong>{s._e(symbol)}</strong> in this window.</p></div>")
    lo, hi, step = s._bounds(vals)
    if unit == "$":
        lo = max(lo, 0.0)
    n = len(dates) - 1

    def x(i: int) -> float:
        return round(s.PL + i / n * (s.PR - s.PL), 1)

    def y(v: float) -> float:
        return round(s.PB - (v - lo) / (hi - lo) * (s.PB - s.PT), 1)

    def fmt(v: float) -> str:
        return f"${v:,.2f}" if unit == "$" else f"{v:,.1f}%"

    grid, ticks = [], []
    t = lo
    while t <= hi + step / 2:
        gy = y(t)
        grid.append(f'<line class="ch-grid" x1="{s.PL}" y1="{gy}" x2="{s.PR}" y2="{gy}"/>')
        ticks.append(f'<text class="ch-tick" x="826" y="{gy + 4}">{fmt(t)}</text>')
        t += step
    paths, labels, gaps = [], [], []
    for label, colour, vs in lines:
        run: list[tuple[float, float]] = []
        segs = []
        for i, v in enumerate(vs):
            if v is None:
                if run:
                    segs.append(run)
                run = []
            else:
                run.append((x(i), y(v)))
        if run:
            segs.append(run)
        for seg in segs:
            if len(seg) == 1:
                paths.append(f'<circle cx="{seg[0][0]}" cy="{seg[0][1]}" r="2.6" fill="{colour}"/>')
            else:
                d = "M" + " L".join(f"{a} {b}" for a, b in seg)
                paths.append(f'<path class="ch-line" pathLength="1" style="stroke:{colour}"'
                             f' d="{d}"/>')
        last = next(((i, v) for i, v in reversed(list(enumerate(vs))) if v is not None), None)
        if last:
            lx, ly = x(last[0]), y(last[1])
            labels.append(f'<text class="ch-cur" x="{max(lx - 8, s.PL + 90)}" y="{ly - 10}"'
                          f' text-anchor="end">{s._e(label)} {fmt(last[1])}</text>')
    for i in range(len(dates)):
        if all(vs[i] is None for _l, _c, vs in lines):
            gaps.append(f'<line class="ch-gapmark" x1="{x(i)}" y1="{s.PB - 4}" x2="{x(i)}"'
                        f' y2="{s.PB + 4}"/>')
    rule = ""
    if marker and marker in dates:
        mx = x(dates.index(marker))
        rule = (f'<line class="ch-marker-rule" x1="{mx}" y1="{s.PT}" x2="{mx}" y2="{s.PB}"/>'
                f'<text class="ch-note" x="{mx + 6}" y="{s.PT + 12}">first print</text>')
    # First and last day always labelled, anchored inward so neither is clipped at the
    # plot edge; the ones between are spaced so no two collide.
    xlabels = []
    every = max(1, len(dates) // 6)
    picks = [i for i in range(0, n + 1, every) if i == 0 or n - i >= every] + [n]
    for i in sorted(set(picks)):
        anchor = "start" if i == 0 else ("end" if i == n else "middle")
        xlabels.append(f'<text class="ch-tick" x="{x(i)}" y="288" text-anchor="{anchor}">'
                       f'{s._e(s._human_date(dates[i]).rsplit(" ", 1)[0])}</text>')
    summary = (f"{symbol}: {', '.join(label for label, _c, _v in lines)}, daily from "
               f"{s._human_date(dates[0])} to {s._human_date(dates[-1])}.")
    return ('<div class="scroll-x scroll-x--recent"><svg class="chart" viewBox="0 0 880 300"'
            f' role="img" aria-label="{s._e(summary)}"><g aria-hidden="true">{"".join(grid)}'
            f'<line class="ch-axis" x1="{s.PL}" y1="{s.PB}" x2="{s.PR}" y2="{s.PB}"/>'
            f'{"".join(gaps)}{rule}{"".join(ticks)}{"".join(xlabels)}</g>'
            f'{"".join(paths)}{"".join(labels)}</svg></div>')


def _legend(items: list[tuple[str, str]], note: str = "") -> str:
    sw = "".join(
        f'<span class="spot-legend__i"><svg width="22" height="8" aria-hidden="true"><line'
        f' x1="1" y1="4" x2="21" y2="4" stroke="{c}" stroke-width="2" stroke-linecap="round"/>'
        f"</svg> {label}</span>" for label, c in items)
    return f'<p class="ledger__d spot-legend">{sw}{note}</p>'


def _table(head: list[tuple[str, bool]], rows: list[list[str]], caption: str) -> str:
    from tci.outputs.forward_page import _table as table

    return table(head, rows, caption)


def _cell(s: Any, v: float | None, fmt: str = "$") -> str:
    if v is None:
        return '<td class="ta-r u">&#8212;</td>'
    text = f"${s._num(v)}" if fmt == "$" else f"{s._num(v, 1)}%"
    return f'<td class="ta-r num">{text}</td>'


def _panel(s: Any, v: spot.RegionView, built: Any) -> str:
    r = v.region
    dates = [d.date for d in v.spot]
    first = next((d.date for d in v.spot if d.status == "print"), None)
    start = spot_start(s)
    daily = _lines_chart(
        s, dates, [("On-demand", "var(--chart-line)", [d.value for d in v.on_demand]),
                   ("Spot", "var(--series-1)", [d.value for d in v.spot])],
        marker=first or start, unit="$", symbol=s.display_series(r.spot))
    disc = [None if (a.value is None or b.value is None or a.value <= 0)
            else (1 - b.value / a.value) * 100 for a, b in zip(v.on_demand, v.spot, strict=True)]
    disc_chart = _lines_chart(s, dates, [("Discount", "var(--chart-line)", disc)],
                              marker=first or start, unit="%", symbol="Spot discount")
    ind = any(d.status == "indicative" for d in v.spot)
    note = ('<div class="gapnote">' + s._icon("warn", 14) + "<p><strong>Indicative.</strong> "
            f"{s._e(s.display_series(r.spot))} prints from "
            f"{s._e(s._human_date(start))}. Values before that are the v0.11.0 calculation "
            "replayed on the prices stored that day; they are never stored as prints.</p>"
            "</div>") if ind and start else ""
    hourly = ""
    if built is not None:
        from tci.outputs import intraday_page as ip

        pts = [(sn.at, sn.values[r.spot][0]) for sn in built.snapshots
               if sn.at >= built.now - timedelta(days=HOURLY_DAYS) and r.spot in sn.values]
        if pts:
            hourly = ('<h4 class="rpanel__h">Spot, every hourly read, last 7 days</h4>'
                      + ip._time_chart(s, pts, [], built.now - timedelta(days=HOURLY_DAYS),
                                       built.now, symbol=s.display_series(r.spot),
                                       focusable=False,
                                       indicative=_indicative_times(built, r.spot)))
    rows = [[f'<th scope="row">{s._e(s._human_date(a.date))}</th>', _cell(s, a.value),
             _cell(s, b.value), _cell(s, c.value),
             f"<td>{s._e(_status_word(b, s))}</td>"]
            for a, b, c in reversed(list(zip(v.on_demand, v.spot, v.spread, strict=True)))]
    table = ('<details class="tableview"><summary>Table view</summary>'
             + _table([("Day", False), ("On-demand", True), ("Spot", True), ("Spread", True),
                       ("Spot status", False)], rows,
                      f"{s.display_series(r.spot)}: daily values") + "</details>")
    return (
        f'<div class="rpanel rpanel--{r.key}"><div class="card"><div class="card__body">'
        f'<h3 class="section__h">{s._e(s.display_series(r.spot))} '
        f'<span class="u">&#183; {s._e(r.long)}</span></h3>{note}{_tiles(s, v)}'
        '<h4 class="rpanel__h">Daily, on-demand and spot</h4>' + daily
        + _legend([("On-demand", "var(--chart-line)"), ("Spot", "var(--series-1)")],
                  " &#183; a tick on the axis is a day neither printed")
        + '<h4 class="rpanel__h">Spot discount to on-demand</h4>' + disc_chart
        + hourly + table + "</div></div></div>")


def spot_start(s: Any) -> str | None:
    start = s.series_start("EU-CRI-H100-SPOT")
    return start[0] if start else None


def _vol_panel(s: Any, v: spot.RegionView) -> str:
    def pct(x: spot.Vol | None) -> str:
        if x is None:
            return '<td class="u">&#8212;</td>'
        if x.value is None:
            return f'<td class="u">not enough history ({x.n_changes} of {x.needed})</td>'
        return (f'<td class="ta-r num">{s._num(x.per_period * 100 if x.per_period else 0, 2)}%'
                f' / {s._num(x.value * 100, 0)}%</td>')

    def usd(x: spot.Vol | None) -> str:
        if x is None:
            return '<td class="u">&#8212;</td>'
        if x.value is None:
            return f'<td class="u">not enough history ({x.n_changes} of {x.needed})</td>'
        return f'<td class="ta-r num">${s._num(x.per_period or 0, 3)} / ${s._num(x.value, 2)}</td>'

    rows = []
    for leg, label in (("on_demand", "On-demand"), ("spot", "Spot")):
        w = v.vol.get(leg, {})
        rows.append([f'<th scope="row">{label}</th>', pct(w.get("7d")), pct(w.get("30d")),
                     pct(w.get("hourly"))])
    w = v.vol.get("spread", {})
    rows.append(['<th scope="row">Spread (USD)</th>', usd(w.get("7d")), usd(w.get("30d")),
                 '<td class="u">&#8212;</td>'])
    return (f'<div class="rpanel rpanel--{v.region.key}">'
            + _table([("", False), ("Last 7 days: per day / annualised", True),
                      ("Last 30 days: per day / annualised", True),
                      ("Hourly, last 7 days: per hour / annualised", True)], rows,
                     f"{v.region.long}: realised volatility") + "</div>")


def _seller_panel(s: Any, v: spot.RegionView) -> str:
    rows = []
    for r in v.sellers:
        flag = (f"spot above on-demand in {r['spot_above_on_demand']} of {r['pairs']}"
                if r["spot_above_on_demand"] else "")
        rows.append([f'<th scope="row">{s._e(r["provider"])}</th>',
                     f"<td>{s._e(r['segment'])}</td>", _cell(s, r["on_demand_usd"]),
                     _cell(s, r["spot_usd"]), _cell(s, r["discount_pct"], "%"),
                     f'<td class="ta-r num">{r["pairs"]}</td>', f'<td class="u">{s._e(flag)}</td>'])
    sellers = _table([("Seller", False), ("Segment", False), ("On-demand", True), ("Spot", True),
                      ("Own discount", True), ("Matched pairs", True), ("Note", False)], rows,
                     f"{v.region.long}: each seller's spot and on-demand price")
    cats = [[f'<th scope="row">{s._e(c["segment"])}</th>',
             f'<td class="ta-r num">{c["sellers"]}</td>', _cell(s, c["median_spot_usd"]),
             "<td>in the spot series</td>"] for c in v.categories]
    cats += [[f'<th scope="row">{s._e(o["provider"])}</th>',
              f'<td class="ta-r num">{o["offers"]}</td>', _cell(s, o["median_usd"]),
              f'<td class="u">not in the index: {s._e(o["why"])}</td>'] for o in v.outside]
    categories = _table([("Segment or seller", False), ("Sellers or offers", True),
                         ("Median spot", True), ("", False)], cats,
                        f"{v.region.long}: spot by segment, and prices kept out")
    return (f'<div class="rpanel rpanel--{v.region.key}"><h3 class="section__h">'
            f"{s._e(v.region.long)}</h3>{sellers}{categories}</div>")


def _body(s: Any, views: list[spot.RegionView], built: Any) -> str:
    from tci.outputs.forward_page import _section as section

    if not any(v.spot for v in views):
        return _page(s, '<span class="chip chip--neutral"><span>No spot prices yet</span></span>',
                     '<div class="gapnote">' + s._icon("warn", 14) + "<p>No spot price has "
                     "been stored yet.</p></div>")
    latest = max((v.spot[-1].date for v in views if v.spot), default="")
    status = (f'<span class="chip"><span>Latest day {s._e(s._human_date(latest))}</span>'
              "</span>") if latest else ""
    panels = []
    for v in views:
        try:
            panels.append(_panel(s, v, built))
        except Exception:  # noqa: BLE001 - one region never takes the page with it
            log.exception("spot page: %s panel not built", v.region.key)
            panels.append(f'<div class="rpanel rpanel--{v.region.key}"><div class="gapnote">'
                          + s._icon("warn", 14) + "<p>This region could not be built on this "
                          "run.</p></div></div>")
    main = section(
        "s-spot", "Spot and on-demand, by region",
        "USD per GPU-hour. On-demand is the region's published series; spot is the same "
        "calculation over the prices sellers publish for capacity they may reclaim, from "
        "every segment. The spread is on-demand minus spot. The break-even overhead is how "
        "many extra GPU-hours, as a share of the job, interruptions may cost before spot "
        "stops being cheaper: discount divided by (1 minus discount).",
        '<div class="rtabs">' + s.region_tabs() + "</div>" + "".join(panels))
    vol = section(
        "s-vol", "How much each price moves",
        "Realised volatility for the region chosen above: the standard deviation of "
        "day-over-day log changes, per day and annualised by the square root of 365; the "
        "spread's in dollars, because it can reach zero. A change is counted only between "
        "two consecutive days that both have a value, so a gap is never bridged. With fewer "
        "changes than the window needs it says so instead of printing a number. Indicative "
        "days count; the table says how many changes each figure rests on.",
        "".join(_vol_panel(s, v) for v in views))
    sell = section(
        "s-sellers", "Each seller's own discount",
        "For the region chosen above: what each seller charges for the same hour on spot and "
        "on-demand, matched by region and node size, and the median of those matched "
        "discounts. The index spread compares two markets; this compares one seller's two "
        "products. Where a seller's catalogue lists spot above on-demand, the row says so. "
        "Then the spot prices by segment, and the stored interruptible prices the series "
        "leaves out, as a reference.",
        "".join(_seller_panel(s, v) for v in views))
    return _page(s, status, main + vol + sell, links=True)
