# Audit 2E — Stock Scanner Scoring Verification

**Scope**: All four stock scanners — scoring formulas, component weights, score distributions, component independence, cliff thresholds, filter-to-score gap, cross-scanner comparability, and preset impact.

**Date**: 2025-07-18
**Auditor**: Copilot (automated deep-read)

---

## Source Files

| Scanner | Service File | Scoring Lines | Filter Lines | Config Lines |
|---------|-------------|---------------|--------------|--------------|
| Pullback Swing | `app/services/pullback_swing_service.py` | L433–560 | L250–275 (inline) | L106–113 |
| Momentum Breakout | `app/services/momentum_breakout_service.py` | L636–795 | L573–635 | L140–156 |
| Mean Reversion | `app/services/mean_reversion_service.py` | L642–815 | L564–641 | L136–145 |
| Volatility Expansion | `app/services/volatility_expansion_service.py` | L811–975 | L713–809 | L147–162 |

Runner: `app/workflows/stock_opportunity_runner.py`
- `MIN_SETUP_QUALITY = 30.0` (L93) — post-normalization filter
- `setup_quality` = `composite_score` (1:1, no transform) via `scanner_candidate_contract.py` L341

---

## 1. Component Score Ranges

### Pullback Swing (Max 100)

| Component | Range | Sub-elements | Key Thresholds |
|-----------|-------|-------------|----------------|
| trend | 0–35 | trend_state (0/12/20), MA alignment (0/4/5), slope_50 (0/3/6) | strong_uptrend=20, sma20>sma50=+5, slope>1%=+6 |
| pullback | 0–35 | pullback_zone (0/5/6/12/18), dist_sma20 (0/4/5/10), sma50 penalty (−8) | sweet spot −1% to −6%=18, near SMA20=+10 |
| reset | 0–20 | RSI zone (2/5/8/10/14), RSI recovering (+4), RSI collapsing (−3) | RSI 40–60=14, recovering=+4 |
| liquidity | 0–10 | dollar_vol (0/2/4/5/7), vol_ratio sanity (0/1/3) | $500M+=7, normal vol range=+3 |

**Theoretical max**: 35+35+20+10 = **100** ✓
**No final cap** — composite is sum of components (each individually capped).

### Momentum Breakout (Max 100)

| Component | Range | Sub-elements | Key Thresholds |
|-----------|-------|-------------|----------------|
| breakout | 0–35 | breakout_state (0/6/12/18), proximity_55 (0/3/5/6–10), ATR quality (0/1/3/5) | confirmed=18, through high=6+4=10 |
| volume | 0–25 | vol_spike (0/2/5/9/12/15), dollar_vol (0/2/4/6/8/10) | ≥3x spike=15, $500M+=10 |
| trend | 0–20 | trend_state (0/8/12), MA alignment (0/2/3), slope (0/1/3) | strong_uptrend=12, sma20>sma50=+3 |
| base_quality | 0–20 | compression (1/4/7/10), range_20 (0/2/4/6), gap penalty (−3/−1) | comp≥0.6=10, range≤6%=+6 |

**Theoretical max**: 35+25+20+20 = **100** ✓
**No final cap** — sum of individually capped components.

### Mean Reversion (Max 100)

| Component | Range | Sub-elements | Key Thresholds |
|-----------|-------|-------------|----------------|
| oversold | 0–40 | RSI14 zone (0/6/12/18/22), zscore (0/3/4/7/10), RSI2 (0/2/4/6) | RSI 25–35=22, z −2.5 to −1.5=+10, RSI2≤5=+6 |
| stabilization | 0–25 | return_1d (0/3/6/8), return_2d (0/2/4), bounce_hint (+4), vol on green (0/3/4/6) | strong green +2%=8, vol≥2x on green=+6 |
| room | 0–20 | dist_sma20 (0/3/7/10/12), sma50 damage (−3/−6), drawdown_20 (0/3/5) | ≥8% below SMA20=12, dd −12% to −4%=+5 |
| liquidity | 0–15 | dollar_vol (0/2/4/5/7/8), ATR% (0/1/3/4/5) | $500M+=8, ATR≤3%=+5 |

**Theoretical max**: 40+25+20+15 = **100** ✓
**No final cap** — sum of individually capped components.

### Volatility Expansion (Max 100, with final cap)

| Component | Range | Sub-elements | Key Thresholds |
|-----------|-------|-------------|----------------|
| expansion | 0–40 | best_ratio tiers (0/10/15/20/25/30), multi-signal bonus (0/6/10) | ≥2.0=30, 3 signals=+10 |
| compression | 0–25 | BB_pctile (0/3/7/11/14), prior_range (0/3/5/7), prior_ATR (0/2/3/4) | BB≤15=14, range≤8%=+7, ATR≤2.5%=+4 |
| confirmation | 0–20 | vol_spike (0/2/5/7/8), direction (0/2/3/4), bullish_bias (+3) | spike≥2.5=8, above SMA20=+4 |
| risk | 0–15 | ATR% (0/1/3/4/5), dollar_vol (0/1/3/4/5/6), gap (−2/0/+2) | ATR≤3%=+5, $500M+=6, small gap=+2 |

**Theoretical max**: 40+25+20+15 = **100**
**Has `min(composite, 100.0)` final cap** — the only scanner with an explicit top-level clamp. Possible because individual components sum to exactly 100, but sub-elements within components can theoretically exceed the cap before `min()` is applied.

---

## 2. Score Distribution Simulation

For each scanner, three analytical scenarios using the exact thresholds from the code:

### Pullback Swing

| Scenario | trend | pullback | reset | liquidity | **Composite** |
|----------|-------|----------|-------|-----------|---------------|
| **Textbook** (strong_uptrend, −3% pullback, RSI 50, $1B vol) | 20+5+4+6=**35** | 18+10=**28** | 14+4=**18** | 7+3=**10** | **91** |
| **Marginal** (uptrend, −8% pullback, RSI 62, $80M vol) | 12+5+4+3=**24** | 12+0=**12** | 8+0=**8** | 4+3=**7** | **51** |
| **Poor (passed filters)** (not_uptrend, −2% pullback, RSI 72, $20M vol) | 0+0+0+0=**0** | 18+4=**22** | 2+0=**2** | 2+1=**3** | **27** |

**Spread**: 91 – 27 = **64 points**. Good discrimination.
**Note**: Poor scenario (27) would be rejected by `MIN_SETUP_QUALITY=30.0` in the runner.

### Momentum Breakout

| Scenario | breakout | volume | trend | base_quality | **Composite** |
|----------|----------|--------|-------|-------------|---------------|
| **Textbook** (confirmed, 3x vol, strong_uptrend, tight base) | 18+10+5=**33** | 15+10=**25** | 12+3+2+3=**20** | 10+6−0=**16** | **94** |
| **Marginal** (attempt, 1.5x vol, uptrend, ok base) | 12+5+3=**20** | 9+6=**15** | 8+3+2+1=**14** | 7+4=**11** | **60** |
| **Poor (passed filters)** (near, 1.2x vol, uptrend, wide base) | 6+3+3=**12** | 5+4=**9** | 8+3+2+0=**13** | 4+2=**6** | **40** |

**Spread**: 94 – 40 = **54 points**. Good discrimination.

### Mean Reversion

| Scenario | oversold | stabilization | room | liquidity | **Composite** |
|----------|----------|--------------|------|-----------|---------------|
| **Textbook** (RSI 30, z=−2.0, RSI2=3, +2% bounce, $1B, ATR 2.5%) | 22+10+6=**38** | 8+4+4+6=**22** | 12+0+5=**17** | 8+5=**13** | **90** |
| **Marginal** (RSI 38, z=−1.3, +0.3% day, $100M, ATR 5%) | 10+4+0=**14** | 3+0+0+0=**3** | 7+0+3=**10** | 5+4=**9** | **36** |
| **Poor (passed filters)** (RSI 34, z=−1.6, flat day, $20M, ATR 8%) | 10+7+4=**21** | 0+0+0+0=**0** | 3+0+0=**3** | 2+3=**5** | **29** |

**Spread**: 90 – 29 = **61 points**. Good discrimination.
**Note**: Poor scenario barely passes filters (RSI 34 < 35 ✓, z=−1.6 < −1.5 ✓ for oversold; flat day with 0 return meets `return_1d >= 0` ✓ for stabilization). Score of 29 would be rejected by `MIN_SETUP_QUALITY=30.0`.

### Volatility Expansion

| Scenario | expansion | compression | confirmation | risk | **Composite** |
|----------|-----------|-------------|-------------|------|---------------|
| **Textbook** (ATR 2.5x, RV 2x, range 2x, BB pctile 10, +3x vol, bullish) | 30+10=**40** | 14+7+4=**25** | 8+4+3+3=**18** | 5+6+2=**13** | **96** → **capped 100** |
| **Marginal** (ATR 1.5x, BB pctile 30, 1.3x vol, above SMA20) | 20+0=**20** | 7+5+3=**15** | 5+4+0+0=**9** | 4+5+0=**9** | **53** |
| **Poor (passed filters)** (range 1.4x only, BB 34 rising, 1.0x vol) | 10+0=**10** | 7+3+2=**12** | 2+0+2+0=**4** | 3+3+0=**6** | **32** |

**Spread**: 96 – 32 = **64 points**. Good discrimination.

### Distribution Summary

| Scanner | Textbook | Marginal | Poor | Spread | MIN_SETUP_QUALITY passes all? |
|---------|----------|----------|------|--------|-------------------------------|
| Pullback Swing | 91 | 51 | 27 | 64 | No — poor=27 < 30 |
| Momentum Breakout | 94 | 60 | 40 | 54 | Yes — poor=40 ≥ 30 |
| Mean Reversion | 90 | 36 | 29 | 61 | No — poor=29 < 30 |
| Volatility Expansion | 96 | 53 | 32 | 64 | Yes — poor=32 ≥ 30 |

**Assessment**: All four scanners show good score discrimination (54–64 point spreads). Textbook setups consistently score 90+, marginals land in 36–60 range, and poor setups land in 27–40.

---

## 3. Component Independence & Overlap

### Shared Metric Inventory

| Metric | Pullback | Momentum | Mean Rev | Vol Exp | Components Used In (per scanner) |
|--------|----------|----------|----------|---------|----------------------------------|
| sma20 | ✓ | ✓ | — | — | PB: trend+pullback; MB: trend+base |
| sma50 | ✓ | ✓ | — | — | PB: trend+pullback; MB: trend |
| sma200 | ✓ | ✓ | — | — | PB: trend+pullback; MB: trend |
| vol_spike_ratio | — | ✓ | ✓ | ✓ | MB: volume+base; MR: stab+liq; VE: confirm+risk |
| atr_pct | — | ✓ | ✓ | ✓ | MB: breakout; MR: liq+filter; VE: risk+filter |
| avg_dollar_vol_20 | ✓ | ✓ | ✓ | ✓ | All: liquidity/risk + filter |
| dist_sma20 | ✓ | — | ✓ | — | PB: pullback; MR: room |
| dist_sma50 | ✓ | — | ✓ | — | PB: pullback; MR: room |

### Cross-Component Overlap Within Each Scanner

**Pullback Swing** — **HIGH overlap**
- `sma20`, `sma50`, `sma200` appear in BOTH `trend` AND `pullback` components (6 of 35+35=70 possible points)
- Effective: strong_uptrend + SMA alignment bonuses in trend component use the same SMAs that anchor pullback distance scoring
- **Impact**: Uptrend = higher trend score AND higher pullback score simultaneously (positive correlation ~0.6–0.8 estimated). Components are NOT independent.

**Momentum Breakout** — **MODERATE overlap**
- `vol_spike_ratio` contributes to BOTH volume (5–15 pts) AND base_quality (penalty logic via gap_pct, not direct)
- `sma20` appears in trend (MA alignment) AND breakout (implicit via extension check in filter, not in scoring)
- Components are more independent than pullback_swing

**Mean Reversion** — **LOW overlap**
- `vol_spike_ratio` in stabilization (up to +6) AND liquidity (not directly, only ATR%). Actually minimal direct overlap.
- `atr_pct` in liquidity score AND filter gate — mild redundancy but filter is pass/fail, scoring is graduated
- Components are relatively independent

**Volatility Expansion** — **MODERATE overlap**
- `vol_spike_ratio` in confirmation (up to +8 pts) AND risk (not direct; risk uses atr_pct and avg_dollar_vol)
- `atr_pct` in risk score (up to +5) AND filter gate (pass/fail at 12%)
- `avg_dollar_vol_20` in risk score (up to +6) AND filter gate (pass/fail at $20M)
- The filter-to-score redundancy means some metrics are counted twice

### Findings

**F-2E-MED-1 — Pullback Swing SMA triple-count**
- `sma20`, `sma50`, `sma200` score in both trend (up to 9 pts for alignment) AND anchor pullback_from_20d_high + distance_to_sma20 calculations
- A stock in strong uptrend with aligned SMAs is virtually guaranteed high pullback scores too, creating unintended correlation
- **Location**: `pullback_swing_service.py` L451–495

**F-2E-LOW-1 — Filter-then-score redundancy on atr_pct and avg_dollar_vol**
- Mean Reversion and Volatility Expansion both filter on `atr_pct` and `avg_dollar_vol` AND score them in the liquidity/risk component
- Passing filters guarantees some minimum score contribution from these metrics
- Not necessarily wrong (filter is a hard floor, score is graduated), but the liquidity/risk score loses ~30% of its discrimination range because the lowest tiers can never be reached

---

## 4. Cliff Thresholds (Discontinuities)

A "cliff" is a threshold where crossing by a tiny amount causes a large score jump.

### Pullback Swing

| Metric | Threshold | Points below → above | Cliff Size |
|--------|-----------|---------------------|------------|
| trend_state | uptrend → strong_uptrend | 12 → 20 | **8 pts** |
| pullback_from_20d_high | −6.01% → −6.00% | 12 → 18 | **6 pts** |
| RSI14 | 39.9 → 40.0 | 10 → 14 | **4 pts** |
| distance_to_sma20 | −1.51% → −1.50% | 5 → 10 | **5 pts** |
| avg_dollar_vol_20 | $99.9M → $100M | 4 → 5 | 1 pt |

### Momentum Breakout

| Metric | Threshold | Points below → above | Cliff Size |
|--------|-----------|---------------------|------------|
| breakout_state | attempt → confirmed | 12 → 18 | **6 pts** |
| vol_spike_ratio | 1.99x → 2.0x | 9 → 12 | **3 pts** |
| vol_spike_ratio | 2.99x → 3.0x | 12 → 15 | **3 pts** |
| compression_score | 0.39 → 0.40 | 4 → 7 | **3 pts** |
| avg_dollar_vol_20 | $199M → $200M | 6 → 8 | **2 pts** |

### Mean Reversion

| Metric | Threshold | Points below → above | Cliff Size |
|--------|-----------|---------------------|------------|
| RSI14 | 24.9 → 25.0 | 18 → 22 | **4 pts** |
| RSI14 | 35.0 → 35.1 | 22 → 10 | **12 pts** ⚠️ |
| zscore_20 | −1.51 → −1.50 | 4 → 10 | **6 pts** |
| dist_sma20 | −5.01% → −5.00% | 10 → 7 | **3 pts** |
| return_1d | 0.019 → 0.020 | 6 → 8 | **2 pts** |

### Volatility Expansion

| Metric | Threshold | Points below → above | Cliff Size |
|--------|-----------|---------------------|------------|
| best_ratio | 1.69 → 1.70 | 20 → 25 | **5 pts** |
| best_ratio | 1.99 → 2.00 | 25 → 30 | **5 pts** |
| bb_width_percentile | 15.1 → 15.0 | 11 → 14 | **3 pts** |
| bb_width_percentile | 25.1 → 25.0 | 7 → 11 | **4 pts** |
| vol_spike_ratio | 1.79 → 1.80 | 5 → 7 | **2 pts** |

### Findings

**F-2E-HIGH-1 — Mean Reversion RSI 35 cliff: 12-point discontinuity**
- RSI14 at 35.0 scores 22 pts (sweet spot), RSI14 at 35.1 scores 10 pts (mildly oversold) — a **12-point** swing from a 0.1 RSI change
- This is the largest cliff across all four scanners
- At the composite level, this alone can move a candidate from "moderate" to "speculative" quality
- **Location**: `mean_reversion_service.py` L664–670

**F-2E-MED-2 — Pullback Swing pullback zone cliff at −6%**
- `pullback_from_20d_high` at −6.01% scores 12 (deeper but ok), at −6.00% scores 18 (sweet spot) — **6-point** swing
- **Location**: `pullback_swing_service.py` L484–492

**F-2E-MED-3 — Mean Reversion zscore cliff at −1.5**
- zscore at −1.51 scores 10 (good stretch), at −1.49 scores 4 (mild stretch) — **6-point** swing
- Doubles as both scoring cliff AND filter gate boundary (filter requires zscore ≤ −1.5 as one of 4 OR conditions)
- **Location**: `mean_reversion_service.py` L676–682

**F-2E-LOW-2 — Expansion ratio tier cliffs at 1.7 and 2.0**
- 5-point jumps at each ratio tier boundary. Less concerning because it's a single metric contributing to a 40-pt component
- **Location**: `volatility_expansion_service.py` L842–852

---

## 5. Filter-to-Score Gap Analysis

### Filter Architecture Comparison

| Scanner | Filter Type | # Gates | Strategy-Specific Thresholds |
|---------|------------|---------|------------------------------|
| Pullback Swing | Inline hard-checks | 3 basic | None — only price, history, $vol |
| Momentum Breakout | `_apply_filters()` | 6 sequential | proximity_55, trend, RSI range, compression, vol spike, extension |
| Mean Reversion | `_apply_filters()` | 4 (2 OR-gates + 2 hard) | oversold OR-gate, stabilization OR-gate, ATR%, dist_sma50 |
| Volatility Expansion | `_apply_filters()` | 4 (3 OR-gates + 1 hard) | expansion OR-gate, compression OR-gate, long bias OR-gate, ATR% |

### Gap Analysis

**Pullback Swing** — **WIDE GAP** ⚠️
- Filters only check: price ≥ $5, history ≥ 220 bars, avg dollar vol ≥ $15M
- No trend filter, no pullback zone filter, no RSI filter
- A stock with NO uptrend, NO pullback, RSI at 80 passes filters and enters scoring
- The scoring function handles differentiation, but there's no early rejection of clearly unsuitable candidates
- This means pullback_swing scans AND scores every symbol that meets basic data requirements — **processing waste**

**Momentum Breakout** — **TIGHT alignment** ✓
- 6 sequential filters cover the same dimensions as scoring: proximity (breakout), trend (SMA), RSI (reset), compression (base), volume, extension
- Filters prevent unsuitable candidates from entering scoring
- Well-designed filter → score pipeline

**Mean Reversion** — **GOOD alignment with caveat**
- OR-gate filters (oversold, stabilization) align with the top 2 scoring components
- **Caveat**: Oversold filter threshold (RSI ≤ 35) differs from scoring sweet spot (RSI 25–35). RSI 34 passes filter but scores 10 pts; RSI 30 also passes and scores 22 pts — filter is less selective than scoring
- Stabilization filter accepts `return_1d ≥ 0` (any flat-to-green) but scoring only gives 3 pts for barely positive. Filter is very permissive.

**Volatility Expansion** — **GOOD alignment** ✓
- 3 OR-gate filters map 1:1 to the first 3 scoring components (expansion, compression, long bias)
- Risk/ATR% check in filter corresponds to risk scoring component
- Strong filter → score alignment

### Findings

**F-2E-HIGH-2 — Pullback Swing has no strategy-specific filters**
- Only 3 basic hard checks (price, history, volume) — no checks for trend, pullback presence, or RSI
- Every symbol in the universe that has enough price history gets scored
- Compare: Momentum Breakout has 6 filters, Mean Reversion has 4, Volatility Expansion has 4
- **Risk**: Processing time waste and potentially returning low-quality scored candidates with composite < 30 that get rejected later by `MIN_SETUP_QUALITY`
- **Location**: `pullback_swing_service.py` L250–275

---

## 6. Cross-Scanner Score Comparability

### Component Weight Distribution

| Scanner | Primary Component | Weight | Secondary | Weight | Pattern |
|---------|-------------------|--------|-----------|--------|---------|
| Pullback Swing | trend | 35% | pullback | 35% | Even split, narrow liquidiy |
| Momentum Breakout | breakout | 35% | volume | 25% | Lead signal, support |
| Mean Reversion | oversold | 40% | stabilization | 25% | Heavy lead signal |
| Volatility Expansion | expansion | 40% | compression | 25% | Heavy lead signal |

### Scoring Philosophy Alignment

All four scanners share:
- 0–100 scale ✓
- Additive composition (no multiplicative interactions) ✓
- Each component independently capped ✓
- `setup_quality` = `composite_score` (no transform) ✓
- Same `MIN_SETUP_QUALITY = 30.0` threshold applied to all ✓

### Score Comparability Assessment

**A score of 70 across scanners**:
- Pullback Swing 70: strong trend (30+), decent pullback (20+), moderate reset + liquidity
- Momentum Breakout 70: confirmed breakout (25+), some volume (15+), trend (15+), ok base (15)
- Mean Reversion 70: oversold with rebound (30+), stabilization with volume (15+), room (12+), liquid (13)
- Volatility Expansion 70: solid expansion (25+), compression (15+), confirmed (15+), ok risk (15)

**Verdict**: Scores are roughly comparable in meaning (70 ≈ "solid setup" across all scanners). The equal scaling and additive structure help. However:

**F-2E-MED-4 — Liquidity component weight varies 3:1 across scanners**
- Pullback Swing: 10 pts (10% of total)
- Mean Reversion: 15 pts (15% of total)
- Momentum Breakout: 10 pts in volume component + dollar_vol overlap
- Volatility Expansion: up to 6 pts in risk component (6% explicit)
- A highly liquid stock gets a bigger score boost in Mean Reversion vs Volatility Expansion
- Not critical but affects relative ranking when comparing across scanners

---

## 7. Preset Impact on Scoring

### Current State

**Presets are NOT implemented.** All four scanners use a single hardcoded `_BALANCED_CONFIG` dict.

| Scanner | Config Constant | Evidence |
|---------|----------------|----------|
| Pullback Swing | `_BALANCED_CONFIG` (L106) | TODO comment: "future phase" |
| Momentum Breakout | `_BALANCED_CONFIG` (L140) | No preset infrastructure |
| Mean Reversion | `_BALANCED_CONFIG` (L136) | No preset infrastructure |
| Volatility Expansion | `_BALANCED_CONFIG` (L147) | No preset infrastructure |

The `snapshot_manifest.py` has `preset_name: str = "balanced"` hardcoded at L48.

### Impact

- **Scoring** is unaffected by presets — scoring functions (`_score()`) do not reference the config dict at all. They use hardcoded thresholds.
- **Filters** would be the preset knob — filter functions use `cfg[...]` values, so a Strict preset could tighten thresholds
- Since only Balanced exists, there is no ability to verify "Strict ≠ Balanced ≠ Wide" per docs/standards/presets.md

### Findings

**F-2E-HIGH-3 — Scoring functions are entirely hardcoded; config has zero effect on scoring**
- All four `_score()` methods use inline numeric constants — they do not reference the `_BALANCED_CONFIG` dict
- Even if presets are implemented for filters, the scoring thresholds would remain frozen
- This means preset changes would only affect which candidates enter scoring, not how they are scored
- **Location**: All four `_score()` functions

**F-2E-MED-5 — No Strict/Wide presets exist to comply with presets.md**
- `docs/standards/presets.md` requires Strict / Balanced / Wide presets that resolve to meaningfully different thresholds
- Stock scanners have only Balanced
- Options scanners (in `scanner_v2/`) may have presets, but stock scanners do not

---

## Finding Severity Summary

| ID | Severity | Scanner(s) | Finding |
|----|----------|------------|---------|
| F-2E-HIGH-1 | HIGH | Mean Reversion | RSI 35.0/35.1 cliff: 12-point discontinuity in oversold scoring |
| F-2E-HIGH-2 | HIGH | Pullback Swing | No strategy-specific filters — every symbol with basic data gets scored |
| F-2E-HIGH-3 | HIGH | All | `_score()` functions use hardcoded thresholds; config/presets cannot affect scoring |
| F-2E-MED-1 | MEDIUM | Pullback Swing | SMA triple-count across trend + pullback components |
| F-2E-MED-2 | MEDIUM | Pullback Swing | Pullback zone cliff at −6% boundary (6-pt swing) |
| F-2E-MED-3 | MEDIUM | Mean Reversion | Zscore cliff at −1.5 (6-pt swing + filter gate boundary) |
| F-2E-MED-4 | MEDIUM | All | Liquidity component weight varies 3:1 across scanners |
| F-2E-MED-5 | MEDIUM | All | No Strict/Wide presets exist for stock scanners |
| F-2E-LOW-1 | LOW | Mean Rev, Vol Exp | Filter-then-score redundancy on atr_pct / avg_dollar_vol |
| F-2E-LOW-2 | LOW | Vol Expansion | Expansion ratio tier cliffs at 1.7 and 2.0 (5-pt each) |

**Total**: 3 HIGH, 5 MEDIUM, 2 LOW

---

## Cross-Reference

| Finding | Related Audit |
|---------|--------------|
| F-2E-HIGH-3 (hardcoded scoring) | Relates to presets.md standard — no preset can change scoring behavior |
| F-2E-HIGH-2 (no PB filters) | 1F scanner data deps — pullback_swing has simplest data path |
| F-2E-MED-4 (liquidity weight variation) | 2B composite aggregation — similar cross-engine weight inconsistency |
| F-2E-MED-1 (SMA triple-count) | 2A pillar scoring — metric reuse across pillars pattern |

---

## Appendix: Scoring Code Locations (Quick Reference)

```
pullback_swing_service.py:
  _BALANCED_CONFIG         L106–113
  _scan_symbol (inline filters)  L250–275
  _score()                 L433–555
  
momentum_breakout_service.py:
  _BALANCED_CONFIG         L140–156
  _apply_filters()         L573–635
  _score()                 L636–795
  
mean_reversion_service.py:
  _BALANCED_CONFIG         L136–145
  _apply_filters()         L564–641
  _score()                 L642–815
  
volatility_expansion_service.py:
  _BALANCED_CONFIG         L147–162
  _apply_filters()         L713–809
  _score()                 L811–975

stock_opportunity_runner.py:
  MIN_SETUP_QUALITY        L93
  _stage_enrich_filter_rank_select  L935–1020
  setup_quality = composite_score via scanner_candidate_contract.py L341
```
