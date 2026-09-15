# Privacy notice

**Version 0.1 (draft, pre-launch) · 15 September 2026 · Mark Rusch, administrator**

> **Status.** This site collects less personal data than most, and this notice exists
> to say exactly what, from where, and for how long — not to describe a program of data
> use the site doesn't actually run. Nothing here is legal advice.

## Who this is

TCI (The Compute Indices) is published by **Mark Rusch**, an
individual based in Amsterdam, Netherlands, operating without a registered business entity
(no chamber-of-commerce or VAT registration exists for this activity as of this version).
Regulation (EU) 2016/679 (GDPR) applies because the administrator is established in the EU,
regardless of where a visitor is. Contact for anything in this notice: the
[contact page](contact.html), or rusch.mh@gmail.com.

This site does not currently sell anything or offer a paid service, so the EU's trader-
identification rules for commercial online services are not engaged today. If commercial
data licensing under [DATA-TERMS.md](DATA-TERMS.md) §3 goes live, a full registered
address will be published here before that starts.

## What is collected, and why

Three things touch a visitor's data. Nothing else on this site does.

**1. The contact form** (`contact.html`). Name (optional), email address (required),
message (required). Submitting posts to a Vercel serverless function which sends it,
via **Resend** (Resend, Inc., a US email-delivery service), straight to the
administrator's inbox as an email — your address is set as the reply-to. It is not
written to any database this site controls; once it's an email, it lives in that inbox
under the administrator's ordinary mail retention, same as any other message received.
*Basis:* legitimate interest in being reachable and in answering what's sent
(GDPR Art. 6(1)(f)).

**2. Visit logging.** Vercel serves this site, so it already sees every request. A small
same-origin function (`site/middleware.js`) additionally records, per page view: the
path, a two-letter country code (from Vercel's edge network), and the first 300
characters of the `Referer` header — into **Upstash** (Upstash, Inc., a US Redis
provider). No visitor is stored by IP or by name. Instead each hit is tagged with
`SHA-256(secret salt + calendar day + IP address + user-agent string)`: the same
person is one consistent tag within a day, and an unrelated-looking tag the next, because
the day changes the hash. That tag is used only to estimate how many distinct visitors a
day had; it cannot be turned back into an IP address. **No cookie is set.**
Per-day counts and visitor-count estimates expire automatically **396 days (13 months)**
after being written; a separate rolling log of the most recent 200 page views is capped
by count rather than time. *Basis:* legitimate interest in knowing whether anyone reads
this and in spotting abusive traffic (Art. 6(1)(f)).

**3. Vercel Web Analytics** — the one third-party `<script>` this site loads, served from
the site's own domain rather than a third-party one. Per Vercel's own documentation it
counts page views without cookies or device fingerprinting and cannot identify a visitor.
This notice describes what Vercel publishes about that product; it is not this site's own
mechanism and this site does not control it beyond turning it on.

## Cookies

This site sets none — no analytics cookie, no preference cookie, nothing. That is why
there is no cookie-consent banner: one is a legal requirement for non-essential cookies
(EU ePrivacy rules), and there are none to consent to.

## Who else sees this

Nobody, for any purpose other than running the site. No data collected here is sold,
rented, or used for advertising. It reaches three processors, each doing one job:

| Processor | Job | Based |
|---|---|---|
| Vercel Inc. | Hosting, edge network, the analytics script above | United States |
| Upstash, Inc. | Stores the hashed visit log (item 2) | United States |
| Resend, Inc. | Delivers the contact-form email (item 1) | United States |

All three are US-based and may process data outside the EEA under their own terms. This
notice does not certify which transfer safeguard each relies on (e.g. the EU–US Data
Privacy Framework, standard contractual clauses) — check the processor's own privacy or
data-processing documentation directly if that matters to you.

## Contributors

Sellers, buyers and brokers who submit term prices under
[CONTRIBUTING-PRICES.md](CONTRIBUTING-PRICES.md) are identified to TCI by name, mapped to
a pseudonym. That name-to-pseudonym key is kept offline, separately from the prices, and
is never published. A contributor's rights over that identifying information are the same
as set out below; contact the same address.

## Your rights

Under GDPR Art. 15–21: access what's held about you, have it corrected, have it erased,
restrict or object to its processing, and receive a copy in a portable format. Use the
[contact page](contact.html) or rusch.mh@gmail.com. You may also complain to a
supervisory authority — in the Netherlands, the
[Autoriteit Persoonsgegevens](https://autoriteitpersoonsgegevens.nl/) — or the one in your
own country.

## Children

This site is a compute-pricing benchmark with no content directed at children and no
age-gating, because none is needed for what it publishes.

## Automated decisions

None. Nothing on this site makes an automated decision about a person; the index
calculation path (see [METHODOLOGY.md](METHODOLOGY.md)) processes public market prices,
not personal data.

## Security

The site is served over HTTPS only. Per its own design rule (see `CLAUDE.md`), it makes
no request to any origin but itself and the three processors named above — no third-party
script, stylesheet, font or tracking pixel.

## Changes

Material changes to this notice are recorded in [CHANGELOG.md](CHANGELOG.md), same as
everything else published here.
