# Oversight Panel: terms of reference

> **Status: DRAFT — not adopted, not in force, not legal advice.**
> Prepared for review by counsel before adoption. Nothing here is a statement of
> compliance with any law, regulation or standard. No Oversight Panel exists as of this draft.

These terms are designed with reference to the oversight function described for commodity
benchmarks in Annex II of Regulation (EU) 2016/1011 and to IOSCO Principle 5 (Internal
Governance and Oversight). [TO VERIFY: Annex II point numbers against the consolidated
EU text.] They are adopted voluntarily. TCI does not claim to be in scope of that
Regulation, does not apply for authorisation under it, and remains a research publication
that may not be used as a reference price in a financial instrument (see
[GOVERNANCE.md](../../GOVERNANCE.md), "Regulatory scope"). Nothing here changes that
position. The design is self-assessed and has not been independently verified.

GOVERNANCE.md lists an independent oversight committee as settlement-grade precondition 5.
Forming the Panel does not satisfy that precondition by itself and is not a statement that
TCI is or will become a settlement benchmark.

## 1. What the Panel is, and what it is not

The Oversight Panel ("the Panel") is a group of two or three outside people who read
TCI's governance record and tell the administrator what they think of it.

The Panel is **advisory only**. It has no power to approve, block, direct or reverse any
decision, print, methodology version, correction or notice. The administrator (Mark Rusch,
an individual, Amsterdam) takes every decision and is the only person who does.

A Panel member:

- is not an officer, director, employee, partner, agent or representative of the
  administrator or of any entity that later takes over the index;
- has no authority to bind, or to speak for, the administrator or TCI, and must not say
  otherwise;
- owes no fiduciary duty to the administrator, to TCI, to any reader or to any user of the
  data, and is not a supervisor, auditor, certifier or guarantor of the index;
- gives opinions, not assurance. A Panel view is not an audit opinion, and the administrator
  will not describe it as one.

Readers who see the words "reviewed by the Oversight Panel" must be able to tell from the
published minutes exactly what was reviewed and that the Panel had no power of decision.
The administrator will not use Panel membership as an endorsement of the accuracy of any
print.

## 2. Liability

[COUNSEL: this whole section. Governing law, whether a volunteer-agreement form is enforceable
as drafted under Dutch law, whether an indemnity from an individual with no legal entity is
worth anything to the member, and how it is affected by the administrator becoming a BV.]

1. **No assumed liability.** A member assumes no personal liability to the administrator or
   to anyone else for advice given in good faith. Each member's engagement letter will say
   so, and will limit the member's undertaking to reading what is sent and giving honest
   views.
2. **Recommendations are not decisions.** Because the administrator decides, responsibility
   for a decision rests with the administrator, whether or not it followed the Panel's
   recommendation.
3. **Indemnity and cover.** The administrator will provide each member with
   [COUNSEL: indemnity for claims arising from good-faith service, and/or D&O-type or
   professional-indemnity cover naming members; scope, cap, exclusions for wilful misconduct
   and gross negligence, and who stands behind an indemnity given by a sole proprietor]. No
   member is to be appointed until the form of protection is settled and in writing. The
   administrator does not promise cover that has not been bought.
4. **No liability on TCI beyond the letter.** The engagement letter is the whole
   arrangement. It creates no employment, agency, partnership or joint venture, and gives no
   member any right to payment, equity, data licences or a future role.
5. **Confidentiality of a member's own conflicts.** Nothing here obliges a member to disclose
   anything beyond the declaration in section 4.

## 3. Composition

- Two or three members. [TO CONFIRM: the target number and whether an alternate is named.]
- Appointed by the administrator by written invitation. The administrator publishes each
  member's name, one-paragraph background and declaration of interests on appointment, with
  the member's prior written consent. Where a member declines to be named, the Panel does
  not go ahead with that member.
- Unpaid. Reasonable out-of-pocket costs may be reimbursed if agreed in advance in writing.
  [COUNSEL: whether reimbursement changes the volunteer characterisation for tax or
  employment purposes.]
- Between them the members should cover at least: commodity or benchmark administration,
  and the compute or data-centre market. No seat is reserved for any provider, buyer or
  trading firm.
- The Panel chooses one member to chair its meetings. The chair has no casting vote over the
  administrator.

## 4. Independence tests

A person is not eligible, and a member must resign or step aside, if any of the following is
true, now or in the previous 12 months:

1. They are employed by, are an officer or consultant of, or hold a material interest in a
   **provider on the panel** named in the current methodology's `panel` list in
   `config/factors.yaml`, or in a provider TCI has under screening in
   `config/source_registry.yaml`.
2. They are employed by, or advise, a **trading counterparty** or a firm that trades
   GPU-linked instruments, compute forwards or financing secured on GPU capacity, or a
   **contributor** to TCI's term-price store.
3. They are the administrator's employer, business partner, close relative or a person
   financially dependent on the administrator, or the reverse.
4. They receive money from the administrator other than agreed expense reimbursement.
5. They hold a position in any GPU-linked instrument or in a listed GPU-cloud provider that a
   reasonable reader would see as material to their judgement. [TO CONFIRM: a de minimis
   level, if any, for diversified fund holdings.]

Each member signs a declaration on appointment and each year, using the same register
fields as the [conflicts of interest policy](conflicts-of-interest-policy.md), and updates
it within 14 days of a change. The Panel's register is published with the administrator's.
A member with a conflict on a specific item, short of one that disqualifies them, declares
it at the start of the item and leaves the discussion of it. The minutes record this.

## 5. What the Panel is asked to review

The administrator will send the Panel, in this order of priority:

1. **Methodology changes** before they are announced, so that any recommendation reaches the
   administrator while a change can still be altered. This covers a new version under
   the procedure in GOVERNANCE.md §1, panel admissions and removals, and changes to the
   unit definition or the aggregation.
2. **Notices** in `config/notices.yaml`, before publication where the timing allows.
3. **Corrections**: every new revision flagged `correction` since the last meeting, with
   the cause.
4. **Complaints** received under GOVERNANCE.md §6 and their outcomes. Complainants' identities
   are removed unless they consent.
5. **Conflicts**: the administrator's register of interests and any recusal made.
6. **The annual review** under GOVERNANCE.md §7 (first due July 2027), including whether the
   Panel itself is working.
7. **Anything a member raises**, including a request to see the constituent audit set for a
   date (`python -m tci.run constituents --date D`) or to run the reproduction check
   (`python -m tci.run reproduce --published`).

Members may see everything in the public repository. They do not receive contributors'
names, individual contributed quotes or the offline key mapping pseudonyms to names. A
member who needs to test an aggregate against the private store does so only under a
confidentiality agreement, as CONTRIBUTING-PRICES.md already says of independent reviewers.
[COUNSEL: form of that agreement.]

The Panel does not review individual daily prints before they are published. The calculation
path has no discretionary judgement, and adding a human approval step would add exactly that.

## 6. Meetings

- Quarterly, by video call, plus a call at a member's or the administrator's request when
  a methodology change is planned. [TO CONFIRM: whether four meetings a year is realistic for
  volunteers and for a 10-15 hour a week project.]
- Papers go out at least 7 days before a meeting where possible. The administrator aims to
  do this and does not guarantee it.
- Quorum is two members. The administrator attends to present, and leaves for any part of
  the meeting the Panel wishes to hold alone.
- Members can send written comments between meetings. They will be handled at the next
  meeting, or sooner if the administrator can manage it.

## 7. Recommendations and the administrator's answer

The Panel records its views as recommendations. For each one the administrator publishes,
in the minutes or the next CHANGELOG.md entry, either:

- that it was accepted and what changed; or
- that it was rejected or only partly followed, **with a reason**.

The reason is published even when the recommendation was unanimous or the reason is that
the administrator disagrees. A dissent by a member is published if the member asks for it.
The administrator is not bound to follow a recommendation and is not bound to wait for one
before acting where he considers a delay would harm the accuracy of a published print. In that
case the minutes say so at the next meeting.

## 8. Minutes and publication

- The administrator keeps minutes, and the Panel approves them at the next meeting.
- Minutes are published in the repository under `docs/oversight/` [TO CONFIRM: path] within
  30 days of approval. They record attendance, declared interests, items reviewed,
  recommendations, and the administrator's answers.
- Nothing is redacted except: a contributor's identity or single quote; security detail that
  would let someone attack the site or a source; personal data of a complainant; and legal
  advice. Each redaction is marked as such, with the category.
- Minutes are kept as set out in the [record-keeping policy](record-keeping-policy.md).

## 9. Term, resignation and removal

- Each appointment runs for two years and can be renewed once. [TO CONFIRM.] Terms are
  staggered where the Panel has three members, so that the whole Panel does not turn over at
  once.
- A member may resign at any time by writing to the administrator. The resignation and the
  member's stated reason, if given and consented to, are published.
- The administrator may remove a member for failing an independence test, for missing more
  than two consecutive meetings without excuse, for disclosing confidential material, or
  where a member asks to be removed. Every removal is published with a reason.
- The Panel may ask the administrator in writing to review a member's position. The
  administrator answers with a published reason.
- A member who leaves stays bound by confidentiality for the period the engagement letter sets.
  [COUNSEL: period.]

## 10. Changes to these terms

The administrator may change these terms. A change takes effect only after the Panel has
been shown it, the administrator has published any Panel objection with a reason, and it is
recorded in CHANGELOG.md. A change cannot reduce a member's protection under section 2 for
service already given.
