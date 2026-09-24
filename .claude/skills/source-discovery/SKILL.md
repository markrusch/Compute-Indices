---
name: source-discovery
description: >-
  Run the recurring hunt for new public price sources for the TCI compute indices. Use
  when asked to find new data sources, new GPU cloud providers, new pricing feeds or
  marketplaces; to check whether a new entrant to the compute market has been missed; to
  widen the index panel or its regional coverage; to review or update
  config/source_registry.yaml or config/regions.yaml; or to run the monthly source scan.
  Also covers deciding whether a discovered source is admissible and where a non-EU
  region's prices should be stored.
---

# Source discovery

The panel is the index. Everything else in this repo — the estimator, the trim ladder,
the weight review — is arithmetic over whatever the panel happens to contain, and a
weighted median of six names is a step function no matter how carefully it is computed.
GOVERNANCE.md asks for fifteen independent constituents before the word "settlement" is
allowed anywhere near this thing. The panel currently holds nine.

So this process has one job: find the next constituent before it has been obvious for six
months. A second job follows from it, because a source found for São Paulo is worth
keeping even though no São Paulo index exists — see `config/regions.yaml` on why history
accumulated now is the only kind that will exist later.

## Cadence

Monthly, on the first working day. Also run it on any of these, without waiting:

- a live source starts failing, or `python -m tci.run sources` lists it as silent
- a print gaps on `insufficient_sources`
- a provider in the panel is acquired, renamed, or announces it is leaving a region
- someone launches a competing index or a compute derivative (see the 2026-09-07 scan)
- an unclassified provider name shows up in the report

## Step 0 — read the state before touching anything

```bash
python -m tci.run sources
```

Four things in that output matter, in this order:

1. **UNCLASSIFIED PROVIDERS.** A name here arrived through a source we already collect
   and nobody has told `factors.yaml` what it is. It is defaulting into the headline
   population. This is the highest-value line in the report and it costs nothing.
2. **UNMAPPED LOCATIONS.** Rows already collected, already stored, unusable because a
   region string has no country. The cheapest coverage in the project.
3. **SILENT LIVE SOURCES.** A collector that stopped is indistinguishable from a quiet
   market until you check.
4. **REVIEW DUE.** Sources whose access basis has not been re-read inside
   `review_interval_days`.

Handle those before going looking for anything new. A source you already have and are
throwing away is worth more than one you have to build.

## Step 1 — sweep

`references/discovery-channels.md` is the channel list, ordered by how early a channel
sees a new entrant rather than by how convenient it is to read. Work down it.

The ordering matters more than the list. A new provider becomes visible in a fixed
sequence — funding or datacentre announcement, then an NVIDIA partner listing, then its
own pricing page, then the comparison aggregators, then everybody's listicles — and the
gap between the first and fourth step runs to months. A process built on aggregators
alone will always be the last to know. Read the leading channels for names, and the
lagging ones only to catch what the leading ones missed.

Do not skip the in-panel channel. New names arrive inside sources already collected —
a marketplace adds a host, a catalog adds a vendor — and that channel is free, automatic,
and the one that fires first for the providers most likely to qualify.

## Step 2 — screen

`references/admission-rubric.md` has the six gates. Apply them in order and stop at the
first failure; they are ordered cheapest-to-check first on purpose.

The screen is deliberately harsh. This index publishes a gap rather than a number it
cannot defend, and a source that a stranger cannot re-read from a public page or a public
API (a free self-service key is fine, a customer account is not) is not defensible however
good its data is. That is why AWS Capacity Blocks is a permanent
`rejected` row despite being the best forward-price signal in the market.

## Step 3 — record every one of them

Write a row in `config/source_registry.yaml` for **everything screened, including
rejections.** A rejection with a reason is an asset: most rejections here are conditional
("needs an API key", "no on-demand rate", "JS-only page"), and conditions expire. Give
each one a `recheck` line naming the specific thing that would change the answer, so the
next sweep is a diff rather than a rediscovery.

Rules for a row:

- `discovered_via` names the channel. After three sweeps this tells you which channels
  actually work and which are ritual.
- `last_reviewed` is the date the access basis was read, not the date the row was typed.
- Never write a price into the registry. Prices live in `config/providers/*.yaml` with a
  `last_verified` date and a staleness clock, or they come from a collector.
- Never record a URL you have not opened. If a search result gave you a pricing page and
  the page 404s, that fact goes in the row.

## Step 4 — put it in a region block

Every source names one or more blocks from `config/regions.yaml`. If a source serves a
block that does not exist yet, add the block; if it serves one currently marked `watch`,
promote it to `shadow` and say in `notes` what changed and on what date.

A non-EEA source is worth building. It cannot enter a published print — `normalise.py`
filters on country and only `factors.yaml:eu_eea_countries` gets through — so there is no
risk of contaminating the headline, and 180 days from now the block will have the history
its first print needs.

## Step 5 — write the scan up

`research/source-scans/YYYY-MM-DD.md`, using the previous scan as the template. That
directory is deliberately one level below `research/`, which the site generator globs
non-recursively, so scans are a working record and not a publication.

Lead with what changed, not with what you did. If the sweep found nothing, write that in
two lines and stop — a scan that found nothing is a real result and padding it is how the
document becomes something nobody reads.

House voice applies (`STYLE.md`): specific numbers, no rule-of-three, no neat bow. Run the
`humanizer` skill over it before committing.

Then append a `scans:` entry to the registry so `sources` can report how stale the sweep is.

## Step 6 — know what promoting a source costs

Writing a collector is the easy half.

Adding a source to the calculation path changes the constituent population, which
GOVERNANCE.md section 1 makes a **minor methodology version bump**: the change, a version
bump in `config/factors.yaml`, a CHANGELOG entry, `python -m tci.run docs`, and one
publication's notice before the first print computed under it. Segment classification in
`factors.yaml:segments` is part of that same change — an unclassified provider defaults to
`neocloud` and lands in the headline, which is the safe default but not a decision.

None of that applies to a registry row. Recording a candidate changes nothing and needs
no ceremony, which is exactly why the register should be generous and the panel should not.

## The rules that keep this honest

- One request per source per day, honest User-Agent, robots.txt respected, no web page
  behind a login. A public API is admissible with or without a free self-service key
  (gate 1 has the definition); a price that needs a customer account, an approval or a
  sales conversation to read is not a public price.
- Never invent a provider, a price, a URL or a date. Every claim in a scan report is
  either a query against `data/eucri.db` that the report makes reproducible, or a page
  that was actually opened.
- A hostile page gets a manually verified static entry with a `last_verified` date, not a
  brittle scraper. The staleness clock is more honest than a parser that silently returns
  last month's number.
- Prefer the operator's own endpoint over an aggregator of it. Aggregators of aggregators
  are never index inputs — the double-counting is invisible and unfixable after the fact.

## Files this process owns

| File | What it holds |
|---|---|
| `config/source_registry.yaml` | every source ever screened, with status and reason |
| `config/regions.yaml` | region blocks, publication status, the population gate |
| `research/source-scans/*.md` | one report per sweep, not published to the site |
| `src/tci/sources.py` | loaders, coverage, the report |
| `tests/test_sources.py` | drift guards between the registers and the calculation path |
