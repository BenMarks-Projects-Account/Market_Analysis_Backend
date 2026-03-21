# BenTrade Decision System — Current State Reference

> **Purpose**: Durable context file for future model/copilot use. Describes BenTrade's actual implementation state as of 2026-03-20. Treat as source-of-truth context; update whenever architecture materially changes.

---

## 1. Executive Summary

### What BenTrade Is
BenTrade is an options-income and stock-swing trading analysis platform for liquid index ETFs (SPY, QQQ, IWM, DIA) and large-cap equities. It combines deterministic quantitative analysis (scanners, scoring, risk gates) with LLM-based reasoning (regime analysis, final trade decisioning, active trade reassessment) to identify and evaluate trade setups.

### What It Does Well
- **V2 Options Scanner Pipeline**: 6-phase, 4-family, fully traceable candidate construction with immutable rejection taxonomy, diagnostics retention, and per-phase survival counts. Production-grade data integrity (None over incorrect numbers, Tradier as single source of truth).
- **Market Intelligence Architecture**: 6 deterministic engines with normalized output contract, conflict detection, composite summarization, and freshness tracking. Clean separation of concerns (breadth ≠ volatility ≠ flows).
- **Workflow Isolation**: Stock and Options workflows are fully independent with file-backed stage artifacts, atomic publish, pointer-based discovery, and lineage preservation. No shared mutable state.
- **Data Quality Infrastructure**: Confidence framework with additive penalties, proxy labeling, quote validation at multiple layers, POP source attribution, and degradation cascading.
- **Prompt Architecture Layering**: Clear separation: regime analysis (raw-inputs only, no anchoring) → strategy scoring → TMC final decision → active trade reassessment. Each layer has documented inputs and output contracts.

### What Limits Trade Quality Today
1. **No credibility gating on options candidates** — garbage-to-the-top problem when sorting by unbounded EV (recently added penny/delta/bid gates, but no minimum EV threshold, no IV-based filtering, no regret-based selection).
2. **Options workflow has no model analysis layer** — stock workflow runs per-candidate LLM review with BUY/PASS + conviction scoring; options workflow ranks by math only.
3. **Flows & Positioning engine is 100% VIX-derived proxy** — no real CFTC COT, ETF fund flows, dealer gamma, or sentiment survey data.
4. **No event calendar engine** — FOMC, CPI, earnings dates not integrated as first-class gates.
5. **Strategy prompts act as BUY/PASS decisioners** — conflates setup identification with final portfolio-level approval.
6. **No prediction-vs-outcome calibration loop** — decisions are not tracked against subsequent performance.
7. **Static policy thresholds** — no regime-adaptive tuning of gates, filters, or concentration limits.

---

## 2. Current Decision System Map

```
┌──────────────────────────────────────────────────────────────────────┐
│  MARKET DATA LAYER                                                    │
│  Tradier (chains, quotes, bars) → Finnhub/Polygon (fallback/context) │
│  FRED (yields, VIX history, credit spreads, macro)                    │
└──────────────────────────┬───────────────────────────────────────────┘
                           ↓
┌──────────────────────────────────────────────────────────────────────┐
│  MARKET INTELLIGENCE RUNNER (scheduled ~5min)                         │
│  6 Engines → Normalize → Composite → Conflict Detection              │
│  → Regime Model (LLM, raw-inputs only)                               │
│  → Publish market_state.json + latest.json pointer                    │
└──────────────────────────┬───────────────────────────────────────────┘
                           ↓
              ┌────────────┴────────────┐
              ↓                         ↓
┌─────────────────────────┐  ┌─────────────────────────┐
│ STOCK OPPORTUNITY RUNNER │  │ OPTIONS OPPORTUNITY      │
│ (8 stages)               │  │ RUNNER (5 stages)        │
│                          │  │                          │
│ 1. load_market_state     │  │ 1. load_market_state     │
│ 2. resolve_scanners      │  │ 2. scan (V2 families)    │
│ 3. run_stock_scanners    │  │ 3. validate_math         │
│ 4. aggregate/dedup       │  │ 4. enrich_evaluate       │
│ 5. enrich/filter/rank    │  │    (credibility gate)    │
│ 6. market_picture        │  │ 5. select_package        │
│ 7. model_analysis (LLM)  │  │                          │
│ 7b. model_filter/rank    │  │ [NO MODEL ANALYSIS]      │
│ 8. package_output        │  │                          │
└────────────┬────────────┘  └──────────┬──────────────┘
             ↓                          ↓
       output.json + latest.json pointer (per-workflow)
             ↓                          ↓
┌──────────────────────────────────────────────────────────┐
│  TMC EXECUTION SEAM                                       │
│  Read models: StockOpportunityReadModel,                  │
│               OptionsOpportunityReadModel                 │
│  API: /api/tmc/workflows/{stock|options}/{run|latest}     │
└──────────────────────────┬───────────────────────────────┘
                           ↓
┌──────────────────────────────────────────────────────────┐
│  TMC FRONTEND (Vanilla JS)                                │
│  normalizeStockCandidate() → renderStockCard()            │
│  normalizeOptionsCandidate() → buildOptionsCard()         │
│  Active trades: separate pipeline + buildActiveTradeCard()│
└──────────────────────────────────────────────────────────┘
```

### Key Architectural Boundaries
- **Market state** is the single shared data dependency (all workflows consume the same MI artifact via pointer)
- **Stock and Options workflows never read each other's artifacts**
- **All stage artifacts are JSON files** — inspectable, replayable, auditable
- **Atomic publish** — write-to-tmp + rename prevents half-written reads
- **TMC is a thin execution seam** — triggers runners, reads output.json via pointer, exposes read models to frontend

---

## 3. Scanner & Candidate Architecture

### 3.1 Stock Scanners

**Four active scanners**, all following the same pattern:

| Scanner Key | Thesis | Scoring (0–100) |
|---|---|---|
| `stock_pullback_swing` | Long pullback off support with confirmation | Strategy-specific sub-scores |
| `stock_momentum_breakout` | Long above 55D resistance, volume + trend | breakout(35) + volume(25) + trend(20) + base(20) |
| `stock_mean_reversion` | Long bounce off oversold (RSI 25–35) | oversold(40) + stabil(25) + room(20) + liq(15) |
| `stock_volatility_expansion` | Long explosion from compression breakout | expansion(40) + compress(25) + confirm(20) + risk(15) |

**Universe**: ~150–400 large/mid-cap stocks. Explicitly excludes index ETFs (SPY, QQQ, IWM, etc.).

**Data source**: Tradier OHLCV bars (async, semaphore-limited to avoid rate limits).

**Aggregation**: `StockEngineService` runs all 4 sequentially, ranks by composite_score DESC, returns top-9. Multi-scanner provenance tracked via `source_scanners` array.

**Output**: Normalized to 27-field canonical contract via `scanner_candidate_contract.py`.

### 3.2 Options Scanner V2 Pipeline

**Four families, 11 scanner keys:**

| Family | Scanner Keys | Legs |
|---|---|---|
| Vertical Spreads | `put_credit_spread`, `call_credit_spread`, `put_debit`, `call_debit` | 2 |
| Iron Condors | `iron_condor` | 4 |
| Butterflies | `butterfly_debit`, `iron_butterfly` | 3–4 |
| Calendars/Diagonals | `calendar_call_spread`, `calendar_put_spread`, `diagonal_call_spread`, `diagonal_put_spread` | 2 (multi-expiry) |

**Symbols**: SPY, QQQ, IWM, DIA (4 symbols × 11 keys = 44 scanner runs).

**6-Phase Pipeline** (per scanner key per symbol):

| Phase | Name | Purpose |
|---|---|---|
| A | Narrowing | DTE window, option-type, moneyness, strike distance filters |
| B | Construction | Family-specific leg generation (O(n²) pairs with safety caps) |
| C | Structural Validation | Leg count, sides, option types, expiry match, width, pricing sanity |
| D | Quote & Liquidity | Bid/ask presence, inverted quotes, OI/volume presence |
| D2 | Trust Hygiene | Negative bids/asks, spread pricing impossibilities, zero OI/volume, dedup |
| E | Recomputed Math | net_credit/debit, max_profit/loss, width, POP, EV, RoR, Kelly, breakevens |
| F | Normalization | Set passed/downstream_usable, contract_version, generated_at |

**Key data contracts**: `V2Candidate` → `V2Leg`, `V2RecomputedMath`, `V2Diagnostics` (with `reject_reasons`, `warnings`, `pass_reasons` — immutable taxonomy).

**Rejection taxonomy**: Stable codes (never renamed, only add). Examples: `v2_missing_quote`, `v2_inverted_quote`, `v2_zero_oi`, `v2_impossible_max_loss`, `v2_exact_duplicate`.

**Generation safety**: Vertical spreads capped at 50K pairs; iron condors use √cap per side to prevent memory explosion on cross-product.

**Calendar/diagonal deferred fields**: `max_profit`, `breakeven`, `POP`, `EV` set to None with explanatory notes (path-dependent, requires IV term structure modeling).

### 3.3 Options Workflow Post-Scan Processing

After V2 pipeline produces passed candidates:

1. **Validate math** — surface structural + math validation summaries
2. **Credibility gate** (3 checks, recently added):
   - `penny_premium`: max(net_credit, net_debit) < $0.05 → reject
   - `zero_delta_short`: pop ≥ 0.995 (all shorts have delta ≈ 0) → reject
   - `all_legs_zero_bid`: every leg bid = 0 (unfillable) → reject
3. **Rank**: EV DESC → RoR DESC → symbol ASC
4. **Select top-N** (default 30)

### 3.4 Quality Notes

**Stock scanners**: Well-structured scoring with deterministic sub-component breakdown. Binary universe (in/out), no continuous quality weighting. No IV-based filtering. Setup quality 0–100 is scanner-anchored, not independently verified.

**Options scanners**: Structural integrity is strong (6-phase validation), but:
- No minimum EV threshold beyond credibility gate
- No IV regime awareness (not adjusting DTE/width preferences by vol regime)
- No event proximity gate (earnings, FOMC within DTE window)
- Ranking by raw EV favors large-width / high-notional candidates regardless of trade quality
- Calendar/diagonal families produce None for key metrics (POP, EV) — cannot be compared with verticals in unified ranking

### 3.5 Legacy Strategy Plugins

Six strategy plugins exist in `app/services/strategies/` (credit_spread, debit_spreads, iron_condor, butterflies, calendars, income). These follow a 4-phase pattern (build → enrich → evaluate → score) and serve the older pipeline architecture. The V2 scanner families are the current production path for options scanning; strategy plugins remain active for some direct-analysis flows.

---

## 4. Prompt Stack Inventory

### 4.1 Strategy Prompts (Per-Setup Scoring)

**File**: `common/stock_strategy_prompts.py`

| Attribute | Detail |
|---|---|
| **Purpose** | Score individual stock setups: "Is this a genuine BUY edge, or noise?" |
| **Role** | Currently acts as **BUY/PASS decisioner** (should arguably be analysis-only) |
| **Dispatch** | `build_stock_strategy_user_prompt(strategy_id, candidate)` → 4 per-strategy builders |
| **Strategies** | pullback_swing, momentum_breakout, mean_reversion, volatility_expansion |
| **Inputs** | Full candidate dict: symbol, price, composite_score, score_breakdown, thesis, metrics (trend, pullback, momentum, liquidity, volatility), optional market_picture_context |
| **Output** | `recommendation` (BUY\|PASS), `score` (0–100), `confidence` (0–100), `summary`, `key_drivers`, `risk_review`, `engine_vs_model`, `data_quality` |
| **Issues** | Generic descriptions; no active-trade awareness; always assumes fresh entry; missing regime-adaptive thresholds |

### 4.2 TMC Final Decision Prompt (Portfolio-Level)

**File**: `common/tmc_final_decision_prompts.py`

| Attribute | Detail |
|---|---|
| **Purpose** | Final go/no-go for a single trade. "Portfolio manager making real allocation decisions." |
| **Role** | **Final BUY/PASS decisioner** — output directly influences portfolio entry |
| **Inputs** | Candidate + full 6-engine market picture + regime context |
| **Output** | `decision` (EXECUTE\|PASS), `conviction` (0–100), `decision_summary`, `technical_analysis`, `factors_considered` (structured: category, factor, assessment, weight, detail), `market_alignment`, `risk_assessment`, `what_would_change_my_mind`, `engine_comparison` |
| **Issues** | Requires full market context; degrades when data incomplete. No position sizing. No portfolio-collision detection. Missing event calendar input. |

### 4.3 Regime Analysis Prompt (Market Intelligence)

**File**: `common/model_analysis.py` → `analyze_regime()`

| Attribute | Detail |
|---|---|
| **Purpose** | Independent market regime assessment (3 dimensions: Structural, Tape, Tactical) |
| **Role** | Market context decisioner — not a trade recommendation |
| **Design** | **Raw-inputs only** — deliberately excludes engine-derived labels/scores to prevent anchoring |
| **Output** | `risk_regime_label`, `trend_label`, `vol_regime_label`, `structural/tape/tactical_assessment`, `key_drivers`, `what_works`, `what_to_avoid`, `confidence` |
| **Issues** | Size budget <4000 chars forces pillar trimming. Model may over-infer from limited data. |

### 4.4 Active Trade Reassessment Prompt

**File**: `app/services/active_trade_pipeline.py` → `run_model_analysis()`

| Attribute | Detail |
|---|---|
| **Purpose** | Should an open position HOLD, REDUCE, CLOSE, or escalate for URGENT_REVIEW? |
| **Role** | Position management decisioner |
| **Inputs** | Reassessment packet: trade identity, position state (P&L, current price), market context (regime, VIX), technical indicators, existing monitor evaluation, engine health score |
| **Output** | `recommendation`, `conviction` (0–1), `rationale_summary`, `key_supporting_points`, `key_risks`, `market_alignment`, `portfolio_fit`, `event_sensitivity`, `suggested_next_move` |
| **Issues** | Depends on monitor + regime caches (45s TTL) being fresh. No cross-correlation detection for correlated positions. |

### 4.5 Prompt Overlap Analysis

| Overlap | Assessment |
|---|---|
| **Stock Strategy ↔ TMC** | **High overlap**: Both score same setup metrics. TMC adds portfolio + regime layer. Stock prompt is 1st-pass, TMC is refined 2nd-pass. No contradiction risk if model is consistent, but duplicative scoring effort. |
| **Regime ↔ Market Picture** | **By design**: Regime output feeds market_picture consumed by TMC/stock prompts. Not duplicated. |
| **TMC ↔ Active Trade** | **Orthogonal**: TMC = entry decisions; Active Trade = position management. No shared data paths. |
| **Decision Policy ↔ Model** | **Sequential**: Policy gates (guardrails) then model reasons (conviction). Policy block → model forced to PASS. |

### 4.6 Missing Prompt Layers

- **Portfolio rebalancing**: No prompt asks "trim position X to fund position Y"
- **Cross-underlying correlation**: No prompt checks correlated asset concentration
- **Event calendar integration**: No prompt incorporates earnings/FOMC/CPI proximity
- **Options trade decisioning**: No LLM prompt evaluates options candidates (stock-only)
- **Dynamic threshold adaptation**: All policy thresholds are static constants

---

## 5. Market Intelligence Engine Inventory

### 5.1 Engine Summary Table

| Engine | Weight | Data Quality | Proxy % | Confidence Cap | Consumer Alignment |
|---|---|---|---|---|---|
| **Breadth & Participation** | 25% | High (Tradier direct) | ~5% | ~85 | Options income: HIGH |
| **Volatility & Options** | 25% | Medium (VIX direct, IV proxy) | ~20% | ~80 | Options income: HIGH |
| **Cross-Asset Macro** | 25% | Medium (FRED EOD+1) | ~15% | ~75 | Regime context: HIGH |
| **Flows & Positioning** | 20% | **LOW (100% proxy)** | **100%** | **~55** | Fragility check: MEDIUM |
| **Liquidity & Conditions** | 15% | Medium (FRED + Tradier) | ~10% | ~80 | Execution risk: HIGH |
| **News & Sentiment** | 10% | Medium (keyword-based) | ~20% | ~70 | Sentiment confirmation: MEDIUM |

### 5.2 Breadth & Participation Engine
- **Answers**: "How broad, durable, and trustworthy is the current market move?"
- **5 Pillars**: Participation Breadth (25%), Trend Breadth (25%), Volume Breadth (20%), Leadership Quality (20%), Participation Stability (10%)
- **Inputs**: Tradier bulk quotes (A/D counts, new highs/lows), daily bars (MAs), SPY vs RSP (EW/CW gap)
- **Strengths**: Direct market data, institutional conceptual base, intraday responsive, explainable scoring
- **Weaknesses**: Static 100-stock universe (survivorship bias), no point-in-time constituents, partial NVD, breadth thrust deferred, no sector rotation weighting

### 5.3 Volatility & Options Structure Engine
- **Answers**: "How is the market pricing fear and hedging? Is premium-selling favorable?"
- **5 Pillars**: Vol Regime (25%), Vol Structure (25%), Tail Risk & Skew (20%), Positioning & Options Posture (15%), Strategy Suitability (15%)
- **Inputs**: VIX (Tradier/Finnhub), VIX history (FRED), VVIX, SPY IV (derived), realized vol, CBOE SKEW (FRED), put skew (derived)
- **Strengths**: Direct VIX/VVIX, clean RV calculation, CBOE SKEW daily, strategy-specific scores (short call/put/bull-call/bear-call/collar/butterfly)
- **Weaknesses**: **No true VIX futures term structure** (inferred from spot vs average), put/call ratio is VIX-derived heuristic, SPY IV not intraday, no dealer gamma, CBOE SKEW delayed 1-2 days

### 5.4 Cross-Asset Macro Engine
- **Answers**: "Are rates, commodities, credit, and dollar confirming or contradicting the equity story?"
- **5 Pillars**: Rates & Yield Curve (25%), Dollar & Commodity (20%), Credit & Risk Appetite (25%), Defensive vs Growth (15%), Macro Coherence (15%)
- **Inputs**: FRED series (DGS10, DGS2, DFF, credit OAS, oil, gold proxy, copper monthly, USD trade-weighted)
- **Strengths**: Rich granular macro, VIX placed only in Pillar 3 (no double-counting), oil ambiguity documented, signal provenance explicit
- **Weaknesses**: Copper is **monthly** (30+ day staleness), gold is NASDAQ index proxy not spot, no real rates (nominal–inflation), all FRED series EOD+1 minimum

### 5.5 Flows & Positioning Engine — CRITICAL QUALITY GAP
- **Answers**: "Are flows and positioning supporting continuation or signaling fragility?"
- **5 Pillars**: Positioning Pressure (25%), Crowding/Stretch (20%), Squeeze/Unwind Risk (20%), Flow Direction (20%), Positioning Stability (15%)
- **CRITICAL**: **ALL inputs are VIX-derived heuristics**. No true CFTC COT, ETF fund flows, dealer gamma, AAII surveys, or short interest data.
  - Example: `futures_net_long_pct = max(10, min(90, 100 - vix * 2.2))`
  - Example: `put_call_ratio_proxy = 0.45 + vix * 0.023`
- **Gate mechanism**: Won't call "supportive" if crowding pillar < 40 (prevents false positives)
- **Confidence permanently capped ~55** due to proxy-only inputs

### 5.6 Liquidity & Financial Conditions Engine
- **Answers**: "How tight or supportive are financial conditions? Is liquidity available?"
- **5 Pillars**: Rates & Policy Pressure (25%), Financial Conditions Tightness (25%), Credit & Funding Stress (20%), Dollar/Global Liquidity (15%), Liquidity Stability (15%)
- **Inputs**: FRED yields, credit OAS, VIX, USD trade-weighted index, FCI proxy (derived)
- **Strengths**: Directly addresses execution risk, early warning for repo stress
- **Weaknesses**: No true FCI (uses composite proxy), neutral rate hardcoded (~3.0%), no SOFR-OIS spread, no repo market data

### 5.7 News & Sentiment Engine
- **Answers**: "What's the narrative tone? Is there consensus or contradiction?"
- **6 Components**: Headline Sentiment (30%), Negative Pressure (20%), Narrative Severity (15%), Source Agreement (10%), Macro Stress (15%), Recency Pressure (10%)
- **Inputs**: Finnhub + Polygon news APIs, keyword-based sentiment classification
- **Strengths**: Fast (5–30min), deterministic/explainable, dual-source agreement, category severity weighting
- **Weaknesses**: Keyword-based (no NLP/topic modeling), source lag, duplicate headline inflation, stale macro context

### 5.8 Missing Engines (Not Implemented)

| Engine | Would Answer | Gap Severity |
|---|---|---|
| **Event Calendar & Risk** | "What events land within the trade's DTE? What's the gamma positioning?" | **CRITICAL** for options income |
| **Index Trend Structure** | "Multi-timeframe trend alignment? Above key MAs?" | HIGH (partially covered by breadth trend pillar) |
| **Sector Relative Strength** | "Which sectors rotating in/out? Relative strength vs SPY?" | MEDIUM (partial sector scoring in breadth) |

### 5.9 Supporting Infrastructure

- **Market Context Service**: Centralized metric envelope (value, source, freshness, observation_date). Source priority: Tradier → Finnhub → FRED. 30s cache.
- **Confidence Framework**: Additive penalty schema (freshness -0.10, quality -0.15, conflict -0.05 to -0.30). Labels: high (0.80+), moderate (0.60–0.79), low (0.30–0.59), none (<0.30).
- **Market Composite**: 3D assessment: `market_state` (risk_on/neutral/risk_off), `support_state` (supportive/mixed/fragile), `stability_state` (orderly/noisy/unstable).
- **Conflict Detector**: Surfaces engine-vs-engine disagreements, candidate-vs-market contradictions, model-vs-engine conflicts.
- **Engine Output Contract**: All 6 engines normalize to identical 23-field shape (engine_key, score, label, confidence, summary, trader_takeaway, bull_factors, bear_factors, risks, regime_tags, engine_status, diagnostics, etc.).

---

## 6. Data Contracts & Gaps

### 6.1 Source of Truth Policy

| Data Type | Primary Source | Fallback | Notes |
|---|---|---|---|
| Option chains | Tradier | None (required) | All chain data must originate from Tradier |
| Option quotes (bid/ask/greeks) | Tradier | None | Execution-critical; no proxy allowed |
| Underlying price | Tradier | Finnhub | Intraday; Finnhub is acceptable fallback |
| Stock OHLCV | Tradier | None | Daily bars for scanner enrichment |
| VIX | Tradier quote | Finnhub → FRED | Triple-fallback; intraday when Tradier available |
| Treasury yields | FRED | None | DGS10, DGS2, DFF (EOD+1 lag always) |
| Credit spreads (IG/HY OAS) | FRED | None | EOD+1 lag |
| News headlines | Finnhub + Polygon | Either alone | Dual-source agreement scored |
| Macro commodities | FRED | None | Oil daily, gold proxy, copper monthly |
| Earnings calendar | **NOT AVAILABLE** | — | Critical gap |
| CFTC COT | **NOT AVAILABLE** | VIX heuristic | Critical quality gap |
| ETF fund flows | **NOT AVAILABLE** | VIX heuristic | Critical quality gap |

### 6.2 Trade Structure Completeness

**Present in Options V2 Candidate**:
- Identity: candidate_id, scanner_key, strategy_id, family_key, symbol
- Structure: legs[] (strike, side, type, bid, ask, mid, delta, gamma, theta, vega, iv, OI, volume), expiration, dte, underlying_price
- Math: net_credit, net_debit, max_profit, max_loss, width, pop, pop_source, ev, ev_per_day, ror, kelly, breakeven
- Validation: structural_checks, math_checks, quote_checks, liquidity_checks, reject_reasons, warnings
- Status: passed, downstream_usable, contract_version, scanner_version, generated_at

**Missing from Options Candidate**:
- `model_score` — no ML/LLM scoring layer for options
- `model_recommendation` — no BUY/PASS decision
- `decision_status` — no approve/reject envelope
- `trade_rationale` — no text explanation
- `risk_adjusted_score` — no portfolio-context-adjusted ranking
- `event_proximity` — no earnings/FOMC distance
- `iv_regime_context` — no vol regime annotation
- `portfolio_fit` — no concentration/correlation check

**Present in Stock Candidate (27 fields normalized)**:
- Full scanner metrics + market_picture_context + model_recommendation/score/confidence + risk_flags + thesis_summary
- Model analysis fields: model_review_summary, model_key_factors, model_caution_notes, model_technical_analysis

### 6.3 Missing Data Dimensions

| Category | Missing Fields | Impact |
|---|---|---|
| **Event/Calendar** | earnings_date, fomc_date, cpi_date, event_within_dte | Cannot gate trades landing on high-vol events |
| **Portfolio Context** | current_positions, symbol_exposure, strategy_concentration, correlation_matrix | Cannot assess portfolio fit in selection |
| **Historical Performance** | prior_trade_outcomes, strategy_win_rate, calibration_accuracy | Cannot learn from past decisions |
| **Risk Scenario** | stress_test_pnl, tail_risk_exposure, scenario_analysis | Cannot model downside scenarios |
| **IV Surface** | iv_smile, iv_term_structure, iv_rank_by_expiry | Cannot assess option cheapness/richness relative to history |

### 6.4 Stale/Proxy/Confidence Issues

| Issue | Location | Impact |
|---|---|---|
| Copper data monthly (FRED) | Cross-Asset Macro engine Pillar 2 | 30+ day staleness for "growth proxy" signal |
| VIX term structure inferred | Volatility engine Pillar 2 | Contango/backwardation confidence degraded (no true futures) |
| All Flows/Positioning inputs | Flows engine (all 5 pillars) | Entire engine is VIX heuristic; confidence capped at ~55 |
| Credit spreads EOD+1 | Cross-Asset + Liquidity engines | Always 1 trading day stale |
| CBOE SKEW EOD+2 | Volatility engine Pillar 3 | 1-2 day lag for tail risk signal |
| Gold price proxy (NASDAQ index) | Cross-Asset Macro Pillar 2 | Not spot gold; daily lag |
| USD trade-weighted weekly | Cross-Asset + Liquidity | Weekly publication; not DXY |
| News sentiment keyword-based | News engine | No semantic understanding; crude word matching |
| Put/call ratio heuristic | Volatility + Flows engines | VIX-derived, not exchange-reported |

---

## 7. Current Weak Points Affecting Trade Quality (Ranked)

### Rank 1: Options candidates have no model analysis layer
The stock workflow runs per-candidate LLM review with BUY/PASS + conviction scoring, then filters to keep only BUY recommendations. The options workflow ranks by raw math (EV, RoR) and takes top-N. There is no assessment of trade quality, market alignment, or risk-reward beyond quantitative metrics. This means options output has no conviction scoring, no market-fit assessment, and no reasoning trail.

### Rank 2: No event calendar as first-class gate
Neither stock nor options workflows know about upcoming FOMC, CPI, earnings, or other high-volatility events. A 5-DTE put credit spread expiring through FOMC has fundamentally different risk than one in a quiet week, but the system treats them identically. For options income strategies, this is the single most dangerous omission.

### Rank 3: Flows & Positioning engine is entirely proxy-derived
The engine that should answer "is the market fragile?" operates entirely on VIX heuristics. When VIX diverges from true positioning (e.g., VIX low but dealer gamma highly negative), the engine will give false reassurance. Confidence is permanently capped at ~55, but downstream consumers may not adequately discount this.

### Rank 4: Setup identification and final decisioning conflated
Stock strategy prompts produce BUY/PASS recommendations — they both identify the setup AND decide whether to trade it. The TMC final decision prompt then re-evaluates the same data. This creates duplicative decisioning with ambiguous authority. Strategy prompts should be setup-analysis specialists; TMC should be the sole approval gate.

### Rank 5: No prediction-vs-outcome calibration
Decisions are never compared against subsequent outcomes. Model scores of 75 vs 85 may not correspond to measurably different win rates. Without a calibration loop, the system cannot learn from its own history or verify that confidence levels are meaningful.

### Rank 6: Static policy thresholds in all regimes
Risk policy thresholds (min_pop 0.60, max_risk_per_trade $1000, max_bid_ask_spread_pct 1.5) don't adapt to market regime. In a low-vol risk-on environment, these may be too conservative; in a high-vol stress environment, they may be too loose. Decision policy checks similarly use hardcoded constants.

### Rank 7: Options ranking favors EV without quality weighting
Sorting by raw EV DESC favors large-width, high-notional candidates regardless of fill probability, liquidity, or strategy suitability. No regret-based selection (EV-to-risk, risk-adjusted EV), no IV-regime filtering, no width-preference scaling by vol regime. Calendar/diagonal families produce None for EV and cannot participate in unified ranking.

### Rank 8: VIX term structure is degraded
True VIX futures (VIX1, VIX2, VIX3) are not available. The system infers contango/backwardation from VIX spot vs 20-day average — a rough proxy that misses actual term structure shape (flat, steep, inverted front-month, etc.). For premium-selling strategies, term structure is a critical input.

### Rank 9: No cross-underlying correlation in selection
Neither options nor stock selection considers correlation between selected candidates. Selecting SPY put credit spread + QQQ put credit spread + IWM put credit spread provides no diversification despite appearing as 3 different symbols. Portfolio risk engine exists but is not integrated into the selection/ranking stage.

### Rank 10: Prompt token budgets not actively managed
Regime prompt uses <4000 char budget forcing aggressive pillar trimming. TMC prompt sends full 6-engine market picture without compression. No explicit token budgeting ensures prompts stay within model context limits while retaining the most decision-relevant information.

---

## 8. Recommended Direction of Evolution

These are architectural directions, not implementation plans. Each represents a structural shift that would meaningfully improve trade quality.

### 8.1 Separate Setup Generation from Final Decisioning
- **Strategy engines** (stock scanners + options V2 pipeline) become **candidate/setup specialists**. Their job is to find and characterize setups, not decide BUY/PASS.
- **TMC/final decision** becomes the sole **portfolio-quality approval gate**. It receives setup characterization + market picture + portfolio context + event risk and makes the only BUY/PASS decision.
- Stock strategy prompts should produce `setup_quality_assessment` (not recommendation), with structured factors (momentum, trend, risk).
- This eliminates the current duplicative BUY/PASS at strategy level then again at TMC level.

### 8.2 Event Risk as First-Class Gate
- New `event_calendar_risk` engine that identifies upcoming events (FOMC, CPI, NFP, earnings for underlying, dividend ex-dates) within configurable horizons.
- Options candidates landing through event dates get an `event_proximity` annotation.
- Decision layer treats high-impact events within DTE window as a mandatory gating signal.
- Implementation can start with a static calendar (FOMC/CPI dates hardcoded for current year) and graduate to API-fed.

### 8.3 Options Model Analysis Layer
- Add per-candidate LLM review to options workflow (parallel to stock workflow stage 7).
- Options prompt receives: candidate math, market picture, vol regime, event proximity, portfolio context.
- Output: recommendation (EXECUTE/PASS), conviction, risk assessment, strategy suitability in current regime.
- Filter-and-rank by model conviction, not raw EV.

### 8.4 Engine Consumer Awareness
- Market intelligence engines should produce outputs tagged for specific consumers.
- Volatility engine `strategy_scores` (short_call_favorable, short_put_favorable, etc.) is a good pattern — extend to all engines.
- Breadth engine should produce `options_income_signal` (broad participation declining = protective tone for writers).
- Each engine annotates whether its output is more relevant for stock swing or options income consumers.

### 8.5 Calibration & Outcome Logging
- Every BUY/EXECUTE decision gets a `decision_id` with timestamp, all inputs (scores, conviction, regime), and expected outcome metrics.
- Outcome logging captures: entry fill, max favorable excursion, max adverse excursion, exit fill, realized P&L, DTE at exit.
- Periodic calibration report: "When model said conviction 80+, actual win rate was X%."
- This is prerequisite for meaningful confidence semantics and threshold tuning.

### 8.6 Portfolio Context in Decision Layer
- Portfolio risk engine output (8 dimensions: directional, underlying, strategy, expiration, correlation, capital, greeks, event exposure) should be a required input to the final decision prompt.
- Selection/ranking stage should incorporate concentration penalties (selecting 3rd SPY spread gets diminishing rank).
- Regime-adaptive policy thresholds: tighter concentration limits in unstable markets, looser in orderly.

### 8.7 Trust & Confidence as Structural Properties
- Confidence should flow from data-quality → engine → composite → decision — not be independently estimated at each layer.
- Proxy-heavy engines (flows/positioning at 100% proxy) should have structurally lower influence on composite until real data sources are integrated.
- Engine confidence should modulate weight in composite (low-confidence engine gets reduced voting power).

### 8.8 IV Regime Awareness in Options Pipeline
- Rank adjustments based on vol regime: in high-IV environments, wider spreads with higher credits are more attractive; in low-IV, tighter structures with better fill probability dominate.
- IV rank/percentile for each underlying (SPY IV rank vs 1Y) as candidate annotation.
- Strategy suitability scoring from volatility engine should influence options candidate ranking.

---

## 9. Recommended Next-Step Workstreams (Ordered by Value)

### Workstream 1: Strategy Prompt Redesign
Convert stock strategy prompts from BUY/PASS decisioners to setup-analysis specialists. Remove recommendation/score; produce structured setup characterization. Consolidate final decisioning to TMC prompt only. Low risk, high clarity improvement.

### Workstream 2: Event Calendar Engine
Build event_calendar_risk engine. Start with static FOMC/CPI calendar. Annotate options candidates with event_proximity. Gate trades landing through high-impact events. Critical for options income risk management.

### Workstream 3: Options Model Analysis Layer
Add LLM review stage to options workflow (parallel to stock stage 7). Build options-specific decision prompt with vol regime context. Filter by conviction, not raw EV. Highest-impact change for options trade quality.

### Workstream 4: Calibration Logging Foundation
Instrument decision_id + input snapshot at BUY/EXECUTE decisions. Build outcome logging (entry fill, MFE/MAE, exit, P&L). Prerequisite for calibration reports and threshold tuning.

### Workstream 5: Index Trend Structure Engine
Separate engine for multi-timeframe trend alignment (SPY/QQQ/IWM relative to 20/50/200 MAs, cross-index divergence). Partially overlaps with breadth engine trend pillar but adds structured alignment scoring.

### Workstream 6: Sector Relative Strength Engine
Separate engine for sector rotation detection (XLV, XLY, XLE, etc. vs SPY). Informs stock scanner universe weighting and options underlying selection.

### Workstream 7: Flows/Positioning Data Upgrade
Replace VIX heuristics with real data sources: CFTC COT (weekly), ETF fund flows, AAII survey. Even one real data source would meaningfully improve confidence cap above ~55.

### Workstream 8: Current-State Prompt/Data Contract Cleanup
Formalize options workflow output contract in standards docs. Document TMC final decision contract. Align frontend field expectations with backend contracts. Remove any remaining dead-code paths.

### Workstream 9: Portfolio Context Integration
Feed portfolio risk engine output into decision layer. Add concentration penalty to selection/ranking. Implement regime-adaptive policy thresholds.

---

## 10. Guidance for Future Copilot / Model Prompts

### How to Use This File

1. **Treat as source-of-truth context** — before writing prompts that touch scanners, filters, engines, prompts, or trade decisions, read this file to understand the current state.

2. **Update when architecture materially changes** — if you add a new engine, modify the prompt stack, change the workflow stages, or alter data contracts, update the relevant sections here.

3. **Reference for gap analysis** — Section 7 (weak points) and Section 8 (target direction) should inform which improvements will most affect trade quality.

4. **Use Section 4 before modifying prompts** — the prompt stack inventory shows current roles, overlaps, and gaps. New prompts should not duplicate existing layers.

5. **Use Section 5 before modifying engines** — the engine inventory shows data sources, proxy status, and confidence limitations. New engines should fill documented gaps, not duplicate coverage.

6. **Use Section 6 for data contract work** — the completeness tables show exactly which fields are present, missing, or derivable.

### Key Conventions

- **Copilot instructions**: See `.github/copilot-instructions.md` for non-negotiable rules, testing constraints, and standards anchors.
- **Standards docs**: `docs/standards/*.md` define rejection taxonomy, preset philosophy, scanner contract, trade card spec, and data quality rules. These are binding.
- **File locations**: Workflows in `app/workflows/`, services in `app/services/`, V2 scanners in `app/services/scanner_v2/`, prompts in `common/`, models in `app/models/`, API routes in `app/api/`.
- **Testing**: Run from `BenTrade/backend/` directory. Only run targeted tests for changed files. Do not run full suite or chase unrelated failures.

### Anti-Patterns to Avoid

- **Don't add new engines that duplicate existing pillar coverage** — check the engine inventory first.
- **Don't add new prompt layers that make BUY/PASS decisions** — only TMC final decision should have approval authority (per target direction).
- **Don't treat proxy data as authoritative** — if data comes from VIX heuristics, label it as proxy and cap confidence accordingly.
- **Don't merge stock and options workflows** — they are intentionally independent with different stage counts and data models.
- **Don't bypass the V2 scanner pipeline** — all options candidate construction should go through the 6-phase pipeline for diagnostics and traceability.

---

## File History

| Date | Change | Author |
|---|---|---|
| 2026-03-20 | Initial creation — full current-state review | Copilot |
