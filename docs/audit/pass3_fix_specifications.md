# BenTrade Foundation Audit — Pass 3 Fix Specifications
## Filtering & Selection Layer: Implementation Guide for Copilot Prompts

**Date**: 2026-03-20
**Purpose**: Structured fix specs for every Pass 3 finding. Each spec contains exact files, current behavior, target behavior, pattern to follow, and acceptance criteria.

**How to use this document**: When ready to generate Copilot prompts, feed this document (plus the original audit files 3A-3E for code snippet context) to Claude and ask for prompts targeting specific fix IDs.

---

## Fix Priority Tiers

| Tier | Fix IDs |
|------|---------|
| **FN (Fix Now)** | FN-7, FN-8 |
| **FS (Fix Soon)** | FS-11, FS-12, FS-13 |
| **FL (Fix Later)** | FL-14, FL-15, FL-16 |

*Note: IDs continue from Pass 2 (FN-1 through FN-6, FS-1 through FS-10, FL-1 through FL-13)*

---

## FN-7: Wire Event Calendar Into Options Pipeline as Soft Gate

### Problem
EventCalendarContext produces event risk state (crowded/elevated/quiet), overlap counts, and 8+ warning flags for FOMC, CPI, NFP, and earnings proximity. No consumer uses these to gate or adjust trade decisions. A 5-DTE put credit spread expiring through FOMC tomorrow has no system-level awareness of the event.

### Files Involved
| File | Role |
|------|------|
| `app/services/event_calendar_context.py` | Existing service — produces risk_state, overlap, warning flags |
| `app/workflows/options_opportunity_runner.py` L918-1040 | Stage 4 (enrich_evaluate) — where gating should be added |
| `app/workflows/stock_opportunity_runner.py` L930-1095 | Stage 5 (enrich) — stock pipeline equivalent |

### Current Behavior
```python
# options_opportunity_runner.py Stage 4:
# Enrichment attaches market_regime and risk_environment
# NO event calendar data loaded or attached
# Credibility gate runs 3 checks (penny, delta, fillability)
# NO event proximity check
```

### Target Behavior
In Stage 4, after market state enrichment and before credibility gate:
1. Load event calendar context via `EventCalendarContext` (it already handles FOMC, CPI, NFP, earnings)
2. For each candidate, check if any high-impact events fall within the candidate's DTE window
3. Attach `event_risk` field to each candidate:
   - `"high"` — high-impact event within 24 hours of expiration
   - `"elevated"` — high-impact event within DTE window
   - `"quiet"` — no significant events in DTE window
   - `"unknown"` — event calendar unavailable
4. Attach `event_details` with specific event names and dates
5. **Phase 1 (now)**: Flag only — do not reject candidates
6. **Phase 2 (later)**: Optionally reject or penalize candidates with `event_risk: "high"`

### Pattern to Follow
EventCalendarContext already provides:
```python
# event_calendar_context.py — existing output shape:
{
    "risk_state": "crowded" | "elevated" | "quiet" | "unknown",
    "events_in_window": [...],
    "overlap_count": int,
    "warning_flags": ["high_importance_event_within_24h", ...],
}
```

The enrichment should follow the same pattern as market_regime attachment:
```python
# Current pattern in Stage 4:
cand["market_regime"] = consumer_summary.get("market_state")
cand["risk_environment"] = consumer_summary.get("stability_state")

# New pattern (add alongside existing enrichment):
event_ctx = await event_calendar_service.get_context(
    symbol=cand["symbol"],
    dte=cand.get("dte"),
    expiration=cand.get("expiration"),
)
cand["event_risk"] = event_ctx.get("risk_state", "unknown")
cand["event_details"] = event_ctx.get("events_in_window", [])
```

### Acceptance Criteria
- [ ] Event calendar context loaded during Stage 4 enrichment
- [ ] Each options candidate has `event_risk` field in output (high/elevated/quiet/unknown)
- [ ] Each options candidate has `event_details` list with event names and dates
- [ ] Candidates with FOMC/CPI within DTE window show `event_risk: "elevated"` or `"high"`
- [ ] No candidates are rejected in Phase 1 (soft gate — flag only)
- [ ] Event calendar service failure → `event_risk: "unknown"` (graceful degradation, no pipeline abort)
- [ ] Output includes event_risk in the compact candidate shape for frontend display
- [ ] Same enrichment added to stock pipeline (Stage 5) for consistency
- [ ] Unit test: candidate with 7 DTE, FOMC in 5 days → event_risk="elevated"
- [ ] Unit test: candidate with 30 DTE, no events → event_risk="quiet"
- [ ] Unit test: event calendar service down → event_risk="unknown"

### Dependencies
None — EventCalendarContext already exists and is functional.

### Estimated Scope
Medium: ~60-80 lines to integrate event calendar into Stage 4 + ~30 lines for stock pipeline equivalent.

---

## FN-8: Wire Regime Label Into Pipelines as Soft Gate

### Problem
RegimeService classifies the market as RISK_ON / NEUTRAL / RISK_OFF with a playbook suggesting strategy types per regime. Neither the stock nor options pipeline uses this label for filtering, threshold adjustment, or strategy blocking. The system scans premium-selling strategies at full aggression during RISK_OFF conditions.

### Files Involved
| File | Role |
|------|------|
| `app/services/regime_service.py` | Produces regime_label, playbook, what_works/what_to_avoid |
| `app/workflows/options_opportunity_runner.py` L918-1040 | Stage 4 — options enrichment |
| `app/workflows/stock_opportunity_runner.py` L930-1095 | Stage 5 — stock enrichment |

### Current Behavior
```python
# Both pipelines:
# market_regime loaded in Stage 1 from consumer_summary
# Attached to candidates as enrichment field
# NEVER used for filtering, scoring adjustment, or strategy blocking
```

### Target Behavior
**Phase 1 (soft gate — implement now):**

Define a strategy-regime alignment mapping:
```python
# Proposed alignment mapping:
REGIME_ALIGNMENT = {
    "risk_off": {
        "aligned": ["put_debit", "bear_put_spread"],          # Protective/bearish
        "neutral": ["iron_condor", "calendar_call_spread"],    # Defined risk
        "misaligned": ["put_credit_spread", "call_credit_spread", "iron_butterfly",
                       "stock_pullback_swing", "stock_momentum_breakout"],  # Premium selling / bullish
    },
    "neutral": {
        "aligned": ["iron_condor", "calendar_call_spread", "calendar_put_spread",
                     "butterfly_debit", "stock_pullback_swing"],
        "neutral": ["put_credit_spread", "call_debit", "stock_mean_reversion"],
        "misaligned": [],  # Nothing is misaligned in neutral
    },
    "risk_on": {
        "aligned": ["put_credit_spread", "call_debit", "stock_pullback_swing",
                     "stock_momentum_breakout"],
        "neutral": ["iron_condor", "calendar_call_spread", "stock_volatility_expansion"],
        "misaligned": ["put_debit"],  # Bearish in risk-on
    },
}
```

For each candidate:
1. Look up `regime_alignment` from the mapping using `market_regime` + `scanner_key`
2. Attach `regime_alignment` field: `"aligned"` / `"neutral"` / `"misaligned"`
3. When `regime_alignment == "misaligned"`, also attach `regime_warning` string explaining why
4. Do NOT reject — flag only

**Phase 2 (hard gate — implement later):**
1. RISK_OFF + misaligned → apply score penalty (-10 points) or require higher min_pop
2. Consider suppressing misaligned strategies from final output entirely

### Acceptance Criteria
- [ ] Each candidate has `regime_alignment` field (aligned/neutral/misaligned)
- [ ] Each misaligned candidate has `regime_warning` string
- [ ] RISK_OFF + put_credit_spread → regime_alignment="misaligned"
- [ ] RISK_ON + call_debit → regime_alignment="aligned"
- [ ] NEUTRAL + iron_condor → regime_alignment="aligned"
- [ ] No candidates are rejected in Phase 1 (soft gate)
- [ ] Alignment mapping is defined as a constant dict (easy to adjust)
- [ ] When market_regime is None/unavailable → regime_alignment="unknown"
- [ ] Both stock and options pipelines enriched consistently
- [ ] Unit test: verify regime_alignment is correct for each strategy×regime combination
- [ ] Unit test: unknown regime → regime_alignment="unknown"

### Dependencies
None.

### Estimated Scope
Small-Medium: ~40-60 lines for alignment mapping + enrichment logic per pipeline.

---

## FS-11: Wire RiskPolicyService Into Preview Flow

### Problem
23 risk policy constants are defined, user-adjustable via API, and compared against active positions. The trading execution path (preview + submit) never calls RiskPolicyService. A user can submit an order that violates every policy limit.

### Files Involved
| File | Role |
|------|------|
| `app/services/risk_policy_service.py` L477-530 | `build_snapshot()` — produces hard_limits/soft_gates |
| `app/trading/service.py` L399-430 | Preview risk checks — should integrate RiskPolicyService |
| `app/trading/risk.py` L34-72 | Existing 4 hard checks (width, max_loss, credit, bid/ask) |

### Current Behavior
```python
# trading/service.py — preview():
# Runs 4 risk hard checks (width_ok, max_loss_ok, credit_floor_ok, legs_have_bid_ask)
# Returns preview ticket
# NEVER calls RiskPolicyService
# NEVER checks portfolio limits, concentration, or risk budgets
```

### Target Behavior
After existing risk checks pass, add policy check:
```python
# In preview flow, after existing risk checks:
snapshot = await risk_policy_service.build_snapshot(proposed_trade=ticket)
policy_warnings = []
if snapshot.get("hard_limits"):
    policy_warnings.extend(snapshot["hard_limits"])
if snapshot.get("soft_gates"):
    policy_warnings.extend(snapshot["soft_gates"])

# Phase 1: attach warnings to preview response (don't block)
preview_response["policy_warnings"] = policy_warnings
preview_response["policy_status"] = "warning" if policy_warnings else "clear"

# Phase 2 (later): block preview when critical hard_limits violated
# if any(w["severity"] == "blocking" for w in hard_limits):
#     raise HTTPException(400, detail={"policy_violation": hard_limits})
```

### Acceptance Criteria
- [ ] Preview response includes `policy_warnings` array (may be empty)
- [ ] Preview response includes `policy_status` ("clear" or "warning")
- [ ] Hard limit violations (e.g., max_risk_per_trade exceeded) appear in policy_warnings
- [ ] Soft gate violations (e.g., approaching concentration limit) appear in policy_warnings
- [ ] Preview is NOT blocked in Phase 1 (warning only)
- [ ] RiskPolicyService failure → empty policy_warnings (graceful degradation)
- [ ] Frontend can display policy_warnings to the user before confirmation
- [ ] Unit test: propose trade exceeding max_risk_per_trade → policy_warning present with description
- [ ] Unit test: propose trade within all limits → policy_warnings empty, policy_status="clear"
- [ ] Unit test: RiskPolicyService unavailable → policy_warnings empty, no preview failure

### Dependencies
None — RiskPolicyService already exists with build_snapshot().

### Estimated Scope
Medium: ~50-70 lines to integrate into preview flow + ~10 lines for response shape.

---

## FS-12: Add ExecutionValidator to Submit Path

### Problem
`validate_trade_for_execution()` is only called from frontend-facing endpoints (/validate, /build-payload). The actual `TradingService.submit()` does not run this validator. Direct API calls can bypass frontend validation.

### Files Involved
| File | Role |
|------|------|
| `app/trading/execution_validator.py` L134-213 | `validate_trade_for_execution()` — 7+ blocking checks |
| `app/trading/service.py` | `submit()` — should call validator before broker call |

### Current Behavior
```python
# service.py — submit():
# 1. Token validation
# 2. Freshness gate (live only)
# 3. Credential resolution
# 4. Broker API call
# Does NOT call validate_trade_for_execution()
```

### Target Behavior
```python
# service.py — submit(), before broker API call:
from app.trading.execution_validator import validate_trade_for_execution

validation = validate_trade_for_execution(ticket)
if not validation.get("valid"):
    raise HTTPException(400, detail={
        "error": "server_side_validation_failed",
        "issues": validation.get("issues", []),
    })
# Proceed to broker API call
```

### Acceptance Criteria
- [ ] `submit()` calls `validate_trade_for_execution()` before broker API call
- [ ] Validation failure returns HTTP 400 with structured error and issue list
- [ ] No change to existing frontend flow (frontend still calls /validate separately)
- [ ] Validation runs AFTER token validation but BEFORE freshness gate
- [ ] Unit test: submit malformed trade (missing OCC symbol) directly → 400 with validation issue
- [ ] Unit test: submit valid trade → validation passes, reaches broker call

### Dependencies
None.

### Estimated Scope
Small: ~10-15 lines added to submit().

---

## FS-13: Graceful Degradation When Model Analysis Fully Fails

### Problem
When LLM service is down, Stage 7b removes all candidates without model analysis, producing 0 final output. High-quality scanner candidates are lost because an unrelated service was unavailable.

### Files Involved
| File | Role |
|------|------|
| `app/workflows/stock_opportunity_runner.py` L1406-1490 | Stage 7b — model_filter_rank |
| `app/workflows/stock_opportunity_runner.py` L1163-1406 | Stage 7 — run_final_model_analysis |

### Current Behavior
```python
# Stage 7b:
for cand in selected:
    if cand["model_review"] is None:
        no_analysis.append(cand)          # Dropped
    elif cand["model_recommendation"] == "PASS":
        passed.append(cand)               # Dropped
    else:
        buy_candidates.append(cand)       # Kept

# When ALL model calls fail → buy_candidates = [] → output has 0 candidates
```

### Target Behavior
```python
# Stage 7b with degradation awareness:
model_available = any(c.get("model_review") is not None for c in selected)

if not model_available:
    # FULL DEGRADATION: No model analysis available
    # Bypass model filtering entirely — use scanner ranking
    logger.warning("event=model_fully_degraded action=bypass_model_filter")
    for cand in selected:
        cand["model_degraded"] = True
        cand["model_recommendation"] = None
        cand["model_score"] = None
    # Rank by setup_quality (scanner score) instead of model_score
    final = sorted(selected, key=lambda c: -(c.get("setup_quality") or 0))
    final = final[:DEFAULT_TOP_N]  # Use Stage 5 cap (20) not Stage 7b cap (10)
    warnings.append("Model analysis unavailable — candidates ranked by scanner score only")
else:
    # NORMAL or PARTIAL: Existing behavior
    # Remove PASS + unanalyzed, rank by model_score, top 10
    ... (existing logic)
```

### Acceptance Criteria
- [ ] When ALL model calls fail → output contains candidates (not empty)
- [ ] Degraded candidates have `model_degraded: true` flag
- [ ] Degraded candidates have `model_recommendation: null` (not "BUY" or "PASS")
- [ ] Degraded ranking uses `setup_quality` DESC (scanner score)
- [ ] Degraded cap is DEFAULT_TOP_N (20) not MODEL_FILTER_TOP_N (10)
- [ ] Pipeline warning indicates degradation
- [ ] When model is partially available (some succeed, some fail) → existing behavior preserved
- [ ] When model is fully available → existing behavior preserved exactly
- [ ] Unit test: mock all model calls failing → verify output has candidates with model_degraded=true
- [ ] Unit test: mock partial model failure → verify existing filter/rank behavior

### Dependencies
None.

### Estimated Scope
Small-Medium: ~30-40 lines.

---

## FL-14: Calendar/Diagonal Separate Ranking Track

### Problem
Calendar and diagonal families have EV=None (correctly deferred). The EV-based sort coerces None to 0.0, placing all calendars at the bottom. With hundreds of positive-EV verticals above them, calendars never appear in the top-30.

### Files Involved
| File | Role |
|------|------|
| `app/workflows/options_opportunity_runner.py` L1013-1066 | Ranking and selection |
| `app/services/ranking.py` | Existing composite rank service (unused by V2) |

### Current Behavior
```python
# All families compete for same top-30 via EV sort:
credible.sort(key=lambda c: (-_safe_float(ev), -_safe_float(ror), symbol))
selected = credible[:30]
# Calendar EV=None → _safe_float(None)=0.0 → bottom of list
```

### Target Behavior
Split selection into family tracks with reserved slots:
```python
# Separate candidates by family
verticals_and_ic = [c for c in credible if c["family_key"] in ("vertical_spreads", "iron_condors")]
butterflies = [c for c in credible if c["family_key"] == "butterflies"]
calendars = [c for c in credible if c["family_key"] in ("calendars", "diagonals")]

# Rank each track by appropriate metric
verticals_and_ic.sort(key=lambda c: -_safe_float(ev))  # Or composite rank
butterflies.sort(key=lambda c: -_safe_float(ev))        # With binary-outcome caveat
calendars.sort(key=lambda c: _safe_float(net_debit) / max(_safe_float(width), 0.01))  # Debit/width ratio

# Reserved slots (configurable)
SLOTS = {"verticals_ic": 18, "butterflies": 6, "calendars": 6}
selected = (
    verticals_and_ic[:SLOTS["verticals_ic"]]
    + butterflies[:SLOTS["butterflies"]]
    + calendars[:SLOTS["calendars"]]
)
```

### Acceptance Criteria
- [ ] Calendar/diagonal candidates appear in final output
- [ ] Calendar candidates ranked by a meaningful metric (debit/width ratio, theta differential, or similar)
- [ ] Family distribution visible in output metadata (how many per family)
- [ ] No single family monopolizes all 30 slots
- [ ] Slot allocation is configurable (not hardcoded inline)
- [ ] When a family has fewer candidates than its slot allocation, unused slots go to other families
- [ ] Unit test: output includes at least 1 calendar candidate when calendars exist in pipeline

### Dependencies
FN-5 (ranking.py integration) should inform the approach for verticals/IC ranking.

### Estimated Scope
Medium: ~50-80 lines.

---

## FL-15: Position-Aware Scan Enrichment

### Problem
Neither pipeline checks existing open positions before scanning. The system recommends trades regardless of current portfolio exposure.

### Files Involved
| File | Role |
|------|------|
| `app/workflows/stock_opportunity_runner.py` Stage 5 | Stock enrichment |
| `app/workflows/options_opportunity_runner.py` Stage 4 | Options enrichment |
| `app/services/risk_policy_service.py` | Has position reading via `build_snapshot()` |
| `app/clients/tradier_client.py` | `get_positions()` — reads active positions |

### Current Behavior
Neither pipeline loads or checks current positions. Candidates are scored, ranked, and output with no awareness of existing portfolio.

### Target Behavior
During enrichment (stock Stage 5, options Stage 4):
1. Load active positions from Tradier via existing `get_positions()` method
2. Build a symbol-to-exposure map: `{symbol: {position_count, total_risk, direction}}`
3. For each candidate:
   - Check if symbol already has open positions
   - Compute `concentration_pct` = existing_risk / max_risk_per_underlying
   - Attach `existing_exposure` dict to candidate
   - If adding this trade would exceed concentration limit → set `concentration_warning: true`

```python
# Enrichment addition per candidate:
cand["existing_exposure"] = {
    "symbol_positions": positions_map.get(cand["symbol"], {}).get("count", 0),
    "symbol_risk_dollars": positions_map.get(cand["symbol"], {}).get("total_risk", 0),
    "concentration_pct": concentration_pct,
    "concentration_warning": concentration_pct > 0.80,  # 80% of limit
}
```

### Acceptance Criteria
- [ ] Candidates have `existing_exposure` field in output
- [ ] Candidates for symbols with existing positions show current exposure
- [ ] `concentration_warning` flags when adding would approach/exceed limits
- [ ] No candidates are rejected (informational for v1)
- [ ] Position fetch failure → empty existing_exposure (graceful degradation)
- [ ] Position data cached for the duration of the pipeline run (not re-fetched per candidate)
- [ ] Unit test: candidate for symbol with 2 existing positions → existing_exposure shows count=2

### Dependencies
None — position reading already exists via Tradier client.

### Estimated Scope
Medium: ~60-80 lines across both pipelines.

---

## FL-16: Expand Credibility Gate

### Problem
Three checks (penny premium, zero delta, fillable leg) catch worthless options but miss marginal trades — tiny widths, wide bid-ask spreads, low RoR, and illiquid contracts.

### Files Involved
| File | Role |
|------|------|
| `app/workflows/options_opportunity_runner.py` L960-1010 | Credibility gate |

### Current Behavior
```python
# 3 sequential checks with continue on first failure:
if max_premium < 0.05: reject("penny_premium"); continue
if pop >= 0.995: reject("zero_delta_short"); continue
if not has_fillable_leg: reject("all_legs_zero_bid"); continue
# Passes → enters ranking
```

### Target Behavior
```python
# Collect ALL applicable reasons (don't short-circuit):
reject_reasons = []

if max_premium < 0.10:                    # Raised from $0.05 to $0.10
    reject_reasons.append("penny_premium")

if pop is not None and pop >= 0.995:
    reject_reasons.append("zero_delta_short")

if not has_fillable_short_leg:            # Changed: require SHORT leg bid > 0
    reject_reasons.append("short_leg_zero_bid")

if width is not None and width < 1.0:     # NEW: minimum $1 width
    reject_reasons.append("narrow_width")

if ror is not None and ror < 0.03:        # NEW: minimum 3% RoR
    reject_reasons.append("low_ror")

# NEW: bid-ask spread sanity
if mid_premium > 0 and spread_pct > 0.50: # Spread > 50% of mid
    reject_reasons.append("wide_spread")

if reject_reasons:
    credibility_reasons_all.update(reject_reasons)
    continue  # Reject candidate
```

### Acceptance Criteria
- [ ] All 6 checks fire independently (not short-circuited)
- [ ] Diagnostic counts reflect ALL applicable reasons per candidate
- [ ] New rejection codes added to taxonomy: `narrow_width`, `low_ror`, `wide_spread`, `short_leg_zero_bid`
- [ ] MIN_PREMIUM raised from $0.05 to $0.10 (or made configurable)
- [ ] Fillability check targets SHORT legs specifically (not any leg)
- [ ] $0.50-wide spreads with $0.06 credit are caught by both `narrow_width` and `penny_premium`
- [ ] Valid trades with reasonable premiums, widths, and liquidity are unaffected
- [ ] Unit test: $0.50 width spread → rejected with "narrow_width"
- [ ] Unit test: 2% RoR trade → rejected with "low_ror"
- [ ] Unit test: spread with 60% bid-ask ratio → rejected with "wide_spread"

### Dependencies
None.

### Estimated Scope
Medium: ~40-60 lines (replace existing gate with expanded version).

---

## Cross-Reference: Finding → Fix Mapping

| Audit Finding | Fix ID | Priority |
|--------------|--------|----------|
| 3D-02 (event risk not gated) | FN-7 | Fix Now |
| 3D-03 (regime not used for filtering) | FN-8 | Fix Now |
| 3D-01 (risk policy not enforced) | FS-11 | Fix Soon |
| 3D-04 (validator not in submit path) | FS-12 | Fix Soon |
| 3A-04 (model failure zeros output) | FS-13 | Fix Soon |
| 3B-01 (calendar exclusion from ranking) | FL-14 | Fix Later |
| 3D-07 (no position awareness in scanning) | FL-15 | Fix Later |
| 3B-06 (credibility gate too thin) | FL-16 | Fix Later |

---

## Implementation Order (Recommended)

### Wave 1 (Independent — connect existing infrastructure)
Run in any order — each is self-contained:
- **FN-7** (event calendar soft gate) — connects existing EventCalendarContext
- **FN-8** (regime soft gate) — connects existing RegimeService output
- **FS-12** (validator in submit) — 10-15 lines defense-in-depth

### Wave 2 (After Wave 1)
- **FS-11** (risk policy in preview) — uses existing RiskPolicyService
- **FS-13** (model failure graceful degradation)

### Wave 3 (Independent hardening)
Run in any order after Waves 1-2:
- **FL-14** (calendar separate ranking track)
- **FL-15** (position-aware enrichment)
- **FL-16** (expanded credibility gate)

---

*End of Pass 3 Fix Specifications*
