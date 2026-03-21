# Audit 3D — Decision Policy & Guardrails

**Scope**: Every policy, guardrail, risk gate, and decision constraint that can block, modify, or override a trade between "candidate scored" and "order executed."

**Date**: 2025-06-01  
**Auditor**: Copilot  
**Method**: Full codebase search and file-level verification of all policy/guardrail/gate/risk_check code across `app/`, `common/`, and `trading/`.

---

## 1. Decision Policy Inventory

### 1A. Scanning/Selection Policy Checks

| Policy | Location | Type | Enforcement |
|--------|----------|------|-------------|
| `MIN_SETUP_QUALITY = 30` | `stock_opportunity_runner.py` L118 | Hard filter | Stage 6: rejects stock candidates with `setup_quality < 30` |
| `MODEL_FILTER_TOP_N = 10` | `stock_opportunity_runner.py` L120 | Rank cutoff | Stage 7b: only model-reviews top-10 after rank sort |
| Model BUY recommendation | `stock_opportunity_runner.py` Stage 8 | Hard gate | Only passes candidates where LLM says BUY |
| `MIN_PREMIUM = $0.05` | `options_opportunity_runner.py` L966 | Credibility gate | Stage 5: rejects "penny premium" candidates |
| `MAX_POP_THRESHOLD = 0.995` | `options_opportunity_runner.py` L967 | Credibility gate | Stage 5: rejects zero-delta-short candidates |
| Fillable leg (bid > 0) | `options_opportunity_runner.py` L993-997 | Credibility gate | Stage 5: rejects all-legs-zero-bid candidates |
| Scanner-specific thresholds | 4 scanner `_BALANCED_CONFIG` dicts | Hard filters | Phase B/C of stock scanners |
| V2 Phase D hygiene checks | `scanner_v2/hygiene/quote_sanity.py`, `liquidity_sanity.py` | Hard filters | Phase D/D2: bid/ask sanity, OI/volume floors |
| V2 Phase E math checks | `scanner_v2/validation/math_checks.py` | Hard reject | Positive max_loss, positive max_profit, finite values |

### 1B. Execution-Path Policy Checks

| Policy | Location | Type | Enforcement |
|--------|----------|------|-------------|
| Preview risk hard checks | `trading/risk.py` L34-72 | 4 hard checks | `width_ok`, `max_loss_ok`, `credit_floor_ok`, `legs_have_bid_ask` — HTTP 400 on failure |
| Execution pre-flight | `trading/execution_validator.py` L134-213 | 7+ blocking checks | OCC format, quantity>0, strike>0, expiration valid, side valid, right valid, net credit/debit present, max_loss>0, negative natural price, short leg bid>0 |
| Development mode safety | `trading/service.py` L580-586 | Forced routing | `ENVIRONMENT=development` → forces all live→paper |
| Execution enable gate | `config.py` L37-41 | Global toggle | `TRADIER_EXECUTION_ENABLED=false` → 403 on live orders |
| Freshness gate (live) | `trading/risk.py` L78-89, `service.py` L636 | Staleness block | Quote/chain age > `LIVE_DATA_MAX_AGE_SECONDS` (30s) → 400 |
| Confirmation token | `trading/service.py` L76-107 | Replay protection | HMAC-signed ticket + TTL (300s default) |
| Idempotency key | `trading/service.py` L572 | Duplicate avoidance | Cached result returned on re-submit |

---

## 2. Risk Policy Constants

### 2A. RiskPolicyService Default Constants

**File**: `app/services/risk_policy_service.py` L44-68  
**Store**: Persisted to `results/risk_policy.json`, updatable via API

| Constant | Default | Category |
|----------|---------|----------|
| `portfolio_size` | $100,000 | Account sizing |
| `max_total_risk_pct` | 6% | Portfolio-level risk cap |
| `max_symbol_risk_pct` | 2% | Concentration cap |
| `max_trade_risk_pct` | 1% | Per-trade risk cap |
| `max_dte` | 45 | Time limit |
| `min_cash_reserve_pct` | 20% | Cash floor |
| `max_position_size_pct` | 5% | Position sizing |
| `default_contracts_cap` | 3 | Quantity cap |
| `max_risk_per_trade` | $1,000 | Dollar risk cap |
| `max_risk_total` | $6,000 | Dollar total cap |
| `max_concurrent_trades` | 10 | Count limit |
| `max_risk_per_underlying` | $2,000 | Symbol-level cap |
| `max_same_expiration_risk` | $500 | Calendar concentration |
| `max_short_strike_distance_sigma` | 2.5σ | Strike placement |
| `min_open_interest` | 500 | Liquidity floor |
| `min_volume` | 50 | Liquidity floor |
| `max_bid_ask_spread_pct` | 1.5% | Spread width cap |
| `min_pop` | 60% | Probability floor |
| `min_ev_to_risk` | 0.02 | Risk-reward floor |
| `min_return_on_risk` | 10% | Return floor |
| `max_iv_rv_ratio_for_buying` | 1.0 | IV premium cap |
| `min_iv_rv_ratio_for_selling` | 1.1 | IV edge floor |

### 2B. Settings-Level Constants (config.py)

| Constant | Default | Purpose |
|----------|---------|---------|
| `MAX_WIDTH_DEFAULT` | 10 | Preview risk width cap |
| `MAX_LOSS_PER_SPREAD_DEFAULT` | $500 | Preview risk loss cap |
| `MIN_CREDIT_DEFAULT` | $0.20 | Preview credit floor |
| `LIVE_DATA_MAX_AGE_SECONDS` | 30 | Freshness gate threshold |
| `DTE_MIN` | 3 | Expiration window floor |
| `DTE_MAX` | 14 | Expiration window ceiling |
| `TRADING_CONFIRMATION_TTL_SECONDS` | 300 | Token expiry |

---

## 3. Portfolio Context Integration

### 3A. What Exists

**RiskPolicyService.build_snapshot()** (L477-530) reads active positions from Tradier or the latest analysis report file, aggregates risk by symbol, computes total risk vs. budget, and compares against all 23 policy constants. Output is a snapshot with `hard_limits` and `soft_gates` arrays.

**routes_portfolio_risk.py** (`/risk/matrix`) reads the same position data, computes Greeks approximations, and generates warnings when policy thresholds are breached.

### 3B. How It Integrates — Or Doesn't

| Capability | Status | Detail |
|------------|--------|--------|
| Read current positions | ✅ Implemented | Tradier active trades + analysis report fallback |
| Aggregate risk by symbol | ✅ Implemented | `_build_warning_groups()` L350-460 |
| Compare vs. policy limits | ✅ Implemented | Generates hard_limits/soft_gates strings |
| **Block new trades** when limits breached | ❌ Not enforced | Warnings rendered on dashboard only |
| Check position before order submission | ❌ Not connected | Trading service does NOT call RiskPolicyService |
| Correlation/sector exposure | ❌ Not implemented | No cross-position correlation analysis |

**Critical Gap**: `RiskPolicyService` produces rich policy warnings but they are **informational only**. The trading execution path (`service.py` preview + submit) never consults `RiskPolicyService`. A user can submit an order that exceeds every policy limit and the system will not block it.

---

## 4. Event Risk Gates

### 4A. What Exists

**EventCalendarContext** (`app/services/event_calendar_context.py`) is a comprehensive module that:
- Fetches macro events (FOMC, CPI, NFP) from Finnhub
- Fetches company earnings from Finnhub
- Classifies importance (high/medium/low)
- Computes time windows (within_24h, within_3d, beyond_3d)
- Computes candidate overlap and portfolio overlap
- Derives risk state: `crowded | elevated | quiet | unknown`
- Generates warning flags (e.g. `high_importance_event_within_24h`)

### 4B. Enforcement Status

| Capability | Status | Detail |
|------------|--------|--------|
| Event data collection | ✅ Implemented | Macro + company events from Finnhub |
| Risk state derivation | ✅ Implemented | Heuristic-based (crowded/elevated/quiet) |
| Warning flag generation | ✅ Implemented | 8+ flags available |
| **Block scan when crowded** | ❌ Not enforced | State is enrichment, not gating |
| **Widen DTE near events** | ❌ Not implemented | No DTE adjustment |
| **Reject earnings-adjacent trades** | ❌ Not enforced | Overlap data computed but never used as filter |

**Gap Summary**: The event calendar produces useful flags and risk states, but **no code path uses them to block, reject, or modify trade decisions**. A candidate can be selected for a stock with earnings tomorrow and the system will not warn the trading path.

---

## 5. Regime-Aware Gating

### 5A. Regime Classification

**RegimeService** (`app/services/regime_service.py`) produces a regime label (RISK_ON / NEUTRAL / RISK_OFF) with a composite 0-100 score via a 3-block scoring model (breadth, volatility, trend).

### 5B. How Regime Is Used

| Consumer | Usage | Gate? |
|----------|-------|-------|
| Market state artifact | Enrichment field | No |
| Stock opportunity runner | Loaded in Stage 2, logged, passed to enrichment | **No filtering** |
| Options opportunity runner | Loaded in Stage 2, logged, passed to enrichment | **No filtering** |
| Active trade monitor | Scoring component for HOLD/WATCH/REDUCE/CLOSE | Informational only |
| Contextual chat | Included in prompt context | No |
| RegimeService playbook | Strategy type suggestions | Informational only |

### 5C. What's Missing

- **No regime-based gate** blocks aggressive strategies in RISK_OFF
- **No regime-based threshold adjustment** changes scanner aggressiveness
- **No regime override** prevents selling premium in high-vol environments
- The playbook suggests strategy types per regime but the scanner/runner ignores it

---

## 6. Static vs. Dynamic Thresholds

| Category | Static | Dynamic | Notes |
|----------|--------|---------|-------|
| Scanner thresholds (4 stock) | ✅ All hardcoded | ❌ | `_BALANCED_CONFIG` frozen in source |
| V2 scanner thresholds | N/A (no presets) | ❌ | Phase D/D2 hygiene checks are hardcoded |
| Credibility gate | ✅ Inline constants | ❌ | `MIN_PREMIUM=0.05`, `MAX_POP=0.995` |
| Risk policy constants | Defaults static | ✅ via API | User can update via `/api/risk/policy` |
| Config.py settings | ✅ Env vars | ❌ at runtime | Set at startup, not adjustable live |
| Preview risk limits | ✅ Env vars | ❌ at runtime | `MAX_WIDTH`, `MAX_LOSS_PER_SPREAD` |

**Assessment**: Only `RiskPolicyService` supports runtime adjustment, but those values are **not enforced** in any pipeline gate. Everything that is actually enforced is static.

---

## 7. Policy Enforcement Map — Guardrail Inventory

The table below maps every gate between "candidate scored" and "order executed," with enforcement status.

### 7A. Scanning/Selection Phase Gates

| # | Gate | File:Line | Blocks? | Status |
|---|------|-----------|---------|--------|
| G1 | Setup quality floor | `stock_opportunity_runner.py` L~870 | ✅ Hard reject | **Implemented** |
| G2 | Model recommendation | `stock_opportunity_runner.py` Stage 8 | ✅ Hard reject | **Implemented** |
| G3 | Credibility gate (3 checks) | `options_opportunity_runner.py` L960-1010 | ✅ Hard reject | **Implemented** |
| G4 | Scanner filter chain | 3 of 4 stock scanner `_apply_filters()` | ✅ Hard reject | **Implemented** (pullback_swing missing) |
| G5 | V2 Phase D/D2 hygiene | `quote_sanity.py`, `liquidity_sanity.py` | ✅ Hard reject | **Implemented** |
| G6 | V2 Phase E math checks | `math_checks.py` | ✅ Hard reject | **Implemented** |
| G7 | Portfolio risk check before scan | N/A | ❌ | **Missing** |
| G8 | Event risk check before scan | N/A | ❌ | **Missing** |
| G9 | Regime-based strategy gate | N/A | ❌ | **Missing** |

### 7B. Execution Phase Gates

| # | Gate | File:Line | Blocks? | Status |
|---|------|-----------|---------|--------|
| G10 | Execution pre-flight validator | `execution_validator.py` L134 | ✅ Hard reject | **Implemented** |
| G11 | Preview risk checks (4 hard) | `risk.py` L34-72, `service.py` L399-430 | ✅ HTTP 400 | **Implemented** |
| G12 | Development mode safety | `service.py` L580-586 | ✅ Forces paper | **Implemented** |
| G13 | Execution enable toggle | `config.py` L37-41 | ✅ HTTP 403 | **Implemented** |
| G14 | Freshness gate (live only) | `risk.py` L78-89, `service.py` L636 | ✅ HTTP 400 | **Implemented** |
| G15 | Confirmation token + TTL | `service.py` L76-107, L572 | ✅ HTTP 400 | **Implemented** |
| G16 | Idempotency dedup | `service.py` L572 | ✅ Returns cached | **Implemented** |
| G17 | Credential resolution | `tradier_credentials.py` | ✅ HTTP 403 | **Implemented** |
| G18 | Portfolio risk check before submit | N/A | ❌ | **Missing** |
| G19 | validate_trade_for_execution in submit path | N/A | ❌ | **Missing** — only called from validate API + build-payload, NOT from submit flow |

### 7C. Post-Execution (Monitoring, Not Blocking)

| # | Gate | File | Action | Status |
|---|------|------|--------|--------|
| G20 | Active trade monitor | `active_trade_monitor_service.py` | HOLD/WATCH/REDUCE/CLOSE | **Informational** — alerts only |
| G21 | Portfolio risk dashboard | `routes_portfolio_risk.py` | Warning strings | **Informational** — display only |
| G22 | Risk policy snapshot | `risk_policy_service.py` L477+ | hard_limits/soft_gates | **Informational** — dashboard only |

---

## 8. Order Execution Guardrails — Detailed Flow

```
User clicks "Execute" in UI
    ↓
POST /api/trading/validate  ← G10: ExecutionValidator blocks malformed trades
    ↓ (UI only enables button if valid=True)
POST /api/trading/preview   ← G11: Risk hard checks (width, loss, credit, bid/ask)
    ↓ (returns ticket + confirmation token)
    ↓ ← G15: Token signed with HMAC, expires in 300s
POST /api/trading/submit    ← G12: Dev mode forces paper
    ↓                       ← G13: Execution gate blocks if disabled
    ↓                       ← G15: Token + hash validation
    ↓                       ← G16: Idempotency dedup
    ↓                       ← G14: Freshness gate (live only, 30s staleness)
    ↓                       ← G17: Credential resolution
    ↓
Tradier API call (or dry-run if disabled)
    ↓
Order reconciliation poll (status mapping)
```

**Notable**: The validate endpoint (G10) is called by the **frontend** as a UI gate. It is **not** called internally before `submit()`. If the frontend is bypassed (e.g., API call), the submit path relies only on the preview risk checks (G11) and freshness (G14).

---

## Findings

### F-3D-01 [HIGH] — RiskPolicyService Not Connected to Execution Path

**What**: `RiskPolicyService` defines 23 risk policy constants and generates `hard_limits` / `soft_gates` arrays comparing active positions against policy thresholds. But the trading execution path (`TradingService.preview()` and `TradingService.submit()`) **never calls** `RiskPolicyService`. A user can submit an order that violates every policy limit.

**Where**: `risk_policy_service.py` defines → `routes_risk_capital.py` renders → `trading/service.py` ignores

**Impact**: Portfolio risk policies are decorative. No enforcement exists.

**Recommendation**: Wire `RiskPolicyService.build_snapshot()` into the preview flow. If any `hard_limits` are triggered, block the preview with details.

---

### F-3D-02 [HIGH] — Event Risk Data Not Used for Gating

**What**: `EventCalendarContext` computes event risk state (crowded/elevated/quiet), overlap counts, and warning flags. No consumer uses these to gate or adjust trade decisions.

**Where**: `event_calendar_context.py` produces → consumed only for display/enrichment

**Impact**: Trades can be opened hours before FOMC, CPI, or earnings with no system awareness.

**Recommendation**: At minimum, add event risk state to the enrichment output and warn in the UI. Ideally, option runner should reject or flag candidates with DTE windows overlapping major events.

---

### F-3D-03 [HIGH] — Regime Label Not Used for Filtering

**What**: `RegimeService` classifies the market as RISK_ON / NEUTRAL / RISK_OFF. Neither the stock nor options pipeline uses this label to gate, adjust thresholds, or block strategy types. The playbook suggests strategy type adjustments per regime but scanners ignore it.

**Where**: `regime_service.py` produces → `stock_opportunity_runner.py` and `options_opportunity_runner.py` use it only for enrichment

**Impact**: The system will scan full-aggression selling strategies in a RISK_OFF environment with no adjustment.

**Recommendation**: Start with soft gating (log + flag), evolving to threshold modifiers per regime. Example: RISK_OFF → require min_pop 70% instead of pipeline default, or suppress sell-premium strategies.

---

### F-3D-04 [MEDIUM] — ExecutionValidator Not in Submit Path

**What**: `validate_trade_for_execution()` is only called from the `/api/trading/validate` and `/api/trading/build-payload` endpoints. The actual `TradingService.submit()` flow does NOT run this validator. If the frontend is bypassed (API call), a malformed trade (missing OCC, zero quantity, no max_loss) could reach the broker.

**Where**: `execution_validator.py` L134 → called in `routes_trading.py` L250, L273 → NOT called in `trading/service.py` submit()

**Impact**: Defense-in-depth gap. The submit path relies on the frontend calling /validate first.

**Recommendation**: Call `validate_trade_for_execution()` inside `TradingService.submit()` as a server-side safety net, independent of what the frontend does.

---

### F-3D-05 [MEDIUM] — Freshness Gate Only on Live Orders

**What**: `evaluate_submit_freshness()` is only called when `account_mode == "live"`. Paper orders skip the freshness check entirely. While paper mode is lower-risk, stale data still produces misleading fills.

**Where**: `trading/service.py` L635-637 — inside `if account_mode == "live":` block

**Impact**: Paper-mode users can submit against 10-minute-old data without warning.

**Recommendation**: Apply freshness warning (not blocking) for paper orders, or extend the freshness check with a paper-mode timeout (e.g., 120s vs 30s).

---

### F-3D-06 [MEDIUM] — All Enforced Thresholds Are Static

**What**: Every threshold that actually blocks a trade (scanner configs, credibility gate, preview risk limits) is hardcoded. The only runtime-adjustable values are in `RiskPolicyService`, which is not enforced. Users cannot tune scanner aggressiveness without code changes.

**Where**: `_BALANCED_CONFIG` in all 4 scanners, `MIN_PREMIUM`/`MAX_POP_THRESHOLD` in options runner, `MAX_WIDTH`/`MAX_LOSS_PER_SPREAD`/`MIN_CREDIT` in config.py

**Impact**: No way to tighten or loosen filters without code changes. Strict/Balanced/Wide presets exist only in the legacy `StrategyService` path (not used by V2 or stock scanners).

**Recommendation**: Centralize enforced thresholds into a single configuration source that supports multiple profiles (at minimum Strict/Balanced/Wide).

---

### F-3D-07 [MEDIUM] — No Position-Aware Scan Filtering

**What**: Neither the stock nor options pipeline checks existing open positions before scanning. The system will happily recommend a 3rd credit spread on SPY when the user already has 2 open SPY positions consuming their entire symbol risk budget.

**Where**: `stock_opportunity_runner.py` and `options_opportunity_runner.py` — neither imports or calls position-checking code

**Impact**: Duplicate and concentrated position recommendations with no awareness of portfolio state.

**Recommendation**: At scan time, load active positions and either: (a) skip symbols already at concentration limit, or (b) flag candidates as "position-additive" with current exposure data.

---

### F-3D-08 [LOW] — Active Trade Monitor Is Advisory Only

**What**: `ActiveTradeMonitorService` produces HOLD/WATCH/REDUCE/CLOSE recommendations with trigger analysis (drawdown, trend break, regime flip). These are displayed in the UI but cannot auto-execute any action.

**Where**: `active_trade_monitor_service.py` — all outputs are `MonitorResult` dicts rendered by frontend

**Impact**: Low — advisory is the appropriate behavior for v1. No immediate fix needed, but worth noting that the monitor cannot protect against catastrophic scenarios without manual intervention.

---

### F-3D-09 [LOW] — FreshnessPolicy allow_stale Defaults to True

**What**: The `FreshnessPolicy` dataclass defaults to `allow_stale=True`, meaning stale market state artifacts are always consumed. No runner or workflow overrides this to `False`. The `is_consumable()` function only rejects stale data when `allow_stale=False`.

**Where**: `architecture.py` L207 → consumed in `market_state_contract.py` L240

**Impact**: Low — runners will use stale market state without complaint. This is appropriate for non-real-time scanning but may produce stale enrichment data.

---

### F-3D-10 [LOW] — Duplicate Risk Warning Logic

**What**: Risk policy warnings are computed in two places: `RiskPolicyService._build_warning_groups()` (L350-460) and `routes_portfolio_risk.py._build_warnings()` (L301-340). Both compare against the same policy constants but use different code paths and produce different output formats.

**Where**: `risk_policy_service.py` L350-460 vs. `routes_portfolio_risk.py` L301-340

**Impact**: Low — maintenance burden. If policy logic changes, both must be updated.

---

## Summary

| Severity | Count | Findings |
|----------|-------|----------|
| **HIGH** | 3 | F-3D-01 (risk policy not enforced), F-3D-02 (event risk not gated), F-3D-03 (regime not used for filtering) |
| **MEDIUM** | 4 | F-3D-04 (validator not in submit), F-3D-05 (freshness live-only), F-3D-06 (all thresholds static), F-3D-07 (no position awareness) |
| **LOW** | 3 | F-3D-08 (monitor advisory), F-3D-09 (allow_stale default), F-3D-10 (duplicate warning logic) |
| **Total** | **10** | |

### Key Architectural Observations

1. **The system has two safety tiers that don't talk to each other**: The scanning/selection tier (runners + scanners) and the execution tier (TradingService) operate independently. Neither consults the other's safety data.

2. **Rich infrastructure, zero enforcement**: Event calendar, regime service, and risk policy service represent significant development investment. All produce useful analysis. None of it is wired into any decision gate.

3. **Execution path is well-guarded for order correctness but blind to portfolio context**: The preview→confirm→submit flow has good structural safeguards (token, freshness, dev-mode, enable gate). But it has zero awareness of whether the trade fits the user's risk policy or portfolio limits.

4. **The gap between "what is defined" and "what is enforced" is the single largest systemic risk**: 23 risk policy constants are defined and user-adjustable. Zero of them block any action.
