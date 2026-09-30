# Record-keeping policy

> **Status: DRAFT — not adopted, not in force, not legal advice.**
> Prepared for review by counsel before adoption. Nothing here is a statement of
> compliance with any law, regulation or standard. It describes what the repository does
> today and states retention periods the administrator proposes to adopt.

This policy is designed with reference to the audit-trail requirement for commodity
benchmarks in Annex II of Regulation (EU) 2016/1011 and to IOSCO Principle 18 (Record
Retention), adopted voluntarily. TCI does not claim to be in scope of that Regulation.
Searching the published text of Annex II shows an audit trail of relevant information
retained for at least five years to document the construction of calculations
(a consolidated copy of Annex II, division 6, point 11, on legislation.gov.uk; that is the
UK-assimilated version and was not checked against the EUR-Lex text).
[TO VERIFY: the point number and wording in the EU text, and whether IOSCO Principle 18
sets its own period; five years is the figure this policy adopts.]

## 1. Principle

Records are kept so that anyone, including the administrator's successor or an independent
reviewer, can trace a published print back to the inputs and the methodology it used. Most of
this is already true by construction. The policy states it, adds what is not yet covered,
and says where retention has to give way to privacy law.

## 2. What is kept, where, and for how long

Period: the proposed minimum is **five years from the end of the calendar year** in which the
record was created. Most public records are kept indefinitely because the repository history
is public and is not deleted.

| Record | Where | How it is protected today | Retention |
|---|---|---|---|
| Raw observations, every candidate price | `observations` in `data/eucri.db` | Triggers `obs_no_update` and `obs_no_delete` block UPDATE and DELETE | Indefinitely |
| Prints and revisions | `daily_index` | Triggers `dix_no_update`, `dix_no_delete`. A correction is a new revision, reads take `MAX(revision)` | Indefinitely |
| Constituent sets: every candidate, price, weight, inclusion, reason | `constituents`, `site/data/prints/YYYY-MM-DD.json` | Published with a sha256 digest | Indefinitely |
| Weight sets | `weight_sets` | Append-only triggers | Indefinitely |
| Intake funnel records | `intake` (migration 0010) | `intake_no_update`, `intake_no_delete` | Indefinitely |
| Term quotes from public sources | `term_quotes` | `tq_no_update`, `tq_no_delete` | Indefinitely |
| Methodology, parameters, code | `config/factors.yaml`, `config/sovereign.yaml`, `config/methodology/`, `src/tci/index.py`, `normalise.py`, `weights.py` | Hash-locked in `METHODOLOGY.lock`, one hash per frozen version; CI fails on mismatch | Indefinitely |
| Notices, changelog, governance documents | `config/notices.yaml`, `CHANGELOG.md`, `GOVERNANCE.md` | Git history | Indefinitely |
| Reproduction | `python -m tci.run reproduce --published` | `tests/test_reproduce.py` recomputes every stored print in CI | Not a record; a check |
| Intraday reads | `data/intraday/YYYY-MM/*.jsonl` | Append-only, hash-verified on every read | Five years, then reviewed. [TO CONFIRM: these reads are not used in any print] |
| Source register and screening | `config/source_registry.yaml`, `SOURCES.md` | Git history | Indefinitely |
| Complaints, corrections requests and outcomes | Administrator's mailbox; outcomes in CHANGELOG and minutes | Mailbox retention only. **Not yet covered.** | Five years from closure. [TO CONFIRM: a separate log] |
| Conflicts register, declarations, recusals | `docs/compliance/` | Git history | Five years after the interest ends |
| Oversight Panel minutes and papers | `docs/oversight/` [TO CONFIRM] | Git history | Indefinitely (published) |
| Contact-form messages | Administrator's mailbox | Ordinary mail retention (PRIVACY.md) | See PRIVACY.md |
| Contributed term prices and identity key | Private store, see section 4 | Section 4 | Section 4 |

## 3. Integrity and continuity

**What already works.** The database is append-only by trigger, not by convention. Revisions
never overwrite. The methodology lock and the reproduce check mean a change to the
calculation path that altered a stored print fails CI. The repository is public, so its
history is copied by anyone who clones it.

**What does not.** These are stated because the policy would otherwise imply more than is
true.

- `data/eucri.db` is committed daily by the GitHub Action to `main`. GitHub is the only
  authoritative copy the administrator controls. [TO CONFIRM: any independent off-platform
  backup. If none exists, one should be set up, and it should be tested by restoring
  from it.]
- Triggers can be dropped by anyone with file access; migration 0004 drops and recreates the
  `observations` triggers. The protection is against accident and casual edits,
  not against the administrator. The digests, the public git history and third-party clones
  are the outside check.
- A source's raw response is not stored, only the parsed observation. Whether the parsed row is
  enough to reconstruct the source is a matter for the annual review.
- Forced pushes would destroy real history. CLAUDE.md warns against them. A branch-protection
  rule on `main` that blocks force-pushes is not documented. [TO CONFIRM.]
- Retention obligations attach to the administrator personally today. If a company takes
  over the index, the records and this policy pass with it. [COUNSEL: succession of the
  duty, and of the records, on a transfer or on cessation under GOVERNANCE.md §8.]

## 4. The private contributor store

Contributions sit in `contributions.db` under `TCI_PRIVATE_DIR` (default `~/.tci-private`).
The software refuses a path inside the repository. It is append-only by trigger
(`contrib_no_update`, `contrib_no_delete`). Each row keeps the sha256 of the submitted file.
A contributor is identified by name to the administrator and stored under a pseudonym; the
name-to-pseudonym key is kept offline, apart from the prices (PRIVACY.md).

**The conflict.** An append-only store cannot honour an erasure request row by row, and
GDPR Art. 17 gives a contributor a right to ask (PRIVACY.md, "Your rights"). Two things
reconcile this, and both are proposals for counsel, not settled positions.

1. **The prices are not personal data if the contributor is a company**, and the store holds
   pseudonyms, not names. The personal data is the name-to-pseudonym key, which is outside
   the store. Erasure is done by destroying the key entry for that contributor. After that,
   the rows cannot be linked to a person by the administrator. [COUNSEL: whether a
   pseudonym plus the administrator's ability to re-identify from other sources is
   still personal data; whether key destruction is adequate erasure; whether the sole
   trader or an individual broker is treated differently from a company.]
2. **A retention exception.** Art. 17(3)(d) allows keeping data needed for research or statistical
   purposes where erasure would seriously impair them, and 17(3)(e) for legal claims
   [TO VERIFY: article and paragraph numbers against the GDPR text]. Whether that fits
   an aggregate that includes the rows is for counsel.

Retention proposed for the store:

- **Rows:** kept for five years from the `as_of` date. After that the administrator
  may destroy the store file for the period, once any aggregate that used it is no longer
  needed for an audit. Because triggers block DELETE, this is done by removing the whole
  database file and not by deleting rows. [TO CONFIRM: whether the store should be
  partitioned by year so a year can be removed cleanly.]
- **Key:** kept while the contributor is active and for as long after as a claim could arise
  from the contributor agreement. [COUNSEL: period.]
- **Published aggregates:** stay in the repository. They hold counts and suppressed cells
  and no contributor or single quote, so they are not affected by destruction of the store.
  They cannot be recomputed once the underlying rows are gone, and the published cell says
  so already.
- **Requests:** a data-subject request is answered within one month (GDPR Art. 12(3)
  [TO VERIFY]). A personal data breach is notified to the Autoriteit Persoonsgegevens within 72
  hours of the administrator becoming aware, where it is required (Art. 33 [TO VERIFY]).
- **Backups:** the store has none unless the administrator makes them. Any backup is kept
  outside the repository, is encrypted, and is deleted on the same schedule.
  [TO CONFIRM: what backups exist now.]
- **A legal demand.** The administrator will not hand rows to anyone without advice.
  [COUNSEL.]

## 5. Access

The administrator is the only person with access to the private store and the key. An
independent reviewer may see the store under a confidentiality agreement. Oversight Panel
members see the public record and do not see the store unless the same conditions are met.
[TO CONFIRM: access controls on the machine that holds `~/.tci-private`, and whether the
disk is encrypted.]

## 6. Making records available

A regulator or a reviewer with a proper request: the administrator will co-operate with a
lawful request and take advice first. The public record is available to everyone at all
times. Records not yet public, such as the complaint log, are not promised to anyone.

## 7. Review

Annually with the methodology review (GOVERNANCE.md §7), and at once on a change of legal
entity, a change of hosting, or a loss of records. A loss of records is disclosed in
CHANGELOG.md as found-not-fixed and does not delay the correction policy.
