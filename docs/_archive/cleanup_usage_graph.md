# BenTrade Usage Graph — Code Inventory

Generated: 2026-02-17 | Branch: `chore/app-cleanup-phase0`

---

## 1. Backend Route → Service Call Graph

21 route modules, **57 endpoints**, registered via `app.include_router()` in `main.py`.

### Route Modules

| # | Route Module | Prefix | Endpoints | Primary Service(s) |
|---|-------------|--------|-----------|---------------------|
| 1 | `routes_health.py` | `/api/health` | GET `/`, GET `/sources` | tradier/finnhub/yahoo/fred clients, base_data_service |
| 2 | `routes_options.py` | `/api/options` | GET `/{symbol}/expirations`, GET `/{symbol}/chain` | tradier_client, base_data_service |
| 3 | `routes_underlying.py` | `/api/underlying` | GET `/{symbol}/snapshot` | base_data_service |
| 4 | `routes_spreads.py` | `/api/spreads` | POST `/analyze` | spread_service |
| 5 | `routes_stock_analysis.py` | `/api/stock` | GET `/summary`, `/scan`, `/scanner`, `/watchlist`, `/macro`; POST `/watchlist` | stock_analysis_service, fred_client |
| 6 | `routes_signals.py` | `/api/signals` | GET `/`, GET `/universe` | signal_service |
| 7 | `routes_playbook.py` | `/api` | GET `/playbook` | playbook_service |
| 8 | `routes_strategies.py` | `/api/strategies` | GET `/`, POST+GET `/{id}/generate`, GET `/{id}/reports`, GET `/{id}/reports/{file}` | strategy_service |
| 9 | `routes_portfolio_risk.py` | `/api/portfolio/risk` | GET `/matrix` | tradier_client, risk_policy_service, base_data_service |
| 10 | `routes_risk_capital.py` | `/api/risk` | GET `/policy`, PUT `/policy`, GET `/snapshot` | risk_policy_service |
| 11 | `routes_regime.py` | `/api` | GET `/regime` | regime_service |
| 12 | `routes_recommendations.py` | `/api/recommendations` | GET `/top` | recommendation_service |
| 13 | `routes_trade_lifecycle.py` | `/api/lifecycle` | POST `/event`, GET `/trades`, GET `/trades/{key}` | trade_lifecycle_service |
| 14 | `routes_trading.py` | `/api/trading` | GET `/test-connection`, POST `/preview`, POST `/submit`, GET `/orders`, GET `/orders/{id}`, POST `/kill-switch/on`, POST `/kill-switch/off` | trading_service, trading_repository, tradier_client |
| 15 | `routes_active_trades.py` | `/api/trading` | GET `/active`, POST `/active/refresh`, GET `/positions`, GET `/orders/open`, GET `/account` | tradier_client, base_data_service |
| 16 | `routes_workbench.py` | `/api/workbench` | POST `/analyze`, GET+POST `/scenarios`, DELETE `/scenarios/{id}` | spread_service, validation_events |
| 17 | `routes_strategy_analytics.py` | `/api/analytics/strategy` | GET `/summary` | trade_lifecycle_service |
| 18 | `routes_reports.py` | *(none)* | GET `/api/reports`, GET `/api/reports/{file}`, GET `/api/generate`, POST `/api/model/analyze`, POST `/api/model/analyze_stock` | report_service, common.model_analysis (file I/O) |
| 19 | `routes_decisions.py` | `/api/decisions` | POST `/reject`, GET `/{report_file}` | decision_service |
| 20 | `routes_admin.py` | `/api/admin` | GET `/data-health`, GET `/data-workbench/trade/{key}`, GET `/data-workbench/trade`, GET `/data-workbench/search` | validation_events, data_workbench_service, base_data_service |
| 21 | `routes_frontend.py` | *(none)* | GET `/`, GET `/{path}` | *(static file serving)* |

---

## 2. Service Dependency Graph

```
main.py
 ├── TradierClient ─────────────────► routes_health, routes_options, routes_active_trades, routes_trading, routes_portfolio_risk
 ├── FinnhubClient ─────────────────► routes_health
 ├── YahooClient ───────────────────► routes_health
 ├── FredClient ────────────────────► routes_health, routes_stock_analysis
 ├── BaseDataService ───────────────► routes_health, routes_options, routes_underlying, routes_active_trades, routes_portfolio_risk
 │    └─ depends on: tradier, finnhub, yahoo, fred clients
 ├── SignalService ─────────────────► routes_signals
 │    └─ depends on: base_data_service, cache
 ├── SpreadService ─────────────────► routes_spreads, routes_workbench
 │    └─ depends on: base_data_service
 ├── StockAnalysisService ──────────► routes_stock_analysis
 │    └─ depends on: base_data_service, signal_service
 ├── StrategyService ───────────────► routes_strategies
 │    └─ depends on: base_data_service, risk_policy_service, signal_service, regime_service
 │    └─ loads plugins: credit_spread, debit_spreads, iron_condor, butterflies, calendars, income
 ├── PlaybookService ───────────────► routes_playbook
 │    └─ depends on: regime_service, signal_service
 ├── RecommendationService ─────────► routes_recommendations
 │    └─ depends on: strategy_service, stock_analysis_service, regime_service
 ├── ReportService ─────────────────► routes_reports (legacy)
 │    └─ depends on: base_data_service
 ├── RegimeService ─────────────────► routes_regime
 │    └─ depends on: base_data_service, cache
 ├── RiskPolicyService ─────────────► routes_risk_capital, routes_portfolio_risk
 ├── DecisionService ───────────────► routes_decisions
 ├── TradeLifecycleService ─────────► routes_trade_lifecycle, routes_strategy_analytics
 ├── ValidationEventsService ───────► routes_admin, routes_workbench
 ├── DataWorkbenchService ──────────► routes_admin (instantiated in route, not in main.py)
 ├── TradingService ────────────────► routes_trading
 │    └─ depends on: base_data_service, repository, paper_broker, live_broker
 ├── InMemoryTradingRepository ─────► routes_trading
 ├── PaperBroker ───────────────────► TradingService
 └── TradierBroker ─────────────────► TradingService
```

---

## 3. Backend Module Reachability

### All Python packages/modules

| Package | Module | Status | Imported By |
|---------|--------|--------|-------------|
| `app/api/` | All 21 route files | **USED** | `main.py` |
| `app/clients/` | `tradier_client.py` | **USED** | main, base_data_service, routes |
| `app/clients/` | `finnhub_client.py` | **USED** | main, base_data_service, routes_health |
| `app/clients/` | `yahoo_client.py` | **USED** | main, base_data_service, routes_health |
| `app/clients/` | `fred_client.py` | **USED** | main, base_data_service, routes_health, routes_stock_analysis |
| `app/models/` | `schemas.py` | **USED** | routes, spread_service, base_data_service |
| `app/models/` | `trade_contract.py` | **USED** | report_service, evaluation/, model_analysis, routes_reports |
| `app/utils/` | `cache.py` | **USED** | main, all clients, regime_service, signal_service |
| `app/utils/` | `computed_metrics.py` | **USED** | strategy_service, data_workbench_service, routes_reports |
| `app/utils/` | `dates.py` | **USED** | credit_spread, strategy_service, report_service, spread_service |
| `app/utils/` | `http.py` | **USED** | main, base_data_service, routes, tradier_broker, clients |
| `app/utils/` | `trade_key.py` | **USED** | decision_service, trade_lifecycle, strategy_service, data_workbench |
| `app/utils/` | `validation.py` | **USED** | base_data_service, model_analysis |
| `app/storage/` | `repository.py` | **USED** | main, trading/service |
| `app/trading/` | `broker_base.py` | **USED** | tradier_broker, paper_broker, service |
| `app/trading/` | `models.py` | **USED** | service, brokers, risk, routes_trading |
| `app/trading/` | `paper_broker.py` | **USED** | main |
| `app/trading/` | `risk.py` | **USED** | trading/service |
| `app/trading/` | `service.py` | **USED** | main |
| `app/trading/` | `tradier_broker.py` | **USED** | main |
| `app/services/` | All 15 service files | **USED** | main.py + route layer |
| `app/services/strategies/` | All 7 plugins + base | **USED** | strategy_service via `__init__.py` |
| `app/services/evaluation/` | All 4 modules | **USED** | report_service |
| `app/services/strategy_scanner/` | `defaults.py`, `__init__.py` | **DEAD** | Not imported by anything outside the package |
| `app/tools/` | `legacy_strategy_report_cleanup.py` | **REVIEW** | Only imported by its own test |
| `common/` | `agent.py` | **DEAD** | Never imported anywhere |
| `common/` | `model_analysis.py` | **USED** | routes_reports, common/utils |
| `common/` | `quant_analysis.py` | **USED** | Many services + strategies |
| `common/` | `utils.py` | **USED** | model_analysis (legacy shim) |

---

## 4. Frontend Route → Page Module Graph

18 SPA routes defined in `router.js`, mapping to page init functions.

| Route Key | View HTML | Init Function | Page JS Module |
|-----------|-----------|---------------|----------------|
| `home` | `home.html` | `initHome` | `pages/home.js` |
| `credit-spread` | `credit-spread.view.html` | `initCreditSpreads` | `pages/strategy_dashboard_shell.js` |
| `strategy-iron-condor` | `credit-spread.view.html` | `initStrategyIronCondor` | `pages/strategy_dashboard_shell.js` |
| `iron-condor` | `credit-spread.view.html` | `initStrategyIronCondor` | `pages/strategy_dashboard_shell.js` |
| `debit-spreads` | `credit-spread.view.html` | `initDebitSpreads` | `pages/strategy_dashboard_shell.js` |
| `butterflies` | `credit-spread.view.html` | `initButterflies` | `pages/strategy_dashboard_shell.js` |
| `calendar` | `credit-spread.view.html` | `initCalendar` | `pages/strategy_dashboard_shell.js` |
| `income` | `credit-spread.view.html` | `initIncome` | `pages/strategy_dashboard_shell.js` |
| `active-trade` | `active_trades.html` | `initActiveTrades` | `pages/active_trades.js` |
| `trade-testing` | `trade_workbench.html` | `initTradeWorkbench` | `pages/trade_workbench.js` |
| `stock-analysis` | `stock_analysis.html` | `initStockAnalysis` | `pages/stock_analysis.js` |
| `stock-scanner` | `stock_scanner.html` | `initStockScanner` | `pages/stock_scanner.js` |
| `risk-capital` | `risk_capital.html` | `initRiskCapital` | `pages/risk_capital.js` |
| `portfolio-risk` | `portfolio_risk.html` | `initPortfolioRisk` | `pages/portfolio_risk.js` |
| `trade-lifecycle` | `trade_lifecycle.html` | `initTradeLifecycle` | `pages/trade_lifecycle.js` |
| `strategy-analytics` | `strategy_analytics.html` | `initStrategyAnalytics` | `pages/strategy_analytics.js` |
| `admin-data-health` | `data_health.html` | `initDataHealth` | `pages/data_health.js` |
| `admin/data-workbench` | `admin_data_workbench.html` | `initAdminDataWorkbench` | `pages/admin_data_workbench.js` |

### Frontend JS Files (loaded via `<script>` in `index.html`)

| File | Global | Status |
|------|--------|--------|
| `app.js` | `window.BenTrade` | **REVIEW** — ~1,700 lines of legacy `initCreditSpread` dead code (lines 109–1816) |
| `router.js` | SPA router | **USED** |
| `api/client.js` | `BenTradeApi` | **USED** |
| `metrics/glossary.js` | `BenTradeMetrics.glossary` | **USED** |
| `strategies/defaults.js` | `BenTradeStrategyDefaults` | **USED** |
| `ui/home_loading_overlay.js` | `BenTradeHomeLoadingOverlay` | **USED** |
| `ui/notes.js` | `BenTradeNotes` | **USED** |
| `ui/source_health.js` | `BenTradeSourceHealthStore` | **USED** |
| `ui/tooltip.js` | `attachMetricTooltips` | **USED** |
| `ui/trade_card.js` | `BenTradeTradeCard` | **USED** |
| `stores/homeCache.js` | `BenTradeHomeCacheStore` | **USED** |
| `stores/sessionStats.js` | `BenTradeSessionStatsStore` | **USED** |
| `state/session_state.js` | `BenTradeSession` | **USED** |
| `utils/rateLimiter.js` | `BenTradeRateLimiter` | **USED** |
| `utils/tradeKey.js` | `BenTradeUtils.tradeKey` | **USED** |
| `pages/credit_spread.js` | `initCreditSpread` | **DEAD** — never reached (overridden by `strategy_dashboard_shell.js`) |
| `pages/strategy_dashboard_shell.js` | Multiple init fns | **USED** — primary strategy page module |
| All other `pages/*.js` | Their init fn | **USED** |

### Dead Dashboard HTML Files (not referenced by router)

| Dead File | Superseded By |
|-----------|--------------|
| `dashboards/active-trade-dashboard.view.html` | `active_trades.html` |
| `dashboards/credit-spread.html` | `credit-spread.view.html` |
| `dashboards/risk-capital-management-dashboard.view.html` | `risk_capital.html` |
| `dashboards/stock-analysis-dashboard.view.html` | `stock_analysis.html` |
| `dashboards/trade-testing-workbench.view.html` | `trade_workbench.html` |
| `dashboards/partials/under-construction-tron.view.html` | *(placeholder, only caller is dead code)* |

---

## 5. Duplicate / Overlapping Concerns

### Backend

| Area | Overlap | Notes |
|------|---------|-------|
| `report_service.py` (1,132 lines) vs `strategy_service.py` (987 lines) | Both generate reports, evaluate trades, score, and write JSON. `report_service` is the older SSE-based generator; `strategy_service` is the newer plugin-based system. | `routes_reports.py` → `report_service`; `routes_strategies.py` → `strategy_service` |
| `routes_reports.py` vs `routes_strategies.py` | Both serve report files. Legacy path: `GET /api/reports/{file}`; New path: `GET /api/strategies/{id}/reports/{file}` | Both normalize trades with the same pipeline |
| `app/services/evaluation/` | Only consumed by `report_service.py` (legacy) | If `report_service` is removed, this entire subpackage becomes dead |

### Frontend

| Area | Overlap | Notes |
|------|---------|-------|
| `iron-condor` vs `strategy-iron-condor` routes | Identical view + init function. Both resolve to `initStrategyIronCondor`. | Sidebar uses `iron-condor`; internal links use `strategy-iron-condor` |
| `credit_spread.js` vs `strategy_dashboard_shell.js` | `credit_spread.js` defines `initCreditSpread` but `strategy_dashboard_shell.js` defines `initCreditSpreads` which wins the fallback chain | Dead page module |
| `app.js` legacy `initCreditSpread` (~1,700 lines) | Entire original monolithic implementation, never reached since `strategy_dashboard_shell.js` always provides the init function first | ~1,700 lines of dead code |
