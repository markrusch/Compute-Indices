# Nine sellers cannot price a grade

**The same-hour spread across European H100 sellers was 7.40x on 7 September, and no regression on that panel can say how much of it is quality. One venue publishes a performance score on every offer, and a design built on it is identified in principle; the book it would be estimated on held 7 datacenter-verified H100 offers from 3 hosts on 12 September, and the score moved 30.50% overnight on one of them.**

TCI Research Note 2026-05 · 14 September 2026 · The Compute Indices

---

## Abstract

Research Note 2026-04 established that the index prices a part number and cannot decompose
its own spread into price and grade. This note establishes which parts of that
decomposition are identifiable from data that exists, and how far the stored record is from
supporting an estimate.

The obvious design fails. A hedonic regression of price on seller-level quality, run across
the European panel, has nine clusters. A seller fixed effect is collinear with any quality
measurement taken once per seller, so a specification that controls for who is selling
cannot also price what they deliver. Five of the nine sellers posted exactly one price
between 18 July and 5 September, so additional sessions add no variation to estimate from.
Over the same window 95.29% of stored observations were rate cards, which makes the object
being differentiated a published pricing rule rather than a market's valuation.

One design survives in principle. vast.ai returns a measured deep-learning performance
score, GPU memory bandwidth, PCIe bandwidth, a power ceiling and a temperature ceiling on
every offer. Within one part number on one session, both price and the score vary across
machines, and a regression with part number and session held fixed identifies a price
gradient with respect to that score without TCI running a benchmark.

The stored record does not yet support it. vast.ai stored no offers from 8 to 10 September.
On 12 and 13 September the index holds 7 and 4 datacenter-verified H100 SXM offers, from 3
and 2 hosts. Hosts set prices, so the clusters that matter number three. One of the two
offers present on both days changed its score by 30.50% overnight, which means the score
carries measurement error of a size that biases any gradient estimated on it toward zero.
And of the four axes of a compute assay, one is reachable this way. The venue's FLOPS field
is a datasheet figure, identical on every H100 machine, and the other two axes are not
reachable from any public source.

---

## The spread the index cannot decompose

On 7 September the index observed eight European H100 SXM sellers in a single collection
run, from $2.16/GPU-hr at seeweb to $15.98/GPU-hr at Azure. The ratio is 7.40x. On 5
September the same measurement gave 8.56x, from $1.87/GPU-hr at vast.ai to $15.98/GPU-hr.

Note 2026-03 attributed spreads of that size to the seller population: who is quoting,
rather than where they are. Note 2026-04 showed the account was incomplete, because it
assumed the good was constant across the panel, and across the 7,798 payloads stored up to
7 September none described a fabric, a power envelope or a thermal limit.

Both notes stop at the same place. Neither says how large the quality component of the
spread is. The open question is whether the difference between an H100-hour at $15.98 and
one at $2.16 is worth $13.82/GPU-hr, and what a buyer is paying for when it is not.

That question has a standard technique behind it, older than the market it would be
applied to.

## A price is a bundle, and the bundle has a known algebra

Hedonic decomposition treats an observed price as the sum of implicit prices of the
attributes bundled into the good, following Rosen's characterisation of implicit markets.
For a price `p` on offer `i` from seller `s` in session `t`:

```
ln p_ist = a + B'x_ist + G'q_ist + d_s + u_t + e_ist
```

where `x` holds the contractual attributes the sources already disclose (GPU count, tenor,
tier, country), `q` holds the assay vector, `d_s` is a seller effect and `u_t` a session
effect. `G` is the object of interest: the vector of implicit prices of delivered quality.

The US Bureau of Labor Statistics has estimated this specification on cloud instances.
Sawyer and O'Bryan applied time-dummy hedonic models to cloud computing services in the
Producer Price Index, with memory, storage, an SSD indicator
and microprocessor characteristics including thermal design power, base frequency and cache,
selecting among models by repeated k-fold cross-validation. Memory carried a positive and
significant coefficient across nearly all of their specifications.

What limited that study is what limits this panel. They report that AWS published a
processor performance measure, the EC2 Compute Unit, that neither Azure nor Google Cloud had
an equivalent, and that third-party benchmarks covered too few of the processors in use to
support a hedonic model. They name latency as a quality dimension they had no way to
quantify. Substitute fabric for latency and the passage transfers to GPU compute unchanged.

Estimating `G` is routine. What it requires is a `q` that exists, varies, and is measured
with less error than the variation it is meant to explain.

## Why nine sellers cannot answer it

Three properties of the European panel defeat the cross-seller specification, and they
defeat it jointly.

The fixed effect and the grade are the same column. An assay run once per seller produces a
`q_s` that does not vary within seller, and neither does `d_s`. The two are perfectly
collinear, so a regression can have the seller control or the quality coefficient and never
both. Dropping `d_s` recovers `G` and loads every unmeasured seller attribute onto it:
support, service levels, compliance posture, egress allowance, contractual flexibility,
counterparty credit. Those attributes are plausibly correlated with delivered performance,
so the coefficient recovered without `d_s` measures reputation under the label of
throughput. Oster's procedure for coefficient stability under unobservable selection states
how large that contamination could be, and it produces a bound rather than an estimate.

The cross-section is nine and the time series is empty. Between 18 July and 5 September the
index stored 7,386 observations from nine providers. Cluster-robust inference with nine
clusters is unreliable in a way that no sample length repairs, because the effective degrees
of freedom come from the sellers and not from the sessions. At eight degrees of freedom the
two-sided 5% critical value is 2.31 and 80% power needs a further 0.89, so an effect smaller
than 3.20 standard errors will usually go undetected. Over that window five of the nine
European H100 sellers (AWS, DataCrunch, Nebius, Scaleway, seeweb) posted exactly one price
and RunPod posted three. A panel whose price series are constants contributes no
within-seller variation to identify anything from.

The prices are mostly not prices. Rate cards were 95.29% of observations in that window.
For sellers who never repriced, a hedonic coefficient estimated across their quotes describes
the output of an internal pricing decision. It is a real object, and it is a finding about
how vendors set list prices rather than a market's valuation of fabric.

The panel is widening. The store now holds EU/EEA H100 SXM rows from 14 providers, and
notice 2026-N2 admits several of them to the index from 22 September. Fourteen is still an
order of magnitude short of what four separately identified coefficients need, when each
axis also requires sellers whose variation on it is uncorrelated with the others. A four-way
decomposition across European sellers is not available to TCI, and it is not available to
anyone else working from the same seller population.

The way past that constraint is a venue where the unit of observation is a machine.

## The venue that publishes its own score

vast.ai is a marketplace of independently operated hosts. The correction to Note 2026-04 of
8 September records what it returns per offer, and the collector has stored those fields
since the first run after that date. Read against the stored payloads, they divide into
three kinds.

`dlperf`, the venue's deep-learning performance score, varies between machines carrying the
same part number. It is the one field that behaves like a measurement.

`total_flops` does not vary. On every datacenter-verified H100 SXM offer stored on 12 and 13
September it is exactly 53.53 per GPU, a datasheet figure multiplied by GPU count. It carries
no information about the machine.

`gpu_max_power` varies, from 525 W to 700 W across the same offers, and `disk_bw` and
`nw_disk_avg_bw` report storage and network bandwidth per host. These are configuration and
idle-host measurements. A disk bandwidth figure is not checkpoint I/O under load, and the
correction to Note 2026-04, which counted three of the four assay axes as published by this
venue, is narrowed here: one axis, sustained throughput, is reachable through `dlperf`.
Straggler behaviour is at most inferable as the tail of the same machine-level distribution.
Collective communication and checkpoint I/O under load are not reachable.

Write the specification at offer level with a fixed effect on part number interacted with
session:

```
ln p_ist = G'q_i + B'x_i + f_(model,t) + e_ist
```

The `f` term holds the GPU model and the collection session jointly constant, so `G` is
identified off differences between machines carrying the same part number on the same
morning. Venue effects, market-wide moves, currency and tenor fall out. What remains is the
variation a datasheet cannot express: silicon, thermal headroom, host configuration, bus
topology.

## What the stored book contains

The design needs many machines per part number per session, from many hosts, with a score
that is stable on the same machine. The record meets none of the three yet.

| Datacenter-verified H100 SXM, worldwide | 12 September | 13 September |
|---|---:|---:|
| Offers stored | 7 | 4 |
| Distinct machines | 5 | 4 |
| Distinct hosts | 3 | 2 |
| Offers in the ascending book, all hosts | 19 | 18 |
| Price range, $/GPU-hr | 2.27 to 3.10 | 2.93 to 3.46 |
| `dlperf` per GPU, range | 223.48 to 338.49 | 297.51 to 343.21 |

vast.ai stored no offers on 8, 9 or 10 September and two RTX 3060 offers on 11 September,
so these two sessions are the whole of the usable record. The books were read in full: both
came back below the collector's fetch limit of 50, so the ascending read was not truncated
on price. What the stored book lost, it lost to scope. The collector admits only datacenter-verified hosts,
and 12 of the 19 offers on 12 September were outside that scope and were not stored.

Hosts set prices. Host 21357 listed three offers at $2.27/GPU-hr on 12 September with
`dlperf` per GPU of 338.49, 296.00 and 280.00. Within that host, price did not respond to
the score at all, and every movement of price against score in the table comes from the
differences between hosts. The observations are machines and the clusters are hosts, three
on one day and two on the next. The nine-cluster problem returns at three.

The score moves on the same machine. Offer 48704108, a single H100, recorded a `dlperf` of
258.98 on 12 September and 337.98 on 13 September, a change of 30.50%, while its price rose
from $3.10/GPU-hr to $3.46/GPU-hr. Offer 37720465, two GPUs, recorded 596.61 and 595.01, a
change of 0.27%, at an unchanged $2.93/GPU-hr that it has carried on eleven sessions since
6 August. The cross-machine range on 13 September was 15.36%. On this evidence the error in
a single reading can be as large as the spread between machines.

That has a known consequence. Classical measurement error in a regressor attenuates its
coefficient toward zero, by the ratio of true variance to observed variance. A gradient
estimated on single daily readings of `dlperf` would be biased toward the finding that the
marketplace does not price performance, whatever the truth. Repeated readings of the same
offer across sessions estimate that reliability ratio directly, which is the reason the
panel has to be read as repeated measurements of machines and not as independent daily
cross-sections.

## The conditions an estimate would have to meet

The order book must be sampled without selection on price. The API clamps each response, and
an ascending read alone returns the cheapest offers and truncates the sample on the
dependent variable. The collector re-reads a full book in descending order. Current H100
books are small enough that neither read is full, and the condition has to be checked per
session rather than assumed.

The score is the venue's own. `dlperf` is a vendor-defined composite, as AWS's ECU was in
the BLS study, and a gradient estimated against it prices the score rather than the
underlying performance. That limitation belongs on the face of any published figure, and it
is the gap an independent assay closes.

The gradient belongs to the marketplace's buyers. Rosen's second stage applies: an estimated
hedonic gradient is the envelope of heterogeneous bid functions, not any one buyer's
valuation. Marketplace buyers mostly rent single nodes, so a gradient estimated there says
what price-sensitive single-node demand pays for node quality. Carrying it across to explain
an Azure rate card would assume the two populations value the same attributes.

What a gradient would support is a bound. Given the dispersion of reliably measured
performance within a part number and the price gradient with respect to it, the maximum
share of a 7.40x cross-venue spread attributable to measured node performance follows
arithmetically. If that ceiling is low, node-level quality cannot account for the spread and
the residual belongs to service, compliance, contract and inertia.

## What buyers reveal that asking prices do not

Every figure above is an ask. A host's listed price is an offer to sell, and a regression
across offers describes what sellers request, not what anyone paid.

The same snapshots contain a second signal. An offer present in one session and absent in
the next has been rented, withdrawn or taken offline, and the time an offer survives in the
book is observable at daily granularity. Of the 7 H100 SXM offers stored on 12 September, 2
were present on 13 September. A discrete-time hazard of disappearance, complementary log-log
on session intervals, with price and measured performance as covariates and part number and
session held fixed, estimates how much cheaper a slower machine has to be to leave the book
at the same rate as a faster one.

Its weakness is a competing risk. Disappearance conflates renting with delisting, and the
two separate only if withdrawal is independent of price and measured quality. That is
doubtful at the extremes, where an unrentable machine is exactly the one a host takes down.
Stated as an upper bound on time to rent, the estimate survives the objection. Stated as a
demand curve, it does not. At two to seven offers a session it is also not estimable, for
the same reason the price gradient is not.

## How many grades there are

Four measurements do not imply four prices. Straggler behaviour, collective bandwidth,
sustained throughput and checkpoint I/O all depend on how well a facility is built and
operated, so they are likely to be correlated across operators. A factor decomposition of
the assay vector answers how many independent grades exist, and the share of variance on the
first component is the number to publish.

That is a design instruction for anyone writing an assay standard. Axes earn their place by
being separately priced, not by being separately measurable, and separate pricing requires
operators that are good on one axis and poor on another. A panel sampled for
representativeness will be dominated by operators that are uniformly good or uniformly poor
and will identify one grade. Crude is graded on two headline numbers rather than forty for
the same reason.

If the first component carries most of the variance, the defensible product is a single
grade with a published loading on each measured axis.

## From a coefficient to a differential

The commercial form of `G` is the structure crude uses, where a reference grade is defined by
assay and every other grade trades at a published differential to it. For seller `s` against
the reference:

```
ln p_s - ln p_ref  =  G'(q_s - q_ref)  +  D_s
```

The first term is the graded differential, slow-moving and structural, and it is what an
assay prices. `D_s` is everything else: service levels, compliance, egress, support,
contractual form, and whatever the market pays out of habit. A graded price would publish
the two components separately and would not describe `D_s` as mispricing, because a rental
contract has more attributes than an assay has axes.

SemiAnalysis's ClusterMAX rates GPU clouds across ten criteria, pricing among them, and says
its top-tier providers "command a pricing premium" because their total cost of ownership is
better even where the raw $/GPU-hr is higher. That asserts a differential exists without
estimating its size. Silicon Data's SiliconMark benchmarks GPU, cluster and
LLM performance, and Silicon Data prices compute through separate index products. Measurement
and price are both in the market. The coefficient that joins them has not been published.

## What would falsify this

If repeated readings of `dlperf` on the same offer show a reliability ratio close to zero,
the score does not measure a property of the machine, and the within-venue design has no
regressor.

If, once reliability is accounted for, the within-venue gradient on measured performance is
indistinguishable from zero with part number and session held fixed, the marketplace does not
price delivered node quality, and the graded component of the differential is not worth
publishing. A gradient estimated on single readings cannot establish this, because
attenuation produces the same result.

If measured performance within a part number is tightly clustered once measurement error is
removed, within a few per cent between the fastest and slowest machine, the machine-level
variation the design relies on is absent, and the axes that matter are the multi-node ones
that no public source reports.

If a second venue in the panel begins publishing per-offer measurements, the claim that the
design is available on exactly one venue expires and should be re-examined rather than cited.

## Limitations

This note reports no estimate. It specifies a design, states which designs the record can
identify, and measures how far the stored book is from supporting the one that survives.

As of 13 September the stored vast.ai book is restricted to datacenter-verified hosts, so
the host counts above describe the index's scope and not the marketplace. The ascending book
counts show that the marketplace itself lists only 18 to 19 rentable on-demand H100 SXM
offers a session, so a wider scope raises the offer count without making it large.

`dlperf` per GPU is TCI's normalisation, the venue's per-offer score divided by GPU count.
The venue does not state that the score scales linearly with GPU count, and offers on the
same machine with different GPU counts are not strictly comparable on it.

The two offers observed on both sessions are the whole evidence on score stability. One
moved 0.27% and one moved 30.50%. That establishes that large single-reading error occurs.
It does not establish how often.

The hazard specification treats daily snapshots as the observation interval. Offers that
appear and disappear within one day are invisible to it.

Nothing here is a forecast, and none of it is investment advice.

## Reproducibility

The stored payload keys for vast.ai:

```sql
SELECT DISTINCT key
FROM observations, json_each(observations.raw_json)
WHERE provider = 'vast.ai' AND ts_utc >= '2026-09-08';
```

The rate-card share and the sellers who never repriced, 18 July to 5 September:

```sql
SELECT tier, COUNT(*) FROM observations
WHERE ts_utc < '2026-09-06' GROUP BY tier;

SELECT provider, COUNT(DISTINCT price_usd_per_gpu_hr) AS distinct_prices,
       COUNT(DISTINCT substr(ts_utc,1,10))            AS days
FROM observations
WHERE gpu_model = 'H100_SXM' AND country IS NOT NULL AND country <> 'US'
  AND ts_utc < '2026-09-06'
GROUP BY provider ORDER BY distinct_prices;
```

The book per session, with hosts, machines, the fetched book size and the score range:

```sql
SELECT substr(ts_utc,1,10)                                     AS d,
       COUNT(*)                                                AS offers,
       COUNT(DISTINCT json_extract(raw_json,'$.machine_id'))   AS machines,
       COUNT(DISTINCT json_extract(raw_json,'$.host_id'))      AS hosts,
       MAX(json_extract(raw_json,'$.book.asc_count'))          AS asc_book,
       MIN(price_usd_per_gpu_hr), MAX(price_usd_per_gpu_hr),
       MIN(json_extract(raw_json,'$.dlperf') / json_extract(raw_json,'$.num_gpus')),
       MAX(json_extract(raw_json,'$.dlperf') / json_extract(raw_json,'$.num_gpus')),
       MIN(json_extract(raw_json,'$.total_flops') / json_extract(raw_json,'$.num_gpus')),
       MAX(json_extract(raw_json,'$.total_flops') / json_extract(raw_json,'$.num_gpus'))
FROM observations
WHERE provider = 'vast.ai' AND gpu_model = 'H100_SXM' AND tier = 'executable'
  AND ts_utc >= '2026-09-08'
GROUP BY d ORDER BY d;
```

Price against score within one host, and the score on the same offer across sessions:

```sql
SELECT json_extract(raw_json,'$.host_id') AS host, json_extract(raw_json,'$.id') AS offer,
       price_usd_per_gpu_hr,
       json_extract(raw_json,'$.dlperf') / json_extract(raw_json,'$.num_gpus') AS dlperf_gpu
FROM observations
WHERE provider = 'vast.ai' AND gpu_model = 'H100_SXM' AND tier = 'executable'
  AND substr(ts_utc,1,10) = '2026-09-12'
ORDER BY host;

SELECT substr(ts_utc,1,10), price_usd_per_gpu_hr, json_extract(raw_json,'$.dlperf')
FROM observations
WHERE provider = 'vast.ai' AND tier = 'executable'
  AND json_extract(raw_json,'$.id') IN (48704108, 37720465)
ORDER BY json_extract(raw_json,'$.id'), ts_utc;
```

The spread figures are reproduced by the final query in Note 2026-04, run for 5 and 7
September.

The remedies indicated by these findings are recorded in the project changelog against the
date of this note. None of them enters the calculation path.

## Sources

- TCI Research Note 2026-03, *Compute prices barely move. Where you buy moves everything.*, 6 September 2026.
- TCI Research Note 2026-04, *A GPU-hour is a part number, not a unit*, 7 September 2026, with the correction of 8 September 2026.
- Rosen, S. (1974), "Hedonic Prices and Implicit Markets: Product Differentiation in Pure Competition", *Journal of Political Economy* 82(1), 34-55.
- Oster, E. (2019), "Unobservable Selection and Coefficient Stability: Theory and Evidence", *Journal of Business & Economic Statistics* 37(2), 187-204.
- Sawyer, S. D. and O'Bryan, C. (2023), "Exploring quality adjustment in PPI cloud computing", *Monthly Labor Review*, US Bureau of Labor Statistics. https://www.bls.gov/opub/mlr/2023/article/exploring-quality-adjustment-in-ppi-cloud-computing.htm
- US Bureau of Labor Statistics, "A Review of Hedonic Price Adjustment Techniques for Products Experiencing Rapid and Complex Quality Change". https://www.bls.gov/cpi/quality-adjustment/hedonic-price-adjustment-techniques.htm
- SemiAnalysis, *ClusterMAX Rating System Overview*. https://clustermax.ai/overview
- Silicon Data, *SiliconMark*. https://www.silicondata.com/products/silicon-mark
