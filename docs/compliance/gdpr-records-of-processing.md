# Records of processing activities (GDPR Art. 30)

> **Status: DRAFT — not adopted, not in force, not legal advice.**
> Prepared for review by counsel before adoption. Nothing here is a statement of
> compliance with any law, regulation or standard. This register is a self-assessment of
> what the repository code and [PRIVACY.md](../../PRIVACY.md) describe, not independently verified.

Controller: Mark Rusch, Amsterdam, Netherlands, an individual with no registered entity.
Contact: rusch.mh@gmail.com. No data protection officer has been appointed; none is
assumed to be required `[COUNSEL: confirm Art. 37 does not apply]`.

Last reviewed: `[TO CONFIRM: date]`. Sources for each entry: `site/api/contact.js`,
`site/middleware.js`, `site/api/stats.js`, `src/tci/contrib.py`, `.github/workflows/`,
PRIVACY.md, CONTRIBUTING-PRICES.md. Processor terms are in
[gdpr-processors.md](gdpr-processors.md).

## Does Art. 30(5) exempt this register?

Art. 30(5) relieves an organisation with fewer than 250 employees of the duty to keep a
register, but not where the processing is likely to result in a risk to data subjects,
is not occasional, or includes special categories or criminal-offence data. One person
plainly meets the headcount test. The visit logging (entry 2) runs on every page view and
so is not occasional, which on the wording of the provision plausibly takes TCI outside the
exemption. `[COUNSEL: confirm the reading of "not occasional" and whether it disapplies
the exemption for the whole register.]`

The register is kept regardless. It is short, it is the source for the privacy notice, and
it makes the answer to a supervisory authority's request a matter of handing over a
file. It also makes the gaps between the notice and the code visible.

## Register

### 1. Contact form

| Item | Entry |
|---|---|
| Purpose | Receive and answer messages sent through `contact.html` |
| Lawful basis | Art. 6(1)(f), legitimate interest in being reachable (stated in PRIVACY.md). `[COUNSEL: legitimate-interest assessment not yet written]` |
| Data subjects | Anyone who submits the form: researchers, journalists, prospective licensees, prospective contributors, others |
| Data | Name (optional), email address (required), message text (free text, may contain anything the sender chooses to write), submission time held in the resulting email |
| Recipients | Vercel (runs the serverless function `POST /api/contact`); Resend (delivers the email); the administrator's mailbox provider `[TO CONFIRM: provider]` |
| Automatic reply | When `CONTACT_ACK_FROM` is set in the Vercel environment, Resend also sends a fixed-text confirmation to the address entered. It repeats nothing the visitor typed. `[TO CONFIRM: whether it is currently enabled]` |
| Third-country transfers | Vercel, Resend: United States. Mechanism per each provider's terms, see the processors list. Mailbox provider `[TO CONFIRM]` |
| Retention | Not written to a database the site controls. Held as an email in the administrator's mailbox under ordinary mail retention. `[TO CONFIRM: no fixed period is set; decide one]`. Resend's own log retention `[TO VERIFY]` |
| Security (general) | HTTPS only; API key held in Vercel environment variables, not the repository; honeypot field against bots; access to the mailbox `[TO CONFIRM: two-factor sign-in]` |

### 2. Visit logging (same-origin middleware)

| Item | Entry |
|---|---|
| Purpose | Estimate how many people read the site, which pages, from which country and referrer; spot abusive traffic |
| Lawful basis | Art. 6(1)(f), legitimate interest (stated in PRIVACY.md). `[COUNSEL: legitimate-interest assessment not yet written]` |
| Data subjects | Visitors to pages served by the Vercel deployment |
| Data | Per page view: path, timestamp, two-letter country code, first 300 characters of the `Referer` header. A daily tag `SHA-256(salt, day, IP, user-agent)` is added to a probabilistic distinct-count structure only; it is not stored as a readable value. No cookie is set. IP address and user-agent are processed transiently to compute the tag |
| Recipients | Vercel (serves the request and sees it in any case); Upstash (stores counts and the rolling event list) |
| Third-country transfers | Vercel and Upstash: United States `[TO VERIFY: Upstash region chosen for the database]` |
| Retention | Per-day counts and distinct-visitor estimates expire after 396 days, set by `EXPIRE` in `site/middleware.js`. The list of the 200 most recent events is capped by count, so its age depends on traffic |
| Security (general) | Upstash credentials in Vercel environment variables; the viewer at `/api/stats` requires a shared key `[TO CONFIRM: how the key is handled and rotated]` |

The tag is pseudonymised, not anonymised: the design reduces linkability, but this
register does not describe it as anonymous data. `[COUNSEL: classification.]`

### 3. Vercel Web Analytics

| Item | Entry |
|---|---|
| Purpose | Page-view counts |
| Lawful basis | Art. 6(1)(f), as in PRIVACY.md |
| Data subjects / data | Visitors; whatever the Vercel product collects, described by Vercel as cookie-free and non-identifying. TCI does not control the mechanism beyond enabling it |
| Recipients | Vercel |
| Transfers, retention | Per Vercel's documentation `[TO VERIFY]` |
| Security | Script is served from the site's own origin (`/_vercel/insights/script.js`) |

### 4. Web hosting and server logs

| Item | Entry |
|---|---|
| Purpose | Serve the site |
| Lawful basis | Art. 6(1)(f) |
| Data subjects / data | Visitors; IP address, request headers and timing as held in the hosts' own request logs. TCI does not read these logs `[TO CONFIRM]` |
| Recipients | Vercel (canonical site); GitHub (Pages mirror at `markrusch.github.io/Compute-Indices/`, deployed by `.github/workflows/pages.yml`) |
| Transfers | United States, per each host's terms |
| Retention | Set by each host `[TO VERIFY]` |

The Pages mirror is not named in PRIVACY.md. See the private gap list.

### 5. Contributed term prices: contributor identification

| Item | Entry |
|---|---|
| Purpose | Accept term-price submissions from sellers, buyers and brokers, attribute each to a pseudonym, prevent one party dominating a published cell, correct or supersede a submission on the contributor's request |
| Lawful basis | Art. 6(1)(f) for contact and identification of a business representative; where a written contributor agreement is signed, Art. 6(1)(b) may fit for a sole trader. `[COUNSEL: choose and record. The agreement is described in CONTRIBUTING-PRICES.md as forthcoming.]` |
| Data subjects | Named contact persons at contributing firms; sole traders who contribute in their own name |
| Data | Name, work email and employer of the contact; the pseudonym assigned; the submitted file. Each row holds date, role, kind, GPU model, GPU count, tenor, price, currency, region and optional start date, payment terms and free-text notes; row receipt time; SHA-256 of the file. The price rows are business data about companies and are personal data only where they can be tied to an individual (for example a sole trader) |
| Storage | Two places: the private SQLite store outside the repository (`TCI_PRIVATE_DIR`, default `~/.tci-private`, refused if inside the repository) holding pseudonym plus rows; and the name-to-pseudonym key "kept offline" per PRIVACY.md `[TO CONFIRM: where and in what form]`. Correspondence sits in the administrator's mailbox |
| Recipients | None. Only `contrib aggregate` output (counts and suppressed cells, no contributor and no single quote) is written to `site/data/term/contributed.json`. Independent reviewers may see the private store under a confidentiality agreement (CONTRIBUTING-PRICES.md) `[COUNSEL: reviewer terms]` |
| Third-country transfers | None by TCI. The mailbox provider and any backup service are relevant `[TO CONFIRM]` |
| Retention | No period defined. The store is append-only by trigger. `[COUNSEL/TO CONFIRM: define a period for identity data and for price rows; see the DPIA on how erasure works against an append-only store]` |
| Security (general) | Store kept outside the repository by code; append-only triggers; suppression thresholds before anything is published; key held separately. Encryption at rest and backups `[TO CONFIRM]` |

### 6. Contributor outreach and onboarding correspondence

| Item | Entry |
|---|---|
| Purpose | Contact prospective contributors and licensees, issue pseudonyms, confirm validation |
| Lawful basis | Art. 6(1)(f); for direct electronic mail to individuals, the Dutch ePrivacy rules apply separately `[COUNSEL]` |
| Data subjects | Business contacts at sellers, buyers and brokers |
| Data | Name, work email, employer, role, correspondence |
| Recipients | Mailbox provider `[TO CONFIRM]` |
| Retention | Not defined `[TO CONFIRM]` |
| Art. 14 notice | Where contact details were not obtained from the person, the Art. 14 information is due. `[COUNSEL: how and when]` |

### 7. Public repository and Actions

| Item | Entry |
|---|---|
| Purpose | Publish code, data and history; run the daily and hourly workflows |
| Lawful basis | Art. 6(1)(f) |
| Data subjects | The administrator; anyone who opens an issue, pull request or comment |
| Data | Commit author name and email (`tci-bot`, rusch.mh@gmail.com); GitHub usernames and content of any public contribution. Workflow secrets (source API tokens) are not personal data |
| Recipients | GitHub; the public |
| Transfers | United States, GitHub's terms |
| Retention | Git history is permanent by design |
| Note | The collectors read public price surfaces. The stored `host_id` and `machine_id` fields from marketplace sources are identifiers assigned by the source; whether any identifies an individual is `[TO CONFIRM]` |

### 8. Newsletter

| Item | Entry |
|---|---|
| Purpose | Publish the newsletter at `computeindex.substack.com`; the repository generates `site/substack_post.md` for pasting |
| Lawful basis | Substack collects the subscriber's email and consent on its own page. The role of the administrator versus Substack `[COUNSEL: independent or joint controllers]` |
| Data subjects / data | Subscribers; email address and Substack account data. Nothing is stored in the TCI repository or database |
| Recipients | Substack |
| Transfers | Substack states it participates in the EU-US Data Privacy Framework `[TO VERIFY at the time of adoption]` |
| Retention | Set in Substack; the administrator can export and delete subscribers there `[TO CONFIRM]` |

### 9. Data-subject requests and breach records

| Item | Entry |
|---|---|
| Purpose | Handle requests under Art. 15–21 and record personal-data breaches under Art. 33(5) |
| Lawful basis | Art. 6(1)(c), legal obligation |
| Data | Requester identity, request, reply, breach log. See [data-subject-requests.md](data-subject-requests.md) and [personal-data-breach-procedure.md](personal-data-breach-procedure.md) |
| Retention | `[COUNSEL: suggest a period; the register of requests is normally kept while a claim is possible]` |

## Not personal-data processing

The index calculation path (collectors, `observations`, `daily_index`) processes public
market prices for companies. PRIVACY.md says the same. The only qualification is the
marketplace identifier point under entry 7.
