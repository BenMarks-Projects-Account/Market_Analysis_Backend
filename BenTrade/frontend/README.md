# BenTrade Frontend

> Last updated: 2026-02-18

Vanilla JS single-page application — no framework, no build step.

## Run

Served by the FastAPI backend at `http://127.0.0.1:5000/`.

## Architecture

- **SPA Router**: `assets/js/router.js` loads `dashboards/*.view.html` fragments into `#view` via `fetch`.
- **App Shell**: `index.html` provides header, left nav, and `#view` container.
- **No build step**: Plain ES modules imported directly in the browser.

## Dashboards

| Route | File | Description |
|---|---|---|
| `home` | `home.html` | Opportunity Engine — top trades across all strategies |
| `credit-spread` | `credit-spread.view.html` | Credit spread scanner results |
| `stock-analysis` | `stock_analysis.html` | Per-symbol stock analysis |
| `stock_scanner` | `stock_scanner.html` | Multi-symbol scanner |
| `active-trades` | `active_trades.html` | Active positions monitor |
| `trade_lifecycle` | `trade_lifecycle.html` | Trade preview → submit flow |
| `portfolio_risk` | `portfolio_risk.html` | Portfolio-level risk dashboard |
| `risk_capital` | `risk_capital.html` | Risk capital management |
| `data_health` | `data_health.html` | Data source health + validation events |
| `trade_workbench` | `trade_workbench.html` | Trade testing workbench |
| `strategy_analytics` | `strategy_analytics.html` | Strategy performance analytics |
| `admin_data_workbench` | `admin_data_workbench.html` | Data Workbench drill-down |

## JS Module Structure

```
assets/js/
├── app.js              # Shared dashboard logic
├── router.js           # SPA route → view loader
├── api/                # Backend API client wrappers
│   └── client.js           # Fetch wrapper for all API calls
├── pages/              # Per-dashboard JS modules
│   ├── home.js             # Homepage Opportunity Engine
│   ├── strategy_dashboard_shell.js  # Generic strategy dashboard
│   ├── active_trades.js
│   ├── admin_data_workbench.js
│   ├── data_health.js
│   ├── portfolio_risk.js
│   ├── risk_capital.js
│   ├── stock_analysis.js
│   ├── stock_scanner.js
│   ├── strategy_analytics.js
│   ├── trade_lifecycle.js
│   └── trade_workbench.js
├── ui/                 # Reusable UI components
│   ├── trade_card.js       # Trade card builder
│   ├── home_loading_overlay.js  # Homepage loading overlay
│   ├── notes.js            # Trade notes (localStorage-backed)
│   ├── source_health.js    # Source health display
│   └── tooltip.js          # Tooltip component
├── stores/             # Client-side data stores
│   ├── homeCache.js        # Homepage data cache
│   └── sessionStats.js     # Session statistics
├── state/              # Reactive state management
│   └── session_state.js    # Session state
├── strategies/         # Strategy-specific rendering
│   └── defaults.js         # Strategy default configurations
├── metrics/            # Metric display helpers
│   └── glossary.js         # Metrics glossary definitions
└── utils/              # Shared utilities
    ├── format.js           # Formatting helpers
    ├── tradeAccessor.js    # Trade data accessor
    ├── tradeKey.js         # Client-side trade key utilities
    ├── rateLimiter.js      # Rate limiting
    └── debug.js            # Debug utilities
```

## Trade Cards

Trade results render as cards via `ui/trade_card.js`. Each card displays:
- Strategy label, ticker, expiration
- Strike range with spread width
- Pill badges: DTE, POP, OI, Volume, Regime
- Computed metrics table (max profit/loss, EV, RoR, kelly, break-even)

Cards consume the `pills` + `computed_metrics` contract from the backend API.

## Add a New Dashboard

1. Create `dashboards/<name>.view.html` with the view fragment
2. Add routing entry in `assets/js/router.js`
3. Add a nav link with `data-route="<name>"` in `index.html`
4. (Optional) Create `assets/js/pages/<name>.js` for dashboard-specific logic
