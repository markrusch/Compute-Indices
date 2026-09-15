# The forward spot estimate S* — data, model and build plan (v2, after review)

Working document, 15 September 2026. Private, like `ROADMAP.md` and `FORWARD-CURVE.md`.
v1 of this plan was reviewed adversarially before any code was written. The review found
21 breaks, six of them critical. This version is the plan after fixing them; section 10
lists each break and what changed.

The requirement: a genuinely forward-looking, martingale-type, or at minimum
no-arbitrage estimate of future spot, usable to hedge spot or on-demand usage, built
without any competitor's data, and fully automated.

---

## 0. The target

`S*(t, h)` is TCI's estimate, made on date `t` from data knowable on `t`, of the
**equal-weighted mean of the published EU-CRI-H100 prints on the calendar days
(t, t + h]**, for `h ∈ {30, 91, 182, 365}` days, in USD per GPU-hour. The index is computed
every calendar day, so "per day" and "per session" coincide.

A window mean, because a hedge of usage hedges a bill. EU-CRI-H100, because it is the only
series with a usable record (22 printed of 60 sessions to 15 September).

Two uses, kept apart:

1. **An index-linked exposure** needs an estimate of the future index: `M(h)`, published as
   `S*`.
2. **One's own usage cost** needs what can be locked today: the lockable-cost curve `L(h)`.
   A price, not an estimate, and not a bound on the index.

## 1. What theory allows

A GPU-hour cannot be stored, so spot does not pin the forward by cash-and-carry. The forward
of a non-storable is an expectation: `F(t,T) = E_t^Q[S_T]`, a martingale in `t`, with
`E_t^P[S_T] = F − π`. With no traded forward on this index (CME's and ICE's contracts
settle on competitors' indices and are excluded), the strict no-arbitrage content is thin:

- **A buyer's cost bound.** With free disposal nobody need pay more for compute over a window
  than the cheapest contract covering it. That bounds the buyer's *cost*, not the index: the
  index is a weighted median of offers, a median payoff cannot be replicated by renting one
  offer, and an offer nobody has rented can be repriced tomorrow.
- **Non-negativity and horizon consistency**, satisfied by construction.

Cross-class dominance (an H100 forward above an H200 forward) is a substitution argument on
medians of offers and is not an arbitrage. It is not built.

So `S*` is a martingale-type expectation. Term prices are published beside it as a
diagnostic, and their information content is measured in the ledger before anyone is asked
to trust them.

## 2. Data inventory

Verified live on 15 September 2026 unless marked.

### 2.1 Stored already

- **Observations** (`observations`): every seller's on-demand and term prices, per day. The
  anchor is recomputed from these (section 3.1), never read from `daily_index`.
- **EU term prices for the index population** (panel marketplace and neocloud providers,
  EU/EEA countries): Seeweb (IT, collector added 15 September, H100 SXM EUR 1.89 on-demand,
  1.80 / 1.70 / 1.61 at 3 / 6 / 12 months); Verda's published schedule (refreshed daily, but
  chip-invariant, so it carries no chip information). Outside the population: Azure
  reservations (hyperscaler, NL/SE/PL/ES), OVHcloud H100 PCIe 1 month (FR, different class).
  Civo carries no country; Latitude is US.
- **vast.ai offers with `duration`** (`observations.raw_json` since the collector began,
  `market_offers` since 14 September). `duration` is in seconds and is the host's listing
  end, not a contractual guarantee. vast.ai's own words: "On-demand instances have a fixed
  price set by the host", with "a maximum duration determined by the host". Long EU supply is
  essentially one Czech host (214845), 2/4/8-GPU offers at 392–458 days on several dates.
- **Published prints** (`daily_index`, MAX(revision)): used only as the realised value when
  scoring a closed window.

### 2.2 New, no credentials, built in v1

**vast.ai reserved quotes** → `term_quotes`. `POST https://console.vast.ai/api/v0/bundles/`
with the collector's H100 SXM query plus `"type": "reserved"` and
`"duration": {"gte": d·86400}`, `d ∈ {30, 90, 180}`. Eight seconds before the first request
and between requests; a 429 honours `Retry-After` (capped at 60 s), retries once, then
records a partial read. All offers stored with `in_index_scope` (verified and
`hosting_type = 1`). A reserved quote counts as a term price only if
`discounted_dph_total < dph_total`; a null or zero discount is not a term offer. Formation:
`market_quoted` (each host sets its own). Prepaid by definition. Captured fixture:
`tests/fixtures/forward/vast_reserved_h100_sxm_90d.json` (7 offers, 1 discounted).

**Interest rates** → `overlay_rates`. Three requests a day:
`GET https://data-api.ecb.europa.eu/service/data/EST/B.EU000A2X2A25.WT` (€STR),
`GET .../YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_3M+SR_6M+SR_1Y+SR_2Y+SR_3Y+SR_5Y` (AAA euro-area
spot curve), both `format=csvdata&lastNObservations=5`; and
`GET https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve&field_tdr_date_value={year}&_format=csv`
(US par curve), built from the run's year, falling back to the previous year when the
current one has no rows yet. `INSERT OR IGNORE` on (series, tenor, date, value), so a rerun
adds nothing and a restated value adds a row. USD prices are restated with the Treasury
curve, EUR prices with the ECB curve. Fixtures captured under `tests/fixtures/forward/`.

### 2.3 One human step, then automated — not in v1

**SF Compute order book.** Market-wide, participant-anonymous quotes, depth and fills for a
delivery window: `GET /preview/v2/orderbook/{quote,depth,fills}`, parameters `requirements`
(`accelerator:h100;availability_zone:…`), `start_at`, `end_at` (Unix epoch, whole minutes),
prices in dollars per node-hour. Bearer JWT required. Fills are transacted forward-starting
prices, the strongest forward-looking data found, but they price SF Compute nodes, not the
index, so they would enter as a basis-adjusted diagnostic like `T`, never as `S*` itself.
Steps: open an account, generate a token, read the terms for storing and publishing
aggregates, confirm GPUs per node and the Amsterdam zone identifier against a live response,
add secret `SFCOMPUTE_API_TOKEN`, then write the collector against a captured response.
Registry: `watchlist`.

### 2.4 Screened out

- CME H100/B200 futures (5 October 2026), ICE GPU futures: competitor indices. `rejected`.
- DigitalOcean 12-month reserved ($3.26 H100): "Reserve via a contractual commitment of 12
  months ... contact sales", no region stated. Not lockable. `watchlist`.
- AWS EC2 Capacity Blocks: forward-starting up to eight weeks, one price repriced quarterly,
  US and London. `watchlist`.
- Internet Archive pricing-page snapshots: Lambda 2024 renders prices in JavaScript;
  Paperspace, DataCrunch and RunPod snapshots returned 503 or nothing. `watchlist`.
- gpuhunt historical catalogs: bucket listing 403. `rejected`.
- Akash GPU price API: public, global spot, no term data. `watchlist`.

## 3. The model

Parameters live in `config/forward.yaml`, frozen before the backfill runs. Every ledger row
records `method_version = <version>+<sha256 of the file>`; a test fails if the file changes
without its version.

### 3.1 The anchor, recomputed under the rule that will apply

The methodology changes on dates already announced (v0.5.0 on 22 September, v0.6.0 on 1
October), so a window starting today spans versions. For each version `v` needed by the
window, the anchor is a **pro-forma series**: EU-CRI-H100 recomputed in memory from each
past day's stored observations under `v`'s parameters, through the same
`normalise_observations` and `compute_print` the calculation uses, never stored. Which
versions exist is knowledge-time: a version counts on date `t` only if its notice was
announced on or before `t` (or it has no notice). Reading observations by collection date
avoids the revision problem entirely; nothing reads `daily_index` to build an estimate.

A test recomputes stored prints under their own version and requires the pro-forma value to
equal the published one.

### 3.2 `M(h)`: the martingale estimate

The anchor is a noisy step function: medians of a handful of rate cards, one of four values
on most days, lag-1 autocorrelation of daily changes −0.32, and 56% of its realised variance
from one print. It is modelled as a **local level**: `y_d = ℓ_d + ε_d`,
`ℓ_d = ℓ_{d−1} + η_d`, in logs, `Var ε = r`, `Var η = q` per day, gaps handled by scaling
`q` with elapsed days. `q` and `r` are fitted by maximum likelihood over a fixed grid
(deterministic, no optimiser). The filtered level is the martingale part; drift is zero,
because 22 prints cannot estimate one and a prior would decide it.

For a window of `h` days: mean `exp(ℓ̂ + P/2)` (a martingale in price levels), log-variance
of the window mean `V = P + q(h+1)(2h+1)/(6h) + r/h`, quantiles from the lognormal with that
mean. Days under different versions are combined by day-weighted mean and variance.

Gaps: no estimate on `t` unless every version the window uses printed pro-forma on `t` and has
at least `min_prints` pro-forma prints. Never the last value carried forward.

### 3.3 `T(h)`: term-implied, diagnostic only

`T(h) = M-mean × median over sellers of the committed/on-demand ratio at exactly that tenor`,
from EU/EEA H100 SXM prices in the index population, formations `administered_differentiated`,
`administered_untested` or `market_quoted` (never `administered_uniform`). Prepaid prices are
restated as pay-as-delivered first. At least three sellers, no interpolation between tenors.
Each vast.ai host is a seller. Published beside `S*`, never used to form it in v1. Today it
gaps at every horizon, and the vast.ai side is one host.

### 3.4 `L(h)`: the lockable-cost curve

For each horizon, the cheapest price per *used* GPU-hour at which an EU buyer can secure H100
SXM compute under the index's unit (EU/EEA, at least two GPUs, datacenter-verified where
applicable), from:

- vast.ai listed asks with host maximum duration ≥ `h` (no minimum commitment);
- vast.ai reserved quotes with prepaid days ≥ `h`, restated as pay-as-delivered, full
  prepaid period charged;
- EU/EEA self-service rate cards whose contract covers `h`, charged for the whole contract
  (free disposal: `K × contract_days / h`), converted to USD at the as-of ECB rate.

Published with the count and the source of the minimum. `M − L` is the expected saving from
locking against rolling.

### 3.5 The ledger and calibration

`forward_estimates`, append-only, one row per `(date, series, component, horizon, revision)`
with value, P10/P50/P90, inputs digest, `method_version`, `backfilled`. Written by the daily
run after the prints, in its own try. A rerun that computes the same digest writes nothing; a
different result writes the next revision; readers take MAX(revision).

On the first run with an empty ledger, every past date with stored observations is estimated
as-of and written with `backfilled = 1`. Backfilled rows are pseudo-out-of-sample (as-of in
data, not in model design): reported separately, never drawn on the public history chart,
never called a track record.

A window's realised value is the mean of the published prints in it, when at least
`min_printed_share` of its days printed. Calibration (bias, RMSE, P10–P90 coverage) is
computed on **non-overlapping** windows only and published once there are at least
`min_nonoverlapping_windows`. Until then the page states the count and what it needs. At the
current print rate a 30-day horizon takes well over a year to qualify; 182 and 365 days take
many years. That is stated, not hidden.

No automatic switch to a term-based headline in v1. If one is added it must be pre-registered:
non-overlapping windows, Hansen-Hodrick errors, continuous shrinkage.

## 4. Architecture

- `src/tci/forward.py`: pure model (strict mypy).
- `src/tci/forward_data.py`: knowledge-time assembly from the database (pro-forma anchor,
  term ratios, lockable offers, rates), the ledger write, the backfill.
- `src/tci/collectors/vast_reserved.py`, `src/tci/collectors/rates.py`.
- `src/tci/models.py`: `TermQuote`. `collectors/base.py`: store `term_quotes` like
  `offer_book`. `canary.py`: count `term_quotes` rows.
- Migrations `0006_term_quotes.sql`, `0007_overlay_rates.sql`, `0008_forward_estimates.sql`,
  each with UPDATE/DELETE triggers.
- `commands.cmd_daily`: `collect_rates` beside `collect_fx`; `VastReservedCollector` last in
  `collectors_for_daily`; `record_forward` after `compute_all_series`, fail-soft with a `runs`
  row on failure.
- `python -m tci.run forward [--date D]`: record and write, used by the smoke test.
- `outputs/webdata.write_forward` → `site/data/forward/latest.json`, `history.csv`
  (non-backfilled rows only), `calibration.json`; own try.
- `outputs/site._forward` → `site/forward.html`, in `NAV`, `generate()`, `EXPECTED_PAGES`.
  Server-rendered SVG, CSS-only toggles, table twin, gap page on any failure.
- `config/source_registry.yaml`: rows for every source above with `access_basis`.

## 5. Governance

Not in the calculation path, not in `METHODOLOGY.lock`, no print moves, no notice.
`test_reproduce.py` and `reproduce --published` unchanged.

## 6. Automation

The daily Action runs everything. Nothing needs a person after deployment. The ledger
backfills itself once, on the runner, the first time it finds itself empty.

## 7. Tests

Model arithmetic against hand-worked values; the local-level fit on a synthetic series;
pro-forma equals stored print under its own version; knowledge-time (a later observation
or version notice does not move an earlier estimate); ledger idempotency and revisions;
backfill runs once; parameter lock; collectors on captured fixtures with no network; site page
generated, self-contained, no-JS, in `EXPECTED_PAGES`; smoke test runs `forward`.

## 8. Deployment

In an isolated worktree branched from `origin/main` (the shared checkout has another
session's uncommitted work). Commit the curve work, then this work. Run ruff, mypy, pytest,
`reproduce --published`. Never commit a locally written database: regenerate `site/` in a
throwaway copy of the tree and copy only `site/` back. Rebase on `origin/main`, push to `main`
well clear of 11:00 UTC. Production is Vercel; verify there. The next scheduled run migrates
the committed database, collects the new sources and backfills the ledger.

## 9. Known limits

- `S*` gaps whenever a version's pro-forma anchor does not print on the day.
- `T` is empty and will stay thin; vast.ai long-duration EU supply is one host.
- Calibration is years from qualifying at long horizons.
- Payment timing of rate cards other than vast.ai reserved is unknown; treated as
  pay-as-delivered.
- Prepayment credit risk is not priced (`prepaid_credit_spread: 0.0`).

## 10. Review findings and what changed

| # | Finding | Change |
|---|---|---|
| 1 | Anchor is a noisy step function; σ, μ quoted wrongly | Local-level model, μ = 0, grid MLE |
| 2 | Index definition changes inside windows | Pro-forma anchor per version, knowledge-time versions |
| 3 | `date ≤ t` let later revisions leak | Estimates rebuilt from observations; prints only for realised values |
| 4 | Ledger/β switch cannot trigger honestly | Switch cut; non-overlapping windows; timescale stated |
| 5 | SF Compute is another underlying, untestable | Cut from v1; exact acquisition steps; basis-adjusted if added |
| 6 | Deploy would push stale DB | Worktree from origin/main; no local DB committed; runner backfills |
| 7 | Inventory stale (Seeweb, host 214845) | Re-inventoried against origin/main |
| 8 | DigitalOcean reserved not lockable; pairing key includes source | Cut to watchlist |
| 9 | Zero discount counted as market quote | Discount must be strictly positive |
| 10 | PV used EUR curve for USD prices | Currency-matched curves; spread parameter |
| 11 | No storage hook, canary counts, 429 handling | `term_quotes` hook, canary counts, spacing and Retry-After |
| 12 | Ledger and rates writes not idempotent | Revisions + digests; INSERT OR IGNORE |
| 13 | A forward bug could take down the site; daily path untested | Own try, gap page, `forward` command in smoke test |
| 14 | Free parameters ungoverned | `config/forward.yaml`, versioned hash, lock test |
| 15 | Cross-class dominance mislabelled | Not built |
| 16 | "Hour-weighted" meaningless; GBM variance overstated | Equal weight per day; variance from local level |
| 17 | Treasury URL year-scoped; rate dates misquoted | URL from run year with fallback; dates as returned |
| 18 | `duration` units and meaning | Seconds; listing end; vast.ai quote cited |
| 19 | Backfill with undefined σ; backfill on chart | Gap below `min_prints`; backfill excluded from chart |
| 20 | Production is Vercel | Verify on Vercel |
| 21 | Terms unverified | `access_basis` recorded per source |
