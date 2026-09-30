# Conflicts of interest policy

> **Status: DRAFT — not adopted, not in force, not legal advice.**
> Prepared for review by counsel before adoption. Nothing here is a statement of
> compliance with any law, regulation or standard. It extends GOVERNANCE.md §4 and does not replace it.

This policy is designed with reference to IOSCO Principles 4 and 5 and to the conflict
provisions for commodity benchmark administrators in Annex II of Regulation (EU) 2016/1011.
[TO VERIFY: Annex II point numbers.] It is adopted voluntarily. TCI does not claim to be in
scope of that Regulation and is a research publication, not for use in financial
instruments. The policy is self-assessed and has not been independently verified.

[GOVERNANCE.md](../../GOVERNANCE.md) §4 says the author may trade on venues whose prices
the index observes, that the calculation path contains no expert judgement, that observations
are immutable, and that positions on an observed venue are disclosed where relevant. This
policy keeps every one of those statements and adds the procedure they leave out.

## 1. Who and what it covers

- **The administrator**, Mark Rusch, as an individual, including anything held through a
  future company. [TO CONFIRM: whether a BV would bring its own directors and shareholders in.]
- **Oversight Panel members** ([terms](oversight-panel-terms.md)). They follow the same
  register and recusal rules, and the independence tests in those terms.
- **Anyone else who is given a role in producing TCI**, such as a person with commit access
  to the repository. [TO CONFIRM: at present the administrator is the only person.]

Contributors of term prices are covered by the
[contributor code of conduct](contributor-code-of-conduct.md), not this policy.

## 2. Interests that must be disclosed

Each of the following, held by the person or by a member of their household, is an interest
to be recorded in the register:

1. **Employment or engagement in finance.** Any employer or client in trading, market making,
   lending, asset management, brokerage or banking. The project roadmap records an open decision about
   taking a finance role alongside or instead of running TCI (docs/ROADMAP.md §5, item 5).
   [TO CONFIRM: whether the employer is to be named in the register. On acceptance, the role, the employer's rules on outside
   activity and its approval of TCI as an outside interest go on the register the same day.]
2. **Personal GPU rental.** Any GPU capacity the person rents or buys from a provider TCI
   observes, and any capacity the person rents out or sells. Spending on ordinary benchmark
   runs for TCI's own measurements is recorded with its provider and its rough annual amount.
3. **Trading or holdings.** Any position, direct or through a fund that the person controls,
   in a provider on the panel or under screening, in a listed GPU-cloud or GPU-manufacturer
   share, or in any GPU-linked instrument, compute forward, or financing secured on GPU
   capacity. The register states the instrument, direction and whether it is above or below
   a level of [TO CONFIRM: de minimis, e.g. a percentage of net worth] rather than its exact
   size.
4. **Consulting or paid work for a provider**, a buyer, a lender or a data vendor in the
   compute market, including paid research or speaking. Unpaid advice counts if it gives
   the person an insider view of a price TCI observes.
5. **Data licensing.** Any commercial agreement over TCI data (DATA-TERMS.md §3), and any
   discussion that could lead to one, with the counterparty named once it is signed.
6. **Roles at a source.** Any directorship, employment or ownership at a source in
   `config/source_registry.yaml`.
7. **Other.** Anything else a reasonable reader would want to know before trusting a print.

## 3. The register of interests

- Held in the repository at `docs/compliance/register-of-interests.md` [TO CONFIRM: path;
  publishing detail level, considering privacy of household members]. Published because
  GOVERNANCE.md §4 already promises disclosure "where relevant" and a register is the
  only way a reader can check that promise.
- Each entry: interest, date started, date ended, the providers or series it touches, and
  the date of the last review.
- Reviewed when anything changes and in any event each January and July. Entries are
  amended by adding a new dated line, never by deleting the old one.
- Entries that would identify a household member or an employer's confidential dealings are
  recorded by category with an [COUNSEL: privacy and employment-law review] and the full
  entry is held privately and shown to the Panel.
- A nil return is recorded as a nil return.

## 4. Rules that apply to the administrator

1. **Trading.** The administrator will not trade in a position that he knows depends on a
   TCI print that has not yet been published, or on the content of a planned methodology
   change or notice that has not yet been published. Publication of a print or notice is
   the moment from which trading on it is treated as trading on public information.
   [COUNSEL: whether market-abuse or insider-dealing rules touch this, and whether a
   pre-clearance list or a blackout window around methodology changes is advisable.]
2. **No discretion over a number.** The calculation path has no expert judgement (GOVERNANCE.md
   §4). The administrator does not override, delay or omit a print to favour a position.
   A gap is published as a gap. A conflict cannot therefore be exercised through a print
   except through the methodology and the panel, which is why section 5 exists.
3. **Methodology and panel decisions.** Admitting, removing or reweighting a provider, and any
   methodology change that moves a print, is a decision where an interest in that provider
   or in a series it feeds matters most. Section 5 applies.
4. **Outside employment.** If the administrator takes a role in finance, the employer's
   consent to TCI is obtained in writing before the role starts, and the role is on the
   register the same day. If the employer's rules stop him from running TCI unaided or
   from holding a position in the register, the response is disclosure, handing off the
   affected work, or ceasing publication under GOVERNANCE.md §8, whichever the rules require.
5. **Personal use of unpublished information.** Contributed term prices and the private
   contributor store are confidential. They are not used for trading, consulting or
   any other purpose than aggregation for TCI.
6. **Do not accept indirect pressure.** A provider, buyer or contributor that asks for a
   particular result, a place on the panel, or early sight of a print is declined. The request
   and the refusal go in the log in section 7.

## 5. Recusal

- An interest in a provider on the panel, or in a series, is disclosed in the minutes or the
  CHANGELOG entry for any decision touching that provider or series. The administrator states
  what he would gain or lose and what steps limit that, such as sending the change to the
  Panel first, and publishing the decision rule rather than the result.
- Because the administrator cannot recuse himself from his own index, the safeguard is
  transparency plus the Panel's review, not abstention. Where the interest is material, the
  Panel's recommendation on the item is published before the change takes effect
  and, if the administrator does not follow it, the reason is published.
- A Panel member with an interest in an item leaves the discussion of it and does not vote or
  offer a recommendation on it. The minutes record the recusal.
- Where the administrator holds an interest in a provider large enough that, in his own
  judgement, it undermines a decision about that provider, the decision is deferred to the
  next Panel meeting if timing allows. [TO CONFIRM: what "large enough" means; propose
  a numeric test with counsel.]

## 6. Gifts, hospitality and benefits

- Neither the administrator nor a Panel member accepts a gift, hospitality, discount, free or
  discounted compute, credits, travel or paid engagement from a provider on the panel, a
  provider under screening, a contributor, or a data licensee, other than: publicly available
  free tiers and self-service API keys that anyone can get (GOVERNANCE.md §1, "A public
  API is a public source"); and items of modest value offered to all attendees of a public
  event. [TO CONFIRM: monetary threshold for modest value, commonly a small figure in euros.]
- A gift over the threshold is declined or returned. If that is impractical, it is recorded in
  the gifts section of the register with its estimated value and given away.
- Credits or hardware for TCI's own measurement work are accepted only if the giver, terms and
  value are published, and the giver has no claim on the outcome of the measurement.
  [COUNSEL: any joint research with a provider or partner (ROADMAP.md §5, item 7) should be
  reviewed under this rule before it goes ahead.]

## 7. Breaches, questions and the log

- Anyone can raise a possible conflict via [the contact page](../../contact.html). It is handled
  under the complaints route in GOVERNANCE.md §6, with the same acknowledgement time
  (7 days) and a published outcome.
- The administrator keeps a log of conflicts raised, decisions taken and requests declined.
  The Panel sees the log at each meeting.
- A conflict found after the event is disclosed in the next CHANGELOG entry with its extent.
  Where it affected a print, the correction policy in GOVERNANCE.md §2 applies.

## 8. Review

Annually, together with the methodology review in GOVERNANCE.md §7, and on any change in the
administrator's employment or in the structure through which TCI is run.
