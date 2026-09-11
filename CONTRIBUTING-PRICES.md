# Contributing term prices

Public rate cards say very little about what compute costs under a commitment. Across
every public source TCI collects, as of September 2026, no committed tenor of any GPU is
published by three or more sellers. The term structure a lender underwrites against,
or a buyer negotiates against, lives in contracts and firm quotes. This document explains
how to contribute those prices to TCI, what happens to them, and what is published.

Nothing contributed enters the daily index. Contributed prices feed a separate table of
term prices by GPU, tenor and region, published only as aggregates.

## Who can contribute

Sellers of GPU capacity (neoclouds, data-centre operators, marketplaces), buyers under
contract, and brokers. Each contributor is identified to TCI by name and recorded under a
pseudonym. The key from pseudonym to name is kept offline and is not stored with the
prices.

## What to send

One CSV per submission, in the format of [`contrib/template.csv`](contrib/template.csv):

| Column | Meaning |
|---|---|
| `as_of` | The date the price applies to (YYYY-MM-DD) |
| `role` | `seller`, `buyer` or `broker` |
| `kind` | `executed` (a signed contract), `firm_quote` (an offer you would sign) or `indicative` |
| `gpu_model` | `H100_SXM`, `H100_PCIE`, `H200_SXM`, `B200_SXM`, `B300_SXM`, `GB200`, `GB300`, `A100_SXM`, `MI300X` |
| `gpus` | GPUs covered by the price |
| `tenor_months` | 1, 3, 6, 12, 18, 24, 36, 48 or 60 |
| `price_per_gpu_hour` | Per GPU-hour, ex-VAT. A monthly or total contract value must be converted first |
| `currency` | `USD` or `EUR` |
| `region` | `EU_EEA`, `UK`, `CH`, `US` or `OTHER`: where the capacity is delivered from |
| `start_date` | Optional. When the term starts |
| `payment` | Optional. `upfront`, `monthly`, or free text |
| `notes` | Optional. Anything that makes the price not comparable (bundled storage, SLA, interconnect) |

The file is validated before anything is stored, and one invalid row rejects the whole
file with the line and field named. You can run the same check yourself:

    python -m tci.run contrib validate --file your-prices.csv

## What happens to it

- **Stored privately.** Contributions are held in a database outside the public
  repository. The software refuses to create that database inside the repository, so a
  contributed price cannot be committed by mistake.
- **Never edited.** The store is append-only. A correction is a one-row file that names
  the row it replaces; both are kept, and only the contributor who sent a row can correct
  it.
- **Aggregated before anything is published.** A cell (GPU, tenor, region, currency) is
  published only when all three hold:
  1. at least **three** contributors have prices in it;
  2. no single contributor holds more than **half** of the cell's GPU volume;
  3. the cell is not made up entirely of indicative quotes.

  A cell that fails any test is published as a count of contributors and quotes, with the
  reason, and no price.
- **One price per contributor.** Your quotes in a cell are reduced to their median before
  they are pooled with anyone else's, so splitting one deal into many small quotes does
  not add weight.
- **What is published for a cell:** the median across contributors, the number of
  contributors and quotes, and how many were executed, firm or indicative. The 25th and
  75th percentiles are added only when the cell has at least **five** contributors,
  because beside a median of three prices they would reveal all three. Never a
  contributor, a single quote, or a date narrower than the aggregation window.

## What contributed cells are not

Everything else TCI publishes can be recomputed by anyone from the public repository.
Contributed cells cannot, by design. Each published cell says so. An independent reviewer
can check them against the private store under a confidentiality agreement.

They are not an index and not a settlement price. They are research aggregates, published
under the same terms as the rest of TCI's data ([DATA-TERMS.md](DATA-TERMS.md)).

## Conduct

- Submit only prices you are entitled to disclose. If a contract has a confidentiality
  clause, check it first; aggregation does not change what you agreed to.
- Submit what was actually quoted or signed. Do not submit a price you would like the
  market to believe.
- A contributor who also trades on, or sells capacity into, the markets these aggregates
  describe must say so when first contributing. The dominance test above exists so that no
  single party can set a published cell.
- A pattern of submissions that looks designed to move a cell (for example, many small
  quotes clustered at one end of a thin cell) is excluded, and the exclusion is recorded
  with the reason.

## How to start

Email [rusch.mh@gmail.com](mailto:rusch.mh@gmail.com) with the subject "Term prices". You
will receive your pseudonym and a confirmation that your first file validated.

*The thresholds above are TCI's own and have not yet been reviewed by competition counsel.
Contributors who compete with each other should take their own advice before sharing
current prices. A written contributor agreement will accompany the first contribution.*
