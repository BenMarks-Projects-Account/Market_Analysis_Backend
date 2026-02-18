# Backend Refactor Plan — Canonical Trade Data Flow

Created: 2026-02-17 | Branch: `chore/app-cleanup-phase0`

## Problem Statement

Trade data currently flows through **5+ normalization functions**, **3 alias maps**, and **2 parallel generation pipelines** that produce subtly different shapes. The same metric can appear under 4–6 different field names depending on which code path produced it. Per-share and per-contract values coexist ambiguously. The frontend and recommendation service must navigate fallback chains of 3–6 field names to find a single value.

### Symptoms

| Symptom | Root cause |
|---------|-----------|
| `_normalize_trade()` and `_normalize_report_trade()` are near-duplicate implementations (~450 lines total) | Two parallel pipelines, not shared |
| 6 field names for probability of profit (`pop`, `p_win_used`, `pop_delta_approx`, `pop_approx`, `pop_butterfly`, `implied_prob_profit`) | Each strategy plugin invented its own name |
| 4 field names for expected value (`expected_value`, `ev_per_contract`, `ev_per_share`, `ev`) at different scales | No single source of truth for scale |
| `max_profit` means per-contract in plugins but per-share in `CreditSpread.summary()` | Ambiguous scale convention |
| Triple-redundant symbol fields (`underlying`, `underlying_symbol`, `symbol`) | Defensive coding against inconsistency |
| Triple-redundant strategy fields (`spread_type`, `strategy`, `strategy_id`) | Same |
| Three separate alias maps (`trade_key._SPREAD_TYPE_ALIASES`, `quant_analysis._CREDIT_SPREAD_TYPE_MAP`, `data_workbench._WORKBENCH_TYPE_ALIASES`) that map to different canonical forms | No single alias registry |
| Per-share → per-contract scaling happens in 3 independent places (`enrich_trade`, `_normalize_trade`, `build_computed_metrics`) | Risk of double-scaling |
| Legacy `report_service` persists flat/raw shape; `strategy_service` persists normalized shape | Two on-disk formats for the same concept |

---

## Canonical Data Contracts

### Three-tier contract model

```
RawTradeCandidate  →  NormalizedTrade  →  UITradeDTO
   (plugin output)     (service output)    (API response)
```

Each tier has a single, well-defined shape. Downstream code only reads the tier it needs. No tier stores both per-share and per-contract values simultaneously.

---

### Tier 1: `RawTradeCandidate`

**What:** The output of a strategy plugin's `build_candidates()` + `enrich()` + `score()` pipeline. This is the raw analytical data before any normalization, augmented with market context from `enrich_trade()`.

**Where built:** Each strategy plugin's `enrich()` method.

**Convention:** All monetary values are **per-contract** (×100 for standard options). Plugins must multiply by `contractsMultiplier` before emitting.

**Required fields (enforced by base class validation):**

```python
class RawTradeCandidate(TypedDict, total=False):
    # --- Identity (required) ---
    underlying: str               # Ticker, upper-cased
    expiration: str               # ISO date
    strategy_id: str              # Canonical strategy ID (from CANONICAL_STRATEGY_IDS)
    spread_type: str              # Same as strategy_id (single source of truth)

    # --- Structure (required) ---
    short_strike: float | None    # Short leg strike price
    long_strike: float | None     # Long leg strike price (None for single-leg)
    dte: int                      # Days to expiration

    # --- Economics (per-contract, required where computable) ---
    max_profit: float | None      # Per-contract max profit ($)
    max_loss: float | None        # Per-contract max loss ($, positive = loss)
    net_credit: float | None      # Per-contract credit received ($)
    net_debit: float | None       # Per-contract debit paid ($)
    break_even: float | None      # Single break-even price (or lower for multi-leg)
    break_even_high: float | None # Upper break-even (multi-leg strategies only)
    underlying_price: float       # Spot price at time of scan

    # --- Probability (required where computable) ---
    pop: float | None             # Probability of profit [0, 1]

    # --- Greeks / Volatility context ---
    iv: float | None              # Implied vol (annualized decimal, e.g. 0.25)
    rv_20d: float | None          # 20-day realized vol (annualized decimal)
    iv_rv_ratio: float | None
    short_delta_abs: float | None
    expected_move: float | None   # 1-sigma expected move ($)

    # --- Derived analytics (per-contract) ---
    expected_value: float | None  # Per-contract EV ($)
    return_on_risk: float | None  # max_profit / max_loss [0, ∞)
    kelly_fraction: float | None
    trade_quality_score: float | None

    # --- Market context ---
    rsi14: float | None
    iv_rank: float | None
    market_regime: str | None     # "bullish_low_vol", etc.

    # --- Liquidity ---
    open_interest: int | None
    volume: int | None
    bid_ask_pct: float | None     # (ask-bid)/mid [0, ∞)

    # --- Scoring ---
    rank_score: float | None
    composite_score: float | None

    # --- Strategy-specific extensions (optional, passed through) ---
    # Iron condor: put_short_strike, put_long_strike, call_short_strike, call_long_strike,
    #              break_even_low, break_even_high, symmetry_score, theta_capture, etc.
    # Butterflies: center_strike, wing_width, butterfly_type, peak_profit_at_center, etc.
    # Calendars: expiration_near, expiration_far, dte_near, dte_far, theta_structure, etc.
    # Income: collateral_per_contract, annualized_yield_on_collateral, assignment_risk_score, etc.
    # Debit spreads: debit_as_pct_of_width, conviction_score, etc.

    contractsMultiplier: int      # Always present (default 100)
    selection_reasons: list[str]  # Why this candidate was selected
```

**Key changes from current state:**

| Current | New | Rationale |
|---------|-----|-----------|
| `p_win_used`, `pop_delta_approx`, `pop_approx`, `pop_butterfly`, `implied_prob_profit` | `pop` only | Single canonical name |
| `ev_per_share`, `ev_per_contract`, `ev`, `expected_value` | `expected_value` only (per-contract) | Single name, single scale |
| `max_profit_per_share`, `max_profit_per_contract`, `max_profit` | `max_profit` only (per-contract) | Single name, single scale |
| `max_loss_per_share`, `max_loss_per_contract`, `max_loss` | `max_loss` only (per-contract) | Single name, single scale |
| `bid_ask_spread_pct` | `bid_ask_pct` | Shorter canonical name |
| `strike_distance_pct`, `strike_distance_vs_expected_move`, `expected_move_ratio` | `strike_dist_pct` | Single canonical name |
| `realized_vol`, `rv_20d`, `realized_vol_20d` | `rv_20d` | Single canonical name |
| `iv`, `implied_vol` | `iv` | Single canonical name |
| `underlying`, `underlying_symbol`, `symbol` | `underlying` only | Single identity field |
| `spread_type`, `strategy`, `strategy_id` | `strategy_id` only | Single canonical strategy name; `spread_type` is a legacy alias |
| `price`, `underlying_price` | `underlying_price` only | Unambiguous |

---

### Tier 2: `NormalizedTrade`

**What:** A fully normalized trade with computed sub-objects, ready for persistence and consumption by recommendation service. This is the single shape that gets written to disk and read back.

**Where built:** A single shared function `normalize_trade()` in a new module `app/utils/normalize.py`. Called from `strategy_service.generate()` after plugin pipeline, and from any report-reading code path.

**Shape:**

```python
class NormalizedTrade(TypedDict, total=False):
    # --- Identity (all from RawTradeCandidate, validated + canonicalized) ---
    underlying: str               # Upper-cased
    strategy_id: str              # Canonical (from CANONICAL_STRATEGY_IDS)
    expiration: str
    trade_key: str                # Canonical trade key (SYMBOL|exp|strategy|strikes|dte)

    # --- All RawTradeCandidate fields pass through ---
    # (short_strike, long_strike, dte, max_profit, max_loss, pop, etc.)

    # --- Computed sub-objects (always present, may contain None values) ---
    computed: ComputedMetrics     # 14 canonical numeric fields (per-contract scale)
    details: TradeDetails         # 6 context/display fields
    pills: TradePills            # UI pill data
    computed_metrics: dict        # Legacy-compatible 22-field flat metrics dict
    metrics_status: MetricsStatus # { ready: bool, missing_fields: list[str] }

    # --- Validation ---
    validation_warnings: list[str]  # Warning codes for missing/estimated values

    # --- Scoring ---
    rank_score: float | None
    rank_components: dict | None    # { structure, underlying, regime_fit }

    # --- Strategy-specific extensions: passed through from RawTradeCandidate ---
```

**Sub-object shapes:**

```python
class ComputedMetrics(TypedDict):
    """Exactly 14 per-contract numeric fields. null = not computable for this strategy."""
    max_profit: float | None
    max_loss: float | None
    pop: float | None
    expected_value: float | None
    return_on_risk: float | None
    kelly_fraction: float | None
    break_even: float | None
    dte: int | None
    expected_move: float | None
    iv_rank: float | None
    iv_rv_ratio: float | None
    trade_quality_score: float | None
    bid_ask_pct: float | None
    strike_dist_pct: float | None

class TradeDetails(TypedDict):
    """Context fields that aren't pure metrics."""
    break_even: float | None            # Same as computed.break_even (convenience)
    break_even_high: float | None       # Upper break-even for multi-leg
    expected_move: float | None
    market_regime: str | None
    rv_20d: float | None
    iv_rv_ratio: float | None

class TradePills(TypedDict):
    """Pre-formatted UI pill data."""
    strategy_label: str
    dte: str | None
    pop: str | None
    oi: str | None
    vol: str | None
    regime_label: str | None
    # Calendar-specific:
    dte_front: str | None
    dte_back: str | None

class MetricsStatus(TypedDict):
    ready: bool
    missing_fields: list[str]
```

---

### Tier 3: `UITradeDTO`

**What:** The JSON shape returned by API endpoints. Identical to `NormalizedTrade` — no further transformation. The API layer does NOT transform trades; it passes `NormalizedTrade` dicts through directly.

**Where built:** No new code needed. The API route returns `NormalizedTrade` from the service layer verbatim.

**Why make this explicit:** Today, `routes_reports.py` has its own `_normalize_report_trade()` that transforms at the API layer. This should be eliminated — normalization should happen once, at the service layer.

---

### Compatibility layer for per-share fields

For any code that still needs `_per_share` values (currently: `CreditSpread.summary()`, `TradeContract` model, `enrich_trade()`), introduce a thin adapter:

```python
# app/utils/compat.py
def per_share_to_per_contract(trade: dict, multiplier: int = 100) -> dict:
    """One-way conversion. Called once at plugin boundary, never downstream."""
    for field in ("max_profit", "max_loss", "expected_value"):
        ps_key = f"{field}_per_share"
        if trade.get(field) is None and trade.get(ps_key) is not None:
            trade[field] = trade[ps_key] * multiplier
    return trade
```

This replaces the current 3 independent scaling points with a single function called once per trade at the plugin → `RawTradeCandidate` boundary.

---

## Single Alias Registry

Replace the three current alias maps with one:

```python
# app/utils/trade_key.py (extend existing)
STRATEGY_ALIASES: dict[str, str] = {
    # --- Credit spreads ---
    "put_credit_spread": "put_credit_spread",
    "put_credit": "put_credit_spread",
    "credit_put_spread": "put_credit_spread",
    "call_credit_spread": "call_credit_spread",
    "call_credit": "call_credit_spread",
    "credit_call_spread": "call_credit_spread",
    # --- Debit spreads ---
    "debit_call_spread": "debit_call_spread",
    "call_debit": "debit_call_spread",
    "debit_put_spread": "debit_put_spread",
    "put_debit": "debit_put_spread",
    # --- Iron condor ---
    "iron_condor": "iron_condor",
    # --- Butterflies ---
    "butterfly_debit": "butterfly_debit",
    "debit_call_butterfly": "butterfly_debit",
    "debit_put_butterfly": "butterfly_debit",
    "butterflies": "butterfly_debit",
    # --- Iron butterfly ---
    "iron_butterfly": "iron_butterfly",
    # --- Calendars ---
    "calendar_spread": "calendar_spread",
    "calendar_call_spread": "calendar_call_spread",
    "calendar_put_spread": "calendar_put_spread",
    # --- Income ---
    "income": "income",
    "cash_secured_put": "cash_secured_put",
    "csp": "cash_secured_put",
    "covered_call": "covered_call",
    # --- Single leg ---
    "single": "single",
    "long_call": "long_call",
    "long_put": "long_put",
}
```

Delete:
- `quant_analysis._CREDIT_SPREAD_TYPE_MAP` → import from `trade_key`
- `data_workbench_service._WORKBENCH_TYPE_ALIASES` → import from `trade_key`
- `data_workbench_service._WORKBENCH_TYPE_VARIANTS` → derive from the single map

The existing `canonicalize_strategy_id()` in `trade_key.py` already uses `_SPREAD_TYPE_ALIASES` — just expand it to cover all aliases.

---

## Current vs. Target Data Flow

### Current flow (multiple paths, divergent shapes)

```
Plugin.build_candidates()           Plugin.build_candidates()
        │                                   │
Plugin.enrich()                     Plugin.enrich()
        │                                   │
enrich_trade() [quant_analysis]     enrich_trade() [quant_analysis]
  ┌─────┤                                   │
  │   (per-share + per-contract mixed)       │
  │     │                                    │
  │  _normalize_trade() [strategy_svc]   _build_candidates() [report_svc]
  │     │                                    │
  │  apply_metrics_contract()            CreditSpread.summary() merge
  │     │                                    │
  │  Persist (normalized, w/ computed)   evaluate_trade_contract()
  │     │                                    │
  │  Re-normalize on read                Inline normalization
  │     │                                    │
  │  API response                        Persist (flat, no computed)
  │                                          │
  │                              _normalize_report_trade() [routes_reports]
  │                                          │
  │                                      API response
  │
  └──→ RecommendationService._build_pick()
         (reads computed + raw fallbacks)
```

### Target flow (single path)

```
Plugin.build_candidates()
        │
Plugin.enrich()
        │
per_share_to_per_contract()  ← single scaling point
        │
  RawTradeCandidate (validated)
        │
normalize_trade()  ← ONE shared function
        │
  NormalizedTrade (with computed/details/pills/metrics_status)
        │
 ┌──────┼──────────────┐
 │      │              │
Persist  SSE stream   API response
 (JSON)  (events)     (= NormalizedTrade)
 │
 │  Read from disk → already normalized
 │      │
 │  API response (= NormalizedTrade, no re-normalization needed)
 │
 └──→ RecommendationService._build_pick()
        (reads from computed sub-object only, no fallback chains)
```

---

## Paths to Collapse

### 1. Merge `_normalize_trade()` and `_normalize_report_trade()`

| What | Where | Action |
|------|-------|--------|
| `StrategyService._normalize_trade()` | `strategy_service.py` L460–672 | **Extract** to `app/utils/normalize.py` as `normalize_trade()` |
| `_normalize_report_trade()` | `routes_reports.py` L130–285 | **Delete.** Import `normalize_trade()` from utils |
| `DataWorkbenchService._normalize_trade_payload()` | `data_workbench_service.py` L125–171 | **Replace** with call to `normalize_trade()` |
| `ReportService` inline normalization | `report_service.py` L991–1065 | **Replace** with call to `normalize_trade()` |

### 2. Consolidate per-share → per-contract scaling

| What | Where | Action |
|------|-------|--------|
| `enrich_trade()` per-contract promotion | `quant_analysis.py` L738–748 | **Remove** — let plugins do it, or call `per_share_to_per_contract()` once |
| `_normalize_trade()` scaling in computed-build | `strategy_service.py` L530–560 | **Remove** — already scaled at plugin boundary |
| `build_computed_metrics()` fallback scaling | `computed_metrics.py` L68–85 | **Keep** as safety net but should never trigger (add warning log if it does) |

### 3. Unify strategy naming at plugin source

| Plugin | Current `strategy` value | Current `spread_type` value | Canonical `strategy_id` | Action |
|--------|-------------------------|----------------------------|------------------------|--------|
| credit_spread | `"put_credit_spread"` | `"put_credit_spread"` | `put_credit_spread` | Already canonical ✅ |
| debit_spreads | `"debit_call_spread"` / `"debit_put_spread"` | Same | `debit_call_spread` / `debit_put_spread` | Already canonical ✅ |
| iron_condor | `"iron_condor"` | `"iron_condor"` | `iron_condor` | Already canonical ✅ |
| butterflies | `"butterflies"` | `"iron_butterfly"` (only for iron variant) | `butterfly_debit` / `iron_butterfly` | Fix: emit `strategy_id` = `butterfly_debit` or `iron_butterfly` |
| calendars | `"calendar_spread"` | Not set directly | `calendar_spread` / `calendar_call_spread` / `calendar_put_spread` | Fix: emit specific `strategy_id` per spread_type |
| income | `"income"` | `"cash_secured_put"` / `"covered_call"` | `cash_secured_put` / `covered_call` | Fix: emit `strategy_id` = specific type |

### 4. Eliminate redundant field aliases in plugins

Each plugin should emit the **canonical field name** directly:

| Current field names (vary by plugin) | Canonical name | Plugins that need update |
|--------------------------------------|----------------|------------------------|
| `p_win_used`, `pop_delta_approx`, `pop_approx`, `pop_butterfly`, `implied_prob_profit` | `pop` | All 6 plugins + `enrich_trade()` |
| `ev_per_share`, `ev_per_contract`, `ev`, `expected_value` | `expected_value` | All 6 plugins + `enrich_trade()` |
| `max_profit_per_share`, `max_profit_per_contract` | `max_profit` | `enrich_trade()`, `CreditSpread.summary()` |
| `max_loss_per_share`, `max_loss_per_contract` | `max_loss` | `enrich_trade()`, `CreditSpread.summary()` |
| `bid_ask_spread_pct` | `bid_ask_pct` | `enrich_trade()`, all plugins |
| `realized_vol_20d`, `realized_vol` | `rv_20d` | `enrich_trade()`, `classify_market_regime()` |
| `implied_vol` | `iv` | `enrich_trade()` |
| `underlying_symbol`, `symbol` (alongside `underlying`) | Remove — keep `underlying` only | All plugins |
| `strategy`, `spread_type` (alongside `strategy_id`) | Remove — keep `strategy_id` only | All plugins |
| `price` (alongside `underlying_price`) | Remove — keep `underlying_price` only | credit_spread, debit_spreads |
| `total_credit` (alongside `net_credit`) | Remove — keep `net_credit` only | iron_condor |
| `break_evens_low`/`break_evens_high` (alongside `break_even_low`/`break_even_high`) | Remove duplicates | iron_condor, butterflies, calendars |

---

## Caching / File Persistence Boundaries

### Current state

| Boundary | Location | Shape persisted | Shape served |
|----------|----------|----------------|-------------|
| Strategy report files | `results/{id}_analysis_{ts}.json` | Normalized (post-`_normalize_trade`) | Re-normalized on every read |
| Legacy report files | `results/analysis_{ts}.json` | Raw/flat (no computed sub-objects) | Normalized via `_normalize_report_trade()` on read |
| Workbench JSONL | `results/data_workbench_records.jsonl` | Normalized trades in workbench envelope | Read as-is |
| Client caches | In-memory TTL per client | Raw API responses (quotes, chains) | Consumed by `BaseDataService` |
| Regime cache | In-memory TTL (45s) | Computed regime payload | Served to routes |
| Signal cache | In-memory TTL (45s) | Computed signal composites | Consumed by services |

### Target state

| Boundary | Change |
|----------|--------|
| Strategy report files | Persist `NormalizedTrade` shape. **Stop re-normalizing on read** — files are already the canonical shape. Only re-normalize if file format version is older (add `format_version` to report blob). |
| Legacy report files | Handled by migration (Step 2 below). Files produced after migration will be `NormalizedTrade` shape. |
| Workbench JSONL | No change; already stores normalized shape. |
| Client / regime / signal caches | No change; these are upstream of trade normalization. |

### File format versioning

Add a `format_version` field to the report blob:

```json
{
  "format_version": 2,
  "strategyId": "credit_spread",
  "generated_at": "...",
  "trades": [ /* NormalizedTrade[] */ ]
}
```

- `format_version: 1` (or absent) = legacy shape → run `normalize_trade()` on read
- `format_version: 2` = canonical shape → pass through directly

---

## Migration Plan

### Step 1: Extract `normalize_trade()` to shared module

**Scope:** Create `app/utils/normalize.py` by extracting the logic from `strategy_service._normalize_trade()`. Keep the original as a thin wrapper calling the shared function during transition.

**Files changed:**
- Create `app/utils/normalize.py`
- `app/services/strategy_service.py` — `_normalize_trade()` delegates to shared function
- `app/api/routes_reports.py` — `_normalize_report_trade()` delegates to shared function
- `app/services/data_workbench_service.py` — `_normalize_trade_payload()` delegates to shared function

**Tests:**
- Existing tests must still pass (no behavior change)
- Add unit tests for `normalize_trade()` with inputs from each strategy type

**API impact:** None — output shapes are unchanged.

---

### Step 2: Canonicalize plugin output field names

**Scope:** Update each strategy plugin's `enrich()` to emit canonical field names and per-contract values directly. Update `enrich_trade()` in `quant_analysis.py` to emit canonical names. Add `per_share_to_per_contract()` at the `CreditSpread.summary()` boundary.

**Files changed:**
- `app/services/strategies/credit_spread.py`
- `app/services/strategies/debit_spreads.py`
- `app/services/strategies/iron_condor.py`
- `app/services/strategies/butterflies.py`
- `app/services/strategies/calendars.py`
- `app/services/strategies/income.py`
- `common/quant_analysis.py` — `enrich_trade()`, `CreditSpread.summary()`
- Create `app/utils/compat.py` — `per_share_to_per_contract()`

**Backward compatibility:** `normalize_trade()` still runs its fallback chains, so old report files on disk still work. New reports use canonical names. The fallback chains become dead code over time.

**Tests:**
- Update plugin-level tests to assert canonical field names
- Regression: existing end-to-end tests must pass

**API impact:** None — `normalize_trade()` still produces the same output shape. The fallback chains just stop firing.

---

### Step 3: Consolidate alias maps and strategy naming

**Scope:** Expand `trade_key._SPREAD_TYPE_ALIASES` to be the single alias registry. Delete `quant_analysis._CREDIT_SPREAD_TYPE_MAP` and `data_workbench._WORKBENCH_TYPE_ALIASES`. Fix plugins that emit non-canonical `strategy` values (butterflies → `butterfly_debit`, income → `cash_secured_put`/`covered_call`).

**Files changed:**
- `app/utils/trade_key.py` — expand alias map
- `common/quant_analysis.py` — delete `_CREDIT_SPREAD_TYPE_MAP`, import `canonicalize_strategy_id` from `trade_key`
- `app/services/data_workbench_service.py` — delete `_WORKBENCH_TYPE_ALIASES`, import from `trade_key`
- `app/services/strategies/butterflies.py` — emit `strategy_id` not `strategy: "butterflies"`
- `app/services/strategies/income.py` — emit `strategy_id` = `cash_secured_put`/`covered_call` not `"income"`
- `app/services/strategies/calendars.py` — emit specific `strategy_id` per variant

**Tests:**
- Add tests that each plugin emits a value in `CANONICAL_STRATEGY_IDS`
- Regression: existing normalization tests pass

**API impact:** Field values change (e.g., `"butterflies"` → `"butterfly_debit"` in `strategy_id`). The frontend already uses `strategy_id` for routing/display and maps via `strategies/defaults.js`. Update `defaults.js` to recognize new canonical IDs. Old report files on disk still work via alias map.

---

### Step 4: Remove redundant fields and collapse symbol/strategy triples

**Scope:** Plugins emit only `underlying` (not `underlying_symbol`, `symbol`). Plugins emit only `strategy_id` (not `strategy`, `spread_type`). `normalize_trade()` still writes the triple for backward compatibility during transition, but marks them as deprecated.

**Files changed:**
- All 6 strategy plugins — remove duplicate field emissions
- `common/quant_analysis.py` — `enrich_trade()` removes duplicate field writes
- `app/utils/normalize.py` — add `# DEPRECATED compat: remove in v3` comments on triple-writes
- `app/services/recommendation_service.py` — read from `strategy_id` not `strategy`
- `app/api/routes_reports.py` — read from `underlying` not `underlying_symbol`

**Tests:**
- Assert plugins emit exactly 1 identity field per concept
- Regression: end-to-end tests pass

**API impact:** API responses still include triples (via `normalize_trade()` compat). Can be removed in a future version.

---

### Step 5: Stop re-normalizing on read + retire legacy report_service

**Scope:** 
1. Add `format_version: 2` to report blobs written by `strategy_service.generate()`.
2. In `strategy_service.get_report()`, skip `normalize_trade()` if `format_version >= 2`.
3. Retire `report_service.py` (move the `POST /api/model/analyze` and `/api/model/analyze_stock` endpoints from `routes_reports.py` to a new `routes_model.py`, then delete `report_service.py`, `evaluation/`, and the legacy `GET /api/generate` SSE endpoint).
4. Remove the `_normalize_report_trade()` function from `routes_reports.py` (now dead).
5. Remove the fallback chains from `normalize_trade()` (all inputs now use canonical names).

**Files changed:**
- `app/services/strategy_service.py` — add `format_version`, skip normalization on read
- `app/services/report_service.py` — **DELETE** (entire file, ~1,132 lines)
- `app/services/evaluation/` — **DELETE** (4 files, ~300 lines)
- `app/api/routes_reports.py` — remove legacy SSE endpoint, remove `_normalize_report_trade()`, extract model endpoints
- Create `app/api/routes_model.py` — model analysis endpoints
- `app/main.py` — remove `report_service` wiring, add `routes_model`
- `app/utils/normalize.py` — remove fallback chains (simplify)
- `app/utils/computed_metrics.py` — remove fallback chains (simplify)

**Tests:**
- Remove/update tests that depend on `report_service`
- Add tests for `routes_model.py`
- Regression: all remaining tests pass

**API impact:**
- `GET /api/generate` — **REMOVED** (SSE legacy endpoint). Document in changelog.
- `GET /api/reports`, `GET /api/reports/{file}` — **REMOVED** (use `GET /api/strategies/{id}/reports` instead). Document in changelog.
- `POST /api/model/analyze`, `POST /api/model/analyze_stock` — **MOVED** to `/api/model/analyze`. No shape change.

---

## Risk Assessment

| Step | Risk | Mitigation |
|------|------|-----------|
| 1 | Low — pure extract refactor, no behavior change | Existing tests validate |
| 2 | Medium — plugins change output field names | `normalize_trade()` fallback chains catch any missed renames; old files on disk still work |
| 3 | Medium — strategy IDs change for butterflies/income/calendars | Frontend `defaults.js` must be updated; old report files handled by alias map |
| 4 | Low — removing duplicate fields that nobody should be reading directly | Triple-write compat layer in `normalize_trade()` ensures API stability |
| 5 | High — deleting ~1,430 lines of legacy code + removing 3 API endpoints | Must verify no frontend code calls `GET /api/generate` or `GET /api/reports`. Must migrate model endpoints first. Run full smoke test. |

---

## Out of Scope (Future Work)

- **Pydantic models for trade contracts:** Currently all trade data flows as raw dicts. Adding Pydantic models for `RawTradeCandidate` and `NormalizedTrade` would provide runtime validation but is a larger refactor. Consider after the field canonicalization is stable.
- **Frontend normalization cleanup:** The frontend has its own `normalizeOpportunity()`, `normalizeTradeIdea()`, `computeRor()` with similar fallback chains. Once the backend emits canonical shapes consistently, these can be simplified.
- **TradeContract dataclass retirement:** `app/models/trade_contract.py` uses per-share fields and is only consumed by `report_service.py`. It will be deleted with Step 5.
- **CreditSpread dataclass refactor:** `common/quant_analysis.CreditSpread` uses per-share convention internally. After Step 2, its `.summary()` output goes through `per_share_to_per_contract()` at the boundary. The internal per-share math is fine — only the output convention matters.
