# BenTrade Architecture Snapshot

> Last updated: 2026-02-18

## High-Level Pipeline

```
Option chains (Tradier)  ──┐
Price history (Polygon)  ──┤
VIX / rates (FRED)       ──┤────▸ BaseDataService.get_analysis_inputs()
Company data (Finnhub)   ──┘              │
                                          ▼
                              StrategyService.generate()
                                    │
                       ┌────────────┼─────────────┐
                       ▼            ▼             ▼
                  Plugin         Plugin        Plugin …
              build_candidates  enrich      evaluate+score
                       │            │             │
                       └────────────┼─────────────┘
                                    ▼
                         normalize_trade()          ← app/utils/normalize.py
                         apply_metrics_contract()   ← app/utils/computed_metrics.py
                                    │
                                    ▼
                          results/<strategy>_analysis_*.json
2. `StrategyService.generate()` collects snapshots via `BaseDataService` (Tradier chains + Polygon/FRED/Finnhub context).
4. Plugin `enrich()` calls `common.quant_analysis.enrich_trades_batch()` — computes **CreditSpread-derived metrics** (max_profit, max_loss, break_even, POP, EV, RoR, kelly) and market-context features (IV/RV/regime/expected_move).
5. Plugin `evaluate()` applies gate filters; `score()` ranks by composite criteria.
6. `normalize_trade()` (in `app/utils/normalize.py`) builds canonical `computed`/`details`/`pills` dicts.
7. `apply_metrics_contract()` produces stable `computed_metrics` + `metrics_status` shape.
8. Reports written to `results/<strategy>_analysis_*.json`; workbench records to `data_workbench_records.jsonl`.
9. API endpoints (`/api/reports`, `/api/strategies`) serve normalized data to the SPA.

1. UI click on Reject removes the card immediately.
3. Backend appends decision to `results/decisions_<report_file>.json`.
4. On report load, UI fetches `/api/decisions/{report_file}` and applies rejects before rendering.

### 3) Model analysis
1. UI calls `/api/model/analyze` for a specific trade and source report.
2. Route wraps payload in `TradeContract` and calls `common.model_analysis.analyze_trade(...)`.
3. Writes `results/model_*.json` artifacts.

### 4) Trade lifecycle (preview → submit)
4. Backend routes to paper or live broker (`paper_broker.py` or `tradier_broker.py`).

### 5) Homepage Opportunity Engine
1. `home.js` loads latest reports from each strategy source.
2. Trades are merged, normalized via `normalizeOpportunity()`, and ranked.
4. Metrics resolved from `computed_metrics`, top-level fields, or `key_metrics` (multi-fallback).


All strategy identifiers go through `canonicalize_strategy_id()` in `app/utils/trade_key.py`.

**Canonical IDs** (the only values that appear in persisted outputs):
- `put_credit_spread`, `call_credit_spread` — credit spreads
- `put_debit`, `call_debit` — debit spreads
- `iron_condor` — iron condors
- `butterfly_debit`, `iron_butterfly` — butterflies
- `calendar_spread`, `calendar_call_spread`, `calendar_put_spread` — calendars
- `income`, `csp`, `covered_call` — income strategies
- `single`, `long_call`, `long_put` — directional

Legacy aliases (`put_credit`, `call_credit`, `credit_put_spread`, etc.) are mapped automatically; a `TRADE_STRATEGY_ALIAS_MAPPED` validation event is emitted when mapping occurs.

## Metrics Pipeline

### Stage 1: Enrichment (`common/quant_analysis.py`)
`enrich_trade()` now:
- Adds market-context features (IV, RV, regime, expected_move, strike_z, bid_ask_pct).
- **Creates a `CreditSpread` object** and computes core metrics: `max_profit_per_share`, `max_loss_per_share`, `break_even`, `pop_delta_approx`, `p_win_used`, `ev_per_share`, `return_on_risk`, `kelly_fraction`, `trade_quality_score`.
- Promotes per-share values to per-contract (`* contractsMultiplier`).

### Stage 2: Normalization (`app/utils/normalize.py`)
`normalize_trade()` is the single source of truth for trade output shape:
- Builds `computed` dict (max_profit, max_loss, pop, expected_value, return_on_risk, kelly_fraction, etc.)
- Builds `details` dict (break_even, dte, expected_move, market_regime, etc.)
- Builds `pills` dict (strategy_label, dte, pop, oi, vol, regime_label)
- Generates canonical `trade_key`
- Strips legacy flat fields at the API boundary via `strip_legacy_fields()`

### Stage 3: Metrics Contract (`app/utils/computed_metrics.py`)
`apply_metrics_contract()` produces:
- `computed_metrics`: unified dict resolving values from `computed`, `details`, and top-level fields
- `metrics_status`: `{ready: bool, missing_fields: [...]}`

### Stage 4: API Serialization
`routes_reports._normalize_report_trade()` applies the same pipeline when serving persisted reports.

## Canonical Trade Contract

`app/models/trade_contract.py` defines `TradeContract` with key fields used end-to-end:
- spread_type, underlying, short_strike, long_strike, dte, net_credit, width
- max_profit_per_share, max_loss_per_share, break_even, return_on_risk
- pop_delta_approx, p_win_used, ev_per_share, ev_to_risk, kelly_fraction
- trade_quality_score, iv, realized_vol, iv_rv_ratio, expected_move
- short_strike_z, bid_ask_spread_pct, composite_score, rank_score, rank_in_report
- model_evaluation (optional dict)

Helper API:
- `TradeContract.from_dict(d)`
- `TradeContract.to_dict()`

## Trade Card Pills

All strategy trade payloads include a canonical `pills` object for UI trade cards:

```json
{
  "strategy_label": "Put Credit Spread",
  "dte": 7,
  "pop": 0.87,
  "oi": 3368,
  "vol": 3488,
  "regime_label": "sideways trend, moderate volatility"
}
```

Calendar trades add `dte_front`, `dte_back`, `dte_label` (e.g. `"DTE 31/59"`).

## Rule Ownership

| Concern | File |
|---|---|
| Gates | `app/services/evaluation/gates.py` |
| Scoring | `app/services/evaluation/scoring.py` |
| Ranking | `app/services/evaluation/ranking.py` |
| Evaluation types | `app/services/evaluation/types.py` |
| Strategy plugins | `app/services/strategies/` |
| Trade normalization | `app/utils/normalize.py` |
| Trade key / canonicalization | `app/utils/trade_key.py` |
| Strategy ID resolution | `app/utils/strategy_id_resolver.py` |
| Computed metrics contract | `app/utils/computed_metrics.py` |
| Report conformance | `app/utils/report_conformance.py` |
| Input validation | `app/utils/validation.py` |
| HTTP helpers | `app/utils/http.py` |
| Quant enrichment + CreditSpread | `common/quant_analysis.py` |
| Validation events | `app/services/validation_events.py` |
| Risk policy | `app/services/risk_policy_service.py` |
| Data Workbench | `app/services/data_workbench_service.py` |
| Report management | `app/services/report_service.py` |
| Signal scoring | `app/services/signal_service.py` |
| Regime classification | `app/services/regime_service.py` |
| Homepage recommendations | `app/services/recommendation_service.py` |
| Spread analysis | `app/services/spread_service.py` |
| Stock analysis | `app/services/stock_analysis_service.py` |
| Trade lifecycle | `app/services/trade_lifecycle_service.py` |
| Playbook / trade ideas | `app/services/playbook_service.py` |
| Trading repository | `app/storage/repository.py` |

## Strategy Plugins

| Plugin | File | Strategies emitted |
|---|---|---|
| Credit Spread | `strategies/credit_spread.py` | `put_credit_spread`, `call_credit_spread` |
| Debit Spreads | `strategies/debit_spreads.py` | `put_debit`, `call_debit` |
| Iron Condor | `strategies/iron_condor.py` | `iron_condor` |
| Butterflies | `strategies/butterflies.py` | `butterfly_debit` |
| Calendars | `strategies/calendars.py` | `calendar_spread`, `calendar_call_spread`, `calendar_put_spread` |
| Income | `strategies/income.py` | `income`, `csp`, `covered_call` |

## Data Sources

| Source | Client | Data |
|---|---|---|
| Tradier | `clients/tradier_client.py` | Option chains, expirations, quotes, order execution |
| Polygon.io | `clients/polygon_client.py` | Daily OHLC price history, SMA/RSI/realized-vol/regime computations |
| FRED | `clients/fred_client.py` | VIX, treasury rates, macro indicators |
| Finnhub | `clients/finnhub_client.py` | Company profiles, news, peer data (fallback quote source) |

> **Note:** `yahoo_client.py` still exists but is vestigial. Polygon.io has replaced Yahoo Finance for all price history and technical indicator computations. Free Polygon tier provides 5 API calls/min with end-of-day data.

## Persisted Artifacts

Under `results/`:
- `<strategy>_analysis_*.json` — generated report payloads (stats/trades/diagnostics/source health)
- `analysis_*.json` — multi-strategy analysis reports
- `model_*.json` — model analysis outputs
- `decisions_*.json` — append-only reject decisions keyed by report file
- `data_workbench_records.jsonl` — trade-level workbench records with input snapshots
- `validation_events.jsonl` — validation/warning events log

## Diagnostic Logging

Trade metric computation logs available via Python logger `bentrade.enrich_trade`:

```python
import logging
logging.getLogger("bentrade.enrich_trade").setLevel(logging.DEBUG)
```

Logs CreditSpread metric values at DEBUG level; computation failures at WARNING level.

## Frontend Architecture

SPA with no framework — vanilla JS modules.

| Component | Path |
|---|---|
| App shell | `frontend/index.html` |
| Router | `frontend/assets/js/router.js` |
| Dashboard views | `frontend/dashboards/*.html` / `*.view.html` |
| Trade card builder | `frontend/assets/js/ui/trade_card.js` |
| Strategy card config | `frontend/assets/js/config/strategy_card_config.js` |
| Option trade card model | `frontend/assets/js/models/option_trade_card_model.js` |
| Home / Opportunity Engine | `frontend/assets/js/pages/home.js` |
| Strategy dashboard shell | `frontend/assets/js/pages/strategy_dashboard_shell.js` |
| API client | `frontend/assets/js/api/client.js` |
| Strategy defaults | `frontend/assets/js/strategies/defaults.js` |
| Metrics glossary | `frontend/assets/js/metrics/glossary.js` |
| Homepage cache | `frontend/assets/js/stores/homeCache.js` |
| Session stats | `frontend/assets/js/stores/sessionStats.js` |
| Session state | `frontend/assets/js/state/session_state.js` |

### Trade Card Rendering (Config + Model Pattern)

Trade cards use a config-driven architecture so adding a new strategy requires only config, no new rendering code:

```
STRATEGY_CARD_CONFIG.forStrategy(id)     ← per-strategy field definitions
        │
        ▼
mapOptionTradeToCardModel(rawTrade)      ← 4-tier resolution & formatting
        │                                   computed → computed_metrics → details → root
        ▼
renderTradeCard(trade, idx)              ← trade_card.js building blocks
        │                                   (metricGrid, section, detailRows, pill)
        ▼
  <div class="trade-card">              ← DOM output
```

**`strategy_card_config.js`** — Per-strategy config defining `headerFields`, `coreMetrics[]`, `detailFields[]`, `requiredKeys[]`.
Each metric descriptor: `{ key, computedKey, detailsKey?, rootFallbacks[], label, format, toneOpts? }`.
Aliases (e.g. `put_credit_spread` → `credit_spread`) resolve automatically (max 3 hops).

**`option_trade_card_model.js`** — Maps raw API trades to clean view-models via `mapOptionTradeToCardModel(rawTrade, strategyHint)`.
4-tier metric resolution: `computed` → `computed_metrics` → `details` → root fallbacks.
Returns `{ header, strategyId, coreMetrics[], detailFields[], pills[], missingKeys[], hasAllRequired, _raw }`.
Debug logging behind `BENTRADE_DEBUG_TRADES=1` or `?debug_trades=1` URL param.

### Frontend Utilities
| Module | Path |
|---|---|
| Formatting helpers | `frontend/assets/js/utils/format.js` |
| Trade data accessor | `frontend/assets/js/utils/tradeAccessor.js` |
| Client-side trade key | `frontend/assets/js/utils/tradeKey.js` |
| Rate limiter | `frontend/assets/js/utils/rateLimiter.js` |
| Debug utilities | `frontend/assets/js/utils/debug.js` |

### UI Components
| Component | Path |
|---|---|
| Trade card | `frontend/assets/js/ui/trade_card.js` |
| Loading overlay | `frontend/assets/js/ui/home_loading_overlay.js` |
| Trade notes | `frontend/assets/js/ui/notes.js` |
| Source health display | `frontend/assets/js/ui/source_health.js` |
| Tooltips | `frontend/assets/js/ui/tooltip.js` |

Dashboards: Home, Credit Spread, Stock Analysis, Stock Scanner, Active Trades, Trade Lifecycle, Portfolio Risk, Risk Capital, Data Health, Admin Data Workbench, Strategy Analytics, Trade Workbench.

## Trading Execution Layer

```
              ┌─────────────────────────┐
              │   TradingService        │
              │   (app/trading/service) │
              └────────┬────────────────┘
                       │
          ┌────────────┼────────────────┐
          ▼            ▼                ▼
    BrokerBase     PaperBroker    TradierBroker
    (ABC)          (paper sim)    (live orders)
```

| Module | File |
|---|---|
| Broker ABC | `app/trading/broker_base.py` |
| Order models | `app/trading/models.py` (`OrderLeg`, `OrderTicket`, `BrokerResult`) |
| Paper broker | `app/trading/paper_broker.py` |
| Live broker | `app/trading/tradier_broker.py` |
| Risk checks | `app/trading/risk.py` |
| Trading service | `app/trading/service.py` |
| In-memory repository | `app/storage/repository.py` (`InMemoryTradingRepository`) |

The `InMemoryTradingRepository` provides thread-safe ticket, order, and idempotency-key storage for the trading flow.

## Strategy ID Resolution

Strategy string validation follows a two-layer approach:

1. **`app/utils/trade_key.py`** — `canonicalize_strategy_id()` maps aliases to canonical IDs and builds trade keys.
2. **`app/utils/strategy_id_resolver.py`** — `resolve_strategy_id()` is the single-entry boundary validator. Canonical IDs pass through unchanged; known aliases resolve with a `STRATEGY_ALIAS_USED` warning; unknown strings raise `StrategyResolutionError` (→ HTTP 400).

All inbound boundaries (scanner output, workbench lookup, report normalization, lifecycle events) go through `resolve_strategy_id()`.

## Test Suite

238 tests across 31 test files covering:
- Trade key canonicalization and strategy resolution
- Metric computation and enrichment validation
- Strategy metrics audit (per-strategy correctness)
- API route smoke tests
- Report conformance and normalization
- Ingress validation and payload contracts
- Trading workflow (preview → submit)
- E2E metric trace (API input → UI output)
- Decision service and persistence
- Polygon client integration
