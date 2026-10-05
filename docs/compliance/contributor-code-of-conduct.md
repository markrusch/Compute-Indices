# Contributor code of conduct

> **Status: DRAFT — not adopted, not in force, not legal advice.**
> Prepared for review by counsel before adoption. Nothing here is a statement of
> compliance with any law, regulation or standard. It supplements
> [CONTRIBUTING-PRICES.md](../../CONTRIBUTING-PRICES.md) and does not replace the written
> contributor agreement that document says will accompany the first contribution.

This code is shaped on the contributor-facing requirements of Annex II of Regulation (EU)
2016/1011 for commodity benchmarks, and on IOSCO Principle 5 and the IOSCO principles for
oil price reporting agencies, in the form of a voluntary standard for people who send prices
to TCI. [TO VERIFY: Annex II point numbers; whether a code of conduct for contributors is
required or only described there.] TCI does not claim to be in scope of that Regulation.
Contributed prices feed a separate table of term prices. They do not enter the daily index
and no print depends on them. TCI is a research publication and may not be used as a
reference price in a financial instrument.

## 1. Who this applies to

Anyone who submits a file through `python -m tci.run contrib` on the administrator's
behalf, or sends a file to the administrator for that purpose: sellers, buyers and brokers.
Submitting a file is acceptance of this code. [COUNSEL: whether acceptance-by-submission
binds, or whether signature of the contributor agreement is needed first. Recommended: no
file is ingested before signature.]

## 2. What may be submitted

Only rows in the format of `contrib/template.csv` and the columns in CONTRIBUTING-PRICES.md.
That means, per row: a price per GPU-hour ex-VAT, the GPU model, GPU count, tenor, region,
currency, and whether it is `executed` (a signed contract), `firm_quote` (an offer the
contributor would sign) or `indicative`.

A contributor must not submit:

- a price that was not actually charged, signed or offered on the terms stated;
- a price for a customer, or under a contract, whose terms forbid disclosure to a third
  party in aggregated form, unless the counterparty has agreed;
- a price that reveals a named customer or supplier. The `notes` field is for what makes a
  price not comparable, such as bundled storage. It is not for names;
- personal data, other than the contact details the contributor gives the administrator
  to be identified;
- a price the contributor has been asked by anyone to submit in order to move a
  published cell.

## 3. Bona fide prices only

1. Submit what was quoted or signed. A price the contributor would like the market to
   believe is not a submission.
2. Label `kind` honestly. An `indicative` price is not a `firm_quote`, and a `firm_quote`
   is not `executed`. Mislabelling to give a price more weight is a breach.
3. Convert monthly or total contract values to a per-GPU-hour, ex-VAT price before
   submitting. If you cannot do so reliably, do not submit.
4. Do not split one deal into many rows. Each contributor's quotes in a cell are reduced to
   their median before pooling, so splitting adds no weight, and a pattern that looks
   designed to move a cell is excluded and the exclusion recorded with its reason.
5. State a correction promptly. If a submitted price was wrong, send a one-row correction
   file that names the row it replaces. The original stays in the store.
6. Disclose, when first contributing and on any change, whether the contributor also trades
   on or sells capacity into the markets the aggregates describe. This is already required
   by CONTRIBUTING-PRICES.md.

## 4. No coordination

[COUNSEL: competition law on sharing current prices between competing sellers. Read
[the whole section] with this in mind. The roadmap records this question as unresolved
(docs/ROADMAP.md §5 item 4 and §10). Until counsel has answered, sellers who compete with one
another should not be onboarded.]

A contributor must not:

- discuss, agree or signal with another contributor or seller what to submit, whether to
  submit, or what price to charge or quote;
- use the administrator, this process or any published cell as a channel for exchanging
  information with a competitor;
- withhold or time a submission to influence a published cell;
- ask the administrator what another contributor submitted. The administrator will not say,
  and no contributor sees another's identity or quotes.

Design features that address the risk, and are not a substitute for legal advice: a cell is
published only with at least three contributors, only if none holds more than half of the
cell's GPU volume, and only if it is not made up entirely of indicative quotes; publication
is an aggregate over a window, never a single quote or a date narrower than the window;
quartiles appear only with five or more contributors. The thresholds are TCI's own and
have not been reviewed by competition counsel. [COUNSEL: whether historic, aggregated,
anonymised data at these thresholds is acceptable, whether forward-looking `firm_quote` rows
change that, and what window is safe.]

## 5. The contributor's responsibility for its right to share

The contributor is solely responsible for having the right to send each price to TCI and for
permitting TCI to store it privately and publish it in aggregate. The contributor confirms this
on each submission. The administrator does not check contracts, and is not liable
if the contributor was not entitled to share a price. Contributors should check for
confidentiality clauses first, because aggregation does not change what a contributor
agreed to. [COUNSEL: warranty and indemnity wording in the contributor agreement; how far
they are enforceable against a small seller, and whether the administrator should ask for
them at all.]

## 6. The administrator's rights

1. **Reject without giving a reason.** The administrator may reject, exclude or hold any
   file or row, before or after ingest, without giving a reason, including where a price
   looks inconsistent with the market, where a pattern looks designed to move a cell, or
   where the contributor has not answered a question. Validation already rejects a file
   with a line and field named; that is technical feedback and does not limit this right.
2. **Suppress or withdraw a cell.** The administrator may decline to publish a cell that
   passes the numerical tests, and may stop publishing contributed cells at any time.
3. **Stop accepting from a contributor.** The administrator may end a contributor's
   participation at any time. Rows already stored stay in the append-only store and stay
   in past aggregates unless the record-keeping policy allows otherwise.
4. **Ask questions.** The administrator may ask a contributor about a price. A
   contributor is not obliged to answer, but the row may be excluded if it does not.
5. **Report.** [COUNSEL: whether the administrator may or must report suspected
   manipulation or a competition-law problem to a regulator, and how to word this.]

## 7. No warranty and no reliance

Contributed cells are research aggregates. TCI gives no warranty that any cell is accurate,
complete, representative or current, and no contributor may describe TCI as having verified
its price. They cannot be recomputed from public data, and each published cell says so. A
cell is not an index and not a settlement price, and no one may use one as a reference in
a financial instrument. TCI accepts no liability to a contributor for how a cell is used
by a third party. [COUNSEL: extent of the exclusion under Dutch law, consumer and
unfair-terms rules if any contributor is not acting in the course of a trade.]

## 8. Confidentiality and privacy

- Contributions are held in a private database outside the repository. The software refuses a
  location inside it. A contributor is identified to TCI by name and recorded under a
  pseudonym. The key from pseudonym to name is kept offline and not with the prices
  (CONTRIBUTING-PRICES.md; [PRIVACY.md](../../PRIVACY.md)).
- Only the output of `contrib aggregate`, which carries counts and suppressed cells but no
  contributor and no single quote, is written to `site/data/term/contributed.json`.
- The administrator will not disclose a contributor's identity or a single quote to any
  person, except to an independent reviewer under a confidentiality agreement, or where
  a law or court order requires it. [COUNSEL: compelled disclosure; legal privilege; what the
  administrator tells the contributor.]
- A contributor's rights over identifying information are those in PRIVACY.md, and how
  they interact with the append-only store is set out in the
  [record-keeping policy](record-keeping-policy.md).

## 9. Breach

Where a contributor breaches this code, the administrator may exclude their rows, stop
accepting them, and record the reason in the private log. Whether the administrator publishes
that a breach happened, and whether a breach is reported outside TCI, is decided with
counsel. [COUNSEL.] The remedies and any liability are those in the contributor agreement.

## 10. Changes

The administrator may change this code by publishing a new version and telling active
contributors. A submission after a change is a submission under the changed code.
