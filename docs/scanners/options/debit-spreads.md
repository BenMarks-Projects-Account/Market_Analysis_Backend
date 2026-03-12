# Debit Spreads Scanner

> **Scanner keys:** `put_debit`, `call_debit`
> **Plugin ID:** `debit_spreads`
> **Plugin class:** `DebitSpreadsStrategyPlugin` in `app/services/strategies/debit_spreads.py`
> **Registered in:** `pipeline_scanner_stage.py` lines 485–486

---

## 1. Purpose

Scans option chains for vertical debit spreads — buying a closer-to-the-money option and selling a further-OTM option to reduce cost. Both directional debit types use the same plugin, differentiated by a `direction` parameter:

- `put_debit` → `{"direction": "put"}` — bearish directional plays
- `call_debit` → `{"direction": "call"}` — bullish directional plays

**Target underlyings:** SPY, QQQ, IWM, DIA, XSP, RUT, NDX (default symbols).

---

## 2. End-to-End Flow

### Phase 1: `build_candidates()`

1. **Parse inputs** — Extract `direction` (`"both"` | `"call"` | `"put"`), `width`, and snapshot data.
2. **Per-snapshot loop:**
   - Validate: symbol, expiration, underlying_price, dte > 0, non-empty contracts.
   - **Strike dedup:** `_best_by_strike()` keeps only the highest-OI contract per strike.
   - Separate contracts into calls and puts.
   - **OTM window filter:** Strikes within `±12%` of underlying price (hardcoded).
   - **Width matching:** Find strike pairs with width within tolerance = `max(0.25, width × 0.4)`.
     - Width is either user-specified or auto-selected by underlying price tier (hardcoded tiers: <$50, <$100, <$200, etc.).
   - **Build pairs:** For each valid long/short strike pair, create a candidate dict with both legs.
   - **Sub-stage tracking:** Records `total_contracts → call/put contracts → after_otm_filter → after_width_match → after_cap`.
3. **Safety cap** at `max_candidates`.

### Phase 2: `enrich()`

The most sophisticated enrichment of all 4 plugins, featuring a **three-tier POP model:**

#### Spread Pricing:
1. `spread_bid = long_bid - short_ask`
2. `spread_ask = long_ask - short_bid`
3. `spread_mid = (spread_bid + spread_ask) / 2`
4. `net_debit = spread_ask` (natural fill) or `spread_mid` (mid fill), controlled by `debit_price_basis` config.

#### POP Model (three tiers, hierarchical):

| Tier | Model | Formula | Reliability |
|------|-------|---------|-------------|
| 1 | `pop_delta_approx` | `|delta_long|` | Baseline, always computed |
| 2 | `pop_breakeven_lognormal` | Black-Scholes: `N(±d2)` where `d2 = [ln(S/K) - σ²T/2] / (σ√T)` | Higher (requires IV, volatility) |
| 3 | `pop_refined` | Delta interpolation or breakeven (hierarchy: breakeven > delta_adjusted > delta_approx) | Highest available |

**Final `p_win_used`:** Prefers refined model → falls back to delta_approx.

#### Core Metrics:
1. `max_profit = (width - debit) × 100`
2. `max_loss = debit × 100`
3. `breakeven = long_strike ± debit` (+ for calls, - for puts)
4. `return_on_risk = max_profit / max_loss`
5. `debit_as_pct = debit / width`

#### EV (Binary Model):
- `EV = p_win × max_profit - (1 - p_win) × max_loss`
- `ev_to_risk = EV / max_loss`

#### Kelly Criterion:
- `f* = (b × p - q) / b` where `b = max_profit / max_loss`, `p = p_win`, `q = 1 - p_win`
- Clamped to `[0, kelly_cap]`

#### Additional Metrics:
- IV/RV: `iv` (leg average), `realized_vol` (from price history), `iv_rv_ratio`, `iv_rank`
- Greeks: `theta_net`, `theta_penalty`
- Liquidity: `min(long_OI, short_OI)`, `min(long_vol, short_vol)`, `bid_ask_spread_pct`
- Expected move, expected fill (via `apply_expected_fill()`)

### Phase 3: `evaluate()`

Sequential gates with rejection tracking and gate-eval snapshots:

| # | Gate | Threshold Source | Reason Code |
|---|------|-----------------|-------------|
| 0 | Sanity | `_valid_for_ranking` flag | `SANITY:*` |
| 1 | Pre-enrichment | `_rejection_codes` from enrich | (from enrich phase) |
| 2 | POP ≥ `min_pop - epsilon` | **configurable** | `pop_below_floor` / `DQ_MISSING:pop` |
| 3 | EV-to-risk ≥ `min_ev_to_risk` | **configurable** | `ev_to_risk_below_floor` |
| 4 | Bid-ask spread % ≤ `max_bid_ask_spread_pct` | **configurable** | `spread_too_wide` |
| 5 | Debit % ≤ `max_debit_pct_width` | **configurable** | `debit_too_close_to_width` |
| 6 | OI/Volume (DQ-mode-aware) | **configurable** | `DQ_MISSING:*` / `DQ_ZERO:*` |

**POP epsilon (Gate 2):** Default `1e-4`. Prevents float precision artifacts from rejecting boundary trades. This is a notable design feature.

**Gate eval snapshot:** Each trade gets an attached snapshot showing the exact threshold values used for its evaluation — good for trace/diagnostics.

### Phase 4: `score()`

Delegates to `compute_rank_score()` (from `ranking.py`). Returns score 0–100 + tie-breaks `{edge, pop, liq}`.

---

## 3. Data Inputs

| Input | Source | Used For |
|-------|--------|----------|
| `snapshots[]` | Tradier chain data per (symbol, expiration) | All per-symbol work |
| `snapshot.contracts[]` | Tradier option chain | Strike/leg selection |
| `snapshot.underlying_price` | Tradier | Spot price, POP, expected move |
| `snapshot.dte` | Derived from expiration | Expected move, POP z-scores |
| `snapshot.prices_history[]` | Tradier/Polygon candles | Realized vol, IV rank |
| `snapshot.iv_history[]` | Provider data | IV rank computation |
| `snapshot.vix` | Market data | IV context |
| Contract `.bid`, `.ask`, `.strike`, `.option_type`, `.open_interest`, `.volume`, `.delta`, `.iv` | Tradier | Pricing, greeks, liquidity |

---

## 4. Preset / Strictness Levels

| Parameter | Strict | Conservative | Balanced | Wide |
|-----------|--------|-------------|----------|------|
| `dte_min` | 14 | 14 | 7 | 3 |
| `dte_max` | 30 | 45 | 45 | 60 |
| `width_min` | 2.0 | 2.0 | 1.0 | 0.5 |
| `width_max` | 5.0 | 5.0 | 10.0 | 10.0 |
| `max_candidates` | 200 | 300 | 400 | 800 |
| `max_debit_pct_width` | 0.40 | 0.45 | 0.50 | 0.65 |
| `max_iv_rv_ratio_for_buying` | 0.90 | 1.00 | 1.10 | 1.30 |
| `min_pop` | 0.65 | 0.55 | 0.50 | 0.40 |
| `min_ev_to_risk` | 0.03 | 0.015 | 0.01 | 0.005 |
| `max_bid_ask_spread_pct` | 1.0 | 1.5 | 2.0 | 3.0 |
| `min_open_interest` | 1000 | 300 | 100 | 25 |
| `min_volume` | 100 | 20 | 5 | 1 |
| `data_quality_mode` | strict | balanced | balanced | lenient |

**Notable:** `max_iv_rv_ratio_for_buying` is specific to debit strategies — it prevents buying options when IV is high relative to realized vol (you're paying inflated premiums).

---

## 5. Configuration Parameters

| Parameter | Default | Valid Values |
|-----------|---------|-------------|
| `direction` | `"both"` | `"call"`, `"put"`, `"both"` |
| `width` | auto by price tier | Explicit width (0.5–10.0) |
| `debit_price_basis` | `"natural"` | `"natural"` (spread_ask), `"mid"` (spread_mid) |
| `data_quality_mode` | per preset | `"strict"`, `"balanced"`, `"lenient"` |
| `pop_epsilon` | `1e-4` | Float — POP boundary tolerance |
| `kelly_cap` | configurable | Max Kelly fraction |
| `max_iv_rv_ratio_for_buying` | per preset | IV/RV ratio ceiling |
| All threshold params | per preset | See preset table above |

---

## 6. Output Contract

```
strategy ("call_debit" | "put_debit"), spread_type (=strategy),
underlying, symbol, expiration, dte, underlying_price,
long_strike, short_strike, width,
legs[] (2 entries with bid/ask/mid/delta/iv/oi/volume),
spread_bid, spread_ask, spread_mid,
net_debit, max_profit, max_loss, break_even, return_on_risk,
debit_as_pct,
pop_delta_approx, pop_breakeven_lognormal, pop_refined,
pop_model_used, p_win_used,
iv, iv_rv_ratio, iv_rank, expected_move, realized_vol,
theta_net, theta_penalty,
open_interest, volume, bid_ask_spread_pct,
ev_per_contract, ev_per_share, ev_to_risk, kelly_fraction,
rank_score, trade_key,
_dq_flags, _pop_gate_eval, _gate_eval_snapshot,
_primary_rejection_reason, _valid_for_ranking
```

**Aliases:** `spread_type ≡ strategy`, `underlying ≡ symbol`, `price ≡ underlying_price`, `implied_vol ≡ iv`.

**Transient fields** (prefixed `_`): `_dq_flags`, `_pop_gate_eval`, `_gate_eval_snapshot`, `_primary_rejection_reason`, `_valid_for_ranking` — used during evaluation, may be stripped before final output.

---

## 7. Complexity Analysis

| Issue | Severity | Description |
|-------|----------|-------------|
| **`short_delta_abs` stores long delta** | **HIGH** | Field naming mismatch: `short_delta_abs` actually contains `|long_delta|`. Misleading for any consumer reading this field. |
| **Spread quote inversion not detected** | **HIGH** | No validation that `spread_bid ≤ spread_ask` after computation. Inverted quotes could produce nonsensical pricing. |
| **Strike window (±12%) hardcoded** | Medium | Not configurable via presets or payload. Limits how far OTM the scanner looks. |
| **Width selection tiers hardcoded** | Medium | Auto-width depends on price (<$50 → narrow, etc.) but tiers are hardcoded, not in presets. |
| **`_best_by_strike()` drops volume** | Medium | Dedup by highest OI discards contracts that may have higher volume but lower OI. |
| **POP model hierarchy complexity** | Medium | Three-tier POP with fallback logic adds code surface. The hierarchy (breakeven > delta_adjusted > delta_approx) is sound but underdocumented. |
| **DQ mode logic split across phases** | Low | Data-quality mode affects both enrich (flagging) and evaluate (gating), making the full picture harder to trace. |
| **RV cache uses `id(snapshot)`** | Low | Fragile caching — uses Python object identity rather than `(symbol, expiration)` tuple key. |
| **IV history threshold (20) hardcoded** | Low | IV rank requires ≥20 history points; threshold not configurable. |
| **Direction param indirection** | Low | `put_debit` scanner key → `{"direction": "put"}` injection at pipeline_scanner_stage. The plugin doesn't know which scanner key invoked it. |

---

## 8. V2 Cutover Status

> **Status: CUT OVER to V2** (Prompt 8)
> **Scanner family:** `vertical_spreads`
> **V2 engine:** `scanner_v2/families/vertical_spreads.py`
> **Migration map:** `put_debit → v2`, `call_debit → v2`
> **Test file:** `tests/test_v2_debit_cutover.py` (32 tests)

### What V2 eliminates

| Legacy Issue | V2 Resolution |
|--------------|---------------|
| `short_delta_abs` stores long delta (naming mismatch) | V2 uses canonical `V2Leg` with explicit `side` and raw `delta` per leg |
| Spread quote inversion not detected | V2 Phase D validates quote integrity before math |
| Three-tier POP hierarchy in scanner-time (over-decisioning) | V2 computes POP in Phase E for informational use only — no gating |
| Excessive evaluate gates (POP, EV, spread-width, DQ) | V2 rejects only structurally broken candidates; downstream decides |
| Direction param indirection | V2 dispatches directly by `scanner_key` via `_VARIANT_CONFIG` |
| Hardcoded strike window / width tiers | V2 Phase A narrowing uses configurable parameters |

### V2 pipeline path

```
pipeline_scanner_stage → should_run_v2("put_debit")=True
  → execute_v2_scanner("put_debit", chain, price)
    → vertical_spreads.run(scanner_key="put_debit")
      → Phase A: Narrowing (filter by option_type=put, DTE)
      → Phase B: Construction (pair strikes, long=higher, short=lower)
      → Phase C: Structural validation (2 legs, correct types/sides)
      → Phase D: Quote/liquidity checks (bid>0, ask>bid, OI present)
      → Phase E: Math (net_debit, max_profit, max_loss, POP, EV)
      → Phase F: Normalize → V2Candidate list
    → _v2_result_to_legacy_shape() → pipeline-compatible dict
```

### Comparison evidence

Golden fixtures in `scanner_v2/comparison/fixtures.py`:
- `fixture_spy_golden_put_debit()`: 4-put chain, 2 valid spreads
- `fixture_spy_golden_call_debit()`: 4-call chain, 2 valid spreads

Comparison harness confirms V2 produces:
- More candidates (no POP/EV scanner-time rejection)
- Clean canonical field naming
- Rich per-candidate diagnostics (structural, quote, liquidity, math checks)

---

## 9. Simplification Recommendations (Legacy Reference)

1. **Fix field naming** — Rename `short_delta_abs` to `long_delta_abs` (HIGH priority, prevents consumer confusion).
2. **Add spread quote validation** — After computing `spread_bid` and `spread_ask`, verify `spread_bid ≤ spread_ask`. Flag or reject inverted quotes.
3. **Parameterize strike window** — Move the ±12% OTM window to presets or payload. This lets presets control scan breadth naturally.
4. **Simplify POP model** — The three-tier hierarchy is defensible but adds code. Consider whether `pop_breakeven_lognormal` alone (with delta fallback) is sufficient, eliminating the `pop_refined` middle tier.
5. **Move quality gates downstream** — Under the new philosophy, POP/EV/spread-width gates should move to ranking/selection stages. Keep only structural checks (debit > 0, debit < width, legs exist) in the scanner.
6. **Extract width selection** — Move auto-width tiers from hardcoded logic to a configuration table or preset parameter.
7. **Keep gate eval snapshots** — The threshold snapshot attached to each trade is a strong pattern. Consider adopting it in other plugins.
