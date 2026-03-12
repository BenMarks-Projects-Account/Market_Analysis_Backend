# Credit Spread Scanner

> **Scanner keys:** `put_credit_spread`, `call_credit_spread`
> **Plugin ID:** `credit_spread`
> **Plugin class:** `CreditSpreadStrategyPlugin` in `app/services/strategies/credit_spread.py`
> **Registered in:** `pipeline_scanner_stage.py` lines 480–481

---

## 1. Purpose

Scans option chains for vertical credit spreads — selling a closer-to-the-money option and buying a further-OTM option as protection. Both put credit spreads (bull put) and call credit spreads (bear call) route to the same plugin.

**Target underlyings:** SPY, QQQ, IWM, DIA, XSP, RUT, NDX (default symbols).

---

## 2. Known Bug — Call Credit Spreads Never Constructed

`build_candidates()` filters contracts to `option_type == "put"` unconditionally. The `call_credit_spread` scanner key is registered and dispatches to this plugin, but no call-side candidates are ever built. This means the `call_credit_spread` scanner always returns zero results.

**Impact:** The call_credit_spread scanner key exists in the UI and pipeline registry but is functionally dead. Any call credit spread results in the system would require this to be fixed.

---

## 3. End-to-End Flow

### Phase 1: `build_candidates()`

1. **Parse inputs** — Extract `distance_mode`, `width`, `distance_target`, `expected_move_multiple` from payload.
2. **Per-snapshot loop** — For each (symbol, expiration) snapshot:
   - Validate: symbol, expiration, underlying_price, dte > 0, non-empty contracts.
   - Filter contracts to `option_type == "put"` (bug: should also handle `"call"`).
   - Compute expected move: `spot × IV × √(dte/365)`.
   - Compute OTM distance window from `distance_min`/`distance_max` config.
   - Filter to OTM strikes within the distance window.
   - **Build pairs:** For each short strike, find valid long strikes that produce a spread width within `[width_min, width_max]`.
   - Apply credit pre-validation: `short_leg.bid - long_leg.ask > 0` (i.e., natural credit > 0).
   - Apply second credit pre-check: short-leg mid ≥ minimum threshold.
   - Emit candidate dicts with raw leg references.
3. **Safety cap** — Stop at `max_candidates` (per-preset limit).

### Phase 2: `enrich()`

For each candidate:
1. Extract bid/ask for both legs.
2. Compute spread pricing:
   - `spread_mid = short_mid - long_mid`
   - `spread_natural = short_bid - long_ask` (worst-case fill)
   - `net_credit` = primary pricing value
3. Compute max profit/loss:
   - `max_profit = net_credit × 100` (per contract)
   - `max_loss = (width - net_credit) × 100`
4. Compute breakeven: `short_strike - net_credit` (for puts)
5. Compute POP via normal CDF: `POP = Φ(z)` where `z = (breakeven - spot) / expected_move`
6. Compute expected value: `EV = POP × max_profit - (1-POP) × max_loss`
7. Compute return on risk: `RoR = net_credit / (width - net_credit)`
8. Compute liquidity score from OI, volume, and bid-ask spread.
9. Compute rank score (see Ranking section).
10. Apply `apply_expected_fill()` for fill estimates.

### Phase 3: `evaluate()`

Sequential gate filters. Each rejected trade gets reason codes appended:

| # | Gate | Threshold Source | Reason Code |
|---|------|-----------------|-------------|
| 1 | Execution validity | enrichment flag | `execution_invalid:{reason}` |
| 2 | Spread pricing available | enrichment | `pricing_unavailable` |
| 3 | Net credit > 0 | enrichment | `non_positive_credit` |
| 4 | Credit < width | enrichment | `credit_ge_width` |
| 5 | Required metrics present | enrichment | `METRICS_MISSING:{field}` |
| 6 | POP ≥ `min_pop` | **configurable** | `pop_below_threshold` |
| 7 | EV-to-risk ≥ `min_ev_to_risk` | **configurable** | `ev_to_risk_below_threshold` |
| 8 | RoR ≥ `min_ror` | **configurable** | `ror_below_threshold` |
| 9 | Bid-ask spread % ≤ `max_bid_ask_spread_pct` | **configurable** | `bid_ask_too_wide` |
| 10 | OI ≥ `min_open_interest` | **configurable** | `liquidity_open_interest_low` |
| 11 | Volume ≥ `min_volume` | **configurable** | `liquidity_volume_low` |

### Phase 4: `score()`

Returns pre-computed `rank_score` from enrich phase, plus tie-break dict.

---

## 4. Data Inputs

| Input | Source | Used For |
|-------|--------|----------|
| `snapshots[]` | Tradier chain data per (symbol, expiration) | All per-symbol work |
| `snapshot.contracts[]` | Tradier option chain | Strike/leg selection |
| `snapshot.underlying_price` | Tradier | Spot price, distance calc, POP |
| `snapshot.dte` | Derived from expiration | Expected move, POP z-scores |
| `snapshot.prices_history[]` | Tradier/Polygon candles | Realized vol fallback |
| Contract: `.bid`, `.ask`, `.strike`, `.option_type`, `.open_interest`, `.volume`, `.iv`, `.delta` | Tradier | Pricing, greeks, liquidity |
| `request` (payload) | Frontend/API | All threshold overrides, preset selection |

---

## 5. Candidate Construction Details

- **OTM distance window:** Strikes filtered to `[distance_min, distance_max]` as a fraction of underlying price (e.g., 0.03–0.08 = 3–8% OTM).
- **Width matching:** Valid long strikes must produce a width in `[width_min, width_max]` dollars.
- **Credit pre-validation:** Two checks before a candidate is created:
  1. `short_bid - long_ask > 0` (natural credit positive)
  2. `short_mid ≥ minimum threshold` (not a penny spread)
- **Expected move multiple:** `expected_move_multiple` scales the expected move used for distance calculations, not for POP/EV.

---

## 6. Ranking

```
rank_score = clamp(
    0.30 × edge_score        # net_credit / width  (normalized)
  + 0.22 × ror_score         # return on risk
  + 0.20 × pop_score         # probability of profit
  + 0.18 × liquidity_score   # composite OI/vol/spread
  + 0.10 × tqs_score         # trade quality score
)
```

Tie-breaks: `{edge, ror, pop, liquidity}`.

---

## 7. Preset / Strictness Levels

| Parameter | Strict | Conservative | Balanced | Wide |
|-----------|--------|-------------|----------|------|
| `dte_min` | 14 | 14 | 7 | 3 |
| `dte_max` | 30 | 30 | 45 | 60 |
| `expected_move_multiple` | 1.2 | 1.0 | 1.0 | 0.8 |
| `width_min` | 3.0 | 3.0 | 1.0 | 1.0 |
| `width_max` | 5.0 | 5.0 | 5.0 | 10.0 |
| `distance_min` | 0.03 | 0.03 | 0.01 | 0.01 |
| `distance_max` | 0.08 | 0.08 | 0.12 | 0.15 |
| `max_candidates` | 200 | 300 | 400 | 800 |
| `min_pop` | 0.70 | 0.60 | 0.55 | 0.45 |
| `min_ev_to_risk` | 0.03 | 0.012 | 0.008 | 0.005 |
| `min_ror` | 0.03 | 0.01 | 0.005 | 0.002 |
| `max_bid_ask_spread_pct` | 1.0 | 1.5 | 2.0 | 3.0 |
| `min_open_interest` | 1000 | 200 | 100 | 25 |
| `min_volume` | 100 | 10 | 5 | 1 |
| `data_quality_mode` | strict | balanced | balanced | lenient |

Preset resolution: `strategy_service._apply_request_defaults()` uses `setdefault()` — user-supplied values override preset values.

---

## 8. Output Contract

Each accepted trade dict contains:

```
strategy, spread_type, underlying, symbol, expiration, dte,
underlying_price, short_strike, long_strike, width,
spread_mid, spread_natural, net_credit,
max_profit, max_loss, max_profit_per_contract, max_loss_per_contract,
break_even, return_on_risk,
pop, p_win_used, pop_model_used,
expected_value, ev_per_contract, ev_to_risk,
liquidity_score, open_interest, volume, bid_ask_spread_pct,
rank_score, trade_key, expected_move,
legs[], tie_breaks{}, selection_reasons[],
contractsMultiplier (100)
```

---

## 9. Complexity Analysis

| Issue | Severity | Description |
|-------|----------|-------------|
| **Call credit spreads never built** | **CRITICAL** | `build_candidates()` hard-filters to `option_type == "put"`. The `call_credit_spread` scanner key is dead. |
| **Dual credit pre-validation** | Medium | Two separate credit checks in `build_candidates()` (natural credit > 0 AND short-leg mid ≥ threshold). The second is partially redundant. |
| **3-layer threshold resolution** | Medium | Thresholds can come from: preset defaults, `_apply_request_defaults()` fallbacks, and hardcoded defaults in `evaluate()`. Hard to trace which value wins. |
| **`expected_move_multiple` unused for POP/EV** | Low | This preset knob scales the expected move for distance calculations only, not for the POP model. Could mislead someone reading presets. |
| **DQ mode complexity** | Low | `data_quality_mode` influences how missing OI/volume are handled but the logic is scattered. |

---

## 10. Simplification Recommendations

1. **Fix the call credit spread bug** — Add `option_type` awareness to `build_candidates()` so call credit spreads are actually constructed. Alternatively, remove the `call_credit_spread` scanner key if it's not needed.
2. **Merge credit pre-checks** — Combine the two candidate-level credit checks into a single gate with a clear threshold.
3. **Move evaluate-phase filters downstream** — Under the new philosophy (scan wide, reject junk), most POP/EV/RoR gates should move to ranking/selection stages. Keep only structural validity checks (pricing available, credit > 0, credit < width) in the scanner.
4. **Remove `expected_move_multiple`** — It adds confusion without affecting trade quality assessment. The distance window (`distance_min`/`distance_max`) already controls which strikes are considered.
5. **Flatten threshold resolution** — Ensure every threshold has exactly one source path: preset value → user override. Remove hardcoded fallbacks in `evaluate()`.
