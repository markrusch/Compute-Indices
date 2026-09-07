# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

TCI (The Compute Indices) publishes a daily reference price for renting GPU compute in the
EU/EEA. A Python pipeline collects public prices into SQLite, computes the index, and
regenerates a static site from the database. It is a published benchmark, so most of the
constraints below exist for one reason: to make it impossible to quietly change a number,
or to let a wrong or stale one reach print.

Read `GOVERNANCE.md` §1 before touching the calculation path, and `STYLE.md` before writing
prose.

## Commands

Windows uses `.venv/Scripts/python.exe`; on Linux and in CI it is plain `python`.

```bash
pip install -e .[dev]

python -m tci.run migrate                      # create/upgrade data/eucri.db
python -m tci.run daily [--date YYYY-MM-DD]    # collect + compute + regenerate site, CSV, charts
python -m tci.run backfill --from D --to D     # recompute prints from STORED observations only
python -m tci.run constituents --date D [--series S]   # per-print audit table
python -m tci.run weights [--date D]
python -m tci.run validate                     # dropout sensitivity + check-series correlation
python -m tci.run post                         # regenerate site/substack_post.md
python -m tci.run docs                         # regenerate METHODOLOGY.md + METHODOLOGY.lock
python -m tci.run sources [--due|--status S|--block B]   # source register + region coverage

pytest                                         # 165 tests
pytest tests/test_site.py::test_pages_are_self_contained -q    # a single test
pytest -k "gap or revision" -q
ruff check src tests
mypy src/tci                                   # strict only on tci.index and tci.normalise
```

`backfill` never re-collects: these collectors report live market prices, not history, so a
past date cannot be honestly re-collected.

CI runs ruff, then mypy, then pytest. The daily GitHub Action runs at 11:00 UTC and commits
`data/eucri.db` and `site/` back to `main`, so **pull before you push** — a force-push here
destroys real published pricing history.

## Architecture

One direction, and each stage earns its place:

`collectors/*.py` → `observations` → `normalise.py` → `index.py` → `daily_index` +
`constituents` → `outputs/*.py` → `site/`

- **Collectors** are fail-soft and make one request per source per day. A collector that
  breaks yields nothing rather than guessing. `runpod.py` is the reference for documenting
  a source's quirks in the module docstring.
- **`normalise.py`** applies the unit definition (EU/EEA only, node-size floor, price band,
  staleness). **`index.py`** aggregates. Together these are the calculation path.
- **`commands.py`** owns series definitions and revision/storage logic. Series predicates
  live here, not in `index.py`: `compute_print` does not filter by model class, so calling
  it directly across all models produces a meaningless number.
- **`outputs/site.py`** (~2,700 lines) generates every page from the database at build
  time. Every number is baked into the HTML, and the pages work with JavaScript disabled.
- **`sources.py`** is off to the side of that pipeline and reads no prints. It loads
  `config/source_registry.yaml` (every source ever screened, rejections included) and
  `config/regions.yaml` (region blocks; one published, eight shadow) and reports coverage
  against them. Neither file is hash-locked. The monthly process that maintains them is
  `.claude/skills/source-discovery/`.

### Two naming systems, deliberately

The database, `site/data/*` and the CSV still use the original **`EU-CRI-*`** series keys,
and the file is still `data/eucri.db`. The published site says **`TCI-CRI-*`**. Renaming
stored identifiers is a governed event needing an old-to-new mapping and an effective date,
not a reskin, so it has not happened yet.

`site.py::display_series()` and `_rebrand_doc()` are the only two places that translation
happens. Do not rename series keys anywhere else, and never rebrand inside a fenced code
block — commands shown on the site have to stay pasteable. Two tests enforce this.

## Invariants

Breaking one of these either fails CI or silently publishes something false.

**A gap stays a gap.** A session below the provider gate publishes null plus a reason in
words. Never interpolate across a missing print, never carry a value forward, and never let
a live surface show an older print as current — that distinction is why `current_print()`
exists alongside `latest_print()`. This is the one failure mode the project cannot afford.

**The database is append-only.** Triggers block UPDATE and DELETE on `observations`,
`daily_index` and `weight_sets`. A correction is a new revision. Every read must take
`MAX(revision)` per `(date, series)` or it will republish a value that was withdrawn.

**The methodology is hash-locked** over five files: `config/factors.yaml`,
`config/sovereign.yaml`, `src/tci/index.py`, `src/tci/normalise.py`, `src/tci/weights.py`.
Changing any of them requires, in one commit: the change, a `methodology_version` bump, a
CHANGELOG entry, and `python -m tci.run docs`. The generator refuses a changed hash under
an unchanged released version; a `-dev` suffix relaxes that pre-launch. A version bump
discards the stored weight review, so an editorial-only change (a comment, a licence
header) is rehashed *without* a bump and the reason recorded in the CHANGELOG.

**`site/*.html` is generated — never hand-edit it.** Change `outputs/site.py` or
`site/assets/{tokens,site}.css` and rerun. Editing the HTML means the next daily run
silently reverts you.

**The site makes no external requests.** No stylesheet link, no script `src`, no `@import`,
no off-origin `url()`. Fonts are self-hosted and charts are hand-rolled inline SVG. The
stylesheet is inlined into each page, so `url()` resolves against the *page* — hence the
`{FONTS}` placeholder that `_css(prefix)` substitutes per page depth.

**One theme.** `#0B0C0D` is the only page background. There is no light palette, no
`prefers-color-scheme` block and no `data-theme` stamp; tests assert their absence.

**Two reds, never inverted.** Wine `#650304` fills large shapes; bright red `#FF0000` is
rationed to small marks. `DESIGN.md` §3 carries the chart prohibitions — notably no area
fill under a truncated axis, and colour is never the only channel.

When changing the site, verify at mobile widths as well as desktop. Headless Chrome clamps
its window to about 485px, so `--window-size=390` yields a cropped desktop render that
looks like a broken phone layout; use a 390px iframe to get a true viewport.

## Writing

Everything written here — research notes, CHANGELOG entries, commit messages, code
comments, site copy, docstrings — must read as though a person wrote it. That is not a
stylistic preference. The project's credibility rests on it, which is why `STYLE.md` exists
and why it says the fastest way to lose the credibility the methodology is built to earn is
to sound like everyone's LLM-generated finance blog.

**Run the `humanizer` skill** over any prose longer than a paragraph before committing it.

`STYLE.md` is the authority. The tells it bans:

- The rule-of-three reflex. Real reasoning is lumpy: sometimes one reason, sometimes five,
  and the second runs three sentences while the first runs one.
- "It's not just X, it's Y" and its cousins. If the second half is true, just say it.
- Throat-clearing openers. Start with the number or the thing that happened. Cut the first
  paragraph of every draft and check whether anyone would miss it.
- The neat bow. A section may end on an open question or an unresolved number.
- Reflexive hedging. Flag specific uncertainty ("n=6 is thin, one dropout moves this"),
  never generic uncertainty.
- Em-dashes standing in for a real sentence break. One or two per piece.
- Uniformly even paragraph lengths. Let some be one line.
- Stock phrases: "it's worth noting that", "at the end of the day", "in conclusion", "the
  landscape of", "leverage"/"unlock"/"harness" as verbs, "game-changer".

Instead: lead with a specific number, vary sentence length on purpose, use "I" rather than
"we" (this is one person's pipeline), show the mess including bugs found and fixed, and
commit to a figure rather than a qualifier. "seeweb prints $2.16, AWS prints $7.36, same
hour, same continent" — not "prices vary quite a bit". Numbers carry two decimals and their
unit every time: $3.25/GPU-hr.

**Never invent a number, a citation or a source.** Every figure in a research note comes
from a query against `data/eucri.db` that the note itself makes reproducible, and every
external claim carries a reference that has actually been checked. Where the data does not
support the point, publish the negative result — several existing notes do exactly that.

Code comments follow the same rule: explain *why*, and especially which failure motivated
the code. `runpod.py`, `site.py::current_print()` and the migration triggers are the house
standard.
