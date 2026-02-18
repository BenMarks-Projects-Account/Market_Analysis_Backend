# BenTrade Architecture Snapshot

> Last updated: 2026-02-17

## High-Level Pipeline

```
Option chains (Tradier) ──┐
Price history (Yahoo)  ────┤
VIX / rates (FRED)     ────┤────▸ BaseDataService.get_analysis_inputs()
Company data (Finnhub) ────┘              │
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
                         _normalize_trade()
                         apply_metrics_contract()
                                    │
                                    ▼
                          results/<strategy>_analysis_*.json
                          data_workbench_records.jsonl
                                    │
                                    ▼
                        API routes ──▸ Frontend SPA
```

## Flows

### 1) Scan → enrich → evaluate → rank → UI
1. Frontend triggers scan via `/api/strategies/{id}/generate` (POST/SSE).
2. `StrategyService.generate()` collects snapshots via `BaseDataService` (Tradier chains + Yahoo/FRED/Finnhub context).
3. Strategy plugin `build_candidates()` creates raw candidate dicts.
4. Plugin `enrich()` calls `common.quant_analysis.enrich_trades_batch()` — this now computes **CreditSpread-derived metrics** (max_profit, max_loss, break_even, POP, EV, RoR, kelly) and market-context features (IV/RV/regime/expected_move).
5. Plugin `evaluate()` applies gate filters; `score()` ranks by composite criteria.
6. `_normalize_trade()` builds canonical `computed`/`details`/`pills` dicts.
7. `apply_metrics_contract()` produces stable `computed_metrics` + `metrics_status` shape.
8. Reports written to `results/<strategy>_analysis_*.json`; workbench records to `data_workbench_records.jsonl`.
9. API endpoints (`/api/reports`, `/api/strategies`) serve normalized data to the SPA.

### 2) Manual reject (persisted)
1. UI click on Reject removes the card immediately.
2. UI posts to `/api/decisions/reject` with `report_file` and `trade_key`.
3. Backend appends decision to `results/decisions_<report_file>.json`.
4. On report load, UI fetches `/api/decisions/{report_file}` and applies rejects before rendering.

### 3) Model analysis
1. UI calls `/api/model/analyze` for a specific trade and source report.
2. Route wraps payload in `TradeContract` and calls `common.model_analysis.analyze_trade(...)`.
3. Writes `results/model_*.json` artifacts.

### 4) Trade lifecycle (preview → submit)
1. UI calls `/api/trading/preview` with spread parameters.
2. Backend validates via `risk.py` gates, creates a preview ticket.
3. UI calls `/api/trading/submit` with `ticket_id` + `confirmation_token` + `idempotency_key`.
4. Backend routes to paper or live broker (`paper_broker.py` or `tradier_broker.py`).

### 5) Homepage Opportunity Engine
1. `home.js` loads latest reports from each strategy source.
2. Trades are merged, normalized via `normalizeOpportunity()`, and ranked.
3. TOP pick displayed in hero card; remaining in scrollable grid.
4. Metrics resolved from `computed_metrics`, top-level fields, or `key_metrics` (multi-fallback).

## Strategy Naming — Canonical IDs

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

### Stage 2: Normalization (`strategy_service._normalize_trade()`)
- Builds `computed` dict (max_profit, max_loss, pop, expected_value, return_on_risk, kelly_fraction, etc.)
- Builds `details` dict (break_even, dte, expected_move, market_regime, etc.)
- Builds `pills` dict (strategy_label, dte, pop, oi, vol, regime_label)
- Generates canonical `trade_key`

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
| Strategy plugins | `app/services/strategies/` |
| Trade key / canonicalization | `app/utils/trade_key.py` |
| Computed metrics contract | `app/utils/computed_metrics.py` |
| Quant enrichment + CreditSpread | `common/quant_analysis.py` |
| Validation events | `app/services/validation_events.py` |
| Risk policy | `app/services/risk_policy_service.py` |
| Data Workbench | `app/services/data_workbench_service.py` |

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
| Yahoo Finance | `clients/yahoo_client.py` | Price history, key stats |
| FRED | `clients/fred_client.py` | VIX, treasury rates, macro indicators |
| Finnhub | `clients/finnhub_client.py` | Company profiles, news, peer data |

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
| Home / Opportunity Engine | `frontend/assets/js/pages/home.js` |
| API client | `frontend/assets/js/api/client.js` |
| Strategy defaults | `frontend/assets/js/strategies/defaults.js` |

Dashboards: Home, Credit Spread, Stock Analysis, Stock Scanner, Active Trades, Trade Lifecycle, Portfolio Risk, Risk Capital, Data Health, Admin Data Workbench, Strategy Analytics, Trade Workbench.
