# Compliance drafts

> **Status: DRAFT — not adopted, not in force, not legal advice.**
> Nothing in this folder is a statement of compliance with any law, regulation or
> standard. Each document is prepared for review by counsel before adoption.

These documents are not rendered on the site. Adopting one means counsel has reviewed it,
Mark Rusch has signed it off, and it has an entry in `CHANGELOG.md`. Only then may
`GOVERNANCE.md`, `PRIVACY.md` or the site link to it.

Material that would expose a weakness, a negotiating position or an unresolved legal risk
stays out of this repository, because the repository is public. That covers security gap
lists, questionnaire answers and the liability memo, which are kept privately.

## How the folder is organised

The order follows what a buyer of a benchmark checks first.

| Layer | Question it answers | Documents |
|---|---|---|
| 1. Benchmark governance | Can this number be trusted, and who checks it? | `iosco-self-assessment.md`, `oversight-panel-terms.md`, `conflicts-of-interest-policy.md`, `contributor-code-of-conduct.md`, `record-keeping-policy.md` |
| 2. Personal data | Is personal data handled lawfully? | `gdpr-records-of-processing.md`, `gdpr-processors.md`, `dpia-contributed-prices.md`, `personal-data-breach-procedure.md`, `data-subject-requests.md` |
| 3. Information security | Can a number be tampered with, or contributed data leak? | `information-security-policy.md`, `SECURITY.draft.md` (moves to the repo root as `SECURITY.md` on adoption) |
| 4. Reuse for audits | What would a later ISO 27001 or SOC 2 audit reuse? | `control-framework-map.md` |

## Frameworks deliberately not pursued yet

- **SOC 2 and ISO/IEC 27001 certification.** Both need a paid external auditor. The
  control map in layer 4 means that work is reused if a customer requires either.
- **DORA.** It binds financial entities, not TCI. They pass its Art. 28–30 terms down to
  suppliers by contract, and those terms are handled when a contract is on the table.
- **NIS2 and the Cyber Resilience Act.** TCI is below the NIS2 size thresholds and is
  not in a covered sector. The CRA covers products with digital elements, and a data
  publication is not one.

## Order of adoption

1. Before the first contributed price is accepted: the contributor code of conduct, the
   DPIA, the breach procedure, the processor list and counsel's view on competition law
   (`docs/ROADMAP.md` §5 item 4).
2. Before the first paid contract: a legal entity, insurance, the liability terms,
   the information security policy and `SECURITY.md`.
3. Before the settlement-grade preconditions in `GOVERNANCE.md` can be met: a seated
   oversight panel, the conflicts register and an external review of the IOSCO
   self-assessment.
