# Options Scanner Overview

> **Location:** `docs/scanners/options/`
> **Last updated:** This document

---

## Scanner Registry

All options scanners are registered in `pipeline_scanner_stage.py` (lines 479–486):

| Scanner Key | Plugin ID | Plugin File | Direction | Strategy Type |
|-------------|-----------|-------------|-----------|---------------|
| `put_credit_spread` | `credit_spread` | `strategies/credit_spread.py` | — | Credit (sell premium) |
| `call_credit_spread` | `credit_spread` | `strategies/credit_spread.py` | — | Credit (sell premium) |
| `iron_condor` | `iron_condor` | `strategies/iron_condor.py` | — | Credit (sell premium) |
| `butterfly_debit` | `butterflies` | `strategies/butterflies.py` | — | Debit (buy structure) |
| `put_debit` | `debit_spreads` | `strategies/debit_spreads.py` | `put` | Debit (directional) |
| `call_debit` | `debit_spreads` | `strategies/debit_spreads.py` | `call` | Debit (directional) |

**6 scanner keys → 4 plugin implementations.** Two plugins serve two keys each:
- `credit_spread` serves both `put_credit_spread` and `call_credit_spread`
- `debit_spreads` serves both `put_debit` and `call_debit`

---

## Plugin Architecture

All plugins inherit from `StrategyPlugin` (ABC) in `strategies/base.py` and implement 4 required phases:

```
build_candidates(inputs) → list[dict]      # Construct spread candidates from raw chains
enrich(candidates, inputs) → list[dict]     # Per-candidate pricing, metrics, legs[]
evaluate(trade) → (pass: bool, reasons[])   # Quality-gate filtering
score(trade) → (rank: float, tie_breaks{})  # Ranking score + tie-break dict
```

Two optional trace hooks:
- `build_near_miss_entry()` — extract diagnostics from rejected candidates
- `compute_enrichment_counters()` — derive quote/spread/POP counters

### Orchestration

`StrategyService.generate()` (`strategy_service.py`) orchestrates:
1. **Preset resolution** — `_apply_request_defaults()` merges preset thresholds via `setdefault()` (user overrides win)
2. **Expiration resolution** — `_resolve_expirations()` fetches and filters by DTE window (up to 4 per symbol)
3. **Chain fetch** — Snapshots with contract data per (symbol, expiration)
4. **4-phase pipeline** — build → enrich → evaluate → score (via plugin)
5. **Post-processing** — context scoring, dedup by `trade_key`, final sort, normalization

---

## Cross-Scanner Comparison

### POP Models

| Plugin | Model | Formula |
|--------|-------|---------|
| Credit spread | Normal CDF | `Φ(z)` where `z = (breakeven - spot) / expected_move` |
| Iron condor | Normal CDF | `Φ(z_high) - Φ(z_low)` between breakevens |
| Butterfly | Normal CDF | `Φ(z_high) - Φ(z_low)` between breakevens |
| Debit spreads | **3-tier hierarchy** | delta_approx → breakeven_lognormal → refined |

The debit spreads plugin has a significantly more sophisticated POP model than the other three plugins.

### EV Computation

| Plugin | Method |
|--------|--------|
| Credit spread | Binary: `p × profit - (1-p) × loss` |
| Iron condor | Numerical integration (201 points, ±4 × EM, Gaussian-weighted) |
| Butterfly | Numerical integration (201 points, ±4 × EM, Gaussian-weighted) |
| Debit spreads | Binary: `p × profit - (1-p) × loss` |

### Liquidity Approach

| Plugin | OI/Vol Gates | Liquidity Score |
|--------|-------------|-----------------|
| Credit spread | Hard reject in evaluate | ✓ |
| Iron condor | **No hard gates** — scoring only | ✓ (worst-leg OI/vol/spread) |
| Butterfly | 20% of preset value (hidden multiplier) | ✓ (0.45×OI + 0.30×vol + 0.25×spread) |
| Debit spreads | DQ-mode-aware gates | ✓ (via compute_rank_score) |

### Ranking Formulas

| Plugin | Components |
|--------|-----------|
| Credit spread | 0.30×edge + 0.22×ror + 0.20×pop + 0.18×liquidity + 0.10×tqs |
| Iron condor | 0.34×theta + 0.26×distance + 0.20×symmetry + 0.20×liquidity - penalties |
| Butterfly | 0.30×efficiency + 0.22×center + 0.22×liquidity + 0.12×ev + 0.14×gamma - penalties |
| Debit spreads | Delegated to `compute_rank_score()` in ranking.py (0–100 scale) |

---

## Shared Preset Structure

All plugins use the same 4-tier preset hierarchy:

| Tier | Philosophy |
|------|-----------|
| **Strict** | Highest quality filters. Best liquidity, tightest spreads, highest POP/EV. Fewest candidates. |
| **Conservative** | Relaxed from strict. Still favors quality. |
| **Balanced** | Default. Moderate filters on all dimensions. |
| **Wide** | Maximum scan breadth. Minimal filtering. Most candidates. Used for exploration. |

Resolution: `_apply_request_defaults()` uses `setdefault()` — user-supplied values always override preset values. Unknown preset names fall back to `"balanced"`.

---

## Critical Bugs and Issues

### Cross-Scanner

| Issue | Severity | Affected |
|-------|----------|----------|
| `call_credit_spread` never builds call spreads | **CRITICAL** | credit_spread plugin |
| Silent drops without rejection tracking | **HIGH** | iron_condor (enrich: net_credit ≤ 0, max_loss ≤ 0) |
| `short_delta_abs` stores long delta | **HIGH** | debit_spreads |
| Duplicate filters across phases | **HIGH** | iron_condor (penny-wing 2×, sigma 3×, symmetry 2×) |
| Spread quote inversion not validated | **HIGH** | debit_spreads |
| Hidden 0.2× OI/vol multiplier | **HIGH** | butterfly |

### Complexity Patterns (Systemic)

1. **Threshold resolution has 3 layers** — Presets → `_apply_request_defaults()` fallbacks → hardcoded defaults in `evaluate()`. Makes it hard to know which value applies.
2. **Output field aliases** — Multiple field names for the same data (backward compat). Worst in iron_condor (~80 keys) and butterfly.
3. **Quality gates in scanner evaluate phase** — All plugins gate on POP/EV/RoR in their evaluate() methods. Under the new philosophy (scan wide, reject junk, let downstream narrow), most of these should move downstream.
4. **Inconsistent POP models** — Credit spreads and iron condors use simple normal CDF; debit spreads uses a 3-tier model. No plugin uses the `POP_SOURCE_MODEL` or `POP_SOURCE_FALLBACK` constants from base.py.

---

## New Philosophy Alignment

The planned rebuild follows this principle: **Scanners should scan broadly, reject only obvious junk, and let downstream ranking/selection/analysis stages do the narrowing.**

### What should stay in scanners:
- Structural validity: legs exist, pricing available, credit > 0 / debit > 0, credit < width / debit < width
- Basic execution feasibility: non-zero bids, non-inverted quotes
- Safety caps: max_candidates limit

### What should move downstream:
- POP thresholds
- EV/EV-to-risk thresholds
- RoR thresholds
- Symmetry thresholds (iron condor)
- Cost efficiency thresholds (butterfly)
- Bid-ask spread % thresholds
- OI/volume minimum gates

### What should be simplified:
- Preset complexity (fewer knobs, wider defaults)
- Duplicate filters across phases (one check per concern, one phase)
- Output field aliases (canonical names only)
- Threshold resolution layers (one source path per threshold)

---

## Per-Scanner Documentation

| Scanner | Doc |
|---------|-----|
| Put/Call Credit Spread | [credit-spread.md](credit-spread.md) |
| Iron Condor | [iron-condor.md](iron-condor.md) |
| Butterfly (Debit/Iron) | [butterfly.md](butterfly.md) |
| Put/Call Debit Spread | [debit-spreads.md](debit-spreads.md) |

---

## File Reference

| File | Role |
|------|------|
| `app/services/strategies/base.py` | `StrategyPlugin` ABC, POP source constants |
| `app/services/strategies/credit_spread.py` | Credit spread plugin |
| `app/services/strategies/iron_condor.py` | Iron condor plugin |
| `app/services/strategies/butterflies.py` | Butterfly plugin |
| `app/services/strategies/debit_spreads.py` | Debit spread plugin |
| `app/services/strategy_service.py` | Orchestrator, preset definitions, resolution |
| `app/services/pipeline_scanner_stage.py` | Pipeline Step 6, scanner key → plugin dispatch |
| `app/services/strategies/calendars.py` | Calendar spread plugin (exists but NOT registered) |
| `app/services/strategies/income.py` | Income plugin (exists but NOT registered) |
