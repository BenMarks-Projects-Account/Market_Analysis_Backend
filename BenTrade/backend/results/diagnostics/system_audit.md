# BenTrade Options Pipeline — Comprehensive Diagnostic Audit

**Generated:** 2025-07-25  
**Scope:** Ranking system, candidate flow, active trades, P&L sign convention, portfolio balance, model router  
**Mode:** Read-only diagnostic — no fixes applied

---

## Section 1: Ranking System

### 1A — Functions, Dicts, and Implementations in `ranking.py`

**File:** `app/services/ranking.py` (575 lines)

#### All Exported Functions

| Function | Line | Purpose |
|----------|------|---------|
| `clamp(x, lo, hi)` | 11 | Clamp a float to [lo, hi] |
| `safe_float(x, default)` | 15 | Safe float conversion with default |
| `minmax_norm(x, lo, hi)` | 23 | Min-max normalize to [0, 1] |
| `_get_pop(trade)` | 32 | Extract POP from `p_win_used` or `pop_delta_approx` |
| `_get_ev_to_risk(trade)` | 38 | Extract EV/risk from `ev_to_risk` field or derive from `ev/max_loss` |
| `compute_liquidity_score(trade)` | 54 | 0-1 liquidity from OI (45%) + volume (35%) + spread penalty (20%) |
| `_compute_rank_components(trade)` | 66 | Build edge/ror/pop/liquidity/tqs component dict |
| `compute_rank_score(trade)` | 82 | Legacy 0–100 weighted score: edge=0.30, ror=0.22, pop=0.20, liquidity=0.18, tqs=0.10 |
| `_trade_tie_break_tuple(trade)` | 119 | 7-element tuple for deterministic tie-breaking |
| `compare_trades_for_rank(a, b)` | 133 | Comparator using rank_score then tie-break tuple |
| `sort_trades_by_rank(trades)` | 146 | Sort using `compute_rank_score` (legacy path) |
| `classify_strategy(scanner_key)` | 172 | Classify into `income` / `directional` / `calendar` / `unknown` |
| `_norm_v2(value, low, high)` | 214 | Normalize value to 0-100 between bounds |
| `_compute_edge_score(m, legs, strategy_class, profile)` | 222 | Edge scoring — income: 60% credit-to-width + 40% EV/risk; directional: EV/risk only |
| `_compute_structure_score(candidate, m, legs, strategy_class, profile)` | 260 | DTE + delta/breakeven + width reasonableness |
| `_compute_market_fit(candidate, strategy_class, regime_label)` | 325 | Regime fit + alignment + event risk adjustments |
| `_compute_execution_quality(legs)` | 352 | Per-leg spread tightness + volume + OI |
| `score_candidate(candidate, regime_label)` | 375 | Full 5-component scoring → composite_score |
| `rank_candidates(candidates, regime_label)` | 425 | Score all candidates, sort descending, assign rank |

#### Key Dicts

| Dict | Contents |
|------|----------|
| `INCOME_STRATEGIES` | `put_credit_spread`, `call_credit_spread`, `iron_condor`, `iron_butterfly` |
| `DIRECTIONAL_STRATEGIES` | `put_debit`, `call_debit`, `butterfly_debit` |
| `CALENDAR_STRATEGIES` | `calendar_call_spread`, `calendar_put_spread`, `diagonal_call_spread`, `diagonal_put_spread` |
| `SCORING_PROFILES` | Per-class parameter dicts (income, directional, calendar, unknown) |
| `_REGIME_FIT` | (strategy_class, regime) → base score matrix |

#### `SCORING_PROFILES` Current Values

| Parameter | Income | Directional | Calendar | Unknown |
|-----------|--------|-------------|----------|---------|
| `pop_range` | (0.45, 0.85) | (0.30, 0.60) | (0.30, 0.60) | (0.30, 0.60) |
| `pop_weight` | 0.30 | 0.20 | 0.20 | 0.20 |
| `credit_to_width_range` | (0.05, 0.30) | None | None | None |
| `ev_to_risk_range` | (0.02, 0.15) | (0.20, 1.00) | (0.05, 0.40) | (0.20, 1.00) |
| `edge_weight` | 0.20 | 0.25 | 0.25 | 0.25 |
| `short_delta_range` | (0.08, 0.35) | — | — | — |
| `short_delta_ideal` | (0.15, 0.30) | — | — | — |
| `dte_ideal` | (21, 45) | (14, 60) | (21, 60) | (14, 60) |
| `structure_weight` | 0.15 | 0.20 | 0.20 | 0.20 |
| `market_fit_weight` | 0.20 | 0.20 | 0.20 | 0.20 |
| `execution_weight` | 0.15 | 0.15 | 0.15 | 0.15 |

**Weight sums:** Income = 1.00 ✓, Directional = 1.00 ✓, Calendar = 1.00 ✓

#### `_REGIME_FIT` Matrix

| Strategy Class | NEUTRAL | RISK_ON | RISK_OFF |
|----------------|---------|---------|----------|
| income | 90 | 70 | 65 |
| directional | 40 | 85 | 80 |
| calendar | 85 | 65 | 55 |

**Design intent:** Income dominates NEUTRAL; directional dominates trending; calendar is a NEUTRAL complement.

### 1B — Imports and Call Sites in `options_opportunity_runner.py`

**Import (line 58):**
```python
from app.services.ranking import compute_rank_score, rank_candidates
```

**Call sites:**

| Function | Line | Context |
|----------|------|---------|
| `compute_rank_score(rank_dict)` | ~395 | Called inside `_compute_candidate_rank()` helper — bridges compact candidate shape to flat dict for legacy scoring path |
| `rank_candidates(credible, regime_label=regime_label)` | ~1326 | Called in `_stage_enrich_evaluate()` — scores and sorts all credible candidates using strategy-aware v2 system |

**Note:** `_compute_candidate_rank()` uses the **legacy** `compute_rank_score()` path (flat dict), while `_stage_enrich_evaluate()` uses the **v2** `rank_candidates()` path (strategy-aware). Both coexist — the v2 path is the authoritative ranking used for final output.

### 1C — Mock Scoring Results

#### classify_strategy verification

| scanner_key | classification | correct? |
|-------------|---------------|----------|
| `put_credit_spread` | income | ✅ |
| `call_credit_spread` | income | ✅ |
| `iron_condor` | income | ✅ |
| `iron_butterfly` | income | ✅ |
| `put_debit` | directional | ✅ |
| `call_debit` | directional | ✅ |
| `butterfly_debit` | directional | ✅ |
| `calendar_call_spread` | calendar | ✅ |
| `diagonal_put_spread` | calendar | ✅ |
| `unknown_key` | unknown | ✅ |

#### Mock Candidate Scoring (PCS vs put_debit)

**Test candidates:**
- Income (PCS): POP=72%, credit/width=22.5%, DTE=30, short delta=0.20
- Directional (put_debit): POP=49%, expected_ror=55%, DTE=25

| Regime | Income Composite | Directional Composite | Income Wins? |
|--------|------------------|-----------------------|--------------|
| **NEUTRAL** | 72.19 | 63.87 | ✅ Yes (+8.32) |
| **RISK_ON** | 68.19 | 72.87 | ❌ No (−4.68) |
| **RISK_OFF** | 67.19 | 71.87 | ❌ No (−4.68) |

**Component-level breakdown (NEUTRAL):**

| Component | Income Score → Weight | Directional Score → Weight |
|-----------|-----------------------|---------------------------|
| probability | 67.5 × 0.30 | 63.3 × 0.20 |
| edge | 51.72 × 0.20 | 43.8 × 0.25 |
| structure | 93.3 × 0.15 | 100.0 × 0.20 |
| market_fit | 90.0 × 0.20 | 40.0 × 0.20 |
| execution | 64.0 × 0.15 | 81.7 × 0.15 |

**Key insight:** The 50-point gap in market_fit (90 vs 40) is the dominant factor giving income the advantage in NEUTRAL. In trending regimes, the gap inverts (70→85 for RISK_ON), and directional edge + structure weights overcome income.

---

## Section 2: Candidate Flow

### 2A — Ranking Audit File (`data/diagnostics/ranking_audit_latest.json`)

**File status:** EXISTS ✓  
**Run ID:** `run_20260327_221109_8f4f`  
**Regime:** NEUTRAL

#### Strategy Distribution

```
total_candidates_ranked: 30
by_strategy_class: { directional: 30 }
by_scanner_key:    { put_debit: 30 }
```

**⚠️ CRITICAL:** All 30 ranked candidates are `put_debit`. Zero income, zero calendar.

#### Scoring Validation

| Metric | Value |
|--------|-------|
| income_candidates_in_top_10 | 0 |
| directional_candidates_in_top_10 | 10 |
| calendar_candidates_in_top_10 | 0 |
| score_range_top_10 | 77.19 – 80.53 |
| score_range_all | 75.84 – 80.53 |
| highest_income_score | null |
| highest_directional_score | 80.53 |
| highest_income_candidate | null |

#### Red Flags Detected

1. **"Score compression: all scores within 4.7 points"** — normalization ranges may be too tight
2. **"ALL top 10 candidates got PASS from model"** — model threshold may be too strict or candidates genuinely weak

#### Top 3 Candidates

| Rank | Symbol | Scanner Key | Strategy Class | Composite |
|------|--------|-------------|----------------|-----------|
| 1 | NVDA | put_debit | directional | 80.53 |
| 2 | NVDA | put_debit | directional | 78.69 |
| 3 | AAPL | put_debit | directional | 78.40 |

### 2B — Latest Output File

**Path:** `data/workflows/options_opportunity/run_20260327_221109_8f4f/output.json`

| Metric | Value |
|--------|-------|
| Total candidates | 30 |
| put_debit count | 30 (100%) |
| Income strategies present | ❌ None |
| Calendar strategies present | ❌ None |

**Top 5 in output:**

| Rank | Symbol | Scanner Key | Composite |
|------|--------|-------------|-----------|
| 1 | SPY | put_debit | 76.67 |
| 2 | NVDA | put_debit | 80.53 |
| 3 | NVDA | put_debit | 78.69 |
| 4 | AAPL | put_debit | 78.40 |
| 5 | AMZN | put_debit | 78.34 |

**Note:** This output was generated **before** the scoring profile recalibration fix. The ranking audit reflects old SCORING_PROFILES values. A fresh run is needed to validate the recalibrated profiles produce income candidates in NEUTRAL.

### 2C — `_write_ranking_audit` Location and Call Sites

**Definition:** `options_opportunity_runner.py` line ~2168  
**Single call site:** `options_opportunity_runner.py` line ~886, at end of `run_options_opportunity()`  
**Called unconditionally** after every run, writing to `data/diagnostics/ranking_audit_{timestamp}.json` and `ranking_audit_latest.json`.

---

## Section 3: Active Trades in Full Refresh

### 3A — Frontend: `handleFullRefresh()` in `trade_management_center.js`

**Location:** Line ~2738

| Aspect | Detail |
|--------|--------|
| API Call | `api.runActiveTradesPipeline({ account_mode, skip_model })` |
| Backend Endpoint | `POST /api/active-trade-pipeline/run` |
| Client Timeout | 185 seconds via `modelFetch()` |
| On Success | `renderActiveResults(data)` |
| On Error | `showActiveError(errMsg, data)` |
| On Timeout (AbortError) | Shows "Active trade analysis timed out" error |
| Render Guard Set | `_lastManualActiveRenderAt = Date.now()` (both error and success paths) |

### 3B — Render Guard System

| Constant | Value | Purpose |
|----------|-------|---------|
| `_MANUAL_RENDER_GUARD_MS` | 30000 (30s) | Prevents orchestrator poll from overwriting manual results |
| `_lastManualActiveRenderAt` | Timestamp (ms) | Set after every manual render; checked in `loadLatestActiveResults()` |

**Guard check (line ~1910):** `loadLatestActiveResults()` skips if `Date.now() - _lastManualActiveRenderAt < _MANUAL_RENDER_GUARD_MS`.

**`_lastManualActiveRenderAt` is set at 5 locations:**
- Line ~1886: after `tmcRunActiveOnly()` success
- Line ~1890: after `renderActiveResults(data)` in standalone mode
- Line ~1898: after `renderActiveResults()` in standalone mode
- Line ~2809: in `handleFullRefresh()` error catch
- Line ~2820: in `handleFullRefresh()` success path

### 3C — `renderActiveResults()` (line ~2017)

**What it does:**
1. Validates grid DOM element exists
2. Guards against `data.ok === false`
3. Shows empty state if no recommendations
4. Sorts rows by urgency then conviction
5. Updates count badge and timestamp
6. Stores sorted rows in `_activeRenderedRows`
7. Builds HTML via `buildActiveTradeCard()` per recommendation
8. **Replaces entire grid:** `grid.innerHTML = html;` (line ~2074)
9. Removes old click listener to prevent stacking
10. Wires delegated click handlers for card actions
11. Renders run metadata (account mode, run ID, duration)

**Overwrite behavior:** Total replacement — old active trade cards are fully cleared before new HTML is injected.

### 3D — Backend: `run_pipeline()` in `routes_active_trade_pipeline.py`

**Route:** `POST /api/active-trade-pipeline/run` (line ~60)  
**Query params:** `account_mode` (live|paper), `skip_model` (boolean)

**Pipeline stages:**
1. Pre-check Tradier credentials
2. Fetch active trades via `_build_active_payload()`
3. Resolve services (monitor, regime, base_data)
4. Run pipeline via `run_active_trade_pipeline()` wrapped in `asyncio.shield()`
5. Store result in-memory, return to client

**Timeout:** Backend relies on client-side 185s abort; `asyncio.shield()` prevents cancellation from propagating to the pipeline task so it can complete even if client disconnects.

### 3E — Orchestrator Polling

- Polls `getOrchestratorStatus()` every 5 seconds (line ~3375)
- On orchestrator cycle complete: calls `loadLatestActiveResults()` (line ~3384)
- Guard at line ~1910 prevents overwrite within 30-second manual render window

---

## Section 4: P&L Sign Convention

### 4A — `_normalize_positions()` in `routes_active_trades.py`

**Location:** Line ~194

#### `cost_basis_total` computation (3 paths):

1. **Direct from Tradier** (lines ~218-219):
   ```python
   cost_basis_total = _to_float(row.get("cost_basis"))
   ```
   Tradier provides signed cost_basis (negative for short positions).

2. **Derive `avg_open_price` from `cost_basis_total`** (lines ~228-232):
   ```python
   divisor = abs(quantity)
   if parsed_occ:  # option — Tradier cost_basis includes 100× multiplier
       divisor *= 100
   avg_open_price = round(abs(cost_basis_total / divisor), 4)
   ```
   **`abs()` applied** to ensure per-share price is always unsigned.

3. **Reconstruct `cost_basis_total` from `avg_open_price`** (lines ~252-253):
   ```python
   cost_basis_total = round(avg_open_price * abs(quantity), 2)
   ```
   Both factors are unsigned → result is always positive.

#### `avg_open_price` derivation precedence:
1. `average_open_price` (Tradier standard for options)
2. `avg_open_price`
3. `average_price` (Tradier standard for equity)
4. `avg_cost`
5. `price` (fill price fallback)
6. Derived from `abs(cost_basis_total / (abs(quantity) × multiplier))`

#### `side` field:
Not determined in `_normalize_positions()` — only raw `quantity` (signed) is stored. Side is derived downstream.

### 4B — `_build_active_trades()` in `routes_active_trades.py`

**Location:** Line ~445

#### Multi-leg sign-flip pattern (lines ~481-502):

```python
avg_open_price = sum(
    float(leg["avg_open_price"]) * (1 if int(leg.get("quantity") or 0) < 0 else -1)
    for leg in strat_legs
)

mark_price = sum(
    float(leg["mark_price"]) * (1 if int(leg.get("quantity") or 0) < 0 else -1)
    for leg in strat_legs
)
```

**Convention:**
- Short legs (qty < 0): multiply by **+1** → positive contribution (premiums received)
- Long legs (qty > 0): multiply by **-1** → negative contribution (premiums paid)
- For credit spreads: net result is positive (credit received)

#### `market_value` computation (lines ~510-513):

```python
market_value = round(mark_price * quantity * 100, 2)
```

Formula: `mark_price × quantity × 100` (always applies 100× multiplier for options).

#### `cost_basis_total` (multi-leg, lines ~508-509):

```python
cost_basis_total = round(avg_open_price * quantity * 100, 2)
```

Uses signed-flipped net avg_open_price.

#### Unrealized P&L (fallback, lines ~504-506):

```python
unrealized = (avg_open_price - mark_price) * quantity * 100
```

For credit spreads: `(credit_received − current_value) × contracts × 100`.
Positive = profitable (credit has decayed).

#### Leg `side` field determination (lines ~584-590):

```python
"side": "sell" if qty_val < 0 else "buy"
"qty": abs(qty_val)
```

Side determined from **quantity sign**, not a dedicated side field. Qty is always unsigned in the output.

---

## Section 5: Portfolio Balance

### 5A — Endpoint

**Route:** `POST /portfolio-balance/run` (in `routes_tmc.py` line ~508)

**Function signature:**
```python
async def run_portfolio_balance(
    request: Request,
    body: TMCPortfolioBalanceRequest | None = None,
) -> dict[str, Any]:
```

**Request model:**
```python
class TMCPortfolioBalanceRequest(BaseModel):
    account_mode: str = Field(default="paper", pattern="^(live|paper)$")
    skip_model: bool = Field(default=False)
    stock_results: dict[str, Any] | None = None
    options_results: dict[str, Any] | None = None
    active_trade_results: dict[str, Any] | None = None
```

### 5B — Timeout

```python
result = await asyncio.wait_for(
    run_portfolio_balance_workflow(...),
    timeout=60.0,
)
```

**60-second hard timeout** — returns 504 Gateway Timeout if exceeded.

### 5C — LLM Calls

**No direct LLM calls** in the endpoint handler. The `skip_model` parameter is passed through to the workflow. Portfolio balance accepts pre-computed results from other workflows.

### 5D — What It Computes

**Workflow:** `portfolio_balancing_runner.py`

| Stage | What It Does |
|-------|--------------|
| 1. Account State | Fetches Tradier account equity and balances |
| 2. Regime Label | Gets current market regime classification |
| 3. Dynamic Risk Policy | Builds risk policy from account balance + regime |
| 6. Portfolio State | Computes per-trade Greeks, position concentration |
| 7. Rebalance Plan | Builds actions: close/hold/open/skip recommendations |

**Return structure:**
```json
{
    "ok": bool,
    "run_id": str,
    "account_mode": str,
    "timestamp": "ISO datetime",
    "duration_ms": int,
    "account_equity": float,
    "regime_label": str,
    "rebalance_plan": { "close_actions", "hold_positions", "open_actions", "skip_actions" },
    "active_trade_summary": { "total", "close", "hold", "open_suggested", "skipped" },
    "risk_policy": dict,
    "stages": dict,
    "errors": list
}
```

---

## Section 6: Model Router Degradation

### 6A — "All candidate providers were disqualified" Origin

**File:** `model_router_policy.py` line ~890

```python
elif final_result is None:
    error_summary = "All candidate providers were disqualified"
    error_detail = f"candidates={candidate_order}, decision_log has details"
```

**Trigger:** `final_result` is `None` after iterating through all candidate providers. Every provider was skipped or failed without retry eligibility.

### 6B — Disqualification Reasons

| SkipReason | Description |
|------------|-------------|
| `NOT_REGISTERED` | Provider not in the registry |
| `NOT_CONFIGURED` | Provider exists but not configured (no endpoint) |
| `UNAVAILABLE` | ProviderState = UNAVAILABLE |
| `FAILED` | ProviderState = FAILED |
| `BUSY` | ProviderState = BUSY |
| `AT_CAPACITY` | Max concurrency exhausted |
| `SLOT_DENIED` | Gate reservation failed |
| `CIRCUIT_OPEN` | Circuit breaker tripped |

**Disqualifying states:** `UNAVAILABLE` and `FAILED` only. BUSY triggers skip but not permanent disqualification.

### 6C — Circuit Breaker

**File:** `model_router_policy.py` lines ~110-202

| Parameter | Value |
|-----------|-------|
| Failure threshold | 3 consecutive failures |
| Cooldown schedule | 30s → 60s → 120s → 240s → 300s (exponential) |
| Thread safety | `threading.Lock()` |
| Scope | Module-level singleton, process lifetime |

### 6D — Fallback Chain

1. **Round-robin rotation** across candidates for load distribution
2. **Probe caching** — probe each provider once per routing cycle
3. **Gate reservation** — check capacity before dispatch
4. **Capacity wait** — up to 180s (6 × 30s) waiting for a slot if all providers at max concurrency
5. **Retryable failures** → move to next provider: `connection_error`, `timeout`, `no_endpoint`, `not_configured`
6. **Non-retryable failures** → stop: application-level errors (provider responded with error)

### 6E — Model Sources

**File:** `model_sources.py`

| Source | Name | Endpoint | Enabled |
|--------|------|----------|---------|
| `local` | Local | `http://localhost:1234/v1/chat/completions` | ✅ |
| `model_machine` | Model Machine | `http://192.168.1.143:1234/v1/chat/completions` | ✅ |
| `premium_online` | Premium Online | None | ❌ |

**Note:** `model_sources.py` is a backward-compatibility layer. For routed execution (Step 8+), authority is in `execution_mode_state` + `model_routing_contract`.

---

## Summary of Findings

### Critical Issues

| # | Area | Finding | Impact |
|---|------|---------|--------|
| 1 | Candidate Flow | **All 30 candidates in latest output are `put_debit`** — zero income strategies despite NEUTRAL regime | Ranking system was validated to produce correct ordering (income > directional in NEUTRAL for representative candidates), but the latest audit file pre-dates the scoring profile fix. A fresh run is needed. |
| 2 | Candidate Flow | **Score compression: 4.7-point range across all 30 candidates** | Insufficient differentiation — candidates are effectively indistinguishable by score |
| 3 | Candidate Flow | **All top 10 got PASS from model** | Model may be too strict, or candidates genuinely weak — either way, zero EXECUTE recommendations |

### Observations (Not Necessarily Issues)

| # | Area | Observation |
|---|------|-------------|
| 4 | Ranking | Two ranking systems coexist: legacy `compute_rank_score()` (flat dict, used in `_compute_candidate_rank`) and v2 `rank_candidates()` (strategy-aware, used in `_stage_enrich_evaluate`). The v2 path is authoritative for final output. |
| 5 | Ranking | Weight sums are correct (1.00) for all strategy classes |
| 6 | Ranking | `classify_strategy()` maps all 11 canonical strategy IDs correctly |
| 7 | P&L | `abs()` is correctly applied to `avg_open_price` derivation in `_normalize_positions()` (line ~232) |
| 8 | P&L | Multi-leg sign-flip uses quantity sign (short=+1, long=-1) — correct for credit spread net premium |
| 9 | P&L | Leg `side` field derived from quantity sign (`qty < 0 → "sell"`) in `_build_active_trades()` |
| 10 | Active Trades | `renderActiveResults()` does full HTML replacement (`grid.innerHTML = html`), preventing stale card accumulation |
| 11 | Active Trades | 30-second render guard prevents orchestrator poll from overwriting manual refresh results |
| 12 | Active Trades | `asyncio.shield()` protects pipeline from client disconnects |
| 13 | Portfolio Balance | 60-second timeout; no direct LLM calls; builds rebalance plan with Greek budgets and concentration |
| 14 | Model Router | Circuit breaker: 3-failure threshold, 30s–300s exponential cooldown |
| 15 | Model Router | "All candidate providers were disqualified" fires when every provider in candidate list is skipped without retry eligibility |
| 16 | Model Router | 2 enabled sources (local + model_machine), 1 disabled placeholder (premium_online) |

### Recommended Next Steps (Not Implemented)

1. **Run a fresh options scan** to validate the recalibrated scoring profiles produce income candidates in NEUTRAL regime
2. **Investigate upstream scanner** — the absence of income candidates may be an upstream issue (scanner not producing credit spreads/ICs) rather than a ranking issue
3. **Review model PASS threshold** — all top 10 getting PASS suggests either threshold is too strict or model prompts need adjustment
4. **Consider removing legacy `compute_rank_score()`** path or documenting its dual-path status if it's still needed
5. **Validate score compression** — even with recalibrated profiles, check whether the new ranges produce better separation

---

*End of audit. No code was modified.*
