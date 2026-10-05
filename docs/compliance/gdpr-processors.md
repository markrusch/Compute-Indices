# Processors and independent controllers

> **Status: DRAFT — not adopted, not in force, not legal advice.**
> Prepared for review by counsel before adoption. Nothing here is a statement of
> compliance with any law, regulation or standard. URLs were opened on 30 September 2026
> to see whether they resolve; terms were not read in full or assessed.

Controller: Mark Rusch. Each row is a service the code or PRIVACY.md shows in use, or
that follows from how the project operates. Entries marked `[TO CONFIRM]` are things only
the administrator can see in his accounts.

"Mark to confirm accepted" means: the administrator has signed in to the account, found
the data processing terms in force, and accepted or countersigned them where the
provider requires that. Tick only after doing so, and note the date.

Transfer mechanisms below are what the provider's own pages say. Whether a provider is
currently listed under the EU-US Data Privacy Framework is checked at
https://www.dataprivacyframework.gov/list `[TO VERIFY at adoption and at each review]`.

## Processors

| Provider | Role | Data | Terms | Transfer mechanism | Mark to confirm accepted |
|---|---|---|---|---|---|
| Vercel Inc. | Processor: hosting, serverless functions, edge network, Web Analytics | Every request (IP, headers); contact-form submissions in transit; the analytics data | DPA: https://vercel.com/legal/dpa (resolves; its Schedule 3 names the 2021 Standard Contractual Clauses as the transfer mechanism) | SCCs per the DPA; DPF participation `[TO VERIFY]` | [ ] Accepted on ____ by ____ `[TO CONFIRM: plan tier and whether the DPA applies to it]` |
| Upstash, Inc. | Processor: Redis store for the visit log | Path, country, referrer prefix, timestamp; distinct-count structures | DPA: https://upstash.com/static/trust/dpa.pdf (resolves; contents not read) | `[TO VERIFY]` | [ ] Accepted on ____ by ____ `[TO CONFIRM: database region]` |
| Resend, Inc. | Processor: email delivery for the contact form and the optional automatic reply | Sender name and address, message text, recipient address | DPA: https://resend.com/legal/dpa (resolves; it includes EU and UK SCCs) | SCCs per the DPA; DPF `[TO VERIFY]` | [ ] Accepted on ____ by ____ |
| GitHub, Inc. (Microsoft) | Processor or independent controller depending on the data `[COUNSEL]`: repository, Actions runner, Pages mirror | Commit author identity, account data of contributors, Pages request logs | Terms and DPA via https://docs.github.com/en/site-policy (the DPA page returned 403 to an automated request; not verified) `[TO VERIFY]` | DPF and SCCs per GitHub's documentation `[TO VERIFY]` | [ ] Accepted on ____ by ____ |
| Mailbox provider `[TO CONFIRM]` | Processor | Contact-form emails, contributor correspondence, contributor identity | `[TO CONFIRM: provider data processing addendum]` | `[TO VERIFY]` | [ ] Accepted on ____ by ____ |
| Domain registrar for thecomputeindices.com | Processor or independent controller `[TO CONFIRM: who]` | Registrant name, address, email, phone as held for the domain | `[TO VERIFY]` | `[TO VERIFY]` | [ ] Accepted on ____ by ____ |
| DNS provider `[TO CONFIRM: registrar or Vercel]` | Processor | Query logs | `[TO VERIFY]` | `[TO VERIFY]` | [ ] Accepted on ____ by ____ |
| Storage or backup for the private contributor store `[TO CONFIRM: none, local disk, or a service]` | Processor if a service is used | The private contributions database and the offline key | `[TO VERIFY]` | `[TO VERIFY]` | [ ] Accepted on ____ by ____ or [ ] None used |

## Independent controllers

| Provider | Role | Data | Terms | Transfer mechanism | Mark to confirm reviewed |
|---|---|---|---|---|---|
| Substack Inc. | Runs the newsletter and its subscriber list; whether it is an independent or joint controller with the administrator is for counsel `[COUNSEL]` | Subscriber email addresses and account data | Privacy policy: https://substack.com/privacy (resolves; states EU-US Data Privacy Framework participation). No separate processor-terms URL found: https://substack.com/dpa returned 404 `[TO VERIFY]` | DPF per Substack's policy `[TO VERIFY]` | [ ] Reviewed on ____ by ____ |
| Price sources listed in `SOURCES.md` and `config/source_registry.yaml` | Independent controllers of their own public data; TCI reads public price pages | Company prices. Marketplace sources expose seller or host identifiers, see entry 7 of the register | Each source's terms, recorded per source in SOURCES.md | None; data is read from the source | [ ] Reviewed on ____ by ____ |
| Contributors | Each supplies its own business data and the contact person's details | See the DPIA | Contributor agreement, not yet written | None by TCI | [ ] Agreement signed per contributor |

## Not in use, on the code as read

No advertising, analytics-cookie, tag-manager, font or CDN provider is loaded by the site.
`site/vercel.json` sets a Content-Security-Policy that limits scripts, styles, fonts,
images and connections to the site's own origin. Fonts are self-hosted. An outbound link
to Substack or GitHub is a link and not a data flow until the visitor follows it.

## Review

Re-check this list whenever a new service is connected, a Vercel environment variable is
added, or a workflow gains a secret. `site/api/*.js` and `.github/workflows/*.yml` are the
files to search for new hostnames.
