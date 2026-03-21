# Audit 5C — Phase B Construction: Butterflies & Calendars

**Pass**: 5 — Options Scanner Construction & Candidate Quality  
**Prompt**: 5C  
**Scope**: `app/services/scanner_v2/families/butterflies.py`, `calendars.py`  
**Date**: 2026-03-21

---

## PART 1 — BUTTERFLY CONSTRUCTION

### 1  Triplet Enumeration

#### 1.1  Debit Butterfly Algorithm

```
for each expiry bucket:
    for each option_type in [call, put] (or context-filtered):
        strike_map = {strike: contract for entry where type == opt_type}
        strikes = sorted(strike_map.keys())
        for i in range(len(strikes)):           # lower wing
            for k in range(i+2, len(strikes)):  # upper wing
                center_needed = (strikes[i] + strikes[k]) / 2
                if center_needed not in strike_set: continue
                width = center_needed - strikes[i]
                if width > max_wing_width: continue
                build 3-leg candidate
                check generation_cap
```

**Source**: [butterflies.py](BenTrade/backend/app/services/scanner_v2/families/butterflies.py#L150-L240)

#### 1.2  Symmetry Requirement

**Strictly symmetric only**.  The center strike must equal `(lower + upper) / 2` exactly.  The builder checks `center_needed not in strike_set` — if the exact midpoint doesn't exist as a strike, the triplet is skipped.

**No broken-wing butterflies** are supported.  Asymmetric constructions are impossible with this algorithm.

#### 1.3  Wing Widths

All widths from the minimum strike increment up to `max_wing_width` ($50 default) are generated.  No preference for standard widths ($1, $2, $5, $10).  The builder generates every symmetric triplet where the midpoint exists as a strike.

**SPY example at $545**: With $1 strike increments near ATM, typical generated widths include $1, $2, $3, $4, $5… up to $50.  With $5 increments further OTM, widths of $5, $10, $15, $20, $25 etc.

#### 1.4  Center Strike Selection

**No targeting**.  Every strike in the option-type-filtered set is a potential center, lower, or upper.  The center is not required to be ATM, near spot, or at a specific delta.  A center at $500 (8% OTM for SPY at $545) is generated alongside one at $545 (ATM).

#### 1.5  Iron Butterfly Algorithm

```
for each expiry bucket:
    put_map = {strike: contract for puts}
    call_map = {strike: contract for calls}
    center_strikes = sorted(put_map.keys() ∩ call_map.keys())
    for each center:
        lower_puts = sorted(s for s in put_map where s < center)
        for each lower_put_strike:
            width = center - lower_put_strike
            upper_needed = center + width
            if width > max_wing: continue
            if upper_needed not in call_map: continue
            build 4-leg candidate
            check generation_cap
```

**Source**: [butterflies.py](BenTrade/backend/app/services/scanner_v2/families/butterflies.py#L250-L320)

#### 1.6  Iron Butterfly Center Definition

The center strike must have **both a put and a call** contract.  There is no constraint that the center be ATM or near spot.  Any strike with both option types qualifies — the builder enumerates all of them.

---

### 2  Debit Butterfly Leg Assembly

#### 2.1  Leg Structure

| Leg | Index | Side | Role |
|-----|-------|------|------|
| Lower wing | 0 | `long` | Buy 1× |
| Center body | 1 | `short` | Sell 2× (implicit) |
| Upper wing | 2 | `long` | Buy 1× |

All three legs share the same `option_type`.

#### 2.2  The 2x Center Quantity

The center leg is stored as a **single V2Leg with `side="short"`**.  The 2× quantity is **implicit** — it is managed in the math formulas, not in the leg structure.  The `_debit_butterfly_math()` function uses `2 * center.bid` when computing net debit.

**Implication**: The V2Leg structure has no `quantity` field.  Downstream consumers (UI, risk calculations) must know that butterfly center legs are 2× by convention.  This is an **implicit contract** not encoded in the data.

#### 2.3  Preliminary Math

```python
# Phase B:
debit = lower.ask + upper.ask - 2 × center.bid
# Only set if 0 < debit < width
```

Phase E recomputes with the same formula via `_debit_butterfly_math()`.

---

### 3  Iron Butterfly Construction

#### 3.1  Leg Structure

| Leg | Index | Side | Type | Role |
|-----|-------|------|------|------|
| Lower wing | 0 | `long` | put | Buy 1× OTM put |
| Center put | 1 | `short` | put | Sell 1× put at center |
| Center call | 2 | `short` | call | Sell 1× call at center |
| Upper wing | 3 | `long` | call | Buy 1× OTM call |

#### 3.2  ATM Definition

The builder does **not define ATM**.  Any strike with both a put and a call contract qualifies as a center.  There is no proximity-to-spot filter.  A strike 20% away from spot is a valid center if both option types exist.

#### 3.3  Wing Symmetry

**Required**.  `upper_needed = center + width` where `width = center - lower_put_strike`.  The wings are always equidistant from center.  The code checks `upper_needed not in call_map` — if no matching call exists at the symmetric position, the triplet is skipped.

#### 3.4  Preliminary Math

```python
# Phase B:
credit = center_put.bid + center_call.bid - lower_put.ask - upper_call.ask
# Only set if credit > 0
```

---

### 4  Quality Evaluation — What Makes a GOOD Butterfly

| Quality Criterion | BenTrade Standard | Builder Implementation | Assessment |
|-------------------|-------------------|----------------------|------------|
| Center at expected price target or ATM | Core butterfly placement | **Not implemented** — all strikes enumerated | MISSING |
| Wing width matches expected move (1-2σ) | Vol-calibrated | **Not implemented** — all widths up to $50 | MISSING |
| Net debit ≤ 30% of wing width | Acceptable risk | **Not implemented** — no debit/width filter | MISSING |
| DTE 14-30 days (payoff peaks near exp) | Optimal butterfly DTE | **Not implemented** — DTE 7-60, all equal | MISSING |
| Adequate liquidity at all 3 strikes | Execution quality | **Not implemented** — no liquidity check | MISSING |

---

### 5  Does the Builder Target Quality?

| Question | Answer |
|----------|--------|
| Expected-move awareness in center placement? | **No** |
| Debit-to-width ratio filtering? | **No** |
| Liquidity check at all 3/4 strikes? | **No** |
| Everything deferred? | **Yes** — but downstream phases also have no butterfly-specific quality gates |

---

### 6  Pass 2 Finding: Binary Outcome Model

The debit butterfly EV uses a **binary outcome model**:

```python
# _debit_butterfly_math():
ev = pop * max_profit - (1 - pop) * max_loss
```

Where:
- `max_profit = (width - debit) × 100` — only achieved if underlying is **exactly at center** at expiration
- `max_loss = debit × 100` — incurred if underlying is outside the wings
- `pop = |Δ_lower| - |Δ_upper|` — probability underlying finishes between the wings

**The triangular payoff issue**: A butterfly's payoff is triangular (peaks at center, tapers to zero at wings).  The binary model assumes max_profit is achieved whenever the underlying is anywhere between the wings.  In reality, profit is proportional to proximity to center.  This systematically **overstates EV** for butterflies because:
- The POP covers the full wing-to-wing range
- But max_profit is only achieved at the exact center
- Average profit when "between the wings" is approximately `max_profit / 2`

**Construction awareness**: Zero.  Construction phase has no knowledge of the triangular payoff.  It builds the butterfly identically to how it would build any spread.  The math overstatement is a Phase E issue, not a construction issue, but construction could mitigate it by favoring narrower wings (where the triangular approximation is less harmful).

The iron butterfly POP uses the same formula as iron condors: `1 - |Δ_short_put| - |Δ_short_call|`.  This has the same triangular payoff problem — profit is maximal only when underlying is exactly at the center straddle strike.

---

## PART 2 — CALENDAR/DIAGONAL CONSTRUCTION

### 7  Cross-Expiration Pairing

#### 7.1  Algorithm

```
sorted_exps = sorted(narrowed_universe.expiry_buckets.keys())
for i, near_exp in enumerate(sorted_exps):
    near_map = contracts_by_type(near_bucket, option_type)
    for far_exp in sorted_exps[i+1:]:
        if far_bucket.dte - near_bucket.dte < min_dte_spread: continue
        far_map = contracts_by_type(far_bucket, option_type)
        
        if is_calendar:
            shared_strikes = sorted(near_map ∩ far_map)
            for each shared_strike: build candidate
        
        if is_diagonal:
            for near_strike in near_map:
                for far_strike in far_map:
                    if near == far: continue  # same strike = calendar
                    if |far - near| > max_strike_shift: continue
                    build candidate
```

**Source**: [calendars.py](BenTrade/backend/app/services/scanner_v2/families/calendars.py#L150-L250)

#### 7.2  DTE Spread Constraints

| Parameter | Default | Configurable | Source |
|-----------|---------|-------------|--------|
| `min_dte_spread` | 7 days | Yes, via `context["min_dte_spread"]` | Per-scan |
| Maximum DTE spread | **None** — no upper bound | Not configurable | Implicit from DTE window 7-90 |
| Preferred DTE ratio | **None** | Not supported | All ratios generated equally |

**Maximum effective DTE spread**: 83 days (DTE 7 near + DTE 90 far).  This includes extreme pairings like 7-DTE/90-DTE (ratio 12.9:1! ) alongside sensible ones like 30-DTE/60-DTE (ratio 2:1).

#### 7.3  Expiration Pair Count Estimate (SPY)

With ~15 expirations in DTE 7-90, the pairwise count is C(15,2) = 105 valid (near, far) pairs (minus those with DTE spread < 7).  Typical: **~80-90 pairs**.

For calendars with ~50 shared calls per pair: ~80 × 50 = **~4,000 calendar candidates per scanner key**.

For diagonals with ~50 near strikes × ~50 far strikes (within $10 shift): ~80 × ~100 valid pairs = **~8,000 diagonal candidates per scanner key**.

Well under the 50,000 generation cap — **calendars/diagonals never hit the cap**.

---

### 8  Strike Selection for Calendars

#### 8.1  Selection Method

Calendars use the **intersection of near and far strike sets** for the given option type.  Any strike that has a contract in both expirations qualifies.  There is:

- **No ATM preference** — strikes deep OTM or ITM are equally valid
- **No delta targeting** — ATM calendars and far-OTM calendars generated alike
- **No IV term structure awareness** — calendars profit from near IV > far IV, but construction ignores IV entirely

#### 8.2  Strike Coverage

For SPY with $1 increments near ATM: ~50-80 shared strikes between any two expirations.  This means ~50-80 calendar candidates per expiration pair × ~85 pairs = ~4,000-7,000 candidates.

---

### 9  Strike Selection for Diagonals

#### 9.1  Selection Method

Diagonals cross-product near and far strikes where:
- `near_strike ≠ far_strike` (must differ — same strike = calendar)
- `|far_strike - near_strike| ≤ max_strike_shift` ($10 default)

#### 9.2  Strike Offset Range

Within ±$10 of each near strike.  For SPY with $1 increments, this gives ~20 far strikes per near strike (±10 excluding same-strike).  With ~50 near strikes × ~20 far options = ~1,000 per pair.

#### 9.3  Directional Design

There is **no constraint** on the direction of the strike shift:
- Far leg more OTM than near (typical diagonal for premium capture) ✓
- Far leg more ITM than near (unusual, aggressive) ✓
- Both generated equally

No distinction between bullish diagonals (higher far call strike) and bearish diagonals (lower far put strike).

---

### 10  Quality Evaluation — What Makes a GOOD Calendar

| Quality Criterion | BenTrade Standard | Builder Implementation | Assessment |
|-------------------|-------------------|----------------------|------------|
| Strike at ATM or slightly OTM | Core calendar placement | **Not implemented** — all strikes | MISSING |
| Near 25-35 DTE, far 55-70 DTE (2:1 ratio) | Optimal DTE pairing | **Not implemented** — all pairs DTE 7-90 | MISSING |
| IV near > IV far (positive vega benefit) | Term structure | **Not implemented** — no IV awareness | MISSING |
| Reasonable debit relative to potential | Risk calibration | **Not implemented** — no debit filter | MISSING |
| No large directional move expected | Neutral position | **Not implemented** — no expected-move | MISSING |

---

### 11  Does the Builder Target Calendar Quality?

| Question | Answer |
|----------|--------|
| IV term structure analysis? | **No** — IV from both legs carried on V2Leg but never compared |
| Expected-move filtering? | **No** |
| DTE ratio optimization? | **No** — 7/14 and 7/90 pairings treated equally |
| ATM vs OTM calendar distinction? | **No** — not considered |
| Near/far IV comparison during construction? | **No** — critical for calendar viability but ignored |

---

### Calendar Math: Honest but Unusable

The calendar `family_math()` is notably **honest** about what it can and cannot compute:

```
TRUSTWORTHY:    net_debit, max_loss (≈ debit paid)
SET TO None:    max_profit, breakeven, POP, EV, RoR, Kelly
```

Each `None` field includes an explanatory note:
- `max_profit`: "path-dependent, depends on far-leg residual value"
- `breakeven`: "depends on IV term structure, no closed-form"
- `POP`: "delta approximation does not work for time spreads"
- `EV`: "requires max_profit (unknown at scanner time)"

This is correct and honest — but means **calendars cannot be ranked or compared** by the V2 pipeline.  They pass structural and hygiene checks but lack the key metrics needed for trade selection.

---

## PART 3 — CROSS-FAMILY COMPARISON

### 12  Construction Sophistication Comparison

| Dimension | Butterflies | Calendars |
|-----------|------------|-----------|
| **Delta awareness** | None | None |
| **IV awareness** | None | None (despite IV being critical for calendar viability) |
| **Liquidity awareness** | None | None |
| **Strike targeting** | None (all symmetric triplets) | None (all shared strikes / shifted pairs) |
| **DTE targeting** | None (DTE 7-60 flat) | None (DTE 7-90 flat, all pair ratios) |
| **Expected move** | None | None |
| **Generation cap risk** | Moderate (symmetric constraint limits combinations) | Low (never hits cap) |
| **Quality gate reliance** | Downstream — but downstream also has no quality gates | Downstream — EV=None means no ranking possible |

#### 12.1  Which Family Generates More Noise?

**Butterflies generate more noise** in absolute numbers — the debit butterfly enumerates both call and put variants, and the iron butterfly enumerates all center strikes with every possible wing width.  However, the symmetry constraint naturally limits combinations (only triplets where the midpoint exists as a strike survive).

**Calendars generate proportionally cleaner candidates** because:
- The shared-strike constraint (calendar) or $10 max shift (diagonal) naturally limits the cross-product
- Far fewer candidates total (~4,000-8,000 vs potentially tens of thousands for butterflies)

But calendar candidates are **functionally useless** since EV=None prevents ranking.

#### 12.2  Which Family Would Benefit Most from Construction Intelligence?

**Calendars would benefit most**.  Two changes would transform the family:

1. **IV term structure filter**: Only construct calendars where `near_IV > far_IV` (or at least check the relationship).  This is the fundamental calendar trade thesis — selling richer near-term vol and buying cheaper far-term vol.

2. **DTE ratio constraint**: Require `far_dte / near_dte ∈ [1.5, 3.0]` to eliminate nonsensical pairings like 7-DTE/90-DTE.

For butterflies, the highest-impact change would be **center strike targeting** — restrict centers to within ±5% of spot for neutral strategies.

---

## CONSTRUCTION FLOW DIAGRAMS

### Debit Butterfly Flow

```
Phase A: narrow_chain()
    └─ DTE window [7, 60]
    └─ No strike/moneyness/distance filter
    └─ Output: V2NarrowedUniverse with all strikes
        │
Phase B: _construct_debit_butterflies()
        │
        ├─ For each expiry bucket:
        │   ├─ For each option_type (call, put):
        │   │   ├─ Build strike_map: {strike → contract}
        │   │   └─ O(n²) symmetric triplet enumeration:
        │   │       └─ For (lower, upper) pairs:
        │   │           center = (lower + upper) / 2
        │   │           if center ∈ strike_set AND width ≤ $50:
        │   │               Build 3-leg V2Candidate
        │   │               (legs[0]=long lower, legs[1]=short center, legs[2]=long upper)
        │   │               Set preliminary debit
        │   │               Check generation_cap
        │   │
        │   └─ cap at 50,000
        │
        └─ Output: list[V2Candidate]
            │
Phase C: Structural (shared + bf_leg_count + bf_symmetry + bf_center_is_short)
Phase D/D2: Quote/liquidity presence + hygiene
Phase E: _debit_butterfly_math() — debit, max_profit, max_loss, POP(delta), EV(binary)
Phase F: passed = no reject reasons
```

### Iron Butterfly Flow

```
Phase B: _construct_iron_butterflies()
        │
        ├─ For each expiry bucket:
        │   ├─ Build put_map and call_map
        │   ├─ center_strikes = put_map ∩ call_map (both types required)
        │   └─ For each center:
        │       For each lower_put where lower < center:
        │           width = center - lower
        │           upper_needed = center + width
        │           if upper_needed ∈ call_map AND width ≤ $50:
        │               Build 4-leg V2Candidate
        │               (long put lower, short put center, short call center, long call upper)
        │               Set preliminary credit
        │               Check generation_cap
        │
        └─ Output: list[V2Candidate]
            │
Phase C: Structural (shared + bf_type_balance + bf_center_match + bf_symmetry + bf_strike_ordering)
Phase E: _iron_butterfly_math() — credit, max_profit, max_loss, POP(1-|Δps|-|Δcs|), EV(binary)
```

### Calendar/Diagonal Flow

```
Phase A: narrow_chain()
    └─ DTE window [7, 90]
    └─ No strike filter, multi_expiry=False (standard single-expiry path)
    └─ Output: V2NarrowedUniverse with all strikes across all DTE 7-90 expirations
        │
Phase B: construct_candidates()
        │
        ├─ sorted_exps = sorted(expiry_buckets.keys())
        ├─ For each (near_exp, far_exp) pair where near < far:
        │   │
        │   ├─ If far_dte - near_dte < 7: skip
        │   │
        │   ├─ CALENDAR: shared_strikes = near_map ∩ far_map
        │   │   For each shared strike:
        │   │       Build 2-leg V2Candidate (short near, long far)
        │   │       Set preliminary net_debit
        │   │
        │   ├─ DIAGONAL: cross-product with |shift| ≤ $10
        │   │   For each (near_strike, far_strike):
        │   │       if near == far: skip (= calendar)
        │   │       if |far - near| > $10: skip
        │   │       Build 2-leg V2Candidate
        │   │
        │   └─ Check generation_cap (50,000)
        │
        └─ Output: list[V2Candidate]
            │
Phase C: Structural (shared + cal_same_type + cal_short_long + cal_different_expiry
                      + cal_temporal_order + cal_same_strike / cal_strike_shift)
Phase E: family_math() — net_debit, max_loss only;
         max_profit=None, POP=None, EV=None, RoR=None (all DEFERRED)
Phase F: passed = no reject reasons (but EV=None → cannot rank)
```

---

## FINDINGS

### Finding 5C-01 (HIGH) — Butterfly EV Uses Binary Model on Triangular Payoff

**Location**: `butterflies.py:_debit_butterfly_math()` L610-660, `_iron_butterfly_math()` L760-790  
**Issue**: Both debit and iron butterfly EV use the binary outcome model: `EV = POP × max_profit - (1-POP) × max_loss`.  But butterfly maximum profit only occurs when the underlying is **exactly at the center strike** at expiration.  The POP (probability between inner and outer wings) covers a much wider range where profit is only partial (triangular payoff).  This **systematically overstates butterfly EV**, potentially by 40-60%.  
**Risk**: Butterflies appear more attractive than they actually are, leading to false positive trade signals.  
**Recommendation**: Use `EV = POP × (max_profit / 2) - (1-POP) × max_loss` as a first-order correction (average profit in the profit zone ≈ half max_profit), or implement proper CDF-based integration across the payoff triangle.

### Finding 5C-02 (HIGH) — Calendar/Diagonal EV=None Makes Entire Family Unrankable

**Location**: `calendars.py:family_math()` L370-470  
**Issue**: Calendar `family_math()` correctly sets `max_profit`, `POP`, `EV`, `RoR`, and `Kelly` all to `None` because they require path-dependent modeling.  While this honesty is good engineering, it means **calendars cannot be ranked, compared, or selected** by the V2 pipeline.  They pass structural/hygiene checks and appear in the output, but without EV they are functionally invisible to any ranking system.  
**Risk**: An entire strategy family (4 scanner keys, ~4,000-8,000 candidates per symbol) produces candidates that cannot be evaluated.  Calendar/diagonal spreads are valuable income strategies that are currently dead weight in the pipeline.  
**Recommendation**: Implement at minimum a heuristic max_profit estimate: `max_profit ≈ (far_IV - near_IV) × vega × 100` for calendars, or use a simple time-value differential model.  Even an approximate EV would allow relative ranking within the family.

### Finding 5C-03 (HIGH) — No IV Term Structure Awareness in Calendar Construction

**Location**: `calendars.py:construct_candidates()` L150-250  
**Issue**: The fundamental thesis of a calendar spread is selling richer near-term volatility and buying cheaper far-term volatility.  The construction phase carries IV on both legs (`V2Leg.iv`) but never compares them.  It constructs calendars where `near_IV < far_IV` (unfavorable term structure) just as readily as `near_IV > far_IV` (favorable).  
**Risk**: A significant fraction of calendar candidates are fundamentally non-viable because the term structure works against the trade.  
**Recommendation**: Add an IV differential check during construction: `if near_iv is not None and far_iv is not None and near_iv < far_iv * 0.95: skip` (skip calendars where near vol is meaningfully lower than far vol).

### Finding 5C-04 (MEDIUM) — Butterfly Center Strike Not Proximity-Filtered

**Location**: `butterflies.py:_construct_debit_butterflies()` L170-230, `_construct_iron_butterflies()` L260-320  
**Issue**: The center strike of a butterfly is not filtered by proximity to spot.  Deep OTM butterflies (center 10%+ from spot) are constructed alongside ATM ones.  For income/neutral trading, butterflies far from the current price have virtually no probability of reaching max profit.  
**Risk**: Noise — many unusable candidates.  For SPY at $545 with center at $490, the butterfly has near-zero probability of profiting.  
**Recommendation**: Filter center strikes to within ±5% of spot for debit butterflies, and use `find_nearest_strike()` to restrict iron butterfly centers to the ~5 strikes closest to spot.

### Finding 5C-05 (MEDIUM) — Calendar DTE Pairings Include Extreme Ratios

**Location**: `calendars.py:construct_candidates()` L165-175  
**Issue**: The only DTE constraint is `min_dte_spread ≥ 7 days`.  This generates pairings like 7/90 (12.9:1 ratio), 7/14 (2:1 — too short for theta benefit), and 80/87 (barely different — too narrow for time value differential).  Viable calendar DTE ratios are typically 1.5:1 to 3:1 with near leg ≥ 20 DTE.  
**Risk**: Nonsensical pairings consume generation budget and produce candidates that would never be traded.  
**Recommendation**: Add constraints: `near_dte ≥ 14`, `far_dte / near_dte ∈ [1.5, 3.5]`, or `max_dte_spread ≤ 60`.

### Finding 5C-06 (MEDIUM) — Debit Butterfly Implicit 2× Center Quantity

**Location**: `butterflies.py:_build_debit_butterfly_candidate()` L830-870  
**Issue**: The center leg of a debit butterfly is stored as a single `V2Leg` with `side="short"`, but the actual position is 2× short.  The 2× multiplier is only applied in `_debit_butterfly_math()` during pricing (`2 * center.bid`).  The V2Leg structure has no `quantity` field.  This creates an **implicit contract** — any code consuming the candidate must know that butterfly center legs are 2× by convention.  
**Risk**: Downstream consumers (UI display, risk aggregation, execution) may misrepresent the position size.  
**Recommendation**: Either add a `quantity` field to `V2Leg` (value 2 for butterfly centers, 1 for all others), or document the convention prominently in the canonical contract.

### Finding 5C-07 (MEDIUM) — Iron Butterfly POP Uses Same Formula as Iron Condor

**Location**: `butterflies.py:_iron_butterfly_math()` L780-790  
**Issue**: Iron butterfly POP = `1 - |Δ_short_put| - |Δ_short_call|` — the same formula used for iron condors.  But iron butterflies have the short strikes at the **same** center strike (ATM straddle), while iron condors have short strikes OTM on both sides.  For an ATM iron butterfly, both short deltas are ~0.50, giving POP ≈ 0%.  This is **mathematically correct for probability of expiring between the short strikes** (which are at the same strike, so the probability is effectively zero).  But it fundamentally misrepresents the iron butterfly payoff — the iron butterfly's max profit zone is **at** the center, not **between** the short strikes.  
**Risk**: Iron butterfly POP is always near 0%, making their EV deeply negative.  This is a formula mismatch, not a pricing error.  
**Recommendation**: Use breakeven-based POP for iron butterflies: `POP = |Δ(center-credit)| - |Δ(center+credit)|` (probability between the breakevens, not between the short strikes).

### Finding 5C-08 (MEDIUM) — Diagonal Strike Shift Has No Directionality

**Location**: `calendars.py:construct_candidates()` L195-210  
**Issue**: Diagonal shifts are allowed in both directions (higher and lower than near strike) with no differentiation.  A bullish diagonal (far call at higher strike) and a bearish diagonal (far call at lower strike — unusual) are both constructed.  Additionally, there is no constraint that the far leg be more OTM (the typical diagonal structure for premium capture).  
**Risk**: ~50% of diagonal candidates may have unusual/non-standard strike relationships.  
**Recommendation**: For income diagonals, constrain the far leg to be same-side or more OTM: for calls, `far_strike ≥ near_strike`; for puts, `far_strike ≤ near_strike`.

### Finding 5C-09 (LOW) — Butterfly Generation Cap Unlikely to Be Hit

**Location**: `butterflies.py` (symmetric constraint)  
**Issue**: The symmetric triplet requirement (`center_needed` must exist as a strike) naturally limits the combinatorial explosion.  For SPY with $1 increments, ~100 strikes produce ~2,500 symmetric triplets per expiration per option type.  With 10 expirations × 2 types = ~50,000 — borderline.  For option types with $5 increments (far OTM), far fewer triplets exist.  
**Risk**: Low — the symmetry constraint acts as an implicit cap.  
**Recommendation**: Monitor but no immediate action needed.

### Finding 5C-10 (LOW) — Calendar Dedup Key Includes Both Expirations

**Location**: `calendars.py:family_dedup_key()` L490-505  
**Issue**: The calendar family correctly overrides `family_dedup_key()` to include both `expiration` and `expiration_back`.  This is good — it prevents false dedup of calendars that differ only in the back expiration.  No issue here; noting as a positive finding for completeness.  
**Risk**: None — correctly implemented.

### Finding 5C-11 (LOW) — Butterfly Construction Generates Both Put and Call Variants

**Location**: `butterflies.py:_construct_debit_butterflies()` L175-185  
**Issue**: By default, the debit butterfly builder generates both call and put variants for every triplet (`option_sides = ["call", "put"]`).  This doubles the candidate count.  For income trading, call debit butterflies and put debit butterflies serve different purposes (bullish vs bearish directional), and generating both without context awareness adds noise.  The `option_side` context parameter can filter to one type, but the default is both.  
**Risk**: Low — doubles candidate count but within cap.  
**Recommendation**: Consider using `option_side` in the scanner dispatch to generate only the appropriate type per scanner key.

---

## SUMMARY

| Severity | Count | Key Theme |
|----------|-------|-----------|
| HIGH | 3 | Butterfly EV binary model on triangular payoff; calendar EV=None (unrankable); no IV term structure |
| MEDIUM | 5 | Center strike not proximity-filtered; extreme DTE pairings; implicit 2× quantity; iron butterfly POP formula; diagonal directionality |
| LOW | 3 | Generation cap unlikely hit; dedup key correct; both put/call variants generated |
| **Total** | **11** | |

### Architectural Assessment

Both families share the same fundamental pattern as verticals and IC: **geometric enumeration with no strategy intelligence**.  The builders correctly enumerate all valid geometric combinations (symmetric triplets for butterflies, cross-expiration strike pairings for calendars) and defer all quality assessment to downstream phases.

**Butterflies** have a unique math problem: the binary EV model systematically overstates their attractiveness.  The triangular payoff means actual expected profit is roughly half what the binary model computes.  This is a formula issue, not a construction issue, but it means the entire butterfly family is scored on a fundamentally incorrect basis.

**Calendars** have the opposite problem: they are **too honest**.  By correctly setting EV=None (because max_profit is path-dependent), the family becomes invisible to any ranking system.  The irony is that calendar construction is relatively clean (fewer candidates, natural constraints from strike intersection and DTE spread) — if the family had even a rough EV estimate, it would produce more actionable output than the other families.

The single highest-impact improvement across both families would be **different for each**:
- **Butterflies**: Fix the binary EV model (5C-01) — this affects every butterfly candidate's ranking
- **Calendars**: Add approximate max_profit estimation (5C-02) — this enables the entire family to participate in trade selection

---

**Provenance**: All findings traced from direct code reads of `families/butterflies.py` (950 lines) and `families/calendars.py` (650 lines).
