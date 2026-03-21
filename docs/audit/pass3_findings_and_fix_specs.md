# BenTrade Foundation Audit — Pass 3 Findings Report
## Filtering & Selection Layer: Consolidated Analysis

**Date**: 2026-03-20
**Scope**: Both pipeline end-to-end flows, filter thresholds, decision policies, guardrails, and output contracts

---

## Executive Assessment

Your filtering and selection layer reveals the most significant gap in the entire system: **you've built sophisticated analysis infrastructure (event calendar, regime service, risk policy service with 23 parameters) that produces useful output but is wired to nothing**. The scanning, selection, and execution paths operate in complete isolation from these safety systems. This is the "rich infrastructure, zero enforcement" problem — the most impactful finding across all three audit passes.

The pipelines themselves are well-structured with good stage boundaries, comprehensive artifact emission, and traceable rejection codes. The V2 options pipeline in particular is production-grade in its validation discipline. But the quality of the final output is limited by what's missing: no regime-aware gating, no event risk blocking, no portfolio concentration awareness, and risk policy constants that are decorative.

---

## Severity 1: Critical Gaps (Infrastructure Exists But Not Enforced)

### G1. Risk Policy Service Is Decorative

23 risk policy constants are defined, user-adjustable via API, and compared against active positions to generate hard_limits and soft_gates arrays. The trading execution path (preview + submit) never calls RiskPolicyService. A user can submit an order that violates every policy limit and the system will not block it.

This is the single largest enforcement gap. The infrastructure investment is substantial — position aggregation, risk budgets, per-symbol concentration, cash reserve checks — all built and functioning. Just not wired into any decision gate.

### G2. Event Calendar Produces Warnings That Nobody Reads

EventCalendarContext computes event risk state (crowded/elevated/quiet), overlap with candidate DTE windows, and 8+ warning flags for FOMC, CPI, NFP, and earnings proximity. No consumer uses these to gate or adjust trade decisions. A 5-DTE put credit spread expiring through FOMC tomorrow has no system-level awareness of the event.

### G3. Regime Label Is Enrichment-Only

RegimeService classifies the market as RISK_ON / NEUTRAL / RISK_OFF with a playbook suggesting strategy types per regime. Neither the stock nor options pipeline uses this label for filtering, threshold adjustment, or strategy blocking. The system will scan and rank premium-selling strategies at full aggression during RISK_OFF conditions.

---

## Severity 2: Pipeline-Level Issues

### P1. Options Pipeline Has No Model Analysis (Asymmetry with Stock Pipeline)

The stock pipeline has 8 stages including LLM review with BUY/PASS and model_score ranking. The options pipeline has 5 stages with pure quantitative selection. Options candidates are ranked by raw EV with no qualitative review of regime appropriateness, risk/reward judgment, or trade thesis assessment.

### P2. Calendar/Diagonal Strategies Are Invisible in Output

Calendar and diagonal families have EV=None (correctly deferred). The EV-based sort coerces None to 0.0, placing all calendars at the bottom of the ranking. With hundreds of positive-EV verticals and condors above them, calendars never appear in the top-30. An entire strategy family is functionally dead in the output despite being a core part of the options philosophy.

### P3. Model Failure Zeroes Stock Output

When the LLM service is down, Stage 7b removes all candidates without model analysis, producing 0 final output. A Tradier-validated, high-quality-scored candidate (setup_quality=85) is lost because an unrelated service was temporarily unavailable. The pipeline should degrade more gracefully.

### P4. Pullback Swing Floods the Pipeline

Pullback Swing has no strategy-specific filters, so all ~196 universe symbols enter scoring. This creates volume imbalance (70-80% of raw candidates are pullback swing), dominates the dedup stage, and forces reliance on MIN_SETUP_QUALITY=30 as the de facto filter. Other scanners with proper filter chains produce 5-40 targeted candidates each.

### P5. ExecutionValidator Not in Submit Path

`validate_trade_for_execution()` is only called from the frontend-facing validate endpoint. The actual `TradingService.submit()` does not run this validator. If the frontend is bypassed (direct API call), a malformed trade could reach the broker.

---

## Severity 3: Filtering Quality Issues

### Q1. Credibility Gate Is Too Thin

Three checks (penny premium, zero delta, fillable leg) catch worthless options but miss low-quality trades: $0.50-wide spreads with $0.06 credit, trades with 50% bid-ask spread relative to mid, 2% RoR trades, and contracts with 5 open interest. The gate catches garbage but lets marginal trades through.

### Q2. All Enforced Thresholds Are Static

Every threshold that actually blocks a trade is hardcoded. The only runtime-adjustable values are in RiskPolicyService, which isn't enforced. Users cannot tune scanner aggressiveness or credibility gate strictness without code changes. Strict/Balanced/Wide presets are documented but not implemented for stock scanners.

### Q3. No Position-Aware Scanning

Neither pipeline checks existing open positions before scanning. The system will recommend a 3rd credit spread on SPY when the user already has 2 open SPY positions consuming their symbol risk budget.

---

## What's Working Well

1. **Pipeline stage architecture**: Both pipelines have clear stage boundaries with comprehensive artifact emission. Every drop point has explicit counts and reason codes. Excellent traceability.

2. **V2 scanner 6-phase validation**: The options pipeline's structural validation, quote hygiene, trust hygiene, and math verification chain is production-grade. Immutable rejection codes provide complete audit trails.

3. **Execution path guardrails**: Preview risk checks, confirmation tokens, freshness gates, development mode safety, and idempotency handling are well-implemented for order correctness.

4. **Output contract alignment**: Stock pipeline output matches frontend expectations with zero mismatches across 26+ fields. Options pipeline has one minor fallback-order issue (frontend tries `underlying` before `symbol`) but no functional breaks.

5. **Graceful degradation patterns**: Both pipelines handle partial failures well — individual scanner failures don't abort the run, market state degradation proceeds with warnings, and Stage 8 always executes to produce output.

---

# Pass 3 Fix Specifications

*IDs continue from Pass 2 (FN-4 through FN-6, FS-6 through FS-10, FL-8 through FL-13)*

---

## FN-7: Wire Event Calendar Into Options Pipeline Gating

### Problem
EventCalendarContext produces event risk warnings but nothing acts on them. Options candidates can land through FOMC/CPI with no awareness.

### Files Involved
| File | Role |
|------|------|
| `app/services/event_calendar_context.py` | Produces risk state, overlap, warnings |
| `app/workflows/options_opportunity_runner.py` L918-1040 | Stage 4 (enrich_evaluate) — where gating should happen |

### Target Behavior
In Stage 4, after enrichment and before credibility gate:
1. Load event calendar context for each candidate's DTE window
2. If event_risk_state is "crowded" (high-impact event within 24h of expiration), flag candidate with `event_risk: "high"`
3. For now: flag only (don't reject) — add the flag to output so the UI can display it
4. Later: reject or penalize candidates with high-impact events within DTE window

### Acceptance Criteria
- [ ] Event calendar context loaded during Stage 4
- [ ] Each candidate has an `event_risk` field (high/elevated/quiet/unknown)
- [ ] Candidates with FOMC/CPI within DTE window are flagged
- [ ] No candidates are rejected (soft gate for v1)
- [ ] Output includes event_risk in the compact candidate shape
- [ ] Unit test: candidate with 7 DTE and FOMC in 5 days → event_risk="elevated"

### Dependencies
None (EventCalendarContext already exists and is functional).

### Estimated Scope
Medium: ~60-80 lines to integrate event calendar into Stage 4.

---

## FN-8: Wire Regime Into Pipeline as Soft Gate

### Problem
Regime label (RISK_ON/NEUTRAL/RISK_OFF) is computed and attached to candidates but never used for filtering or adjustment.

### Files Involved
| File | Role |
|------|------|
| `app/workflows/stock_opportunity_runner.py` Stage 5 | Stock enrichment |
| `app/workflows/options_opportunity_runner.py` Stage 4 | Options enrichment |

### Target Behavior
Phase 1 (soft gate — flag only):
1. When regime is RISK_OFF, add `regime_warning: "risk_off_environment"` to premium-selling candidates
2. When regime is RISK_ON, add `regime_warning: "risk_on_environment"` to bearish/protective candidates
3. Flag in output, don't reject

Phase 2 (hard gate — later):
1. RISK_OFF + premium-selling → require min_pop 70% (vs pipeline default)
2. RISK_OFF + momentum breakout → suppress from stock pipeline
3. Regime-based score adjustment (-5 to -10 points for misaligned strategies)

### Acceptance Criteria
- [ ] Candidates have `regime_alignment` field (aligned/neutral/misaligned)
- [ ] RISK_OFF + put_credit_spread → regime_alignment="misaligned"
- [ ] RISK_ON + call_debit → regime_alignment="aligned"
- [ ] No candidates are rejected in Phase 1 (soft gate)
- [ ] Unit test: verify regime_alignment is correct for each strategy×regime combination

### Dependencies
None.

### Estimated Scope
Small-Medium: ~40-60 lines for soft gate.

---

## FS-11: Wire RiskPolicyService Into Preview Flow

### Problem
23 risk policy constants defined, compared against positions, but never enforced in the execution path.

### Files Involved
| File | Role |
|------|------|
| `app/services/risk_policy_service.py` | Produces hard_limits/soft_gates |
| `app/trading/service.py` L399-430 | Preview risk checks — should call RiskPolicyService |

### Target Behavior
In the preview flow, after existing risk checks (width, max_loss, credit floor, bid/ask):
1. Call `risk_policy_service.build_snapshot()` with the proposed trade
2. If any `hard_limits` are triggered, add them to the preview response as `policy_warnings`
3. Phase 1: warnings only (don't block)
4. Phase 2: block preview when critical hard_limits are violated (e.g., max_total_risk exceeded)

### Acceptance Criteria
- [ ] Preview response includes `policy_warnings` array
- [ ] Hard limit violations appear in policy_warnings with clear descriptions
- [ ] Preview is not blocked in Phase 1 (warning only)
- [ ] Frontend can display policy_warnings to the user
- [ ] Unit test: propose trade that exceeds max_risk_per_trade → policy_warning present

### Dependencies
None (RiskPolicyService already exists and functions).

### Estimated Scope
Medium: ~50-70 lines to integrate into preview flow.

---

## FS-12: Add ExecutionValidator to Submit Path

### Problem
`validate_trade_for_execution()` only runs from frontend-facing endpoints, not from `TradingService.submit()`.

### Files Involved
| File | Role |
|------|------|
| `app/trading/execution_validator.py` L134-213 | The validator |
| `app/trading/service.py` | submit() — should call validator |

### Target Behavior
Call `validate_trade_for_execution()` inside `TradingService.submit()` before sending to broker. If validation fails, return 400 with details.

### Acceptance Criteria
- [ ] submit() calls validate_trade_for_execution() before broker API call
- [ ] Validation failure returns HTTP 400 with structured error
- [ ] No change to existing frontend flow (frontend still calls /validate separately)
- [ ] Unit test: submit malformed trade directly → 400 with validation details

### Dependencies
None.

### Estimated Scope
Small: ~10-15 lines.

---

## FS-13: Graceful Degradation When Model Analysis Fails

### Problem
When LLM service is down, Stage 7b removes all candidates (no model analysis = removed), producing 0 output.

### Files Involved
| File | Role |
|------|------|
| `app/workflows/stock_opportunity_runner.py` L1406-1490 | Stage 7b filter logic |

### Target Behavior
When Stage 7 is fully degraded (all model calls failed):
1. Skip Stage 7b filtering entirely
2. Pass candidates through from Stage 5 with `model_review: null` and `model_degraded: true`
3. Apply the Stage 5 ranking (setup_quality) as final ranking instead of model_score
4. Cap at DEFAULT_TOP_N (20) instead of MODEL_FILTER_TOP_N (10)
5. Add pipeline warning: "Model analysis unavailable — candidates ranked by scanner score only"

### Acceptance Criteria
- [ ] When model is fully down, output contains candidates (not empty)
- [ ] Candidates have `model_degraded: true` flag
- [ ] Ranking uses setup_quality when model_score is unavailable
- [ ] Pipeline warns about degradation
- [ ] When model is partially down, existing behavior preserved (mixed analyzed + unanalyzed)

### Dependencies
None.

### Estimated Scope
Small-Medium: ~30-40 lines.

---

## FL-14: Calendar/Diagonal Separate Ranking Track

### Problem
Calendars with EV=None are invisible in unified EV ranking.

### Files Involved
| File | Role |
|------|------|
| `app/workflows/options_opportunity_runner.py` L1013-1066 | Ranking and selection |

### Target Behavior
Split selection into family tracks:
- Verticals + IC: top 20 by EV (or composite rank from ranking.py)
- Butterflies: top 5 by EV (with binary-outcome caveat)
- Calendars/Diagonals: top 5 by net_debit/width ratio or theta differential

Alternative: reserve slots per family in the top-30 (e.g., 18 verticals/IC + 6 butterflies + 6 calendars).

### Acceptance Criteria
- [ ] Calendar/diagonal candidates appear in final output
- [ ] Calendar candidates ranked by a meaningful metric (not EV=0)
- [ ] Family distribution is visible in output metadata
- [ ] No family monopolizes all 30 slots

### Dependencies
None, but FN-5 (ranking.py integration) should inform the approach.

### Estimated Scope
Medium: ~50-80 lines.

---

## FL-15: Position-Aware Scan Enrichment

### Problem
Neither pipeline checks existing open positions.

### Files Involved
| File | Role |
|------|------|
| `app/workflows/stock_opportunity_runner.py` Stage 5 | Stock enrichment |
| `app/workflows/options_opportunity_runner.py` Stage 4 | Options enrichment |
| `app/services/risk_policy_service.py` | Has position reading capability |

### Target Behavior
During enrichment:
1. Load active positions from Tradier (already available via RiskPolicyService)
2. For each candidate, check if the symbol already has open positions
3. Add `existing_exposure` field: `{symbol_positions: N, symbol_risk: $X, concentration_pct: Y%}`
4. Flag candidates that would exceed concentration limits: `concentration_warning: true`

### Acceptance Criteria
- [ ] Candidates have `existing_exposure` field
- [ ] Candidates for symbols with existing positions show current exposure
- [ ] Concentration warnings flag when adding would exceed limits
- [ ] No candidates are rejected (informational for v1)

### Dependencies
None (position reading already exists).

### Estimated Scope
Medium: ~60-80 lines.

---

## FL-16: Expand Credibility Gate

### Problem
Three checks catch garbage but miss marginal trades.

### Files Involved
| File | Role |
|------|------|
| `app/workflows/options_opportunity_runner.py` L960-1010 | Credibility gate |

### Target Behavior
Add additional checks:
1. `min_width >= $1.00` for index ETFs → reject code `v2_narrow_width`
2. `bid_ask_spread_ratio < 0.50` (spread < 50% of mid) → reject code `v2_wide_spread`
3. `min_ror >= 0.03` (3% minimum return on risk) → reject code `v2_low_ror`
4. Short leg bid > 0 for credit strategies → reject code `v2_short_leg_zero_bid`
5. Collect ALL applicable reasons per candidate (don't `continue` after first failure)

### Acceptance Criteria
- [ ] New rejection codes added to taxonomy
- [ ] Each check fires independently (not short-circuited)
- [ ] Diagnostic counts reflect all applicable reasons per candidate
- [ ] Marginal trades (tiny width, wide spread, low RoR) are caught
- [ ] Valid trades unaffected

### Dependencies
None.

### Estimated Scope
Medium: ~40-60 lines.

---

## Cross-Reference: Finding → Fix Mapping

| Audit Finding | Fix ID | Priority |
|--------------|--------|----------|
| 3D-01 (risk policy not enforced) | FS-11 | Fix Soon |
| 3D-02 (event risk not gated) | FN-7 | Fix Now |
| 3D-03 (regime not used for filtering) | FN-8 | Fix Now |
| 3D-04 (validator not in submit) | FS-12 | Fix Soon |
| 3A-04 (model failure zeros output) | FS-13 | Fix Soon |
| 3B-01 (calendar exclusion) | FL-14 | Fix Later |
| 3D-07 (no position awareness) | FL-15 | Fix Later |
| 3B-06 (credibility gate thin) | FL-16 | Fix Later |
| 3A-01, FS-10 from Pass 2 (pullback swing no filters) | FS-10 | Fix Soon (already spec'd) |
| 3B-02 (no options model analysis) | Future workstream | Beyond current audit |

---

## Implementation Order

### Wave 1 (Independent — highest impact)
- **FN-7** (event calendar soft gate) — connect existing infrastructure
- **FN-8** (regime soft gate) — connect existing infrastructure
- **FS-12** (validator in submit path) — 10-15 lines, defense-in-depth

### Wave 2 (After Wave 1)
- **FS-11** (risk policy in preview) — uses existing RiskPolicyService
- **FS-13** (model failure graceful degradation)

### Wave 3 (Hardening)
- **FL-14** (calendar separate ranking)
- **FL-15** (position-aware enrichment)
- **FL-16** (expanded credibility gate)

---

*End of Pass 3 Findings Report & Fix Specifications*
