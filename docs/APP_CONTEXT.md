# BenTrade — Full Application Context

**Generated:** 2026-04-16
**Git commit:** `0ea8b48` (main / origin/main) — "New model and analysis"
**Branch:** `main`
**Repo root:** `C:\Users\benja\OneDrive\Desktop\GitHub_Projects\Market_Analysis_Backend`
**Purpose:** Drop this file into a new Claude chat for instant full-project context.

---

## 1. What This Application Does

BenTrade is a **personal institutional-grade trading platform** focused on two trade families: (1) options income (high-probability, risk-defined credit spreads, iron condors, butterflies, and calendars/diagonals on liquid index ETFs and a ~30-symbol mega-cap universe) and (2) stock swing trading (pullback, momentum breakout, mean reversion, volatility expansion) across a larger ~150–400-stock universe. The goal is moderate, consistent income with portfolio-level risk management — not aggressive speculation.

The platform combines **deterministic quantitative analysis** (scanners, Greeks, EV/POP/RoR math, regime engines) with **LLM-assisted reasoning** (regime interpretation, per-candidate EXECUTE/PASS decisioning, active-trade reassessment). Every LLM call is routed through a central `execute_routed_model()` / `model_router` seam that supports local LM Studio on the dev box (`localhost:1234`), a second LM Studio on the "model machine" (`192.168.1.143:1234`), and AWS Bedrock (`us.amazon.nova-pro-v1:0`) as a premium tier.

BenTrade does **not** do fundamental / DCF / comps / EVA valuation. That work lives in a separate project — the **Company Evaluator (CE)** — which runs on the model machine at `192.168.1.143:8100`. BenTrade's On-Demand Evaluator dashboard is a proxy-based UI that delegates analysis to CE and renders the result locally. The two backends are independent FastAPI processes with separate repos; BenTrade hunts **trades** (options/stock setups), CE hunts **companies** (small/mid cap → large/mega cap quality screens and valuation models).

---

## 2. Architecture Overview

### 2.1 System diagram

```
                                  Browser (vanilla-JS SPA)
                                  http://192.168.1.89:5000/
                                               │
                                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│  BenTrade backend  (FastAPI / uvicorn)   192.168.1.89:5000           │
│  backend/app/main.py  → 50+ routers under /api/*                     │
│                                                                      │
│  Workflows (backend/app/workflows/):                                 │
│   • ContinuousWorkflowOrchestrator  (MI → TMC Full Refresh → delay)  │
│   • MarketIntelligenceRunner        (6 engines + regime LLM)         │
│   • StockOpportunityRunner          (8 stages, 4 scanners + LLM)     │
│   • OptionsOpportunityRunner        (6 stages, V2 families + LLM)    │
│   • PortfolioBalancingRunner                                         │
│   • TMC bootstrap / service                                          │
│   • ActiveTradePipeline (7 stages)                                   │
│                                                                      │
│  Model router → MODEL_SOURCES + execute_routed_model()               │
└──────────┬────────────────┬───────────────┬───────────────┬──────────┘
           │                │               │               │
           ▼                ▼               ▼               ▼
     Tradier API      Finnhub API      FRED API      FMP / Polygon
     chains, quotes,  news, earnings,  macro, yields, historical OHLC,
     Greeks, orders,  econ calendar    credit spreads fundamentals
     positions (LIVE                                  (Polygon paid)
     + PAPER)                                                 │
                                                              │
                                                              ▼
                                           LM Studio local  http://localhost:1234
                                           LM Studio remote http://192.168.1.143:1234
                                           AWS Bedrock      us-east-1 / Nova Pro

                             ┌───────────── Cross-machine ───────────────┐
                             │   Company Evaluator (separate project)    │
                             │   FastAPI on 192.168.1.143:8100           │
                             │   /api/companies/*, /api/pipeline/*,      │
                             │   /api/valuation/{dcf,eva,comps},         │
                             │   /api/on-demand/analyze  (jobs)          │
                             └──────────────────┬───────────────────────┘
                                                │
                 BenTrade proxies to CE via /api/company-evaluator/*
                 (routes_company_evaluator.py — 30+ proxy endpoints)

                             ┌───────── NAS (192.168.1.149) ───────────┐
                             │  (Used by CE for snapshot/result        │
                             │  archival; BenTrade itself stores       │
                             │  artifacts locally under backend/data)  │
                             └─────────────────────────────────────────┘
```

### 2.2 Machines and services

| Machine        | IP              | Role                                          | Services / Ports                                    |
|----------------|-----------------|-----------------------------------------------|-----------------------------------------------------|
| Dev box        | `192.168.1.89`  | Primary dev, BenTrade backend + frontend      | BenTrade FastAPI `:5000`, LM Studio `:1234`         |
| Model machine  | `192.168.1.143` | Heavy model inference, Company Evaluator host | LM Studio `:1234`, Company Evaluator FastAPI `:8100`|
| NAS            | `192.168.1.149` | Long-term storage (CE-owned archives)         | SMB / file share                                    |

### 2.3 Technology stack

- **Python:** 3.11+ (requires-python = ">=3.11" in `pyproject.toml`)
- **Backend framework:** FastAPI 0.116.1 + uvicorn 0.35.0 (a stub `Flask==3.1.2` is still pinned but the live app is FastAPI)
- **HTTP client:** `httpx` 0.28.1 (async) + `requests` 2.31.0 (sync, inside `model_router.model_request`)
- **Data / math:** numpy 2.4.2, pandas 3.0.0, scipy 1.17.0
- **Cloud / LLM:** boto3 1.42.48 (AWS Bedrock)
- **Fallback scrape:** yfinance 0.2.54 (sector/industry only, non-authoritative)
- **Frontend:** **vanilla JS** (no framework), HTML partials per dashboard, single SPA shell (`frontend/index.html`) with a custom hash router (`frontend/assets/js/router.js`). CSS is hand-rolled in `frontend/assets/css/app.css` (~9.1k lines) + per-module CSS files.
- **Launcher:** PyInstaller `launcher.py` (Tkinter single-instance) → spawns `start_backend.ps1`
- **Storage:** JSON files on disk — `backend/data/workflows/`, `backend/data/market_state/`, `backend/data/snapshots/`. No SQL DB.

---

## 3. Project Structure

```
Market_Analysis_Backend/                  ← workspace root (this repo)
├── pyproject.toml                        ← Python 3.11+ project
├── ARCHITECTURE.md / CLEANUP_BASELINE.md
├── BenTrade/                             ← the app
│   ├── backend/
│   │   ├── start_backend.ps1 / .sh       ← uvicorn launcher (port 5000)
│   │   ├── launcher.py / launcher.spec   ← Tkinter + PyInstaller single-instance launcher
│   │   ├── requirements.txt              ← pinned runtime deps
│   │   ├── README.md                     ← backend user guide
│   │   ├── app/
│   │   │   ├── main.py                   ← FastAPI create_app(), registers ~50 routers
│   │   │   ├── config.py                 ← pydantic Settings, .env loading, runtime toggles
│   │   │   ├── model_sources.py          ← MODEL_SOURCES: local, model_machine, premium_online
│   │   │   ├── api/                      ← 50 route modules (routes_*.py)
│   │   │   ├── clients/                  ← tradier / finnhub / fred / fmp / futures / yahoo / coingecko
│   │   │   ├── services/                 ← ~110 service modules (engines, scanners, policies)
│   │   │   │   ├── scanner_v2/           ← options V2 pipeline (phases, families, hygiene, validation)
│   │   │   │   ├── strategies/           ← per-strategy builders (credit_spread, iron_condor, …)
│   │   │   │   ├── evaluation/           ← gates / ranking / scoring / types
│   │   │   │   ├── trading/              ← order_builder
│   │   │   │   └── _deprecated_pipeline/ ← kept for reference only; DO NOT use
│   │   │   ├── trading/                  ← Tradier broker, paper broker, risk, validator
│   │   │   ├── workflows/                ← runners + orchestrator + TMC
│   │   │   ├── models/                   ← schemas, snapshot manifest, trade_contract
│   │   │   ├── storage/                  ← InMemoryTradingRepository
│   │   │   ├── tools/                    ← CLI: capture_snapshot
│   │   │   └── utils/                    ← cache, http, normalize, market_hours, trade_key, etc.
│   │   ├── common/                       ← json_repair, model_analysis (legacy sync helpers)
│   │   ├── data/                         ← runtime artifacts (gitignored)
│   │   │   ├── workflows/{stock,options}_opportunity/  ← latest.json + run_<id>/
│   │   │   ├── market_state/                            ← MI artifacts + pointer
│   │   │   └── snapshots/tradier/                       ← option-chain snapshots
│   │   ├── results/                      ← per-strategy JSON reports
│   │   ├── diagnostics/ / requests/      ← request captures
│   │   ├── scripts/                      ← diagnostic scripts
│   │   └── tests/                        ← pytest (~238 tests, 31 files per README)
│   └── frontend/
│       ├── index.html                    ← SPA shell (header, sidebar, router mount)
│       ├── assets/
│       │   ├── css/                      ← app.css (9.1k), module-dashboard.css, on_demand_evaluator.css, price_chart.css, decision-response-card.css
│       │   ├── js/
│       │   │   ├── router.js             ← hash router + history stack
│       │   │   ├── app.js                ← tiny TradeTicket adapter shim
│       │   │   ├── api/client.js         ← BenTradeApi (jsonFetch, modelFetch, timedFetch)
│       │   │   ├── pages/                ← 29 page controllers (one per dashboard)
│       │   │   ├── ui/                   ← trade_card, trade_ticket, chat_drawer, banner_ticker, …
│       │   │   ├── stores/               ← dashboardCache, homeCache, marketContext, …
│       │   │   ├── components/           ← price_chart, pre_market_display, metric_formatter
│       │   │   ├── strategies/           ← profiles / defaults (UI-side strategy config)
│       │   │   ├── metrics/              ← tooltip_dictionary (1.2k-line glossary)
│       │   │   ├── models/               ← trade_ticket_model, stock/option trade_card_mapper
│       │   │   ├── state/ / config/ / utils/ / tests/
│       │   ├── glossary_content.json     ← 510-line in-app glossary
│       │   ├── branding/ / icons/
│       ├── dashboards/                   ← HTML partials (fetched by router)
│       │   └── partials/                 ← shared partials
│       └── tests/                        ← browser-runnable test HTMLs
├── docs/
│   ├── APP_CONTEXT.md                    ← this file
│   ├── architecture/bentrade_decision_system_current_state.md  ← anchor doc
│   ├── standards/                        ← canonical-contract, scanner-contract, rejection-taxonomy, presets, data-quality-rules, ui-tradecard-spec
│   ├── scanners/ / scoring/ / audit/
│   └── _archive/
└── scripts/                              ← ad-hoc scripts (rebuild-launcher, dump prompts, etc.)
```

---

## 4. Backend Architecture

### 4.1 Runner system

Runners are file-based, pointer-driven workflows. Each runner is isolated: it reads the previous stage from disk, writes stage artifacts, and atomically publishes an `output.json` + updates a `latest.json` pointer.

| Runner | File | Stages | Produces |
|---|---|---|---|
| **ContinuousWorkflowOrchestrator** | `workflows/continuous_workflow_orchestrator.py` | MI → TMC Full Refresh → delay → repeat | In-memory status; drives the other runners |
| **MarketIntelligenceRunner** | `workflows/market_intelligence_runner.py` | `collect_inputs → build_snapshot → run_engines → run_model_interpretation → assemble_market_state → publish_market_state` | `data/market_state/<ts>.json` + `latest.json` |
| **StockOpportunityRunner** | `workflows/stock_opportunity_runner.py` | 8 stages: `load_market_state → resolve_stock_scanner_suite → run_stock_scanner_suite → aggregate_dedup_candidates → enrich_filter_rank_select → append_market_picture_context → run_final_model_analysis → package_publish_output` | `data/workflows/stock_opportunity/latest.json` + per-run stage artifacts |
| **OptionsOpportunityRunner** | `workflows/options_opportunity_runner.py` | 6 stages: `load_market_state → scan → validate_math → enrich_evaluate → model_analysis → model_filter → select_package` | `data/workflows/options_opportunity/latest.json` + per-run stage artifacts |
| **PortfolioBalancingRunner** | `workflows/portfolio_balancing_runner.py` | Risk/concentration/Greek-budget balancing after stock+options+active runs | Balance recommendations |
| **ActiveTradePipeline** | `services/active_trade_pipeline.py` | 7 stages: load → market_context → build_packets → deterministic_engine → model_analysis → normalize → complete | `HOLD / REDUCE / CLOSE / URGENT_REVIEW` recs + optional close orders |

**Trigger model:** The `ContinuousWorkflowOrchestrator` (singleton) is started after the boot modal and loops MI → TMC Full Refresh with a configurable delay. API endpoints also support manual triggers (`/api/tmc/stock/run`, `/api/tmc/options/run`, etc.).

### 4.2 Market Intelligence engines (6)

Each engine has a service + data provider + `normalize_engine_output()` contract (`services/engine_output_contract.py`):

1. **Breadth & Participation** — `breadth_engine.py`, `breadth_service.py`, `breadth_data_provider.py`
2. **Volatility & Options** — `volatility_options_engine.py` + service + provider
3. **Cross-Asset / Macro** — `cross_asset_macro_engine.py` + service + provider (FRED-driven)
4. **Flows & Positioning** — `flows_positioning_engine.py` + service + provider (currently 100% VIX-derived proxy — flagged as improvement target in `bentrade_decision_system_current_state.md`)
5. **Liquidity & Financial Conditions** — `liquidity_conditions_engine.py` + service + provider
6. **News Sentiment** — `news_sentiment_engine.py`, `news_sentiment_service.py`

Additional signal providers: `institutional_13f_engine` / `institutional_13f_service`, `insider_catalyst_service`, `smart_money_service`, `pre_market_intelligence`.

Regime classification (`RISK_ON / NEUTRAL / RISK_OFF`) comes from `regime_service.py` consuming the composite of all six engine outputs.

### 4.3 Scanners

**Stock scanners (4)** — `pullback_swing_service.py`, `momentum_breakout_service.py`, `mean_reversion_service.py`, `volatility_expansion_service.py`. Aggregated by `stock_engine_service.py`; output normalized via `scanner_candidate_contract.py`.

**Options V2 scanner** — `services/scanner_v2/` with:
- **Families (4):** `vertical_spreads.py`, `iron_condors.py`, `butterflies.py`, `calendars.py` (11 scanner keys total)
- **Phases:** `phases.py` (Phase A–F pipeline)
- **Data narrowing:** `data/{chain,strikes,expiry,narrow,contracts}.py`
- **Hygiene:** `hygiene/{quote_sanity,liquidity_sanity,dedup}.py`
- **Validation:** `validation/{structural,math_checks,tolerances,contracts}.py`
- **Diagnostics:** `diagnostics/{builder,diagnostic_item,reason_codes}.py` — stable taxonomy
- **Managed EV + Migration + Verify + Comparison harness**

### 4.4 Data providers

| Provider | Client | Role | Source-of-truth for |
|---|---|---|---|
| **Tradier** | `clients/tradier_client.py` (async, 2 req/sec leaky bucket, 429 retry, dual LIVE+PAPER credentials) | Option chains, option quotes, Greeks, stock quotes, positions, orders, account | **Option chains, option quotes, execution pricing, positions, Greeks** |
| **FRED** | `clients/fred_client.py` | Treasury yields (DGS2/10/30), DFF, credit spreads (BAMLC0A0CM, BAMLH0A0HYM2), oil (DCOILWTICO), USD (DTWEXBGS), copper (PCOPPUSDM), VIXCLS, SKEW | **Macro economic indicators** |
| **Finnhub** | `clients/finnhub_client.py` | News headlines + sentiment, earnings calendar, economic event calendar (FOMC/CPI/NFP) | **News, economic calendar, earnings dates** |
| **FMP (Financial Modeling Prep)** | `clients/fmp_client.py` (3000 RPM cap) | Historical OHLC, fundamentals fallback | Used inside some scanners/contexts; CE is primary FMP consumer |
| **Polygon.io** | direct HTTP (no dedicated client) — paid tier | Historical OHLCV bars for technical indicators | **Historical OHLCV** (replaced Yahoo for reliability) |
| **Yahoo Finance** | `clients/yahoo_client.py` via yfinance | Sector / industry classification fallback | Non-authoritative |
| **Futures** | `clients/futures_client.py` | Futures quotes | Banner ticker live context |
| **CoinGecko** | `clients/coingecko_client.py` | Crypto sentiment | News/Sentiment dashboard only |

Every derived metric is tagged with provenance (`direct`, `derived`, `proxy`, `proxy_of_proxy`) in `SIGNAL_PROVENANCE`.

### 4.5 Broker integration (Tradier)

- **Dual credential sets**: `TRADIER_API_KEY_LIVE` / `TRADIER_ACCOUNT_ID_LIVE` / `TRADIER_ENV_LIVE=live` and `TRADIER_API_KEY_PAPER` / `TRADIER_ACCOUNT_ID_PAPER` / `TRADIER_ENV_PAPER=sandbox`.
- **Account-mode switch**: UI selector in TMC passes `account_mode=live|paper`; `trading/tradier_credentials.py` resolves the correct key set.
- **Execution gate**: `TRADIER_EXECUTION_ENABLED` (persisted in `data/runtime_config.json`) — when `false`, every order becomes a **DRY RUN** (payload logged only). `set_tradier_execution_enabled()` in `config.py` is the single toggle.
- **Order flow**: preview → confirm → submit, multi-leg builder in `trading/tradier_order_builder.py`. Close-order generation via `services/close_order_builder.py` (for CLOSE/REDUCE recs on active trades).
- **Routes**: `routes_trading.py` (preview/submit/orders/runtime-config/close-preview/close-submit), `routes_active_trades.py` (positions, monitor, close-position, model-analysis).

### 4.6 Company Evaluator integration

BenTrade does **not** run CE logic locally — it proxies. `routes_company_evaluator.py` exposes 30+ `/api/company-evaluator/*` endpoints that forward to CE at `http://192.168.1.143:8100` (or `http://localhost:8100` when the connection-mode toggle is set to `local`).

Key proxy endpoints (prefix `/api/company-evaluator`):
- `GET/POST /connection` — switch local/remote
- `GET /ranked` — ranked companies
- `GET /company/{symbol}` — full detail
- `POST /evaluate/{symbol}` — sync evaluation
- `POST /on-demand/analyze` → `GET /on-demand/jobs/{id}` → `GET /on-demand/jobs/{id}/result` — async job flow (used by the On-Demand Evaluator dashboard)
- `GET /on-demand/research-prompt/{symbol}` — deep-research prompt generator
- `GET /charts/{symbol}` — OHLC chart data
- `GET /valuation/dcf/{symbol}` / `/eva/{symbol}` / `/comps/{symbol}` + POST variants
- `GET /smart-money/{symbol}` — 13F smart-money data
- `GET /status` — pipeline + health
- `GET /admin/fmp-status`, `POST /universe/add`, `POST /crawl`

### 4.7 LLM integration and model routing

- **Legacy direct path**: `services/model_router.py` exposes `model_request()` (sync, `requests.post`) and `async_model_request()` (async, `httpx`). Both resolve endpoint via `get_model_endpoint()` → `model_state.get_model_source()` → `MODEL_SOURCES[key]`.
- **Provider-abstraction path**: `execute_with_provider()` targets a specific provider.
- **Distributed routing**: `route_and_execute()` returns `(result, trace)` — the trace records requested mode, attempted providers, fallback reasons.
- **Contract**: `services/model_routing_contract.py` defines enums — `ExecutionMode` (`LOCAL`, `MODEL_MACHINE`, `PREMIUM_ONLINE`, `LOCAL_DISTRIBUTED`, `ONLINE_DISTRIBUTED`), `Provider` (`LOCALHOST_LLM`, `NETWORK_MODEL_MACHINE`, `BEDROCK_TITAN_NOVA_PRO`), `ProviderState`, `FallbackReason`, `RouteResolutionStatus`, `ExecutionStatus`.
- **Adapters & registry**: `model_provider_adapters.py`, `model_provider_base.py`, `model_provider_registry.py`.
- **Policy & config**: `model_router_policy.py`, `model_routing_config.py`, `model_routing_integration.py`, `execution_mode_state.py`.
- **Telemetry**: `model_routing_telemetry.py`, `routing_dashboard_service.py`, `routing_dashboard_contract.py` — exposed via `/api/admin/health|system|recent|dashboard|execution-mode|refresh-config|circuit-breaker/*`.
- **Health**: `model_health_service.py` — provider ping + circuit-breaker.
- **Gate**: `model_execution_gate.py` — refuses execution below data-quality floor.
- **All system prompts** must include the anti-injection security preamble (per `.github/copilot-instructions.md`); conviction < 60 on EXECUTE/BUY → coerced to PASS; parse-failure default = 10.

---

## 5. Frontend Architecture

### 5.1 Dashboard inventory

Routes live in `frontend/assets/js/router.js`. Each route loads an HTML partial from `frontend/dashboards/` and calls `window.BenTradePages.init<Name>()`.

| Hash route | Partial (`dashboards/`) | Controller (`assets/js/pages/`) | Purpose |
|---|---|---|---|
| `#/home` | `home.html` (336 L) | `home.js` (4298 L) | Opportunity Engine homepage: regime, composites, top setups, session stats |
| `#/news-sentiment` | `news_sentiment.html` (1649 L) | `news_sentiment.js` (868 L) | News + macro headlines + sentiment engine output |
| `#/credit-spread` · `#/strategy-iron-condor` · `#/iron-condor` · `#/debit-spreads` · `#/butterflies` · `#/calendar` · `#/income` | `credit-spread.view.html` (63 L) | `strategy_dashboard_shell.js` (1739 L) | Shared strategy-card shell (Phase A–F scanner → TradeCard grid) |
| `#/active-trade` | `active_trades.html` (106 L) | `active_trades.js` (1439 L) | Tradier positions + monitor + model analysis + close-position |
| `#/trade-testing` | `trade_workbench.html` (119 L) | `trade_workbench.js` (729 L) | Data Workbench — trade-level input drill-down |
| `#/trade-management` | `trade_management_center.html` (104 L) | `trade_management_center.js` (4171 L) | **TMC — primary control surface** (see §5.3) |
| `#/stock-analysis` | `stock_analysis.html` (73 L) | `stock_analysis.js` (613 L) | Per-symbol stock snapshot + indicators |
| `#/stock-scanner` | `stock_scanner.html` (21 L) | `stock_scanner.js` (584 L) | Raw stock-scanner grid |
| `#/stocks/pullback-swing` · `#/stocks/momentum-breakout` · `#/stocks/mean-reversion` · `#/stocks/volatility-expansion` | `stock_strategy.html` (173 L) | `stock_pullback_swing.js` / `stock_momentum_breakout.js` / `stock_mean_reversion.js` / `stock_volatility_expansion.js` (each 206 L) | Per-strategy stock scanner views |
| `#/risk-capital` | `risk_capital.html` (137 L) | `risk_capital.js` (308 L) | Risk policy, position sizing |
| `#/portfolio-risk` | `portfolio_risk.html` (69 L) | `portfolio_risk.js` (171 L) | Portfolio matrix, concentration, Greek budgets |
| `#/trade-lifecycle` | `trade_lifecycle.html` (45 L) | `trade_lifecycle.js` (85 L) | Trade-event log |
| `#/strategy-analytics` | `strategy_analytics.html` (57 L) | `strategy_analytics.js` (159 L) | Per-strategy performance summary |
| `#/admin-data-health` | `data_health.html` (123 L) | `data_health.js` (693 L) | Provider health dashboard |
| `#/admin/data-workbench` | `admin_data_workbench.html` (40 L) | `admin_data_workbench.js` (270 L) | Admin Data Workbench |
| `#/admin/tooltip-test` | `tooltip_test.html` (268 L) | — | Tooltip QA |
| `#/admin/scanner-review` | `scanner_review.html` (135 L) | `scanner_review.js` (682 L) | Per-run scanner routing + candidate drill-down |
| `#/market/breadth` | `breadth_participation.html` (367 L) | `breadth_participation.js` (873 L) | Breadth engine detail |
| `#/market/volatility` | `volatility_options.html` (248 L) | `volatility_options.js` (663 L) | Volatility engine detail |
| `#/market/cross-asset` | `cross_asset_macro.html` (234 L) | `cross_asset_macro.js` (546 L) | Cross-asset macro detail |
| `#/market/flows` | `flows_positioning.html` (229 L) | `flows_positioning.js` (575 L) | Flows & positioning detail |
| `#/market/liquidity` | `liquidity_conditions.html` (226 L) | `liquidity_conditions.js` (615 L) | Liquidity & financial conditions detail |
| `#/notifications` | `notifications.html` (13 L) | `notifications.js` (162 L) | In-app notifications |
| `#/company-evaluator` | `company_evaluator.html` (373 L) | `company_evaluator.js` (3544 L) | CE ranked-universe browser (proxy to CE) |
| `#/on-demand-evaluator` | `on_demand_evaluator.html` (277 L) | `on_demand_evaluator.js` (2528 L) | **On-Demand CE analysis** (see §5.2) |

Standalone demos: `decision_response_demo.html`, `active_trade_pipeline.html`.

### 5.2 On-Demand Evaluator dashboard — detailed

File: `frontend/dashboards/on_demand_evaluator.html` (277 L) + `frontend/assets/js/pages/on_demand_evaluator.js` (2528 L) + `frontend/assets/css/on_demand_evaluator.css` (2375 L). Cache-busted `?v=20260413b`.

Flow (all endpoints are BenTrade proxy → CE):
1. User enters symbol → `POST /api/company-evaluator/on-demand/analyze` returns `job_id`.
2. Front-end polls `GET /api/company-evaluator/on-demand/jobs/{job_id}` every 2 s (15 s fetch timeout, tolerates 4 consecutive failures before showing "Connection Lost").
3. On complete, fetches `GET /api/company-evaluator/on-demand/jobs/{job_id}/result`.
4. Renders panels: **company header**, **quality signals** (smart-money / analyst / catalyst badges), **price chart** (`components/price_chart.js`, uses `GET /charts/{symbol}`), **valuation models** (DCF / EVA / Comps), **pillar breakdown**, **entry price targets**, **business profile**, **AI investment thesis**, **AI deep research analysis** (user-pastable), **raw financials**, **glossary panel** (driven by `frontend/assets/glossary_content.json`, 510 L).
5. Buttons: **Analyze**, **Deep Research Prompt** (calls `GET /on-demand/research-prompt/{symbol}` → opens a paste-into-chat prompt), **Add Analysis** (lets user paste back a chat model's research response as a panel), **Export PDF** (POST `/api/export/on-demand-pdf`).

### 5.3 Trade Management Center (TMC)

File: `frontend/dashboards/trade_management_center.html` (104 L) + `trade_management_center.js` (4171 L). This is the **primary user interface** for all workflows.

Sections:
1. **Control bar** — Full Refresh (runs Stock → Options → Active → Portfolio in parallel where possible), Run Stock, Run Options, Reset Providers (clears circuit breakers), Refresh, orchestrator status.
2. **Risk utilization bar** — live risk-budget bar.
3. **Stock Opportunities** — ranked stock candidates from `StockOpportunityRunner` latest, rendered via `trade_card.js`.
4. **Options Opportunities** — ranked options candidates from `OptionsOpportunityRunner` latest.
5. **Active Trades** — Tradier positions with recommendation badges from `ActiveTradePipeline`, close/reduce/modify actions.
6. **Portfolio Balance** — recommendations from `PortfolioBalancingRunner`.

Backend endpoints: `POST /api/tmc/stock/run`, `POST /api/tmc/options/run`, `GET /api/tmc/stock/latest`, `GET /api/tmc/options/latest`, `GET /api/tmc/{stock,options}/summary`, `POST /api/tmc/model/final-decision`, `POST /api/tmc/portfolio-balance/run`, `GET /api/tmc/diagnostics/ranking-audit`.

Account-mode (live/paper) selector is visible here and propagates to all position/close flows.

### 5.4 Other shared frontend pieces

- **`ui/trade_card.js`** (406 L) — single TradeCard primitive (per `docs/standards/ui-tradecard-spec.md`): footer always visible, collapse/expand preserved, tooltips via `TooltipProvider`.
- **`ui/trade_ticket.js`** (1116 L) — unified preview → confirm → submit modal (replaces the legacy `BenTradeExecutionModal`, kept as a shim in `app.js`).
- **`ui/chat_drawer.js`** (706 L) — contextual chat (talks to `/api/chat/contextual`).
- **`ui/banner_ticker.js`** — live futures / index ticker across the header.
- **`ui/tooltip.js` + `ui/ben_tooltip.js` + `metrics/tooltip_dictionary.js` (1225 L)** — unified tooltip system (all dashboards must use this, no one-offs).
- **`ui/decision_response_card.js`** — standardized decision-response rendering.
- **`stores/*.js`** — per-dashboard caches (`homeCache`, `dashboardCache`, `scanResultsCache`, `marketContext`, `playbookScoring`, `sessionStats`, `scannerOrchestrator`, `modelAnalysisStore`, `symbolUniverse`).
- **`api/client.js`** (700 L) — `BenTradeApi.{jsonFetch, modelFetch (185 s timeout), timedFetch}` with structured error handling + automatic source-health refresh on success.

### 5.5 Static assets

- `frontend/assets/glossary_content.json` (510 L) — in-app glossary.
- `frontend/assets/css/*.css` — ~14k total lines, hand-written.
- `frontend/assets/branding/`, `icons/favicon*` — app iconography.
- No bundler, no transpilation — files are served directly by `routes_frontend.py`.

---

## 6. API Endpoints

Router inclusion order and prefixes are set in `backend/app/main.py`. Admin and routing routers are mounted under `/api/admin`; the rest use their file-defined prefixes.

### 6.1 Core endpoints (selected)

| Method | Path | Handler file |
|---|---|---|
| GET | `/api/health` | `routes_health.py` |
| GET | `/api/health/sources` | `routes_health.py` |
| GET | `/api/options/{symbol}/expirations` | `routes_options.py` |
| GET | `/api/options/{symbol}/chain` | `routes_options.py` |
| GET | `/api/underlying/{symbol}/snapshot` | `routes_underlying.py` |
| GET | `/api/regime` · `/api/regime/proxies` | `routes_regime.py` |
| GET | `/api/playbook` | `routes_playbook.py` |
| GET/POST | `/api/strategies`, `/{id}/generate`, `/{id}/reports/…` | `routes_strategies.py` |
| POST | `/api/spreads/analyze` | `routes_spreads.py` |
| GET/POST | `/api/stock/scan` · `/quotes` · `/macro` · `/watchlist` · `/summary` · `/ticker-universe` · `/ticker-snapshot` | `routes_stock_analysis.py` |
| GET/POST | `/api/stock-strategies/{pullback-swing,momentum-breakout,mean-reversion,volatility-expansion,insider-catalyst,engine}` · `/execute` · `/execute/status` | `routes_stock_strategies.py` |
| POST | `/api/workbench/analyze` · `/scenarios` | `routes_workbench.py` |
| GET | `/api/signals` · `/universe` | `routes_signals.py` |
| GET | `/api/recommendations/top` | `routes_recommendations.py` |
| POST | `/api/decisions/reject` · GET `/{report_file}` | `routes_decisions.py` |
| GET/POST | `/api/trade-lifecycle/event`, `/trades`, `/trades/{key}` | `routes_trade_lifecycle.py` |
| GET/PATCH/POST | `/api/trading/status`, `/preview`, `/submit`, `/orders`, `/orders/{id}`, `/runtime-config`, `/validate`, `/build-payload`, `/close-preview`, `/close-submit`, `/test-connection` | `routes_trading.py` |
| GET/POST | `/api/active/…`, `/api/monitor`, `/api/monitor/narrative`, `/api/close-position`, `/api/positions`, `/api/orders/open`, `/api/account` | `routes_active_trades.py` |
| POST/GET | `/api/active-trade-pipeline/run`, `/results`, `/results/{id}`, `/runs` | `routes_active_trade_pipeline.py` |
| GET/POST | `/api/portfolio-risk/matrix` | `routes_portfolio_risk.py` |
| GET/PUT/POST | `/api/risk-capital/{policy,snapshot,size,state,validate,management-policies}` | `routes_risk_capital.py` |
| GET | `/api/strategy-analytics/summary` | `routes_strategy_analytics.py` |
| GET/POST | `/api/reports`, `/api/reports/{file}`, `/api/generate`, `/api/model/{analyze,analyze_regime,analyze_stock,analyze_stock_strategy,active-trade-analysis}` | `routes_reports.py` |

### 6.2 Market Intelligence engine endpoints

For each engine — `/api/breadth`, `/api/cross-asset-macro`, `/api/flows-positioning`, `/api/liquidity-conditions`, `/api/news-sentiment`, `/api/volatility-options`:
- `GET ""` — engine public output
- `GET /engine` — raw engine computation
- `POST /model` — run LLM interpretation

Plus `/api/market-picture/scoreboard`, `/history`, `/model-scores` (`routes_market_picture.py`) and `/api/market-intel/*` + `/api/admin/pillars/13f/*` (`routes_market_intel.py`).

### 6.3 TMC + orchestrator + refresh

| Method | Path | File |
|---|---|---|
| POST | `/api/tmc/stock/run` · `/options/run` · `/model/final-decision` · `/portfolio-balance/run` | `routes_tmc.py` |
| GET | `/api/tmc/{stock,options}/{latest,summary}` · `/diagnostics/ranking-audit` | `routes_tmc.py` |
| GET/POST | `/api/orchestrator/{status,start,stop,pause,resume,delay}` | `routes_orchestrator.py` |
| GET/POST | `/api/refresh/{state,pause,resume}` | `routes_refresh.py` |
| GET/POST | `/api/data-population/{status,trigger}` | `routes_data_population.py` |
| GET | `/api/scanner-review/routing` · `/runs/{id}/scanner-summary` · `/candidates` | `routes_scanner_review.py` |

### 6.4 Admin + routing

All under `/api/admin`:
- `/data-health`, `/data-workbench/trade/{key}`, `/data-workbench/search`
- `/platform/data-source` (GET/PUT), `/platform/snapshot-cleanup`, `/platform/model-source` (GET/POST)
- `/snapshots/capture`, `/snapshots`, `/snapshots/{trace_id}`
- `/health` (routing), `/system`, `/recent`, `/dashboard`, `/execution-mode`, `/refresh-config`, `/refresh-providers`, `/refresh-runtime`, `/circuit-breaker/{reset,status}`

### 6.5 Pre-market + market intel + specialty signals

- `/api/pre-market/{briefing,snapshots,snapshot/{inst},bars/{inst},vix-term-structure,health}` (`routes_pre_market.py`)
- `/api/market/{movers,sectors,pre-market-movers,upgrades-downgrades}` (`routes_market_intel.py`)
- `/api/signals/{congress,insider-clusters,unusual-options}` (`routes_specialty_signals.py`)
- `/api/sentiment/crypto` (`routes_sentiment.py`)
- `/api/calendar/{economic,earnings}`, `/api/news/market` (`routes_calendar_news.py`)

### 6.6 Chat / contextual / notifications / dev

- `POST /api/chat/contextual` · `GET /api/chat/starters/{context_type}` (`routes_contextual_chat.py`)
- `/api/notifications[…]` (`routes_notifications.py`)
- `POST /api/dev/capture` (`routes_dev.py`)
- `POST /api/export/on-demand-pdf` (`routes_export.py`)
- `GET /api/portfolio/equity-curve` · `POST /api/portfolio/equity-snapshot` · `GET /api/market/vix-pulse` (`routes_dashboard_fixes.py`)

### 6.7 Company Evaluator proxy (`/api/company-evaluator/*`)

See §4.6. All 30+ endpoints in `routes_company_evaluator.py` forward to CE (`192.168.1.143:8100` remote or `localhost:8100` local). Connection mode is controlled by `GET/POST /api/company-evaluator/connection`.

### 6.8 Frontend static serving

`routes_frontend.py` — `GET /` serves `index.html`; `GET /{filename:path}` serves files from `frontend/` (final router registered in `main.py`).

---

## 7. Data Flow

### 7.1 Market Intelligence flow

```
Tradier + FRED + Finnhub + FMP
          │
          ▼
BaseDataService + MarketContextService + per-engine DataProviders
          │
          ▼
MarketIntelligenceRunner.stage_run_engines()
  ├─ breadth_engine.run()
  ├─ volatility_options_engine.run()
  ├─ cross_asset_macro_engine.run()
  ├─ flows_positioning_engine.run()
  ├─ liquidity_conditions_engine.run()
  └─ news_sentiment_engine.run()
          │
          ▼
engine_output_contract.normalize_engine_output() ×6
          │
          ▼
market_composite.build_market_composite() + conflict_detector.detect_conflicts()
          │
          ▼
run_model_interpretation() → LLM regime reasoning (raw inputs, no anchoring)
          │
          ▼
assemble_market_state() (15 keys) → validate_market_state()
          │
          ▼
atomic_write_json() to data/market_state/<ts>.json + write_pointer(latest.json)
          │
          ▼
Consumed by StockOpportunityRunner + OptionsOpportunityRunner via
market_state_consumer.load_latest_market_state()
```

### 7.2 Stock / Options opportunity flow

Stock (8 stages): `load_market_state → resolve_scanners → run_scanners (4 services) → aggregate+dedup → enrich/filter/rank/select → append Market Picture → per-candidate LLM (TMC stock prompt, BUY/PASS + conviction) → package output`.

Options (6 stages, V2): `load_market_state → scan (4 families, Phase A–F) → validate_math (structural + recomputed) → enrich_evaluate (credibility gate, rank top 30) → model_analysis (top 15 via Options TMC prompt) → model_filter (keep EXECUTE, discard PASS, top 10) → select_package`.

### 7.3 On-demand evaluation flow

```
Browser ──POST /api/company-evaluator/on-demand/analyze──▶ BenTrade
BenTrade ──httpx──▶ http://192.168.1.143:8100/api/on-demand/analyze ──▶ CE
CE returns {job_id}
Browser polls /api/company-evaluator/on-demand/jobs/{id} every 2s
Browser fetches .../result → renders panels (quality signals, chart,
  DCF/EVA/comps, thesis, deep research, glossary)
```

### 7.4 Trade execution flow

```
User picks candidate in TMC / Home / Strategy dashboard
  → ui/trade_ticket.js opens (unified modal)
  → POST /api/trading/preview  (validated, priced)
  → user confirms
  → POST /api/trading/submit   (respects TRADIER_EXECUTION_ENABLED gate;
                                 dry-run if false, real submit if true)
  → routes_trading → trading/service → trading/tradier_broker
  → tradier_client (rate-limited, 429-retried)
  → Tradier API (live or sandbox based on account_mode)
  → order recorded in InMemoryTradingRepository + trade_lifecycle_service
  → TMC refreshes via /api/active/refresh
```

For CLOSE/REDUCE on active trades: `close_order_builder.py` constructs the closing multi-leg order → `POST /api/trading/close-preview` → `/close-submit`.

---

## 8. Configuration

### 8.1 Key `Settings` fields (`backend/app/config.py`)

| Field | Default | Purpose |
|---|---|---|
| `ENVIRONMENT` | `development` | `development` forces PAPER routing; `production` respects UI |
| `TRADIER_EXECUTION_ENABLED` | `false` (persisted) | Master execution gate — `false` = dry run |
| `TRADIER_API_KEY_LIVE` / `_PAPER` | — | Dual credential sets |
| `TRADIER_ACCOUNT_ID_LIVE` / `_PAPER` | — | Account IDs |
| `TRADIER_ENV_LIVE` / `_PAPER` | `live` / `sandbox` | Tradier env |
| `FINNHUB_KEY`, `FRED_KEY`, `FMP_API_KEY`, `POLYGON_API_KEY` | — | Provider keys |
| `BEDROCK_ENABLED` | `true` | AWS Bedrock gate |
| `BEDROCK_REGION` | `us-east-1` | |
| `BEDROCK_MODEL_ID` | `us.amazon.nova-pro-v1:0` | Premium Nova Pro |
| `BEDROCK_TIMEOUT_SECONDS` | `120` | |
| `COMPANY_EVALUATOR_URL` | `http://192.168.1.143:8100` | CE base URL |
| `MODEL_TIMEOUT_SECONDS` | `60` | LLM timeout |
| `HTTP_TIMEOUT_SECONDS` | `15` | Generic HTTP timeout |
| `QUOTE_CACHE_TTL_SECONDS` | `60` | |
| `CHAIN_CACHE_TTL_SECONDS` | `300` | |
| `FRED_CACHE_TTL_SECONDS` | `300` | |
| `CANDLES_CACHE_TTL_SECONDS` | `1800` | |
| `OPTIONS_SCAN_SYMBOLS` | SPY,QQQ,IWM,DIA + 26 mega-caps + 3 sector ETFs | Options universe |
| `OPTIONS_MODEL_ANALYSIS_TOP_N` | `20` | Candidates sent to LLM |
| `DTE_MIN` / `DTE_MAX` | `3` / `14` | Default DTE window |
| `MAX_EXPIRATIONS_PER_SYMBOL` | `6` | |
| `MAX_WIDTH_DEFAULT` | `50` | Max spread width ($) |
| `MAX_LOSS_PER_SPREAD_DEFAULT` | `2000` | |
| `MIN_CREDIT_DEFAULT` | `0.20` | |
| `SNAPSHOT_CAPTURE` / `OPTION_CHAIN_SOURCE` | `0` / `tradier` | Offline replay toggles |
| `SNAPSHOT_MAX_AGE_HOURS` / `SNAPSHOT_RETENTION_DAYS` | `48` / `7` | |
| `INSIDER_*`, `PILLAR_13F_*`, `SMART_MONEY_*` | — | Specialty signal tuning |

### 8.2 `.env` structure (keys redacted)

```
AWS_ACCESS_KEY_ID=***REDACTED***
AWS_SECRET_ACCESS_KEY=***REDACTED***
AWS_DEFAULT_REGION=us-east-1
FINNHUB_API_KEY=***REDACTED***
FRED_API_KEY=***REDACTED***

# Tradier LIVE
TRADIER_API_KEY_LIVE=***REDACTED***
TRADIER_ACCOUNT_ID_LIVE=6YB72056
TRADIER_ENV_LIVE=live

# Tradier PAPER
TRADIER_API_KEY_PAPER=***REDACTED***
TRADIER_ACCOUNT_ID_PAPER=VA74095461
TRADIER_ENV_PAPER=sandbox

# Alpaca LIVE (credentials stored; Tradier is the active broker)
ALPACA_API_KEY_LIVE=***REDACTED***
ALPACA_SECRET_KEY_LIVE=***REDACTED***
ALPACA_ACCOUNT_ID_LIVE=200518605
ALPACA_ENV_LIVE=live

# Alpaca PAPER
ALPACA_API_KEY_PAPER=***REDACTED***
ALPACA_SECRET_KEY_PAPER=***REDACTED***
ALPACA_ACCOUNT_ID_PAPER=PA3DTLLUT4NI
ALPACA_ENV_PAPER=sandbox

TRADIER_EXECUTION_ENABLED=false

POLYGON_API_KEY=***REDACTED***
FMP_API_KEY=***REDACTED***
COMPANY_EVALUATOR_URL=http://192.168.1.143:8100

# (optional overrides)
# OPTIONS_SCAN_SYMBOLS=SPY,QQQ,IWM,DIA,AAPL,MSFT,...
# OPTIONS_MODEL_ANALYSIS_TOP_N=20
```

### 8.3 Runtime feature flags / toggles

- `data/runtime_config.json` — persists `tradier_execution_enabled` across restarts.
- `platform_settings.data_source_mode` — `live` vs `snapshot` (toggle in `/api/admin/platform/data-source`).
- `model_state.model_source` — `local` / `model_machine` / `premium_online` (toggle in `/api/admin/platform/model-source`).
- `execution_mode_state` — runtime mode for distributed routing (`/api/admin/execution-mode`).
- Orchestrator: paused/running/stopped/delay (`/api/orchestrator/*`).
- Refresh state: paused/resumed (`/api/refresh/*`).

### 8.4 Ports

| Port | Service | Machine |
|---|---|---|
| `5000` | BenTrade FastAPI (uvicorn) | `192.168.1.89` |
| `8100` | Company Evaluator FastAPI | `192.168.1.143` (remote) / localhost (when mode=local) |
| `1234` | LM Studio chat completions | both `192.168.1.89` and `192.168.1.143` |
| `55123` | Launcher single-instance lock (socket only) | `192.168.1.89` |

---

## 9. LLM Model Routing (Detail)

### 9.1 Architecture

All LLM calls converge on `backend/app/services/model_router.py`. There are three callable entry points, designed to be layered:

1. **Legacy direct** — `model_request()` (sync via `requests`) and `async_model_request()` (async via `httpx`). Resolves endpoint from `MODEL_SOURCES[get_model_source()]`. Forces `stream: false`. Used by `common/model_analysis.py`, `common/utils.py`, and some active-trade callers.
2. **Provider-abstraction** — `execute_with_provider(execution_request, provider_id)` — resolves a specific adapter in `model_provider_registry.py` via `model_provider_adapters.py`.
3. **Distributed routing** — `route_and_execute(execution_request)` → `(result, trace)`. Resolves `ExecutionMode` → ordered `Provider` chain → invokes each adapter with circuit-breaker + health check; returns a full `RoutingTrace`.

### 9.2 Routing modes (`ExecutionMode`)

- `local` — only `localhost:1234`
- `model_machine` — only `192.168.1.143:1234`
- `premium_online` — only AWS Bedrock Nova Pro
- `local_distributed` — round-robin / failover across both LM Studio endpoints
- `online_distributed` — LM Studio primary, Bedrock fallback (and vice versa under configuration)

### 9.3 Providers (`Provider` enum)

| Provider ID | URL / target | Machine | Model |
|---|---|---|---|
| `LOCALHOST_LLM` | `http://localhost:1234/v1/chat/completions` | `192.168.1.89` | Whatever LM Studio has loaded |
| `NETWORK_MODEL_MACHINE` | `http://192.168.1.143:1234/v1/chat/completions` | `192.168.1.143` | Whatever LM Studio has loaded |
| `BEDROCK_TITAN_NOVA_PRO` | AWS Bedrock | `us-east-1` | `us.amazon.nova-pro-v1:0` |

### 9.4 Health checking & fallback

- `model_health_service.py` pings each provider; unavailable → `ProviderState.UNAVAILABLE`.
- Circuit breaker in `model_router_policy.py` trips after repeated failures; resettable via `POST /api/admin/circuit-breaker/reset`.
- `FallbackReason` captured on every fallback: `PROVIDER_UNAVAILABLE`, `PROVIDER_BUSY`, `PROVIDER_FAILED`, `PROVIDER_DEGRADED`, `PROVIDER_TIMEOUT`, `PROVIDER_ERROR`, `EXPLICIT_OVERRIDE`.
- Telemetry in `model_routing_telemetry.py` feeds `/api/admin/dashboard` (routing dashboard).

### 9.5 Integration with Company Evaluator

CE runs an analogous routing layer on the model machine. From CE's perspective, the two LM Studio endpoints are **reversed** (CE's "local" = `192.168.1.143:1234`, CE's "remote" = `192.168.1.89:1234`). When BenTrade calls CE (via `/api/company-evaluator/*`), CE does its own LLM routing internally — BenTrade does not propagate routing mode to CE.

---

## 10. Database / Storage

### 10.1 Local on-disk store

No SQL. Everything is JSON on disk, gitignored under `backend/data/`:

| Path | Contents |
|---|---|
| `backend/data/market_state/` | `latest.json` pointer + `<run_id>.json` artifacts |
| `backend/data/workflows/stock_opportunity/` | `latest.json` + `run_<id>/stage_*.json` |
| `backend/data/workflows/options_opportunity/` | `latest.json` + `run_<id>/stage_*.json` |
| `backend/data/snapshots/tradier/` | Option-chain snapshots (manifest + per-symbol files) |
| `backend/data/diagnostics/` | Diagnostic captures |
| `backend/data/runtime_config.json` | Persisted runtime toggles |
| `backend/results/` | Per-strategy generated reports |

Write semantics: `atomic_write_json()` (write-to-tmp + rename) so half-written reads cannot occur. Retention: `SNAPSHOT_MAX_AGE_HOURS=48`, `SNAPSHOT_RETENTION_DAYS=7`.

### 10.2 In-memory stores

- `storage/repository.py` → `InMemoryTradingRepository` — preview/submit orders, runtime state.
- `TTLCache` (`utils/cache.py`) wired into every client for provider responses.

### 10.3 NAS integration

BenTrade itself does not read/write NAS (`192.168.1.149`). CE uses the NAS for long-term snapshot and result archival. If BenTrade ever needs NAS-stored CE artifacts, it requests them through CE proxy endpoints.

### 10.4 Caching strategy

In-process `TTLCache` + per-endpoint TTLs:
- Quotes: 60 s · Expirations: 300 s · Chains: 300 s · Candles: 1800 s · FRED: 300 s
- Model calls: **no cache** (always fresh)
- Market state / workflow outputs: file-backed (`latest.json` pointer) — no in-memory cache; re-read on each request.

---

## 11. Performance Profile

- **Tradier rate limit**: client-side leaky bucket at 2 req/sec with 429 retry (3 attempts, exponential backoff). Snapshot capture fans out under an asyncio semaphore.
- **FMP**: 3000 RPM cap (see `FMP_MAX_RPM`).
- **Model timeouts**: backend 60 s default (`MODEL_TIMEOUT_SECONDS`), frontend 185 s client-side (`MODEL_TIMEOUT_MS` in `client.js`).
- **MI cycle**: ~5 minutes baseline (informal — orchestrator runs back-to-back with configurable delay).
- **Full Refresh** (TMC): Stock + Options + Active Trades in parallel → Portfolio Balance. Typical wall-clock dominated by per-candidate LLM calls (options top 15 ≈ 15 × model-timeout).
- **Recent optimizations** (per git history / repo memory): scanner V2 forward, strategy-aware ranking v2, EV ranking diversity fix, context-assembly scanner review fix, startup-orchestration redesign, engine confidence normalization (0–100 → 0–1).

---

## 12. Known Issues and Pending Work

### 12.1 Active bugs / gaps

- **No credibility gating on options candidates** — garbage-to-the-top problem when sorting by unbounded EV (recent penny/delta/bid gates partially mitigate). No minimum EV threshold, no IV-based filter, no regret-based selection.
- **Flows & Positioning engine is 100% VIX-derived proxy** — no real CFTC COT, ETF fund flows, dealer gamma, or sentiment survey data.
- **No event calendar engine** as a first-class gate (FOMC/CPI/earnings are read but not gated).
- **Strategy prompts conflate setup ID with portfolio-level approval** — should split.
- **No prediction-vs-outcome calibration loop** — decisions are not scored against realized performance.
- **Static policy thresholds** — no regime-adaptive tuning.
- **PDF export blank-data** issue noted in prior sessions for the On-Demand Evaluator (`POST /api/export/on-demand-pdf`) — verify current state before assuming fixed.
- **`requirements.txt` still pins `Flask==3.1.2`** although the app is pure FastAPI — harmless but confusing.
- **`_deprecated_pipeline/`** under `backend/app/services/` — 20 modules kept for historical reference only; DO NOT import.

### 12.2 Pending features (design only)

- Options model-analysis layer (bring the stock workflow's per-candidate LLM review to options — already scoped in §1 of `docs/architecture/bentrade_decision_system_current_state.md`).
- Calibration loop (ledger of decisions → realized outcomes → per-strategy / per-regime accuracy).
- Regime-adaptive thresholds (let policy knobs move with `RISK_ON`/`NEUTRAL`/`RISK_OFF`).
- First-class event-calendar engine.

### 12.3 Pending Copilot prompt files

No Copilot-prompt `.md` files exist in the repo or in `%APPDATA%\Code\User\prompts` at the time of generation. If/when prompts are added they are typically scoped per-project and live in the user's prompts folder.

### 12.4 Technical debt

- 20 deprecated pipeline modules in `services/_deprecated_pipeline/` (awaiting deletion).
- Duplicate `trade_ticket` shim (`app.js` adapter) can be removed once all callers are updated.
- `Flask` pin in requirements.txt.
- Legacy single-set Tradier vars (`TRADIER_TOKEN`, `TRADIER_ACCOUNT_ID`) kept for back-compat inside `Settings.model_post_init()`.

---

## 13. Key Decisions and Conventions

### 13.1 Operating model

- **Ben** — co-architect / product owner.
- **Claude** — tech lead, writes Copilot prompts.
- **GitHub Copilot (VS Code, Opus 4.x)** — developer, executes prompts.
- Prompts are scoped to a single project workspace (BenTrade **or** CE, never both in one prompt).

### 13.2 Coding conventions

**Backend (Python 3.11+):**
- Async-first (`httpx.AsyncClient`, `asyncio`). Sync paths explicitly labelled.
- `logging.getLogger(__name__)` with structured `event=…` keys.
- Null / `None` over guessed values; every derived metric tagged with provenance.
- Dataclasses + enums for contracts; Pydantic models at HTTP boundary only.
- `from __future__ import annotations` in new modules.

**Frontend (vanilla JS):**
- No framework — files loaded via `<script>` tags in `index.html`, hash routing.
- Dark theme, `Exo 2` display + `Inter` body, CSS custom properties in `app.css`.
- Single TradeCard primitive (`trade_card.js`), unified TradeTicket modal (`trade_ticket.js`), unified tooltip system (`tooltip.js` + `ben_tooltip.js`).
- `window.BenTrade*` namespace pattern for module exports.
- Per-page controllers registered as `window.BenTradePages.init<Name>(rootEl)` — called by the router.
- Caches live in `stores/`; dashboards call `BenTradeApi.*` for data.

### 13.3 Copilot prompt conventions

- Scope header at top of prompt (which project, which files are in/out of scope).
- Explicit **STOP-and-report** gates between risky steps.
- Numbered steps, each independently verifiable.
- **Do NOT** section listing forbidden actions (don't add features beyond ask, don't touch out-of-scope files, don't run full test suite).
- Git tag and commit message spelled out at the end.
- `.github/copilot-instructions.md` is always loaded as context.

### 13.4 Architectural decisions (and why)

- **Vanilla JS over React** — zero build chain, faster iteration, deterministic load order, no lockfile drift for a single-user app.
- **Tradier** — cheapest institutional-grade options data + live execution + dual live/paper credentials under one provider.
- **Separate CE backend** — fundamental analysis has very different data providers (FMP-heavy) and cadence (slow/batch) than trade scanning; keeping it in its own process on the model machine keeps CE compute off the trading box and lets the heavy LLM run next to it.
- **LLM routing architecture** — started as "local vs remote LM Studio" and generalized to `ExecutionMode`/`Provider` so Bedrock Nova Pro could be added without touching callers.
- **File-backed workflows with pointer files** — inspectable, replayable, auditable; no DB to migrate.
- **Tradier as single source of truth for options/prices** — eliminates cross-provider reconciliation bugs during execution.
- **V2 options scanner rebuild** — replaced the legacy strategy-specific scanners with a family-based, phase-driven, diagnostics-retaining pipeline so every rejection has a stable reason code.

---

## 14. Quick Reference

### 14.1 Common commands (PowerShell)

```powershell
# Start backend (from BenTrade/backend)
.\start_backend.ps1

# Run the launcher (Tkinter single-instance, starts backend + opens browser)
python BenTrade\backend\launcher.py

# Run a narrow test subset
cd BenTrade\backend
python -m pytest tests/test_<file>.py -q

# Route-presence smoke (matches start_backend.ps1 preflight)
python -c "from fastapi.testclient import TestClient; from app.main import create_app; c=TestClient(create_app()); print('/api/stock/scan' in (c.get('/openapi.json').json()['paths']))"

# Check git state
git -C BenTrade log --oneline -25
```

### 14.2 Key file locations

| Thing | Path |
|---|---|
| FastAPI app factory | `BenTrade/backend/app/main.py` |
| Settings / env loader | `BenTrade/backend/app/config.py` |
| LLM endpoints | `BenTrade/backend/app/model_sources.py` |
| Model router seam | `BenTrade/backend/app/services/model_router.py` |
| Market Intelligence runner | `BenTrade/backend/app/workflows/market_intelligence_runner.py` |
| Stock Opportunity runner | `BenTrade/backend/app/workflows/stock_opportunity_runner.py` |
| Options Opportunity runner | `BenTrade/backend/app/workflows/options_opportunity_runner.py` |
| Active Trade pipeline | `BenTrade/backend/app/services/active_trade_pipeline.py` |
| Continuous orchestrator | `BenTrade/backend/app/workflows/continuous_workflow_orchestrator.py` |
| Tradier client | `BenTrade/backend/app/clients/tradier_client.py` |
| Tradier broker | `BenTrade/backend/app/trading/tradier_broker.py` |
| CE proxy | `BenTrade/backend/app/api/routes_company_evaluator.py` |
| TMC routes | `BenTrade/backend/app/api/routes_tmc.py` |
| SPA shell | `BenTrade/frontend/index.html` |
| Router | `BenTrade/frontend/assets/js/router.js` |
| TMC controller | `BenTrade/frontend/assets/js/pages/trade_management_center.js` |
| Home controller | `BenTrade/frontend/assets/js/pages/home.js` |
| On-Demand Evaluator controller | `BenTrade/frontend/assets/js/pages/on_demand_evaluator.js` |
| TradeCard primitive | `BenTrade/frontend/assets/js/ui/trade_card.js` |
| TradeTicket modal | `BenTrade/frontend/assets/js/ui/trade_ticket.js` |
| API client | `BenTrade/frontend/assets/js/api/client.js` |
| Tooltip glossary | `BenTrade/frontend/assets/js/metrics/tooltip_dictionary.js` |
| App CSS | `BenTrade/frontend/assets/css/app.css` |
| Anchor architecture doc | `docs/architecture/bentrade_decision_system_current_state.md` |
| Standards | `docs/standards/{canonical-contract,scanner-contract,rejection-taxonomy,presets,data-quality-rules,ui-tradecard-spec}.md` |

### 14.3 Port assignments

| Port | Service | Machine |
|---|---|---|
| 5000 | BenTrade FastAPI (uvicorn) | `192.168.1.89` |
| 8100 | Company Evaluator FastAPI | `192.168.1.143` |
| 1234 | LM Studio (chat completions) | `192.168.1.89` and `192.168.1.143` |
| 55123 | Launcher single-instance socket | `192.168.1.89` |

### 14.4 Related projects

| Project | Host | Port | Role |
|---|---|---|---|
| **Company Evaluator** | `192.168.1.143` | 8100 | Fundamentals / DCF / EVA / comps / smart-money; owns FMP-heavy data |
| **LM Studio (local)** | `192.168.1.89` | 1234 | BenTrade-side LLM (fast iteration) |
| **LM Studio (model machine)** | `192.168.1.143` | 1234 | Heavy-model inference; used by both BenTrade and CE |
| **AWS Bedrock** | `us-east-1` | — | Premium tier (Nova Pro) via boto3 |
| **NAS** | `192.168.1.149` | SMB | Long-term archive (CE-owned) |

### 14.5 Prompt file inventory

No Copilot prompt `.md` files are currently tracked in the repo or in `%APPDATA%\Code\User\prompts`. When new ones are authored, add them here with status (`completed` / `in-progress` / `pending`) and a one-line description.

---

*End of APP_CONTEXT.md — maintained as a drop-in project primer. Update whenever architecture, ports, dashboards, or provider integrations materially change.*
