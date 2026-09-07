# A GPU-hour is a part number, not a unit

**The index records an interconnect for all 7,798 observations and has observed one for none of them, 805 rows assert an SXM part and a PCIe bus at the same time, and no source in the panel has ever disclosed a fabric, a power envelope or a thermal limit.**

TCI Research Note 2026-04 · 7 September 2026 · Mark Rusch

---

## Abstract

Every price index rests on a unit that is assumed homogeneous. Brent and WTI are separate
grades because crude is not one substance, and the difference is quantified by assay:
density, sulphur, a published differential. TCI's unit is **one NVIDIA H100 SXM 80GB
GPU-hour**, which is a part number. It says what silicon is installed. It says nothing
about what an hour of that silicon delivers.

Auditing what the index actually knows about the goods it prices: of 7,798 stored
observations, **zero** carry an interconnect value read from a field any source
disclosed. 57.6% carry a constant hardcoded in the collector. The other 42.4% carry a
guess parsed out of a SKU or instance-name string. Across all 7,798 raw payloads, the
number mentioning InfiniBand, RoCE, NVSwitch, TDP, watts, cooling, fabric, or bandwidth
is **zero**.

The guessing is not harmless. **805 rows, 13.4% of every SXM row in the database,
assert `gpu_model='H100_SXM'` and `interconnect='PCIe'` simultaneously**, which is a
contradiction in terms: SXM and PCIe are mutually exclusive form factors. They come from
a fallback branch that returns `"PCIe"` whenever a SKU name fails two substring tests.

None of this touches a published number, because `interconnect` is not in the calculation
path. What it touches is the audit trail, which currently states things nobody told it.
And it bounds what this index can honestly claim: today's spread across European H100
sellers was **7.40x**, from $2.16 to $15.98, and I cannot tell you how much of that is
price and how much is grade.

---

## 1. Why this note exists

Practitioners who benchmark GPU fleets for a living make a claim that ought to worry
anyone building a compute price index: published specifications generalise across
silicon, and delivered performance does not. The reasons they give are the silicon
lottery, heterogeneous power delivery, network topology that differs between nominally
identical nodes, and cooling. Their method is to run short reference workloads shaped
like real jobs: synchronised training steps, collective communication, sustained matmul,
checkpoint I/O under load. Those numbers, rather than the datasheet, are what they treat
as the node's capability.

If that is right, then a benchmark quoting one price for "an H100-hour" is averaging over
goods of different quality and calling the result a price. That is a serious charge
against my own construction, and the first thing to do with it is not to argue but to
check what the index actually records.

I cannot test the performance claim. I have no benchmark harness, no fleet access, and no
delivered-FLOPs data. What I can do is audit the disclosure: if quality varies, does the
index capture anything that would let it adjust? The answer turned out to be cleaner and
worse than I expected.

---

## 2. Data

`data/eucri.db` in the public repository, all sources, all models, 2026-07-18 to
2026-09-07: **7,798 observations** across nine providers. The relevant columns are
`gpu_model`, `gpu_count`, `interconnect`, and `raw_json`, which stores the source payload
as received. Every query is in §12.

---

## 3. Finding 1: the index records a quality attribute it has never observed

`interconnect` is populated on 100% of rows. That looked like a disclosure success until
I traced where the values come from:

| Source | Providers | How `interconnect` is set | Rows |
|---|---|---|---:|
| `gpuhunt_.py` | aws, gcp | literal `interconnect="NVLink"` | 4,009 |
| `static_yaml.py` | datacrunch, nebius, seeweb | literal `interconnect="NVLink"` | 111 |
| `runpod.py` | runpod | constant in a `GPU_MODEL_MAP` lookup | 111 |
| `vast_ai.py` | vast.ai | constant in a `GPU_MODEL_MAP` lookup | 264 |
| `scaleway.py` | scaleway | `"NVLink" if "SXM" in instance_name else "PCIe"` | 415 |
| `azure_retail.py` | azure | substring tests on the SKU string | 2,888 |

**57.6% of the database carries a constant a developer typed.** The remaining 42.4% is
parsed from a product name, which is a naming convention rather than a specification.
Nothing is read from a disclosed field, because there is no disclosed field to read.

A column that is 100% populated and 0% observed is worse than an empty one. An empty
column advertises what you do not know.

---

## 4. Finding 2: no source discloses anything that determines delivered performance

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

The 264 hits are the string "NVLink" appearing inside a GPU *display name* on two
marketplaces. Not one payload in the database describes a fabric, a power envelope, or a
thermal limit.

Here is an entire Azure payload, which is the most structured source in the panel:

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

A price, a region, a SKU string, a billing unit. That is the whole of what the market
tells a price index about the good being sold.

---

## 5. Finding 3: the column holds two different axes, and one of them exists for one seller

The values in `interconnect` fall into two groups that do not belong in one column:

- **Intra-node bus**, how GPUs inside a node talk to each other: `NVLink`, `NVL`, `PCIe`.
- **Inter-node fabric**, how one node reaches another: `InfiniBand`, `Ethernet`.

A row reading `NVLink` says nothing about whether the node can reach another node at all.
A row reading `InfiniBand` says nothing about the bus inside it. And **only Azure ever
produces an inter-node value**, 678 rows out of 7,798. For the other eight providers the
fabric is not unknown, it is unrepresented: the column has no way to say it.

For single-GPU inference that gap is survivable. For the multi-node synchronised training
that dominates large-scale demand, inter-node fabric is close to the whole story, and it
is the axis the index is blindest on.

---

## 6. Finding 4: 805 rows contradict themselves

`azure_retail.py` derives the field like this:

```python
def _interconnect(sku: str) -> str:
    if "noIB" in sku:
        return "Ethernet"
    if "isr" in sku:
        return "InfiniBand"
    return "PCIe"
```

Applied to the three H100 SKUs Azure actually publishes:

| SKU | Assigned | Rows | Correct? |
|---|---|---:|---|
| `Standard_ND96isr_H100_v5` | InfiniBand | 169 | yes |
| `Standard_ND96is_noIB_H100_v5` | Ethernet | 168 | yes |
| `Standard_ND96is_H100_v5` | **PCIe** | 123 | **no** |

The ND H100 v5 series is built on H100 **SXM5** GPUs with NVLink 4.0 between them.[^1]
The `isr`/`is` difference is the RDMA suffix, not the bus. So the third row is not a
near-miss, it is the fallback branch firing on a SKU it was never taught, and the answer
it gives contradicts the same row's own `gpu_model`.

Counted across the database, **805 of 5,998 SXM rows (13.4%) assert an SXM part and a
PCIe bus at once.** 123 are H100, 682 are A100. A row that disagrees with itself is a
useful thing to find, because it is the only class of error in this dataset that can be
caught without an external reference.

---

## 7. What this does to the published spread

Today, 7 September, EU/EEA H100 SXM, one collection run:

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

**7.40x.** Note 2026-03 attributed spreads like this one to the seller population, who is
quoting rather than where. That still holds and I am not withdrawing it. But it was an
incomplete account, because it assumed the good was constant across the panel, and this
note is the evidence that the index has no basis for that assumption.

To be precise about the claim: the index cannot decompose that spread, and a benchmark
that cannot decompose its own spread should say so rather than let a reader assume the
cheapest row and the dearest row are the same good bought at different prices. The
direction of the likely bias is worth stating too: if the expensive end delivers more
useful work per hour, then the true price dispersion is *narrower* than 7.40x, and a
median over undifferentiated grades sits somewhere nobody can locate.

---

## 8. Every older commodity solved this the same way

Crude is priced by grade, and the grade is defined by assay: API gravity and sulphur
content, measured to a standard, with a published differential between benchmarks. Dry
bulk freight is priced per route *and* per vessel description. The Baltic indices specify
deadweight, age, draft and speed, because a Capesize hour is not a Handysize hour.
Electricity is priced by delivery point and delivery hour, since a megawatt-hour in the
wrong place is a different product.

The pattern is always the same and always in this order: name the attributes that make
the good non-fungible, measure them to a published standard, and quote the differential.
Compute has completed step one informally (everyone knows fabric and thermals matter)
and has not started step two.

TCI is not able to start it either, and saying so plainly is the point of this note. The
reference-unit definition in `factors.yaml` is a part number because a part number is the
only thing the sources supply.

---

## 9. What an assay would have to measure

The reference-workload approach maps onto the four things this dataset is blind to, and
the mapping is tight enough to be worth writing down:

**Synchronised training steps** catch straggler behaviour. In a data-parallel step every
rank waits for the slowest, so a fleet's throughput is set by its worst node rather than
its average one. That is the failure a datasheet cannot express, and it is what the
silicon lottery and thermal variation produce.

**Collective communication** measures the inter-node fabric: the axis eight of nine
sources in this panel never mention, and the one that dominates multi-node training.

**Sustained matmul throughput** separates peak from sustained. Published TFLOPS are boost
figures. What a node holds under a long job is a function of its power envelope and its
cooling, neither of which appears anywhere in 7,798 payloads.

**Checkpoint I/O under load** covers the storage path, which no rate card in this panel
describes at all, and which turns into wall-clock time on any large training run.

Those four would constitute a compute assay. The index does not need to run them itself.
Crude benchmarks do not run their own assays; they cite an independent one measured to a
published standard. What compute lacks is the standard and the independent assayer, not the
technique.

---

## 10. What TCI should do, in order

**Stop asserting what it has not observed.** Split `interconnect` into an observed value
and a provenance marker, and let the observed value be null when no source supplied one.
On today's data that would make the column null on 100% of rows, which is the honest
reading and a far better prompt to fix it than a column full of confident guesses.

**Fix the contradiction.** `azure_retail._interconnect` should return null on an unknown
SKU rather than falling through to `"PCIe"`. It is a one-line change in a collector that
is not hash-locked, and it stops the only self-detectable error class in the dataset.

**Separate the two axes** into an intra-node bus and an inter-node fabric, so that "not
disclosed" and "not applicable" stop sharing a representation.

**Then, and only then, consider a grade dimension in the series.** A `-IB` cut of the
headline would be a real product. Attempting it on the current data would be inventing the
grades, which is worse than not having them.

I am publishing this before making any of those changes, because the measurement is what
makes the case for them, and because a fix that lands in the same commit as its own
justification is harder for anyone else to check.

---

## 11. What would falsify this

If the reference workloads, run across this panel, showed delivered performance clustering
tightly for a given part number, say within a few per cent between the cheapest and
dearest European H100, then the part number is an adequate unit, the disclosure gap is a
tidiness problem rather than a measurement one, and this note overstates it.

If a source in the panel begins publishing fabric and power alongside price, the audit
above expires and should be rerun rather than cited.

And if the price dispersion turns out to be uncorrelated with every quality attribute once
one can be measured, then grade is not in the spread and Note 2026-03's population
account was complete on its own.

---

## 12. Behind the print

I went looking for a disclosure rate and expected something like 40%. Finding 100%
populated felt like good news for about a minute, until I opened `gpuhunt_.py` and saw
`interconnect="NVLink"` sitting there as a literal, applied to every AWS and GCP row in
the database. I had written that line. The comment above it says *8x hyperscaler H100
nodes are SXM/HGX*, which is a reasonable inference and still an inference, stored in a
column that reads to anyone downstream as an observation.

The self-contradicting rows were the part I did not see coming. 805 rows saying SXM and
PCIe in the same breath had been sitting in the audit table since the Azure collector
went in on 16 August, through every daily run, past every test. Nothing caught it because
nothing was looking: `interconnect` is not in the calculation path, so no test asserts
anything about it, and a field nothing checks is a field that drifts.

That is the more general lesson and it is not about GPUs. An audit trail is only as good
as the weakest field in it, and the weakest field is whichever one no test has an opinion
about.

---

## 13. Limitations

I have no performance data. Everything above is an audit of disclosure, and none of it
demonstrates that delivered performance actually varies across this panel. It establishes
that the index could not detect it if it did.

`interconnect` is not in the calculation path, so no published print is wrong as a result
of any of this. The defect is in the audit trail, which matters under IOSCO P16 and does
not move a number.

The provenance table in §3 comes from reading the collectors, not from a stored field.
That is precisely the marker §10 proposes adding.

Nothing here is a forecast, and none of it is investment advice.

---

## 14. Reproducibility

```sql
-- §3: where interconnect values come from, by source
SELECT source, provider, interconnect, COUNT(*) FROM observations
GROUP BY 1, 2, 3 ORDER BY source, provider;

-- §4: payloads mentioning any fabric, power or thermal term
SELECT COUNT(*) FROM observations WHERE lower(raw_json) LIKE '%infiniband%';
--   repeat for roce, nvswitch, fabric, bandwidth, gbps, tdp, watt, power, cool

-- §6: rows asserting an SXM part and a PCIe bus at once
SELECT source, gpu_model, interconnect, COUNT(*) FROM observations
WHERE gpu_model LIKE '%SXM%' AND interconnect = 'PCIe'
GROUP BY 1, 2, 3;

-- §7: today's spread across European H100 sellers
SELECT provider, tier, MIN(price_usd_per_gpu_hr), MAX(price_usd_per_gpu_hr)
FROM observations
WHERE gpu_model = 'H100_SXM' AND substr(ts_utc, 1, 10) = '2026-09-07'
  AND country IS NOT NULL AND country <> 'US'
GROUP BY provider ORDER BY 3;
```

---

## Sources

[^1]: Microsoft, *ND-H100-v5 size series*, Azure Virtual Machines documentation: 8× NVIDIA H100 SXM5 80GB with NVLink 4.0 intra-node and 400 Gb/s NVIDIA Quantum-2 CX7 InfiniBand per GPU. https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndh100v5-series

The framing in §1 and the four workload classes in §9 restate what fleet-benchmarking
practitioners describe as standard practice. TCI has run none of it and claims no part in
it; it is cited as the technique a compute assay would use, not as a result.

Prior notes referenced: 2026-03, *Compute prices barely move. Where you buy moves
everything*, for the spread decomposition this note qualifies; 2026-01, *When an index
measures its own sampling*, for the bimodal panel.

— MR, Amsterdam
