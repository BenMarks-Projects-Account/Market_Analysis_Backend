# BenTrade Foundation Audit — Pass 2 Fix Specifications
## Computation Layer: Implementation Guide for Copilot Prompts

**Date**: 2026-03-20
**Purpose**: Structured fix specs for every Pass 2 finding. Each spec contains exact files, current behavior, target behavior, pattern to follow, and acceptance criteria.

---

## Fix Priority Tiers

| Tier | Fix IDs |
|------|---------|
| **FN (Fix Now)** | FN-4, FN-5, FN-6 |
| **FS (Fix Soon)** | FS-6, FS-7, FS-8, FS-9, FS-10 |
| **FL (Fix Later)** | FL-8, FL-9, FL-10, FL-11, FL-12, FL-13 |

*Note: IDs continue from Pass 1 (FN-1 through FN-3, FS-1 through FS-5, FL-1 through FL-7)*

---

## FN-4: Fix Engine Confidence Scale at Regime Extraction

### Problem
`regime_service.py` `_extract_engine_confidence()` clamps extracted values to [0.0, 1.0]. Engine confidence is 0-100 scale. Any value ≥1 (all real values: 55, 70, 85) becomes 1.0. Regime sees all engines as maximum confidence.

### Files Involved
| File | Role |
|------|------|
| `app/services/regime_service.py` L355-368 | `_extract_engine_confidence()` — the broken extraction |
| All 5 engine files | Produce confidence on 0-100 scale |

### Current Behavior
```python
# regime_service.py:
conf = data.get("confidence") or data.get("confidence_score")
return max(0.0, min(1.0, float(conf)))  # conf=85 → min(1.0, 85.0) → 1.0
```

### Target Behavior
```python
conf = data.get("confidence") or data.get("confidence_score")
if conf is not None:
    conf = float(conf)
    if conf > 1.0:  # Engine uses 0-100 scale
        conf = conf / 100.0
    return max(0.0, min(1.0, conf))
return None
```

### Acceptance Criteria
- [ ] Engine confidence of 85 → extracted as 0.85 (not 1.0)
- [ ] Engine confidence of 55 → extracted as 0.55
- [ ] Engine confidence of 0.75 (if any engine ever returns 0-1) → preserved as 0.75
- [ ] None → returns None (not 0.0)
- [ ] Block-level confidence averages now reflect actual engine confidence differences
- [ ] Unit test: mock engine outputs with confidence=55 and confidence=85 → verify regime extracts 0.55 and 0.85

### Dependencies
None.

### Estimated Scope
Tiny: ~5 lines changed.

---

## FN-5: Connect V2 Options Ranking to Composite Rank Score

### Problem
V2 pipeline sorts by raw EV descending, creating width bias and ignoring liquidity. `ranking.py` already has a proper composite formula with capital-efficiency normalization and liquidity weighting.

### Files Involved
| File | Role |
|------|------|
| `app/workflows/options_opportunity_runner.py` L1013-1030 | Current raw EV sort |
| `app/services/ranking.py` L80-160 | Existing composite rank with liquidity, EV/risk normalization |

### Current Behavior
```python
# options_opportunity_runner.py:
credible.sort(key=lambda c: (
    -_safe_float((c.get("math") or {}).get("ev")),   # Raw EV DESC
    -_safe_float((c.get("math") or {}).get("ror")),  # RoR tiebreak
    c.get("symbol", ""),
))
```

### Target Behavior
Either:
**Option A (preferred)**: Import and use `ranking.py`'s `compute_rank_score()` for each candidate, then sort by rank_score.
**Option B**: Port the ranking formula into the workflow inline, computing `ev_to_risk = ev / max_loss` as the primary sort key instead of raw EV, with liquidity as secondary.

### Pattern to Follow
```python
# From ranking.py L80-130 (already exists):
edge = minmax_norm(ev_to_risk, 0.00, 0.05)    # EV/max_loss normalized
ror = minmax_norm(return_on_risk, 0.05, 0.50)  # RoR normalized
pop_norm = minmax_norm(pop, 0.50, 0.95)        # POP normalized
liquidity = compute_liquidity_score(trade)       # OI + vol + spread
rank = edge*0.30 + ror*0.22 + pop*0.20 + liquidity*0.18 + tqs*0.10
```

### Acceptance Criteria
- [ ] V2 pipeline uses capital-efficiency-adjusted ranking (EV/max_loss or full composite)
- [ ] Wider spreads no longer systematically outrank narrower ones with better capital efficiency
- [ ] Liquidity quality influences ranking position
- [ ] Calendar/diagonal candidates with EV=None are sorted into a separate section or ranked by net_debit/width
- [ ] Butterfly candidates include a caveat on their EV calculation
- [ ] Unit test: $5-wide spread with EV=$5 ranks above $10-wide spread with EV=$8 when $5-wide has better EV/risk ratio

### Dependencies
None (ranking.py already exists).

### Estimated Scope
Medium: ~40-60 lines to integrate ranking.py or port its formula.

---

## FN-6: Flag Butterfly EV as Approximate

### Problem
Butterfly EV uses binary-outcome model (`POP × max_profit`) on a triangular payoff. Overestimates EV by ~40-50%. Can flip sign — code shows +$10 when true EV is -$48.

### Files Involved
| File | Role |
|------|------|
| `app/services/scanner_v2/families/butterflies.py` L649-707 (debit), L787-830 (iron) | Butterfly math |
| `app/services/scanner_v2/phases.py` | Phase E orchestrator |

### Current Behavior
Butterfly EV computed identically to vertical spreads: `POP × max_profit - (1-POP) × max_loss`. No caveat or adjustment.

### Target Behavior
Two-part fix:
1. **Immediate**: Add a `ev_caveat` field to butterfly candidates: `"EV is approximate — binary-outcome model applied to non-binary payoff. Actual EV is likely 40-50% lower. Use with caution for ranking."` Also add `ev_accuracy: "approximate"` (vs `"standard"` for verticals/ICs).
2. **Better (later)**: Apply an adjustment factor to butterfly EV: `adjusted_ev = ev * 0.55` (conservative estimate of binary-to-triangular correction). Or compute expected payoff using a simplified integral approximation.

### Acceptance Criteria
- [ ] Butterfly candidates have `ev_caveat` field explaining the approximation
- [ ] Butterfly candidates have `ev_accuracy: "approximate"` (verticals/ICs have `"standard"`)
- [ ] Kelly for butterflies is either flagged as `"unreliable"` or not computed
- [ ] Ranking logic either adjusts butterfly EV or sorts butterflies in a separate tier
- [ ] Unit test: verify butterfly candidate output includes caveat fields

### Dependencies
None.

### Estimated Scope
Small: ~20-30 lines for caveats. Medium: ~40-60 lines for EV adjustment.

---

## FS-6: Wire Confidence Into Regime Score Weighting

### Problem
Engine confidence is computed but never weights scores. A low-confidence Flows engine (0.55) gets the same block weight as a high-confidence Breadth engine (0.90).

### Files Involved
| File | Role |
|------|------|
| `app/services/regime_service.py` L793-960 | Block score computation (all 3 blocks) |

### Current Behavior
```python
# Block score = weighted average of inputs with FIXED weights:
weighted_sum += score * fixed_weight
```

### Target Behavior
```python
# Confidence-adjusted weighting:
adjusted_weight = fixed_weight * engine_confidence  # Scale weight by confidence
weighted_sum += score * adjusted_weight
weight_total += adjusted_weight
```

This means a Flows engine with confidence=0.55 contributes 55% of its normal weight, while Breadth with confidence=0.90 contributes 90% of its normal weight. The re-normalization in the weighted average handles the arithmetic.

### Acceptance Criteria
- [ ] Block composites weight engine scores by confidence
- [ ] Low-confidence engines have proportionally less influence
- [ ] FN-4 (scale fix) must be in place first for this to work correctly
- [ ] If all engines have similar confidence, results are nearly identical to current behavior
- [ ] Unit test: two engines with scores 70 and 30 — with equal confidence, composite ≈ 50. With confidences 0.90 and 0.50, composite shifts toward the high-confidence engine.

### Dependencies
- FN-4 (confidence scale fix) MUST be done first

### Estimated Scope
Small-Medium: ~30-50 lines across 3 block computation functions.

---

## FS-7: Fix Flows Engine Gate to Adjust Score

### Problem
Safety gates change the label but NOT the score. Score=78 with crowding<40 outputs "Mixed (Gated)" label but numeric score 78. Regime block uses the score, not the label.

### Files Involved
| File | Role |
|------|------|
| `app/services/flows_positioning_engine.py` L277-330 | Gate logic |

### Current Behavior
```python
# Gates ONLY modify the label:
if crowd_score < 40 and composite >= 55:
    label = "Mixed but Tradable (Gated)"
    # score remains unchanged
```

### Target Behavior
Option A (recommended): When a gate fires, apply a score penalty proportional to the gate violation:
```python
if crowd_score is not None and crowd_score < 40 and composite >= 55:
    gate_penalty = min(15, (40 - crowd_score) * 0.5)  # Up to -15 pts
    composite = max(45, composite - gate_penalty)  # Don't drop below "Cautious"
    label = "Mixed but Tradable (Gated)"
```

Option B: Emit both `raw_score` and `gated_score` in the output, let consumers choose.

Also fix the None bypass: if gating pillars are None, apply a default conservative gate (assume the worst rather than bypassing).

### Acceptance Criteria
- [ ] Gate fires → numeric score is adjusted (not just label)
- [ ] Gate penalty is proportional to how far the pillar is below the threshold
- [ ] Score never drops below the "Cautious" band floor (45) from gating alone
- [ ] When gating pillars are None, gate applies conservatively (not bypassed)
- [ ] Output includes `gate_applied: true/false` and `gate_details` for transparency
- [ ] Unit test: composite=78, crowding=35 → score adjusts to ~75.5, label="Mixed (Gated)"

### Dependencies
None.

### Estimated Scope
Small: ~25-40 lines.

---

## FS-8: Smooth Volatility Engine Bell Curve Discontinuities

### Problem
VIX Rank 30D scoring: ascending branch ends at 95, descending starts at 75 — 20-point gap at the peak (value=50). VIX Percentile and VRP have similar gaps.

### Files Involved
| File | Role |
|------|------|
| `app/services/volatility_options_engine.py` L316-340 (VIX Rank), L336-340 (VIX Pctile), L482-486 (VRP) | Bell curve scoring |

### Current Behavior
```python
# VIX Rank 30D:
if rank <= 50:
    score = _interpolate(rank, 20, 50, 75, 95)   # Ascending: peaks at 95
else:
    score = _interpolate(rank, 50, 70, 75, 55)    # Descending: starts at 75 ← GAP
```

### Target Behavior
Align the peak values:
```python
if rank <= 50:
    score = _interpolate(rank, 20, 50, 75, 88)    # Ascending: peaks at 88
else:
    score = _interpolate(rank, 50, 70, 88, 55)     # Descending: starts at 88 ← SMOOTH
```

Choose a shared peak value (e.g., 88 or 90) that both branches meet at.

### Acceptance Criteria
- [ ] VIX Rank at exactly 50 produces the same score from both branches
- [ ] VIX Percentile at exactly 50 produces the same score from both branches
- [ ] VRP at exactly 1.5 produces the same score from both branches
- [ ] Score surface is continuous (no jumps when sweeping through the peak)
- [ ] Unit test: sweep VIX Rank from 0 to 100 in steps of 1 → verify no score jump > 5 points between adjacent values

### Dependencies
None.

### Estimated Scope
Tiny: ~6 lines changed (2 per bell curve).

---

## FS-9: Smooth Mean Reversion RSI 35 Cliff

### Problem
RSI14 at 35.0 scores 22 pts, RSI14 at 35.1 scores 10 pts — 12-point cliff.

### Files Involved
| File | Role |
|------|------|
| `app/services/mean_reversion_service.py` L664-670 | RSI scoring in oversold component |

### Current Behavior
```python
if rsi14 >= 25 and rsi14 <= 35:
    score += 22  # Sweet spot
elif rsi14 > 35 and rsi14 <= 40:
    score += 10  # Mildly oversold
```

### Target Behavior
Use interpolation to create a smooth transition:
```python
if rsi14 >= 25 and rsi14 <= 30:
    score += 22  # Deep sweet spot — full points
elif rsi14 > 30 and rsi14 <= 40:
    score += round(_interpolate(rsi14, 30, 40, 22, 6), 0)  # Smooth 22→6 over 10 RSI points
```

### Acceptance Criteria
- [ ] RSI 35 no longer has a 12-point discontinuity
- [ ] RSI 25-30 still scores maximum (22)
- [ ] RSI 40 still scores at the lower tier level
- [ ] Transition from sweet spot to mildly oversold is smooth (max 2-3 point change per RSI unit)
- [ ] Unit test: sweep RSI from 20 to 50 → verify no score jump > 4 points between adjacent integer values

### Dependencies
None.

### Estimated Scope
Tiny: ~5-8 lines changed.

---

## FS-10: Add Strategy-Specific Filters to Pullback Swing

### Problem
Pullback swing only has 3 basic filters (price, history, volume). Every symbol with data gets scored. Other scanners have 4-6 strategy-specific filters.

### Files Involved
| File | Role |
|------|------|
| `app/services/pullback_swing_service.py` L250-275 | Current filter logic (inline) |
| `app/services/momentum_breakout_service.py` L573-635 | **Pattern to follow** — has `_apply_filters()` with 6 gates |

### Current Behavior
Only checks: min_price, min_history_bars, min_avg_dollar_vol. No trend, pullback, or RSI checks.

### Target Behavior
Add an `_apply_filters()` method with strategy-specific gates:
1. **Trend filter**: Must be in uptrend or strong_uptrend (trend_state check)
2. **Pullback present**: `pullback_from_20d_high` must be between -1% and -12% (not at highs, not in freefall)
3. **RSI range**: RSI14 must be between 30 and 65 (not overbought, not crashed)
4. **SMA50 above**: Price must be above SMA50 (trend intact)

### Acceptance Criteria
- [ ] `_apply_filters()` method added following momentum_breakout pattern
- [ ] Stocks without uptrend are rejected before scoring
- [ ] Stocks at 20-day highs (no pullback) are rejected
- [ ] Rejection reasons use clear codes (e.g., `NO_UPTREND`, `NO_PULLBACK`, `RSI_OUT_OF_RANGE`)
- [ ] Fewer candidates enter scoring (processing efficiency improvement)
- [ ] No previously valid textbook setups are rejected (thresholds are looser than scoring sweet spots)
- [ ] Unit test: stock with downtrend → rejected with NO_UPTREND; stock with 0% pullback → rejected with NO_PULLBACK

### Dependencies
None.

### Estimated Scope
Medium: ~50-70 lines for filter function + wiring.

---

## FL-8: Unify All-None Default to 50.0

### Problem
4 engines default to 0.0, 2 to 50.0, regime blocks to 50.0 when all data is missing.

### Files Involved
| File | Role |
|------|------|
| `app/services/volatility_options_engine.py` | Currently: 0.0 |
| `app/services/breadth_engine.py` | Currently: 0.0 |
| `app/services/flows_positioning_engine.py` | Currently: 0.0 |
| `app/services/cross_asset_macro_engine.py` | Currently: 0.0 |
| `app/services/liquidity_conditions_engine.py` | Currently: 50.0 ✓ |
| `app/services/news_sentiment_engine.py` | Currently: 50.0 ✓ |

### Target Behavior
All engines: when `_weighted_avg` returns None (all pillars None), set composite to 50.0 (not 0.0) and mark `data_status: "no_data"` in the output. This prevents a no-data situation from producing an extreme label.

### Acceptance Criteria
- [ ] All 6 engines produce 50.0 when all data is missing
- [ ] Output includes `data_status: "no_data"` flag when this occurs
- [ ] Label is "Neutral" or engine-specific neutral equivalent
- [ ] Downstream consumers can distinguish "genuinely scored 50" from "no data, defaulted to 50"

### Dependencies
None.

### Estimated Scope
Small: ~4 lines per engine.

---

## FL-9: Add News Sentiment Confidence Function

### Problem
News engine has no `_compute_confidence()`. Every component defaults to 50 internally. No way to assess data quality.

### Files Involved
| File | Role |
|------|------|
| `app/services/news_sentiment_engine.py` | Needs confidence function added |
| `app/services/flows_positioning_engine.py` L1150-1228 | **Pattern to follow** |

### Target Behavior
Add `_compute_confidence()` that penalizes:
- Low headline count (< 5 headlines → penalty)
- Low source diversity (< 2 sources → penalty)
- High proportion of defaulted components (each component that used its 50-default → penalty)
- No macro context data → penalty

### Acceptance Criteria
- [ ] `_compute_confidence()` added returning (confidence, penalties) tuple
- [ ] Confidence reflects actual data availability (not always 50-100)
- [ ] When all components defaulted to 50 (no real data), confidence is < 30
- [ ] Regime service can extract news confidence (no longer returns None)
- [ ] Unit test: zero headlines → confidence < 30; 20 diverse headlines → confidence > 70

### Dependencies
None.

### Estimated Scope
Medium: ~50-70 lines.

---

## FL-10: Safety Gates for Additional Engines

### Problem
Only Flows engine has safety gates. Other engines can produce high composite scores while individual pillars are dangerously low.

### Files Involved
| File | Role |
|------|------|
| `app/services/volatility_options_engine.py` | Add gate: if VIX Regime pillar > 60 (stress) but composite > 70, gate the label |
| `app/services/breadth_engine.py` | Add gate: if Trend Breadth pillar < 35 (deteriorating) but composite > 55, gate |
| `app/services/cross_asset_macro_engine.py` | Add gate: if Credit pillar < 35 (stressed) but composite > 55, gate |
| `app/services/flows_positioning_engine.py` L277-330 | **Pattern to follow** |

### Acceptance Criteria
- [ ] Each engine has at least 1 safety gate for its most critical pillar
- [ ] Gates follow the Flows engine pattern: check pillar score against threshold, gate label if triggered
- [ ] With FS-7 applied, gates also adjust the numeric score
- [ ] Gate firing is logged and included in output diagnostics

### Dependencies
- FS-7 (gate score adjustment) should be done first to establish the improved pattern

### Estimated Scope
Medium: ~30-40 lines per engine.

---

## FL-11: Implement Stock Scanner Presets for Scoring

### Problem
Presets only affect filters, not scoring. `_score()` functions use hardcoded constants.

### Files Involved
All 4 stock scanner service files.

### Target Behavior
Extract scoring thresholds into the config dict alongside filter thresholds. Presets can then adjust both:
```python
_BALANCED_CONFIG = {
    "min_price": 7, "min_vol": 20_000_000,  # existing filter thresholds
    "score_rsi_sweet_spot": (40, 60),         # NEW: scoring thresholds
    "score_pullback_sweet_spot": (-1, -6),    # NEW
    "score_trend_bonus_strong": 20,           # NEW
}
```

### Acceptance Criteria
- [ ] Scoring thresholds are in the config dict, not inline
- [ ] Changing config values changes scoring behavior
- [ ] Strict preset uses tighter scoring (higher thresholds for points)
- [ ] Wide preset uses looser scoring (lower thresholds)
- [ ] Current balanced behavior is preserved exactly when using balanced config

### Dependencies
None, but best done after FS-9 and FS-10 (which change some scoring logic).

### Estimated Scope
Large: ~100-150 lines per scanner to extract thresholds.

---

## FL-12: Consider VIX Exposure Cap in MI Composite

### Problem
VIX feeds 5 of 6 engines. A spike simultaneously moves all 5, creating correlated movements that look like independent agreement.

### Target Behavior (design exploration, not a concrete fix yet)
Options to explore:
1. **VIX budget**: Compute total VIX-attributable composite movement. If it exceeds a threshold (e.g., >15 points of composite movement from VIX alone), cap the excess.
2. **Decorrelation**: In the regime block synthesis, adjust for known VIX correlation between Structural and Tactical blocks.
3. **VIX isolation**: Route all VIX-dependent scoring through the Volatility engine only. Other engines use VIX only for classification (thresholds), not continuous scoring.

This is a design question, not a code fix. Recommend exploring after Passes 3-4.

### Estimated Scope
Unknown — depends on approach chosen.

---

## FL-13: Credibility Gate Improvements

### Problem
1. $0.05 minimum premium allows 1.2% RoR trades through
2. Fillable-leg check requires ANY leg bid>0, should require SHORT leg bid>0

### Files Involved
| File | Role |
|------|------|
| `app/workflows/options_opportunity_runner.py` L960-1010 | Credibility gate |

### Target Behavior
1. Raise MIN_PREMIUM to $0.10 (or add a secondary check: net_credit/width > 0.02)
2. Change fillable-leg check: for credit strategies, require at least one SHORT leg with bid > 0

### Acceptance Criteria
- [ ] Marginal-quality trades (1.2% RoR) are rejected
- [ ] Credit spreads with short.bid=0 are rejected
- [ ] Valid trades with reasonable premiums are unaffected
- [ ] Unit test: credit spread with short.bid=0, long.bid=0.10 → rejected; credit spread with short.bid=1.50 → passes

### Dependencies
None.

### Estimated Scope
Small: ~15-20 lines.

---

## Cross-Reference: Finding → Fix Mapping

| Audit Finding | Fix ID | Priority |
|--------------|--------|----------|
| 2D-HIGH-1 (confidence scale lost at regime) | FN-4 | Fix Now |
| 2F-HIGH-2, 2F-HIGH-3 (raw EV ranking, ranking.py unused) | FN-5 | Fix Now |
| 2F-HIGH-1 (butterfly EV overestimation) | FN-6 | Fix Now |
| 2B-HIGH-1, 2D-MED-2 (confidence doesn't weight scores) | FS-6 | Fix Soon |
| 2B-MED-1, 2C-HIGH-2 (Flows gate score-label disconnect) | FS-7 | Fix Soon |
| 2C-HIGH-1 (VIX rank bell curve discontinuity) | FS-8 | Fix Soon |
| 2E-HIGH-1 (RSI 35 cliff) | FS-9 | Fix Soon |
| 2E-HIGH-2 (pullback swing no filters) | FS-10 | Fix Soon |
| 2B-HIGH-2 (all-None default mismatch) | FL-8 | Fix Later |
| 2D-HIGH-2 (no news confidence) | FL-9 | Fix Later |
| 2A-H3, Flows gate concept | FL-10 | Fix Later |
| 2E-HIGH-3 (hardcoded scoring) | FL-11 | Fix Later |
| 2A-H2 (VIX cross-engine amplification) | FL-12 | Fix Later |
| 2F-MED-2, 2F-MED-3 (credibility gate gaps) | FL-13 | Fix Later |

---

## Implementation Order (Recommended)

### Wave 1 (Independent, no dependencies — can run in parallel with Pass 1 Wave 1)
- **FN-4** (confidence scale fix) — 5 lines
- **FN-6** (butterfly EV caveat) — 20-30 lines
- **FS-8** (bell curve smoothing) — 6 lines
- **FS-9** (RSI cliff smoothing) — 5-8 lines

### Wave 2 (FN-4 must be done first)
- **FN-5** (V2 ranking integration) — needs ranking.py compatible with V2 candidate shape
- **FS-6** (confidence-weighted regime scoring) — depends on FN-4
- **FS-7** (Flows gate score adjustment) — independent but informed by FS-6 pattern

### Wave 3 (Independent hardening)
- **FS-10** (pullback swing filters)
- **FL-8** (unify all-None defaults)
- **FL-9** (news confidence function)
- **FL-13** (credibility gate improvements)

### Wave 4 (After foundational fixes stabilize)
- **FL-10** (safety gates for other engines)
- **FL-11** (scoring presets)
- **FL-12** (VIX exposure cap — design exploration)

---

*End of Pass 2 Fix Specifications*
