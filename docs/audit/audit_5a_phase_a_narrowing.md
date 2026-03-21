# Audit 5A — Phase A Narrowing: Strike & DTE Selection Logic

**Pass**: 5 — Options Scanner Construction & Candidate Quality  
**Prompt**: 5A  
**Scope**: `app/services/scanner_v2/data/` (contracts.py, chain.py, narrow.py, expiry.py, strikes.py) + `base_scanner.py` + family `build_narrowing_request()` overrides  
**Date**: 2025-07-11

---

## 1  Expiration Filtering

### 1.1  DTE Calculation

| Aspect | Implementation |
|--------|---------------|
| Function | `expiry._compute_dte()` |
| Formula | `(date.fromisoformat(expiration) - date.today()).days` |
| Unit | Calendar days |
| Date source | `date.today()` — system-local date, **not** market close |

### 1.2  DTE Window Check

| Aspect | Implementation |
|--------|---------------|
| Function | `expiry._check_dte_window()` |
| Lower bound | `dte < request.dte_min` → reject `dte_below_min` |
| Upper bound | `dte > request.dte_max` → reject `dte_above_max` |
| Boundaries | **Both inclusive** |
| Invalid date | `dte_invalid` reason code |

### 1.3  Single-Expiry Path

`narrow_expirations()` iterates all contracts, caches DTE per unique expiration string, filters by window, populates diagnostics with kept/dropped counts and reason codes.

### 1.4  Multi-Expiry Path

`narrow_expirations_multi()` supports separate near/far DTE windows.  Returns `(near_contracts, far_contracts)`.

- Falls back to `dte_min`/`dte_max` if `near_dte_min`/`near_dte_max` or `far_dte_min`/`far_dte_max` are None.
- Contracts qualifying for both windows are added to both lists (dual-role).
- Additional reason codes: `dte_below_near_min`, `dte_between_windows`, `dte_above_far_max`.

**Critical**: No family currently activates the multi-expiry path (see §4).

---

## 2  Strike Filtering

### 2.1  Pipeline Stages

`strikes.narrow_strikes()` applies four stages in order:

| Stage | Function | Filter Logic |
|-------|----------|-------------|
| 1 — Option type | `_filter_option_type()` | Keep only requested types; empty list = keep both |
| 2 — Moneyness | `_check_strike_window()` | OTM/ITM/ATM filter; ATM = within 0.5% of spot |
| 3 — Distance | `_check_strike_window()` | `abs(strike - price) / price` vs `[distance_min_pct, distance_max_pct]`, both inclusive |
| 4 — Dedup | `_group_and_dedup()` | Per (expiration, strike, option_type), keep highest OI |

### 2.2  Distance Calculation

```python
# V2OptionContract.distance_pct()
abs(self.strike - underlying_price) / underlying_price
```

Positive decimal fraction.  E.g., 0.05 = 5% away from spot.

### 2.3  Moneyness Definitions

| Value | Meaning |
|-------|---------|
| `"otm"` | Put: `strike < underlying`.  Call: `strike > underlying`. |
| `"itm"` | Inverse of OTM |
| `"atm"` | `distance_pct <= 0.005` (within 0.5% of spot) |

### 2.4  Deduplication

- Groups by expiration, then by `(strike, option_type)` key.
- Keeps contract with highest `open_interest` (ties: first seen wins).
- Computes `median_iv` per bucket (stored, not used for filtering).
- Dropped duplicates tracked under `duplicate_dropped` reason.

### 2.5  Reason Codes

`wrong_type`, `wrong_moneyness`, `distance_below_min`, `distance_above_max`, `distance_unknown`, `duplicate_dropped`.

---

## 3  `narrow_chain()` Orchestration

**File**: `data/narrow.py`

### 3.1  Pipeline

```
_build_request() → normalize_chain() → [multi_expiry? → narrow_expirations_multi : narrow_expirations] → narrow_strikes() → V2NarrowedUniverse
```

### 3.2  Request Merging

`_build_request()` creates a `V2NarrowingRequest` (or accepts an existing one) and applies non-None kwargs via `object.__setattr__`.  This allows callers to pass convenience overrides without constructing a full request.

### 3.3  Multi-Expiry Branch

When `req.multi_expiry is True`:
1. `narrow_expirations_multi()` → `(near_contracts, far_contracts)`
2. `narrow_strikes()` runs independently on each list (diag=None for both).
3. Buckets merged via `{**near_buckets, **far_buckets}` — far takes precedence on key collision.

### 3.4  Output

`V2NarrowedUniverse` containing:
- `underlying` — `V2UnderlyingSnapshot` (symbol, price, source)
- `expiry_buckets` — `dict[str, V2ExpiryBucket]` keyed by ISO date
- `diagnostics` — `V2NarrowingDiagnostics` (full pipeline trace)
- `request` — the resolved `V2NarrowingRequest`

---

## 4  Per-Family Narrowing Configuration

### 4.1  Family DTE Ranges

| Family | Scanner Class | `dte_min` | `dte_max` | Overrides `build_narrowing_request()`? |
|--------|--------------|-----------|-----------|---------------------------------------|
| Verticals | `VerticalSpreadsV2Scanner` | 1 | 90 | **No** |
| Iron Condors | `IronCondorsV2Scanner` | 7 | 60 | **No** |
| Butterflies | `ButterfliesV2Scanner` | 7 | 60 | **No** |
| Calendars | `CalendarsV2Scanner` | 7 | 90 | **No** |

### 4.2  Base `build_narrowing_request()`

```python
# base_scanner.py:262
def build_narrowing_request(self, *, context=None):
    return V2NarrowingRequest(
        dte_min=self.dte_min,
        dte_max=self.dte_max,
    )
```

Only `dte_min` and `dte_max` are populated.  All other fields (`option_types`, `distance_min_pct`, `distance_max_pct`, `moneyness`, `multi_expiry`, `near_dte_min/max`, `far_dte_min/max`) remain at defaults (empty list / None / False).

### 4.3  Effective Narrowing Parameters

Since no family overrides `build_narrowing_request()`:

| Parameter | Effective Value (all families) |
|-----------|-------------------------------|
| `option_types` | `[]` — keep both puts and calls |
| `distance_min_pct` | `None` — no constraint |
| `distance_max_pct` | `None` — no constraint |
| `moneyness` | `None` — no filter |
| `multi_expiry` | `False` — single-expiry path |
| `near_dte_*` / `far_dte_*` | `None` — unused |

**Result**: Phase A narrowing is a DTE-only geometric filter.  Every strike at every in-window expiration passes through to Phase B.

### 4.4  Registry Mapping (11 keys → 4 families)

| Scanner Key | Family Class |
|-------------|-------------|
| `put_credit_spread`, `call_credit_spread`, `put_debit`, `call_debit` | `VerticalSpreadsV2Scanner` |
| `iron_condor` | `IronCondorsV2Scanner` |
| `butterfly_debit`, `iron_butterfly` | `ButterfliesV2Scanner` |
| `calendar_call_spread`, `calendar_put_spread`, `diagonal_call_spread`, `diagonal_put_spread` | `CalendarsV2Scanner` |

---

## 5  Quality of Narrowing Decisions

### 5.1  What Phase A Does Well

1. **Clear pipeline stages** — normalize → expiry filter → strike filter → package, each with reason codes.
2. **Diagnostics transparency** — `V2NarrowingDiagnostics` captures: total loaded, kept/dropped per stage, drop reason counts, data quality tallies, final contract count.
3. **Deduplication** — sensible (highest OI wins).
4. **Multi-expiry infrastructure** — the code path exists and is well-designed with dual-role handling, even though no family uses it.
5. **DTE boundaries are inclusive** — standard convention, correctly implemented.
6. **Data quality tracking** — chain normalization tallies missing bid/ask/delta/iv/oi/volume and inverted quotes before narrowing begins.

### 5.2  What Phase A Misses

Phase A's single job is DTE window filtering.  All strike-level intelligence is deferred to Phase B.  This design choice has consequences:

| Missing Filter | Impact |
|---------------|--------|
| No delta targeting | Short strike selection in verticals/IC/butterflies is brute-force enumeration rather than delta-targeted.  ALL strikes pass Phase A, then Phase B generates O(n²) combinations. |
| No distance window | SPY with 300+ strikes per expiration yields an enormous candidate matrix.  A 2-10% distance window could reduce strike count by ~80%. |
| No moneyness filter | Phase A carries ITM strikes through for credit spread families (put_credit, call_credit, iron_condor) even though they will never be used. |
| No IV awareness | Selling premium without filtering for minimum implied volatility. |
| No liquidity gate | Illiquid strikes (OI=0, volume=0, wide bid-ask) pass Phase A and create candidates in Phase B that will be rejected later by hygiene filters. |

### 5.3  Cascade Impact

Because Phase A is permissive, Phase B must:
- Generate a massive O(n²) candidate set (verticals cap at 50,000; IC uses √cap side limit).
- Rely on generation caps to prevent runaway.
- Build candidates that will be immediately rejected in Phase C (hygiene/validation).

This means the scanner does significant work constructing candidates that have no chance of passing.

---

## 6  What Gets Lost in Narrowing

### 6.1  Contracts Preserved Through Phase A

Since no strike/moneyness/distance/type filtering is applied, Phase A preserves **all contracts within the DTE window**.  Nothing of analytical value is lost by the narrowing itself.

### 6.2  What Gets Lost by Design

| Lost Data | How |
|-----------|-----|
| Expired expirations (DTE < min) | DTE window drops them with `dte_below_min` — correct. |
| Far-dated expirations (DTE > max) | DTE window drops them with `dte_above_max` — correct for income strategies. |
| Duplicate contracts | Dedup keeps highest OI — correct. |
| Unparseable expirations | `dte_invalid` — correct. |

### 6.3  What *Should* Be Filtered But Isn't

These survive Phase A and create unnecessary Phase B work:

| Surviving Category | Why It Shouldn't | Volume Estimate (SPY example) |
|-------------------|-----------------|------------------------------|
| Deep ITM strikes | Credit spreads only use OTM/near-money strikes | ~40% of chain |
| Strikes >15% from spot | No income strategy targets Δ<0.02 options | ~30% of remaining |
| Zero-OI strikes | No liquidity for execution | Variable, can be 10-20% |
| Invalid-quote contracts | `quote_valid=False` passed through | Variable |

---

## 7  Findings

### Finding 5A-01 (HIGH) — Phase A Is a DTE-Only Pass-Through

**Location**: `base_scanner.py:262` (`build_narrowing_request()`), all family subclasses  
**Issue**: No family overrides `build_narrowing_request()` to set `option_types`, `distance_min_pct/max_pct`, or `moneyness`.  Phase A is purely a DTE window filter.  For SPY with ~600 contracts per expiration and ~15 valid expirations (DTE 1–90), this passes ~9,000 contracts to Phase B where O(n²) enumeration creates up to 50,000 candidates, most of which will be rejected.  
**Risk**: Performance — unnecessary construction work; memory pressure under concurrent scans.  
**Recommendation**: Each family should set at minimum `moneyness="otm"` (credit families) and `distance_max_pct=0.15` in `build_narrowing_request()` overrides.

### Finding 5A-02 (HIGH) — No Delta-Targeted Strike Narrowing

**Location**: `data/strikes.py`, `data/contracts.py`  
**Issue**: The narrowing framework has no delta-based strike selection.  `V2OptionContract` carries `delta` from chain normalization, but it is never used in Phase A.  Short-strike selection happens by brute-force pairing in Phase B.  For an income-focused platform, the ideal 0.15–0.30 delta range for short strikes should inform narrowing.  
**Risk**: Strategy quality — the scanner constructs many candidates far outside the desirable delta range only to reject them later.  
**Recommendation**: Add `delta_min`/`delta_max` to `V2NarrowingRequest` and filter in `_check_strike_window()`.

### Finding 5A-03 (HIGH) — Calendar Family Does Not Use Multi-Expiry Narrowing Path

**Location**: `families/calendars.py` (no `build_narrowing_request()` override), `base_scanner.py:262`  
**Issue**: `CalendarsV2Scanner` has `require_same_expiry = False` but does not override `build_narrowing_request()`, so `multi_expiry=False`.  The well-designed `narrow_expirations_multi()` code path is unreachable.  Calendar construction works around this by loading all DTE 7-90 expirations flat and doing its own near/far pairing in Phase B.  
**Risk**: The multi-expiry infrastructure is dead code; Phase A provides no structural support for cross-expiry strategies.  
**Recommendation**: Calendar family should override `build_narrowing_request()` to set `multi_expiry=True` with appropriate near/far DTE windows (e.g., near 7-30, far 30-90).

### Finding 5A-04 (MEDIUM) — No Liquidity Pre-Filter in Phase A

**Location**: `data/strikes.py`  
**Issue**: Contracts with `open_interest=0`, `volume=0`, or `quote_valid=False` pass through Phase A.  These generate candidates in Phase B that are then rejected by Phase C hygiene validators.  
**Risk**: A significant fraction of Phase B construction work may be wasted on illiquid contracts.  
**Recommendation**: Add an optional `min_open_interest` parameter to `V2NarrowingRequest` and filter in `narrow_strikes()`.

### Finding 5A-05 (MEDIUM) — No IV-Aware Narrowing

**Location**: `data/strikes.py:_group_and_dedup()`  
**Issue**: `median_iv` is computed per `V2ExpiryBucket` but only stored, never used for filtering or ranking.  For an income strategy platform, selling premium when IV is low is a core anti-pattern.  Phase A has the data to apply a minimum IV threshold but does not.  
**Risk**: Low-IV candidates are constructed and scored, consuming resources without producing viable trades.  
**Recommendation**: Add `min_iv` to `V2NarrowingRequest` and filter contracts below threshold before grouping.

### Finding 5A-06 (MEDIUM) — Multi-Expiry Bucket Merge Overwrites Near-Leg Data

**Location**: `data/narrow.py:128` — `all_buckets = {**near_buckets, **far_buckets}`  
**Issue**: When near and far DTE windows overlap (which they would for dual-role expirations), the dict merge `{**near, **far}` silently overwrites near-leg buckets with far-leg versions.  The comment says "far-leg pricing matters more" but this loses near-leg strike data for shared expirations.  Since `narrow_strikes()` is called with `diag=None` for both legs, diagnostic counts are also lost.  
**Risk**: Data loss for cross-expiry strategies if multi-expiry path is ever activated.  Currently dormant since no family uses it.  
**Recommendation**: Merge strategy should union strikes from both legs rather than overwrite.  Pass diagnostics for at least one leg.

### Finding 5A-07 (MEDIUM) — `quote_valid=False` Contracts Not Filtered

**Location**: `data/chain.py` (normalization), `data/strikes.py` (filtering)  
**Issue**: Chain normalization sets `quote_valid=False` on contracts with missing or inverted bid/ask, but `narrow_strikes()` does not check this flag.  Invalid-quote contracts create candidates that will fail downstream hygiene checks.  
**Risk**: Wasted construction work; potential for miscalculated credit/debit on invalid quotes.  
**Recommendation**: Filter `quote_valid=False` contracts in `narrow_strikes()` with a `bad_quote` reason code, or at least after option-type filtering as an early exit.

### Finding 5A-08 (LOW) — DTE Uses System-Local Date, Not Market Close

**Location**: `data/expiry.py:_compute_dte()` — `date.today()`  
**Issue**: `date.today()` returns the system-local date.  On a server in UTC, a scan run at 11 PM ET Saturday would compute DTE from Sunday, off by one day from the market-close perspective.  On weekdays after 4 PM ET, the "next trading date" is really the next business day, but DTE doesn't account for this.  
**Risk**: Off-by-one DTE on boundary expirations (especially 0-1 DTE for credit spreads).  Minor for longer-dated strategies.  
**Recommendation**: Use market-aware date (ET business day) or at minimum `datetime.now(ZoneInfo("America/New_York")).date()`.

### Finding 5A-09 (LOW) — Fixed Percentage Distance vs. Volatility-Adjusted

**Location**: `data/contracts.py:V2OptionContract.distance_pct()`  
**Issue**: Strike distance is measured as a fixed percentage of underlying price.  This treats a 5% move identically in a 10-vol and 40-vol environment.  An ATR-based or sigma-based distance measure would normalize across volatility regimes.  
**Risk**: Low severity — distance filtering isn't currently used by any family anyway.  Becomes relevant if §5A-01 is addressed.  
**Recommendation**: When adding distance filters, consider sigma-based distance: `(strike - price) / (price * IV * sqrt(DTE/365))`.

### Finding 5A-10 (LOW) — ATM Threshold Is Hardcoded

**Location**: `data/strikes.py:_check_strike_window()` — `dist > 0.005`  
**Issue**: ATM moneyness is defined as within 0.5% of spot, hardcoded.  For a $600 underlying (SPY), this is $3; for a $30 underlying (IWM), it's $0.15 (likely less than one strike).  The definition is not volatility-adjusted.  
**Risk**: ATM filter may be too narrow for low-priced underlyings and too wide for high-priced ones.  Currently not used by any family.  
**Recommendation**: If ATM filtering is used, parameterize the threshold or use a strike-count-based approach (nearest N strikes to ATM).

---

## 8  Summary

| Severity | Count | Key Theme |
|----------|-------|-----------|
| HIGH | 3 | Phase A is effectively a no-op past DTE filtering; no delta/distance/type narrowing; calendar multi-expiry path unused |
| MEDIUM | 4 | No liquidity pre-filter; no IV awareness; multi-expiry merge overwrites data; invalid-quote contracts pass through |
| LOW | 3 | DTE timezone, fixed % distance, hardcoded ATM threshold |
| **Total** | **10** | |

### Architectural Assessment

Phase A narrowing infrastructure is well-engineered with clean data types, comprehensive diagnostics, and reason-code traceability.  The framework *supports* all the needed filters (distance, moneyness, option_type, multi-expiry) — the gap is that **no family activates any of them**.  The result is an extremely permissive Phase A that passes the full chain (minus out-of-DTE contracts) to Phase B, where the O(n²) construction creates massive candidate sets capped only by generation limits.  The highest-impact fix is having each family override `build_narrowing_request()` with strategy-appropriate filters.

---

**Provenance**: All findings traced from direct code reads of `app/services/scanner_v2/data/` and `app/services/scanner_v2/families/` source files.
