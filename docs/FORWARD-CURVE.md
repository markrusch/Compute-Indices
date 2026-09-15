# The forward curve — model, methodology and build plan

Working document, 15 September 2026. Revised the same day after a first draft conflated two
different curves. Private, like `ROADMAP.md`: nothing here is copied into a published page.

This is L5's missing half. L2 built the intake (`tci.term`, `term.html`) and L5.3 widened it.
Neither produces a curve.

---

## 1. Three objects, and the discipline of never merging them

The first draft of this document built one curve and gave it two names. The correction is
the most important thing in the file, so it goes first.

| | Symbol | What it is | What it hedges | Buildable today |
|---|---|---|---|---|
| **Committed-cost curve** | `K(m)` | What a named seller charges per GPU-hour to lock `m` months starting today | Your own bill, physically, with that seller | **Yes**, 5 sellers |
| **Implied forward commitment** | `Φ(m1,m2)` | Marginal cost per GPU-hour of the period between two tenors | The extend-or-not decision | **Yes**, derived from `K` |
| **Forward spot curve** | `S*(m)` | Fixed rate on a swap against the TCI spot index | Index-linked exposure, financially | **No.** Section 3 |

`K` and `Φ` are commitment economics. `S*` is a market expectation. Silicon Data's product
page presents its bootstrapped curve as "market expectations for future spot prices", which
is a claim about `S*` made from data that only supports `K`. TCI should not copy that.

The relationship is not an identity, it is an identity plus an unknown:

```
S*(m) = Φ(m) + π(m)
```

`π` is the term premium: the value of the optionality the buyer surrenders, plus
counterparty credit, plus vendor lock-in, minus whatever capacity certainty is worth to
them. `π` is large. It is also, on today's data, unmeasured. Section 5 is how TCI measures
it, and that measurement is the whole product.

---

## 2. The arithmetic, which is the same for all three

Cumulative cost is the primitive. A GPU-hour is a flow: you pay dollars per hour of
delivery, there is no reinvestment, and nothing compounds. So the cost of a contract is rate
times hours, and the cost of two consecutive contracts is the sum of two such products.

```
h(m) = m * HOURS_PER_MONTH          hours delivered by month m
C(m) = h(m) * K(m)                  total cost of locking m months today
K(m) = C(m) / h(m)                  the committed rate, recovered
Φ(m1, m2) = (C(m2) - C(m1)) / (h(m2) - h(m1))
```

`HOURS_PER_MONTH = 730` (365.25 x 24 / 12 = 730.5, rounded; a convention, versioned in
config, not a fact about any month).

This is the simple-rate bootstrap from fixed income with the compounding taken out.
Interpolating on `C` rather than on `K` is what makes it roll-consistent: interpolate a
9-month committed rate halfway between the 6- and 12-month rates and the implied forwards
depend on which knots you happened to pick. Interpolating on `C` cannot.

### No-arbitrage, stated as conditions on C

**NA-1. `C` is non-decreasing**, equivalently every forward is non-negative. If
`C(24) < C(12)`, a buyer takes the 24-month contract, uses 12 months, abandons the tail and
has bought a year of compute below the one-year price. A real trade and a real bound. It
binds when a schedule takes more than half the rate off for a doubling of tenor.

**NA-2. Spot anchors the ratio, not the cost.** `S` is the same-day on-demand price for that
seller and product. It does not enter `C`: the cost of locking zero months is zero whatever
on-demand costs, so `Φ(0, m1)` is simply `K(m1)`, the price of the shortest contract the
seller sells. Spot is the denominator of the ratio `K(m)/S` that gets pooled, and the
ceiling above which a "commitment" is not a discount; a knot at or above it is dropped with
a reason. It is also why no rate is interpolated below the shortest committed tenor.
Interpolating from `C(0) = 0` would quote Azure a three-month reservation at its
twelve-month price, and Azure sells no three-month reservation.

An interpolated rate between two committed tenors is a convention, not a replication price.
A reservation cannot be sold back, so nobody can synthesise a nine-month contract from a
six- and a twelve-month one. NA-1 survives that, because it needs only free disposal.

**NA-3. Roll consistency.** Bootstrapping in any order gives the same `C`. Free, if `C` is
the primitive.

A violation is published and the segment gapped, never repaired. Monotone projection would
produce a full curve and would restate a seller's published price as a number that seller
does not charge.

---

## 3. Price formation: the gate that keeps the curves apart

A term price is only forward-looking if somebody set it while looking forward. Most of
these were not.

Every term observation gets a `price_formation` tag, and the tag decides which curve it may
enter:

| Tag | Meaning | May build `K` | May build `S*` |
|---|---|---|---|
| `administered_uniform` | One schedule applied across chip generations | Yes | **No** |
| `administered_differentiated` | Rate card that varies by chip | Yes | **No** |
| `administered_untested` | Fewer than two chips priced, so the test cannot run | Yes | **No** |
| `market_quoted` | Broker or OTC quote for a specific chip and tenor | Yes | Yes |
| `transacted` | A deal that was actually done | Yes | Yes |

**The three administered tags are assigned automatically, not by hand.** If a seller's ratio
`K(m)/S` is identical across GPU generations at every tenor, its curve contains no
chip-specific information by construction. Run against the database on 15 September:

```
                12mo ratio, by chip                              chip-invariant?
verda      0.9200 on all six: A100, A100-40GB, B200, B300,
           H100, H200                                            YES
latitude   0.3500 on B300, H100 and RTX_PRO_6000                 YES
azure      0.5486 (H200) .. 0.7500 (H100_PCIE)                   no
civo       0.8165 (A100_40GB) .. 0.9140 (H200)                   no
```

A Blackwell B300 and a workstation RTX PRO 6000 cannot share a forward price path.
Latitude's schedule is a commercial policy and Verda's is literally a percentage table read
out of `config/term_schedules.yaml`. Bootstrapping either produces today's spot times a
constant, which carries no information a reader does not already have from the spot index.

That test is cheap, automatic, and publishable as a finding in its own right. It is also
the CI gate: a source tagged `market_quoted` that turns out to be chip-invariant fails the
check and is re-tagged.

**On 15 September, TCI holds zero `market_quoted` or `transacted` term prices.** Every one
of the five sellers is administered. The vast.ai offer book was the one plausible source of
market-formed term prices and it is not: `discounted_dph_total` equals `dph_total` on all
169 stored offers, because the host's reserved discount is applied at reservation time and
never appears in the search feed. Hosts do advertise contracts to 1,205 days, with no price
attached.

**So `S*` does not publish today, and the honest design says so structurally rather than in
a footnote.** The rail exists, it is empty, and the page counts what would fill it.

---

## 4. The curves that do publish

### 4.1 Per-seller committed-cost curves

One curve per `(date, provider, gpu_model, currency)`, from what `tci.term.seller_terms`
already returns; OVHcloud quotes some GPUs in EUR and in USD, and a curve is never
converted. Knots are `m = 0` at the seller's own on-demand price plus one knot per observed
tenor at `S * ratio(m)`. On today's database:

```
azure   H100_SXM   spot $14.3793          civo   H100_SXM   spot $2.9900
  K(12) = $9.2028   Φ( 0,12) = $9.2028      K( 6) = $2.7900   Φ( 0, 6) = $2.7900
  K(36) = $6.3125   Φ(12,36) = $4.8674      K(12) = $2.6901   Φ( 6,12) = $2.5902
  K(60) = $5.7517   Φ(36,60) = $4.9105      K(24) = $2.5899   Φ(12,24) = $2.4898
                                            K(36) = $2.4901   Φ(24,36) = $2.2903
```

Both pass NA-1. Azure's forward turns up at the long end, $4.91 against $4.87, which is
real rather than a fitting artefact because flat-forward bootstrapping introduces nothing
between knots. The level gap is 4.8x on the same day for nominally the same chip, which is
the bimodality the panel notes already document and the reason nothing here pools
hyperscaler with neocloud.

Per-seller curves carry no minimum-seller floor. A rate card is that seller's published
fact. They are also not restricted to the panel, which governs admission to a *print*.

**Interpolation: flat forward.** Linear on `C`, so every published forward is the
arithmetic consequence of exactly two observed prices. Monotone convex and PCHIP both give
a prettier curve and both are defensible in rates, where you have twenty knots; the H100
panel has at most four, and a global fit means yesterday's 6-month forward moves because a
36-month price changed. Flat-forward is local.

**No extrapolation.** The curve ends at the longest observed tenor. Azure's H100 runs to 60
months and OVHcloud's H100 PCIe runs to 1, because that is where each seller's published
prices stop. A curve that ends early is information. Segments wider than
`max_span_months` publish, flagged `wide_span`, carrying the two knots behind them.

### 4.2 The aggregate committed-cost curve

Pool the **shape**, not the level:

```
K_agg(m) = I_spot * r(m)
```

`I_spot` is the published TCI headline for that class and date; `r(m)` is the cross-seller
median of the ratio `K_p(m) / S_p`.

Pooling levels breaks the moment the seller mix differs by tenor, which it does: 12 months
has Azure, Civo, Latitude and Verda; 24 months has Civo and Verda. The level would fall
from 12 to 24 months because Azure left, and the curve would read that as a term discount
(`research/composition-vs-price.md` is the note on exactly this failure). Ratios are
within-seller, so a seller entering or leaving changes who votes on the shape and cannot
move the level.

Consequences, all wanted: if the headline gaps the aggregate gaps, on one gap rule; L4
quality adjustment touches the level only; the pooled leg inherits the index's panel, so
Civo contributes to its own curve and not to the aggregate; pooling is per segment.

**The floor.** `r(m)` publishes at three sellers in one segment or not at all. Today no
tenor of any GPU clears it: H100 SXM at 12 months has Azure alone in hyperscaler and no admitted seller in neocloud:
Civo and Latitude are off the panel, Verda's knots come from a published schedule rather
than a panel source, and OVHcloud enters with v0.5.0 on 22 September. The aggregate does not publish on day one, and the page says so with the counts.
That makes the feature ship useful immediately on per-seller curves, with a countable gauge
on what L5.1 is for.

**Arbitrage after pooling.** With the same sellers at both tenors the median cannot break
NA-1: when every seller has `24 x r(24) >= 12 x r(12)`, each order statistic of `r(24)`
dominates the same order statistic of `r(12)/2`, the median included. A pooled violation
can only come from different sellers voting at different tenors. So re-check NA-1 after
pooling, gap the segment, name the composition change as the reason, and never project.

---

## 5. Making it hedgeable, which is the point

A curve that cannot be traded against is a chart. Three things separate them, and TCI can
build all three.

### 5.1 The physical hedge works today

If the exposure is your own on-demand bill, the committed-cost curve *is* the hedge
instrument and the decision is already well posed: locking 12 months of Azure H100 SXM at
$9.2028 against rolling at $14.3793 fixes your cost for a year. `Φ(12,36) = $4.8674` is
what extending that lock by two more years costs per GPU-hour for those two years.

Both are decision-useful without any claim about future spot, and both are transactable
today by placing an order with that seller. What they carry is seller lock-in, utilisation
risk on every unused hour, and counterparty credit.

So `K` and `Φ` should be framed on the page as **procurement** numbers, and the decision
rule stated in the direction it actually runs: locking beats rolling if realised spot
averages above `Φ`, and `Φ` is biased *low* as a forecast of spot by the value of the
optionality surrendered. A reader who takes `Φ(12,36) = $4.87` against $14.38 spot as a
forecast of a 66% decline has been misled, and TCI will have misled them.

### 5.2 The financial hedge needs `S*`, and `S*` needs the premium measured

To strike a swap — pay fixed, receive the TCI H100 index monthly — you need `S*`, and `S*`
needs `π`. There is no listed market to read `π` from and no quoted term price in the
database to infer it from.

**But TCI has the one asset that lets it be measured directly: an independent daily spot
index with a published, recomputable history.** Nobody holding only rate cards can do this.

The construction is a ledger, and it starts paying quickly:

1. Every session, store the full committed-cost curve for every seller and chip. Already
   implied by C4 below; it is the same write.
2. For every past strike date `d` and tenor `m` where `d + m` has now elapsed, compute the
   **realised roll cost**: the hour-weighted mean of the published spot index over
   `[d, d+m]`.
3. `π̂(m) = realised roll cost − Φ(m) struck at d`. Averaged over strike dates, that is a
   direct ex-post estimate of the term premium at tenor `m`.
4. `S*(m) = Φ(m) + π̂(m)`, published with the strike count and the dispersion behind `π̂`,
   and gapped until that count clears a floor.

The first 1-month reading arrives about 30 days after the ledger starts. The first 3-month
reading lands in mid-December. Twelve months is a 2027 number. That is slow, and it is the
only honest route from a rate card to a forward, and the clock does not start until the
ledger does — which is the argument for building C4 before C5.

It is also the thing a lender or a desk cannot get anywhere else, and it compounds: every
session makes the estimate better and the moat deeper.

### 5.3 What the product has to expose for any of it to be usable

Not a curve viewer. A position surface:

- **Roll versus lock, priced.** "You use N GPU-hours a month of H100 SXM. Rolling at
  today's index costs $X a month. Locking 12 months at seller P costs $Y. Break-even index
  level $Z." Every input visible, every number recomputable.
- **Mark to market.** A lock struck on date `d` revalued against today's curve, so an
  existing commitment has a current value rather than a historical price.
- **Realised roll versus lock**, for every window that has closed. Section 5.2's ledger,
  shown as a record and not a model.
- **Hedge effectiveness.** Seller `P`'s committed price against the TCI index: the basis,
  and what is left unhedged. A lock at one seller does not hedge index exposure one for one
  and the page must not imply it does.

### 5.4 The wall in front of all of this

`GOVERNANCE.md` and the site both state that TCI is not administered as a benchmark under
EU Regulation 2016/1011 and must not be used as a reference price in a financial
instrument. Physical procurement hedging is unaffected and can ship. **A swap or a future
settling on a TCI print is forbidden by TCI's own published posture**, so §5.2's `S*` is
research and a negotiating reference until that posture changes, which is an L6 decision
with real cost attached. Worth deciding deliberately rather than discovering when somebody
asks to settle on it.

---

## 6. Named, deferred, parameterised

Each gets a config parameter at the neutral value, so turning it on is a version bump.

**Payment timing.** `C(m)` treats every contract as paid as delivered. Azure's reservations
are prepaid. At a 4% discount rate a three-year prepayment is worth roughly 6% of the
contract, larger than most differences this curve is built to measure. The fix is to make
`C(m)` a present value of the payment stream. `forward_curve.discount_rate: 0.0`, with the
rationale that TCI does not know the payment structure for most rows and an intent to
record it in `raw_json` where the collector can see it. Largest known error in the model;
name it on the methodology page.

**Counterparty credit.** A 36-month lock with a neocloud is not the instrument a 36-month
lock with Microsoft is. Part of why Civo's 36-month discount is 17% and Azure's 56% is that
Civo cannot sell three years of certainty as cheaply. No adjustment; the segment split is
the crude proxy.

**Utilisation.** A committed rate assumes every hour is used. At 60% utilisation the
effective rate is well above the curve. Out of scope for a price benchmark, in scope for
§5.3's calculator, where the user supplies it.

**Zero-discount "commitments".** OVHcloud's H200 monthly plan prices at exactly 1.0000 of
on-demand. That is the same product billed monthly, not a term price. Ratio `== 1.0` should
be excluded with its own reason rather than admitted as a knot.

---

## 7. Architecture

### 7.1 The module

`src/tci/curve.py`. Pure functions, no I/O, no database, no config loading, the same
posture as `tci/term.py` and for the same reason.

```
CurveKey     gpu_model, provider|None, segment|None, region_block='EU'
Knot         tenor_months, rate, price_formation, observed; hours and cum_cost derived
Curve        key, date, currency, knots, kind: 'committed' | 'forward_spot', dropped
Forward      m1, m2, rate, span_months, wide_span
Violation    m1, m2, reason, detail

classify(ratios_by_model)       -> price_formation     chip-invariance test, one seller
build(key, date, spot, points)  -> Curve | None        refuses S* from administered prices
forwards(curve)                 -> (forwards, violations)
rate_at(curve, m)               -> float | None        None before the first committed
                                                       tenor and after the last
pool(curves)                    -> list[PooledPoint]   shape only, min_providers gate
apply_level(points, level, ...) -> Curve | None
premium(ledger, index)          -> not built; C7
```

`Curve.kind` is the type-level guard against the mistake this document exists to correct:
`apply_level` and `premium` refuse a curve of the wrong kind, and a `forward_spot` curve
cannot be built from a knot tagged `administered_*`. A test asserts both.

### 7.2 Config

Built for now as module constants in `tci/curve.py`, as `tci.term`'s are, until open
question 3 is answered. The intended home is a new block in `config/factors.yaml`,
therefore inside `METHODOLOGY.lock`:

```yaml
forward_curve:
  hours_per_month: 730
  interpolation: flat_forward
  min_providers: 3              # pooled curves; per-seller curves have no floor
  max_span_months: 6            # wider segments publish, flagged wide_span
  max_tenor_months: 60          # never extrapolate past the last observed knot
  discount_rate: 0.0            # payment timing not modelled; see section 6
  min_strikes_for_premium: 20   # below this, pi-hat is reported and S* gaps
  segments: [hyperscaler, neocloud, marketplace]
```

### 7.3 Storage

No new table for `K` or `Φ`: `term_tables` already rebuilds any date from stored
observations by a fixed rule, so curve history is derivable and `write_curve` loops dates
as `write_term` does. Output `site/data/curve/latest.json` and `history.csv`, one row per
`(date, key, tenor)`.

§5.2's ledger is the exception and does need to persist, because a realised roll cost is a
statement about a strike that existed on a past date. It is append-only like everything
else, and it is derivable from `history.csv` plus the index history, so it can start as a
derived file and become a table when it earns one.

### 7.4 Position in the pipeline

```
observations -> term.seller_terms -> curve.classify -> curve.build -> curve.forwards
                                                            |
                                                            +-> curve.pool -> apply_level
                                                            |                      ^
                                                            |            daily_index
                                                            +-> ledger -> curve.premium -> S*
                                                                              ^
                                                                    index history
```

Off to the side of the calculation path, like `term.py` and `sources.py`. It reads prints;
no print reads it. `tests/test_reproduce.py` is untouched by anything here, which is what
makes it safe to build fast.

### 7.5 Scaling

| Dimension | Today | Cost to extend |
|---|---|---|
| GPU class | ~12 with term data | Free; keyed by `gpu_model` |
| Seller | 5, all administered | Free |
| Tenor | 1, 3, 6, 12, 24, 36, 60 | One entry in `term.TENOR_MONTHS` |
| Region / leg | EU only | A `CurveKey` field that already exists |
| Quoted / transacted prices | none | A `price_formation` value; unlocks `S*` |
| Quality adjustment | none | Level only; shape untouched |

---

## 8. The UI

Constraints from `CLAUDE.md` and `DESIGN.md`, none negotiable: no external request, no
script `src`, every number baked in at build time, operable with scripting disabled, one
dark theme, hand-rolled inline SVG, greyscale by default with an eight-slot categorical
palette assigned per entity and never cycled, no dual axis, no area fill under a truncated
baseline, a table twin for every chart, filter row above what it scopes.

**Pages, not query strings.** One generated page per GPU class with at least one curve:
`curve.html` is the index, `curve/h100-sxm.html` and siblings are the charts. The class
selector is `<a>` links, so it works with scripting off and each class is separately
linkable. Twelve pages today, generated only where data exists.

**Filtering without JavaScript.** Every seller's line is rendered into the SVG at build
time; checkboxes and sibling selectors hide and show them
(`#p-azure:checked ~ .plot [data-provider="azure"]`). Already the house pattern for the
hover layers and the mobile nav, with a property worth keeping: the y-axis does not rescale
on filter, so a seller's line stays where it was. The view toggle is radios driving
pre-rendered `<g>` groups. JS adds the crosshair and nothing the page needs.

**The three views, labelled as three different things.** Committed cost `K(m)` as a line
per seller, filled 8px ringed markers at observed knots so observed and interpolated are
distinguishable without the legend. Implied forward commitment `Φ` as a **stair**, which
self-documents that the value is flat between knots and makes a wide span visibly wide.
Cumulative `C(m)` as monotone lines where an NA-1 violation is a visible down-step. No fill
under any of them; all three have truncated axes. Forward spot `S*`, when it exists, is a
**fourth view on its own axis label and its own colour**, never drawn on the same axes as
`Φ`. The tab is present and disabled until §5.2 clears, reading "needs quoted prices" with
the count.

**The seller badge carries price formation.** Verda and Latitude show `administered,
chip-invariant` beside their names. A reader should be able to see, without opening the
methodology, that those two curves are rate cards.

**The arithmetic strip**, for the focused segment, with the real numbers:

```
Φ(12,36) = (36 x 730 x $6.3125  -  12 x 730 x $9.2028) / ((36 - 12) x 730)
         = $4.8674 / GPU-hour     azure . H100_SXM . 2026-09-15
         administered rate card - a commitment price, not a spot forecast
```

**The calculator** of §5.3 sits below the chart: usage in GPU-hours a month, roll versus
lock at every tenor and seller, break-even index level. It needs JS for the input, so the
no-JS fallback is the same table at three fixed usage levels.

**The coverage panel** carries the count behind every pooled cell, the tenors one seller
short, excluded pairs with reasons, NA violations, stale schedules past `max_age_days`, and
the `market_quoted` count that currently reads zero.

**The table twin** holds every number in the SVG, before it in source order.

---

## 9. Build order

Nothing here touches the calculation path, so `reproduce --published` passes throughout.

**C1 — `src/tci/curve.py` and tests.** Accept: Azure H100 SXM on 2026-09-15 reproduces
`Φ(12,36) = $4.8674` and `Φ(36,60) = $4.9105` from a hand-worked fixture; `classify` tags
Verda and Latitude `administered_uniform` and Azure and Civo `administered_differentiated`
from the database; building a `forward_spot` curve from an `administered_*` knot raises;
a non-monotone `C` yields a violation and no forward; `mypy` strict on the module.

**C2 — `forward_curve` block in `factors.yaml`, lock regenerated.** Accept:
`python -m tci.run docs` regenerates `METHODOLOGY.lock`; the methodology page states the
three objects of section 1 separately, the `S* = Φ + π` bridge, the price-formation gate,
and the extrapolation refusal.

**C3 — `python -m tci.run curve [--date D] [--gpu G] [--provider P]`.** Accept: prints
every curve for a date with knots, forwards, spans, formation tags and violations, and
reports pooled cells publishing versus one seller short.

**C4 — `site/data/curve/*.json` and `history.csv`.** §5.2's ledger is this file. Its clock
is already running: term observations have been stored daily since 11 September and every
curve rebuilds from them, so C4 publishes the strike history rather than starting it. One
exception: Verda's knots come from `config/term_schedules.yaml`, and a past date is rebuilt
against today's copy of that file, not the one live on that date. Verda is chip-invariant
and never reaches `S*`, so this costs the ledger nothing, but it is the same defect
`write_term` already has. Accept: a past date's curve rebuilds from stored observations and
matches the file; the JSON carries `observed` and `price_formation` per knot and
`span_months` per forward.

**C5 — the pages.** Accept: charts render and filter with scripting disabled;
`test_pages_are_self_contained` passes; the table twin carries every number in the SVG; the
`S*` tab is present, disabled and counting; layout holds in a 390px iframe (not a 390px
window — headless Chrome clamps to ~485px and gives a cropped desktop render that looks
like a broken phone).

**C6 — the calculator and mark-to-market.** Accept: roll-versus-lock reproduces by hand
from the published JSON; a no-JS fallback table exists at three usage levels.

**C7 — `π̂` and `S*`, when strikes clear the floor.** First 1-month reading roughly 30 days
after C4 lands. Accept: `S*` gaps below `min_strikes_for_premium` and says so with the
count; the published `π̂` carries its strike count and dispersion.

---

## 10. Open questions for Mark

1. **Does `S*` belong in this repo at all, given §5.4?** The site says TCI must not be a
   reference price in a financial instrument. A forward spot curve is built to be exactly
   that. Options: build it as research with the posture unchanged, or treat the posture as
   the L6 decision it is and sequence it deliberately.
2. **Series naming for the aggregate.** `TCI-K-H100-NEOCLOUD` / `TCI-FW-H100-NEOCLOUD`
   published against `EU-K-*` / `EU-FW-*` stored, following the existing two-name split? Or
   fold tenor into the CRI key as `EU-CRI-H100-SXM-12M`? The first reads better and keeps
   the three objects visibly distinct; the second reuses machinery.
3. **Inside `METHODOLOGY.lock`, or outside like `tci.term`?** I lean inside, because
   `hours_per_month: 730` is exactly the convention that gets changed quietly.
4. **Payment timing.** An L5.5 to record payment structure in `raw_json` where the
   collector can see it, or a stated limitation until a contributor asks?
5. **Is chasing `market_quoted` prices now the top L5 priority?** It is the only thing that
   unlocks `S*` on a horizon shorter than §5.2's ledger, and it re-points the contributor
   funnel from "more sellers" to "brokers and buyers who will share a struck price".
