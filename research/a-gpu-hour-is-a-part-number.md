# A GPU-hour is a part number, not a unit

**The index records an interconnect for all 7,798 observations and has observed one for none of them, 805 rows assert an SXM part and a PCIe bus at the same time, and nothing the index had stored described a fabric, a power envelope or a thermal limit.**

TCI Research Note 2026-04 · 7 September 2026 · The Compute Indices

---

> **Correction, 8 September 2026.** As first published, this note stated that no source in
> the panel discloses a fabric, a power envelope or a thermal limit. That was measured
> against what the index had stored, and it was the wrong place to look for it. One source
> does publish those attributes: vast.ai returns measured deep-learning throughput, total
> FLOPS, GPU memory bandwidth, PCIe bandwidth, NVLink bandwidth, a power ceiling and a
> temperature ceiling on every offer. The collector was reducing each offer to 21 pricing
> fields before storage and discarding the rest, so the audit below saw an absence the
> market had not created.
>
> The measurements in this note are unchanged and reproduce as published: the stored
> payloads did contain none of those terms, `interconnect` was inferred on every row, and
> 805 rows still contradict themselves. What was wrong is the attribution. For eight of
> the nine sources the gap is the market's; for vast.ai it was this project's.
>
> The collector now stores those fields, from 2026-09-08. The section headed *What an
> assay would have to measure* should be read against that: for one venue in the panel,
> three of its four axes are already being published and are now being kept.

## Abstract

Every price index rests on a unit assumed to be homogeneous. Brent and WTI are separate
grades because crude is not one substance, and the difference between them is quantified
by assay: density, sulphur, a published differential. The TCI unit is one NVIDIA H100 SXM
80GB GPU-hour, which is a part number. It states what silicon is installed. It states
nothing about what an hour of that silicon delivers.

This note audits what the index records about the quality of the goods it prices. Of
7,798 stored observations, none carries an interconnect value read from a field that any
source disclosed. 57.6% carry a constant written into a collector. The remaining 42.4%
carry a value parsed out of a SKU or instance-name string. Across all 7,798 raw payloads,
the number mentioning InfiniBand, RoCE, NVSwitch, TDP, watts, cooling, fabric or bandwidth
is zero.

The inference has a measurable cost. 805 rows, 13.4% of every SXM row in the database,
assert `gpu_model='H100_SXM'` and `interconnect='PCIe'` at the same time, which is a
contradiction in terms: SXM and PCIe are mutually exclusive form factors. They originate
in a fallback branch returning `"PCIe"` whenever a SKU name fails two substring tests.

No published number is affected, because `interconnect` does not enter the calculation
path. What is affected is the audit trail, which asserts facts no source supplied. The
consequence for the index is a bound on what it can claim: the spread across European
H100 sellers on 7 September was 7.40x, from $2.16 to $15.98, and the index cannot
decompose that figure into price and grade.

---

## The unit problem

Practitioners who benchmark GPU fleets make a claim that bears directly on the
construction of any compute price index: published specifications generalise across
silicon, and delivered performance does not. The reasons given are the silicon lottery,
heterogeneous power delivery, network topology that differs between nominally identical
nodes, and cooling. The method used is to run short reference workloads shaped like real
jobs, covering synchronised training steps, collective communication, sustained matmul and
checkpoint I/O under load, then to treat those results rather than the datasheet as the
node's capability.

If that is correct, a benchmark quoting a single price for an H100-hour is averaging
across goods of different quality and presenting the result as a price. That is a serious
charge against the construction used here, and the appropriate first response to it is
measurement rather than argument.

The performance claim itself cannot be tested from this dataset. TCI operates no benchmark
harness, holds no fleet access and has no delivered-throughput data. What can be
established is narrower and still decisive: if quality varies across the panel, does the
index capture anything that would allow it to adjust?

All figures below derive from `data/eucri.db` in the public repository, covering every
source and model from 2026-07-18 to 2026-09-07, a total of 7,798 observations across nine
providers. The relevant columns are `gpu_model`, `gpu_count`, `interconnect` and
`raw_json`, which stores each source payload as received. Every query appears at the end
of this note.

---

## A field that is fully populated and never observed

`interconnect` carries a value on every row. Tracing where those values originate:

| Source | Providers | How `interconnect` is set | Rows |
|---|---|---|---:|
| `gpuhunt_.py` | aws, gcp | literal `interconnect="NVLink"` | 4,009 |
| `static_yaml.py` | datacrunch, nebius, seeweb | literal `interconnect="NVLink"` | 111 |
| `runpod.py` | runpod | constant in a `GPU_MODEL_MAP` lookup | 111 |
| `vast_ai.py` | vast.ai | constant in a `GPU_MODEL_MAP` lookup | 264 |
| `scaleway.py` | scaleway | `"NVLink" if "SXM" in instance_name else "PCIe"` | 415 |
| `azure_retail.py` | azure | substring tests on the SKU string | 2,888 |

57.6% of the database carries a constant typed into a collector. The remaining 42.4% is
parsed from a product name, which is a naming convention rather than a specification.
Nothing is read from a disclosed field. No source in the panel publishes an
interconnect as a field; see the correction above for what one of them does publish.

A column that is fully populated and never observed is worse than an empty column. An
empty column advertises the gap.

---

## What the market actually discloses

Searching all 7,798 stored payloads for the terms that would matter:

| Term | Payloads containing it |
|---|---:|
| `nvlink` | 264 (3.39%) |
| `infiniband` | 0 |
| `roce` | 0 |
| `nvswitch` | 0 |
| `fabric` | 0 |
| `bandwidth` | 0 |
| `gbps` | 0 |
| `tdp` | 0 |
| `watt` | 0 |
| `power` | 0 |
| `cool` | 0 |

The 264 hits are the string "NVLink" appearing inside a GPU display name on two
marketplaces, not a fabric specification. No payload stored up to this date describes a
fabric, a power envelope or a thermal limit. As the correction above records, that was a
statement about what the collectors kept, not about what every source offers.

The most structured source in the panel returns this:

```json
{
  "armSkuName": "Standard_ND96isr_H100_v5",
  "armRegionName": "westeurope",
  "retailPrice": 132.232,
  "unitOfMeasure": "1 Hour",
  "meterName": "ND96isrH100v5",
  "productName": "Virtual Machines NDsr H100 v5 Series Windows",
  "currencyCode": "USD",
  "type": "Consumption"
}
```

A price, a region, a SKU string and a billing unit. That is the whole of what the market
tells a price index about the good being sold.

---

## Two axes in one column

The values recorded in `interconnect` fall into two groups that do not belong together.
`NVLink`, `NVL` and `PCIe` describe the intra-node bus, meaning how GPUs inside a single
node communicate. `InfiniBand` and `Ethernet` describe the inter-node fabric, meaning how
one node reaches another.

A row reading `NVLink` says nothing about whether the node can reach another node at all,
and a row reading `InfiniBand` says nothing about the bus inside it. Only Azure ever
produces an inter-node value, 678 rows out of 7,798, so for the other eight providers the
fabric is not merely unknown but unrepresentable: the column has no way to express it.

For single-GPU inference that gap is survivable. For the multi-node synchronised training
that dominates large-scale demand, inter-node fabric is close to the whole story, and it
is the axis on which the index is blindest.

---

## Rows that contradict themselves

The Azure collector derives the field as follows:

```python
def _interconnect(sku: str) -> str:
    if "noIB" in sku:
        return "Ethernet"
    if "isr" in sku:
        return "InfiniBand"
    return "PCIe"
```

Applied to the three H100 SKUs Azure publishes:

| SKU | Assigned | Rows | Correct |
|---|---|---:|---|
| `Standard_ND96isr_H100_v5` | InfiniBand | 169 | yes |
| `Standard_ND96is_noIB_H100_v5` | Ethernet | 168 | yes |
| `Standard_ND96is_H100_v5` | PCIe | 123 | no |

The ND H100 v5 series is built on H100 SXM5 GPUs with NVLink 4.0 between them.[^1] The
`isr` and `is` suffixes distinguish RDMA support, not the bus. The third row is therefore
not a near miss. It is the fallback branch firing on a SKU it was never taught, returning
an answer that contradicts the same row's own `gpu_model`.

Across the database, 805 of 5,998 SXM rows, or 13.4%, assert an SXM part and a PCIe bus
simultaneously; 123 are H100 and 682 are A100. Rows that disagree with themselves are the
one error class in this dataset detectable without an external reference, and they went
undetected because `interconnect` sits outside the calculation path, where no test asserts
anything about it. A field that nothing checks is a field that drifts. That observation
generalises past this column: an audit trail is only as reliable as its weakest field, and
the weakest field is whichever one no test has an opinion about.

---

## What this means for the published spread

On 7 September, EU/EEA H100 SXM, in a single collection run:

| Seller | Tier | Price |
|---|---|---:|
| seeweb | list | $2.16 |
| vast.ai | executable | $2.27 |
| datacrunch | list | $3.25 |
| runpod | executable | $3.49 |
| nebius | list | $3.85 |
| gcp | list | $5.44 |
| aws | list | $7.36 |
| azure | list | $15.98 |

A spread of 7.40x. Research Note 2026-03 attributed spreads of this kind to the seller
population, meaning who is quoting rather than where. That account stands and is not
withdrawn, but it was incomplete, because it assumed the good was constant across the
panel. This note is the evidence that the index has no basis for that assumption.

The claim here is bounded. The index cannot decompose that spread, and a benchmark unable
to decompose its own spread should say so rather than allow a reader to assume that the
cheapest row and the dearest row represent the same good at different prices. The likely
direction of the bias is worth stating: if the expensive end delivers more useful work per
hour, then true price dispersion is narrower than 7.40x, and a median taken across
undifferentiated grades sits at a level that cannot be located.

---

## How older commodities solved this

Crude is priced by grade, and grade is defined by assay: API gravity and sulphur content,
measured to a standard, with a published differential between benchmarks. Dry bulk freight
is priced per route and per vessel description, and the Baltic indices specify deadweight,
age, draft and speed, because a Capesize hour is not a Handysize hour. Electricity is
priced by delivery point and delivery hour, since a megawatt-hour in the wrong place is a
different product.

The sequence is consistent across all three: name the attributes that make the good
non-fungible, measure them to a published standard, then quote the differential. Compute
has completed the first step informally, in that fabric and thermals are widely understood
to matter, and has not begun the second. TCI is not in a position to begin it either, and
the reference-unit definition in `factors.yaml` is a part number precisely because a part
number is the only thing the sources supply.

---

## What an assay would have to measure

The reference-workload approach maps closely onto the four properties this dataset cannot
see.

Synchronised training steps capture straggler behaviour. In a data-parallel step every
rank waits for the slowest, so fleet throughput is set by the worst node rather than the
average one. That is the failure a datasheet cannot express, and it is what the silicon
lottery and thermal variation produce.

Collective communication measures the inter-node fabric, the axis eight of nine sources in
this panel never mention and the one that dominates multi-node training.

Sustained matmul throughput separates peak from sustained. Published TFLOPS figures are
boost numbers, and what a node holds under a long job is a function of its power envelope
and its cooling, neither of which appears in any of the 7,798 payloads examined here.

Checkpoint I/O under load covers the storage path, which no rate card in this panel
describes and which converts directly into wall-clock time on a large training run.

Those four measurements would constitute a compute assay. An index does not need to
perform them itself: crude benchmarks do not run their own assays, they cite an
independent assay measured to a published standard. What compute lacks is the standard and
the independent assayer, not the technique.

---

## What would falsify this

If reference workloads run across this panel showed delivered performance clustering
tightly for a given part number, within a few per cent between the cheapest and dearest
European H100, then the part number is an adequate unit, the disclosure gap is a tidiness
problem rather than a measurement one, and this note overstates the case.

If a source in the panel begins publishing fabric and power alongside price, the audit
above expires and should be rerun rather than cited.

If price dispersion proves uncorrelated with every quality attribute once one becomes
measurable, then grade is not present in the spread, and the population account in Note
2026-03 was complete on its own.

---

## Limitations

No performance data underlies this note. It is an audit of disclosure, and nothing in it
demonstrates that delivered performance varies across this panel. It establishes that the
index could not detect such variation if it existed.

`interconnect` is not in the calculation path, so no published print is affected. The
defect lies in the audit trail, which matters under IOSCO P16 and does not move a number.

The provenance table derives from reading the collectors rather than from a stored field,
which is itself the gap this note identifies.

Nothing here is a forecast, and none of it is investment advice.

---

## Reproducibility

```sql
-- Where interconnect values come from, by source
SELECT source, provider, interconnect, COUNT(*) FROM observations
GROUP BY 1, 2, 3 ORDER BY source, provider;

-- Payloads mentioning any fabric, power or thermal term
SELECT COUNT(*) FROM observations WHERE lower(raw_json) LIKE '%infiniband%';
--   repeat for roce, nvswitch, fabric, bandwidth, gbps, tdp, watt, power, cool

-- Rows asserting an SXM part and a PCIe bus at once
SELECT source, gpu_model, interconnect, COUNT(*) FROM observations
WHERE gpu_model LIKE '%SXM%' AND interconnect = 'PCIe'
GROUP BY 1, 2, 3;

-- The spread across European H100 sellers
SELECT provider, tier, MIN(price_usd_per_gpu_hr), MAX(price_usd_per_gpu_hr)
FROM observations
WHERE gpu_model = 'H100_SXM' AND substr(ts_utc, 1, 10) = '2026-09-07'
  AND country IS NOT NULL AND country <> 'US'
GROUP BY provider ORDER BY 3;
```

The remedies indicated by these findings are recorded in the project changelog against the
date of this note. Any change they produce in the calculation path will be announced in
advance under the notice procedure in GOVERNANCE.md §1 and published on the notices page.

---

## Sources

[^1]: Microsoft, *ND-H100-v5 size series*, Azure Virtual Machines documentation: 8× NVIDIA H100 SXM5 80GB with NVLink 4.0 intra-node and 400 Gb/s NVIDIA Quantum-2 CX7 InfiniBand per GPU. https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndh100v5-series

The framing of the unit problem and the four workload classes restate what
fleet-benchmarking practitioners describe as standard practice. TCI has performed none of
that work and claims no part in it. It is cited as the technique a compute assay would
use, not as a result obtained here.

Prior notes referenced: 2026-03, *Compute prices barely move. Where you buy moves
everything*, for the spread decomposition this note qualifies; 2026-01, *When an index
measures its own sampling*, for the bimodal panel.
