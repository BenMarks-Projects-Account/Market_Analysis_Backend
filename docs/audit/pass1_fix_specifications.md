# BenTrade Foundation Audit — Pass 1 Fix Specifications
## Data Integrity Layer: Implementation Guide for Copilot Prompts

**Date**: 2026-03-20
**Purpose**: Structured fix specs for every Pass 1 finding. Each spec contains everything needed to generate a targeted Copilot prompt: exact files, current behavior, target behavior, pattern to follow, and acceptance criteria.

**How to use this document**: When ready to generate Copilot prompts, feed this document (plus the original audit files 1A-1F for code snippet context) to Claude and ask for prompts targeting specific fix IDs.

---

## Fix Priority Tiers

| Tier | Meaning | Fix IDs |
|------|---------|---------|
| **FN (Fix Now)** | Blocks data quality improvement; small scope, high impact | FN-1, FN-2, FN-3 |
| **FS (Fix Soon)** | Structural improvements; medium scope, high cumulative impact | FS-1, FS-2, FS-3, FS-4, FS-5 |
| **FL (Fix Later)** | Foundation hardening; larger scope or lower immediate urgency | FL-1, FL-2, FL-3, FL-4, FL-5, FL-6, FL-7 |

---

## FN-1: Observation-Date Staleness for All FRED Series

### Problem
Only copper (PCOPPUSDM) has an `observation_date`-based staleness check. All other FRED series (DGS10, DGS2, DGS30, DFF, DCOILWTICO, DTWEXBGS, BAMLC0A0CM, BAMLH0A0HYM2) can be days stale with zero detection, zero confidence penalty, and zero downstream signal.

### Files Involved
| File | Role |
|------|------|
| `app/services/cross_asset_macro_data_provider.py` | Has the working `_days_stale()` pattern (lines 223-241) |
| `app/services/liquidity_conditions_data_provider.py` | Fetches IG/HY spreads, yields — no staleness check |
| `app/services/market_context_service.py` | Produces metric envelopes with `observation_date` — the data is available but unused |
| `app/services/volatility_options_data_provider.py` | Fetches VIX/SKEW from FRED — no staleness check |

### Current Behavior
```python
# cross_asset_macro_data_provider.py lines 223-241 (ONLY copper):
copper_days_stale = _days_stale(copper_date)
if copper_days_stale is not None and copper_days_stale > 5:
    logger.info("event=cross_asset_copper_stale days_stale=%d ...", copper_days_stale, ...)
# Other series: no check at all
```

### Target Behavior
Every data provider that consumes FRED data should:
1. Extract `observation_date` from the metric envelope (it's already there)
2. Compute `_days_stale()` for each FRED-sourced metric
3. Log staleness warnings when age exceeds thresholds
4. Pass staleness info to source_meta so engines CAN see it
5. Apply a confidence penalty proportional to staleness

### Pattern to Follow
The existing `_days_stale()` helper in `cross_asset_macro_data_provider.py` (lines 223-241) is the pattern. Replicate it for all FRED metrics, then add a penalty table:

```python
# Proposed staleness penalty table (business days):
# 0-1 days: no penalty (normal FRED lag)
# 2-3 days: minor penalty (-0.03 confidence)
# 4-7 days: moderate penalty (-0.08 confidence)
# 8-14 days: significant penalty (-0.15 confidence)
# 15+ days: heavy penalty (-0.25 confidence)
# 30+ days (monthly series): flag but don't reject
```

### Acceptance Criteria
- [ ] `_days_stale()` is a shared utility (not copy-pasted per provider)
- [ ] Every FRED-sourced metric in every data provider has staleness computed
- [ ] Staleness values are included in `source_meta` output
- [ ] Staleness > 3 business days triggers a log warning
- [ ] Staleness is available for downstream confidence adjustment
- [ ] Existing copper staleness behavior is preserved (not broken by refactor)
- [ ] Unit test: mock FRED returning an observation_date 5 days old → verify warning logged and staleness value in source_meta

### Dependencies
None — can be implemented independently.

### Estimated Scope
Small: ~50-80 lines of new code + refactoring existing `_days_stale()` into a shared utility.

---

## FN-2: Add SIGNAL_PROVENANCE to Volatility Engine

### Problem
The volatility engine uses 5+ proxy/proxy-of-proxy metrics but has no SIGNAL_PROVENANCE dict. The `engine_output_contract.py` normalizer reports `proxy_count=0` for this engine — factually incorrect. Dashboard and downstream consumers believe the vol engine has zero proxies.

### Files Involved
| File | Role |
|------|------|
| `app/services/volatility_options_engine.py` | Engine file — needs SIGNAL_PROVENANCE added |
| `app/services/flows_positioning_engine.py` (lines 72-130) | **Pattern to follow** — has complete SIGNAL_PROVENANCE |
| `app/services/cross_asset_macro_engine.py` (lines 83-173) | **Pattern to follow** — has complete SIGNAL_PROVENANCE |
| `app/services/liquidity_conditions_engine.py` (lines 54-120) | **Pattern to follow** — has complete SIGNAL_PROVENANCE |
| `common/engine_output_contract.py` (lines 491-497) | Normalizer that counts proxies from SIGNAL_PROVENANCE |

### Current Behavior
```python
# engine_output_contract.py lines 491-497:
provenance = diag.get("signal_provenance") or {}
proxy_count = 0
for _sig, info in provenance.items():
    if isinstance(info, dict):
        sig_type = info.get("type", "")
        if sig_type == "proxy":
            proxy_count += 1
# When signal_provenance is absent → proxy_count=0 (WRONG for vol engine)
```

### Target Behavior
The volatility engine should have a SIGNAL_PROVENANCE dict following the same schema as the flows engine (the most complete example).

### Metrics to Tag

| Metric Key | Type | Notes |
|-----------|------|-------|
| `vix_spot` | `"direct"` | Real-time VIX quote from Tradier |
| `vvix` | `"direct"` | Real-time VVIX quote from Tradier |
| `iv_30d` | `"direct"` | SPY implied volatility from options chain |
| `rv_30d` | `"derived"` | Realized vol from SPY daily closes (standard formula) |
| `cboe_skew` | `"direct"` | CBOE SKEW index from FRED (1-2 day lag, note delay) |
| `put_skew_25d` | `"direct"` | 25-delta put IV from SPY options |
| `equity_pc_ratio` | `"proxy"` | SPY P/C ratio used as proxy for broader equity P/C |
| `vix_rank_30d` | `"proxy"` | VIX index rank, NOT true option IV rank. Notes: "VIX history rank used as proxy for IV rank. VIX is a single index; true IV rank would use stock/ETF option IV history." |
| `vix_percentile_1y` | `"proxy"` | Same issue as vix_rank_30d |
| `vix_2nd_month` | `"proxy"` | Fabricated heuristic from VIX spot vs 20-day average. Notes: "No VIX futures data available. Term structure inferred from spot/average ratio. Direction hint only, not magnitude." |
| `vix_3rd_month` | `"proxy"` | Same as vix_2nd_month |
| `option_richness` | `"proxy_of_proxy"` | Depends on vix_rank_30d (proxy) + iv_30d + rv_30d |
| `premium_bias` | `"proxy_of_proxy"` | Depends on vix_rank_30d (proxy) + VRP + P/C ratio |
| `tail_risk_numeric` | `"derived"` | Interpolation from put_skew_25d (direct) + cboe_skew (direct) |

### Pattern to Follow
```python
# From flows_positioning_engine.py lines 72-130:
SIGNAL_PROVENANCE = {
    "put_call_ratio": {
        "type": "proxy",
        "upstream": "VIX",
        "formula": "0.45 + VIX × 0.023",
        "frequency": "intraday (inherits VIX)",
        "delay": "none beyond VIX",
        "notes": "No exchange-reported put/call data. VIX-derived heuristic.",
    },
    # ... more entries
}
```

### Acceptance Criteria
- [ ] `SIGNAL_PROVENANCE` dict added to volatility engine file
- [ ] All 14 metrics listed above are tagged with correct `type`
- [ ] `engine_output_contract.py` normalizer now reports correct proxy_count for vol engine (should be ≥5)
- [ ] Proxy metrics include `notes` explaining what they actually are vs what they claim to be
- [ ] `vix_2nd_month` and `vix_3rd_month` entries clearly state "fabricated heuristic, not market data"
- [ ] Existing engine scoring behavior is unchanged (this is metadata-only, no scoring changes)
- [ ] Unit test: verify `normalize_engine_output()` produces proxy_count ≥ 5 for vol engine output

### Dependencies
None — metadata addition only, no scoring logic changes.

### Estimated Scope
Small: ~80-100 lines of dict definition + verification.

---

## FN-3: Delta-Presence Gate in Options Phase D

### Problem
Options Phase D validates bid/ask presence and OI/volume presence, but does NOT validate delta presence. Contracts with missing delta pass all hygiene checks, produce POP=None and EV=None, and enter ranking where they can't be meaningfully compared.

### Files Involved
| File | Role |
|------|------|
| `app/services/scanner_v2/phases.py` | Phase D functions: `phase_d_quote_liquidity_sanity()`, `phase_d2_trust_hygiene()` |
| `app/services/scanner_v2/data/chain.py` | `normalize_contract()` — where delta is extracted via `_resolve_greek()` |
| `app/services/scanner_v2/families/*.py` | Family math functions that consume delta for POP |

### Current Behavior
```python
# phases.py — phase_d_quote_liquidity_sanity():
# Checks: bid is not None, ask is not None, bid <= ask, OI present, volume present
# Does NOT check: delta is not None
# Result: contract with bid=1.50, ask=1.60, delta=None passes Phase D
```

### Target Behavior
For candidates where POP computation requires delta (all families except calendars/diagonals):
1. Check that short leg(s) have non-None delta
2. If delta is missing on a short leg, reject with code `v2_missing_short_delta`
3. For long legs, delta=None is acceptable (it doesn't affect POP)
4. Calendars/diagonals are exempt (they already set POP=None by design)

### Pattern to Follow
Existing Phase D rejection pattern:
```python
# Current pattern in phase_d_quote_liquidity_sanity():
if leg.bid is None:
    reject_reasons.append("v2_missing_bid")
    passed = False
# New check should follow same pattern:
if leg.is_short and leg.delta is None:
    reject_reasons.append("v2_missing_short_delta")
    passed = False
```

### Acceptance Criteria
- [ ] New rejection code `v2_missing_short_delta` added to rejection taxonomy
- [ ] Phase D checks delta presence on short legs for vertical, iron condor, and butterfly families
- [ ] Calendars/diagonals are exempt from delta check
- [ ] Rejected candidates have the rejection code in their `reject_reasons` list
- [ ] Phase survival counts reflect the new gate (some candidates now rejected that previously passed)
- [ ] Existing valid candidates (with delta present) are unaffected
- [ ] Unit test: construct a candidate with short leg delta=None → verify rejection with correct code

### Dependencies
None — additive check in existing phase.

### Estimated Scope
Small: ~15-25 lines of new validation logic + taxonomy entry.

---

## FS-1: Pass Data-Quality Tags Through to Engines

### Problem
All data providers call `_extract_value()` which strips the metric envelope to a bare float. Engines receive numbers with no metadata about freshness, source, or proxy status.

### Files Involved
| File | Role |
|------|------|
| `app/services/cross_asset_macro_data_provider.py` (line 40-54) | Has `_extract_value()` |
| `app/services/flows_positioning_data_provider.py` (line 26-29) | Has `_extract_value()` |
| `app/services/liquidity_conditions_data_provider.py` (line 28) | Has `_extract_value()` |
| `app/services/volatility_options_data_provider.py` | Inline extraction |
| `app/services/news_sentiment_service.py` | Inline extraction |

### Current Behavior
```python
# All providers:
def _extract_value(metric):
    if isinstance(metric, dict):
        return metric.get("value")  # Strips source, freshness, observation_date
    return metric
```

### Target Behavior
Option A (lightweight — recommended): Add a companion `_extract_quality()` function that produces a quality summary dict alongside the value. Both value and quality are passed to the engine.

```python
def _extract_quality(metric):
    """Extract data-quality metadata from metric envelope."""
    if not isinstance(metric, dict):
        return {"source": "unknown", "age_days": None, "is_proxy": False}
    obs_date = metric.get("observation_date")
    age_days = _days_stale(obs_date) if obs_date else None
    return {
        "source": metric.get("source", "unknown"),
        "freshness": metric.get("freshness", "unknown"),
        "age_days": age_days,
        "observation_date": obs_date,
        "is_proxy": False,  # Override per-metric where applicable
    }
```

Option B (heavier): Replace bare floats with `QualifiedValue` dataclass throughout the engine interface. More type-safe but requires engine signature changes.

### Pattern to Follow
The liquidity data provider already extracts freshness metadata via `_extract_source()` and `_extract_freshness()` (audit 1E finding 1E-07). Extend this pattern to all providers, and make the extracted quality data available to engines (not just stored in source_meta).

### Acceptance Criteria
- [ ] `_extract_quality()` is a shared utility (not per-provider)
- [ ] `_extract_value()` is also shared (consolidate the 4 duplicate implementations)
- [ ] Every data provider produces a `data_quality` companion dict alongside pillar data
- [ ] Engine input assembly includes data_quality in kwargs (or a parallel structure)
- [ ] At minimum, engines can access `max_age_days` across their inputs to modulate confidence
- [ ] Existing engine scoring is not broken (quality data is additive, not a schema change to pillar dicts)
- [ ] Unit test: mock a metric envelope with observation_date 5 days old → verify quality dict shows age_days=5

### Dependencies
- FN-1 (staleness computation utility) should be implemented first, since `_extract_quality()` will use it

### Estimated Scope
Medium: ~100-150 lines of shared utility + changes to 5 data providers + engine kwarg additions.

---

## FS-2: Fix `fetched_at` Semantics

### Problem
`fetched_at` in the metric envelope is set at envelope construction time (`datetime.now(UTC)`), not at API call time. Due to layered caching (FRED 300s cache under Market Context 30s cache), `fetched_at` always shows approximately "now" even when the underlying data was fetched minutes ago.

### Files Involved
| File | Role |
|------|------|
| `app/services/market_context_service.py` (line 77) | `_metric()` sets `fetched_at = datetime.now(UTC)` |
| `app/workflows/market_intelligence_runner.py` (lines 846-908) | `_build_freshness_section()` uses `fetched_at` for staleness tiers |
| `app/clients/fred_client.py` | FredClient — could record actual API call timestamp |
| `app/clients/tradier_client.py` | TradierClient — could record actual API call timestamp |

### Current Behavior
```python
# market_context_service.py line 77:
"fetched_at": datetime.now(timezone.utc).isoformat(),
# This is set EVERY TIME _metric() is called, regardless of whether the
# underlying data was just fetched or served from a 300-second cache.
```

### Target Behavior
**Recommended approach**: Stop relying on `fetched_at` for staleness. Switch all freshness checks to `observation_date`-based computation.

For FRED data: `observation_date` is the authoritative staleness indicator.
For Tradier intraday data: freshness should be gated by market-hours awareness (see FS-3). During market hours, a Tradier quote is assumed current (10s cache). During off-hours, it's marked as the prior session's close.

### Specific Changes
1. In `_build_freshness_section()`: replace `fetched_at` age computation with `observation_date` age computation for FRED-sourced metrics
2. For Tradier-sourced metrics (no observation_date): use `is_intraday=True` + market-hours check
3. Alternatively, record `api_call_timestamp` in the client-level cache and propagate it through the envelope (more work but more precise)

### Acceptance Criteria
- [ ] `_build_freshness_section()` no longer uses `fetched_at` as the sole staleness indicator
- [ ] FRED-sourced metrics use `observation_date` vs current date for staleness tiers
- [ ] Tradier-sourced metrics during market hours show "fresh"
- [ ] Tradier-sourced metrics during off-hours show "eod" or "stale" (not "intraday")
- [ ] MI Runner freshness tiers now correctly identify data that's actually stale (e.g., FRED data from 3 days ago)
- [ ] Unit test: mock FRED metric with observation_date 3 days old, fetched_at now → verify tier is "warning" not "fresh"

### Dependencies
- FS-3 (market hours awareness) for Tradier staleness classification
- FN-1 (staleness computation utility) for observation_date age calculation

### Estimated Scope
Medium: ~60-100 lines of changes to freshness computation logic.

---

## FS-3: Market Hours Awareness in Data Pipeline

### Problem
`_is_market_open()` exists in `app/trading/risk.py` but is only used for order risk warnings. The MI Runner, Market Context Service, and scanners have no market-hours awareness. This causes unnecessary API load on weekends/holidays, and Friday's VIX close is labeled "intraday" on Monday.

### Files Involved
| File | Role |
|------|------|
| `app/trading/risk.py` (lines 16-22) | Existing `_is_market_open()` — basic but functional |
| `app/services/data_population_service.py` (lines 118-131) | MI Runner loop — runs every 5 min 24/7 |
| `app/services/market_context_service.py` | No hours awareness |
| `app/utils/market_hours.py` (NEW) | Proposed shared utility |

### Current Behavior
```python
# risk.py lines 16-22:
def _is_market_open(now=None):
    ts = now or datetime.now(timezone.utc)
    if ts.weekday() >= 5:
        return False
    mins = ts.hour * 60 + ts.minute
    return 14 * 60 + 30 <= mins <= 21 * 60  # UTC approximation, no DST
```

### Target Behavior
1. Extract market-hours logic into a shared utility (`app/utils/market_hours.py`)
2. Add US market holiday calendar (at least the standard NYSE holidays)
3. Handle DST (Eastern Time zone conversion, not UTC window approximation)
4. Wire into MI Runner: during off-hours, either skip runs or extend cache TTLs significantly
5. Wire into Market Context Service: during off-hours, label Tradier quotes as "prior_close" not "intraday"

### Acceptance Criteria
- [ ] Shared `market_hours.py` utility with `is_market_open()`, `is_extended_hours()`, `next_market_open()`
- [ ] US holiday calendar for current year (at minimum: New Year, MLK, Presidents Day, Good Friday, Memorial Day, Juneteenth, July 4th, Labor Day, Thanksgiving, Christmas)
- [ ] DST-aware (uses `pytz` or `zoneinfo` for US/Eastern)
- [ ] MI Runner either skips runs during off-hours or extends interval to 30+ minutes
- [ ] Market Context Service downgrades `freshness` from "intraday" to "prior_close" during off-hours for Tradier-sourced data
- [ ] `risk.py` updated to use the shared utility instead of its own inline implementation
- [ ] Unit test: verify Saturday returns is_market_open=False, verify Christmas returns False, verify DST transition dates are correct

### Dependencies
None — but FS-2 (fetched_at fix) benefits from this being in place.

### Estimated Scope
Medium: ~80-120 lines for the utility + ~30 lines of wiring into MI Runner and Market Context Service.

---

## FS-4: Unify Freshness Vocabulary

### Problem
Three incompatible freshness classification systems exist:
- Metric envelope: `"intraday"` / `"eod"` / `"delayed"` (source type)
- MI Runner: `"fresh"` / `"warning"` / `"stale"` (age-based)
- Confidence framework: `"live"` / `"recent"` / `"stale"` / `"very_stale"` (penalty-mapped)

### Files Involved
| File | Role |
|------|------|
| `app/services/market_context_service.py` (lines 57-68) | `_metric()` freshness assignment |
| `app/workflows/market_intelligence_runner.py` (lines 846-908) | `_build_freshness_section()` tier assignment |
| `app/services/confidence_framework.py` | `FRESHNESS_PENALTIES` dict, `impact_from_freshness()` |

### Target Behavior
A single `compute_data_currency()` function that takes:
- `observation_date` (when the data was observed in the market)
- `source_type` ("tradier" / "finnhub" / "fred")
- `current_time` (now)
- `is_market_open` (from FS-3)

And returns:
- A standardized freshness tier from the confidence framework vocabulary (`"live"` / `"recent"` / `"stale"` / `"very_stale"`)
- The corresponding confidence penalty from `FRESHNESS_PENALTIES`

### Acceptance Criteria
- [ ] Single `compute_data_currency()` function in a shared location
- [ ] Maps all three existing vocabularies into one
- [ ] MI Runner uses the unified function instead of its own tier logic
- [ ] Metric envelope's `freshness` field is either replaced or supplemented with the unified tier
- [ ] Confidence framework penalties are automatically applied via the unified function
- [ ] Unit test: FRED data from 1 day ago during market hours → "recent" tier, 0.00 penalty; FRED data from 5 days ago → "stale" tier, 0.10 penalty

### Dependencies
- FS-2 (fetched_at fix) and FS-3 (market hours) should be in place first
- FN-1 (staleness utility) provides the age calculation

### Estimated Scope
Medium: ~60-80 lines for the unified function + refactoring callers.

---

## FS-5: Confidence Penalty for Proxy-Heavy Engines

### Problem
Only the Flows engine reduces confidence for proxy reliance. The Volatility engine (5+ proxies), Liquidity engine (4 proxies), and News engine (keyword-based proxy sentiment) apply no proxy-related confidence penalties.

### Files Involved
| File | Role |
|------|------|
| `app/services/flows_positioning_engine.py` | **Pattern to follow** — has proxy penalties in `_compute_confidence()` |
| `app/services/volatility_options_engine.py` | Needs proxy penalties added to confidence |
| `app/services/liquidity_conditions_engine.py` | Needs proxy penalties added to confidence |
| `app/services/news_sentiment_engine.py` | Needs confidence mechanism entirely |

### Current Behavior (Flows — the pattern)
```python
# flows_positioning_engine.py — _compute_confidence():
# Heavy proxy reliance (≥4 proxy sources): -8
# No direct institutional flow data: -5
# No direct futures positioning data: -5
# Single-source dependency (1 upstream, ≥6 proxies): -12
```

### Target Behavior
Each engine's `_compute_confidence()` should include penalties based on its SIGNAL_PROVENANCE:
- Count proxies from SIGNAL_PROVENANCE dict
- If proxy_count >= 3: apply -5 penalty
- If proxy_count >= 5: apply -10 penalty
- If any proxy-of-proxy metrics exist: apply additional -3 per P-of-P metric (cap -9)
- If engine has no SIGNAL_PROVENANCE: treat as unknown quality, apply -5 baseline

### Acceptance Criteria
- [ ] Volatility engine confidence penalizes for proxy metrics (vix_rank, term structure, etc.)
- [ ] Liquidity engine confidence penalizes for FCI proxy, funding stress proxy, etc.
- [ ] News engine has a `_compute_confidence()` function (currently none exists)
- [ ] Penalty amounts are proportional to proxy density (not binary)
- [ ] Engine confidence scores are lower for proxy-heavy engines than for direct-data engines (breadth should be highest confidence, flows lowest)
- [ ] Unit test: verify vol engine confidence is lower than breadth engine confidence given identical coverage

### Dependencies
- FN-2 (SIGNAL_PROVENANCE for vol engine) must be in place first

### Estimated Scope
Medium: ~50-80 lines per engine for confidence adjustments.

---

## FL-1: Cross-Series Date Alignment for Derived Metrics

### Problem
Yield curve spread (DGS10 - DGS2) is computed without verifying both series share the same observation date.

### Files Involved
| File | Role |
|------|------|
| `app/services/market_context_service.py` (lines 210-228) | Yield spread computation |

### Current Behavior
```python
ten_year, two_year = await asyncio.gather(
    self._fred_metric("DGS10"),
    self._fred_metric("DGS2"),
)
if ten_year["value"] is not None and two_year["value"] is not None:
    yield_spread_val = round(ten_year["value"] - two_year["value"], 3)
    # NO CHECK: ten_year["observation_date"] == two_year["observation_date"]
```

### Target Behavior
Before computing derived metrics from multiple FRED series:
1. Compare `observation_date` fields
2. If dates match: compute normally
3. If dates differ by 1 business day: compute but log warning, add `"cross_series_date_mismatch": True` to source metadata
4. If dates differ by >1 business day: still compute but flag in source_meta with severity

### Acceptance Criteria
- [ ] Yield spread computation checks observation_date alignment
- [ ] Mismatched dates are logged and flagged in source_meta
- [ ] Any future derived metric combining multiple FRED series follows the same pattern
- [ ] Unit test: mock DGS10 with obs_date Monday, DGS2 with obs_date Friday → verify warning logged

### Dependencies
None.

### Estimated Scope
Small: ~20-30 lines.

---

## FL-2: Input Validation at Engine Entry Points

### Problem
No schema validation, type checking, or completeness assertion occurs before engine invocation. Malformed data causes runtime errors caught only by per-pillar try/except.

### Files Involved
| File | Role |
|------|------|
| `app/services/breadth_service.py` | Service layer — calls engine |
| `app/services/volatility_options_service.py` | Service layer — calls engine |
| `app/services/cross_asset_macro_service.py` | Service layer — calls engine |
| `app/services/flows_positioning_service.py` | Service layer — calls engine |
| `app/services/liquidity_conditions_service.py` | Service layer — calls engine |
| `app/services/news_sentiment_service.py` | Service layer — calls engine |

### Target Behavior
Add a lightweight `validate_engine_input(pillar_dicts, expected_schema)` function that:
1. Asserts all expected top-level keys are present
2. Asserts values are `float | int | None` for numeric fields
3. Asserts values are within plausible ranges where applicable (e.g., RSI 0-100, yields 0-20%, VIX 0-100)
4. Logs warnings for out-of-range values but doesn't reject (engines should be robust)
5. Returns a validation summary dict that can be included in engine diagnostics

### Acceptance Criteria
- [ ] Shared `validate_engine_input()` function
- [ ] Called by each service layer before engine invocation
- [ ] Catches type errors (string where float expected)
- [ ] Catches extreme values (VIX of 500, yield of -10%)
- [ ] Does not block engine execution — logs and continues
- [ ] Validation summary included in engine output diagnostics

### Dependencies
None.

### Estimated Scope
Medium: ~80-100 lines for the validator + ~5 lines per service to wire it in.

---

## FL-3: Move Pre-Computed Signals From Vol Data Provider Into Engine

### Problem
The vol data provider computes `tail_risk_signal/numeric`, `option_richness/label`, and `premium_bias` before the engine sees the data. The provider's threshold choices shape the engine's interpretation. Changing engine scoring has limited effect.

### Files Involved
| File | Role |
|------|------|
| `app/services/volatility_options_data_provider.py` (lines 140-233) | Current pre-computation location |
| `app/services/volatility_options_engine.py` | Should own these computations |

### Target Behavior
1. Data provider passes raw components: `put_skew_25d`, `cboe_skew`, `vix_rank_30d`, `iv_30d`, `rv_30d`, `equity_pc_ratio`
2. Engine computes `tail_risk_numeric`, `option_richness`, `premium_bias` internally using its own threshold logic
3. Provider no longer pre-digests signals

### Acceptance Criteria
- [ ] Provider output contains only raw/direct metrics, no pre-computed signals
- [ ] Engine receives raw components and computes derived signals internally
- [ ] Final engine scores are identical (formulas moved, not changed)
- [ ] Unit test: same inputs → same outputs before and after refactor

### Dependencies
- FN-2 (SIGNAL_PROVENANCE) should be done first so the moved metrics are properly tagged

### Estimated Scope
Medium: ~100-150 lines moved from provider to engine.

---

## FL-4: Client-Level Rate Limiter for Tradier

### Problem
No rate limiter on TradierClient. Breadth engine fires ~150 bar requests per cycle. HTTP 429 is detected but not retried.

### Files Involved
| File | Role |
|------|------|
| `app/clients/tradier_client.py` | Client class — needs rate limiter |
| `app/utils/http.py` | HTTP utility — currently retries 502/503/504 but not 429 |

### Target Behavior
1. Add `asyncio.Semaphore` or token-bucket rate limiter at TradierClient level
2. Add 429 retry with exponential backoff (separate from 5xx retry)
3. Configure Tradier rate limit (typically 120 requests/minute for market data)

### Acceptance Criteria
- [ ] TradierClient has a configurable rate limiter
- [ ] HTTP 429 responses trigger retry with backoff (not immediate failure)
- [ ] Rate limiter prevents burst requests from exceeding Tradier's limits
- [ ] Existing breadth engine semaphore(10) still functions as concurrency control
- [ ] Log when rate limiter throttles requests

### Dependencies
None.

### Estimated Scope
Small-Medium: ~40-60 lines for rate limiter + ~20 lines for 429 retry logic.

---

## FL-5: Add SIGNAL_PROVENANCE to News Engine

### Problem
News engine has no SIGNAL_PROVENANCE, no proxy labeling, and no confidence mechanism. Keyword-based sentiment scoring is proxy-like but unlabeled.

### Files Involved
| File | Role |
|------|------|
| `app/services/news_sentiment_engine.py` | Engine file — needs SIGNAL_PROVENANCE + confidence |
| `app/services/flows_positioning_engine.py` | **Pattern to follow** |

### Target Behavior
Add SIGNAL_PROVENANCE tagging headline_sentiment as proxy (keyword matching), negative_pressure as proxy-of-proxy, narrative_severity as proxy, source_agreement as proxy-of-proxy, macro_stress as derived, recency_pressure as proxy-of-proxy.

Also add a `_compute_confidence()` function (currently none exists).

### Acceptance Criteria
- [ ] SIGNAL_PROVENANCE dict added
- [ ] `_compute_confidence()` function added
- [ ] Confidence penalizes for keyword-based proxy nature
- [ ] engine_output_contract.py reports correct proxy_count for news engine

### Dependencies
None.

### Estimated Scope
Small: ~60-80 lines.

---

## FL-6: Regime Service FRED Cache Bypass

### Problem
Regime service calls FRED directly via `request_json()`, bypassing the FredClient 300s cache. Creates redundant API calls and potential data inconsistency with Market Context Service.

### Files Involved
| File | Role |
|------|------|
| `app/services/regime_service.py` | `_fred_recent_values()` bypasses cache |

### Target Behavior
Either:
1. Route regime service FRED calls through FredClient (preferred — uses cache), or
2. Add a dedicated cache for regime service multi-observation fetches

Note: regime service needs 6 observations (not just latest), so it can't use the standard `get_series_with_date()` which returns only the latest. May need a new FredClient method: `get_recent_observations(series_id, count)` with its own cache key.

### Acceptance Criteria
- [ ] Regime service FRED calls go through a cached path
- [ ] No duplicate API calls for the same FRED series during a single MI cycle
- [ ] Regime service and Market Context Service see consistent data for VIX/DGS10

### Dependencies
None.

### Estimated Scope
Medium: ~40-60 lines for new FredClient method + regime service refactor.

---

## FL-7: Chain Completeness and Bid-Zero Checks

### Problem
1. No chain completeness check — a chain with 3 contracts looks the same as one with 300
2. Bid=0 passes validation — produces $0 credit in spread calculations

### Files Involved
| File | Role |
|------|------|
| `app/services/base_data_service.py` | `normalize_chain()` — chain validation |
| `app/clients/tradier_client.py` | `get_chain()` — raw chain fetch |
| `app/services/scanner_v2/phases.py` | Phase D — quote hygiene |

### Target Behavior
1. After chain normalization, check contract count. If < 20 contracts for an index ETF chain, log warning and include in diagnostics.
2. In Phase D or D2, add a check: if a short leg has bid=0, reject with `v2_zero_bid_short_leg`. (The existing credibility gate catches penny net_credit, but individual zero-bid legs can still participate in multi-leg constructions.)

### Acceptance Criteria
- [ ] Chain completeness warning logged when contract count is unexpectedly low
- [ ] Zero-bid short legs rejected in Phase D/D2 with specific rejection code
- [ ] Existing valid candidates unaffected

### Dependencies
None.

### Estimated Scope
Small: ~20-30 lines.

---

## Cross-Reference: Finding → Fix Mapping

| Audit Finding | Fix ID | Severity |
|--------------|--------|----------|
| 1B Critical #1-2 (FRED staleness) | FN-1 | Fix Now |
| 1C Flag #1 (vol engine no SIGNAL_PROVENANCE) | FN-2 | Fix Now |
| 1F Finding 1F-06 (delta-presence gate) | FN-3 | Fix Now |
| 1D Critical C2 (engines get bare values) | FS-1 | Fix Soon |
| 1D Critical C1 (fetched_at misrepresents age) | FS-2 | Fix Soon |
| 1D High H3 (no market hours) | FS-3 | Fix Soon |
| 1D Medium M2 (three freshness vocabularies) | FS-4 | Fix Soon |
| 1C Flag #3 (confidence only penalizes flows) | FS-5 | Fix Soon |
| 1B Critical #3 (cross-series dates) | FL-1 | Fix Later |
| 1E Finding 1E-01 (no input validation) | FL-2 | Fix Later |
| 1E Finding 1E-04 (vol pre-computation) | FL-3 | Fix Later |
| 1A Critical #1-2 (rate limiting) | FL-4 | Fix Later |
| 1C Flag #2 (news engine no provenance) | FL-5 | Fix Later |
| 1B High #4 (regime bypasses cache) | FL-6 | Fix Later |
| 1A High #5-6 (bid=0, chain completeness) | FL-7 | Fix Later |

---

## Implementation Order (Recommended)

### Wave 1 (Independent, no dependencies)
Run these in any order — each is self-contained:
- **FN-1** (FRED staleness) — highest ROI single fix
- **FN-2** (Vol SIGNAL_PROVENANCE) — metadata only
- **FN-3** (Delta gate) — small additive check

### Wave 2 (FN-1 should be done first)
- **FS-3** (Market hours utility) — no dependencies but enables FS-2 and FS-4
- **FS-1** (Data quality tags) — uses FN-1's staleness utility
- **FS-5** (Proxy confidence penalties) — uses FN-2's SIGNAL_PROVENANCE

### Wave 3 (FS-2 and FS-3 should be done first)
- **FS-2** (Fix fetched_at) — uses FS-3's market hours
- **FS-4** (Unify freshness vocabulary) — uses FS-2 and FS-3

### Wave 4 (Independent hardening)
Run in any order after Waves 1-3:
- **FL-1** through **FL-7**

---

*End of Pass 1 Fix Specifications*
