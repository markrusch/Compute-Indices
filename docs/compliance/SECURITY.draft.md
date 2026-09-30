# Security policy

> **Status: DRAFT — not adopted, not in force, not legal advice.**
> Prepared for review by counsel before adoption. Nothing here is a statement of
> compliance with any law, regulation or standard. [COUNSEL: safe harbour wording]

TCI publishes a daily reference price for GPU compute. The security question that matters
most is whether a published number can be changed, withheld or forged without it being
noticed. Reports on that are as welcome as reports on code.

## How to report

Use GitHub private vulnerability reporting: open the repository's **Security** tab and
choose **Report a vulnerability**. [TO CONFIRM: private vulnerability reporting is enabled
on the repository.] If you cannot use GitHub, email [TO CONFIRM: security contact
address]. Please do not open a public issue or pull request for a vulnerability.

Include what you found, where, how to reproduce it, and what you think it affects. Say
whether you want to be credited by name.

TCI aims to acknowledge a report. It gives no response-time or fix-time commitment, and it
is run by one person.

## In scope

- The code in this repository and its GitHub Actions workflows.
- The data pipeline: collectors, normalisation, index calculation, storage.
- The published site and the files under `site/data/`.
- The integrity of published prints: a way to alter, replace, backdate or suppress a
  print, a digest, `METHODOLOGY.lock` or the record `python -m tci.run reproduce`
  checks.
- Any path by which contributed prices, or the identity of a contributor, could reach the
  public repository or site.
- Credentials or tokens committed by mistake.

## Out of scope

- Findings in a price source's own site or API. Report those to that source.
- Third-party services TCI relies on (GitHub, the hosting provider, the email provider),
  except for how TCI configures them.
- Denial-of-service testing, volumetric traffic, or anything that places load on a price
  source or on the site.
- Social engineering, phishing, or physical attacks against the operator or anyone else.
- Reports that a price is wrong, a source is missing or the methodology is a poor choice.
  Those are methodology questions: see `GOVERNANCE.md` on complaints and corrections.
- Missing headers or settings with no demonstrated effect, and output from automated
  scanners submitted without analysis.

## Good-faith research

TCI will not pursue a claim against a person for research that stays within this policy:
it is done in good faith, avoids accessing more data than needed to show the problem,
does not read, change or keep contributed or personal data, does not degrade the
service, and is reported privately and not disclosed until TCI has had a reasonable
opportunity to respond. This is a statement of TCI's own intent. It does not bind
GitHub or any other third party, does not authorise access to anyone else's systems, and
does not remove any legal duty that applies to you. [COUNSEL: safe harbour wording]

## No bounty

TCI does not pay for reports. Credit in `CHANGELOG.md` is offered if you want it.

## Disclosure

Where a report leads to a fix, the fix and its effect on any published number are
recorded in `CHANGELOG.md`, and a correction to a print follows `GOVERNANCE.md` §2.
TCI asks reporters to hold details until the fix is published.
