# Audit 2B — Composite Score Aggregation

**Audit date:** 2025-07-18
**Scope:** How pillar scores aggregate into engine composites, and how engine composites feed into the regime/market composite
**Prerequisite:** Audit 2A (pillar scoring formulas)

---

## Table of Contents

1. [Engine-Level Aggregation (All 6 Engines)](#1-engine-level-aggregation)
2. [Regime Service Aggregation](#2-regime-service-aggregation)
3. [Cross-Pillar Data Overlap per Engine](#3-cross-pillar-data-overlap-per-engine)
4. [Double-Counting Across Blocks](#4-double-counting-across-blocks)
5. [Confidence in Aggregation](#5-confidence-in-aggregation)
6. [Summary Table](#6-summary-table)
7. [Findings](#7-findings)

---

## 1. Engine-Level Aggregation

### Universal Pattern (5 Structural Engines)

All 5 structural engines (Volatility, Breadth, Flows, Liquidity, Cross-Asset) use **identical** composite aggregation code:

```python
# File: each engine's main compute function
# Formula: Σ(pillar_score × weight) / Σ(active_weights)
weighted_parts: list[tuple[float | None, float]] = []
for pname, weight in PILLAR_WEIGHTS.items():
    pdata = pillars.get(pname, {})
    weighted_parts.append((pdata.get("score"), weight))

composite = _weighted_avg(weighted_parts)
if composite is None:
    composite = 0.0  # ← WARNING: all-None fallback is 0.0, not 50.0
    logger.warning("event=<engine>_composite_failed reason=no_valid_pillars")
```

Where `_weighted_avg` (identical across all 5 engines):
```python
def _weighted_avg(parts: list[tuple[float | None, float]]) -> float | None:
    valid = [(v, w) for v, w in parts if v is not None]
    if not valid:
        return None
    total_w = sum(w for _, w in valid)
    return round(sum(v * w for v, w in valid) / total_w, 2)
```

**Key properties:**
- Weighted average with None-skip and weight re-normalization
- Returns `None` when all inputs are None (then converted to 0.0 by caller)
- No post-aggregation clamping (relies on pillar scores being 0–100)
- Rounds to 2 decimal places

---

### Engine 1: Volatility & Options Structure

**File:** `volatility_options_engine.py`, composite at L1188–1196

| Pillar | Key | Weight |
|--------|-----|--------|
| Volatility Regime | `volatility_regime` | 0.25 |
| Volatility Structure | `volatility_structure` | 0.25 |
| Tail Risk & Skew | `tail_risk_skew` | 0.20 |
| Positioning & Options | `positioning_options_posture` | 0.15 |
| Strategy Suitability | `strategy_suitability` | 0.15 |

**Weights sum:** 1.00 ✓
**Aggregation method:** Weighted average via `_weighted_avg`
**Weight rationale:** Not documented in code. Regime (0.25) and Structure (0.25) get equal top weighting. Strategy Suitability (0.15) is a derived pillar that reuses inputs from other pillars — arguably should not get equal weight treatment.

#### Degenerate Cases

**Normal (all 5 scored):**
Example: P1=75, P2=80, P3=60, P4=70, P5=65
```
composite = (75×0.25 + 80×0.25 + 60×0.20 + 70×0.15 + 65×0.15)
          = (18.75 + 20.0 + 12.0 + 10.5 + 9.75) / 1.0
          = 71.0
```

**Only P1 scored (others None):**
```
valid = [(75, 0.25)]
total_w = 0.25
composite = 75 × 0.25 / 0.25 = 75.0
```
P1's weight re-normalizes from 25% to 100%. **Composite = single pillar score.**

**Zero pillars scored:**
```
_weighted_avg returns None → composite = 0.0
```
⚠️ **0.0 maps to label "Volatility Stress / Defensive"** — this is misleading for a no-data situation.

#### Score Distribution

- **Theoretical min:** 0.0 (all pillars score 0, or all None → 0.0 fallback)
- **Theoretical max:** 100.0 (all pillars score 100)
- **Central tendency bias:** YES. With 5 independently varying pillars in a weighted average, extreme composites require ALL pillars to be extreme. In practice, composites will tend toward 40–70.
- **No floor or cap** applied to composite (implicit 0–100 from pillar bounds)

---

### Engine 2: Breadth & Participation

**File:** `breadth_engine.py`, composite at L1237–1245

| Pillar | Key | Weight |
|--------|-----|--------|
| Participation Breadth | `participation_breadth` | 0.25 |
| Trend Breadth | `trend_breadth` | 0.25 |
| Volume Breadth | `volume_breadth` | 0.20 |
| Leadership Quality | `leadership_quality` | 0.20 |
| Participation Stability | `participation_stability` | 0.10 |

**Weights sum:** 1.00 ✓
**Aggregation method:** Identical to Volatility
**Weight rationale:** Not documented. Stability at 0.10 is the lowest weight — reasonable given it has scaffolded (inactive) submetrics.

#### Degenerate Cases

- **Normal:** Same formula as Volatility
- **Only 1 pillar:** Re-normalizes to 100% weight on that pillar
- **Zero pillars:** `composite = 0.0`, label = "Deteriorating"

#### Special: Internal Sub-Aggregation

Pillar 2 (Trend Breadth) uses internal tiered aggregation before its pillar score is submitted:
```
Short tier (0.30) → [pct_above_20dma(0.50), pct_20_over_50(0.30), momentum(0.20)]
Intermediate tier (0.40) → [pct_above_50dma(0.70), momentum(0.30)]
Long tier (0.30) → [pct_above_200dma(0.40), pct_50_over_200(0.40), momentum(0.20)]
```
This creates a 2-level weighted average (submetrics → tiers → pillar → composite), which **further dampens extreme values**. For Trend Breadth to score 90+, ALL tiers must score 90+.

---

### Engine 3: Flows & Positioning

**File:** `flows_positioning_engine.py`, composite at L1421–1429

| Pillar | Key | Weight |
|--------|-----|--------|
| Positioning Pressure | `positioning_pressure` | 0.25 |
| Crowding / Stretch | `crowding_stretch` | 0.20 |
| Squeeze / Unwind Risk | `squeeze_unwind_risk` | 0.20 |
| Flow Direction & Persistence | `flow_direction_persistence` | 0.20 |
| Positioning Stability | `positioning_stability` | 0.15 |

**Weights sum:** 1.00 ✓
**Aggregation method:** Identical weighted average
**Weight rationale:** Not documented.

#### Unique Feature: Label Safety Gates

After composite computation, the Flows engine applies **safety gates** (L277–330) that can override the label:

```python
_CROWDING_GATE_THRESHOLD = 40    # Pillar 2 below this → block "Supportive" labels
_STABILITY_GATE_THRESHOLD = 35   # Pillar 5 below this → block "Supportive" labels
_SQUEEZE_RISK_GATE_THRESHOLD = 35  # Pillar 3 below this → block "Supportive" labels
```

**Gate behavior:**
- Only applies when composite ≥ 55 (top two label bands)
- If ANY gate pillar is below its threshold, label is capped at "Mixed but Tradable (Gated)"
- The **numeric composite score is NOT modified** — only the label changes
- Gate warnings are included in output

**⚠️ This creates a score-label disconnect:** A composite of 78 with crowding at 38 will display label "Mixed but Tradable (Gated)" but output score 78. Downstream consumers using the numeric score will not see the gate.

#### Degenerate Cases

- **Normal / Only 1 pillar / Zero pillars:** Same as other engines
- **Gate edge case:** If crowding pillar is None (not scored), gate check `crowding_score is not None and crowding_score < 40` evaluates False — **gate is NOT applied**. Missing data bypasses safety.

---

### Engine 4: News & Sentiment

**File:** `news_sentiment_engine.py`, composite at L129–137

⚠️ **Different architecture** — uses 6 weighted components (weights sum to 100, not 1.0)

| Component | Key | Weight |
|-----------|-----|--------|
| Headline Sentiment | `headline_sentiment` | 30 |
| Negative Pressure | `negative_pressure` | 20 |
| Narrative Severity | `narrative_severity` | 15 |
| Source Agreement | `source_agreement` | 10 |
| Macro Stress | `macro_stress` | 15 |
| Recency Pressure | `recency_pressure` | 10 |

**Weights sum:** 100 ✓ (percentage-based, not fractional)

**Aggregation code (inline, no `_weighted_avg`):**
```python
total_weight = 0.0
weighted_sum = 0.0
for name, weight in _WEIGHTS.items():
    comp = components.get(name)
    if comp and comp.get("score") is not None:
        weighted_sum += comp["score"] * weight
        total_weight += weight

composite = _bounded(weighted_sum / total_weight, 0.0, 100.0) if total_weight > 0 else 50.0
```

**Key differences from structural engines:**
1. **Explicit clamping** via `_bounded(0.0, 100.0)` — structural engines don't clamp
2. **All-None default is 50.0** (neutral), not 0.0
3. **Weights are percentage-based** (30, 20, 15...) — division by `total_weight` normalizes correctly regardless
4. **In practice, components rarely return None** because each component already defaults to 50 internally

**Weight rationale:** Documented in module docstring (L7–17). Headline sentiment gets highest weight (30%) as the primary signal.

#### Degenerate Cases

**Normal (all 6 scored):**
Example: C1=72, C2=55, C3=85, C4=60, C5=65, C6=50
```
weighted_sum = 72×30 + 55×20 + 85×15 + 60×10 + 65×15 + 50×10
             = 2160 + 1100 + 1275 + 600 + 975 + 500 = 6610
total_weight = 100
composite = _bounded(6610 / 100, 0, 100) = _bounded(66.1, 0, 100) = 66.1
```

**Only 1 component scored:**
```
weighted_sum = 72 × 30 = 2160
total_weight = 30
composite = _bounded(2160 / 30, 0, 100) = 72.0
```

**Zero components scored:**
```
total_weight = 0 → composite = 50.0 (explicit neutral default)
```
✓ Better than 0.0 — no misleading extreme label.

**⚠️ But: components almost never return None.** Each component function defaults to 50 internally when data is missing. So in practice, "zero components scored" is unreachable — the engine will produce ~50 from defaults even with no real data. This masks data absence.

---

### Engine 5: Liquidity & Financial Conditions

**File:** `liquidity_conditions_engine.py`, composite at L1620–1628

| Pillar | Key | Weight |
|--------|-----|--------|
| Rates & Policy Pressure | `rates_policy_pressure` | 0.25 |
| Financial Conditions Tightness | `financial_conditions_tightness` | 0.25 |
| Credit & Funding Stress | `credit_funding_stress` | 0.20 |
| Dollar / Global Liquidity | `dollar_global_liquidity` | 0.15 |
| Liquidity Stability & Fragility | `liquidity_stability_fragility` | 0.15 |

**Weights sum:** 1.00 ✓
**Aggregation method:** Identical to Volatility/Breadth/Flows
**Weight rationale:** Not documented. Rates + Financial Conditions get 50% combined; Credit gets 20%; Dollar + Stability get 30%.

#### Special: Pillar 5 Cross-Pillar Dependency

Pillar 5 (Liquidity Stability & Fragility) receives scores from Pillars 1–4 as input for its `contradiction_between_pillars` submetric. This creates a **circular dependency** at the pillar level:

```
P1, P2, P3, P4 are computed first
→ P5 ← uses P1–P4 scores as inputs
→ composite ← uses P1–P5
```

**Impact:** P5 is not independent — it measures COHERENCE of the other 4 pillars. If P1–P4 scores vary by >50 points, P5's `contradiction_between_pillars` (0.30 weight in P5) drops to ~10. This means incoherent pillars get an additional ~4.5-point penalty at the composite level (0.30 × 0.15 × 80 ≈ 3.6 effective composite impact).

#### Degenerate Cases

Same pattern as other structural engines. All-None → 0.0.

---

### Engine 6: Cross-Asset / Macro Confirmation

**File:** `cross_asset_macro_engine.py`, composite at L1130–1138

| Pillar | Key | Weight |
|--------|-----|--------|
| Rates & Yield Curve | `rates_yield_curve` | 0.25 |
| Dollar & Commodity | `dollar_commodity` | 0.20 |
| Credit & Risk Appetite | `credit_risk_appetite` | 0.25 |
| Defensive vs Growth | `defensive_vs_growth` | 0.15 |
| Macro Coherence | `macro_coherence` | 0.15 |

**Weights sum:** 1.00 ✓
**Aggregation method:** Identical to other structural engines
**Weight rationale:** Not documented. Rates and Credit get 50% combined; Dollar+Commodity 20%; two "meta" pillars (Defensive/Growth, Macro Coherence) get 30%.

#### Special: Pillar 5 Ternary Scoring

Pillar 5 (Macro Coherence) uses a fundamentally different scoring method (graded ternary: +1/0/−1 per signal) rather than continuous interpolation. This means:
- Pillar 5 produces scores in discrete clusters (~15, ~35, ~50, ~70, ~92) rather than smooth 0–100
- These cluster values participate in the weighted average, creating subtle discontinuities in the composite

#### Degenerate Cases

Same pattern. All-None → 0.0.

---

## 2. Regime Service Aggregation

**File:** `regime_service.py`

### 2.1 Architecture

The regime service computes a 3-block hierarchy:

```
MI Engines + Market Data
        ↓
┌──────────────────────────────────────────────────────────┐
│  Structural Block (30%)  │  Tape Block (40%)  │  Tactical Block (30%)  │
│  - Liquidity (35%)       │  - Breadth (45%)    │  - Volatility (35%)    │
│  - Cross-Asset (35%)     │  - Trend (25%)      │  - Flows (30%)         │
│  - Rates (15%)           │  - Momentum (15%)   │  - Sentiment (20%)     │
│  - Vol Structure (15%)   │  - Small-cap (15%)  │  - Rate Pressure (15%) │
└──────────────────────────────────────────────────────────┘
        ↓
  Regime Score = Σ(block_score × block_weight) / Σ(active_block_weights)
        ↓
  Label Assignment (5-tier, confidence-aware)
```

### 2.2 Block Weights

**Defined at:** `regime_service.py` L18

```python
_BLOCK_WEIGHTS = {"structural": 0.30, "tape": 0.40, "tactical": 0.30}
```

**Sum:** 1.00 ✓

### 2.3 Block-Level Aggregation

Each block uses **inline weighted average with re-normalization** (not `_weighted_avg`):

```python
# regime_service.py, pattern at L805-812, L875-886, L951-962
weighted_sum = 0.0
weight_total = 0.0
for key, w in _BLOCK_WEIGHTS_LOCAL.items():
    s = pillar_scores.get(key)
    if s is not None:
        weighted_sum += s * w
        weight_total += w

if weight_total > 0:
    block_score = weighted_sum / weight_total
else:
    block_score = 50.0  # ← neutral default (NOT 0.0)
```

**Key difference from engines:** Block-level all-None default is **50.0** (neutral), not 0.0.

### 2.4 Engine-to-Block Mapping

#### Structural Block (30% of regime)

| Input | Source | Weight | Data Quality |
|-------|--------|--------|-------------|
| `liquidity` | Liquidity MI engine composite | 0.35 | Engine output |
| `macro` | Cross-Asset MI engine composite | 0.35 | Engine output |
| `rates` | `_score_rates_regime(10Y, delta_bps)` | 0.15 | Direct FRED (proxy) |
| `vol_structure` | `_score_volatility_structure(VIX, 5d_change)` | 0.15 | Direct FRED (proxy) |

**`_score_rates_regime`** (L517–556):
- Step function (not continuous): <3.5%→90, 3.5–4%→75, 4–4.5%→60, 4.5–5%→40, >5%→20
- Direction adjustment: δ>25bps→−25, δ>15bps→−15, δ>8bps→−8, δ<−10bps→+10, δ<−5bps→+5
- Clamped [0, 100]
- Returns None if 10Y is None

**`_score_volatility_structure`** (L558–595):
- Step function: VIX<14→90, 14–18→80, 18–22→55, 22–28→35, >28→15
- Direction adjustment: Δ>20%→−15, Δ>10%→−8, Δ<−10%→+5
- Clamped [0, 100]
- Returns None if VIX is None

#### Tape Block (40% of regime)

| Input | Source | Weight | Data Quality |
|-------|--------|--------|-------------|
| `breadth` | Breadth MI engine composite | 0.45 | Engine output |
| `trend` | `index_metrics["trend_score"]` | 0.25 | Computed from index prices |
| `momentum` | `index_metrics["momentum_score"]` | 0.15 | Computed from RSI |
| `smallcap` | `index_metrics["smallcap_score"]` | 0.15 | Computed from IWM vs large-cap |

**Breadth dominates** at 45%. The other inputs (trend, momentum, smallcap) are direct computations from market data, not MI engine outputs.

#### Tactical Block (30% of regime)

| Input | Source | Weight | Data Quality |
|-------|--------|--------|-------------|
| `volatility` | Volatility MI engine composite | 0.35 | Engine output |
| `flows` | Flows MI engine composite | 0.30 | Engine output |
| `sentiment` | Sentiment MI engine composite | 0.20 | Engine output |
| `rate_pressure` | `_score_rate_pressure(delta_bps)` | 0.15 | Direct FRED (proxy) |

**`_score_rate_pressure`** (L598–621):
- Step function: δ<−15bps→90, −15 to −5→75, −5 to +5→60, +5 to +15→45, +15 to +25→30, >25bps→15
- Returns None if delta_bps is None

### 2.5 Block-to-Regime Synthesis

**Code at:** `regime_service.py` L1007–1093 (`_synthesize`)

```python
available = {k: v for k, v in block_scores.items() if v is not None}

# Renormalize weights for available blocks
weight_sum = sum(_BLOCK_WEIGHTS[k] for k in available)
regime_score = sum(
    available[k] * (_BLOCK_WEIGHTS[k] / weight_sum) for k in available
)
regime_score = self._bounded(regime_score, 0.0, 100.0)
```

**Properties:**
- Weighted average with None-skip and re-normalization (same concept as engines)
- **Explicit clamping** via `_bounded(0, 100)` (unlike engine composites)
- If no blocks available → returns `(50.0, 0.3, {...})` with neutral baseline

### 2.6 Regime Label Assignment

**Code at:** `regime_service.py` L1095–1117

| Score Range | Aligned Blocks | Conflicted Blocks |
|-------------|---------------|-------------------|
| ≥ 65 | RISK_ON | RISK_ON_CAUTIOUS |
| 55–64 | RISK_ON_CAUTIOUS | NEUTRAL |
| 40–54 | NEUTRAL | NEUTRAL |
| 30–39 | RISK_OFF_CAUTION | NEUTRAL |
| < 30 | RISK_OFF | RISK_OFF_CAUTION |

**Override:** If confidence < 0.4 → always NEUTRAL regardless of score.

**Conflict definition:** `_CONFLICT_SPREAD_THRESHOLD = 30.0` (L19). Blocks are "aligned" if max spread ≤ 30.

**Label distribution observation:**
- NEUTRAL covers a wide range: 40–64 (always) + anything conflicted between 30–64 + anything with confidence <0.4
- RISK_ON requires ≥65 AND aligned — this is hard to achieve when averaging 3 blocks
- RISK_OFF requires <30 AND aligned — equally hard
- **Most real-world outputs will be NEUTRAL or RISK_ON_CAUTIOUS**

### 2.7 Regime Confidence

**Code at:** `regime_service.py` L1067–1085

```python
# Base from data coverage
coverage = len(available) / 3.0
base_confidence = coverage * 0.85  # Max 0.85 from coverage alone

# Conflict penalty
if max_spread > _CONFLICT_SPREAD_THRESHOLD:
    excess = max_spread - _CONFLICT_SPREAD_THRESHOLD
    conflict_penalty = min(0.30, excess / 100.0)

confidence = self._bounded(base_confidence - conflict_penalty, 0.1, 0.95)
```

| Scenario | Coverage Base | Conflict Penalty | Result |
|----------|--------------|-----------------|--------|
| All 3 blocks, aligned | 0.85 | 0.0 | **0.85** |
| All 3 blocks, spread=50 | 0.85 | 0.20 | **0.65** |
| All 3 blocks, spread=80 | 0.85 | 0.30 (cap) | **0.55** |
| 2 blocks, aligned | 0.567 | 0.0 | **0.57** |
| 1 block only | 0.283 | 0.0 | **0.28** |
| 0 blocks | — | — | **0.30** (hardcoded default) |

**Maximum confidence:** 0.85 (never reaches 0.95 cap — requires 3 blocks × 0.85 = 0.85)
**Minimum confidence:** 0.10 (floor)

---

## 3. Cross-Pillar Data Overlap per Engine

### Engine 1: Volatility

| Input | Pillars Used In | Effective Composite Weight |
|-------|-----------------|---------------------------|
| `vix_level` | P1 (vix_level 0.35), P4 (equity_pc bell curve uses implicitly), P5 (premium_selling, directional, hedging) | ~0.25 |
| `vix_term_structure` | P2 (term_structure_shape 0.30), P5 (vol_structure_plays) | ~0.10 |
| `iv_rv_spread` | P2 (iv_rv_spread 0.30), P5 (premium_selling) | ~0.10 |
| `put_call_ratio` | P4 (equity_pc_ratio 0.30, spy_pc_ratio 0.25), P5 (premium_selling) | ~0.12 |

**Key overlap:** VIX level feeds P1 (25% pillar weight) and P5 (15% pillar weight). P5 is explicitly a derived pillar that reprocesses the same inputs as P1–P4.

### Engine 2: Breadth

**Minimal overlap.** Each pillar reads distinct data:
- P1: A/D ratio, % up, new highs/lows
- P2: % above SMAs
- P3: Volume ratios
- P4: EW vs CW, sector returns
- P5: Persistence metrics

No significant cross-pillar data sharing.

### Engine 3: Flows & Positioning

| Input | Pillars Used In | Effective Composite Weight |
|-------|-----------------|---------------------------|
| **`futures_net_long_pct`** | P1 (0.25×0.25), P2 (0.30×0.20), P3 (0.30×0.20), P5 (part of 0.20×0.15) | **~0.19** |
| `put_call_ratio` | P1 (0.30×0.25), P2 (0.20×0.20), P3 (part of 0.25×0.20) | ~0.12 |
| `vix_level` | P1 (0.25×0.25), P2 (0.15×0.20), P5 (part of 0.25×0.15) | ~0.10 |
| `vix_term_structure` | P1 (partial), P3 (partial), P5 (partial) | ~0.06 |

**⚠️ `futures_net_long_pct` has ~19% effective composite weight** across 4 of 5 pillars. This is the most extreme cross-pillar coupling in any engine.

### Engine 4: News & Sentiment

**Minimal overlap.** Each component reads distinct data (headlines, macro context, source metadata). VIX appears only in C5 (macro_stress) as an adjustment.

### Engine 5: Liquidity

| Input | Pillars Used In | Effective Composite Weight |
|-------|-----------------|---------------------------|
| `vix_level` | P2 (fci_proxy 1/3×0.30, vix_conditions 0.25), P3 (credit_stress 0.30×0.25), P4 (dollar_liq 0.35×0.35), P5 (stability 1/3×0.25) | **~0.14** |
| `ig_spread` | P2 (fci_proxy 1/3×0.30, conditions_support 1/3×0.25, broad_tight 1/2×0.20), P3 (direct 0.25, credit_stress 0.70×0.25), P5 (stability, stress_risk) | **~0.17** |
| `hy_spread` | P2 (conditions_support 1/3×0.25), P3 (direct 0.25, breakage 1/2×0.10), P5 (stress_risk 1/3×0.15, fragility penalty thresholds) | **~0.12** |
| `two_year_yield` | P1 (direct 0.25, front_end 0.10), P2 (fci_proxy 1/3×0.30), P5 (stability 1/3×0.25) | **~0.14** |
| `dxy_index` | P4 (dxy_level 0.40, dollar_liq 0.65×0.35, risk_impact 0.25), P5 (stress_risk 1/3×0.15) | ~0.12 |

**⚠️ IG Spread has ~17% effective composite weight** across pillars 2, 3, and 5. Combined with HY spread, credit data accounts for ~29% of the Liquidity composite despite the "Rates" pillars being weighted at 25%.

### Engine 6: Cross-Asset

| Input | Pillars Used In | Effective Composite Weight |
|-------|-----------------|---------------------------|
| `ten_year_yield` | P1 (10Y level 0.30, rate_diff partial), P4 (gold_yield_div partial) | ~0.10 |
| `hy_spread` | P3 (direct 0.35, hy_ig_ratio partial), P5 (ternary threshold) | ~0.11 |
| `ig_spread` | P3 (direct 0.30, hy_ig_ratio partial), P5 (ternary threshold) | ~0.09 |
| `vix_level` | P3 (vix_level 0.20), P5 (ternary threshold) | ~0.06 |
| `gold_price` | P2 (gold_level 0.20), P4 (copper_gold, gold_yield partial) | ~0.06 |
| `copper_price` | P2 (copper_level 0.30), P4 (copper_gold 0.55) | ~0.10 |

Moderate overlap, primarily credit spreads and VIX appearing in both P3 and P5.

---

## 4. Double-Counting Across Blocks

### Engine Assignment (No Engine Duplication)

Each MI engine feeds **exactly one block**:

| Engine | Block | Verified |
|--------|-------|----------|
| Liquidity & Financial Conditions | Structural | ✓ Single |
| Cross-Asset / Macro | Structural | ✓ Single |
| Breadth & Participation | Tape | ✓ Single |
| Volatility & Options | Tactical | ✓ Single |
| Flows & Positioning | Tactical | ✓ Single |
| News & Sentiment | Tactical | ✓ Single |

**No engine appears in multiple blocks.** ✓

### Raw Data Duplication Across Blocks

Despite engine separation, the **same raw data points** feed multiple blocks via different engines:

| Raw Input | Structural Block | Tape Block | Tactical Block |
|-----------|-----------------|------------|----------------|
| **VIX** | `vol_structure` (15% of block) + Liquidity engine VIX usage | — | Volatility engine (VIX is ~20% of engine) + Flows engine (VIX in 3 pillars) |
| **10Y Yield** | `rates` (15% of block) + Liquidity P1 (2Y/10Y) + Cross-Asset P1 (10Y) | — | `rate_pressure` (15% of block) |
| **IG Spread** | Liquidity P2/P3/P5 + Cross-Asset P3/P5 | — | — |
| **HY Spread** | Liquidity P3/P5 + Cross-Asset P3/P5 | — | — |
| **DXY** | Liquidity P4 + Cross-Asset P2 | — | — |

**⚠️ VIX is the most duplicated input:**
- Structural block: `vol_structure` scoring (15% of block) + subset of Liquidity engine composite
- Tactical block: large portions of Volatility engine (P1) + Flows engine (P1/P2/P5)
- Net: VIX movements shift BOTH Structural and Tactical blocks simultaneously

**⚠️ 10Y Yield appears in both Structural and Tactical:**
- Structural: `_score_rates_regime()` at 15% weight + within Liquidity/Cross-Asset engines
- Tactical: `_score_rate_pressure()` at 15% weight (5-day delta only)
- These measure different aspects (level+delta vs delta only), but raw input is the same

---

## 5. Confidence in Aggregation

### Engine Confidence → Block Confidence

**Block confidence is an average of engine confidences plus direct-data constants:**

#### Structural Block
```python
confs = []
if liq_conf is not None: confs.append(liq_conf)       # Liquidity engine confidence
if macro_conf is not None: confs.append(macro_conf)     # Cross-Asset engine confidence
if rates_score is not None: confs.append(0.9)           # Direct FRED → hardcoded high
if vol_struct_score is not None: confs.append(0.9)      # Direct FRED → hardcoded high
block_confidence = sum(confs) / len(confs) if confs else 0.5
```

#### Tape Block
```python
confs = []
if breadth_conf: confs.append(breadth_conf)             # Breadth engine confidence
# trend/momentum/smallcap → computed directly, not engine outputs
# Their confidence is NOT included (missing from code)
block_confidence = sum(confs) / len(confs) if confs else 0.5
```

**⚠️ Tape block confidence = just Breadth engine confidence** (trend, momentum, smallcap scores don't contribute to block confidence). If Breadth engine has 60% confidence, the entire Tape block reports 60% confidence even though 55% of the block comes from direct market data.

#### Tactical Block
```python
confs = [
    self._extract_engine_confidence(mi_results, k)
    for k in ("volatility_options", "flows_positioning", "news_sentiment")
]
valid_confs = [c for c in confs if c is not None]
if rate_pressure_score is not None:
    valid_confs.append(0.85)
block_confidence = sum(valid_confs) / len(valid_confs) if valid_confs else 0.5
```

### Critical Finding: Confidence Does NOT Weight Scores

**Engine confidence is used ONLY for:**
1. Computing block-level confidence (simple average)
2. Computing regime-level confidence (coverage-based)
3. Label override (confidence < 0.4 → force NEUTRAL)

**Engine confidence is NOT used for:**
- Weighting engine scores in the block composite
- Weighting block scores in the regime composite
- Any multiplicative adjustment to scores

**Impact:** A Flows engine with confidence 0.55 (degraded, heavy proxy reliance) gets **exactly the same 30% weight** in the Tactical block as when it has confidence 0.90. The low confidence does NOT reduce its influence on the regime score.

### Regime Confidence Formula

```
base = (blocks_available / 3) × 0.85
conflict_penalty = min(0.30, max(0, (max_spread - 30) / 100))
confidence = clamp(base - conflict_penalty, 0.10, 0.95)
```

**Does NOT incorporate engine confidence at all.** Regime confidence measures:
1. How many blocks are available (coverage)
2. How much blocks disagree (conflict spread)

It does NOT measure:
- Whether the underlying engine data is fresh
- Whether engines relied on proxies
- Whether individual engine confidence is high or low

---

## 6. Summary Table

| Engine | Weights Sum | Aggregation | None Handling | All-None Default | Post-Processing | Label Assignment |
|--------|-------------|-------------|---------------|-----------------|-----------------|------------------|
| Volatility | 1.00 | `_weighted_avg` | Skip + re-normalize | **0.0** | None | Band lookup |
| Breadth | 1.00 | `_weighted_avg` | Skip + re-normalize | **0.0** | None | Band lookup |
| Flows | 1.00 | `_weighted_avg` | Skip + re-normalize | **0.0** | None | Band lookup **+ safety gates** |
| Sentiment | 100 | Inline weighted avg | Skip + re-normalize | **50.0** | `_bounded(0, 100)` | Ternary (stress-aware) |
| Liquidity | 1.00 | `_weighted_avg` | Skip + re-normalize | **0.0** | None | Band lookup |
| Cross-Asset | 1.00 | `_weighted_avg` | Skip + re-normalize | **0.0** | None | Band lookup |
| **Regime** | **1.00** | **Inline weighted avg** | **Skip + re-normalize** | **50.0** | **`_bounded(0, 100)`** | **5-tier, confidence-aware** |

| Level | Theoretical Min | Theoretical Max | Central Tendency Bias | Confidence Weighted? |
|-------|-----------------|-----------------|----------------------|---------------------|
| Engine composite | 0 (pillar floors) | 100 (pillar ceilings) | Moderate (5-pillar avg) | No |
| Block composite | ~0 (engine 0 + direct score 0) | ~100 (all inputs max) | Strong (4-input avg) | No |
| Regime composite | ~0 (all blocks 0) | ~100 (all blocks 100) | Very strong (3-block avg) | No |

---

## 7. Findings

### HIGH

| # | Finding | Impact | Location |
|---|---------|--------|----------|
| H1 | **Confidence does NOT weight scores** — Engine confidence is purely informational. A low-confidence engine (flows at 0.55) gets identical weight in the block composite as a high-confidence engine (breadth at 0.90). | Low-quality engine outputs have disproportionate influence on regime score. Trash proxy data is treated the same as direct market data. | regime_service.py L793–960 (all block computations) |
| H2 | **All-None default mismatch** — 4 engines default to 0.0, 1 engine defaults to 50.0, 1 engine defaults to 50.0. The regime service blocks default to 50.0. | If all Volatility engine pillars fail, it scores 0.0 (extreme bearish label "Stress/Defensive"). This 0.0 then pulls the Tactical block down, even though 0.0 means "no data" not "maximum stress." | All engine composite assembly code |
| H3 | **Sentiment engine masks data absence** — Every component defaults to 50 internally, so the composite is ~50 even with zero real data. Unlike other engines where None propagates up, Sentiment always produces a number. | Regime service cannot distinguish "no news data" from "genuinely neutral sentiment." A stale sentiment engine appears to be functioning normally. | news_sentiment_engine.py component defaults |

### MEDIUM

| # | Finding | Impact | Location |
|---|---------|--------|----------|
| M1 | **Flows engine score-label disconnect** — Safety gates change the label but NOT the numeric score. Downstream consumers using the score (not the label) will not see the gate. | Tactical block uses the numeric score, not the label. An engine scoring 78 with gated label "Mixed (Gated)" feeds 78 into the block composite — the gate is invisible to the regime. | flows_positioning_engine.py L277–330 |
| M2 | **VIX double-counting across blocks** — VIX feeds Structural block (vol_structure at 15% + within Liquidity engine) AND Tactical block (Volatility engine + Flows engine). A VIX spike moves both blocks simultaneously. | The 3-block structure was designed for signal independence. VIX appearing in 2/3 blocks undermines that independence and amplifies VIX's regime impact beyond what weights suggest. | regime_service.py (Structural vol_structure + Tactical engines) |
| M3 | **10Y yield double-counting across blocks** — 10Y feeds Structural block (_score_rates_regime at 15% + Liquidity P1 + Cross-Asset P1) AND Tactical block (_score_rate_pressure at 15%). | Similar to M2 but lower magnitude. Different aspects measured (level vs delta), but correlated moves affect both blocks. | regime_service.py (rates + rate_pressure functions) |
| M4 | **Tape block confidence = Breadth engine only** — Trend, momentum, and smallcap inputs (55% of Tape block weight) contribute ZERO to block confidence. | Tape block confidence may appear high (if Breadth is strong) even when the majority of its inputs are missing or degraded. | regime_service.py Tape block confidence assembly |
| M5 | **`futures_net_long_pct` at 19% effective weight** in Flows engine — a single raw input drives nearly 1/5 of the engine composite through 4 of 5 pillars. | If this value is stale, proxied, or wrong, the Flows engine becomes unreliable. This then feeds 30% of Tactical block, which is 30% of regime. Effective regime impact: ~1.7%. | flows_positioning_engine.py (cross-pillar analysis) |
| M6 | **Central tendency bias in regime score** — 3 levels of averaging (submetrics → pillars → engine → block → regime) make extreme regime scores nearly impossible in realistic conditions. Scores will cluster 40–65. | The 5-tier label system (RISK_ON, RISK_ON_CAUTIOUS, NEUTRAL, RISK_OFF_CAUTION, RISK_OFF) is underutilized. RISK_ON (≥65 + aligned) and RISK_OFF (<30 + aligned) require extreme market conditions to trigger. | Architecture-level |
| M7 | **Flows engine gate bypass on None** — If the crowding/stability/squeeze pillars return None (not scored), the safety gate check evaluates False and the gate is NOT applied. | A high composite from 2 scored pillars will get a "Supportive" label even though the safety check couldn't run. | flows_positioning_engine.py L277–330 gate conditions |

### LOW

| # | Finding | Impact | Location |
|---|---------|--------|----------|
| L1 | **Liquidity P5 circular dependency** — P5 reads P1–P4 scores to detect contradictions. This makes P5 a "meta-pillar" that amplifies disagreement via a ~3.6-point composite penalty. | Design is intentional and well-implemented. Noting for transparency — P5 is not an independent measurement. | liquidity_conditions_engine.py P5 computation |
| L2 | **Cross-Asset P5 discrete clustering** — Ternary scoring (+1/0/−1) produces discrete score clusters rather than smooth 0–100. | Minor discontinuity in composite. Effect is dampened by P5's 15% weight. | cross_asset_macro_engine.py P5 |
| L3 | **No composite clamping in 4 structural engines** — Composites rely on pillar scores being 0–100, but no explicit `_bounded` call. Sentiment and Regime service do clamp explicitly. | Theoretically safe (since `_interpolate` clamps within submetrics), but inconsistent defensive coding. | All structural engine composite assembly |
| L4 | **Weight rationale undocumented** — All 5 structural engines have hardcoded pillar weights with no inline documentation explaining the basis. Only Sentiment engine documents weights in its docstring. | Makes future weight adjustments arbitrary — no record of why current values were chosen. | All engine PILLAR_WEIGHTS declarations |

---

*Audit complete. 3 HIGH, 7 MEDIUM, 4 LOW findings covering engine composite aggregation, regime block synthesis, cross-pillar overlap, double-counting across blocks, and confidence weighting behavior.*
