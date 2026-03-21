# Audit 5B — Phase B Construction: Vertical Spreads & Iron Condors

**Pass**: 5 — Options Scanner Construction & Candidate Quality  
**Prompt**: 5B  
**Scope**: `app/services/scanner_v2/families/vertical_spreads.py`, `iron_condors.py`, `phases.py` (math), `base_scanner.py` (hooks)  
**Date**: 2026-03-21

---

## PART 1 — VERTICAL SPREAD CONSTRUCTION

### 1  Leg Pairing Logic

#### 1.1  Algorithm

```
for each expiry bucket in narrowed_universe.expiry_buckets:
    typed_contracts = [entries where option_type == target_type]
    sort typed_contracts ascending by strike
    for i in range(len(typed_contracts)):          # S_low
        for j in range(i+1, len(typed_contracts)): # S_high
            if S_high - S_low > max_width:
                break  # remaining j values only wider
            assign short/long per _VARIANT_CONFIG
            build V2Candidate
            if seq >= generation_cap: stop
```

**Source**: [vertical_spreads.py](BenTrade/backend/app/services/scanner_v2/families/vertical_spreads.py#L107-L210)

#### 1.2  Cross-Product Nature

This is a **pure O(n²) cross-product** of all same-type strikes per expiry, bounded only by `max_width ≤ $50`.  There is:
- **No delta targeting** — no effort to select strikes near a target delta.
- **No distance filtering** — all strikes in the bucket are used regardless of distance from spot.
- **No credit/premium minimum** — all pairs are constructed regardless of pricing.

The only efficiency is the `break` on `s_high - s_low > max_width` (since strikes are sorted ascending, once width exceeds max, further j values are wider).

#### 1.3  Short Leg Selection

The short leg is selected by positional rule, not by delta target:

| Variant | `short_is_higher` | Short Strike | Long Strike | Intent |
|---------|-------------------|-------------|-------------|--------|
| `put_credit_spread` | `True` | S_high (closer to ATM for puts) | S_low (further OTM) | Credit |
| `call_credit_spread` | `False` | S_low (closer to ATM for calls) | S_high (further OTM) | Credit |
| `put_debit` | `False` | S_low (further OTM) | S_high (closer to ATM) | Debit |
| `call_debit` | `True` | S_high (further OTM) | S_low (closer to ATM) | Debit |

**Key insight**: "Short" and "long" refer to the option position side, not strike height.  The mapping is correct for the intended credit/debit semantics.

#### 1.4  Code Path Unity

All four variants (`put_credit_spread`, `call_credit_spread`, `put_debit`, `call_debit`) share **one code path** in `construct_candidates()`.  The only difference is the `_VARIANT_CONFIG` lookup which determines `option_type` and `short_is_higher`.

---

### 2  Width Selection

#### 2.1  Width Generation

The builder generates **ALL widths** from $1 up to `max_width` ($50 default).  It does not target standard widths ($1, $2, $5, $10).  Every pair `(S_low, S_high)` where `S_high - S_low ≤ max_width` becomes a candidate.

#### 2.2  Width Configurability

| Parameter | Default | Configurable? | Source |
|-----------|---------|--------------|--------|
| `max_width` | $50.00 | Yes, via `context["max_width"]` | Per-scan call |
| Minimum width | **None** | No parameter exists | Always ≥ minimum strike increment |
| Preferred widths | **None** | Not supported | All widths generated equally |

#### 2.3  Strike Increment Reality

SPY options use $1 strike increments for near-the-money and $5 increments for far OTM.  For a given short strike at, say, $530 on SPY:

| Long Strike | Width | Would be generated? |
|-------------|-------|-------------------|
| $529 | $1 | Yes |
| $528 | $2 | Yes |
| $525 | $5 | Yes |
| $520 | $10 | Yes |
| $510 | $20 | Yes |
| $500 | $30 | Yes |
| $490 | $40 | Yes |
| $480 | $50 | Yes |

For SPY's ~50 OTM put strikes per expiration, one short strike can produce up to 49 width variations (bounded by max_width).  **Typical**: ~25-40 widths per short strike depending on available strikes within $50.

#### 2.4  No Width Preference

There is no intelligence about width suitability.  A $1-wide spread on SPY (likely $0.03-0.05 credit, $0.95-0.97 max loss) is generated with the same priority as a $5-wide spread (~ $1.00-1.50 credit, $3.50-4.00 max loss).  The $1-wide spread is almost always a poor income trade but still consumes a generation cap slot.

---

### 3  Credit/Debit Determination

#### 3.1  Construction Phase (Phase B)

Phase B computes a **preliminary** credit/debit from raw quotes:

```python
# _build_candidate()
credit = short_contract.bid - long_contract.ask
if credit > 0:
    math.net_credit = round(credit, 4)
elif credit < 0:
    math.net_debit = round(-credit, 4)
```

This is purely informational for traceability.  Phase E recomputes everything.

#### 3.2  Phase E Recomputation

```python
# _recompute_vertical_math()
credit = short.bid - long.ask
if credit > 0:  # credit spread
    net_credit, max_profit = credit, credit × 100
    max_loss = (width - credit) × 100
else:            # debit spread
    net_debit = long.ask - short.bid
    max_profit = (width - debit) × 100
    max_loss = debit × 100
```

#### 3.3  Can Credit Scanners Produce Debit Spreads?

**Yes**.  The scanner key `put_credit_spread` determines which strike is short (higher = closer to ATM for puts), but the bid/ask relationship determines whether the resulting spread is actually credit or debit.  If the short leg's bid < long leg's ask (e.g., deep OTM where both legs are nearly worthless), the "credit spread" becomes a debit spread.

Phase C `validate_pricing_sanity` rejects candidates where `credit ≤ 0` for credit strategies, so these would be caught — but they consume generation cap slots before rejection.

---

### 4  Quality Evaluation — What Makes a GOOD Vertical Spread

| Quality Criterion | BenTrade Income Standard | Builder Implementation | Assessment |
|-------------------|-------------------------|----------------------|------------|
| Short strike delta 0.15-0.30 | Core income targeting | **Not implemented** — no delta filter | MISSING |
| Width balances premium vs max loss ($5 typical for SPY) | Standard width | **Not implemented** — all widths $1-$50 generated | MISSING |
| Net credit ≥ 20% of width | Adequate premium | **Not implemented** — no credit/width ratio filter | MISSING |
| Short strike below support levels | Technical awareness | **Not implemented** — no technical level data | MISSING |
| DTE 30-45 day sweet spot | Theta decay optimization | **Not implemented** — DTE 1-90 all treated equally | MISSING |

**Assessment**: The builder targets **zero** of the five standard income quality criteria.  All quality filtering is deferred to later phases.

---

### 5  Does the Builder Target Quality?

| Question | Answer |
|----------|--------|
| Delta-targeting in short strike selection? | **No** — all strikes enumerated regardless of delta |
| Premium-to-width ratio filtering? | **No** — all credit amounts accepted |
| Technical level awareness? | **No** — no support/resistance data available |
| Quality filtering deferred to Phase D/E? | **Phase D/E only reject structural impossibilities** — they also have no delta/premium/EV filters (see §10 Findings) |

**Critical**: The gap is not "construction doesn't filter, later phases do."  The gap is "**no phase in the entire pipeline applies strategy-quality filters**."  The reserved `THRESHOLD` reject reason category exists in the taxonomy but has no implementations.

---

## PART 2 — IRON CONDOR CONSTRUCTION

### 6  Four-Leg Assembly

#### 6.1  Algorithm

```
for each expiry bucket:
    1. Separate OTM puts (strike < spot) and OTM calls (strike > spot)
    2. Sort puts ascending, calls ascending
    3. Build put credit spread sides:
       for each (long_put, short_put) where long < short:
           if short - long <= max_wing_width: add to put_sides[]
           cap put_sides at √generation_cap
    4. Build call credit spread sides:
       for each (short_call, long_call) where short < long:
           if long - short <= max_wing_width: add to call_sides[]
           cap call_sides at √generation_cap
    5. Cross-product: for each (put_side, call_side):
       build 4-leg V2Candidate
       cap total at generation_cap
```

**Source**: [iron_condors.py](BenTrade/backend/app/services/scanner_v2/families/iron_condors.py#L100-L230)

#### 6.2  Independent Sides + Cross-Product

The builder constructs put and call sides **independently**, then cross-products them.  This is a deliberate design choice — it avoids the O(n⁴) of enumerating all 4-strike combinations directly.

The non-overlap constraint (put_short < call_short) is **automatically satisfied** because put sides use `strike < spot` and call sides use `strike > spot`.

#### 6.3  Side Cap

`side_cap = int(math.isqrt(generation_cap))` = `√50,000 ≈ 223`

This caps each side at 223 combinations, so the worst-case cross-product is 223 × 223 = 49,729, just under the 50,000 generation cap.  This is a smart O(n⁴) → O(√cap²) reduction.

---

### 7  Symmetry and Balance

#### 7.1  Width Symmetry

**Not required**.  Put width and call width are generated independently.  A condor with a $5-wide put side and a $10-wide call side is valid.

Risk width = `max(put_width, call_width)`, so asymmetric condors have risk dominated by the wider side.

#### 7.2  Delta Balance

**Not enforced**.  There is no constraint that `|delta_short_put| ≈ |delta_short_call|`.  A condor with a 0.10-delta short put and a 0.30-delta short call is generated with the same priority as a balanced 0.16/0.16 condor.

#### 7.3  Skewed Condors

**Freely generated**.  The builder produces all geometric combinations without awareness of:
- Directional skew (wider put side for bearish lean)
- Volatility skew (put options are more expensive)
- Expected move calibration

#### 7.4  Wing Placement

**No strategy**.  Wings are placed at all available strike widths up to $50.  There is no concept of:
- Standard deviations from spot
- Expected move boundaries
- Premium efficiency (thin wings capture less but risk less)

---

### 8  Quality Evaluation — What Makes a GOOD Iron Condor

| Quality Criterion | BenTrade Income Standard | Builder Implementation | Assessment |
|-------------------|-------------------------|----------------------|------------|
| Short strikes at 1-2σ OTM (~16-delta) | Core IC targeting | **Not implemented** — all OTM strikes used | MISSING |
| Equal delta on short strikes | Balanced risk | **Not implemented** — no delta constraint | MISSING |
| Combined credit ≥ 33% of single-side width | Adequate premium | **Not implemented** — no credit minimum | MISSING |
| Wings wide enough for meaningful premium | Practical wing sizing | **Not implemented** — all widths $1-$50 | MISSING |
| DTE 30-45 days | Optimal theta/gamma | **Not implemented** — DTE 7-60 all treated equally | MISSING |

---

### 9  Does the Builder Target IC Quality?

| Question | Answer |
|----------|--------|
| Delta-matching between put/call short strikes? | **No** |
| Combined-credit minimum during construction? | **No** |
| Awareness of expected move? | **No** |
| Volatility skew awareness (put skew)? | **No** |
| Any quality filter at construction? | **No** — pure geometric enumeration |

---

## PART 3 — EXPLOSION CONTROL

### 10  Generation Cap

#### 10.1  Cap Values

| Family | Default Cap | Source |
|--------|------------|--------|
| Verticals | 50,000 | `_DEFAULT_GENERATION_CAP` in `vertical_spreads.py` |
| Iron Condors | 50,000 | `_DEFAULT_GENERATION_CAP` in `iron_condors.py` |
| IC Side Cap | √50,000 ≈ 223 | `math.isqrt(generation_cap)` |

#### 10.2  Cap Application

The cap is per `(scanner_key, symbol)` — each scanner key running on each symbol has its own 50,000 limit.  Running all 11 scanner keys on SPY could produce up to 550,000 candidates total.

#### 10.3  Hit Frequency Estimate (SPY at $545)

**Vertical Spreads**:
- SPY has ~15 valid expirations (DTE 1-90)
- ~100+ put strikes per expiration (both OTM and ITM pass Phase A — see 5A)
- Per expiration with 100 same-type strikes: C(100, 2) = 4,950 pairs before max_width filter
- With max_width=$50 and $1 increments near ATM: ~40-50 valid widths per strike ≈ ~2,000-3,000 per expiration
- 15 expirations × ~2,500 = **~37,500 candidates** — close to but often under the 50,000 cap
- SPY would **routinely approach or hit the cap** for individual vertical scanner keys

**Iron Condors**:
- ~40-50 OTM puts, ~40-50 OTM calls per expiration
- ~1,200 put side pairs × ~1,200 call side pairs ÷ side cap (223) → 223² ≈ 49,729
- Per expiration, hits the mathematical ceiling of the side cap immediately
- Additional expirations are **completely excluded** by the cap
- SPY IC would **always hit the cap**, with only 1-2 expirations represented

#### 10.4  Which Candidates Are Kept?

**FIFO — first generated, first kept**.  The construction loop iterates:
1. Expirations in sorted order (earliest first)
2. Within each expiry: strikes from lowest to highest (S_low ascending)
3. Within each S_low: S_high from S_low+1 upward

When the cap is hit:
- **Kept**: Earlier expirations, lower strikes, narrower widths
- **Discarded**: Later expirations, higher strikes, wider widths

This creates a **systematic bias toward short-DTE, low-strike, narrow-width candidates**.  For put credit spreads on SPY:
- 7-DTE $520/$519 (deep OTM, $1 wide, ~$0.02 credit) is kept
- 45-DTE $530/$525 ($5 wide, ~$1.50 credit) may be discarded

This is the **opposite** of what an income trader wants.

#### 10.5  Smarter Approaches

The generation cap is a blunt instrument.  Better alternatives:
- **Per-expiration cap**: Distribute the budget evenly across expirations
- **Delta-targeted narrowing**: Only construct around 0.10-0.40 delta range
- **Width filtering**: Only generate standard widths ($1, $2, $5, $10, $20) or skip $1-wide
- **Progressive cap**: Start with the "sweet spot" (30-45 DTE, 0.15-0.30 delta), then expand outward

---

### 11  Compute Cost

#### 11.1  Estimated Timing

These are structural estimates based on the algorithm complexity:

| Family | SPY Candidates | Operations | Est. Time |
|--------|---------------|-----------|-----------|
| Verticals (1 key) | ~37,500 | 37,500 V2Candidate allocations + leg construction | ~50-100ms |
| Verticals (4 keys) | ~150,000 | 4 × ~37,500 | ~200-400ms |
| Iron Condors | ~49,700 | Side enumeration + cross-product + candidate construction | ~100-200ms |
| **All 5 keys** | **~200,000** | | **~300-600ms** |

Phase B construction itself is fast (primarily object allocation).  The real cost is downstream — Phases C/D/D2/E must process every constructed candidate with validations and math recomputation.

#### 11.2  Pipeline Bottleneck

The pipeline bottleneck is **not Phase B construction** but rather **Phases D/E processing 50,000+ candidates** that were always going to fail.  A candidate with $1 width, $0.02 credit, and 0.01-delta short strike must still:
1. Pass Phase C structural checks (4-8 checks)
2. Pass Phase D quote/liquidity (5 per-leg checks × 2 legs = 10)
3. Pass Phase D2 quote/liquidity/dedup sanity (6+ checks)
4. Have math recomputed in Phase E (15+ field calculations)
5. Pass math verification (10+ tolerance checks)

Each of these ~40+ checks per candidate × 50,000 candidates = ~2 million check operations per scanner key.

#### 11.3  Caching/Memoization

**None** in Phase B.  No memoization of leg objects, no caching of strike combinations across expirations, no reuse between scanner keys scanning the same symbol.

Phase A's narrowed universe is shared (one `narrow_chain()` call per scanner key), but each scanner key runs its own complete Phase A with its own DTE window.

---

## CONSTRUCTION FLOW DIAGRAMS

### Vertical Spread Flow

```
Phase A: narrow_chain()
    └─ DTE window [1, 90] → all in-window expirations
    └─ No strike/moneyness/distance filter
    └─ Output: V2NarrowedUniverse with all strikes
        │
Phase B: construct_candidates()
        │
        ├─ For each expiry bucket:
        │   ├─ Filter to target option_type (put or call)
        │   ├─ Sort strikes ascending
        │   └─ O(n²) enumeration:
        │       └─ For each (S_low, S_high) where width ≤ $50:
        │           ├─ Assign short/long per _VARIANT_CONFIG
        │           ├─ Build V2Candidate with 2 V2Legs
        │           ├─ Set preliminary credit/debit
        │           └─ Check generation_cap (50,000)
        │
        └─ Output: list[V2Candidate] (up to 50,000)
            │
Phase C: Structural (8 checks) → rejects malformed
Phase D: Quote/liquidity presence → rejects None fields
Phase D2: Quote sanity + liquidity sanity + dedup
Phase E: Math recomputation + verification
Phase F: passed=True if zero reject_reasons
```

### Iron Condor Flow

```
Phase A: narrow_chain()
    └─ DTE window [7, 60] → all in-window expirations
    └─ No strike/moneyness/distance filter
    └─ Output: V2NarrowedUniverse with all strikes
        │
Phase B: construct_candidates()
        │
        ├─ For each expiry bucket:
        │   ├─ Separate OTM puts (strike < spot) and OTM calls (strike > spot)
        │   │
        │   ├─ Build put credit spread sides:
        │   │   O(n²) on puts, capped at √50,000 ≈ 223 sides
        │   │   (long_put < short_put, width ≤ $50)
        │   │
        │   ├─ Build call credit spread sides:
        │   │   O(n²) on calls, capped at 223 sides
        │   │   (short_call < long_call, width ≤ $50)
        │   │
        │   └─ Cross-product: put_sides × call_sides
        │       Build 4-leg V2Candidate for each pair
        │       Cap at 50,000 total
        │
        └─ Output: list[V2Candidate] (up to 50,000)
            │
Phase C: Structural (8 shared + 4 IC-specific geometry)
Phase D: Quote/liquidity presence (5 checks × 4 legs)
Phase D2: Quote sanity + liquidity sanity + dedup
Phase E: IC-specific math (family_math override)
Phase F: passed=True if zero reject_reasons
```

---

## FINDINGS

### Finding 5B-01 (HIGH) — Pure Brute-Force Enumeration with No Strategy Targeting

**Location**: `vertical_spreads.py:160-200`, `iron_condors.py:150-220`  
**Issue**: Both families use pure geometric enumeration — every valid (short, long) pair within width bounds is constructed.  There is no delta targeting, no credit minimum, no premium-to-width ratio filter, no DTE preference.  This produces tens of thousands of candidates that have no strategic merit for income trading (e.g., $1-wide spreads at 0.01-delta, 7-DTE spreads with no theta edge).  
**Risk**: The scanner builds candidates a knowledgeable options trader would never consider, wasting compute and making the "all candidates pass" output overwhelmingly noisy.  
**Recommendation**: Add construction-time filters: `min_width >= $2`, `max_delta <= 0.40`, `min_delta >= 0.05`, and `min_credit_pct >= 0.10` (credit/width ratio).  Even simple heuristics would reduce candidate count by 60-80%.

### Finding 5B-02 (HIGH) — No Quality Gates Anywhere in the Pipeline

**Location**: `phases.py`, `validation/`, `hygiene/`, `base_scanner.py`  
**Issue**: The audit of post-construction phases reveals that **no phase in the entire V2 pipeline applies strategy-quality filters**.  Phase C checks structural validity, Phase D checks data presence, Phase D2 checks quote/liquidity sanity, Phase E recomputes math and checks consistency.  None of them filter for favorable POP, minimum EV, adequate credit-to-width ratio, or delta range.  The `THRESHOLD` reject category is reserved in the taxonomy but has zero implementations.  Every structurally valid, non-degenerate candidate with non-None quotes passes the entire pipeline.  
**Risk**: The pipeline output is a massive undifferentiated candidate pool where a $1-wide 0.01-delta 7-DTE spread sits alongside a $5-wide 0.16-delta 30-DTE spread with no ranking or filtering by trade quality.  
**Recommendation**: Implement the `THRESHOLD` category gates — at minimum: `v2_credit_below_floor` (credit/width < 10%), `v2_pop_below_floor` (POP < 50% for credit, POP > 50% for debit), `v2_delta_out_of_range` (short delta outside 0.05-0.40).

### Finding 5B-03 (HIGH) — Generation Cap Creates Systematic Bias Toward Low-Quality Candidates

**Location**: `vertical_spreads.py:200-210`  
**Issue**: When the generation cap (50,000) is hit, FIFO ordering means earlier expirations and lower/narrower strikes are kept while later expirations are discarded.  For put credit spreads, this keeps deep OTM/narrow width/short DTE candidates and discards the 30-45 DTE sweet-spot candidates that income traders actually want.  The bias is exactly backwards — the worst candidates are kept, the best are cut.  
**Risk**: The cap hit guarantees the output pool is dominated by low-quality candidates.  SPY routinely approaches the cap.  
**Recommendation**: Either (a) pre-filter at Phase A to reduce candidate volume so the cap is never hit, or (b) use per-expiration budget allocation: `cap_per_exp = generation_cap / num_expirations`, or (c) reverse iteration order (sweet-spot DTEs first).

### Finding 5B-04 (HIGH) — IC Generation Cap Limits to 1-2 Expirations

**Location**: `iron_condors.py:152-157` (side_cap = √generation_cap)  
**Issue**: With side_cap ≈ 223, a single SPY expiration with 40+ OTM puts and 40+ OTM calls produces ~223 put sides × ~223 call sides ≈ 49,729 condor candidates — virtually the entire generation cap.  Any additional expirations are completely excluded.  This means IC output represents **one expiration** even though the DTE window is 7-60 days.  
**Risk**: The IC scanner produces candidates from only the nearest expiration, missing the 30-45 DTE sweet spot entirely.  
**Recommendation**: Cap per-expiration: `side_cap_per_exp = int(math.isqrt(generation_cap / num_expirations))`.  With 8 expirations, this gives ~79 sides/exp × 8 = ~50,000 total but distributed across all DTEs.

### Finding 5B-05 (MEDIUM) — No Width Intelligence for Income Trading

**Location**: `vertical_spreads.py:149` (`max_width`), no min_width  
**Issue**: All widths from $1 to $50 are generated.  For SPY income trading, $1-wide spreads are impractical (credit too small relative to commissions, poor risk/reward), and >$20-wide spreads are uncommon for retail accounts.  The absence of a `min_width` means a large fraction of candidates are $1-$2 wide with near-zero credit.  
**Risk**: Low-quality narrow spreads consume generation cap budget.  
**Recommendation**: Add `min_width` parameter (default $2 for SPY-class, $1 for cheaper underlyings).

### Finding 5B-06 (MEDIUM) — No Delta Balance Constraint on Iron Condors

**Location**: `iron_condors.py:200-225` (cross-product)  
**Issue**: Put and call sides are independently constructed and cross-producted with no delta-balance constraint.  A condor with short put at 0.08-delta and short call at 0.35-delta is generated alongside a balanced 0.16/0.16 condor.  Skewed condors represent directional bets, not the neutral income strategy IC construction implies.  
**Risk**: Heavily skewed condors pass the pipeline and appear in output as "iron condors" despite being directionally inappropriate for neutral income trading.  
**Recommendation**: Add optional delta-balance filter: `|delta_short_put| / |delta_short_call| ∈ [0.5, 2.0]` or tighter.

### Finding 5B-07 (MEDIUM) — IC Width Uses max() Not Matched Sides

**Location**: `iron_condors.py:380` (`m.width = max(put_width, call_width)`)  
**Issue**: The IC risk width is computed as the maximum of the two side widths, not requiring matched widths.  While this is technically correct for risk calculation (max loss occurs on the wider side), it means the scanner freely generates asymmetric condors where one side is $2 wide and the other is $20 wide.  The $2 side provides negligible premium while the $20 side carries significant risk.  
**Risk**: Asymmetric condors with lopsided risk profiles may confuse users expecting balanced condors.  
**Recommendation**: Track both `put_width` and `call_width` as separate fields in `V2RecomputedMath.notes` (already done) and optionally add a `max_width_ratio` filter.

### Finding 5B-08 (MEDIUM) — Vertical POP Uses Only Short-Leg Delta

**Location**: `phases.py:342` (`pop = 1 - |short.delta|`)  
**Issue**: The vertical spread POP approximation `1 - |short.delta|` models the probability that the short strike expires OTM.  This ignores the long leg entirely and treats it as a binary outcome (full win or full loss), identical to a naked short option.  For narrow-width spreads, the actual loss profile is bounded and the POP estimate is reasonable.  For wide spreads ($20+), the spread can lose partially and the binary approximation overestimates the expected loss, biasing EV downward.  
**Risk**: EV calculations for wide spreads are systematically pessimistic, biasing the scanner away from wider (potentially better risk/reward) spreads.  This was also noted in Pass 2 findings.  
**Recommendation**: Document this as a known limitation.  A more accurate POP would account for the long-strike boundary: `POP_OTM = 1 - |delta_short|`, `POP_full_loss = |delta_long|`, `POP_partial = |delta_short| - |delta_long|`.

### Finding 5B-09 (MEDIUM) — Credit/Debit Spreads Can Cross Over

**Location**: `vertical_spreads.py` variant config, `phases.py:316-340`  
**Issue**: The `put_credit_spread` scanner can produce a debit spread if the short leg's bid < long leg's ask (deep OTM, both nearly worthless).  Phase C's `validate_pricing_sanity` catches `credit ≤ 0` for credit strategies and rejects them, but only after consuming a generation cap slot.  Similarly, `call_debit` can produce a credit spread.  
**Risk**: Generation cap slots wasted on candidates that will be rejected for structural impossibility.  For deep OTM strikes, many pairs may fall into this category.  
**Recommendation**: Check credit/debit sign during Phase B construction before allocating a candidate: `if credit <= 0: continue`.

### Finding 5B-10 (LOW) — No Memoization Across Scanner Keys

**Location**: `base_scanner.py:124` (separate `narrow_chain()` per key)  
**Issue**: Each of the 4 vertical scanner keys and the IC key runs `narrow_chain()` independently on the same symbol's chain.  Since Phase A is DTE-only filtering (5A finding), the same normalization and expiry filtering work is repeated 5 times per symbol for these families.  
**Risk**: Redundant compute.  Minor for single symbols, meaningful under concurrent multi-symbol scans.  
**Recommendation**: Cache `V2NarrowedUniverse` per (symbol, dte_min, dte_max) and share across scanner keys with identical narrowing parameters.

### Finding 5B-11 (LOW) — IC POP Delta Approximation Rounds Low for Wide Condors

**Location**: `iron_condors.py:430` (`pop = 1 - |delta_put_short| - |delta_call_short|`)  
**Issue**: The IC POP formula `1 - |Δ_put| - |Δ_call|` approximates the probability that the underlying stays between short strikes.  This is mathematically correct for the probability of both short strikes expiring OTM simultaneously, but only as a rough approximation.  It ignores the probability density under the tails and the correlation between the two sides.  For wide condors (~10-delta on each side), this gives POP ≈ 0.80 which is reasonable; for tight condors (~30-delta), it gives POP ≈ 0.40 which may understate the actual probability since partial outcomes are possible.  
**Risk**: Low severity — standard industry approximation.  
**Recommendation**: Accept as documented limitation.

### Finding 5B-12 (LOW) — IC Side Cap Ignores Width-Quality Distribution

**Location**: `iron_condors.py:152` (side_cap)  
**Issue**: The side cap (√generation_cap ≈ 223) is applied per side regardless of width distribution.  With FIFO ordering, the first 223 put sides are the narrowest (lowest S_low with closest S_high).  Many of these are $1-$2 wide — adequate for the put side but when cross-producted with call sides, they produce condors where one side's premium is negligible.  
**Risk**: The side cap preserves many low-quality narrow sides while potentially dropping wider, more premium-rich sides.  
**Recommendation**: Within each side, prioritize by width or approximate premium rather than FIFO: e.g., sort sides by width descending before applying the cap.

---

## SUMMARY

| Severity | Count | Key Theme |
|----------|-------|-----------|
| HIGH | 4 | Brute-force enumeration, no quality gates anywhere in pipeline, generation cap bias, IC limited to 1-2 expirations |
| MEDIUM | 5 | No width intelligence, no delta balance on IC, width uses max(), POP ignores long leg, credit/debit crossover wastes cap |
| LOW | 3 | No memoization, IC POP approximation, side cap ignores quality |
| **Total** | **12** | |

### Architectural Assessment

The vertical spread and iron condor builders are well-engineered **geometric enumerators** — they correctly handle variant configuration, strike pair generation, and preliminary pricing.  The code is clean, well-documented, and structurally sound.

The fundamental gap is that **they are enumerators, not strategy selectors**.  The builder's job is to produce every geometrically valid combination, relying on downstream phases to separate good from bad.  But the downstream phases only filter for structural validity and data quality — they never ask "is this actually a good income trade?"

The result is a pipeline that constructs 50,000+ candidates, validates them all for structural soundness, and passes through every one that has non-None quotes and positive credit.  The output is a massive, undifferentiated pool where the best and worst trades are indistinguishable without external ranking.

The single highest-impact fix would be implementing **construction-time delta targeting**: instead of enumerating all strikes, identify the ~5-10 strikes nearest to the 0.16-delta target and build spreads only from those.  This would reduce candidate counts by 80-90% while preserving the highest-quality setups.

---

**Provenance**: All findings traced from direct code reads of `families/vertical_spreads.py`, `families/iron_condors.py`, `phases.py`, `base_scanner.py`, `validation/structural.py`, `hygiene/quote_sanity.py`, `hygiene/liquidity_sanity.py`, `hygiene/dedup.py`, `validation/math_checks.py`, `validation/tolerances.py`, `diagnostics/reason_codes.py`.
