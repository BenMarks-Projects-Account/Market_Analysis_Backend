# Cleanup Baseline — `chore/app-cleanup-phase0`

**Date:** 2026-02-17  
**Branch:** `chore/app-cleanup-phase0`  
**Base commit:** `737f1e10173a2fc383d30d8fc103489be9ba825a` (`main` — "in flight 2")

---

## Test Suite Results

| Metric           | Value                     |
|------------------|---------------------------|
| Total tests      | 125                       |
| Passed           | 123                       |
| Failed           | 2 (pre-existing)          |
| Execution time   | 3.77 s                    |
| Python           | 3.14.3                    |
| pytest           | 9.0.2                     |

### Pre-existing failures (not introduced by this branch)

| Test | Reason |
|------|--------|
| `test_trading_positions_smoke::test_positions_returns_200_with_ok_false_when_credentials_missing` | Requires live broker credentials |
| `test_trading_positions_smoke::test_positions_returns_200_with_ok_true_when_credentials_present` | Requires live broker credentials |

---

## Smoke Checklist

| Check                          | Result | Notes |
|--------------------------------|--------|-------|
| Backend starts (uvicorn)       | PASS   | `app.main:app` on port 8111 |
| `GET /docs`                    | PASS   | HTTP 200 |
| `GET /api/strategies`          | PASS   | HTTP 200 — lists all strategy IDs |
| `GET /api/strategies/credit_spread/reports` | PASS | HTTP 200 — 8 reports listed |
| `GET /api/strategies/credit_spread/reports/{latest}` | PASS | HTTP 200 — report loads but **0 trades** (see Issues) |
| `GET /api/reports`             | PASS   | HTTP 200 |
| `GET /api/recommendations/top` | PASS   | HTTP 200 (slow first call ~15 s due to regime service init) |
| `GET /api/admin/data-health`   | PASS   | HTTP 200 |
| `POST /api/workbench/ticket`   | PASS   | HTTP 405 (expected — method verification) |

---

## Key Endpoints

| Endpoint | Router file | Purpose |
|----------|-------------|---------|
| `GET /api/admin/data-health` | `routes_admin.py` | Source freshness + data quality |
| `GET /api/reports` | `routes_reports.py` | Legacy report listing |
| `GET /api/reports/{filename}` | `routes_reports.py` | Legacy individual report |
| `GET /api/generate` | `routes_reports.py` | Legacy multi-strategy generate |
| `GET /api/strategies` | `routes_strategies.py` | List strategy IDs |
| `POST /api/strategies/{id}/generate` | `routes_strategies.py` | Run strategy scanner |
| `GET /api/strategies/{id}/reports` | `routes_strategies.py` | List strategy reports |
| `GET /api/strategies/{id}/reports/{file}` | `routes_strategies.py` | Fetch single report (normalized) |
| `GET /api/recommendations/top` | `routes_recommendations.py` | Homepage top picks (unified metrics) |
| `POST /api/workbench/ticket` | `routes_workbench.py` | Trade execution preview/submit |
| `WS /api/ws` | `routes_ws.py` | WebSocket for live progress |

---

## Issues Found (DO NOT FIX YET)

### 1. Credit spread scanner returns 0 trades (latest 3 reports)

- **File:** `credit_spread_analysis_20260218_024724.json`
- **Diagnostics:** 28 candidates built, 28 enriched, **0 accepted**
- **Root cause:** All candidates fail `evaluate()` gate in `credit_spread.py` (lines 168–230). Default thresholds: `min_pop=0.65`, `min_ev_to_risk=0.02`, `min_open_interest=500`, `min_volume=50`. Current market data likely has thin OI/volume or low POP across all candidates.
- **Impact:** UI shows empty scanner results; homepage picks may have no strategy candidates.
- **Action needed:** Consider logging rejection reasons per candidate in diagnostics; consider relaxing OI/volume thresholds for thinly-traded underlyings.

### 2. `test_trading_positions_smoke` always fails without live credentials

- **Impact:** CI will always show 2 failures.
- **Action needed:** Mark as `@pytest.mark.skipif` or mock the broker client.

### 3. Homepage `/api/recommendations/top` slow on first call (~15 s)

- **Root cause:** Regime service performs external API calls on first request.
- **Impact:** Frontend may show loading spinner for a long time on cold start.
- **Action needed:** Consider caching regime data or adding a startup warm-up.

### 4. Session stats log warnings for missing metrics

```
[session-stats] credit_put: best_score missing; defaulted to null
  | avg_quality_score missing; defaulted to null
  | avg_return_on_risk missing; defaulted to null
```

- **Root cause:** Report has 0 trades → `report_stats` has all-null aggregates. Frontend `sessionStats.js` logs warnings when these are null.
- **Impact:** Console noise only; no functional impact.
- **Action needed:** Frontend should gracefully handle empty reports without logging warnings.

### 5. 977 pytest warnings

- The test suite emits 977 warnings (mostly deprecation and async resource warnings).
- **Action needed:** Triage and suppress or fix in a follow-up pass.
