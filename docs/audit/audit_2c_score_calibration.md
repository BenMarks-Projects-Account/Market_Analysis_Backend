# Audit 2C — Score Calibration & Range Behavior

**Auditor**: Copilot (automated)
**Date**: 2025-07-18
**Scope**: All 6 MI engines + Regime service — label assignment, score bunching, interpolation boundaries, trader takeaways, strategy suitability, regime labels, what_works/what_to_avoid
**Engine locations**: `BenTrade/backend/app/services/`

---

## Table of Contents

1. [Label Assignment per Engine](#1-label-assignment-per-engine)
2. [Label-Score Consistency Check](#2-label-score-consistency-check)
3. [Score Bunching Analysis](#3-score-bunching-analysis)
4. [Interpolation Boundary Behavior](#4-interpolation-boundary-behavior)
5. [Trader Takeaway Generation](#5-trader-takeaway-generation)
6. [Strategy Suitability Scores](#6-strategy-suitability-scores)
7. [Regime Label Assignment](#7-regime-label-assignment)
8. [what_works / what_to_avoid Generation](#8-what_works--what_to_avoid-generation)
9. [Summary Table](#9-summary-table)
10. [Findings](#10-findings)
11. [Cross-References](#11-cross-references)

---

## 1. Label Assignment per Engine

All 5 structural engines use `_label_from_score()` with **identical 6-tier bands**. Flows engine uses a variant `_label_from_score_with_gates()` that can override the label downward. Sentiment engine uses its own 4-tier band. Regime service uses a 5-tier system with confidence and alignment checks.

### 1.1 Structural Engines (Vol, Breadth, Liquidity, Cross-Asset, Flows-base)

| Score Range | Volatility Label | Breadth Label | Liquidity Label | Cross-Asset Label |
|-------------|-----------------|---------------|-----------------|-------------------|
| 85–100 | Premium Selling Strongly Favored | Strong Breadth | Highly Supportive | Strongly Favorable |
| 70–84.99 | Constructive / Favorable Structure | Constructive Breadth | Supportive | Favorable |
| 55–69.99 | Mixed but Tradable | Mixed but Tradable | Moderate / Neutral | Mixed but Tradable |
| 45–54.99 | Cautious / Elevated Risks | Caution — Breadth Weakening | Tightening Signals | Cautious |
| 30–44.99 | Deteriorating / High Risk | Deteriorating Breadth | Restrictive | Deteriorating |
| 0–29.99 | Hostile / Extreme Risk | Hostile — Broad Decline | Hostile | Hostile |

### 1.2 Flows Engine: `_label_from_score_with_gates()` (L277–330)

Base bands are identical to structural engines above. Then **3 gate checks** override the label downward:

| Gate | Threshold | Override Applied |
|------|-----------|-----------------|
| Crowding score < 40 | `crowd_score < 40` | Label ≥ "Constructive" → demoted to "Mixed" |
| Stability score < 35 | `stability_score < 35` | Label ≥ "Mixed" → demoted to "Cautious" |
| Squeeze signal < 35 | `squeeze_score < 35` | Label ≥ "Cautious" → demoted to "Deteriorating" |

**CRITICAL**: Gates change the LABEL only — the numeric score is NOT modified. A trade getting score=78 with crowding<40 gets label "Mixed but Tradable" despite the 78 score. This is a known finding from 2B (see [Finding F-HIGH-1] and cross-reference in audit_2b).

### 1.3 Sentiment Engine: 4-tier bands

| Score Range | Label |
|-------------|-------|
| ≥ 65 | Risk-On |
| 40–64 | Neutral |
| 25–39 | Mixed |
| < 25 | Risk-Off / High Stress |

The sentiment engine operates on a 0–100 scale with weights summing to 100 (not 1.0). Its 4-tier system is coarser than the 6-tier structural engines — 25 points of score (40–64) all map to "Neutral", making label transitions require larger score movements.

---

## 2. Label-Score Consistency Check

### 2.1 Band Width Distribution (5 Structural Engines)

| Label | Score Range | Width | % of Scale |
|-------|------------|-------|------------|
| Top tier (Strongly Favored) | 85–100 | 15 pts | 15% |
| 2nd tier (Constructive) | 70–84.99 | 15 pts | 15% |
| 3rd tier (Mixed) | 55–69.99 | 15 pts | 15% |
| 4th tier (Cautious) | 45–54.99 | 10 pts | 10% |
| 5th tier (Deteriorating) | 30–44.99 | 15 pts | 15% |
| 6th tier (Hostile) | 0–29.99 | 30 pts | 30% |

**Observations:**
- The **Cautious band (45–54.99) is narrower** (10 pts) than all other bands. Scores crossing 45→55 or 55→44 change label faster.
- The **Hostile band (0–29.99) is twice as wide** (30 pts) as middle bands. This is appropriate: extreme conditions deserve a wide catch-all.
- The top four bands are evenly distributed across 45–100 (roughly 15 pts each), which is sensible.

### 2.2 Could a Human Disagree with the Label?

**Score 45 (Cautious)**: This is the bottom of "Cautious" — 1 point lower and it becomes "Deteriorating". The narrow 45–55 band means a human might view a score of 46 as closer to "Deteriorating" than "Cautious". This is a mild semantic stretch but not egregious.

**Score 55 (Mixed but Tradable)**: Bottom of "Mixed" — a human would likely agree this is mixed territory, not yet constructive. This is appropriate.

**Score 69 (Mixed but Tradable)**: Top of "Mixed" — a score of 69 is labeled "Mixed" while 70 is "Constructive". This boundary is tight but reasonable for a threshold-based system.

### 2.3 Sentiment Engine Band Review

| Label | Score Range | Width |
|-------|------------|-------|
| Risk-On | 65–100 | 35 pts |
| Neutral | 40–64 | 25 pts |
| Mixed | 25–39 | 15 pts |
| Risk-Off | 0–24 | 25 pts |

The **Risk-On band (65–100) is very wide** — 35 points of range all get the same label. This means mild optimism (66) and extreme optimism (95) look the same to label consumers. Given that sentiment is weighted at 20% in the Tactical regime block, this coarseness has limited downstream impact.

---

## 3. Score Bunching Analysis

### 3.1 Normal Market Scenario

**Inputs used** (calm 2024-style market):
VIX=16, VVIX=85, contango=1.05, CBOE Skew=125, 10Y=4.2%, 2Y=4.5%, IG=1.2%, HY=4.0%, DXY=103, P/C=0.75, IV-RV spread=3

| Engine | Normal-Market Composite | Label | Bunching Zone? |
|--------|------------------------|-------|----------------|
| **Volatility** | ~73 | Constructive | No — well above midpoint |
| **Breadth** | ~68 | Mixed but Tradable | Mild — near center |
| **Flows** | ~65 | Mixed but Tradable | Mild — near center |
| **Liquidity** | ~70 | Constructive | No — above midpoint |
| **Cross-Asset Macro** | ~66 | Mixed but Tradable | Mild — near center |
| **Sentiment** | ~55 | Neutral | Yes — center of Neutral band |

**Regime composite** (from block synthesis): ~68 → NEUTRAL (confidence may be low)

### 3.2 Does Bunching Occur?

**During calm markets: Composites cluster in the 55–73 range across all engines.** This is the 55–73 "comfort zone" where:
- Pillar-level scores individually range 55–85 (most land 60–80)
- The weighted-average composite narrows this further (central-tendency bias from 4+ pillar averaging)
- The effective usable scale under normal conditions is **~55–75**, not 0–100

**This is expected behavior** for a calibrated scoring system. Normal markets should NOT produce extremes. However, downstream consumers should understand that:
- Scores of 0–30 represent genuine crisis (2020 COVID, 2008 GFC)
- Scores of 90+ represent once-a-year premium-selling nirvana
- Day-to-day variation is 55–75

### 3.3 What Would It Take to Reach Extremes?

**Composite below 20** (any structural engine):
- VIX > 35–40 (crisis)
- Term structure in backwardation (ratio < 0.95)
- IG spreads > 2.5%, HY > 7%
- Breadth collapse (AD ratio < 0.5)
- All pillars would need to score < 25 simultaneously
- **Verdict**: Requires multi-dimensional stress — not possible from a single metric spike

**Composite above 85**:
- VIX 12–15 in sustained contango (ratio > 1.10)
- Low skew (< 120), positive IV-RV spread
- Strong breadth (AD > 2.0), trending momentum
- Tight credit spreads, falling yields, risk-on sentiment
- **Verdict**: Requires broad market euphoria across all pillars — rare but possible (Q1 2024 type conditions)

### 3.4 Bunching Risk by Engine

| Engine | Bunching Risk | Reason |
|--------|--------------|--------|
| Volatility | **LOW** | 64 interpolation calls, bell curves, and inverted ranges create score diversity across pillars |
| Breadth | **MEDIUM** | Pure linear ladders with no bell curves — pillars tend to co-move |
| Flows | **MEDIUM** | 37 interpolations, bell curves help, but `futures_net_long_pct` dominates 4/5 pillars |
| Sentiment | **HIGH** | 4-tier labels with broad neutral band; keyword-based → tends toward 50 |
| Liquidity | **MEDIUM** | Rate/credit metrics co-move; inverted interpolations help spread but can synchronize |
| Cross-Asset Macro | **MEDIUM** | 23 interpolations; 10Y bell curve and VIX bell curve add diversity, but commodities bunch in neutral zone ($45–85 oil → score 50–55) |

---

## 4. Interpolation Boundary Behavior

### 4.1 The `_interpolate()` Function

All 5 structural engines share an identical `_interpolate()` implementation:

```python
def _interpolate(value, in_low, in_high, out_low, out_high):
    if in_high == in_low:
        return (out_low + out_high) / 2.0  # degenerate range → midpoint
    t = (value - in_low) / (in_high - in_low)
    t = max(0.0, min(1.0, t))  # HARD CLAMP
    return round(out_low + t * (out_high - out_low), 2)
```

**Boundary behavior: CLAMPED. No extrapolation. No error. No overflow.**

- Input below `in_low`: `t` clamped to 0.0 → returns `out_low`
- Input above `in_high`: `t` clamped to 1.0 → returns `out_high`
- Degenerate range (`in_low == in_high`): returns midpoint of output range
- Output is always within `[min(out_low, out_high), max(out_low, out_high)]`

### 4.2 Interpolation Inventory

| Engine | Total Calls | Inverted | Bell Curves | Discontinuities |
|--------|-------------|----------|-------------|-----------------|
| Volatility | 64 | 12 | 6 | **3** |
| Breadth | 25 | 0 | 0 | 0 |
| Flows | 37 | 3 | 4 | 0 |
| Liquidity | 36 | 6 | 0 | 0 |
| Cross-Asset Macro | 23 | 5 | 3 | 0 |
| Sentiment | 0 | — | — | — |
| Regime | 0 (step functions) | — | — | — |
| **TOTAL** | **185** | **26** | **13** | **3** |

### 4.3 Boundary Discontinuities (Volatility Engine Only)

Three multi-range scoring patterns in the volatility engine have **score jumps at boundary transitions**:

| Metric | Boundary Value | Left Score | Right Score | Gap | Location |
|--------|---------------|-----------|------------|-----|----------|
| VIX Rank 30D | 50 | 95 | 75 | **−20 pts** | L318/L320 |
| VIX Percentile 1Y | 50 | 90 | 70 | **−20 pts** | L336/L338 |
| Vol Risk Premium | 1.5 | 95 | 80 | **−15 pts** | L482/L486 |

**Impact**: These are **intentional bell curves** — the score peaks around the optimum value and drops on both sides. The discontinuity occurs because the ascending and descending branches don't share a common peak value. A VIX Rank crossing from 49.99→50.01 would see a 20-point score drop (95→75). While intentional in design, this creates a **non-smooth scoring landscape** at the optimum.

### 4.4 Bell Curve Patterns (13 total across engines)

**Volatility engine (6)**:
- VIX Rank 30D: peak ~50 (95), edges 0→50, 100→30
- VIX Percentile 1Y: peak ~50 (90), edges 0→55, 100→25
- Vol Risk Premium: peak ~1.2–1.5 (95), edges 0.5→20, 2.5→40
- Equity P/C Ratio: peak ~0.7–0.9 (85), edges 0.3→55, 1.5→30
- SPY P/C Ratio: peak ~0.65–0.85 (90), edges 0.3→75, 1.5→30
- VIX Level (6-range mountain): peak ~15 (95), edges 8→60, 80→0

**Flows engine (4)**:
- Positioning Bias: peak at P/C=0.8 (85), edges 0.5→55, 1.5→20
- Directional Exposure: peak at futures_net=55 (82), edges 0→25, 100→18
- Options Posture: peak at VIX=17 (88), edges 8→60, 40→15
- Flow Concentration: peak at VIX=22 (75), edges 8→40, 40→35

**Cross-Asset Macro (3)**:
- Ten-Year Yield: peak at 3.5% (90), edges 0→40, 6.5→20
- VIX Level: peak at 15 (90), edges 8→75, 40→15
- Oil Level: intentional neutral band $45–85 (~50–55), not a true bell curve

### 4.5 Inverted Interpolations (26 total)

"Inverted" means `out_low > out_high` — higher input produces lower score. These are used for metrics where high values are bearish:

**Volatility (12)**: VIX Trend, VIX descending branches, CBOE Skew, Put Skew, Tail Risk, VRP descending branch, P/C descending branches
**Liquidity (6)**: 2Y yield, policy gap, IG spread, HY spread, VIX stress, tightness
**Cross-Asset Macro (5)**: USD level, IG spread, HY spread, gold level, 10Y descending branch
**Flows (3)**: Crowding proxy, positioning bias descending, options posture descending

All are semantically correct — high VIX, high spreads, strong USD, etc. properly produce lower scores.

### 4.6 Regime Service Step Functions (3 functions, no interpolation)

The regime service uses pure step functions:

**`_score_rates_regime()`** (5 bands + direction adjustment):
| 10Y Yield | Base Score | Direction Adjustment (5-day Δ) |
|-----------|-----------|-------------------------------|
| < 3.5% | 90 | > +25 bps: −25 |
| 3.5–4.0% | 75 | +8 to +25: −8 to −15 |
| 4.0–4.5% | 60 | −5 to +8: 0 |
| 4.5–5.0% | 40 | −5 to −10: +5 |
| ≥ 5.0% | 20 | < −10: +10 |

**`_score_volatility_structure()`** (5 bands + direction adjustment):
| VIX Level | Base Score | Direction Adjustment (5-day % chg) |
|-----------|-----------|-----------------------------------|
| < 14 | 90 | > +20%: −15 |
| 14–18 | 80 | +10% to +20%: −8 |
| 18–22 | 55 | −10% to +10%: 0 |
| 22–28 | 35 | < −10%: +5 |
| ≥ 28 | 15 | |

**`_score_rate_pressure()`** (6 bands, no direction adjustment):
| Rate Change (bps) | Score |
|-------------------|-------|
| < −15 | 90 |
| −15 to −5 | 75 |
| −5 to +5 | 60 |
| +5 to +15 | 45 |
| +15 to +25 | 30 |
| > +25 | 15 |

**Boundary behavior**: Step functions have inherent discontinuities at every breakpoint. A yield moving from 3.499% to 3.501% causes a 90→75 score jump (−15 pts). This is a design choice — step functions trade smoothness for simplicity.

---

## 5. Trader Takeaway Generation

### 5.1 Mechanism

**All trader takeaways are 100% deterministic — NO LLM involvement.**

Each engine's `_build_composite_explanation()` method generates the `trader_takeaway` string using the composite score and pillar data:

```python
def _build_composite_explanation(self, composite_score, pillars, ...):
    if composite_score >= 85:
        tone = "Premium selling strongly favored..."
    elif composite_score >= 70:
        tone = "Constructive environment..."
    elif composite_score >= 55:
        tone = "Mixed conditions..."
    elif composite_score >= 45:
        tone = "Caution warranted..."
    elif composite_score >= 30:
        tone = "Deteriorating conditions..."
    else:
        tone = "Hostile environment..."
    # Appends pillar-specific notes based on individual pillar scores
    return tone + pillar_notes
```

### 5.2 Consistency with Labels

The takeaway thresholds align with the label bands (85/70/55/45/30). Each takeaway message opens with language that matches the assigned label. Pillar-specific notes (e.g., "VIX term structure is in backwardation") are appended based on individual pillar scores, providing detail beyond the top-level label.

### 5.3 Confidence Warning

All 5 structural engines inject a confidence warning when `confidence < 60`:
```python
if confidence < 60:
    explanation += " (Note: confidence is moderate — data may be limited.)"
```

This is informational only — it does not modify the score or label.

### 5.4 Sentiment Engine

The sentiment engine builds its takeaway from the weighted sentiment aggregate score with similar tier-based messaging. It includes per-source notes (e.g., "Finnhub sentiment negative, Yahoo neutral"). Like structural engines, it is fully deterministic.

---

## 6. Strategy Suitability Scores

### 6.1 Location

Volatility engine only — Pillar 5 ("Strategy Suitability"), weighted at 15% of the Vol composite.

### 6.2 Four Strategy Submetrics

| Strategy | Weight in Pillar 5 | Key Inputs |
|----------|-------------------|------------|
| `premium_selling` | 40% | VIX level (30%), IV/RV ratio (25%), contango (20%), skew (15%), VIX rank (10%) |
| `directional` | 20% | VIX rank (40%, U-shaped), VIX spot (35%), VVIX (25%) |
| `vol_structure_plays` | 20% | Contango signal (40%), vol premium (30%), IV rank (30%) |
| `hedging` | 20% | IV level (30%), skew (30%), VIX spot (25%), tail risk (15%) |

### 6.3 Output Assembly

Strategy scores are extracted 1:1 from Pillar 5 submetrics (no separate computation layer):

```python
# ~L1243 in volatility_options_engine.py
strategy_scores = {}
for sm in strat_pillar.get("submetrics", []):
    strategy_scores[sm["name"]] = {
        "score": sm.get("score"),
        "description": sm.get("details", {}).get("description", ""),
    }
```

### 6.4 Traceability

A downstream consumer CAN trace why `premium_selling` scored 58:
1. `premium_selling` score = weighted average of 5 subcomponents
2. Each subcomponent uses `_interpolate()` from a raw market input
3. The raw inputs (VIX, IV/RV ratio, contango, skew, VIX rank) are all from Tradier/market data

**No opacity** — every strategy score is a deterministic function of observable inputs.

### 6.5 Scope Limitation

Strategy suitability scores are derived ONLY from Pillar 5 submetrics. They do NOT incorporate:
- Cross-engine signals (breadth, liquidity, macro conditions)
- Regime label or regime score
- Flows/positioning data

This means `premium_selling` could score 85 (vol conditions favorable) while the regime is RISK_OFF (broad market stress). Downstream consumers must cross-reference strategy scores with regime context.

---

## 7. Regime Label Assignment

### 7.1 Location

`regime_service.py` → `_assign_label()` (L1095–1117)

### 7.2 Label Mapping

The regime service produces ONE primary label (`regime_label`) from the weighted composite score of 3 blocks (Structural 30%, Tape 40%, Tactical 30%):

| Score Range | Alignment | Confidence | Label |
|-------------|-----------|------------|-------|
| ≥ 65 | blocks aligned | ≥ 0.4 | **RISK_ON** |
| ≥ 65 | conflicting or <0.4 conf | — | **RISK_ON_CAUTIOUS** |
| 40–64 | any | any | **NEUTRAL** |
| < 40 | any | < 0.4 | **NEUTRAL** (overrides to NEUTRAL on low confidence) |
| < 40 | blocks aligned | ≥ 0.4 | **RISK_OFF** |
| < 40 | conflicting | ≥ 0.4 | **RISK_OFF_CAUTION** |
| < 30 | aligned | ≥ 0.4 | **RISK_OFF** |

### 7.3 Block-Level Labels

Each regime block (Structural, Tape, Tactical) also gets its own label based on its block score, using the same 6-tier bands as the structural engines.

### 7.4 Observations

- The **NEUTRAL band (40–64)** is wide (25 pts) and acts as a "default" for ambiguous conditions.
- Low-confidence risk-off is promoted to NEUTRAL — this prevents overreacting to uncertain bearish signals.
- The alignment check requires all 3 blocks to agree on direction for a definitive RISK_ON or RISK_OFF call.
- Score alone is insufficient for RISK_ON — alignment AND confidence must also be favorable.

---

## 8. what_works / what_to_avoid Generation

### 8.1 Mechanism

**100% deterministic — NO LLM involvement.**

Five deterministic builder functions in `regime_service.py`:

| Function | Inputs Used | Block-Enhanced? |
|----------|-------------|-----------------|
| `_build_interpretation()` (L1099–1131) | regime label, score, block labels | Yes |
| `_build_playbook()` (L1134–1161) | regime label only | No |
| `_build_what_works_avoids()` (L1163–1199) | regime label, tape label, tactical label | Yes |
| `_build_change_triggers()` (L1221–1250) | regime label only | No |
| `_build_key_drivers()` | key_signals from blocks | Yes (signal-driven) |

### 8.2 Playbook Mapping

| Regime Label | Primary Strategies | Avoid |
|--------------|-------------------|-------|
| RISK_ON | put_credit_spread, covered_call, call_debit | Large undefined risk |
| RISK_ON_CAUTIOUS | put_credit_spread (smaller), iron_condor | Aggressive directional |
| NEUTRAL | iron_condor, credit_spread_wider, calendar | Undefined risk, naked positions |
| RISK_OFF_CAUTION | put_debit, iron_condor (bearish bias), cash | Premium selling, naked calls |
| RISK_OFF | put_debit, cash, hedges | All premium selling, all credit risk |

### 8.3 what_works Block Enhancement

The `what_works` list is augmented based on block-level labels:

| Condition | Added Recommendation |
|-----------|---------------------|
| RISK_ON + tape="Trending" | "Momentum continuation entries on pullbacks" |
| RISK_ON + tactical="Expansionary" | "Vol premium capture via short strangles" |
| RISK_OFF + tape="Weakening" | "Bearish debit spreads on breakdown confirmations" |
| RISK_OFF + tactical="Contractive" | "Cash / money market positions" |
| NEUTRAL + tape="Rotational" | "Sector rotation plays with defined risk" |
| NEUTRAL + tactical="Transitional" | "Small position sizes while regime clarifies" |

### 8.4 Determinism Verification

All 5 functions use only string templates and conditional logic. There are:
- No API calls
- No LLM model invocations
- No randomness or timestamp-dependent behavior
- Same inputs always produce identical outputs

---

## 9. Summary Table

| Engine | Score Range (Normal) | Score Range (Full) | Label Distribution | Bunching Risk | Boundary Behavior |
|--------|---------------------|--------------------|--------------------|---------------|-------------------|
| **Volatility** | 65–80 | 0–100 | 6 tiers, even (except narrow Cautious) | LOW | Clamped; 3 discontinuities at bell curve peaks |
| **Breadth** | 55–75 | 0–100 | 6 tiers, even | MEDIUM | Clamped; all boundaries aligned |
| **Flows** | 55–72 | 0–100 | 6 tiers + gate overrides | MEDIUM | Clamped; all boundaries aligned |
| **Sentiment** | 45–65 | 0–100 | 4 tiers (coarse) | HIGH | N/A (no interpolation — keyword-based) |
| **Liquidity** | 60–78 | 0–100 | 6 tiers, even | MEDIUM | Clamped; all boundaries aligned |
| **Cross-Asset Macro** | 58–74 | 0–100 | 6 tiers, even | MEDIUM | Clamped; all boundaries aligned |
| **Regime** | 55–72 | 0–100 | 5 tiers + alignment/confidence checks | MEDIUM | Step functions with inherent breakpoint jumps |

---

## 10. Findings

### HIGH Severity

#### [F-2C-HIGH-1] VIX Rank / VIX Percentile Discontinuities (Volatility Engine)

**Location**: `volatility_options_engine.py` L316–340
**Issue**: The VIX Rank 30D and VIX Percentile 1Y scoring functions have **20-point score drops** at their peak boundaries (value=50). The ascending branch ends at 95 but the descending branch starts at 75, creating a non-smooth transition. A VIX Rank crossing from 49.99→50.01 sees a 95→75 score jump.
**Impact**: These submetrics each contribute to Pillar 1 (Vol Regime, 25% composite weight) with 10–15% pillar weight. The 20-point discontinuity at the optimum is counter-intuitive — the "best" VIX rank should produce the highest score, not a score 20 points below the ascending peak.
**Risk**: Downstream consumers who expect smooth scoring surfaces will see unexpected jumps. The composite dampens this (~3-point effect on composite), but it represents a calibration gap.
**Recommendation**: Align peak values — the ascending branch should end at the same score where the descending branch begins (both at 85 or both at 90).

#### [F-2C-HIGH-2] Flows Engine Label-Score Disconnect

**Location**: `flows_positioning_engine.py` L277–330
**Issue**: The gate system in `_label_from_score_with_gates()` changes the label WITHOUT modifying the numeric score. A score of 78 with crowding<40 gets label "Mixed but Tradable" — the LABEL says caution but the SCORE says constructive.
**Impact**: Any downstream consumer using the SCORE gets a different signal than one using the LABEL. This is a duality that should be explicit in the output (e.g., `gated_label` vs `raw_label`).
**Cross-reference**: Also identified in audit_2b as a HIGH finding.
**Recommendation**: Either (a) adjust the score when gates fire, or (b) emit separate `raw_label` and `gated_label` fields so consumers know a gate was active.

#### [F-2C-HIGH-3] Strategy Suitability Scores Ignore Cross-Engine Context

**Location**: `volatility_options_engine.py` ~L760–970
**Issue**: Strategy suitability scores (premium_selling, directional, vol_structure_plays, hedging) are computed ONLY from volatility-engine submetrics. They do not incorporate regime label, breadth, flows, or macro conditions. A `premium_selling` score of 85 can coexist with a RISK_OFF regime.
**Impact**: Downstream consumers (scanners, trade builders) that rely on strategy scores without cross-referencing the regime label may enter premium-selling trades during market stress.
**Recommendation**: Document this limitation explicitly in the strategy_scores output, or add a `regime_context` field alongside strategy scores.

### MEDIUM Severity

#### [F-2C-MED-1] Central Tendency Bias — Effective Score Range ≈ 55–75 in Normal Markets

**Issue**: Weighted averaging across 4–5 pillars per engine compresses the composite score toward the center. Under normal market conditions, all 6 engines + regime produce composites in the 55–75 range. The 0–100 scale is effectively a 55–75 scale in practice.
**Impact**: Small composite differences (e.g., 62 vs 68) correspond to meaningfully different market conditions but appear close together. The narrow "Cautious" band (45–54.99) is particularly impacted — a composite needs genuine multi-pillar stress to drop into it.
**Recommendation**: Downstream consumers should treat composite scores as ordinal rankings within the practical range, not as absolute percentages.

#### [F-2C-MED-2] Sentiment Engine Coarse 4-Tier Labeling

**Issue**: The sentiment engine uses 4 labels vs 6 for structural engines. The "Neutral" band spans 25 points (40–64), and "Risk-On" spans 35 points (65–100). This means mild and extreme conditions share the same label.
**Impact**: Regime service consumes sentiment as part of the Tactical block (20% weight). The coarse labeling means the regime gets less granular sentiment signal than it does from other engines.
**Recommendation**: Consider aligning sentiment to the 6-tier system, or adding a secondary sentiment intensity metric.

#### [F-2C-MED-3] VRP Discontinuity at Peak (Volatility Engine)

**Location**: `volatility_options_engine.py` L482–486
**Issue**: The Vol Risk Premium scoring has a 15-point discontinuity at VRP=1.5 (ascending ends at 95, descending starts at 80). Similar to F-2C-HIGH-1 but with a smaller gap.
**Impact**: VRP is part of Pillar 2 (Vol Structure, 25% composite weight) with ~20% pillar weight. The composite effect is ~2 points.
**Recommendation**: Align the peak values of the ascending and descending branches.

#### [F-2C-MED-4] Regime Step Functions Create Large Score Jumps

**Issue**: The 3 regime scoring helpers use pure step functions with 15–25 point jumps between adjacent bands. A 10Y yield moving from 3.499%→3.501% causes a 90→75 base score change (−15 pts). VIX crossing 22→22.01 causes 55→35 jump (−20 pts).
**Impact**: Block scores can jump significantly on small input changes at breakpoints. The 3-block weighted average partially smooths this, but the step function nature means regime labels can flip on tiny market moves near breakpoints.
**Recommendation**: Consider replacing step functions with `_interpolate()` for smoother transitions, or document the step-function behavior for regime consumers.

#### [F-2C-MED-5] Neutral Regime Band Acts as Default Catch-All

**Issue**: The NEUTRAL regime label covers scores 40–64 (25 points) AND also captures low-confidence risk-off conditions (score < 40 but confidence < 0.4). This means NEUTRAL is the most common regime label in practice.
**Impact**: Downstream playbook recommendations for NEUTRAL are generic ("iron condors, wider credit spreads, calendars"). Traders in a 42-score neutral vs a 62-score neutral get the same playbook despite meaningfully different conditions.
**Recommendation**: Consider sub-labeling NEUTRAL (e.g., NEUTRAL_BEARISH_LEANING vs NEUTRAL_BULLISH_LEANING) based on score within the band.

#### [F-2C-MED-6] Narrow "Cautious" Band (45–54.99 = 10 points)

**Issue**: The Cautious label band is only 10 points wide vs 15 for all other mid-range bands. Scores transit through Cautious quickly, spending little time in this state before becoming either "Mixed" (↑) or "Deteriorating" (↓).
**Impact**: The label transition from "Mixed" (55) → "Deteriorating" (30) happens faster than from "Constructive" (70) → "Mixed" (55), creating asymmetric label velocity. The Cautious label is underrepresented in practice.
**Recommendation**: Consider widening Cautious to 40–54.99 (at the expense of Deteriorating, which would become 25–39.99). This is a trade-off and the current design may be intentional.

### LOW Severity

#### [F-2C-LOW-1] Oil Price Neutral Band Creates Dead Zone

**Location**: `cross_asset_macro_engine.py` L445–462
**Issue**: Oil prices from $45–$85 produce scores in the narrow 50–55 range — a 40-point input range mapping to just 5 points of output. This is intentional (oil in the "normal" range has minimal macro signal), but it means the oil submetric contributes almost no differentiation under typical conditions.
**Impact**: Minimal — oil is one submetric within one pillar.

#### [F-2C-LOW-2] Confidence Warning Threshold (< 60) Is Generous

**Issue**: Confidence < 60 triggers a warning in trader takeaways, but confidence is not used to weight scores or modify labels. Even at confidence = 20, the score is presented at face value with only a text note.
**Impact**: Low — confidence is informational. But downstream consumers may not notice the warning amid other takeaway text.

#### [F-2C-LOW-3] 10Y Yield Bell Curve Peak (3.5%) May Be Stale

**Location**: `cross_asset_macro_engine.py` L346–349
**Issue**: The 10Y yield scoring peaks at 3.5% (score=90), treating this as the "goldilocks" rate. In 2024–2025, sustained yields of 4.0–4.5% are normal, yet the scoring treats this as sub-optimal (score ~60-75). The peak may need periodic recalibration.
**Impact**: Low in isolation (one submetric), but the same 10Y yield feeds both Cross-Asset Macro and Liquidity engines, so the calibration affects two composites.

---

## 11. Cross-References

| Finding | Related Audit | Notes |
|---------|---------------|-------|
| F-2C-HIGH-2 (Flows label-score disconnect) | 2B F-HIGH-1 | Same finding, consistent |
| F-2C-MED-1 (Central tendency bias) | 2B F-MED-7 | 2B identified 4 layers of averaging; 2C quantifies the practical range |
| F-2C-HIGH-1 (VIX Rank discontinuity) | 2A (submetric inventory) | 2A cataloged interpolation ranges; 2C identifies the boundary behavior |
| F-2C-HIGH-3 (Strategy scores ignore context) | 2A (Pillar 5 scope) | 2A documented Pillar 5 inputs; 2C highlights the cross-engine blindness |
| F-2C-MED-5 (Neutral catch-all) | 2B (regime synthesis) | 2B documented block weights; 2C identifies the downstream label compression |
