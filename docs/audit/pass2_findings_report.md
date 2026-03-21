# BenTrade Foundation Audit — Pass 2 Findings Report
## Computation Layer: Consolidated Analysis

**Date**: 2026-03-20
**Auditor**: Claude (synthesis of 6 Copilot-generated audit documents)
**Scope**: All scoring formulas, composite aggregation, calibration, confidence, scanner scoring, and options math

---

## Executive Assessment

Your computation layer is **mathematically correct but architecturally disconnected from data quality**. The formulas themselves are sound — standard interpolation, proper None handling via re-weighted averages, correct options math for verticals and iron condors. But the scoring system has three structural problems that limit its usefulness: confidence scores don't actually influence anything, VIX bleeds through 5 of 6 engines creating correlated movements that look like independent agreement, and the ranking system for options uses raw EV instead of the risk-adjusted composite score you already built.

The good news: the infrastructure for better computation exists in your codebase already. You have a confidence framework that no engine uses, a ranking service with proper liquidity weighting that the V2 pipeline ignores, and safety gates in the Flows engine that other engines could adopt. The fixes are mostly about wiring existing components together, not building new things.

---

## Severity 1: Systemic Computation Issues

### C1. Confidence Is Purely Decorative

This is the single most important computation finding. Confidence scores are computed by 5 of 6 engines, stored in output, displayed on the dashboard — and used for absolutely nothing. A Flows engine scoring 65 with confidence 55 (degraded proxy data) gets exactly the same weight in the regime composite as a Breadth engine scoring 65 with confidence 90 (direct market data). The only exception is the regime service's confidence < 0.4 → NEUTRAL override, which is a coarse safety valve, not a weighting mechanism.

**Compounding problem**: The regime service's `_extract_engine_confidence()` clamps to [0.0, 1.0], but engines report confidence on a 0-100 scale. Any engine confidence ≥ 1 (which is ALL of them — typical values are 55-90) gets clamped to 1.0. The scale mismatch means the regime service sees every engine as maximum confidence. Even if confidence-weighted scoring were implemented at the regime level, the current extraction code would make all engines equal.

**What exists but isn't wired in**: The `confidence_framework.py` has proper penalty tables (QUALITY, FRESHNESS, CONFLICT, COVERAGE) on a 0-1 scale, but zero engines import or use it. There are two parallel confidence systems that never intersect.

### C2. VIX Cross-Engine Amplification

VIX appears as a scoring input in 5 of 6 engines with an aggregate effective weight of roughly 20% of the total MI composite. When VIX spikes, it simultaneously moves Volatility (primary home), Flows (all 12 proxy metrics are VIX functions), Liquidity (FCI proxy, VIX conditions), Cross-Asset (credit pillar, coherence), and Sentiment (macro stress adjustment). The 6-engine architecture was designed for signal independence, but VIX creates correlated movements that look like cross-engine agreement.

At the regime level, VIX feeds both the Structural block (via vol_structure scoring and within the Liquidity engine) and the Tactical block (via Volatility and Flows engines). A VIX spike of 10 points can move the regime score by 8-15 points through this double-block exposure.

### C3. All-None Default Inconsistency

Four engines default to 0.0 when all pillars are None (no data). Two engines (Sentiment and Liquidity) default to 50.0. The regime service blocks default to 50.0. This creates an asymmetry: if the Volatility engine has no data, it scores 0.0 ("Volatility Stress / Defensive") — a meaningful extreme label applied to a no-data situation. Meanwhile, Sentiment with no data scores 50.0 ("Neutral") — indistinguishable from genuinely neutral conditions.

The Sentiment engine is particularly problematic because every component individually defaults to 50 when data is missing. The engine literally cannot return None — it always produces a plausible-looking number, even with zero real data. This masks data absence from every downstream consumer.

---

## Severity 2: Scoring Formula Issues

### F1. Flows Engine: futures_net_long_pct Dominates 4/5 Pillars

This single VIX-derived proxy metric appears in Positioning Pressure, Crowding/Stretch, Squeeze/Unwind Risk, and Positioning Stability — 4 of 5 pillars. Its effective composite weight is approximately 19%, meaning nearly 1/5 of the entire Flows engine composite is driven by one number. Combined with the fact that this number is itself a VIX proxy (`max(10, min(90, 100 - vix * 2.2))`), the Flows engine has perhaps 0.5 degrees of actual freedom.

### F2. Flows Engine Score-Label Disconnect

The Flows engine's safety gates change the label but NOT the numeric score. A composite of 78 with crowding < 40 displays "Mixed but Tradable (Gated)" while outputting score=78. The Tactical regime block consumes the numeric score, not the label — the gate is invisible to the regime. Additionally, if the gating pillars return None (not scored), the gate check evaluates False and doesn't fire — missing data bypasses safety.

### F3. Bell Curve Discontinuities in Volatility Engine

VIX Rank 30D and VIX Percentile 1Y have 20-point score drops at their peak boundaries (value=50). The ascending branch ends at 95 but the descending branch starts at 75. A VIX Rank crossing from 49.99 to 50.01 sees a 95→75 score jump. Similarly, Vol Risk Premium has a 15-point discontinuity at 1.5. These are intended as bell curves (optimum in the middle) but the ascending and descending branches don't meet at the same peak value, creating non-smooth scoring surfaces.

### F4. Mean Reversion RSI 35 Cliff

The largest single cliff threshold across all scanners: RSI14 at 35.0 scores 22 points (sweet spot), RSI14 at 35.1 scores 10 points — a 12-point swing from a 0.1 RSI change. At the composite level, this alone can move a candidate between quality tiers.

### F5. Butterfly EV Overestimation

The butterfly POP formula measures P(stock finishes between outer strikes), but max profit only occurs at the exact center strike. The EV formula uses `POP × max_profit - (1-POP) × max_loss`, which assumes binary outcomes. For butterflies, this overestimates EV by approximately 40-50%. Critically, the sign can flip — a code-positive-EV butterfly may actually be EV-negative. This means the ranking system can promote butterfly trades that a correctly-computed ranking would reject.

### F6. V2 Ranking Ignores Existing Ranking Service

The V2 options workflow sorts by raw EV descending. Meanwhile, `ranking.py` already implements a proper composite rank score: `edge(0.30) + ror(0.22) + pop(0.20) + liquidity(0.18) + tqs(0.10)` with EV normalized by max_loss (capital efficiency) and a liquidity component. This ranking service is used by legacy strategy services but NOT by the V2 pipeline. Raw EV ranking creates systematic width bias (wider spreads rank higher) and ignores liquidity quality entirely.

---

## Severity 3: Calibration & Configuration Issues

### K1. Score Bunching (Effective Range 55-75)

Under normal market conditions, all 6 engines produce composites in the 55-75 range. The 0-100 scale is effectively a 55-75 scale in practice. This is expected behavior from weighted averaging across 4-5 pillars, but downstream consumers should understand that day-to-day variation lives in a 20-point band, not a 100-point range. The regime score bunches even more tightly due to 3-block averaging.

### K2. NEUTRAL Regime Is the Default State

The NEUTRAL regime label covers scores 40-64 (25 points) plus low-confidence risk-off overrides. RISK_ON requires ≥65 AND aligned blocks AND confidence ≥0.4. RISK_OFF requires <30 AND aligned. In practice, most real-world outputs will be NEUTRAL or RISK_ON_CAUTIOUS. The 5-tier label system is underutilized.

### K3. Scoring Thresholds Are Entirely Hardcoded

All four stock scanners use inline numeric constants in their `_score()` functions. The `_BALANCED_CONFIG` dict controls filter thresholds but NOT scoring thresholds. Presets cannot change how setups are scored, only which setups enter scoring. No Strict/Wide presets exist to comply with the documented preset standard.

### K4. Pullback Swing Has No Strategy-Specific Filters

The pullback swing scanner only has 3 basic hard checks (price, history, volume) — no checks for trend presence, pullback zone, or RSI. Every symbol with enough price history gets scored. Compare: Momentum Breakout has 6 filters, Mean Reversion has 4, Volatility Expansion has 4. This wastes processing and can return low-quality candidates that get rejected later by MIN_SETUP_QUALITY.

### K5. 10Y Yield Bell Curve Peak May Be Stale

The Cross-Asset engine peaks at 3.5% (score=90) for 10Y yields. In sustained 4.0-4.5% environments (which have been normal recently), the scoring treats this as sub-optimal (score 60-75). The Liquidity engine peaks at 3.2%. Both peaks may need periodic recalibration to reflect the current rate regime.

---

## What's Working Well

1. **Interpolation infrastructure**: The shared `_interpolate()` function is correct, well-clamped, handles degenerate ranges, and is identical across all 5 structural engines. No extrapolation possible, no overflow possible.

2. **None handling via `_weighted_avg`**: Skips None values, re-normalizes remaining weights, returns None when all inputs are None. This is correct and consistent (with the caveat about the 0.0 vs 50.0 fallback inconsistency).

3. **Options vertical/IC math**: Phase E formulas for vertical spreads and iron condors are correct. Net credit from bid-ask (not mid), width from strikes, POP from delta — all standard and verified.

4. **Calendar honest deferral**: Setting path-dependent fields to None instead of fabricating values is the right call. The explanatory notes are a good touch.

5. **Flows engine safety gates**: The concept of gating labels based on pillar-level thresholds is good design — it prevents composite averaging from hiding dangerous sub-signals. This pattern should be extended to other engines.

6. **Scanner score discrimination**: All four stock scanners show 54-64 point spreads between textbook and poor setups. Textbook scores consistently hit 90+. The scoring formulas differentiate well.

7. **Anti-anchoring exclusions**: Model analysis correctly strips composite scores and labels before the LLM sees data. Well-implemented across all engines.

---

## Recommended Fix Priority (Pass 2 Findings)

### Fix Now
- **Fix the engine confidence scale at regime extraction** (0-100 → 0-1 conversion)
- **Connect the V2 ranking to `ranking.py`** (or port its composite formula into the V2 sort)
- **Fix butterfly EV** by flagging or adjusting the binary-outcome assumption

### Fix Soon
- **Wire confidence into score weighting** at the regime block level
- **Fix the Flows engine gate to also adjust the numeric score** (not just the label)
- **Fix bell curve discontinuities** in the volatility engine (align ascending/descending peaks)
- **Smooth the Mean Reversion RSI 35 cliff** (use interpolation instead of hard threshold)
- **Add strategy-specific filters to pullback swing scanner**

### Fix Later
- **Unify all-None defaults** (choose 50.0 consistently, or emit None and let consumers decide)
- **Add safety gates to other engines** (breadth, volatility, cross-asset)
- **Implement presets for stock scanner scoring** (not just filters)
- **Consider VIX exposure caps** across the composite to limit correlated amplification
- **Recalibrate 10Y yield bell curve peaks** to current rate regime
