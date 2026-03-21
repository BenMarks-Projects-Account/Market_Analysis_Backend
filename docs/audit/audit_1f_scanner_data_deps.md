# Audit 1F — Scanner Data Dependencies

**Scope:** All scanner subsystems — stock scanners (4) and options V2 pipeline (4 strategy families, 11 strategy IDs).  
**Goal:** Map every data input to its source API call, verify point-in-time (PIT) correctness, document math formula traceability, and flag look-ahead / stale-data risks.  
**Date:** 2025-06-19  
**Auditor:** Copilot (code-level review)

---

## 1  Stock Scanner Subsystem

### 1.1  Scanner Inventory

| Scanner | File | Strategy ID | Min History | Lookback (cal) | Min Price | Min Avg $ Vol |
|---|---|---|---|---|---|---|
| Mean Reversion | `mean_reversion_service.py` | `mean_reversion` | 120 bars | 300 days | $5 | $15M |
| Momentum Breakout | `momentum_breakout_service.py` | `momentum_breakout` | 220 bars | 400 days | $7 | $20M |
| Pullback Swing | `pullback_swing_service.py` | `pullback_swing` | 220 bars | 400 days | $7 | $20M |
| Volatility Expansion | `volatility_expansion_service.py` | `volatility_expansion` | 120 bars | 280 days | $5 | $15M |

### 1.2  Universe & Data Source

**Universe:** Static `_BALANCED_UNIVERSE` (~196 curated large/mid-cap tickers), identical copy in each scanner file. ETFs excluded via `_ETF_EXCLUSIONS` frozenset.

| Data Flow Step | Source |
|---|---|
| Primary bars (OHLCV) | `tradier_client.get_daily_bars(symbol, lookback_days)` |
| Fallback (close only) | `base_data_service.get_prices_history(symbol)` |
| Current price | `closes[-1]` from the fetched bar series |
| Confidence | 1.0 (Tradier) / 0.7 (BDS fallback) |

**PIT assessment:** Bars are backward-looking daily OHLCV. No forward data used.

**⚠ Finding 1F-01 (LOW): Intraday price reference from `closes[-1]`.**  
During market hours, `closes[-1]` is the *previous* session close, not a live quote. This is conservative (no look-ahead), but the price used for metrics (distance_to_sma, z-score, etc.) and the final `"price"` output field may be stale by hours. This is acceptable for daily-timeframe scanners but worth noting.

**⚠ Finding 1F-02 (LOW): Static universe duplication.**  
`_BALANCED_UNIVERSE` and `_ETF_EXCLUSIONS` are copy-pasted into all 4 scanner files. A single shared constant module would eliminate drift risk. No current data-integrity impact, but a maintenance hazard.

### 1.3  Technical Indicator Implementations

All scanners compute indicators inline or via `common/quant_analysis.py`.

| Indicator | Source Module | Formula (verified) |
|---|---|---|
| RSI(14) / RSI(2) | `quant_analysis.rsi(prices, period)` | Cutler's RSI: avg_gain / avg_loss over `period` bars. Correct. |
| SMA(20/50/200) | `quant_analysis.simple_moving_average(prices, window)` | `sum(prices[-window:]) / window`. Correct. |
| ATR(14) | Inline `_atr()` in each scanner | `mean(max(H-L, |H-prevC|, |L-prevC|))` over 14 bars. Correct. |
| Realized Vol (20d) | `quant_analysis.realized_vol_annualized(prices, trading_days=252)` | `std(log_returns) × √252`. Correct. |
| Z-score(20) | Inline | `(price − SMA20) / std(closes[-20:])`. Correct. |
| Bollinger Width | Vol expansion inline | `4 × std(closes[-20:]) / SMA20`. BB %ile via rolling rank over 180 bars. Correct. |

All indicator implementations use standard textbook formulas. No forward bias detected.

### 1.4  Per-Scanner Scoring Details

#### 1.4.1  Mean Reversion (0–100)

| Component | Weight | Key Inputs | Thresholds |
|---|---|---|---|
| oversold | 0–40 | RSI14, z-score_20, distance_sma20 | RSI sweet spot 25–35; z-score bonus at −2.5 to −1.5 |
| stabilization | 0–25 | RSI2, bounce_hint, slope_sma20 | RSI2 ≤ 5 bonus; requires upward slope confirmation |
| room | 0–20 | drawdown_55d, distance_sma50 | Penalizes >18% below SMA50 |
| liquidity | 0–15 | avg_dollar_vol_20 | $500M+ = 10 pts, $50–100M = 5 pts |

#### 1.4.2  Momentum Breakout (0–100)

| Component | Weight | Key Inputs | Thresholds |
|---|---|---|---|
| breakout | 0–35 | breakout_state, proximity_55d_high, ATR | State bonuses; proximity pct vs config thresholds |
| volume | 0–25 | vol_spike_ratio, today_vol | Spike ≥ 2.5 = 12 pts; ≥ 1.5 = 8 pts |
| trend | 0–20 | SMA alignment, slope_50 | SMA20 > SMA50 > SMA200 stacking |
| base_quality | 0–20 | compression_ratio, consolidation_days | Lower compression = tighter base = better |

#### 1.4.3  Pullback Swing (0–100)

| Component | Weight | Key Inputs | Thresholds |
|---|---|---|---|
| trend | 0–35 | trend_state, SMA alignment, slope_50 | strong_uptrend=20, uptrend=12; MA stack bonuses |
| pullback | 0–35 | pullback_from_20d_high, dist_sma20, dist_sma50 | Sweet spot: −1% to −6% pullback; SMA20 proximity ±1.5% |
| reset | 0–20 | RSI14, rsi_change_5d | RSI 40–60 ideal reset zone; stabilizing momentum bonus |
| liquidity | 0–10 | avg_dollar_vol_20, today_vol_vs_avg | $500M+ = 7 pts; normal volume range bonus |

#### 1.4.4  Volatility Expansion (0–100)

| Component | Weight | Key Inputs | Thresholds |
|---|---|---|---|
| expansion | 0–40 | atr_ratio_10, rv_ratio, range_ratio | Best ratio ≥ 2.0 = 30 pts; multi-signal bonus |
| compression | 0–25 | bb_width_percentile_180, prior_range_20_pct, prior_atr_pct | BB %ile ≤ 15 = 14 pts; prior range ≤ 8% = 7 pts |
| confirmation | 0–20 | vol_spike_ratio, return_1d/2d, close_vs_sma20, bullish_bias | Volume spike + directional confirmation |
| risk | 0–15 | atr_pct, avg_dollar_vol_20, gap_pct | ATR% reasonableness + liquidity + gap size |

**⚠ Finding 1F-03 (MEDIUM): All scoring weights and thresholds are hardcoded.**  
Every scanner's `_score()` function uses inline numeric constants (e.g., `if rsi14 >= 25 and rsi14 <= 35: score += 18`). There is no mechanism to adjust scoring sensitivity without code changes. The `_BALANCED_CONFIG` dict controls *filter* thresholds (min_price, min_vol, etc.) but NOT scoring thresholds. This means:
- No preset-based scoring adjustment (Strict/Balanced/Wide affects filters, not scores)
- No A/B testing of weight variants without code changes
- Weights are not documented in a central reference (each scanner defines its own)

### 1.5  Filter Pipeline (per scanner)

Each scanner follows the same flow:

```
_build_universe(~196 tickers)
  → fetch bars (Tradier primary, BDS fallback)
    → validate min_history_bars
      → validate min_price
        → validate min_avg_dollar_vol
          → _compute_metrics()
            → _apply_filters() [scanner-specific]
              → _score()
                → build output dict
```

All 4 scanners validate `min_history_bars` before computing any metrics — preventing partial-data calculations. This is correct.

**Concurrency:** asyncio.Semaphore(8), per-symbol timeout of 12 seconds.

---

## 2  Options V2 Scanner Pipeline

### 2.1  Pipeline Architecture (6 Phases)

| Phase | Name | Purpose |
|---|---|---|
| A | Data Narrowing | Raw Tradier chain → DTE filter → strike-distance filter → V2NarrowedUniverse |
| B | Candidate Construction | Family-specific: enumerate spread/condor/butterfly/calendar candidates from narrowed contracts |
| C | Structural Validation | Shared + family-specific geometry checks (leg count, strike ordering, width) |
| D/D2 | Quote & Trust Hygiene | Missing/inverted quote rejection, OI/volume presence, deduplication |
| E | Recomputed Math | All pricing from leg quotes: net_credit/debit, max_profit, max_loss, POP, EV, RoR, Kelly, breakevens |
| F | Normalize | Timestamps, passed/rejected flag, scanner version |

### 2.2  Data Source Chain

| Data Element | Source | Fetch Point |
|---|---|---|
| Option chain contracts | Tradier `get_expirations(symbol)` → `get_analysis_inputs(symbol, exp)` | `options_scanner_service._run_one()` |
| Greeks (delta, gamma, theta, vega) | Tradier API `greeks` dict in chain response | Extracted by `normalize_contract()` via `_resolve_greek()` |
| Underlying price | `base_data_service.get_underlying_price(symbol)` | `options_scanner_service._run_one()` |
| Bid / Ask / Mid | Tradier chain response per contract | `normalize_contract()` — mid = (bid+ask)/2 |
| OI / Volume | Tradier chain response per contract | `normalize_contract()` via `_safe_int()` |
| DTE | Computed: `(expiration_date - today).days` | `narrow_expirations()` during Phase A |

**Symbol universe (options):** `("SPY", "QQQ", "IWM", "DIA")` — hardcoded in `options_opportunity_runner.py` as `DEFAULT_SYMBOLS`.

**Data boundary:** The workflow runner (`options_opportunity_runner.py`) NEVER calls Tradier directly. `OptionsScannerService` is the data-provider boundary. This is clean architecture.

### 2.3  Chain Normalization (Phase A)

`scanner_v2/data/chain.py::normalize_contract()` maps Tradier dict → `V2OptionContract`:

| Field | Tradier Key | Null Handling |
|---|---|---|
| symbol | `symbol` | Required |
| strike | `strike` | `_safe_float()` — None if missing |
| option_type | `option_type` | "call" / "put" |
| expiration | `expiration_date` | Required |
| bid | `bid` | `_safe_float()` — None if missing |
| ask | `ask` | `_safe_float()` — None if missing |
| mid | computed | `(bid+ask)/2` if both present, else None |
| delta | `greeks.delta` | `_resolve_greek()` — None if missing |
| gamma | `greeks.gamma` | `_resolve_greek()` — None if missing |
| theta | `greeks.theta` | `_resolve_greek()` — None if missing |
| vega | `greeks.vega` | `_resolve_greek()` — None if missing |
| open_interest | `open_interest` | `_safe_int()` — None if missing |
| volume | `volume` | `_safe_int()` — None if missing |

**Correct:** `_safe_float()` and `_safe_int()` return None for missing/invalid, never 0 sentinel. This follows the data-quality rules (missing ≠ 0).

### 2.4  Narrowing Pipeline (Phase A)

`scanner_v2/data/narrow.py::narrow_chain()`:

1. **Normalize:** Raw Tradier chain → `V2OptionContract` list
2. **Expiry narrow:** Filter by DTE window (family-default: 7–60 vertical/IC/butterfly, 7–90 calendar)
3. **Strike narrow:** Filter by distance from underlying (1–12% OTM default), option type, moneyness
4. **Package:** `V2NarrowedUniverse` with `V2NarrowingDiagnostics`

Multi-expiry (calendars/diagonals): Uses `narrow_expirations_multi()` to produce separate near/far contract lists, then strike-narrows each independently.

**Underlying price dependency:** `narrow_chain()` receives `underlying_price` as a parameter for strike-distance calculations. If underlying price is missing/zero, families that require it (iron_condors, butterflies) return empty candidate lists (`spot <= 0 → skip`). This is a silent failure path.

**⚠ Finding 1F-04 (MEDIUM): Silent skip on missing underlying price.**  
If `base_data_service.get_underlying_price(symbol)` returns `None`, the pipeline sets `underlying_price=None`, which becomes `price = 0.0` in `base_scanner.run()`. Family builders check `if spot <= 0: return []` and log a warning. No rejection code is emitted — the symbol simply produces zero candidates. This should be a tracked data-quality failure, not a silent skip.

### 2.5  Strategy Family Math (Phase E)

#### 2.5.1  Vertical Spreads (2-leg)

**Strategy IDs:** `put_credit_spread`, `call_credit_spread`, `put_debit`, `call_debit`  
**Math function:** `phases.py::_recompute_vertical_math()`

| Field | Formula | Inputs |
|---|---|---|
| width | `abs(short.strike - long.strike)` | Leg strikes |
| net_credit | `short.bid - long.ask` (if > 0) | Leg bid/ask |
| net_debit | `long.ask - short.bid` (if credit ≤ 0) | Leg bid/ask |
| max_profit (credit) | `net_credit × 100` | net_credit |
| max_loss (credit) | `(width - net_credit) × 100` | width, net_credit |
| max_profit (debit) | `(width - net_debit) × 100` | width, net_debit |
| max_loss (debit) | `net_debit × 100` | net_debit |
| POP | `1 - abs(short.delta)` | Tradier delta |
| EV | `POP × max_profit - (1-POP) × max_loss` | POP, max_profit, max_loss |
| ev_per_day | `EV / DTE` | EV, DTE |
| RoR | `max_profit / max_loss` | max_profit, max_loss |
| Kelly | `POP - (1-POP) / RoR` | POP, RoR |
| Breakeven (put credit) | `short.strike - net_credit` | strike, net_credit |
| Breakeven (call credit) | `short.strike + net_credit` | strike, net_credit |
| Breakeven (call debit) | `long.strike + net_debit` | strike, net_debit |
| Breakeven (put debit) | `long.strike - net_debit` | strike, net_debit |

All formulas verified against code. Correct for vertical spreads.

#### 2.5.2  Iron Condors (4-leg)

**Strategy ID:** `iron_condor`  
**Math function:** `families/iron_condors.py::family_math()`

| Field | Formula | Inputs |
|---|---|---|
| put_side_credit | `put_short.bid - put_long.ask` | Put leg bid/ask |
| call_side_credit | `call_short.bid - call_long.ask` | Call leg bid/ask |
| net_credit | `put_side_credit + call_side_credit` | Side credits |
| max_profit | `net_credit × 100` | net_credit |
| width | `max(put_width, call_width)` | Side widths |
| max_loss | `(width - net_credit) × 100` | width, net_credit |
| POP | `1 - abs(delta_put_short) - abs(delta_call_short)` | Tradier deltas |
| breakeven_low | `put_short.strike - net_credit` | strike, net_credit |
| breakeven_high | `call_short.strike + net_credit` | strike, net_credit |
| EV | `POP × max_profit - (1-POP) × max_loss` | POP, max_profit, max_loss |
| RoR | `max_profit / max_loss` | max_profit, max_loss |

All formulas verified. Correct for iron condors.

**Note:** `width = max(put_width, call_width)` assumes the "effective risk width" is the wider side. This is mathematically correct because max loss occurs when the wider wing is fully breached and the narrower wing's loss cannot exceed its own width.

#### 2.5.3  Debit Butterflies (3-leg)

**Strategy ID:** `butterfly_debit`  
**Math function:** `families/butterflies.py::_debit_butterfly_math()`

| Field | Formula | Inputs |
|---|---|---|
| width | `center.strike - lower.strike` | Leg strikes |
| net_debit | `ask(lower) + ask(upper) - 2 × bid(center)` | Leg bid/ask |
| max_profit | `(width - net_debit) × 100` | width, net_debit |
| max_loss | `net_debit × 100` | net_debit |
| breakeven_low | `lower.strike + net_debit` | strike, net_debit |
| breakeven_high | `upper.strike - net_debit` | strike, net_debit |
| POP (call) | `abs(Δ_lower) - abs(Δ_upper)` | Tradier deltas |
| POP (put) | `abs(Δ_upper) - abs(Δ_lower)` | Tradier deltas |
| EV | `POP × max_profit - (1-POP) × max_loss` | POP, max_profit, max_loss |
| RoR | `max_profit / max_loss` | max_profit, max_loss |
| Kelly | `POP - (1-POP) / RoR` | POP, RoR |

Verified. Validity constraint enforced: `0 < net_debit < width`.

**⚠ Finding 1F-05 (MEDIUM): Butterfly POP overestimates profit probability.**  
The delta approximation `|Δ_lower| - |Δ_upper|` measures P(lower < S_T < upper), which covers the *entire* strike range, not just the narrower profit zone near the center. The code notes this: `"full strike range, overestimates profit zone"`. For steep butterflies (wide wings), this overestimation can be material (e.g., 40%+ vs actual 15-20%). The POP field should carry a stronger caveat for downstream consumers. EV derived from this POP inherits the overestimation.

#### 2.5.4  Iron Butterflies (4-leg)

**Strategy ID:** `iron_butterfly`  
**Math function:** `families/butterflies.py::_iron_butterfly_math()`

| Field | Formula | Inputs |
|---|---|---|
| net_credit | `bid(ps) + bid(cs) - ask(pl) - ask(cl)` | Center straddle bid - wing ask |
| max_profit | `net_credit × 100` | net_credit |
| width | `center - lower` (equidistant wings) | Leg strikes |
| max_loss | `(width - net_credit) × 100` | width, net_credit |
| POP | `1 - abs(Δ_ps) - abs(Δ_cs)` | Tradier deltas |
| breakeven_low | `center - net_credit` | center strike, net_credit |
| breakeven_high | `center + net_credit` | center strike, net_credit |

Verified. Same structure as iron condor POP (both use 2-delta subtraction).

#### 2.5.5  Calendars & Diagonals (2-leg, multi-expiry)

**Strategy IDs:** `calendar_call_spread`, `calendar_put_spread`, `diagonal_call_spread`, `diagonal_put_spread`  
**Math function:** `families/calendars.py::family_math()`

| Field | Value | Note |
|---|---|---|
| net_debit | `far_leg.ask - near_leg.bid` | Trustworthy — directly from leg quotes |
| max_loss | `net_debit × 100` | Approximate — debit paid is max loss |
| max_profit | **None** | Path-dependent; depends on far-leg residual at near-leg expiration |
| breakeven | **None** | IV-term-structure dependent; no closed form |
| POP | **None** | Delta approximation does not work for time spreads |
| EV | **None** | Requires max_profit (unknown) |
| RoR | **None** | Requires max_profit (unknown) |

**This is the correct approach.** Setting path-dependent fields to `None` with explanatory notes is honest and follows the data-integrity principle: "null > incorrect numbers." The notes in `V2RecomputedMath.notes` explain exactly why each field is deferred.

### 2.6  POP Methodology Across Families

| Family | POP Formula | Source | Accuracy |
|---|---|---|---|
| Vertical Spreads | `1 - abs(short.delta)` | Tradier delta | Good for OTM credit spreads; weakens for ATM/ITM |
| Iron Condors | `1 - abs(Δ_ps) - abs(Δ_cs)` | Tradier deltas | Good for balanced condors; assumes independent wings |
| Debit Butterfly | `abs(Δ_lower) - abs(Δ_upper)` | Tradier deltas | Overestimates (covers full range, not profit zone) |
| Iron Butterfly | `1 - abs(Δ_ps) - abs(Δ_cs)` | Tradier deltas | Same as iron condor approach |
| Calendars | **None** | N/A | Correctly deferred |

**⚠ Finding 1F-06 (HIGH): POP is universally delta-approximated from Tradier greeks.**  
All computed POP values depend entirely on Tradier's delta values. If Tradier returns stale/missing deltas (e.g., after hours, low liquidity contracts), POP and all derived metrics (EV, Kelly) become unreliable. There is no fallback POP calculation (e.g., using a Black-Scholes model with IV). Phase D rejects contracts with missing bid/ask but does NOT reject contracts with missing delta — a contract can pass all hygiene checks and still have `POP = None` if delta is missing.

**Impact:** When delta is None, POP is None → EV is None → the candidate passes but has no EV for ranking. This is not a data integrity violation (None ≠ wrong), but it degrades selection quality since EV-less candidates can't be meaningfully compared.

### 2.7  Phase D Quote/Liquidity Hygiene

`phases.py::phase_d_quote_liquidity_sanity()` rejects candidates where:
- Any leg has `bid is None` or `ask is None`
- Any leg has `bid > ask` (inverted quote)
- Any leg has `open_interest is None` or `volume is None`

`phases.py::phase_d2_trust_hygiene()` applies:
- Spread sanity: `(ask - bid) / mid > threshold`
- Liquidity sanity: min OI / volume requirements
- Deduplication: identical candidates from overlapping expirations

**Correct:** Quote integrity validation happens BEFORE any math computation (Phase D before Phase E). This ensures Phase E math operates on validated inputs.

---

## 3  Cross-Cutting Concerns

### 3.1  Data Timing Coherence

| Scanner Type | Price Timing | Options Chain Timing | Risk |
|---|---|---|---|
| Stock scanners | Previous close (daily bars) | N/A | Low (daily timeframe) |
| Options V2 | N/A (underlying from Tradier quote) | Tradier real-time chain | Low-Medium |

**⚠ Finding 1F-07 (LOW): Options underlying price vs. chain quote timing.**  
`get_underlying_price(symbol)` and `get_analysis_inputs(symbol, exp)` are separate async calls in `_run_one()`. The underlying price fetch and chain fetch may be milliseconds to seconds apart. During volatile markets, the underlying price used for strike narrowing (Phase A) may not exactly match the price implied by the chain quotes used in Phase E math. This is a minor timing skew inherent to any multi-call API approach.

### 3.2  Missing-Field Policy Compliance

| System | Missing = None? | Missing = 0? | Compliant? |
|---|---|---|---|
| Chain normalization (`_safe_float`, `_safe_int`) | ✅ | ❌ | ✅ |
| Stock scanner metrics | ✅ (all computed metrics) | ❌ | ✅ |
| Phase E math | ✅ (all optional fields) | ❌ | ✅ |
| Calendar deferred fields | ✅ (explicit None) | ❌ | ✅ |

**All subsystems comply with the missing-field policy.** No cases of treating missing as 0 were found.

### 3.3  Rejection Code Taxonomy

Stock scanners use reason codes: `PRICE_TOO_LOW`, `LOW_LIQUIDITY`, `NO_EXPANSION`, `NO_PRIOR_COMPRESSION`, `NO_LONG_BIAS`, `TOO_VOLATILE`, and scanner-specific strategy codes.

Options V2 uses `v2_*` prefixed codes: `v2_missing_quotes`, `v2_inverted_quote`, `v2_low_oi`, `v2_missing_volume`, `v2_malformed_legs`, `v2_ic_invalid_geometry`, `v2_cal_invalid_geometry`, `v2_math_fail`, `v2_duplicate`, etc.

Both systems track rejection reasons per candidate. No silent drops detected in the options pipeline (every Phase C/D/E rejection appends a reason code).

---

## 4  Findings Summary

| ID | Severity | Component | Finding |
|---|---|---|---|
| 1F-01 | LOW | Stock scanners | `closes[-1]` as price reference may be stale during market hours (previous session close). Conservative but potentially misleading for real-time display. |
| 1F-02 | LOW | Stock scanners | `_BALANCED_UNIVERSE` and `_ETF_EXCLUSIONS` copy-pasted across 4 files. Maintenance drift risk. |
| 1F-03 | MEDIUM | Stock scanners | All scoring weights/thresholds hardcoded inline — no preset integration, no A/B configurability, no central weight reference. |
| 1F-04 | MEDIUM | Options V2 | Missing underlying price causes silent skip (zero candidates) instead of tracked data-quality failure. No rejection code emitted. |
| 1F-05 | MEDIUM | Options V2 (butterflies) | Butterfly POP overestimates profit probability by measuring full strike range instead of narrower profit zone. EV inherits this bias. |
| 1F-06 | HIGH | Options V2 (all families) | POP depends entirely on Tradier delta. No fallback when delta is missing/stale. Contracts with missing delta pass hygiene but produce POP=None, degrading ranking quality. Phase D does not gate on delta presence. |
| 1F-07 | LOW | Options V2 | Underlying price and chain data fetched in separate API calls — minor timing skew possible during volatile markets. |

### Severity Distribution

| Severity | Count |
|---|---|
| HIGH | 1 |
| MEDIUM | 3 |
| LOW | 3 |
| **Total** | **7** |

---

## 5  Per-Scanner Data Dependency Matrix

### 5.1  Stock Scanners — Input→Output Traceability

```
Tradier get_daily_bars(symbol, lookback_days)
  ├─ OHLCV arrays (closes, highs, lows, opens, volumes)
  │   ├─ RSI(14), RSI(2)          ← quant_analysis.rsi(closes, period)
  │   ├─ SMA(20/50/200)           ← quant_analysis.simple_moving_average(closes, window)
  │   ├─ ATR(14)                  ← inline _atr(highs, lows, closes, 14)
  │   ├─ Realized Vol(20d)        ← quant_analysis.realized_vol_annualized(closes[-21:])
  │   ├─ Z-score(20)              ← (price - SMA20) / std(closes[-20:])
  │   ├─ Distance metrics         ← (price - SMA_n) / SMA_n
  │   ├─ Return metrics           ← (closes[-1] - closes[-n-1]) / closes[-n-1]
  │   ├─ Drawdown metrics         ← (price - max(closes[-n:])) / max(closes[-n:])
  │   ├─ Volume metrics           ← avg(volumes[-20:]), spike ratios
  │   ├─ Bollinger % rank         ← rolling BB width percentile over 180 bars
  │   └─ 52-week range            ← max/min(closes[-252:])
  │
  └─ price = closes[-1]
      ├─ Filter gates (min_price, min_avg_dollar_vol)
      ├─ ATR% = atr_14 / price
      └─ Output "price" / "underlying_price" / "entry_reference"
```

### 5.2  Options V2 — Input→Output Traceability

```
OptionsScannerService._run_one(symbol)
  ├─ get_expirations(symbol)               → expiration list
  ├─ get_underlying_price(symbol)          → underlying_price (float | None)
  └─ get_analysis_inputs(symbol, exp) ×N   → merged option chain
      │
      ▼
  normalize_chain() [Phase A]
  ├─ Per contract: strike, bid, ask, mid, delta, gamma, theta, vega, OI, vol
  ├─ narrow_expirations(): DTE 7-90 filter
  └─ narrow_strikes(): distance 1-12% OTM filter using underlying_price
      │
      ▼
  construct_candidates() [Phase B — family-specific]
  ├─ Verticals: pair OTM put/call spreads from same expiry
  ├─ Iron Condors: cross-product put + call credit spreads
  ├─ Butterflies: symmetric triplets (debit) or center straddle + wings (iron)
  └─ Calendars: near/far expiry pairing, same strike (calendar) or shifted (diagonal)
      │
      ▼
  phase_c_structural_validation() [Phase C]
  ├─ Shared: leg count, same expiry (or different for calendars), width > 0
  └─ Family: strike ordering, side balance, geometry
      │
      ▼
  phase_d_quote_liquidity_sanity() + phase_d2_trust_hygiene() [Phase D/D2]
  ├─ Gate: bid/ask present, not inverted, OI/volume present
  ├─ Spread sanity, liquidity sanity
  └─ Deduplication
      │
      ▼
  phase_e_recomputed_math() [Phase E]
  ├─ Family-dispatched math (vertical default or family_math override)
  ├─ net_credit/debit from leg bid/ask
  ├─ max_profit, max_loss from net_credit/debit + width
  ├─ POP from Tradier delta (family-specific formula)
  ├─ EV = POP × max_profit - (1-POP) × max_loss
  └─ RoR, Kelly, breakevens
      │
      ▼
  phase_f_normalize() [Phase F]
  └─ passed=True/False, timestamps, scanner_version
```

---

## 6  Conclusion

The scanner subsystems demonstrate strong data-integrity practices:

1. **Source fidelity:** Tradier is the sole authoritative source for both stock bars and option chains/greeks. No mixing of non-authoritative data into trade-critical calculations.

2. **Missing-field policy:** Universally correct — all subsystems use None for missing data, never 0 sentinels.

3. **Formula traceability:** All math formulas in stock scanners and options Phase E are documented inline, use standard financial formulas, and are traceable from API inputs through to output fields.

4. **Point-in-time correctness:** No forward-looking bias detected. Stock scanners use backward-looking daily bars. Options pipeline uses current-snapshot chain data.

5. **Honest uncertainty:** Calendar/diagonal spreads correctly set path-dependent fields to None rather than fabricating values. Butterfly POP notes its overestimation (though the caveat could be stronger).

The most significant finding is **1F-06**: the complete dependency on Tradier delta for POP across all options families, with no fallback or delta-presence gate. This creates a single point of failure for EV-based ranking quality.

---

*This is audit 1F, the final audit in Pass 1 (Data Integrity). All six Pass 1 audits are now complete:*
- *1A: Tradier Ingestion*
- *1B: FRED Ingestion*
- *1C: Proxy Inventory*
- *1D: Market Context Freshness*
- *1E: Engine Input Assembly*
- *1F: Scanner Data Dependencies*
