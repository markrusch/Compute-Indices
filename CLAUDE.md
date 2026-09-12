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
python -m tci.run reproduce [--date D] [--published]    # recompute stored prints, check digests
python -m tci.run contrib validate|ingest|aggregate    # contributed term prices (private store)

pytest                                         # 289 tests
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

**The methodology is hash-locked and versioned by date.** The lock covers the head
parameters (`config/factors.yaml`, `config/sovereign.yaml`), `config/methodology/succession.yaml`,
and `src/tci/index.py`, `src/tci/normalise.py`, `src/tci/weights.py`, plus one hash per frozen
version under `config/methodology/<version>/`. A print is computed under the version live on
its date, so **always pass `for_date` to `config.load_factors` in anything that computes a
print**; the head can be an announced version that is not yet in effect. A new version: freeze
the current head into `config/methodology/<head>/`, edit the head, bump `methodology_version`,
add the version to `succession.yaml` with its notice's effective date, add the notice to
`config/notices.yaml`, a CHANGELOG entry, then `python -m tci.run docs`. Never edit a frozen
snapshot; the lock refuses it. The `-dev` exemption ended at v0.4.0.

**The panel decides admission.** `panel` in factors.yaml names every (provider, collector,
class) that may reach a print. A new collector stores rows without moving anything; do not
add it to the panel outside a new version. Tests of calculation mechanics that use invented
providers use the `unpanelled` fixture.

**Code changes must reproduce the record.** `tests/test_reproduce.py` recomputes every stored
print from stored observations and checks every published digest. If it fails after a code
change, the change alters published numbers and is a methodology change, not a refactor.

**Contributed prices never enter the repository.** `tci.contrib` stores them in a private
database outside the repo (`TCI_PRIVATE_DIR`, default `~/.tci-private`) and refuses a path
inside it. Only `contrib aggregate` output, which carries counts and suppressed cells but no
contributor or single quote, may be written to `site/data/term/contributed.json`. The term
tables (`tci.term`, `term.html`) are research outputs and are not in the calculation path.

**`src/tci/vendor/computable/` is vendored upstream code** (Apache-2.0). Keep it byte-identical
to upstream apart from import paths; TCI's judgements about those rows live in
`collectors/computable_sources.py`. It is excluded from ruff and mypy on purpose.

**`site/*.html` is generated — never hand-edit it.** Change `outputs/site.py` or
`site/assets/{tokens,site}.css` and rerun. Editing the HTML means the next daily run
silently reverts you. One page is not generated: `site/components.html`, the design-system
gallery, is maintained by hand beside `DESIGN.md`, linked from nowhere and `Disallow`ed in
robots.txt. It is the only exception, and `tests/test_pipeline_smoke.py` is where the list
lives — a page that stops being generated has to be added there deliberately, not
discovered later.

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

**Never publish internal reasoning.** Anything the public reads — a research note, site
copy, the methodology or governance pages — states findings and positions, not the process
that produced them. No narration of what was tried, how it felt, what surprised you, what
you plan to fix next, in what order, or why publication was timed a certain way. A
remediation plan inside a published document reads as an internal file that escaped.

Where that material belongs: a defect and its remedy go in `CHANGELOG.md` as
found-not-fixed; a procedure goes in `GOVERNANCE.md`; a change that will move a print gets
a notice in `config/notices.yaml` before it takes effect. If a published finding implies
work, state the finding and stop.

The two registers are different and the distinction is load-bearing. The **newsletter** is
one person with a voice, per `STYLE.md`. A **research note** is the institution speaking:
institutional byline, no first-person process, essay structure with unnumbered prose
headings. `.claude/skills/research-note/SKILL.md` has the full rules and a
pre-publication checklist — load it before writing anything in `research/`.

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
