# BenTrade Architecture Snapshot

## Flows

### 1) Scan → filter → rank → UI
1. Frontend triggers `/api/generate` SSE.
2. `ReportService.generate_live_report()` gathers data from Tradier/FRED/Yahoo/Finnhub via `BaseDataService`.
3. Candidates are enriched (`common.quant_analysis.enrich_trades_batch`), evaluated by gates (`app/services/evaluation/gates.py`), scored (`evaluation/scoring.py`), and ranked (`evaluation/ranking.py`).
4. Report artifacts are written to `backend/results/analysis_*.json` and rendered by `/api/reports/{filename}` in the SPA.

### 2) Manual reject (persisted)
1. UI click on Reject removes the card immediately (existing behavior).
2. UI also posts to `/api/decisions/reject` with `report_file` and `trade_key`.
3. Backend appends decision to `backend/results/decisions_<report_file>.json`.
4. On report load, UI fetches `/api/decisions/{report_file}` and applies rejects before rendering cards.

### 3) Model analysis
1. UI calls `/api/model/analyze` for a specific trade and source report.
2. Route wraps payload in `TradeContract` and calls `common.model_analysis.analyze_trade(...)`.
3. `common.model_analysis` delegates to legacy model evaluator path for compatibility and writes `backend/results/model_*.json` artifacts.

## Canonical Trade Contract

`backend/app/models/trade_contract.py` defines `TradeContract` with key fields used end-to-end:
- spread_type, underlying, short_strike, long_strike, dte, net_credit, width
- max_profit_per_share, max_loss_per_share, break_even, return_on_risk
- pop_delta_approx, p_win_used, ev_per_share, ev_to_risk, kelly_fraction
- trade_quality_score, iv, realized_vol, iv_rv_ratio, expected_move
- short_strike_z, bid_ask_spread_pct, composite_score, rank_score, rank_in_report
- model_evaluation (optional dict)

Helper API:
- `TradeContract.from_dict(d)`
- `TradeContract.to_dict()`

## Rule Ownership

- Gates: `backend/app/services/evaluation/gates.py`
- Scoring: `backend/app/services/evaluation/scoring.py`
- Ranking: `backend/app/services/evaluation/ranking.py`

`report_service.py` now routes candidate decisions through this evaluation facade while preserving existing outputs through legacy wrappers.

## Persisted Artifacts

Under `backend/results/`:
- `analysis_*.json` — generated report payloads (stats/trades/diagnostics/source health)
- `model_*.json` — model analysis outputs
- `decisions_*.json` — append-only reject decisions keyed by report file
