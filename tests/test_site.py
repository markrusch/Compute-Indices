# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Static site generation: revision selection, gap honesty, and the page shell contract."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from tci import db
from tci.outputs import site, webdata
from tests.conftest import insert_run

# Asserting the site publishes what it is supposed to publish is the one place a list
# belongs, because the point is completeness. Every other test derives the list from what
# was written, so an invariant covers a new page the day it exists.
EXPECTED_PAGES = (
    "forward.html", "intraday.html",
    "index.html", "basis.html", "term.html", "methodology.html", "data.html",
    "governance.html", "notices.html", "reliability.html", "performance.html",
    "research.html", "contact.html", "privacy.html", "intake.html",
)


def pages_in(built: Path) -> list[str]:
    """Every top-level page `site.generate` actually wrote.

    This was a hand-kept tuple of five names, and the site had grown to nine: basis,
    term, notices and reliability were all being published without anything checking
    them for the invariant below. Deriving it means a new page is covered the day it
    exists rather than the day somebody remembers to add it here. 404.html is excluded —
    it is written after the page list on purpose and is noindex.
    """
    return sorted(
        p.name for p in built.glob("*.html")
        if p.name not in ("404.html", "components.html")
    )


def _print(
    conn: sqlite3.Connection,
    date: str,
    series: str,
    revision: int,
    value: float | None,
    *,
    flags: str = "",
    n_sources: int = 5,
    n_executable: int = 1,
    run_id: str = "r1",
) -> None:
    conn.execute(
        "INSERT INTO daily_index (date, series, revision, value_usd, value_eur, fx_rate,"
        " fx_date, n_sources, n_executable, flags, methodology_version, computed_at, run_id)"
        " VALUES (?, ?, ?, ?, ?, 1.1, ?, ?, ?, ?, '0.3.0-dev', ?, ?)",
        (date, series, revision, value, None if value is None else value / 1.1,
         date, n_sources, n_executable, flags, db.utc_now_iso(), run_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# max revision per (date, series) — the single read rule the whole site depends on
# ---------------------------------------------------------------------------


def test_series_history_takes_the_max_revision_per_date_and_series(conn):
    insert_run(conn, "r1", "2026-08-01")
    # rev 1 said 3.00; rev 2 corrected it to 3.50. Only rev 2 may ever be published.
    _print(conn, "2026-08-01", "EU-CRI-H100", 1, 3.00)
    _print(conn, "2026-08-01", "EU-CRI-H100", 2, 3.50, flags="correction")
    _print(conn, "2026-08-02", "EU-CRI-H100", 1, 3.60)

    history = site.series_history(conn, "EU-CRI-H100")

    assert [(p.date, p.value) for p in history] == [
        ("2026-08-01", 3.50),
        ("2026-08-02", 3.60),
    ]
    assert history[0].flags == "correction"


def test_max_revision_withdrawal_to_a_gap_is_respected(conn):
    """A correction that withdraws a value must leave a GAP, not resurrect revision 1."""
    insert_run(conn, "r1", "2026-08-01")
    _print(conn, "2026-08-01", "EU-CRI-H100", 1, 3.00)
    _print(conn, "2026-08-01", "EU-CRI-H100", 2, None, flags="insufficient_sources,correction")

    history = site.series_history(conn, "EU-CRI-H100")

    assert len(history) == 1
    assert history[0].value is None


def test_series_history_does_not_leak_other_series(conn):
    insert_run(conn, "r1", "2026-08-01")
    _print(conn, "2026-08-01", "EU-CRI-H100", 1, 3.00)
    _print(conn, "2026-08-01", "EU-CRI-A100", 3, 1.00)

    assert [p.value for p in site.series_history(conn, "EU-CRI-H100")] == [3.00]
    assert [p.value for p in site.series_history(conn, "EU-CRI-A100")] == [1.00]


def test_series_history_honours_the_since_bound(conn):
    insert_run(conn, "r1", "2026-08-01")
    for day, value in (("2026-07-30", 1.0), ("2026-08-01", 2.0), ("2026-08-02", 3.0)):
        _print(conn, day, "EU-CRI-H100", 1, value)

    assert [p.date for p in site.series_history(conn, "EU-CRI-H100", since="2026-08-01")] == [
        "2026-08-01", "2026-08-02"
    ]


def test_previous_published_skips_gaps_and_stale_revisions(conn):
    insert_run(conn, "r1", "2026-08-01")
    _print(conn, "2026-08-01", "EU-CRI-H100", 1, 3.10)
    _print(conn, "2026-08-02", "EU-CRI-H100", 1, 3.20)
    _print(conn, "2026-08-02", "EU-CRI-H100", 2, None, flags="insufficient_sources")
    _print(conn, "2026-08-03", "EU-CRI-H100", 1, 3.30)

    # 08-02 was withdrawn at rev 2, so the prior published print is 08-01, not 3.20.
    assert site.previous_published(conn, "EU-CRI-H100", "2026-08-03") == ("2026-08-01", 3.10)


# ---------------------------------------------------------------------------
# window alignment and gaps
# ---------------------------------------------------------------------------


def test_window_is_a_full_calendar_range_ending_on_the_date():
    window = site._window("2026-08-16", 5)
    assert window == [
        "2026-08-12", "2026-08-13", "2026-08-14", "2026-08-15", "2026-08-16"
    ]


def test_windowed_fills_absent_days_as_gaps():
    points = [site.Point("2026-08-16", 3.29)]
    filled = site._windowed(points, site._window("2026-08-16", 3))

    assert [p.value for p in filled] == [None, None, 3.29]
    assert filled[0].flags == "no_print"


# ---------------------------------------------------------------------------
# chart: never interpolate across a missing print
# ---------------------------------------------------------------------------


def test_chart_breaks_the_line_across_a_gap():
    contiguous = site.line_chart(
        [site.Point(f"2026-08-0{d}", 3.0 + d / 10) for d in (1, 2, 3)],
        symbol="X",
    )
    gapped = site.line_chart(
        [
            site.Point("2026-08-01", 3.1),
            site.Point("2026-08-02", None),
            site.Point("2026-08-03", 3.3),
        ],
        symbol="X",
    )
    assert contiguous.count('class="ch-line"') == 1
    # no path may span the gap; the two survivors are drawn as isolated points instead
    assert 'class="ch-line"' not in gapped
    assert gapped.count('class="ch-gapmark"') == 1
    assert "1 published, 2 gapped" not in gapped and "2 published, 1 gapped" in gapped


def test_chart_with_no_published_value_says_so_instead_of_drawing():
    out = site.line_chart([site.Point("2026-08-01", None)], symbol="EU-CRI-H200")
    assert "gapnote" in out
    assert "<svg class=\"chart\"" not in out
    assert 'class="ch-line"' not in out


def test_chart_marks_exactly_one_current_value():
    out = site.line_chart(
        [site.Point(f"2026-08-0{d}", 3.0 + d / 10) for d in (1, 2, 3)], symbol="X"
    )
    assert out.count('class="ch-marker"') == 1
    assert out.count('class="ch-marker-rule"') == 1


def test_sparkline_needs_two_points():
    assert site.sparkline([site.Point("2026-08-01", 3.0)]) == ""
    assert "<svg" in site.sparkline(
        [site.Point("2026-08-01", 3.0), site.Point("2026-08-02", 3.1)]
    )


# ---------------------------------------------------------------------------
# whole-site generation
# ---------------------------------------------------------------------------


@pytest.fixture()
def built(conn, tmp_path, monkeypatch) -> Path:
    out = tmp_path / "site"
    (out / "assets").mkdir(parents=True)
    for name in ("tokens.css", "site.css"):
        (out / "assets" / name).write_text(
            (site.ASSETS / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    research = tmp_path / "research"
    research.mkdir()
    (research / "a-note.md").write_text(
        "# A note title\n\n**The dek line.**\n\n16 August 2026\n\n## One\n\ntext\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(site, "SITE_DIR", out)
    monkeypatch.setattr(site, "ASSETS", out / "assets")
    monkeypatch.setattr(site, "RESEARCH_SRC", research)

    insert_run(conn, "r1", "2026-08-16")
    _print(conn, "2026-08-10", "EU-CRI-H100", 1, 3.25)
    _print(conn, "2026-08-16", "EU-CRI-H100", 1, 3.29)
    _print(conn, "2026-08-16", "EU-CRI-H200", 1, None, flags="insufficient_sources",
           n_sources=0, n_executable=0)
    conn.executemany(
        "INSERT INTO constituents (date, series, revision, provider, source, tier,"
        " price_usd, weight, included, exclusion_reason, flags) VALUES"
        " (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("2026-08-16", "EU-CRI-H100", 1, "runpod", "runpod", "executable", 3.29,
             25.0, 1, None, "weight_capped"),
            ("2026-08-16", "EU-CRI-H100", 1, "seeweb", "static_yaml", "list", 2.16,
             18.75, 1, "trimmed", ""),
            ("2026-08-16", "EU-CRI-H100", 1, "aws", "", "", 0.0, 0.0, 0,
             "out_of_population", ""),
        ],
    )
    conn.commit()
    site.generate(conn)
    return out


def test_generate_writes_every_page(built):
    for name in EXPECTED_PAGES:
        assert (built / name).exists(), name
    assert set(pages_in(built)) == set(EXPECTED_PAGES), (
        "a page was added or removed without updating EXPECTED_PAGES"
    )
    assert (built / "research" / "a-note.html").exists()


def test_pages_are_self_contained(built):
    """No off-origin request is possible: no stylesheet link, no script src, no @import,
    no url() that resolves anywhere. Prose that merely mentions the words is allowed.

    The two typefaces are self-hosted for exactly this reason (site/assets/fonts/LICENSE):
    a Google Fonts <link> would be the site's only off-origin request and would hand every
    visitor's IP to a third party.
    """
    names = pages_in(built)
    assert len(names) >= 8, f"fewer pages than expected, is generate() failing? {names}"
    for name in names:
        html = (built / name).read_text(encoding="utf-8")
        assert '<link rel="stylesheet"' not in html
        assert not re.search(r"@import\s+(url\(|[\"'])", html)
        assert not re.search(r"\burl\(\s*['\"]?(https?:)?//", html)
        assert "fonts.googleapis.com" not in html and "fonts.gstatic.com" not in html
        assert "<style>" in html  # tokens + components are inlined

        # Exactly one script src is permitted, and only this one: Vercel Web Analytics,
        # at a same-origin path. Vercel serves the canonical site and therefore already
        # sees every request to it, so this reaches no party that was not already in the
        # path -- which is why it does not break "no visitor's IP reaches a third party".
        # Anything else, including a second copy of this one, fails here.
        srcs = re.findall(r"<script[^>]*\bsrc=\"([^\"]+)\"", html)
        assert srcs == ["/_vercel/insights/script.js"], f"{name}: unexpected script src {srcs}"

    # The absolute URLs in the markup are the canonical and card metadata only. They are
    # <meta>/<link> values that a crawler resolves, never anything the browser fetches.
    names = pages_in(built)
    assert len(names) >= 8, f"fewer pages than expected, is generate() failing? {names}"
    for name in names:
        html = (built / name).read_text(encoding="utf-8")
        body = html.split("</head>", 1)[1]
        assert "thecomputeindices.com" not in body, f"{name}: absolute URL escaped the head"


def test_font_urls_resolve_from_the_page_that_inlines_them(built):
    """The sheet is inlined, so url() resolves against the PAGE, not the stylesheet.

    A note at research/x.html therefore needs ../assets/fonts/; a top-level page needs
    assets/fonts/. Getting this wrong is silent — the browser just falls back to the
    system stack and nobody notices until the brand looks wrong on one page.
    """
    top = (built / "index.html").read_text(encoding="utf-8")
    nested = (built / "research" / "a-note.html").read_text(encoding="utf-8")

    assert 'url("assets/fonts/outfit-latin.woff2")' in top
    assert "{FONTS}" not in top  # the placeholder is always substituted
    assert 'url("../assets/fonts/outfit-latin.woff2")' in nested
    assert 'url("assets/fonts/outfit-latin.woff2")' not in nested


def test_pages_carry_one_h1_and_a_current_nav_marker(built):
    names = pages_in(built)
    assert len(names) >= 8, f"fewer pages than expected, is generate() failing? {names}"
    for name in names:
        html = (built / name).read_text(encoding="utf-8")
        body = html.split("</style>", 1)[1]  # the CSS mentions the attribute in a selector
        assert body.count("<h1") == 1, name
        assert body.count('aria-current="page"') == 1, name


def test_single_theme_contract_is_present_on_every_page(built):
    """TCI ships ONE theme, and the contract is that nothing can change it.

    Until the rebrand this asserted the opposite: a full light palette plus two dark
    blocks, with an explicit stamp beating the OS in both directions. The brand guide is
    explicit that #0B0C0D is "the page background — the only page-background color", so
    there is no light palette to switch to and the toggle went with it. What has to hold
    now is that an OS set to light renders exactly the same page:

      * body carries an explicit token background (a transparent body borrows the host's
        theme and the whole palette comes apart);
      * color-scheme is declared dark, in the CSS and in the meta tag, so form controls
        and scrollbars match rather than painting white;
      * nothing anywhere reacts to prefers-color-scheme, and no data-theme stamp survives.
    """
    for name in pages_in(built):
        page = (built / name).read_text(encoding="utf-8")
        assert "background: var(--page)" in page
        assert "color-scheme: dark" in page
        assert '<meta name="color-scheme" content="dark">' in page
        # The at-rule and the selector, not the words: the stylesheet's own comments
        # explain why neither is used, and prose that mentions them is not a leak.
        assert not re.search(r"@media[^{]*prefers-color-scheme", page), name
        assert not re.search(r"\[data-theme[~^|$*]?=", page), name


def test_dashboard_shows_the_headline_and_never_carries_a_gap_forward(built):
    html = (built / "index.html").read_text(encoding="utf-8")
    assert "3.29" in html
    # the gapped sub-index shows a dash and the reason, not the last good value
    assert "below the provider gate" in html
    assert "H200" in html


def test_live_surfaces_never_show_a_stale_print_as_current(conn):
    """A series that stops being computed must vanish from the live surfaces, not keep
    rendering its last good value.

    Regression: EU-CRI-H100-CLOUD was retired in v0.3.0 but left in site.TICKER, so the
    ticker went on showing 3.85 from 2026-08-15 under methodology 0.2.0-dev. On
    2026-09-04 it was the only number on a ticker where every live series was honestly
    gapped -- precisely the "stale value dressed as live" this generator exists to
    prevent.
    """
    insert_run(conn, "r1", "2026-08-16")
    session = "2026-08-16"
    # A series still being computed, gapped this session after an earlier real value.
    _print(conn, "2026-08-10", "EU-CRI-H100", 1, 3.85)
    _print(conn, "2026-08-16", "EU-CRI-H100", 1, None, flags="insufficient_sources",
           n_sources=4, n_executable=0)
    # A retired series: its newest row is old, and it must not resolve as current.
    _print(conn, "2026-08-10", "EU-CRI-RETIRED", 1, 3.85)

    assert site.latest_print(conn, "EU-CRI-RETIRED")["value_usd"] == 3.85
    assert site.current_print(conn, "EU-CRI-RETIRED", session) is None
    # A live series keeps resolving, and its gap stays a gap rather than reverting to 3.85.
    assert site.current_print(conn, "EU-CRI-H100", session)["value_usd"] is None

    # And the retired series is gone from every surface that renders live values.
    assert "EU-CRI-H100-CLOUD" not in site.TICKER
    assert "EU-CRI-H100-CLOUD" not in site.SERIES_LABEL
    assert "EU-CRI-H100-CLOUD" not in webdata.ALL_SERIES


# ---------------------------------------------------------------------------
# the rename boundary: TCI on the page, EU-CRI in the store
# ---------------------------------------------------------------------------


def test_display_series_is_the_only_place_the_rename_happens():
    assert site.display_series("EU-CRI-H100") == "TCI-CRI-H100"
    assert site.display_series("EU-CRI-COMPUTE") == "TCI-CRI-COMPUTE"
    # The stored constants are untouched: the DB, the CSV and latest.json still key on
    # them, and renaming those is a governed change, not a restyle (GOVERNANCE.md §1).
    assert site.HEADLINE == "EU-CRI-H100"
    assert all(s.startswith("EU-CRI") for s in site.TICKER + site.TILES)


def test_pages_publish_tci_names_and_say_where_the_stored_keys_still_apply(built):
    index = (built / "index.html").read_text(encoding="utf-8")
    body = index.split("</style>", 1)[1]
    assert "TCI&#8209;CRI&#8209;H100" in body
    assert "The Compute Indices" in body

    # The one place the mismatch is allowed to surface is the Data page, and it has to
    # be stated outright -- a reader who cites TCI-CRI-H100 must be able to find it in
    # the CSV. Leaving that to be discovered inside a download costs the citation.
    data = (built / "data.html").read_text(encoding="utf-8")
    assert "EU-CRI-H100" in data and "TCI-CRI-H100" in data
    assert "old&#8594;new mapping" in data


# ---------------------------------------------------------------------------
# the mobile layer
# ---------------------------------------------------------------------------


def test_mobile_nav_is_operable_without_javascript(built):
    """The menu is a checkbox and its label, so it opens with scripting off.

    The trap this guards: hiding the checkbox with `display:none` would still
    let a mouse open the menu via the label, so it would look fine, while
    removing the input from the tab order and making the nav keyboard-only
    unreachable on a phone. It has to be invisible AND focusable.
    """
    for name in pages_in(built):
        page = (built / name).read_text(encoding="utf-8")
        body = page.split("</style>", 1)[1]
        assert '<input type="checkbox" id="navtoggle"' in body, name
        assert 'for="navtoggle"' in body, name
        # opened by CSS state, never by a script
        assert ".navtoggle:checked ~ .nav" in page, name

    css = (built / "assets" / "site.css").read_text(encoding="utf-8")
    mobile = css.split("@media (max-width: 860px)", 1)[1]
    assert ".navtoggle { display: block; }" in mobile
    assert "opacity: 0" in css.split(".navtoggle {", 1)[1][:200]


def test_desktop_nav_is_untouched_by_the_mobile_layer(built):
    """The disclosure control is inert above the breakpoint.

    Both halves are display:none by default, which also takes the checkbox out
    of the tab order, so a desktop visitor tabs from the brand straight into the
    nav exactly as before the mobile layer existed.
    """
    css = (built / "assets" / "site.css").read_text(encoding="utf-8")
    base = css.split("@media", 1)[0]
    assert (
        ".navtoggle { position: absolute; opacity: 0; pointer-events: none;"
        " display: none; }"
    ) in base
    assert ".navbtn { display: none; }" in base


def test_mobile_never_hides_a_value_the_desktop_shows(built):
    """A phone gets the page rearranged, not a reduced version of it.

    The old layout hid `.tickerbar__stamp` below 720px, which was the one piece
    of live state the small screen was not told: it is the cell the refresh
    script rewrites. Anything display:none in a mobile query now has to be
    either decorative or genuinely redundant, and this pins the list.
    """
    css = (built / "assets" / "site.css").read_text(encoding="utf-8")

    def blocks(text: str):
        """Brace-match each max-width block. A naive `.*?\\n}` stops at the first
        line-start brace, which silently swallows everything after a one-line
        media query and reports base rules as mobile-hidden."""
        for m in re.finditer(r"@media \([^)]*max-width[^)]*\)\s*\{", text):
            depth, i = 1, m.end()
            while depth and i < len(text):
                depth += (text[i] == "{") - (text[i] == "}")
                i += 1
            yield text[m.end():i - 1]

    hidden = set()
    for block in blocks(css):
        for rule in re.finditer(r"([^;{}\n]+)\{[^}]*display:\s*none", block):
            hidden.add(rule.group(1).strip())
    allowed = {
        ".hero__wave",           # decorative brand furniture, aria-hidden
        ".brand__name",          # the wordmark itself still shows
        ".nav",                  # collapsed behind the Menu button, not removed
        ".stable thead th:nth-child(2), .stable tbody td:nth-child(2)",  # constant "EU/EEA"
    }
    assert hidden <= allowed, f"mobile hides something new: {hidden - allowed}"
    # and specifically, the live timestamp survives
    assert ".tickerbar__stamp { display: none" not in css


def test_sticky_offsets_are_tokenised_not_hardcoded(built):
    """Anchor targets must follow the header height, which differs per breakpoint.

    A heading linked from the table of contents has to land below the masthead
    rather than under it. Hard-coding the desktop height silently mis-scrolls
    every anchor on mobile, where only the bar stays stuck.
    """
    css = (built / "assets" / "site.css").read_text(encoding="utf-8")
    assert "scroll-margin-top: 110px" not in css
    assert css.count("scroll-margin-top: var(--sticky-h)") >= 3
    assert "--sticky-h: 64px" in css        # mobile bar only
    tokens = (built / "assets" / "tokens.css").read_text(encoding="utf-8")
    assert "--sticky-h:      110px" in tokens


def test_404_is_branded_noindex_and_out_of_the_sitemap(built):
    """An unmatched path has to land on a page that still looks like the site.

    Before this it fell through to the host's default, which on Vercel is an unstyled
    white page — the worst possible place to drop a dark-only theme, because someone who
    mistypes a URL sees a page belonging to nobody.

    Three things have to hold, and each fails silently on its own: it is noindex (a 404
    that gets indexed competes with pages that exist), it is absent from the sitemap, and
    it marks no nav item current because none of them is where the reader is.
    """
    page = (built / "404.html").read_text(encoding="utf-8")
    body = page.split("</style>", 1)[1]

    assert '<meta name="robots" content="noindex">' in page
    assert body.count("<h1") == 1
    assert body.count('aria-current="page"') == 0
    assert "404" not in (built / "sitemap.xml").read_text(encoding="utf-8")
    # Same chrome and the same self-containment rules as every other page.
    assert "background: var(--page)" in page
    assert re.findall(r"<script[^>]*\bsrc=\"([^\"]+)\"", page) == ["/_vercel/insights/script.js"]
    # Links out are relative, so it works on the mirror and from a local checkout too.
    assert 'href="index.html"' in body


def test_rebrand_renames_prose_but_never_a_pasteable_command():
    """The embedded docs print commands whose --series argument is a database key.

    Renaming it would hand a reader a command that returns nothing, so fenced blocks and
    inline code are held out of the substitution while the prose around them is renamed.
    """
    out = site._rebrand_doc(
        "EU-CRI is a benchmark; EU-CRI-H100 is its headline.\n\n"
        "```\npython -m tci.run constituents --series EU-CRI-H100\n```\n\n"
        "Config lives in `EU-CRI-H100` and prose does not."
    )
    assert out.startswith("TCI is a benchmark; TCI-CRI-H100 is its headline.")
    assert "--series EU-CRI-H100" in out          # fenced block untouched
    assert "`EU-CRI-H100`" in out                 # inline code untouched
    # Order matters: renaming the bare brand first would leave TCI-H100, not TCI-CRI-H100.
    assert "TCI-H100" not in out


def test_ticker_marquee_reads_each_value_once(built):
    """The band duplicates its run to loop seamlessly; only one run is announced."""
    html = (built / "index.html").read_text(encoding="utf-8")
    assert html.count('class="tickerbar__run"') == 2
    assert html.count('class="tickerbar__run" aria-hidden="true"') == 1
    # And the as-of stamp stays outside the marquee, where the refresh script can find
    # it and where it does not scroll away from the reader.
    marquee = html.split('class="tickerbar__marquee"', 1)[1].split("</div></div>", 1)[0]
    assert 'id="asof"' not in marquee


def test_research_note_is_rendered_from_markdown(built):
    html = (built / "research" / "a-note.html").read_text(encoding="utf-8")
    assert "A note title" in html
    assert "The dek line." in html
    assert "Content slot" not in html
    index = (built / "research.html").read_text(encoding="utf-8")
    assert 'href="research/a-note.html"' in index
    assert "16 Aug 2026" in index  # date sniffed from the byline, no front matter needed


def test_planned_note_without_copy_gets_a_marked_slot(built):
    html = (built / "research" / "composition-vs-price.html").read_text(encoding="utf-8")
    assert "Content slot" in html
    assert "has not been written yet" in html


def test_methodology_ledger_is_read_from_config(built):
    html = (built / "methodology.html").read_text(encoding="utf-8")
    assert "Publication gate" in html
    assert "Concentration cap" in html
    assert "sha256:" in html
    assert "ledger__row" in html


def test_front_matter_is_optional_and_parsed_when_present():
    meta, body = site._front_matter("---\ntitle: T\ndate: 2026-01-02\n---\n\n# H\n")
    # PyYAML types a bare date; the generator stringifies it, so both forms are fine.
    assert meta["title"] == "T"
    assert str(meta["date"]) == "2026-01-02"
    assert body.startswith("# H")
    assert site._front_matter("# H\n") == ({}, "# H\n")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("byline 2026-08-16 here", "2026-08-16"),
        ("EU-CRI Research Note 2026-01 · 16 August 2026 · Mark Rusch", "2026-08-16"),
        ("no date at all", ""),
    ],
)
def test_sniff_date(text, expected):
    assert site._sniff_date(text) == expected


def test_note_masthead_is_not_rendered_twice():
    """The page header publishes title + dek + byline; the body must not repeat them."""
    body = _strip_note_masthead_fixture()
    assert body.startswith("## Abstract")
    assert "Title here" not in body
    assert "The dek." not in body
    assert "Mark Rusch" not in body


def _strip_note_masthead_fixture() -> str:
    return site._strip_note_masthead(
        "# Title here\n\n**The dek.**\n\nNote 2026-01 · 16 August 2026 · Mark Rusch\n\n"
        "---\n\n## Abstract\n\nbody text\n"
    )


def test_note_without_a_rule_only_loses_its_h1():
    out = site._strip_note_masthead("# Title\n\nintro paragraph\n\n## One\n")
    assert out == "intro paragraph\n\n## One\n"


def test_note_without_an_h1_is_untouched():
    assert site._strip_note_masthead("## One\n\ntext\n") == "## One\n\ntext\n"


def test_the_reliability_page_lists_the_gaps_from_the_record(built):
    """L3.2: generated from stored prints, with no hand-written entry possible."""
    html = (built / "reliability.html").read_text(encoding="utf-8")
    body = html.split("</style>", 1)[1]
    # The fixture stores one H200 gap and no headline gap.
    assert "TCI-CRI-H200" in body
    assert "Reliability" in body


def test_the_reliability_page_never_invents_a_reason(built, conn):
    """A flag nobody has written words for is published as the flag, not as a guess."""
    from tci import reliability

    g = reliability.Gap("2026-08-16", "EU-CRI-H100", "a_flag_from_the_future", 1, "0.6.0")
    assert g.reason == "a_flag_from_the_future"


def test_the_reliability_page_is_reachable(built):
    """A page linked from nowhere is components.html, and that one is Disallowed."""
    index = (built / "index.html").read_text(encoding="utf-8")
    assert 'href="reliability.html"' in index
    sitemap = (built / "sitemap.xml").read_text(encoding="utf-8")
    assert "reliability.html" in sitemap


def test_a_page_builder_that_returns_nothing_writes_no_file(built, monkeypatch, conn):
    """The performance page reads a snapshot that could be missing. Writing its empty
    string would replace a good page with a blank one, which is worse than skipping it."""
    monkeypatch.setattr(site, "_performance", lambda ctx: "")
    (built / "performance.html").write_text("previous good page", encoding="utf-8")
    site.generate(conn)
    assert (built / "performance.html").read_text(encoding="utf-8") == "previous good page"


def test_the_performance_page_survives_a_missing_mlperf_snapshot(built, monkeypatch, conn):
    """Research beside the index must never take the day's prints down with it, now that
    a failed output build fails the daily run."""
    from tci import mlperf

    def boom(*_a, **_k):
        raise FileNotFoundError("no snapshot")

    monkeypatch.setattr(mlperf, "load", boom)
    site.generate(conn)  # must not raise
    assert (built / "index.html").exists()


def test_a_scroll_container_contains_its_absolutely_positioned_children(built):
    """`.scroll-x` must carry a position, or a `.vh` inside it escapes to the document.

    Overflow only clips an absolutely positioned descendant when the clipping ancestor
    is in its containing-block chain. Unpositioned, the four screen-reader captions in
    the constituents tables resolved against the initial containing block and reported
    their far edge to `documentElement`: index.html measured 419px of scroll width
    against a 390px viewport, and a phone could drag the whole page 29px sideways (59px
    at 360px). `clip-path` hid those spans; it did not stop them widening the document.
    """
    css = (built / "assets" / "site.css").read_text(encoding="utf-8")
    m = re.search(r"\.scroll-x\s*\{([^}]*)\}", css)
    assert m, ".scroll-x rule is gone"
    assert re.search(r"position:\s*(relative|sticky)", m.group(1)), (
        "`.scroll-x` has no position, so an absolutely positioned child of a wide table "
        "will widen the document again"
    )


def test_every_scroller_shows_that_it_has_more_to_show(built):
    """A hidden column needs a cue, and a touch browser draws no scrollbar at rest.

    Eleven scrollers hide 107-826px of content on a 390px phone. The methodology ledger
    showed Version and Effective-from while Status sat off-screen, inside a rounded card
    border that reads as "this table is complete". The cue is a pair of shadow layers
    pinned to the scrollport under a pair of page-coloured layers that travel with the
    content, so it appears only while something really is hidden in that direction.
    """
    css = (built / "assets" / "site.css").read_text(encoding="utf-8")
    m = re.search(r"\.scroll-x,\s*\.tableview__scroll\s*\{(.*?)\n\}", css, re.S)
    assert m, "the scroll-affordance rule is gone"
    block = m.group(1)
    # The pairing is what makes the wash exact: drop the `local` layers and it never
    # goes away at the ends; drop the `scroll` layers and there is nothing to reveal.
    assert "background-attachment: local, local, scroll, scroll" in block
    assert block.count("linear-gradient") == 6  # 4 wash layers + 2 mask (prefixed pair)
    assert "--page" in block, "the cover layers must use the one page background"
    # The wash must be light. A dark shadow is the conventional choice and it is
    # invisible on #0B0C0D, which is what made this look done when it was not.
    assert "rgba(0, 0, 0, .6)" not in block
    assert "rgba(245, 245, 246, .20)" in block

    # The fade is driven by scroll position so that a table which fits keeps its last
    # column. Base state is zero at both edges; only an active timeline moves it.
    assert re.search(r"--edge-s:\s*0px", block) and re.search(r"--edge-e:\s*0px", block)
    assert "@property --edge-s" in css and "@property --edge-e" in css, (
        "custom properties must be registered as <length> or they cannot interpolate"
    )
    assert "@supports (animation-timeline: scroll())" in css, (
        "the fade must be behind a support query: without one, an engine that cannot "
        "run it is left with a mask referring to properties that never animate"
    )
    assert "animation-timeline: scroll(self inline)" in css

    # No JavaScript may be involved: these pages have to work with scripting off.
    dash = (built / "index.html").read_text(encoding="utf-8")
    body = dash[dash.index("<body") :]
    for script in re.findall(r"<script\b[^>]*>(.*?)</script>", body, re.S):
        assert "scrollLeft" not in script, "the scroll cue must not depend on scripting"


def test_the_history_chart_opens_on_the_current_print_not_the_oldest_session(built):
    """The 30-session chart is 660px wide at its floor and a phone column is 324px.

    Anchored at the left, the third of the picture a phone showed was the start of the
    window: at 390px the visible labels were "19 Aug", "26 Aug" and the points from
    20-31 Aug, with the current print, its marker and the whole y-axis scale off-screen
    — on the one chart whose job is to show that the price moved. The 660px floor stays,
    because the axis labels are viewBox units and shrink with the picture; the scroller
    starts at its inline end instead.
    """
    css = (built / "assets" / "site.css").read_text(encoding="utf-8")
    assert re.search(r"\.scroll-x--recent\s*\{[^}]*direction:\s*rtl", css)
    # rtl on the container only. Anything inside it reads left-to-right as before.
    assert re.search(r"\.scroll-x--recent\s*>\s*\*\s*\{[^}]*direction:\s*ltr", css)

    dash = (built / "index.html").read_text(encoding="utf-8")
    assert 'class="scroll-x scroll-x--recent"' in dash, (
        "the dashboard history chart is not opening on the latest session"
    )
    # The forward curve is read from its near horizon, which is on the left, so it must
    # not pick this up by being a chart.
    fwd = (built / "forward.html").read_text(encoding="utf-8")
    for chart in re.findall(r'<div class="([^"]*scroll-x[^"]*)"><svg class="chart"', fwd):
        assert "scroll-x--recent" not in chart, (
            "the forward curve must stay anchored at its near horizon"
        )


def test_the_dashboard_leads_with_the_print_and_keeps_the_banner_above_it(built):
    """The one number this site publishes comes before the table that details it.

    The print card used to sit behind the brand statement, the announcement banner and
    the six-row series table: 2014px down a 390px phone, 2.39 viewports, and 1590px on a
    1440x900 desktop. Leading with it measured 1354px and 1053px.

    The banner's position is not a layout preference and is asserted here so a later
    reshuffle cannot quietly invert it: a notice is worth nothing if it is only reachable
    from a page nobody visits, so it stays above the print it is going to change. What
    moved is the series table, which is the detail behind the number, not the way in.
    """
    dash = (built / "index.html").read_text(encoding="utf-8")
    banner = dash.find("notice-banner")
    print_card = dash.find('id="print"')
    table = dash.find('id="indices"')
    assert print_card != -1 and table != -1, "the dashboard lost a landmark"
    assert print_card < table, "the series table is back in front of the print"
    if banner != -1:  # only raised while a change is announced and not yet in effect
        assert banner < print_card, "the announced-change banner fell below the print"

    # The way in to the number should land on the number, from the hero and from the
    # masthead of every other page.
    assert 'href="#print"' in dash
    assert 'href="index.html#print"' in (built / "methodology.html").read_text(
        encoding="utf-8"
    )


def test_the_scroll_entrance_is_gated_and_never_touches_the_print(built):
    """Motion that cannot be turned off, or that hides content, is worse than none.

    The entrance rides a scroll-driven timeline, and three things keep it safe. It sits
    behind `@supports`, so an engine without scroll-driven animations renders the flat
    page it renders today rather than a page of invisible blocks. It sits behind
    `prefers-reduced-motion: no-preference`, so under `reduce` the rule never applies at
    all — it is NOT switched off afterwards, because the blanket override in tokens.css
    is `animation-duration: 1ms !important` and duration does not govern a scroll-driven
    animation, so a rule relying on that would have kept animating for exactly the
    readers who asked it not to. And the range is a pixel length, not a percentage of
    `entry`, which scales with the subject: the 2594px notice card would have faded over
    1300px of scrolling while a 91px heading faded over 45px.
    """
    css = (built / "assets" / "site.css").read_text(encoding="utf-8")
    gate = re.search(
        r"@supports \(animation-timeline: view\(\)\)\s*\{\s*"
        r"@media \(prefers-reduced-motion: no-preference\)\s*\{",
        css,
    )
    assert gate, "the entrance is not behind both a support query and a motion query"

    # The entrance rule ONLY, bounded at its own closing brace. Capturing to the end of
    # the file instead let this assertion pass against the chart rules further down: the
    # range check below was satisfied by `entry 0px entry 340px` on the draw while the
    # entrance itself had been switched to a percentage. 85kB of accidental capture.
    rule = re.search(
        r"\n(\s*\.section__head,.*?)\{(.*?)\n\s*\}", css[gate.end() :], re.S
    )
    assert rule, "the entrance rule is gone"
    selectors, decls = rule.group(1), rule.group(2)

    assert "animation-name: tci-fadeup" in decls
    assert "animation-timing-function: linear" in decls
    assert re.search(r"animation-range: entry 0px entry \d+px", decls), (
        "the range must be a pixel length; a percentage of entry scales with the element"
    )
    assert "%" not in re.search(r"animation-range:[^;]+", decls).group(0)

    # The print card carries the one published number and must never fade.
    assert ".print" not in selectors
    dash = (built / "index.html").read_text(encoding="utf-8")
    assert 'class="print"' in dash


def test_the_chart_draw_cannot_strand_a_half_drawn_price_line(built):
    """A price line frozen part-way across is a false chart, so three things prevent it.

    The timeline is named on the SCROLLER. An anonymous `view()` on the path resolves
    against `.scroll-x` — `overflow-x: auto` makes overflow-y compute to `auto`, so the
    scroller is a scroll container on both axes and never scrolls vertically — which
    measured as a timeline that never advanced and every segment stranded at
    stroke-dashoffset 0.297. Naming it on the scroller makes the page the scrollport.
    Anchoring to the svg is the same trap: it is inside the scroller, and it froze at
    0.338 in both engines.

    The base is `stroke-dashoffset: 0`, fully drawn, so a timeline that never resolves
    leaves a complete line rather than a truncated one.

    And the rule is keyed to `[pathLength]`. Without that attribute `stroke-dasharray: 1`
    means one user unit and renders the line dashed, which DESIGN.md §3 forbids outright:
    a dashed line reads as a projection in a data product.
    """
    css = (built / "assets" / "site.css").read_text(encoding="utf-8")
    assert re.search(r"\.scroll-x:has\(\.chart\)\s*\{[^}]*view-timeline-name:\s*--tci-chart", css)
    assert not re.search(r"\.card:has\(\.chart\)\s*\{[^}]*view-timeline-name", css), (
        "the card is 278px above the plot, so a range measured from it is spent before "
        "the chart is on screen"
    )
    assert not re.search(r"svg\.chart\s*\{[^}]*view-timeline-name", css), (
        "the svg is inside the scroller and its timeline never advances"
    )

    draw = re.search(r"\.chart \.ch-line\[pathLength\]\s*\{([^}]*)\}", css)
    assert draw, "the draw must be keyed to [pathLength] or it renders a dashed line"
    assert "stroke-dashoffset: 0" in draw.group(1), "an unresolved timeline must leave it drawn"
    assert "stroke-dasharray: 1" in draw.group(1)

    # Every line the chart draws has to carry the attribute the rule keys off, or that
    # `stroke-dasharray: 1` lands on it as one user unit and dashes it.
    drawn = site.line_chart(
        [site.Point(f"2026-08-0{d}", 3.0 + d / 10) for d in (1, 2, 3)], symbol="X"
    )
    paths = re.findall(r"<path class=\"ch-line\"([^>]*)/>", drawn)
    assert paths, "the chart stopped drawing a line"
    for attrs in paths:
        assert 'pathLength="1"' in attrs

    # And whatever a built page happens to contain must satisfy the same rule.
    for page in ("index.html", "forward.html"):
        html = (built / page).read_text(encoding="utf-8")
        for attrs in re.findall(r"<path class=\"ch-line\"([^>]*)/>", html):
            assert 'pathLength="1"' in attrs, f"{page} has a ch-line without pathLength"

    # The forward page draws its curve from its own module, and the fixture database has
    # no forward data, so nothing above reaches it — stripping the attribute there went
    # unnoticed. Every module that emits a price line is checked at the source instead,
    # which also covers the next chart somebody adds.
    outputs = Path(site.__file__).parent
    emitters = 0
    for mod in sorted(outputs.glob("*.py")):
        src = mod.read_text(encoding="utf-8")
        for emission in re.findall(r'<path class="ch-line"[^>]*?/>', src):
            emitters += 1
            assert 'pathLength="1"' in emission, f"{mod.name} emits a ch-line without pathLength"
    assert emitters >= 2, f"expected the history and forward charts, found {emitters}"


def test_no_entrance_animated_block_sits_inside_a_scroll_container(built):
    """An entrance inside a scroller strands part-way and stays there.

    `tci-fadeup` starts at `opacity: 0` and holds its frame with `animation-fill-mode:
    both`, so unlike the chart draw — whose base is the fully drawn line — a timeline
    that resolves but never advances leaves the block stuck at whatever progress that
    degenerate timeline reports. Nested into a `.scroll-x` deliberately, a section
    heading held 0.51 in Chromium and 0.50 in WebKit through a full scroll of the page:
    permanent half-opacity body content, which is a legibility defect rather than a
    visible one somebody would report.

    That is the same trap as the stranded price line — `overflow-x: auto` makes
    overflow-y compute to `auto`, so a horizontal scroller is a scroll container on both
    axes and never scrolls vertically — but it fails quietly instead of conspicuously.
    The guards on the draw do not help here, so the arrangement is forbidden outright:
    no animated block may have a scroll container above it.

    Nothing on the site does this today. This exists so that a later refactor that wraps
    a section in a scroller fails here rather than shipping faded copy.
    """
    from html.parser import HTMLParser

    SCROLLERS = {"scroll-x", "tableview__scroll"}
    GROUPS = {"tiles", "stack", "pillars", "famcards", "linklist"}

    class Finder(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.stack: list[tuple[str, set[str]]] = []
            self.bad: list[str] = []

        def handle_starttag(self, tag, attrs):  # type: ignore[no-untyped-def]
            if tag in ("br", "img", "input", "meta", "link", "hr", "path", "use"):
                return
            classes = set()
            for k, v in attrs:
                if k == "class" and v:
                    classes = set(v.split())
            parent = self.stack[-1][1] if self.stack else set()
            animated = (
                "section__head" in classes
                or "teaser" in classes
                or bool(parent & GROUPS)
                or ("section" in parent and bool(classes & {"card", "scroll-x"}))
            )
            # strictly ancestors: `.section > .scroll-x` animates the scroller itself,
            # which is fine — its own timeline resolves against the page.
            if animated and any(cls & SCROLLERS for _, cls in self.stack):
                self.bad.append(f"<{tag} class={sorted(classes)}>")
            self.stack.append((tag, classes))

        def handle_endtag(self, tag):  # type: ignore[no-untyped-def]
            for i in range(len(self.stack) - 1, -1, -1):
                if self.stack[i][0] == tag:
                    del self.stack[i:]
                    return

    # Positive control first. A detector that has never fired is indistinguishable from
    # one that cannot, and this whole test is an assertion of absence — so prove it sees
    # the arrangement it forbids before believing it when it says the site is clean.
    control = Finder()
    control.feed(
        '<section class="section"><div class="scroll-x"><table>'
        '<tr><td><div class="section__head">x</div></td></tr>'
        "</table></div></section>"
    )
    assert control.bad, "the detector cannot see a heading nested inside a scroller"

    # And the arrangement that is fine must NOT trip it: `.section > .scroll-x` animates
    # the scroller itself, whose own timeline resolves against the page.
    ok = Finder()
    ok.feed('<section class="section"><div class="scroll-x"><table></table></div></section>')
    assert not ok.bad, "the detector flags the scroller it is supposed to allow"

    seen = 0
    for page in sorted(built.glob("*.html")):
        f = Finder()
        f.feed(page.read_text(encoding="utf-8"))
        seen += 1
        assert not f.bad, f"{page.name} animates a block inside a scroller: {f.bad[:3]}"
    assert seen >= 10, f"the page sweep found only {seen} pages"


def test_intraday_is_a_top_level_tab(built):
    """The hourly line is reached from the menu on every page, not only from the footer
    and a link under the headline chart, which on a phone nobody found."""
    from tci.outputs.site import NAV

    assert ("intraday.html", "Intraday") in NAV
    for name in ("index.html", "data.html", "intraday.html"):
        html = (built / name).read_text(encoding="utf-8")
        nav = html.split('<nav class="nav"', 1)[1].split("</nav>", 1)[0]
        assert 'href="intraday.html"' in nav, name
    page = (built / "intraday.html").read_text(encoding="utf-8").split("</style>", 1)[1]
    assert 'href="intraday.html" aria-current="page"' in page
