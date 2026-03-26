# Trade Ticket Data Mapping Audit

**Date:** 2026-03-25  
**Status:** ROOT CAUSES IDENTIFIED — 3 critical mismatches found  
**Files analyzed:**
- `frontend/assets/js/pages/trade_management_center.js` — `_executeOptionsTrade()` (line 1561)
- `frontend/assets/js/models/trade_ticket_model.js` — `normalizeForTicket()` (line 193)
- `frontend/assets/js/ui/trade_ticket.js` — `open()` (line 914)
- `backend/app/workflows/options_opportunity_runner.py` — `_extract_compact_candidate()`

---

## Executive Summary

The TradeTicket modal receives the raw scanner candidate correctly, but `normalizeForTicket()` cannot extract most fields because of **3 critical mismatches** between the scanner output shape and what the normalizer searches for. All reported errors trace to these same 3 root causes.

---

## Data Flow Trace

```
User clicks "Execute" on options card
  → TMC _findOptionsRowByTradeKey(tradeKey)
      → returns raw API candidate from _optionsRenderedRows[]  (un-normalized)
  → _executeOptionsTrade(btn, tradeKey, rawCandidate)
      → guards: rawCandidate.legs is non-empty
      → BenTradeTradeTicket.open(rawCandidate)
          → source = rawCandidate  (no opts.rawTrade since TMC calls open(rawCandidate) directly)
          → _ticket = normalize(source)   ← BenTradeTradeTicketModel.normalizeForTicket()
          → _render()
```

**Key detail:** `_findOptionsRowByTradeKey` returns `_optionsRenderedRows[i]` — the **raw API object** from the backend. It is NOT the normalized TMC object from `normalizeOptionsCandidate()`. This raw object has `math: { ... }` nested metrics and `legs[].side = "short"/"long"` and NO `occ_symbol` on legs.

---

## ROOT CAUSE 1: `_dig()` does not search `raw.math`

### The problem

The backend scanner nests ALL math metrics under `candidate.math.*`:
```json
{
  "math": {
    "net_credit": 0.45,
    "max_profit": 45.0,
    "max_loss": -455.0,
    "pop": 0.72,
    "ev": 28.40,
    "ror": 0.099,
    "width": 5.0,
    "breakeven": [535.55],
    "ev_per_day": 1.23
  }
}
```

But `_dig()` in trade_ticket_model.js (line 167) searches these tiers only:
1. `raw.computed[key]`
2. `raw.computed_metrics[key]`
3. `raw.details[key]`
4. `raw[key]` (root)

**It NEVER searches `raw.math[key]`.** Since the scanner candidate has no `computed`, `computed_metrics`, or `details` sub-objects, and no root-level `net_credit`/`max_loss`/etc., every `_dig()` call returns `null`.

### Fields affected

| Field | `_dig()` lookup keys | Scanner location | Result |
|---|---|---|---|
| netPremium | `['net_credit']`, `['net_debit']` | `math.net_credit` | **null** |
| maxProfit | `['max_profit', 'max_profit_per_contract']` | `math.max_profit` | **null** |
| maxLoss | `['max_loss', 'max_loss_per_contract']` | `math.max_loss` | **null** |
| pop | `['pop', 'probability_of_profit']` | `math.pop` | **null** |
| ev | `['expected_value', 'ev']` | `math.ev` | **null** |
| ror | `['return_on_risk', 'ror', 'ev_to_risk']` | `math.ror` | **null** |
| midPrice | `['spread_mid', 'mid_price', 'mid']` | not present | **null** |
| naturalPrice | `['spread_natural', 'natural_price', 'natural']` | not present | **null** |

### Downstream impact

- `limitPrice` = `Math.abs(netPremium)` → null → falls to `Math.abs(midPrice)` → null → **limitPrice = null**
- Validation error: "Limit price must be a positive number" (the 0.01 seen in UI is likely a default min from the HTML input)
- All Risk & Reward fields show dashes
- All Pricing Context fields show dashes

### Fix

Add `raw.math` as a search tier in `_dig()`:

```javascript
function _dig(raw, keys) {
  for (var i = 0; i < keys.length; i++) {
    var v = null;
    if (raw.computed && raw.computed[keys[i]] != null)         v = toNum(raw.computed[keys[i]]);
    if (v != null) return v;
    if (raw.computed_metrics && raw.computed_metrics[keys[i]] != null) v = toNum(raw.computed_metrics[keys[i]]);
    if (v != null) return v;
    if (raw.math && raw.math[keys[i]] != null)                v = toNum(raw.math[keys[i]]);  // ← ADD
    if (v != null) return v;
    if (raw.details && raw.details[keys[i]] != null)           v = toNum(raw.details[keys[i]]);
    if (v != null) return v;
    v = toNum(raw[keys[i]]);
    if (v != null) return v;
  }
  return null;
}
```

Also handle `breakeven` which is resolved separately (line ~277): the code looks for `raw.breakevens` / `raw.breakeven` but the scanner puts it in `raw.math.breakeven`. Add:

```javascript
var be = raw.breakevens || raw.breakeven
  || (raw.math && (raw.math.breakevens || raw.math.breakeven))    // ← ADD
  || (raw.computed && raw.computed.breakevens)
  || (raw.details && (raw.details.breakevens || raw.details.break_even))
  || raw.break_even;
```

---

## ROOT CAUSE 2: OCC symbols not built when legs exist

### The problem

`_normalizeLegs()` has two paths:

1. **Legs ABSENT** (synthetic path): Builds OCC symbols via `_buildOccSymbol(symbol, expiration, strike, callput)` ✅
2. **Legs PRESENT** (real legs): Maps directly, reading `leg.occ_symbol || leg.option_symbol || leg.optionSymbol` — **never calls `_buildOccSymbol()`** ❌

The backend scanner legs do NOT include `occ_symbol`, `option_symbol`, or `optionSymbol`. They have:
```json
{ "strike": 535, "side": "short", "option_type": "put", "expiration": "2026-04-17", "bid": 0.25, "ask": 0.30 }
```

So `optionSymbol` resolves to `""` for every leg → validation error: "2 leg(s) missing OCC symbol — cannot execute."

### Fix

In the real-legs path of `_normalizeLegs()`, fall back to `_buildOccSymbol()` when no OCC is present:

```javascript
return rawLegs.map(function (leg) {
  var occ = String(leg.occ_symbol || leg.option_symbol || leg.optionSymbol || '');
  // Build OCC from components when not pre-built
  if (!occ) {
    var cp = String(leg.callput || leg.right || leg.option_type || '').toLowerCase();
    var exp = String(leg.expiration || header.expiration || '');
    occ = _buildOccSymbol(header.symbol, exp, toNum(leg.strike), cp);
  }
  return {
    side:          _normSide(leg.side),
    optionSymbol:  occ,
    expiration:    String(leg.expiration || header.expiration || ''),
    strike:        toNum(leg.strike) || 0,
    right:         String(leg.callput || leg.right || leg.option_type || 'put').toLowerCase(),
    quantity:      toNum(leg.qty || leg.quantity) || 1,
    bid:           toNum(leg.bid),
    ask:           toNum(leg.ask),
    mid:           toNum(leg.mid),
  };
});
```

---

## ROOT CAUSE 3: Strike derivation doesn't recognize "short"/"long" sides

### The problem

When `raw.short_strike` and `raw.long_strike` are absent (the scanner doesn't emit them), `normalizeForTicket()` tries to derive them from legs (line ~219):

```javascript
var side = String(leg.side || '').toLowerCase();
if (side === 'sell_to_open' || side === 'sell') {
  if (shortStrike == null) shortStrike = st;
} else if (side === 'buy_to_open' || side === 'buy') {
  if (longStrike == null) longStrike = st;
}
```

Scanner legs use `side: "short"` and `side: "long"`. These don't match any of the recognized values (`sell_to_open`, `sell`, `buy_to_open`, `buy`), so both `shortStrike` and `longStrike` remain null.

Note: `_normSide()` in the SIDE_MAP correctly maps `"short" → "sell_to_open"` and `"long" → "buy_to_open"`, but it's not used in the strike derivation path.

### Downstream impact

- `shortStrike` = null, `longStrike` = null
- `width` = null (depends on strikes)
- Falls through to synthetic leg path in some edge cases

### Fix

Add "short" and "long" to the side checks:

```javascript
var side = String(leg.side || '').toLowerCase();
if (side === 'sell_to_open' || side === 'sell' || side === 'short') {
  if (shortStrike == null) shortStrike = st;
} else if (side === 'buy_to_open' || side === 'buy' || side === 'long') {
  if (longStrike == null) longStrike = st;
}
```

---

## Full Field Mapping Table

| TradeTicket Field | Where normalize() looks | Scanner candidate actual path | Match? | Root Cause |
|---|---|---|---|---|
| underlying (symbol) | `input.symbol \|\| raw.symbol \|\| raw.underlying` | `candidate.symbol` or `candidate.underlying` | ✅ | — |
| strategyId | `input.strategyId \|\| input.strategy_id \|\| raw.strategy_id` | `candidate.strategy_id` | ✅ | — |
| expiration | `input.expiration \|\| raw.expiration` | `candidate.expiration` | ✅ | — |
| dte | `input.dte \|\| raw.dte` | `candidate.dte` | ✅ | — |
| underlyingPrice | `input.underlyingPrice \|\| raw.underlying_price` | `candidate.underlying_price` | ✅ | — |
| shortStrike | `raw.short_strike`, then derives from legs checking "sell"/"sell_to_open" | Legs with `side: "short"` | ❌ | RC3 |
| longStrike | `raw.long_strike`, then derives from legs checking "buy"/"buy_to_open" | Legs with `side: "long"` | ❌ | RC3 |
| width | `raw.width` or `abs(short - long)` | `candidate.math.width` (root width absent) | ❌ | RC1 + RC3 |
| netPremium | `_dig(raw, ['net_credit'])` / `_dig(raw, ['net_debit'])` | `candidate.math.net_credit` | ❌ | RC1 |
| limitPrice | derived from netPremium or midPrice | both null | ❌ | RC1 |
| maxProfit | `_dig(raw, ['max_profit', 'max_profit_per_contract'])` | `candidate.math.max_profit` | ❌ | RC1 |
| maxLoss | `_dig(raw, ['max_loss', 'max_loss_per_contract'])` | `candidate.math.max_loss` | ❌ | RC1 |
| pop | `_dig(raw, ['pop', 'probability_of_profit'])` | `candidate.math.pop` | ❌ | RC1 |
| ev | `_dig(raw, ['expected_value', 'ev'])` | `candidate.math.ev` | ❌ | RC1 |
| ror | `_dig(raw, ['return_on_risk', 'ror', 'ev_to_risk'])` | `candidate.math.ror` | ❌ | RC1 |
| breakevens | `raw.breakevens \|\| raw.breakeven \|\| computed.breakevens \|\| details.breakevens` | `candidate.math.breakeven` | ❌ | RC1 |
| midPrice | `pricing.spread_mid` or `_dig(raw, ['spread_mid', 'mid_price', 'mid'])` | Not present in scanner output | ❌ | No source (expected) |
| naturalPrice | `pricing.spread_natural` or `_dig(raw, ['spread_natural', 'natural_price', 'natural'])` | Not present in scanner output | ❌ | No source (expected) |
| iv | `_dig(raw, ['iv', 'implied_volatility'])` | Per-leg `legs[].iv` only, not root | ⚠️ | Minor |
| ivRank | `_dig(raw, ['iv_rank', 'iv_percentile'])` | Not present in scanner output | ❌ | No source |
| legs[].optionSymbol | `leg.occ_symbol \|\| leg.option_symbol \|\| leg.optionSymbol` | Not present — backend legs have no OCC | ❌ | RC2 |
| legs[].side | `_normSide(leg.side)` → SIDE_MAP maps "short"→"sell_to_open" | `leg.side = "short"/"long"` | ✅ | — |
| legs[].strike | `toNum(leg.strike)` | `leg.strike` | ✅ | — |
| legs[].right | `leg.callput \|\| leg.right \|\| leg.option_type` | `leg.option_type = "put"/"call"` | ✅ | — |
| legs[].bid | `toNum(leg.bid)` | `leg.bid` | ✅ | — |
| legs[].ask | `toNum(leg.ask)` | `leg.ask` | ✅ | — |
| legs[].mid | `toNum(leg.mid)` | Not present in scanner legs | ❌ | Minor — derivable |
| legs[].expiration | `leg.expiration \|\| header.expiration` | `leg.expiration` | ✅ | — |
| legs[].quantity | `leg.qty \|\| leg.quantity` | Not present (no qty field) → defaults to 1 | ✅ | Default ok |

---

## Error-to-Root-Cause Mapping

| Validation Error | Root Cause | Fix |
|---|---|---|
| "Limit price must be a positive number" | RC1: netPremium=null (math.net_credit not found), midPrice=null → limitPrice=null | Add `raw.math` to `_dig()` |
| "2 leg(s) missing OCC symbol — cannot execute" | RC2: `_normalizeLegs` doesn't build OCC for real legs | Add `_buildOccSymbol()` fallback in real-legs path |
| "Max loss is unavailable" | RC1: `_dig(raw, ['max_loss'])` → null, because it's at `math.max_loss` | Add `raw.math` to `_dig()` |
| "Breakeven not computed" | RC1: breakeven resolution doesn't search `raw.math.breakeven` | Add `raw.math` to breakeven resolution |
| "Spread mid price unavailable" | No source: scanner doesn't emit spread-level mid price | Compute from leg bids/asks in normalize |
| "Natural price unavailable" | No source: scanner doesn't emit spread-level natural price | Compute from leg bids/asks in normalize |

---

## Fix Priority

### P0 — Blocking (modal cannot execute without these)

1. **Add `raw.math` to `_dig()` search tiers** — Fixes net_credit, max_profit, max_loss, pop, ev, ror, width, limitPrice
2. **Build OCC symbols from components when not pre-built** — Fixes "2 leg(s) missing OCC symbol"
3. **Add `raw.math.breakeven` to breakeven resolution** — Fixes "Breakeven not computed"
4. **Add "short"/"long" to strike derivation side checks** — Fixes shortStrike/longStrike/width

### P1 — Important (quality / safety warnings)

5. **Compute spread mid/natural from leg-level bid/ask** — For a 2-leg credit spread: `mid = leg[sell].mid - leg[buy].mid`, `natural = leg[sell].bid - leg[buy].ask` (most conservative fill)
6. **Derive `width` from `raw.math.width` via `_dig()`** — Width is in `math.width`, will be found after fix #1

### P2 — Nice to have

7. Aggregate per-leg IV to a spread-level IV estimate
8. ivRank is not emitted by scanner — would need backend addition

---

## Debug Console Logs Added

Three `console.log()` statements have been added for runtime verification:

1. **`trade_management_center.js`** — Before `BenTradeTradeTicket.open()`:
   ```
   [TMC] Raw candidate passed to modal: { ... full JSON ... }
   ```

2. **`trade_ticket_model.js`** — Start of `normalizeForTicket()`:
   ```
   [TradeTicket] Input to normalize: { ... full JSON ... }
   ```

3. **`trade_ticket_model.js`** — End of `normalizeForTicket()`:
   ```
   [TradeTicket] Output from normalize: { ... full JSON ... }
   ```

These can be removed after the fix is validated.

---

## Files Modified (debug logs only)

| File | Change |
|---|---|
| `frontend/assets/js/pages/trade_management_center.js` | Added `console.log` before modal open in `_executeOptionsTrade()` |
| `frontend/assets/js/models/trade_ticket_model.js` | Added `console.log` at start and end of `normalizeForTicket()` |
