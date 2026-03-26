# Options Trade Execution Audit

**Date:** 2025-03-25
**Scope:** End-to-end trace from TMC "Execute Trade" button → Tradier order submission
**Finding:** Execution fails for ALL strategies at Step 1 — payload schema mismatch causes HTTP 422

---

## 1. Execution Flow

```
User clicks "Execute Trade" on options TradeCard
   │
   ▼
┌─────────────────────────────────────────────────────────────────┐
│ _executeOptionsTrade(btn, tradeKey, rawCandidate)               │
│ trade_management_center.js:1561                                  │
│                                                                  │
│ 1. normalizeOptionsCandidate(rawCandidate)   → card model       │
│ 2. Build orderLegs[] from c.legs             → OCC symbols      │
│ 3. Build orderPayload = {                                       │
│       class: "multileg",          ← NOT in backend schema       │
│       symbol: c.symbol,           ← matches                     │
│       type: "market",             ← NOT in backend schema       │
│       duration: "day",            ← NOT in backend schema       │
│       legs: orderLegs,            ← partial match               │
│       _meta: { strategy_id }      ← NOT in backend schema       │
│         ↑↑ MISSING: strategy, expiration, quantity,              │
│            limit_price, time_in_force, mode                      │
│    }                                                             │
│ 4. api.tradingPreview(orderPayload)  ← SENDS WRONG SHAPE        │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│ POST /api/trading/preview                                        │
│ routes_trading.py:84                                             │
│                                                                  │
│ FastAPI parses body into TradingPreviewRequest (Pydantic)        │
│  → MISSING REQUIRED FIELDS: strategy, expiration,                │
│    quantity (ge=1), limit_price (gt=0)                            │
│  → HTTP 422 Unprocessable Entity                          ❌ FAIL │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│ .catch(function (err) {                                          │
│   console.error('[TMC] Options trade preview failed:', err);     │
│ })                                                               │
│ → BenTradeExecutionModal.open() NEVER CALLED                     │
│ → User sees button flash "Previewing…" then reset to             │
│   "Execute Trade" with NO feedback                        ❌ DEAD │
└─────────────────────────────────────────────────────────────────┘
```

**The TradeTicket modal (which HAS the correct preview/submit flow) never opens.**

The irony: a fully working preview → confirm → submit pipeline exists in `trade_ticket.js` + `trade_ticket_model.js`, with proper `toPreviewRequest()` that maps to `TradingPreviewRequest`. But `_executeOptionsTrade()` bypasses it by calling `api.tradingPreview()` directly with a Tradier-shaped payload, fails, and never opens the modal.

---

## 2. Break Point

**Where:** `_executeOptionsTrade()` at [trade_management_center.js](trade_management_center.js#L1615) — `api.tradingPreview(orderPayload)`

**What happens:** FastAPI returns HTTP 422 Unprocessable Entity because the payload is missing 4 required fields.

**What JS sends:**
```json
{
  "class": "multileg",
  "symbol": "SPY",
  "type": "market",
  "duration": "day",
  "legs": [
    { "option_symbol": "SPY260418P00550000", "side": "sell_to_open",
      "quantity": 1, "strike": 550, "option_type": "put" },
    { "option_symbol": "SPY260418P00545000", "side": "buy_to_open",
      "quantity": 1, "strike": 545, "option_type": "put" }
  ],
  "_meta": { "strategy_id": "put_credit_spread", "source": "tmc_options" }
}
```

**What the backend requires** (`TradingPreviewRequest` at [models.py](models.py#L63)):
```json
{
  "symbol":        "SPY",                ← present
  "strategy":      "put_credit",         ← MISSING (required, no default)
  "expiration":    "2026-04-18",          ← MISSING (required, no default)
  "quantity":      1,                     ← MISSING (required, ge=1)
  "limit_price":   0.50,                 ← MISSING (required, gt=0)
  "legs": [
    { "strike": 550, "side": "SELL_TO_OPEN", "option_type": "put", "quantity": 1 }
  ],
  "time_in_force": "DAY",                ← MISSING (defaults to "DAY")
  "mode":          "paper"                ← MISSING (defaults to "paper")
}
```

**Fields present but not in schema** (ignored by Pydantic, but indicate intent mismatch):
- `class` — Tradier order field, not a preview parameter
- `type` — Tradier order field (credit/debit/market), not a preview parameter
- `duration` — Tradier order field, not a preview parameter
- `_meta` — TMC metadata, not a preview parameter

**FastAPI 422 response body:**
```json
{
  "detail": [
    { "loc": ["body", "strategy"], "msg": "field required", "type": "value_error.missing" },
    { "loc": ["body", "expiration"], "msg": "field required", "type": "value_error.missing" },
    { "loc": ["body", "quantity"], "msg": "field required", "type": "value_error.missing" },
    { "loc": ["body", "limit_price"], "msg": "field required", "type": "value_error.missing" }
  ]
}
```

---

## 3. Per-Strategy Status

| Strategy | Button? | JS Handler Fires? | Preview API Accepts? | Submit Works? | Error |
|---|:-:|:-:|:-:|:-:|---|
| put_credit_spread | ✅ | ✅ | ❌ 422 | — | Missing: strategy, expiration, quantity, limit_price |
| call_credit_spread | ✅ | ✅ | ❌ 422 | — | Same 422 |
| put_debit | ✅ | ✅ | ❌ 422 | — | Same 422 |
| call_debit | ✅ | ✅ | ❌ 422 | — | Same 422 |
| iron_condor | ✅ | ✅ | ❌ 422 | — | Same 422 + strategy not in STRATEGY_LITERAL |
| iron_butterfly | ✅ | ✅ | ❌ 422 | — | Same 422 + strategy not in STRATEGY_LITERAL |
| butterfly_debit | ✅ | ✅ | ❌ 422 | — | Same 422 |
| calendar_call_spread | ✅ | ✅ | ❌ 422 | — | Same 422 + strategy not in STRATEGY_LITERAL |
| calendar_put_spread | ✅ | ✅ | ❌ 422 | — | Same 422 + strategy not in STRATEGY_LITERAL |
| diagonal_call_spread | ✅ | ✅ | ❌ 422 | — | Same 422 + strategy not in STRATEGY_LITERAL |
| diagonal_put_spread | ✅ | ✅ | ❌ 422 | — | Same 422 + strategy not in STRATEGY_LITERAL |

**All strategies fail at the same point — the first `api.tradingPreview()` call in `_executeOptionsTrade()`.**

---

## 4. Root Causes

### 4.1 PRIMARY: `_executeOptionsTrade()` sends wrong payload shape to preview API

**Evidence:**
- [_executeOptionsTrade()](trade_management_center.js#L1561) builds a Tradier multileg order payload (class, type, duration, legs with OCC symbols)
- It sends this directly to `POST /api/trading/preview` at [line 1615](trade_management_center.js#L1615)
- The backend expects a `TradingPreviewRequest` Pydantic model ([models.py:63](models.py#L63)) with completely different fields
- 4 required fields with no defaults are missing: `strategy`, `expiration`, `quantity`, `limit_price`
- FastAPI rejects with HTTP 422 before any business logic runs
- The `.catch()` handler at [line 1626](trade_management_center.js#L1626) swallows the error silently (console.error only — no user feedback)

**Fix:** Remove the premature preview call. Open the TradeTicket modal directly with the raw candidate data. The modal's own `_doPreview()` → `toPreviewRequest()` pipeline already produces the correct payload shape.

```javascript
// BEFORE (broken): call preview API with wrong payload, then open modal
api.tradingPreview(orderPayload).then(function (preview) {
  BenTradeExecutionModal.open(orderPayload, preview);
});

// AFTER (correct): open modal directly with raw candidate
BenTradeExecutionModal.open(rawCandidate);
// Modal handles: normalize → validate → preview → confirm → submit
```

### 4.2 SECONDARY: Scanner side values "short"/"long" not in trade_ticket_model SIDE_MAP

**Evidence:**
- Scanner V2 candidates have `legs[].side = "short" | "long"` ([contracts.py:41](contracts.py#L41))
- [trade_ticket_model.js](trade_ticket_model.js#L56) `SIDE_MAP` maps: `sell`, `buy`, `sell_to_open`, `buy_to_open`, `sell_to_close`, `buy_to_close`
- `"short"` is not in `SIDE_MAP` → defaults to `"buy_to_open"` (WRONG — should be `"sell_to_open"`)
- `"long"` is not in `SIDE_MAP` → defaults to `"buy_to_open"` (correct by accident)

Currently masked because `_executeOptionsTrade()` pre-maps sides before building `orderPayload`. But once Fix 4.1 is applied (passing raw candidate to modal), this bug becomes live.

**Fix:** Add `short` and `long` to `SIDE_MAP`:
```javascript
var SIDE_MAP = {
  sell:          'sell_to_open',
  buy:           'buy_to_open',
  short:         'sell_to_open',   // ← ADD
  long:          'buy_to_open',    // ← ADD
  sell_to_open:  'sell_to_open',
  buy_to_open:   'buy_to_open',
  sell_to_close: 'sell_to_close',
  buy_to_close:  'buy_to_close',
};
```

### 4.3 TERTIARY: `STRATEGY_LITERAL` missing 5 strategy types

**Evidence:**
- Backend [models.py:10](models.py#L10) defines:
  ```python
  STRATEGY_LITERAL = Literal[
      "put_credit", "call_credit", "put_debit", "call_debit",
      "iron_condor", "butterfly_debit",
  ]
  ```
- Scanner produces 11 strategy_ids. After the `toPreviewRequest()` mapping ([trade_ticket_model.js:427](trade_ticket_model.js#L427)), these are NOT covered:
  - `iron_butterfly` → falls back to `"put_credit"` (WRONG)
  - `calendar_call_spread` → falls back to `"put_credit"` (WRONG)
  - `calendar_put_spread` → falls back to `"put_credit"` (WRONG)
  - `diagonal_call_spread` → falls back to `"put_credit"` (WRONG)
  - `diagonal_put_spread` → falls back to `"put_credit"` (WRONG)

**Fix:** Add missing strategies to both backend and frontend:

Backend `STRATEGY_LITERAL`:
```python
STRATEGY_LITERAL = Literal[
    "put_credit", "call_credit", "put_debit", "call_debit",
    "iron_condor", "iron_butterfly", "butterfly_debit",
    "calendar_call", "calendar_put",
    "diagonal_call", "diagonal_put",
]
```

Frontend `strategyMap` in `toPreviewRequest()`:
```javascript
iron_butterfly:        'iron_butterfly',
calendar_call_spread:  'calendar_call',
calendar_put_spread:   'calendar_put',
diagonal_call_spread:  'diagonal_call',
diagonal_put_spread:   'diagonal_put',
```

### 4.4 MINOR: No user feedback on preview failure

**Evidence:** The `.catch()` at [line 1626](trade_management_center.js#L1626) only does `console.error()`. No toast, no alert, no button state change indicating the error. User sees button momentarily say "Previewing…" then reset to "Execute Trade" with zero feedback.

**Fix:** Moot once Fix 4.1 is applied (the premature preview call is removed entirely). But if retained for any reason, should show user-visible error feedback.

---

## 5. Missing Components

### 5.1 Execute Trade button on options TradeCards?

**Status:** ✅ **Present**

Button is rendered in `buildOptionsTradeCard()` at [trade_management_center.js:1450](trade_management_center.js#L1450):
```html
<button type="button" class="btn btn-exec btn-action"
  data-action="execute"
  data-trade-key="..."
  title="Preview and execute this options trade">
  Execute Trade
</button>
```

Event delegation wired at [line 1193](trade_management_center.js#L1193):
```javascript
if (action === 'execute' && row) {
  _executeOptionsTrade(btn, tradeKey, row);
}
```

### 5.2 Candidate-to-order conversion function?

**Status:** ⚠️ **Exists but bypassed**

`trade_ticket_model.js` has a complete conversion pipeline:
- `normalize()` at [line 211](trade_ticket_model.js#L211): converts raw candidate → TradeTicketModel
- `toPreviewRequest()` at [line 424](trade_ticket_model.js#L424): converts TradeTicketModel → TradingPreviewRequest
- Handles strategy mapping, OCC construction, side normalization, limit price derivation

**Problem:** `_executeOptionsTrade()` in TMC builds its own Tradier-style payload instead of using this pipeline. The modal never opens, so `toPreviewRequest()` never runs.

### 5.3 OCC symbol construction?

**Status:** ✅ **Implemented correctly in two places**

1. [trade_management_center.js:1575](trade_management_center.js#L1575) — in `_executeOptionsTrade()`:
   ```javascript
   var yy = parts[0].slice(-2);
   var mm = parts[1];
   var dd = parts[2];
   var pc = optionType.charAt(0).toUpperCase();
   var strikeInt = Math.round(Number(leg.strike) * 1000);
   // → SPY260418P00550000  ✅ Correct OCC format
   ```

2. [trade_ticket_model.js:87](trade_ticket_model.js#L87) — `_buildOccSymbol()`:
   ```javascript
   // Same algorithm: SYMBOL + YYMMDD + P/C + 8-digit strike
   // Also validates: sym length ≤ 6, parts.length === 3, pc is P or C
   ```

3. Backend [order_builder.py:211](order_builder.py#L211) — `build_occ_symbol()`:
   ```python
   # Root uppercase 1-6 chars + YYMMDD + P/C + strike*1000 zero-padded 8 digits
   ```

Both JS implementations and the backend builder use the same correct OCC formula.

### 5.4 Preview/submit flow handle multileg correctly?

**Status:** ✅ **Backend fully supports 2-leg and 4-leg multileg orders**

- `TradingPreviewRequest` accepts `legs: list[PreviewLeg]` with 2-4 legs
- `OrderTicket` supports `legs: list[OrderLeg] = Field(min_length=2, max_length=4)`
- `build_tradier_multileg_order()` produces indexed `side[0]`, `option_symbol[0]`, `quantity[0]` format
- `TradierBroker.build_payload()` at [tradier_broker.py:56](tradier_broker.py#L56) iterates all legs
- Tradier API accepts up to 4 legs per multileg order

### 5.5 Calendar/diagonal cross-expiration support?

**Status:** ⚠️ **Partial**

- Scanner candidates for calendars/diagonals have different `expiration` per leg
- `PreviewLeg` does NOT have an `expiration` field — legs inherit the header `expiration`
- `OrderLeg` at [models.py:16](models.py#L16) DOES have `expiration: str` per leg
- `_build_legs_from_preview()` in [service.py](service.py#L196) may not correctly handle per-leg expirations
- Tradier's multileg API does support different expirations per leg (each OCC symbol encodes its own expiration)

**Risk:** Calendar/diagonal trades may preview with the wrong expiration on one leg. The OCC symbol would be correct (built from leg-specific expiration) but the chain lookup in `_build_legs_from_preview()` may only fetch the header expiration's chain.

---

## 6. Downstream Flow Verification (once fix applied)

Assuming Fix 4.1 is applied and the TradeTicket modal opens successfully, here's the remaining flow and its status:

| Step | Component | Status | Notes |
|---|---|---|---|
| Modal open | `trade_ticket.js:916` | ✅ | Normalizes via `trade_ticket_model.normalize()` |
| Validation | `trade_ticket_model.validate()` | ✅ | Checks OCC, legs, limit_price |
| Preview button | `_doPreview()` | ✅ | Calls `toPreviewRequest()` → `api.tradingPreview()` |
| Payload shape | `toPreviewRequest()` | ✅ | Correctly maps to `TradingPreviewRequest` schema |
| Backend preview | `service.preview()` | ✅ | Fetches chain, builds legs, risk checks |
| Order builder | `build_tradier_multileg_order()` | ✅ | Produces Tradier multileg format |
| Tradier preview | `broker.preview_raw_payload()` | ✅ | POST with `preview=true` |
| Confirm button | `_doSubmit()` | ✅ | Uses ticket_id + confirmation_token |
| Backend submit | `service.submit()` | ✅ | Token validation, credential routing |
| Tradier submit | `broker.place_order()` | ✅ | POST to `/v1/accounts/{id}/orders` |
| Reconciliation | `_doReconcile()` | ✅ | Polls order status |

**The downstream pipeline is structurally complete.** The only gap is the entry point.

---

## 7. Recommended Fixes (in priority order)

### Fix 1: Rewrite `_executeOptionsTrade()` to open modal directly (PRIMARY FIX)

**Impact:** Fixes ALL strategies
**Effort:** ~20 lines changed in `trade_management_center.js`

Replace the current `_executeOptionsTrade()` function. Instead of calling `api.tradingPreview()` with a wrong-shaped payload and then trying to open the modal on success, simply open the modal directly with the raw candidate data. The TradeTicket modal already has the complete normalize → validate → preview → confirm → submit pipeline.

**Current** (broken — [trade_management_center.js:1561](trade_management_center.js#L1561)):
```javascript
function _executeOptionsTrade(btn, tradeKey, rawCandidate) {
  var c = normalizeOptionsCandidate(rawCandidate);
  // ... 40 lines building Tradier-format orderPayload ...
  api.tradingPreview(orderPayload)       // ← WRONG SCHEMA → 422
    .then(function (preview) {
      BenTradeExecutionModal.open(orderPayload, preview);  // ← NEVER REACHED
    })
    .catch(function (err) {
      console.error(err);                // ← SILENT FAILURE
    });
}
```

**Fixed:**
```javascript
function _executeOptionsTrade(btn, tradeKey, rawCandidate) {
  if (!rawCandidate || !rawCandidate.legs || rawCandidate.legs.length === 0) {
    console.warn('[TMC] Cannot execute options trade: no legs on candidate');
    return;
  }
  // Open TradeTicket directly — it handles normalize, validate, preview, submit
  if (window.BenTradeExecutionModal && window.BenTradeExecutionModal.open) {
    window.BenTradeExecutionModal.open(rawCandidate);
  } else {
    console.error('[TMC] Execution modal not available');
  }
}
```

### Fix 2: Add "short"/"long" to SIDE_MAP in trade_ticket_model.js

**Impact:** Prevents wrong side assignment when scanner leg data flows to TradeTicket
**Effort:** 2 lines added

At [trade_ticket_model.js:56](trade_ticket_model.js#L56), add:
```javascript
var SIDE_MAP = {
  sell:          'sell_to_open',
  buy:           'buy_to_open',
  short:         'sell_to_open',    // ← ADD: scanner V2 uses "short"
  long:          'buy_to_open',     // ← ADD: scanner V2 uses "long"
  sell_to_open:  'sell_to_open',
  buy_to_open:   'buy_to_open',
  sell_to_close: 'sell_to_close',
  buy_to_close:  'buy_to_close',
};
```

### Fix 3: Add missing strategies to backend `STRATEGY_LITERAL` and frontend `strategyMap`

**Impact:** Enables execution for iron_butterfly, calendar, and diagonal strategies
**Effort:** Small — add entries to two files

Backend [models.py:10](models.py#L10):
```python
STRATEGY_LITERAL = Literal[
    "put_credit", "call_credit", "put_debit", "call_debit",
    "iron_condor", "iron_butterfly", "butterfly_debit",
    "calendar_call", "calendar_put",
    "diagonal_call", "diagonal_put",
]
```

Frontend [trade_ticket_model.js:427](trade_ticket_model.js#L427) `strategyMap`:
```javascript
iron_butterfly:        'iron_butterfly',
calendar_call_spread:  'calendar_call',
calendar_put_spread:   'calendar_put',
diagonal_call_spread:  'diagonal_call',
diagonal_put_spread:   'diagonal_put',
```

Backend `service.py` — update `_CREDIT_STRATEGIES` and `_DEBIT_STRATEGIES` sets to include the new strategy IDs for correct price_effect determination.

### Fix 4: Handle per-leg expiration for calendars/diagonals

**Impact:** Enables correct chain lookup for cross-expiration strategies
**Effort:** Medium — modify `_build_legs_from_preview()` in service.py

Currently `service.preview()` fetches one chain for `req.expiration`. For calendars/diagonals, legs have different expirations. The fix needs to:
1. Extract unique expirations from `req.legs`
2. Fetch chains for each unique expiration
3. Look up each leg's contract in the correct expiration's chain

---

## 8. Key Code Locations

| File | Lines | What |
|---|---|---|
| [trade_management_center.js](trade_management_center.js) | 1561-1640 | `_executeOptionsTrade()` — THE BREAK POINT |
| [trade_management_center.js](trade_management_center.js) | 1171-1200 | Event delegation for "execute" action |
| [trade_management_center.js](trade_management_center.js) | 1450-1470 | Execute Trade button HTML in card |
| [trade_management_center.js](trade_management_center.js) | 282-330 | `normalizeOptionsCandidate()` |
| [trade_ticket_model.js](trade_ticket_model.js) | 211-315 | `normalizeForTicket()` — correct normalizer |
| [trade_ticket_model.js](trade_ticket_model.js) | 424-498 | `toPreviewRequest()` — correct payload builder |
| [trade_ticket_model.js](trade_ticket_model.js) | 56-65 | `SIDE_MAP` — missing "short"/"long" |
| [trade_ticket.js](trade_ticket.js) | 700-760 | `_doPreview()` — correct preview flow |
| [trade_ticket.js](trade_ticket.js) | 765-860 | `_doSubmit()` — correct submit flow |
| [app.js](app.js) | 1-18 | `BenTradeExecutionModal` adapter |
| [client.js](client.js) | 417-435 | `tradingPreview()`, `tradingSubmit()` API calls |
| [models.py](models.py) | 10-14 | `STRATEGY_LITERAL` — missing 5 strategies |
| [models.py](models.py) | 63-80 | `TradingPreviewRequest` — what backend expects |
| [routes_trading.py](routes_trading.py) | 84-130 | Preview route handler |
| [service.py](service.py) | 264-549 | `TradingService.preview()` |
| [service.py](service.py) | 551-734 | `TradingService.submit()` |
| [tradier_broker.py](tradier_broker.py) | 56-81 | `build_payload()` — Tradier multileg format |
| [tradier_broker.py](tradier_broker.py) | 152-200 | `preview_raw_payload()` — Tradier preview call |
| [tradier_broker.py](tradier_broker.py) | 400-470 | `place_order()` — Tradier submit call |
| [order_builder.py](order_builder.py) | 60-191 | `build_tradier_multileg_order()` |
| [order_builder.py](order_builder.py) | 211-263 | `build_occ_symbol()` |
| [tradier_credentials.py](tradier_credentials.py) | | `resolve_tradier_credentials()` |

---

## 9. Risk Assessment

| Risk | Severity | Mitigation |
|---|---|---|
| Fix 1 changes trade execution entry point | Medium | `BenTradeExecutionModal.open()` is the standard pattern used by all other dashboards (stock scanner, strategy shell) — adopting same pattern in TMC |
| Fix 2 could affect existing side mappings | Low | Only adds new entries to SIDE_MAP — no existing entries changed |
| Fix 3 adds new strategy types to trading pipeline | Medium | Need to verify `_estimate_max_pnl()` handles 4-leg profit/loss correctly for iron_butterfly and calendars |
| Fix 4 multi-expiration chain fetch changes preview latency | Low | Only affects calendar/diagonal strategies, adds one extra chain fetch |
| Calendar/diagonal with Tradier multileg API | Medium | Tradier supports different expirations in multileg orders (each OCC symbol encodes its own date). Verify with sandbox testing |

---

## 10. Summary

**Root cause:** `_executeOptionsTrade()` in TMC sends a Tradier-format order payload directly to `/api/trading/preview`, but the backend expects a `TradingPreviewRequest` with different required fields. FastAPI rejects with HTTP 422. The error is silently swallowed. The TradeTicket modal (which has the correct pipeline) never opens.

**User experience:** Button flashes "Previewing…" → resets to "Execute Trade". No error message shown. Console shows `[TMC] Options trade preview failed: Error`.

**Fix complexity:** Fix 1 (rewrite entry point) is ~20 lines and unblocks all 6 supported strategies immediately. Fixes 2-3 are small additions. Fix 4 (calendar/diagonal support) is medium complexity and only needed for those 5 strategies.

**What works already:** The entire downstream pipeline — TradeTicket modal, normalize, validate, `toPreviewRequest()`, backend preview service, order builder, Tradier broker, credential routing, dry-run support, reconciliation — is fully implemented and structurally correct. The only disconnect is the entry point in TMC.
