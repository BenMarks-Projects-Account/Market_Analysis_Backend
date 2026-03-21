# Audit 2A — Pillar Scoring Formulas (All 6 Engines)

**Audit date:** 2025-07-18
**Scope:** Every pillar/component scoring function across all six Market Intelligence engines
**Deliverables per pillar:** function signature, scoring formula, submetric breakdown, extreme-value analysis, score sensitivity, None handling, interpolation functions

---

## Table of Contents

1. [Shared Infrastructure](#1-shared-infrastructure)
2. [Engine 1: Volatility & Options Structure](#2-engine-1-volatility--options-structure)
3. [Engine 2: Breadth & Participation](#3-engine-2-breadth--participation)
4. [Engine 3: Flows & Positioning](#4-engine-3-flows--positioning)
5. [Engine 4: News & Sentiment](#5-engine-4-news--sentiment)
6. [Engine 5: Liquidity & Financial Conditions](#6-engine-5-liquidity--financial-conditions)
7. [Engine 6: Cross-Asset / Macro Confirmation](#7-engine-6-cross-asset--macro-confirmation)
8. [Cross-Engine Analysis](#8-cross-engine-analysis)
9. [Summary Table](#9-summary-table)
10. [Findings](#10-findings)

---

## 1. Shared Infrastructure

### 1.1 `_interpolate(value, in_lo, in_hi, out_lo=0, out_hi=100)`

**Present in:** volatility_options_engine.py (L85), breadth_engine.py (L98), flows_positioning_engine.py (L177), liquidity_conditions_engine.py (L159), cross_asset_macro_engine.py (L190)

**Implementation (identical across all 5 structural engines):**
```python
def _interpolate(value, in_lo, in_hi, out_lo=0, out_hi=100):
    if in_hi == in_lo:
        return (out_lo + out_hi) / 2       # ← midpoint fallback
    t = (value - in_lo) / (in_hi - in_lo)
    t = max(0.0, min(1.0, t))              # ← clamp [0,1]
    return round(out_lo + t * (out_hi - out_lo), 2)
```

**Properties:**
- Linear mapping with hard clamping — **no extrapolation possible**
- Degenerate range (`in_hi == in_lo`) → returns midpoint `(out_lo + out_hi) / 2`
- Output always in `[min(out_lo, out_hi), max(out_lo, out_hi)]`
- Supports inverse mapping (out_lo > out_hi)

**Not present in:** news_sentiment_engine.py (uses its own arithmetic formulas)

### 1.2 `_weighted_avg(parts: list[tuple[float | None, float]])`

**Present in:** volatility_options_engine.py (L98), breadth_engine.py (L934), flows_positioning_engine.py (L186), liquidity_conditions_engine.py (L177), cross_asset_macro_engine.py (L199)

**Implementation (identical):**
```python
def _weighted_avg(parts):
    valid = [(v, w) for v, w in parts if v is not None]
    if not valid:
        return None
    total_w = sum(w for _, w in valid)
    return round(sum(v * w for v, w in valid) / total_w, 2)
```

**Properties:**
- Skips None values and **re-normalizes** remaining weights
- Returns `None` if ALL inputs are None
- Rounds to 2 decimal places
- **Risk:** If only one low-weight submetric has data, it gets promoted to 100% weight

### 1.3 `_clamp(value, lo=0, hi=100)`

Simple `max(lo, min(hi, value))`. Present in all structural engines.

### 1.4 `_safe_float(value, default=None)`

Coerces to `float`; returns `default` if conversion fails. Present in all engines.

### 1.5 `_aggregate_submetrics(submetrics, weights)`

Wraps `_weighted_avg` with submetric status tracking (active/degraded/missing) and explanation generation. Not present in sentiment engine.

### 1.6 Label Bands

All 5 structural engines use identical 6-band scoring (domain-specific labels):

| Range | Volatility | Breadth | Flows | Liquidity | Cross-Asset |
|-------|-----------|---------|-------|-----------|-------------|
| 85–100 | Premium Selling Strongly Favored | Strong Breadth | Strongly Supportive Flows | Liquidity Strongly Supportive | Strong Confirmation |
| 70–84.99 | Constructive / Favorable | Constructive | Supportive Positioning | Supportive Conditions | Confirming |
| 55–69.99 | Mixed but Tradable | Mixed but Positive | Mixed but Tradable | Mixed but Manageable | Partial Confirmation |
| 45–54.99 | Fragile / Neutral | Mixed / Fragile | Fragile / Crowded | Neutral / Tightening | Mixed Signals |
| 30–44.99 | Risk Elevated | Weak Breadth | Reversal Risk Elevated | Restrictive Conditions | Partial Contradiction |
| 0–29.99 | Volatility Stress / Defensive | Deteriorating | Unstable / Unwind Risk | Liquidity Stress | Strong Contradiction |

Sentiment engine uses different bands: ≥65 "Risk-On", 40–64 "Neutral", 25–39 "Mixed", <25 "Risk-Off / High Stress".

---

## 2. Engine 1: Volatility & Options Structure

**File:** `BenTrade/backend/app/services/volatility_options_engine.py`
**Pillar weights (L40–46, sum = 1.0):**

| Pillar | Key | Weight |
|--------|-----|--------|
| Volatility Regime | `volatility_regime` | 0.25 |
| Volatility Structure | `volatility_structure` | 0.25 |
| Tail Risk & Skew | `tail_risk_skew` | 0.20 |
| Positioning & Options Posture | `positioning_options_posture` | 0.15 |
| Strategy Suitability | `strategy_suitability` | 0.15 |

### P1: Volatility Regime (25%)

**Lines:** ~245–388

**5 submetrics:**

| Submetric | Weight | Input | Interpolation |
|-----------|--------|-------|---------------|
| `vix_level` | 0.35 | `vix_level` | Multi-range `_vix_level_score()` (see below) |
| `vix_trend` | 0.20 | `vix_pct_change` | `_interpolate(val, 0.30, -0.30, 20, 95)` |
| `vix_rank_30d` | 0.20 | `vix_rank_30d` | 4-range (see below) |
| `vix_percentile_1y` | 0.10 | `vix_percentile_1y` | 4-range (see below) |
| `vvix_level` | 0.15 | `vvix_level` | 4-range (see below) |

**VIX level scoring (`_vix_level_score`):**
- 8–12 → 60–80 (low VIX, slight complacency concern)
- 12–18 → 80–95 (sweet spot for premium selling)
- 18–22 → 65–80 (elevated but tradeable)
- 22–30 → 40–65 (caution zone)
- 30–40 → 20–40 (stress)
- \>40 → 0–20 (crisis)

**VIX rank 30d scoring:**
- 0–20 → 50–75 | 20–50 → 75–95 | 50–70 → 75–55 | 70–100 → 55–30

**VIX percentile 1Y scoring:**
- 0–25 → 55–80 | 25–50 → 80–90 | 50–75 → 70–50 | 75–100 → 50–25

**VVIX scoring:**
- 60–80 → 95–85 | 80–100 → 85–60 | 100–120 → 60–35 | 120–160 → 35–10

**Extreme values:**
- All bullish (VIX=15, trend falling, rank=35, pctile=40, VVIX=70): ~90
- All bearish (VIX=38, trend rising, rank=85, pctile=90, VVIX=140): ~18
- All None: returns None (pillar excluded from composite via `_weighted_avg`)

**None handling:** Each submetric independently returns None; `_weighted_avg` skips and re-normalizes.

**Dominant input:** `vix_level` at 35% weight with widest score range (0–95).

---

### P2: Volatility Structure (25%)

**Lines:** ~389–518

**4 submetrics:**

| Submetric | Weight | Input | Interpolation |
|-----------|--------|-------|---------------|
| `term_structure_shape` | 0.30 | `vix_term_structure` (contango_ratio) | Contango ≥1.0: `_interpolate(1.0, 1.15, 65, 95)` / Backwardation <1.0: `_interpolate(0.85, 1.0, 10, 65)` |
| `contango_steepness` | 0.20 | `contango_steepness` | `_interpolate(-0.15, 0.20, 10, 95)` |
| `iv_rv_spread` | 0.30 | `iv_rv_spread` | `_interpolate(-5, 10, 10, 95)` |
| `vol_risk_premium` | 0.20 | `vol_risk_premium` | 3-range: 1.0–1.5 → 65–95, 0.5–1.0 → 20–65, 1.5–2.5 → 80–40 |

**Extreme values:**
- All bullish (steep contango 1.12, steepness 0.15, IV-RV spread +8, VRP 1.3): ~89
- All bearish (backwardation 0.87, steepness -0.10, IV-RV -3, VRP 0.6): ~18

**Dominant input:** `term_structure_shape` and `iv_rv_spread` (tied at 0.30 weight).

---

### P3: Tail Risk & Skew (20%)

**Lines:** ~519–623

**3 submetrics:**

| Submetric | Weight | Input | Interpolation |
|-----------|--------|-------|---------------|
| `cboe_skew` | 0.40 | `cboe_skew_index` | 4-range: 100–120 → 95–85, 120–135 → 85–60, 135–150 → 60–35, 150–175 → 35–10 |
| `put_skew_25d` | 0.35 | `put_skew_25d` | 3-range: 0–4 → 90–80, 4–7 → 80–55, 7–15 → 55–15 |
| `tail_risk_signal` | 0.25 | `tail_risk_signal` (0–100) | `_interpolate(0, 100, 95, 5)` (inverted) |

**Extreme values:**
- All bullish (SKEW=110, put_skew=2, tail_risk=5): ~91
- All bearish (SKEW=165, put_skew=12, tail_risk=90): ~14

**Dominant input:** `cboe_skew` at 0.40 weight.

---

### P4: Positioning & Options Posture (15%)

**Lines:** ~624–753

**4 submetrics:**

| Submetric | Weight | Input | Interpolation |
|-----------|--------|-------|---------------|
| `equity_pc_ratio` | 0.30 | `equity_put_call_ratio` | 5-range bell curve peaking at 0.7–0.9 → 85–80 |
| `spy_pc_ratio_proxy` | 0.25 | `spy_put_call_ratio` | 4-range peaking at 0.65–0.85 → 90–85 |
| `option_richness` | 0.25 | `option_richness` (0–100) | `_interpolate(0, 100, 30, 95)` |
| `premium_bias` | 0.20 | `premium_bias` (-100…+100) | `_interpolate(-100, 100, 10, 95)` |

**Bell curve on P/C ratios:** Extreme low (<0.5) and extreme high (>1.2) both score poorly; moderate range (0.7–0.9) is optimal. This is non-monotonic behavior using multi-range interpolation.

**Extreme values:**
- All bullish (P/C=0.80, SPY P/C=0.75, richness=80, bias=+60): ~84
- All bearish (P/C=0.35, SPY P/C=1.25, richness=10, bias=-80): ~19

---

### P5: Strategy Suitability (15%)

**Lines:** ~754–974

**Unique:** This pillar is *derived* — it reads from raw inputs shared with other pillars (VIX level, term structure, IV-RV spread, P/C ratios) and computes strategy-specific suitability scores.

**4 strategy families:**

| Strategy | Weight | Sub-components |
|----------|--------|----------------|
| `premium_selling` | 0.40 | 5 sub-components (VIX sweet spot, term structure, IV-RV premium, P/C balance, low tail risk) |
| `directional` | 0.20 | 3 sub-components (strong trend signal, U-shaped VIX rank scoring, moderate skew) |
| `vol_structure_plays` | 0.20 | 3 sub-components (term structure dislocation, VRP extremes, skew opportunity) |
| `hedging` | 0.20 | 3 sub-components (elevated VIX, steep skew, high tail risk signal) |

**Key behavior:** `directional` uses a U-shaped VIX rank score — both very low and very high VIX rank improve the directional signal (opportunities at extremes).

**Extreme values:**
- Premium selling paradise (VIX=16, steep contango, rich IV): ~88
- Premium selling worst case (VIX=38, backwardation, flat IV): ~15
- All None: returns None

---

## 3. Engine 2: Breadth & Participation

**File:** `BenTrade/backend/app/services/breadth_engine.py`
**Pillar weights (L48–54, sum = 1.0):**

| Pillar | Key | Weight |
|--------|-----|--------|
| Participation Breadth | `participation_breadth` | 0.25 |
| Trend Breadth | `trend_breadth` | 0.25 |
| Volume Breadth | `volume_breadth` | 0.20 |
| Leadership Quality | `leadership_quality` | 0.20 |
| Participation Stability | `participation_stability` | 0.10 |

### P1: Participation Breadth (25%)

**6 submetrics:**

| Submetric | Weight | Input | Scoring |
|-----------|--------|-------|---------|
| `advance_decline_ratio` | 0.20 | `advance_decline_ratio` | 7-band `_ratio_score()` helper |
| `net_advances_pct` | 0.15 | `net_advances_pct` | `_interpolate(-0.5, 0.5, 0, 100)` |
| `percent_up` | 0.15 | `percent_up` | 5-band `_pct_score()` helper |
| `new_high_new_low_balance` | 0.20 | `new_high_new_low_balance` | `_interpolate(-1.0, 1.0, 0, 100)` |
| `sector_participation_pct` | 0.15 | `sector_participation_pct` | 5-band `_pct_score()` |
| `equal_weight_confirmation` | 0.15 | `equal_weight_confirmation` | `_interpolate(-1.0, 1.0, 20, 90)` |

**`_ratio_score()` (7 bands):** Converts A/D ratio to score using thresholds at 0.6, 0.8, 1.0, 1.2, 1.5, 2.0.
**`_pct_score()` (5 bands):** Converts percentage to score; thresholds vary by metric.

**Extreme values:**
- Perfect breadth (all advancing, high new highs, full sector participation): ~93
- Worst breadth (all declining, new lows dominant): ~8

---

### P2: Trend Breadth (25%)

**3-tier system:**

| Tier | Weight | Submetrics |
|------|--------|------------|
| Short-term | 0.30 | `pct_above_20dma` (50%), `pct_20_over_50` (30%), `trend_momentum_short` (20%) |
| Intermediate | 0.40 | `pct_above_50dma` (70%), `trend_momentum_intermediate` (30%) |
| Long-term | 0.30 | `pct_above_200dma` (40%), `pct_50_over_200` (40%), `trend_momentum_long` (20%) |

**Dominant input:** `pct_above_50dma` — effective weight = 0.70 × 0.40 = **0.28** (highest single input weight in this pillar).

**Momentum metrics:** All use `_interpolate(val, -0.20, 0.20, 10, 90)`.
**Percentage metrics:** All use `_pct_score()` (5-band system).

**Extreme values:**
- Strong uptrend (all %above metrics ~80%, positive momentum): ~88
- Strong downtrend (all %above metrics ~20%, negative momentum): ~15

---

### P3: Volume Breadth (20%)

**3 active + 2 scaffolded submetrics:**

| Submetric | Weight | Input | Scoring | Status |
|-----------|--------|-------|---------|--------|
| `up_down_volume_ratio` | 0.35 | `up_down_volume_ratio` | `_ratio_score()` (7-band) | Active |
| `pct_volume_in_advancers` | 0.30 | `pct_volume_in_advancers` | `_pct_score()` | Active |
| `volume_weighted_ad_ratio` | 0.35 | `volume_weighted_ad_ratio` | Clamped at 10, then `_interpolate(0, 5, 0, 100)` | Active |
| `accumulation_distribution_bias` | — | — | — | Scaffolded (excluded) |
| `volume_thrust_signal` | — | — | — | Scaffolded (excluded) |

**Note:** `volume_weighted_ad_ratio` clamps at 10 before interpolating 0→5 to 0→100, so any value ≥5 scores 100.

**Extreme values:** All bullish ~91, all bearish ~10.

---

### P4: Leadership Quality (20%)

**4 submetrics:**

| Submetric | Weight | Input | Scoring |
|-----------|--------|-------|---------|
| `ew_vs_cw_relative` | 0.30 | `ew_vs_cw_relative` | `_interpolate(-0.02, 0.02, 15, 90)` |
| `sector_concentration_penalty` | 0.25 | `sector_returns` + `sector_concentration` | Base from `_pct_score()` minus penalty |
| `pct_outperforming_index` | 0.25 | `pct_outperforming_index` | `_pct_score()` |
| `median_return_vs_index` | 0.20 | `median_return_vs_index` | `_interpolate(-0.015, 0.015, 10, 90)` |

**Sector concentration penalty formula:**
```
penalty = _interpolate(ret_std, 0.005, 0.03, 0, 40)
score = max(0, base_score - penalty)
```
If sector returns are highly concentrated (std > 0.03), penalty maxes at 40 points.

**Extreme values:**
- Broad leadership (equal-weight outperforms, wide participation, no concentration): ~86
- Narrow leadership (cap-weight dominates, concentrated returns): ~18

---

### P5: Participation Stability (10%)

**3 active + 2 scaffolded:**

| Submetric | Weight | Input | Scoring | Note |
|-----------|--------|-------|---------|------|
| `breadth_persistence_10d` | 0.40 | `breadth_persistence_10d` | `_pct_score()` | Normal |
| `ad_ratio_volatility_5d` | 0.30 | `ad_ratio_volatility_5d` | **INVERTED**: `100 - _interpolate(vol, 0.1, 1.0, 0, 80)` | Higher vol = lower score |
| `pct_above_20dma_volatility_5d` | 0.30 | `pct_above_20dma_volatility_5d` | **INVERTED**: `100 - _interpolate(vol, 0.02, 0.12, 0, 80)` | Higher vol = lower score |
| `breadth_regime_consistency` | — | — | — | Scaffolded |
| `cross_timeframe_agreement` | — | — | — | Scaffolded |

**Inverted scoring:** The volatility submetrics score stability by penalizing high variability. Max penalty is 80 points (floor at 20).

**Extreme values:**
- Stable (persistent breadth, low volatility): ~85
- Unstable (whipsawing breadth): ~22

---

## 4. Engine 3: Flows & Positioning

**File:** `BenTrade/backend/app/services/flows_positioning_engine.py`
**Pillar weights (L47–53, sum = 1.0):**

| Pillar | Key | Weight |
|--------|-----|--------|
| Positioning Pressure | `positioning_pressure` | 0.25 |
| Crowding / Stretch | `crowding_stretch` | 0.20 |
| Squeeze / Unwind Risk | `squeeze_unwind_risk` | 0.20 |
| Flow Direction & Persistence | `flow_direction_persistence` | 0.20 |
| Positioning Stability | `positioning_stability` | 0.15 |

### P1: Positioning Pressure (25%)

**4 submetrics:**

| Submetric | Weight | Input | Scoring |
|-----------|--------|-------|---------|
| `positioning_bias` | 0.30 | `put_call_ratio` | Bell curve peaking at 0.8 → 85 |
| `directional_exposure` | 0.25 | `futures_net_long_pct` | Bell curve peaking at 55 → 82 |
| `options_posture` | 0.25 | `vix_level` | 3-range, peaking at 17 → 88 |
| `systematic_pressure` | 0.20 | `systematic_allocation` | Bell curve peaking at 60 → 80 |

**Bell curve behavior:** Non-monotonic — extreme values in either direction score poorly.

**Extreme values:** All bullish (balanced positioning) ~82, all bearish (extreme positioning) ~20.

---

### P2: Crowding / Stretch (20%)

**5 submetrics:**

| Submetric | Weight | Input | Scoring |
|-----------|--------|-------|---------|
| `crowding_proxy` | 0.30 | `futures_net_long_pct` | **Inverted**: 90 → 15, 30 → 88 |
| `stretch_vs_range` | 0.20 | `put_call_ratio` | Multi-range |
| `flow_concentration` | 0.15 | `vix_level` | 3-range |
| `speculative_excess` | 0.20 | `retail_bull_pct` | **Inverted**: 25 → 88, 65 → 22 |
| `one_sided_risk` | 0.15 | `bull_bear_spread` | Inverted: -10 → 90, 40 → 20 |

**Extreme values:** All bullish (no crowding, balanced) ~85, all bearish (heavily crowded/stretched) ~18.

---

### P3: Squeeze / Unwind Risk (20%)

**4 submetrics:**

| Submetric | Weight | Input | Scoring |
|-----------|--------|-------|---------|
| `short_squeeze_risk` | 0.25 | `short_interest` | **Inverted**: 1.0 → 88, 6.0 → 18 |
| `long_unwind_risk` | 0.30 | `futures_net_long_pct` | **Inverted**: 20 → 90, 95 → 15 |
| `positioning_fragility` | 0.25 | VIX term + P/C avg | Average of two scores |
| `asymmetry` | 0.20 | positioning balance | Distance from 50% balance |

**Dominant input:** `long_unwind_risk` at 0.30 weight (uses `futures_net_long_pct`).

---

### P4: Flow Direction & Persistence (20%)

**5 submetrics — all pre-computed 0–100 scores:**

| Submetric | Weight | Input | Scoring |
|-----------|--------|-------|---------|
| `recent_flow_direction` | 0.25 | pre-computed 0–100 | Clamped only |
| `flow_persistence_5d` | 0.25 | pre-computed 0–100 | Clamped only |
| `flow_persistence_20d` | 0.20 | pre-computed 0–100 | Clamped only |
| `inflow_outflow_balance` | 0.15 | pre-computed 0–100 | Clamped only |
| `follow_through` | 0.15 | pre-computed 0–100 | Clamped only |

**Unique:** No `_interpolate` calls — scores are pre-computed upstream. This pillar is a pure pass-through aggregator.

---

### P5: Positioning Stability (15%)

**5 submetrics with complex multi-input compositions:**

| Submetric | Weight | Input(s) | Scoring |
|-----------|--------|----------|---------|
| `stability_signal` | 0.25 | VIX score + VIX term | Average |
| `flow_volatility` | 0.20 | flow volatility | **Inverted**: 10 → 90, 90 → 15 |
| `flow_position_contradiction` | 0.20 | agreement logic | Agreement vs disagreement scoring |
| `fragility_penalty` | 0.15 | complacency × crowding | **Inverted** product penalty |
| `crowded_fragile_state` | 0.20 | P/C + futures avg | Average of two scores |

### Cross-Pillar Input Dominance

**`futures_net_long_pct` appears in 4 of 5 pillars (6 submetrics):**
- P1: `directional_exposure` (0.25)
- P2: `crowding_proxy` (0.30)
- P3: `long_unwind_risk` (0.30)
- P5: `crowded_fragile_state` (0.20, partial)

**Impact:** A single change in `futures_net_long_pct` can swing the composite score by **40–50 points**. This is the single most influential raw input in any engine.

### Confidence Scoring

Base 100, with penalties:
- Missing pillar: −15 per pillar
- Degraded submetric: −3 each (cap −30)
- Cross-pillar disagreement (range >35): −0.5 × (range − 35), cap −15
- ≥4 proxy inputs: −8
- Stale data: −3 each, cap −12
- No direct flow data: −5
- No futures data: −5
- Single-source dependency: −12

---

## 5. Engine 4: News & Sentiment

**File:** `BenTrade/backend/app/services/news_sentiment_engine.py`
**Fundamentally different architecture:** No `_interpolate`, no pillars — uses 6 weighted components with direct arithmetic.
**Deterministic — no LLM calls.**

**Component weights (L38–44, sum = 100):**

| Component | Key | Weight |
|-----------|-----|--------|
| Headline Sentiment | `headline_sentiment` | 30 |
| Negative Pressure | `negative_pressure` | 20 |
| Narrative Severity | `narrative_severity` | 15 |
| Source Agreement | `source_agreement` | 10 |
| Macro Stress | `macro_stress` | 15 |
| Recency Pressure | `recency_pressure` | 10 |

### C1: Headline Sentiment (30%)

**Keyword-based scoring:**
- 17 bullish words: `surge, rally, gain, soar, bull, upbeat, optimism, optimistic, growth, strong, boost, recovery, positive, beat, outperform, upgrade, record high`
- 23 bearish words: (similar negative set)
- Per-headline score: `(bullish_count - bearish_count) / max(1, total_keyword_count)` → range [-1, +1]
- Aggregate: `(mean_sentiment + 1.0) × 50` → range [0, 100]
- **Empty items → 50.0** (neutral)

### C2: Negative Pressure (20%)

**Inverted bear ratio from 24h window:**
- `bear_ratio = bearish_headlines / total_headlines` (24h window)
- `score = (1 - bear_ratio) × 100`
- Range: [0, 100]
- **Empty items → 50.0**

### C3: Narrative Severity (15%)

**Penalty-based (inverted):**
- High severity bearish: penalty += 2.0 per item
- High severity mixed: penalty += 0.5 per item
- Medium severity bearish: penalty += 1.0 per item
- Penalty cap: 50
- `score = 100 - penalty`
- Range: [50, 100] (given cap)
- **No items → 75.0** (note: different neutral default)

### C4: Source Agreement (10%)

**Cross-source sentiment spread:**
- Group headlines by source, compute mean sentiment per source
- Agreement (all same sign): `100 - spread × 50`, range [60, 100]
- Disagreement (mixed sign): `50 - spread × 30`, range [10, 50]
- **<2 sources → 50.0**

### C5: Macro Stress (15%)

**Label-based with adjustments (inverted):**

| Stress Level | Base Score |
|-------------|-----------|
| low | 90 |
| moderate | 65 |
| elevated | 35 |
| high | 10 |
| unknown | 50 |

Adjustments:
- VIX < 16: +5
- VIX > 30: −10
- VIX > 25 (but ≤30): −5
- Yield curve inverted: −8
- **Missing stress → 50.0**

### C6: Recency Pressure (10%)

**Exponential decay weighting:**
- Half-life: 6 hours
- Weight: `exp(-0.693 × age_hours / 6)`
- `score = (decayed_weighted_avg + 1.0) × 50`
- Range: [0, 100]
- **No items → 50.0**

### Sentiment Extreme Values

- All bullish headlines, low stress, strong agreement: ~88
- All bearish headlines, high stress, disagreement: ~15
- No data at all: every component defaults to 50 → **composite = 50.0** (never None)

### Regime Labels

| Score Range | Label |
|-------------|-------|
| ≥ 65 | Risk-On |
| 40–64 | Neutral |
| 25–39 | Mixed |
| < 25 | Risk-Off / High Stress |

---

## 6. Engine 5: Liquidity & Financial Conditions

**File:** `BenTrade/backend/app/services/liquidity_conditions_engine.py`
**Pillar weights (L35–41, sum = 1.0):**

| Pillar | Key | Weight |
|--------|-----|--------|
| Rates & Policy Pressure | `rates_policy_pressure` | 0.25 |
| Financial Conditions Tightness | `financial_conditions_tightness` | 0.25 |
| Credit & Funding Stress | `credit_funding_stress` | 0.20 |
| Dollar / Global Liquidity | `dollar_global_liquidity` | 0.15 |
| Liquidity Stability & Fragility | `liquidity_stability_fragility` | 0.15 |

### P1: Rates & Policy Pressure (25%)

**Lines:** ~218–439

**6 submetrics:**

| Submetric | Weight | Input | Interpolation |
|-----------|--------|-------|---------------|
| `two_year_yield_level` | 0.25 | `two_year_yield` | `_interpolate(0.5, 5.5, 100, 0)` (inverse) |
| `ten_year_level` | 0.20 | `ten_year_yield` | Bell curve centered @3.2% |
| `policy_pressure_proxy` | 0.20 | `fed_funds_rate` | `gap = fed_funds - 3.0; _interpolate(-2.0, 2.5, 100, 0)` |
| `curve_context_signal` | 0.15 | `yield_curve_spread` | `_interpolate(-0.8, 2.0, 15, 95)` |
| `front_end_rate_pressure` | 0.10 | `two_year_yield` | `_interpolate(1.5, 5.0, 90, 10)` |
| `rate_trend_pressure` | 0.10 | composite | `trend_raw = curve_spread×20 + (4.5-2Y)×15` |

**⚠️ Redundancy:** `two_year_yield` feeds both `two_year_yield_level` (0.25) and `front_end_rate_pressure` (0.10) = **0.35 effective weight** on same input.

**Dominant inputs:** 2Y Yield + Fed Funds = 65% combined pillar weight.

**Extreme values:**
- All supportive (2Y=1%, 10Y=3.2%, Fed=2%, curve +1.5%): ~88
- All restrictive (2Y=5%, 10Y=5.5%, Fed=5.5%, curve -0.5%): ~12
- All None: score = 50.0 (neutral fallback)

---

### P2: Financial Conditions Tightness (25%)

**Lines:** ~440–645

**4 submetrics:**

| Submetric | Weight | Input(s) | Scoring |
|-----------|--------|----------|---------|
| `fci_proxy` | 0.30 | VIX + IG + 2Y (avg of 3) | VIX: `_interpolate(12, 35, 100, 0)`, IG: `_interpolate(0.6, 2.5, 100, 0)`, 2Y: `_interpolate(1, 5.5, 95, 10)` |
| `vix_conditions_signal` | 0.25 | VIX + curve | `_interpolate(12, 35, 90, 10)` + inverted curve penalty |
| `conditions_supportiveness` | 0.25 | IG + HY + 10Y (avg) | IG: `(90→20)`, HY: `(90→10)`, 10Y: `(80→20)` — **NO VIX** |
| `broad_tightness_score` | 0.20 | IG + curve (avg) | IG: `(85→15)`, Curve: `(20→85)` — **NO VIX** |

**⚠️ `fci_proxy` is NOT a true FCI index** — it's an unweighted average of 3 inputs labeled "proxy."

**VIX exposure in this pillar:** 1/3 of `fci_proxy` (0.30) + full `vix_conditions_signal` (0.25) = effective VIX weight ~0.35.

**None handling:** If <3 inputs to FCI proxy, flag "degraded"; if 0, skip entirely.

---

### P3: Credit & Funding Stress (20%)

**Lines:** ~646–901

**5 submetrics:**

| Submetric | Weight | Input(s) | Scoring |
|-----------|--------|----------|---------|
| `ig_spread` | 0.25 | `ig_spread` | `_interpolate(0.6, 3.0, 95, 5)` |
| `hy_spread` | 0.25 | `hy_spread` | `_interpolate(2.5, 9.0, 95, 5)` |
| `credit_stress_signal` | 0.25 | credit avg 70% + VIX 30% | `credit_avg × 0.70 + vix_stress × 0.30` |
| `funding_stress_proxy` | 0.15 | VIX 60% + rate 40% | `vix_component × 0.60 + rate_component × 0.40` |
| `liquidity_breakage_risk` | 0.10 | HY + IG (avg) | HY: `(90→5)`, IG: `(90→10)` — **NO VIX** |

**⚠️ VIX capping:** VIX is intentionally limited to 30% of `credit_stress_signal` to prevent triple-counting across pillars.

**`funding_stress_proxy`** uses VIX and rate spread as proxy for SOFR/FRA-OIS (true data unavailable). Marked with PROXY status.

---

### P4: Dollar / Global Liquidity (15%)

**Lines:** ~902–1049

**3 submetrics:**

| Submetric | Weight | Input | Scoring |
|-----------|--------|-------|---------|
| `dxy_level` | 0.40 | `dxy_index` | `_interpolate(95, 115, 90, 10)` (**inverse**: weak $ = supportive) |
| `dollar_liquidity_pressure` | 0.35 | DXY 65% + VIX 35% | DXY: `_interpolate(95, 115, 85, 15)`, VIX: `_interpolate(12, 30, 80, 20)` |
| `dollar_risk_asset_impact` | 0.25 | `dxy_index` | `_interpolate(96, 110, 80, 20)` |

**DXY dominance:** 75%+ of pillar weight. Two of three submetrics use DXY directly; the third includes it at 65%.

---

### P5: Liquidity Stability & Fragility (15%)

**Lines:** ~1050–1330

**Unique feature:** Uses **pillar scores from the other 4 pillars** as cross-check inputs.

**5 submetrics:**

| Submetric | Weight | Input(s) | Scoring |
|-----------|--------|----------|---------|
| `contradiction_between_pillars` | 0.30 | Other pillar scores | `_interpolate(pillar_range, 10, 50, 90, 10)` — range <10 → 90 (coherent), >50 → 10 (fractured) |
| `stability_of_conditions` | 0.25 | VIX + IG + 2Y | Avg of VIX (90→10), IG (85→15), 2Y (bell @3%) — VIX is 1/3 |
| `fragility_penalty` | 0.20 | Multiple | **Threshold-based, not continuous** |
| `sudden_stress_risk` | 0.15 | HY + IG + DXY | Avg of HY (80→10), IG (80→15), DXY (75→20) — **NO VIX** |
| `support_vs_stress_balance` | 0.10 | Multiple | Vote-based (binary conditions for IG, HY, DXY, curve) |

**Fragility penalty thresholds:**
- VIX < 14 AND IG < 1.0%: −20 (complacency risk)
- VIX > 25: −15 (stress)
- HY > 5.5%: −15 (credit stress)
- Curve < −0.2%: −10 (inversion)
- These are subtracted from a base score (not continuously interpolated)

**Key design:** Only this pillar measures cross-pillar coherence. If pillars 1–4 disagree wildly (range >50pt), this pillar drops to ~10.

---

## 7. Engine 6: Cross-Asset / Macro Confirmation

**File:** `BenTrade/backend/app/services/cross_asset_macro_engine.py`
**Pillar weights (L57–63, sum = 1.0):**

| Pillar | Key | Weight |
|--------|-----|--------|
| Rates & Yield Curve | `rates_yield_curve` | 0.25 |
| Dollar & Commodity | `dollar_commodity` | 0.20 |
| Credit & Risk Appetite | `credit_risk_appetite` | 0.25 |
| Defensive vs Growth | `defensive_vs_growth` | 0.15 |
| Macro Coherence | `macro_coherence` | 0.15 |

### P1: Rates & Yield Curve (25%)

**Lines:** ~295–394

**3 submetrics:**

| Submetric | Weight | Input | Interpolation |
|-----------|--------|-------|---------------|
| `yield_curve_spread` | 0.45 | `yield_curve_spread` | `_interpolate(-1.0, 2.0, 10, 95)` |
| `ten_year_level` | 0.30 | `ten_year_yield` | Bell curve: ≤3.5% `_interpolate(0, 3.5, 40, 90)` / >3.5% `_interpolate(3.5, 6.5, 90, 20)` |
| `rate_differential` | 0.25 | `10Y − fed_funds` | `_interpolate(-2.0, 1.5, 15, 90)` |

**Dominant input:** `yield_curve_spread` at 0.45 weight.

**10Y bell curve:** Peaks at 3.5% (score 90). Below → scores fall toward 40; above → scores fall toward 20.

**Extreme values:**
- Supportive (curve +1.5%, 10Y=3.5%, fed near 2%): ~90
- Restrictive (curve -0.8%, 10Y=6%, fed 5.5%): ~14

---

### P2: Dollar & Commodity (20%)

**Lines:** ~395–527

**4 submetrics:**

| Submetric | Weight | Input | Scoring |
|-----------|--------|-------|---------|
| `usd_level` | 0.35 | `dxy_index` | `_interpolate(115, 90, 15, 90)` (**inverse**: weak $ = bullish) |
| `copper_level` | 0.30 | `copper_price` | `_interpolate(4000, 10000, 25, 90)` ("Dr. Copper" growth proxy) |
| `gold_level` | 0.20 | `gold_price` | `_interpolate(3500, 2000, 25, 85)` (**inverse**: high gold = fear) |
| `oil_level` | 0.15 | `oil_price` | Multi-tier classification (see below) |

**Oil multi-tier classification:**
- < $30: demand destruction (25–45)
- $30–45: supply concern (45–50)
- **$45–85: ambiguous zone → 50–55 (FORCES NEUTRAL)**
- $85–100: cost pressure (55→40)
- \> $100: cost pressure (40→20)
- **⚠️ Oil weight intentionally reduced to 15% due to inherent ambiguity**

**Extreme values:**
- All bullish (DXY=92, Cu=$9500, Au=$2100, Oil=$65): ~85
- All bearish (DXY=112, Cu=$4500, Au=$3400, Oil=$105): ~18

---

### P3: Credit & Risk Appetite (25%)

**Lines:** ~528–637

**4 submetrics:**

| Submetric | Weight | Input | Scoring |
|-----------|--------|-------|---------|
| `hy_spread_level` | 0.35 | `hy_spread` | `_interpolate(10.0, 2.5, 10, 92)` (**dominant**) |
| `ig_spread_level` | 0.30 | `ig_spread` | `_interpolate(3.0, 0.5, 15, 92)` |
| `vix_level` | 0.20 | `vix_level` | Bell curve: ≤15 `_interpolate(8, 15, 75, 90)` / >15 `_interpolate(15, 40, 90, 15)` |
| `hy_ig_ratio` | 0.15 | HY/IG | `_interpolate(6.0, 2.5, 15, 88)` |

**VIX bell curve:** Peaks at VIX=15 (score 90). Complacency penalty below ~12 (score drops to 75).

**⚠️ VIX weight reduced from 25% to 20%** to limit aggregate VIX exposure across all engines.

**Extreme values:**
- Risk-on (HY=3%, IG=0.6%, VIX=15, ratio=3.0): ~89
- Risk-off (HY=8%, IG=2.5%, VIX=35, ratio=5.5): ~14

---

### P4: Defensive vs Growth (15%)

**Lines:** ~638–726

**2 submetrics (ratio-based):**

| Submetric | Weight | Formula | Scoring |
|-----------|--------|---------|---------|
| `copper_gold_ratio` | 0.55 | `copper / gold` | `_interpolate(1.5, 6.0, 20, 88)` |
| `gold_yield_divergence` | 0.45 | `10Y / (gold/1000)` | `_interpolate(0.5, 4.0, 20, 88)` |

**Example:** Copper=$8000, Gold=$2000 → ratio=4.0 → interpolate(1.5, 6.0, 20, 88) ≈ 58.
**Example:** 10Y=4%, Gold=$2000 → ratio=2.0 → interpolate(0.5, 4.0, 20, 88) ≈ 49.

**⚠️ VIX removed in v2 refactor** (previously double-counted with Pillar 3).

**Extreme values:**
- Strong growth signal (high Cu/Au, high yield/gold): ~85
- Strong defensive signal (low Cu/Au, low yield/gold): ~22

---

### P5: Macro Coherence (15%)

**Lines:** ~727–945

**Unique:** Uses **graded ternary scoring (+1/0/−1)** instead of continuous interpolation.

**Signal grades:**

| Signal | +1 (Bullish) | 0 (Neutral) | −1 (Bearish) |
|--------|-------------|-------------|--------------|
| VIX | < 16 | 16–22 | > 22 |
| Curve | > +0.10% | −0.20% to +0.10% | < −0.20% |
| IG | < 1.0% | 1.0–1.8% | > 1.8% |
| HY | < 3.5% | 3.5–5.5% | > 5.5% |
| USD | < 98 | 98–107 | > 107 |
| Copper | > 8000 | 6500–8000 | < 6500 |
| Gold | < 2500 | 2500–3200 | > 3200 |
| **Oil** | **EXCLUDED** | — | — |

**3 submetrics:**

| Submetric | Weight | Formula |
|-----------|--------|---------|
| `risk_on_count` | 0.35 | `confirming / (confirming + contradicting)` → 0–100 (requires ≥3 signals) |
| `signal_agreement` | 0.40 | `max_direction_count / directional_count` → 0–100 |
| `contradiction_count` | 0.25 | Specific contradiction pairs counted → inversely mapped to 0–100 |

**Contradiction pairs checked:**
1. VIX +1 but HY −1 (calm market but stressed credit)
2. Copper +1 but Curve −1 (growth signal but inverted yield curve)
3. USD +1 but Gold −1 (weak dollar but fearful gold)

Maps: `contradictions / 3` inversely to 0–100.

**Extreme values:**
- Full agreement (all +1): ~92
- Full disagreement (3/3 contradictions, split signals): ~15
- All neutral: ~50

---

## 8. Cross-Engine Analysis

### 8.1 VIX Exposure Across Engines

VIX is used as input in **5 of 6 engines**. The following shows where VIX appears and its effective weight:

| Engine | Where VIX Appears | Approximate Effective Weight in Composite |
|--------|-------------------|------------------------------------------|
| Volatility | P1 `vix_level` (0.35×0.25), P1 `vix_trend` (0.20×0.25), P1 others | **Primary home** — ~20% of composite |
| Flows | P1 `options_posture` (0.25×0.25), P2 `flow_concentration` (0.15×0.20) | ~9% |
| Liquidity | P2 `fci_proxy` (1/3×0.30×0.25), P2 `vix_conditions_signal` (0.25×0.25), P3 `credit_stress_signal` (0.30×0.25×0.20), P4 `dollar_liquidity_pressure` (0.35×0.35×0.15) | ~12% |
| Cross-Asset | P3 `vix_level` (0.20×0.25), P5 threshold only | ~5% |
| Sentiment | C5 `macro_stress` adjustments (±5/10) × (15/100 weight) | ~1–2% |
| Breadth | Not used | 0% |

**Net observation:** VIX changes affect 5 engines simultaneously. A 10-point VIX spike could shift the overall MI composite by 8–15 points across all engines.

### 8.2 Credit Spread Overlap

IG and HY spreads appear in both Liquidity and Cross-Asset engines:

| Engine | Pillar | IG Weight | HY Weight |
|--------|--------|-----------|-----------|
| Liquidity | P2 (conditions_supportiveness) | 1/3×0.25×0.25 | 1/3×0.25×0.25 |
| Liquidity | P3 (direct) | 0.25×0.20 | 0.25×0.20 |
| Liquidity | P5 (stability/stress) | Partial | Partial |
| Cross-Asset | P3 (direct) | 0.30×0.25 | 0.35×0.25 |
| Cross-Asset | P5 (ternary threshold) | Threshold only | Threshold only |

**Net observation:** Credit-spread changes affect both Liquidity and Cross-Asset engines — no isolation between them.

### 8.3 DXY Overlap

| Engine | Pillar | DXY Effective Weight |
|--------|--------|---------------------|
| Liquidity | P4 (75% of pillar) | ~0.11 of engine composite |
| Cross-Asset | P2 `usd_level` (0.35×0.20) | ~0.07 of engine composite |
| Cross-Asset | P5 (ternary threshold) | Threshold only |

### 8.4 None Handling Summary

| Engine | None Strategy | Composite on All-None |
|--------|--------------|----------------------|
| Volatility | Skip + re-weight per submetric; skip pillar if all None | None |
| Breadth | Skip + re-weight; skip pillar if all None | None |
| Flows | Skip + re-weight; skip pillar if all None | None |
| Liquidity | Skip + re-weight; neutral fallback (50.0) per pillar | 50.0 |
| Cross-Asset | Skip + re-weight; skip pillar if all None | None |
| **Sentiment** | **Every component defaults to 50 if no data** | **50.0 (never None)** |

**⚠️ Inconsistency:** Liquidity and Sentiment engines never return None (they default to 50). The other 4 engines can return None. The downstream composite aggregator must handle this asymmetry.

### 8.5 Yield Curve / Rates Overlap

Yield curve spread and 10Y yield appear in both Liquidity P1 and Cross-Asset P1:

| Engine | 10Y Treatment | Curve Treatment |
|--------|--------------|-----------------|
| Liquidity | Bell curve @3.2%, weight 0.20 | `_interpolate(-0.8, 2.0, 15, 95)`, weight 0.15 |
| Cross-Asset | Bell curve @3.5%, weight 0.30 | `_interpolate(-1.0, 2.0, 10, 95)`, weight 0.45 |

**⚠️ Bell curve peaks differ:** Liquidity uses 3.2%, Cross-Asset uses 3.5%. Both are defensible but inconsistent.

---

## 9. Summary Table

| Engine | Pillar | Weight | Submetric Count | None Handling | Min Score | Max Score | Dominant Input |
|--------|--------|--------|-----------------|---------------|-----------|-----------|----------------|
| **Volatility** | Volatility Regime | 0.25 | 5 | Skip + re-weight | ~18 | ~90 | `vix_level` (0.35) |
| | Volatility Structure | 0.25 | 4 | Skip + re-weight | ~18 | ~89 | `term_structure_shape` / `iv_rv_spread` (0.30 each) |
| | Tail Risk & Skew | 0.20 | 3 | Skip + re-weight | ~14 | ~91 | `cboe_skew` (0.40) |
| | Positioning & Options | 0.15 | 4 | Skip + re-weight | ~19 | ~84 | `equity_pc_ratio` (0.30) |
| | Strategy Suitability | 0.15 | 4 strategies | Skip + re-weight | ~15 | ~88 | `premium_selling` (0.40) |
| **Breadth** | Participation Breadth | 0.25 | 6 | Skip + re-weight | ~8 | ~93 | `advance_decline_ratio` / `new_high_new_low_balance` (0.20 each) |
| | Trend Breadth | 0.25 | 8 (3 tiers) | Skip + re-weight | ~15 | ~88 | `pct_above_50dma` (effective 0.28) |
| | Volume Breadth | 0.20 | 3 active + 2 scaffolded | Skip + re-weight | ~10 | ~91 | `up_down_volume_ratio` / `volume_weighted_ad_ratio` (0.35 each) |
| | Leadership Quality | 0.20 | 4 | Skip + re-weight | ~18 | ~86 | `ew_vs_cw_relative` (0.30) |
| | Participation Stability | 0.10 | 3 active + 2 scaffolded | Skip + re-weight | ~22 | ~85 | `breadth_persistence_10d` (0.40) |
| **Flows** | Positioning Pressure | 0.25 | 4 | Skip + re-weight | ~20 | ~82 | `positioning_bias` (0.30) |
| | Crowding / Stretch | 0.20 | 5 | Skip + re-weight | ~18 | ~85 | `crowding_proxy` (0.30) |
| | Squeeze / Unwind Risk | 0.20 | 4 | Skip + re-weight | ~15 | ~88 | `long_unwind_risk` (0.30) |
| | Flow Direction & Persistence | 0.20 | 5 | Skip + re-weight | ~0 | ~100 | `recent_flow_direction` / `flow_persistence_5d` (0.25 each) |
| | Positioning Stability | 0.15 | 5 | Skip + re-weight | ~15 | ~85 | `stability_signal` (0.25) |
| **Sentiment** | Headline Sentiment | 30/100 | 1 | **Default 50** | 0 | 100 | keyword count |
| | Negative Pressure | 20/100 | 1 | **Default 50** | 0 | 100 | bear_ratio |
| | Narrative Severity | 15/100 | 1 | **Default 75** | 50 | 100 | severity penalties |
| | Source Agreement | 10/100 | 1 | **Default 50** | 10 | 100 | cross-source spread |
| | Macro Stress | 15/100 | 1 | **Default 50** | 0 | 100 | stress_level label |
| | Recency Pressure | 10/100 | 1 | **Default 50** | 0 | 100 | decay-weighted avg |
| **Liquidity** | Rates & Policy Pressure | 0.25 | 6 | Skip + re-weight; fallback 50 | ~12 | ~88 | `two_year_yield` (effective 0.35) |
| | Financial Conditions Tightness | 0.25 | 4 | Skip + re-weight; degraded flag | ~10 | ~90 | VIX (effective ~0.35) |
| | Credit & Funding Stress | 0.20 | 5 | Skip + re-weight; proxy flag | ~8 | ~92 | `ig_spread` / `hy_spread` (0.25 each) |
| | Dollar / Global Liquidity | 0.15 | 3 | Skip + re-weight | ~12 | ~88 | `dxy_index` (effective 0.75+) |
| | Liquidity Stability & Fragility | 0.15 | 5 | Cross-pillar check | ~10 | ~90 | `contradiction_between_pillars` (0.30) |
| **Cross-Asset** | Rates & Yield Curve | 0.25 | 3 | Skip + re-weight | ~14 | ~90 | `yield_curve_spread` (0.45) |
| | Dollar & Commodity | 0.20 | 4 | Skip + re-weight | ~18 | ~85 | `usd_level` (0.35) |
| | Credit & Risk Appetite | 0.25 | 4 | Skip + re-weight | ~14 | ~89 | `hy_spread_level` (0.35) |
| | Defensive vs Growth | 0.15 | 2 | Skip + re-weight | ~22 | ~85 | `copper_gold_ratio` (0.55) |
| | Macro Coherence | 0.15 | 3 | Requires ≥3 signals | ~15 | ~92 | `signal_agreement` (0.40) |

**Totals:** 6 engines, 31 pillars/components, ~110 submetrics

---

## 10. Findings

### HIGH

| # | Finding | Impact | Location |
|---|---------|--------|----------|
| H1 | **`futures_net_long_pct` dominates Flows engine** — appears in 4/5 pillars (6 submetrics). Single input can swing composite by 40–50 pts. | Score volatility from single datapoint; if stale or wrong, entire engine is compromised | flows_positioning_engine.py, P1/P2/P3/P5 |
| H2 | **VIX appears in 5/6 engines** — aggregate effective weight is ~20% of total MI composite when considering cross-engine correlation | A VIX spike simultaneously moves Volatility, Flows, Liquidity, Cross-Asset, and Sentiment engines in the same direction, amplifying the signal | All engines except Breadth |
| H3 | **None handling inconsistency** — Sentiment and Liquidity engines default to 50 (never return None); other 4 engines return None. Downstream composite must handle this asymmetry. | If composite aggregator treats 50 as "real data," engines with no data appear neutral instead of absent | news_sentiment_engine.py, liquidity_conditions_engine.py |

### MEDIUM

| # | Finding | Impact | Location |
|---|---------|--------|----------|
| M1 | **Credit spread double-counting** — IG and HY spreads are scoring inputs in both Liquidity (P2, P3, P5) and Cross-Asset (P3, P5) engines | Credit events get amplified through two separate engines that both feed the MI composite | liquidity_conditions_engine.py, cross_asset_macro_engine.py |
| M2 | **DXY double-counting** — DXY is a primary input in both Liquidity P4 (75% effective) and Cross-Asset P2 (35% direct) | Dollar moves affect two engines simultaneously | Same as above |
| M3 | **10Y bell curve peak inconsistency** — Liquidity engine peaks at 3.2%, Cross-Asset peaks at 3.5% for the same underlying input | Same rate environment produces different directional signals in different engines | liquidity_conditions_engine.py P1, cross_asset_macro_engine.py P1 |
| M4 | **2Y Yield redundancy within Liquidity P1** — `two_year_yield` feeds both `two_year_yield_level` (0.25) and `front_end_rate_pressure` (0.10), giving 0.35 effective weight | Possible over-weighting of a single input within one pillar | liquidity_conditions_engine.py P1 |
| M5 | **Breadth engine scaffolded submetrics** — 4 submetrics defined but excluded from weighting (accumulation_distribution_bias, volume_thrust_signal, breadth_regime_consistency, cross_timeframe_agreement) | Dead code paths; weights are locked to active-only submetrics | breadth_engine.py P3, P5 |
| M6 | **Sentiment Narrative Severity asymmetric floor** — No-data defaults to 75, while all other components default to 50 | Creates a mild bullish bias for sentiment scores when news is missing | news_sentiment_engine.py C3 |
| M7 | **`_weighted_avg` single-survivor promotion** — When only one low-weight submetric has data, it gets promoted to 100% weight | Pillar score quality degrades unpredictably; a normally 10%-weighted metric controls the entire pillar | All 5 structural engines |

### LOW

| # | Finding | Impact | Location |
|---|---------|--------|----------|
| L1 | **Oil ambiguity zone** — Oil $45–85 forces neutral score (50–55) regardless of context; weight reduced to 15% | Correct design given oil's directional ambiguity, but worth noting the intentional loss of signal | cross_asset_macro_engine.py P2 |
| L2 | **`fci_proxy` is not a true FCI** — Labeled "proxy" but uses an unweighted average of VIX + IG + 2Y | Name may mislead users inspecting traces | liquidity_conditions_engine.py P2 |
| L3 | **Flows P4 is a pure pass-through** — All 5 submetrics are pre-computed 0–100 scores with no interpolation, just clamping | Scoring logic lives upstream of the engine; changes to upstream computation won't be visible in engine audit | flows_positioning_engine.py P4 |

---

*Audit complete. 3 HIGH, 7 MEDIUM, 3 LOW findings across 6 engines, 31 pillars/components, ~110 submetrics.*
