# BenTrade Foundation Audit — Pass 5 Fix Specifications
## Options Scanner Construction: Implementation Guide for Copilot Prompts

**Date**: 2026-03-21
**Purpose**: Structured fix specs for Pass 5 findings — the options scanner construction layer.

---

## Fix Priority Tiers

| Tier | Fix IDs |
|------|---------|
| **FN (Fix Now)** | FN-12, FN-13, FN-14 |
| **FS (Fix Soon)** | FS-19, FS-20, FS-21, FS-22 |
| **FL (Fix Later)** | FL-23, FL-24, FL-25, FL-26, FL-27, FL-28, FL-29 |

*IDs continue from Pass 4 (FN-9 through FN-11, FS-14 through FS-18, FL-17 through FL-22)*

---

## FN-12: Delta Pre-Filtering in Construction

### Problem
Phase B enumerates ALL strikes blindly. For SPY put credit spreads, this generates ~50,000 candidates mostly outside the income delta sweet spot (0.15-0.30). The generation cap hits at DTE ~15-20, excluding the 30-45 DTE theta sweet spot entirely.

### Files Involved
| File | Role |
|------|------|
| `app/services/scanner_v2/families/vertical_spreads.py` L107-210 | Vertical construction loop |
| `app/services/scanner_v2/families/iron_condors.py` L100-230 | IC side construction loops |
| `app/services/scanner_v2/families/butterflies.py` L150-240 | Butterfly center enumeration |
| `app/services/scanner_v2/base_scanner.py` L262 | `build_narrowing_request()` — could add delta to narrowing |

### Current Behavior
```python
# vertical_spreads.py — current construction:
for i in range(len(typed_contracts)):          # ALL strikes
    for j in range(i+1, len(typed_contracts)): # ALL pairs
        if S_high - S_low > max_width: break
        # Build candidate — no delta check
```

### Target Behavior
```python
# Option A (preferred): Filter short strikes by delta BEFORE pairing
DELTA_MIN = 0.05   # Skip ultra-far-OTM (penny options)
DELTA_MAX = 0.40   # Skip near-ATM (too aggressive for income)

# For credit spreads: short leg is closer to ATM
viable_shorts = [
    c for c in typed_contracts
    if c.delta is not None and DELTA_MIN <= abs(c.delta) <= DELTA_MAX
]

for short in viable_shorts:
    for long_candidate in typed_contracts:
        if abs(long_candidate.strike - short.strike) > max_width: continue
        if long_candidate.strike == short.strike: continue
        # Assign short/long per variant config
        # Build candidate
```

```python
# Option B: Add to build_narrowing_request() in base_scanner or family override
def build_narrowing_request(self, *, context=None):
    return V2NarrowingRequest(
        dte_min=self.dte_min,
        dte_max=self.dte_max,
        # NEW: family-specific narrowing
        option_types=["put"],         # credit put spreads only need puts
        moneyness="otm",             # only OTM strikes
        distance_max_pct=0.15,       # max 15% from spot
    )
```

Option A is simpler and more targeted. Option B reduces Phase A volume but doesn't address delta directly.

### Acceptance Criteria
- [ ] Short strikes filtered to `0.05 ≤ |delta| ≤ 0.40` before pairing
- [ ] Candidate count per scanner_key drops from ~50,000 to ~5,000-8,000
- [ ] Generation cap is NOT hit for SPY (all expirations DTE 1-90 are represented)
- [ ] DTE 30-45 candidates appear in output (no longer excluded by FIFO cap)
- [ ] Top-30 by EV contains trades in the 15-25 delta range (income sweet spot)
- [ ] Filter is parameterized (DELTA_MIN/DELTA_MAX) for future preset support
- [ ] Contracts with delta=None are excluded (data quality gate)
- [ ] Applied to all 4 vertical scanner keys and IC side construction
- [ ] Unit test: SPY scan produces candidates across full DTE 1-90 range
- [ ] Unit test: no short strike in output has |delta| > 0.40 or < 0.05

### Dependencies
None — delta is already on every V2OptionContract from chain normalization.

### Estimated Scope
Small: ~15-20 lines per family (verticals + IC + iron butterfly).

### Impact
**This is the single highest-impact fix in the entire 5-pass audit.** It simultaneously solves:
- FIFO DTE bias (all expirations fit under cap)
- Near-ATM EV bias (aggressive trades filtered out)
- Construction noise (80% reduction)
- Downstream processing cost (fewer candidates through 5 validation phases)

---

## FN-13: Per-Expiration Generation Cap Budget

### Problem
FIFO ordering means earliest expirations consume the entire generation cap. IC exhausts budget on 1-2 expirations. Later expirations (including theta sweet spot) are never constructed.

### Files Involved
| File | Role |
|------|------|
| `app/services/scanner_v2/families/vertical_spreads.py` L200-210 | Global cap check |
| `app/services/scanner_v2/families/iron_condors.py` L152-157 | Side cap = √global_cap |

### Current Behavior
```python
# Verticals: single global counter
if seq >= generation_cap: stop  # Entire construction stops

# IC: side cap per type, but only per first expiration
side_cap = int(math.isqrt(generation_cap))  # ~223 per side
# First expiration fills all 223 sides → 49,729 condors → cap hit
```

### Target Behavior
```python
# Verticals: per-expiration budget
num_expirations = len(narrowed_universe.expiry_buckets)
per_exp_cap = max(100, generation_cap // max(num_expirations, 1))

for exp_key in sorted(narrowed_universe.expiry_buckets.keys()):
    exp_seq = 0
    for i in range(len(typed_contracts)):
        for j in range(i+1, len(typed_contracts)):
            if exp_seq >= per_exp_cap: break
            # Build candidate
            exp_seq += 1
            total_seq += 1
    if total_seq >= generation_cap: break  # Safety cap still in place

# IC: per-expiration side cap
per_exp_side_cap = max(20, int(math.isqrt(generation_cap // max(num_expirations, 1))))
# With 10 expirations: √(50000/10) = √5000 ≈ 70 sides per exp → 70² × 10 ≈ 49,000 total
```

### Acceptance Criteria
- [ ] Every expiration in the DTE window has candidates in the output
- [ ] IC candidates span multiple expirations (not just the nearest)
- [ ] Total candidate count still respects the global generation cap
- [ ] Per-expiration budget distributes evenly across available expirations
- [ ] The 30-45 DTE expirations are represented in the candidate pool
- [ ] Unit test: SPY IC produces candidates from at least 5 different expirations

### Dependencies
FN-12 (delta pre-filtering) should ideally be done first — it reduces per-expiration volume so the per-exp budget is more generous.

### Estimated Scope
Small-Medium: ~20-30 lines per family.

---

## FN-14: Minimum Width Filter

### Problem
$1-wide spreads on SPY ($0.03-$0.10 credit, $90-$97 max loss) waste generation cap slots and are marginal income trades.

### Files Involved
| File | Role |
|------|------|
| `app/services/scanner_v2/families/vertical_spreads.py` L149 | `max_width` exists; `min_width` does not |
| `app/services/scanner_v2/families/iron_condors.py` | Same pattern |

### Current Behavior
```python
# No minimum width check:
if S_high - S_low > max_width: break  # Only maximum
# $1-wide spreads always generated
```

### Target Behavior
```python
MIN_WIDTH_DEFAULT = 2.0  # $2 minimum for SPY-class underlyings

min_width = context.get("min_width", MIN_WIDTH_DEFAULT)

for i in range(len(typed_contracts)):
    for j in range(i+1, len(typed_contracts)):
        width = typed_contracts[j].strike - typed_contracts[i].strike
        if width < min_width: continue   # NEW: skip narrow
        if width > max_width: break
        # Build candidate
```

### Acceptance Criteria
- [ ] `min_width` parameter added (default $2)
- [ ] $1-wide SPY spreads no longer generated
- [ ] Configurable via context for cheaper underlyings where $1 is appropriate
- [ ] Generation cap slots freed for higher-quality candidates
- [ ] Unit test: SPY scan produces no candidates with width < $2

### Dependencies
None.

### Estimated Scope
Tiny: ~5 lines per family.

---

## FS-19: IC Delta Balance Constraint

### Problem
IC builder independently constructs put and call sides, then cross-products them with no delta balance check. A condor with 0.08-delta put and 0.40-delta call is treated identically to a balanced 0.20/0.20 condor.

### Files Involved
| File | Role |
|------|------|
| `app/services/scanner_v2/families/iron_condors.py` L200-225 | Cross-product loop |

### Current Behavior
```python
# Cross-product with no balance check:
for put_side in put_sides:
    for call_side in call_sides:
        build_4_leg_candidate(put_side, call_side)  # No delta comparison
```

### Target Behavior
```python
MAX_DELTA_RATIO = 2.5  # Allow some skew but not extreme imbalance

for put_side in put_sides:
    put_delta = abs(put_side.short_contract.delta or 0)
    for call_side in call_sides:
        call_delta = abs(call_side.short_contract.delta or 0)
        # Skip if either delta is zero (data quality)
        if put_delta < 0.01 or call_delta < 0.01: continue
        # Skip if ratio exceeds tolerance
        ratio = max(put_delta, call_delta) / min(put_delta, call_delta)
        if ratio > MAX_DELTA_RATIO: continue
        build_4_leg_candidate(put_side, call_side)
```

### Acceptance Criteria
- [ ] IC candidates have approximately balanced deltas (ratio ≤ 2.5:1)
- [ ] Heavily skewed condors (e.g., 0.05/0.40) are not constructed
- [ ] Balanced condors (e.g., 0.18/0.22) pass through unchanged
- [ ] Delta balance ratio is configurable
- [ ] Unit test: IC candidates all have `max(|Δ_put|, |Δ_call|) / min(...) ≤ 2.5`

### Dependencies
FN-12 (delta pre-filtering) should be done first — it already limits individual side deltas.

### Estimated Scope
Small: ~10-15 lines.

---

## FS-20: Calendar Separate Ranking Track

### Problem
Calendar EV=None → sorts as 0.0 → never appears in top-30. Entire family is invisible.

### Files Involved
| File | Role |
|------|------|
| `app/workflows/options_opportunity_runner.py` L1013-1066 | Ranking and selection |

### Target Behavior
Split selection into family tracks with reserved slots:
```python
# Separate candidates by rankability
ev_rankable = [c for c in credible if c.get("math", {}).get("ev") is not None]
calendars = [c for c in credible if c.get("math", {}).get("ev") is None
             and c.get("family_key") in ("calendars",)]

# Rank EV-trackable candidates by EV (or composite rank from FN-5)
ev_rankable.sort(key=lambda c: -_safe_float(c.get("math", {}).get("ev")))

# Rank calendars by net_debit / max_loss (capital efficiency proxy)
calendars.sort(key=lambda c: (
    _safe_float(c.get("math", {}).get("net_debit", 0))
    / max(_safe_float(c.get("math", {}).get("max_loss", 1)), 0.01)
))

# Reserved slots
EV_SLOTS = 25
CALENDAR_SLOTS = 5
selected = ev_rankable[:EV_SLOTS] + calendars[:CALENDAR_SLOTS]
```

### Acceptance Criteria
- [ ] Calendar/diagonal candidates appear in final output
- [ ] Calendar candidates ranked by a meaningful metric (not EV=0)
- [ ] When fewer than CALENDAR_SLOTS calendars exist, unused slots go to EV-rankable
- [ ] Family distribution visible in output metadata
- [ ] Unit test: output includes calendar candidates when calendars pass credibility gate

### Dependencies
None, but benefits from FL-14 (Pass 3) which also proposed family tracks.

### Estimated Scope
Medium: ~30-50 lines.

---

## FS-21: Butterfly Center Strike Proximity Filter

### Problem
Butterfly center strikes enumerated at ALL strikes — including deep OTM with near-zero probability. Only ATM-area butterflies are practically viable.

### Files Involved
| File | Role |
|------|------|
| `app/services/scanner_v2/families/butterflies.py` L150-240 | Debit butterfly construction |
| `app/services/scanner_v2/families/butterflies.py` L250-320 | Iron butterfly construction |

### Current Behavior
All strikes qualify as center. A center at $450 (18% below SPY at $545) is constructed alongside ATM.

### Target Behavior
```python
MAX_CENTER_DISTANCE_PCT = 0.05  # Center within ±5% of spot

for center_strike in candidate_centers:
    distance_pct = abs(center_strike - underlying_price) / underlying_price
    if distance_pct > MAX_CENTER_DISTANCE_PCT: continue
    # Proceed with wing enumeration from this center
```

### Acceptance Criteria
- [ ] Butterfly center strikes within ±5% of spot only
- [ ] Deep OTM butterfly constructions eliminated (~80% reduction)
- [ ] MAX_CENTER_DISTANCE_PCT is configurable
- [ ] Iron butterfly uses same filter
- [ ] Unit test: SPY at $545 → no butterfly center below $518 or above $572

### Dependencies
None.

### Estimated Scope
Tiny: ~5 lines per butterfly type.

---

## FS-22: DTE Bucket Labeling in Output

### Problem
The top-30 mixes candidates across all DTEs without risk profile differentiation. A 3-DTE and 45-DTE spread appear as comparable alternatives despite fundamentally different risk profiles.

### Files Involved
| File | Role |
|------|------|
| `app/workflows/options_opportunity_runner.py` L264-380 | `_extract_compact_candidate()` |

### Target Behavior
Add `dte_bucket` field to each candidate:
```python
def _classify_dte_bucket(dte):
    if dte is None: return "unknown"
    if dte <= 7: return "weekly"
    if dte <= 21: return "short_term"
    if dte <= 45: return "optimal"      # Theta sweet spot
    return "long_term"

# In _extract_compact_candidate():
compact["dte_bucket"] = _classify_dte_bucket(dte)
```

### Acceptance Criteria
- [ ] Every options candidate has `dte_bucket` field
- [ ] Buckets: weekly (≤7), short_term (8-21), optimal (22-45), long_term (46+)
- [ ] Frontend can group/filter by dte_bucket
- [ ] Output metadata includes dte_bucket distribution counts

### Dependencies
None.

### Estimated Scope
Tiny: ~10 lines.

---

## FL-23: IV Environment Awareness in Construction

### Problem
Scanner runs identically regardless of IV environment. Low-IV environments produce inadequate premium; high-IV environments warrant wider strategy range.

### Target Behavior
Pass IV rank/percentile into construction context. Adjust delta targeting:
- Low IV (rank < 20): tighten to 0.20-0.35 delta (only close-to-ATM has viable premium)
- Normal IV (rank 20-80): standard 0.10-0.35 delta range
- High IV (rank > 80): widen to 0.08-0.40 delta (more opportunities, can go wider)

### Dependencies
IV rank computation requires historical IV data. May need to compute from VIX or per-symbol IV history.

### Estimated Scope
Medium: ~40-60 lines for IV rank computation + context passing.

---

## FL-24: Expected Move Calibration for Wing Placement

### Problem
Wings placed by fixed dollar width with no reference to expected move. Short strikes inside the expected move have meaningful breach probability.

### Target Behavior
Compute expected move in Phase B and filter:
```python
expected_move = underlying_price * iv * math.sqrt(dte / 365)
# For short strikes: require distance from spot ≥ 1.0 × expected_move
min_distance = expected_move * 1.0  # At least 1 standard deviation
if abs(short_strike - underlying_price) < min_distance: skip
```

### Dependencies
IV must be available (it is, on V2Leg). DTE must be per-expiration (it is).

### Estimated Scope
Small-Medium: ~20-30 lines.

---

## FL-25: Phase A Family-Specific Narrowing Overrides

### Problem
No family overrides `build_narrowing_request()`. Phase A passes all strikes through for all families.

### Target Behavior
Each family overrides with appropriate filters:
```python
# VerticalSpreadsV2Scanner:
def build_narrowing_request(self, *, context=None):
    return V2NarrowingRequest(
        dte_min=self.dte_min, dte_max=self.dte_max,
        option_types=["put"] if "put" in self.strategy_id else ["call"],
        moneyness="otm",
        distance_max_pct=0.15,
    )

# CalendarsV2Scanner:
def build_narrowing_request(self, *, context=None):
    return V2NarrowingRequest(
        dte_min=self.dte_min, dte_max=self.dte_max,
        multi_expiry=True,
        near_dte_min=7, near_dte_max=35,
        far_dte_min=30, far_dte_max=90,
    )
```

### Dependencies
FN-12 may reduce the urgency if delta pre-filtering in Phase B already reduces volume sufficiently.

### Estimated Scope
Medium: ~20-30 lines per family override.

---

## FL-26: Calendar IV Term Structure Pre-Check

### Problem
Calendars profit from selling richer near-term vol. Builder ignores IV entirely — generates calendars with unfavorable term structure.

### Target Behavior
```python
# In calendar construction, after identifying near/far legs:
if near_contract.iv is not None and far_contract.iv is not None:
    if near_contract.iv < far_contract.iv * 0.90:  # Near IV materially below far
        continue  # Skip — unfavorable term structure
```

### Acceptance Criteria
- [ ] Calendars with unfavorable IV term structure are not constructed
- [ ] Calendars where near IV ≈ far IV (within 10%) are still generated
- [ ] The 0.90 threshold is configurable
- [ ] IV comparison logged in diagnostics

### Dependencies
None — IV is already on V2Leg.

### Estimated Scope
Tiny: ~5-8 lines.

---

## FL-27: Phase A Liquidity Pre-Filter

### Problem
Zero-OI and zero-volume contracts pass Phase A, creating candidates rejected later by hygiene.

### Target Behavior
```python
# In narrow_strikes(), after option_type filter:
if contract.open_interest is not None and contract.open_interest < min_oi:
    reasons.append("low_oi")
    continue
```

### Dependencies
None.

### Estimated Scope
Tiny: ~5 lines.

---

## FL-28: Calendar Multi-Expiry Path Activation

### Problem
`narrow_expirations_multi()` exists and is well-designed but unreachable — no family sets `multi_expiry=True`.

### Target Behavior
Calendar family overrides `build_narrowing_request()` with `multi_expiry=True` and appropriate near/far DTE windows. Also fix the bucket merge logic (5A-06: `{**near, **far}` overwrites).

### Dependencies
FL-25 (family narrowing overrides) covers this.

### Estimated Scope
Small: ~15 lines for override + ~10 lines for merge fix.

---

## FL-29: Credit-to-Width Ratio Quality Gate

### Problem
No phase filters for adequate premium relative to risk. Trades with 5% credit-to-width ratio pass the entire pipeline.

### Target Behavior
Add to Phase C or as a new Phase C2 "quality gate":
```python
# After Phase E math recomputation:
if family in ("vertical_spreads", "iron_condors"):
    credit_width_ratio = net_credit / width if width > 0 else 0
    if credit_width_ratio < 0.10:  # Less than 10% of width
        reject("v2_credit_below_floor")
```

This implements the reserved `THRESHOLD` reject category that exists in the taxonomy but has zero implementations.

### Acceptance Criteria
- [ ] `v2_credit_below_floor` reject reason implemented
- [ ] Credit/width < 10% candidates rejected
- [ ] Threshold is configurable per family
- [ ] Reject counts tracked in Phase diagnostics

### Dependencies
FN-12 should reduce volume enough that this gate isn't processing 50,000 candidates.

### Estimated Scope
Small: ~15-20 lines.

---

## Cross-Reference: Finding → Fix Mapping

| Audit Finding | Fix ID | Priority |
|--------------|--------|----------|
| 5A-01, 5B-01 (brute-force, no delta) | FN-12 | Fix Now |
| 5B-03, 5B-04, 5D-02 (FIFO cap bias) | FN-13 | Fix Now |
| 5B-05, 5D-11 ($1-wide waste) | FN-14 | Fix Now |
| 5B-06, 5D-05 (IC delta imbalance) | FS-19 | Fix Soon |
| 5D-03, 3B-01 (calendar invisible) | FS-20 | Fix Soon |
| 5C butterfly center not targeted | FS-21 | Fix Soon |
| 5D-04 (mixed DTE risk profiles) | FS-22 | Fix Soon |
| 5D-06 (no IV awareness) | FL-23 | Fix Later |
| 5D-07 (no expected move) | FL-24 | Fix Later |
| 5A-01 (Phase A pass-through) | FL-25 | Fix Later |
| 5C calendar IV ignored | FL-26 | Fix Later |
| 5A-04 (no liquidity pre-filter) | FL-27 | Fix Later |
| 5A-03 (multi-expiry unused) | FL-28 | Fix Later |
| 5B-02 (no quality gates, THRESHOLD unused) | FL-29 | Fix Later |

---

## Implementation Order

### Wave 1 (Highest impact — do first, in this order)
1. **FN-12** (delta pre-filtering) — the single most impactful fix. Reduces noise 80%, eliminates FIFO bias, surfaces income sweet spot.
2. **FN-14** (min width) — trivial, saves cap slots. Do alongside FN-12.
3. **FN-13** (per-exp cap budget) — ensures even DTE distribution. Do after FN-12 (which may make this less critical if delta filtering reduces volume enough).

### Wave 2 (Quality targeting — do after Wave 1 stabilizes)
4. **FS-19** (IC delta balance)
5. **FS-21** (butterfly center proximity)
6. **FS-22** (DTE bucket labeling)
7. **FS-20** (calendar ranking track) — also addresses FL-14 from Pass 3

### Wave 3 (Advanced construction intelligence)
8. **FL-29** (credit-to-width quality gate)
9. **FL-25** (Phase A family overrides)
10. **FL-26** (calendar IV term structure)
11. **FL-24** (expected move calibration)
12. **FL-23** (IV environment awareness)
13. **FL-27** (Phase A liquidity pre-filter)
14. **FL-28** (calendar multi-expiry activation)

---

*End of Pass 5 Fix Specifications*
