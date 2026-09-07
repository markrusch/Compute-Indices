# Discovery channels

Ordered by how early the channel sees a new entrant, not by how easy it is to read. A
provider becomes visible roughly in this sequence, and the gap between tier 0 and tier 3
runs to months:

    funding / DC announcement  ->  NVIDIA partner listing  ->  own pricing page
      ->  comparison aggregators  ->  listicles and "top 10" blogs

Every URL below was opened on the date in the "last checked" column. If you cannot open
one, record that in the scan rather than quietly dropping the channel.

---

## Tier 0 — inside the data we already have (free, automatic, fires first)

The only channel that works while nobody is looking, and the one most likely to surface a
provider that already qualifies.

| Check | How | Last checked |
|---|---|---|
| Unclassified provider names | `python -m tci.run sources`, section UNCLASSIFIED PROVIDERS | 2026-09-07 |
| Unmapped regions | same report, section UNMAPPED LOCATIONS | 2026-09-07 |
| Providers in gpuhunt we do not query | `from gpuhunt._internal.catalog import OFFLINE_PROVIDERS, ONLINE_PROVIDERS` — the collector queries three of fifteen | 2026-09-07 |
| New SKUs in the Azure retail feed | the collector skips any `armSkuName` not in `SKU_MAP`; a skipped-SKU log line is a new product | 2026-09-07 |
| New hosts on Vast.ai | new `host_id` values in stored `raw_json` | 2026-09-07 |

Query for the gpuhunt check, which costs nothing because the catalogs are already local:

```python
import gpuhunt, collections
for p in ["verda", "lambdalabs", "nebius", "oci", "cloudrift", "runpod"]:
    items = gpuhunt.query(gpu_name=["H100"], provider=[p], spot=False)
    print(p, len(items), collections.Counter(i.location for i in items))
```

## Tier 1 — leading indicators (months before a price page exists)

| Channel | URL | What to look for | Last checked |
|---|---|---|---|
| NVIDIA partner directory | https://marketplace.nvidia.com/en-eu/enterprise/partners/ | Cloud-partner type, filtered to Europe. NCP certification tracks deployment, so a name here is usually 1–2 quarters from GA | 2026-09-07 |
| NVIDIA GPU cloud partners page | https://www.nvidia.com/en-us/data-center/gpu-cloud-computing/partners/ | the global list, coarser but broader | 2026-09-07 |
| EuroHPC JU | https://eurohpc-ju.europa.eu/large-scale-access-ai-factories_en | 19 AI Factories, six deploying in 2026 across CZ, LT, NL, PL, RO, ES. Not a price source (subsidised access is not a market rate) but the hosting entities often sell commercial capacity alongside | 2026-09-07 |
| CME / NYMEX product listings | https://www.cmegroup.com/media-room/press-releases/ | contracts referencing compute; tells you which index someone else already trusts | 2026-09-07 |
| Datacentre and colocation press | Data Center Dynamics, The Register | GW-scale campus announcements name the operator before the operator names a price | — |

## Tier 2 — coincident (the operator's own surface, and the best kind of source)

Always prefer these to any aggregator. This is where a collector should read from.

| Provider | Pricing surface | Note | Last checked |
|---|---|---|---|
| Sesterce | https://cloud.sesterce.com (rates shown on https://www.sesterce.com/) | FR; live on-demand USD; H100 $1.83, H200 $2.48, B200 $4.11 per GPU-hr on 2026-09-07. `/pricing` 404s — the homepage carries the rates | 2026-09-07 |
| Gcore | https://gcore.com/ | LU; H100 with InfiniBand in Luxembourg, but the H100 offer is described as six-month commitment. Verify an on-demand hourly rate exists before building anything | 2026-09-07 |
| Lambda | https://lambda.ai/pricing | also in the gpuhunt offline catalog as `lambdalabs` — take the catalog, not the page | 2026-09-07 |
| Oracle OCI | via gpuhunt `oci` | 44 H100 rows on 2026-09-07 including eu-amsterdam-1, eu-stockholm-1, eu-paris-1, uk-cardiff-1 | 2026-09-07 |

When a page renders prices only under JavaScript, do not build a scraper. Look for the
API the console itself calls — that is how `scaleway.py` came to exist — and if there
isn't one, use a static entry with a `last_verified` date.

## Tier 3 — aggregators (lagging; use to catch what tiers 0–2 missed)

Read these for **names**, never for prices. An aggregator's number is somebody else's
collection decision, and ingesting one makes the index unreproducible and quietly
double-counts.

| Aggregator | URL | Coverage on last check | Last checked |
|---|---|---|---|
| GetDeploying | https://getdeploying.com/gpus | 4,774 prices, 77 providers, ~17 European. The best single name-source found so far | 2026-09-07 |
| ComputePrices | https://computeprices.com/ | public API at `/api/v1/gpu-prices`, free tier 750 req/day, but Bearer-key gated | 2026-09-07 |
| GPU.ai price index | https://gpu.ai/gpu-price-index | no-auth JSON API, free to cite with attribution, 12+ clouds — but no regional breakdown at all, so it can never be an EU input | 2026-09-07 |
| GPUs.io | https://gpus.io/en | 2,319 configurations, 27 providers | 2026-09-07 |
| GPUFinder | https://gpufinder.dev/gpu/h100 | 21 providers on H100 | 2026-09-07 |

European names harvested from GetDeploying on 2026-09-07, minus those already in the
panel: Sesterce (FR), Gcore (LU), UpCloud (FI), Leafcloud (NL), Exoscale (CH), Elastx
(SE), Lyceum (DE), Impossible Cloud (DE), Contabo (DE), Beyond.pl (PL), Koyeb (FR).
Most of those are general-purpose clouds that may carry no H100-class product at all —
the name is a lead, not a candidate, until gate 2 of the rubric is checked.

## Tier 4 — competitor and reference indices

Not sources. Watch them because they define what "settlement-grade" will mean to a
counterparty, and because a methodology published by someone else is free peer review.

| Index | Note | Last checked |
|---|---|---|
| Silicon Data | 150k daily records, 50–100 platforms, 40–50 countries. CME/NYMEX listing H100 and B200 Rental Index Futures on 2026-10-05 pending regulatory review | 2026-09-07 |
| SemiAnalysis rental index | 1-year contract prices; a term structure this index does not observe | 2026-09-07 |
| Kalshi / prediction venues | public levels only, as a sanity check | — |

## Channels considered and not used

- **Reddit, Discord, HN threads.** Names surface here first, sometimes weeks before
  anywhere else, but nothing found this way is citable until it has an operator page. Use
  as a pointer into tier 2, never as evidence.
- **Vendor-sponsored "top 10 GPU clouds" posts.** Ranked by affiliate relationship.
- **PeeringDB / ASN registries.** Genuinely early for datacentre operators, but the signal
  is capacity rather than a rentable product, and the false-positive rate is high.
