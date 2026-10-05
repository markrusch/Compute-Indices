# IOSCO Principles for Financial Benchmarks: self-assessment

> **Status: DRAFT — not adopted, not in force, not legal advice.**
> Prepared for review by counsel before adoption. Nothing here is a statement of
> compliance with any law, regulation or standard. This is a self-assessment by the
> administrator, not independently verified.

TCI is designed with reference to the IOSCO Principles for Financial Benchmarks (July 2013,
19 principles) as voluntary practice. It is a research publication, not licensed for use in
financial instruments, provided as-is, and not administered as a benchmark under Regulation
(EU) 2016/1011 (`GOVERNANCE.md`, `NOTICE`). Nothing below changes that posture.

Status terms: **Addressed** (the repo shows the practice in operation), **Partly addressed**
(some of it, with a stated gap), **Not addressed**, **Not applicable**. "Addressed" means
the artefact exists and does what is described. It does not mean the practice has been
tested by anyone other than the administrator.

Principle numbers and titles follow the IOSCO final report (July 2013). [TO VERIFY: titles
below were checked against a secondary summary only; confirm against IOSCO PD415 before
adoption.]

## Reconciliation with existing text

`METHODOLOGY.md` §8 maps six groups of principles and `GOVERNANCE.md` labels its sections
P4-P5, P6-P7, P12, P13 and P16. Two labels differ from the numbering used here: the
correction policy (`GOVERNANCE.md` §2) is labelled P13 and the audit trail (§3) P16, whereas
the IOSCO final report numbers the audit trail P18, complaints procedures P16 and transition
policy P13. This document uses the IOSCO numbering. The seven settlement-grade preconditions
in `GOVERNANCE.md` are a separate, stricter list; the table at the end shows how they relate.

## Governance

| # | Principle | What it asks | How TCI addresses it | Status |
|---|---|---|---|---|
| 1 | Overall responsibility of the administrator | The administrator is responsible for all stages of determination and controls them | One named administrator (`GOVERNANCE.md` header). Every stage is in the repo: collectors, `normalise.py`, `index.py`, `outputs/`. | **Partly addressed.** Responsibility is clear, but rests on one person with no delegation or backup. |
| 2 | Oversight of third parties | Where third parties supply data or services, the administrator oversees them | Source register with endpoint, tier, robots/ToS basis and review date (`SOURCES.md`, `config/source_registry.yaml`, `python -m tci.run sources --due`); a canary workflow for collector changes (`.github/workflows/canary.yml`); one request per source per day. Upstream redistributors (frankfurter.dev, gpuhunt) are named in `NOTICE`. | **Partly addressed.** Sources are screened and monitored, but no agreement exists with any of them and none oversees TCI. [COUNSEL: relevance of scraping terms.] |
| 3 | Conflicts of interest for administrators | Identify, disclose, manage and mitigate conflicts | `GOVERNANCE.md` §4 discloses that the author may trade on observed venues, states mitigations (no judgement in the calculation path, immutable observations, public code). | **Partly addressed.** Disclosed, but the only control is disclosure; no separation of duties or trading restriction exists. |
| 4 | Control framework for administrators | Documented controls covering the whole process, including continuity | Hash-locked methodology (`METHODOLOGY.lock`, `tests/test_methodology_guard.py`); append-only database triggers (`tests/test_db_immutability.py`); publication gate; CI with ruff, mypy, pytest; `reproduce --published`; fail-soft collectors (`tests/test_reliability.py`, `tests/test_collector_persistence.py`). | **Partly addressed.** Strong technical controls. No written business-continuity, key-person or incident procedure, and no access-control policy beyond repository permissions. |
| 5 | Internal oversight | An independent oversight function reviews the administrator's work | None. `GOVERNANCE.md` precondition 5 states the gap: governance by one person is not something code can fix. | **Not addressed.** A sole operator cannot provide oversight of themselves. |

## Quality of the benchmark

| # | Principle | What it asks | How TCI addresses it | Status |
|---|---|---|---|---|
| 6 | Benchmark design | Design reflects the market measured, with defined scope and no distortion | Unit definition and panel (`METHODOLOGY.md` §1); weighted median with count-based trim (§3); companion series by segment and generation (§4). | **Partly addressed.** The design is specified and tested (`tests/test_index_golden.py`, `tests/test_trim.py`, `tests/test_weighted_median.py`). It measures public list and marketplace quotes, not transactions, and the per-provider weight cap is inert on a narrow panel (`config/factors.yaml`, `weights.max_weight_share_pct`). |
| 7 | Data sufficiency | Inputs are enough to represent the market; a minimum is enforced | `aggregation.min_providers: 5` and `min_offers: 5`; below that the print is null with `insufficient_sources`; a gap stays a gap (`GOVERNANCE.md` §5, `CLAUDE.md` invariants). | **Partly addressed.** The gate is enforced. `GOVERNANCE.md` preconditions 1, 2 and 4 (15 constituents, 180 days at 95% coverage, 50% executable share) are unmet. |
| 8 | Hierarchy of data inputs | Stated order of preference among input types, applied consistently | `METHODOLOGY.md` §2: executable quotes weight 2.0x over list prices; overlay data never enters the calculation. | **Partly addressed.** A hierarchy exists but its top tier is executable list quotes; TCI has no transaction feed and says so. |
| 9 | Transparency of benchmark determination | Publish enough for stakeholders to understand how a value was determined and its limits | Full methodology generated from config (`python -m tci.run docs`); per-print constituent set and digest (`site/data/prints/`); `constituents` and `weights` commands. | **Addressed.** Every published number can be traced to its inputs and recomputed. |
| 10 | Periodic review | Review the benchmark's continued validity | Annual review, first due July 2027, logged in `CHANGELOG.md` (`GOVERNANCE.md` §7); `python -m tci.run validate` for dropout sensitivity and check-series correlation. | **Partly addressed.** Scheduled but not yet held, and done by the administrator alone. |

## Quality of the methodology

| # | Principle | What it asks | How TCI addresses it | Status |
|---|---|---|---|---|
| 11 | Content of the methodology | Methodology states definitions, inputs, calculation, limits, and how discretion is used | `METHODOLOGY.md` §§1-7 and the verbatim parameter listing. The calculation path has no discretion (`GOVERNANCE.md` §4). | **Addressed.** The document is generated from the parameters in force, so it cannot drift from them. |
| 12 | Changes to the methodology | Published procedure, advance notice, consultation where appropriate | `GOVERNANCE.md` §1: version bump, changelog, notice in `config/notices.yaml` one day before effect, frozen snapshots, succession dates enforced in code (`tests/test_succession.py`, `tests/test_version_effect.py`). | **Partly addressed.** Notice and versioning are mechanical. The notice period is one day and there is no consultation step. |
| 13 | Transition policy | Procedure for cessation or change that considers users' positions | `GOVERNANCE.md` §8: at least 30 days' notice of cessation; repository, data and history stay public. | **Partly addressed.** Cessation is covered. Nothing covers a successor administrator or a transfer on incorporation. [COUNSEL] |

## Accountability

| # | Principle | What it asks | How TCI addresses it | Status |
|---|---|---|---|---|
| 14 | Submitter code of conduct | Where submitters contribute, a code of conduct governs them | No submitters feed the calculation path. Contributed term prices are a research output held outside the repository (`CONTRIBUTING-PRICES.md`, `tci.contrib`). | **Not applicable** to the index today; would apply if contributions ever entered a print. A code in that shape is drafted at `contributor-code-of-conduct.md` so that it is in place first. |
| 15 | Internal controls over data collection | Controls on how input data is collected, checked and stored | Fail-soft collectors; normalisation with price band, node floor and staleness rules (`normalise.py`); append-only observations; intake funnel (`python -m tci.run intake`); canary. | **Partly addressed.** Screening and staleness controls exist. Nothing detects a plausible-looking but wrong price from a source beyond the band. |
| 16 | Complaints procedures | Published process to receive and resolve complaints, with records | `GOVERNANCE.md` §6: contact page, acknowledgement aims within 7 days, outcome published with the next print. | **Partly addressed.** A route exists. There is no escalation path outside the administrator and no complaint log. |
| 17 | Audits | Periodic independent review of compliance with the methodology and framework | None. `GOVERNANCE.md` precondition 6 (external methodology assurance) is unmet. `tests/test_reproduce.py` is an automated self-check, not an audit. | **Not addressed.** No independent party has reviewed anything. |
| 18 | Audit trail | Retain records of inputs, determinations and changes | Append-only `observations`, `daily_index`, `weight_sets`; per-print constituent set and digest; `reproduce --published`; FX rate stored with each print (`GOVERNANCE.md` §3). | **Addressed** for retention and reproducibility. Retention is in the public repository and its history; there is no separate archive. |
| 19 | Cooperation with regulatory authorities | Retain and provide records to regulators on request | No statement in the repo. Records are public. | **Not addressed.** No procedure or contact for regulatory requests. [COUNSEL: whether a statement is wanted at all under the current posture.] |

## Settlement-grade preconditions, in IOSCO terms

`GOVERNANCE.md` lists seven conditions before TCI may be represented as a settlement
benchmark. Its status line reads "as of methodology v0.3.0"; the line is dated and has not
been re-assessed here. [TO CONFIRM: current state of preconditions 1-4 against the live dashboard.]

| Precondition | Related principles | Assessment |
|---|---|---|
| 1. At least 15 independent constituents, 5 executable | 7, 8 | Not shown to be met |
| 2. 180 consecutive days at 95% coverage | 7 | Not shown to be met |
| 3. Volatility driven by price, not composition | 6 | Untested in the repo as a standing check |
| 4. Executable share at least 50% by weight | 8 | Not shown to be met |
| 5. Independent oversight committee | 5 | Unmet by design |
| 6. External methodology assurance | 17 | Unmet |
| 7. Legal opinion on Article 2(1c) | 19 and scope | Unmet |

## Summary

Of 19 principles:
addressed 3 (9, 11, 18); partly addressed 12 (1, 2, 3, 4, 6, 7, 8, 10, 12, 13, 15, 16);
not addressed 3 (5, 17, 19); not applicable 1 (14).

The strongest area is reproducibility and audit trail. The weakest are independent oversight
(P5) and independent audit (P17), which no change to the code can supply.
