# Audit 3E — Output Contract & Consumer Alignment

**Scope**: What the stock and options pipelines actually produce, what downstream consumers expect, and where mismatches exist.

**Date**: 2025-06-01  
**Auditor**: Copilot  
**Method**: Full trace of pipeline output through extraction → read model → API → frontend normalization. All field inventories verified against source files.

---

## PART 1: Stock Pipeline Output

### 1. Stock Candidate Output Schema

Each stock candidate passes through: raw scanner dict → 28-field normalized contract → enrichment → model analysis → compact extraction → `output.json`.

**Compact candidate shape** (from `_extract_compact_stock_candidate()` in `stock_opportunity_runner.py` L325-395):

| # | Field | Type | Source Stage | Always Present? | Example |
|---|-------|------|-------------|-----------------|---------|
| 1 | `symbol` | `str` | Scanner (Stage 3) | ✅ Yes | `"AAPL"` |
| 2 | `scanner_key` | `str` | Scanner (Stage 3) | ✅ Yes | `"stock_mean_reversion"` |
| 3 | `scanner_name` | `str` | Normalize (Stage 4) | ✅ Yes | `"Mean Reversion"` |
| 4 | `setup_type` | `str` | Normalize (Stage 4) | ✅ Yes | `"mean_reversion"` |
| 5 | `direction` | `str` | Scanner metadata | ✅ Yes | `"long"` |
| 6 | `source_scanners` | `list[str]` | Dedup (Stage 4) | ✅ Yes | `["stock_mean_reversion"]` |
| 7 | `setup_quality` | `float\|None` | Scanner (Stage 3) | ⚠️ Should be | `72.5` |
| 8 | `confidence` | `float\|None` | Scanner (Stage 3) | ⚠️ Should be | `1.0` |
| 9 | `rank` | `int` | Rank (Stage 6) | ✅ Yes | `3` |
| 10 | `thesis_summary` | `list[str]` | Scanner (Stage 3) | ✅ Default `[]` | `["RSI oversold..."]` |
| 11 | `supporting_signals` | `list[str]` | Scanner (Stage 3) | ✅ Default `[]` | `["Below SMA50"]` |
| 12 | `risk_flags` | `list[str]` | Scanner (Stage 3) | ✅ Default `[]` | `[]` |
| 13 | `entry_context` | `dict` | Normalize (Stage 4) | ✅ Yes | `{"price": 175.20, "state": "oversold"}` |
| 14 | `market_regime` | `str\|None` | Enrich (Stage 5) | ⚠️ Optional | `"bullish"` |
| 15 | `risk_environment` | `str\|None` | Enrich (Stage 5) | ⚠️ Optional | `"stable"` |
| 16 | `market_state_ref` | `str\|None` | Enrich (Stage 5) | ⚠️ Optional | `"mkt_state_v1::..."` |
| 17 | `vix` | `float\|None` | Enrich (Stage 5) | ⚠️ Optional | `18.5` |
| 18 | `regime_tags` | `list[str]` | Enrich (Stage 5) | ✅ Default `[]` | `["risk_on"]` |
| 19 | `support_state` | `str\|None` | Enrich (Stage 5) | ⚠️ Optional | `"holding"` |
| 20 | `market_picture_summary` | `dict\|None` | Enrich (Stage 6) | ⚠️ Optional | `{6 engine cards}` |
| 21 | `top_metrics` | `dict` | Package (Stage 8) | ✅ Yes | `{"rsi": 28, "atr_pct": 0.03}` |
| 22 | `review_summary` | `str` | Package (Stage 8) | ✅ Yes | `"Mean Reversion setup..."` |
| 23 | `model_recommendation` | `str\|None` | Model (Stage 7) | ⚠️ None if skipped | `"BUY"` |
| 24 | `model_confidence` | `float\|None` | Model (Stage 7) | ⚠️ None if skipped | `0.85` |
| 25 | `model_score` | `int\|None` | Model (Stage 7) | ⚠️ None if skipped | `82` |
| 26 | `model_review_summary` | `str\|None` | Model (Stage 7) | ⚠️ None if skipped | `"Strong setup..."` |
| 27 | `model_key_factors` | `list\|None` | Model (Stage 7) | ⚠️ None if skipped | `[{factor, impact, evidence}]` |
| 28 | `model_caution_notes` | `list\|None` | Model (Stage 7) | ⚠️ None if skipped | `["Vol elevated"]` |
| 29 | `model_technical_analysis` | `dict\|None` | Model (Stage 7) | ⚠️ None if skipped | `{full breakdown}` |

**Stock output.json envelope** (L1549-1580):

```
{
  "contract_version": "1.0",
  "workflow_id": "stock_opportunity",
  "run_id": str,
  "generated_at": ISO 8601,
  "batch_status": "completed" | "partial",
  "market_state_ref": str | None,
  "publication": { "status": "valid"|"degraded", "market_state_publication_status": str },
  "candidates": [ <compact candidates> ],
  "quality": {
    "level": "good"|"degraded"|"no_candidates",
    "total_candidates_found": int,
    "selected_count": int,
    "top_n_cap": int,
    "scanners_ok": int,
    "scanners_total": int
  },
  "scanner_coverage": { per-scanner hit counts },
  "scanner_suite": [...],
  "filter_counts": { stage → count },
  "market_picture_summary": { MI digest },
  "model_analysis_counts": { analyzed, buy, pass, error }
}
```

---

### 2. Scanner Candidate Contract (28 Fields)

**File**: `app/services/scanner_candidate_contract.py`

The architecture doc mentions "27 normalized fields." The actual `REQUIRED_FIELDS` frozenset contains **28 fields** (L195-224):

| # | Field | Type | Purpose |
|---|-------|------|---------|
| 1 | `candidate_id` | `str` | Unique identifier |
| 2 | `scanner_key` | `str` | Machine identifier |
| 3 | `scanner_name` | `str` | Display name |
| 4 | `strategy_family` | `str` | `"stock"` or `"options"` |
| 5 | `setup_type` | `str` | Strategy label |
| 6 | `asset_class` | `str` | `"equity"` or `"option"` |
| 7 | `symbol` | `str` | Uppercase ticker |
| 8 | `underlying` | `str` | Underlying ticker |
| 9 | `direction` | `str` | `"long"`, `"short"`, `"neutral"` |
| 10 | `thesis_summary` | `list[str]` | Setup thesis bullets |
| 11 | `entry_context` | `dict` | Price/state context |
| 12 | `time_horizon` | `str` | From shared vocabulary |
| 13 | `setup_quality` | `float\|None` | 0-100 composite |
| 14 | `confidence` | `float\|None` | 0.0-1.0 source confidence |
| 15 | `risk_definition` | `dict` | Structured risk params |
| 16 | `reward_profile` | `dict` | Structured reward params |
| 17 | `supporting_signals` | `list[str]` | Bullish/confirming signals |
| 18 | `risk_flags` | `list[str]` | Warning signals |
| 19 | `invalidation_signals` | `list[str]` | Invalidation conditions |
| 20 | `market_context_tags` | `list[str]` | Machine-readable context |
| 21 | `position_sizing_notes` | `None\|str` | Sizing guidance |
| 22 | `data_quality` | `dict` | Quality summary |
| 23 | `source_status` | `dict` | Source availability |
| 24 | `pricing_snapshot` | `dict` | Current pricing |
| 25 | `strategy_structure` | `None\|dict` | Legs for options, None for stocks |
| 26 | `candidate_metrics` | `dict` | Key computed numbers |
| 27 | `detail_sections` | `dict` | Scanner-specific extras |
| 28 | `generated_at` | `str` | ISO 8601 timestamp |

**Scanner Population Verification**:

All 4 stock scanners call `normalize_candidate_output()` (L254) which routes to `_normalize_stock_candidate()` (L279). This function populates all 28 fields from the scanner's raw dict using a shared mapping. All 4 scanners produce the same 28-field shape.

| Field | Mean Reversion | Momentum | Pullback | Vol Expansion |
|-------|:---:|:---:|:---:|:---:|
| All 28 fields | ✅ Via shared normalizer | ✅ Via shared normalizer | ✅ Via shared normalizer | ✅ Via shared normalizer |
| `candidate_metrics` content | RSI, zscore, atr_pct, drawdown | RSI, high_55d, vol_spike | trend, pullback, liquidity | ATR ratio, BB width, compression |
| `detail_sections` content | reversion_state, sub_scores | breakout_state, sub_scores | trend_state | expansion_state, sub_scores |

**Discrepancy**: Architecture doc says 27 fields; actual contract has **28** (28th is `generated_at` which may have been added after the doc was written).

---

### 3. Model Analysis Fields

The LLM model analysis (Stage 7) adds these fields to each candidate:

| Field | Type | Source | Always Present? |
|-------|------|--------|-----------------|
| `model_recommendation` | `str` | LLM response parse | ✅ `"BUY"` or `"PASS"` |
| `model_confidence` | `float` | LLM response parse | ✅ 0.0-1.0 |
| `model_score` | `int` | LLM response parse | ✅ 0-100 |
| `model_review_summary` | `str` | LLM response parse | ✅ Free text |
| `model_key_factors` | `list[dict]` | LLM response parse | ✅ `[{factor, impact, evidence}]` |
| `model_caution_notes` | `list[str]` | LLM response parse | ✅ Risk bullets |
| `model_technical_analysis` | `dict` | LLM response parse | ⚠️ Optional |

When model analysis is **skipped or fails**, all model fields are `None` in the compact output.

---

### 4. TMC Frontend Stock Consumer

**File**: `frontend/assets/js/pages/trade_management_center.js` L233-278

`normalizeStockCandidate(raw)` reads these fields:

| Frontend Field | Backend Field | Transform | Mismatch? |
|----------------|--------------|-----------|-----------|
| `symbol` | `raw.symbol` | Direct | ✅ Match |
| `action` | `raw.direction` | `"long"→"buy"`, `"short"→"sell"` | ✅ Match |
| `setupQuality` | `raw.setup_quality` | Direct | ✅ Match |
| `confidence` | `raw.confidence` | Direct | ✅ Match |
| `rank` | `raw.rank` | Direct | ✅ Match |
| `rationale` | `raw.review_summary` | Direct | ✅ Match |
| `thesis` | `raw.thesis_summary` | Array coerce | ✅ Match |
| `points` | `raw.supporting_signals` | Array coerce | ✅ Match |
| `risks` | `raw.risk_flags` | Array coerce | ✅ Match |
| `scannerName` | `raw.scanner_name \|\| raw.scanner_key` | Fallback | ✅ Match |
| `setupType` | `raw.setup_type` | Direct | ✅ Match |
| `topMetrics` | `raw.top_metrics` | Default `{}` | ✅ Match |
| `marketRegime` | `raw.market_regime` | Direct | ✅ Match |
| `riskEnvironment` | `raw.risk_environment` | Direct | ✅ Match |
| `sourceScanners` | `raw.source_scanners` | Array coerce | ✅ Match |
| `marketPictureSummary` | `raw.market_picture_summary` | Direct | ✅ Match |
| `marketStateRef` | `raw.market_state_ref` | Direct | ✅ Match |
| `vix` | `raw.vix` | Direct | ✅ Match |
| `regimeTags` | `raw.regime_tags` | Array coerce | ✅ Match |
| `supportState` | `raw.support_state` | Direct | ✅ Match |
| `modelRecommendation` | `raw.model_recommendation` | Direct | ✅ Match |
| `modelConfidence` | `raw.model_confidence` | Direct | ✅ Match |
| `modelScore` | `raw.model_score` | Direct | ✅ Match |
| `modelReviewSummary` | `raw.model_review_summary` | Direct | ✅ Match |
| `modelKeyFactors` | `raw.model_key_factors` | Array coerce | ✅ Match |
| `modelCautionNotes` | `raw.model_caution_notes` | Array coerce | ✅ Match |

**Result**: **No mismatches** in stock pipeline. Frontend reads 26 fields, backend produces all 29 compact fields. 3 backend fields not consumed by normalizer:
- `entry_context` — used elsewhere in card rendering
- `model_technical_analysis` — may be surfaced in detail view
- Backend `scanner_key` is read as fallback for `scanner_name`

---

## PART 2: Options Pipeline Output

### 5. Options Candidate Output Schema

Each options candidate passes through: V2Candidate → compact extraction → enrichment → credibility gate → `output.json`.

**Compact candidate shape** (from `_extract_compact_candidate()` in `options_opportunity_runner.py` L264-380):

| # | Field | Type | Source | Always? | Example |
|---|-------|------|--------|---------|---------|
| 1 | `candidate_id` | `str` | Phase B | ✅ | `"SPY\|put_credit_spread\|2026-03-27\|540/535\|1"` |
| 2 | `scanner_key` | `str` | Phase B | ✅ | `"put_credit_spread"` |
| 3 | `strategy_id` | `str` | Phase B | ✅ | `"put_credit_spread"` |
| 4 | `family_key` | `str` | Phase B | ✅ | `"vertical_spreads"` |
| 5 | `symbol` | `str` | Phase A | ✅ | `"SPY"` |
| 6 | `underlying_price` | `float\|None` | Phase A | ⚠️ | `545.30` |
| 7 | `expiration` | `str` | Phase A | ✅ | `"2026-03-27"` |
| 8 | `expiration_back` | `str\|None` | Phase B | Calendar only | `"2026-04-17"` |
| 9 | `dte` | `int\|None` | Phase A | ⚠️ | `7` |
| 10 | `dte_back` | `int\|None` | Phase B | Calendar only | `28` |
| 11 | `legs` | `list[dict]` | Phase A+B | ✅ | `[{strike, side, option_type, bid, ask, delta, iv, OI, vol}]` |
| 12 | `leg_count` | `int` | Derived | ✅ | `2` |
| 13 | `math.net_credit` | `float\|None` | Phase E | Credit only | `0.45` (per-share) |
| 14 | `math.net_debit` | `float\|None` | Phase E | Debit only | `1.20` (per-share) |
| 15 | `math.max_profit` | `float\|None` | Phase E | Most (not cal) | `45.00` (per-contract) |
| 16 | `math.max_loss` | `float\|None` | Phase E | Most | `455.00` (per-contract) |
| 17 | `math.width` | `float\|None` | Phase E | Multi-leg | `5.0` (per-share) |
| 18 | `math.pop` | `float\|None` | Phase E | Most (not cal) | `0.70` |
| 19 | `math.pop_source` | `str\|None` | Phase E | If pop computed | `"delta_approx"` |
| 20 | `math.ev` | `float\|None` | Phase E | Most (not cal) | `12.50` (per-contract) |
| 21 | `math.ev_per_day` | `float\|None` | Phase E | If ev+dte | `1.78` |
| 22 | `math.ror` | `float\|None` | Phase E | Most (not cal) | `0.099` |
| 23 | `math.kelly` | `float\|None` | Phase E | If pop+ror | `0.14` |
| 24 | `math.breakeven` | `list[float]` | Phase E | Most (not cal) | `[535.55]` |
| 25 | `structural_validation` | `dict` | Phase C | ✅ | `{total_checks, passed, pass_count, failure_count}` |
| 26 | `math_validation` | `dict` | Phase E | ✅ | `{total_checks, passed, pass_count, failure_count}` |
| 27 | `hygiene` | `dict` | Phase D | ✅ | `{quote_sanity_ok, liquidity_ok, ...}` |
| 28 | `diagnostics_summary` | `dict` | Phases C-F | ✅ | `{reject_reasons, warnings, pass_reasons}` |
| 29 | `passed` | `bool` | Phase F | ✅ | `true` |
| 30 | `downstream_usable` | `bool` | Phase F | ✅ | `true` |
| 31 | `contract_version` | `str` | Constant | ✅ | `"2.0.0"` |
| 32 | `scanner_version` | `str` | Family impl | ✅ | `"1.0"` |
| 33 | `generated_at` | `str` | Phase F | ✅ | ISO 8601 |
| 34 | `market_state_ref` | `str\|None` | Enrich (Stage 4) | ⚠️ | Attached in Stage 5 |
| 35 | `rank` | `int` | Enrich (Stage 4) | ✅ | 1-based EV rank |

---

### 6. V2Candidate Data Model

**File**: `app/services/scanner_v2/contracts.py` L242-325

**29 dataclass fields** (4 identity + 6 asset/expiry + 2 structure + 4 diagnostics + 3 lineage + 1 debug):

| Category | Fields | Type | Calendar-Specific |
|----------|--------|------|-------------------|
| Identity | `candidate_id`, `scanner_key`, `strategy_id`, `family_key` | `str` | — |
| Asset | `symbol`, `underlying_price` | `str`, `float\|None` | — |
| Expiry | `expiration`, `dte`, `expiration_back`, `dte_back` | Mixed | `expiration_back` + `dte_back` populated |
| Structure | `legs: list[V2Leg]`, `math: V2RecomputedMath` | Compound | — |
| Diag | `diagnostics: V2Diagnostics`, `passed`, `downstream_usable` | Mixed | — |
| Lineage | `contract_version`, `scanner_version`, `generated_at` | `str` | — |
| Debug | `_raw_construction` | `dict` | Stripped on serialize |

**Calendar/Diagonal None fields**: `max_profit`, `pop`, `ev`, `ev_per_day`, `ror`, `kelly`, `breakeven` are all `None` (path-dependent, no closed-form). Only `net_debit` and approximate `max_loss` are populated.

---

### 7. TMC Frontend Options Consumer

**File**: `frontend/assets/js/pages/trade_management_center.js` L280-318

`normalizeOptionsCandidate(raw)` reads these fields:

| Frontend Field | Backend Field | Transform | Mismatch? |
|----------------|--------------|-----------|-----------|
| `symbol` | `raw.underlying \|\| raw.symbol` | Fallback | ⚠️ Backend uses `symbol`, not `underlying` |
| `strategy` | `raw.strategy_id \|\| raw.strategy_type \|\| raw.family_key` | Fallback chain | ✅ Match |
| `family` | `raw.family_key` | Direct | ✅ Match |
| `ev` | `m.ev` | Number coerce | ✅ Match |
| `pop` | `m.pop` | Number coerce | ✅ Match |
| `popSource` | `m.pop_source` | Direct | ✅ Match |
| `maxLoss` | `m.max_loss` | Number coerce | ✅ Match |
| `maxProfit` | `m.max_profit` | Number coerce | ✅ Match |
| `credit` | `m.net_credit` | Number coerce | ✅ Match |
| `debit` | `m.net_debit` | Number coerce | ✅ Match |
| `premium` | Derived | `credit > 0 ? credit : debit` | ✅ Derived |
| `premiumLabel` | Derived | `"credit"` or `"debit"` | ✅ Derived |
| `dte` | `raw.dte` | Direct | ✅ Match |
| `width` | `m.width` | Number coerce | ✅ Match |
| `ror` | `m.ror` | Number coerce | ✅ Match |
| `evPerDay` | `m.ev_per_day` | Number coerce | ✅ Match |
| `breakeven` | `m.breakeven` | Default `[]` | ✅ Match |
| `legs` | `raw.legs` | Array coerce | ✅ Match |
| `rank` | `raw.rank` | Direct | ✅ Match |
| `expiration` | `raw.expiration` | Direct | ✅ Match |
| `underlyingPrice` | `raw.underlying_price` | Direct | ✅ Match |

**Minor Symbol Mismatch**: Frontend tries `raw.underlying` first, then `raw.symbol`. Backend compact output uses `symbol` (not `underlying`). This works because `symbol` is always present, but the fallback order is backwards — it tries a field that doesn't exist first.

**Fields backend produces but frontend ignores** (14 fields):
- `candidate_id`, `scanner_key`, `leg_count`
- `expiration_back`, `dte_back` (calendar data not displayed)
- `math.kelly` (not surfaced in cards)
- `structural_validation`, `math_validation`, `hygiene`, `diagnostics_summary`
- `passed`, `downstream_usable`, `contract_version`, `scanner_version`, `generated_at`
- `market_state_ref` (not used in options cards unlike stock cards)

---

## PART 3: Market Intelligence Output

### 8. MI Output Contract (market_state.json)

**Schema defined in**: `app/workflows/market_state_contract.py` L246-283

**15 required top-level keys** (`REQUIRED_TOP_LEVEL_KEYS`):

| Key | Type | Description |
|-----|------|-------------|
| `contract_version` | `str` | `"1.0"` |
| `artifact_id` | `str` | Unique run ID |
| `workflow_id` | `str` | `"market_intelligence"` |
| `generated_at` | `str` | ISO 8601 |
| `publication` | `dict` | `{status, published_at, prior_artifact_id}` |
| `freshness` | `dict` | `{overall, per_source}` |
| `quality` | `dict` | Source health (9 sub-fields) |
| `market_snapshot` | `dict` | Macro metric envelopes |
| `engines` | `dict` | `{engine_key → normalized 25-field output}` |
| `composite` | `dict` | 3-state composite + evidence |
| `conflicts` | `dict\|None` | Conflict detector report |
| `model_interpretation` | `dict\|None` | LLM market analysis |
| `consumer_summary` | `dict` | Compact downstream digest |
| `lineage` | `dict` | Provenance references |
| `warnings` | `list[str]` | Aggregated warnings |

**Consumers**:
- Stock opportunity runner (Stage 2): loads `consumer_summary` for regime/VIX enrichment
- Options opportunity runner (Stage 1): loads `consumer_summary` for regime enrichment
- Frontend home dashboard: loads engines for 6-engine chart + scoreboard
- Frontend TMC stock cards: receives `market_picture_summary` (compact digest of 6 engines)
- `market_picture_contract.py`: normalizes engine data for dashboard cards

---

### 9. Engine Output Contract (25 Fields)

**File**: `app/services/engine_output_contract.py`

**`REQUIRED_FIELDS` frozenset** (25 fields, L52-67):

| # | Field | Type | Always Populated? |
|---|-------|------|-------------------|
| 1 | `engine_key` | `str` | ✅ |
| 2 | `engine_name` | `str` | ✅ |
| 3 | `as_of` | `str` | ✅ |
| 4 | `score` | `int\|None` | ⚠️ None if engine failed |
| 5 | `label` | `str` | ✅ Default `"Unknown"` |
| 6 | `short_label` | `str` | ✅ Default `"Unknown"` |
| 7 | `confidence` | `int` | ✅ Default `0` |
| 8 | `signal_quality` | `str` | ✅ `"high"\|"medium"\|"low"` |
| 9 | `time_horizon` | `str` | ✅ From vocabulary |
| 10 | `freshness` | `dict` | ✅ `{compute_duration_s, cache_hit, sources}` |
| 11 | `summary` | `str` | ✅ Default `""` |
| 12 | `trader_takeaway` | `str` | ✅ Default `""` |
| 13 | `bull_factors` | `list[str]` | ✅ Default `[]` |
| 14 | `bear_factors` | `list[str]` | ✅ Default `[]` |
| 15 | `risks` | `list[str]` | ✅ |
| 16 | `regime_tags` | `list[str]` | ✅ Derived |
| 17 | `supporting_metrics` | `list[dict]` | ✅ Max 10 |
| 18 | `contradiction_flags` | `list[str]` | ✅ Default `[]` |
| 19 | `data_quality` | `dict` | ✅ 5 sub-fields |
| 20 | `warnings` | `list[str]` | ✅ |
| 21 | `source_status` | `dict` | ✅ |
| 22 | `pillar_scores` | `list[dict]` | ✅ Ordered by weight |
| 23 | `detail_sections` | `dict` | ✅ Engine-specific |
| 24 | `engine_status` | `str` | ✅ `"ok"\|"degraded"\|"error"\|"no_data"` |
| 25 | `status_detail` | `dict` | ✅ |

**All 25 fields always populated**: The normalizer (`_normalize_pillar_engine` and `_normalize_news`) assigns defaults for every field. The `score` field may be `None` for failed engines, but the key is always present.

**Coverage**: 6 engines normalized through this contract:
- 5 pillar engines via `_normalize_pillar_engine()`: breadth_participation, volatility_options, cross_asset_macro, flows_positioning, liquidity_financial_conditions
- 1 special case via `_normalize_news()`: news_sentiment (uses `internal_engine` instead of `engine_result`, `regime_label` instead of `label`)

---

## PART 4: Cross-Pipeline Consistency

### 10. Read Model Alignment

**Stock Read Model** (`StockOpportunityReadModel` in `tmc_service.py` L124-158):

| Read Model Field | Pipeline Output Field | Match? |
|------------------|----------------------|--------|
| `run_id` | `output_data["run_id"]` | ✅ |
| `workflow_id` | `output_data["workflow_id"]` | ✅ |
| `generated_at` | `output_data["generated_at"]` | ✅ |
| `market_state_ref` | `output_data["market_state_ref"]` | ✅ |
| `status` | Derived from TMCStatus | ✅ |
| `batch_status` | `output_data["batch_status"]` | ✅ |
| `total_candidates` | `output_data["quality"]["total_candidates_found"]` | ✅ |
| `selected_count` | `output_data["quality"]["selected_count"]` | ✅ |
| `quality_level` | `output_data["quality"]["level"]` | ✅ |
| `candidates` | `output_data["candidates"]` | ✅ Pass-through |
| `warnings` | Aggregated | ✅ |

**No deprecated fields found**. The read model is a thin wrapper that extracts envelope fields and passes candidates through unmodified.

**Options Read Model** (`OptionsOpportunityReadModel` in `tmc_service.py` L159-210):

| Read Model Field | Pipeline Output Field | Match? |
|------------------|----------------------|--------|
| Same 11 envelope fields | Same | ✅ |
| `scan_diagnostics` | `output_data["scan_diagnostics"]` | ✅ |
| `validation_summary` | `output_data["validation_summary"]` | ✅ |

**No deprecated fields found**. Options read model adds 2 extra diagnostic fields vs stock model.

**Structural difference**: Stock output.json has `scanner_coverage`, `scanner_suite`, `filter_counts`, `market_picture_summary`, `model_analysis_counts` at top level. Options has `scan_diagnostics`, `validation_summary`, `quality.credibility_filter` at top level. The read models only extract fields they need — both are aligned.

---

### 11. API Contract Stability

**Versioning mechanism**:
- `WORKFLOW_VERSION = "1.0"` in `definitions.py` — shared by stock and options runners
- `SCANNER_V2_CONTRACT_VERSION = "2.0.0"` in `scanner_v2/contracts.py` — V2 candidate shape
- `MARKET_STATE_CONTRACT_VERSION = "1.0"` in `market_state_contract.py` — MI artifact

**Version checking**:
- Backend: Includes `contract_version` in every output.json
- Frontend: **Does NOT validate** the version field. Uses null-coalescing (`raw.field || null`) for all field access
- No schema validation exists on either side

**Breaking change protection**:
- Backend uses defensive extraction with `.get()` and defaults everywhere
- Frontend uses `||` fallbacks for every field
- No formal schema validation, no breaking-change detection
- A removed field would silently become `null` on the frontend rather than error

---

## Comparison Matrix

### Stock Pipeline: Backend → Frontend

| Field | Backend Output | Frontend Expects | Match? |
|-------|---------------|-----------------|--------|
| `symbol` | ✅ Always `str` | `raw.symbol` | ✅ |
| `direction` | ✅ Always `str` | `raw.direction` → `action` | ✅ |
| `setup_quality` | ✅ `float\|None` | `raw.setup_quality` | ✅ |
| `confidence` | ✅ `float\|None` | `raw.confidence` | ✅ |
| `rank` | ✅ `int` | `raw.rank` | ✅ |
| `review_summary` | ✅ `str` | `raw.review_summary` → `rationale` | ✅ |
| `thesis_summary` | ✅ `list[str]` | `raw.thesis_summary` → `thesis` | ✅ |
| `supporting_signals` | ✅ `list[str]` | `raw.supporting_signals` → `points` | ✅ |
| `risk_flags` | ✅ `list[str]` | `raw.risk_flags` → `risks` | ✅ |
| `scanner_name` | ✅ `str` | `raw.scanner_name` | ✅ |
| `top_metrics` | ✅ `dict` | `raw.top_metrics` | ✅ |
| `market_regime` | ⚠️ Optional | `raw.market_regime` | ✅ null ok |
| `market_picture_summary` | ⚠️ Optional | `raw.market_picture_summary` | ✅ null ok |
| `model_recommendation` | ⚠️ None if skipped | `raw.model_recommendation` | ✅ null ok |
| `model_confidence` | ⚠️ None if skipped | `raw.model_confidence` | ✅ null ok |
| `model_score` | ⚠️ None if skipped | `raw.model_score` | ✅ null ok |

### Options Pipeline: Backend → Frontend

| Field | Backend Output | Frontend Expects | Match? |
|-------|---------------|-----------------|--------|
| `symbol` | ✅ `str` | `raw.underlying \|\| raw.symbol` | ⚠️ Backend has `symbol`, FE tries `underlying` first |
| `strategy_id` | ✅ `str` | `raw.strategy_id` | ✅ |
| `family_key` | ✅ `str` | `raw.family_key` | ✅ |
| `math.ev` | ⚠️ None for calendars | `m.ev` | ✅ null ok |
| `math.pop` | ⚠️ None for calendars | `m.pop` | ✅ null ok |
| `math.net_credit` | ⚠️ Credit strategies only | `m.net_credit` | ✅ null ok |
| `math.net_debit` | ⚠️ Debit strategies only | `m.net_debit` | ✅ null ok |
| `math.max_loss` | ⚠️ Approx for calendars | `m.max_loss` | ✅ null ok |
| `math.max_profit` | ⚠️ None for calendars | `m.max_profit` | ✅ null ok |
| `math.width` | ⚠️ Not for all | `m.width` | ✅ null ok |
| `math.ror` | ⚠️ None for calendars | `m.ror` | ✅ null ok |
| `dte` | ✅ | `raw.dte` | ✅ |
| `legs` | ✅ array | `raw.legs` | ✅ |
| `rank` | ✅ `int` | `raw.rank` | ✅ |
| `expiration` | ✅ `str` | `raw.expiration` | ✅ |
| `underlying_price` | ⚠️ Optional | `raw.underlying_price` | ✅ null ok |
| `expiration_back` | Calendar only | **Not consumed** | ⚠️ Gap |
| `dte_back` | Calendar only | **Not consumed** | ⚠️ Gap |
| `market_state_ref` | ✅ Enriched | **Not consumed** | — |
| 14 other fields | ✅ Produced | **Not consumed** | — |

---

## Findings

### F-3E-01 [HIGH] — Calendar/Diagonal Candidates Have Mostly-Null Math in Output

**What**: Calendar and diagonal spread candidates have `None` for `max_profit`, `pop`, `ev`, `ev_per_day`, `ror`, `kelly`, and `breakeven`. Only `net_debit` and approximate `max_loss` are populated. The frontend handles this gracefully (null-coalescing), but these candidates render as mostly-empty cards with no EV, POP, or ROR — making them non-comparable to verticals/IC/butterflies.

**Where**: `V2RecomputedMath` defaults; calendar family Phase E implementation

**Impact**: Calendar candidates can reach output.json but are effectively useless for comparison. They sort to the bottom of EV-ranked lists (EV=None treated as worst). Frontend displays them with blank metrics.

**Recommendation**: Either (a) implement approximate calendar math (e.g., scenario-based POP/EV), or (b) segregate calendars into a separate ranking tier with explicit "informational only" labeling.

---

### F-3E-02 [MEDIUM] — Architecture Doc Says 27 Fields, Contract Has 28

**What**: The architecture doc and scanner_candidate_contract.py docstring mention "27 normalized fields." The actual `REQUIRED_FIELDS` frozenset contains **28 fields** (the 28th is `generated_at`).

**Where**: `scanner_candidate_contract.py` L195 — `REQUIRED_FIELDS` has 28 entries

**Impact**: Low functional impact (just a doc discrepancy), but documentation drift erodes trust in the contract specification.

**Recommendation**: Update architecture doc to say 28 fields.

---

### F-3E-03 [MEDIUM] — Frontend Options Normalizer Tries `underlying` Before `symbol`

**What**: `normalizeOptionsCandidate()` reads `raw.underlying || raw.symbol` for the symbol field. The options compact output uses `symbol` — there is no `underlying` field at the compact candidate level. The code works because `raw.underlying` evaluates to `undefined` (falsy) and falls through to `raw.symbol`, but the field access order is misleading.

**Where**: `trade_management_center.js` L289 — `raw.underlying || raw.symbol`

**Impact**: No functional bug (fallback works), but if `underlying` were ever added with a different value than `symbol`, the behavior would be incorrect. Also violates principle of least surprise.

**Recommendation**: Change to `raw.symbol || raw.underlying` to match the backend field name, or simply `raw.symbol`.

---

### F-3E-04 [MEDIUM] — No Schema Validation on Frontend

**What**: The frontend reads `contract_version` from the response but **does not validate** it. If the backend changes the output schema (renames a field, changes types), the frontend silently shows `null` values rather than detecting the mismatch.

**Where**: Both `normalizeStockCandidate()` and `normalizeOptionsCandidate()` — no version check

**Impact**: Breaking changes in the backend contract will produce silently degraded UI rather than a clear error. Since there's no CI contract test between frontend and backend, drift accumulates undetected.

**Recommendation**: Add a version assertion in both normalizers: if `raw.contract_version` doesn't match expected version, log a warning or show a "stale client" banner.

---

### F-3E-05 [MEDIUM] — Stock vs Options Output Envelope Asymmetry

**What**: Stock and options output.json use different top-level field sets:
- **Stock only**: `scanner_coverage`, `scanner_suite`, `filter_counts`, `market_picture_summary`, `model_analysis_counts`
- **Options only**: `scan_diagnostics`, `validation_summary`, `quality.credibility_filter`

The read models (`StockOpportunityReadModel` vs `OptionsOpportunityReadModel`) reflect this asymmetry, but there is no shared base envelope schema.

**Where**: `stock_opportunity_runner.py` L1549-1580 vs `options_opportunity_runner.py` L1141-1175

**Impact**: Any code that tries to handle "any workflow output" generically must special-case each workflow. Frontend already knows which API to call, but a future unified dashboard would need per-workflow parsing. Not a bug, but a maintenance consideration.

**Recommendation**: Define a shared envelope base (version, run_id, workflow_id, generated_at, batch_status, publication, candidates, quality) and allow pipeline-specific extensions. This would simplify the read model layer.

---

### F-3E-06 [MEDIUM] — Calendar Backend Fields Not Consumed by Frontend

**What**: The options compact output includes `expiration_back` and `dte_back` fields specifically for calendar/diagonal spreads. The frontend normalizer does not read these fields, so calendar candidates display without back-leg expiration or DTE information.

**Where**: Backend: `_extract_compact_candidate()` includes `expiration_back`, `dte_back`. Frontend: `normalizeOptionsCandidate()` does not read them.

**Impact**: Calendar spread cards show only the front expiration, missing the defining characteristic of the strategy (the time spread between expirations).

**Recommendation**: Add `expirationBack` and `dteBack` to the options normalizer and display them in calendar/diagonal cards.

---

### F-3E-07 [LOW] — Options Compact Output Has 14 Fields Frontend Ignores

**What**: The options compact candidate includes diagnostic/validation fields (`structural_validation`, `math_validation`, `hygiene`, `diagnostics_summary`, `passed`, `downstream_usable`, `contract_version`, `scanner_version`, `generated_at`, `candidate_id`, `scanner_key`, `leg_count`, `math.kelly`, `market_state_ref`) that the frontend normalizer never reads. These add ~40% payload size per candidate.

**Where**: `_extract_compact_candidate()` L264-380 vs `normalizeOptionsCandidate()` L280-318

**Impact**: Low — extra data doesn't hurt, and it's useful for debugging/API consumers. But it inflates the response size unnecessarily if the primary consumer is the frontend.

---

### F-3E-08 [LOW] — Stock Model Fields Have No Corresponding Options Equivalent

**What**: Stock candidates include 7 model analysis fields (`model_recommendation`, `model_confidence`, `model_score`, `model_review_summary`, `model_key_factors`, `model_caution_notes`, `model_technical_analysis`). Options candidates have no equivalent fields. This is a known architectural gap (options pipeline has no model analysis stage), but it means the two pipelines produce fundamentally different candidate shapes.

**Where**: Stock compact extraction L365-395 vs Options compact extraction (no model fields)

**Impact**: Low for current usage (separate normalizers handle each pipeline), but prevents a unified "all opportunities" view that could compare stock and options candidates side-by-side.

---

### F-3E-09 [LOW] — Engine Contract Normalizes News Differently

**What**: The News & Sentiment engine uses `internal_engine` instead of `engine_result`, and `regime_label` instead of `label`. The normalizer (`_normalize_news()`) handles this with a separate code path, but the structural inconsistency means any new engine consumer must know about this special case.

**Where**: `engine_output_contract.py` `_normalize_news()` vs `_normalize_pillar_engine()`

**Impact**: Low — the normalizer abstracts this away. Downstream consumers see the same 25-field shape regardless.

---

## Summary

| Severity | Count | Findings |
|----------|-------|----------|
| **HIGH** | 1 | F-3E-01 (calendar math mostly null) |
| **MEDIUM** | 5 | F-3E-02 (28 vs 27 doc drift), F-3E-03 (underlying/symbol order), F-3E-04 (no schema validation), F-3E-05 (envelope asymmetry), F-3E-06 (calendar fields not consumed) |
| **LOW** | 3 | F-3E-07 (14 unused fields), F-3E-08 (stock/options shape gap), F-3E-09 (news special case) |
| **Total** | **9** | |

### Key Architectural Observations

1. **Contract design is strong**: The scanner candidate contract (28 fields), V2Candidate (29 fields), and engine output contract (25 fields) are well-defined with explicit field lists, type annotations, and documented monetary conventions. This is genuinely good engineering.

2. **Frontend-backend alignment is good for stock, acceptable for options**: Stock normalizer maps 26/29 backend fields with zero mismatches. Options normalizer maps 21/35 backend fields with one minor ordering issue (`underlying` vs `symbol`) and ignores 14 diagnostic fields.

3. **Calendar spreads are the weak link**: They reach the output but are fundamentally incomplete — most math fields are null, defining fields (`expiration_back`, `dte_back`) are not consumed by the frontend, and they can't be meaningfully compared to other strategy families.

4. **No breaking-change detection**: Both frontend normalizers silently degrade when fields are missing (null-coalescing). The `contract_version` field is present but unchecked. There's no CI test that verifies frontend expectations against backend output schemas.
