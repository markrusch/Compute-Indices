# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Pages and sections that describe the market around the prints, not the prints themselves.

- availability.html: why the headline moved, whose price it landed on, and what stock the
  sellers that publish it reported (tci.explain, tci.availability).
- running-cost.html: rent against the cash cost of keeping a GPU on, per generation
  (tci.costfloor).
- sections spliced into existing pages: the make-up of the EU-US basis (basis.html), the
  EU term panel (term.html), market-price statements (data.html), and one line on the
  dashboard's print card saying who set the price and why it moved.

All of it is research beside the index, built from stored rows by observers that write
nothing to the database. Each builder is called inside a guard in site.py, so a failure
here costs the section or the page and never the prints or the rest of the site. Data
files are written beside the pages they describe, in the same call, so the two always
describe the same build.
"""

from __future__ import annotations

import csv
import json
import logging
import sqlite3
from collections import Counter
from datetime import date as date_type
from datetime import timedelta
from pathlib import Path
from typing import Any

from tci import availability, costfloor, explain, panel, statement

log = logging.getLogger("tci.outputs.market_pages")

HEADLINE = "EU-CRI-H100"
US_SERIES = "EU-CRI-H100-US"
BASIS = "EU-CRI-H100-BASIS-US"
WINDOW_DAYS = 60
STEP_WORDS = {
    "methodology": "a methodology change",
    "exits": "sellers leaving the book",
    "price": "price changes by sellers present on both days",
    "entries": "sellers joining the book",
}

_CACHE: dict[tuple[Any, ...], Any] = {}


def _site() -> Any:
    from tci.outputs import site

    return site


def _data_dir() -> Path:
    return _site().SITE_DIR / "data"


def _stamp(conn: sqlite3.Connection) -> tuple[Any, ...]:
    """Identifies the database state, so a cached observer result is never served stale."""
    name = conn.execute("PRAGMA database_list").fetchone()[2] or f"mem:{id(conn)}"
    prints = conn.execute("SELECT MAX(rowid) FROM daily_index").fetchone()[0]
    obs = conn.execute("SELECT MAX(id) FROM observations").fetchone()[0]
    return (name, prints, obs)


def _cached(key: tuple[Any, ...], fn: Any) -> Any:
    if key not in _CACHE:
        _CACHE[key] = fn()
    return _CACHE[key]


def _window(end: str, days: int = WINDOW_DAYS) -> list[str]:
    e = date_type.fromisoformat(end)
    return [(e - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]


def move(conn: sqlite3.Connection, series: str, date: str) -> explain.Move | None:
    return _cached(("move", _stamp(conn), series, date),
                   lambda: explain.explain_move(conn, series, date))


def moves(conn: sqlite3.Connection, series: str, end: str) -> list[explain.Move]:
    out = []
    for d in _window(end):
        m = move(conn, series, d)
        if m is not None:
            out.append(m)
    return out


def setters(conn: sqlite3.Connection, series: str, end: str) -> dict[str, tuple[str, ...]]:
    return _cached(("setters", _stamp(conn), series, end),
                   lambda: explain.setter_history(conn, series, _window(end)))


# -------------------------------------------------------------------------- words


def _money(x: float | None, signed: bool = False) -> str:
    site = _site()
    if x is None:
        return "&#8212;"
    if not signed:
        return f"${site._num(x)}"
    sign = "+" if x > 0 else "&#8722;" if x < 0 else ""
    return f"{sign}${site._num(abs(x))}"


def _names(providers: tuple[str, ...] | list[str]) -> str:
    items = [str(p) for p in providers]
    if not items:
        return "none"
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def cause(m: explain.Move) -> str:
    """The step that moved the print most, in words. Empty when nothing moved."""
    if abs(m.total) < 1e-9:
        return ""
    if not m.split:
        return "the steps cannot be separated: an intermediate book fell below the gate"
    big = max((s for s in m.steps if s.delta is not None), key=lambda s: abs(s.delta or 0.0))
    return STEP_WORDS[big.name]


def why_line(ctx: Any) -> str:
    """One line on the dashboard print card: who set the print and what moved it."""
    site = _site()
    if ctx.head is None or ctx.head["value_usd"] is None:
        return ""
    try:
        m = move(ctx.conn, HEADLINE, ctx.date)
    except Exception:  # noqa: BLE001 - an observer must never cost the print card
        log.exception("print card: move not explained")
        return ""
    if m is None:
        return ""
    who = f"Set by {site._e(_names(m.setters))}."
    since = site._e(site._human_date(m.prev_date))
    if abs(m.total) < 1e-9:
        what = " Unchanged."
    elif not m.split:
        what = (f" {_money(m.total, signed=True)} since {since}; this move cannot be split"
                " into steps.")
    else:
        what = f" {_money(m.total, signed=True)} since {since}, mostly from {site._e(cause(m))}."
    return (f'<div class="print__row"><span class="print__vs">{who}{what} '
            '<a href="availability.html">Why it moved</a></span></div>')


# -------------------------------------------------------------------- data files


def _write_csv(path: Path, header: list[str], rows: list[list[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n",
                    encoding="utf-8", newline="\n")


def _move_row(m: explain.Move) -> list[Any]:
    steps = {s.name: s.delta for s in m.steps}
    return [m.date, m.series, m.value, m.prev_date, m.prev_value, m.total,
            steps["methodology"], steps["exits"], steps["price"], steps["entries"],
            " ".join(m.exited), " ".join(m.entered),
            " ".join(f"{p}:{a:.4f}>{b:.4f}" for p, a, b in m.repriced), " ".join(m.setters)]


MOVE_HEADER = ["date", "series", "value_usd", "prev_date", "prev_value_usd", "move_usd",
               "methodology_usd", "exits_usd", "price_usd", "entries_usd", "exited",
               "entered", "repriced", "set_by"]
STOCK_HEADER = ["date", "gpu_model", "sellers", "publish_stock", "stock_recorded", "stock_2plus",
                "stock_8", "vast_offers", "vast_gpus", "vast_largest", "runpod_2plus",
                "runpod_8", "runpod_eu_datacentres", "hyperstack_countries"]


def _stock_row(r: availability.Reading) -> list[Any]:
    def blank(x: Any) -> Any:
        return "" if x is None else x

    return [r.date, r.gpu_model, len(r.sellers), r.publishers, r.publishing, r.with_stock,
            r.with_8gpu_stock, blank(r.vast_offers), blank(r.vast_gpus), blank(r.vast_largest),
            r.runpod_node2 or "", r.runpod_node8 or "",
            "" if r.runpod_eu_datacentres is None else r.runpod_eu_datacentres,
            " ".join(r.hyperstack_countries)]


def readings(conn: sqlite3.Connection, end: str) -> list[availability.Reading]:
    def build() -> list[availability.Reading]:
        dates = [d for d in availability.collection_dates(conn) if d <= end]
        since = _window(end)[0]
        out = [availability.reading(conn, d) for d in dates if d >= since]
        # Sessions before any stock was recorded say nothing and are left out. RunPod's
        # all-regions label counts as a record even before its datacentre list was stored.
        return [r for r in out if r.publishing or r.runpod_node2 is not None]

    return _cached(("stock", _stamp(conn), end), build)


def write_availability_data(ctx: Any) -> None:
    ms = moves(ctx.conn, HEADLINE, ctx.date)
    d = _data_dir()
    _write_csv(d / "moves" / "history.csv", MOVE_HEADER, [_move_row(m) for m in ms])
    latest = ms[-1] if ms and ms[-1].date == ctx.date else None
    _write_json(d / "moves" / "latest.json", {
        "series": HEADLINE, "date": ctx.date,
        "order": ["methodology", "exits", "price", "entries"],
        "move": None if latest is None else dict(zip(MOVE_HEADER, _move_row(latest), strict=True)),
    })
    rs = readings(ctx.conn, ctx.date)
    _write_csv(d / "availability" / "history.csv", STOCK_HEADER, [_stock_row(r) for r in rs])
    today = rs[-1] if rs and rs[-1].date == ctx.date else None
    _write_json(d / "availability" / "latest.json", {
        "date": ctx.date, "gpu_model": "H100_SXM", "block": "EU_EEA",
        "sellers": [] if today is None else [s.__dict__ for s in today.sellers],
        "note": "What each seller published about its own stock. Not utilisation.",
    })


# ------------------------------------------------------------------ availability.html


def _steps_table(m: explain.Move) -> str:
    # The amount sits beside the step name, before the description, so a 390px screen
    # shows it without scrolling the table sideways.
    site = _site()
    rows = "".join(
        f'<tr><th scope="row" class="ta-l">{i}. {site._e(s.name.capitalize())}</th>'
        f'<td class="ta-r num">{_money(s.delta, signed=True)}</td>'
        f'<td>{site._e(STEP_WORDS[s.name])}</td></tr>'
        for i, s in enumerate(m.steps, 1)
    )
    return f"""<div class="card card__body--flush"><div class="scroll-x">
  <table class="grid grid--narrow">
  <caption class="vh">The move split into four steps, in order</caption>
  <thead><tr><th scope="col">Step</th><th scope="col" class="ta-r">USD/GPU&#8209;hr</th>
  <th scope="col">What it prices</th></tr></thead>
  <tbody>{rows}<tr><th scope="row" class="ta-l">Total</th>
  <td class="ta-r num strong">{_money(m.total, signed=True)}</td><td>published move</td></tr>
  </tbody></table></div></div>"""


# One company under two provider keys over time. v0.5.0 replaced the static `datacrunch`
# entry with the `verda` feed (factors.yaml: the same company, renamed), so counting the
# keys apart split one seller's sessions in two.
SAME_COMPANY = {"datacrunch": "verda"}


def _who_set(conn: sqlite3.Connection, end: str) -> tuple[str, int, list[tuple[str, int]]]:
    hist = setters(conn, HEADLINE, end)
    counts: Counter[str] = Counter()
    for names in hist.values():
        for n in {SAME_COMPANY.get(x, x) for x in names}:
            counts[n] += 1
    return f"{len(hist)}", len(hist), counts.most_common()


def _runpod_placement_finding(rs: list[availability.Reading], conn: sqlite3.Connection) -> str:
    """Sessions in which RunPod's price entered the headline with no EU/EEA stock reported."""
    site = _site()
    from tci.series_read import constituents_for

    entered, without = 0, []
    for r in rs:
        if r.runpod_eu_datacentres is None:
            continue
        inc = any(c["provider"] == "runpod" and c["included"]
                  for c in constituents_for(conn, HEADLINE, r.date))
        if inc:
            entered += 1
            if r.runpod_eu_datacentres == 0:
                without.append(r.date)
    if not entered:
        return ""
    dates = ", ".join(site._e(site._human_date(d)) for d in without) or "none"
    return (f"<p>RunPod publishes one price for all regions. Its EU/EEA row is recorded "
            f"against Romania by convention, while its US row is recorded only on a day a "
            f"US datacentre reports stock. Of the {entered} sessions in this window in which "
            f"RunPod's price entered the headline, {len(without)} had no EU/EEA datacentre "
            f"reporting H100 stock: {dates}.</p>")


def _previous(conn: sqlite3.Connection, date: str) -> tuple[str, float] | None:
    from tci.series_read import previous_published

    return previous_published(conn, HEADLINE, date)


def availability_page(ctx: Any) -> str:
    site = _site()
    e, hd = site._e, site._human_date
    write_availability_data(ctx)
    ms = moves(ctx.conn, HEADLINE, ctx.date)
    today = ms[-1] if ms and ms[-1].date == ctx.date else None
    rs = readings(ctx.conn, ctx.date)
    reading = rs[-1] if rs and rs[-1].date == ctx.date else None
    n_set, n_hist, counts = _who_set(ctx.conn, ctx.date)

    if today is not None:
        left = f"Left the book: {e(_names(today.exited))}." if today.exited else ""
        joined = f" Joined: {e(_names(today.entered))}." if today.entered else ""
        repriced = "".join(
            f"<li>{e(p)}: {_money(a)} to {_money(b)}/GPU&#8209;hr</li>"
            for p, a, b in today.repriced
        )
        rep = f'<div class="md"><ul>{repriced}</ul></div>' if repriced else ""
        now = f"""<p class="section__dek">{e(hd(today.date))}: {_money(today.value)}/GPU&#8209;hr
        against {_money(today.prev_value)} on {e(hd(today.prev_date))}. Set by
        {e(_names(today.setters))}. {left}{joined}</p>
        {_steps_table(today)}
        {('<p class="section__dek">Sellers on both days whose audit price changed:</p>' + rep)
         if rep else ''}"""
    elif ctx.head is None or ctx.head["value_usd"] is None:
        now = ('<p class="section__dek">The headline did not publish this session, so there '
               "is no move to explain. A gap is not a move.</p>")
    elif _previous(ctx.conn, ctx.date) is None:
        now = ('<p class="section__dek">This is the first published print, so there is no '
               "earlier print to move from.</p>")
    else:
        now = ('<p class="section__dek">This session&#8217;s move is not split: the stored '
               "print or the one before it does not recompute from stored rows under the "
               'current code. <code class="inline">python -m tci.run reproduce</code> lists '
               "it.</p>")

    set_rows = "".join(
        f'<tr><td>{e(p)}</td><td class="ta-r num">{c}</td>'
        f'<td class="ta-r num">{site._num(c / n_hist * 100, 0)}%</td></tr>'
        for p, c in counts
    )
    move_rows = "".join(
        f'<tr><td class="num">{e(m.date)}</td><td class="ta-r num">{_money(m.value)}</td>'
        f'<td class="ta-r num">{_money(m.total, signed=True)}</td>'
        + "".join(f'<td class="ta-r num">{_money(s.delta, signed=True)}</td>' for s in m.steps)
        + f"<td>{e(_names(m.exited)) if m.exited else ''}</td>"
        f"<td>{e(_names(m.entered)) if m.entered else ''}</td>"
        f"<td>{e(_names(m.setters))}</td></tr>"
        for m in reversed(ms) if abs(m.total) > 1e-9 or m.date == ctx.date
    )
    if reading is not None:
        stock_rows = "".join(
            f"<tr><td>{e(s.provider)}</td>"
            f"<td>{'yes' if s.publishes_stock else 'no'}</td>"
            f"<td>{e(s.node2) if s.node2 else '&#8212;'}</td>"
            f"<td>{e(s.node8) if s.node8 else '&#8212;'}</td>"
            f'<td class="u">{e(s.detail)}</td></tr>'
            for s in reading.sellers
        )
        stock_now = f"""<p class="section__dek">{e(hd(reading.date))}: {reading.publishers} of
        {len(reading.sellers)} sellers that price the H100 in the EU/EEA publish anything
        about stock. {reading.with_stock} of them showed stock at the headline's node floor of
        two GPUs, {reading.with_8gpu_stock} at a full 8-GPU node. The other
        {len(reading.sellers) - reading.publishers} publish a price and nothing about whether
        it can be bought.</p>
        <div class="card card__body--flush"><div class="scroll-x">
        <table class="grid">
        <caption class="vh">Stock each seller published</caption>
        <thead><tr><th scope="col">Seller</th><th scope="col">Publishes stock</th>
        <th scope="col">2+ GPUs</th><th scope="col">8 GPUs</th><th scope="col">What it
        published</th></tr></thead><tbody>{stock_rows}</tbody></table></div></div>"""
    else:
        stock_now = '<p class="section__dek">No stock reading was stored this session.</p>'
    hist_rows = "".join(
        f'<tr><td class="num">{e(r.date)}</td><td class="ta-r num">{r.publishing}</td>'
        f'<td class="ta-r num">{r.with_stock}</td><td class="ta-r num">{r.with_8gpu_stock}</td>'
        f"<td>{e(r.runpod_node2 or 'not recorded')}</td>"
        f"<td>{e(r.runpod_node8 or 'not recorded')}</td>"
        f'<td class="ta-r num">'
        f'{"&#8212;" if r.runpod_eu_datacentres is None else r.runpod_eu_datacentres}</td>'
        f'<td class="ta-r num">{"&#8212;" if r.vast_gpus is None else r.vast_gpus}</td></tr>'
        for r in reversed(rs)
    )
    finding = _runpod_placement_finding(rs, ctx.conn)

    body = f"""<main class="wrap" id="main">
  <div class="pagehead">
    <div class="eyebrow">Research beside the index</div>
    <h1 class="pagehead__h pagehead__h--display">Why the price moved, and whether it could
    be bought.</h1>
    <p class="pagehead__dek">A weighted median moves when a seller changes a price, but also
    when a seller drops out of the book or joins it, and on most sessions it lands exactly
    on one seller's posted price. This page shows which of those happened, and what the
    sellers that publish stock said about it. None of it changes a print.</p>
    <div class="pagehead__meta"><span><a href="data/moves/history.csv">moves/history.csv</a></span>
      <span><a href="data/availability/history.csv">availability/history.csv</a></span>
      <span><a href="methodology.html">Methodology</a></span></div>
  </div>

  <section class="section" aria-labelledby="s-move">
    <div class="section__head"><div>
      <h2 class="section__h" id="s-move">Today's move</h2></div></div>
    {now}
    <div class="fnstrip"><p style="margin:0;font-size:var(--text-xs);color:var(--ink-2);
      line-height:var(--leading-prose)">The split is a path taken in a fixed order, every
      step priced under the rules live on the later date: the earlier book recomputed under
      today's rules, then without the sellers absent today, then today's offers from the
      sellers present on both days, then today's full book. The four steps sum to the
      published move exactly. Each is rounded to the cent on its own here, so the cents
      shown may not add up to the total;
      <a href="data/moves/history.csv">moves/history.csv</a> carries them to six decimals.
      Another order would split it differently with the same total, and a weighted median
      has no order-free split.</p></div>
  </section>

  <section class="section" aria-labelledby="s-set">
    <div class="section__head"><div>
      <h2 class="section__h" id="s-set">Whose price the headline landed on</h2>
      <p class="section__dek">Over the {n_set} published sessions in the last
      {WINDOW_DAYS} days, the seller with an offer priced exactly at the print. Two sellers
      posting the same price both count. DataCrunch's sessions before it was renamed Verda
      count under Verda.</p></div></div>
    <div class="card card__body--flush"><div class="scroll-x">
    <table class="grid grid--narrow"><caption class="vh">Sellers that set the headline</caption>
    <thead><tr><th scope="col">Seller</th><th scope="col" class="ta-r">Sessions</th>
    <th scope="col" class="ta-r">Share</th></tr></thead><tbody>{set_rows}</tbody></table>
    </div></div>
  </section>

  <section class="section" aria-labelledby="s-stock">
    <div class="section__head"><div>
      <h2 class="section__h" id="s-stock">What sellers published about stock</h2></div></div>
    {stock_now}
    <div class="md">{finding}<p>RunPod and vast.ai leave the book on a day they have no
    qualifying stock, which moves the median. The sellers that publish only a rate card stay
    in it whether or not anything is for sale. A stock level is the seller's own label and
    is not utilisation.</p></div>
  </section>

  <section class="section" aria-labelledby="s-hist">
    <div class="section__head"><div>
      <h2 class="section__h" id="s-hist">Moves, last {WINDOW_DAYS} days</h2>
      <p class="section__dek">Sessions in which the headline changed, and today. Gaps are not
      moves and are left out; a move is measured from the last published print.</p></div></div>
    <div class="card card__body--flush"><div class="scroll-x">
    <table class="grid"><caption class="vh">Headline moves split into four steps</caption>
    <thead><tr><th scope="col">Date</th><th scope="col" class="ta-r">Print</th>
    <th scope="col" class="ta-r">Move</th><th scope="col" class="ta-r">Method</th>
    <th scope="col" class="ta-r">Exits</th><th scope="col" class="ta-r">Price</th>
    <th scope="col" class="ta-r">Entries</th><th scope="col">Left</th>
    <th scope="col">Joined</th><th scope="col">Set by</th></tr></thead>
    <tbody>{move_rows}</tbody></table></div></div>
  </section>

  <section class="section" aria-labelledby="s-stockhist">
    <div class="section__head"><div>
      <h2 class="section__h" id="s-stockhist">Stock, last {WINDOW_DAYS} days</h2></div></div>
    <div class="card card__body--flush"><div class="scroll-x">
    <table class="grid"><caption class="vh">Stock readings by session</caption>
    <thead><tr><th scope="col">Date</th><th scope="col" class="ta-r">Recorded</th>
    <th scope="col" class="ta-r">Stock at 2+</th><th scope="col" class="ta-r">Stock at 8</th>
    <th scope="col">RunPod 2+, all regions</th><th scope="col">RunPod 8, all regions</th>
    <th scope="col" class="ta-r">RunPod EU/EEA datacentres</th>
    <th scope="col" class="ta-r">vast.ai EU GPUs</th></tr></thead>
    <tbody>{hist_rows}</tbody></table></div></div>
  </section>
</main>"""
    return site._shell(
        ctx,
        title=f"Why the price moved — {site.BRAND}",
        description=(
            "Why the EU/EEA H100 headline moved each session, which seller's price it landed "
            "on, and what sellers published about stock."
        ),
        current="index.html",
        canonical="availability.html",
        body=body,
    )


# ------------------------------------------------------------------ running-cost.html


def write_cost_data(ctx: Any, table: costfloor.Table, history: list[costfloor.Table]) -> None:
    d = _data_dir() / "costs"
    _write_json(d / "latest.json", costfloor.as_json(table))
    rows = []
    for t in history:
        for r in t.rows:
            eu, world = r.ratio(r.eu), r.ratio(r.world)
            rows.append([t.date, r.cls, r.cost.low, r.cost.high, r.eu.eur, r.eu.sellers,
                         eu.low if eu else "", eu.high if eu else "", r.world.eur,
                         r.world.sellers, world.low if world else "",
                         world.high if world else "", t.power.eur_mwh, t.power.days])
    _write_csv(d / "history.csv",
               ["date", "class", "cost_low_eur", "cost_high_eur", "rent_eu_eur", "sellers_eu",
                "ratio_eu_low", "ratio_eu_high", "rent_world_eur", "sellers_world",
                "ratio_world_low", "ratio_world_high", "power_eur_mwh", "power_days"], rows)


def running_cost_page(ctx: Any) -> str:
    site = _site()
    e, num = site._e, site._num
    cfg = costfloor.load_inputs()
    t = costfloor.table(ctx.conn, ctx.date, cfg)
    if t is None:
        return ""
    history = [x for x in (costfloor.table(ctx.conn, d, cfg) for d in _window(ctx.date, 30)
                           if d <= ctx.date) if x is not None and any(r.eu.sellers for r in x.rows)]
    write_cost_data(ctx, t, history)

    def rng(r: costfloor.Range | None, dp: int = 2, unit: str = "") -> str:
        if r is None:
            return '<span class="u">fewer than three sellers</span>'
        return f"{num(r.low, dp)}{unit}&#8211;{num(r.high, dp)}{unit}"

    def eur(x: float | None) -> str:
        return ("&#8364;" + num(x)) if x is not None else "&#8212;"

    rows = "".join(
        f"<tr><th scope=\"row\" class=\"ta-l\">{e(r.cls)}</th>"
        f'<td class="ta-r num">{num(r.kw, 3)}</td>'
        f'<td class="ta-r num">&#8364;{rng(r.cost)}</td>'
        f'<td class="ta-r num">&#8364;{rng(r.power)}</td>'
        f'<td class="ta-r num">{eur(r.eu.eur)}'
        f' <span class="u">({r.eu.sellers})</span></td>'
        f'<td class="ta-r num strong">{rng(r.ratio(r.eu), 1, "&#215;")}</td>'
        f'<td class="ta-r num">{eur(r.world.eur)}'
        f' <span class="u">({r.world.sellers})</span></td>'
        f'<td class="ta-r num">{rng(r.ratio(r.world), 1, "&#215;")}</td></tr>'
        for r in t.rows
    )
    if t.power.days:
        power_line = (f"Power is the mean day-ahead price across {e(', '.join(t.power.zones))} "
                      f"over the last {t.power.days} stored days, &#8364;{num(t.power.eur_mwh)}"
                      f"/MWh. Day-ahead prices: {e(costfloor.attribution(t.power))}.")
    else:
        power_line = (f"Power is &#8364;{num(t.power.eur_mwh)}/MWh, the German 2025 day-ahead "
                      "average published by the Bundesnetzagentur, until enough daily prices "
                      "are stored to use their mean.")
    src = cfg
    sources = "".join(
        f'<li><a href="{e(u)}" rel="noopener">{e(label)}</a></li>'
        for label, u in (
            ("NVIDIA DGX A100 system power", src["it_kw_per_gpu"]["A100"]["url"]),
            ("NVIDIA DGX H100/H200 system power", src["it_kw_per_gpu"]["H100"]["url"]),
            ("AMAX: DGX B200 system power", src["it_kw_per_gpu"]["B200"]["url"]),
            ("PUE 1.20: German Energy Efficiency Act target", src["pue"]["low"]["url"]),
            ("PUE 1.54: Uptime Institute 2025 average", src["pue"]["high"]["url"]),
            ("Frankfurt colocation \u20ac165\u2013185/kW/month",
             src["colocation_eur_kw_month"]["url"]),
            ("Operating lines: Cloudrift GPU cost model", src["other_usd_per_gpu_hour"]["url"]),
            ("German 2025 day-ahead average", src["power"]["fallback_eur_mwh"]["url"]),
        )
    )
    body = f"""<main class="wrap" id="main">
  <div class="pagehead">
    <div class="eyebrow">Research beside the index</div>
    <h1 class="pagehead__h pagehead__h--display">Rent against running cost.</h1>
    <p class="pagehead__dek">A GPU stays switched on while its rent covers the cash it takes
    to run: power, colocation and a few operating lines. Depreciation and financing are left
    out, because once the hardware is bought they no longer decide whether to keep it on. The
    ratio below is how far each generation's rent sits above that floor on
    {e(site._human_date(t.date))}. A ratio near one is the end of a GPU's economic life,
    whatever its book life says.</p>
    <div class="pagehead__meta"><span><a href="data/costs/latest.json">costs/latest.json</a></span>
      <span><a href="data/costs/history.csv">costs/history.csv</a></span>
      <span>Inputs <span class="num">v{e(t.config_version)}</span></span></div>
  </div>
  <section class="section" aria-labelledby="s-table">
    <div class="section__head"><div>
      <h2 class="section__h" id="s-table">Per GPU-hour, EUR</h2>
      <p class="section__dek">Rent is the on-demand price the index already collects, under
      the headline population's rules: one price per seller, its lowest qualifying offer, and
      the median across sellers, shown only with three or more sellers. The count is in
      brackets. Every input is a range, so every cost and ratio is a range.</p></div></div>
    <div class="card card__body--flush"><div class="scroll-x">
    <table class="grid"><caption class="vh">Rent against running cost by GPU</caption>
    <thead><tr><th scope="col">GPU</th><th scope="col" class="ta-r">IT kW</th>
    <th scope="col" class="ta-r">Running cost</th><th scope="col" class="ta-r">of which power</th>
    <th scope="col" class="ta-r">EU/EEA rent</th><th scope="col" class="ta-r">Rent / cost</th>
    <th scope="col" class="ta-r">All regions rent</th><th scope="col" class="ta-r">Rent / cost</th>
    </tr></thead><tbody>{rows}</tbody></table></div></div>
    <p class="section__dek">{power_line} EUR at the ECB rate the day's prints recorded,
    {num(t.fx, 4)} USD per EUR.</p>
  </section>
  <section class="section" aria-labelledby="s-what">
    <div class="section__head"><div>
      <h2 class="section__h" id="s-what">What this is not</h2></div></div>
    <div class="md"><ul>
      <li>Not a cost of ownership. Capital recovery is most of the full cost of a GPU-hour
      and is excluded on purpose.</li>
      <li>Power is the wholesale day-ahead price, without grid fees or levies, so the power
      line is a lower bound on what a site pays.</li>
      <li>IT power is the vendor's system maximum divided by eight, which overstates average
      draw. The operating lines come from a US colocation model; no public source gives a
      European staff cost per GPU-hour, and none is added.</li>
      <li>The rent is a posted on-demand price for small blocks. A GPU under a multi-year
      contract earns that contract's rate, which is not published.</li>
    </ul></div>
  </section>
  <section class="section" aria-labelledby="s-src">
    <div class="section__head"><div>
      <h2 class="section__h" id="s-src">Inputs</h2>
      <p class="section__dek">Every figure is in <code class="inline">config/cost_floor.yaml</code>
      with its source and the date it was read.</p></div></div>
    <div class="md"><ul>{sources}</ul></div>
  </section>
</main>"""
    return site._shell(
        ctx,
        title=f"Rent against running cost — {site.BRAND}",
        description=(
            "How far GPU rent sits above the cash cost of running the GPU, per generation, "
            "from TCI's on-demand prices and sourced power and colocation costs."
        ),
        current="research.html",
        canonical="running-cost.html",
        body=body,
    )


# ------------------------------------------------------------- basis.html section


def basis_makeup_section(ctx: Any) -> str:
    """The like-for-like spread and whose price set each leg, today and since the start."""
    site = _site()
    e, hd = site._e, site._human_date
    conn = ctx.conn
    first = conn.execute(
        "SELECT MIN(date) FROM daily_index WHERE series = ? AND value_usd IS NOT NULL", (BASIS,)
    ).fetchone()[0]
    if first is None:
        return ""
    dates = [d for d in _window(ctx.date, 400) if d >= first]
    made = _cached(("basis", _stamp(conn), ctx.date), lambda: [
        m for m in (explain.basis_makeup(conn, d, BASIS, HEADLINE, US_SERIES) for d in dates)
        if m is not None and m.basis is not None
    ])
    _write_csv(_data_dir() / "basis" / "makeup.csv",
               ["date", "basis_usd", "sellers_in_both", "identical_price", "like_for_like_usd",
                "eu_set_by", "us_set_by", "same_seller_set_both"],
               [[m.date, m.basis, len(m.both), m.identical, m.like_for_like,
                 " ".join(m.lead_setters), " ".join(m.reference_setters), int(m.shared_setter)]
                for m in made])
    if not made:
        return ""
    shared = sum(1 for m in made if m.shared_setter)
    flat = sum(1 for m in made if m.both and m.identical == len(m.both))
    last = made[-1]
    rows = "".join(
        f'<tr><td class="num">{e(m.date)}</td><td class="ta-r num">{_money(m.basis, True)}</td>'
        f'<td class="ta-r num">{_money(m.like_for_like, True)}</td>'
        f'<td class="ta-r num">{m.identical} of {len(m.both)}</td>'
        f"<td>{e(_names(m.lead_setters))}</td><td>{e(_names(m.reference_setters))}</td></tr>"
        for m in reversed(made)
    )
    today = ""
    if last.date == ctx.date:
        both_legs = sorted(set(last.lead_setters) & set(last.reference_setters))
        verdict = (f" {e(_names(both_legs))} set both legs at one price, so the basis is zero "
                   "by construction." if last.shared_setter and abs(last.basis or 0) < 1e-9
                   else "")
        today = (f"<p>{e(hd(last.date))}: the EU/EEA leg was set by "
                 f"{e(_names(last.lead_setters))} and the US leg by "
                 f"{e(_names(last.reference_setters))}.{verdict}</p>")
    return f"""<div class="md">{today}<p>On {shared} of the {len(made)} sessions the basis
    has published, the same seller had an offer exactly at both legs&#8217; prints. On {flat}
    of them every seller present
    in both legs charged the same price on both sides of the Atlantic, so the like-for-like
    spread was zero and the whole basis came from which sellers each leg contained. The
    like-for-like spread is the median, over sellers in both legs, of what each charged in
    the EU/EEA minus what it charged in the US.</p></div>
    <div class="card card__body--flush"><div class="scroll-x">
    <table class="grid"><caption class="vh">What each basis print was made of</caption>
    <thead><tr><th scope="col">Date</th><th scope="col" class="ta-r">Basis</th>
    <th scope="col" class="ta-r">Like for like</th>
    <th scope="col" class="ta-r">Same price both sides</th>
    <th scope="col">EU/EEA set by</th><th scope="col">US set by</th></tr></thead>
    <tbody>{rows}</tbody></table></div></div>
    <p class="section__dek"><a href="data/basis/makeup.csv">basis/makeup.csv</a></p>"""


# --------------------------------------------------------------- term.html section


def panel_section() -> str:
    site = _site()
    e = site._e
    path = _data_dir() / "term" / "panel.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = panel.empty()
    link = f"{site.REPO_URL}/blob/main/TERM-PANEL.md"
    cfg = panel.load_config()
    rows = []
    contracts = "".join(f"<li><strong>{e(c.id)}</strong>: {e(c.description)}</li>"
                        for c in cfg.cells)
    for c in data.get("cells", []):
        if c.get("published"):
            value = (f"${site._num(c['value_usd'])}"
                     + (f' <span class="u">&#8364;{site._num(c["value_eur"])}</span>'
                        if c.get("value_eur") is not None else ""))
        else:
            value = f'<span class="u">{e(c.get("reason") or "not published")}</span>'
        spec = cfg.cell(c["id"])
        short = (f"{spec.gpu_model.replace('_', ' ')}, {spec.tenor_months} months, "
                 f"{spec.gpus[0]}&#8211;{spec.gpus[1]:,} GPUs" if spec else e(c["id"]))
        rows.append(f"<tr><td class=\"num\">{e(c['id'])}</td><td>{short}</td>"
                    f"<td class=\"ta-r num\">{e(c.get('panellists', 0))}</td><td>{value}</td></tr>")
    window = data.get("window")
    when = f"Window {e(window)}." if window else "No window has been assessed."
    return f"""<div class="md"><p>Three standard 12-month contracts, assessed each week by
    brokers, marketplaces, buyers and sellers under the rules in
    <a href="{e(link)}">TERM-PANEL.md</a>. A value needs three panellists, sellers fewer than
    half of them, and every assessment inside the week's window; from five, the highest and
    lowest are dropped. Panel values are judgements and quotes from named market
    participants, recorded privately, and cannot be recomputed from public data.</p></div>
    <div class="md"><ul>{contracts}</ul></div>
    <p class="section__dek">{when}</p>
    <div class="card card__body--flush"><div class="scroll-x">
    <table class="grid grid--narrow"><caption class="vh">EU term panel cells</caption>
    <thead><tr><th scope="col">Cell</th><th scope="col">Contract</th>
    <th scope="col" class="ta-r">Panellists</th><th scope="col">Per GPU-hour</th></tr></thead>
    <tbody>{"".join(rows)}</tbody></table></div></div>"""


# --------------------------------------------------------------- data.html section


def write_statements(conn: sqlite3.Connection, through: str) -> list[Path]:
    return statement.write_monthly(conn, _data_dir() / "statements", through)


_STATEMENT_COMMANDS = (
    "python -m tci.run statement build --series EU-CRI-H100 \\\n"
    "    --from 2026-09-01 --to 2026-09-30 --out sept.json\n"
    "python -m tci.run statement verify --file sept.json"
)


def statements_section(ctx: Any) -> str:
    site = _site()
    e = site._e
    path = _data_dir() / "statements" / "index.json"
    try:
        items = json.loads(path.read_text(encoding="utf-8")).get("statements", [])
    except (OSError, ValueError):
        items = []
    def mean_cell(x: float | None) -> str:
        return ("&#8364;" + site._num(x, 2)) if x is not None else "&#8212;"

    rows = "".join(
        f"<tr><td>{e(site.display_series(s['series']))}</td>"
        f'<td class="num">{e(s["month"])}</td>'
        f'<td class="ta-r num">{e(s["published"])} of {e(s["calendar_days"])}</td>'
        f'<td class="ta-r num">{mean_cell(s["mean_eur"])}</td>'
        f'<td><a href="{e(s["path"])}">JSON</a> '
        f'<span class="u">{e(s["digest"][7:19])}&#8230;</span></td></tr>'
        for s in sorted(items, key=lambda x: (x["month"], x["series"]), reverse=True)
    )
    table = (f"""<div class="card card__body--flush"><div class="scroll-x">
    <table class="grid"><caption class="vh">Monthly market-price statements</caption>
    <thead><tr><th scope="col">Series</th><th scope="col">Month</th>
    <th scope="col" class="ta-r">Sessions published</th>
    <th scope="col" class="ta-r">Mean</th><th scope="col">Statement</th></tr></thead>
    <tbody>{rows}</tbody></table></div></div>""" if rows else
             '<p class="section__dek">No month has closed since the first print.</p>')
    return f"""<div class="md"><p>A statement lists every calendar day of a period for one
    series: the value published, the ECB rate it was converted at, the methodology version,
    the gaps with their reasons, and the digest TCI published for each print. The verify
    command rebuilds the statement from the record and reports any session, value or label
    that differs from the file. A statement is evidence of what the series printed. It is
    not a valuation, and it says nothing about the terms of any particular contract.</p>
    <p>One is published for each closed month for the headline and the spot series. Any
    other period is one command in a clone of the repository, and the same command checks a
    statement against the record:</p></div>
    <pre class="code"><code>{_STATEMENT_COMMANDS}</code></pre>
    {table}"""
