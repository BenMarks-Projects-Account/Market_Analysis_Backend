# BenTrade KEEP / DELETE / REVIEW

Generated: 2026-02-17 | Branch: `chore/app-cleanup-phase0`
Updated: 2026-02-17 — DELETE tier completed

Classification criteria:
- **KEEP** — Actively used in production code path
- **DELETED** — Dead code removed (was DELETE tier)
- **DELETE** — Dead code; never imported or called; safe to remove
- **REVIEW** — Borderline; may have residual value or needs closer inspection before removal

---

## Backend — Python

### ~~DELETE~~ → DELETED ✅

All backend DELETE items removed in Batch 1.

| File | Lines | Status |
|------|-------|--------|
| `app/services/strategy_scanner/` (2 files) | ~70 | **Deleted** — entire package removed |
| `common/agent.py` | ~50 | **Deleted** — unused strands Agent wrapper |

Also updated: `backend/README.md` — removed `strategy_scanner/` from directory tree.

### REVIEW (may remove after verification)

| File | Lines | Justification |
|------|-------|--------------|
| `app/tools/legacy_strategy_report_cleanup.py` | ~100 | One-off migration tool. Only imported by its own test (`test_legacy_strategy_report_cleanup.py`). If the migration it implements is completed, both the tool and its test can be deleted. |
| `app/services/report_service.py` | **1,132** | **Legacy report generator.** Duplicates `strategy_service.py`'s plugin-based pipeline. Only consumer is `routes_reports.py` (`GET /api/generate` SSE endpoint). If the legacy generate endpoint is retired, this entire service + its `evaluation/` subpackage become dead. |
| `app/services/evaluation/` (4 files) | ~300 | `gates.py`, `ranking.py`, `scoring.py`, `types.py` — only consumed by `report_service.py`. Dead if `report_service` is removed. |
| `app/api/routes_reports.py` | ~460 | **Partially legacy.** Contains: (1) `GET /api/reports` and `GET /api/reports/{file}` — legacy report listing/reading, duplicates `routes_strategies.py`; (2) `GET /api/generate` — legacy SSE generator using `report_service`; (3) `POST /api/model/analyze` and `/api/model/analyze_stock` — model analysis endpoints (still active, needed). If retired, the model endpoints must be migrated elsewhere first. |
| `build/launcher/` | ? | PyInstaller build artifacts. Review whether this build pipeline is still used. |
| `launcher.py` + `launcher.spec` | ~260 | Desktop GUI launcher (tkinter). Review if still in use or superseded by `start_backend.ps1`. |
| `common/utils.py` | ~50 | Legacy shim — only imported by `common/model_analysis.py`. Consider inlining if trivial. |

### KEEP (all used)

| Category | Files | Count |
|----------|-------|-------|
| Route modules (`app/api/`) | All 21 route files | 21 |
| Service layer (`app/services/`) | 15 service files (minus `report_service` under REVIEW) | 14 |
| Strategy plugins (`app/services/strategies/`) | `base.py`, `credit_spread.py`, `debit_spreads.py`, `iron_condor.py`, `butterflies.py`, `calendars.py`, `income.py`, `__init__.py` | 8 |
| Clients (`app/clients/`) | tradier, finnhub, yahoo, fred | 4 |
| Models (`app/models/`) | `schemas.py`, `trade_contract.py` | 2 |
| Utils (`app/utils/`) | `cache.py`, `computed_metrics.py`, `dates.py`, `http.py`, `trade_key.py`, `validation.py` | 6 |
| Storage (`app/storage/`) | `repository.py` | 1 |
| Trading (`app/trading/`) | `broker_base.py`, `models.py`, `paper_broker.py`, `risk.py`, `service.py`, `tradier_broker.py` | 6 |
| Common (`common/`) | `model_analysis.py`, `quant_analysis.py` | 2 |

---

## Frontend — HTML Dashboards

### ~~DELETE~~ → DELETED ✅

All dead HTML dashboards removed in Batch 2.

| File | Status |
|------|--------|
| `dashboards/active-trade-dashboard.view.html` | **Deleted** |
| `dashboards/credit-spread.html` | **Deleted** |
| `dashboards/risk-capital-management-dashboard.view.html` | **Deleted** |
| `dashboards/stock-analysis-dashboard.view.html` | **Deleted** |
| `dashboards/trade-testing-workbench.view.html` | **Deleted** |
| `dashboards/partials/under-construction-tron.view.html` | **Deleted** (empty `partials/` dir also removed) |

Also updated: `frontend/README.md` — corrected dashboard filenames.

### KEEP (active dashboard files)

| File | Route |
|------|-------|
| `dashboards/home.html` | `#/home` |
| `dashboards/credit-spread.view.html` | `#/credit-spread`, `#/iron-condor`, `#/debit-spreads`, `#/butterflies`, `#/calendar`, `#/income` |
| `dashboards/active_trades.html` | `#/active-trade` |
| `dashboards/trade_workbench.html` | `#/trade-testing` |
| `dashboards/stock_analysis.html` | `#/stock-analysis` |
| `dashboards/stock_scanner.html` | `#/stock-scanner` |
| `dashboards/risk_capital.html` | `#/risk-capital` |
| `dashboards/portfolio_risk.html` | `#/portfolio-risk` |
| `dashboards/trade_lifecycle.html` | `#/trade-lifecycle` |
| `dashboards/strategy_analytics.html` | `#/strategy-analytics` |
| `dashboards/data_health.html` | `#/admin-data-health` |
| `dashboards/admin_data_workbench.html` | `#/admin/data-workbench` |

---

## Frontend — JavaScript

### ~~DELETE~~ → DELETED ✅

All frontend JS DELETE items removed in Batch 3.

| File / Section | Lines Removed | Status |
|----------------|---------------|--------|
| `pages/credit_spread.js` | ~6 | **Deleted** — file removed, `<script>` tag removed from `index.html` |
| `app.js` lines 109–1816 (legacy `initCreditSpread`) | **~1,708** | **Deleted** — `app.js` truncated from 1,875 → 107 lines |
| `app.js` lines 1817–1875 (placeholder utilities) | ~58 | **Deleted** — removed with truncation above |

Also cleaned up:
- `router.js` — removed dead fallbacks from credit-spread init chain (`initCreditSpread` references)
- `strategy_dashboard_shell.js` — removed no-op `window.BenTrade?.initCreditSpread?.(rootEl)` call
- `app.js` line 1 comment — updated from "Credit Spread Analysis" to "Shared execution modal"

### REVIEW

| File / Section | Est. Lines | Justification |
|----------------|------------|--------------|
| `router.js` duplicate routes: `iron-condor` + `strategy-iron-condor` | 10 | Both map to the same view + init function. Sidebar nav uses `iron-condor`; internal links in `home.js` use `strategy-iron-condor`. Consolidate to one route key. |
| `home.js` line 522: `BenTradeSessionState` reference | 1 | References `window.BenTradeSessionState` which doesn't exist (actual global is `BenTradeSession`). No-op due to optional chaining, but should be fixed. |
| ~~`frontend/README.md`~~ | ~~~30~~ | ✅ Fixed — dashboard filenames corrected during Batch 2. |

### KEEP (all used)

| Category | Files | Count |
|----------|-------|-------|
| Page modules (`pages/`) | `home.js`, `strategy_dashboard_shell.js`, `active_trades.js`, `trade_workbench.js`, `stock_analysis.js`, `stock_scanner.js`, `risk_capital.js`, `portfolio_risk.js`, `trade_lifecycle.js`, `strategy_analytics.js`, `data_health.js`, `admin_data_workbench.js` | 12 |
| Core (`assets/js/`) | `app.js` (minus dead sections), `router.js` | 2 |
| API (`api/`) | `client.js` | 1 |
| UI components (`ui/`) | `home_loading_overlay.js`, `notes.js`, `source_health.js`, `tooltip.js`, `trade_card.js` | 5 |
| Stores (`stores/`) | `homeCache.js`, `sessionStats.js` | 2 |
| State (`state/`) | `session_state.js` | 1 |
| Utils (`utils/`) | `rateLimiter.js`, `tradeKey.js` | 2 |
| Metrics (`metrics/`) | `glossary.js` | 1 |
| Strategies (`strategies/`) | `defaults.js` | 1 |

---

## Summary

| Action | Backend | Frontend | Total |
|--------|---------|----------|-------|
| **DELETED** ✅ | 3 files (~120 lines) | 6 HTML + 1 JS file + ~1,766 lines in `app.js` | 10 items |
| **REVIEW** (remaining) | 7 items (~2,300 lines, mainly `report_service.py`) | 2 items (~11 lines) | 9 items |
| **KEEP** | 64 files | 26 files | 90 files |

### Lines removed

| Batch | Component | Lines Removed |
|-------|-----------|---------------|
| 1 | `strategy_scanner/` (2 files) + `common/agent.py` | ~120 |
| 2 | 6 dead HTML dashboards + empty `partials/` dir | ~varies |
| 3 | `pages/credit_spread.js` + `app.js` dead code (1,768 lines) | ~1,774 |
| — | Reference cleanups (`router.js`, `strategy_dashboard_shell.js`, READMEs) | ~5 |
| **Total removed** | | **~1,899+ lines** |

### Remaining REVIEW items (not yet actioned)

| Component | Est. Lines |
|-----------|-----------|
| Backend Python (`report_service` + `evaluation/` + `routes_reports` legacy parts) | ~2,300 |
| Frontend JS (duplicate routes, stale reference) | ~11 |
| Backend misc (`legacy_strategy_report_cleanup.py`, `launcher.*`, `build/`, `common/utils.py`) | ~460 |
| **Total REVIEW** | **~2,771 lines** |
