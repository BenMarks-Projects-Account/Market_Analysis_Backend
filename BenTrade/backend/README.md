# BenTrade Backend

> Last updated: 2026-02-17

FastAPI backend for multi-strategy options analysis, report generation, trade lifecycle management, and trading execution.

## Features

- **Multi-Strategy Scanner**: Credit spreads, debit spreads, iron condors, butterflies, calendars, income strategies
- **Quantitative Analysis**: Full CreditSpread-derived metrics (max profit/loss, break-even, POP, EV, RoR, kelly) plus market context (IV/RV, regime, expected move)
- **Computed Metrics Contract**: Stable `computed_metrics` + `metrics_status` shape across all endpoints
- **Canonical Strategy Naming**: Single source of truth for strategy IDs (e.g. `put_credit_spread`) with automatic legacy alias mapping
- **Report Generation**: Timestamped JSON reports per strategy under `results/`
- **Data Workbench**: Per-trade input snapshot tracking and diagnostic drill-down
- **Trade Lifecycle**: Preview → submit flow with paper and live broker support
- **Web Dashboard**: Interactive SPA frontend with Opportunity Engine homepage
- **REST API**: ~20 route modules covering analysis, trading, risk, signals, and admin

## Quick Start

### 1. Start Backend

```bash
# From the backend directory
./start_backend.sh
```

Or on Windows PowerShell:

```powershell
.\start_backend.ps1
```

Visit `http://127.0.0.1:5000/` in your browser.

### 2. Run Tests

```bash
python -m pytest tests/ -q
```

108 tests covering trade key canonicalization, metric computation, strategy metrics audit, API routes, ingress validation, and more.

### 3. API Usage

**List Reports:**
```bash
curl http://127.0.0.1:5000/api/reports
```

**Get Specific Report:**
```bash
curl http://127.0.0.1:5000/api/reports/credit_spread_analysis_20260216_052504.json
```

**Generate scan (SSE stream):**
```bash
curl http://127.0.0.1:5000/api/strategies/credit_spread/generate
```

## Strategy Plugins

| Plugin | Canonical IDs | Description |
|---|---|---|
| `credit_spread` | `put_credit_spread`, `call_credit_spread` | Bull put / bear call credit spreads |
| `debit_spreads` | `put_debit`, `call_debit` | Directional debit spreads |
| `iron_condor` | `iron_condor` | Neutral iron condors |
| `butterflies` | `butterfly_debit` | Debit butterflies (call/put) |
| `calendars` | `calendar_spread`, `calendar_call_spread`, `calendar_put_spread` | Calendar spreads |
| `income` | `income`, `csp`, `covered_call` | Income strategies |

## Canonical Strategy Naming

All strategy IDs are normalized via `canonicalize_strategy_id()` in `app/utils/trade_key.py`.

- **Canonical credit spread IDs**: `put_credit_spread` and `call_credit_spread`
- Legacy aliases (`put_credit`, `credit_put_spread`, etc.) are mapped automatically
- A `TRADE_STRATEGY_ALIAS_MAPPED` validation event is emitted when mapping occurs
- Trade keys use canonical IDs: `SPY|2026-02-23|put_credit_spread|655|650|7`

## Analysis Metrics

Each enriched trade includes:

### Core Trade Metrics (from CreditSpread model)
- **Max Profit / Max Loss**: Per-share and per-contract
- **Break-even**: Strategy-specific (short ± credit for credit spreads)
- **POP**: Probability of profit (1 − |delta|)
- **Expected Value**: EV per share/contract
- **Return on Risk**: Profit / loss ratio
- **Kelly Fraction**: Optimal position sizing
- **Trade Quality Score**: Composite quality metric

### Market Context (from enrichment)
- IV / RV / IV-RV ratio
- Expected move (1σ)
- Strike distance (% and σ)
- Bid-ask spread %
- Market regime classification
- RSI-14, SMA-20/50, realized vol 20d

### Metric Correctness by Strategy

| Strategy | max_profit | max_loss | POP | EV | RoR | kelly | break_even |
|---|---|---|---|---|---|---|---|
| credit_spread | ✅ CreditSpread model | ✅ Real | ✅ Delta-derived | ✅ Real | ✅ Real | ✅ Real | ✅ Real |
| debit_spreads | ✅ Per-contract | ✅ Per-contract | ⚠️ implied_prob (debit/width) | ⚠️ Heuristic | ✅ Real | — N/A | ✅ Real |
| butterflies | ✅ Per-contract | ✅ Per-contract | ✅ Normal CDF (break-evens) | ✅ Numerical integration | ✅ Real | — N/A | ✅ Real (lower+debit / upper−debit) |
| iron_condor | ✅ Per-contract | ✅ Per-contract | ✅ Normal CDF (break-evens) | ✅ POP-derived | ✅ Real | — N/A | ✅ Real |
| income | ✅ Per-contract | ✅ Per-contract | ⚠️ 1−delta approx | ✅ POP-derived | ✅ Real | — N/A | ✅ Real |
| calendars | — None (unknowable) | ✅ Net debit | — None | — None | — None | — N/A | ⚠️ Rough est. |

### Computed Metrics Contract
All trades include:
```json
{
  "computed_metrics": {
    "max_profit": 35.0,
    "max_loss": 465.0,
    "pop": 0.8707,
    "expected_value": -29.65,
    "return_on_risk": 0.0753,
    "kelly_fraction": -0.847,
    "break_even": 654.65,
    "dte": 7,
    ...
  },
  "metrics_status": {
    "ready": false,
    "missing_fields": ["iv_rank", "rsi14", "rv_20d"]
  }
}
```

## Canonical Trade Card Pills

All strategy trade payloads include a `pills` object for UI trade cards:

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

### Cleanup legacy scan reports

To archive or delete older report files that still contain legacy strategy strings (e.g. `put_credit`) and optionally regenerate:

```bash
python -m app.tools.legacy_strategy_report_cleanup --results-dir backend/results --mode archive
```

- Use `--mode delete` to remove files instead of archiving.
- Use `--no-regenerate` to skip regeneration.
- Use `--dry-run` to preview matching files without modifying anything.

## File Structure

```
backend/
├── app/
│   ├── api/                    # ~20 FastAPI route modules
│   │   ├── routes_strategies.py    # Strategy scan generation
│   │   ├── routes_reports.py       # Report serving + normalization
│   │   ├── routes_workbench.py     # Data Workbench endpoints
│   │   ├── routes_trading.py       # Trading preview/submit
│   │   ├── routes_active_trades.py # Active positions
│   │   └── ...
│   ├── clients/                # External data source clients
│   │   ├── tradier_client.py       # Option chains, quotes, orders
│   │   ├── yahoo_client.py         # Price history
│   │   ├── fred_client.py          # VIX, rates
│   │   └── finnhub_client.py       # Company data
│   ├── models/                 # Pydantic schemas + trade contract
│   │   ├── schemas.py
│   │   └── trade_contract.py
│   ├── services/
│   │   ├── strategy_service.py     # Orchestrator: generate → normalize → persist
│   │   ├── data_workbench_service.py
│   │   ├── strategies/             # Plugin implementations
│   │   │   ├── credit_spread.py
│   │   │   ├── debit_spreads.py
│   │   │   ├── iron_condor.py
│   │   │   ├── butterflies.py
│   │   │   ├── calendars.py
│   │   │   └── income.py
│   │   ├── evaluation/             # Gates, scoring, ranking
│   │   └── ...
│   ├── trading/                # Order execution layer
│   │   ├── service.py              # Trading service
│   │   ├── risk.py                 # Pre-trade risk checks
│   │   ├── paper_broker.py         # Paper trading
│   │   └── tradier_broker.py       # Live trading via Tradier
│   ├── tools/                  # CLI utilities
│   │   └── legacy_strategy_report_cleanup.py
│   └── utils/
│       ├── trade_key.py            # Canonical strategy IDs + trade key builder
│       ├── computed_metrics.py     # Metrics contract (computed_metrics + metrics_status)
│       ├── dates.py
│       └── cache.py
├── common/                     # Shared quant/model modules
│   ├── quant_analysis.py           # CreditSpread model + enrich_trade()
│   ├── model_analysis.py
│   └── utils.py
├── tests/                      # ~85 unit tests
├── results/                    # Generated reports + workbench records
├── start_backend.ps1
├── start_backend.sh
└── requirements.txt
```

## Development

### Prerequisites
- Python 3.11+
- UV package manager (or pip)

### Setup
```bash
# Install dependencies
uv sync

# Run tests
python -m pytest tests/ -q

# Start development server
python -m uvicorn app.main:app --host 127.0.0.1 --port 5000 --reload
```

### Diagnostic Logging

Enable trade metric computation trace logging:

```python
import logging
logging.getLogger("bentrade.enrich_trade").setLevel(logging.DEBUG)
```

## API Endpoints

### Analysis & Reports
- `POST /api/strategies/{id}/generate` — Generate strategy scan report
- `GET /api/strategies/{id}/generate` — Generate via SSE stream
- `GET /api/reports` — List all analysis reports
- `GET /api/reports/{filename}` — Get specific report (normalized)

### Market Data
- `GET /api/options/{symbol}/chain` — Option chain with greeks
- `GET /api/underlying/{symbol}/snapshot` — Underlying quote + stats
- `GET /api/spreads/analyze` — Analyze specific spread candidates

### Trading
- `POST /api/trading/preview` — Preview multi-leg spread order
- `POST /api/trading/submit` — Submit previewed order
- `GET /api/trading/orders` — List orders
- `GET /api/trading/active` — Active positions
- `POST /api/trading/kill-switch/on|off` — Runtime kill switch

### Admin & Diagnostics
- `GET /api/admin/data-health` — Source health + validation events
- `POST /api/workbench/analyze` — Analyze single trade (workbench)
- `GET /api/workbench/scenarios` — Workbench scenarios
- `GET /api/risk/policy` — Risk policy configuration
- `GET /api/risk/snapshot` — Portfolio risk snapshot
- `GET /api/regime` — Market regime classification
- `GET /api/signals/{symbol}` — Signal scores

### Frontend
- `GET /` — Web dashboard (SPA)