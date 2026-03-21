# Audit 5E — Registry, Configuration & Family Coordination

**Pass**: 5 — Options Scanner Construction & Candidate Quality  
**Prompt**: 5E  
**Scope**: `app/services/scanner_v2/registry.py`, `base_scanner.py`, family-level configs, `options_scanner_service.py`, `options_opportunity_runner.py`  
**Date**: 2026-03-21

---

## 1  Registry Architecture

### 1.1  Static Registry

**File**: [registry.py](BenTrade/backend/app/services/scanner_v2/registry.py)

The registry is a **module-level static list** of `V2FamilyMeta` dataclasses, built at import time:

```python
_FAMILY_REGISTRY: list[V2FamilyMeta] = [
    V2FamilyMeta(
        family_key="vertical_spreads",
        strategy_ids=["put_credit_spread", "call_credit_spread", "put_debit", "call_debit"],
        leg_count=2,
        module_path="app.services.scanner_v2.families.vertical_spreads",
        class_name="VerticalSpreadsV2Scanner",
        implemented=True,
    ),
    V2FamilyMeta(family_key="iron_condors",  strategy_ids=["iron_condor"], ...),
    V2FamilyMeta(family_key="butterflies",   strategy_ids=["butterfly_debit", "iron_butterfly"], ...),
    V2FamilyMeta(family_key="calendars",     strategy_ids=["calendar_call_spread", ...], ...),
]
```

Two derived lookup dictionaries are built at module scope:

```python
V2_FAMILIES: dict[str, V2FamilyMeta] = {fm.family_key: fm for fm in _FAMILY_REGISTRY}
_STRATEGY_TO_FAMILY: dict[str, str] = {sid: fm.family_key for fm in _FAMILY_REGISTRY for sid in fm.strategy_ids}
```

### 1.2  Lazy-Load + Cache Pattern

Scanner class instances are **lazily imported** on first use and **cached** in a module-level dict:

```python
_SCANNER_CACHE: dict[str, Any] = {}

def _load_family(meta: V2FamilyMeta) -> Any:
    if meta.family_key in _SCANNER_CACHE:
        return _SCANNER_CACHE[meta.family_key]
    mod = importlib.import_module(meta.module_path)
    cls = getattr(mod, meta.class_name)
    instance = cls()
    _SCANNER_CACHE[meta.family_key] = instance
    return instance
```

**Key detail**: The cache key is `family_key`, not `strategy_id`. All variants within a family (e.g., all 4 vertical spread types) share **one scanner instance**. This is correct because the family class is parameterized by `strategy_id` at each `run()` call.

### 1.3  Public API

| Function | Purpose |
|----------|---------|
| `is_v2_supported(strategy_id)` | Returns `True` if strategy_id has an `implemented=True` family |
| `get_v2_family(strategy_id)` | Returns `V2FamilyMeta` without instantiating the scanner |
| `get_v2_scanner(strategy_id)` | Lazy-loads + caches + returns the scanner instance. Raises `ValueError` (unknown) or `NotImplementedError` (not implemented) |

### 1.4  Initialization Flow

```
Module import → _FAMILY_REGISTRY built → V2_FAMILIES dict built → _STRATEGY_TO_FAMILY dict built
                                                                           ↓
First get_v2_scanner("put_credit_spread") call:
    _STRATEGY_TO_FAMILY["put_credit_spread"] → "vertical_spreads"
    V2_FAMILIES["vertical_spreads"] → V2FamilyMeta
    _load_family(meta) → importlib.import_module("...vertical_spreads") → VerticalSpreadsV2Scanner()
    _SCANNER_CACHE["vertical_spreads"] = instance
    return instance

Second get_v2_scanner("call_credit_spread") call:
    _STRATEGY_TO_FAMILY["call_credit_spread"] → "vertical_spreads"  
    _SCANNER_CACHE["vertical_spreads"] → same instance  ← cache hit
```

---

## 2  Complete Scanner Key → Family → Behavior Mapping

### 2.1  The 11-Key Map

| # | Scanner Key | Family Key | Family Class | DTE Range | Phase E Math | Phase C Family Checks | Dedup Key |
|---|-------------|-----------|--------------|-----------|-------------|----------------------|-----------|
| 1 | `put_credit_spread` | `vertical_spreads` | `VerticalSpreadsV2Scanner` | 1–90 | Default (vertical) | `v2_malformed_legs` | Default |
| 2 | `call_credit_spread` | `vertical_spreads` | `VerticalSpreadsV2Scanner` | 1–90 | Default (vertical) | `v2_malformed_legs` | Default |
| 3 | `put_debit` | `vertical_spreads` | `VerticalSpreadsV2Scanner` | 1–90 | Default (vertical) | `v2_malformed_legs` | Default |
| 4 | `call_debit` | `vertical_spreads` | `VerticalSpreadsV2Scanner` | 1–90 | Default (vertical) | `v2_malformed_legs` | Default |
| 5 | `iron_condor` | `iron_condors` | `IronCondorsV2Scanner` | 7–60 | IC override | `v2_ic_invalid_geometry` | Default |
| 6 | `butterfly_debit` | `butterflies` | `ButterfliesV2Scanner` | 7–60 | Butterfly override | 3-leg butterfly checks | Default |
| 7 | `iron_butterfly` | `butterflies` | `ButterfliesV2Scanner` | 7–60 | Butterfly override | 4-leg butterfly checks | Default |
| 8 | `calendar_call_spread` | `calendars` | `CalendarsV2Scanner` | 7–90 | Calendar override (partial None) | Multi-expiry checks | Calendar override |
| 9 | `calendar_put_spread` | `calendars` | `CalendarsV2Scanner` | 7–90 | Calendar override (partial None) | Multi-expiry checks | Calendar override |
| 10 | `diagonal_call_spread` | `calendars` | `CalendarsV2Scanner` | 7–90 | Calendar override (partial None) | Multi-expiry checks | Calendar override |
| 11 | `diagonal_put_spread` | `calendars` | `CalendarsV2Scanner` | 7–90 | Calendar override (partial None) | Multi-expiry checks | Calendar override |

### 2.2  What Varies Per Scanner Key Within a Family

| Family | Differentiation Between Keys | Mechanism |
|--------|------------------------------|-----------|
| **Vertical Spreads** | `option_type` (put/call) and `short_is_higher` (credit/debit) | `_VARIANT_CONFIG[strategy_id]` lookup in Phase B |
| **Iron Condors** | None — only one key | N/A |
| **Butterflies** | Debit (3-leg) vs Iron (4-leg) — different builder methods | `if strategy_id == "butterfly_debit"` dispatch in `construct_candidates()` |
| **Calendars** | `option_type` (call/put) and `is_diagonal` (same/different strike) | `_STRATEGY_CONFIG[strategy_id]` lookup in Phase B |

### 2.3  Narrowing Configuration (All Keys)

Every family uses the **same base `build_narrowing_request()`**:

```python
def build_narrowing_request(self, *, context=None):
    return V2NarrowingRequest(dte_min=self.dte_min, dte_max=self.dte_max)
```

**No family overrides `build_narrowing_request()`.**

This means **all 11 scanner keys** use only DTE window filtering. No key sets:
- `option_types` (both puts and calls pass through)
- `distance_min_pct` / `distance_max_pct` (no strike distance filter)
- `moneyness` (OTM/ITM/ATM filter disabled)
- `multi_expiry` (always False, even for calendars — per 5A-03)

---

## 3  BaseV2Scanner Hook System

### 3.1  Hook Method Inventory

| Hook | Default Behavior | Override Detection | Phase |
|------|-----------------|-------------------|-------|
| `build_narrowing_request()` | Returns `V2NarrowingRequest(dte_min, dte_max)` | Direct call (no detection) | A |
| `construct_candidates()` | **@abstractmethod** — must override | N/A | B |
| `family_structural_checks()` | Returns `[]` (no family checks) | Direct call (always called) | C |
| `family_math()` | Returns `None` → vertical default math | `type(self).family_math is BaseV2Scanner.family_math` | E |
| `family_dedup_key()` | Calls `candidate_dedup_key()` (generic) | `type(self).family_dedup_key is BaseV2Scanner.family_dedup_key` | D2 |

### 3.2  Override Detection Pattern

Two hooks use **identity-based override detection** via `type(self).method is BaseV2Scanner.method`:

```python
def _get_family_math_fn(self):
    if type(self).family_math is BaseV2Scanner.family_math:
        return None  # Use default vertical math
    # Wrap subclass method...
    return _fn

def _get_dedup_key_fn(self):
    if type(self).family_dedup_key is BaseV2Scanner.family_dedup_key:
        return None  # Use default from dedup module
    return self.family_dedup_key
```

This means:
- If a family **doesn't override** the hook → base behavior (default math / default dedup) is used directly in the phase function
- If a family **overrides** the hook → the subclass method is wrapped and injected into the phase function

### 3.3  Family Override Matrix

| Hook | Verticals | Iron Condors | Butterflies | Calendars |
|------|-----------|-------------|------------|-----------|
| `build_narrowing_request()` | — | — | — | — |
| `construct_candidates()` | ✅ | ✅ | ✅ | ✅ |
| `family_structural_checks()` | ✅ | ✅ | ✅ | ✅ |
| `family_math()` | — | ✅ | ✅ | ✅ |
| `family_dedup_key()` | — | — | — | ✅ |

**Pattern**: Consistent and clean. Every family overrides the required abstract method (`construct_candidates`) and the structural checks. Only families with non-vertical math override `family_math()`. Only calendars (multi-expiry) override `family_dedup_key()`.

### 3.4  Hook Resolution Order

```
Phase A: BaseV2Scanner.build_narrowing_request() → V2NarrowingRequest
    ↓ no override by ANY family
Phase B: Subclass.construct_candidates() → list[V2Candidate]
    ↓ ALWAYS from subclass (@abstractmethod)
Phase C: BaseV2Scanner._get_family_checks_fn() → wraps Subclass.family_structural_checks()
    ↓ shared checks first, then family checks
Phase D: phase_d_quote_liquidity_sanity() → no hooks
Phase D2: BaseV2Scanner._get_dedup_key_fn() → None (use default) | Subclass.family_dedup_key
    ↓ only calendars override
Phase E: BaseV2Scanner._get_family_math_fn() → None (use _recompute_vertical_math) | Subclass.family_math
    ↓ IC, butterflies, calendars override
Phase F: phase_f_normalize() → no hooks
```

---

## 4  Configuration Sources

### 4.1  Where Configuration Lives

| Parameter | Source | Hardcoded In | Overridable? |
|-----------|--------|-------------|-------------|
| DTE window (`dte_min`/`dte_max`) | Family class attribute | Each family module | No (class attribute, no external config) |
| Generation cap | Family module constant (`_DEFAULT_GENERATION_CAP`) | Each family module | Yes — via `context["generation_cap"]` |
| Max width / max wing width | Family module constant | Each family module | Yes — via `context["max_width"]` or `context["max_wing_width"]` |
| Max strike shift (diagonals) | Calendar module constant | calendars.py | Yes — via `context["max_strike_shift"]` |
| Min DTE spread (calendars) | Calendar module constant | calendars.py | Yes — via `context["min_dte_spread"]` |
| Variant config (option_type, short_is_higher) | Family module dict | vertical_spreads.py, calendars.py | No |
| `require_same_expiry` | Family class attribute | Each family module | No |
| Top-N | Runner config | options_opportunity_runner.py | Yes — `RunnerConfig.top_n` |
| Scanner keys to run | Runner config | options_opportunity_runner.py | Yes — `RunnerConfig.scanner_keys` |
| Credibility thresholds | Runner Stage 4 constants | options_opportunity_runner.py | No |

### 4.2  Single Place to See All Config?

**No.** Configuration is scattered across four layers:

```
Layer 1: Registry      → family_key, strategy_ids, module_path, class_name, implemented
Layer 2: Family class  → dte_min, dte_max, require_same_expiry, scanner_version
Layer 3: Family module → _DEFAULT_GENERATION_CAP, _DEFAULT_MAX_WIDTH, _VARIANT_CONFIG
Layer 4: Runner        → top_n, scanner_keys, credibility thresholds (MIN_PREMIUM, MAX_POP)
```

To understand the complete configuration for `put_credit_spread`:
1. Check `registry.py` → family_key = "vertical_spreads", implemented = True
2. Check `vertical_spreads.py` class → dte_min=1, dte_max=90
3. Check `vertical_spreads.py` constants → cap=50,000, max_width=$50
4. Check `_VARIANT_CONFIG` → option_type="put", short_is_higher=True
5. Check `base_scanner.py` → build_narrowing_request only uses dte_min/dte_max
6. Check `options_opportunity_runner.py` → credibility gate thresholds, top_n=30

### 4.3  Context Override Mechanism

The `context` dict is the single runtime override channel. It flows:

```
RunnerConfig → _stage_scan() → options_scanner_service.scan(context={...}) →
  scanner.run(context=context) → construct_candidates(context=context)
```

Each family extracts overrides from context in Phase B:
```python
generation_cap = int(context.get("generation_cap", _DEFAULT_GENERATION_CAP))
max_width = float(context.get("max_width", _DEFAULT_MAX_WIDTH))
```

**Currently no runner-level code sets any context overrides.** The context dict arrives at the family effectively empty unless explicitly populated by the caller.

---

## 5  Cross-Family Coordination

### 5.1  Shared State Between Scanner Keys

| Shared Resource | Mechanism | Scope |
|----------------|-----------|-------|
| **Option chain data** | Cached at `base_data_service` level | Per (symbol, expiration) |
| **Tradier API responses** | HTTP-level caching in Tradier client | Per API call |
| **Scanner instances** | `_SCANNER_CACHE` in registry.py | Per family_key (process lifetime) |

**The option chain is fetched once per (symbol, expiration)** at the `base_data_service` level. When multiple scanner keys run for the same symbol, they all receive the same merged chain dict. However, each scanner family receives a **fresh copy** — the chain dict is constructed in `_run_one()` for each (scanner_key, symbol) pair.

### 5.2  Chain Data Flow

```
options_scanner_service._run_one("put_credit_spread", "SPY"):
    ├─ get_expirations("SPY")           → [cached at BDS level]
    ├─ for exp in expirations:
    │     get_analysis_inputs("SPY", exp) → [cached at BDS level]
    ├─ merge into chain dict             → [new dict per call]
    └─ scanner.run(chain=chain)          → [independent run]

options_scanner_service._run_one("call_credit_spread", "SPY"):
    ├─ get_expirations("SPY")           → [cache HIT]
    ├─ for exp in expirations:
    │     get_analysis_inputs("SPY", exp) → [cache HIT]
    ├─ merge into chain dict             → [new dict per call]
    └─ scanner.run(chain=chain)          → [independent run]
```

**Each scanner key for the same symbol gets the same underlying data** (via BDS cache) but runs completely independently. Phase A narrowing is re-executed for each key.

### 5.3  Cross-Family Deduplication

**None exists.** Each scanner key produces an independent `V2ScanResult`. The workflow runner aggregates all candidates into one list:

```python
all_candidates: list[dict] = []
for sr in scan_results:
    all_candidates.extend(sr.get("candidates", []))
```

No cross-scanner dedup is performed. This means:

- A put credit spread ($530/$525) from the verticals family and the put side of an iron condor ($530/$525) can both appear in the top-30 as separate candidates
- A call debit spread and a put credit spread at the same strikes (equivalent payoffs for European options) are both constructed and ranked independently
- No inter-family awareness exists

### 5.4  Inter-Family Constraints

**None.** Families run independently with no knowledge of each other's results. There is no mechanism for:
- "Don't build IC if vertical already exists"
- "Prefer calendar over butterfly when IV term structure is favorable"
- "Skip debit spreads if credit spreads dominate"

---

## 6  Scanner Key Variants Within a Family

### 6.1  Vertical Spreads: 4 Variants

| Variant | option_type | short_is_higher | Description |
|---------|-------------|----------------|-------------|
| `put_credit_spread` | put | True | Short put (higher strike) + Long put (lower) |
| `call_credit_spread` | call | False | Short call (lower strike) + Long call (higher) |
| `put_debit` | put | False | Long put (higher) + Short put (lower) |
| `call_debit` | call | True | Long call (lower) + Short call (higher) |

**All 4 variants**:
- Use the same narrowing config (DTE 1-90, no strike/moneyness/distance filters)
- Use the same Phase B builder (`construct_candidates()`) parameterized by `_VARIANT_CONFIG`
- Use the same Phase E math (default `_recompute_vertical_math`)
- Use the same Phase C family checks
- Each runs as a completely separate (scanner_key, symbol) pair with independent narrowing and construction

**Structural redundancy**: For a given expiration, the put credit spread and put debit scanners process the **same put contracts** and generate the **same (S_low, S_high) pairs**. They differ only in which leg is short vs long. The narrowing phase runs twice, construction loops twice, and the generation cap is consumed twice for what is essentially the same geometric space.

### 6.2  Calendar Family: 4 Variants

| Variant | option_type | is_diagonal | Description |
|---------|-------------|-------------|-------------|
| `calendar_call_spread` | call | False | Same strike, call options, different expirations |
| `calendar_put_spread` | put | False | Same strike, put options, different expirations |
| `diagonal_call_spread` | call | True | Different strikes (±$10), call options |
| `diagonal_put_spread` | put | True | Different strikes (±$10), put options |

Same narrowing config (DTE 7-90). Same Phase E math (partial None). Same Phase D2 dedup key (includes both expirations).

### 6.3  Butterfly Family: 2 Variants

| Variant | Construction Method | Legs |
|---------|-------------------|------|
| `butterfly_debit` | `_construct_debit_butterflies()` | 3 (long/short/long, same option_type) |
| `iron_butterfly` | `_construct_iron_butterflies()` | 4 (long put/short put/short call/long call) |

Same narrowing config (DTE 7-60). Different Phase B builders with different geometry. Different Phase C structural checks (3-leg vs 4-leg). Both use butterfly family math (debit variant or iron variant).

---

## 7  Missing from Registry/Configuration

### 7.1  Enable/Disable Mechanism

**Partial.** The `implemented: bool` flag on `V2FamilyMeta` disables an entire family, but:
- It's a hardcoded flag — requires code change to toggle
- It operates at the family level, not scanner_key level
- You cannot disable `put_debit` while keeping `put_credit_spread`
- No runtime API to enable/disable

The `RunnerConfig.scanner_keys` field provides caller-level control:
```python
scanner_keys: tuple[str, ...] | list[str] = ALL_V2_SCANNER_KEYS
```
Callers can pass a subset of keys. But the default runs **all 11**.

### 7.2  Aggressiveness / Preset Mechanism

**None.** There is no Strict / Balanced / Wide preset system for scanner construction parameters. The scanner-contract docs reference presets, but the V2 scanner has no implementation:
- No DTE range presets (strict: 30-45, balanced: 14-60, wide: 7-90)
- No delta targeting presets
- No width range presets
- No credit minimum presets

The `context` dict could carry preset values, but no code generates or consumes them.

### 7.3  Adding a New Scanner Key

**Requirements**: To add a new scanner key (e.g., `broken_wing_butterfly`):

1. If **new family**: Create a new family module, add `V2FamilyMeta` to `_FAMILY_REGISTRY` in registry.py
2. If **existing family**: Add the strategy_id to the family's `strategy_ids` list in `_FAMILY_REGISTRY`, add variant config in the family module, update `construct_candidates()` to handle the new ID
3. Add the key to `ALL_V2_SCANNER_KEYS` in options_opportunity_runner.py

**Not fully open/closed**: Adding to an existing family requires modifying both registry.py and the family module. Adding a new family requires registry.py + a new module.

### 7.4  Versioning Support

**None.** Each family stores a `scanner_version` string ("2.0.0") on the class, which is stamped onto `V2ScanResult`. But:
- The registry has no version-routing logic
- No mechanism to run "v2" and "v3" side-by-side
- No version negotiation or compatibility checking
- Upgrade path: replace the class, update version string

---

## 8  Consistency Check

### 8.1  Lifecycle Consistency

All 4 families follow the **exact same** 6-phase lifecycle:

```
Phase A → Phase B → Phase C → Phase D → Phase D2 → Phase E → Phase F
narrow    construct   structural  quote/liq   trust/dedup  math      normalize
```

This lifecycle is enforced by `BaseV2Scanner.run()` — families cannot skip or reorder phases. They can only customize behavior within phases via hooks.

### 8.2  Phase A → Phase B Interface

**100% consistent.** Every family receives the same interface:

```python
construct_candidates(
    chain: dict[str, Any],
    symbol: str,
    underlying_price: float | None,
    expirations: list[str],
    strategy_id: str,
    scanner_key: str,
    context: dict[str, Any],
    narrowed_universe: V2NarrowedUniverse | None = None,
)
```

### 8.3  V2Candidate Field Population

| Field | Verticals | IC | Butterflies | Calendars |
|-------|-----------|-----|------------|-----------|
| `candidate_id` | ✅ | ✅ | ✅ | ✅ |
| `scanner_key` | ✅ | ✅ | ✅ | ✅ |
| `strategy_id` | ✅ | ✅ | ✅ | ✅ |
| `family_key` | ✅ | ✅ | ✅ | ✅ |
| `symbol` | ✅ | ✅ | ✅ | ✅ |
| `underlying_price` | ✅ | ✅ | ✅ | ✅ |
| `expiration` | ✅ (single) | ✅ (single) | ✅ (single) | ✅ (near leg) |
| `expiration_back` | None | None | None | ✅ (far leg) |
| `dte` | ✅ | ✅ | ✅ | ✅ (near) |
| `dte_back` | None | None | None | ✅ (far) |
| `legs` | 2 legs | 4 legs | 3 or 4 legs | 2 legs |
| `math.net_credit` | ✅ or None | ✅ or None | ✅ or None | None (debit-based) |
| `math.net_debit` | ✅ or None | None (credit only) | ✅ or None | ✅ or None |
| `math.max_profit` | ✅ | ✅ | ✅ | None |
| `math.max_loss` | ✅ | ✅ | ✅ | ✅ |
| `math.pop` | ✅ (delta approx) | ✅ (delta approx) | ✅ (delta approx) | None |
| `math.ev` | ✅ | ✅ | ✅ | None |
| `math.ror` | ✅ | ✅ | ✅ | None |
| `math.kelly` | ✅ | ✅ | ✅ | None |
| `math.breakeven` | ✅ | ✅ | ✅ | None |

**Calendar's None fields** are the structural outlier — see 5C-02 for the full analysis of why these are None and the impact on ranking.

### 8.4  Shared Phase Invariants

| Invariant | Enforced By | Consistent? |
|-----------|-------------|------------|
| All candidates get `passed` flag | Phase F | ✅ Yes |
| All rejected candidates retained | `run()` method | ✅ Yes |
| All candidates get `generated_at` | Phase F | ✅ Yes |
| `reject_reason_counts` aggregated | `V2ScanResult.from_candidates()` | ✅ Yes |
| `phase_counts` array populated | `run()` method | ✅ Yes |

---

## REGISTRY MAP

### Complete Scanner Key → Family → Config → Behavior Chain

```
┌─────────────────────────────────────────────────────────────────┐
│  REGISTRY (_FAMILY_REGISTRY)                                     │
│                                                                   │
│  strategy_id → family_key → module_path → class_name              │
│  ─────────────────────────────────────────────────────             │
│  put_credit_spread    → vertical_spreads → ...vertical_spreads    │
│  call_credit_spread   → vertical_spreads → ...vertical_spreads    │
│  put_debit            → vertical_spreads → ...vertical_spreads    │
│  call_debit           → vertical_spreads → ...vertical_spreads    │
│  iron_condor          → iron_condors    → ...iron_condors         │
│  butterfly_debit      → butterflies     → ...butterflies          │
│  iron_butterfly       → butterflies     → ...butterflies          │
│  calendar_call_spread → calendars       → ...calendars            │
│  calendar_put_spread  → calendars       → ...calendars            │
│  diagonal_call_spread → calendars       → ...calendars            │
│  diagonal_put_spread  → calendars       → ...calendars            │
└───────────────────────────┬─────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
┌───────────────┐  ┌────────────────┐  ┌────────────────┐
│ Family Class  │  │ Family Module  │  │ Base Scanner   │
│ (instance)    │  │ (constants)    │  │ (hooks)        │
│               │  │                │  │                │
│ dte_min       │  │ _GEN_CAP       │  │ run()          │
│ dte_max       │  │ _MAX_WIDTH     │  │ narrow_chain() │
│ require_same_ │  │ _VARIANT_CONFIG│  │ phase_c/d/d2/  │
│   expiry      │  │                │  │   e/f dispatch │
└───────────────┘  └────────────────┘  └────────────────┘
        │                                       │
        ▼                                       ▼
┌─────────────────────────────────────────────────────────┐
│  EXECUTION PATH per (scanner_key, symbol)                │
│                                                           │
│  1. get_v2_scanner(scanner_key) → cached family instance  │
│  2. scanner.run(scanner_key, strategy_id, symbol, chain)  │
│     A: build_narrowing_request() → V2NarrowingRequest     │
│        narrow_chain(chain, request) → V2NarrowedUniverse  │
│     B: construct_candidates(..., strategy_id, ...) → []   │
│        ← parameterized by _VARIANT_CONFIG[strategy_id]    │
│     C: phase_c(candidates, family_checks=hook())          │
│     D: phase_d(candidates)                                │
│     D2: phase_d2(candidates, dedup_key_fn=hook())         │
│     E: phase_e(candidates, family_math=hook())            │
│     F: phase_f(candidates) → V2ScanResult                 │
└─────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────┐
│  ORCHESTRATION (options_scanner_service.py)               │
│                                                           │
│  for scanner_key in scanner_keys:                         │
│      for symbol in symbols:                               │
│          chain = fetch_and_merge(symbol)  ← BDS-cached   │
│          result = scanner.run(...)                         │
│          all_results.append(result)                        │
│                                                           │
│      NO cross-scanner dedup                               │
│      NO cross-family coordination                         │
└─────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────┐
│  WORKFLOW RUNNER (options_opportunity_runner.py)          │
│                                                           │
│  Stage 2: Dispatch all scanner_keys → options_scanner_svc │
│  Stage 3: Flatten all candidates from all scan_results    │
│  Stage 4: Credibility gate → EV sort → rank assignment    │
│  Stage 5: Slice to top_n (30) → output.json               │
└─────────────────────────────────────────────────────────┘
```

---

## FINDINGS

### Finding 5E-01 (HIGH) — Phase A Narrowing Repeated 11× Per Symbol

**Location**: `options_scanner_service._run_one()`, `base_scanner.run()`  
**Issue**: Each scanner key independently calls `narrow_chain()` for the same symbol. For SPY with all 11 scanner keys enabled, Phase A narrowing (chain normalization + DTE filter + strike filter + dedup) runs **11 times** on the same chain data. Since narrowing configuration is identical for all vertical variants (same DTE window, same empty strike/moneyness/distance filters), the first 4 calls produce **identical** `V2NarrowedUniverse` objects.  
**Waste**: 4 identical narrowing runs for verticals, 2 identical for butterflies (same DTE as IC), plus calendar and IC. Roughly 7 redundant narrowing executions per symbol.  
**Recommendation**: Cache `V2NarrowedUniverse` by `(symbol, dte_min, dte_max, require_same_expiry, option_types, moneyness, distance_pct)` key. Families with identical narrowing requests would share one cached result.

### Finding 5E-02 (HIGH) — No Narrowing Configuration Differentiation

**Location**: All family `build_narrowing_request()` (none override the base)  
**Issue**: The `V2NarrowingRequest` supports rich filtering (`option_types`, `distance_min_pct`, `distance_max_pct`, `moneyness`, `multi_expiry`, `near_dte_*`, `far_dte_*`). All 11 fields beyond `dte_min`/`dte_max` are at their defaults (empty/None/False) for every scanner key. This means Phase A is a **DTE-only pass-through** for all families — cross-reference to 5A-01 (HIGH).  
**Impact**: Every strike at every in-window expiration passes to Phase B, where brute-force enumeration generates 50,000+ candidates from material that could have been filtered to 5,000 with family-appropriate narrowing.  
**Recommendation**: Override `build_narrowing_request()` per family:
- Verticals: `moneyness="otm"`, `option_types=[target_type]`, `distance_max_pct=0.15`
- IC: `moneyness="otm"`, `distance_max_pct=0.15`
- Butterflies: `distance_max_pct=0.10`
- Calendars: `multi_expiry=True`, `distance_max_pct=0.10`

### Finding 5E-03 (MEDIUM) — Configuration Scattered Across 4 Layers

**Location**: registry.py, base_scanner.py, family modules, runner  
**Issue**: To understand the complete behavior of a single scanner key, a developer must read 4 files across 4 layers (see §4.2). There is no single configuration file, dataclass, or method that aggregates all behavior-relevant parameters for a scanner key. This makes it difficult to audit, compare, or modify scanner behavior.  
**Risk**: Configuration drift — a change in one layer (e.g., modifying DTE range in the family class) may not be reflected in assumptions made in another layer (e.g., runner credibility thresholds).  
**Recommendation**: Add a `describe_scanner_key(scanner_key) → dict` utility that collects and returns all configuration from all layers for one key. This aids debugging and audit without changing architecture.

### Finding 5E-04 (MEDIUM) — No Cross-Family Deduplication

**Location**: `options_opportunity_runner._stage_scan()`, `options_scanner_service.scan()`  
**Issue**: A vertical put credit spread at $530/$525 and the put side of an iron condor at $530/$525 can both appear in the top-30 as separate candidates. They represent the same economic position but are generated by different families. Similarly, credit and debit variants at the same strikes (e.g., put credit $540/$535 and put debit $535/$540) have equivalent payoff profiles.  
**Risk**: Top-30 may contain multiple representations of the same trade, consuming slots that could present unique opportunities.  
**Recommendation**: Add a cross-scanner dedup step in Stage 3 (after aggregation, before credibility gate). Key on `(symbol, expiration, frozenset_of_leg_tuples)` where leg_tuples are `(strike, option_type, side)`.

### Finding 5E-05 (MEDIUM) — No Per-Key Enable/Disable at Runtime

**Location**: registry.py  
**Issue**: The `implemented` flag is hardcoded on `V2FamilyMeta` and operates at the family level. There is no mechanism to:
- Disable individual scanner keys (e.g., skip `put_debit` while keeping `put_credit_spread`)
- Disable keys at runtime via configuration (requires code change)
- Disable keys per symbol (e.g., skip calendars for low-liquidity underlyings)  
**Risk**: Running all 11 keys is wasteful for scenarios where only a subset is relevant (e.g., credit-only scan, specific family scan).  
**Recommendation**: The `RunnerConfig.scanner_keys` field already supports subsets — expose this to the API layer and UI as a scanner key selector.

### Finding 5E-06 (MEDIUM) — No Preset / Aggressiveness System

**Location**: All families, context dict  
**Issue**: The scanner-contract docs reference Strict / Balanced / Wide presets with "meaningfully different thresholds." The V2 scanner has no preset implementation — all families run with their hardcoded defaults regardless of market conditions or user preference. The `context` dict could carry preset parameters but no code generates them.  
**Risk**: Violates the scanner-contract standard (`docs/standards/presets.md`). No way for users to adjust scan aggressiveness.  
**Recommendation**: Implement a preset resolver that maps preset name → context overrides:
```python
PRESETS = {
    "strict":   {"delta_min": 0.15, "delta_max": 0.25, "min_dte": 25, "max_dte": 50},
    "balanced": {"delta_min": 0.10, "delta_max": 0.35, "min_dte": 14, "max_dte": 60},
    "wide":     {"delta_min": 0.05, "delta_max": 0.45, "min_dte": 7,  "max_dte": 90},
}
```

### Finding 5E-07 (MEDIUM) — Vertical Credit/Debit Redundant Narrowing

**Location**: vertical_spreads.py, options_scanner_service.py  
**Issue**: `put_credit_spread` and `put_debit` scan the same put strikes with the same DTE window. They produce the same (S_low, S_high) pairs — only the short/long assignment differs. Running both means Phase A narrows the same chain twice, Phase B enumerates the same pairs twice, and both consume independent 50,000-slot generation caps. For 4 vertical variants, this is 2× redundant computation (put pair = credit + debit; call pair = credit + debit).  
**Risk**: Compute waste and doubled generation cap consumption.  
**Recommendation**: Combine credit and debit construction into one pass per (option_type, symbol, expiry). Generate both candidates from each pair with one loop iteration.

### Finding 5E-08 (LOW) — Scanner Version Not Used for Routing or Compatibility

**Location**: base_scanner.py, registry.py  
**Issue**: Each family stores `scanner_version = "2.0.0"` which is stamped onto `V2ScanResult`. But the version is never checked for routing, compatibility, or migration. If a v2.1 scanner produces different output shapes, there is no mechanism to route traffic between versions or validate compatibility.  
**Risk**: Low currently (all families at 2.0.0). Would become important during any migration or A/B testing.  
**Recommendation**: No action needed now. Note for future: if v3 families are introduced, add version-aware routing to the registry.

### Finding 5E-09 (LOW) — V2FamilyMeta leg_count Is Informational Only

**Location**: registry.py  
**Issue**: `V2FamilyMeta.leg_count` is set to `2`, `4`, `"3-4"`, or `2` for the four families. But this field is **never referenced by any code** — Phase C structural checks handle leg count validation in the family hooks. The registry field is purely documentary.  
**Risk**: None — informational duplication. Could become stale if a family adds a new variant with different leg count.  
**Recommendation**: No action needed. Consider documenting that `leg_count` is informational only.

### Finding 5E-10 (LOW) — Chain Dict Reconstructed Per Scanner Key

**Location**: `options_scanner_service._run_one()`  
**Issue**: For each (scanner_key, symbol) pair, `_run_one()` fetches expirations and chain data (from BDS cache) and reconstructs the `chain = {"options": {"option": merged_options}}` dict. While the underlying API calls are cached, the loop that iterates all expirations and calls `model_dump()` on each contract runs 11 times per symbol. For SPY with ~60-80 expirations and ~200+ contracts per expiration, this is ~180,000 `model_dump()` calls that produce identical results.  
**Risk**: Moderate performance cost. Each `model_dump()` is fast, but 180,000 × 11 = ~2M calls is non-trivial.  
**Recommendation**: Cache the merged chain dict per symbol at the service level:
```python
if symbol not in self._chain_cache:
    self._chain_cache[symbol] = await self._build_merged_chain(symbol)
chain = self._chain_cache[symbol]
```

---

## SUMMARY

| Severity | Count | Key Theme |
|----------|-------|-----------|
| HIGH | 2 | Redundant narrowing 11× per symbol; no narrowing differentiation per family |
| MEDIUM | 5 | Config scattered across 4 layers; no cross-family dedup; no per-key enable/disable; no presets; vertical credit/debit redundancy |
| LOW | 3 | Version not used; leg_count informational; chain dict rebuilt per key |
| **Total** | **10** | |

### Architecture Assessment

The V2 scanner infrastructure is **well-designed but underutilized**:

**Strengths**:
1. **Clean separation** — BaseV2Scanner provides a consistent 6-phase lifecycle with well-defined hooks
2. **Template Method pattern** — families customize behavior without altering orchestration
3. **Identity-based override detection** — the `type(self).method is BaseV2Scanner.method` pattern elegantly determines whether to inject family math or use defaults
4. **Consistent output contract** — all 11 scanner keys produce identical `V2ScanResult` shapes with the same phase_counts, reject_reason taxonomy, and diagnostic structure
5. **Lazy loading** — scanner classes are imported only when first used, with instance caching

**Weaknesses**:
1. **Narrowing hooks unused** — `build_narrowing_request()` is the only hook NO family overrides, yet it's the one that would have the most impact on construction quality
2. **Configuration invisible** — understanding a single scanner key requires reading 4 files across 4 layers
3. **No runtime flexibility** — presets, per-key enable/disable, and aggressiveness controls are all absent despite the infrastructure supporting them (via `context` dict)
4. **11× redundancy** — each scanner key independently narrows the same chain, reconstructs the same chain dict, and produces candidates that compete without cross-family dedup

The registry and hook system would support all the improvements identified in audits 5A-5D (delta targeting, IV awareness, preset system, narrowing optimization) without architectural changes. The `context` dict is the natural channel; `build_narrowing_request()` is the natural hook. The infrastructure is ready — it just needs to be activated.

---

**Provenance**: All findings traced from direct code reads of `registry.py`, `base_scanner.py`, `contracts.py`, all 4 family modules, `options_scanner_service.py`, and `options_opportunity_runner.py`.
