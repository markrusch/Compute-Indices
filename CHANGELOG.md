# Changelog

All methodology-affecting changes require an entry here **before** the lock regenerates
(see GOVERNANCE.md §1). Format: version, date, what changed, why.

## 0.3.0-dev — 2026-08-16

A structural rebuild. Validation against the stored observations showed the v0.2.0 index
was measuring its own sampling rather than the market: over 14 collection days the
published headline moved +10.03% while the matched-pair market move was **+0.00%**, and
a +10% shock to four of six constituents moved the print by **exactly 0.00%**. The root
cause was not the estimator but the unit definition. Changes, in order of impact:

- **Node-size floor 8 → 2 GPUs** (`filters.min_gpu_count`). The 8-GPU floor admitted
  marketplace inventory on **1 collection day in 10** and discarded essentially all price
  discovery: vast.ai posted 9 distinct EU H100 prices spanning +12.3% (~179% annualised
  vol) and almost none of it reached a print. Marketplace supply is 1/2/4-GPU. Measured
  within one venue on one day, the per-GPU discount **saturates at 2 GPUs** (1×=1.000,
  2×=0.951, 4×=0.916, 8×=0.916), so 2/4/8-GPU offers are comparable within ~4% and need
  no adjustment, while the ~9% 1-GPU small-order premium stays excluded rather than
  normalised away. **Effect on the level**: the headline now responds to marketplace
  price moves at all.
- **Aggregation unit: offer, not provider.** A weighted median over ~6 providers has
  ∂I/∂p = 1 for one name and 0 for every other, and steps discontinuously when the 50%
  crossing point moves. A capacity-weighted median over many offers is the SOFR
  construction — locally smooth, and it always lands on a price someone actually quoted.
- **Market-segment segregation.** The constituent distribution is bimodal (measured
  separation **5.4 sd** between the neocloud/marketplace cluster and the hyperscaler
  catalog cluster); a mean across that gap falls in an empty interval and prices nothing.
  The headline population is marketplace + neocloud; hyperscaler catalog rates move to
  `EU-CRI-H100-HS`, where they are the correct object of measurement. New series
  `EU-CRI-H100-NC` and `EU-CRI-H100-HS`; `EU-CRI-H100-CLOUD` is retired.
  **Effect on the level**: removes AWS/GCP catalog rates from the headline.
- **Count-based trim replaces percentile winsorising.** `winsorise_pct: [5, 95]` was a
  **no-op for the entire history of the index** — at n=6, nearest-rank p5/p95 *and*
  p10/p90 both resolve to (min, max) and clamp nothing; percentiles only begin binding
  at n≥10, and even there only on the upper tail. Replaced with clamping the k highest
  and k lowest, k by panel size.
- **Provider weighting is tier-only.** Capacity is unobservable for every list source, so
  `default_capacity: 8` made AWS and Seeweb identical while giving vast.ai — which
  honestly discloses a real 2-GPU offer — a *lower* weight than either, inverting the
  hierarchy the executable multiplier exists to express. Capacity still weights offers
  *within* a provider, where it is genuinely observed. Scheduled weight reviews and the
  `bootstrap_weights` / `no_weight_history` flags are retired from the calculation path.
- **No assumed variant factors.** `H100_NVL_94GB: 1.0 # default until measured` is
  removed; H100 PCIe becomes its own class rather than being folded into SXM. A factor
  may enter only when measured from same-venue, same-day, same-SKU pairs. Measured
  candidates are published under `measured_factors` and are *not* in the calculation path.
- **FX look-ahead fixed.** `latest_rate()` returned the globally latest ECB rate with no
  date bound, so backfilled prints for 2026-07-18/19 were computed with a rate published
  on 2026-07-21. Replaced with `rate_for(conn, date)` (`fx_date <= date`), regression
  tested. The EUR leg is now stated as **T-1**: the ECB publishes ~14:00 UTC, after the
  11:00 UTC cut-off, so same-day FX was never achievable. Providers quoting natively in
  EUR are converted from the native amount at print time rather than at collection.
- **New classes and series**: H200, B300, H100-PCIe. New sources: Scaleway (public
  Instance API, French/sovereign, also carries B300 in the EEA) and Azure Retail Prices
  (10 EEA regions). Switzerland is **not** in the EEA and is excluded from both.
- **Scope statement corrected.** Regulation (EU) 2025/914 (applying 1 Jan 2026) removes
  non-significant benchmarks from BMR Titles II–VI, but new Article 2(1c) separately
  applies Article 19 to *any commodity benchmark based on contributed input data*, exempt
  only below EUR 200m average notional over 12 months. The research-publication
  disclaimer is retained, and the settlement-grade preconditions are now published and
  falsifiable rather than implied.
- 2026-09-04 (still 0.3.0-dev, pre-launch): **methodology lock rehashed for an editorial
  change — no numeric effect.** The project was licensed (Apache-2.0 for the software,
  CC BY 4.0 for the documentation, separate terms for the index data), which added a
  two-line SPDX/copyright header to every source file including three hash-locked ones:
  `index.py`, `normalise.py`, `weights.py`. The diff is 6 inserted comment lines and zero
  deletions; the golden print, weighting, normalisation, trim and estimator tests all
  reproduce their pinned values unchanged. `methodology_version` is deliberately **not**
  bumped: this is editorial under §1's patch definition, and a bump would discard the
  stored weight review (`_load_review` drops a review whose methodology version differs)
  and force a recomputation for a licence header. Recorded here because the lock hash
  moving without a version change is exactly the kind of thing an audit trail exists to
  explain.

## Mobile layout — 2026-09-06 (presentation; desktop unchanged)

The site had responsive rules but no mobile layout. Measured in headless Chrome at 320,
375, 414, 768 and 1280px across all five pages, before and after.

- **Nav collapses behind a Menu button below 860px**, built from a checkbox and its
  label so it opens with no JavaScript. The checkbox is `opacity:0` rather than
  `display:none` on mobile, which keeps it in the tab order: `display:none` would have
  left the menu working by mouse and unreachable by keyboard. Links become full-width
  44px rows; measured at 44px with the menu open.
- **Sticky chrome cut from ~92px to 60px.** Only the masthead bar stays stuck on mobile;
  the ticker band scrolls away. On a landscape phone the bar unsticks entirely.
- **The as-of stamp is no longer hidden.** A leftover one-line media query was hiding
  `.tickerbar__stamp` below 720px, which was the only piece of live state the small
  screen was not shown, and it is the cell the refresh script rewrites. It now wraps onto
  its own row.
- **Sticky offsets are tokenised** (`--sticky-h`). Anchor targets were hard-coded to the
  desktop header height, so every link from a table of contents mis-scrolled on mobile.
- **Fixed real horizontal overflow.** Citation URLs in the footnote strip are unbreakable
  130-character tokens; at 375px one of them was pushing the document to 645px of
  scrollable width. All five pages now measure zero overflow at every tested width.
- The headline table drops its Region column on mobile (constant "EU/EEA", already
  stated in the section dek) and lets the gap-reason column wrap, which is what removes
  the last horizontal scrollbar.
- Also fixed a pre-existing bug at all widths: a gap-reason chip is longer than a 220px
  tile and `.chip` is nowrap, so the text was being clipped in every sub-index tile.

Nothing is hidden on mobile that the desktop shows, apart from the decorative hero wave
and a constant table column; a test now pins that list.

## Research notes revised for house voice — 2026-09-06 (prose; one correction)

All three research notes were rewritten against STYLE.md and the Wikipedia-derived
"Signs of AI writing" pattern set. Prose only. Every figure, date, table, citation and
finding was held fixed and the invariance was checked mechanically, by diffing the
multiset of numbers, money amounts, percentages, ratios, dates, footnote markers, URLs
and code spans between the old and new text.

- **Notes 2026-01 and 2026-02 now use "I" rather than "we"** (17 occurrences). STYLE.md
  has always required it: the project is one person, and the plural was a tell.
- **Em and en dashes in prose: 25 → 3, 26 → 6, 35 → 3.** STYLE.md budgets "one or two a
  post"; the notes were running roughly one every 70 words. What survives is numeric
  ranges, the cited IEA report title, and the sign-off.
- **One factual correction, in note 2026-03.** The note said RunPod "moved three times in
  35 days". Three distinct prices imply at least two transitions, not three, and the data
  shows exactly two (2026-08-05, $2.99 → $3.29; 2026-09-04, $3.29 → $3.49). Corrected to
  "twice". This is a correction to a published note and is recorded here rather than made
  silently.
- **One mislabelled table header, in note 2026-01.** The column reading "Distinct prices
  observed" contained prices, not counts. Relabelled "Price(s) observed, USD/GPU-hr". No
  cell value changed.
- The notes keep their original `EU-CRI` naming in source. They record what was true when
  written, and the site rebrands their prose at render.

## Repository rename — 2026-09-06 (lock rehashed, editorial, no numeric effect)

The Python package was renamed to match the published brand: import `eucri` → `tci`,
distribution `eu-compute-index` → `tci-compute-index`, CLI `python -m eucri.run` →
`python -m tci.run`. The three GitHub Actions workflows were updated in the same commit
so the scheduled daily run never sees a half-renamed tree.

- **The lock was rehashed and `methodology_version` was deliberately not bumped.** Three
  hash-locked files (`index.py`, `normalise.py`, `weights.py`) changed, and the lock also
  hashes each file's relative path, which moved from `src/eucri/` to `src/tci/`. The
  content diff is **seven import lines and no logic**; the golden print, weighting,
  normalisation, trim, estimator and FX regression tests all reproduce their pinned values
  unchanged. This is editorial under GOVERNANCE.md §1's patch definition, and a bump would
  discard the stored weight review for an import statement. Same precedent as the SPDX
  headers on 2026-09-04. Hash `903fcfec…` → `5c61ce6b…`.
- **`data/eucri.db` is deliberately unchanged.** It is a storage path, not a brand
  surface, and renaming it would move a committed binary the daily job writes to. It is
  the one remaining `eucri` string in the tree.
- **Series identifiers are unchanged**: the database, `latest.json` and
  `index_history.csv` still key on `EU-CRI-*`. That rename is a separate governed change
  owing an old→new mapping and an effective date, and is not bundled here.
- **The collector User-Agent changed** from `EU-CRI-collector/x.y.z` to
  `TCI-CRI-collector/x.y.z`; SOURCES.md is updated to match, since it documents the UA as
  part of the collection basis.
- **Forward-looking documents carry the new name** (README, NOTICE, DATA-TERMS,
  GOVERNANCE, LICENSE-docs, STYLE, SOURCES, DESIGN, METHODOLOGY). This CHANGELOG and
  research notes 2026-01/02 are **not** rewritten: they record what was true when written.
  The site rebrands their prose at render instead, holding out code spans.
- The GitHub repository name and the `markrusch.github.io/Compute-Index/` Pages URL are
  **unchanged**, on purpose. That URL is the attribution target in DATA-TERMS §6, and it
  should change exactly once, when a real domain lands.

## Presentation — 2026-09-06 (no methodology change, no version bump)

The published site was rebranded to **TCI — The Compute Indices** and rebuilt on a new
visual system (dark ground, two-tone red, Outfit + JetBrains Mono). No calculation, no
config, no stored data and none of the five hash-locked files were touched; the
methodology lock is unchanged. Recorded here because the *names a reader sees* moved,
and that is the kind of thing an index owes an audit trail even when no number did.

- **Display rename, not a data rename.** The site publishes `TCI-CRI-*`; `daily_index`,
  `site/data/latest.json` and `index_history.csv` still carry the original `EU-CRI-*`
  keys. Renaming a stored identifier owes readers a published old→new mapping and an
  effective date (GOVERNANCE.md §1), so it stays a separate, governed change.
  `site.display_series()` is the single boundary where a stored key becomes a published
  name, and the Data page states the mismatch outright rather than leaving it to be
  discovered inside a download.
- **Embedded documents are rebranded at render time**, prose only: fenced blocks and
  inline code are held out, because `--series EU-CRI-H100` in METHODOLOGY.md is a
  database key a reader is meant to paste, not a brand.
- **One theme.** The brand's ground (#0B0C0D) is the only page background it allows, so
  the light palette and the Auto/Light/Dark toggle are gone rather than left inert. The
  site test now asserts the opposite contract: an OS set to light must render the same
  page.
- **Fonts are self-hosted** (`site/assets/fonts/`, SIL OFL 1.1) rather than loaded from
  Google's CDN. Same files, same subsets, same rendering — but the site keeps making
  zero off-origin requests, and no visitor's IP reaches a third party.
- **Two defects fixed on the way through**: the constituents table emitted
  `badge--l1/l2/l3` while the stylesheet defined only `badge--t1/t2/t3`, so seven tier
  badges per print rendered unstyled on the live site; and `#hero-usd`/`#asof`, which the
  refresh script rewrites every five minutes, had no `aria-live`.
- **New page**: `data.html` — downloads, series identifiers, terms in short, and the
  citation format. The settlement-grade precondition checklist on the governance page is
  parsed out of GOVERNANCE.md rather than retyped, and reports a condition the document
  is silent about as "not stated", never as met by inference.

## 0.2.0-dev — 2026-07-19

Adaptive, data-driven weighting — standard index procedure (scheduled reviews,
concentration caps, chain linking) adapted to a young, fast-moving market:

- **Scheduled weight reviews** (new `src/tci/weights.py`, hash-locked): constituent
  weights are recomputed each Monday from the trailing 28-day observation window —
  provider weight = median daily (qualifying capacity × tier multiplier) × presence
  ratio — and held fixed between reviews. Rationale: same-day capacity weighting let a
  single day's listings swing constituent influence; review weights make the weighting
  data-driven yet stable, and each review is stored append-only (`weight_sets`) and
  auditable via the new `weights` CLI command. Bootstrap rule below 5 collection days:
  same-day capacity weighting, prints flagged `bootstrap_weights`.
- **Concentration cap**: no constituent above 25% of a print's weight; excess
  redistributed pro-rata; included weights published as shares summing to 100.
  Rationale: dropout sensitivity showed a single marketplace (vast.ai, 53% of headline
  weight) could set the median alone; the memo's own rule ("if any single source moves
  the index >5%, cap its weight") is now structural, as in mainstream commodity
  benchmarks. **Effect on the level**: the headline no longer prints the dominant
  marketplace's ask when that venue alone crosses 50% of weight.
- **Model classes**: the unit definition generalises to classes (H100, A100, B200),
  each with a reference variant and published per-variant factors (replaces the flat
  `adjustment_factors` map). New class series `EU-CRI-A100` / `EU-CRI-B200`, published
  once they clear the same ≥5-provider gate.
- **EU-CRI-COMPUTE**: chain-linked composite of class series (base 100). Class basket
  shares = observed qualifying capacity share at each review (floor 5% / cap 75% when
  ≥2 classes are eligible). Chaining means reweights never jump the level, and the
  basket migrates across hardware generations without methodology changes.
- Governance clarification: a scheduled weight review executes a fixed published
  formula with no discretion — it is a data update, not a methodology change; the
  formula and its parameters remain hash-locked.

## 0.1.0-dev — 2026-07-18

Initial construction. Unit definition, filters, weights, and aggregation per the EU-CRI
methodology memo (17 Jul 2026):

- Unit: H100 SXM 80GB GPU-hour, on-demand, 8-GPU NVLink node, EU/EEA datacenter,
  excl. storage/egress; USD primary, EUR at ECB reference rate.
- Aggregation: per-provider representative price (min executable ask, else list price),
  winsorised 5/95 (nearest-rank, clamp), capacity-capped weighted median
  (cap 64, default 8, executable ×2), minimum 5 providers.
- Deviations from the memo, deliberate: static entries excluded (not just warned) when
  `last_verified` > 90 days; junk-listing guards (price band $0.25–$25.00, ≥8 GPUs,
  datacenter-verified only); >30% day-over-day constituent moves flagged for review.
- Governance: append-only observations and prints (trigger-enforced), constituent-level
  audit table, METHODOLOGY.lock hash guard in CI.
- 2026-07-18 (still 0.1.0-dev, pre-launch): capacity weighting tightened after the
  first live print — capacity counts as observable only for executable marketplace
  listings; list-price catalog rows always carry the default weight. Rationale: a
  hyperscaler catalog enumerating one instance type across N regions is not N units
  of available capacity, and the sum rule let aws/azure/gcp each hit the 64-GPU
  weight cap and set the median. Also: known sub-node configurations (e.g. 1x H100
  instances) are excluded at any tier, not only for executable asks.
