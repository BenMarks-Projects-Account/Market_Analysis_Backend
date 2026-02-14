# Credit Spread Analysis Backend

FastAPI backend for credit spread analysis, report generation, and trading preview/submit flows.

## Features

- **Quantitative Analysis**: Detailed metrics for put and call credit spreads
- **Batch Processing**: Analyze multiple trades from JSON input
- **Report Generation**: Automatic timestamped JSON reports
- **Web Dashboard**: Interactive frontend to view analysis results
- **REST API**: Programmatic access to analysis and reports

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

### 2. API Usage

**List Reports:**
```bash
curl http://127.0.0.1:5000/api/reports
```

**Get Specific Report:**
```bash
curl http://127.0.0.1:5000/api/reports/analysis_20260212_214558.json
```

## JSON Input Format

Request/response schemas are defined in `app/models/schemas.py` and exposed via FastAPI docs.

## Analysis Metrics

Each trade analysis includes:

- **Basic Trade Info**: Strikes, credit, expiration
- **Risk Metrics**: Max profit/loss, break-even, risk-reward ratio
- **Probability Metrics**: Probability of profit (POP), expected value
- **Position Sizing**: Kelly fraction for optimal sizing
- **Market Context**: IV/RV ratio, expected move, strike distance
- **Composite Score**: Weighted quality score for trade ranking

## File Structure

```
backend/
├── app/                 # FastAPI application, routes, services, clients
├── common/              # Quant/model helper modules
├── tests/               # Unit tests
├── results/             # Generated analysis/model reports
├── start_backend.ps1
├── start_backend.sh
└── requirements.txt
```

## Development

### Prerequisites
- Python 3.11+
- UV package manager

### Setup
```bash
# Install dependencies
uv sync

# Run tests
python -m unittest discover -s tests -p "test_*.py" -v

# Start development server
python -m uvicorn app.main:app --host 127.0.0.1 --port 5000 --reload
```

## API Endpoints

- `GET /` - Web dashboard
- `GET /api/reports` - List all analysis reports
- `GET /api/reports/{filename}` - Get specific report

## FastAPI Service (base data + quant enrichment)

This repo now includes a FastAPI service under `backend/app` that fetches base data from Tradier/Finnhub/FRED and then delegates derived metric enrichment to `common.quant_analysis.enrich_trades_batch`.

Run it from `backend/`:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### curl examples

**Analyze spread**

```bash
curl -X POST "http://127.0.0.1:8000/api/spreads/analyze" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "SPY",
    "expiration": "2026-03-20",
    "strategy": "put_credit",
    "candidates": [
      {"short_strike": 665, "long_strike": 660}
    ],
    "contractsMultiplier": 100
  }'
```

**Get chain**

```bash
curl "http://127.0.0.1:8000/api/options/SPY/chain?expiration=2026-03-20&greeks=true"
```

**Get snapshot**

```bash
curl "http://127.0.0.1:8000/api/underlying/SPY/snapshot"
```

## Trading API (preview -> submit)

Safety defaults:

- Live trading is OFF by default.
- You must preview first and submit with `ticket_id` + `confirmation_token` + `idempotency_key`.
- Analysis endpoints do not place orders.

**Preview multi-leg spread order**

```bash
curl -X POST "http://127.0.0.1:8000/api/trading/preview" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol":"SPY",
    "strategy":"put_credit",
    "expiration":"2026-03-20",
    "short_strike":665,
    "long_strike":660,
    "quantity":1,
    "limit_price":0.92,
    "time_in_force":"DAY",
    "mode":"paper"
  }'
```

**Submit previously previewed order**

```bash
curl -X POST "http://127.0.0.1:8000/api/trading/submit" \
  -H "Content-Type: application/json" \
  -d '{
    "ticket_id":"<ticket-id>",
    "confirmation_token":"<token-from-preview>",
    "idempotency_key":"my-unique-key-001",
    "mode":"paper"
  }'
```

**List/Get orders**

```bash
curl "http://127.0.0.1:8000/api/trading/orders"
curl "http://127.0.0.1:8000/api/trading/orders/<broker-order-id>"
```

**Runtime kill switch (live mode)**

```bash
curl -X POST "http://127.0.0.1:8000/api/trading/kill-switch/on"
curl -X POST "http://127.0.0.1:8000/api/trading/kill-switch/off"
```