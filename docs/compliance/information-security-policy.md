# Information security policy

> **Status: DRAFT — not adopted, not in force, not legal advice.**
> Prepared for review by counsel before adoption. Nothing here is a statement of
> compliance with any law, regulation or standard. It is designed with reference to
> common small-operator practice, self-assessed and not independently verified.
> [COUNSEL: review before adoption]

## Purpose and scope

This policy covers the systems, accounts and data that produce and publish the TCI
indices, run by one person, the operator (Mark Rusch), without a legal entity at
present. [COUNSEL: update when a company is formed.] Its aim is narrow: a published
number is not changed unnoticed, a day's collection is not lost, contributed prices are
not disclosed, and the accounts that can change the record are not taken over.

It is proportionate to a one-person operation. Where a control is not yet in place the
text says "aims to". TCI does not claim conformity with ISO/IEC 27001, SOC 2 or any other
standard; `control-framework-map.md` is a mapping only.

## Assets

| Asset | Where | Why it matters |
|---|---|---|
| Source repository and history | GitHub, public | The audit trail of code, methodology and prints |
| Database `data/eucri.db` and published files under `site/` | Repository | Raw observations and prints; append-only |
| Intraday log `data/intraday/` | Repository | Research record; hash-verified on read |
| Private contributor store | Outside the repository, `TCI_PRIVATE_DIR` (default `~/.tci-private`) on the operator's machine | Contributed prices and contributor identity; confidential |
| Operator's GitHub account and the Actions token | GitHub | Can commit to `main`, which publishes |
| Repository secrets (source API keys) | GitHub Actions secrets | Access to data sources |
| Domain and site hosting | [TO CONFIRM: registrar and host] | Where readers find the record |
| Operator email, including the contact address | [TO CONFIRM: provider] | Receives contact-form mail and report traffic |
| Operator's workstation | Operator | Holds the private store and any local credentials |

## Access control

One person holds administrative access to every asset above. Nobody else has access to the
private contributor store. The aim is to keep it that way; any additional person with
access to the repository, hosting or private store is added deliberately, with the least
access that does the job, and this policy is updated first. [COUNSEL: contractor and
oversight-panel access terms.]

The scheduled workflows declare their own permissions: the test and canary workflows are
read-only, and only the daily and intraday jobs may write, to commit data. Secrets are held
as Actions secrets, are not stored in the repository, and are passed only to the step that
uses them.

## Account security

The operator aims to:

- protect the GitHub account, registrar, hosting and email accounts with a unique
  passphrase from a password manager and phishing-resistant two-factor authentication
  (hardware security key or passkey), with recovery codes stored offline;
- review the list of active sessions, personal access tokens, deploy keys and authorised
  applications on the GitHub account at least yearly and remove what is not used;
- protect `main` so that changes reach it through checks the repository already runs,
  as far as the daily automated commit allows; [TO CONFIRM: branch protection settings]
- keep the workstation's disk encrypted, its operating system patched and a screen lock
  on.

## Change control

Most of this already exists and is enforced by tests, not by intent:

- The database refuses UPDATE and DELETE on observations, daily indices and weight sets;
  a correction is a new revision.
- The calculation path and its parameters are hash-locked in `METHODOLOGY.lock`; a change
  needs a version, a notice and a changelog entry (`GOVERNANCE.md` §1).
- `tests/test_reproduce.py` recomputes stored prints from stored observations and checks
  every published digest, so a code change that moves a number fails CI.
- CI runs lint, type checks, the test suite, a reproduction of the published record and a
  workflow linter on every push and pull request.
- The private store refuses to be created inside the repository, and a test asserts it.

The operator aims to pin third-party GitHub Actions to a reviewed version and to read
workflow changes as carefully as code changes, since the workflows hold the write token.

## Backups and continuity

The repository, with its full history, is the primary record of published data, and each
clone is a copy. The daily job commits its data even when an earlier step fails, so a
day's collection is kept whenever the runner survives to that step.

The operator aims to:

- keep a periodic offline or second-location copy of the repository;
- keep an encrypted backup of the private contributor store in a separate location, with
  the key kept apart from the backup, and test a restore at least yearly;
- record in `CHANGELOG.md` any day on which collection or a print was lost.

`GOVERNANCE.md` §8 covers cessation.

## Dependency management

Direct dependencies are declared with version bounds in `pyproject.toml`; the vendored
upstream code under `src/tci/vendor/` is kept as upstream. The operator aims to enable
automated dependency and security alerts on the repository, review updates before merging
them (the test suite and reproduction check are the gate), and remove dependencies that
are no longer used.

## Vulnerability handling

Reports come in as set out in `SECURITY.md`. TCI aims to acknowledge them and to record
any fix in `CHANGELOG.md`.

## Incident handling

An incident is any event that may have changed, withheld or exposed a published number, a
credential, or contributed or personal data. The operator aims to: contain it (revoke
tokens, disable the affected workflow, rotate secrets); preserve evidence, including
workflow logs, before they expire; check the record with `python -m tci.run reproduce
--published`; and correct any affected print under `GOVERNANCE.md` §2.

Where personal data may be involved, follow
[personal-data-breach-procedure.md](personal-data-breach-procedure.md), which carries the
statutory notice periods. A publicly visible incident is recorded in `CHANGELOG.md`.

## Suppliers

TCI relies on GitHub, the hosting provider and the email provider named in `PRIVACY.md`.
Their security is theirs; TCI's part is how it configures them. Suppliers that touch
personal data are handled under the GDPR documents in this folder.

## Review

The operator aims to review this policy, the asset list and the account inventory once a
year and after any incident, and to record the date of each review below.
[TO CONFIRM: first review date]

| Review date | Outcome |
|---|---|
| | |
