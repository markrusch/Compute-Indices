# TCI roadmap — from a working index to a product people pay for

Written 12 September 2026, edited the same day after the L0–L2 merge landed. This is the
single source of truth for what is built, what is broken, what happens next, and what
"done" means at each step. Edit it in place rather than starting a new plan.

It is a private working document. Nothing in it is copied into published pages, which
state findings and never plans (`CLAUDE.md`, "Never publish internal reasoning").

---

## 0. How a session should use this file

1. Read `CLAUDE.md` first, then `GOVERNANCE.md` §1, then this file.
2. Work top-down: section 2 is the state, section 3 the invariants, section 4 what is due
   now. Sections 6 onward are the phased roadmap.
3. Every task has an **acceptance** line. A task is not done until that line is true and
   `pytest`, `ruff check src tests`, `mypy src/tci` and
   `python -m tci.run reproduce --published` all pass.
4. Anything that can move a published number needs a version, a notice, a CHANGELOG entry
   and a regenerated lock **before** it takes effect. No exceptions, including for
   "obvious" fixes.
5. Ask Mark only for the things in section 5. Everything else is yours to decide.

---

## 1. What this project is, in one paragraph

TCI publishes a daily reference price for renting GPU compute in the EU/EEA, computed
from public prices by open code, reproducible by anyone, and governed like a benchmark:
dated methodology versions, published notices before a change takes effect, an audit set
per print, and a digest per published file. The wedge against Silicon Data and ORNN is
not breadth. It is that a lender, an auditor or a counterparty can recompute every number
and trace it to a dated rule, and that Europe is priced as Europe instead of being
normalised away. The commercial path runs through people who need a defensible number:
lenders financing GPU fleets, funds and desks pricing the CME compute futures' regional
basis, and buyers negotiating term deals.

---

## 2. State of the world, 12 September 2026 (after the merge)

### 2.1 Published and live

- The L0–L2 stack is on `main` (PR #1). Live methodology version **v0.3.0-dev**; v0.4.0
  takes effect 15 September, v0.5.0 on 22 September, v0.6.0 on 1 October.
- **The headline printed $3.49/GPU-hr on 12 September on 6 providers**, after four
  sessions gapping at 4 of 5. The vast.ai per-chip fix is what restored it.
- The daily GitHub Action runs 11:00 UTC and commits `data/eucri.db` and `site/`.
- Pages: dashboard, basis, term, methodology, data, research, governance, notices,
  reliability.

### 2.2 The collectors have now run live

Section 4.2 is closed. On 12 September all 13 new sources stored rows, and
`python -m tci.run canary` confirmed **15 of 15 sources reporting**. CoreWeave failed that
morning on a page reshape — a schema.org block repeating the section anchors as plain text
— and the parser was re-anchored on the heading tag. The reshape is now a fixture.

The panel held exactly as designed: DigitalOcean, Lambda, Oracle and Verda all stored rows
on 12 September and all appear in the audit set as `not_in_panel`, moving nothing.

### 2.3 The honest caveats

- **The US leg has one session of evidence, at exactly the gate.** See §4.3 — the number
  the original monitoring query gave was about twice the real one.
- **Across every public source TCI collects, no commitment tenor of any GPU is published
  by three or more sellers.** The term table publishes each seller's own curve and no
  pooled figure. That is a finding, not a bug.
- **19 of 57 sessions have printed**, 33.3%, and the current unbroken run is 1 session.
  This is now on `reliability.html` rather than known privately.

---

## 3. Invariants: break one and the project loses its reason to exist

Full text in `CLAUDE.md`. The short version:

1. **A gap stays a gap.** Never interpolate, never carry forward, never show an old print
   as current.
2. **The database is append-only.** A correction is a new revision. Every read takes
   `MAX(revision)`.
3. **The methodology is hash-locked and dated.** Always pass `for_date` to
   `config.load_factors` in anything that computes a print.
4. **The panel decides admission.** A new collector stores rows and moves nothing until a
   version admits it.
5. **Code changes must reproduce the record.** If `tests/test_reproduce.py` fails after a
   refactor, the refactor changed published numbers and is a methodology change.
6. **`site/*.html` is generated**, except `components.html`. Edit `outputs/site.py`.
7. **Contributed prices never enter the repository.**
8. **Never invent a number, a citation or a source.**

---

## 4. Due now

### 4.1 Merge the three branches — DONE

`main` contains `639ce12`. v0.4.0 still takes effect on 15 September, so the timing
contingency in the original plan did not fire.

### 4.2 Prove the collectors live — DONE

All four acceptance criteria were met on 12 September: the headline printed on 6
providers, vast.ai contributed H100 SXM rows again, every new source stored rows, and no
collector raised inside `run_collector`.

Use `python -m tci.run canary` for this from now on. It collects into a temporary database
and reports what stopped reporting, without touching the record.

### 4.3 Watch the US leg before 1 October

**The query in the original plan measured the wrong population.** It counted every
provider with US H100 SXM rows, including AWS, GCP and Oracle. `EU-CRI-H100-US` draws on
the headline population, which is marketplace and neocloud only. On every session before
12 September that query read 2 or 3 where the answer was 0 or 1, and on 12 September it
reads 8 where the answer is 5.

**The original plan also says the US series "computes but does not publish" until 1
October. It does not compute at all.** v0.6.0 is what defines the series, a print is
computed under the version live on its date, and v0.6.0 is not live until 1 October. There
will be no US print history when the decision below comes due, so the watch has to run
over observations.

```bash
python -m tci.run reliability --coverage EU-CRI-H100-US --days 21
```

That replays the unit definition over stored observations through the same
`normalise_observations` and `provider_offers` the calculation uses, and prints the stored
record beside the replay.

**Where it stands.** 12 September was the first session any US candidate but vast.ai
reported, and all five qualified: vast.ai, RunPod, Lambda, DigitalOcean, Voltage Park.
Five against a gate of five, with no margin. Every prior session was 0 or 1.

**Decision point, 28 September.** If the median over the preceding 10 sessions is below 5,
do not lower the gate — that breaks the like-for-like construction that makes the spread a
basis. Either (a) admit one more US seller in a v0.7.0 with its own notice (CoreWeave and
Crusoe are collected in shadow and are US-heavy), or (b) postpone N3 to a later effective
date with an amended notice. Write the choice into the CHANGELOG with the counts that
drove it.

### 4.4 Static entries expiring

**Seeweb is already past its 45-day warning** (`last_verified: 2026-07-18`, 56 days on 12
September) and is excluded at 90 days, around 16 October. It is currently an included
constituent in the headline at $2.16, the lowest price in the panel. Either re-verify on
seeweb.it and update `last_verified`, or retire the entry in a version with a notice —
removing a constituent moves the print.

### 4.5 Notice N2's quantified effect has to be restated on the day

N2 says v0.5.0 would take the headline from $3.25 to $3.49 on the 7 September panel. The
headline reached $3.49 on 12 September under v0.3.0-dev for an unrelated reason: vast.ai
returned and the weighted median landed on RunPod's price. The two figures coinciding is a
coincidence, and a reader comparing published prints on 22 September will see a step of
roughly nothing.

Nothing needs withdrawing. N2 already commits to the right remedy — "the first print under
v0.5.0 will state its size against a v0.4.0 recomputation of the same day" — and nothing
could produce that number until now:

```bash
python -m tci.run effect --date 2026-09-22 --before 0.4.0 --after 0.5.0
```

**On 12 September's observations the answer is zero.** Both legs print $3.49; the panel
widens from six sellers to eight and the two entrants land either side of the median. The
same run shows `EU-CRI-H100-NC` going from a gap to $3.8368, which is the one thing v0.5.0
actually unlocks on that day.

So the CHANGELOG entry for the 22 September print carries this figure, computed on the
day's own data, not N2's 7 September estimate. Do not let it be skipped because the step
turned out small — a notice that quantified a step readers cannot see is exactly the thing
the after-the-fact figure exists to settle.

---

## 5. What only Mark can do

| # | Action | Why it blocks | Deadline |
|---|---|---|---|
| 1 | ~~Push and merge the three branches~~ | done 12 Sep | — |
| 2 | Re-verify or retire the Seeweb static price | Constituent drops out at 90 days, and it is already past the warning | 16 Oct |
| 3 | Send contributor outreach (drafts in the build plan) | L5 has no data without contributors | Sept–Oct |
| 4 | Lawyer review: contributor agreement, data terms, and the competition-law question on sharing current prices between competing sellers | Before the first real contribution is accepted | before first contribution |
| 5 | Decide ABN AMRO vs Optiver, and whether TCI stays a side project | Changes how much of section 9 is realistic | Q4 |
| 6 | Decide the legal entity and whether to keep the "not a benchmark under BMR" posture | Needed before any paid contract | before first revenue |
| 7 | Reply to Sixtytwo on the joint research note (§7.4) | The measurement half of L4 depends on it | Sept |

---

## 5A. Phase L0.5 — the pipeline must not break the pipeline — DONE 12 September

Added because the daily Action is unattended, holds the only copy of a day's collection
until it commits, and had never been tested by anything. Both of this repo's data-loss
incidents were inside that YAML rather than in the Python.

- **The daily entrypoint runs in CI.** `tests/test_pipeline_smoke.py` runs
  `python -m tci.run` as a subprocess against a throwaway copy of the tree, with no
  network, and requires every generated page back after deleting them first.
- **The workflows are under test.** `tests/test_workflows.py` asserts the properties whose
  absence caused the incidents: the commit step runs on failure, the run never
  force-pushes, it cannot race itself, the schedule is the published 11:00 UTC, every job
  runs the same Python, and the daily job installs the package as a user gets it. Each was
  verified by breaking the workflow and watching the test fail. `bash -n` covers every run
  block; actionlint runs in CI.
- **A live-source canary**, on collector pull requests and on demand, never scheduled —
  SOURCES.md commits TCI to one request per source per day.
- **Publication drift is detectable.** A stored print with no published file now fails
  `reproduce --published`, and a failed site build fails the run instead of going green.
- **No test can write to `data/eucri.db`.**

**Acceptance:** met. 334 tests, ruff and mypy clean, `reproduce --published` exit 0.

**What is still not covered.** The canary cannot run on a fork's pull request without
secrets, and nothing exercises the commit-and-push half of `daily.yml` against a real
remote — those steps are asserted structurally, not run. A staging repository would close
it and is not worth the maintenance yet.

---

## 6. Phase L3 — earn the right to be quoted (mid-September to end of October)

The index exists. Nobody has a reason to trust it yet. L3 is about the record.

### L3.1 Twenty unbroken sessions
Daily prints with no gap in the headline, all reproducing, across the v0.4.0 and v0.5.0
transitions. **The run currently stands at 1.**
**Acceptance:** `reproduce --published` exit 0 over the whole history, and no session in
the window with fewer than 5 included providers. Watch it with
`python -m tci.run reliability --coverage EU-CRI-H100`.

### L3.2 A gap-and-incident log on the site — DONE
`reliability.html`, generated from `daily_index` on every run. Every headline gap with its
reason in words; the other series by count and state. No hand-written entry is possible.

### L3.3 Research note: the EU–US basis (publish mid-October)
Not before both legs have about two weeks of prints, which cannot begin before 1 October.
Follow `.claude/skills/research-note/SKILL.md` exactly: institutional register, no
first-person process, every figure reproducible from a query the note names.
**Content:** what the basis has been; how often each leg gapped; the sellers present in
both legs and what they charged on each side; what the basis is *not* — it is not a basis
to the Silicon Data index the CME contracts settle on, whose methodology is not public.
**Acceptance:** every number reproduces from `data/eucri.db`; a reader can rerun the
queries; the construction difference is stated in the first third, not in a footnote.

### L3.4 Distribution
A Substack post per research note, a LinkedIn post per methodology notice. Audience:
European neocloud finance leads, GPU lenders, and the desks trading the CME contracts from
5 October.
**Acceptance:** the first note reaches 3 named people who reply. Vanity metrics do not
count.

### L3.5 A machine-readable interface — DONE
`site/data/v1/`: a catalogue plus one file per series carrying its whole history and the
same digest the print file for that date publishes. A gap is a row with a null value and a
reason, never an absent row. The site is static, so there is no `?date=`; a range is a
filter over one file, which is what the Data page documents.
**Acceptance:** met. Verified by hand as an outsider — range pulled from one file, gaps
preserved, digest recomputed with four lines of standard library. A test recomputes every
published digest by the documented `jq` recipe rather than by calling this project's code,
and a second test keeps the published content ASCII, without which that recipe would
quietly stop matching.

**L3 evaluation gate:** would a risk manager cite this index in a credit memo? If the
answer is still no, the reason is either coverage or history, and both are cured by
running longer, not by building more.

---

## 7. Phase L4 — quality-adjusted price (October to December)

The layer that answers the question Mark actually cares about: **what does the price
difference between a full-service hyperscaler and a neocloud really mean, once you account
for what you get?** A hyperscaler H100 at $14/GPU-hr and a marketplace H100 at $2.10/GPU-hr
are not the same product, and today TCI prices both without saying so beyond the segment
label.

Three layers, cheapest first. Do them in order; each stands alone.

### L4.1 Declared quality attributes — DONE
`src/tci/attributes.py`, and a table beside the constituents on the dashboard.

**The finding is the coverage, and it is thin.** Of 42 cells across the constituents of
the 12 September print, 11 carry something the seller publishes as a field. Across all 304
H100 SXM rows that day: GPU memory declared by 78%, interconnect by 3%, vCPUs by 2%, system
memory by 2%, local storage by 1%. Sellers publish a price and a chip name and little else.
That is the answer to the declared half of the question and the reason the measured half
(L4.2, L4.3, L4.4) cannot be skipped.

**A trap found on the way in.** Every value in the stored `interconnect` column is read off
a SKU string — Azure from `isr`/`noIB`, gpuhunt and Scaleway from `SXM`, static entries
hardcoded. Surfacing it as a declared attribute would have had the site asserting the
fabric behind named companies' products on the strength of three characters in a product
code. It is published marked as derived, with the rule attached. Only Latitude.sh
("800Gbps Dual Plane RoCE") and Voltage Park ("ethernet") state a fabric as a field.

**Deviation from the plan, deliberately.** The plan says to promote these to typed columns
on `observations`. That table is append-only, so new columns could only be populated
forward, and every historical row would still need reading out of `raw_json` — two code
paths for one fact and a permanently thinner older record. Reading `raw_json` at the point
of use covers the whole history and leaves the hash-locked calculation path untouched.

**Not covered, and not by oversight:** network egress allowance and storage product.
Neither is published as a field by any source currently collected.

### L4.2 Public performance results — DONE (Training; Inference not attempted)
`src/tci/mlperf.py`, `data/mlperf/training.json` (upstream commits pinned),
`python -m tci.run mlperf`, and `performance.html`.

**The overlap is the finding, and it is thinner than "thin".** Six panel providers have
ever submitted to MLPerf Training — Azure, CoreWeave, Google, Lambda, Nebius, Oracle — and
**none has ever submitted an H100 system**, which is the GPU the headline prices. Ten
sellers priced on the site have never submitted anything.

Requiring the same round, accelerator, benchmark and GPU count leaves two comparable cells
across v5.0, v5.1 and v6.0. One has an EU/EEA price for both sellers:

| v5.0, B200-SXM-180GB, llama2_70b_lora, 8 GPUs | time | price | run |
|---|---|---|---|
| Lambda | 10.9 min | $6.79/GPU-hr | $9.89 |
| Oracle | 11.0 min | $14.00/GPU-hr | $20.49 |

Times differ by 0.5%, cost by 2.07×. On that workload the premium is price, not delivered
performance. One cell, one scale, one round, two vendor-tuned configurations — a data point,
not a conclusion. It is the first time TCI has been able to state one at all.

**Why this matters for L4.4.** The public-results route cannot answer the question for all
but one pair of sellers. That is the argument for the measured route, and it is worth
putting in front of Sixtytwo as evidence rather than as an opinion.

**Two joins were wrong in the first cut**, both producing plausible numbers rather than
errors: a node-count fallback attached an H200 time-to-train to a B200 price, and joining
on `system_name` (not unique within a submitter) merged an 8-GPU run with a 512-GPU one.
Both now key on the system file's stem, and an ambiguous directory is skipped.

**Not attempted:** MLPerf Inference (`$ per million tokens`). The submitter set there is
organised by division rather than by company and needs its own reading; Training answered
the question first and answered it negatively.

### L4.3 Own measurements (cheap: €20–€60 total, ~30 h)
Most of TCI's panel never submits to MLPerf. A fixed, open workload run by TCI on rented
capacity closes the gap for single-node figures.

- Use a published recipe rather than a home-made one, so the workload is not TCI's
  opinion: NVIDIA's DGX Cloud Benchmarking Recipes on NGC, or MLPerf Inference's reference
  implementations. Check each licence before publishing results.
- Start single-GPU and single-node. At €2–€4 per GPU-hour a 20-minute run costs about €1,
  and per-minute billing makes a 10-provider sweep a €10–€30 exercise.
- Publish: effective price per unit of delivered throughput, raw throughput, the exact
  image and recipe version, instance type, region, timestamp, and the run's own logs. One
  run is an anecdote — repeat monthly and publish the distribution.
- **Do not** attempt multi-node cluster-scale or reliability measurement here. That is what
  a partner is for.

**Acceptance:** a reproducible harness in `bench/` with pinned container digests; a results
table in `site/data/bench/`; a research note whose headline figure is a *range* across
repeats, never a single run.
**Evaluation gate:** if the measured spread between providers is inside the noise of
repeats, publish that negative result and stop.

### L4.4 What needs a partner — live conversation
Cluster-scale throughput, provisioning success rates, node-failure rates over a multi-day
job, checkpoint I/O under load, failover behaviour. These need sustained paid access across
many providers, which is a business, not a side project.

Mark has proposed a joint research note to Sixtytwo (sixtytwo.ai), who do this
commercially: their measurements, TCI's prices and audit trail, both names on it, no money
either way. First round kept small — one GPU class, one price tier, five EU neoclouds and
at least one hyperscaler as reference — so the methodology can be checked before it is
scaled. Aggregates by provider are acceptable where the underlying numbers are
client-confidential. The approach also states plainly that TCI is Mark's own project and
is not run on ABN AMRO Clearing's behalf.

**Decision rule unchanged:** pursue it, but treat L4.1–L4.3 as the plan of record. Nothing
in this roadmap may depend on a partner who has not signed anything.

**L4 evaluation gate:** can TCI state, with a defensible number and stated uncertainty,
what fraction of the hyperscaler premium is price and what fraction is delivered
performance? If only L4.1 lands, say so and publish the qualitative decomposition.

---

## 8. Phase L5 — the term curve (October onwards)

L2 built the intake. The curve needs data.

### L5.1 Contributor funnel
Target 8 sellers, 4 buyers, 2 brokers. Sequence: intro email (drafts in
`strategy/tci-build-plan.md`) → 20-minute call → template CSV → first file → pseudonym →
monthly cadence.
**Acceptance:** three contributors in the same (GPU, tenor, region) cell. That single
milestone is what turns L2 from infrastructure into a product.

### L5.2 Publish the first contributed cell
`python -m tci.run contrib aggregate --from … --to … --out site/data/term/contributed.json`,
then regenerate the site. The page already renders it.
**Acceptance:** the published cell carries its contributor and quote counts, the kinds
behind it, and the statement that it cannot be recomputed from public data.

### L5.3 Widen the public side while the funnel runs — DONE, 12 September
- **Latitude's prepaid-annual price.** `src/tci/collectors/latitude_annual.py` reads the
  `year` field the vendored recipe discards, off the same already-fetched page (one
  request, not two), stored as `reserved_1yr`. Checked live: the discount compounds
  sensibly with tenor (~50% at 1 month, ~65% at 12), so the number is trustworthy, not
  just structurally present. A real bug was caught before it shipped — the first cut
  fetched through a name invisible to the test suite's offline patch, so `pytest` was
  quietly hitting latitude.sh's live page on every run.
- **Every other panel provider checked for a discount schedule; none qualified.** OVHcloud
  and Scaleway explicitly exclude GPUs from their savings plans; RunPod, Nebius and
  Hetzner publish no percentage schedule; DigitalOcean publishes concrete 12-month
  reserved prices (a genuine gap, needing its own Latitude-annual-shaped adapter — not
  attempted). vast.ai publishes a schedule but says outright it is a default individual
  hosts vary, so applying it uniformly would misstate a marketplace as one seller.
  Recorded in `config/term_schedules.yaml` so the next review doesn't re-cover this.
- **`python -m tci.run term`**, new: the cells for one date against the 3-seller
  threshold, and which cells are one seller short. This is the quarterly recheck tool —
  today's reading, 0 of 64 cells published, 10 one seller away.

**L5 evaluation gate:** would a lender size a facility using the published cell? If the
answer needs a caveat longer than the number, the cell is not ready.

---

## 9. Phase L6 — from research publication to business (Q1 2027)

Only start this once L3 has a clean 60-session record.

1. **Licensing.** Today: CC BY 4.0 for non-commercial use, commercial terms "being
   finalised". Decide the actual commercial tier: redistribution inside a product, use in
   a term sheet, use as a settlement reference (which the current posture forbids).
2. **The BMR question.** The site states TCI is not administered as a benchmark under EU
   Regulation 2016/1011 and must not be used as a reference price in financial
   instruments. That posture is right for now and is also the ceiling on the business.
3. **First paid pilot.** Most likely buyer: a lender or fund needing a defensible EU price
   and the EU–US basis. Price it as a data subscription plus a bespoke report, not as
   consulting hours.
4. **Entity and cost base.** A Dutch BV once revenue is real. Until then costs are a
   domain, a GitHub account and the occasional benchmark rental.

---

## 10. Standing risks

| Risk | Why it matters | Mitigation in place | Still open |
|---|---|---|---|
| A source blocks the collector | The panel narrows and the gate bites | Fail-soft collectors; 15 sources; the canary catches a reshape before the daily run does | Nothing replaces vast.ai as the marketplace leg |
| The US leg gaps often | The basis is the differentiator | Disclosed; gap never interpolated; the coverage replay measures the right population | One session of evidence, exactly at the gate |
| Static entries go stale | Three of the panel are hand-verified | 45/90-day clocks; the excluded reason is published | Seeweb is past the warning now |
| A competitor publishes an EU series | Silicon Data has the distribution | Reproducibility and EU-native pricing are hard to copy credibly | Speed |
| Contributor data never arrives | L5 stays infrastructure | Thresholds and privacy built | Outreach is the only cure |
| Competition law on price sharing | Sellers are competitors | Aggregation thresholds, no single-party cells | Needs counsel (§5.4) |
| The daily Action breaks on a push | A lost session cannot be re-collected | L0.5: smoke test, workflow tests, canary | The push half of `daily.yml` is asserted, not executed |
| Key-person | One person, 10–15 h/week | Everything is in the repo and reproducible | No second pair of hands |

---

## 11. Weekly rhythm that fits 10–15 hours

- **Mon (1 h):** the weekend's prints, gaps and source failures —
  `python -m tci.run reliability` and `python -m tci.run canary`.
- **Tue–Wed (4–6 h):** the current phase's build task.
- **Thu (2 h):** writing — a research note, a notice, or outreach replies.
- **Fri (1 h):** the source register's review clock (`python -m tci.run sources --due`).
- **Sat (2–4 h, optional):** the deeper piece of work.
- **Never:** merge between 11:00 and 11:20 UTC.

---

## 12. Appendix A — command cheat sheet

```bash
pip install -e .[dev]
python -m tci.run migrate
python -m tci.run daily [--date YYYY-MM-DD]     # collect + compute + regenerate everything
python -m tci.run backfill --from D --to D      # recompute from stored observations only
python -m tci.run constituents --date D [--series S]
python -m tci.run reproduce [--date D] [--published]
python -m tci.run sources [--due|--status S|--block B]
python -m tci.run canary [--source ID]          # live collection into a throwaway db
python -m tci.run reliability [--series S]      # the gap log
python -m tci.run reliability --coverage S --days N   # gate replay for one series
python -m tci.run docs                          # regenerate METHODOLOGY.md + .lock
python -m tci.run contrib validate|ingest|aggregate
pytest && ruff check src tests && mypy src/tci
```

Adding a methodology version (the only correct order):

1. Freeze the current head into `config/methodology/<head-version>/`.
2. Edit `config/factors.yaml`; bump `methodology_version`.
3. Add the version to `config/methodology/succession.yaml` with its effective date.
4. Add the notice to `config/notices.yaml`, with the expected effect quantified.
5. Add a CHANGELOG entry.
6. `git checkout origin/main -- METHODOLOGY.lock && python -m tci.run docs`.
7. `pytest && python -m tci.run reproduce --published`.

Adding a collector:

1. Write it fail-soft, one request per source per day, honest User-Agent.
2. Capture a live fixture into `tests/fixtures/`, add parser tests and a persistence test.
3. Register it in `config/source_registry.yaml` with status `shadow`.
4. It stores rows and moves nothing until a version admits it to `panel:`.
5. Run `python -m tci.run canary --source <id>` before opening the pull request.

---

## 13. Appendix B — the things already decided, so they are not relitigated

- **Europe first, US as the reference leg.** Not a European island: the basis to the US
  contracts is the product.
- **Asia is out of scope** until Europe prints reliably.
- **No settlement claims.** TCI is a price-transparency benchmark, not a settlement
  benchmark, and says so on every page.
- **Open source and open data.** The moat is the record and the reproducibility, not
  secrecy.
- **Contributed data is private by construction**, and the thresholds are not negotiable
  per contributor.
- **A median minus a median has no additive decomposition.** The basis page lists the
  sellers present in both legs instead of a fabricated split.
