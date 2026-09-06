# Compute prices barely move. Where you buy moves everything.

**Five of nine European sellers never changed their H100 price once in fifty days, the same-hour spread across sellers was 8.56x, and the hyperscaler rate cards are region-flat to four decimal places, which is the argument for regional benchmarks and not the argument most people make for them.**

TCI Research Note 2026-03 · 6 September 2026 · Mark Rusch

---

## Abstract

Between 18 July and 5 September 2026 the index collected 7,386 GPU price observations
from nine sellers across fourteen EU/EEA countries. Over that window, five of the nine
sellers offering an H100 SXM in Europe posted **exactly one price and never changed it**.
A sixth posted three. Ninety-five per cent of every quote the index has ever seen is a
published rate card rather than an executable offer.

Almost none of the variation a European buyer faces is temporal. On 5 September 2026 the
same GPU-hour, on the same continent, in the same hour, was quoted at $1.87 and at
$15.98. That is a spread of **8.56x**. Per seller the picture is starker: AWS's European
H100 rate moved 0.00% across 35 collection days, and Amazon's European price sat at
exactly **1.070000×** its US price on every one of those days, to four decimal places. The
one seller whose regional spread actually moves is the marketplace, where the EU-versus-US
basis averaged **−15.39%** and ranged from −36.38% to +8.21% over nine overlapping days.

That combination is the case for a regional benchmark. It is not the case usually made.
The usual case, that European compute costs more so you should measure Europe, is close to
false at the vendor level, and I can show it is false. What survives is that a region is a
**different population of sellers** rather than a geographic adjustment to a global one,
and the population is what sets the price a European buyer can actually pay.

---

## 1. Why this note exists

CME Group lists Silicon Data H100 and B200 Rental Index Futures on NYMEX on 5 October
2026, cash-settled on a monthly average of a daily $/GPU-hour index.[^1] Both are global
in scope and USD-denominated. A European buyer who hedges an EU compute book with them
takes a basis nobody currently publishes, and this note is an attempt to work out what
that basis is made of.

I did not know the answer when I started. My prior was the intuitive one, the one you will
hear at any European cloud conference: European compute is dearer because European inputs
are dearer, so a regional index would mostly capture a level shift. The data says
something else, and the something else is more interesting.

---

## 2. Data

Everything below comes from `data/eucri.db` in the public repository. The window runs
2026-07-18 to 2026-09-05: **50 index sessions, 35 of which have collected observations**
(the daily job did not run every day in the first three weeks), 7,386 observations, nine
sellers, nine GPU models, fourteen EU/EEA countries plus the United States.

Of the 50 sessions the headline published on 16 and gapped on 34, which is **32.0%
coverage**. That is bad. It is published rather than hidden, and §12 says what it does to
the confidence you should place in any of this. Every query is in §13.

---

## 3. Finding 1: five of nine sellers never changed their price

The H100 SXM, EU/EEA only, over the whole history:

| Seller | Tier | Days seen | Quotes | Distinct prices | Min | Max | Total move |
|---|---|---:|---:|---:|---:|---:|---:|
| vast.ai | executable | 21 | 44 | 30 | $1.87 | $3.54 | 89.5% |
| seeweb | list | 35 | 35 | **1** | $2.16 | $2.16 | 0.0% |
| runpod | executable | 35 | 35 | 3 | $2.99 | $3.49 | 16.7% |
| datacrunch | list | 35 | 35 | **1** | $3.25 | $3.25 | 0.0% |
| scaleway | list | 16 | 16 | **1** | $3.31 | $3.31 | 0.0% |
| nebius | list | 35 | 35 | **1** | $3.85 | $3.85 | 0.0% |
| gcp | list | 35 | 775 | 11 | $5.44 | $13.78 | 153.2% |
| aws | list | 35 | 35 | **1** | $7.36 | $7.36 | 0.0% |
| azure | list | 21 | 441 | 4 | $14.38 | $16.53 | 15.0% |

Seeweb, DataCrunch, Nebius, AWS and Scaleway published one number each and left it there.
Not "roughly stable". One distinct value to four decimal places, every day they appeared.
RunPod moved twice in 35 days. Only vast.ai behaves like something being priced
continuously, with 30 distinct prices across 21 days.

The GCP and Azure "moves" in that table are not moves. They are the spread across regions
and SKUs collapsing into a min and a max, and §6 takes GCP's apart.

This is what a rate-card market looks like. 95.29% of all 7,386 observations the index has
ever collected carry `tier='list'`; 4.71% are executable offers with a demonstrated node
size, and they come from two venues. For most of its constituents, an index built on this
population is reading a published policy rather than a transaction.

---

## 4. Finding 2: the variation is cross-sectional, not temporal

On 5 September 2026, H100 SXM, EU/EEA, same collection run:

| Seller | Tier | Country | Price |
|---|---|---|---:|
| vast.ai | executable | CZ | $1.87 |
| seeweb | list | IT | $2.16 |
| datacrunch | list | FI | $3.25 |
| runpod | executable | RO | $3.49 |
| nebius | list | FI | $3.85 |
| gcp | list | DE | $5.44 |
| aws | list | SE | $7.36 |
| azure | list | NL | $15.98 |

**8.56x**, one continent, one hour, one GPU. Against that, the largest move over time any
European seller made in seven weeks was vast.ai's: 60.65% within a single region across
days, 89.5% top to bottom once its regions are pooled. The median seller's was zero.

So if you are a European buyer, "what will an H100-hour cost me next month" is dominated
almost entirely by "which of these eight counterparties will I be able to transact with".
That is a composition question, and it is a regional one.

---

## 5. Finding 3: the vendor's regional price is administered, not discovered

Here is the number that changed how I think about this.

AWS quoted its European H100 (`eu-north-1`, Stockholm) at **$7.3616** and its US H100
(`us-east-1`) at **$6.8800**, on every one of 35 collection days. The ratio is
**1.070000**. Not approximately seven per cent. 7.0000%, unchanged for seven weeks. GCP's
cheapest European region sat a similarly immobile **1.10%** above its cheapest US region,
also on all 35 days.

Azure is starker. On 5 September its H100 range in `polandcentral`, `spaincentral`,
`swedencentral` and `westeurope` was identical: $14.379 to $15.977 in all four. Four
distinct H100 prices appear anywhere in Azure's European data across 21 collection days.

Now put that beside the input costs. The IEA reports that in 2025 EU electricity prices for
energy-intensive industries ran at **roughly double** US levels and more than 50% above
China and India, with EU wholesale prices around **$95/MWh**, the highest of any market it
tracks.[^2] Within Europe the markets are not one market either. Finland and Sweden led the
continent in negatively-priced hours in 2024, at 8% and 7% of all hours (both fell sharply
in 2025, by around 40% and 30% respectively), while Poland's day-ahead average in May 2026
was **€101.8/MWh**.[^3]

Azure charges the same for an H100-hour in Warsaw as in Stockholm, to a tenth of a cent,
across a power-price gap that large. Whatever that number is, it is not a price formed by
European supply and European demand. It is a global list price with a regional multiplier
bolted on, and the multiplier is a policy decision reviewed on some internal calendar
nobody outside the vendor can see.

Which kills the naive argument for a regional index. If the regional component were AWS's
7.00%, you would not need a regional benchmark at all. You would need a global index and a
one-column spread table, and you could update the table once a quarter.

---

## 6. Finding 4: the biggest "regional" spread in the data is an artefact

GCP's European regions spanned 2.34x on 5 September, from $5.44 in `europe-west4` to
$12.74 in `europe-west9`. Read carelessly, that is a spectacular intra-European price
signal, and it is the kind of number that ends up in a slide deck.

It is not a price signal. GCP's *US* regions spanned only 1.10x on the same day, a 10%
span, and the underlying distribution is bimodal in a way that ignores geography entirely.
One cluster sits at $5.38 to $6.10 and holds `us-east4`, `europe-west4`, `europe-west1`,
`europe-west3`, `us-central1`, `asia-south1` and `australia-southeast1`. The other sits at
$12.09 to $15.83 and holds `northamerica-northeast2`, `europe-north1`, `europe-west2`,
`europe-west9`, `asia-east1` and `asia-northeast1`. A split that puts Belgium and Iowa on
one side and Finland and Toronto on the other is not measuring geography. It is two
machine families in one catalog feed.

I am publishing this because it is the most seductive wrong answer available in this
dataset, and because the same trap sits in every public cloud rate card a compute index
might scrape. A "regional spread" computed off a catalog without controlling for the SKU
will produce a large, stable, entirely fictional number.

---

## 7. Finding 5: the one live regional basis is on the competitive venue

Vast.ai stores offers globally and the index filters them to the EU at calculation time.
That makes it the only source where an EU leg and a non-EU leg can be compared at the same
venue, the same tier, the same day and the same SKU. It is also the standard the
methodology already requires before any normalisation factor may enter the calculation
path.

On the nine days where both legs exist, the EU offer was on average **15.39% cheaper** than
the US offer, with a range from **−36.38% to +8.21%**.

Two things about that. The sign is the opposite of the prior I started with: cheap European
marketplace inventory, rather than a European premium. And the dispersion is 44 percentage
points across nine days, against AWS's 7.00% that never moved at all.

Nine days is nothing and I would not trade on it. But it is the only regional basis in the
dataset that carries information, and it exists only because one venue lets the same
population be split by geography. A global index over a global population averages it away
by construction.

---

## 8. So what moves a compute price?

Ranked by how much variance each source actually contributed over these seven weeks, the
answer starts with who is quoting. That is the 8.56x, and every published print's level is
set by whichever constituent happens to sit at the 50% weight crossing. Scaleway delisted
its H100 SXM from the public API on 1 September and took the panel from five names to four,
which is exactly why the index went dark for four sessions. It came back on 5 September at
$3.25 because a collector fix restored RunPod, not because anything repriced.

Second, and a long way behind, the marketplace tier. vast.ai's daily minimum ranged 87.4%
(σ = $0.384 across 21 days). Everything else in the panel is a step function or a constant.

Currency contributes a little. EUR/USD moved from 1.1435 to 1.1622 over the window, 2.46%,
and it only touches the EUR companion series.

Geography contributes essentially nothing, at least not within a vendor. Five of nine
European sellers operate from a single region, and the ones with several charge the same in
all of them or differ by a constant.

Notice what is missing from that list. Demand. Nothing in this dataset lets me observe it:
utilisation, queue depth and booked hours are not public, and an index built on rate cards
cannot see any of them. What I can measure is what sellers *say* and who is *there*, and
the second turns out to matter more than the first.

---

## 9. Why regional benchmarks are the future

Not because a European GPU-hour costs more. Sometimes it costs less, and where it costs
more at a hyperscaler the premium is an administered constant a spreadsheet could handle.

The case is that **a region is a different population, and the population is the price.**

A European buyer's realistic choice set on 5 September ran from $1.87 to $15.98. Both ends
of that range are region-specific facts: the cheapest offer was Czech marketplace
inventory, the dearest an Azure European region. A global index prices a basket containing
sellers a European buyer cannot lawfully or practically use, and omits the long tail of
European neoclouds (Seeweb at $2.16, DataCrunch at $3.25) that dominate the bottom of the
actual choice set. The number it produces is correct about nothing in particular.

The forces acting on those populations are pushing them apart rather than together, and the
biggest is input costs. EU energy-intensive industrial power at roughly twice US levels is
not a cyclical gap.[^2] Today the vendors absorb it into a global list price, and §5 shows
how completely they absorb it. That can persist while GPU supply is the binding constraint
and power is a rounding error against the cost of the silicon. It gets much harder to
sustain as fleets age, depreciation runs off, and electricity becomes the dominant line
item in a GPU-hour. I do not know when that crossover happens. I do think it is the single
thing most likely to make this note look either prescient or silly in three years.

European supply is also being deliberately and publicly localised. The EuroHPC Joint
Undertaking opened its AI Gigafactories call on 30 July 2026, with submissions closing
12 November 2026, for up to seven facilities of roughly 100,000 advanced processors each
and an expected mobilisation of more than €20 billion in private investment.[^4] Whatever
one thinks of industrial policy, that is a large, dated, region-specific change to who will
be selling European compute in 2028. It changes the population, which is the thing this
note finds to be doing the work.

Then there is regulation, which acts on the choice set rather than the price. Data-residency
and sovereignty requirements do not make a European GPU-hour more expensive so much as they
make the US ones *unavailable*. A benchmark that averages over sellers a buyer is not
permitted to use is not measuring that buyer's market.

Put those together and the design conclusion is specific. The useful regional product is
not a European level competing with a global level. It is the **basis**: EU population
against global population, like for like on tier and unit, published as its own series with
its own gap discipline. Nobody publishes that today. Both listed contracts are global, and
the European buyer holding them is carrying an unmeasured spread.

---

## 10. What would falsify this

If the hyperscalers begin repricing European regions independently over the next six
months, with AWS's 1.070000 ratio moving more than once or Azure's four EU regions
separating, then the regional signal *is* inside the vendor rate card, a global index plus
a spread table captures it, and the population argument weakens a lot.

If the EU-versus-US marketplace basis converges toward zero and stays there once the sample
is 90 days rather than nine, then the live basis I am pointing at was noise from a thin
panel, and I would say so in a follow-up note.

And if European neocloud pricing turns out to track US pricing one-for-one with a lag, then
the populations are already integrated and the regional cut adds nothing but sampling
error.

---

## 11. Behind the print

The Scaleway delisting is the cleanest illustration in this note of what the note argues.
On 1 September the H100 SXM simply stopped appearing in Scaleway's public Instance API. No
price change, no announcement. The SKU left the surface. The panel fell to four names
against a five-provider gate, and the index published nothing for four consecutive sessions.

What restored it was not the market. Probing RunPod's stock at 8, 4 and 2 GPUs instead of 8
alone turned up a real 2-GPU pod that had been in hand all along and was being thrown away
because it could not demonstrate an 8-GPU node. That fix went in on 4 September. The run on
5 September printed $3.25 from five providers.

So a European compute benchmark went dark for four days because one French vendor edited a
JSON endpoint, and came back because I changed a GraphQL query. Neither event had anything
to do with the price of compute in Europe. Both moved the published number. If you want a
single argument for why the composition of a regional panel deserves more attention than
its level, it is that paragraph.

I would also rather say plainly that the power-price argument in §9 rests entirely on
external sources. The repository has an ENTSO-E collector for day-ahead prices and it has
collected **zero rows**. The overlay table is empty. Until that runs I cannot test the
energy hypothesis on my own data, and I have not pretended otherwise above.

---

## 12. Limitations

Thirty-five collection days and 32.0% publication coverage. This is a young index with a
thin panel, and every number here should be read as a description of this dataset rather
than an estimate of a population parameter.

The EU-versus-US basis in §7 rests on nine overlapping days at one venue. It is the weakest
quantitative claim in the note and the one I would most expect to move.

Two executable sources out of nine sellers means the "market" being described is mostly a
set of published intentions. Rate cards are real prices in the sense that you can pay them.
They are not real prices in the sense that they clear anything.

The GPU models are not interchangeable and I have not normalised across them anywhere here.
Every figure is H100 SXM 80GB unless stated.

Nothing in this note is a forecast, and none of it is investment advice.

---

## 13. Reproducibility

Against the committed database:

```sql
-- Table 1: per-seller price behaviour, EU/EEA H100 SXM
SELECT provider, tier, COUNT(DISTINCT substr(ts_utc,1,10)) days, COUNT(*) quotes,
       COUNT(DISTINCT ROUND(price_usd_per_gpu_hr,4)) distinct_prices,
       MIN(price_usd_per_gpu_hr), MAX(price_usd_per_gpu_hr)
FROM observations
WHERE gpu_model='H100_SXM' AND country IS NOT NULL AND country<>'US'
GROUP BY provider ORDER BY 6;

-- §5: the AWS regional constant
SELECT DISTINCT region, price_usd_per_gpu_hr FROM observations
WHERE provider='aws' AND gpu_model='H100_SXM' AND region IN ('eu-north-1','us-east-1');

-- §6: is GCP's regional spread geographic?
SELECT region, country, price_usd_per_gpu_hr FROM observations
WHERE provider='gcp' AND gpu_model='H100_SXM' AND substr(ts_utc,1,10)='2026-09-05'
GROUP BY region ORDER BY price_usd_per_gpu_hr;

-- §7: same venue, same day, EU vs US
SELECT substr(ts_utc,1,10) d,
       MIN(CASE WHEN country='US' THEN price_usd_per_gpu_hr END) us,
       MIN(CASE WHEN country<>'US' THEN price_usd_per_gpu_hr END) eu
FROM observations WHERE provider='vast.ai' AND gpu_model='H100_SXM'
  AND country IS NOT NULL GROUP BY d HAVING us IS NOT NULL AND eu IS NOT NULL;
```

The constituent set behind any print, including the rejected candidates and the reason for
each:

```
python -m tci.run constituents --date 2026-09-05 --series EU-CRI-H100
```

---

## Sources

[^1]: CME Group, *CME Group and Silicon Data to Launch Compute Futures on October 5*, 11 August 2026, and Special Executive Report SER-9785, *Initial Listing of Two (2) Compute Futures Contracts*. https://www.cmegroup.com/media-room/press-releases/2026/8/11/cme_group_and_silicondatatolaunchcomputefuturesonoctober5tounloc.html · https://www.cmegroup.com/notices/ser/2026/08/ser-9785.html

[^2]: International Energy Agency, *Electricity 2026 — Prices*. EU energy-intensive industry prices roughly double US levels in 2025; EU wholesale ~$95/MWh, the highest of the markets analysed. https://www.iea.org/reports/electricity-2026/prices

[^3]: Negatively-priced hours by market, and the 2025 decline, from IEA *Electricity 2026 — Prices* (Finland 8% and Sweden 7% of hours in 2024; down ~40% and ~30% in 2025). Poland's May 2026 day-ahead average of €101.8/MWh is Ember data as reported June 2026. https://www.iea.org/reports/electricity-2026/prices · https://www.indexbox.io/blog/european-wholesale-electricity-prices-rise-in-may-2026-amid-gas-volatility-and-lower-wind-output/

[^4]: EuroHPC Joint Undertaking, *The EuroHPC Joint Undertaking launches the AI Gigafactories Call*, 30 July 2026; submission deadline 12 November 2026. https://www.eurohpc-ju.europa.eu/eurohpc-joint-undertaking-launches-ai-gigafactories-call-2026-07-30_en

Prior notes referenced: 2026-01, *When an index measures its own sampling*, on the
composition-versus-price decomposition and the bimodal panel; 2026-02, *What makes a
compute benchmark hedgeable*, on why a monthly average and a dense panel are preconditions
for settlement.

— MR, Amsterdam
