# Iron Condor Scanner

> **Scanner key:** `iron_condor`
> **Plugin ID:** `iron_condor`
> **Plugin class:** `IronCondorStrategyPlugin` in `app/services/strategies/iron_condor.py`
> **Registered in:** `pipeline_scanner_stage.py` line 482

---

## 1. Purpose

Scans option chains for iron condor spreads — selling both a put credit spread and a call credit spread at OTM distances from the underlying, profiting from premium decay when the underlying stays within the short strikes. This is a 4-leg, risk-defined, premium-collection strategy.

**Target underlyings:** SPY, QQQ, IWM, DIA, XSP, RUT, NDX (default symbols).

---

## 2. End-to-End Flow

### Phase 1: `build_candidates()`

1. **Parse inputs** — Extract `distance_mode` (default: `"expected_move"`), `distance_target`, `wing_width_put`, `wing_width_call`, penny-wing thresholds.
2. **Per-snapshot loop** — For each (symbol, expiration) snapshot:
   - Validate: symbol, expiration, underlying_price, dte > 0, non-empty contracts.
   - Build separate strike maps for puts and calls.
   - Compute expected move: `spot × IV × √(dte/365)`.
   - **Distance targeting:** Center the short strikes at `distance_target × expected_move` from spot (sigma-based positioning).
   - **Short strike selection:**
     - Put short: strikes at `spot - target_distance`, snapped to nearest available.
     - Call short: strikes at `spot + target_distance`, snapped to nearest available.
   - **Long strike (wing) placement:**
     - Put long = put_short - `wing_width_put`
     - Call long = call_short + `wing_width_call`
   - **Penny-wing pre-check (1st occurrence):** Reject if `short_leg_mid < min_short_leg_mid` or `side_credit < min_side_credit`. Configurable per preset.
   - **Sigma distance pre-check (1st occurrence):** Reject if computed sigma distance < `min_sigma_distance`.
   - **Symmetry pre-check (1st occurrence):** Reject if symmetry ratio < 0.55 (hardcoded).
   - Emit 4-leg candidate dict.
3. **Safety cap** — Stop at `max_candidates`.

### Phase 2: `enrich()`

For each candidate:
1. Extract bid/ask for all 4 legs.
2. Compute spread pricing:
   - Put side credit: `put_short_bid - put_long_ask`
   - Call side credit: `call_short_bid - call_long_ask`
   - `net_credit = put_side_credit + call_side_credit` (mid-based)
   - `net_credit_natural = put_natural + call_natural` (worst-case fill)
3. **Silent drops:** Skip (with no rejection tracking) if `net_credit ≤ 0` or `max_loss ≤ 0`.
4. Compute:
   - `max_profit = net_credit × 100`
   - `max_loss = (max_width - net_credit) × 100` where `max_width = max(put_width, call_width)`
   - Breakevens: `put_short - net_credit` and `call_short + net_credit`
5. **Penny-wing detection (2nd occurrence):** Different thresholds than build phase. Flags `is_penny_wing = True` if side credit < threshold; sets `rank_score = 0.0`.
6. **Sigma distance check (2nd occurrence):** Recomputed for enriched row; may diverge from build-phase result.
7. Compute POP via normal CDF: probability price stays between breakevens.
8. Compute EV via numerical integration (same approach as butterflies).
9. Compute symmetry ratio: `min(put_distance, call_distance) / max(put_distance, call_distance)`.
10. Compute liquidity score from worst-leg OI, volume, bid-ask spread.
11. **Rank score:**
    ```
    rank_score = clamp(
        0.34 × theta_score
      + 0.26 × distance_score    # sigma distance from ATM
      + 0.20 × symmetry_score
      + 0.20 × liquidity_score
      - penny_wing_penalty
      - distance_penalty          # if too close to ATM
    )
    ```
12. Apply `apply_expected_fill()`.

### Phase 3: `evaluate()`

| # | Gate | Threshold Source | Reason Code |
|---|------|-----------------|-------------|
| 1 | Execution validity | enrichment flag | `execution_invalid:{reason}` |
| 2 | Spread pricing available | enrichment | `pricing_unavailable` |
| 3 | Net credit > 0 | enrichment | `non_positive_credit` |
| 4 | Credit < max_width | enrichment | `credit_ge_width` |
| 5 | Required metrics present | enrichment | `METRICS_MISSING:{field}` |
| 6 | RoR ≥ `min_ror` | **configurable** | `ror_below_threshold` |
| 7 | Credit ≥ `min_credit` | **configurable** | `credit_below_minimum` |
| 8 | EV-to-risk ≥ `min_ev_to_risk` | **configurable** | `ev_to_risk_below_threshold` |
| 9 | POP ≥ `min_pop` | **configurable** | `pop_below_threshold` |
| 10 | **Symmetry ≥ `symmetry_target` (2nd occurrence)** | **configurable** (default 0.70) | `symmetry_below_target` |
| 11 | **Sigma distance ≥ `min_sigma_distance` (3rd occurrence)** | **configurable** | `sigma_distance_below_floor` |

### Phase 4: `score()`

Returns pre-computed `rank_score` from enrich phase, plus tie-break dict.

---

## 3. Data Inputs

| Input | Source | Used For |
|-------|--------|----------|
| `snapshots[]` | Tradier chain data per (symbol, expiration) | All per-symbol work |
| `snapshot.contracts[]` | Tradier option chain | 4-leg selection |
| `snapshot.underlying_price` | Tradier | Spot price, distance calc, POP |
| `snapshot.dte` | Derived from expiration | Expected move, POP z-scores |
| `snapshot.prices_history[]` | Tradier/Polygon candles | Realized vol fallback |
| Contract: `.bid`, `.ask`, `.strike`, `.option_type`, `.open_interest`, `.volume`, `.iv`, `.delta`, `.gamma`, `.theta` | Tradier | Pricing, greeks, liquidity |
| `request` (payload) | Frontend/API | All threshold overrides, preset selection |

---

## 4. Candidate Construction Details

- **Distance mode:** Default `"expected_move"` — short strikes placed at `distance_target × σ` from spot. Sigma distance = `|strike - spot| / expected_move`.
- **Wing widths:** Separate `wing_width_put` and `wing_width_call` allow asymmetric iron condors (when `allow_skewed = True`).
- **4-leg structure:** Put long → Put short — [underlying price] — Call short → Call long.
- **Penny-wing rejection:** If short-leg mid or side credit falls below configurable minimum, the candidate is tagged as a penny wing and either rejected (build phase) or penalized to rank 0 (enrich phase).

---

## 5. Filtering Logic — Duplicate Checks Across Phases

This is the most significant complexity issue in the iron condor scanner. Several filters run multiple times across different phases with potentially different thresholds:

### Penny-wing detection (checked 2×)
| Phase | Threshold | Source |
|-------|-----------|--------|
| Build | `min_short_leg_mid`, `min_side_credit` | Preset (e.g., strict: 0.10/0.10) |
| Enrich | Different internal thresholds | Hardcoded in enrichment logic |

### Sigma distance (checked 3×)
| Phase | Threshold | Source |
|-------|-----------|--------|
| Build | `min_sigma_distance` (from preset) | Configurable |
| Enrich | Recomputed; different fallback | Mixed |
| Evaluate | `min_sigma_distance` (from preset) | Configurable |

### Symmetry (checked 2×)
| Phase | Threshold | Source |
|-------|-----------|--------|
| Build | 0.55 | **Hardcoded** |
| Evaluate | `symmetry_target` (default 0.70) | **Configurable** (per preset: strict=0.80, wide=0.40) |

---

## 6. Preset / Strictness Levels

| Parameter | Strict | Conservative | Balanced | Wide |
|-----------|--------|-------------|----------|------|
| `dte_min` | 21 | 21 | 14 | 14 |
| `dte_max` | 45 | 45 | 45 | 60 |
| `distance_mode` | expected_move | expected_move | expected_move | expected_move |
| `distance_target` | 1.2 | 1.1 | 1.0 | 0.9 |
| `min_sigma_distance` | 1.2 | 1.1 | 1.0 | 0.9 |
| `wing_width_put` | 5.0 | 5.0 | 5.0 | 5.0 |
| `wing_width_call` | 5.0 | 5.0 | 5.0 | 5.0 |
| `wing_width_max` | 10.0 | 10.0 | 10.0 | 15.0 |
| `allow_skewed` | false | false | false | true |
| `symmetry_target` | 0.80 | 0.70 | 0.55 | 0.40 |
| `min_ror` | 0.15 | 0.12 | 0.08 | 0.05 |
| `min_credit` | 0.15 | 0.10 | 0.10 | 0.05 |
| `min_ev_to_risk` | 0.05 | 0.02 | 0.00 | -0.05 |
| `min_pop` | 0.55 | 0.50 | 0.45 | 0.35 |
| `max_candidates` | 220 | 220 | 300 | 500 |
| `min_open_interest` | 1000 | 500 | 300 | 100 |
| `min_volume` | 100 | 50 | 0 | 0 |
| `data_quality_mode` | strict | balanced | balanced | lenient |
| `max_bid_ask_spread_pct` | — | — | — | 2.0 |
| `min_short_leg_mid` | 0.10 | 0.08 | 0.05 | 0.05 |
| `min_side_credit` | 0.10 | 0.08 | 0.05 | 0.03 |

**Note:** `max_bid_ask_spread_pct` is only defined in the Wide preset. Other presets inherit whatever fallback exists in `evaluate()`.

---

## 7. Unused Preset Keys

The following keys are defined in presets but **never consumed** by the plugin code:

| Key | Notes |
|-----|-------|
| `wing_width_max` | Defined in all 4 presets; never read by `build_candidates()` or `evaluate()` |
| `max_candidates` | Defined but the build-phase cap may use a different mechanism |
| `max_bid_ask_spread_pct` | Only in Wide; no bid-ask spread gate exists in `evaluate()` for this plugin |

---

## 8. Output Contract

Each accepted trade dict contains ~80 keys including:

```
strategy ("iron_condor"), spread_type, underlying, symbol, expiration, dte,
underlying_price, expected_move,
put_short_strike, put_long_strike, call_short_strike, call_long_strike,
put_width, call_width, wing_width,
net_credit, net_credit_natural,
put_side_credit, call_side_credit,
max_profit, max_loss, max_profit_per_contract, max_loss_per_contract,
break_even_low, break_even_high, return_on_risk,
pop, p_win_used, pop_model_used,
expected_value, ev_per_contract, ev_to_risk,
sigma_distance_put, sigma_distance_call, sigma_distance_min,
symmetry_ratio,
is_penny_wing,
liquidity_score, open_interest, volume, bid_ask_spread_pct,
rank_score, trade_key,
legs[] (4 entries), tie_breaks{}, selection_reasons[],
contractsMultiplier (100)
```

Many fields exist as backward-compat aliases (e.g., multiple breakeven field names).

---

## 9. Complexity Analysis

| Issue | Severity | Description |
|-------|----------|-------------|
| **Penny-wing detection runs 2×** | **HIGH** | Build phase rejects, enrich phase re-checks with different thresholds and sets `rank_score = 0`. Divergent logic. |
| **Sigma distance checked 3×** | **HIGH** | Build, enrich, and evaluate all check sigma distance. The enrich check may use a different fallback value than the preset-configured one. |
| **Symmetry checked 2× with different thresholds** | **HIGH** | Build uses hardcoded 0.55; evaluate uses configurable `symmetry_target` (strict=0.80). A candidate passing build at 0.56 could fail evaluate at 0.80. |
| **Silent drops in enrich** | Medium | Candidates with `net_credit ≤ 0` or `max_loss ≤ 0` are silently dropped (no rejection reason tracked). Violates scanner contract. |
| **~80 output keys with aliases** | Medium | Significant backward-compat alias surface. Multiple breakeven field names, multiple credit representations. |
| **No OI/volume hard gates** | Low | Unlike credit_spread, iron_condor has no hard OI/volume rejection gates. These metrics only affect the liquidity score. Balanced/Wide presets set `min_volume = 0`. |
| **`wing_width_max` preset key never consumed** | Low | Dead configuration — defined in presets but never referenced by plugin code. |

---

## 10. Simplification Recommendations

1. **Deduplicate filter checks** — Each filter (penny-wing, sigma distance, symmetry) should run exactly once, in one phase, with one set of thresholds. Recommendation: keep only the build-phase structural checks (just pass/fail on catastrophic conditions like zero-credit wings) and move quality gates to evaluate.
2. **Track all rejections** — The silent drops in enrich for `net_credit ≤ 0` and `max_loss ≤ 0` must emit rejection reason codes rather than silently discarding candidates.
3. **Move quality gates downstream** — Under the new philosophy, POP/EV/RoR/symmetry/sigma gates should move to ranking/selection stages. The scanner should only reject structurally invalid candidates (no legs found, impossible pricing).
4. **Clean up unused preset keys** — Remove `wing_width_max` or wire it up. Clarify `max_bid_ask_spread_pct` (only in Wide, no gate exists).
5. **Reduce output alias surface** — Standardize on canonical field names and remove backward-compat duplicates once all consumers are updated.
6. **Unify symmetry threshold** — Remove the hardcoded 0.55 in build and use only the configurable `symmetry_target` from presets.
