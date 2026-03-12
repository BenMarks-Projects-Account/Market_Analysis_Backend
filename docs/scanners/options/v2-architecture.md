# Options Scanner Core V2 — Architecture

> **Status:** Active workstream. Foundation defined; family implementations in progress.
> **Created:** 2026-03-11

---

## 1. Philosophy

V2 replaces the current options scanner subsystem with a simpler, more
trustworthy architecture built alongside the legacy scanners and cut over
family-by-family.

### Core principles

| # | Principle | What it means |
|---|-----------|---------------|
| 1 | **Scan wide** | Generate all structurally valid candidates. Let downstream stages narrow. |
| 2 | **Reject only junk** | Scanner-time rejection is limited to structural invalidity, impossible pricing, and broken quotes. |
| 3 | **No strictness levels** | One operating mode. No preset jungle. No Strict/Balanced/Wide branching. |
| 4 | **Explicit pass/reject** | Every candidate carries a diagnostics record explaining why it passed or was rejected. |
| 5 | **Recomputed math** | Core metrics (credit, debit, max profit, max loss, POP, EV, RoR) are recomputed from leg quotes — never inherited from upstream blobs. |
| 6 | **Normalized contracts** | Every family produces the same `V2Candidate` shape. |
| 7 | **Family-by-family cutover** | Legacy and V2 coexist. Families migrate one at a time. |
| 8 | **Debug-friendly** | No silent drops. No hidden multipliers. No alias clutter. |

### What changed vs V1

| Area | V1 (legacy) | V2 |
|------|-------------|-----|
| Strictness | 4 preset levels per strategy × 12+ knobs | None. One wide-scan mode. |
| Heavy filtering | Scanner applies POP/EV/RoR hard gates | Scanner validates structure only; POP/EV/RoR move downstream. |
| Candidate shape | Loose dict with per-plugin extras | Typed `V2Candidate` dataclass. |
| Diagnostics | Filter trace bolted on as post-hoc dict | `V2Diagnostics` is a first-class part of every candidate. |
| Duplicate quotes | quote validity checked repeatedly at build, enrich, evaluate | Single `validate_quotes` phase. |
| Plugin phases | build → enrich → evaluate → score (4 phases, score is scanner-time) | construct → validate → normalize (3 phases, scoring moves downstream). |

---

## 2. Scanner-time vs Downstream Responsibilities

This is the hard boundary. V2 scanners own everything above the line.
Everything below the line is downstream (Step 7+).

### Scanner-time (V2 owns)

| Responsibility | Description |
|----------------|-------------|
| **Data loading** | Fetch option chains from Tradier. Fetch underlying quote. |
| **Expiration narrowing** | Filter to DTE window (structural: e.g., 1–90 DTE). |
| **Candidate construction** | Build all valid leg combinations for the family. |
| **Structural validation** | Reject malformed legs, mismatched expiries, invalid widths, impossible pricing. |
| **Quote sanity** | Reject inverted bid/ask, missing quotes on required legs, zero mid. |
| **Liquidity sanity** | Reject legs with null OI or null volume (data-quality rejection, not threshold). |
| **Recomputed core math** | Compute net credit/debit, max profit, max loss, width from leg quotes. Flag (don't hard-reject) if POP/EV/RoR cannot be computed. |
| **Candidate normalization** | Package into `V2Candidate` with full diagnostics. |
| **Diagnostics packaging** | Attach `V2Diagnostics` to every candidate (pass or reject). |

### Downstream (NOT scanner-time)

| Responsibility | Where it belongs |
|----------------|-----------------|
| POP/EV/RoR hard gating | Step 7 (candidate selection) or later |
| Quality ranking / composite score | Step 7 (candidate selection) |
| Market-context preference (regime, volatility regime) | Step 8 (shared context) / Step 9 (enrichment) |
| Portfolio fit / position limits | Step 11 (portfolio policy) |
| Nuanced trade desirability | Step 12 (trade decision packet) |
| Final recommendation | Step 14 (final model execution) |
| Credit/debit minimum thresholds | Downstream preference, not scanner gate |
| Open interest / volume minimum thresholds | Downstream preference, not scanner gate |

### Scanner-time reject reasons (exhaustive)

These are the ONLY reasons V2 will reject a candidate at scanner-time:

| Reject reason | Code | Description |
|---------------|------|-------------|
| Malformed leg structure | `v2_malformed_legs` | Wrong number of legs, missing strike/type. |
| Mismatched expiries | `v2_mismatched_expiry` | Legs have different expirations (except calendars/diagonals). |
| Invalid width | `v2_invalid_width` | Width ≤ 0 or structurally impossible. |
| Impossible pricing | `v2_impossible_pricing` | Credit ≥ width (degenerate), or debit ≥ width. |
| Non-positive credit/debit | `v2_non_positive_credit` | Credit ≤ 0 when credit is required, or debit ≤ 0 when debit is required. |
| Inverted bid/ask | `v2_inverted_quote` | Ask < bid on a required leg. |
| Missing required quote | `v2_missing_quote` | No bid/ask data returned for a required leg. |
| Zero mid | `v2_zero_mid` | Both bid and ask are zero/missing on a required leg. |
| Missing OI (null) | `v2_missing_oi` | Open interest is null/absent on a required leg. |
| Missing volume (null) | `v2_missing_volume` | Volume is null/absent on a required leg. |
| Impossible max loss | `v2_impossible_max_loss` | Computed max loss is ≤ 0 or not finite. |
| Impossible max profit | `v2_impossible_max_profit` | Computed max profit is ≤ 0 or not finite. |

### Scanner-time does NOT reject for

- POP below any threshold
- EV below any threshold  
- RoR below any threshold
- Credit below a dollar amount
- OI below a count threshold
- Volume below a count threshold
- Bid-ask spread percentage exceeding a threshold
- DTE preferences (only structural DTE window)
- Distance/width preferences
- IV/RV ratio
- Kelly fraction

These are downstream concerns.

---

## 3. Architecture Layers

V2 scanners execute in 6 ordered phases. Each phase has a single responsibility.

```
Phase A ── Universe & Chain Loading (data narrowing)
        │  Load option chain from Tradier.
        │  Normalize raw contracts → V2OptionContract list.
        │  Filter expirations to structural DTE window.
        │  Narrow strikes by distance / moneyness / option-type.
        │  Deduplicate and group into V2ExpiryBucket.
        │  Produce V2NarrowedUniverse with diagnostics.
        │  (Multi-expiry mode for calendars/diagonals.)
        ▼
Phase B ── Candidate Construction
        │  Family-specific: build all valid leg combos.
        │  Vertical spreads: every (short, long) pair per expiration.
        │  Iron condors: every (put_short, put_long, call_short, call_long) combo.
        │  Butterflies: every (wing, body, wing) combo.
        │  Calendars: every (front, back) pair across expirations.
        ▼
Phase C ── Structural Validation
        │  Shared + family-specific structural checks.
        │  Rejects: malformed legs, mismatched expiry, invalid width,
        │  impossible pricing, non-positive credit/debit.
        │  Each rejection → reason code + diagnostics.
        ▼
Phase D ── Quote & Liquidity Sanity
        │  Shared across all families.
        │  Rejects: inverted quotes, missing quotes, zero mid,
        │  missing OI, missing volume.
        │  Each rejection → reason code + diagnostics.
        ▼
Phase E ── Recomputed Math
        │  Recompute from leg quotes (never copy from upstream):
        │    net_credit / net_debit
        │    max_profit / max_loss
        │    width
        │    POP (if computable — flag if not, DON'T reject)
        │    EV (if POP available)
        │    RoR (if max_loss available)
        │  Attach recomputed values + computation notes.
        ▼
Phase F ── Normalization & Packaging
        │  Package into V2Candidate dataclass.
        │  Attach V2Diagnostics (all checks, pass reasons, warnings).
        │  Assign candidate_id.
        │  Set downstream_usable = True for passing candidates.
        │  Set timestamp / lineage / version.
        ▼
        Output: list[V2Candidate]
```

---

## 4. Module Layout

```
BenTrade/backend/app/services/scanner_v2/
├── __init__.py                 # Public API surface
├── contracts.py                # V2Candidate, V2Diagnostics, V2Leg, V2RecomputedMath
├── base_scanner.py             # BaseV2Scanner ABC — shared 6-phase runner
├── phases.py                   # Shared phase implementations (C, D, E, F)
├── registry.py                 # Family registry + metadata
├── migration.py                # V2/legacy routing, side-by-side dispatch
├── data/                       # Shared data-narrowing framework (Phase A)
│   ├── __init__.py             # Public API: narrow_chain, V2NarrowedUniverse, etc.
│   ├── contracts.py            # V2NarrowingRequest, V2OptionContract, V2ExpiryBucket, etc.
│   ├── chain.py                # Chain normalization: raw Tradier → V2OptionContract
│   ├── expiry.py               # Expiry narrowing: DTE windows, multi-expiry
│   ├── strikes.py              # Strike-window narrowing: distance, moneyness, dedup
│   └── narrow.py               # Orchestrator: narrow_chain() full pipeline
├── comparison/                 # Side-by-side comparison harness (Prompt 2)
│   ├── __init__.py
│   ├── contracts.py
│   ├── equivalence.py
│   ├── harness.py
│   ├── snapshots.py
│   └── fixtures.py
└── families/
    ├── __init__.py
    ├── vertical_spreads.py     # Phase B for put/call credit & debit spreads
    ├── iron_condors.py         # Phase B for iron condors
    ├── butterflies.py          # Phase B for debit & iron butterflies
    └── calendars.py            # Phase B for calendar & diagonal spreads
```

### Separation of concerns

| Module | Owns |
|--------|------|
| `contracts.py` | All V2 data shapes (candidate, diagnostics, legs, math). No logic. |
| `base_scanner.py` | The 6-phase runner. Calls family-specific Phase B, then shared C→F. |
| `phases.py` | Shared implementations of Phase C (structural), D (quote/liquidity), E (math), F (normalization). |
| `data/` | Shared data-narrowing framework (Phase A): chain normalization, expiry narrowing, strike-window narrowing, underlying snapshot, diagnostics. Produces `V2NarrowedUniverse`. |
| `registry.py` | Maps `strategy_id` → family module + metadata. Single source of truth for which families exist. |
| `migration.py` | Routing seam: decides whether a scanner_key runs legacy or V2. Enables side-by-side comparison. |
| `comparison/` | Side-by-side harness: frozen snapshots, equivalence matching, structured diff reports. |
| `families/*.py` | Family-specific Phase B (candidate construction) and any family-specific structural validation rules. |

---

## 5. Strategy Family Layering

### Shared framework

All families share:
- Phase A (data loading) — provided by the runner
- Phase C (structural validation) — common checks + family-specific hooks
- Phase D (quote/liquidity sanity) — identical across all families
- Phase E (recomputed math) — common formulas + family-specific overrides
- Phase F (normalization) — identical across all families

### Family-specific

Each family provides:
- **Phase B implementation** — candidate construction logic
- **Family-specific structural checks** (optional) — e.g., iron condors can validate wing symmetry
- **Family-specific math overrides** (optional) — e.g., butterflies have different max-profit formula

### Family registry

| Family key | Strategy IDs | Leg count | Construction |
|------------|-------------|-----------|--------------|
| `vertical_spreads` | `put_credit_spread`, `call_credit_spread`, `put_debit`, `call_debit` | 2 | Every (short, long) pair per expiration |
| `iron_condors` | `iron_condor` | 4 | Every (put_short, put_long, call_short, call_long) combo |
| `butterflies` | `butterfly_debit`, `iron_butterfly` | 3–4 | Every (wing, body, wing) combo |
| `calendars` | `calendar_spread`, `calendar_call_spread`, `calendar_put_spread` | 2 | Every (front, back) pair across expirations |

---

## 6. Migration & Cutover Strategy

### Coexistence model

During migration, both legacy and V2 scanners exist in the codebase:

```
pipeline_scanner_stage.py
├── scanner_stage_handler()
│   ├── _select_scanners()
│   │   ├── legacy registry (existing)
│   │   └── V2 registry (new)
│   ├── _default_scanner_executor()  ← legacy path
│   └── _v2_scanner_executor()       ← V2 path
│
│   migration.py decides which executor to use per scanner_key
```

### Routing

`migration.py` exports a `get_scanner_version(scanner_key) → "v1" | "v2"` function.

During migration:
1. **Default:** All scanners run legacy (`v1`).
2. **Per-family cutover:** Change the mapping for one family at a time.
3. **Side-by-side mode:** Run both and compare (Prompt 2 builds the comparison harness).
4. **Full cutover:** All families on V2. Legacy code deleted.

### Migration phases

| Phase | State | Description |
|-------|-------|-------------|
| **Phase 0** (current prompt) | Foundation | V2 contracts, scaffolding, registry. All traffic still on legacy. |
| **Phase 1** (Prompt 2) | Comparison harness | Side-by-side mode: run both, diff output, track discrepancies. |
| **Phase 2** (Prompt 3+) | First family | Vertical spreads V2 fully implemented + validated via comparison. |
| **Phase 3** | Family-by-family | Iron condors, butterflies, calendars migrated one at a time. |
| **Phase 4** | Cutover | All families on V2. Legacy code marked for deletion. |
| **Phase 5** | Cleanup | Legacy scanner code removed. V2 becomes the only path. |

### Cutover criteria (per family)

Before cutting a family from legacy to V2:
1. ✅ V2 produces structurally valid candidates for all test symbols.
2. ✅ V2 candidate count ≥ legacy candidate count (V2 scans wider).
3. ✅ Every legacy-accepted candidate appears in V2 output (superset check).
4. ✅ V2 diagnostics are complete and correct.
5. ✅ Downstream stages (Steps 7–14) can consume V2 candidates without error.
6. ✅ No regression in pipeline end-to-end tests.

---

## 7. Contracts Overview

### V2Candidate

The normalized output for every candidate (pass or reject). See `contracts.py` for the full dataclass.

Key areas:
- **Identity:** `candidate_id`, `scanner_key`, `strategy_id`, `family_key`
- **Symbol/underlying:** `symbol`, `underlying_price`
- **Expiry:** `expiration`, `dte`
- **Legs:** `list[V2Leg]` — structured, typed, per-leg data
- **Core pricing:** `V2RecomputedMath` — net_credit/debit, max_profit/loss, width, POP, EV, RoR
- **Diagnostics:** `V2Diagnostics` — all checks passed/failed, reasons, warnings
- **Status:** `passed`, `downstream_usable`
- **Lineage:** `contract_version`, `scanner_version`, `generated_at`

### V2Diagnostics

Attached to every candidate. See `contracts.py` for the full dataclass.

Key areas:
- **Structural checks:** list of `(check_name, passed, detail)`
- **Quote checks:** list of `(check_name, passed, detail)`
- **Liquidity checks:** list of `(check_name, passed, detail)`
- **Math checks:** list of `(check_name, passed, detail)`
- **Reject reasons:** list of reason codes (from V2 taxonomy)
- **Warnings:** list of warning messages (non-fatal)
- **Pass reasons:** list of reasons the candidate is valid

### V2Leg

Per-leg structured data:
- `index`, `side` (long/short), `strike`, `option_type` (put/call)
- `expiration`, `bid`, `ask`, `mid`
- `delta`, `gamma`, `theta`, `vega`, `iv`
- `open_interest`, `volume`

### V2RecomputedMath

All derived from leg quotes — never copied from upstream:
- `net_credit`, `net_debit`, `max_profit`, `max_loss`, `width`
- `pop`, `pop_source`, `ev`, `ev_per_day`, `ror`, `kelly`
- `breakeven` (list — some strategies have multiple breakevens)
- Computation notes / flags for each field

---

## 8. Extension Seams

These are the points where later prompts plug in:

| Seam | Module | Later prompt |
|------|--------|-------------|
| Comparison harness | `comparison/` | Prompt 2 ✅ |
| Data narrowing layer | `data/` | Prompt 3 ✅ |
| Vertical spreads family | `families/vertical_spreads.py` | Prompt 4 |
| Iron condors family | `families/iron_condors.py` | Prompt 5 |
| Butterflies family | `families/butterflies.py` | Prompt 6 |
| Calendars family | `families/calendars.py` | Prompt 7 |
| Scanner stage integration | `pipeline_scanner_stage.py` | Prompt 2 (routing seam) |
| Downstream candidate selection | `pipeline_candidate_selection_stage.py` | V2-aware selection |
| Data loading helpers | `base_scanner.py` | Shared Tradier chain loading |

---

## 9. Data Narrowing Framework (Prompt 3)

The data narrowing layer centralizes all chain loading, normalization, expiry narrowing,
and strike-window narrowing into a shared module (`scanner_v2/data/`).  Family builders
consume a `V2NarrowedUniverse` instead of raw chain dicts and loose expiration lists.

### Purpose

- **Eliminate ~200 lines of duplicated chain-narrowing code** across 4 legacy families.
- **Normalize Tradier data once** into typed `V2OptionContract` objects.
- **Produce full diagnostics** for every narrowing run (what was loaded, kept, dropped, why).
- **Support multi-expiry families** (calendars/diagonals) with near/far DTE windows.

### Pipeline stages

```
Raw Tradier chain (dict)
         │
    1. normalize_chain()     → list[V2OptionContract]
         │
    2. narrow_expirations()  → filtered by DTE window
         │
    3. narrow_strikes()      → filtered by distance/moneyness/type
         │                     grouped into V2ExpiryBucket
         │
    4. Package               → V2NarrowedUniverse
                               (underlying + buckets + diagnostics + request)
```

### Key contracts

| Type | Role |
|------|------|
| `V2NarrowingRequest` | Parameters: DTE window, option types, distance bounds, moneyness, multi-expiry. |
| `V2OptionContract` | Normalized option contract (strike, bid/ask/mid, greeks, OI, volume, quality flags). |
| `V2StrikeEntry` | One strike within an expiry bucket (deduplicated). |
| `V2ExpiryBucket` | All narrowed contracts for one expiration date (with helper methods). |
| `V2UnderlyingSnapshot` | Normalized underlying price and context. |
| `V2NarrowedUniverse` | Complete pipeline output for family builders. |
| `V2NarrowingDiagnostics` | Full trace: loaded/kept/dropped counts, reason codes, quality tallies. |

### How families use it

```python
class VerticalSpreadsV2Scanner(BaseV2Scanner):

    def build_narrowing_request(self, *, context=None):
        return V2NarrowingRequest(
            dte_min=self.dte_min,
            dte_max=self.dte_max,
            option_types=["put"],       # if scanning put credit spreads
            distance_min_pct=0.01,
            distance_max_pct=0.12,
            moneyness="otm",
        )

    def construct_candidates(self, *, narrowed_universe, **kw):
        for bucket in narrowed_universe.expiry_buckets.values():
            strike_map = bucket.get_strike_map()
            # Build all (short, long) pairs from the narrowed strikes...
```

### Narrowing ≠ rejection

Narrowing is "what's worth constructing from?" — it reduces the search space for
family builders.  It is NOT trade acceptance.  Candidates rejected at scanner-time
are rejected in Phases C–F with explicit reason codes.

---

## 10. Comparison Harness (Prompt 2)

The comparison harness enables side-by-side execution of legacy and V2 scanners on identical market data, producing structured diff reports.

### Philosophy

- **Legacy is reference, not truth.**  Differences are exposed, not auto-failed.
- **Same input, always.**  Both systems see the identical `ComparisonSnapshot` (frozen chain + underlying price).
- **Family-by-family.**  Compare one scanner_key at a time — no need to run the entire universe.
- **Trust over parity.**  The harness answers "is V2 more trustworthy?" not "does V2 match legacy exactly?"

### Architecture

```
ComparisonSnapshot (frozen input)
         │
    ┌────┴────┐
    ▼         ▼
 Legacy     V2
 Runner    Runner
    │         │
    ▼         ▼
legacy_result  v2_result
    │         │
    └────┬────┘
         ▼
  match_candidates()        ← equivalence.py
         │
         ▼
  compare_from_results()    ← harness.py
         │
         ▼
  ComparisonReport
```

### Candidate Equivalence

Candidates are matched by a **structural comparison key**:

```
"{symbol}|{strategy_id}|{expiration}|{sorted_strikes}"
```

Examples:
- `SPY|put_credit_spread|2026-03-20|585/590`
- `SPY|iron_condor|2026-04-17|500/510/530/540`

This works because two candidates with the same underlying, strategy, expiration, and strikes are structurally equivalent regardless of which system built them or what IDs they assigned.

### Comparison Report Fields

| Category | Fields |
|----------|--------|
| Identity | comparison_id, scanner_family, scanner_key, snapshot_id |
| Counts | legacy/v2 total_constructed/passed/rejected, overlap_count, legacy_only_count, v2_only_count |
| Matches | list of CandidateMatch (matched / legacy_only / v2_only) |
| Rejections | legacy_rejection_counts, v2_rejection_counts |
| Phase trace | legacy_stage_counts, v2_phase_counts |
| Trust signals | v2_caught_broken, v2_new_valid, v2_diagnostics_richer_count |
| Metrics | per-metric mean/max abs_diff and pct_diff across matched candidates |
| Summary | anomalies, conclusions |

### Snapshot Fixtures

Snapshots freeze market data for deterministic comparison:

| Fixture | Scenario |
|---------|----------|
| `fixture_spy_golden_put_spread` | 4-put chain, 2 valid credit spreads, all clean |
| `fixture_spy_bad_liquidity` | Missing OI, zero volume |
| `fixture_spy_wide_spreads` | Wide bid-ask, one inverted quote |
| `fixture_spy_empty_chain` | No options |
| `fixture_spy_golden_iron_condor` | 2 puts + 2 calls for valid IC |

### Module Layout

```
scanner_v2/comparison/
├── __init__.py          # Public API
├── contracts.py         # ComparisonReport, CandidateMatch, MetricDelta, etc.
├── equivalence.py       # build_comparison_key, match_candidates
├── harness.py           # compare_scanner_family, compare_from_results
├── snapshots.py         # build_snapshot, load_snapshot, save_snapshot
└── fixtures.py          # Pre-built test fixtures
```

### Usage in Family Cutover

When implementing a V2 family (e.g. vertical spreads in Prompt 3+):

1. Use `fixture_spy_golden_put_spread()` as frozen input.
2. Inject a real legacy runner and the new V2 runner.
3. Call `compare_scanner_family()`.
4. Inspect the `ComparisonReport` for:
   - Overlap percentage (structural equivalence)
   - V2 caught broken candidates legacy accepted?
   - V2 surfaced valid candidates legacy over-filtered?
   - Metric deltas within tolerance?
5. When satisfied, flip `_SCANNER_VERSION_MAP["put_credit_spread"] = "v2"`.

---

## 10. Cross-References

- Legacy scanner contract: [docs/standards/scanner-contract.md](../../standards/scanner-contract.md)
- Rejection taxonomy (legacy codes): [docs/standards/rejection-taxonomy.md](../../standards/rejection-taxonomy.md)
- Canonical trade contract: [docs/standards/canonical-contract.md](../../standards/canonical-contract.md)
- Data quality rules: [docs/standards/data-quality-rules.md](../../standards/data-quality-rules.md)
- V2 contracts (code): `BenTrade/backend/app/services/scanner_v2/contracts.py`
- V2 migration routing: `BenTrade/backend/app/services/scanner_v2/migration.py`
- V2 comparison harness: `BenTrade/backend/app/services/scanner_v2/comparison/`
