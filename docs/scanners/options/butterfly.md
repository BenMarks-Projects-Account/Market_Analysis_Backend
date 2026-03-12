# Butterfly Scanner

> **Scanner key:** `butterfly_debit`
> **Plugin ID:** `butterflies`
> **Plugin class:** `ButterfliesStrategyPlugin` in `app/services/strategies/butterflies.py`
> **Registered in:** `pipeline_scanner_stage.py` line 483

---

## 1. Purpose

Scans option chains for butterfly spreads — three-leg (debit butterflies) or four-leg (iron butterflies) structures that profit when the underlying stays near a center strike at expiration. Supports debit call butterflies, debit put butterflies, and iron butterflies.

The scanner key `butterfly_debit` maps to the `butterflies` plugin, which internally handles all butterfly variants via the `butterfly_type` and `option_side` payload parameters.

**Target underlyings:** SPY, QQQ, IWM, DIA, XSP, RUT, NDX (default symbols).

---

## 2. End-to-End Flow

### Phase 1: `build_candidates()`

1. **Parse inputs** — Extract from payload:
   - `butterfly_type`: `"debit"` | `"iron"` | `"both"` (default: `"debit"`)
   - `option_side`: `"call"` | `"put"` | `"both"` (default: `"call"`)
   - `center_mode`: `"spot"` | `"forecast"` | `"expected_move"` (default: `"spot"`)
   - `width`: optional explicit wing width
   - `max_candidates` from `_generation_cap` (default: 20,000)
2. **Per-snapshot loop:**
   - Validate: symbol, expiration, underlying_price, dte > 0, non-empty contracts.
   - Build separate strike maps for calls and puts (keyed by strike price, favoring higher OI on duplicates).
   - **IV estimation:** Sample IV from first 25 call + 25 put contracts, take arithmetic mean.
   - **Realized vol:** Requires ≥25 price history points, ≥12 log-returns. Annualize via `pstdev × √252`.
   - **Expected move:** `spot × vol × √(dte/365)`. Floor: `max(spot×0.02, 1.0)` if vol unavailable, minimum `0.5`.
   - **Step size:** Minimum nonzero difference between sorted strikes, floored at `0.5`.
   - **Width candidates:** If user specifies `width`, use `[width]`. Otherwise: `[1×step, 2×step, 5×step]`.
   - **Sides/types expansion:** Cartesian product of butterfly_type × option_side.
   - **Center strike selection** via `center_mode`:
     - `"spot"` → ATM (nearest to spot)
     - `"expected_move"` → `spot + EM` (calls) or `spot - EM` (puts)
     - `"forecast"` → drift-projected spot clamped to `±1.25 × EM`
   - **Debit butterfly construction (3 legs):**
     - Lower = nearest(strikes, center - width)
     - Upper = nearest(strikes, center + width)
     - Guard: lower < center < upper
     - All 3 legs from same strike map (call or put)
   - **Iron butterfly construction (4 legs):**
     - Put long at lower, put short + call short at center, call long at upper
     - Mixed maps: puts from put_map, calls from call_map
3. **Safety cap** at `max_candidates`.

### Phase 2: `enrich()`

This is the most complex phase (~475 lines). Two completely separate pricing branches for debit vs iron:

#### Debit butterfly pricing:
1. Extract bid/ask for 3 legs.
2. Per-leg mid = `(bid + ask) / 2`.
3. `spread_mid = mid(lower) + mid(upper) - 2×mid(center)` (1×2×1 structure).
4. `spread_natural = ask(lower) + ask(upper) - 2×bid(center)` (worst-case fill).
5. `total_debit = spread_mid` (preferred) or `spread_natural` (fallback).
6. Sanity: `execution_invalid = True` if debit ≤ 0 or debit ≥ wing_width.

#### Iron butterfly pricing:
- 4-leg credit structure: `total_credit = bid(shorts) - ask(longs)`.
- Mirror logic with inverted credit/debit semantics.

#### Common enrichment (both types):
1. **Max profit/loss:**
   - Debit: `max_profit = (wing_width - debit) × 100`, `max_loss = debit × 100`
   - Iron: `max_profit = credit × 100`, `max_loss = (wing_width - credit) × 100`
2. **Breakevens:** `break_even_low = lower + debit`, `break_even_high = upper - debit`
3. **Greeks:** `net_gamma = gamma(lower) + gamma(upper) - 2×gamma(center)`, same for theta.
4. **Payoff function:** `payoff_at(price) = (max(0, wing_width - |price - center|) - debit) × 100`
5. **Cost efficiency:** `max_profit / max_loss`
6. **POP:** Normal CDF model — `POP = Φ(z_high) - Φ(z_low)` where `z = (breakeven - spot) / expected_move`
7. **Expected value:** Numerical integration over 201 points spanning `±4 × expected_move`, Gaussian-weighted.
8. **Center alignment:** `clamp(1 - |spot - center| / (1.25 × expected_move))`
9. **Debit % of width:** `total_debit / wing_width`
10. **Liquidity scoring:** `0.45×oi_score + 0.30×vol_score + 0.25×spread_score`
11. **Rank score:**
    ```
    rank_score = clamp(
        0.30 × efficiency_score       # cost_efficiency / 2.0
      + 0.22 × center_alignment
      + 0.22 × liquidity_score
      + 0.12 × ev_score
      + 0.14 × gamma_peak_score      # |net_gamma| / 0.08
      - 0.15 × debit_vs_em_penalty   # (debit_vs_em - 0.45) / 0.65
      - 0.14 × pop_penalty           # (0.30 - pop) / 0.30
      - 0.08 × time_decay_risk       # max(0, -net_theta) / 0.08
    )
    ```
12. Apply `apply_expected_fill()`.

### Phase 3: `evaluate()`

Sequential gates:

| # | Gate | Threshold Source | Reason Code |
|---|------|-----------------|-------------|
| 1 | Execution validity | enrichment flag | `execution_invalid:{reason}` |
| 2 | Spread pricing available | enrichment | `pricing_unavailable` |
| 3 | Debit sanity | enrichment | `non_positive_debit`, `debit_ge_width` |
| 4 | Required metrics present | enrichment | `METRICS_MISSING:{field}` |
| 5 | Debit % of width ≤ `max_debit_pct_width` | **configurable** (default 0.60) | `BUTTERFLY_DEBIT_TOO_LARGE` |
| 6 | Liquidity (OI+vol floor) | **configurable** (applied at 20%!) | `liquidity_open_interest_low` |
| 7 | Liquidity score ≥ 0.15 | **hardcoded** | `liquidity_score_low` |
| 8 | Bid-ask spread % ≤ `max_bid_ask_spread_pct` | **configurable** (default 2.5) | `bid_ask_too_wide` |
| 9 | Worst leg spread ≤ `max_worst_leg_spread` | **configurable** (default 1.5) | `worst_leg_too_wide` |
| 10 | Max profit > 0 | enrichment | `no_profit_zone` |
| 11 | Cost efficiency ≥ `min_cost_efficiency` | **configurable** (default 2.0) | `cost_efficiency_below_floor` |
| 12 | POP ≥ `min_pop` | **configurable** (default 0.04) | `pop_below_threshold` |
| 13 | EV ≥ `min_expected_value` | **configurable** | `expected_value_below_threshold` |
| 14 | EV-to-risk ≥ `min_ev_to_risk` | **configurable** | `ev_to_risk_below_threshold` |

---

## 3. Data Inputs

| Input | Source | Used For |
|-------|--------|----------|
| `snapshots[]` | Tradier chain data per (symbol, expiration) | All per-symbol work |
| `snapshot.contracts[]` | Tradier option chain | Strike/leg selection |
| `snapshot.underlying_price` | Tradier | Spot price, center targeting, POP |
| `snapshot.dte` | Derived from expiration | Expected move, POP z-scores |
| `snapshot.prices_history[]` | Tradier/Polygon candles | Realized vol, forecast center mode |
| Contract `.bid`, `.ask`, `.strike`, `.option_type`, `.open_interest`, `.volume`, `.gamma`, `.theta`, `.iv` | Tradier | Pricing, greeks, liquidity |

---

## 4. Preset / Strictness Levels

| Parameter | Strict | Conservative | Balanced | Wide |
|-----------|--------|-------------|----------|------|
| `dte_min` | 7 | 7 | 7 | 3 |
| `dte_max` | 21 | 30 | 45 | 60 |
| `width_min` | 2.0 | 2.0 | 1.0 | 0.5 |
| `width_max` | 10.0 | 10.0 | 15.0 | 20.0 |
| `max_candidates` | 200 | 260 | 400 | 800 |
| `min_open_interest` | 1000 | 500 | 300 | 50 |
| `min_volume` | 100 | 50 | 20 | 5 |
| `max_bid_ask_spread_pct` | 1.0 | 1.5 | 2.0 | 3.0 |
| `min_pop` | 0.08 | 0.06 | 0.04 | 0.02 |
| `min_ev_to_risk` | 0.01 | 0.005 | -0.01 | -0.05 |
| `min_expected_value` | 0.0 | 0.0 | -10.0 | -50.0 |
| `min_cost_efficiency` | 2.0 | 1.5 | 1.0 | 0.5 |
| `max_debit_pct_width` | 0.35 | 0.45 | 0.60 | 0.80 |
| `data_quality_mode` | strict | balanced | balanced | lenient |

---

## 5. Configuration Parameters

| Parameter | Default | Valid Values |
|-----------|---------|-------------|
| `butterfly_type` | `"debit"` | `"debit"`, `"iron"`, `"both"` |
| `option_side` | `"call"` | `"call"`, `"put"`, `"both"` |
| `center_mode` | `"spot"` | `"spot"`, `"forecast"`, `"expected_move"` |
| `width` | auto (1/2/5 × step) | Explicit wing width in dollars |
| `preset` | `"balanced"` | `"strict"`, `"conservative"`, `"balanced"`, `"wide"`, `"manual"` |
| `symbols` | DEFAULT_SCANNER_SYMBOLS | Override target symbols |
| `max_expirations_per_symbol` | 4 | Max expirations per symbol |
| All threshold params | per preset | See preset table above |

---

## 6. Output Contract

```
strategy ("butterflies"), spread_type, butterfly_type, option_side,
underlying, symbol, expiration, dte, underlying_price,
center_strike, lower_strike, upper_strike,
short_strike (=center), long_strike (=lower),
wing_width,
break_even_low, break_even_high, break_evens_low, break_evens_high, break_even,
spread_mid, spread_natural, spread_mark,
net_debit|net_credit, total_debit|total_credit,
max_profit, max_profit_per_contract,
max_loss, max_loss_per_contract,
peak_profit_at_center, payoff_slope,
execution_invalid, execution_invalid_reason, readiness,
probability_of_touch_center (always None),
pop_butterfly, p_win_used, pop_model_used,
expected_value, ev_per_contract, ev_per_share, ev_to_risk,
cost_efficiency, return_on_risk, debit_pct_of_width, debit_vs_expected_move,
gamma_peak_score, time_decay_risk,
liquidity_score, center_alignment,
worst_leg_spread, open_interest, volume, bid_ask_spread_pct,
rank_score, trade_key, expected_move,
contractsMultiplier (100), selection_reasons[],
tie_breaks {edge, liquidity, conviction}
```

---

## 7. Complexity Analysis

| Issue | Severity | Description |
|-------|----------|-------------|
| **Dual-structure branching in enrich** | **HIGH** | Two ~130-line pricing branches for debit vs iron butterflies share the same POP model despite different risk profiles. Iron butterfly (credit structure) uses the same normal-CDF approach designed for debit butterflies. |
| **OI/vol gate applies 0.2× hidden multiplier** | **HIGH** | `evaluate()` silently reduces preset OI/vol thresholds by 80%. A preset `min_open_interest=1000` actually enforces min 200 (floored at 5). Undocumented. |
| **Hardcoded defaults shadow presets** | Medium | `evaluate()` has fallback defaults (e.g., `min_cost_efficiency=2.0`, `min_pop=0.04`) that don't match any single preset. Creates a "shadow manual tier." |
| **`probability_of_touch_center` always None** | Medium | Field emitted but never computed. UI must handle null. |
| **Rank score weights sum > 1.0** | Medium | Positive weights = 1.00, negative weights = 0.37. A maximally bad candidate can go negative before clamping masks it. |
| **`spread_natural` inverted semantics** | Medium | For debit butterflies, `spread_natural` = most you'd pay (buy wings at ask, sell body at bid). For iron butterflies, = most you'd receive. Same field name, opposite meaning. |
| **Output field duplication** | Medium | Many 2-3× aliases: `break_even_low`/`break_evens_low`/`break_even`, `max_profit`/`max_profit_per_contract`, `pop_butterfly`/`p_win_used`, `expected_value`/`ev_per_contract`. |
| **`long_strike = lower` is arbitrary** | Low | Canonical 2-leg aliases (`short_strike=center`, `long_strike=lower`) map a 3-leg structure into a 2-leg schema. Upper wing is also long — this could confuse consumers. |
| **Width search only produces 3 widths** | Low | Auto-widths are `[1×step, 2×step, 5×step]`. Gaps at 3× and 4× may miss good structures. |
| **Forecast drift not annualized** | Low | Uses `exp(drift × dte)` where drift = average of last 15 log-returns. For large DTE, the exponential can project far, only constrained by ±1.25×EM clamp. |

---

## 8. Simplification Recommendations

1. **Split debit and iron butterfly plugins** — The dual-structure branching in `enrich()` is the largest complexity source. Iron butterflies are credit structures with fundamentally different risk profiles. Splitting into separate plugins would halve the per-plugin complexity and allow proper POP models per structure.
2. **Remove the 0.2× OI/vol multiplier** — Either enforce the preset value directly or document/expose the relaxation factor as a configurable parameter. Hidden multipliers violate the "presets must be verifiable" principle.
3. **Eliminate hardcoded defaults in `evaluate()`** — Ensure every threshold goes through preset resolution. Remove fallback defaults that create a shadow tier.
4. **Remove `probability_of_touch_center`** — It's always None. Remove from output until a barrier model is implemented.
5. **Move quality gates downstream** — Under the new philosophy, cost_efficiency, POP, EV, and debit_pct gates should move to ranking/selection stages. Keep only structural checks (pricing valid, debit > 0, debit < width) in the scanner.
6. **Clean up field aliases** — Standardize on canonical names and remove backward-compat duplicates.
7. **Fix `long_strike` mapping** — Either remove the 2-leg canonical aliases or document that `long_strike` = lower wing (arbitrary choice for butterflies).
