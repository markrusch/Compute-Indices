# TCI Data Sources

Collection conduct (all sources): public pages and public APIs only; no scraping behind
logins; robots.txt respected; **1 request per source per day** unless a row below says
otherwise and why; honest User-Agent
(`TCI-CRI-collector/x.y.z (research index; contact: rusch.mh@gmail.com)`); every collector
fails soft (log + continue — a source outage never fabricates or blocks a print).
Where a page is hostile to scraping, a manually refreshed static entry with a visible
`last_verified` date is used instead — more credible than a brittle scraper.

| Source | Endpoint | Auth | IOSCO tier | robots/ToS basis | Status | Last reviewed |
|---|---|---|---|---|---|---|
| Vast.ai | `POST https://console.vast.ai/api/v0/bundles/` | none | executable | Public search API used by the site's own search UI; scope = datacenter-verified hosts only (verification=verified AND hosting_type=1); offers stored globally, EU filter in the calculation path. **One request per chip (9 chips, plus a descending re-read for a full book), spaced 0.75 s**: the endpoint clamps every response at about 64 offers, and a single unfiltered query returns only the cheapest consumer cards (the cause of the 8–11 September 2026 gaps) | live | 2026-09-11 |
| Static EU neoclouds (8) | Public pricing pages (nebius, datacrunch→Verda, scaleway, ovhcloud, hetzner, genesis_cloud, seeweb, leaseweb) | none | list | Manual reads of public pages; entries carry `last_verified`, warned at 45d, excluded at 90d. 3 priced (nebius, datacrunch, seeweb); 5 null with documented reasons (JS-only pages, no H100 product, monthly-only, 404) | live | 2026-07-18 |
| ECB FX (frankfurter.dev) | `GET https://api.frankfurter.dev/v1/latest?from=EUR&to=USD` | none | FX only | Free public API redistributing ECB reference rates (frankfurter.app 301s here) | live | 2026-07-18 |
| RunPod | `POST https://api.runpod.io/graphql` (gpuTypes/securePrice) | none | executable | Public GraphQL endpoint; secure cloud only; secure pricing is region-flat and deliverable from EU-RO/EU-SE/EU-NL — observations recorded against EU-RO by convention. If auth becomes required, collector is retired (no workarounds) | live | 2026-07-18 |
| gpuhunt (dstack) | pip package; aws/azure/gcp catalogs (live), oci/lambdalabs/verda/nebius catalogs (shadow until v0.5.0, 2026-09-22); H100, H200, B200, B300, A100; 2+ GPU instances | none | list | Open-source package redistributing public catalog prices. Form factor pinned from each provider's instance name; a row that cannot be pinned is skipped. For some providers the catalog is every instance type times every location, so a location is where the provider sells, not proof of stock that day | live | 2026-09-11 |
| Scaleway | `GET https://api.scaleway.com/instance/v1/zones/{zone}/products/servers` | none | list | Scaleway's own public product-catalog API, the documented source the console reads; 9 EEA zones (fr-par, nl-ams, pl-waw), a failing zone is skipped not fatal. Prices are **EUR per instance** — divided by GPU count and converted at print-time FX, never frozen at collection. Supersedes the JS-only pricing page that kept `config/providers/scaleway.yaml` null | live | 2026-08-15 |
| Azure Retail Prices | `GET https://prices.azure.com/api/retail/prices` (OData) | none | list | Microsoft's public, unauthenticated retail price feed; 10 EEA regions, paginated with a page cap. Only hand-mapped SKUs are emitted — the API exposes no GPU-count field, so an unmapped SKU is skipped rather than guessed. `ND128isr_NDR_GB200_v6` is deliberately excluded pending verification of its accelerator count. Spot/Low-Priority meters excluded (outside the on-demand unit). **switzerlandnorth and uksouth are not queried — neither is in the EEA** | live | 2026-08-15 |
| Computable recipes (9) | OVHcloud order catalogue, Civo, CoreWeave, Voltage Park, DigitalOcean, Latitude.sh, Hyperstack, Crusoe, Lambda pricing page | none | list | Collector recipes vendored from the Computable GPU Index (Apache-2.0), run under TCI's User-Agent, one request per surface (two for Civo, Hyperstack and Voltage Park, four for OVHcloud). Every GPU row stored with its published tier and tenor; a country only where the surface names one. **Shadow**: no row reaches a print until a version admits the provider to the panel. OVHcloud's H100P rows are admitted from v0.5.0 | shadow | 2026-09-11 |
| RunPod datacentre stock | `POST https://api.runpod.io/graphql` (dataCenters.gpuAvailability) | none | reference | A separate second request, so a schema change cannot take the price query down. Records which datacentres report stock of each GPU type; audit data only | live | 2026-09-11 |
| Published discount schedules | `config/term_schedules.yaml` (Verda) | none | term table only — **never an index input** | Manual reads of a seller's public pricing page where it states commitment discounts as percentages; entry carries the URL and `last_verified`, dropped after 90 days. Feeds `term.html` only | live | 2026-09-11 |
| AWS Capacity Blocks | `DescribeCapacityBlockOfferings` (EC2) | **IAM required** | — | Investigated as a public forward curve and **rejected**: it is a signed EC2 API action scoped to the caller's own account and region, so a third party cannot reproduce a print from it. Excluded from the calculation path on reproducibility grounds, not availability | rejected | 2026-08-15 |
| Shadeform | aggregator API | free key | list | Optional; only with an issued key per their terms | not built (key needed) | — |
| ENTSO-E Transparency | REST API, day-ahead prices NL / DE-LU / FR / SE3 | free token | overlay only — **never an index input** | Public data platform; collector skips cleanly until ENTSOE_TOKEN is set | built, token pending | 2026-07-18 |

Validation-only cross-checks (never ingested): computeprices.com, cloud-gpus.com,
Silicon Data public prints, Kalshi/Ornn public levels, computepulse.net (undocumented
`/api/indices/history` endpoint, `config/check_series.csv`).

Review cadence: each row's ToS basis re-checked when a collector changes, and at the
annual methodology review at the latest.

## The register behind this table

This table is what is in the calculation path. The working register behind it is
`config/source_registry.yaml`, which also holds candidates, leads, and every source
screened and rejected with the reason it failed and the specific thing that would change
the answer. A rejection is kept because most of them are conditional — "needs an API
key", "no on-demand rate", "JS-only page" — and conditions expire without announcement.

`config/regions.yaml` says what happens to a price once it is collected. One block is
published (EU/EEA, the headline family). Eight are **shadow**: collected, stored, and
published nowhere, accumulating the history a future regional index would need before its
first print is worth anything. Nothing in a shadow block can reach a published print —
`normalise.py` filters on `factors.yaml:eu_eea_countries` and only that.

Both files are outside the calculation path and outside `METHODOLOGY.lock`. Adding a row
to either changes no number. Promoting a source into the calculation path is a minor
methodology change under GOVERNANCE.md §1.

    python -m tci.run sources          # register, review clock, per-block coverage
    python -m tci.run sources --due     # sources past their review date

Sources are re-swept monthly by the process in `.claude/skills/source-discovery/`; each
sweep is written up in `research/source-scans/`.
