# Changelog

All methodology-affecting changes require an entry here **before** the lock regenerates
(see GOVERNANCE.md §1). Format: version, date, what changed, why.

## 0.6.0 — announced 2026-09-11, effective 2026-10-01 (notice 2026-N3)

- **US reference block.** `blocks.US` and `regional_series.EU-CRI-H100-US` in factors.yaml:
  the headline's unit, estimator, weights and gate applied to offers delivered from the
  United States. `normalise.py` takes the block's countries as a parameter (default
  EU/EEA, so every existing series computes exactly as before; verified by
  `reproduce`). Voltage Park enters the panel; it has no EU/EEA rows.
- **EU-US basis.** `basis_series.EU-CRI-H100-BASIS-US` = `EU-CRI-H100` minus
  `EU-CRI-H100-US`, USD per GPU-hour with a EUR companion, computed by the new
  hash-locked `src/tci/basis.py`. A day on which either leg gaps is a gap in the basis.
- **Collection.** RunPod records a second, US row for a GPU type only when its own
  datacentre-stock query reports stock in a US datacentre that day. Voltage Park rows
  carry country US, on the provider's statement that it operates only there.
- 0.5.0 frozen under `config/methodology/0.5.0/`.

## 0.5.0 — announced 2026-09-11, effective 2026-09-22 (notice 2026-N2)

Constituent changes, and one correction to the FX rule. Full text and expected effect in
`config/notices.yaml` (2026-N2).

- **Panel.** Verda, Nebius and Lambda enter the EU/EEA population through the gpuhunt
  catalogues and DigitalOcean through its pricing page (Amsterdam); Oracle enters the
  hyperscaler segment; OVHcloud enters the H100 PCIe class through its order-catalogue
  API; vast.ai is admitted to H100 PCIe, H200, B200 and B300. Estimated effect on the
  last full panel (7 September): $3.25 -> $3.49/GPU-hr, a composition step from sellers
  priced above the old median, stated in the notice so it is not read as a price move.
  The static `datacrunch` entry leaves the panel (Verda is the same company) and Nebius's
  static entry is replaced by its feed. `config/sovereign.yaml` names verda instead of
  datacrunch.
- **FX.** `fx.strictly_before: true`. The EUR leg is T-1 on every day. Until now the rate a
  print used depended on the time its run finished, and a recomputation could pick up a
  rate the original print never saw: 120 of 479 stored prints could not be recomputed with
  the rate they were published at until the recorded rate was made the reproduction input.
- Hash-locked parameter snapshot of 0.4.0 frozen under `config/methodology/0.4.0/`.

## 0.4.0 — announced 2026-09-08, effective 2026-09-15 (notice 2026-N1)

The first version without the `-dev` suffix: from here the lock generator refuses any
change to a released version's hash, and every change is a new version.

- **Norway.** `eu_eea_countries` quotes every code. Under YAML 1.1 the bare `NO` parsed as
  boolean false and Norway had never been in the constituent population (126 azure
  norwayeast rows dropped by 2026-09-08). The 0.3.0-dev snapshot keeps the bug on purpose,
  because the prints stored under it were computed without Norway.
- **Node floor at collection.** The gpuhunt collector discarded every instance below 8
  GPUs while the published floor has been 2 since v0.3.0. From 2026-09-15 it keeps
  instances of 2 GPUs and up for the providers that were already constituents
  (`LEGACY_FLOOR_UNTIL`); rows from before that day cannot be recovered.
- **Explicit panel, same membership.** `factors.yaml` gains `panel`: provider, segment,
  and for each collector the classes its rows may enter. `normalise.py` admits a row only
  if (provider, collector, class) is on the panel; anything else is stored and listed in
  the audit set as `not_in_panel`. Before this, an unlisted provider defaulted to the
  neocloud segment, so adding a collector was a silent constituent change. The 0.4.0 panel
  is exactly the population that could reach a print before it. Verified: every stored
  print recomputes to the same value, counts and constituent set.
- **Methodology succession.** `config/methodology/succession.yaml` lists every version with
  its effective date, and each print is computed under the version live on its date.
  Superseded versions are frozen snapshots under `config/methodology/<version>/`, each with
  a rendered METHODOLOGY.md; the lock records every snapshot's hash and refuses a change to
  one. The reason is practical: GOVERNANCE.md requires one publication's notice before a
  change applies, and until now that depended on merging the change on the right day.
- The 0.3.0-dev parameter set was frozen as a snapshot, with its implicit panel written
  out (a re-expression with no numeric effect, verified by recomputation).

## Reproduction, digests, and a marketplace restored — 2026-09-11 — no methodology change

- **Fixed: no H100 had reached the index from vast.ai since 2026-09-08.** The tenor work of
  that day removed the per-chip `gpu_name` filter so that unmapped silicon would show in the
  logs. The endpoint clamps every response (64 offers, as Computable measured on 2026-08-22)
  and the query was ordered by price ascending, so it returned only the cheapest offers on
  the whole marketplace, which are consumer cards. The
  8-10 September runs died on the IntegrityError fixed the same day and stored nothing, so
  11 September is the only session the defect can be seen in: two vast.ai rows, both RTX
  3060s in the US, and a headline gapped at 4 of 5 providers. The collector
  now reads one chip per request (a one-element `gpu_name in` filter, the operator already
  proven live on this endpoint), re-reads a full book in descending order,
  stores per-chip book statistics on every row, and fails the run if nine chips return zero
  offers. The query shape is the one Computable hardened live (Apache-2.0). This restores
  the population the published methodology describes; it is a correction, not a change.
- **`python -m tci.run reproduce`.** Recomputes every stored print from stored observations
  under the version live on its date, on an in-memory copy of the database, and compares
  value, EUR value, FX, provider and executable counts, flags, version and the full
  constituent set with the latest stored revision. With `--published` it also recomputes
  the digest of every published print file. On 2026-09-11: 479 of 479 prints matched and
  504 of 504 published digests matched; the 14 rows of the retired `-CLOUD` series are
  reported as RETIRED rather than claimed.
- **Found by it:** a print's FX was not a reproducible input. `rate_for` took the latest
  rate dated on or before the print date that existed when the run happened, so a later
  recomputation could convert at a rate the print never used (120 prints). The rate a
  print used is recorded with it, and `reproduce` now converts at that rate. The rule
  itself changes in v0.5.0.
- **Published print files.** `site/data/prints/YYYY-MM-DD.json` carries every series'
  latest revision, its constituent audit set and a sha256 digest of that content;
  `latest.json` carries the revision and digest of each series. `tests/test_reproduce.py`
  runs both checks in CI.
- **Sources collected in shadow from 2026-09-11.** gpuhunt now reads oci, lambdalabs,
  verda and nebius as well as aws/azure/gcp, and five classes instead of one, pinning the
  form factor from each provider's instance name and skipping any row it cannot pin.
  Nine collector recipes vendored from the Computable GPU Index (OVHcloud, Civo, CoreWeave,
  Voltage Park, DigitalOcean, Latitude.sh, Hyperstack, Crusoe, Lambda's pricing page) store
  every GPU row their surfaces publish, under TCI's own User-Agent. RunPod's collector adds
  a separate request for per-datacentre stock. None of this reaches a print before a
  version admits it to the panel.
- **Migration 0004** widens the stored `term` vocabulary to the tenors those surfaces
  publish (1-, 3- and 6-month commitments, 2-year reservations, and `reserved_unspecified`
  for committed prices published as a range or a floor).
- `sources.py` gains a `shadow` status: a collector that runs daily and stores rows the
  panel does not admit.

## Daily run restored after four dark sessions — 2026-09-11 — no methodology change

The daily workflow failed on 8, 9, 10 and 11 September. No hash-locked file changed and
no published value was wrong; the index simply stopped producing one. Root cause and the
three fixes:

- **Cause.** The tenor work of 2026-09-08 made the collectors emit `tier='spot'` and
  `tier='interruptible'`, against an `observations` CHECK constraint that allowed
  `('executable','list')` only. Every run died on an IntegrityError. The full test suite
  stayed green throughout, because the collector tests assert on returned objects and the
  normalise tests assert on dicts: nothing in the suite ever wrote a row, so nothing
  looked at the seam between what a collector produces and what the database accepts.
- **Fix 1, schema (`0003_tenor_vocabulary.sql`).** The tier vocabulary is widened to
  include spot, interruptible and community, and `term` gains the CHECK it never had,
  covering on_demand and the three reserved tenors. Both stay closed lists: the
  constraint's value is catching a typo at write time, and `term` having no constraint at
  all is why a mistyped tenor would have stored silently. Index eligibility is unchanged
  and does not live in the schema — `normalise.py` decides it, and
  `tests/test_normalise.py` pins that.
- **Fix 2, failure isolation (`collectors/base.py`).** `run_collector` guarded the fetch
  but not the insert, so a persistence error escaped and aborted the whole run: no index
  computed, no site regenerated, nothing committed, for every other source too. Fetch and
  insert now share one fail-soft boundary, and a failed run records the reason in
  `runs.notes` rather than only in a CI log that ages out and is not public.
- **Fix 3, the workflow.** The commit step now runs even when the daily step fails.
  Observations are live prices that cannot honestly be re-collected for a past date, so a
  day's raw data is irreplaceable once the runner is torn down. Four sessions of
  collection were discarded because this step was skipped on failure. A commit made after
  a failed run is marked as partial in its message, and a new diagnostic step prints the
  per-source status and reason while the job is still on screen.
- **New `tests/test_collector_persistence.py`.** Eight tests covering the seam that was
  unwatched: every collector's fixture output is inserted into a real migrated database,
  every tier and term the collectors emit is asserted storable, the vocabulary is asserted
  still closed against junk values, one unstorable collector is asserted not to stop the
  others, and a rejected batch is asserted to leave nothing behind. Verified to fail
  against the pre-fix code.
- **8, 9 and 10 September are recorded as gaps** with `n_sources = 0`, backfilled so the
  outage appears in the published history rather than as three dates with no row at all.
  No observations exist for those days and none can be recovered; the collectors report
  live prices, and a past day cannot honestly be re-collected.

## Tenor and capability collection — 2026-09-08 — no methodology change

A scan for sources that could support a forward curve, and for compute types beyond the
three vast.ai models mapped so far. No hash-locked file changed. Every row added below is
structurally unable to reach a print: `normalise.py` admits only
`term == reference_unit.term` (on_demand) and `tier in (executable, list)`, and drops any
`gpu_model` that is not a configured reference variant. `tests/test_normalise.py` now pins
both guarantees, because they are the only thing separating this data from the headline.

- **Azure publishes a real term structure, and the collector was keeping one point of
  it.** The same OData query that already returns on-demand meters also returns 1-, 3- and
  5-year reservations, plus spot and low-priority meters. Measured live in westeurope on
  2026-09-08 for `Standard_ND96isr_H100_v5`: on-demand $15.98, 1-year $10.23, 3-year
  $7.01, 5-year $6.39, low-priority $3.20, spot $2.95 per GPU-hour. That is six observed
  points on one node from one request, and it is the only term structure available to this
  index from any source currently in the panel.
- **The reservation figure is not an hourly rate.** Azure returns a whole-term upfront
  total and labels `unitOfMeasure` "1 Hour", which is wrong in their feed. Taken at face
  value a 1-year reservation reads as roughly $700,000 per GPU-hour. The collector divides
  by term hours and stores the untouched upfront figure and the divisor in `raw_json`, so
  the derivation can be checked rather than trusted. A test pins it, because the failure
  mode is a well-formed, plausible-looking number that is wrong by four orders of
  magnitude.
- **Fixed: the Azure page cap was silently truncating.** `MAX_PAGES_PER_REGION` was 8
  against a 1,000-row page size, and westeurope alone returns 8,675 on-demand rows, so
  roughly 675 rows a day were being discarded with no log line. The cap is now 20 and
  hitting it logs a warning naming the region. A missing constituent nobody logged is
  indistinguishable from one that was never offered.
- **vast.ai publishes per-offer measured capability, and the collector was discarding
  it.** `RAW_FIELDS` reduced each offer to 21 pricing fields before storage. It now also
  keeps `dlperf`, `total_flops`, `gpu_mem_bw`, `pcie_bw`, `bw_nvlink`, `gpu_max_power`,
  `gpu_max_temp`, `disk_bw`, `inet_down/up`, `compute_cap` and the reliability fields. All
  were 100% populated on every datacenter-verified offer in a live probe. This is audit
  data; nothing here is read by the calculation path.
- **Research Note 2026-04 corrected.** It reported that no source in the panel discloses a
  fabric, a power envelope or a thermal limit. The measurement was of what the index had
  stored and reproduces unchanged; the attribution was wrong. For eight of nine sources the
  gap is the market's, and for vast.ai it was this project's. A dated correction is
  published at the head of the note.
- **vast.ai model coverage widened** from 3 mapped models to 16, adding A100 PCIe, L40/L40S,
  RTX 6000 Ada, RTX PRO 6000 WS, RTX 5090/4090/4080, RTX 3090/3080/3060, A6000 and A40.
  None is a configured reference variant, so all are collected and none is priced. The
  `gpu_name` filter was removed from the query so an unmapped model now appears in the logs
  instead of being invisible server-side.
- **vast.ai bid prices collected** as a second row per offer at `tier=interruptible`,
  making the bid-ask spread on the same machine observable rather than inferred.

**Available and deliberately not adopted:** vast.ai offers B200 and H200. `B200_SXM` and
`H200_SXM` are reference variants of published classes, so mapping them would add a
constituent to a published series. That is a minor methodology change under GOVERNANCE.md
§1 and needs a version bump and one publication's notice. It is left for the administrator
to decide rather than introduced through a collector edit.

## Methodology notices, and a research register change — 2026-09-08 — no methodology change

- **`config/notices.yaml` and `notices.html`.** GOVERNANCE.md §1 step 5 has always
  required one publication's notice before the first print under a new methodology
  version, and that requirement had no public surface: a change could satisfy every other
  step and still leave a reader to discover it in a changelog afterwards. Notices are now
  published on their own page, and any notice still awaiting its effective date raises a
  banner on the dashboard, directly above the print it is going to change. Every notice
  must state an expected effect on the level; "may affect the level" is explicitly not an
  acceptable value. A notice is never deleted, only withdrawn, because a reader who saw
  the announcement is owed the retraction. The register is not hash-locked and does not
  enter the calculation path.
- **Notice 2026-N1 published**, announcing the Norway and node-size-floor corrections
  recorded as found-not-fixed on 2026-09-07. Effective 2026-09-15 with methodology
  v0.4.0.
- **Research notes move to an institutional register.** Note 2026-04 was published with a
  personal byline, first-person process narration and a section proposing what to fix
  next, which reads as an internal document rather than an administrator's position. It
  has been revised: institutional byline, no first-person, unnumbered essay headings, and
  the remediation section removed. The remedies were already recorded in this changelog,
  which is where they belong. No figure, table or finding changed.
- **New `.claude/skills/research-note/` skill** carrying the register, the essay
  structure, the rule against publishing internal reasoning, and a pre-publication
  checklist. STYLE.md now separates the two registers explicitly: the newsletter is a
  person, a research note is the institution. The three earlier notes keep their numbered
  sections, because each cross-references its own section numbers internally and
  de-numbering them mechanically would break those references.

## Roadmap and distribution — 2026-09-07 — no methodology change

- **TCI-ERI withdrawn from the published roadmap.** The energy index card said "in
  development" on the strength of a day-ahead power collector that has never landed a
  row, which made it an intention advertised as a pipeline. The family is now TCI-CRI
  (live) and TCI-SRI (planned, and labelled as collecting nothing yet). The ENTSO-E
  collector stays in the tree; it is the claim on the website that was withdrawn, not
  the code.
- **`site/feed.xml`, an Atom feed of the research notes.** The research is the part of
  this project most likely to reach someone who never opens the dashboard, and there was
  no way to follow it except by checking the page. Every page advertises it via
  `<link rel="alternate">`. Only published notes with a date are included, so a planned
  note never appears as an entry pointing at an empty slot. The feed's `updated` stamp is
  the newest note rather than the build time, because the site regenerates daily and a
  feed that claims to change every day gets ignored.

## Research Note 2026-04 — 2026-09-07 — no methodology change

Published *A GPU-hour is a part number, not a unit*, an audit of what the index actually
records about the quality of the goods it prices. `interconnect` is not read by
`normalise.py`, `index.py` or `weights.py`, so no hash-locked file changed and no print is
affected. Three defects found in the audit trail, none fixed in this commit, because the
measurement is the case for the fix and a fix landing beside its own justification is
harder for anyone else to check.

- **Found, not fixed: `interconnect` is 100% populated and 0% observed.** Of 7,798 stored
  observations, none carries a value read from a field a source disclosed. 57.6% carry a
  constant hardcoded in a collector (`gpuhunt_.py` and `static_yaml.py` both write a
  literal `"NVLink"`; `runpod.py` and `vast_ai.py` take one from a `GPU_MODEL_MAP`). The
  other 42.4% is parsed out of a SKU or instance-name string. Remedy: split the column
  into an observed value plus a provenance marker, and let the observed value be null
  when nothing supplied one. On today's data that makes it null on every row, which is
  the honest reading.
- **Found, not fixed: 805 rows contradict themselves.** 13.4% of all SXM rows assert
  `gpu_model` ending in `_SXM` and `interconnect='PCIe'` at once, which is impossible:
  the two are mutually exclusive form factors. 123 are H100, 682 are A100. They come from
  `azure_retail._interconnect`, whose `else` branch returns `"PCIe"` for any SKU failing
  two substring tests, including `Standard_ND96is_H100_v5` — an HGX node with NVLink 4.0
  between its GPUs. Remedy: return null on an unrecognised SKU rather than guessing.
- **Found, not fixed: the column holds two incommensurable axes.** `NVLink`/`NVL`/`PCIe`
  describe the intra-node bus; `InfiniBand`/`Ethernet` describe the inter-node fabric.
  Only Azure ever produces the second, 678 rows of 7,798, so for eight of nine providers
  the fabric is not unknown but unrepresentable. Remedy: two fields.
- No source in the panel discloses a fabric, power envelope or thermal limit. Across all
  7,798 raw payloads the count mentioning InfiniBand, RoCE, NVSwitch, TDP, watts, cooling,
  fabric, bandwidth or Gbps is zero.

## Canonical domain — 2026-09-07 — no methodology change

`thecomputeindices.com` was registered and is now the canonical home. No hash-locked file
changed and no print is affected; this is publishing plumbing.

- **Canonical URLs, social cards and structured data.** Every page now carries
  `<link rel="canonical">`, Open Graph and Twitter card metadata, and a JSON-LD graph
  (Organization, WebSite, WebPage, plus Dataset on the dashboard). Before this the site
  lived at two host-shaped URLs with no canonical, so the mirror competed with the
  canonical site for the same content, and a shared link rendered as a bare grey URL.
  The home page's canonical is the bare domain, not `/index.html`, because that is the
  URL people actually link to.
- **`sitemap.xml` and `robots.txt` are generated**, the sitemap from the list of pages
  actually written, so a new page cannot be published and then left out of the index.
  `lastmod` is the print date rather than the build timestamp: the site regenerates daily
  whether or not anything changed, and claiming every page changed every day is how a
  sitemap gets ignored. `components.html` is disallowed — 164 KB, linked from nowhere.
- **`site/assets/og-card.png`**, rendered from `tools/og-card.html` through headless
  Chrome so it uses the real brand faces rather than an approximation. It carries no
  price: platforms cache a card for days, and a stale number presented as current is the
  one failure this project does not accept.
- **`CITATION.cff`** added, pinned to the methodology version rather than the package
  version, since it is the methodology that identifies which construction produced a
  print.
- **One `<script src>` now exists**: Vercel Web Analytics, at the same-origin path
  `/_vercel/insights/script.js`. Vercel already serves the canonical site and so already
  sees every request to it, so this reaches no party that was not already in the path —
  the claim the site makes, that no visitor's IP reaches a third party, is unchanged. It
  is inert on the GitHub Pages mirror, where the file does not exist, and inert
  everywhere until analytics is switched on in the Vercel project.
  `test_pages_are_self_contained` now permits exactly this one path and fails on any
  other, including a second copy of it.
- **`site/404.html`.** An unmatched path used to fall through to the host's default,
  which on Vercel is an unstyled white page — the worst place for a dark-only site to
  drop its theme. Both hosts serve `404.html` automatically, so this needed no routing
  change. It is noindex and absent from the sitemap.
- Repository URLs point at `markrusch/Compute-Indices`, and the published attribution
  string in DATA-TERMS.md §6 now names the domain. Anyone who cited the old Pages URL
  still resolves: GitHub redirects a renamed repository permanently.

## Unreleased — 2026-09-07 — no methodology change

None of the five hash-locked files changed, so `methodology_version` stays at
`0.3.0-dev` and no print computed to date is affected. Added a repeatable process for
finding new price sources, and ran it once.

- **New: `config/source_registry.yaml`.** Every price surface the project has screened,
  including the rejections, each with the reason and a `recheck` line naming what would
  change the answer. 29 rows against the 10 in `SOURCES.md`, because most rejections here
  are conditional and conditions expire.
- **New: `config/regions.yaml`.** Thirteen region blocks, one published (EU/EEA) and eight
  shadow. A shadow block's prices are collected and stored and published nowhere; the
  reason is in the file's header. Neither register is read by the calculation path, so
  neither is hash-locked. `tests/test_sources.py` asserts the EU_EEA block still matches
  `factors.yaml`, so they cannot drift apart quietly.
- **New: `python -m tci.run sources`.** Register, review clock, per-block coverage, silent
  live sources, unclassified provider names, and stored rows whose region never resolved
  to a country.
- **gpuhunt region map widened from 25 regions to 90.** 1,518 rows had been collected,
  stored and dropped for want of a country mapping, including London three times over and
  288 observations of gcp `asia-southeast1`. Costs nothing: the package downloads whole
  catalogs and queries them locally. Changes no published print, because `normalise.py`
  still filters on `eu_eea_countries`. GovCloud, AWS Local Zones and the China regions
  stay unmapped on purpose, each for a reason recorded in the collector.
- **Found, not fixed: `factors.yaml` has never contained Norway.** YAML 1.1 reads the bare
  token `NO` as boolean false, so `eu_eea_countries` holds `False` where it should hold
  `'NO'` and all 126 stored Norwegian observations have been dropped by the country
  filter since azure `norwayeast` started reporting on 2026-08-16. The fix is one
  character, but adding a country to the constituent population is a minor methodology
  change under GOVERNANCE.md §1 and needs a version bump plus one publication's notice.
  A strict xfail in `tests/test_sources.py` will fail the moment it is fixed.
- **Found, not fixed: `gpuhunt_.py` drops offers below 8 GPUs at collection.** The
  methodology floor has been 2 since v0.3.0, so the collector has been applying a
  stricter threshold than the published unit, before storage, on a source that cannot be
  re-collected. Same governance path as the Norway fix and they should share one notice.

Full scan report, including the five candidates found: `research/source-scans/2026-09-07.md`.

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
