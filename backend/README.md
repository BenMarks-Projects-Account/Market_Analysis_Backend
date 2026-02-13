# Credit Spread Analysis System

A comprehensive quantitative analysis tool for credit spread options trading with web dashboard.

## Features

- **Quantitative Analysis**: Detailed metrics for put and call credit spreads
- **Batch Processing**: Analyze multiple trades from JSON input
- **Report Generation**: Automatic timestamped JSON reports
- **Web Dashboard**: Interactive frontend to view analysis results
- **REST API**: Programmatic access to analysis and reports

## Quick Start

### 1. Run Analysis from JSON

```bash
# From the backend directory
uv run python quant_analysis.py test_trades.json
```

### 2. Start Web Dashboard

```bash
# From the backend directory
uv run python main.py
```

Visit `http://127.0.0.1:5000/` in your browser

### 3. API Usage

**List Reports:**
```bash
curl http://127.0.0.1:5000/api/reports
```

**Get Specific Report:**
```bash
curl http://127.0.0.1:5000/api/reports/analysis_20260212_214558.json
```

**Analyze Trades via API:**
```bash
curl -X POST http://127.0.0.1:5000/analyze \
  -H "Content-Type: application/json" \
  -d @test_trades.json
```

## JSON Input Format

```json
[
  {
    "spread_type": "put_credit",
    "underlying_price": 500.0,
    "short_strike": 485.0,
    "long_strike": 480.0,
    "net_credit": 1.20,
    "dte": 7,
    "short_delta_abs": 0.25,
    "implied_vol": 0.18,
    "realized_vol": 0.14,
    "iv_rank_value": 0.6
  }
]
```

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
├── main.py              # Flask web server & API
├── quant_analysis.py    # Core analysis engine
├── dashboard.html       # Web dashboard
├── test_trades.json     # Sample input data
├── results/             # Generated analysis reports
│   └── analysis_*.json
└── pyproject.toml       # Project dependencies
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
uv run python quant_analysis.py test_trades.json

# Start development server
uv run python main.py
```

## API Endpoints

- `GET /` - Web dashboard
- `GET /api/reports` - List all analysis reports
- `GET /api/reports/<filename>` - Get specific report
- `POST /analyze` - Analyze trades from JSON payload