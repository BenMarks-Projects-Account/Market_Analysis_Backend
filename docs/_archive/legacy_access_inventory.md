# Legacy Field Access Inventory

> **Status:** Audit snapshot — no changes applied.
> **Date:** 2026-02-17
> **Companion:** `docs/canonical_contract.md` (defines the target structure)

Every entry below is a non-test, non-normalizer, non-alias-definition site
that reads or writes a legacy field.  These are the locations that must be
migrated to the canonical TradeDTO (`computed.*` / `details.*` / `pills.*`
sub-dicts, `strategy_id`, `symbol`).

---

## 1. Per-Share Fields

> **Target:** Remove entirely.  Use `computed.max_profit`, `computed.max_loss`,
> `computed.expected_value` (all per-contract).

### `ev_per_share`

| File | Line | Function | R/W | Snippet |
|------|------|----------|-----|---------|
| `common/quant_analysis.py` | 362 | `summary()` | W | `out["ev_per_share"] = ...` |
| `common/quant_analysis.py` | 778 | `enrich_trade_metrics()` | R | `t.get("ev_per_share")` |
| `app/services/strategies/debit_spreads.py` | 352 | `_build_candidate()` | W | `"ev_per_share": (ev / 100.0)` |
| `app/services/strategies/iron_condor.py` | 264, 391 | `_build_trade()` | W | `ev_per_share = ev_per_contract / 100.0` |
| `app/services/risk_policy_service.py` | 199, 275 | `_build_snapshot()` | R+W | `trade.get("ev_per_share")` |
| `app/services/risk_policy_service.py` | 444 | `_apply_hard_limits()` | R | `trade.get("ev_per_share")` |
| `app/services/strategies/credit_spread.py` | 174 | `_filter_trade()` | R | `trade.get("ev_per_share")` |
| `app/api/routes_strategy_analytics.py` | 174 | `_build_analytics()` | R | `snapshot.get("ev_per_share")` |
| `frontend/assets/js/pages/home.js` | 168, 194, 254 | `extractRawFields()`, `extractMetrics()` | R | `trade?.ev_per_share` |
| `frontend/assets/js/pages/trade_workbench.js` | 398, 412 | `renderTradeCard()` | R | `trade.ev_per_share` |
| `frontend/assets/js/stores/homeCache.js` | 67 | `normalizeRow()` | R | `raw?.ev_per_share` |

### `max_profit_per_share`

| File | Line | Function | R/W | Snippet |
|------|------|----------|-----|---------|
| `common/quant_analysis.py` | 182–199, 254, 347 | `CreditSpread` class, `summary()` | R+W | property + dict write |
| `common/quant_analysis.py` | 778 | `enrich_trade_metrics()` | R | `t.get("max_profit_per_share")` |
| `app/services/recommendation_service.py` | 215 | `_score_candidate()` | R | `raw.get("max_profit_per_share")` |
| `frontend/assets/js/app.js` | 14 | `tradeDetailsHtml()` | R | `row.max_profit_per_share` |
| `frontend/assets/js/pages/home.js` | 172, 218 | `extractRawFields()`, `extractMetrics()` | R | `trade?.max_profit_per_share` |
| `frontend/assets/js/stores/homeCache.js` | 55 | `normalizeRow()` | R | `raw?.max_profit_per_share` **(preferred over per-contract!)** |

### `max_loss_per_share`

| File | Line | Function | R/W | Snippet |
|------|------|----------|-----|---------|
| `common/quant_analysis.py` | 182–199, 254, 348 | `CreditSpread` class, `summary()` | R+W | property + dict write |
| `common/quant_analysis.py` | 778 | `enrich_trade_metrics()` | R | `t.get("max_loss_per_share")` |
| `app/services/recommendation_service.py` | 216 | `_score_candidate()` | R | `raw.get("max_loss_per_share")` |
| `app/services/risk_policy_service.py` | 232 | `_estimate_risk()` | R | `trade.get("max_loss_per_share")` |
| `frontend/assets/js/app.js` | 13 | `tradeDetailsHtml()` | R | `row.max_loss_per_share` |
| `frontend/assets/js/pages/home.js` | 173, 219 | `extractRawFields()`, `extractMetrics()` | R | `trade?.max_loss_per_share` |
| `frontend/assets/js/stores/homeCache.js` | 56 | `normalizeRow()` | R | `raw?.max_loss_per_share` **(preferred over per-contract!)** |

---

## 2. Legacy Flat Metric Fields

> **Target:** Read from `computed.*` or `details.*` sub-dicts via
> `tradeAccessor.resolve()`.

### `p_win_used` (→ `computed.pop`)

| File | Line | Function | R/W | Snippet |
|------|------|----------|-----|---------|
| `common/quant_analysis.py` | 360 | `summary()` | W | `out["p_win_used"] = ...` |
| `common/quant_analysis.py` | 791 | `enrich_trade_metrics()` | R | `t.get("p_win_used")` |
| `common/utils.py` | 124–129 | `_merge_enriched()` | R+W | `merged.get('p_win_used')` / `merged['p_win_used'] = est_pop` |
| `app/services/strategies/iron_condor.py` | 376 | `_build_trade()` | W | `"p_win_used": pop_approx` |
| `app/services/strategies/credit_spread.py` | 173, 230 | `_filter_trade()`, `_score_trade()` | R | `trade.get("p_win_used")` |
| `app/services/risk_policy_service.py` | 196, 272, 436 | `_build_snapshot()`, `_apply_hard_limits()` | R+W | `trade.get("p_win_used")` |
| `app/services/strategy_service.py` | 490 | `_aggregate_summary()` | R | `t.get("p_win_used")` |
| `app/services/recommendation_service.py` | 258 | `_score_candidate()` | R | `raw.get("p_win_used")` |
| `app/api/routes_portfolio_risk.py` | 103, 160 | `_build_row()`, `_summary_row()` | R | `item.get("p_win_used")` |
| `frontend/assets/js/pages/home.js` | 169, 195, 257 | `extractRawFields()`, `extractMetrics()` | R | `trade?.p_win_used` |
| `frontend/assets/js/pages/trade_workbench.js` | 411 | `renderTradeCard()` | R | `trade.p_win_used` |
| `frontend/assets/js/stores/homeCache.js` | 68 | `normalizeRow()` | R | `raw?.p_win_used` |
| `frontend/assets/js/pages/admin_data_workbench.js` | 97 | `renderTradeCard()` | R | `metricNumber(trade, 'pop', 'p_win_used', ...)` |

### `pop_delta_approx` (→ `computed.pop`)

| File | Line | Function | R/W | Snippet |
|------|------|----------|-----|---------|
| `common/quant_analysis.py` | 352 | `summary()` | W | `"pop_delta_approx": pop` |
| `app/services/strategies/credit_spread.py` | 173 | `_filter_trade()` | R | fallback after `p_win_used` |
| `app/services/risk_policy_service.py` | 196, 272 | `_build_snapshot()` | R | fallback |
| `app/services/strategy_service.py` | 490 | `_aggregate_summary()` | R | fallback |
| `app/services/recommendation_service.py` | 260 | `_score_candidate()` | R | `raw.get("pop_delta_approx")` |
| `app/api/routes_portfolio_risk.py` | 103, 160 | `_build_row()`, `_summary_row()` | R | fallback |
| `frontend/assets/js/pages/home.js` | 170, 259 | `extractRawFields()`, `extractMetrics()` | R | `trade?.pop_delta_approx` |
| `frontend/assets/js/stores/homeCache.js` | 68 | `normalizeRow()` | R | `raw?.pop_delta_approx` |

### `ev_to_risk` (→ `computed.expected_value`)

| File | Line | Function | R/W | Snippet |
|------|------|----------|-----|---------|
| `common/quant_analysis.py` | 363 | `summary()` | W | `out["ev_to_risk"] = self.ev_to_risk(...)` |
| `common/utils.py` | 133 | `_merge_enriched()` | W | `merged['ev_to_risk'] = ...` |
| `app/services/strategies/credit_spread.py` | 175, 207–208, 229 | `_filter_trade()`, `_score_trade()` | R | `trade.get("ev_to_risk")` |
| `app/services/strategies/debit_spreads.py` | 293–295, 353, 405 | `_build_candidate()`, `_score_trade()` | W+R | `ev_to_risk = (ev / max_loss)` |
| `app/services/strategies/iron_condor.py` | 265, 389 | `_build_trade()` | W | `ev_to_risk = ev_per_contract / max_loss` |
| `app/services/strategy_service.py` | 739 | `_sort_analytics()` | R | `(tr.get("tie_breaks")...).get("ev_to_risk")` |
| `app/api/routes_strategy_analytics.py` | 172–184 | `_build_analytics()` | R+W | `snapshot.get("ev_to_risk")` → outputs `"ev_to_risk"` |
| `app/services/recommendation_service.py` | 254–256, 318 | `_score_candidate()` | R+W | `raw.get("ev_to_risk")` |
| `frontend/assets/js/pages/home.js` | 167, 252 | `extractRawFields()`, `extractMetrics()` | R | `trade?.ev_to_risk` |
| `frontend/assets/js/pages/strategy_analytics.js` | 153 | `renderTable()` | R | `p.ev_to_risk` |
| `frontend/assets/js/pages/strategy_dashboard_shell.js` | 605 | column config | R | `'ev_to_risk'` |
| `frontend/assets/js/stores/homeCache.js` | 67 | `normalizeRow()` | R | `raw?.ev_to_risk` |

### `bid_ask_spread_pct` (→ `computed.bid_ask_pct`)

| File | Line | Function | R/W | Snippet |
|------|------|----------|-----|---------|
| `common/quant_analysis.py` | 723 | `enrich_trade_metrics()` | W | `t["bid_ask_spread_pct"] = ...` |
| `app/services/strategies/credit_spread.py` | 179, 231 | `_filter_trade()`, `_score_trade()` | R | `trade.get("bid_ask_spread_pct")` |
| `app/services/strategies/debit_spreads.py` | 300, 350, 373 | `_build_candidate()`, `_filter_trade()` | W+R | `bid_ask_spread_pct = spread_pct` |
| `app/services/strategies/iron_condor.py` | 387 | `_build_trade()` | W | `"bid_ask_spread_pct": ...` |
| `app/services/risk_policy_service.py` | 195, 271, 432–434 | `_build_snapshot()`, `_apply_hard_limits()` | R+W | `trade.get("bid_ask_spread_pct")` |
| `frontend/assets/js/pages/home.js` | 302 | `extractMetrics()` | R | `raw?.bid_ask_spread_pct` |
| `frontend/assets/js/pages/admin_data_workbench.js` | 109 | `renderTradeCard()` | R | `metricNumber(trade, 'bid_ask_pct', 'bid_ask_spread_pct')` |

### `strike_distance_pct` (→ `computed.strike_dist_pct`)

| File | Line | Function | R/W | Snippet |
|------|------|----------|-----|---------|
| `common/quant_analysis.py` | 355 | `summary()` | W | `"strike_distance_pct": self.strike_distance_pct()` |
| `common/quant_analysis.py` | 736 | `enrich_trade_metrics()` | W | `t["strike_distance_pct"] = abs(...)` |

### `realized_vol_20d` (→ `computed.rv_20d`)

| File | Line | Function | R/W | Snippet |
|------|------|----------|-----|---------|
| `common/quant_analysis.py` | 606 | `compute_underlying_metrics()` | W | `"realized_vol_20d": ...` |
| `frontend/assets/js/pages/stock_scanner.js` | 270 | tile rendering | R | `dataMetric: 'realized_vol_20d'` |
| `frontend/assets/js/pages/stock_analysis.js` | 453, 554 | `renderResults()`, `renderDetail()` | R | `data-metric="realized_vol_20d"` |

### `probability_of_profit` (→ `computed.pop`)

| File | Line | Function | R/W | Snippet |
|------|------|----------|-----|---------|
| `app/services/recommendation_service.py` | 260 | `_score_candidate()` | R | `raw.get("probability_of_profit")` |
| `frontend/assets/js/pages/home.js` | 195, 259 | `extractMetrics()` | R | `row?.probability_of_profit` |
| `frontend/assets/js/stores/homeCache.js` | 68 | `normalizeRow()` | R | `raw?.probability_of_profit` |

### `expiration_date` (→ `expiration`)

| File | Line | Function | R/W | Snippet |
|------|------|----------|-----|---------|
| `app/services/base_data_service.py` | 317 | `_filter_expired()` | R | `row.get("expiration_date") or row.get("expiration")` |
| `common/model_analysis.py` | 78 | `_parse_idea()` | R | `idea.get("expiration_date")` |
| `frontend/assets/js/app.js` | 10 | `tradeDetailsHtml()` | R | `row.expiration_date` |

---

## 3. Duplicate Identity Fields

> **Target:** Read `strategy_id` only, `symbol` only.

### `spread_type` / `strategy` / `strategy_id` Triple-Reads

Every file below reads 2–3 of these variants to find the strategy name.
After migration, read `strategy_id` only.

| File | Lines | Function |
|------|-------|----------|
| `app/services/decision_service.py` | 40 | `record_decision()` |
| `app/services/risk_policy_service.py` | 173, 186, 226 | `_build_snapshot()`, `_estimate_risk()` |
| `app/services/strategy_service.py` | 143, 292, 441, 454, 563 | `_infer_option_type()`, `_normalize_trade()`, `_get_option_type()` |
| `app/services/trade_lifecycle_service.py` | 100–102, 125, 237, 311 | `normalize_payload()`, `record_event()`, `get_positions()` |
| `app/services/recommendation_service.py` | 49–50 | `_canonicalize()` |
| `app/services/data_workbench_service.py` | 140 | `_parse_trade_key()` |
| `app/services/report_service.py` | 1003 | `_normalize_report()` |
| `app/api/routes_portfolio_risk.py` | 87 | `_build_row()` |
| `app/api/routes_strategy_analytics.py` | 114, 170 | `_build_row()`, `_build_analytics()` |
| `frontend/assets/js/app.js` | 9 | `tradeDetailsHtml()` |
| `frontend/assets/js/pages/active_trades.js` | 37 | `normalizePosition()` |
| `frontend/assets/js/pages/trade_workbench.js` | 382 | `renderTradeCard()` |
| `frontend/assets/js/pages/home.js` | 197, 234, 344, 360, 383, 421, 445, 496, 512, 559 | Multiple functions |
| `frontend/assets/js/stores/homeCache.js` | 72 | `normalizeRow()` |
| `frontend/assets/js/pages/admin_data_workbench.js` | 116 | `renderTradeCard()` |
| `frontend/assets/js/ui/trade_card.js` | 31, 39 | `normalize()`, `render()` |
| `frontend/assets/js/utils/tradeKey.js` | 54 | `buildKey()` |

### `underlying` / `underlying_symbol` / `symbol` Triple-Reads

| File | Lines | Function |
|------|-------|----------|
| `app/services/decision_service.py` | 42 | `record_decision()` |
| `app/services/risk_policy_service.py` | 169, 224 | `_build_snapshot()`, `_estimate_risk()` |
| `app/services/strategy_service.py` | 187, 240, 553 | `_validate()`, `_aggregate()` |
| `app/services/trade_lifecycle_service.py` | 100, 307–308 | `normalize_payload()`, `get_positions()` |
| `app/services/recommendation_service.py` | 159 | `_group()` |
| `app/services/data_workbench_service.py` | 182, 650–651 | `_parse()`, `_merge()` |
| `app/services/ranking.py` | 121 | `_rank_trade()` |
| `app/services/report_service.py` | 375, 986, 1074 | `_summary()`, `_normalize()`, `_build_pill()` |
| `app/api/routes_active_trades.py` | 177 | `_build_position()` |
| `app/api/routes_portfolio_risk.py` | 86, 141–142 | `_build_row()`, `_summary_row()` |
| `app/api/routes_strategy_analytics.py` | 115 | `_build_row()` |
| `app/api/routes_reports.py` | 82 | `_summary()` |
| `frontend/assets/js/app.js` | 8 | `tradeDetailsHtml()` |
| `frontend/assets/js/pages/home.js` | 191, 233, 342–343, 444, 557–558 | Multiple functions |
| `frontend/assets/js/stores/homeCache.js` | 65 | `normalizeRow()` |
| `frontend/assets/js/pages/admin_data_workbench.js` | 118 | `renderTradeCard()` |
| `frontend/assets/js/ui/trade_card.js` | 29, 37 | `normalize()`, `render()` |

---

## 4. Legacy Spread-Type Alias Values

> **Target:** Emit canonical `strategy_id` values at the source.
> These are places that **hard-code legacy alias strings** as values
> (not alias-map definitions).

### Backend – Plugin Emitters

| File | Line | Function | Value Written |
|------|------|----------|---------------|
| `app/services/strategies/credit_spread.py` | 90, 132–133 | `_build_candidate()` | `"put_credit_spread"` (canonical ✓) |
| `app/services/strategies/debit_spreads.py` | 104–105, 135–136, 206 | `_build_candidate()` | `"debit_call_spread"`, `"debit_put_spread"` (non-canonical ✗) |
| `app/services/strategies/iron_condor.py` | 167 | `_build_trade()` | `"iron_condor"` (canonical ✓) |
| `app/services/stock_analysis_service.py` | 476, 615 | `_suggest_strategy()` | `"credit_put_spread"`, `"credit_call_spread"` (non-canonical ✗) |

### Backend – Service/Route Consumers

| File | Line | Function | Value |
|------|------|----------|-------|
| `app/api/routes_active_trades.py` | 307 | `_group_spreads()` | `"put_credit_spread"` / `"call_credit_spread"` (canonical ✓) |
| `app/services/regime_service.py` | 334, 354 | `_strategy_buckets()` | `"credit_put_spread"` (✗), `"debit_put_spread"` (✗) |
| `app/services/playbook_service.py` | 51–67 | `_build_playbook()` | `"credit_put_spread"` (✗), `"debit_call_spread"` (✗) |
| `app/services/strategy_service.py` | 146–148 | `_infer_option_type()` | `"put_credit_spread"` / `"call_credit_spread"` (✓) |
| `app/services/data_workbench_service.py` | 143–145 | `_infer_option_type()` | `"put_credit_spread"` / `"call_credit_spread"` (✓) |
| `common/utils.py` | 46–67, 151 | `DEMO_TRADES`, `merge_enriched()` | `'put_credit'`, `'call_credit'` (✗) |

### Frontend

| File | Line | Function | Value |
|------|------|----------|-------|
| `pages/stock_analysis.js` | 178–182, 273, 490 | `suggestStrategy()`, defaults | `'credit_put_spread'`, `'credit_call_spread'`, `'debit_call_spread'`, `'debit_put_spread'` (✗) |
| `pages/trade_workbench.js` | 53–56, 158, 290–308, 728 | `STRATEGIES`, `currentStrategy()`, `buildLegs()` | All four non-canonical credit/debit forms (✗) |
| `pages/stock_scanner.js` | 139–140, 322, 484 | `suggestStrategy()`, `runScan()` | `'credit_put_spread'`, `'credit_call_spread'` (✗) |
| `pages/home.js` | 94–98, 421, 1482–1483, 1753–1754 | `STRATEGY_ROUTE_MAP`, pipeline | `'credit_put_spread'`, `'debit_call_spread'`, etc. (✗) |
| `pages/strategy_dashboard_shell.js` | 576 | default filters | `'credit_call_spread'` (✗) |

---

## 5. Other Legacy Fields

### `estimated_risk` (→ `computed.max_loss`)

| File | Line | Function | R/W |
|------|------|----------|-----|
| `app/services/risk_policy_service.py` | 182, 258, 374, 391, 402, 460, 478, 485 | multiple | R+W |
| `app/api/routes_strategy_analytics.py` | 175 | `_build_analytics()` | R |
| `frontend/assets/js/pages/risk_capital.js` | 163, 227 | `renderTrade()`, `renderTable()` | R |
| `frontend/assets/js/pages/home.js` | 1007 | `buildPortfolioView()` | R |

### `risk_amount` (→ `computed.max_loss`)

| File | Line | Function | R/W |
|------|------|----------|-----|
| `frontend/assets/js/app.js` | 13 | `tradeDetailsHtml()` | R |
| `frontend/assets/js/pages/home.js` | 1007 | `buildPortfolioView()` | R |

### `estimated_max_profit` (→ `computed.max_profit`)

| File | Line | Function | R/W |
|------|------|----------|-----|
| `frontend/assets/js/app.js` | 14 | `tradeDetailsHtml()` | R |
| `app/trading/models.py` | 38 | `OrderPreview` model | W (dataclass) |
| `app/trading/service.py` | 255 | `preview_order()` | W |

### `premium_received` / `premium_paid` (→ `computed.net_credit` / `computed.net_debit`)

| File | Line | Function | R/W |
|------|------|----------|-----|
| `frontend/assets/js/app.js` | 15 | `tradeDetailsHtml()` | R |

### `scanner_score` (→ `details.trade_quality_score` or `computed_metrics.rank_score`)

| File | Line | Function | R/W |
|------|------|----------|-----|
| `app/services/stock_analysis_service.py` | 339, 378 | `_scan_ticker()`, `scan()` | W+R |
| `app/services/recommendation_service.py` | 193 | `_rank()` | R |
| `frontend/assets/js/pages/stock_analysis.js` | 443 | `renderResults()` | R |
| `frontend/assets/js/pages/stock_scanner.js` | 70 | `normalizeRow()` | R |
| `frontend/assets/js/pages/home.js` | 192 | `extractMetrics()` | R |
| `frontend/assets/js/stores/homeCache.js` | 66 | `normalizeRow()` | R |

---

## 6. Priority Migration Order

Ranked by blast radius (number of downstream consumers affected):

| Priority | Field(s) | Occurrences | Highest-Impact Files |
|----------|----------|-------------|----------------------|
| **P0** | `p_win_used` / `pop_delta_approx` | ~23 | `risk_policy_service.py`, `credit_spread.py`, `home.js` |
| **P0** | `ev_per_share` / `ev_to_risk` | ~30 | `quant_analysis.py`, `home.js`, `risk_policy_service.py` |
| **P0** | `max_profit_per_share` / `max_loss_per_share` | ~20 | `quant_analysis.py`, `homeCache.js`, `app.js` |
| **P1** | `spread_type`/`strategy` triple-reads | ~37 | `home.js`, `strategy_service.py`, `trade_lifecycle_service.py` |
| **P1** | `underlying`/`underlying_symbol` triple-reads | ~31 | `home.js`, `report_service.py`, `strategy_service.py` |
| **P1** | Legacy alias string values | ~35 | `trade_workbench.js`, `home.js`, `stock_analysis.js` |
| **P2** | `bid_ask_spread_pct` | ~12 | `credit_spread.py`, `risk_policy_service.py` |
| **P2** | `estimated_risk` | ~12 | `risk_policy_service.py`, `risk_capital.js` |
| **P3** | `scanner_score` | 7 | `stock_analysis_service.py`, `home.js` |
| **P3** | `expiration_date` | 3 | `base_data_service.py`, `app.js` |
| **P3** | `realized_vol_20d` | 4 | `stock_scanner.js`, `stock_analysis.js` |
| **P3** | Others (`risk_amount`, `premium_*`, `probability_of_profit`) | ~8 | scattered |

---

## 7. Highest-Impact Files (Migration Order)

Files ranked by total legacy field access count:

| # | File | ~Legacy Reads | Key Concerns |
|---|------|---------------|--------------|
| 1 | `frontend/assets/js/pages/home.js` | 25+ | Most complex frontend consumer; `extractRawFields()`, `extractMetrics()`, `buildPayload()` all have long fallback chains |
| 2 | `backend/app/services/risk_policy_service.py` | 15+ | Consumes per-share, `p_win_used`, `bid_ask_spread_pct`, `estimated_risk` |
| 3 | `backend/common/quant_analysis.py` | 12+ | **Source** of per-share field writes in `summary()` and `enrich_trade_metrics()` |
| 4 | `backend/app/services/strategy_service.py` | 10+ | Triple-identity reads, `p_win_used` |
| 5 | `frontend/assets/js/stores/homeCache.js` | 8+ | Duplicates home.js fallback chains; **prefers per-share over per-contract** |
| 6 | `backend/app/services/strategies/credit_spread.py` | 8+ | Reads `p_win_used`, `ev_to_risk`, `bid_ask_spread_pct` directly |
| 7 | `backend/app/services/strategies/debit_spreads.py` | 7+ | Writes `ev_per_share`, `ev_to_risk`, `bid_ask_spread_pct` |
| 8 | `backend/app/services/strategies/iron_condor.py` | 6+ | Writes `ev_per_share`, `p_win_used`, `ev_to_risk` |
| 9 | `frontend/assets/js/pages/trade_workbench.js` | 6+ | Uses legacy aliases for strategy values + per-share reads |
| 10 | `backend/app/services/recommendation_service.py` | 6+ | Reads per-share, `p_win_used`, `ev_to_risk` |
