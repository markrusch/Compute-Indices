# Data-subject requests (GDPR Art. 15 to 22)

> **Status: DRAFT — not adopted, not in force, not legal advice.**
> Prepared for review by counsel before adoption. Nothing here is a statement of
> compliance with any law, regulation or standard. Deadlines are the statutory ones; the
> administrator adds no shorter promise.

Controller: Mark Rusch, rusch.mh@gmail.com, or the site's contact page.
[PRIVACY.md](../../PRIVACY.md) tells people they may use either channel.

## Where to look for a person's data

Search each location in the [register](gdpr-records-of-processing.md):

| Location | What may hold the person |
|---|---|
| Administrator's mailbox | Contact-form emails, contributor and outreach correspondence |
| Resend | Delivery logs of the contact-form email and any automatic reply `[TO VERIFY: what it retains]` |
| Private contributor store and identity key | Pseudonym, rows, contact details |
| Upstash visit log | Path, country, referrer, timestamp; no name, email or IP, and the distinct-count structure cannot be queried for a person. A visitor cannot be singled out from it, so this log will normally not answer a request. `[COUNSEL: Art. 11 position]` |
| Vercel, GitHub | Provider-held logs and account data: direct the person to the provider where TCI cannot retrieve it |
| Substack | Subscriber records: the person should use Substack's own controls; the administrator can remove a subscriber from his publication `[TO CONFIRM]` |
| Public repository | Commit metadata and any issue or pull request the person wrote |

## Steps

1. **Log the request** on receipt: date received, channel, requester, right invoked. The
   period runs from receipt (Art. 12(3)).
2. **Recognise a request.** No form or magic words are required. A message asking what TCI
   holds, or to delete something, is a request. Requests may also come by a
   representative; ask for proof of authority.
3. **Confirm identity, proportionately** (Art. 12(6)). If the request comes from the address
   already held, that is usually enough. If there is reasonable doubt, ask for the minimum
   needed to confirm, such as a reply from the address in the record or a fact only that
   person would know. Do not collect a passport copy where an email reply will do. Do not
   disclose data to someone whose identity is unresolved. Asking for identification
   pauses nothing in law except by the circumstances; note that the clock keeps running
   `[COUNSEL: whether the period is suspended while identity is confirmed]`.
4. **Decide the right and gather the data** (table below).
5. **Reply within one month** of receipt, free of charge. The period may be extended by two
   further months where requests are complex or numerous; the person must be told of the
   extension and the reason within the first month (Art. 12(3)).
6. **Refusal.** If not acting, say so within the month with reasons, and tell the person of
   the right to complain to the Autoriteit Persoonsgegevens and to seek a judicial remedy
   (Art. 12(4)). A manifestly unfounded or excessive request may be refused or charged a
   reasonable fee (Art. 12(5)) `[COUNSEL before relying on this]`.
7. **Tell recipients** of any rectification, erasure or restriction, unless impossible or
   disproportionate, and tell the person who they are if asked (Art. 19).
8. **Record the outcome** and what was sent.

## The rights

| Right | What it means here | Notes |
|---|---|---|
| Access (Art. 15) | Confirm whether data is processed; give a copy and the Art. 15(1) information: purposes, categories, recipients, retention, source, rights, transfers safeguards | The copy must not adversely affect others' rights: redact other people's details. Business contributors' rows are provided only to the extent they are that person's |
| Rectification (Art. 16) | Correct inaccurate data | For contributor prices, a correction is a superseding row from the contributor, per CONTRIBUTING-PRICES.md; contact details are corrected in the correspondence record |
| Erasure (Art. 17) | Delete where a ground applies, subject to Art. 17(3) | Contact-form emails: delete from the mailbox. Contributor store is append-only, see the [DPIA](dpia-contributed-prices.md). Commit history is not rewritten `[COUNSEL]` |
| Restriction (Art. 18) | Stop use other than storage while accuracy or an objection is checked | Mark the record; do not aggregate the person's rows in the meantime `[TO CONFIRM: how, given the tool]` |
| Portability (Art. 20) | Machine-readable copy where processing rests on consent or contract and is automated | Basis for TCI's processing is mostly legitimate interest, so often not applicable; say so. A contributor agreement may change that |
| Objection (Art. 21) | Where processing rests on legitimate interest, stop unless compelling legitimate grounds override or it is needed for legal claims | Contact-form and outreach data: stop using and delete unless there is a reason to retain. Visit logging: no data identifies the person |
| Automated decisions (Art. 22) | None are made (PRIVACY.md) | Say so if asked |

## Template replies

Replace bracketed text. Keep each reply factual.

**Acknowledgement**

> Subject: Your request under the GDPR
>
> Hello [name],
>
> I received your request on [date]. I will reply by [date one month after receipt]. If I
> need more time because the request is complex, I will tell you before that date and give
> the reason.
>
> [If identity is in doubt: To make sure I give the data to the right person, please
> [specific step, for example reply from the address you used before].]
>
> Mark Rusch, The Compute Indices

**Access, data found**

> Hello [name],
>
> Attached is a copy of the personal data I hold about you. In summary: I hold [categories],
> received from [source], used for [purpose] on the basis of [basis]. It is shared with
> [recipients]; [transfer information]. I keep it for [retention or the criteria]. You also
> have the rights to correction, erasure, restriction and objection where they apply, and
> the right to complain to the Autoriteit Persoonsgegevens (autoriteitpersoonsgegevens.nl)
> or another supervisory authority.
>
> Mark Rusch

**Access, nothing held**

> Hello [name],
>
> I searched [the mailbox and the records listed] for [the address or name you gave] and
> found no personal data about you. [If a provider holds a log: [Provider] holds its own
> records, and you can ask it directly.]
>
> Mark Rusch

**Erasure, done**

> Hello [name],
>
> I have deleted [what] on [date]. [Provider] may keep its own copy for [period]; I have
> asked it to delete it. [Anything retained and the reason.]
>
> Mark Rusch

**Refusal in whole or part**

> Hello [name],
>
> I am not able to [action] because [specific reason and the provision relied on]. You can
> complain to the Autoriteit Persoonsgegevens (autoriteitpersoonsgegevens.nl) and you can
> ask a court to review this decision.
>
> Mark Rusch

**Extension**

> Hello [name],
>
> Your request needs more time because [reason]. I will reply by [date, at most two months
> after the original due date].
>
> Mark Rusch

## Request log

```
Request ID:            DSR-YYYY-NNN
Received (date, channel):
Requester / representative:
Right(s) invoked:
Identity confirmed (how, date):
Due date (one month):  Extended to (if so, reasons told on):
Data found and where:
Decision and reasons:
Recipients informed (Art. 19):
Reply sent (date):
Closed:
```

Keep the log privately. Retention period `[COUNSEL]`.
