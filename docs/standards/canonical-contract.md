# Canonical Trade Contract

> **Status:** Reference spec — no implementation yet.
> **Date:** 2026-02-17

This document defines the single data structure that every backend service and
frontend page must converge on.  It replaces the current mix of per-share flat
fields, duplicate identity keys, and scattered alias maps.

---

## 1. Canonical `strategy_id` Set

These are the **only** valid values for `strategy_id` across the entire system.
Every other string (e.g. `credit_put_spread`, `put_credit`, `debit_call_spread`,
`cash_secured_put`, `butterflies`) is a **legacy alias** that must be resolved
to one of these before the trade is surfaced to the UI.

### Credit Spreads

| `strategy_id`       | Display Label        | Current Source          |
|----------------------|----------------------|-------------------------|
| `put_credit_spread`  | Put Credit Spread    | `CANONICAL_STRATEGY_IDS` in `trade_key.py` |
| `call_credit_spread` | Call Credit Spread   | `CANONICAL_STRATEGY_IDS` in `trade_key.py` |

### Debit Spreads

| `strategy_id` | Display Label     | Current Source          |
|----------------|-------------------|-------------------------|
| `put_debit`    | Put Debit Spread  | `CANONICAL_STRATEGY_IDS` in `trade_key.py` |
| `call_debit`   | Call Debit Spread | `CANONICAL_STRATEGY_IDS` in `trade_key.py` |

### Butterflies

| `strategy_id`     | Display Label     | Current Source          |
|--------------------|-------------------|-------------------------|
| `butterfly_debit`  | Debit Butterfly   | `CANONICAL_STRATEGY_IDS` in `trade_key.py` |
| `iron_butterfly`   | Iron Butterfly    | `CANONICAL_STRATEGY_IDS` in `trade_key.py` |

### Multi-Leg

| `strategy_id`  | Display Label | Current Source          |
|-----------------|---------------|-------------------------|
| `iron_condor`   | Iron Condor   | `CANONICAL_STRATEGY_IDS` in `trade_key.py` |

### Calendars

| `strategy_id`         | Display Label         | Current Source          |
|------------------------|-----------------------|-------------------------|
| `calendar_spread`      | Calendar Spread       | `CANONICAL_STRATEGY_IDS` in `trade_key.py` |
| `calendar_call_spread` | Call Calendar Spread  | `CANONICAL_STRATEGY_IDS` in `trade_key.py` |
| `calendar_put_spread`  | Put Calendar Spread   | `CANONICAL_STRATEGY_IDS` in `trade_key.py` |

### Income / Single-Leg

| `strategy_id`  | Display Label     | Current Source          |
|-----------------|-------------------|-------------------------|
| `csp`           | Cash Secured Put  | `CANONICAL_STRATEGY_IDS` in `trade_key.py` |
| `covered_call`  | Covered Call      | `CANONICAL_STRATEGY_IDS` in `trade_key.py` |
| `income`        | Income Strategy   | `CANONICAL_STRATEGY_IDS` in `trade_key.py` |
| `single`        | Single Option     | `CANONICAL_STRATEGY_IDS` in `trade_key.py` |
| `long_call`     | Long Call         | `CANONICAL_STRATEGY_IDS` in `trade_key.py` |
| `long_put`      | Long Put          | `CANONICAL_STRATEGY_IDS` in `trade_key.py` |

### Legacy Aliases (resolve → canonical)

| Legacy String            | Canonical Target       | Where Used                              |
|--------------------------|------------------------|-----------------------------------------|
| `put_credit`             | `put_credit_spread`    | `quant_analysis.py`, `schemas.py`, `tradeKey.js`, `common/utils.py` |
| `call_credit`            | `call_credit_spread`   | `quant_analysis.py`, `schemas.py`, `tradeKey.js` |
| `credit_put_spread`      | `put_credit_spread`    | `stock_analysis_service.py`, `home.js`, multiple frontend pages |
| `credit_call_spread`     | `call_credit_spread`   | `stock_analysis_service.py`, `strategy_dashboard_shell.js` |
| `debit_call_spread`      | `call_debit`           | `debit_spreads.py` plugin output, `home.js`, `stock_analysis.js` |
| `debit_put_spread`       | `put_debit`            | `debit_spreads.py` plugin output, `home.js`, `stock_analysis.js` |
| `debit_call_butterfly`   | `butterfly_debit`      | `butterflies.py` plugin output          |
| `debit_put_butterfly`    | `butterfly_debit`      | `butterflies.py` plugin output          |
| `debit_butterfly`        | `butterfly_debit`      | alias map only                          |
| `butterflies`            | `butterfly_debit`      | alias map, dashboard defaults key       |
| `cash_secured_put`       | `csp`                  | `income.py` plugin output               |

### Not Strategy IDs (separate namespaces)

These strings appear in the codebase but are **navigation/module/playbook
identifiers**, not trade-level strategy IDs.  They must not be confused with
`strategy_id`:

- **Dashboard module keys:** `credit_spread`, `debit_spreads`, `calendars`, `butterflies`, `income`
- **Session stats modules:** `credit_put`, `credit_call`, `stock_scanner`
- **Playbook keys:** `cash_secured_put_far_otm`, `iron_condor_tight`, `credit_spreads_wider`, `short_put_spreads_near_spot`, `aggressive_directional_debit_spreads`, `aggressive_short_calls`, `hedges`

---

## 2. Canonical TradeDTO Shape

This is the JSON shape that the backend MUST emit and the frontend MUST consume.
After migration, no frontend page should read any field not listed here.

```jsonc
{
  // ── Identity (required, always present after normalization) ────────
  "trade_key":          "SPY|put_credit_spread|2026-02-21|440|435|7",
  "symbol":             "SPY",           // uppercase, single canonical field
  "strategy_id":        "put_credit_spread",  // from CANONICAL_STRATEGY_IDS
  "expiration":         "2026-02-21",
  "dte":                7,               // int | null
  "short_strike":       440,             // float | composite string | null
  "long_strike":        435,             // float | composite string | null

  // ── Computed (per-contract monetary, required sub-dict) ───────────
  "computed": {
    "max_profit":        92.0,           // float | null — per-contract ($)
    "max_loss":          408.0,          // float | null — per-contract ($)
    "pop":               0.818,          // float | null — probability [0,1]
    "return_on_risk":    0.2254,         // float | null — ratio
    "expected_value":    1.20,           // float | null — per-contract ($)
    "kelly_fraction":    0.013,          // float | null — ratio [0,1]
    "net_credit":        0.92,           // float | null — per-share ($)
    "net_debit":         null,           // float | null — per-share ($)
    "iv_rank":           0.45,           // float | null — [0,1]
    "short_strike_z":    0.909,          // float | null — z-score
    "bid_ask_pct":       0.0435,         // float | null — ratio
    "strike_dist_pct":   0.0239,         // float | null — ratio
    "rsi14":             55.2,           // float | null — [0,100]
    "rv_20d":            0.10,           // float | null — annualized vol
    "open_interest":     4200,           // float | null
    "volume":            1800,           // float | null
    "underlying_price":  450.25          // float | null
  },

  // ── Details (supplementary analytics) ─────────────────────────────
  "details": {
    "break_even":            436.08,     // float | null
    "dte":                   7,          // float | null (may differ from root)
    "expected_move":         17.93,      // float | null
    "iv_rv_ratio":           1.899,      // float | null
    "trade_quality_score":   0.598,      // float | null
    "market_regime":         "bullish trend, moderate volatility"  // str | null
  },

  // ── Pills (pre-formatted UI badges) ──────────────────────────────
  "pills": {
    "strategy_label":  "Put Credit Spread",
    "dte":             7,
    "pop":             0.818,
    "oi":              4200,
    "vol":             1800,
    "regime_label":    "bullish trend, moderate volatility",
    // Calendar-only additions:
    "dte_front":       7,                // float | null
    "dte_back":        21,               // float | null
    "dte_label":       "DTE 7/21"        // str | null
  },

  // ── Metrics status ────────────────────────────────────────────────
  "metrics_status": {
    "ready":          true,
    "missing_fields": []                 // list of metric names still null
  },

  // ── Validation warnings ───────────────────────────────────────────
  "validation_warnings": []              // list of warning code strings
}
```

### Key Rules

1. **`strategy_id` is the single strategy field.**
   - `spread_type` and `strategy` are back-fill aliases kept for legacy
     compat only.  New code reads `strategy_id`.

2. **`symbol` is the single symbol field.**
   - `underlying` and `underlying_symbol` are back-fill aliases
     for legacy compat.  New code reads `symbol`.

3. **All monetary values in `computed` are per-contract.**
   - `max_profit`, `max_loss`, `expected_value` are ×100 of per-share
     values.  `net_credit`/`net_debit` remain per-share by convention
     (option premium quote units).
   - No `*_per_share` or `*_per_contract` suffixed fields in the DTO.

4. **`null` over wrong data.**
   - Frontend renders "N/A" for null — never "0.00".

5. **No legacy flat fields in the contract.**
   - `ev_per_share`, `max_profit_per_share`, `max_loss_per_share`,
     `ev_per_contract`, `max_profit_per_contract`, `max_loss_per_contract`,
     `p_win_used`, `pop_delta_approx`, `ev_to_risk`, `bid_ask_spread_pct`,
     `strike_distance_pct`, `realized_vol_20d`, `estimated_risk`,
     `risk_amount`, `estimated_max_profit`, `premium_received`,
     `premium_paid`, `probability_of_profit`, `scanner_score`,
     `expiration_date` are all **legacy**.
   - Backend keeps back-filling them in `normalize_trade()` step 10 for
     now; they will be removed once all consumers are migrated.

6. **`computed_metrics` is a superset for backward compat.**
   - Currently `apply_metrics_contract()` produces this.  It overlaps with
     `computed` and `details`.  Future: merge into `computed` and retire.

### What Goes Where

| Need | Read from |
|------|-----------|
| Per-contract max profit | `computed.max_profit` |
| POP | `computed.pop` |
| EV per contract | `computed.expected_value` |
| Return on risk | `computed.return_on_risk` |
| Break-even price | `details.break_even` |
| IV/RV ratio | `details.iv_rv_ratio` |
| Quality score | `details.trade_quality_score` |
| Strategy display name | `pills.strategy_label` |
| Market regime text | `pills.regime_label` or `details.market_regime` |
| Trade key | `trade_key` (root) |
| Symbol | `symbol` (root) |
| Strategy ID | `strategy_id` (root) |

---

## 3. Known Inconsistencies to Fix

### 3a. Frontend `tradeKey.js` Maps to Short Forms

`tradeKey.js` `STRATEGY_ALIASES` maps `put_credit_spread` → `put_credit`
(short form), while backend `trade_key.py` maps to `put_credit_spread`
(long form).  **Trade keys generated frontend vs backend will not match for
credit spreads.**  Fix: align `tradeKey.js` to use the same long-form
canonicals as the backend.

### 3b. `quant_analysis.py` Has Its Own SpreadType

`SpreadType = Literal["put_credit", "call_credit"]` and a local
`_CREDIT_SPREAD_TYPE_MAP` that maps to short forms.  This is isolated to
`enrich_trade()` internals but creates a second source of truth.

### 3c. `schemas.py` Uses Short Forms

`SpreadAnalyzeRequest.strategy: Literal["put_credit", "call_credit"]`
accepts legacy aliases rather than canonical IDs.

### 3d. `net_credit`/`net_debit` Not in `computed`

Frontend's `tradeAccessor.js` FIELD_MAP looks for `computed.net_credit` and
`computed.net_debit`, but `normalize_trade()` never puts them there.  The
fallback to root-level works but is a dead miss on every lookup.

### 3e. `underlying_price` Not in `computed`

Same pattern — frontend looks for it in `computed`, but it's root-only.

### 3f. `computed_metrics` Overlaps `computed` + `details`

`apply_metrics_contract()` creates a superset dict that duplicates data
already in `computed` and `details`.  Future: eliminate the overlap.

### 3g. `homeCache.js` Prefers Per-Share

`homeCache.js` resolves `max_profit_per_share` **before**
`max_profit_per_contract`, potentially selecting a value 100× too small.

### 3h. Plugins Emit Pre-Canonical Strings

Most scanner plugins emit non-canonical `spread_type` values
(e.g. `debit_call_spread`, `cash_secured_put`) that rely on downstream
normalization.  Only `credit_spread.py` emits the canonical value.

### 3i. `iron_condor` Missing from `_SPREAD_TYPE_ALIASES`

Works only because `canonicalize_strategy_id()` checks
`CANONICAL_STRATEGY_IDS` as fallback, but should be explicit in the alias
map for clarity.

---

## 4. Migration Path (Summary)

1. **Backend:** ensure `normalize_trade()` populates `net_credit`,
   `net_debit`, and `underlying_price` into `computed`.  Add `iron_condor`
   to `_SPREAD_TYPE_ALIASES`.
2. **Frontend:** align `tradeKey.js` aliases to long-form canonical IDs.
   Migrate all pages to read from `computed.*` / `details.*` / `pills.*`
   via `tradeAccessor.js` instead of ad-hoc field chains.
3. **Backend:** update `schemas.py` and `quant_analysis.py` to accept
   canonical IDs (`put_credit_spread` / `call_credit_spread`).
4. **Both:** stop writing `*_per_share`, `*_per_contract`, and other
   legacy flat fields once all consumers are on the DTO.
5. **Both:** remove `computed_metrics` once `computed` contains all needed
   fields.
6. **Both:** remove root-level back-fill aliases (step 10 in
   `normalize_trade()`) once no consumer reads them.
