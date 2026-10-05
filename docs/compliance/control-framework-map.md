# Control framework map

> **Status: DRAFT — not adopted, not in force, not legal advice.**
> Prepared for review by counsel before adoption. Nothing here is a statement of
> compliance with any law, regulation or standard.

This is a mapping, not an assessment. It records which framework area each policy in this
folder is relevant to, so that a later audit can reuse the work. It says nothing about
whether any control is implemented or effective, and TCI does not claim conformity with
CIS Controls, ISO/IEC 27001 or SOC 2. Mapping is at family or theme level only; control
numbers and wording should be taken from the current published frameworks.
[TO VERIFY: framework versions and family names against the published texts before any
external use.]

## Frameworks referenced

- CIS Critical Security Controls v8, Implementation Group 1 (IG1), by control family.
- ISO/IEC 27001:2022 Annex A, by theme (organisational, people, physical, technological).
- AICPA SOC 2 Trust Services Criteria, Security (common criteria), by criteria group.

## Crosswalk

| Document | CIS v8 IG1 family | ISO/IEC 27001:2022 Annex A theme | SOC 2 Security (CC) group |
|---|---|---|---|
| `information-security-policy.md` | Inventory of assets; data protection; secure configuration; account and access control; continuous vulnerability management; data recovery; incident response | Organisational (policies, asset management, access control, supplier relationships, incident management, continuity); Technological (configuration, backup, vulnerability management) | Control environment; risk assessment; logical access; system operations; change management; risk mitigation |
| `record-keeping-policy.md` | Data protection; data recovery | Organisational (records, information classification); Technological (backup, logging) | Logical and physical access; system operations |
| `personal-data-breach-procedure.md` | Incident response | Organisational (incident management, legal and regulatory requirements) | System operations (incident detection and response); communication and information |
| `dpia-contributed-prices.md` | Data protection; account and access control | Organisational (privacy and protection of personal data, information classification); Technological (data masking, access) | Risk assessment; logical access; confidentiality-related criteria if selected |
| `gdpr-*.md` | Data protection | Organisational (privacy and protection of personal data, legal requirements, supplier relationships) | Communication and information; risk mitigation (vendors) |
| `data-subject-requests.md` | Data protection | Organisational (privacy and protection of personal data) | Communication and information |
| `contributor-code-of-conduct.md` | Data protection; security awareness | People (terms of employment, confidentiality, awareness); Organisational (acceptable use) | Control environment (integrity and ethical values) |
| `conflicts-of-interest-policy.md` | Not directly covered | Organisational (segregation of duties, policies) | Control environment |
| `oversight-panel-terms.md` | Not directly covered; access control where panel members receive data | Organisational (roles and responsibilities, confidentiality, supplier and third-party relationships) | Control environment; monitoring activities |
| `iosco-self-assessment.md` | Not directly covered | Organisational (compliance, independent review of information security); Technological (change management) | Monitoring activities; change management |

Repository controls that sit behind these documents and would be the evidence in an audit:
the append-only triggers in `src/tci/migrations/`, `METHODOLOGY.lock` and its guard test,
`tests/test_reproduce.py`, the CI workflows in `.github/workflows/`, and the private-store
path check in `src/tci/contrib.py`.

## Reading the table

- "Not directly covered" means the document is governance or integrity material with no
  matching security family, not that a gap exists.
- The IOSCO self-assessment is a different kind of framework (benchmark principles) and
  appears here only for its overlap on change control and review.
- If a company is formed, or a customer or regulator asks for a specific framework, a
  control-level assessment by a qualified independent party would be a separate piece of
  work. [COUNSEL: whether and when to commission one]
