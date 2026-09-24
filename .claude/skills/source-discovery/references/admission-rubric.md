# Admission rubric

Six gates, applied in order. Stop at the first failure and write the row with that reason.
They are ordered by how cheap they are to check, so most rejections cost one page load.

A gate failure is not a verdict on the data. AWS Capacity Blocks carries the best forward
price in the market and is permanently rejected on gate 1.

---

## Gate 1 — reproducible by a stranger

**Can anyone read this price today, through a public page or a public API, with no
relationship to the seller?**

The whole claim of this index is that any reader can recompute a print from public
sources. A public API passes even when it asks for a key. A key that anyone can get the
same way we did doesn't stop a reader checking the number. What does stop them is a
price that exists only for a customer, or only after someone has approved them.

A **public API** here means all of these:

- it is documented by the operator for outside use;
- the key, if there is one, is free and self-service: sign up and receive it, with no
  sales contact, approval, contract or payment details;
- it returns the operator's published price, the same for every caller, and not a price
  negotiated with, or scoped to, the account that asks.

| Verdict | |
|---|---|
| pass | public page, or an open-source package redistributing public catalogs |
| pass | public API with no key |
| pass | public API with a free self-service key (as defined above), for price inputs and reference series alike |
| fail | key issued only after approval, a partnership or a sales conversation |
| fail | key that needs a paying or billable customer account (payment details on file) |
| fail | price scoped to the caller's own account (AWS Capacity Blocks: a signed EC2 action returning offers for the caller) |
| fail | price on a web page behind a login, a quote form, or "contact sales" |

A keyed source adds three duties, and a collector that misses any of them fails this gate:

1. **The key is a repository secret, never a committed value**, and the collector skips
   cleanly with no rows when the secret is absent, as `entsoe.py` does with
   `ENTSOE_TOKEN`. A missing key must never fail the daily run.
2. **The stored row is the evidence.** `raw_json` keeps the fields the price was read
   from, so every print recomputes from the database without calling the API again.
3. **SOURCES.md names the endpoint and how a reader obtains a key**, so "anyone can
   check" is a set of steps and not a claim.

Passing gate 1 settles access and nothing else. An aggregator still has to pass gate 5.

## Gate 2 — the right product

**Is there an on-demand, hourly, per-GPU price for a class in `factors.yaml:model_classes`?**

Check in this order, because each is cheaper than the next:

1. A class the index already prices — H100 SXM, H200, B200, B300, A100, H100 PCIe.
2. On-demand. A six-month commitment is a different product with a different price
   (Gcore's Luxembourg H100 fails here as of 2026-09-07). Reserved and spot are out.
3. Per-GPU or per-node with a **stated, verifiable** GPU count. If the feed exposes no
   GPU-count field, only hand-mapped SKUs may be emitted — the Azure collector's rule.
   A guessed denominator silently corrupts a per-GPU series and nothing downstream
   catches it.
4. Node size ≥ 2 GPUs. The 1-GPU offer carries a measured ~9% small-order premium and is
   excluded rather than normalised.

## Gate 3 — locatable

**Does the price come with a region or country that resolves to a block?**

`normalise.py` drops any row whose country is not in `factors.yaml:eu_eea_countries`, so a
price with no location is unusable for the headline and unusable for every future
regional index too. This is where GPU.ai's index fails: a no-auth JSON API, free to cite,
and no geographic breakdown at all.

"European capacity" in marketing copy is not a location. Map to a specific region string
and then to a country, or the row does not count. If the country falls outside every
block in `config/regions.yaml`, add the block rather than dropping the row.

## Gate 4 — permitted

**Does reading it daily comply with robots.txt and the operator's terms?**

Record the specific basis in `access_basis`, in one sentence, naming what makes it
permissible. "Public API" is not a basis; "the public product-catalog API the console
itself reads" is.

Automatic fails: a web page behind a login, anything robots.txt disallows, any endpoint
whose terms forbid automated access. A public API's free key (gate 1) is not a login,
but its terms of use are read here like any page's. Where a page is hostile but public, the answer is a
static entry with a `last_verified` date, not a cleverer scraper.

## Gate 5 — first-hand

**Is this the operator's own price, or somebody's copy of it?**

Prefer the operator. An aggregator is admissible only when it redistributes public
catalogs transparently and identifiably — `gpuhunt` passes because it is open source, its
catalogs are inspectable, and the provider is named on every row.

An aggregator of aggregators is never admissible. The double-counting is invisible in the
output and unfixable afterwards, and you inherit somebody else's undocumented collection
decisions.

Where a source is also covered first-hand, take the first-hand route. Two sources for one
provider's price is a weighting bug waiting to happen, not extra evidence.

## Gate 6 — will it still be here next quarter

**Judgement call, and the only one on the list. Record it either way.**

- an endpoint the operator's own console depends on is durable; a marketing page is not
- an open-source catalog with contributors outlives a single-maintainer scraper
- a provider whose H100 price has not moved in six months may not be selling H100s
- a source that has already broken once is more likely to break again

Failing gate 6 alone is not a rejection. It is a reason to write the collector
fail-soft — which they all are — and to expect the row.

---

## Recording the outcome

| Outcome | status | Required fields |
|---|---|---|
| passes all six, collector written | `live` | `collector`, `access_basis`, `last_reviewed` |
| passes, blocked on something external | `built` | `recheck` naming the blocker |
| passes, no collector yet | `candidate` | `blocks`, `gpu_classes` |
| fails a gate, could pass later | `watchlist` | `recheck` naming the specific change |
| fails a gate on principle | `rejected` | `reason`, in full sentences |
| was live, is gone | `retired` | `reason` with the date it stopped |

`watchlist` versus `rejected` is the distinction that makes the register worth keeping.
Rejected means the gate failure is structural: AWS Capacity Blocks would need AWS to
publish clearing prices unauthenticated. Watchlist means it is contingent: Gcore would
need to publish an on-demand hourly rate, which is a product decision they could make any
week. Write the `recheck` line specifically enough that the next sweep can answer it with
one page load.
