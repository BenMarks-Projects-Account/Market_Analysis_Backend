# Scoring Audit Report — Credit Spread Pipeline

> **Scope:** Read-only audit of the current scoring and ranking pipeline for the
> BenTrade credit-spread scanner.  No production logic was changed.
>
> **Generated:** 2025-02-26 (manual audit)

---

## Table of Contents

1. [File Inventory](#1-file-inventory)
2. [Stage 1 — Plugin `score()` (Structure Score)](#2-stage-1--plugin-score-structure-score)
3. [Stage 2 — Context Blending (`_apply_context_scores`)](#3-stage-2--context-blending-_apply_context_scores)
4. [Normalization Methods](#4-normalization-methods)
5. [Ranking, De-duplication & Tie-Breaking](#5-ranking-de-duplication--tie-breaking)
6. [Gate Interaction (Score vs Filter Ordering)](#6-gate-interaction-score-vs-filter-ordering)
7. [Example Trade Traces](#7-example-trade-traces)
8. [Frontend Display Pipeline](#8-frontend-display-pipeline)
9. [Risk / Alignment Assessment](#9-risk--alignment-assessment)
10. [Findings & Recommendations](#10-findings--recommendations)

---

## 1. File Inventory

| File | Role | Key exports / functions |
|------|------|------------------------|
| `BenTrade/backend/app/services/ranking.py` | **Core scoring engine** | `compute_rank_score()`, `compute_liquidity_score()`, `sort_trades_by_rank()`, `compare_trades_for_rank()`, `safe_float()`, `minmax_norm()`, `clamp()` |
| `BenTrade/backend/app/services/strategies/credit_spread.py` | Plugin `score()` wrapper | `CreditSpreadStrategyPlugin.score()` — delegates to `compute_rank_score()` |
| `BenTrade/backend/app/services/strategies/base.py` | Plugin ABC | `StrategyPlugin.score()` abstract interface |
| `BenTrade/backend/app/services/strategy_service.py` | Orchestrator | `_apply_context_scores()`, `_normalize_rank_100()`, `_regime_fit_from_playbook()`, dedup, final sort |
| `BenTrade/backend/common/quant_analysis.py` | Enrichment math | `CreditSpread.trade_quality_score()`, `enrich_trade()`, `enrich_trades_batch()` |
| `BenTrade/backend/app/utils/normalize.py` | Output normalization | `normalize_trade()` — aliases `rank_score` ↔ `composite_score` |
| `BenTrade/backend/app/utils/computed_metrics.py` | Metrics contract | `build_computed_metrics()` — resolves `rank_score`, `composite_score` for UI |
| `BenTrade/backend/app/services/evaluation/ranking.py` | Thin wrapper (TradeContract) | Delegates to `ranking.py` via `legacy_ranking` alias |
| `BenTrade/backend/app/services/recommendation_service.py` | Top-picks re-ranking | `_build_pick()` blends `0.6×rank + 0.2×regime + 0.2×liquidity` |
| `BenTrade/frontend/assets/js/utils/format.js` | Score display | `normalizeScore()`, `formatScore()` |
| `BenTrade/frontend/assets/js/ui/trade_card.js` | Card rendering | Reads `rank_score` → `formatScore()` → "Score 65.5%" badge |
| `BenTrade/backend/tests/test_report_ranking.py` | Unit tests | 3 tests covering score ordering, tie-breaks, sort |

---

## 2. Stage 1 — Plugin `score()` (Structure Score)

### 2.1 Entry Point

```
credit_spread.py:676  CreditSpreadStrategyPlugin.score(trade)
  → ranking.py:91       compute_rank_score(trade)
```

`score()` is called **only for trades that pass all gates** (evaluate returned `True`).
It returns `(rank_score: float, tie_breaks: dict)`.

### 2.2 `compute_rank_score()` — Detailed Formula

Located in `ranking.py:91-112`.

#### Step A — Extract Raw Inputs

| Input | Source field(s) | Fallback chain |
|-------|-----------------|----------------|
| `ev_to_risk` | `trade["ev_to_risk"]` | `ev_per_share / max_loss_per_share` → `expected_value / max_loss` |
| `return_on_risk` | `trade["return_on_risk"]` | Defaults to `0.0` |
| `pop` | `trade["p_win_used"]` | `trade["pop_delta_approx"]` → `0.0` |
| `trade_quality_score` | `trade["trade_quality_score"]` | `None` (optional component) |
| `open_interest` | `trade["open_interest"]` | `0.0` |
| `volume` | `trade["volume"]` | `0.0` |
| `bid_ask_spread_pct` | `trade["bid_ask_spread_pct"]` | `0.30` (for liquidity) / `9.99` (for penalty) |

#### Step B — Normalize to [0, 1] via `minmax_norm()`

Each raw value is clipped to `[lo, hi]` then linearly mapped to `[0, 1]`:

```
minmax_norm(x, lo, hi) = clamp(x, lo, hi) - lo) / (hi - lo)
```

| Component | Raw field | lo | hi | Interpretation |
|-----------|-----------|----|----|----------------|
| `edge` | ev_to_risk | 0.00 | 0.05 | EV efficiency per dollar at risk |
| `ror` | return_on_risk | 0.05 | 0.50 | Return on risk |
| `pop` | p_win_used | 0.50 | 0.95 | Probability of profit |
| `tqs` | trade_quality_score | 0.40 | 0.85 | Composite quality (optional) |

**Liquidity** is computed separately via `compute_liquidity_score()`:

```python
oi_score   = clamp(open_interest / 5000.0)         # [0, 1]
vol_score  = clamp(volume / 5000.0)                 # [0, 1]
spread_pen = clamp(bid_ask_spread_pct / 0.30)       # [0, 1]

liquidity  = clamp(0.45 × oi_score + 0.35 × vol_score + 0.20 × (1 - spread_pen))
```

#### Step C — Weighted Combination

| Component | Weight | Always present? |
|-----------|--------|-----------------|
| `edge` | **0.30** | Yes |
| `ror` | **0.22** | Yes |
| `pop` | **0.20** | Yes |
| `liquidity` | **0.18** | Yes |
| `tqs` | **0.10** | **Only if not None** |

When `tqs` is present, total weight = 1.00.
When `tqs` is absent, total weight = 0.90, and the score is **re-normalized** by dividing by total weight:

```python
score = Σ(weight_i × value_i) / Σ(weight_i)
```

This means the 4-component score (without TQS) effectively uses weights:
**edge=0.333, ror=0.244, pop=0.222, liquidity=0.200**.

#### Step D — Liquidity Penalty (Post-Blend)

After computing the weighted average, a **multiplicative penalty** is applied
based on the bid-ask spread:

```python
spread_pct = trade["bid_ask_spread_pct"]  # default 9.99 if missing
liquidity_penalty = clamp((spread_pct - 0.30) / 0.70, 0, 1)
score = score × (1 - 0.75 × liquidity_penalty)
```

- If `spread_pct ≤ 0.30` → penalty = 0 → no reduction.
- If `spread_pct = 1.00` → penalty = 1.0 → score reduced to **25%** of original.
- If `spread_pct ≥ 1.00` → penalty clamped at 1.0 → minimum 25%.

**Note:** This means liquidity is penalized *twice* — once inside the weighted
average (0.18 weight) and again via the multiplicative penalty.

#### Step E — Final Clamping

```python
return round(clamp(score, 0, 1), 6)
```

The raw structure score output is in the range **[0.0, 1.0]**.

### 2.3 Tie-Break Tuple

When two trades have the same `rank_score`, they are ordered by
`_trade_tie_break_tuple()` which returns:

```
(edge, pop, -spread_pct, open_interest, SYMBOL, short_strike, long_strike)
```

This is a **lexicographic** comparison — higher edge wins first, then higher POP,
then tighter spread, then higher OI, then alphabetical symbol, then strike values.

### 2.4 Credit Spread Plugin `score()` Tie-Breaks

```python
tie_breaks = {
    "edge": ev_to_risk or 0.0,
    "pop": p_win_used or pop_delta_approx or 0.0,
    "liq": -(bid_ask_spread_pct or 1.0),    # negated so higher = tighter spread
}
```

These are stored on the trade dict for use in the **final sort** (Section 5).

---

## 3. Stage 2 — Context Blending (`_apply_context_scores`)

Located in `strategy_service.py:870-895`.

After plugin scoring, `_apply_context_scores()` blends the structure score with
two external signals:

### 3.1 Three Input Components

| Component | Weight | Source | Range |
|-----------|--------|--------|-------|
| `structure` | **0.60** | Plugin `rank_score` (Stage 1 output, converted to 0-100) | 0–100 |
| `underlying_composite` | **0.20** | `signal_service.get_symbol_signals(symbol, "6mo")` | 0–100 |
| `regime_fit` | **0.20** | `_regime_fit_from_playbook(strategy, regime_payload)` | 0–100 |

### 3.2 Score Rescaling

The structure score from Stage 1 is in [0, 1]. It is converted to [0, 100] via
`_normalize_rank_100()`:

```python
def _normalize_rank_100(value):
    n = float(value)
    if n <= 1.0:
        n *= 100.0
    return clamp(n, 0, 100)
```

### 3.3 Blending Formula

```python
blended = (0.60 × structure_score) + (0.20 × underlying_composite) + (0.20 × regime_fit)
```

The blended score is written back to `trade["rank_score"]` (overwriting the raw
structure score), and the original is preserved in `trade["rank_score_raw"]`.

### 3.4 Regime Fit Scoring

`_regime_fit_from_playbook()` in `strategy_service.py:848-868`:

| Condition | Score |
|-----------|-------|
| Strategy is in playbook `primary` list | **100.0** |
| Strategy is in playbook `avoid` list | **10.0** |
| RISK_OFF regime + put credit / short put strategy | **15.0** |
| RISK_ON regime + put credit / covered call strategy | **90.0** |
| Default / neutral | **55.0** |

### 3.5 Underlying Composite Signal

Falls back to **50.0** if the signal service is unavailable or throws.

### 3.6 Output Fields

After blending, each accepted trade carries:

```json
{
  "rank_score": 65.49,          // blended (displayed in UI)
  "rank_score_raw": 64.984,     // pre-blend structure score (0-100)
  "rank_components": {
    "structure": 64.984,
    "underlying_composite": 32.5,
    "regime_fit": 100.0,
    "blended": 65.49
  }
}
```

---

## 4. Normalization Methods

### 4.1 `minmax_norm(x, lo, hi)` — ranking.py

Linear map from `[lo, hi]` → `[0, 1]`, with clamping. Returns 0.0 for `None`
input or degenerate bounds.

### 4.2 `_normalize_rank_100(value)` — strategy_service.py

Converts a score that may be in [0, 1] or [0, 100] to always be in [0, 100].
Heuristic: if `value ≤ 1.0`, multiply by 100.

### 4.3 `normalizeScore(raw)` — format.js (frontend)

Identical heuristic for display: if `0 < value ≤ 1`, multiply by 100.
Then clamps to [0, 100]. Rendered via `formatScore()` as "65.5%".

### 4.4 `normalize_trade()` — normalize.py

Aliases `rank_score` → `composite_score` when `composite_score` is None.
This ensures backward compatibility with older persisted reports.

### 4.5 `build_computed_metrics()` — computed_metrics.py

Resolves `rank_score` and `composite_score` from the 4-tier container chain:
`computed_metrics → computed → details → root`. These are surfaced to the UI
as part of the canonical metrics contract.

---

## 5. Ranking, De-duplication & Tie-Breaking

### 5.1 Pipeline Ordering (strategy_service.py `generate()`)

```
1. enrich() → all candidates enriched with CreditSpread metrics
2. evaluate() loop → pass/fail gates, collect rejected_rows
3. plugin.score() → compute rank_score + tie_breaks for passing trades
4. _apply_context_scores() → blend with underlying/regime signals
5. De-duplicate by trade_key → keep highest rank_score per key
6. Final sort → descending by (rank_score, edge, pop, liq)
```

### 5.2 De-duplication Logic

```python
deduped = {}
for trade in accepted:
    key = trade["trade_key"]
    if key not in deduped or trade["rank_score"] > deduped[key]["rank_score"]:
        deduped[key] = trade
```

`trade_key` is a composite key: `{symbol}|{expiration}|{spread_type}|{short_strike}|{long_strike}|{dte}`

### 5.3 Final Sort

```python
accepted.sort(
    key=lambda tr: (
        float(tr["rank_score"]),
        float(tr["tie_breaks"]["edge"]),
        float(tr["tie_breaks"]["pop"]),
        float(tr["tie_breaks"]["liq"]),
    ),
    reverse=True,
)
```

### 5.4 Per-Symbol Limiting

There is **no explicit per-symbol cap** in the current pipeline. All passing
trades from all symbols are returned. The only limit mechanism is the
`max_candidates` cap applied during `build_candidates()` (construction phase),
not during scoring/ranking.

### 5.5 `sort_trades_by_rank()` (ranking.py)

A standalone function that re-computes `rank_score` for each trade and sorts
using `compare_trades_for_rank()`. This is used by the workbench and report
retrieval paths, but **not** by the main `generate()` pipeline (which uses
its own sort after context blending).

---

## 6. Gate Interaction (Score vs Filter Ordering)

### 6.1 Ordering Guarantee

**Scoring happens AFTER gates.** Only trades that pass ALL gates are scored.

```
evaluate() → if all gates pass → score() → rank
                                         ↘ context blend → final rank
           → if any gate fails → rejected (no score computed)
```

This means:
- A trade with spectacular metrics that fails a single gate (e.g., OI too low)
  will **never receive a score**.
- The near-miss system compensates by computing a **nearness score** for
  rejected trades (separate from `rank_score`), but this is diagnostic only.

### 6.2 Gate Groups (for reference)

The `_GATE_GROUPS` mapping in `strategy_service.py` categorizes rejection
reasons into gates:

| Gate | Rejection codes |
|------|----------------|
| quote_validation | `QUOTE_INVALID:*`, `MISSING_QUOTES:*` |
| metrics_computation | `CREDIT_SPREAD_METRICS_FAILED` |
| probability | `pop_below_floor` |
| expected_value | `ev_to_risk_below_floor`, `ev_negative` |
| return_on_risk | `ror_below_floor` |
| spread_structure | `invalid_width`, `non_positive_credit`, `credit_ge_width` |
| liquidity | `spread_too_wide`, `open_interest_below_min`, `volume_below_min` |
| data_quality | `DQ_MISSING:*`, `DQ_ZERO:*` |

### 6.3 Implicit Gate in Scoring

Even after passing all evaluate() gates, the `compute_rank_score()` function
applies an **additional liquidity penalty** (Section 2.2, Step D). This means
a trade could pass the bid-ask spread gate but still receive a significantly
reduced score.

Example: spread_pct = 1.0% passes the default `max_bid_ask_spread_pct = 1.5%`
gate, but in `compute_rank_score()`, the raw decimal 0.01 yields
`penalty = clamp((0.01 - 0.30) / 0.70) = 0` — no penalty. However, a
spread_pct of 0.50 (50%) would still pass a lenient gate but get severely
penalized.

---

## 7. Example Trade Traces

### 7.1 Report: `credit_spread_analysis_20260226_143105.json` (3 accepted)

#### Trade 1: RUT 2645/2640 Put Credit Spread

```
Raw Inputs:
  ev_to_risk       = 0.04825
  return_on_risk   = 0.7544
  p_win_used       = 0.5975
  trade_quality_score = 0.689
  bid_ask_spread_pct  = 0.02697
  open_interest    = 3
  volume           = 0

Stage 1 — compute_rank_score():
  edge    = minmax_norm(0.04825, 0, 0.05)  = 0.965
  ror     = minmax_norm(0.7544,  0.05, 0.50) = 1.0   (capped at hi)
  pop     = minmax_norm(0.5975,  0.50, 0.95) = 0.217
  tqs     = minmax_norm(0.689,   0.40, 0.85) = 0.642
  liquidity:
    oi_score   = clamp(3 / 5000)     = 0.0006
    vol_score  = clamp(0 / 5000)     = 0.0
    spread_pen = clamp(0.02697/0.30) = 0.0899
    liquidity  = 0.45×0.0006 + 0.35×0.0 + 0.20×(1-0.0899) = 0.000 + 0.0 + 0.182 = 0.182

  Weighted sum (all 5 components):
    0.30×0.965 + 0.22×1.0 + 0.20×0.217 + 0.18×0.182 + 0.10×0.642
    = 0.2895 + 0.22 + 0.0434 + 0.0328 + 0.0642 = 0.6499
    / 1.00 = 0.6499

  Liquidity penalty:
    spread_pct defaults from trade = 0.02697
    penalty = clamp((0.02697 - 0.30) / 0.70) = 0  (negative → clamped to 0)
    score = 0.6499 × (1 - 0.75×0) = 0.6499

  → rank_score (raw) ≈ 0.6499 → ×100 → 64.99 (reported as 64.984)

Stage 2 — _apply_context_scores():
  structure_score     = 64.984
  underlying_composite = 32.5   (signal service for RUT)
  regime_fit          = 100.0   (strategy in playbook primary)

  blended = 0.60×64.984 + 0.20×32.5 + 0.20×100.0
          = 38.990 + 6.500 + 20.000 = 65.49

  → final rank_score = 65.49 → UI shows "Score 65.5%"
```

#### Trade 2: NDX 25090/25080 (Missing POP/EV — incomplete enrichment)

```
Raw Inputs:
  ev_to_risk       = None (missing)
  return_on_risk   = 1.0202
  p_win_used       = None (missing)
  trade_quality_score = None
  bid_ask_spread_pct  = 0.02699

Stage 1:
  edge = minmax_norm(0, 0, 0.05) = 0.0  (ev_to_risk → _get_ev_to_risk returned 0.0)
  ror  = minmax_norm(1.0202, 0.05, 0.50) = 1.0  (capped)
  pop  = minmax_norm(0, 0.50, 0.95) = 0.0  (_get_pop returned 0.0)
  tqs  = None → excluded
  liquidity ≈ 0.182  (OI=0, vol=0, tight spread)

  4-component weighted sum: 0.30×0 + 0.22×1.0 + 0.20×0 + 0.18×0.182 = 0.2528
  / 0.90 = 0.2809

  → rank_score_raw ≈ 28.08 → blended with underlying=82.5, regime=100.0 → 53.35
```

**Observation:** This trade has **no POP and no EV** yet still passes all gates
and receives a non-trivial score. The missing POP defaults to 0.0 inside
`_get_pop()`, and the evaluate gate `pop_below_floor` checks
`if pop is not None and pop < min_pop` — but since the enrichment path for
NDX failed to produce `p_win_used`, `pop` is `None` at evaluate time, which
**bypasses the pop gate entirely**. This is a significant finding (see Section 10).

---

## 8. Frontend Display Pipeline

### 8.1 Score Resolution Chain

```
trade_card.js:285
  resolveMetric(trade, { key: 'rank_score', computedKey: 'rank_score', rootFallbacks: ['composite_score'] })
  → tries: trade.computed_metrics.rank_score → trade.computed.rank_score → trade.rank_score → trade.composite_score
```

### 8.2 Display Formatting

```
format.js:normalizeScore(raw)
  if 0 < raw ≤ 1.0 → raw × 100    (converts 0–1 to 0–100)
  if 1 < raw ≤ 100  → raw as-is
  negative → 0
  > 100 → 100

format.js:formatScore(raw, 1)
  → normalizeScore(raw).toFixed(1) + '%'
  → e.g., "65.5%"
```

### 8.3 Dual-Scale Ambiguity

The backend now outputs `rank_score` in the **0-100** range (after context
blending), but the `normalizeScore()` function still checks for `≤ 1.0` to
auto-scale. This means:

- A blended score of **0.85** (should be 0-100 but equals 0.85) → frontend
  interprets as 0-1 → displays as **85.0%** (incorrect — should be 0.85%).
- Current data shows scores like 65.49, 53.35 → correctly displayed.
- **Risk:** If a future code path writes `rank_score` as a 0-1 decimal (as
  `compute_rank_score()` returns), the frontend will auto-multiply by 100,
  leading to correct display. But if normalization fails to run, a 0-1 value
  and a 0-100 value could coexist.

---

## 9. Risk / Alignment Assessment

### 9.1 Alignment with BenTrade Philosophy

| Principle | Current Implementation | Aligned? |
|-----------|----------------------|----------|
| High-probability strategies | POP weight = 0.20 (Structure) + bypassed if None | ⚠️ Partial |
| Risk-defined | Max loss used in EV/risk and ROR | ✅ Yes |
| EV-positive selection | Edge (ev_to_risk) is the highest weight at 0.30 | ✅ Yes |
| Moderate, consistent income | ROR normalized cap = 0.50 (50% max) | ✅ Yes |
| Liquidity matters | Liquidity in both weighted avg AND multiplicative penalty | ✅ Yes (double-counted) |
| Index ETF focus | Regime fit favors credit puts in risk-on | ✅ Yes |

### 9.2 Key Concerns

1. **POP bypassed when None.** The evaluate gate uses
   `if pop is not None and pop < min_pop` — a None POP silently passes. This
   contradicts the "high-probability first" philosophy.

2. **Double liquidity penalization.** Liquidity is counted twice: once as a
   0.18-weight component and again as a multiplicative penalty. Wide-spread
   trades are penalized more aggressively than the weights suggest.

3. **TQS optional with re-normalization.** When `trade_quality_score` is
   missing, the other 4 components get proportionally boosted. This silently
   changes the effective weight distribution.

4. **No minimum score threshold.** Any trade that passes gates gets into the
   final output, even with a near-zero score. There is no `min_rank_score`
   gate.

5. **Regime fit can dominate.** With 0.20 weight, regime_fit = 100 vs 10
   creates an 18-point swing. For a borderline structure score (e.g., 40),
   regime fit alone can push the blended score above 50.

---

## 10. Findings & Recommendations

### Finding 1: `None` POP Bypasses Gate — HIGH

**Location:** `credit_spread.py:607-608`
```python
if pop is not None and pop < min_pop:
    reasons.append("pop_below_floor")
```

**Issue:** Trades with missing POP (None) pass the probability gate silently.
In `compute_rank_score()`, `_get_pop()` returns 0.0 for None, so the score
reflects zero probability, but the trade is still accepted.

**Risk:** Trades with no delta data (e.g., some NDX options) can be accepted
despite having unknown probability of profit. The example NDX 25090/25080 trade
shows this in practice.

**Recommendation:** Add a `pop_missing` rejection code when POP is None, or at
minimum track it as a data-quality flag.

---

### Finding 2: Double Liquidity Penalization — MEDIUM

**Location:** `ranking.py:64-68` (component) + `ranking.py:107-110` (penalty)

**Issue:** Liquidity affects the score twice:
1. As a weighted component (0.18 / 0.20 effective weight)
2. As a multiplicative penalty reducing the entire score by up to 75%

**Risk:** For a trade with tight bid-ask but low OI/volume, the component
gives a low score, but the penalty is minimal (spread-based only). Conversely,
a wide-spread trade gets hit hard twice. The total effective weight of
liquidity-related factors can be much larger than the stated 0.18.

**Recommendation:** Document this as intentional (if it is), or consolidate
into a single mechanism.

---

### Finding 3: TQS Absence Changes Weights — LOW

**Location:** `ranking.py:96-103`

**Issue:** When `trade_quality_score` is None, the 0.10 weight is removed
and the remaining weights are renormalized (divided by 0.90 instead of 1.00).
This boosts edge from 0.30→0.333, ror from 0.22→0.244, etc.

**Risk:** Two otherwise identical trades — one with TQS=0.5, one without —
will have different effective weights for all other components. This makes
score comparison across trades non-uniform.

**Recommendation:** Consider using a neutral default (e.g., 0.5) for missing
TQS instead of dropping the component entirely.

---

### Finding 4: No Minimum Score Gate — LOW

**Issue:** After scoring and context blending, there is no minimum `rank_score`
threshold. Any trade that passes the filter gates is included in the output,
even if its blended score is very low (e.g., 10).

**Recommendation:** Consider adding an optional `min_rank_score` filter in
the generate pipeline, perhaps as a preset-configurable parameter.

---

### Finding 5: Dual-Scale Score Ambiguity — LOW

**Location:** `compute_rank_score()` returns [0, 1]; `_apply_context_scores()`
converts to [0, 100] and overwrites `rank_score`.

**Issue:** Different code paths may encounter `rank_score` in either scale.
The `_normalize_rank_100()` and frontend `normalizeScore()` heuristics (if
value ≤ 1, multiply by 100) handle this, but it's fragile.

**Recommendation:** Standardize on one scale (0-100) throughout and document it
in the canonical contract.

---

### Summary Table

| # | Finding | Severity | Category |
|---|---------|----------|----------|
| 1 | None POP bypasses gate | HIGH | Data integrity |
| 2 | Double liquidity penalization | MEDIUM | Score accuracy |
| 3 | TQS absence changes weights | LOW | Score comparability |
| 4 | No minimum score gate | LOW | Filter completeness |
| 5 | Dual-scale score ambiguity | LOW | Maintainability |

---

*End of audit report.*
