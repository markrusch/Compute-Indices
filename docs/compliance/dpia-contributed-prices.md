# Data protection impact assessment: contributed term prices

> **Status: DRAFT — not adopted, not in force, not legal advice.**
> Prepared for review by counsel before adoption. Nothing here is a statement of
> compliance with any law, regulation or standard. It is a self-assessment against
> Art. 35 GDPR, not independently verified.

Controller: Mark Rusch. Scope: the intake, private storage and aggregation of term
prices under [CONTRIBUTING-PRICES.md](../../CONTRIBUTING-PRICES.md), implemented in
`src/tci/contrib.py`. Status of the processing: no real contribution has been accepted yet
(ROADMAP §5, item 4). This assessment is written before the first one.

## Screening: is a DPIA mandatory?

Art. 35(1) requires one where processing is likely to result in a high risk to natural
persons. Art. 35(3) lists three cases: systematic and extensive profiling with significant
effects, large-scale special-category or criminal-offence data, and large-scale systematic
monitoring of public areas. The Autoriteit Persoonsgegevens also publishes a list of
processing types that require one `[TO VERIFY: current list]`.

None of the Art. 35(3) cases fits. The substance of a contribution is a price, a quantity,
a tenor and a region belonging to a company. The personal data is the name, work email and
employer of the contact person, and a pseudonym mapped to them. Volume is expected to be
small (a handful to a few dozen contact persons). No special-category data is sought.
Nothing about a person is evaluated or scored.

Conclusion: a DPIA does not appear mandatory. `[COUNSEL: confirm. Two features could push
towards a different answer: contributors who are sole traders, where the price data is
about a natural person's commercial activity; and free-text fields that could hold
anything.]` It is written up anyway, because the store is append-only by design, which
sits awkwardly with erasure, and that is worth thinking through before there is data.

## Description of the processing

Data flow, from the code:

1. A contributor emails a CSV to the administrator (subject "Term prices").
2. `contrib validate` checks it; one invalid row rejects the file. `contrib ingest` writes
   rows to a SQLite database in `TCI_PRIVATE_DIR` under a pseudonym and stores the SHA-256
   of the file.
3. Rows cannot be updated or deleted (triggers `contrib_no_update`, `contrib_no_delete`).
   A correction is a new row that supersedes an earlier one, and only its author may send it.
4. `contrib aggregate` computes medians per cell (GPU, tenor, region, currency). A cell
   is priced only with at least three contributors, with none above half the volume,
   and not all indicative. Quartiles need five. The output holds counts and suppressed
   cells and no contributor or single quote.
5. The name-to-pseudonym key is held offline, separately (PRIVACY.md).

Personal data: contact name, work email, employer, pseudonym, and any personal data in the
`notes` and `payment` free-text columns. Recipients: none outside the administrator, apart
from a reviewer under a confidentiality agreement. Retention: not yet defined.

## Necessity and proportionality

- Purpose: a term-structure table that public sources cannot supply. The identifying data
  is limited to what is needed to attribute rows to one party, apply the dominance test
  and process corrections.
- Minimisation: the schema has no personal field except the pseudonym. The template asks
  for no individual's details. Free text is the exception (risk 3).
- Basis: Art. 6(1)(f) or (b) `[COUNSEL]`.
- Information: PRIVACY.md has a paragraph on contributors. It does not name the basis,
  retention or transfers for this processing. `[COUNSEL: add, or cover in the contributor
  agreement.]`
- Rights: see [data-subject-requests.md](data-subject-requests.md).

## Risks to individuals

| # | Risk | Likelihood | Severity | Notes |
|---|---|---|---|---|
| 1 | Identity of a contact person, or that their firm contributed, becomes known to competitors or the public | Low | Medium: commercial and possibly employment consequences for the individual | Key kept apart from prices; nothing identifying is published; small cells suppressed |
| 2 | Re-identification through a thin cell: a contributor's price inferred from a published median | Low to medium in early cells | Medium | Three-contributor minimum, one-half volume cap, quartiles from five. Thresholds are TCI's own and unreviewed by competition counsel |
| 3 | Personal data in free-text `notes` or `payment` | Low | Low to medium | Cannot be deleted from the store once ingested |
| 4 | Loss or theft of the private store or the key (laptop loss, unencrypted backup, account takeover) | Low to medium: one person, one machine `[TO CONFIRM]` | Medium | See breach procedure |
| 5 | Erasure or rectification request cannot be met because the store is immutable | Medium once data exists | Low to medium | Section below |
| 6 | Data kept indefinitely because no period is set | Certain until a period is chosen | Low | |
| 7 | Contributor emails accessible to a mailbox provider without a data processing agreement | `[TO CONFIRM]` | Low | See processors list |
| 8 | Key-person: the only person with access is unavailable, so requests and incidents are not handled | Medium | Low | Statutory deadlines still run |

## Measures

In place, from the code and documents:

- Private store outside the repository, refused by `private_db_path` if inside it.
- Append-only triggers; a digest of each submitted file.
- Aggregation thresholds applied before any output is written.
- Only the aggregate output has a path into `site/data/term/contributed.json`.
- Separate storage of the key.

Proposed, for the administrator and counsel to accept or reject:

- Define retention: identity data for the life of the relationship plus a stated period;
  price rows for as long as needed for the research series `[COUNSEL]`.
- Full-disk encryption on the machine holding the store and the key; an encrypted backup
  with a stated location `[TO CONFIRM]`.
- Tell contributors in the agreement not to put personal data in `notes` and `payment`.
- Contributor agreement covering basis, purpose, retention, reviewer access and liability
  allocation by contract. `[COUNSEL]`
- Reviewers who see the private store do so under a confidentiality agreement as
  advisers, not as controllers or processors, unless counsel advises otherwise. `[COUNSEL]`
- Log the store's location and who has had access.

## Erasure against an append-only store

The triggers block UPDATE and DELETE on purpose: a published aggregate has to be
checkable against the rows behind it. Art. 17 gives a right to erasure where a ground
applies, and Art. 17(3) has exceptions, including archiving and research in the public
interest and legal claims, where they apply `[COUNSEL: whether any fits]`. Two routes are
available without touching the code:

1. The identity key sits outside the database. Deleting a person's entry from the key
   leaves rows under a pseudonym that no one can map to a name; whether that counts as
   erasure or as anonymisation depends on whether re-identification remains possible from
   the rows (for a small firm, the content may identify it). `[COUNSEL]`
2. Rebuilding the store without the person's rows, done by the administrator as a
   deliberate operation outside the tool, after which the affected aggregates are
   recomputed and republished as revised. This changes a published research series and
   should be treated as an exceptional event.

Changing the triggers is a code change to the private-store schema and not a methodology
change, but it weakens an audit property and needs a decision recorded in CHANGELOG.md.

## Conclusion

Screening: not mandatory on the facts as they stand, subject to counsel. Residual risk
after the proposed measures: low, provided the first contributions are not accepted until
retention, encryption and the contributor agreement exist. Review this document before
accepting the first contribution and again after twelve months of data.

Consultation with the Autoriteit Persoonsgegevens (Art. 36) is required only where a
DPIA shows a high residual risk. None is identified here.

| Item | Entry |
|---|---|
| Assessment date | `[TO CONFIRM]` |
| Approved by | `[TO CONFIRM]` |
| Next review | Before the first contribution is accepted |
