---
name: research-note
description: >-
  Write or revise a TCI research note for publication on thecomputeindices.com. Use when
  asked to draft, edit or publish a research note, a research paper, a finding, an
  analysis of the index data, or anything destined for research/*.md. Covers the
  institutional register these notes are published in, the essay structure they use, the
  rule against publishing internal reasoning, and the evidence standard every figure has
  to meet. Does not cover the Substack newsletter, which has its own voice in STYLE.md.
---

# Writing a TCI research note

A research note is published by **The Compute Indices**, an index administrator. It is
not a blog post, not a changelog, and not a message to a colleague. Someone may cite it
years from now to justify a number, so it has to read like a document that expects to be
cited.

STYLE.md governs the *newsletter*, which is one person's voice and is allowed to be
personal. A research note is the institution speaking. Where the two conflict, this file
wins for anything in `research/`.

## The register

**Byline is the institution.** `TCI Research Note 2026-NN · D Month YYYY · The Compute
Indices`. Not a personal byline, no sign-off, no "— MR, Amsterdam".

**No first person about process.** The note reports what was measured and what follows.
It does not narrate how the author felt while measuring it. "I went looking for X and
expected Y" is a Slack message. "Of 7,798 observations, none carries a disclosed value"
is a finding.

Acceptable: "This note audits...", "The claim here is bounded...", "TCI operates no
benchmark harness". Unacceptable: "I had written that line", "felt like good news for
about a minute", "the part I did not see coming".

**Never publish internal reasoning.** Anything about what to fix next, in what order, who
should do it, why publication was timed a certain way, or what the author intends to
change belongs in CHANGELOG.md, GOVERNANCE.md, or an issue. It does not belong in a
published note. A remediation plan in a research note reads as an internal document that
escaped.

If a finding implies work, state the finding and stop. The changelog records the remedy.

**State bounds, do not hedge.** "The index cannot decompose that spread" is a bound.
"This could potentially suggest" is a hedge. Keep the first, cut the second.

## The structure

**Essay, not a numbered report.** Headings are prose phrases that say what the section
establishes, with no numbers:

- Good: `## Rows that contradict themselves`, `## What the market actually discloses`
- Bad: `## 3. Finding 1: the index records a quality attribute it never observed`

Number a section only when the note genuinely needs cross-references between distant
parts, which is rare. If sections need numbers to be navigable, there are too many of
them.

The generator builds the on-page contents from the headings, so a heading that reads as a
sentence also produces a readable contents list.

**Shape.** Title, bold dek stating the finding, institutional byline, rule, then:
Abstract, the problem, the findings as prose sections, what it means, limitations,
reproducibility, sources. Findings flow into each other rather than sitting in numbered
bins.

**Length.** 2,000 to 3,500 words. The published notes sit in that band.

## Evidence

**Every figure comes from a query that the note publishes.** The reproducibility section
carries the SQL. A number that cannot be reproduced from `data/eucri.db` or a cited
external source does not go in.

**Never invent a number, a citation or a source.** Verify external claims before
publishing them. A claim that a third party's product behaves a certain way needs their
documentation cited, not an inference from a name.

**Publish negative results.** A finding that the data does not support the interesting
hypothesis is worth more than a hedge. Several published notes are exactly this.

**Attribute other people's ideas.** If the framing comes from someone else's work, say so
and say plainly that TCI did not do that work.

## House rules that still apply

From STYLE.md, unchanged: two decimals with the unit every time ($3.25/GPU-hr); vary
sentence length on purpose; no rule-of-three reflex; no "it's not just X, it's Y"; no
throat-clearing openers; specific uncertainty rather than generic; at most one or two em
dashes in a whole note, and the published notes manage zero.

Run the `humanizer` skill over the draft before publishing.

## Before publishing

- Headings carry no numbers and read as phrases.
- No first-person process narration, and no section proposing what to fix.
- Byline is The Compute Indices.
- Every figure appears in the reproducibility section or a cited source.
- Em dash count is zero or one.
- Any methodology consequence is recorded in CHANGELOG.md, and if it will change a print,
  a notice exists in `config/notices.yaml` before the change takes effect.
