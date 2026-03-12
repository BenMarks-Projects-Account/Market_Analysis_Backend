# Trust Hygiene Layer — Phase D2

**Added:** Prompt 9  
**Location:** `scanner_v2/hygiene/`  
**Pipeline position:** After Phase D (quote/liquidity presence) → Before Phase E (recomputed math)

## Purpose

Phase D2 provides shared candidate-level quality checks that go **beyond Phase D's presence checks** to catch structurally broken or untradeable candidates before they reach math recomputation.

Philosophy: **reject obvious junk, warn on marginal, leave nuanced desirability to downstream.**

This layer is NOT a ranking engine — it does not score or prefer candidates.

## Modules

### 1. Quote Sanity (`hygiene/quote_sanity.py`)

**`run_quote_sanity(candidates, *, wide_leg_spread_ratio=1.0)`**

| Check | Scope | Action | Code |
|-------|-------|--------|------|
| Negative bid | Per-leg | Reject | `v2_negative_bid` |
| Negative ask | Per-leg | Reject | `v2_negative_ask` |
| Wide leg spread | Per-leg | Warn | `v2_warn_wide_leg_spread` |
| Spread pricing impossible | Candidate | Reject | `v2_spread_pricing_impossible` |

**Spread pricing impossible:** For credit spreads, verifies `short.bid - long.ask > 0`. For debit spreads, verifies `long.ask - short.bid > 0`. If the spread can't produce the expected credit/debit, rejects.

### 2. Liquidity Sanity (`hygiene/liquidity_sanity.py`)

**`run_liquidity_sanity(candidates, *, low_oi_warn=10, low_volume_warn=5, wide_spread_warn_pct=0.50)`**

| Check | Scope | Action | Code |
|-------|-------|--------|------|
| Dead leg (OI=0 AND vol=0) | Per-leg | Reject | `v2_dead_leg` |
| Low OI | Per-leg | Warn | `v2_warn_low_oi` |
| Low volume | Per-leg | Warn | `v2_warn_low_volume` |
| Wide composite spread | Candidate | Warn | `v2_warn_wide_composite_spread` |

**Dead leg:** Both OI=0 AND volume=0 means no market exists. This is the only hard-reject for liquidity. All other liquidity concerns are warnings.

### 3. Duplicate Suppression (`hygiene/dedup.py`)

**`run_dedup(candidates, *, key_fn=None)`** → `(list[V2Candidate], DedupResult)`

**Default dedup key:** `(symbol, strategy_id, expiration, frozenset((side, strike, option_type)))`

**Keeper selection policy** (deterministic, best tuple wins):
1. Quote quality score — sum of leg quote quality (+1 valid, -1 inverted, -0.5 missing)
2. Liquidity score — `min_oi × min_vol` across legs
3. Diagnostics richness — count of check results
4. Candidate ID — tie-breaker for full determinism

| Code | Action |
|------|--------|
| `v2_exact_duplicate` | Reject (suppressed duplicate) |
| `v2_pass_dedup_unique` | Pass (unique or keeper) |

**DedupResult** provides: `total_before`, `total_after`, `duplicates_suppressed`, `groups`, `keeper_ids`, `suppressed_ids`, `to_dict()`.

## Integration

### Phase D2 Glue (`phases.py`)

```python
phase_d2_trust_hygiene(candidates, *, dedup_key_fn=None)
→ (list[V2Candidate], hygiene_summary)
```

Runs quote sanity → liquidity sanity → dedup in sequence.

### Base Scanner Hook

`BaseV2Scanner.family_dedup_key(candidate)` — optional override for family-specific dedup keys.

### Phase Trace

After D2, the phase trace includes:
```
constructed → structural_validation → quote_liquidity_sanity → trust_hygiene → recomputed_math → normalized
```

## Reject vs. Warn Boundaries

| Condition | Action | Rationale |
|-----------|--------|-----------|
| Negative bid/ask | Reject | Structurally impossible quote |
| Dead leg (OI=0, vol=0) | Reject | No market exists |
| Spread pricing impossible | Reject | Economically nonsensical |
| Exact duplicate | Reject | Spam suppression |
| Low OI/volume (non-zero) | Warn | Thin but possibly tradeable |
| Wide leg spread | Warn | Poor fill quality, not broken |
| Wide composite spread | Warn | Poor fill quality, not broken |

## Reason Codes Added

### Reject (5)
- `v2_negative_bid` → canonical: `invalid_quote`
- `v2_negative_ask` → canonical: `invalid_quote`
- `v2_spread_pricing_impossible` → canonical: `invalid_quote`
- `v2_dead_leg` → canonical: `missing_open_interest`
- `v2_exact_duplicate` → canonical: `duplicate_suppressed`

### Warning (5)
- `v2_warn_wide_leg_spread`
- `v2_warn_low_oi`
- `v2_warn_low_volume`
- `v2_warn_wide_composite_spread`
- `v2_warn_near_duplicate_suppressed`

### Pass (3)
- `v2_pass_quote_sanity_clean`
- `v2_pass_liquidity_sanity_ok`
- `v2_pass_dedup_unique`

## Family Extension

New V2 scanner families can customize:
- Override `family_dedup_key(candidate)` for family-specific dedup key logic
- Thresholds are configurable per call via keyword arguments
- All checks use `DiagnosticsBuilder(source_phase="D2")` for traceability
