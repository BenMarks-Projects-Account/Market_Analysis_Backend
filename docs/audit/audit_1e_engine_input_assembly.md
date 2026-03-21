# Audit 1E — Engine Input Assembly

> **Scope**: Trace how raw data is assembled into input payloads for each of the 6 MI engines.
> **Date**: 2025-07-11
> **Auditor**: Copilot (automated code trace)
> **Status**: Complete

---

## Executive Summary

All 6 engines follow a consistent three-layer assembly pattern: **Data Provider → Service → Engine**. The data provider fetches raw data from upstream sources (Tradier, FRED, MarketContextService) and assembles pillar-keyed dicts. The service layer connects provider output to the engine's `compute_*_scores()` function. The engine receives typed pillar dicts and computes deterministic scores.

**Key findings:**
- **No input validation exists** — engines receive unvalidated dicts with no schema checks, range guards, or required-field assertions before scoring.
- **Temporal misalignment is systemic** — engines routinely combine intraday data (VIX, quotes) with data that may be days or weeks old (FRED monthly copper, FRED credit spreads) with no alignment check or warning.
- **Pre-computation happens in data providers** — several engines receive values already derived from formulas (e.g., all 12 flows/positioning proxy metrics, term structure estimates, trend momentum). The engine then computes pillar scores from these pre-computed inputs.
- **Anti-anchoring (excluded_fields)** is well-implemented for LLM model analysis — composite scores, labels, and narratives are stripped before model sees data. However, pillar_scores ARE included, partially anchoring the model.
- **None propagation is properly handled** — providers pass None for missing data, engines guard with `if value is not None` checks, and missing metrics are tracked in `missing_inputs`.

---

## Architecture Pattern (All 6 Engines)

```
Upstream Sources (Tradier, FRED, MarketContextService)
        │
        ▼
  Data Provider  ─── fetch_*_data() → pillar-keyed dict
        │              • _extract_value() strips metric envelope → bare float
        │              • inline pre-computation for derived metrics
        │              • None preserved for missing data
        ▼
  Service Layer  ─── get_*_analysis() → orchestrator
        │              • cache check (TTLCache, 90-120s TTL)
        │              • calls provider.fetch_*_data()
        │              • unpacks pillar dicts → engine kwargs
        │              • wraps engine_result + data_quality + normalized
        ▼
  Engine         ─── compute_*_scores() → deterministic scoring
        │              • receives typed pillar dicts as kwargs
        │              • computes per-pillar scores via scoring functions
        │              • weighted composite: Σ(pillar_score × weight) / Σ(active_weights)
        │              • None scores → pillar excluded from composite
        ▼
  Output Contract ── normalize_engine_output() → canonical shape
```

---

## Engine 1: Breadth & Participation

### Input Assembly Function
- **File**: `BenTrade/backend/app/services/breadth_data_provider.py`
- **Function**: `BreadthDataProvider._assemble_breadth_data()` (line 264)
- **Called by**: `BreadthDataProvider.fetch_breadth_data()` (line 158)

### Data Sources
- **Tradier bulk quotes** — `get_quotes()` for ~140 SP500_PROXY tickers (batched, 50/request)
- **Tradier benchmark quotes** — SPY + RSP (equal-weight ETF)
- **Tradier daily bars** — historical bars for all universe tickers (10 concurrent, semaphore-limited)
- **No MarketContextService** — breadth is fully self-contained via Tradier

### Field Mapping

#### Pillar 1: participation_data (Participation Breadth)
| Field | Source | Computation | None Handling |
|-------|--------|-------------|---------------|
| `advancing` | Tradier quotes | Count of tickers with `change > 0` | Defaults to 0 (int counter) |
| `declining` | Tradier quotes | Count of tickers with `change < 0` | Defaults to 0 |
| `unchanged` | Tradier quotes | Count of tickers with `change == 0` | Defaults to 0 |
| `total_valid` | Computed | `advancing + declining + unchanged` | Always ≥ 0 |
| `new_highs` | Tradier quotes | `close >= week_52_high * 0.99` | Skipped if close or high is None |
| `new_lows` | Tradier quotes | `close <= week_52_low * 1.01` | Skipped if close or low is None |
| `sectors_positive` | Tradier quotes | Count of sectors with avg change_pct > 0 | 0 if no sector data |
| `sectors_total` | Computed | Number of sectors with any data | 0 if empty |
| `ew_return` | Tradier quote (RSP) | `change_percentage` (normalized to decimal) | None if RSP fetch fails |
| `cw_return` | Tradier quote (SPY) | `change_percentage` (normalized to decimal) | None if SPY fetch fails |

#### Pillar 2: trend_data (Trend Breadth)
| Field | Source | Computation | None Handling |
|-------|--------|-------------|---------------|
| `total_valid` | Computed | Tickers with ≥ 20 closes | Always ≥ 0 |
| `pct_above_20dma` | Tradier bars | `above_20 / total` via `_sma(closes, 20)` | 0/total if none above |
| `pct_above_50dma` | Tradier bars | `above_50 / total` via `_sma(closes, 50)` | None if < 50 bars |
| `pct_above_200dma` | Tradier bars | `above_200 / total` via `_sma(closes, 200)` | None if < 200 bars |
| `pct_20_over_50` | Tradier bars | Count of `sma_20 > sma_50` / total | 0 if insufficient data |
| `pct_50_over_200` | Tradier bars | Count of `sma_50 > sma_200` / total | 0 if insufficient data |
| `trend_momentum_short` | Tradier bars | **PRE-COMPUTED**: Δ(pct_above_20dma) over 5 days | None if < 10 counted |
| `trend_momentum_intermediate` | Tradier bars | **PRE-COMPUTED**: Δ(pct_above_50dma) over 10 days | None if < 10 counted |
| `trend_momentum_long` | Tradier bars | **PRE-COMPUTED**: Δ(pct_above_200dma) over 20 days | None if < 10 counted |

#### Pillar 3: volume_data (Volume Breadth)
| Field | Source | None Handling |
|-------|--------|---------------|
| `up_volume` | Tradier quotes | 0 if no advancing stocks with volume |
| `down_volume` | Tradier quotes | 0 if no declining stocks with volume |
| `total_volume` | Tradier quotes | 0 if no volume data |
| `advancing` | Tradier quotes | 0 counter |
| `declining` | Tradier quotes | 0 counter |

#### Pillar 4: leadership_data (Leadership Quality)
| Field | Source | None Handling |
|-------|--------|---------------|
| `ew_return` | Tradier (RSP) | None if fetch fails |
| `cw_return` | Tradier (SPY) | None if fetch fails |
| `index_return` | = cw_return | None if SPY fails |
| `median_return` | Computed | None if no stock_returns |
| `pct_outperforming_index` | Computed | None if no data |
| `sector_returns` | Computed | Empty dict if no sectors |

#### Pillar 5: stability_data (Participation Stability)
| Field | Source | None Handling |
|-------|--------|---------------|
| `breadth_persistence_10d` | Tradier bars | **PRE-COMPUTED**: fraction of last 10 sessions with A/D > 1 | None if no data |
| `ad_ratio_volatility_5d` | Tradier bars | **PRE-COMPUTED**: stdev of last 5 A/D ratios | None if < 2 values |
| `pct_above_20dma_volatility_5d` | Tradier bars | **PRE-COMPUTED**: stdev of last 5 pct_above_20dma | None if < 2 values |

#### universe_meta
| Field | Source | Note |
|-------|--------|------|
| `name` | Config | "SP500_PROXY" |
| `expected_count` | len(universe) | ~140 |
| `actual_count` | Tickers with quotes | Variable |
| `coverage_pct` | `actual / expected * 100` | |
| `survivorship_bias_risk` | Hardcoded `True` | Static list, not point-in-time |
| `as_of` | `datetime.now(UTC)` | Assembly timestamp |

### Pre-computation Summary
- **trend_momentum_short/intermediate/long**: Δ(breadth) over multiple lookback windows — computed in provider
- **breadth_persistence_10d**: multi-day A/D ratio analysis — computed in provider
- **ad_ratio_volatility_5d**, **pct_above_20dma_volatility_5d**: stdev calculations — computed in provider
- All percentage metrics (pct_above_*, pct_outperforming_index) are computed from raw counts in provider

### Excluded Fields (Anti-Anchoring for Model Analysis)
From `common/model_analysis.py` line 2481 — `_BREADTH_EXCLUDED_FIELDS` (10 fields):
```
score, label, short_label, summary, trader_takeaway,
positive_contributors, negative_contributors, conflicting_signals,
confidence_score, signal_quality
```
**Included in model input**: `raw_inputs` (5 pillar sub-dicts), `pillar_scores` (5 numeric scores), `pillar_weights`, `universe`, `warnings`, `missing_inputs`

### Timestamp Alignment
- **Single-source, single-moment**: All data fetched from Tradier in one async burst. Quotes and bars are from the same fetch session. No cross-source temporal mismatch.
- **Risk**: Low. Single data source (Tradier) with consistent timestamps.

---

## Engine 2: Volatility & Options Structure

### Input Assembly Function
- **File**: `BenTrade/backend/app/services/volatility_options_data_provider.py`
- **Function**: `VolatilityOptionsDataProvider.fetch_volatility_data()` (line 91)
- **Inline assembly**: No separate `_assemble` method — pillar dicts built inline in `fetch_volatility_data()`

### Data Sources
- **MarketContextService** — VIX spot (via `_fetch_vix_data()`)
- **Tradier** — VVIX, SPY IV data, SPY options chain analysis
- **FRED** — CBOE SKEW index (`_fetch_cboe_skew()`)
- **Tradier bars** — VIX history (`_fetch_vix_history()`), SPY realized volatility (`_fetch_spy_rv()`)

### Field Mapping

#### Pillar 1: regime_data (Volatility Regime)
| Field | Source | Computation | None Handling |
|-------|--------|-------------|---------------|
| `vix_spot` | MarketContextService | Extracted via `_fetch_vix_data()` | None if MCS fails |
| `vix_avg_20d` | Tradier bars | VIX 20-day average from history | None if no VIX history |
| `vix_rank_30d` | Tradier bars | **PROXY**: VIX history rank, not true IV rank | None if no history |
| `vix_percentile_1y` | Tradier bars | **PROXY**: VIX history percentile, not IV percentile | None if no history |
| `vvix` | Tradier | Direct VVIX quote | None if fetch fails |

#### Pillar 2: structure_data (Volatility Structure)
| Field | Source | Computation | None Handling |
|-------|--------|-------------|---------------|
| `vix_front_month` | = vix_spot | Direct pass-through | None if VIX unavailable |
| `vix_2nd_month` | **PRE-COMPUTED PROXY** | If VIX < avg: `vix_avg_20d`; else: `vix_spot * 0.97` | None if VIX or avg missing |
| `vix_3rd_month` | **PRE-COMPUTED PROXY** | If VIX < avg: `vix_avg_20d * 1.03`; else: `vix_spot * 0.95` | None if VIX or avg missing |
| `iv_30d` | Tradier (SPY IV) | From SPY options analysis | None if SPY IV fetch fails |
| `rv_30d` | Tradier bars (SPY) | **PRE-COMPUTED**: close-to-close annualized RV | None if insufficient bars |

#### Pillar 3: skew_data (Tail Risk & Skew)
| Field | Source | Computation | None Handling |
|-------|--------|-------------|---------------|
| `cboe_skew` | FRED | CBOE SKEW index direct | None if FRED fails |
| `put_skew_25d` | Tradier (SPY IV) | 25-delta put IV premium | None if incomplete |
| `tail_risk_signal` | **PRE-COMPUTED** | Label: Low/Moderate/Elevated/High from blended skew/CBOE SKEW | None if both inputs None |
| `tail_risk_numeric` | **PRE-COMPUTED** | 0-100 from `_interpolate()` of skew components | None if both inputs None |

#### Pillar 4: positioning_data (Positioning & Options Posture)
| Field | Source | Computation | None Handling |
|-------|--------|-------------|---------------|
| `equity_pc_ratio` | Tradier (SPY) | SPY put/call ratio — **PROXY** for broader equity P/C | None if unavailable |
| `spy_pc_ratio_proxy` | = equity_pc_ratio | Duplicate, explicitly labeled as proxy | None |
| `option_richness` | **PRE-COMPUTED** | 0-100 from VIX rank + IV-RV spread logic | None if insufficient inputs |
| `option_richness_label` | **PRE-COMPUTED** | "Rich"/"Fair"/"Cheap" from thresholds | None |
| `premium_bias` | **PRE-COMPUTED** | Blended from IV-RV spread, VIX rank, P/C ratio | None if no components |

**Note**: Pillar 5 (Strategy Suitability) has no separate input dict — it is derived from pillars 1-4 raw data inside the engine.

### Pre-computation Summary (HEAVY)
- **vix_2nd_month, vix_3rd_month**: Term structure proxied from VIX spot vs 20-day average — no actual futures data
- **tail_risk_signal/numeric**: Multi-input interpolation blending put_skew_25d and CBOE SKEW
- **option_richness/label**: Complex logic combining VIX rank + IV > RV spread
- **premium_bias**: Blended signal from VRP, VIX rank, P/C deciles
- **rv_30d**: Realized volatility calculation from SPY daily returns

### Excluded Fields (Anti-Anchoring for Model Analysis)
From `common/model_analysis.py` line 2818 — `_VOL_EXCLUDED_FIELDS` (10 fields):
```
score, label, short_label, summary, trader_takeaway,
positive_contributors, negative_contributors, conflicting_signals,
confidence_score, signal_quality
```

### Timestamp Alignment
- **HIGH RISK**: Combines:
  - VIX spot from MarketContextService (may be intraday or cached/stale)
  - VVIX from Tradier (real-time quote)
  - SPY IV from Tradier options analysis (real-time)
  - CBOE SKEW from FRED (1-2 business days lag)
  - VIX history / SPY RV from Tradier bars (end-of-day)
- **No temporal alignment check**: The engine can receive today's VVIX alongside 2-day-old CBOE SKEW with no awareness of the mismatch.

---

## Engine 3: Cross-Asset / Macro Confirmation

### Input Assembly Function
- **File**: `BenTrade/backend/app/services/cross_asset_macro_data_provider.py`
- **Function**: `CrossAssetMacroDataProvider.fetch_cross_asset_data()` (line 66)
- **Extraction**: `_extract_value()` (line 40) strips metric envelope → bare float

### Data Sources
- **MarketContextService** — VIX, 10Y/2Y yields, fed funds rate, oil WTI, USD index, yield curve spread, CPI YoY
- **FRED** — Gold (GOLDPMGBD228NLBM/GC_F proxy), Copper (PCOPPUSDM), IG Spread (BAMLC0A0CM), HY Spread (BAMLH0A0HYM2)

### Field Mapping

#### Pillar 1: rates_data (Rates & Yield Curve, 25%)
| Field | Source | None Handling |
|-------|--------|---------------|
| `ten_year_yield` | MCS → `_extract_value()` | None if MCS fails |
| `two_year_yield` | MCS → `_extract_value()` | None |
| `yield_curve_spread` | MCS → `_extract_value()` | None |
| `fed_funds_rate` | MCS → `_extract_value()` | None |

#### Pillar 2: dollar_commodity_data (Dollar & Commodity, 20%)
| Field | Source | None Handling |
|-------|--------|---------------|
| `usd_index` | MCS → `_extract_value()` | None |
| `oil_wti` | MCS → `_extract_value()` | None |
| `gold_price` | FRED observation value | None if FRED fails |
| `copper_price` | FRED observation value | None if FRED fails |

#### Pillar 3: credit_data (Credit & Risk Appetite, 25%)
| Field | Source | None Handling |
|-------|--------|---------------|
| `ig_spread` | FRED (BAMLC0A0CM) | None if FRED fails |
| `hy_spread` | FRED (BAMLH0A0HYM2) | None if FRED fails |
| `vix` | MCS → `_extract_value()` | None |

#### Pillar 4: defensive_growth_data (Defensive vs Growth, 15%)
| Field | Source | None Handling |
|-------|--------|---------------|
| `gold_price` | FRED | None (shared with Pillar 2) |
| `ten_year_yield` | MCS | None (shared with Pillar 1) |
| `copper_price` | FRED | None (shared with Pillar 2) |

#### Pillar 5: coherence_data (Macro Coherence, 15%)
| Field | Source | None Handling |
|-------|--------|---------------|
| `vix`, `yield_curve_spread`, `ig_spread`, `hy_spread`, `usd_index`, `oil_wti`, `gold_price`, `copper_price`, `cpi_yoy` | Mixed MCS + FRED | None per-field |

**Note**: Fields are intentionally shared across pillars (e.g., gold_price in pillars 2, 4, 5).

### Pre-computation Summary
- **Minimal**: No formulas applied during assembly. `_extract_value()` only unwraps the metric envelope. The engine receives raw values.
- **yield_curve_spread**: Pre-computed by MarketContextService (10Y - 2Y), not re-derived here.
- **copper_days_stale**: Staleness check computed for logging/source_meta but NOT passed to the engine.

### Excluded Fields (Anti-Anchoring for Model Analysis)
From `common/model_analysis.py` line 3110 — `_CROSS_ASSET_EXCLUDED_FIELDS` (10 fields):
```
score, label, short_label, summary, trader_takeaway,
confirming_signals, contradicting_signals, mixed_signals,
confidence_score, signal_quality
```

### Timestamp Alignment
- **CRITICAL RISK**: Combines:
  - VIX, yields, oil, USD from MarketContextService (freshness varies: intraday/stale/unknown)
  - Gold from FRED (daily, 1 business day lag)
  - Copper from FRED (MONTHLY — up to 30 days stale)
  - IG/HY spreads from FRED (daily, 1-2 business day lag)
  - CPI YoY from FRED (monthly, significant lag)
- **Staleness tracked in source_meta** (copper_days_stale, observation dates) but **NOT propagated to engine** — engine has no awareness of temporal mismatch.
- **Example**: Engine may combine today's real-time VIX alongside last month's copper price, treating both as current.

---

## Engine 4: Flows & Positioning

### Input Assembly Function
- **File**: `BenTrade/backend/app/services/flows_positioning_data_provider.py`
- **Function**: `FlowsPositioningDataProvider.fetch_flows_positioning_data()` (line ~50)
- **Extraction**: `_extract_value()` strips MCS metric envelope → bare float

### Data Sources
- **MarketContextService** — VIX only (single metric)
- **All other metrics**: **PRE-COMPUTED PROXIES** derived from VIX via deterministic formulas

### Field Mapping

#### Pillar 1: positioning_data (Positioning Pressure)
| Field | Source | Formula | None Handling |
|-------|--------|---------|---------------|
| `put_call_proxy` | **PROXY** from VIX | `0.45 + vix * 0.023` | None if VIX None |
| `systematic_proxy` | **PROXY** from VIX | `max(5, min(95, 110 - vix * 2.5))` | None if VIX None |
| `futures_proxy` | **PROXY** from VIX | `max(5, min(95, 65 + (20 - vix) * 2))` | None if VIX None |

#### Pillar 2: crowding_data (Crowding & Stretch)
| Field | Source | Formula | None Handling |
|-------|--------|---------|---------------|
| `short_interest_proxy` | **PROXY** from VIX | `max(1, min(15, 3 + (vix - 15) * 0.3))` | None if VIX None |
| `retail_bull_proxy` | **PROXY** from VIX | `max(20, min(75, 70 - vix * 0.8))` | None if VIX None |
| `retail_bear_proxy` | **PROXY** from VIX | `max(10, min(60, 15 + vix * 0.8))` | None if VIX None |

#### Pillar 3: squeeze_data (Squeeze / Unwind Risk)
| Field | Source | Formula | None Handling |
|-------|--------|---------|---------------|
| `short_interest_proxy` | Same as Pillar 2 | Shared reference | None |
| `vix` | MCS | Direct value | None |

#### Pillar 4: flow_data (Flow Direction & Persistence)
| Field | Source | Formula | None Handling |
|-------|--------|---------|---------------|
| `flow_direction_proxy` | **PROXY** from VIX | `max(-1, min(1, (20 - vix) / 20))` | None if VIX None |
| `flow_persistence_5d` | **PROXY** from VIX | `max(0, min(1, (25 - vix) / 25))` | None |
| `flow_persistence_20d` | **PROXY** from VIX | `max(0.1, min(0.9, 0.6 + (20 - vix) * 0.01))` | None |
| `flow_volatility_proxy` | **PROXY** from VIX | `max(5, min(40, vix * 0.6 + 3))` | None |

#### Pillar 5: stability_data (Positioning Stability)
| Field | Source | Formula | None Handling |
|-------|--------|---------|---------------|
| `inflow_balance_proxy` | **PROXY** from VIX | `max(-50, min(50, (20 - vix) * 2.5))` | None |
| `follow_through_proxy` | **PROXY** from VIX | `max(30, min(80, 70 - vix * 0.5))` | None |
| `flow_volatility_proxy` | Same as Pillar 4 | Shared reference | None |

### Pre-computation Summary (EXTREME)
- **ALL 12 non-VIX metrics are synthetic proxies** derived from a single VIX value via deterministic formulas
- This engine effectively has **one real input** (VIX) masquerading as a 5-pillar system
- The 12 formulas generate correlated outputs from a single variable — the 5-pillar weighted composite collapses toward a single-variable function of VIX
- **This is the most significant pre-computation concern in the system** (documented in audit 1C as proxy laundering)

### Excluded Fields (Anti-Anchoring for Model Analysis)
From `common/model_analysis.py` line 3390 — `_FLOWS_POSITIONING_EXCLUDED_FIELDS` (11 fields):
```
score, label, short_label, summary, trader_takeaway,
positive_contributors, negative_contributors, conflicting_signals,
confidence_score, signal_quality, strategy_bias
```

### Timestamp Alignment
- **Low concern (but misleading)**: All data derives from a single VIX value, so temporal alignment is trivially consistent. However, VIX itself may be cached/stale via MarketContextService (see audit 1D findings on freshness stripping).

---

## Engine 5: Liquidity & Financial Conditions

### Input Assembly Function
- **File**: `BenTrade/backend/app/services/liquidity_conditions_data_provider.py`
- **Function**: `LiquidityConditionsDataProvider.fetch_liquidity_conditions_data()` (line 79)
- **Extraction**: `_extract_value()`, `_extract_source()`, `_extract_freshness()` — this provider uniquely extracts freshness metadata

### Data Sources
- **MarketContextService** — VIX, 10Y/2Y yields, fed funds rate, USD index, yield curve spread
- **FRED (via MCS.fred)** — IG spread (BAMLC0A0CM), HY spread (BAMLH0A0HYM2)

### Field Mapping

#### Pillar 1: rates_data (Rates & Policy Pressure)
| Field | Source | None Handling |
|-------|--------|---------------|
| `two_year_yield` | MCS → `_extract_value()` | None |
| `ten_year_yield` | MCS → `_extract_value()` | None |
| `fed_funds_rate` | MCS → `_extract_value()` | None |
| `yield_curve_spread` | MCS → `_extract_value()` | None |

#### Pillar 2: conditions_data (Financial Conditions Tightness)
| Field | Source | None Handling |
|-------|--------|---------------|
| `vix` | MCS → `_extract_value()` | None |
| `ig_spread` | FRED BAMLC0A0CM | None if FRED fails |
| `hy_spread` | FRED BAMLH0A0HYM2 | None if FRED fails |
| `two_year_yield` | MCS (shared) | None |
| `ten_year_yield` | MCS (shared) | None |
| `yield_curve_spread` | MCS (shared) | None |

#### Pillar 3: credit_data (Credit & Funding Stress)
| Field | Source | None Handling |
|-------|--------|---------------|
| `ig_spread` | FRED (shared) | None |
| `hy_spread` | FRED (shared) | None |
| `vix` | MCS (shared) | None |
| `fed_funds_rate` | MCS (shared) | None |
| `two_year_yield` | MCS (shared) | None |

#### Pillar 4: dollar_data (Dollar / Global Liquidity)
| Field | Source | None Handling |
|-------|--------|---------------|
| `dxy_level` | MCS (usd_index) → `_extract_value()` | None |
| `vix` | MCS (shared) | None |

#### Pillar 5: stability_data (Liquidity Stability & Fragility)
| Field | Source | None Handling |
|-------|--------|---------------|
| `vix` | MCS (shared) | None |
| `ig_spread` | FRED (shared) | None |
| `hy_spread` | FRED (shared) | None |
| `two_year_yield` | MCS (shared) | None |
| `dxy_level` | MCS (shared) | None |
| `yield_curve_spread` | MCS (shared) | None |

**Note**: Engine internally injects `_pillar_scores` (scores from pillars 1-4) into pillar 5 data for cross-pillar contradiction check.

### Pre-computation Summary
- **Minimal inline pre-computation** — `_extract_value()` only unwraps envelopes
- **FCI composite and funding stress** are computed inside the engine, not the provider (correctly)
- **yield_curve_spread**: Pre-computed by MCS (10Y - 2Y), not re-derived
- **proxy_count = 2** hardcoded in source_meta: FCI proxy and funding stress proxy

### Unique Feature: Freshness Extraction
This is the **only data provider** that extracts `_extract_source()` and `_extract_freshness()` from metric envelopes. These are captured in `source_meta.source_detail` but **NOT passed to the engine's pillar dicts** — the engine receives bare values only.

### Excluded Fields (Anti-Anchoring for Model Analysis)
From `common/model_analysis.py` line 3672 — `_LIQUIDITY_CONDITIONS_EXCLUDED_FIELDS` (11 fields):
```
score, label, short_label, summary, trader_takeaway,
positive_contributors, negative_contributors, conflicting_signals,
confidence_score, signal_quality, support_vs_stress
```

### Timestamp Alignment
- **MODERATE RISK**: Combines:
  - VIX, yields, fed funds, USD from MarketContextService (variable freshness)
  - IG/HY spreads from FRED via `MCS.fred` (daily, 1-2 day lag)
- **Freshness is tracked** in source_meta but not used for engine decisions
- Cross-asset alignment concern — engine sees today's VIX alongside yesterday's credit spreads

---

## Engine 6: News Sentiment

### Input Assembly Function
- **File**: `BenTrade/backend/app/services/news_sentiment_service.py`
- **Function**: `NewsSentimentService.get_news_sentiment()` (line 121) — no separate data provider
- **Macro context**: `_fetch_macro_context()` (line 506) builds `MacroContext` dataclass

### Data Sources
- **Finnhub** — News headlines (primary)
- **Polygon** — News headlines (secondary)
- **MarketContextService** — Macro context (VIX, yields, oil, USD) for stress level
- **FRED** — Legacy fallback path if MCS unavailable

### Field Mapping

#### Input 1: items (list[dict]) — News Headlines
| Field | Source | None Handling |
|-------|--------|---------------|
| `headline` | Finnhub/Polygon | Required (filtered out if None) |
| `summary` | Finnhub/Polygon | May be None or empty |
| `source` | Provider attribution | Always present |
| `published_at` | Finnhub/Polygon | Used for recency scoring |
| `category` | Provider classification | May be None |
| `sentiment_score` | **PRE-COMPUTED** | Rule-based keyword scoring in `_score_sentiment()` | Defaults to 0 |
| `sentiment_label` | **PRE-COMPUTED** | "BULLISH"/"BEARISH"/"NEUTRAL" from score thresholds | Always present |
| `relevance_score` | **PRE-COMPUTED** | Keyword-based relevance to market/SPY/options | Defaults to 0 |

#### Input 2: macro_context (dict) — FRED Macro Snapshot
| Field | Source | None Handling |
|-------|--------|---------------|
| `vix` | MCS → metric envelope → value | None if MCS fails |
| `us_10y_yield` | MCS (ten_year_yield) | None if MCS fails |
| `us_2y_yield` | MCS (two_year_yield) | None if MCS fails |
| `fed_funds_rate` | MCS | None |
| `oil_wti` | MCS | None |
| `usd_index` | MCS | None |
| `yield_curve_spread` | **PRE-COMPUTED** | `us_10y_yield - us_2y_yield` (if both present) | None if either missing |
| `stress_level` | **PRE-COMPUTED** | "low"/"moderate"/"elevated"/"high" from VIX thresholds | "unknown" if VIX None |
| `as_of` | `datetime.now(UTC)` | Always present |
| `_freshness` | MCS metric envelopes | Per-metric freshness map (source, freshness, is_intraday, etc.) |

### Pre-computation Summary
- **sentiment_score**: Each news item is pre-scored with rule-based keyword analysis before engine
- **sentiment_label**: Derived from sentiment_score thresholds before engine
- **relevance_score**: Keyword-based relevance scoring before engine
- **stress_level**: VIX-based stress classification before engine (used in regime labeling)
- **yield_curve_spread**: Arithmetic difference computed in `_fetch_macro_context()`

### Engine Structure (Unique)
Unlike the other 5 engines, news_sentiment does NOT use pillar-keyed input dicts. It takes:
- `items: list[dict]` — up to 100 normalized news items
- `macro_context: dict` — FRED macro snapshot

Engine computes 6 components (not "pillars"): headline_sentiment, negative_pressure, narrative_severity, source_agreement, macro_stress, recency_pressure.

### Excluded Fields (Anti-Anchoring for Model Analysis)
From `common/model_analysis.py` line 2277 — `_NEWS_SENTIMENT_EXCLUDED_FIELDS` (9 fields):
```
sentiment_score, sentiment_label, regime_label, overall_score,
headline_pressure_24h, headline_pressure_72h, top_narratives,
divergence, stress_level
```
**Leak detection**: `analyze_news_sentiment()` actively checks `user_data_str` for forbidden field names and logs errors if found.

### Timestamp Alignment
- **MODERATE RISK**: News items have `published_at` timestamps (used for recency scoring), but macro context freshness varies:
  - VIX/yields from MCS may be cached
  - `_freshness` map is included in macro_dict but engine does NOT consume it
  - Engine's `_compute_recency_pressure()` uses item timestamps — this IS temporal-aware for news
  - Engine's `_compute_macro_stress()` uses macro values with no freshness check

---

## Data Shape Validation

### Finding: NO INPUT VALIDATION EXISTS

Across all 6 engines, there is **no pre-send validation** of the assembled input payloads:

| Check | Status |
|-------|--------|
| Required field presence | ❌ Not checked |
| Numeric range guards | ❌ Not checked |
| Type assertions (float vs str vs None) | ❌ Not checked |
| Schema validation (expected keys vs actual keys) | ❌ Not checked |
| Completeness threshold ("at least N fields must be non-None") | ❌ Not checked |
| Cross-field consistency (e.g., advancing + declining ≤ total) | ❌ Not checked |

**Mitigation**: Engines handle None gracefully via `if value is not None` guards, and track missing data in `missing_inputs` / `warnings`. But there is no gate that prevents an engine from running with 0% data coverage — it would produce a score of 0.0 with all warnings.

**Risk**: An upstream failure returning malformed data (e.g., a string where a float is expected) would cause an uncaught exception in the pillar scoring function, caught only by the per-pillar try/except in the engine.

---

## Excluded Fields (Anti-Anchoring) — Cross-Engine Summary

| Engine | Excluded Count | Core Exclusions | Engine-Specific Extras |
|--------|---------------|-----------------|----------------------|
| Breadth | 10 | score, label, short_label, summary, confidence_score, signal_quality, trader_takeaway | positive_contributors, negative_contributors, conflicting_signals |
| Volatility | 10 | (same core) | positive_contributors, negative_contributors, conflicting_signals |
| Cross-Asset | 10 | (same core) | confirming_signals, contradicting_signals, mixed_signals |
| Flows | 11 | (same core) | positive_contributors, negative_contributors, conflicting_signals, **strategy_bias** |
| Liquidity | 11 | (same core) | positive_contributors, negative_contributors, conflicting_signals, **support_vs_stress** |
| News | 9 | sentiment_score, sentiment_label, regime_label, overall_score | headline_pressure_24h/72h, top_narratives, divergence, stress_level |

**Pattern**: All 5 pillar engines exclude `{score, label, short_label, summary, trader_takeaway, confidence_score, signal_quality}` plus engine-specific narrative fields. News engine has a different exclusion set aligned with its component structure.

**Critical note**: `pillar_scores` ARE included in the model input for all 5 pillar engines. The model receives the numeric scores per pillar (just not the composite score/label). This means the model is partly anchored to the engine's pre-computed pillar assessments.

---

## Per-Engine Summary Table

| Engine | Total Input Fields | Direct Market Data | Derived/Computed | Proxy | Pre-computed Scores | Missing Data Handling |
|--------|-------------------|-------------------|------------------|-------|--------------------|-----------------------|
| Breadth | ~30 across 5 pillars + universe | 30 (all Tradier) | 8 (momentum, persistence, stdev) | 0 | 0 | None preserved; counters default to 0 |
| Volatility | ~17 across 4 pillars | 6 (VIX, VVIX, IV, SKEW, P/C, RV) | 8 (richness, bias, tail risk, term structure) | 4 (vix_rank, vix_pctl, vix_2nd/3rd month) | 0 | None preserved; degraded fallbacks for richness |
| Cross-Asset | ~20 across 5 pillars | 12 (MCS + FRED) | 0 | 0 | 0 | None preserved; source_errors tracked |
| Flows | ~15 across 5 pillars | 1 (VIX) | 0 | **12** (all from VIX formulas) | 0 | None when VIX None; all proxies or nothing |
| Liquidity | ~19 across 5 pillars | 8 (MCS + FRED) | 0 | 2 (FCI, funding stress — inside engine) | 0 | None preserved; source_detail tracked |
| News | items + 10 macro fields | ~5 macro (MCS) + all headlines | 4 (sentiment_score, label, relevance, stress) | 0 | 0 | None in macro; items filtered |

---

## Findings

### Finding 1E-01 — No Input Validation Before Engine (HIGH)

**Location**: All service layers (breadth_service.py, volatility_options_service.py, etc.)
**Issue**: No schema validation, type checking, or completeness assertion occurs between the data provider output and engine invocation. The service layer passes provider output directly to engine kwargs with no guards.
**Risk**: Malformed upstream data (wrong types, unexpected structures) would cause runtime errors caught only by per-pillar try/except blocks. An engine could run with 0% data coverage and produce a misleading 0.0 score.
**Recommendation**: Add a lightweight validation step (at minimum: assert all expected keys present, assert types are float|None for numeric fields) in each service layer before calling the engine.

### Finding 1E-02 — Temporal Misalignment Not Surfaced to Engine (CRITICAL)

**Location**: cross_asset_macro_data_provider.py, volatility_options_data_provider.py, liquidity_conditions_data_provider.py
**Issue**: Engines receive data from multiple sources with fundamentally different update frequencies (real-time VIX, daily credit spreads, monthly copper) but have no visibility into temporal alignment. The data provider strips temporal metadata via `_extract_value()`, sending only bare floats.
**Evidence**:
- Cross-asset: today's VIX + last month's copper price + last Friday's IG spread
- Volatility: today's VVIX + 2-day-old CBOE SKEW
- Liquidity: today's VIX + yesterday's credit spreads
**Mitigation in place**: `source_meta` tracks freshness in some providers, but it is NOT passed to engines.
**Recommendation**: Add temporal_alignment_metadata to engine input (max staleness, source timestamps). Consider confidence degradation when max staleness exceeds threshold.

### Finding 1E-03 — Flows/Positioning: 12 Proxy Metrics from Single VIX Input (CRITICAL)

**Location**: flows_positioning_data_provider.py
**Issue**: The entire Flows & Positioning engine operates on 12 synthetic proxy metrics, all derived from a single VIX value via deterministic formulas. The 5-pillar structure creates an illusion of independent signals, but mathematically it is a single-variable function.
**Cross-reference**: Documented in audit 1C (proxy laundering).
**Risk**: Engine produces seemingly granular analysis across 5 pillars, but the output has exactly 1 degree of freedom (VIX). Pillar weights are meaningless when all pillars are deterministic functions of the same input.
**Recommendation**: Either (a) source actual flows/positioning data (COT, fund flows), or (b) reduce to a single-pillar VIX-based assessment with explicit proxy labeling.

### Finding 1E-04 — Pre-computed Values in Volatility Provider Could Anchor Model (HIGH)

**Location**: volatility_options_data_provider.py (lines 140-220)
**Issue**: The volatility data provider computes several derived signals before the engine:
- `tail_risk_signal/numeric`: Blended from put_skew and CBOE SKEW via interpolation
- `option_richness/label`: VIX rank + IV-RV spread logic
- `premium_bias`: Blended from 3 sub-components
These pre-computed values become engine inputs, and their formulas contain embedded assumptions about risk thresholds. The engine then scores based on these pre-digested signals.
**Risk**: The data provider's threshold choices (e.g., tail_risk "Elevated" at >60) shape the engine's interpretation. Changing the engine's scoring logic has limited effect because the provider already categorized the signal.
**Recommendation**: Pass raw components (put_skew_25d, cboe_skew, vix_rank_30d, iv_30d, rv_30d) to engine, let engine compute derived signals internally.

### Finding 1E-05 — pillar_scores Included in Model Analysis Input (MEDIUM)

**Location**: common/model_analysis.py — all `_extract_*_raw_evidence()` functions
**Issue**: While composite score/label are correctly excluded from model input, `pillar_scores` (the per-pillar numeric scores) ARE included. The model receives both raw_inputs AND pre-computed pillar scores. This partially anchors the model's assessment to the engine's scoring.
**Architectural intent**: Documented as "model may reinterpret these" — the model is expected to validate/challenge pillar scores, not blindly accept them.
**Risk**: Models tend to anchor to numeric scores when available, potentially rubber-stamping engine assessments rather than providing independent analysis.
**Recommendation**: Consider providing pillar_scores in a separate "engine_assessment" section, clearly framed as "the engine's interpretation, which may differ from yours."

### Finding 1E-06 — Breadth Universe Survivorship Bias (MEDIUM)

**Location**: breadth_data_provider.py — SP500_PROXY list (line ~25)
**Issue**: The breadth universe is a hardcoded list of ~140 tickers (SP500_PROXY). `survivorship_bias_risk` is correctly set to `True` in universe_meta, but no point-in-time constituent data is used.
**Risk**: Removed or delisted companies silently disappear from the universe. The remaining healthy companies skew breadth metrics upward over time.
**Recommendation**: Low priority — this is documented and flagged. True fix requires S&P 500 historical constituent data from a financial data provider.

### Finding 1E-07 — Liquidity Provider: Unique Freshness Extraction Not Used (LOW)

**Location**: liquidity_conditions_data_provider.py — `_extract_freshness()` (line 60)
**Issue**: This is the ONLY data provider that extracts freshness metadata from MCS metric envelopes. It populates `source_meta.source_detail` with per-metric source and freshness info. However, this metadata is passed to `source_meta` only — the engine does not use it for scoring or confidence adjustment.
**Recommendation**: This freshness extraction pattern should be adopted by cross_asset_macro and volatility providers. Freshness data should influence engine confidence scoring.

### Finding 1E-08 — News Sentiment: Pre-scored Items (LOW)

**Location**: news_sentiment_service.py — `_score_sentiment()`, `_item_to_dict()`
**Issue**: News items arrive at the engine with pre-computed `sentiment_score` and `sentiment_label` from a rule-based keyword scorer. The engine's `_compute_headline_sentiment()` uses these pre-scored values as inputs.
**Mitigation**: The model analysis explicitly excludes `sentiment_score` and `sentiment_label`. The engine itself does not exclude them — they are the primary signal.
**Risk**: Low — the engine is designed to consume these as inputs, and the model analysis correctly strips them for independent assessment.

---

## Cross-Audit References

| Prior Finding | Relevance to 1E |
|--------------|-----------------|
| **1C-01** (Proxy laundering in flows/positioning) | Confirmed: 12 proxy metrics from single VIX assembled in data provider (1E-03) |
| **1C-02** (SIGNAL_PROVENANCE gaps) | `_extract_value()` strips source/freshness labels during assembly — this is the extraction point (1E-02) |
| **1D-01** (fetched_at misrepresents age) | Stale data enters engine via bare floats; provider strips temporal metadata (1E-02) |
| **1D-02** (Engines get bare values) | Confirmed: all 5 MCS-consuming providers use `_extract_value()` → bare float (1E-02) |
| **1D-03** (No staleness enforcement) | No validation gate prevents stale data from reaching engine (1E-01) |

---

## Risks & Assumptions

1. **Assumption**: Engine per-pillar try/except blocks are sufficient error containment for malformed inputs. Without input validation, this is the only safety net.
2. **Risk**: If MarketContextService returns a non-dict metric (e.g., raw float due to upstream change), `_extract_value()` handles it via isinstance check — but engine field mapping may break.
3. **Assumption**: `_weighted_avg()` correctly handles all-None pillar scores (returns None, then defaulted to 0.0). This means an engine with 100% missing data produces score=0.0 rather than an error.
4. **No tests were run** as part of this audit — findings are from static code analysis only.
