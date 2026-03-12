# Butterfly Family — V2 Scanner Architecture

**Status**: Implemented (Prompt 11)  
**Scanner version**: 2.0.0  
**Family key**: `butterflies`  
**Strategy IDs**: `butterfly_debit`, `iron_butterfly`  

## Variants

### Debit Butterfly (`butterfly_debit`, 3 legs)
- All legs share the same `option_type` (call or put).
- **Leg ordering**: `[long_lower, short_center, long_upper]`
- Center body is sold 2× (implicit in pricing formula, not modeled as separate legs since V2Leg has no quantity field).
- Both call and put variants generated unless `context.option_side` filters.

**Geometry**: `lower < center < upper`, `center = (lower + upper) / 2` (symmetric wings).

**Pricing**:
- `net_debit = ask(lower) + ask(upper) − 2 × bid(center)`
- `max_profit = (width − net_debit) × 100`
- `max_loss = net_debit × 100`
- `breakevens = [lower + debit, upper − debit]`
- `POP ≈ |Δ_lower| − |Δ_upper|` (calls) or `|Δ_upper| − |Δ_lower|` (puts)
  - Source: `delta_approx` — covers full strike range, overestimates actual profit zone.

### Iron Butterfly (`iron_butterfly`, 4 legs)
- Center straddle (short put + short call at same strike) plus equidistant wings.
- **Leg ordering**: `[long_put_lower, short_put_center, short_call_center, long_call_upper]`

**Geometry**: Center put and call share the same strike. `center − lower = upper − center`.

**Pricing**:
- `net_credit = bid(ps) + bid(cs) − ask(pl) − ask(cl)`
- `max_profit = net_credit × 100`
- `max_loss = (width − net_credit) × 100`
- `breakevens = [center − credit, center + credit]`
- `POP ≈ 1 − |Δ_ps| − |Δ_cs|` (same formula as iron condor)

## Construction Algorithm (Phase B)

### Debit Butterfly
For each expiry bucket, for each `option_type` (call/put):
1. Collect all strikes of that option type → `strike_map`
2. For each pair `(i, k)` where `k > i + 1`:
   - Compute `center_needed = (strikes[i] + strikes[k]) / 2`
   - If `center_needed` exists in strike set → valid symmetric triplet
3. Filter by `max_wing_width` (default: 50.0)
4. Respect `generation_cap` (default: 50,000)

Complexity: O(N²) per expiry × option_type.

### Iron Butterfly
For each expiry bucket:
1. Build `put_map` and `call_map` from contracts
2. Center candidates = strikes with both put AND call
3. For each center, enumerate lower puts:
   - `width = center − lower_strike`
   - `upper_needed = center + width` (must exist in call_map)
4. Filter by `max_wing_width`, respect `generation_cap`

## Structural Checks (Phase C)

### Debit Butterfly (3-leg)
| Check | Rule |
|---|---|
| `bf_leg_count` | Exactly 3 legs |
| `bf_option_type` | All same option_type |
| `bf_side_balance` | 2 long + 1 short |
| `bf_center_is_short` | Middle strike is the short leg |
| `bf_symmetry` | `center = (lower + upper) / 2` within 0.01 |

### Iron Butterfly (4-leg)
| Check | Rule |
|---|---|
| `bf_leg_count` | Exactly 4 legs |
| `bf_type_balance` | 2 puts + 2 calls |
| `bf_sides` | Center legs short, wing legs long |
| `bf_center_match` | `put_short.strike == call_short.strike` |
| `bf_symmetry` | `put_width == call_width` within 0.01 |
| `bf_strike_ordering` | `pl < ps ≤ cs < cl` |

Rejection code: `v2_bf_invalid_geometry`

## Math Verification Extensions (Phase E)

The following `math_checks.py` functions were extended for butterflies:

| Function | 3-leg Debit Path | 4-leg Iron Path |
|---|---|---|
| `verify_width` | `center − lower` | Reuses iron condor path (`family_key in ("iron_condors", "butterflies")`) |
| `verify_net_credit_or_debit` | `ask(l) + ask(u) − 2×bid(c)` | Reuses iron condor path |
| `verify_breakeven` | `[lower + debit, upper − debit]` | Reuses iron condor path |

## Shared Infrastructure Reuse

| Phase | Mechanism |
|---|---|
| A (Narrowing) | `narrow_chain()` — same DTE/strike narrowing |
| C (Structural) | `run_shared_structural_checks()` + family hook |
| D (Quote/Liquidity) | `phase_d_quote_liquidity_sanity()` — shared |
| D2 (Hygiene) | `run_quote_sanity()`, `run_liquidity_sanity()`, `run_dedup()` — shared |
| E (Math) | `run_math_verification()` — extended for butterfly paths |
| F (Normalize) | `phase_f_normalize()` — shared |

## Key Design Decisions

1. **No hidden threshold multipliers** — unlike legacy 0.2× OI/volume multiplier, V2 uses shared hygiene without butterfly-specific relaxation.
2. **2× center body modeled in pricing only** — V2Leg has no quantity field; the 2× multiplier is a formula detail in `family_math`.
3. **POP overestimates documented** — delta approximation covers full strike range, not just narrower profit zone between breakevens.
4. **Iron butterfly reuses iron condor math verification** — both are 4-leg structures with identical net_credit/breakeven formulas.

## Files Modified/Created

| File | Change |
|---|---|
| `scanner_v2/families/butterflies.py` | Full implementation (was skeleton) |
| `scanner_v2/diagnostics/reason_codes.py` | Added `REJECT_BF_INVALID_GEOMETRY` |
| `scanner_v2/validation/math_checks.py` | Extended 3 verify functions for butterfly paths |
| `scanner_v2/registry.py` | Set `implemented=True` |
| `tests/test_v2_butterflies.py` | 75 comprehensive tests |
| `tests/test_v2_diagnostics.py` | Updated count 26→27, added BF code to expected set |
| `tests/test_v2_iron_condors.py` | Updated reject count 26→27 |
