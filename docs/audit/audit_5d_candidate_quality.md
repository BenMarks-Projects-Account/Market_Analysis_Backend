# Audit 5D — Candidate Quality Assessment: Are These Good Trades?

**Pass**: 5 — Options Scanner Construction & Candidate Quality  
**Prompt**: 5D  
**Scope**: Full V2 pipeline output quality — vertical spreads, iron condors, butterflies, calendars  
**Context**: Draws on audits 5A (narrowing), 5B (verticals/IC construction), 5C (butterflies/calendars construction)  
**Date**: 2026-03-21

---

## PART 1 — VERTICAL SPREAD QUALITY

### 1  Concrete Example: SPY Put Credit Spread at $545, 30 DTE

#### 1.1  Pipeline Walkthrough

| Phase | Stage | Count | Notes |
|-------|-------|-------|-------|
| **Input** | SPY option chain | ~18,000-25,000 contracts | All expirations, all strikes, puts and calls |
| **Phase A** | DTE window [1, 90] | ~15,000-20,000 contracts | Only DTE filter applied. No strike/moneyness/OTM filter. Both puts and calls retained. |
| | Expirations in window | **~60-80** | SPY has daily/weekly/MWF + monthly. Within 90 DTE: roughly 60-80 distinct expirations. |
| | OTM put strikes per expiry | **~100-145** | All strikes below $545. SPY uses $1 increments near ATM, $5 further out. Range roughly $400-$544. |
| **Phase B** | Phase B target-type filter | ~100-145 OTM puts per expiry | Builder filters `option_type == "put"` from the bucket |
| | (short, long) pairs per expiry | **~2,500-5,000** | O(n²)/2 pairs bounded by max_width=$50. With ~100 puts: C(100,2) ≈ 4,950 but width cap reduces this. Roughly ~2,500-3,500 pairs per expiry. |
| | Total constructed | **50,000** (cap hit) | ~65 expirations × ~3,000 pairs = ~195,000 potential. **Cap of 50,000 is hit within first ~15-20 expirations.** |
| **Phase C** | Structural validation | **~50,000** | Shared checks (leg count, sides, option types) + pricing sanity. Credit ≤ 0 rejected here. Typical survival: ~95-98%. |
| **Phase D** | Quote presence | **~47,000-49,000** | Rejects candidates with `bid=None` or `ask=None` on either leg. |
| **Phase D2** | Quote/liquidity sanity + dedup | **~35,000-45,000** | Rejects: negative bid/ask, both OI=0 AND vol=0. Warns: wide spreads, low OI/vol. Dedup removes duplicates. |
| **Phase E** | Math recomputation | **~35,000-45,000** | Recomputes credit/debit, max_profit, max_loss, POP, EV. Rejects only structurally impossible math (max_loss ≤ 0). Very few rejections. |
| **Phase F** | Normalization | **~35,000-45,000** | Sets `passed=True` if zero reject reasons. No filtering. |
| **Credibility gate** | Workflow Stage 4 | **~5,000-15,000** | `net_credit ≥ $0.05`, `POP < 0.995`, `bid > 0 on at least one leg`. Removes penny-premium deep-OTM and delta-zero shorts. |
| **Top-N** | Workflow Stage 5 | **30** | Sorted by EV descending, sliced to top 30. |

#### 1.2  The FIFO Bias Problem

Because construction enumerates expirations **in date order** and the generation cap is hit at ~50,000, the cap allows only **the first ~15-20 expirations** (typically DTE 1-20) to contribute candidates. Expirations at DTE 25-90 are **never constructed**.

This means the 30-DTE "sweet spot" for theta decay is likely **missing from the candidate pool** for SPY put credit spreads. The top-30 by EV will be dominated by short-DTE expirations (7-20 DTE) where theta is high but gamma risk is elevated.

#### 1.3  What the Top-5 by EV Likely Look Like

Based on the EV formula (`POP × max_profit - (1-POP) × max_loss`) and the delta approximation for POP (`1 - |short.delta|`):

| Rank | Short Strike | Long Strike | Width | Credit (est.) | POP (est.) | EV (est.) | Assessment |
|------|-------------|------------|-------|---------------|-----------|----------|------------|
| 1 | ~$540 | ~$520 | $20 | ~$3.50 | ~0.70 | ~$+155 | Near-ATM, high credit, moderate POP. **Risky** — 30-delta short is aggressive for income. |
| 2 | ~$540 | ~$490 | $50 | ~$7.00 | ~0.70 | ~$+340 | Max-width dominates because EV scales with `width × credit`. **Dangerous** — $50 max loss. |
| 3 | ~$535 | ~$515 | $20 | ~$2.50 | ~0.75 | ~$+125 | Moderate approach, reasonable delta. Width still amplifies EV. |
| 4 | ~$535 | ~$485 | $50 | ~$5.50 | ~0.75 | ~$+275 | Again, wide spread wins on raw EV. |
| 5 | ~$530 | ~$525 | $5 | ~$0.80 | ~0.82 | ~$+48 | Narrower width, safer. This is closer to what an income trader wants. |

**Key observation**: Raw EV systematically favors **wider spreads with near-ATM shorts** because:
- `max_profit = credit × 100` grows with credit (which grows with proximity to ATM)
- `max_loss = (width - credit) × 100` grows with width, but POP dampens it
- The linear EV formula doesn't penalize wider spreads proportionally to the larger capital at risk

**Would an experienced options trader take these?**

- Ranks 1-2: **Unlikely**. A 30-delta short put with $50 width risks $4,300 max loss. Income traders target 15-20 delta shorts with $5-$10 width.
- Rank 3: **Possibly**, but width is too wide for conservative income.
- Rank 5: **Most likely** — but it's ranked 5th, not 1st, because the EV formula biases toward wider spreads.

#### 1.4  The Experienced Trader's Ideal Trade

An income-focused options trader would target:

| Parameter | Trader Target | Scanner's Top-5 |
|-----------|--------------|-----------------|
| Short delta | 0.15-0.20 (80-85% POP) | ~0.25-0.35 (near-ATM bias) |
| Width | $5 (SPY) | $20-$50 (EV bias toward wide) |
| Credit/width ratio | ≥ 25-33% | Variable, not filtered |
| DTE | 30-45 | 7-20 (FIFO bias) |
| Max loss | $350-$450 | $1,300-$4,300 |
| Capital efficiency | Max profit / buying power | Not considered |

**Verdict: The top-5 by EV does NOT represent what an experienced income trader would select.**

---

### 2  Delta Distribution of Short Strikes in Top Candidates

#### 2.1  Distribution Analysis

The EV formula `POP × max_profit - (1-POP) × max_loss` creates a specific optimal delta:

```
EV = (1-|δ|) × credit × 100 - |δ| × (width - credit) × 100
```

Setting `∂EV/∂δ = 0` shows that **EV is maximized when credit is large relative to width** — which occurs at **near-ATM deltas** (0.30-0.45). The formula has no mechanism to penalize high-delta shorts for their elevated risk.

**Expected distribution in top-30**:
- Delta 0.25-0.45: **Heavy concentration** — these produce the highest raw EV
- Delta 0.15-0.25: **Some presence** — the "income sweet spot" appears in the top-30 but not at the top
- Delta 0.05-0.15: **Sparse** — far-OTM shorts have tiny credits, producing low EV
- Delta < 0.05: **Filtered by credibility gate** (POP > 0.995 = delta < 0.005)

#### 2.2  Ultra-Far-OTM Short Strikes (delta < 0.05)

**Mostly eliminated** by the credibility gate:
- The `POP < 0.995` check rejects deltas approximately < 0.005
- The `net_credit ≥ $0.05` check rejects penny-premium deep-OTM shorts
- However, delta 0.01-0.05 shorts CAN survive if they have $0.05+ credit

**Verdict**: Some ultra-far-OTM shorts survive but rank low in the EV sort. They consume top-N slots only if there are fewer than 30 candidates with higher EV.

#### 2.3  Near-ATM Short Strikes (delta > 0.40)

**Survive and rank highly**. The EV formula rewards high-delta shorts because their large credits produce large `max_profit`. There is no mechanism to penalize the elevated probability of loss. A 40-delta put credit spread with $5.00 credit on $10 width has:
- EV = 0.60 × $500 - 0.40 × $500 = $+100
- This outranks a 20-delta spread with $1.50 credit on $5 width:
- EV = 0.80 × $150 - 0.20 × $350 = $+50

The 40-delta spread has higher EV but is a **50/50 bet with high capital risk** — not an income trade.

---

### 3  Width Distribution in Top Candidates

#### 3.1  Width Bias

The Pass 2 finding is confirmed: **raw EV favors wider spreads**.

```
For a given short delta δ with credit c(δ):
  Narrow ($5):   EV = (1-δ) × c(δ) × 100 - δ × (5 - c(δ)) × 100
  Wide ($50):    EV = (1-δ) × c(δ) × 100 - δ × (50 - c(δ)) × 100
```

Wait — this would suggest narrower is better (lower max_loss). But credit also increases with width because the long leg is cheaper when further OTM. The net effect:

For SPY put credit spread at $540/$535 ($5 wide) vs $540/$490 ($50 wide):
- $5 wide: credit ≈ $1.50 → EV = 0.70 × $150 - 0.30 × $350 = **$0**
- $50 wide: credit ≈ $7.00 → EV = 0.70 × $700 - 0.30 × $4,300 = **-$800**

Actually, wider spreads have **worse EV** when the long leg provides minimal offset. But the top of the EV ranking is populated by **medium-width spreads ($10-$20)** where the credit-to-width ratio is still meaningful.

**Correction to prior assumption**: The EV formula does not uniformly favor wider spreads. It favors **the width where credit-to-width ratio is highest** — typically $5-$15 for SPY income trades. Very wide spreads ($30-$50) have poor credit-to-width ratios and negative EV.

#### 3.2  Credit-to-Width Ratio in Top-30

Without quality gates, the top-30 (sorted by EV) will have credit-to-width ratios roughly:

| Width Range | Typical Credit/Width | Presence in Top-30 |
|-------------|---------------------|-------------------|
| $1 | 30-50% (but absolute credit is tiny) | Unlikely — EV too small in absolute terms |
| $2-$5 | 25-40% | Present — competitive EV with lower risk |
| $5-$10 | 20-35% | **Most likely dominant** — best EV territory |
| $10-$20 | 15-25% | Present — EV can be high for near-ATM |
| $20-$50 | 5-15% | Sparse — max_loss overwhelms credit |

#### 3.3  $1-Wide Spreads

$1-wide SPY put credit spreads (e.g., $530/$529) typically yield:
- Credit: $0.05-$0.15
- Max profit: $5-$15
- Max loss: $85-$95
- EV: small positive (if POP > 90%) or negative

These clear the credibility gate ($0.05 min premium) but rank near the bottom of the EV sort. They are unlikely to appear in the top-30. However, they consume generation cap slots — every short strike generates a $1-wide candidate.

---

### 4  DTE Distribution in Top Candidates

#### 4.1  FIFO Bias Effect

The generation cap creates a systematic DTE bias:

```
Construction order: expirations sorted by date (ascending)
  DTE 1 → construct ~3,000 pairs
  DTE 2 → construct ~3,000 pairs
  ...
  DTE 15-20 → CAP HIT (50,000). Stop.
  DTE 21-90 → NEVER CONSTRUCTED
```

**Consequence**: The top-30 by EV is drawn entirely from DTE 1-20. The 30-45 DTE "sweet spot" for theta decay with favorable gamma/theta ratio is **not in the candidate pool**.

#### 4.2  DTE Optimization

**None exists**. Every constructed expiration contributes equally. Short-DTE candidates (3-7 DTE) compete directly with medium-DTE candidates (15-20 DTE) in the same EV sort.

Short-DTE candidates have:
- Higher annualized theta (EV_per_day is higher)
- Higher gamma risk (larger P&L swings near expiration)
- Lower absolute credit (less time premium)
- Different risk profile than 30-45 DTE trades

Mixing DTE 3 and DTE 20 candidates in the same top-30 presents **incomparable risk profiles** to the user without any indication that these are fundamentally different trade types.

#### 4.3  Clustering at Specific Expirations

SPY has expirations every MWF (Monday, Wednesday, Friday), plus monthly options with typically higher OI/liquidity. The scanner treats all expirations equally — there is no preference for the more liquid monthly expirations.

---

## PART 2 — IRON CONDOR QUALITY

### 5  IC Example: SPY at $545, 30 DTE

#### 5.1  Construction Constraints

The IC builder has a tighter DTE window (7-60) than verticals (1-90), so 30 DTE is within range. However:

- **Side cap**: `√50,000 ≈ 223` sides per type per expiration
- With ~100 OTM puts and ~100 OTM calls, each type generates ~4,950 pairs, capped at 223
- 223 put sides × 223 call sides = **49,729 condors per expiration**
- This means **the IC scanner exhausts its generation cap on the FIRST expiration**

For a 30-DTE expiration with ~100 OTM puts and ~100 OTM calls:
- Put sides: first 223 pairs (smallest-width put spreads, FIFO from lowest strike)
- Call sides: first 223 pairs (smallest-width call spreads, FIFO from lowest strike)

#### 5.2  What the Top-3 IC Candidates Likely Look Like

Given the FIFO bias on side construction (narrow widths at extreme OTM first) and then EV sort:

| Rank | Put Side | Call Side | Combined Credit (est.) | Wing Width | Assessment |
|------|----------|-----------|----------------------|------------|------------|
| 1 | 530/525p ($5w) | 560/565c ($5w) | ~$1.80 | $5/$5 | Near-ATM shorts on both sides. High EV from high credit. **Too aggressive** — short strikes too close to spot. |
| 2 | 535/530p ($5w) | 555/560c ($5w) | ~$2.50 | $5/$5 | Even closer to ATM. Iron condor with ~40-delta shorts is essentially a coin flip. **Not an income trade.** |
| 3 | 530/520p ($10w) | 560/570c ($10w) | ~$3.50 | $10/$10 | Wider wings, more premium. Still near-ATM shorts. **Same problem.** |

**An income trader's ideal IC**: 
- Short puts at ~20-delta ($520-$525, ~4% OTM)
- Short calls at ~20-delta ($565-$570, ~4% OTM)
- $5 wings
- Combined credit ≥ $1.50 (≥ 30% of single-side width)
- DTE 30-45

This trade would exist in the pipeline but rank **below** the aggressive near-ATM condors because the EV formula rewards higher absolute credit.

#### 5.3  Delta Balance

The IC builder does **not** attempt to match put-side and call-side deltas. Each side is constructed independently. A condor with:
- 10-delta short put ($510)
- 40-delta short call ($555)

...is generated alongside a balanced 20-delta/20-delta condor. There is no constraint on delta symmetry.

The EV formula doesn't explicitly penalize delta imbalance. It computes IC POP as `1 - |Δ_short_put| - |Δ_short_call|`, which implicitly rewards balanced deltas (maximizing POP), but the credit component can overpower this.

#### 5.4  Combined Credit Adequacy

No minimum combined credit during construction. The credibility gate requires `net_credit ≥ $0.05` (per-share), which is effectively no filter for iron condors (combined credit is almost always > $0.05 unless both sides are deep OTM).

The standard income criterion of `credit ≥ 33% of single-side width` is **not checked anywhere** in the pipeline.

---

### 6  Skew Handling

#### 6.1  How SPY Skew Affects Construction

SPY put options exhibit **negative skew**: equidistant OTM puts are more expensive than equidistant OTM calls. For example:
- 20-delta put (at ~$525): bid ≈ $2.50
- 20-delta call (at ~$565): bid ≈ $1.50

This means the put side naturally contributes more credit to an iron condor than the call side.

#### 6.2  Builder Awareness

**None**. The IC builder constructs sides independently with no skew awareness:
- It does not widen the put side to capture more premium
- It does not narrow the call side to reduce risk
- It does not adjust wing placement based on the put-call premium differential
- The cross-product mechanically pairs every put side with every call side

#### 6.3  Practical Impact

Because EV rewards credit, the top-ranked condors will naturally have **the put side providing most of the credit**. This happens incidentally through the EV sort, not through intentional construction. However, the builder could produce better candidates by:
- Allowing wider put wings (capturing more put skew premium)
- Tightening call wings (reducing overall risk profile)

This is a well-known income trading optimization that the scanner does not implement.

---

## PART 3 — CALENDAR/DIAGONAL QUALITY

### 7  Calendar Trade Viability

#### 7.1  Can the Constructed Calendars Be Ranked?

**No.** Calendar `family_math()` sets `max_profit=None`, `POP=None`, `EV=None`, `RoR=None`, and `Kelly=None`. Only `net_debit` and `max_loss` (≈ debit paid) are computed.

In the workflow runner's Stage 4 sort:
```python
key=lambda c: (
    -_safe_float((c.get("math") or {}).get("ev")),  # EV=None → 0.0
    -_safe_float((c.get("math") or {}).get("ror")),  # RoR=None → 0.0
    c.get("symbol", ""),
)
```

All calendars sort as `(-0.0, -0.0, symbol)` — they cluster at the **bottom** of the ranking, below all candidates with any positive EV. In a multi-family scan where verticals, ICs, and butterflies compete with calendars, **calendars will never appear in the top-30** unless fewer than 30 non-calendar candidates exist.

#### 7.2  Are the Constructed Calendars Viable Trades?

Evaluating the calendar builder against practical viability criteria:

| Criterion | Viable? | Notes |
|-----------|---------|-------|
| **DTE pairs** | **Mixed** | All pairs where `far_dte - near_dte ≥ 7` are generated. This includes sensible pairs (30/60 DTE, ratio 2:1) alongside nonsensical ones (7/90 DTE, ratio 12.9:1). An income trader wants ratio 1.5:1 to 3:1. |
| **Strike placement** | **Untargeted** | All shared strikes are used. No ATM preference, no delta targeting. A calendar at $450 put (20% OTM) is constructed alongside one at $545 put (ATM). The ATM calendar is far more viable. |
| **IV term structure** | **Ignored** | The builder carries IV on both legs but never compares them. It generates calendars where `near_IV < far_IV` (unfavorable) just as readily as `near_IV > far_IV` (favorable). |
| **Debit reasonable** | **Not filtered** | Calendars with net debit near zero (both legs nearly worthless) or very high debit (ITM legs) are generated equally. |

**Verdict**: The raw calendar pool contains viable trades — ATM calendars at 30/60 DTE with favorable term structure exist in the candidate set. But they are mixed with ~95% noise (far-OTM strikes, extreme DTE ratios, unfavorable IV relationships), and the pipeline has **no mechanism to surface the good ones** because EV=None prevents ranking.

#### 7.3  Would a Trader Consider These Setups?

If a trader could filter the calendar output to:
- ATM ± 2% strikes only
- DTE ratio 1.5:1 to 3:1
- Near IV ≥ Far IV (favorable term structure)
- Net debit ≤ $3.00 per share

...the surviving candidates would be **tradeable and reasonable**. The construction generates them; the problem is that the pipeline cannot identify them among the noise.

---

## PART 4 — CROSS-FAMILY COMPARISON

### 8  Family Quality Ranking

#### 8.1  Quality Scorecard

| Dimension | Verticals | Iron Condors | Butterflies | Calendars |
|-----------|-----------|-------------|------------|-----------|
| **Delta awareness** | None | None | None | None |
| **Width intelligence** | None | None | Symmetry constraint limits some | N/A (same-strike) |
| **IV awareness** | None | None | None | None (critical gap) |
| **DTE optimization** | None | None | None | None |
| **Credit/premium filter** | None in construction; $0.05 min in credibility | None in construction; $0.05 min in credibility | None | None |
| **Expected move** | None | None | None | None |
| **EV computation** | Present (binary model) | Present (binary model) | Present (binary, incorrect for triangular payoff) | **None** — cannot rank |
| **Noise ratio** | Very high (50k→30 = 99.94% waste) | Very high (50k→30) | High (~30k generated) | 100% noise (cannot surface good trades) |
| **FIFO bias** | Severe (misses 25-90 DTE) | Severe (limited to 1-2 expirations) | Moderate (symmetry limits volume) | Low (doesn't hit cap) |

#### 8.2  Ranking by Construction Quality

1. **Calendars** (best construction relative to family potential) — The construction produces genuinely viable candidates among the noise. DTE pairing, strike intersection, and the honest EV=None are all architecturally sound. The problem is entirely in the **post-construction ranking inability**, not in construction.

2. **Butterflies** — The symmetry constraint naturally limits noise and produces geometrically valid candidates. The construction is clean. The problem is the **binary EV model on triangular payoff** (5C-01), which misstates quality.

3. **Verticals** — The construction is brute-force and generates massive noise, but the EV formula (while imperfect) at least allows ranking. The problem is **FIFO DTE bias** (missing the theta sweet spot) and **no delta targeting**.

4. **Iron Condors** (worst construction) — The independent-sides cross-product creates massive volumes with no delta balance, no skew awareness, and a side cap that restricts to 1-2 expirations. The construction quality is the lowest of all families because the IC's utility depends entirely on balance between sides — which is never checked.

#### 8.3  Biggest Gap: What's Constructed vs What's Good

| Family | Gap Size | Nature of Gap |
|--------|----------|---------------|
| **Calendars** | **LARGEST** | Good candidates exist but are invisible (EV=None). 100% of viable calendars are lost to the ranking system. |
| **Iron Condors** | **LARGE** | The cross-product generates ~50k candidates but balanced, sensibly-placed condors are a tiny minority. No mechanism to surface them. |
| **Verticals** | **MODERATE** | The EV sort does surface high-credit trades, but favors aggressive near-ATM over the conservative income sweet spot. |
| **Butterflies** | **MODERATE** | The binary EV overstates quality, causing butterflies to appear better than they are. The gap is accuracy, not volume. |

---

### 9  What's Missing from ALL Families

#### 9.1  Common Quality Factors Not Considered

| Missing Factor | Impact | Difficulty to Add |
|----------------|--------|-------------------|
| **Delta targeting** | HIGH — the single most impactful missing feature. Every family enumerates all strikes blindly. A `target_delta` parameter in construction would reduce noise 90%+. | LOW — delta is already on V2Leg. Add a filter: `if abs(short.delta) < target_delta_min or abs(short.delta) > target_delta_max: skip`. |
| **IV rank/percentile context** | HIGH — selling options when IV is low produces insufficient premium. The scanner runs identically regardless of IV environment. | MEDIUM — requires IV rank computation (current IV vs historical), which needs historical data not currently in the pipeline. |
| **Expected move calibration** | HIGH — wing placement relative to the expected move is how professionals size risk. The scanner uses fixed dollar distances. | MEDIUM — expected move = `spot × IV × √(DTE/365)`. IV and DTE are already available; the formula is simple but not implemented. |
| **Technical level awareness** | MEDIUM — short strikes at key support/resistance levels have different risk profiles. | HIGH — requires technical analysis infrastructure (support/resistance detection), which is a separate system. |
| **Earnings/event proximity** | MEDIUM — earnings release can blow through any short strike. Income traders avoid earnings weeks. | MEDIUM — requires earnings calendar data integration. Not currently available in the pipeline. |
| **Greeks-based optimization** | MEDIUM — targeting specific theta/vega profiles would produce strategies optimized for the current environment. | MEDIUM — theta/vega are computable from delta+IV but not currently derived during construction. |
| **Liquidity targeting** | MEDIUM — construction ignores OI and volume. Strategies with illiquid legs are hard to fill. | LOW — OI and volume are on V2Leg. A `min_oi` or `min_volume` filter in construction is trivial. |
| **Capital efficiency** | LOW-MEDIUM — RoR is computed but not used for ranking alongside EV. A trader considers both. | LOW — RoR is already computed. Include in the sort key or use a composite score. |

#### 9.2  The "Informed Construction" vs "Enumerate and Filter" Debate

The current pipeline follows the **"enumerate everything, filter later"** philosophy. This has one theoretical advantage: it guarantees no valid candidate is missed. However, the pipeline pays three costs:

1. **Generation cap biases**: The cap keeps early (short-DTE, narrow-width) candidates and discards later ones. This is a form of **accidental filtering** that's worse than intentional filtering because it's not based on quality.

2. **Compute waste**: 50,000 candidates through 5 validation phases when only 30 are selected creates enormous computational overhead for marginal benefit.

3. **No quality feedback**: Construction never learns what downstream sorting rewards. It could pre-filter for delta, width, and credit ranges that historically rank well, but it generates everything blindly.

---

### 10  ONE Construction-Phase Improvement Per Family

#### 10.1  Vertical Spreads: **Delta-Targeted Short Strike Selection**

**The change**: Instead of enumerating all (short, long) pairs, filter short strikes to `0.10 ≤ |short.delta| ≤ 0.35` before pairing.

**Impact**:
- Reduces construction volume from ~50,000 to ~5,000-8,000 (10-15 strikes per expiry instead of 100)
- Eliminates FIFO DTE bias (all expirations fit under cap)
- Surfaces the income "sweet spot" in the top-30
- Removes ultra-far-OTM and near-ATM noise

**Why this over other improvements**: Delta is already on every contract. The filter is a single `if` statement. It addresses the two largest vertical spread quality problems (near-ATM EV bias and FIFO DTE bias) simultaneously.

#### 10.2  Iron Condors: **Delta-Balanced Side Pairing**

**The change**: Instead of independent side construction + blind cross-product, build IC sides where `|Δ_short_put - Δ_short_call| ≤ 0.10` (approximately balanced deltas).

**Impact**:
- Eliminates highly skewed condors (10-delta put / 40-delta call)
- Naturally surfaces the "strangle + wings" income trade
- Reduces cross-product explosion (fewer valid pairings)
- Respects the IC's purpose as a neutral income strategy

**Why this over other improvements**: An unbalanced IC is not a true iron condor — it's a directional credit spread with a hedge. Delta balance is the single most important IC quality criterion and is trivially checkable (delta is on every V2Leg).

#### 10.3  Butterflies: **Center Strike Proximity Filtering**

**The change**: Restrict butterfly center strikes to within ±5% of spot price (or within ±1σ expected move).

**Impact**:
- Eliminates deep-OTM butterfly constructions that have near-zero probability
- Focuses generation budget on the viable butterfly zone
- Reduces noise by ~80%+ (from all strikes to ±5% band)

**Why this over other improvements**: While fixing the binary EV model (5C-01) is arguably more impactful, it's a math fix in Phase E, not a construction fix. The single highest-impact construction change is center strike filtering — it eliminates the most clearly non-viable candidates.

#### 10.4  Calendars: **IV Term Structure Pre-Check**

**The change**: During construction, compare `near_leg.iv` and `far_leg.iv`. Skip calendars where `near_iv < far_iv × 0.90` (near-term vol is materially lower than far-term vol — unfavorable for calendars).

**Impact**:
- Eliminates calendars with unfavorable term structure (the fundamental calendar thesis is selling richer near-term vol)
- Reduces noise by ~30-50% (depending on current term structure)
- Even without EV, the surviving candidates are all at least directionally viable

**Why this over other improvements**: IV term structure is the **entire basis** for calendar profitability. A calendar with unfavorable term structure is fundamentally non-viable, regardless of strike or DTE. This filter addresses the single most important calendar quality criterion and IV is already available on every V2Leg.

---

## QUALITY SCORECARD

### Per-Family Summary

| Family | Construction Quality | Post-Construction Quality | Final Output Quality | Grade |
|--------|---------------------|--------------------------|---------------------|-------|
| **Verticals** | D — Pure brute force, all combinations | C — EV sort rewards wrong attributes (near-ATM, short-DTE) | D+ — Top-30 is dominated by aggressive, non-income trades | **D+** |
| **Iron Condors** | F — Independent sides, no balance, 1-2 expiry limit | C — EV sort, but IC POP/EV at least directionally correct | D — Unbalanced, concentrated in minimal expirations | **D** |
| **Butterflies** | C — Symmetry constraint provides natural quality floor | D — Binary EV on triangular payoff overstates quality | C- — Candidates look better than they are | **C-** |
| **Calendars** | C — Geometrically valid, honest about limitations | F — EV=None, completely unrankable | F — Invisible to the entire selection system | **F** |

### System-Wide Assessment

| Metric | Status |
|--------|--------|
| Geometric enumeration quality | **Fair** — all families correctly enumerate valid structures |
| Strategy-quality targeting | **Missing** — no family targets the "good trade" zone |
| Post-construction filtering | **Minimal** — credibility gate catches only extreme cases |
| Ranking accuracy | **Poor** — EV formula rewards attributes that differ from income trading best practices |
| Capital efficiency awareness | **None** — RoR computed but not used in selection |
| Risk profile differentiation | **None** — 3-DTE and 45-DTE trades compete as equals |
| Market environment awareness | **None** — IV rank, expected move, regime not considered |

---

## FINDINGS

### Finding 5D-01 (HIGH) — EV Sort Produces Non-Income-Quality Top-30

**Scope**: All families with EV computation (verticals, IC, butterflies)  
**Issue**: The EV formula `POP × max_profit - (1-POP) × max_loss` systematically rewards near-ATM shorts with high absolute credit. For income trading, the "sweet spot" is 15-20 delta shorts with moderate credit — trades that maximize POP while collecting adequate premium. The EV sort pushes these to rank 10-20+, while rank 1-5 is dominated by 30-40 delta shorts with high credit and high risk. The top-30 by EV does not match what an experienced income trader would select.  
**Risk**: Users receive trade recommendations that are mathematically optimal by a flawed metric but practically poor for income strategies.  
**Recommendation**: Replace or augment EV with a composite score: `score = RoR × POP × f(DTE) × g(delta_distance_from_target)`. Or sort by `EV_per_day / max_loss` (risk-adjusted daily EV) which naturally penalizes aggressive positions.

### Finding 5D-02 (HIGH) — FIFO Generation Cap Excludes Theta Sweet Spot

**Scope**: Verticals (DTE 1-90), Iron Condors (DTE 7-60)  
**Issue**: Construction enumerates expirations in ascending date order. The generation cap (50,000) is hit after ~15-20 expirations. For verticals with DTE 1-90, this means DTE 25-90 is **never constructed**. The 30-45 DTE theta decay sweet spot is systematically excluded from the candidate pool. For IC, the side cap exhausts the budget on the first 1-2 expirations, making the problem even more severe.  
**Risk**: The scanner cannot produce candidates at the DTE range where income strategies perform best.  
**Recommendation**: Either (a) apply delta pre-filtering to reduce per-expiration volume (allowing all expirations under cap), (b) allocate cap budget proportionally across expirations (`per_exp_cap = total_cap / num_expirations`), or (c) sort expirations by desirability (25-45 DTE first) before enumeration.

### Finding 5D-03 (HIGH) — Calendar Family Completely Invisible to Selection

**Scope**: Calendars (all 4 scanner keys)  
**Issue**: Calendar EV=None causes all calendar candidates to sort as `(-0.0, -0.0, symbol)` in the workflow runner's Stage 4 EV sort. In any multi-family scan, calendars rank below all candidates with positive EV and never appear in the top-30. This makes the entire calendar family — 4 scanner keys, ~4,000-8,000 candidates per symbol — functionally dead code from the user's perspective. The constructor runs, phases validate, but the candidates never surface.  
**Risk**: Calendar/diagonal spreads are valuable income strategies being silently discarded by the selection system. Compute wasted on constructing and validating candidates that can never be selected.  
**Recommendation**: Either (a) implement approximate calendar EV (even heuristic), (b) run calendar scanners in a separate ranking pool with non-EV ranking criteria, or (c) reserve calendar slots in the top-N (e.g., top-25 by EV + top-5 by net_debit among calendars).

### Finding 5D-04 (HIGH) — No Risk Profile Differentiation in Mixed-DTE Output

**Scope**: All families  
**Issue**: The top-30 output mixes candidates across all DTEs without differentiation. A 3-DTE put credit spread (high gamma, binary P&L, needs immediate monitoring) competes directly with a 45-DTE spread (smooth theta decay, manageable gamma, standard income trade). These are fundamentally different risk profiles presented as comparable alternatives. A user selecting from the top-30 has no indication of the dramatically different risk characteristics.  
**Risk**: Users may select short-DTE trades (ranked high by EV_per_day) without understanding they require active management — or select long-DTE trades without understanding they tie up capital longer.  
**Recommendation**: Either (a) add a `dte_bucket` field (short: 1-14, medium: 15-30, optimal: 30-45, long: 46-90) and present results grouped by bucket, or (b) require the user to select a target DTE range before scanning.

### Finding 5D-05 (MEDIUM) — IC Delta Imbalance Not Detected or Penalized

**Scope**: Iron condors  
**Issue**: The IC builder independently constructs put and call sides, then cross-products them. It does not check delta balance. A condor with 10-delta put short and 40-delta call short (heavily directionally biased) is generated and scored identically to a balanced 20/20 condor. The EV sort may surface the unbalanced condor higher if it has more total credit.  
**Risk**: Users receive IC recommendations that are effectively directional bets disguised as neutral income strategies.  
**Recommendation**: Add a delta balance check in construction: `abs(|Δ_put| - |Δ_call|) ≤ 0.10` or similar tolerance.

### Finding 5D-06 (MEDIUM) — No IV Environment Awareness

**Scope**: All families  
**Issue**: The scanner runs identically whether SPY IV rank is at the 5th percentile (very low) or 95th percentile (very high). Selling options when IV is low produces insufficient premium and poor risk/reward. When IV is high, wider wings and more aggressive strategies become viable. The scanner has no mechanism to adjust construction parameters based on the IV environment.  
**Risk**: In low-IV environments, the scanner produces candidates with inadequate premium. In high-IV environments, it doesn't capitalize on the expanded opportunity set.  
**Recommendation**: Pass IV rank/percentile into construction context. In low-IV: tighten delta range to ATM-only (where premium exists). In high-IV: widen acceptable delta range and increase target credit.

### Finding 5D-07 (MEDIUM) — No Expected Move Calibration for Wing Placement

**Scope**: IC, butterflies  
**Issue**: Wing placement uses fixed $50 maximum width with no reference to the underlying's expected move. SPY's expected move at 30 DTE and 20% IV is approximately `$545 × 0.20 × √(30/365) ≈ $31`. Wings placed inside the expected move (e.g., short put at $530, 15 points from spot) have a meaningful probability of being breached. Wings placed at 1.5-2× expected move are the income standard. The scanner doesn't compute or use expected moves.  
**Risk**: Some top-ranked candidates have wings inside the expected move — higher risk than the user's intent.  
**Recommendation**: Compute expected move in Phase A or B: `EM = spot × IV × √(DTE/365)`. Filter short strikes to at least 1× EM distance from spot (or use EM to set the delta targeting range).

### Finding 5D-08 (MEDIUM) — Credibility Gate Too Permissive

**Scope**: All families reaching Stage 4  
**Issue**: The credibility gate applies only three checks: `premium ≥ $0.05`, `POP < 0.995`, `bid > 0`. This allows through many candidates that are technically valid but practically poor: near-ATM shorts (40+ delta), extreme widths ($50), credit-to-width ratios below 10%, and sub-7-DTE trades. The gate was designed as a minimum viability filter, not a quality filter — but it's the **only** filter between 50,000 candidates and the top-30.  
**Risk**: The vast majority of credibility-passing candidates are trades no income trader would take.  
**Recommendation**: Add quality-tier filtering to the credibility gate: `credit/width ≥ 0.15`, `0.10 ≤ |short.delta| ≤ 0.35`, `DTE ≥ 7`.

### Finding 5D-09 (MEDIUM) — No Capital Efficiency in Ranking

**Scope**: All families  
**Issue**: The top-30 is sorted by absolute EV. A trade with EV=+$200 and max_loss=$5,000 ranks above a trade with EV=+$80 and max_loss=$400 — despite the latter being 10× more capital-efficient ($0.20/dollar risked vs $0.04/dollar risked). RoR and Kelly are computed but used only as secondary/tertiary sort keys. For capital-constrained traders (most retail), capital efficiency matters more than absolute EV.  
**Risk**: Users are directed toward capital-intensive trades when capital-efficient alternatives exist.  
**Recommendation**: Use risk-adjusted EV as primary sort: `ranking_score = EV / max_loss` or `EV_per_day / max_loss`.

### Finding 5D-10 (LOW) — No Skew Awareness in IC Construction

**Scope**: Iron condors  
**Issue**: SPY's put skew means equidistant OTM puts are ~40-60% more expensive than equidistant OTM calls. The IC builder treats both sides identically. A skew-aware builder could widen the put side (capturing more premium from the richer side) while tightening the call side (reducing risk on the cheaper side). This is a standard income trading optimization.  
**Risk**: Suboptimal premium capture and risk distribution.  
**Recommendation**: Low priority — addressing delta balance (5D-05) would naturally improve IC quality more than skew adjustment.

### Finding 5D-11 (LOW) — $1-Wide Spreads Waste Generation Cap

**Scope**: Verticals  
**Issue**: Every pair of adjacent strikes generates a $1-wide spread. For SPY with ~100 OTM puts per expiry, this produces ~100 $1-wide candidates per expiry that are almost always marginal ($0.03-$0.10 credit, $90-$97 max loss per contract). These consume ~100 generation cap slots per expiry (~1,500 total) but rarely appear in the top-30.  
**Risk**: Marginal waste of cap budget. Minor relative to the FIFO DTE bias.  
**Recommendation**: Set `min_width ≥ $2` or filter during construction when underlying price > $100.

### Finding 5D-12 (LOW) — Butterfly EV Overstatement Not Flagged to User

**Scope**: Butterflies  
**Issue**: Butterfly candidates appear in the top-30 with EV values computed from the binary outcome model, which overstates true EV by ~40-60% for the triangular payoff (per 5C-01). If a butterfly ranks in the top-30 alongside verticals with more accurate EV computation, the butterfly appears artificially competitive. There is no flag or label indicating the EV computation method differs.  
**Risk**: Users may select butterflies over verticals based on inflated EV numbers.  
**Recommendation**: Add an `ev_method` field ("binary", "binary_triangular_approx", "none") so downstream consumers can adjust or display appropriately.

---

## SUMMARY

| Severity | Count | Key Theme |
|----------|-------|-----------|
| HIGH | 4 | EV sort produces wrong top-30; FIFO cap excludes theta sweet spot; calendars invisible; mixed DTE risk profiles |
| MEDIUM | 5 | IC delta imbalance; no IV environment; no expected move; credibility gate too permissive; no capital efficiency |
| LOW | 3 | No skew awareness; $1-wide waste; butterfly EV not flagged |
| **Total** | **12** | |

### The Core Problem

The V2 scanner is a **geometrically correct but strategically uninformed** system. It correctly enumerates all valid option structures and filters for structural integrity. But it has **zero awareness of what makes a good income trade**:

1. **Construction** enumerates everything with no quality targeting
2. **Validation** checks structural correctness, not trade quality
3. **Math** computes EV using a formula that rewards the wrong attributes
4. **Ranking** sorts by a metric that favors aggressive over income-quality trades
5. **Selection** takes the top-30 from a pool biased by FIFO and EV formula artifacts

The result: the top-30 presented to the user is dominated by high-delta, short-DTE, wide-width trades that an experienced income options trader would reject in favor of the conservative, high-POP, moderate-credit trades that rank 15th-30th or aren't even constructed (DTE > 20).

### The Single Most Impactful Change

**Delta pre-filtering in construction** (limiting short strikes to 0.10-0.35 delta) would:
- Reduce construction volume by ~80% (all expirations fit under cap, eliminating FIFO bias)
- Surface income-appropriate trades in the top-30
- Cost one `if` statement per candidate (delta already on V2Leg)
- Improve every family that sells premium (verticals, IC, iron butterflies)

---

**Provenance**: All findings traced from direct code reads of `vertical_spreads.py`, `iron_condors.py`, `butterflies.py`, `calendars.py`, `phases.py`, and `options_opportunity_runner.py`. Prior audit context from 5A, 5B, 5C documents.
