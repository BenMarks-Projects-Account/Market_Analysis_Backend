# Scoring & Gating Consistency Hardening — Developer Note

**Date:** 2026-02-26
**Scope:** credit spread pipeline only; no EV model changes, no threshold changes, no strategy philosophy changes.

---

## 1. POP Missing Enforcement

**Before:** `evaluate()` Gate 4 only checked `pop < min_pop` when POP was present.
If POP was `None`, the check was silently skipped — the trade could pass all gates
without a probability estimate, violating BenTrade's probability-first philosophy.

**After:** Missing POP is now handled by `data_quality_mode`:

| Mode | POP = None behavior |
|---|---|
| `strict` | Reject with `DQ_MISSING:pop` |
| `balanced` | Reject with `DQ_MISSING:pop` |
| `lenient` | Waive (no rejection), but `dq_waived_count` incremented |

`DQ_MISSING:pop` is categorized under the `probability` gate group in `_GATE_GROUPS`
so it appears correctly in gate-breakdown summaries.

**File:** `BenTrade/backend/app/services/strategies/credit_spread.py` — Gate 4 block.

---

## 2. Liquidity De-Duplication

**Before:** Liquidity impacted `rank_score` twice:
1. As a weighted component (0.18 weight in the blend)
2. As a post-blend multiplicative penalty: `score *= (1 - 0.75 × penalty)` based on `bid_ask_spread_pct`

This caused nonlinear cliffs — a trade with moderately wider spread could lose 50%+
of its score from the multiplier alone, on top of the weighted liquidity component
already reducing it.

**After (Option A):** The multiplicative penalty is removed entirely. Liquidity
impacts scores **only** through the weighted component (0.18 weight), which itself
blends OI (45%), volume (35%), and spread tightness (20%).

Score now degrades smoothly and monotonically as spread widens, with no cliff effects.
Verified by `TestLiquidityNoPenalty` tests.

**File:** `BenTrade/backend/app/services/ranking.py` — `compute_rank_score()`.

---

## 3. Rank Score Scale Definition (0–100)

**Before:** `compute_rank_score()` returned 0.0–1.0 (6 decimal places). The downstream
`_apply_context_scores()` used a heuristic (`if n <= 1.0 → n *= 100`) to normalize
to 0–100 for the blended context score. The frontend `normalizeScore()` applied the
same heuristic. This dual-scale ambiguity made trace JSON values hard to interpret.

**After:**
- `compute_rank_score()` → returns **0–100** (3 decimal places), canonical scale.
- `rank_score_raw` = pre-context-blend structural score (0–100).
- `rank_score` = final blended score after `_apply_context_scores()` (0–100).

The `_normalize_rank_100()` heuristic is retained as a legacy safety net for external
signals that may still arrive as 0–1 (e.g., signal composite scores). It is **not**
the primary normalization path for `rank_score` anymore.

The frontend `normalizeScore()` continues to work correctly — scores >1 and ≤100
pass through as-is.

**Files:**
- `BenTrade/backend/app/services/ranking.py` — `compute_rank_score()`.
- `BenTrade/backend/app/services/strategy_service.py` — `_normalize_rank_100()` docstring.

---

## 4. Trace DQ Summary Block

A new `dq_summary` key is added to the filter trace JSON:

```json
{
  "dq_summary": {
    "missing_pop_count": 12,
    "missing_delta_count": 0,
    "zero_open_interest_count": 45,
    "zero_volume_count": 23,
    "quote_rejected_count": 3,
    "dq_waived_count": 8
  }
}
```

The existing `missing_field_counts` dict also gains `missing_pop` and `missing_delta`.

When a scan returns 0 trades, the `dq_summary` + `gate_breakdown` together answer
"Was it EV? liquidity? POP? data quality?" without manual digging.

**File:** `BenTrade/backend/app/services/strategy_service.py` — filter trace assembly.

---

## Test Coverage

17 new tests in `BenTrade/backend/tests/test_scoring_gating_hardening.py`:

| Class | Tests |
|---|---|
| `TestPopGateBehavior` | 6 — present/passes, below-floor, missing in strict/balanced/lenient, gate breakdown |
| `TestLiquidityNoPenalty` | 3 — monotonic decline, smooth degradation, no zero-out |
| `TestScoreScale` | 6 — range check, high/low quality, sort output, plugin score(), zero-input |
| `TestRankingRegression` | 2 — ordering preference, sort descending |
