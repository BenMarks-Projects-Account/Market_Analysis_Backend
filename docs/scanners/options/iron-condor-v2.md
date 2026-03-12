# Iron Condor Scanner — V2

> **Family key:** `iron_condors`
> **Strategy ID:** `iron_condor`
> **Scanner class:** `IronCondorsV2Scanner` in `app/services/scanner_v2/families/iron_condors.py`
> **Registry entry:** `registry.py` — `implemented=True`, `leg_count=4`
> **Prompt:** 10

---

## 1. Purpose

V2 iron condor scanner — builds 4-leg iron condors as a composition
of two credit spread sides (put credit + call credit) using the trusted
V2 spread primitive infrastructure.

Replaces the legacy `IronCondorStrategyPlugin` filter-swamp with clean,
phase-disciplined construction and shared validation.

---

## 2. Architecture: Composition from Spread Primitives

Unlike the legacy scanner, V2 iron condors are **not** a monolithic
strategy plugin.  They are built by composing two credit spread sides
within each expiry bucket:

```
Narrowed Universe (Phase A)
    │
    ├── OTM Puts (strike < spot)
    │     └── All valid put credit spread sides (short > long)
    │
    ├── OTM Calls (strike > spot)
    │     └── All valid call credit spread sides (short < long)
    │
    └── Cross-product → 4-leg iron condor candidates
```

The non-overlap constraint (put_short < call_short) is **automatically
satisfied** because OTM puts < underlying < OTM calls.

---

## 3. Phase Pipeline

| Phase | What happens for iron condors |
|-------|------------------------------|
| **A — Narrowing** | Shared `narrow_chain()`. DTE 7–60. |
| **B — Construction** | `construct_candidates()` — spread side pairing. |
| **C — Structural** | Shared checks + family hook: 4 legs, 2P+2C, ordering, side widths. |
| **D — Quote/Liquidity** | Shared presence checks (bid/ask/OI/volume on all 4 legs). |
| **D2 — Trust Hygiene** | Quote sanity, liquidity sanity, dedup (4-leg dedup key). |
| **E — Math** | Family math override: condor-specific net credit, width, breakevens, POP. |
| **F — Normalize** | Shared: set passed/downstream_usable, collect pass reasons. |

---

## 4. Leg Ordering Convention

Stable ordering — do not reorder:

| Index | Side  | Type | Description |
|-------|-------|------|-------------|
| 0     | short | put  | Short put (closer to ATM) |
| 1     | long  | put  | Long put (wing, lower strike) |
| 2     | short | call | Short call (closer to ATM) |
| 3     | long  | call | Long call (wing, higher strike) |

**Geometry constraint:** `put_long < put_short < call_short < call_long`

---

## 5. Family Math Formulas

All derived fields with their input sources:

| Field | Formula | Inputs |
|-------|---------|--------|
| `put_side_credit` | `put_short.bid - put_long.ask` | legs[0].bid, legs[1].ask |
| `call_side_credit` | `call_short.bid - call_long.ask` | legs[2].bid, legs[3].ask |
| `net_credit` | `put_side_credit + call_side_credit` | above |
| `put_width` | `put_short.strike - put_long.strike` | legs[0].strike, legs[1].strike |
| `call_width` | `call_long.strike - call_short.strike` | legs[3].strike, legs[2].strike |
| `width` | `max(put_width, call_width)` | above (effective risk width) |
| `max_profit` | `net_credit × 100` | net_credit |
| `max_loss` | `(width - net_credit) × 100` | width, net_credit |
| `breakeven_low` | `put_short.strike - net_credit` | legs[0].strike, net_credit |
| `breakeven_high` | `call_short.strike + net_credit` | legs[2].strike, net_credit |
| `POP` | `1 - |delta_put_short| - |delta_call_short|` | legs[0].delta, legs[2].delta |
| `EV` | `POP × max_profit - (1-POP) × max_loss` | above |
| `RoR` | `max_profit / max_loss` | above |
| `Kelly` | `POP - (1-POP) / RoR` | above |

All formulas are traced in `math.notes` for auditability.

---

## 6. Construction Controls

| Parameter | Default | Description |
|-----------|---------|-------------|
| `generation_cap` | 50,000 | Max candidates per symbol (context override) |
| `max_wing_width` | $50 | Max per-side width in dollars (structural bound) |
| `dte_min` | 7 | Minimum DTE |
| `dte_max` | 60 | Maximum DTE |

---

## 7. Reason Codes

### New (Prompt 10)

| Code | Category | Severity | Description |
|------|----------|----------|-------------|
| `v2_ic_invalid_geometry` | structural | error | Strike ordering or side width violation |

### Reused from shared V2

- `v2_malformed_legs` — wrong leg count or type balance
- All shared quote, liquidity, math, and hygiene codes

---

## 8. Math Verification Extensions

Three shared verification functions were extended for iron condors
(in `validation/math_checks.py`):

- **`verify_width`** — Uses `max(put_width, call_width)` instead of
  `max(strikes) - min(strikes)` fallback.
- **`verify_net_credit_or_debit`** — Computes 4-leg credit from
  `(ps.bid - pl.ask) + (cs.bid - cl.ask)`.
- **`verify_breakeven`** — Checks both breakevens, takes worst result.

---

## 9. What V2 Does NOT Reimplement

These legacy features are deliberately excluded:

- Sigma distance filtering (desirability gate, not structural)
- Penny-wing detection (downstream scoring concern)
- Wing width targeting (V2 generates all valid combinations)
- Symmetry scoring (downstream scoring concern)
- Expected move calculations (not needed for construction)
- Per-leg diagnostics duplication (shared hygiene handles this)

---

## 10. Test Coverage

`tests/test_v2_iron_condors.py` — 51 tests across 9 test classes:

1. **TestConstruction** (10) — OTM geometry, combinatorial count, caps
2. **TestStructuralChecks** (7) — leg count, type balance, ordering, widths
3. **TestFamilyMath** (12) — credit, profit/loss, breakevens, POP, EV, RoR, Kelly
4. **TestMathVerification** (6) — width, credit, breakeven IC paths
5. **TestHygieneIntegration** (4) — quote/liquidity/dedup on 4-leg candidates
6. **TestEndToEnd** (2) — full pipeline via `run()`
7. **TestReasonCodeRegistry** (3) — new code registered correctly
8. **TestRegistry** (4) — family metadata, scanner loading
9. **TestBuildCondorCandidate** (3) — construction helper

---

## 11. Comparison / Cutover Readiness

The V2 iron condor produces `V2Candidate` objects compatible with
the comparison harness.  The canonical taxonomy mapping for
`v2_ic_invalid_geometry` is `"invalid_geometry"`.  Cutover from
legacy `IronCondorStrategyPlugin` can proceed using the same
side-by-side comparison approach used for vertical spreads.
