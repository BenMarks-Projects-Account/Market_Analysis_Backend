# Audit 3C — Filter Threshold Analysis

**Scope**: Comprehensive inventory of every filter threshold in both stock and options pipelines — numeric values that determine whether a candidate is kept or rejected. Cross-pipeline comparison of filtering depth and quality control.

**Date**: 2025-07-19
**Auditor**: Copilot (automated deep-read)

---

## PART 1: Stock Scanner Filters

### Universe Definition

| Property | Value | Location |
|----------|-------|----------|
| Universe name | `_BALANCED_UNIVERSE` | Duplicated in each scanner file |
| Symbol count | ~196 | Technology (~45), Healthcare (~15), Financials (~15), Consumer (~20), Industrials (~16), Energy (~13), Materials (~10), REITs (7), Utilities (5), Communication (7) |
| ETF exclusions | ~100+ ETFs blocked | `_ETF_EXCLUSIONS` frozenset per scanner file |

---

### Scanner 1: Mean Reversion (`mean_reversion_service.py`)

#### Config Thresholds (`_BALANCED_CONFIG`, L108)

| Threshold | Value | Configurable? | What Gets Rejected | Rationale |
|-----------|-------|---------------|-------------------|-----------|
| min_history_bars | 120 | No — hardcoded | < 120 bars of data | 120 trading days ≈ 6 months; mean reversion needs less history than SMA200 strategies |
| min_price | $5.00 | No — hardcoded | Penny stocks | Exchange listing standards, options availability |
| min_avg_dollar_vol | $15,000,000 | No — hardcoded | Illiquid names | 20D avg daily dollar volume; ensures fills |
| lookback_days | 300 | No — hardcoded | Insufficient data request | Calendar days → ~200 trading days |
| per_symbol_timeout | 12.0s | No — hardcoded | Slow/timeout | Async timeout per symbol |
| atr_pct_max | 0.10 (10%) | No — hardcoded | Too volatile: ATR > 10% of price | Avoid extremely wild names |
| dist_sma50_floor | -0.18 (-18%) | No — hardcoded | Structural breakdown: > 18% below SMA50 | Deep structural damage, not a bounce candidate |

#### Strategy-Specific Filter Gates (`_apply_filters`, L564)

| Gate | Condition | Must Meet | Rejection Code |
|------|-----------|-----------|----------------|
| **Oversold gate** | RSI14 ≤ 35, OR RSI2 ≤ 10, OR zscore_20 ≤ -1.5, OR dist_sma20 ≤ -5% | ONE of 4 | NOT_OVERSOLD |
| **Stabilization gate** | return_1d ≥ 0, OR return_2d ≥ 0.5%, OR bounce_hint=True | ONE of 3 | NO_STABILIZATION |
| **ATR% sanity** | atr_pct ≤ 0.10 | ALL | TOO_VOLATILE |
| **Structural damage** | dist_sma50 ≥ -0.18 | ALL | STRUCTURAL_DAMAGE |

**Filter order**: Sequential with early-return on first failure (oversold → stabilization → ATR → structural).

#### Scoring Components (0–100 max)

| Component | Max Points | Key Thresholds |
|-----------|-----------|----------------|
| oversold_score | 40 | RSI14: 25–35=22pt, 20–25=18pt, 35–40=10pt; Zscore bonus [-2.5 to -1.5]=+10pt |
| stabilization_score | 25 | return_1d ≥ 0=+3–8pt, return_2d ≥ 0.005=+2–4pt, bounce_hint=+4pt, vol_spike_on_green=+3–6pt |
| room_score | 20 | Distance below SMA20 (snapback potential) |
| liquidity_score | 15 | avg dollar volume + ATR% |

---

### Scanner 2: Momentum Breakout (`momentum_breakout_service.py`)

#### Config Thresholds (`_BALANCED_CONFIG`, L105)

| Threshold | Value | Configurable? | What Gets Rejected | Rationale |
|-----------|-------|---------------|-------------------|-----------|
| min_history_bars | 220 | No — hardcoded | < 220 bars | Need SMA200 + lookback stability |
| min_price | $7.00 | No — hardcoded | Penny stocks | Higher floor — breakouts need established names |
| min_avg_dollar_vol | $20,000,000 | No — hardcoded | Illiquid | Higher bar — needs volume confirmation |
| lookback_days | 400 | No — hardcoded | Insufficient data | ~280 trading days |
| per_symbol_timeout | 12.0s | No — hardcoded | Slow/timeout | |
| proximity_55d_pct | 0.03 (3%) | No — hardcoded | Too far below 55D high | Must be within 3% of 55D high to qualify |
| breakout_min_pct | 0.003 (0.3%) | No — hardcoded | No breakout signal | 0.3% through high = breakout |
| breakout_max_pct | 0.03 (3%) | No — hardcoded | Already extended | >3% through still considered if vol OK |
| trend_required | True | No — hardcoded | Failed trend | SMA50 > SMA200 mandatory |
| rsi_min | 55 | No — hardcoded | RSI too low (oversold) | Not momentum-ready |
| rsi_max | 78 | No — hardcoded | RSI too high (blow-off) | Avoid chasing extremes |
| vol_spike_min | 1.2x | No — hardcoded | Weak volume | today_vol / avg20 ≥ 1.2x |
| extension_max_pct | 0.08 (8%) | No — hardcoded | Too extended above SMA20 | Avoid chasing too far |
| compression_max | 0.15 (15%) | No — hardcoded | Wide range (not tight base) | 20D range / price ≤ 15% |

#### Strategy-Specific Filter Gates (`_apply_filters`, L573)

| Gate | Condition | Must Meet | Rejection Code |
|------|-----------|-----------|----------------|
| **Proximity** | Within 3% of 55D high (or above) | ALL | TOO_FAR_FROM_HIGH |
| **Trend** | SMA50 > SMA200 | ALL | TREND_FAILED |
| **RSI range** | 55 ≤ RSI14 ≤ 78 | ALL | RSI_TOO_LOW / RSI_TOO_HIGH |
| **Base compression** | 20D range ≤ 15% of price | ALL | RANGE_TOO_WIDE |
| **Volume spike** | vol_spike ≥ 1.2x avg | ALL | VOLUME_INSUFFICIENT |
| **Extension** | dist_sma20 ≤ 8% | ALL | TOO_EXTENDED |

**Filter order**: Sequential — proximity → trend → RSI → compression → volume → extension.

#### Scoring Components (0–100 max)

| Component | Max Points | Key Thresholds |
|-----------|-----------|----------------|
| breakout_score | 35 | breakout_state (confirmed=18pt, attempt=12pt); proximity to 55D high; ATR quality |
| volume_score | 25 | vol_spike: ≥3.0x=15pt, ≥2.0x=12pt, ≥1.5x=9pt, ≥1.2x=5pt |
| trend_score | 20 | trend_state (strong_uptrend=12pt, uptrend=8pt); MA alignment (SMA20>50=+3pt, 50>200=+2pt) |
| base_quality | 20 | compression score, tight 20D range, gap penalty |

---

### Scanner 3: Pullback Swing (`pullback_swing_service.py`)

#### Config Thresholds (`_BALANCED_CONFIG`, L106)

| Threshold | Value | Configurable? | What Gets Rejected | Rationale |
|-----------|-------|---------------|-------------------|-----------|
| min_history_bars | 220 | No — hardcoded | < 220 bars | SMA200 stability |
| min_price | $5.00 | No — hardcoded | Penny stocks | Lowest threshold |
| min_avg_dollar_vol | $15,000,000 | No — hardcoded | Illiquid | Same as mean reversion |
| lookback_days | 400 | No — hardcoded | Insufficient data | ~280 trading days |
| per_symbol_timeout | 12.0s | No — hardcoded | Slow/timeout | |

#### Strategy-Specific Filter Gates

**⚠ NONE** — Pullback Swing has **no `_apply_filters()` method**. All ~196 symbols that pass the basic data checks (min_history_bars, min_price, min_avg_dollar_vol) are scored with no strategy-specific gating. This is unique among the 4 scanners.

**Impact**: Every symbol with sufficient data gets a composite score, even if it's not in a pullback, not in an uptrend, or in a structural breakdown. The only filtering is MIN_SETUP_QUALITY=30 at the runner level.

#### Scoring Components (0–100 max)

| Component | Max Points | Key Thresholds |
|-----------|-----------|----------------|
| trend_score | 35 | trend_state (strong=20pt, uptrend=12pt); MA alignment; slope |
| pullback_score | 35 | pullback zone: -1% to -6%=18pt, -6% to -10%=12pt, -10% to -15%=5pt |
| reset_score | 20 | RSI14: 40–60=14pt, 35–40=10pt, 60–68=8pt, 30–35=5pt |
| liquidity_score | 10 | avg_dollar_vol: ≥$500M=7pt, ≥$100M=5pt, ≥$50M=4pt, ≥$15M=2pt |

---

### Scanner 4: Volatility Expansion (`volatility_expansion_service.py`)

#### Config Thresholds (`_BALANCED_CONFIG`, L111)

| Threshold | Value | Configurable? | What Gets Rejected | Rationale |
|-----------|-------|---------------|-------------------|-----------|
| min_history_bars | 120 | No — hardcoded | < 120 bars | Shortest history requirement |
| min_price | $7.00 | No — hardcoded | Penny stocks | Higher bar |
| min_avg_dollar_vol | $20,000,000 | No — hardcoded | Illiquid | Highest liquidity bar |
| lookback_days | 280 | No — hardcoded | Insufficient data | ~190 trading days |
| per_symbol_timeout | 12.0s | No — hardcoded | Slow/timeout | |
| atr_pct_max | 0.12 (12%) | No — hardcoded | Too volatile | Risk sanity gate |
| atr_ratio_min | 1.25x | No — hardcoded | No ATR expansion | Expansion gate 1 |
| rv_ratio_min | 1.25x | No — hardcoded | No RV expansion | Expansion gate 2 |
| range_ratio_min | 1.35x | No — hardcoded | No range expansion | Expansion gate 3 |
| bb_width_percentile_max | 35 | No — hardcoded | No prior compression | Compression gate 1 |
| prior_range_20_max | 0.14 (14%) | No — hardcoded | Prior range too large | Compression gate 2 |
| prior_atr_pct_max | 0.045 (4.5%) | No — hardcoded | Prior ATR too high | Compression gate 3 |

#### Strategy-Specific Filter Gates (`_apply_filters`, L713)

| Gate | Condition | Must Meet | Rejection Code |
|------|-----------|-----------|----------------|
| **Expansion** | atr_ratio ≥ 1.25x, OR rv_ratio ≥ 1.25x, OR range_ratio ≥ 1.35x | ONE of 3 | NO_EXPANSION |
| **Prior compression** | bb_pctile ≤ 35 & bb_rising, OR prior_range ≤ 14%, OR prior_atr ≤ 4.5% | ONE of 3 | NO_PRIOR_COMPRESSION |
| **Long bias** | close ≥ SMA20, OR return_2d ≥ 0, OR (return_1d > 0 & vol_spike ≥ 1.2) | ONE of 3 | NO_LONG_BIAS |
| **Risk sanity** | atr_pct ≤ 0.12 | ALL | TOO_VOLATILE |

**Filter order**: Sequential — expansion → compression → long bias → risk.

#### Scoring Components (0–100 max)

| Component | Max Points | Key Thresholds |
|-----------|-----------|----------------|
| expansion_score | 40 | best_ratio: ≥2.0x=30pt, ≥1.7x=25pt, ≥1.5x=20pt, ≥1.35x=15pt, ≥1.25x=10pt; signal_count bonus |
| compression_score | 25 | BB percentile, prior range, prior ATR with graduated bonuses |
| confirmation_score | 20 | vol_spike, direction confirmation, bullish_bias |
| risk_score | 15 | ATR%, liquidity, gap_pct |

---

### Stock Runner Thresholds (`stock_opportunity_runner.py`)

| Threshold | Value | Location | Configurable? | What Gets Rejected |
|-----------|-------|----------|---------------|-------------------|
| MIN_SETUP_QUALITY | 30.0 | L93 | No — hardcoded | Candidates with composite_score < 30 |
| DEFAULT_TOP_N | 20 | L96 | Yes — RunnerConfig.top_n | Candidates ranked > 20 |
| _ENGINE_SCAN_LIMIT | 200 | L101 | No — hardcoded | Silent trim — max candidates requested from engine scan |
| MODEL_FILTER_TOP_N | 10 | L1406 | No — hardcoded | After model analysis: keep only top 10 by model_score |

#### Stage 7b Filter Logic

1. Discard candidates where `model_recommendation == "PASS"`
2. Discard candidates with no model analysis (`model_review is None`)
3. Rank remaining by `model_score` descending (None scores sort last)
4. Keep top `MODEL_FILTER_TOP_N` = 10

---

### Cross-Scanner Threshold Comparison

#### Universal Data Thresholds

| Threshold | Mean Reversion | Momentum Breakout | Pullback Swing | Volatility Expansion |
|-----------|:--------------:|:------------------:|:--------------:|:--------------------:|
| min_history_bars | 120 | **220** | **220** | 120 |
| min_price | $5.00 | **$7.00** | $5.00 | **$7.00** |
| min_avg_dollar_vol | $15M | **$20M** | $15M | **$20M** |
| lookback_days | 300 | 400 | 400 | 280 |
| per_symbol_timeout | 12.0s | 12.0s | 12.0s | 12.0s |
| Strategy filter chain? | **YES** (4 gates) | **YES** (6 gates) | **NO** ⚠ | **YES** (4 gates) |

**Observations**:
- Momentum Breakout and Volatility Expansion have higher liquidity bars ($7 min price, $20M min vol) — appropriate for their higher-conviction signals
- Mean Reversion and Pullback Swing have relaxed bars ($5 min price, $15M min vol) — appropriate for oversold/reset opportunities
- **Pullback Swing is the only scanner with NO strategy-specific filter gates** — every symbol that passes basic data checks is scored

#### Strategy-Specific Threshold Coverage

| Filter Type | Mean Rev | Momentum | Pullback | Vol Expansion |
|-------------|:--------:|:--------:|:--------:|:-------------:|
| Oversold/signal gate | ✅ RSI/zscore/dist | ✅ Proximity/trend | ❌ None | ✅ Expansion ratio |
| Confirmation gate | ✅ Stabilization | ✅ Volume spike | ❌ None | ✅ Compression + long bias |
| Extension/extreme gate | ❌ None | ✅ RSI max + dist | ❌ None | ❌ None |
| ATR/volatility cap | ✅ 10% | ❌ None | ❌ None | ✅ 12% |
| Structural damage | ✅ dist_sma50 | ❌ None | ❌ None | ❌ None |
| Compression/base check | ❌ None | ✅ range ≤ 15% | ❌ None | ✅ bb_pctile ≤ 35 |

---

### Threshold Sensitivity Analysis

#### Most Impactful Thresholds (Stock)

| Scanner | Threshold | Current | 10% Relaxed | 10% Tightened | Natural Breakpoint? |
|---------|-----------|---------|-------------|---------------|-------------------|
| Mean Rev | RSI14 ≤ 35 | 35 | ≤ 38.5 | ≤ 31.5 | Yes — RSI 30 is classic oversold; 35 is balanced |
| Mean Rev | min_avg_dollar_vol | $15M | $13.5M | $16.5M | Arbitrary — no exchange rule at $15M |
| Momentum | vol_spike_min | 1.2x | 1.08x | 1.32x | Arbitrary — 1.2x is a common convention, not a data breakpoint |
| Momentum | rsi_min/max | 55/78 | 49.5/85.8 | 60.5/70.2 | Arbitrary — 55 and 78 are heuristics |
| Vol Exp | atr_ratio_min | 1.25x | 1.125x | 1.375x | Arbitrary — chosen as "meaningful expansion" heuristic |
| Vol Exp | bb_width_percentile_max | 35 | 38.5 | 31.5 | Arbitrary — 35th percentile is a convention |
| All | MIN_SETUP_QUALITY | 30 | 27 | 33 | Arbitrary — 30 is "minimally interesting" heuristic |

**Sensitivity note**: Exact sensitivity per threshold cannot be computed without running live data through the pipeline. The estimates above are directional — a 10% relaxation of RSI from 35 to 38.5 could roughly double qualifying candidates (RSI is broadly distributed), while 10% relaxation of vol_spike from 1.2x to 1.08x would add significantly more candidates since volume spikes cluster near 1.0x.

---

## PART 2: Options Pipeline Filters

### Phase A — Narrowing Thresholds

#### DTE Windows (family-specific)

| Family | dte_min | dte_max | Source | Configurable? |
|--------|---------|---------|--------|---------------|
| Vertical Spreads (default) | 1 | 90 | base_scanner.py L71–74 | No — hardcoded in class |
| Iron Condors | 7 | 60 | iron_condors.py L83–86 | No — hardcoded in class |
| Butterflies | 7 | 60 | butterflies.py L73–76 | No — hardcoded in class |
| Calendars/Diagonals | 7 | 90 | calendars.py L116–119 | No — hardcoded in class |

#### Strike Distance / Moneyness

| Threshold | Value | Source | Family | Configurable? |
|-----------|-------|--------|--------|---------------|
| ATM threshold | 0.005 (0.5% from spot) | strikes.py L142 | All (when moneyness="atm") | Via V2NarrowingRequest |
| distance_min_pct | Family-dependent | Per family config | All | Via V2NarrowingRequest |
| distance_max_pct | Family-dependent | Per family config | All | Via V2NarrowingRequest |

#### Multi-Expiry Specific (Calendars/Diagonals)

| Threshold | Value | Source | Configurable? |
|-----------|-------|--------|---------------|
| min_dte_spread | 7 days | calendars.py L130–131 | Yes — context["min_dte_spread"] |
| max_strike_shift (diagonals) | $10.00 | calendars.py L132 | Yes — context["max_strike_shift"] |

---

### Phase B — Candidate Construction Thresholds

| Threshold | Value | Source | Applies To | Configurable? |
|-----------|-------|--------|-----------|---------------|
| _DEFAULT_GENERATION_CAP | 50,000 | vertical_spreads.py L54 | All families | Yes — context["generation_cap"] |
| _DEFAULT_MAX_WIDTH | $50.00 | vertical_spreads.py L66 | Verticals, IC wings, BF wings | Yes — context["max_width"] |
| IC side_cap | √50,000 ≈ 223 per side | iron_condors.py (derived) | Iron Condors | Derived from generation_cap |

---

### Phase C — Structural Validation

| Check | Requirement | Applies To | Rejection Code |
|-------|-------------|-----------|----------------|
| Leg count | 2 | Verticals, Calendars | v2_malformed_legs |
| Leg count | 3 | Debit Butterflies | v2_malformed_legs |
| Leg count | 4 (2P + 2C) | Iron Condors, Iron Butterflies | v2_malformed_legs |
| Same option_type | 1 short + 1 long, same type | Verticals | v2_malformed_legs |
| IC geometry | pl < ps < cs < cl | Iron Condors | v2_ic_invalid_geometry |
| Butterfly symmetry | Wings equidistant from center | Butterflies | v2_bf_asymmetric |
| Same expiry | All legs same expiration | Verticals, IC, BF | v2_mixed_expiry |
| Different expiry | Near ≠ Far expiration | Calendars/Diagonals | v2_same_expiry |

**Note**: No numeric thresholds — Phase C is purely structural (geometry, leg count, expiry matching). Width limits are NOT checked in Phase C; they are only applied during Phase B construction.

---

### Phase D — Quote & Liquidity Sanity (Hard Rejects)

| Check | Condition | Rejection Code | Source |
|-------|-----------|----------------|--------|
| Quote presence | bid ≠ None AND ask ≠ None | v2_missing_quote | phases.py L138 |
| Quote not inverted | ask ≥ bid | v2_inverted_quote | phases.py L152 |
| Positive mid | (bid+ask)/2 > 0 | v2_zero_mid | phases.py L167 |
| OI presence | open_interest ≠ None | v2_missing_oi | phases.py L181 |
| Volume presence | volume ≠ None | v2_missing_volume | phases.py L195 |

**Note**: Phase D checks **presence only** — OI and volume must not be None, but can be 0.

---

### Phase D2 — Trust Hygiene (Hard Rejects + Warnings + Dedup)

#### Hard Rejects

| Check | Condition | Rejection Code | Source |
|-------|-----------|----------------|--------|
| Negative bid | bid < 0 on any leg | v2_negative_bid | quote_sanity.py L65–76 |
| Negative ask | ask < 0 on any leg | v2_negative_ask | quote_sanity.py L77–87 |
| Dead leg | OI == 0 AND volume == 0 on any leg | v2_dead_leg | liquidity_sanity.py L55–75 |
| Credit spread impossible | short.bid − long.ask ≤ 0 (for credit structures) | v2_spread_pricing_impossible | quote_sanity.py L193–218 |
| Debit spread impossible | long.ask − short.bid ≤ 0 (for debit structures) | v2_spread_pricing_impossible | quote_sanity.py L219–240 |

#### Warnings (NOT rejects — diagnostics only)

| Check | Threshold | Default | Configurable? | Source |
|-------|-----------|---------|---------------|--------|
| Wide leg spread | (ask−bid)/mid > ratio | 1.0 (100%) | Yes — `wide_leg_spread_ratio` | quote_sanity.py L44 |
| Low OI | OI < threshold on any leg | 10 | Yes — `low_oi_warn` | liquidity_sanity.py L47 |
| Low volume | volume < threshold on any leg | 5 | Yes — `low_volume_warn` | liquidity_sanity.py L50 |
| Wide composite spread | sum(ask−bid)/sum(mid) > pct | 0.50 (50%) | Yes — `wide_spread_warn_pct` | liquidity_sanity.py L53 |

#### Duplicate Suppression

| Property | Value |
|----------|-------|
| Dedup key | (symbol, strategy_id, expiration, frozenset{(side, strike, option_type)}) |
| Keeper policy | Highest quote quality → highest liquidity (min OI × vol) → richest diagnostics → stable tiebreak (candidate_id) |
| Rejection code | v2_dedup_duplicate_suppress |

---

### Phase E — Math Verification Thresholds

#### Hard Rejects (existence/sanity)

| Check | Condition | Rejection Code | Source |
|-------|-----------|----------------|--------|
| Positive max_loss | max_loss > 0 (if set) | v2_impossible_max_loss | math_checks.py L82–96 |
| Positive max_profit | max_profit > 0 (if set) | v2_impossible_max_profit | math_checks.py L99–113 |
| Finite values | All numeric fields finite | v2_non_finite_math | math_checks.py L116–135 |

#### Tolerance-Based Verification (WARN, not reject by default)

| Metric | abs_pass | abs_warn | rel_warn | IC Override | BF Override | Cal Override |
|--------|----------|----------|----------|-------------|-------------|--------------|
| net_credit | $0.005 | $0.020 | — | $0.01/$0.04 | $0.01/$0.04 | — |
| net_debit | $0.005 | $0.020 | — | — | $0.01/$0.04 | $0.005/$0.02 |
| max_profit | $0.50 | $2.00 | 1% | $1.00/$4.00/2% | $1.00/$5.00/2% | — |
| max_loss | $0.50 | $2.00 | 1% | $1.00/$4.00/2% | $1.00/$5.00/2% | $0.50/$2.00/1% |
| width | $0.001 | $0.010 | — | — | — | — |
| breakeven | $0.01 | $0.05 | — | — | — | — |
| ror | 0.001 | 0.010 | — | — | — | — |
| ev | $1.00 | $5.00 | 2% | — | — | — |

Source: tolerances.py L37–100

---

### Credibility Gate Thresholds (Post-Scanner, Runner Level)

| Check | Threshold | Condition | Rejection Code | Source | Configurable? |
|-------|-----------|-----------|----------------|--------|---------------|
| Minimum premium | $0.05/share | max(net_credit, net_debit) < 0.05 | penny_premium | runner L963 | No |
| Maximum POP | 0.995 | pop ≥ 0.995 | zero_delta_short | runner L964 | No |
| Fillable leg | > 0 bid on at least 1 leg | All legs bid ≤ 0 | all_legs_zero_bid | runner L990–995 | No |

---

### Post-Pipeline Selection Thresholds

| Threshold | Value | Pipeline | Configurable? | Source |
|-----------|-------|----------|---------------|--------|
| DEFAULT_TOP_N (options) | 30 | Options | Yes — RunnerConfig.top_n | options runner L98 |
| DEFAULT_TOP_N (stocks) | 20 | Stocks | Yes — RunnerConfig.top_n | stock runner L96 |
| MIN_SETUP_QUALITY | 30.0 | Stocks | No | stock runner L93 |
| MODEL_FILTER_TOP_N | 10 | Stocks | No | stock runner L1406 |
| _ENGINE_SCAN_LIMIT | 200 | Stocks | No | stock runner L101 |

---

## Legacy StrategyService Presets (V1 — NOT used by V2 scanner pipeline)

The `StrategyService._PRESETS` dictionary defines Strict/Balanced/Conservative/Wide thresholds for the **legacy** options scanning path. The V2 scanner pipeline (used by `options_opportunity_runner.py`) **does not use these presets** — V2 explicitly declares `"resolved_thresholds": {}` in its filter trace (migration.py L205).

These presets are documented here for completeness and as a reference for what the V2 pipeline is missing:

### Credit Spread Presets

| Threshold | Strict | Conservative | Balanced | Wide |
|-----------|--------|--------------|----------|------|
| dte_min | 14 | 14 | 7 | 3 |
| dte_max | 30 | 30 | 45 | 60 |
| width_min | $3.00 | $3.00 | $1.00 | $1.00 |
| width_max | $5.00 | $5.00 | $5.00 | $10.00 |
| distance_min | 3% | 3% | 1% | 1% |
| distance_max | 8% | 8% | 12% | 15% |
| min_pop | 0.70 | 0.60 | 0.55 | 0.45 |
| min_ev_to_risk | 0.03 | 0.012 | 0.008 | 0.005 |
| min_ror | 0.03 | 0.01 | 0.005 | 0.002 |
| max_bid_ask_spread_pct | 1.0 | 1.5 | 2.0 | 3.0 |
| min_open_interest | 1000 | 200 | 100 | 25 |
| min_volume | 100 | 10 | 5 | 1 |
| max_candidates | 200 | 300 | 400 | 800 |

### Iron Condor Presets

| Threshold | Strict | Conservative | Balanced | Wide |
|-----------|--------|--------------|----------|------|
| dte_min | 21 | 21 | 14 | 14 |
| dte_max | 45 | 45 | 45 | 60 |
| distance_target (EM mult) | 1.2 | 1.1 | 1.0 | 0.9 |
| wing_width_max | $10.00 | $10.00 | $10.00 | $15.00 |
| min_ror | 0.15 | 0.12 | 0.08 | 0.05 |
| min_credit | $0.15 | $0.10 | $0.10 | $0.05 |
| min_ev_to_risk | 0.05 | 0.02 | 0.00 | -0.05 |
| min_pop | 0.55 | 0.50 | 0.45 | 0.35 |
| min_open_interest | 1000 | 500 | 300 | 100 |
| min_volume | 100 | 50 | 0 | 0 |
| min_short_leg_mid | $0.10 | $0.08 | $0.05 | $0.05 |
| min_side_credit | $0.10 | $0.08 | $0.05 | $0.03 |

### Butterfly Presets

| Threshold | Strict | Conservative | Balanced | Wide |
|-----------|--------|--------------|----------|------|
| dte_min | 7 | 7 | 7 | 3 |
| dte_max | 21 | 30 | 45 | 60 |
| width_min | $2.00 | $2.00 | $1.00 | $0.50 |
| width_max | $10.00 | $10.00 | $15.00 | $20.00 |
| min_pop | 0.08 | 0.06 | 0.04 | 0.02 |
| min_ev_to_risk | 0.01 | 0.005 | -0.01 | -0.05 |
| min_cost_efficiency | 2.0 | 1.5 | 1.0 | 0.5 |
| max_debit_pct_width | 0.35 | 0.45 | 0.60 | 0.80 |
| min_open_interest | 1000 | 500 | 300 | 50 |
| min_volume | 100 | 50 | 20 | 5 |

### Calendar Presets

| Threshold | Strict | Conservative | Balanced | Wide |
|-----------|--------|--------------|----------|------|
| near_dte_min | 7 | 7 | 7 | 5 |
| near_dte_max | 14 | 14 | 21 | 28 |
| far_dte_min | 30 | 28 | 21 | 21 |
| far_dte_max | 60 | 60 | 60 | 90 |
| min_open_interest | 1000 | 500 | 300 | 100 |
| min_volume | 100 | 50 | 20 | 5 |
| required_metrics_complete | True | True | True | True |

**Note on calendars**: All presets set `required_metrics_complete=True`, which means the legacy path would reject every calendar candidate because POP/EV are not computed. The V2 path bypasses this entirely.

---

## PART 3: Cross-Pipeline Comparison

### Pipeline Filtering Stage Count

| Stage | Stock Pipeline | Options Pipeline |
|-------|---------------|-----------------|
| Universe / symbol selection | 1 (hardcoded 196 symbols) | 1 (hardcoded 4 ETFs) |
| Data fetch & basic checks | 1 (min_history, min_price, min_vol) | 1 (chain fetch per expiration) |
| Strategy-specific filter | 1 (scanner _apply_filters) — except Pullback | 0 (no strategy-specific filter in V2) |
| Narrowing / DTE filtering | 0 | 1 (Phase A) |
| Candidate construction | 0 | 1 (Phase B) |
| Structural validation | 0 | 1 (Phase C) |
| Quote/liquidity sanity | 0 | 2 (Phase D + D2) |
| Math verification | 0 | 1 (Phase E) |
| Normalization | 0 | 1 (Phase F) |
| Quality threshold | 1 (MIN_SETUP_QUALITY=30) | 0 |
| Dedup | 1 (by symbol, keep max score) | 1 (Phase D2 dedup) |
| Credibility gate | 0 | 1 (3 checks) |
| Ranking + top-N | 1 (top 20 by setup_quality) | 1 (top 30 by EV) |
| Model analysis | 1 (LLM review per candidate) | 0 |
| Model filter/rank | 1 (remove PASS, top 10 by model_score) | 0 |
| **Total filtering stages** | **7** | **10** |

### Estimated Rejection Distribution — Where Do Most Candidates Die?

#### Stock Pipeline

```
~196 symbols per scanner
  ↓
Basic data checks (min_history, min_price, min_vol)
  → Rejects ~10-30% (NO_DATA, INSUFFICIENT_HISTORY, PRICE_TOO_LOW, LOW_LIQUIDITY)
  ↓
Strategy-specific filters (3 of 4 scanners)
  → Rejects ~60-90% of remaining (BIGGEST FILTER for MR, MB, VE)
  → Pullback Swing: 0% rejection here (no filter chain)
  ↓
Scoring → MIN_SETUP_QUALITY=30
  → Rejects ~20-50% of scored candidates
  ↓
Dedup (multi-scanner → by symbol)
  → Rejects ~15-30% (keep best score per symbol)
  ↓
Top-20 selection
  → Rejects remainder (typically ~5-15 candidates cut)
  ↓
Model analysis → remove PASS
  → Rejects ~20-40% (model says PASS = don't trade)
  ↓
Top-10 by model_score
  → Final output: ~5-10 candidates
```

**Biggest rejection stage**: Strategy-specific filters (60-90% of data-valid symbols). Pullback Swing is the exception — its biggest rejector is MIN_SETUP_QUALITY.

#### Options Pipeline

```
4 symbols × 11 scanner_keys = 44 scan runs
  ↓
Phase A (narrow by DTE/strikes)
  → Removes ~50-70% of raw contracts from chain
  ↓
Phase B (construct candidates)
  → EXPANDS to potentially thousands-50K candidates per run
  ↓
Phase C (structural validation)
  → Rejects ~1-5% (malformed legs only)
  ↓
Phase D (quote/liquidity sanity)
  → Rejects ~10-30% (missing quotes, inverted, missing OI/volume)
  ↓
Phase D2 (trust hygiene + dedup)
  → Rejects ~15-40% (dead legs, impossible pricing, DEDUP is a major filter here)
  ↓
Phase E (math verification)
  → Rejects ~1-5% (impossible max_loss/profit, non-finite)
  ↓
Phase F (normalize)
  → 0% additional rejection (just sets passed/rejected flags)
  ↓
Stage 3 validate_math (downstream_usable)
  → Rejects ~0-5% (mostly already caught by Phase E)
  ↓
Credibility gate (3 checks)
  → Rejects ~30-60% (BIGGEST POST-SCANNER FILTER)
  ↓
Top-30 by EV
  → Final output: 30 candidates (or fewer if < 30 survive)
```

**Biggest rejection stages**: Phase A narrowing (removes most raw contracts) and Phase D2 dedup + credibility gate (removes most constructed candidates).

### Quality Control Comparison

| Dimension | Stock Pipeline | Options Pipeline | Winner |
|-----------|---------------|-----------------|--------|
| **Pre-filter depth** | Basic data + strategy gates → only qualified candidates scored | Phase A narrowing → all combinations constructed → post-hoc filter | **Stock** (rejects early) |
| **Structural validation** | None (no leg structure) | Phase C geometry checks | N/A (different domains) |
| **Quote quality** | Implicit (Tradier data required) | Phase D + D2 (extensive) | **Options** (explicit) |
| **Math verification** | None (simple scoring) | Phase E + tolerance checks | **Options** (explicit) |
| **Quality threshold** | MIN_SETUP_QUALITY=30 (composite score) | Credibility gate (3 checks) | **Stock** (composite score is richer) |
| **Model review** | YES — LLM analysis with BUY/PASS | NO | **Stock** (qualitative review) |
| **Preset framework** | NO (hardcoded _BALANCED_CONFIG) | NO for V2 (legacy presets exist but unused) | **Neither** |
| **Total stages** | 7 | 10 | **Options** (more stages, but no model) |
| **Effective quality floor** | MIN_SETUP_QUALITY=30 + model BUY | MIN_PREMIUM=$0.05 + POP<0.995 | **Stock** (higher bar) |

---

## Master Threshold Table

| Pipeline | Stage | Threshold Name | Value | Configurable? | Rejects Most/Fewest? |
|----------|-------|---------------|-------|---------------|---------------------|
| **Stock** | Universe | Universe size | ~196 symbols | No | N/A — defines scope |
| Stock | Data | min_history_bars | 120–220 | No | Moderate |
| Stock | Data | min_price | $5–$7 | No | Few (most universe names > $7) |
| Stock | Data | min_avg_dollar_vol | $15M–$20M | No | Few (universe pre-screened) |
| Stock | Filter | RSI14 ≤ 35 (MR) | 35 | No | **Most** (MR scanner) |
| Stock | Filter | proximity_55d ≥ -3% (MB) | -0.03 | No | **Most** (MB scanner) |
| Stock | Filter | atr_ratio ≥ 1.25x (VE) | 1.25 | No | **Most** (VE scanner) |
| Stock | Filter | *No filter* (PS) | — | — | N/A — no filter chain |
| Stock | Quality | MIN_SETUP_QUALITY | 30 | No | Moderate–High |
| Stock | Selection | DEFAULT_TOP_N | 20 | Yes | Few (trim) |
| Stock | Model | PASS removal | — | No | 20–40% |
| Stock | Model | MODEL_FILTER_TOP_N | 10 | No | Trim to final 10 |
| **Options** | DTE | dte_min/max | 1–90 (varies) | No | Moderate (limits expirations) |
| Options | Construction | _DEFAULT_GENERATION_CAP | 50,000 | Yes (context) | Safety cap only |
| Options | Construction | _DEFAULT_MAX_WIDTH | $50 | Yes (context) | Few (very wide spreads rare) |
| Options | Phase D | Quote presence | bid/ask ≠ None | No | Moderate |
| Options | Phase D | OI/volume presence | ≠ None | No | Low–Moderate |
| Options | Phase D2 | Dead leg (OI=0 AND vol=0) | 0 | No | Moderate |
| Options | Phase D2 | Spread pricing impossible | credit/debit ≤ 0 | No | Moderate |
| Options | Phase D2 | Dedup | Structural match | No | **High** (major filter) |
| Options | Phase D2 | Wide leg spread (warn) | 100% of mid | Yes | Warning only |
| Options | Phase D2 | Low OI (warn) | < 10 | Yes | Warning only |
| Options | Phase D2 | Low volume (warn) | < 5 | Yes | Warning only |
| Options | Phase E | Positive max_loss | > 0 | No | Few |
| Options | Phase E | Positive max_profit | > 0 | No | Few |
| Options | Phase E | Finite values | All finite | No | Few |
| Options | Credibility | MIN_PREMIUM | $0.05/share | No | **Highest** (post-scanner) |
| Options | Credibility | MAX_POP_THRESHOLD | 0.995 | No | Moderate |
| Options | Credibility | All legs zero bid | Any bid > 0 | No | Low |
| Options | Selection | DEFAULT_TOP_N | 30 | Yes | Trim to final 30 |

---

## Findings

### F-3C-01 — HIGH: Pullback Swing Has No Strategy-Specific Filter Chain

**Evidence**: Pullback Swing (`pullback_swing_service.py`) has **no `_apply_filters()` method**. All ~196 symbols that pass basic data checks (min_history, min_price, min_vol) are scored. The other 3 scanners reject 60-90% of symbols via strategy-specific gates before scoring.

**Impact**: Pullback Swing floods the pipeline with ~150+ scored candidates, most of which are not in a pullback setup. Only MIN_SETUP_QUALITY=30 catches these, but that's a blunt instrument — a symbol with no pullback can still score 30+ if it has good liquidity and moderate trend metrics.

**Recommendation**: Add an `_apply_filters()` method with at least:
- Pullback zone gate: dist_sma20 between -1% and -15% (reject if not pulled back)
- Trend gate: SMA50 > SMA200 or uptrend state (reject if no uptrend to pull back from)
- RSI range: 30–68 (reject if not in reset zone)

---

### F-3C-02 — HIGH: V2 Options Pipeline Has No Preset Framework — All Thresholds Hardcoded

**Evidence**: The V2 scanner pipeline explicitly declares `"resolved_thresholds": {}` (migration.py L205). All DTE windows, generation caps, and credibility gate values are hardcoded. Meanwhile, the legacy `StrategyService._PRESETS` has a well-designed 4-level preset system (Strict/Conservative/Balanced/Wide) with ~15 thresholds per strategy family.

**Impact**: 
- No way to adjust scanning sensitivity without code changes
- The `presets.md` standard requires Strict/Balanced/Wide to resolve to meaningfully different thresholds — V2 has only one effective "wide scan" mode
- The copilot-instructions.md requires "Preset resolution must be centralized in one function/module" — V2 doesn't use any

**Recommendation**: Port the StrategyService preset framework to V2, mapping preset knobs to:
- DTE windows (tighter for strict, wider for wide)
- Phase D2 liquidity warnings → promotable to hard rejects in strict mode
- Generation cap (lower for strict = faster, higher for wide = more discovery)
- Credibility gate thresholds (higher MIN_PREMIUM for strict)

---

### F-3C-03 — HIGH: All Stock Scanner Thresholds Are Hardcoded — No Runtime Configurability

**Evidence**: Every stock scanner uses a single `_BALANCED_CONFIG` dict with hardcoded values. There is no preset resolution, no config file, no CLI override. The `presets.md` standard requires Strict/Balanced/Wide presets for all scanners.

**Impact**: Cannot tune scanner sensitivity without editing source code. Cannot A/B test different threshold levels. Cannot adapt to different market conditions.

**Recommendation**: 
1. Extract all `_BALANCED_CONFIG` dicts into a centralized preset resolution function
2. Add Strict and Wide variants with meaningful differences
3. Make preset_name configurable via RunnerConfig

---

### F-3C-04 — MEDIUM: Universe Duplicated Across 4 Scanner Files

**Evidence**: `_BALANCED_UNIVERSE` (196 symbols) and `_ETF_EXCLUSIONS` (100+ ETFs) are copy-pasted in all 4 scanner files: mean_reversion_service.py, momentum_breakout_service.py, pullback_swing_service.py, volatility_expansion_service.py.

**Impact**: Maintenance drift risk. Adding or removing a symbol requires editing 4 files. If one file is updated and another isn't, different scanners scan different universes.

**Recommendation**: Extract to a shared module: `app/services/stock_universe.py` with `BALANCED_UNIVERSE` and `ETF_EXCLUSIONS`.

---

### F-3C-05 — MEDIUM: V2 Credibility Gate Missing Quality Thresholds Available in Legacy Presets

**Evidence**: The legacy StrategyService presets define rich quality gates:
- `min_pop`: 0.35–0.70 (V2 only checks POP < 0.995)
- `min_ror`: 0.002–0.15 (V2 has no RoR check)
- `min_ev_to_risk`: -0.05–0.05 (V2 has no EV threshold)
- `min_open_interest`: 25–1000 (V2 only warns at OI < 10, never rejects)
- `min_volume`: 0–100 (V2 only warns at vol < 5, never rejects)
- `max_bid_ask_spread_pct`: 1.0–3.0 (V2 only warns at spread > 100%, never rejects)
- `min_credit`/`min_side_credit`: $0.03–$0.15 (V2 checks MIN_PREMIUM=$0.05)
- `width_min`: $0.50–$3.00 (V2 has no minimum width check)

The V2 pipeline's 3-check credibility gate is dramatically thinner than the legacy preset framework's ~15 quality knobs per family.

**Impact**: Low-quality candidates that the legacy path would reject can pass V2's credibility gate and occupy top-30 slots.

**Recommendation**: Migrate key legacy preset thresholds to V2 credibility gate or add a new quality gate stage: min_ror, min_oi, min_width, max_bid_ask_spread_pct at minimum.

---

### F-3C-06 — MEDIUM: Phase D Checks Are Presence-Only — OI=0 and Volume=0 Individually Pass

**Evidence**: Phase D checks `open_interest is not None` and `volume is not None` but does NOT check their values. A leg with OI=0 and volume=5 passes Phase D. Only Phase D2 catches the case where **both** OI=0 AND volume=0 on the same leg (`v2_dead_leg`).

**Impact**: A candidate can have legs with OI=0 (no open interest — no existing positions) as long as volume > 0. This is technically not "dead" but is a liquidity concern — a single day's volume doesn't mean there's a market.

**Note**: The Phase D2 warnings for `low_oi < 10` and `low_volume < 5` flag these, but warnings don't reject.

---

### F-3C-07 — MEDIUM: Sensitivity Analysis Not Possible Without Pipeline Instrumentation

**Evidence**: None of the scanners log intermediate candidate counts at each filter stage. There is no diagnostic output showing "196 symbols entered → 150 passed data checks → 45 passed strategy filters → 20 passed MIN_SETUP_QUALITY". The filter_trace required by scanner-contract.md exists for options (phase_counts) but not for stocks.

**Impact**: Cannot answer "how many additional candidates would pass if RSI threshold relaxed by 10%" without running the pipeline with modified thresholds.

**Recommendation**: Add filter_trace to stock scanners matching the scanner-contract.md spec: stage_counts at each filter gate, rejection_reason_counts.

---

### F-3C-08 — LOW: Momentum Breakout Has No ATR/Volatility Cap

**Evidence**: Mean Reversion has `atr_pct_max=0.10`, Volatility Expansion has `atr_pct_max=0.12`, but Momentum Breakout has **no ATR cap**. A stock with 25% daily ATR can qualify as a breakout candidate.

**Impact**: Very volatile names could pass all momentum breakout filters if they meet proximity/trend/RSI/compression/volume criteria. High-ATR breakouts are riskier due to wider stops.

**Recommendation**: Add `atr_pct_max=0.10` or `0.12` to momentum breakout config to match other scanners' risk sanity gates.

---

### F-3C-09 — LOW: _DEFAULT_MAX_WIDTH=$50 Is Very Permissive for V2 Options

**Evidence**: The default max width for vertical spreads, IC wings, and butterfly wings is $50. For SPY trading at ~$500, a $50-wide spread has max_loss of $5,000 per contract minus credit. For lower-priced underlyings like IWM (~$200), $50 is 25% of the stock price.

**Impact**: Very wide spreads are constructed and pass through the pipeline. They'll compete for top-30 slots based on EV, and wider spreads tend to have higher absolute EV (more premium for more risk), creating the bias documented in F-3B-04.

**Recommendation**: Consider a percentage-based max width (e.g., 10% of underlying price) or family-specific max widths. The legacy presets cap width at $5–$15 depending on preset level.

---

### F-3C-10 — LOW: Calendar Presets Set required_metrics_complete=True (Dead Feature)

**Evidence**: All 4 calendar preset levels set `required_metrics_complete=True`, which means the legacy `evaluate()` function would reject every calendar candidate because POP/EV are not implemented. The V2 pipeline bypasses this entirely (V2 honestly sets EV=None for calendars).

**Impact**: The legacy calendar scanning path is effectively disabled. V2 handles calendars correctly but with no quality gate for EV (since EV=None → 0.0 in ranking).

---

## Summary

| Severity | Count | Key Theme |
|----------|-------|-----------|
| HIGH | 3 | Pullback Swing missing filter chain; V2 no presets; Stock scanners all hardcoded |
| MEDIUM | 4 | Credibility gate too thin vs legacy presets; Phase D presence-only checks; No pipeline instrumentation; Universe duplication |
| LOW | 3 | Momentum breakout missing ATR cap; $50 max width permissive; Calendar legacy dead feature |
| **Total** | **10** | |

### Threshold Count Summary

| Pipeline | Hard-Reject Thresholds | Warning Thresholds | Scoring Thresholds | Total |
|----------|----------------------|-------------------|-------------------|-------|
| Stock (per scanner) | 3–13 (varies by scanner) | 0 | ~15–20 (score component breakpoints) | ~18–33 |
| Stock (runner) | 3 (MIN_SETUP_QUALITY + MODEL_FILTER + PASS removal) | 0 | 0 | 3 |
| Options V2 (per scan) | ~15 (Phase A-F) | 4 (D2 warnings) | 0 | ~19 |
| Options (runner) | 3 (credibility gate) | 0 | 0 | 3 |
| Legacy presets (V1) | ~15 per family per preset | 0 | 0 | ~60+ total |

### Configurability Assessment

- **Stock pipeline**: 0% configurable — all thresholds hardcoded in `_BALANCED_CONFIG` dicts
- **Options V2 pipeline**: ~30% configurable — construction params (generation_cap, max_width, min_dte_spread, max_strike_shift) and D2 warning thresholds are overridable via context params, but DTE windows, credibility gate, and phase checks are hardcoded
- **Options V1 (legacy)**: ~90% configurable — StrategyService._PRESETS has 4 levels per family, and `resolve_thresholds()` supports per-call overrides
- **Neither pipeline implements the Strict/Balanced/Wide preset standard** required by `presets.md`
