# Audit 2D — Confidence Score Computation

**Auditor**: Copilot (automated)
**Date**: 2025-07-18
**Scope**: All 6 MI engines + confidence_framework.py + regime service — confidence computation, penalty catalogs, framework integration, calibration
**Engine locations**: `BenTrade/backend/app/services/`

---

## Table of Contents

1. [Engine-by-Engine Confidence Audit](#1-engine-by-engine-confidence-audit)
2. [Engines Without _compute_confidence()](#2-engines-without-_compute_confidence)
3. [Confidence Framework (confidence_framework.py)](#3-confidence-framework)
4. [Regime Service Confidence](#4-regime-service-confidence)
5. [Comparison Table](#5-comparison-table)
6. [Findings](#6-findings)
7. [Cross-References](#7-cross-references)

---

## 1. Engine-by-Engine Confidence Audit

### 1.1 Volatility Options Engine

**Function**: `_compute_confidence()` at `volatility_options_engine.py` L975–1016
**Base confidence**: 100.0 (0–100 scale)

#### Penalty Catalog

| # | Trigger Condition | Penalty Amount | Cap | Type |
|---|---|---|---|---|
| 1 | Missing submetrics (`missing_count > 0` across pillars) | `total_missing * 5` | **40** | Additive (capped) |
| 2 | Cross-pillar spread > 30 pts | `(spread - 30) * 0.5` | **15** | Additive (capped) |
| 3 | Fewer than 3 active pillars | `(3 - active_pillars) * 10` | 20 (implicit: max 2 missing pillars × 10) | Additive |

#### Penalty Stacking
All 3 penalties fire simultaneously and are additive.
- **Theoretical minimum**: 100 − 40 − 15 − 20 = **25.0**
- **Floor**: `_clamp(base)` → clamped to [0, 100]

#### Data Quality Dimensions

| Dimension | Covered? | Amount |
|-----------|----------|--------|
| Missing data | ✅ YES | −5 per missing submetric, capped −40 |
| Proxy reliance | ❌ NO | — |
| Cross-pillar disagreement | ✅ YES | −0.5 per point over 30, capped −15 |
| Temporal staleness | ❌ NO | — |

#### Usage
- Returned as `(confidence, penalties_list)` tuple
- Used in `_build_composite_explanation()` to append note if `confidence < 60`
- **Purely informational** — does NOT weight the composite score or modify the label

---

### 1.2 Breadth Engine

**Function**: `_compute_confidence()` at `breadth_engine.py` L1059–1087  
**Delegates to**: `breadth_diagnostics.compute_quality_scores()` at `breadth_diagnostics.py` L555+  
**Base confidence**: 100.0 (0–100 scale)

#### Penalty Catalog (from breadth_diagnostics.py)

| # | Trigger Condition | Penalty Amount | Cap | Type |
|---|---|---|---|---|
| 1 | Missing active submetrics | `missing_pct * 30` (where `missing_pct = missing / total`) | **30** | Additive (proportional) |
| 2 | Universe coverage < 90% | `(1 - coverage) * 40` | **40** | Additive (proportional) |
| 3 | Cross-pillar disagreement | Variable from `analyze_disagreement()` | Variable | Additive |
| 4 | Survivorship bias risk | Variable from `assess_survivorship_risk()` | Variable | Additive |
| 5 | Pillar(s) unavailable (score=None) | `unavailable_count * 10` | ~50 (5 pillars × 10) | Additive |
| 6 | EW benchmark unavailable | −5 | **5** | Additive (special case) |

#### Penalty Stacking
All penalties are additive and can stack.
- **Theoretical minimum**: 0.0 (all penalties can drain to 0, clamped via `max(0, min(100, ...))`)
- **Floor**: 0.0

#### Data Quality Dimensions

| Dimension | Covered? | Amount |
|-----------|----------|--------|
| Missing data | ✅ YES | −30 max (proportional to % missing) |
| Proxy reliance | ❌ NO | — |
| Cross-pillar disagreement | ✅ YES | Variable |
| Temporal staleness | ❌ NO (but survivorship bias partially covers this) | — |
| Universe coverage | ✅ YES | −40 max (unique to breadth) |
| Survivorship bias | ✅ YES | Variable (unique to breadth) |

#### Additional Output
Breadth diagnostics returns a richer structure than other engines:
- `confidence_score` (0–100)
- `data_quality_score` (0–100) — separate metric
- `historical_validity_score` (0–100) — separate metric
- `signal_quality` ("high"/"medium"/"low")
- Detailed penalty breakdown with per-factor amounts

---

### 1.3 Flows & Positioning Engine

**Function**: `_compute_confidence()` at `flows_positioning_engine.py` L1150–1228  
**Base confidence**: 100.0 (0–100 scale)

#### Penalty Catalog

| # | Trigger Condition | Penalty Amount | Cap | Type |
|---|---|---|---|---|
| 1 | Missing entire pillar(s) (score=None) | `missing_count * 15` | Uncapped | Additive |
| 2 | Missing submetrics within pillars | `total_missing * 3` | **30** | Additive (capped) |
| 3 | Cross-pillar range > 35 pts | `(range - 35) * 0.5` | **15** | Additive (capped) |
| 4 | Heavy proxy reliance (≥ 4 proxy sources) | **8** | N/A | Additive |
| 5 | Moderate proxy reliance (≥ 2 proxy sources) | **4** | N/A | Additive (else branch of #4) |
| 6 | Stale data sources | `stale_count * 3` | **12** | Additive (capped) |
| 7 | No direct institutional flow data | **5** | N/A | Additive |
| 8 | No direct futures positioning data | **5** | N/A | Additive |
| 9 | Single-source dependency (≤1 upstream + ≥6 proxies) | **12** | N/A | Additive |

#### Penalty Stacking
All 9 penalties are additive and can stack simultaneously.
- **Theoretical minimum**: 100 − 75 (all pillars missing) − 30 − 15 − 8 − 12 − 5 − 5 − 12 = **−62** → clamped to **0.0**
- **Floor**: `_clamp(..., 0, 100)` → 0.0
- **Realistic worst case** (partial data): ~15–25

#### Data Quality Dimensions

| Dimension | Covered? | Amount |
|-----------|----------|--------|
| Missing data | ✅ YES | −15/pillar + −3/submetric (capped −30) |
| Proxy reliance | ✅ YES | −4 moderate, −8 heavy, −12 single-source |
| Cross-pillar disagreement | ✅ YES | −0.5 per pt over 35, capped −15 |
| Temporal staleness | ✅ YES | −3 per stale source, capped −12 |
| Specific data checks | ✅ YES | −5 no flow data, −5 no futures data |

**Note**: The Flows engine has the **most comprehensive** confidence computation of all 6 engines.

---

### 1.4 Liquidity Conditions Engine

**Function**: `_compute_confidence()` at `liquidity_conditions_engine.py` L1331–1422  
**Base confidence**: 100.0 (0–100 scale)

#### Penalty Catalog

| # | Trigger Condition | Penalty Amount | Cap | Type |
|---|---|---|---|---|
| 1 | Missing entire pillar(s) | `missing_count * 15` | Uncapped | Additive |
| 2 | Missing submetrics | `total_missing * 3` | **30** | Additive (capped) |
| 3 | Per-pillar proxy concentration (>50% proxy weight) | `proxy_heavy_count * 4` | Uncapped | Additive |
| 4 | Cross-pillar range > 35 pts | `(range - 35) * 0.5` | **15** | Additive (capped) |
| 5 | Heavy proxy reliance (≥ 4 proxy sources) | **8** | N/A | Additive |
| 6 | Moderate proxy reliance (≥ 2 proxy sources) | **4** | N/A | Additive (else branch of #5) |
| 7 | Stale data sources | `stale_count * 3` | **12** | Additive (capped) |
| 8 | No credit spread data | **5** | N/A | Additive |
| 9 | No direct funding stress data | **5** | N/A | Additive |

#### Penalty Stacking
All 9 penalties additive, can stack.
- **Theoretical minimum**: 100 − 75 − 30 − 20 − 15 − 8 − 12 − 5 − 5 = **−70** → clamped to **0.0**
- **Floor**: `_clamp(..., 0, 100)` → 0.0

#### Data Quality Dimensions

| Dimension | Covered? | Amount |
|-----------|----------|--------|
| Missing data | ✅ YES | −15/pillar + −3/submetric (capped −30) |
| Proxy reliance | ✅ YES | −4 per >50% proxy pillar + −4/−8 source-level |
| Cross-pillar disagreement | ✅ YES | −0.5 per pt over 35, capped −15 |
| Temporal staleness | ✅ YES | −3 per stale source, capped −12 |
| Specific data checks | ✅ YES | −5 no credit spreads, −5 no funding data |

**Unique**: Per-pillar proxy concentration check (>50% by weight) — only engine with this granularity.

---

### 1.5 Cross-Asset Macro Engine

**Function**: `_compute_confidence()` at `cross_asset_macro_engine.py` L946–1002  
**Base confidence**: 100.0 (0–100 scale)

#### Penalty Catalog

| # | Trigger Condition | Penalty Amount | Cap | Type |
|---|---|---|---|---|
| 1 | Missing entire pillar(s) | `missing_count * 15` | Uncapped | Additive |
| 2 | Missing submetrics | `total_missing * 3` | **25** | Additive (capped — note: 25 not 30) |
| 3 | Cross-pillar range > 40 pts | `(range - 40) * 0.5` | **15** | Additive (capped — note: threshold is 40, not 35) |
| 4 | FRED copper > 5 days stale | `3 + max(0, (days - 15) * 0.25)` | **8** | Additive (scaled) |
| 5 | Copper present but monthly FRED series | **1** | N/A | Additive (note only) |

#### Penalty Stacking
- **Theoretical minimum**: 100 − 75 − 25 − 15 − 8 − 1 = **−24** → clamped to **0.0**
- **Floor**: `_clamp(..., 0, 100)` → 0.0
- **Realistic minimum** (with data): ~36.0

#### Data Quality Dimensions

| Dimension | Covered? | Amount |
|-----------|----------|--------|
| Missing data | ✅ YES | −15/pillar + −3/submetric (capped −25) |
| Proxy reliance | ❌ NO | — |
| Cross-pillar disagreement | ✅ YES | −0.5 per pt over 40, capped −15 |
| Temporal staleness | ✅ PARTIAL | Copper-specific only (not general staleness) |

**Unique**: Higher disagreement threshold (40 vs 30–35) and lower submetric cap (25 vs 30).

---

## 2. Engines Without _compute_confidence()

### 2.1 News Sentiment Engine

**File**: `news_sentiment_engine.py`  
**Has `_compute_confidence()`**: ❌ **NO**

**What appears instead**:
- Output dict contains `signal_quality.strength` ("high" / "medium" / "low") and `signal_quality.explanation`
- **No `confidence` or `confidence_score` key** in the output dict
- Signal quality is determined by score thresholds and component analysis — not a penalty-based computation

**Downstream impact**: When the regime service calls `_extract_engine_confidence(mi_results, "news_sentiment")`:
1. It looks for `data.get("confidence")` → `None`
2. Falls back to `data.get("confidence_score")` → `None`
3. Returns `None` → excluded from the tactical block's confidence average

**Result**: The tactical block's confidence is computed from only volatility and flows engines (+ direct FRED data at 0.85). The news sentiment engine's data quality is invisible to regime confidence.

---

## 3. Confidence Framework

### 3.1 Location & Design

**File**: `confidence_framework.py` at `BenTrade/backend/app/services/`

#### Penalty Tables (0.0–1.0 scale)

**QUALITY_PENALTIES**:
| Status | Penalty |
|--------|---------|
| good | 0.00 |
| acceptable | 0.00 |
| degraded | 0.15 |
| poor | 0.30 |
| unavailable | 0.40 |
| unknown | 0.10 |

**FRESHNESS_PENALTIES**:
| Status | Penalty |
|--------|---------|
| live | 0.00 |
| recent | 0.00 |
| stale | 0.10 |
| very_stale | 0.25 |
| unknown | 0.05 |

**CONFLICT_PENALTIES**:
| Severity | Penalty |
|----------|---------|
| none | 0.00 |
| low | 0.05 |
| moderate | 0.15 |
| high | 0.30 |

**COVERAGE_PENALTIES**:
| Level | Penalty |
|-------|---------|
| full | 0.00 |
| high | 0.02 |
| partial | 0.10 |
| sparse | 0.15 |
| minimal | 0.25 |
| none | 0.40 |

#### Key Functions
- `normalize_confidence(raw)` — converts 0–100 or 0–1 to canonical 0.0–1.0
- `confidence_label(score)` — "high"/"moderate"/"low"/"none" from 0–1
- `signal_quality_label(score)` — "high"/"medium"/"low"
- `build_confidence_assessment(...)` — structured assessment with impacts
- `apply_impacts(base_score, impacts)` — subtracts penalties from base

### 3.2 Integration Status

| Consumer | Imports confidence_framework? | Uses penalty tables? |
|----------|-------------------------------|---------------------|
| `market_composite.py` | ✅ YES | ✅ YES |
| `decision_prompt_payload.py` | ✅ YES (`quick_assess`) | ✅ YES |
| `decision_response_contract.py` | ✅ YES | ✅ YES |
| `volatility_options_engine.py` | ❌ NO | ❌ NO — inline |
| `breadth_engine.py` | ❌ NO | ❌ NO — uses diagnostics |
| `flows_positioning_engine.py` | ❌ NO | ❌ NO — inline |
| `news_sentiment_engine.py` | ❌ NO | ❌ NO — no confidence |
| `liquidity_conditions_engine.py` | ❌ NO | ❌ NO — inline |
| `cross_asset_macro_engine.py` | ❌ NO | ❌ NO — inline |
| `regime_service.py` | ❌ NO | ❌ NO — own formula |

### 3.3 Framework–Engine Gap Analysis

#### Framework defines penalties that NO engine applies:

| Framework Category | Framework Penalty | Engine Equivalent |
|--------------------|------------------|-------------------|
| `QUALITY_PENALTIES["degraded"]` = 0.15 | N/A | No engine uses "degraded" status labels |
| `QUALITY_PENALTIES["poor"]` = 0.30 | N/A | No engine uses "poor" status labels |
| `QUALITY_PENALTIES["unavailable"]` = 0.40 | Engines use `missing_count` instead | Different mechanism |
| `FRESHNESS_PENALTIES["stale"]` = 0.10 | Flows/Liquidity: −3 per stale source | Different scale (0–1 vs 0–100) |
| `FRESHNESS_PENALTIES["very_stale"]` = 0.25 | Cross-Asset: copper-specific only | Not generalized |
| `COVERAGE_PENALTIES["partial"]` = 0.10 | Breadth: universe coverage penalty | Different formula |

#### Engines apply penalties NOT in the framework:

| Engine Penalty | Framework Equivalent |
|----------------|---------------------|
| Vol: `missing_count * 5` (data completeness) | `QUALITY_PENALTIES` (but different calculation) |
| Flows: `−5` no direct flow data | No framework category for data source absence |
| Flows: `−12` single-source dependency | No framework category |
| Liquidity: per-pillar proxy >50% check | `COVERAGE_PENALTIES` (rough match only) |
| Cross-Asset: copper staleness days-based | `FRESHNESS_PENALTIES` (categorical vs numeric) |
| Breadth: survivorship bias | No framework category |
| Breadth: universe coverage | `COVERAGE_PENALTIES` (different formula) |

### 3.4 Scale Mismatch

The framework operates on a **0.0–1.0 scale** with penalties like 0.15, 0.30, etc. All engines operate on a **0–100 scale** with penalties like −5, −15, −40. This fundamental scale difference means the framework CANNOT be used directly by engines without conversion, explaining the disconnect.

---

## 4. Regime Service Confidence

### 4.1 Engine Confidence Extraction

**Function**: `_extract_engine_confidence()` at `regime_service.py` L355–368

```python
conf = data.get("confidence")        # First try
if conf is None:
    conf = data.get("confidence_score")  # Fallback
# ...
return max(0.0, min(1.0, float(conf)))   # Clamp to [0.0, 1.0]
```

**Scale normalization**: Engine confidence values (0–100) are clamped to [0.0, 1.0]. This means a confidence of 85 (out of 100) is treated as 0.85 **only if 0 < conf < 1 already**. If `conf = 85`, the clamp produces `min(1.0, 85.0) = 1.0` — this is **always 1.0 for any engine confidence > 1.0**.

**CRITICAL**: There is a potential scale interpretation issue. If engines return confidence on 0–100 scale and regime_service clamps to [0.0, 1.0], any engine confidence ≥ 1.0 is treated as maximum confidence (1.0). This effectively means engine confidence distinction (e.g., 55 vs 85) is **lost** — both become 1.0 after clamping. **However**, this would only matter IF engine confidence is not divided by 100 upstream. Need to verify the engine output dict — see Finding F-2D-HIGH-1.

### 4.2 Block-Level Confidence (Per Block)

Each block computes its own confidence by averaging extracted engine confidences:

**Structural block** (L742–780):
```python
confs = [
    self._extract_engine_confidence(mi_results, "liquidity_financial_conditions"),
    self._extract_engine_confidence(mi_results, "cross_asset_macro"),
]
# Plus fixed confidences for direct data:
# FRED 10Y → 0.9
# VIX data → 0.9
block_confidence = sum(valid_confs) / len(valid_confs) if valid_confs else 0.5
```

**Tape block** (L823–876):
```python
confs = [
    self._extract_engine_confidence(mi_results, "breadth_participation"),
]
# Plus fixed: trend → 0.9, small-cap → 0.85
block_confidence = average(valid_confs) or 0.5
```

**Tactical block** (L878–990):
```python
confs = [
    self._extract_engine_confidence(mi_results, "volatility_options"),
    self._extract_engine_confidence(mi_results, "flows_positioning"),
    self._extract_engine_confidence(mi_results, "news_sentiment"),  # → always None
]
# Plus fixed: rate_pressure → 0.85
block_confidence = average(valid_confs) or 0.5
```

### 4.3 Regime-Level Confidence

**Location**: Within `_synthesize()` at L994–1056

#### Formula

```python
coverage = len(available_blocks) / 3.0       # How many of 3 blocks have scores
base_confidence = coverage * 0.85             # Max 0.85 from coverage alone

# Conflict penalty from block score spread
if max_spread > 15.0:
    conflict_penalty = min(0.30, (max_spread - 15.0) / 100.0)
else:
    conflict_penalty = 0.0

confidence = base_confidence - conflict_penalty
confidence = max(0.1, min(0.95, confidence))  # Floor 0.1, ceiling 0.95
```

#### Base Confidence by Coverage

| Blocks Available | Base Confidence |
|------------------|-----------------|
| 3 of 3 | 0.85 |
| 2 of 3 | 0.567 |
| 1 of 3 | 0.283 |

#### Conflict Penalty

| Block Spread | Penalty | Result (from 3-block base) |
|-------------|---------|---------------------------|
| ≤ 15 pts | 0.0 | 0.85 |
| 20 pts | 0.05 | 0.80 |
| 30 pts | 0.15 | 0.70 |
| 40 pts | 0.25 | 0.60 |
| ≥ 45 pts | 0.30 (cap) | 0.55 |

#### Output Range
- **Minimum**: 0.10 (floor)
- **Maximum**: 0.95 (ceiling)
- **Scale**: 0.0–1.0

### 4.4 Does Regime Confidence Weight by Engine Confidence?

**NO.** Engine confidence values are:
1. Extracted per block and averaged into a block-level confidence
2. Block-level confidence is stored in the output but is **NOT used to weight block scores**
3. Block scores use FIXED weights (Structural 30%, Tape 40%, Tactical 30%)
4. Regime-level confidence is based ONLY on coverage (block availability) and block score conflict

**Engine confidence is fully decoupled from score aggregation at the regime level.**

### 4.5 Confidence's Role in Regime Labeling

Regime confidence IS used for one purpose:
```python
if confidence < 0.4:
    return "NEUTRAL"  # Override regardless of score
```

If regime confidence drops below 0.4 (40%), the label is forced to NEUTRAL regardless of what the score says. This only happens when:
- Fewer than 2 blocks available (base < 0.567), OR
- 2 blocks available + significant conflict penalty

---

## 5. Comparison Table

| Engine | Has `_compute_confidence`? | Base | Scale | Missing Data Penalty | Proxy Penalty | Disagreement Penalty | Staleness Penalty | Min Possible | Max Possible |
|--------|---------------------------|------|-------|---------------------|---------------|---------------------|------------------|-------------|-------------|
| **Volatility** | ✅ | 100 | 0–100 | −5/sub, cap −40 | ❌ None | −0.5/pt >30, cap −15 | ❌ None | 25 | 100 |
| **Breadth** | ✅ (delegates) | 100 | 0–100 | −30 max (proportional) | ❌ None | ✅ Variable | ❌ (survivorship partial) | 0 | 100 |
| **Flows** | ✅ | 100 | 0–100 | −15/pillar + −3/sub cap −30 | −4/−8 + −12 single-src | −0.5/pt >35, cap −15 | −3/stale, cap −12 | 0 | 100 |
| **News Sentiment** | ❌ | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| **Liquidity** | ✅ | 100 | 0–100 | −15/pillar + −3/sub cap −30 | −4/−8 + −4/proxy-pillar | −0.5/pt >35, cap −15 | −3/stale, cap −12 | 0 | 100 |
| **Cross-Asset** | ✅ | 100 | 0–100 | −15/pillar + −3/sub cap −25 | ❌ None | −0.5/pt >40, cap −15 | Copper-only (−3 to −8) | 0 | 100 |
| **Regime** | Own formula | 0.85 max | 0–1 | N/A (coverage-based) | N/A | Block spread >15 (−0.30 cap) | N/A | 0.10 | 0.95 |
| **Framework** | Central tables | 1.0 | 0–1 | Via QUALITY table (−0.40 max) | Via QUALITY table | Via CONFLICT table (−0.30) | Via FRESHNESS table (−0.25) | 0.0 | 1.0 |

---

## 6. Findings

### HIGH Severity

#### [F-2D-HIGH-1] Engine Confidence Scale Lost at Regime Level

**Location**: `regime_service.py` L355–368 (`_extract_engine_confidence`)
**Issue**: The regime service clamps extracted engine confidence to [0.0, 1.0] via `max(0.0, min(1.0, float(conf)))`. Engine confidence is on a 0–100 scale. Any engine confidence ≥ 1 (which is ALL non-trivial values — e.g., 55, 70, 85) gets clamped to 1.0.
**Impact**: The regime service treats ALL engines as having maximum confidence (1.0) regardless of their actual confidence score. An engine with confidence=55 (many penalties fired) and an engine with confidence=95 (near-perfect data) both appear as 1.0 to the regime service. Block-level confidence averages are therefore always ~1.0 when engines are present.
**Root Cause**: Scale mismatch — engines use 0–100, but `_extract_engine_confidence` assumes 0–1.
**Recommendation**: Add `conf / 100.0` conversion when `conf > 1.0`, or standardize all engines to 0–1 output.

#### [F-2D-HIGH-2] News Sentiment Engine Has No Confidence Metric

**Location**: `news_sentiment_engine.py`
**Issue**: The news sentiment engine is the ONLY engine without a `_compute_confidence()` function. It outputs `signal_quality.strength` ("high"/"medium"/"low") instead, but this is not compatible with the `(confidence, penalties)` tuple structure used by the other 5 engines.
**Impact**: (1) The regime service's tactical block extracts `None` for news_sentiment confidence, excluding it from the confidence average. (2) Downstream consumers of MI data who expect a confidence field get inconsistent output shapes. (3) No way to assess news sentiment data quality numerically.
**Recommendation**: Add a `_compute_confidence()` function to the news sentiment engine that accounts for headline count, source diversity, and data freshness.

#### [F-2D-HIGH-3] Confidence Framework Is Unused by All Engines

**Location**: `confidence_framework.py`
**Issue**: A centralized confidence framework exists with carefully designed penalty tables (QUALITY, FRESHNESS, CONFLICT, COVERAGE), but **zero** MI engines import or use it. All 5 engines with confidence compute it inline with their own ad-hoc penalty schedules. The framework is only used by `market_composite.py`, `decision_prompt_payload.py`, and `decision_response_contract.py`.
**Impact**: (1) Penalty amounts and thresholds are inconsistent across engines (see F-2D-MED-1). (2) The framework's scale (0–1) doesn't match the engines' scale (0–100). (3) Changes to confidence policy require editing 5 separate engine files instead of one central location.
**Recommendation**: Either adopt the framework in engines (with scale conversion) or deprecate it. The current state has two parallel confidence systems that never intersect.

### MEDIUM Severity

#### [F-2D-MED-1] Inconsistent Penalty Thresholds Across Engines

**Issue**: Engines that share the same penalty concept use different thresholds and caps:

| Penalty Dimension | Volatility | Flows | Liquidity | Cross-Asset |
|---|---|---|---|---|
| Missing submetric penalty | −5 each | −3 each | −3 each | −3 each |
| Missing submetric cap | −40 | −30 | −30 | −25 |
| Disagreement threshold | >30 pts | >35 pts | >35 pts | >40 pts |

The volatility engine penalizes missing submetrics more heavily (−5 vs −3) and has a lower disagreement threshold (30 vs 35–40). No documented rationale for these differences.

#### [F-2D-MED-2] Confidence Never Weights Scores — Purely Informational

**Issue**: Across ALL engines and the regime service, confidence is computed but NEVER used to weight, gate, or modify scores or labels (except the regime's confidence < 0.4 → NEUTRAL override). A composite score of 75 with confidence=30 is presented identically to a score of 75 with confidence=95.
**Impact**: Low-quality scores carry the same weight as high-quality scores in all downstream computations. The confidence value exists in the output but nothing acts on it except a UI warning note.
**Cross-reference**: Also identified in audit_2b.

#### [F-2D-MED-3] Volatility Engine Cannot Detect Proxy or Stale Data

**Issue**: The volatility engine's confidence function checks ONLY missing submetrics and pillar disagreement. It has NO penalty for proxy reliance or temporal staleness. Yet from audit_1a, VIX data can be proxied when Tradier is unavailable, and data freshness is not tracked.
**Impact**: Volatility engine confidence may report 90+ when all data is proxy-derived or hours stale. Other engines (Flows, Liquidity) would penalize this scenario.

#### [F-2D-MED-4] Breadth Engine Confidence Opacity

**Issue**: The breadth engine's `_compute_confidence()` delegates to `breadth_diagnostics.compute_quality_scores()`, which contains 5+ penalty categories including survivorship bias and universe coverage — concepts not present in any other engine. The penalty computations depend on internal functions (`analyze_disagreement()`, `assess_survivorship_risk()`) that produce variable amounts.
**Impact**: Breadth confidence is not directly comparable to other engines because it uses different penalty categories and variable amounts. It also returns a richer structure (`data_quality_score`, `historical_validity_score`) that other engines don't produce.

#### [F-2D-MED-5] Regime Confidence Floor (0.10) May Be Too Generous

**Issue**: Regime confidence is bounded to [0.10, 0.95]. The minimum of 0.10 means the regime ALWAYS reports at least 10% confidence, even when only 1 of 3 blocks has data (base=0.283) with maximum conflict penalty (−0.30). The 0.10 floor prevents the confidence < 0.4 NEUTRAL override from ever being mathematically impossible.
**Impact**: Minor — the floor exists to prevent exactly-zero confidence states, which is reasonable. But a regime with 1 block and high conflict should arguably report lower than 10%.

### LOW Severity

#### [F-2D-LOW-1] Cross-Asset Engine Has Higher Disagreement Threshold

**Issue**: Cross-Asset triggers disagreement penalty at spread > 40, while Flows and Liquidity trigger at > 35, and Volatility at > 30. The higher threshold means Cross-Asset tolerates more inter-pillar spread before penalizing confidence.
**Impact**: Minimal — may be intentional if cross-asset pillars are expected to have wider natural spread due to diverse asset classes.

#### [F-2D-LOW-2] Copper Staleness Is the Only Temporal Check in Cross-Asset

**Issue**: Cross-Asset Macro engine only checks staleness for FRED copper data. Other FRED data sources (10Y yield, DXY, gold, oil) could also be stale but are not checked.
**Impact**: Low — other FRED data is typically daily resolution vs copper's monthly resolution.

#### [F-2D-LOW-3] Fixed Confidence Values for Direct Data in Regime

**Issue**: The regime service assigns fixed confidence values to direct data computations: FRED 10Y → 0.9, VIX → 0.9, trend → 0.9, small-cap → 0.85, rate_pressure → 0.85. These never change regardless of actual data freshness.
**Impact**: Low — direct FRED/market data is typically reliable, so fixed high confidence is reasonable. But it masks potential staleness issues.

---

## 7. Cross-References

| Finding | Related Audit | Notes |
|---------|---------------|-------|
| F-2D-HIGH-1 (scale mismatch) | 2B (confidence not weighting scores) | 2B noted confidence is informational; 2D reveals WHY — the scale conversion is broken |
| F-2D-HIGH-2 (no sentiment confidence) | 2C (sentiment coarse labels) | 2C noted 4-tier coarse labeling; 2D reveals no confidence dimension at all |
| F-2D-MED-2 (confidence purely informational) | 2B F-HIGH-3 | Same fundamental finding, confirmed from 2D's perspective |
| F-2D-MED-3 (vol engine no proxy/stale check) | 1A (proxy handling) | 1A documented how data can be proxied; 2D shows vol engine is blind to this |
| F-2D-HIGH-3 (framework unused) | N/A | New finding unique to 2D |
