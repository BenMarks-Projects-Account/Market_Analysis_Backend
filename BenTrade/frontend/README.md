# BenTrade Frontend

> Last updated: 2026-02-17

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
├── pages/              # Per-dashboard JS modules (home.js, etc.)
├── ui/                 # Reusable UI components (trade_card.js, etc.)
├── stores/             # Client-side data stores
├── state/              # Reactive state management
├── strategies/         # Strategy-specific rendering
├── metrics/            # Metric display helpers
└── utils/              # Shared utilities
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
