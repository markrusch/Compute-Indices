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

PAGES = (
    "index.html", "methodology.html", "data.html", "governance.html", "research.html",
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
    for name in PAGES:
        assert (built / name).exists(), name
    assert (built / "research" / "a-note.html").exists()


def test_pages_are_self_contained(built):
    """No off-origin request is possible: no stylesheet link, no script src, no @import,
    no url() that resolves anywhere. Prose that merely mentions the words is allowed.

    The two typefaces are self-hosted for exactly this reason (site/assets/fonts/LICENSE):
    a Google Fonts <link> would be the site's only off-origin request and would hand every
    visitor's IP to a third party.
    """
    for name in PAGES:
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
    for name in PAGES:
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
    for name in PAGES:
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
    for name in PAGES:
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
    for name in PAGES:
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
