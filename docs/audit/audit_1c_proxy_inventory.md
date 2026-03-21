# Audit 1C — Derived & Proxy Metrics Inventory

> Generated: 2026-03-20  
> Scope: Every metric in BenTrade that is NOT a direct market observation  
> Classification: DIRECT → DERIVED → PROXY → PROXY-OF-PROXY

---

## Table of Contents

1. [Classification Definitions](#1-classification-definitions)
2. [Flows & Positioning Engine — 100% Proxy](#2-flows--positioning-engine--100-proxy)
3. [Volatility Engine — Term Structure & Rank Proxies](#3-volatility-engine--term-structure--rank-proxies)
4. [Cross-Asset Macro Engine — Commodity & Dollar Proxies](#4-cross-asset-macro-engine--commodity--dollar-proxies)
5. [Liquidity Engine — FCI & Funding Stress Proxies](#5-liquidity-engine--fci--funding-stress-proxies)
6. [Breadth Engine — Fully Derived (No Proxies)](#6-breadth-engine--fully-derived-no-proxies)
7. [News Sentiment Engine — Keyword & Macro Heuristics](#7-news-sentiment-engine--keyword--macro-heuristics)
8. [Market Context Service — Derived Metrics](#8-market-context-service--derived-metrics)
9. [Regime Service — Score-of-Scores & Block Synthesis](#9-regime-service--score-of-scores--block-synthesis)
10. [Options Scanner (quant_analysis.py) — Derived Math](#10-options-scanner-quant_analysispy--derived-math)
11. [Stock Scanners — Derived Technical Indicators](#11-stock-scanners--derived-technical-indicators)
12. [SIGNAL_PROVENANCE Coverage Gap](#12-signal_provenance-coverage-gap)
13. [Confidence Penalty Analysis](#13-confidence-penalty-analysis)
14. [Summary Table](#14-summary-table)
15. [Critical Flags](#15-critical-flags)

---

## 1. Classification Definitions

| Classification | Definition | Example |
|---|---|---|
| **DIRECT** | Value comes straight from a market data feed | VIX spot price, SPY quote, 10Y yield |
| **DERIVED** | Computed from direct observations using standard methodology | RSI, SMA, ATR, yield curve spread (10Y-2Y) |
| **PROXY** | Estimated/inferred from loosely related data | VIX-derived put/call ratio, FCI proxy from VIX+credit |
| **PROXY-OF-PROXY** | Derived from another proxy | Score computed from VIX-derived positioning estimate, follow_through from flow_direction_proxy |

---

## 2. Flows & Positioning Engine — 100% Proxy

**Source**: `flows_positioning_data_provider.py` lines 92–210  
**Upstream input**: VIX (DIRECT) — single input for ALL 12 proxy metrics  
**SIGNAL_PROVENANCE**: YES — `flows_positioning_engine.py` lines 72–130

The Flows & Positioning engine is documented as "Phase 1 — 100% proxy" in its module docstring (line 12–18). Every positioning and flow metric is a linear/clamped transformation of VIX.

### 2.1 Proxy Metrics (all from VIX)

#### `put_call_proxy` → dict key `"put_call_ratio"`
- **Claims to represent**: Equity put/call ratio (protective buying activity)
- **Actual computation** (line 104):
  ```python
  put_call_proxy = round(0.45 + vix * 0.023, 3)
  # VIX 12 → 0.73,  VIX 18 → 0.86,  VIX 25 → 1.03,  VIX 35 → 1.26
  ```
- **Data source**: VIX (DIRECT)
- **Classification**: **PROXY** — no actual exchange put/call volume data
- **Consumed by**: Flows Pillar 1 (positioning_pressure), Pillar 2 (crowding_stretch)
- **Labeled as proxy?**: YES — SIGNAL_PROVENANCE marks `put_call_ratio` as `type: "proxy"`
- **Confidence penalty**: YES — `-5` for "No direct institutional flow data" (line 1206), `-8` for heavy proxy reliance (line 1192)

#### `systematic_proxy` → dict key `"systematic_allocation"`
- **Claims to represent**: CTA/vol-control/risk-parity allocation level
- **Actual computation** (line 111):
  ```python
  systematic_proxy = round(max(5, min(95, 110 - vix * 2.5)), 1)
  # VIX 10 → 85,  VIX 20 → 60,  VIX 30 → 35,  VIX 40 → 10
  ```
- **Data source**: VIX (DIRECT)
- **Classification**: **PROXY** — no CTA positioning data, no risk-parity allocation data
- **Consumed by**: Flows Pillar 1 (positioning_pressure)
- **Labeled as proxy?**: YES — SIGNAL_PROVENANCE `systematic_flow_proxy` type: "proxy"
- **Confidence penalty**: Included in aggregate proxy count penalty

#### `futures_proxy` → dict key `"futures_net_long_pct"`
- **Claims to represent**: Net long futures positioning (institutional)
- **Actual computation** (line 119):
  ```python
  futures_proxy = round(max(10, min(90, 100 - vix * 2.2)), 1)
  # VIX 10 → 78,  VIX 20 → 56,  VIX 30 → 34,  VIX 40 → 12
  ```
- **Data source**: VIX (DIRECT)
- **Classification**: **PROXY** — no CFTC COT data
- **Consumed by**: Flows Pillars 1, 2, 3 (positioning, crowding, squeeze)
- **Labeled as proxy?**: YES — SIGNAL_PROVENANCE `futures_positioning_proxy` type: "proxy"
- **Confidence penalty**: YES — `-5` for "No direct futures positioning data" (line 1212)

#### `short_interest_proxy` → dict key `"short_interest_pct"`
- **Claims to represent**: Short interest as % of float
- **Actual computation** (line 125):
  ```python
  short_interest_proxy = round(max(0.8, min(6.0, 0.1 + vix * 0.12)), 2)
  # VIX 10 → 1.3%,  VIX 20 → 2.5%,  VIX 30 → 3.7%,  VIX 40 → 4.9%
  ```
- **Data source**: VIX (DIRECT)
- **Classification**: **PROXY** — no exchange-reported short interest
- **Consumed by**: Flows Pillars 2, 3 (crowding, squeeze)
- **Labeled as proxy?**: YES — SIGNAL_PROVENANCE type: "proxy"

#### `retail_bull_proxy` / `retail_bear_proxy`
- **Claims to represent**: AAII-style retail sentiment survey percentages
- **Actual computation** (lines 133, 136):
  ```python
  retail_bull_proxy = round(max(15, min(60, 65 - vix * 1.1)), 1)
  retail_bear_proxy = round(max(15, min(55, 10 + vix * 1.05)), 1)
  ```
- **Data source**: VIX (DIRECT)
- **Classification**: **PROXY** — no AAII survey data
- **Consumed by**: Flows Pillars 1, 2 (positioning, crowding)
- **Labeled as proxy?**: YES — SIGNAL_PROVENANCE `retail_sentiment` type: "proxy"

#### `flow_direction_proxy` → dict key `"flow_direction_score"`
- **Claims to represent**: Net ETF fund flow direction (inflow vs outflow)
- **Actual computation** (line 142):
  ```python
  flow_direction_proxy = round(max(15, min(85, 90 - vix * 1.8)), 1)
  # 50 = neutral, >50 = inflow, <50 = outflow
  ```
- **Data source**: VIX (DIRECT)
- **Classification**: **PROXY** — no ETF fund flow data
- **Consumed by**: Flows Pillar 4 (flow_direction_persistence), Pillar 5 (stability)

#### `flow_persistence_5d` / `flow_persistence_20d`
- **Claims to represent**: Consistency of fund flows over 5 and 20 trading days
- **Actual computation** (lines 148–149):
  ```python
  flow_persistence_5d = round(max(20, min(85, 95 - vix * 2.2)), 1)
  flow_persistence_20d = round(max(15, min(80, 88 - vix * 2.0)), 1)
  ```
- **Classification**: **PROXY**

#### `flow_volatility_proxy`
- **Claims to represent**: Instability/volatility of fund flows
- **Actual computation** (line 151):
  ```python
  flow_volatility_proxy = round(max(10, min(90, vix * 2.5 - 10)), 1)
  ```
- **Classification**: **PROXY**

#### `inflow_balance_proxy`
- **Claims to represent**: Inflow/outflow balance
- **Actual computation** (line 156):
  ```python
  inflow_balance_proxy = flow_direction_proxy  # Alias
  ```
- **Classification**: **PROXY** (alias of another proxy)

#### `follow_through_proxy` → dict key `"follow_through_score"`
- **Claims to represent**: Sustainability score for flow continuation
- **Actual computation** (line 160):
  ```python
  follow_through_proxy = round(max(20, min(80, flow_direction_proxy * 0.85 + 8)), 1)
  ```
- **Data source**: `flow_direction_proxy` (PROXY, itself from VIX)
- **Classification**: **PROXY-OF-PROXY** — derived from a proxy, not from market data
- **Consumed by**: Flows Pillar 4

### 2.2 Dict Key Laundering

When proxy values are assembled into pillar input dicts (lines 164–200), they are assigned neutral-sounding keys that obscure their proxy nature:

```python
positioning_data = {
    "put_call_ratio": put_call_proxy,         # ← looks like real p/c ratio
    "vix": vix,
    "retail_bull_pct": retail_bull_proxy,      # ← looks like real AAII data
    "systematic_allocation": systematic_proxy, # ← looks like real CTA data
    "futures_net_long_pct": futures_proxy,     # ← looks like real CFTC data
}
```

**Mitigation**: The engine's `SIGNAL_PROVENANCE` dict (lines 72–130) explicitly tags each as `type: "proxy"`, and the `source_meta` dict (lines 232–246) embeds `has_direct_flow_data: False`, `has_futures_positioning: False`, and `unique_upstream_count: 1`.

### 2.3 Single-Source Dependency

All 12 proxy metrics are deterministic functions of VIX alone. The data provider documents this:
```python
"unique_upstream_count": 1,  # Phase 1: VIX is the only upstream source
```

This means the engine's 5 pillars, 24+ submetrics, and composite score are ALL variations on the same VIX input. They cannot detect genuine divergence between positioning, flows, and crowding.

---

## 3. Volatility Engine — Term Structure & Rank Proxies

**Source**: `volatility_options_data_provider.py`  
**SIGNAL_PROVENANCE**: **NO** — the volatility engine has NO SIGNAL_PROVENANCE dict.

### 3.1 `vix_rank_30d` (PROXY)

- **Claims to represent**: IV Rank over 30 days (position of current IV within recent range)
- **What it actually is**: VIX INDEX level rank — computed from VIX closing levels, NOT from option IV history
- **Computation** (via `_fetch_vix_history()`):
  ```python
  vix_rank_30d = (vix_spot - min_30d) / (max_30d - min_30d) * 100
  ```
- **Data source**: FRED VIXCLS history (DIRECT observation of VIX index, not option IV)
- **Classification**: **PROXY** — VIX rank ≠ true IV rank. VIX is a single index; true IV rank would use the stock/ETF's actual option IV history.
- **Code label**: Comment says `# PROXY renamed from iv_rank_30d` (line 242) — acknowledges the proxy nature
- **Consumed by**: Volatility Pillar 1 (regime) → `vix_rank_30d_score`, option_richness computation, premium_bias computation
- **Labeled as proxy downstream?**: NO — the engine's scoring functions receive it as a float with no proxy metadata. The `"vix_rank_30d"` comment in the data provider is the only label.
- **Confidence penalty**: NO specific proxy penalty — the vol engine's `_compute_confidence()` (line 975–1018) only penalizes missing data and cross-pillar disagreement, NOT proxy reliance

### 3.2 `vix_percentile_1y` (PROXY)

- **Claims to represent**: IV percentile over 1 year
- **What it actually is**: VIX INDEX percentile ranking
- **Computation**:
  ```python
  vix_percentile_1y = count(history < current) / total * 100
  ```
- **Classification**: **PROXY** — same issue as vix_rank_30d
- **Consumed by**: Vol Pillar 1 → `vix_percentile_1y_score`
- **Labeled as proxy downstream?**: NO
- **Confidence penalty**: None

### 3.3 `vix_2nd_month` / `vix_3rd_month` — Term Structure Inference (PROXY)

- **Claims to represent**: VIX 2nd and 3rd month futures prices (VIX term structure)
- **What it actually is**: Deterministic heuristic from spot/average ratio
- **Computation** (lines 253–263):
  ```python
  ratio = vix_spot / vix_avg_20d
  if ratio < 1.0:
      # VIX below average → contango likely
      vix_2nd = vix_avg_20d
      vix_3rd = vix_avg_20d * 1.03
  else:
      # VIX above average → backwardation possible
      vix_2nd = vix_spot * 0.97
      vix_3rd = vix_spot * 0.95
  ```
- **Data source**: VIX spot (DIRECT) + vix_avg_20d (DERIVED from VIXCLS history)
- **Classification**: **PROXY** — no VIX futures data from Tradier or any source. The term structure shape is a fabricated heuristic, not market-observed.
- **Consumed by**: Vol Pillar 2 (structure) → `term_structure_shape`, `contango_steepness`
- **Labeled as proxy downstream?**: There's a code comment (`"VIX futures are not directly available via Tradier"`, line 249) but the scoring engine receives `vix_2nd_month` as a plain float with no proxy flag.
- **Confidence penalty**: None
- **Integrity risk**: HIGH — downstream scoring treats these as if they were real futures prices. Contango/backwardation classification is reliable only as direction hint, not magnitude.

### 3.4 `option_richness` / `option_richness_label` (DERIVED from PROXY inputs)

- **Claims to represent**: Whether options are expensive (Rich), neutral (Fair), or cheap (Cheap)
- **Actual computation** (lines 195–222):
  ```python
  if is_high_rank and is_iv_high:      # vix_rank_30d > 60 AND iv_30d > rv_30d
      option_richness_label = "Rich"    # → 75
  elif is_low_rank or is_iv_low:        # vix_rank_30d < 30 OR iv_30d <= rv_30d
      option_richness_label = "Cheap"   # → 25
  else:
      option_richness_label = "Fair"    # → 50
  ```
- **Data source**: `vix_rank_30d` (PROXY) + `iv_30d` (DIRECT from SPY options) + `rv_30d` (DERIVED from SPY closes)
- **Classification**: **PROXY-OF-PROXY** — depends on vix_rank_30d which is itself a proxy
- **Consumed by**: Vol Pillar 4 (positioning) → `option_richness_score`
- **Labeled as proxy downstream?**: NO
- **Confidence penalty**: None

### 3.5 `premium_bias` (DERIVED from PROXY inputs)

- **Claims to represent**: Directional bias for option selling vs buying
- **Actual computation** (lines 224–233):
  ```python
  # If not provided by spy_iv, compute:
  vrp = iv_30d - rv_30d
  bias_components = [
      min(max(vrp * 5, -50), 50),           # IV-RV spread contribution
      (vix_rank_30d - 50) * 0.8,             # VIX rank contribution (PROXY)
      (0.85 - eq_pc) * 40,                   # P/C ratio contribution
  ]
  premium_bias = sum(bias_components) / len(bias_components)
  ```
- **Data source**: `iv_30d` (DIRECT), `rv_30d` (DERIVED), `vix_rank_30d` (PROXY), `equity_pc_ratio` (DIRECT from SPY)
- **Classification**: **PROXY-OF-PROXY** — includes vix_rank_30d (proxy) as 1/3 of the formula
- **Consumed by**: Vol Pillar 4 → `premium_bias_score`
- **Labeled as proxy downstream?**: NO
- **Confidence penalty**: None

### 3.6 `tail_risk_numeric` / `tail_risk_signal` (DERIVED)

- **Claims to represent**: Tail risk level from skew data
- **Computation** (lines 157–185):
  ```python
  skew_components = []
  if put_skew_25d is not None:
      skew_components.append(_interpolate(put_skew_25d, -2.0, 5.0, 20.0, 85.0))
  if cboe_skew is not None:
      skew_components.append(_interpolate(cboe_skew, 110.0, 160.0, 15.0, 90.0))
  tail_risk_numeric = sum(skew_components) / len(skew_components)
  # → "Low"|"Moderate"|"Elevated"|"High" from thresholds
  ```
- **Data source**: `put_skew_25d` (DIRECT from SPY options), `cboe_skew` (DIRECT from FRED SKEW index)
- **Classification**: **DERIVED** — standard normalization of direct observations
- **Labeled as proxy?**: N/A (correctly derived)

### 3.7 `rv_30d` (DERIVED)

- **Claims to represent**: 30-day realized volatility (close-to-close, annualized)
- **Computation**:
  ```python
  log_returns = [math.log(prices[i] / prices[i-1]) for i in range(1, len(prices))]
  std_dev = statistics.stdev(log_returns)
  rv_30d = std_dev * math.sqrt(252) * 100
  ```
- **Data source**: SPY daily closes from Tradier (DIRECT)
- **Classification**: **DERIVED** — standard methodology, correct annualization

---

## 4. Cross-Asset Macro Engine — Commodity & Dollar Proxies

**Source**: `cross_asset_macro_data_provider.py`, `cross_asset_macro_engine.py`  
**SIGNAL_PROVENANCE**: YES — `cross_asset_macro_engine.py` lines 83–173

### 4.1 `usd_index` (PROXY)

- **Claims to represent**: US Dollar Index (DXY)
- **What it actually is**: FRED DTWEXBGS — Trade-Weighted US Dollar Index, Broad
- **Classification**: **PROXY** — DTWEXBGS is directionally similar to DXY but tracks different basket and weightings
- **SIGNAL_PROVENANCE**: YES — marked `type: "proxy"`, notes: "Trade-Weighted US Dollar Index (Broad). Proxy for DXY; directionally similar but not identical."
- **Consumed by**: Cross-Asset Pillar 2 (dollar_commodity), Liquidity Pillar 4 (dollar/global)
- **Confidence penalty**: No specific penalty for this proxy

### 4.2 `gold_price` (PROXY)

- **Claims to represent**: Gold spot price
- **What it actually is**: FRED NASDAQQGLDI — NASDAQ Gold FLOWS103 Price Index
- **Classification**: **PROXY** — an index tracking LBMA gold price, not direct gold spot
- **SIGNAL_PROVENANCE**: YES — marked `type: "proxy"`, daily frequency
- **Consumed by**: Cross-Asset Pillar 2 → `gold_price_score`

### 4.3 `copper_price` (PROXY — MONTHLY lag)

- **Claims to represent**: Global copper price for growth signal
- **What it actually is**: FRED PCOPPUSDM — Global Price of Copper, MONTHLY average, USD/metric ton
- **Classification**: **PROXY** (with severe staleness)
- **SIGNAL_PROVENANCE**: YES — marked `type: "proxy"`, delay: "monthly (significant lag for daily confirmation)", notes: "monthly series is a SLOW proxy"
- **Consumed by**: Cross-Asset Pillar 2 → `copper_price_score`
- **Staleness detection**: `_days_stale()` helper checks age, BUT only logs a warning; score is still used regardless of staleness.
- **Confidence penalty**: SIGNAL_PROVENANCE notes say "Confidence is reduced when this is the only growth signal available" but there is NO code implementing this reduction.

### 4.4 `yield_curve_spread` (DERIVED)

- **Claims to represent**: 10Y - 2Y yield spread
- **Computation**: `ten_year_yield - two_year_yield`
- **SIGNAL_PROVENANCE**: YES — marked `type: "derived"`, formula documented
- **Classification**: **DERIVED** — standard methodology
- **Integrity note**: No cross-series date alignment (see Audit 1B Finding #3)

### 4.5 Pillar Composite Scores (DERIVED)

Each pillar produces a weighted average of its submetrics. The overall composite:
```python
CrossAssetComposite = 0.25×rates + 0.20×dollar_commodity + 0.25×credit + 0.15×defensive + 0.15×coherence
```
- **Classification**: **DERIVED** — standard weighted aggregation
- All pillar weights are hardcoded constants (not configurable externally)

---

## 5. Liquidity Engine — FCI & Funding Stress Proxies

**Source**: `liquidity_conditions_engine.py`  
**SIGNAL_PROVENANCE**: YES — lines 54–120

### 5.1 `fci_proxy` — Financial Conditions Proxy (PROXY)

- **Claims to represent**: Financial Conditions Index (like Chicago Fed NFCI)
- **Actual computation** (lines 478–520):
  ```python
  fci_inputs = []
  if vix is not None:
      fci_inputs.append(_interpolate(vix, 12, 35, 100, 0))     # VIX contribution
  if ig_spread is not None:
      fci_inputs.append(_interpolate(ig_spread, 0.6, 2.5, 100, 0))  # IG OAS contribution
  if two_y is not None:
      fci_inputs.append(_interpolate(two_y, 1.0, 5.5, 95, 10))      # 2Y rate contribution
  fci_score = sum(fci_inputs) / len(fci_inputs)
  ```
- **Data source**: VIX (DIRECT), IG spread (DIRECT from FRED BAMLC0A0CM), 2Y yield (DIRECT from FRED DGS2)
- **Classification**: **PROXY** — simple average of 3 normalized direct observations, NOT a true factor-model FCI
- **SIGNAL_PROVENANCE**: YES — `type: "proxy"`, notes: "Composite proxy for broad financial conditions. Not a true FCI index."
- **Submetric status**: Explicitly set to `"status": "proxy"` in submetric output (line 500)
- **Consumed by**: Liquidity Pillar 2 (financial_conditions_tightness) with 30% weight
- **Confidence penalty**: No specific penalty; the proxy nature is documented but doesn't reduce the confidence score

### 5.2 `funding_stress_proxy` (PROXY)

- **Claims to represent**: Interbank funding stress (like SOFR-OIS or FRA-OIS spread)
- **SIGNAL_PROVENANCE**: YES — `type: "proxy"`, notes: "Proxy for funding stress. True SOFR/FRA-OIS not yet integrated."
- **Consumed by**: Liquidity Pillar 3 (credit_funding_stress)
- **Classification**: **PROXY** — no direct interbank funding market data

### 5.3 `policy_pressure_proxy` (PROXY)

- **Claims to represent**: Fed policy pressure on markets
- **Computation**: Fed funds rate vs neutral rate estimate (2.5%), combined with 2Y yield direction
- **Classification**: **PROXY** — "neutral rate" is an assumption, not observed
- **Consumed by**: Liquidity Pillar 1 (rates_policy_pressure) with 20% weight

### 5.4 `vix_conditions_proxy` (PROXY)

- **Claims to represent**: Financial conditions tightness from volatility perspective
- **Computation** (line 525):
  ```python
  vix_cond_score = _interpolate(vix, 12, 35, 90, 10)  # High VIX = tight
  ```
- **Data source**: VIX (DIRECT)
- **Classification**: **PROXY** — VIX level is loosely correlated with financial conditions, not a measurement of them
- **SIGNAL_PROVENANCE**: YES — `type: "proxy"`, notes: "VIX as financial conditions proxy"
- **Consumed by**: Liquidity Pillar 2 with 25% weight

### 5.5 VIX Double-Counting Note

The engine documents intentional management of VIX across submetrics:
```python
# NOTE: VIX contributes to fci_proxy (as 1/3 of composite) and
# vix_conditions_signal only. Other submetrics intentionally exclude
# VIX to avoid double-counting across pillars.
```
(line 451–454)

This is good transparency, though VIX still appears in 2 of 4 submetrics in Pillar 2.

---

## 6. Breadth Engine — Fully Derived (No Proxies)

**Source**: `breadth_engine.py`, `breadth_data_provider.py`  
**SIGNAL_PROVENANCE**: **NO** — breadth engine has no SIGNAL_PROVENANCE dict.

All breadth metrics are computed from Tradier bulk quote and price data (DIRECT observations). No proxies are used.

### 6.1 Key Derived Metrics

| Metric | Formula | Classification |
|---|---|---|
| `advance_decline_ratio` | `advancing / max(declining, 1)` | DERIVED |
| `net_advances_pct` | `(advancing - declining) / total_valid` | DERIVED |
| `pct_above_20dma` | `count(close > SMA20) / total` | DERIVED |
| `pct_above_50dma` | `count(close > SMA50) / total` | DERIVED |
| `pct_above_200dma` | `count(close > SMA200) / total` | DERIVED |
| `bullish_volume_pct` | `sum(vol where up) / total_vol` | DERIVED |
| `ew_vs_cw_relative_perf` | EW return / CW return ratio | DERIVED |
| `equal_weight_confirmation` | `1 - clamp(\|ew - cw\|/\|cw\|, 0, 2)/2` | DERIVED |
| `stability_5d` / `stability_20d` | Std dev of breadth scores inverted | DERIVED |

All inputs are Tradier bulk quotes and daily bars for the SP500_PROXY universe (~150 tickers). Standard, well-defined computations.

### 6.2 Universe as Implicit Proxy

While the breadth metrics themselves are DERIVED, the SP500_PROXY universe of ~150 curated tickers is a **proxy for true S&P 500 breadth** (500 constituents). This is not documented as a proxy in the engine output. The gap matters: 150 stocks may not capture the same breadth dynamics as the full 500.

---

## 7. News Sentiment Engine — Keyword & Macro Heuristics

**Source**: `news_sentiment_engine.py`  
**SIGNAL_PROVENANCE**: **NO** — news engine has no SIGNAL_PROVENANCE dict.

### 7.1 `headline_sentiment` — Keyword Scoring (PROXY-like)

- **Claims to represent**: Market sentiment from news headlines
- **Computation** (lines 196–216):
  ```python
  def _score_text_sentiment(text):
      # Count bullish vs bearish keyword matches
      # 20 bullish words, 27 bearish words (hardcoded frozensets)
      # Return -1..+1 → normalize to 0-100
  ```
- **Classification**: **PROXY** — keyword matching is a crude approximation of sentiment. No NLP model, no entity resolution, no context awareness.
- **Labeled as proxy?**: NO
- **Consumed by**: News composite score with 30% weight (highest single weight)

### 7.2 `negative_pressure` (DERIVED from PROXY)

- **Computation**: `bearish_count / (bullish + bearish)` using keyword classification
- **Classification**: **PROXY-OF-PROXY** — depends on the keyword-based bull/bear classification
- **Weight**: 20% of news composite

### 7.3 `narrative_severity` (PROXY)

- **Claims to represent**: Severity of detected narrative themes
- **Computation**: Category-weighted penalties (geopolitical/fed/macro = 2.0, commodities = 1.0, others = 0.5)
- **Classification**: **PROXY** — category assignment from news items may be unreliable
- **Weight**: 15%

### 7.4 `source_agreement` (DERIVED)

- **Computation**: Std dev of per-source sentiment means
- **Classification**: **PROXY-OF-PROXY** — agreement measured on keyword-derived sentiment scores
- **Weight**: 10%

### 7.5 `macro_stress` (DERIVED from DIRECT)

- **Claims to represent**: Macro stress from economic indicators
- **Computation** (lines 323–360):
  ```python
  vix_stress = _interpolate(vix, [10, 40], [90, 10])
  curve_stress = _interpolate(curve_spread, [-1, 2], [80, 10])
  # Average of available stress inputs
  ```
- **Data source**: VIX (DIRECT), yield_curve_spread (DERIVED), 10Y yield (DIRECT)
- **Classification**: **DERIVED** — normalized direct observations
- **Weight**: 15%

### 7.6 `recency_pressure` (DERIVED from PROXY)

- **Computation**: Time-decay weighted sentiment using keyword scores
- **Classification**: **PROXY-OF-PROXY** — time-decay applied to keyword-based sentiment
- **Weight**: 10%

---

## 8. Market Context Service — Derived Metrics

**Source**: `market_context_service.py`

### 8.1 `yield_spread` (DERIVED)

- **Computation** (line 224):
  ```python
  yield_spread = ten_year_yield - two_year_yield
  ```
- **Classification**: **DERIVED** — standard methodology
- **Integrity note**: No date alignment between DGS10 and DGS2 (could be from different observation dates)
- **Source tag**: `"source": "derived (10Y-2Y)"` — correctly labeled

### 8.2 `cpi_yoy` (DERIVED)

- **Computation** (line 270):
  ```python
  cpi_yoy = (CPIAUCSL[0] / CPIAUCSL[12]) - 1.0
  ```
- **Classification**: **DERIVED** — standard YoY methodology
- **Integrity note**: No contiguity check on 13 observations; no verification that obs[12] is actually 12 months prior

### 8.3 VIX Fallback Chain

The VIX value uses a 3-tier fallback:
1. Tradier (DIRECT — real-time)
2. Finnhub (DIRECT — near-real-time)
3. FRED VIXCLS (DIRECT — EOD+1 lag)

All three are direct observations of the same index, but the freshness degrades dramatically at each fallback tier. The metric envelope's `source` field tracks which tier was used, which is correct behavior.

---

## 9. Regime Service — Score-of-Scores & Block Synthesis

**Source**: `regime_service.py`

The regime service is the **highest-level score-of-scores** in the system. It combines all 6 MI engine outputs plus raw FRED data into a single regime label.

### 9.1 Architecture: Three Blocks

```
Structural Block (30%):
  - liquidity_financial_conditions MI engine (35%)
  - cross_asset_macro MI engine (35%)
  - rates_regime from FRED DGS10 (15%)     ← documented as "temporary proxy"
  - volatility_structure from FRED VIX (15%) ← documented as "temporary proxy"

Tape Block (40%):
  - breadth_participation MI engine (40%)
  - index trend scores from Tradier prices (25%)  ← DERIVED
  - index momentum from RSI (20%)                  ← DERIVED
  - small-cap confirmation (15%)                   ← DERIVED

Tactical Block (30%):
  - volatility_options MI engine (30%)
  - flows_positioning MI engine (25%)
  - news_sentiment MI engine (25%)
  - sector breadth from Tradier (20%)              ← DERIVED
```

### 9.2 Block Composite Formula (Score-of-Scores)

```python
regime_score = Σ(block_score × block_weight) / Σ(available_weights)
# block_weights: structural=0.30, tape=0.40, tactical=0.30
```
- **Classification**: **PROXY-OF-PROXY** — the regime score is a weighted average of block scores, which are themselves weighted averages of engine scores, which are themselves weighted averages of pillar scores, which are weighted averages of submetric scores.
- **Depth of derivation chain**: Market data → submetric → pillar → engine composite → block → regime. **5 levels deep.**

### 9.3 Block-Level Proxy Labels

The structural block correctly labels its raw-data components as temporary proxies:
```python
pillar_detail["rates_regime"] = {
    "proxy": "FRED DGS10 — temporary proxy for full rates complex",
}
pillar_detail["volatility_structure"] = {
    "proxy": "FRED VIXCLS — temporary proxy for vol term structure",
}
```

### 9.4 Confidence in Synthesis

```python
base_confidence = coverage * 0.85  # 0-0.85 from data coverage (3 blocks max)
conflict_penalty = min(0.30, excess / 100.0)  # if block spread > threshold
confidence = base_confidence - conflict_penalty  # clamped [0.1, 0.95]
```

The regime confidence does NOT account for how many proxy inputs fed the block scores. A regime score derived from flows_positioning (100% proxy) gets the same weight as one derived from breadth (100% direct data).

### 9.5 Index Trend Score (DERIVED)

- **Computation** (lines 430–464):
  ```python
  # For each index (SPY, QQQ, IWM, DIA):
  i_points += 10 if last > ema20     # EMA20 above check
  i_points += 5  if last > ema50     # EMA50 above check
  i_points += 10 if sma50 > sma200   # Golden cross check
  idx_score = (i_points / i_avail) * 100
  ```
- **Classification**: **DERIVED** — standard technical analysis from price data

### 9.6 Momentum Score (DERIVED)

- **Computation** (lines 482–497):
  ```python
  if 45 <= avg_rsi <= 65:
      momentum_score = 100.0  # Ideal band
  else:
      distance = min(abs(avg_rsi - 45), abs(avg_rsi - 65))
      momentum_score = max(0, 1 - min(distance, 25) / 25) * 100
  ```
- **Classification**: **DERIVED** — standard RSI interpretation

### 9.7 Small-Cap Confirmation (DERIVED)

- **Computation** (line 506):
  ```python
  smallcap_score = 50 + (iwm_score - avg_largecap) * 0.5
  ```
- **Classification**: **DERIVED** — relative performance comparison

### 9.8 VIX Delta / 10Y Delta (DERIVED)

- **Computation** (lines 293–306):
  ```python
  vix_5d_change = (vix_recent[0] - vix_recent[5]) / vix_recent[5]
  ten_year_delta_bps = (ten_year_recent[0] - ten_year_recent[5]) * 100
  ```
- **Data source**: FRED VIXCLS (6 observations), FRED DGS10 (6 observations)
- **Classification**: **DERIVED**
- **Integrity note**: "5-day" is actually 6 observations which may span weekends/holidays — not necessarily 5 trading days

---

## 10. Options Scanner (quant_analysis.py) — Derived Math

**Source**: `common/quant_analysis.py`

### 10.1 POP — Probability of Profit (DERIVED)

- **Formula** (line ~218):
  ```python
  POP = 1 - abs(delta_short)
  ```
- **Data source**: `short_delta_abs` from Tradier options chain (DIRECT)
- **Classification**: **DERIVED** — standard delta-based POP approximation
- **Assumption**: Delta accurately represents probability. This is approximately true for liquid options near ATM but less accurate for deep OTM or in volatile environments.

### 10.2 Expected Value Per Share (DERIVED)

- **Formula** (lines 255–268):
  ```python
  EV = (p_win × max_profit) - ((1 - p_win) × max_loss)
  ```
- **Data source**: `p_win` (from POP via delta), strike prices, net_credit
- **Classification**: **DERIVED** — standard EV formula
- **Assumption**: Binary outcome model (full max profit or full max loss). Ignores partial outcomes.

### 10.3 Return on Risk (DERIVED)

- **Formula** (line ~180):
  ```python
  RoR = net_credit / (width - net_credit)
  ```
- **Classification**: **DERIVED** — direct arithmetic from trade parameters

### 10.4 Kelly Fraction (DERIVED)

- **Formula** (lines 287–309):
  ```python
  f* = (b × p - q) / b
  # where b = max_profit/max_loss, p = p_win, q = 1-p
  ```
- **Classification**: **DERIVED** — standard Kelly criterion
- **Depends on**: POP (DERIVED from DIRECT delta)

### 10.5 Trade Quality Score (DERIVED from PROXY-like inputs)

- **Formula** (lines 320–345):
  ```python
  score = 0.4 × POP + 0.3 × min(RoR/0.5, 1.0) + 0.3 × iv_rank
  ```
- **`iv_rank`**: If supplied from VIX rank (PROXY), this makes the composite score partly proxy-dependent
- **Classification**: **DERIVED** if iv_rank is from actual option IV; **PROXY-OF-PROXY** if iv_rank comes from VIX rank

### 10.6 Expected Move (DERIVED)

- **Formula** (line 42):
  ```python
  EM = S × IV × sqrt(DTE/365)
  ```
- **Classification**: **DERIVED** — standard 1-sigma move formula

---

## 11. Stock Scanners — Derived Technical Indicators

**Source**: `mean_reversion_service.py`, `momentum_breakout_service.py`, `pullback_swing_service.py`, `volatility_expansion_service.py`

All stock scanner metrics are DERIVED from Tradier price/volume data (DIRECT). No proxies.

| Scanner | Key Metrics | Classification |
|---|---|---|
| Mean Reversion | RSI-14, z-score, SMA distances, volume spike | DERIVED |
| Momentum Breakout | 55D high proximity, ATR quality, vol ratio, SMA alignment | DERIVED |
| Pullback Swing | Pullback depth, SMA20 proximity, RSI 40-60 zone | DERIVED |
| Vol Expansion | ATR ratio, RV ratio, BB width percentile, vol spike | DERIVED |

Scoring formulas use hardcoded sub-score weights within fixed component ranges (e.g., breakout 0-35, volume 0-25). All computations are traceable to OHLCV data.

---

## 12. SIGNAL_PROVENANCE Coverage Gap

### Engines WITH SIGNAL_PROVENANCE:

| Engine | Has SIGNAL_PROVENANCE | Proxy Count Tracked | Proxy Count in Output |
|---|---|---|---|
| `cross_asset_macro` | YES (lines 83–173) | 3 (usd, gold, copper) | YES → `engine_output_contract.py` |
| `flows_positioning` | YES (lines 72–130) | 8+ tagged | YES |
| `liquidity_financial_conditions` | YES (lines 54–120) | 4 (fci, funding, policy, vix_conditions) | YES |

### Engines WITHOUT SIGNAL_PROVENANCE:

| Engine | Has SIGNAL_PROVENANCE | Proxy Inputs Present? | Impact |
|---|---|---|---|
| **`volatility_options`** | **NO** | YES — vix_rank_30d, vix_percentile_1y, vix_2nd/3rd, option_richness, premium_bias | **HIGH** — proxy-of-proxy metrics arrive with no downstream labeling |
| **`breadth_participation`** | **NO** | Minimal (SP500_PROXY universe is an implicit proxy) | LOW — metrics are DERIVED from direct data |
| **`news_sentiment`** | **NO** | YES — keyword sentiment is proxy-like | **MEDIUM** — keyword scoring is crude NLP proxy for true sentiment |

**Finding**: The `engine_output_contract.py` normalizer (line 491–497) counts proxy vs direct from `signal_provenance` when available. For engines without it, `proxy_count` is always 0 — **incorrectly implying they have no proxy inputs**.

```python
# engine_output_contract.py lines 491–497
provenance = diag.get("signal_provenance") or {}
proxy_count = 0
direct_count = 0
for _sig, info in provenance.items():
    if isinstance(info, dict):
        sig_type = info.get("type", "")
        if sig_type == "proxy":
            proxy_count += 1
        else:
            direct_count += 1
```

When `signal_provenance` is absent (vol, breadth, news), this loop produces `proxy_count=0, direct_count=0` — the dashboard sees "0 proxies" which is misleading for the volatility engine.

---

## 13. Confidence Penalty Analysis

### Engines with Proxy-Aware Confidence Reduction

| Engine | Proxy Penalties Applied | Amount |
|---|---|---|
| `flows_positioning` | Missing data per-submetric (-3/ea, cap -30) | Variable |
| | Cross-pillar disagreement (>35pt spread) | -0.5/pt above 35, cap -15 |
| | Heavy proxy reliance (≥4 proxy sources) | -8 |
| | No direct institutional flow data | -5 |
| | No direct futures positioning data | -5 |
| | Single-source dependency (1 upstream, ≥6 proxies) | -12 |
| **Net effect (Phase 1)** | **All penalties apply simultaneously** | **~-30 to -50** |

### Engines WITHOUT Proxy-Aware Confidence

| Engine | Confidence Factors | Proxy Awareness |
|---|---|---|
| `volatility_options` | Missing data (-5/ea, cap -40), pillar disagreement (-0.5/pt, cap -15), few active pillars (-10/ea) | **NONE** — no proxy penalty despite vix_rank, term structure, option_richness proxies |
| `breadth_participation` | Missing data, universe coverage | **N/A** — no proxies, correct behavior |
| `cross_asset_macro` | (via engine internal) | Partial — copper staleness mentioned in SIGNAL_PROVENANCE but not penalized in code |
| `liquidity_financial_conditions` | (via engine internal) | Partial — fci_proxy labeled "proxy" in submetric status but no confidence reduction |
| `news_sentiment` | None explicit | **NONE** — no confidence mechanism at all |

### Regime Service Confidence

The regime synthesis confidence (line 1006) is based on:
- Block coverage (how many of 3 blocks have scores)
- Block agreement (spread between block scores)

It does **NOT** factor in the proxy density of constituent engine inputs. A regime score where flows_positioning contributes 25% of the tactical block (itself 30% of regime) carries no proxy penalty at the regime level beyond what the flows engine internally applies.

---

## 14. Summary Table

| # | Metric | Claims To Be | Actually Derived From | Classification | Labeled as Proxy? | Confidence Penalty? |
|---|---|---|---|---|---|---|
| 1 | `put_call_proxy` | Equity P/C ratio | `0.45 + VIX × 0.023` | PROXY | YES (SIGNAL_PROVENANCE) | YES (-5 no direct flow) |
| 2 | `systematic_proxy` | CTA allocation level | `max(5, min(95, 110 - VIX×2.5))` | PROXY | YES | YES (aggregate) |
| 3 | `futures_proxy` | Net long futures % | `max(10, min(90, 100 - VIX×2.2))` | PROXY | YES | YES (-5 no futures) |
| 4 | `short_interest_proxy` | Short interest % | `max(0.8, min(6, 0.1 + VIX×0.12))` | PROXY | YES | YES (aggregate) |
| 5 | `retail_bull_proxy` | Retail bullish % | `max(15, min(60, 65 - VIX×1.1))` | PROXY | YES | YES (aggregate) |
| 6 | `retail_bear_proxy` | Retail bearish % | `max(15, min(55, 10 + VIX×1.05))` | PROXY | YES | YES (aggregate) |
| 7 | `flow_direction_proxy` | ETF fund flow direction | `max(15, min(85, 90 - VIX×1.8))` | PROXY | YES | YES (-5 no direct flow) |
| 8 | `flow_persistence_5d` | 5-day flow consistency | `max(20, min(85, 95 - VIX×2.2))` | PROXY | YES | YES (aggregate) |
| 9 | `flow_persistence_20d` | 20-day flow consistency | `max(15, min(80, 88 - VIX×2.0))` | PROXY | YES | YES (aggregate) |
| 10 | `flow_volatility_proxy` | Flow instability | `max(10, min(90, VIX×2.5 - 10))` | PROXY | YES | YES (aggregate) |
| 11 | `follow_through_proxy` | Flow sustainability | `flow_direction_proxy × 0.85 + 8` | **PROXY-OF-PROXY** | YES | YES (aggregate) |
| 12 | `inflow_balance_proxy` | Inflow/outflow balance | Alias of flow_direction_proxy | PROXY | YES | YES (aggregate) |
| 13 | `vix_rank_30d` | IV Rank (30d) | VIX index history rank | PROXY | Partial (code comment only) | **NO** |
| 14 | `vix_percentile_1y` | IV Percentile (1y) | VIX index history percentile | PROXY | Partial (code comment only) | **NO** |
| 15 | `vix_2nd_month` | VIX 2nd month future | `vix_avg_20d` or `vix_spot × 0.97` | PROXY | Partial (code comment only) | **NO** |
| 16 | `vix_3rd_month` | VIX 3rd month future | `vix_avg_20d × 1.03` or `vix_spot × 0.95` | PROXY | Partial (code comment only) | **NO** |
| 17 | `option_richness` | Option richness level | vix_rank_30d (PROXY) + iv_30d + rv_30d | **PROXY-OF-PROXY** | **NO** | **NO** |
| 18 | `premium_bias` | Sell/buy bias | vix_rank_30d (PROXY) + VRP + P/C | **PROXY-OF-PROXY** | **NO** | **NO** |
| 19 | `fci_proxy` | Financial Conditions Index | Avg(VIX_norm, IG_norm, 2Y_norm) | PROXY | YES (SIGNAL_PROVENANCE + submetric status) | **NO** (documented but not implemented) |
| 20 | `funding_stress_proxy` | Funding stress (SOFR/OIS) | VIX + fed funds heuristic | PROXY | YES (SIGNAL_PROVENANCE) | **NO** |
| 21 | `policy_pressure_proxy` | Fed policy pressure | Fed funds vs neutral rate (2.5%) | PROXY | YES (SIGNAL_PROVENANCE) | **NO** |
| 22 | `vix_conditions_proxy` | Financial conditions tightness | `interpolate(VIX, 12, 35, 90, 10)` | PROXY | YES (SIGNAL_PROVENANCE) | **NO** |
| 23 | `usd_index` | US Dollar Index (DXY) | FRED DTWEXBGS (Trade-Weighted Broad) | PROXY | YES (SIGNAL_PROVENANCE) | **NO** |
| 24 | `gold_price` | Gold spot price | FRED NASDAQQGLDI (NASDAQ Gold Index) | PROXY | YES (SIGNAL_PROVENANCE) | **NO** |
| 25 | `copper_price` | Copper for growth signal | FRED PCOPPUSDM (MONTHLY avg) | PROXY (severe lag) | YES (SIGNAL_PROVENANCE) | **NO** (documented but not implemented) |
| 26 | `headline_sentiment` | Market sentiment | Keyword matching (20 bull / 27 bear words) | PROXY | **NO** | **NO** |
| 27 | `negative_pressure` | Bearish pressure | Ratio from keyword classification | **PROXY-OF-PROXY** | **NO** | **NO** |
| 28 | `narrative_severity` | Theme severity | Category-weighted penalties | PROXY | **NO** | **NO** |
| 29 | `source_agreement` | Source consensus | Std dev of keyword-derived scores | **PROXY-OF-PROXY** | **NO** | **NO** |
| 30 | `recency_pressure` | Recent sentiment | Time-decay × keyword scores | **PROXY-OF-PROXY** | **NO** | **NO** |
| 31 | `regime_score` | Overall market regime | Block synthesis (3 blocks × 6 MI engines) | Score-of-Scores (5 levels deep) | Partial (block-level proxy labels) | Partial (block agreement only) |
| 32 | `yield_curve_spread` | 10Y-2Y spread | DGS10 - DGS2 (no date alignment) | DERIVED | YES (source tag) | N/A |
| 33 | `cpi_yoy` | CPI year-over-year | CPIAUCSL[0]/CPIAUCSL[12] - 1 | DERIVED | YES (source tag) | N/A |
| 34 | SP500_PROXY universe | S&P 500 breadth | 150 curated tickers (not 500) | PROXY (implicit) | **NO** | **NO** |
| 35 | `POP` (options) | Probability of Profit | `1 - abs(delta_short)` | DERIVED | N/A | N/A |
| 36 | `EV` (options) | Expected Value | `p_win × max_profit - (1-p_win) × max_loss` | DERIVED | N/A | N/A |
| 37 | `trade_quality_score` | Trade ranking score | `0.4×POP + 0.3×RoR_norm + 0.3×iv_rank` | DERIVED (or P-of-P if iv_rank from VIX) | N/A | N/A |

---

## 15. Critical Flags

### FLAG 1 — Volatility Engine Has No SIGNAL_PROVENANCE (CRITICAL)

The volatility engine uses 5+ proxy/proxy-of-proxy metrics (`vix_rank_30d`, `vix_percentile_1y`, `vix_2nd_month`, `vix_3rd_month`, `option_richness`, `premium_bias`) but has **no SIGNAL_PROVENANCE dict**. The `engine_output_contract.py` normalizer reports `proxy_count=0` for this engine, which is factually incorrect and misleading to any dashboard or consumer checking proxy status.

**Impact**: Downstream systems (dashboard metadata, regime service) believe the vol engine has zero proxies. This is the textbook case of "proxy laundering" — proxy metrics enter as plain floats and lose their proxy label.

### FLAG 2 — News Engine Has No Proxy Labeling (HIGH)

The news sentiment engine's `headline_sentiment` component (30% weight) is a crude keyword-matching proxy for actual NLP sentiment analysis. Four of its six components are PROXY or PROXY-OF-PROXY. None are labeled. No SIGNAL_PROVENANCE. No confidence mechanism.

### FLAG 3 — Confidence Only Penalizes Proxies in Flows Engine (HIGH)

Only `flows_positioning_engine.py` reduces confidence for proxy reliance. The other 5 engines' `_compute_confidence()` functions (where they exist) only penalize missing data and cross-pillar disagreement. An engine could be 100% proxy-fed and still report confidence 100.

### FLAG 4 — VIX is the Master Proxy Input (HIGH)

VIX appears as a scored input in 4 of 6 engines:

| Engine | VIX Role | Double-Count Risk |
|---|---|---|
| Flows & Positioning | ALL 12 proxy metrics | N/A (single-source by design) |
| Volatility | Pillar 1 regime scoring, term structure inference | Low (primary domain) |
| Cross-Asset Macro | Pillar 3 credit/risk appetite (20% of pillar) | Documented; no double-count in P4/P5 |
| Liquidity | Pillar 2 fci_proxy (1/3), vix_conditions (25%) | Documented but VIX in 2/4 submetrics |

When VIX moves, it simultaneously shifts ALL four engines. At the regime level, this creates correlated movements that look like cross-engine agreement but are actually single-variable dependency.

### FLAG 5 — Term Structure Fabrication Not Flagged in Submetric (HIGH)

The term structure metrics (`vix_2nd_month`, `vix_3rd_month`) are fabricated heuristics — not market observations. They're scored in Pillar 2 as `term_structure_shape` and `contango_steepness` without any submetric `status` field indicating they're inferred. The vol engine's Pillar 2 (25% weight) is largely built on fabricated data.

### FLAG 6 — Proxy Confidence Penalties Don't Cascade to Regime (MEDIUM)

The flows engine applies ~30-50 points of confidence penalty for proxy reliance. This confidence value is available in the regime service via `_extract_engine_confidence()`. However, the regime synthesis (line 994–1062) uses block-level coverage and agreement for confidence — it does NOT weight engine confidence into the regime confidence. A low-confidence flows engine score counts the same as a high-confidence breadth score in the block average.

### FLAG 7 — Score-of-Scores Depth (MEDIUM)

The regime label is 5 levels of derivation from market data:
```
Market Data → Submetric Score → Pillar Score → Engine Composite → Block Score → Regime Score
```
At each level, information is compressed via weighted averaging and clamping. By the time a VIX reading becomes part of the regime label, it has been:
1. Normalized (0-100 via _interpolate)
2. Averaged with other submetrics (via weights)
3. Averaged with other pillars (via weights)
4. Averaged with other engines (via block weights)
5. Averaged with other blocks (via regime weights)

Each averaging step dampens signal strength and can mask divergent inputs. A VIX spike from 15→25 might only move the regime score by 3-5 points after all the averaging.

### FLAG 8 — Breadth Universe Proxy Unlabeled (LOW)

The breadth engine uses ~150 curated tickers as a proxy for the S&P 500 (500 constituents). This is never documented as a proxy in the engine output or SIGNAL_PROVENANCE (which doesn't exist for breadth). For breadth/advance-decline statistics, using 30% of the index may produce different dynamics than the full index.
