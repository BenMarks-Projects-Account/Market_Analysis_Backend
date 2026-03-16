# Calendar / Diagonal Spreads — V2 Scanner (Prompt 12)

## Supported Variants

| Strategy ID | Option Type | Strike Relationship | Multi-Expiry |
|---|---|---|---|
| `calendar_call_spread` | call | same strike | sell near, buy far |
| `calendar_put_spread` | put | same strike | sell near, buy far |
| `diagonal_call_spread` | call | different strike (within shift) | sell near, buy far |
| `diagonal_put_spread` | put | different strike (within shift) | sell near, buy far |

**Family key:** `calendars`  
**Registry:** `implemented=True`, 4 strategy IDs  

## Architecture

### Multi-Expiry (First-Class)

Calendars/diagonals are the first V2 family to span multiple expirations.
The `V2Candidate` already had `expiration_back` and `dte_back` fields (added during Prompt 3),
and the calendar family populates them:

- `expiration` / `dte` → near (front-month, short leg)
- `expiration_back` / `dte_back` → far (back-month, long leg)

### Narrowing Strategy

Uses **standard narrowing** with a wide DTE window (7–90 days), NOT multi-expiry narrowing.

Rationale: `narrow_expirations_multi()` has a priority issue where overlapping near/far
DTE windows cause contracts to always land in "near" (checked first), missing valid
calendar pairs like 20/35 DTE. Instead, all expirations in the 7–90 DTE window are
collected, and construction pairs them freely: every valid (near_exp, far_exp) where
`near_exp < far_exp` and DTE spread ≥ `min_dte_spread` (default 7).

### Leg Ordering

| Index | Side | Position | Leg |
|---|---|---|---|
| 0 | short | near (front-month) | Sell to open |
| 1 | long | far (back-month) | Buy to open |

### Construction Algorithm (Phase B)

1. Get all expiry buckets from narrowed universe.
2. Sort expirations chronologically.
3. For each `(near_exp, far_exp)` pair where `near < far` and DTE spread ≥ `min_dte_spread`:
   - **Calendar:** find strikes present in BOTH expirations → one candidate per shared strike.
   - **Diagonal:** cross-product of near and far strikes with `|near_strike − far_strike| ≤ max_strike_shift` (default $10, same-strike pairs excluded — those are calendars).
4. Generation cap (default 50,000) prevents combinatorial explosion.

## Scanner-Time Metrics

### Trustworthy (Computed from Leg Quotes)

| Metric | Formula | Notes |
|---|---|---|
| `net_debit` | `far_leg.ask − near_leg.bid` | Debit paid to open |
| `max_loss` | `net_debit × 100` | Approximate — actual max loss ≈ debit paid |
| `width` | `\|far_strike − near_strike\|` | Diagonals only; None for calendars |

### Deferred / Informational (Set to None with Notes)

| Metric | Why Deferred |
|---|---|
| `max_profit` | Path-dependent: depends on far-leg residual value at near-leg expiration and underlying price at that point |
| `breakeven` | Depends on IV term structure; no closed-form for time spreads |
| `POP` | Delta approximation doesn't work for multi-expiry time spreads |
| `EV` | Requires max_profit (unknown at scanner time) |
| `RoR` | Requires max_profit (unknown at scanner time) |

**Philosophy:** Trustworthy limited output > fake exactness.

## Reason Codes

| Code | Category | Description |
|---|---|---|
| `v2_cal_invalid_geometry` | structural | Calendar/diagonal geometry invalid |

Checked conditions: leg count (2), same option type, short+long sides, different expirations,
temporal ordering (short=near < long=far), strike relationship (same for calendar, different for diagonal).

## Base Scanner Change

Added `require_same_expiry: bool = True` class attribute to `BaseV2Scanner`, passed to
`phase_c_structural_validation()`. All existing families keep the default (True).
CalendarsV2Scanner sets `require_same_expiry = False` so multi-expiry candidates
are not rejected by shared structural checks.

## What Was NOT Recreated from Legacy

- No legacy calendar scanner code was ported. This is a clean V2 implementation.
- No fake max_profit/breakeven/POP formulas. Legacy may have used approximations
  that produced misleading numbers for time spreads.
- No multi-expiry narrowing split (`narrow_expirations_multi`). Standard narrowing
  with free pairing is simpler and more correct.

## Files Modified

| File | Change |
|---|---|
| `app/services/scanner_v2/families/calendars.py` | Full implementation (~450 lines) |
| `app/services/scanner_v2/base_scanner.py` | Added `require_same_expiry` class attribute |
| `app/services/scanner_v2/diagnostics/reason_codes.py` | Added `REJECT_CAL_INVALID_GEOMETRY` |
| `app/services/scanner_v2/registry.py` | Updated strategy IDs + `implemented=True` |
| `app/services/scanner_v2/validation/tolerances.py` | Added calendars tolerances |
| `tests/test_v2_calendars.py` | 67 comprehensive tests |
| `tests/test_v2_diagnostics.py` | Count 27→28 + expected code set |
| `tests/test_v2_iron_condors.py` | Count 27→28 |

## Test Coverage

67 tests covering: construction (calendar + diagonal), structural checks,
math (trustworthy + deferred), math verification integration, hygiene/dedup,
end-to-end pipeline, reason codes, registry, dedup key, informational notes.
