# The EU term panel

Most GPU capacity is not rented by the hour. CoreWeave reported 98% of its first-half 2026
revenue under committed contracts
([10-Q for the period to 30 June 2026](https://www.sec.gov/Archives/edgar/data/0001769628/000176962826000366/crwv-20260630.htm)).
Nobody publishes what a 12-month block of GPUs costs in Europe. This panel is how TCI
intends to.

Each week, panellists say where a deal of a fixed specification would be agreed. TCI
combines the answers under the rules below and publishes one value per contract, or a count
of panellists where there are too few for a value. Nothing from the panel enters the daily
index. The output appears on the [term page](https://thecomputeindices.com/term.html) and in
`site/data/term/panel.json`.

## What is assessed

Three standard contracts, defined in [`config/term_panel.yaml`](config/term_panel.yaml):

| Cell | Contract |
|---|---|
| `EU-H100-12M-8` | 12 months of 8 to 63 H100 SXM GPUs at one EU/EEA site, whole 8-GPU nodes, starting within 30 days |
| `EU-H100-12M-64` | 12 months of 64 to 1,024 H100 SXM GPUs in one cluster at one EU/EEA site, non-blocking InfiniBand or RoCE between nodes, starting within 60 days |
| `EU-B200-12M-64` | The same for B200 |

All three are billed monthly, ex-VAT, and price compute only: storage and egress are left
out. Prices are per GPU-hour.

## How to assess

Once a week, a range in USD or EUR per GPU-hour for each cell you can judge, and what the
range rests on:

| Basis | Meaning |
|---|---|
| `transacted` | Deals you saw agreed this week. Give the number in `deals_seen` |
| `firm_quote` | Offers that would have been signed as stated |
| `indicative` | Quotes given without commitment |
| `judgement` | Your view where nothing traded |

Leave a cell out rather than guess. A range wider than 1.5 times its low end is refused,
because at that width it is not an assessment. The format is
[`contrib/panel-template.csv`](contrib/panel-template.csv), and the same check TCI runs can
be run before sending:

    python -m tci.run panel validate --file assessments.csv

## The window

Each assessment names an ISO week, such as `2026-W42`. It counts if TCI receives it between
Monday 00:00 UTC and Friday 12:00 UTC of that week. A later one is stored and not counted,
and so is a later correction: the assessment it would have replaced stands. No week is
aggregated before its window closes, so nobody can shape an input to its value.

## What is published

- **A value needs three panellists.** With three or four, it is the mean of their
  mid-points. From five, the highest and lowest mid-points are dropped and the rest are
  averaged, and the 25th and 75th percentiles of the mid-points are published beside it.
  It is never a median of three, which would be one panellist's mid-point to the cent.
- **Sellers must be fewer than half of a cell's panellists.** A cell where they are half
  or more publishes no value that week. Competing sellers pooling current prices is the
  arrangement competition law watches most closely. Brokers, marketplaces and buyers are
  the panel this is built for.
- **No panellist and no single assessment is ever published.** What leaves the private
  store is the value, the number of panellists, how many assessments rested on each basis,
  and how many came from each kind of panellist.
- **A cell below the rules publishes a count, never a value.** The page shows how many
  panellists assessed it and how many are needed.

Panel values are judgements and quotes from named market participants. Unlike everything
else TCI publishes, they cannot be recomputed from public data. They can be audited only
against the private store.

## Who can be a panellist

A firm that has brokered, sold, operated a marketplace for, or bought GPU capacity in the
EU/EEA in the past three months. Each panellist signs a short agreement, declares its
conflicts in writing, and is recorded under a pseudonym; the key from pseudonym to firm is
kept offline. The register is append-only, so joining, suspension and leaving all stay on
record.

## What a panellist agrees to

1. Assess honestly, from your own view of the market, and only cells you can judge.
2. Send assessments only to TCI, and not discuss them with other panellists or sellers.
3. Not trade any instrument whose payout references a panel value, and declare any
   position that does.
4. Keep the records behind each assessment for five years, and show them to TCI's
   reviewer on request.
5. Correct a mistake by sending a correction that names the assessment it replaces.
   Corrections, like everything else, are kept.

A panellist that breaks these is suspended. While suspended, its assessments count in no
week, past weeks included, and a past value that changes as a result is republished with a
notice.

## What panellists receive

The full history of every cell, with quartiles where they exist, at the same time as
everyone else sees the headline values. No panellist sees another's assessment, and none
receives anything before publication.

## Status

The panel is open and has no panellists yet, so no cell has a value. No assessment is
accepted until the panellist agreement and these thresholds have had a competition-law
review.

Questions and applications: [contact](https://thecomputeindices.com/contact.html).
