# Audit 4D — Active Trade Reassessment Prompt Review

**Scope**: Active trade reassessment LLM call — evaluates whether open positions should HOLD, REDUCE, CLOSE, or be flagged for URGENT_REVIEW  
**Date**: 2025-07-18  
**Status**: Complete  

---

## 1  Files Examined

| File | Role |
|------|------|
| `app/services/active_trade_pipeline.py` L67-1000 | Pipeline stages, engine scoring, system prompt, model call, normalization |
| `app/api/routes_active_trades.py` L1016-1210 | Second model endpoint (`/active/model-analysis`) with different prompt |
| `app/services/active_trade_pipeline.py` L152-296 | `build_reassessment_packet()` — single packet builder for engine + model |
| `app/services/active_trade_pipeline.py` L300-525 | `run_analysis_engine()` — deterministic scoring engine |
| `app/services/active_trade_pipeline.py` L786-870 | `run_model_analysis()` — LLM call and output parsing |
| `app/services/active_trade_pipeline.py` L880-1000 | `normalize_recommendation()` — merges engine + model |

---

## 2  System Prompt Analysis

### Complete System Prompt (`_ACTIVE_TRADE_SYSTEM_PROMPT`)

```
You are BenTrade's active trade reassessment engine.
You will receive a structured reassessment packet for an open options position.

The packet contains:
- Trade identity (symbol, strategy, strikes, expiration, DTE)
- Position state (P&L, entry vs current price)
- Market context (regime, VIX, indicators)
- Existing monitor evaluation (score, triggers, recommended action)
- Internal engine metrics (trade health score, risk flags, component scores)

Analyse the position and return ONLY valid JSON (no markdown, no commentary)
with exactly these keys:
{
  "recommendation": "HOLD" | "REDUCE" | "CLOSE" | "URGENT_REVIEW",
  "conviction": <float 0.0 to 1.0>,
  "rationale_summary": "<2-4 sentence summary explaining why>",
  "key_supporting_points": ["<point1>", "<point2>", ...],
  "key_risks": ["<risk1>", "<risk2>", ...],
  "market_alignment": "<how current market conditions affect this position>",
  "portfolio_fit": "<whether this position still makes sense in context>",
  "event_sensitivity": "high" | "moderate" | "low" | "none",
  "suggested_next_move": "<specific actionable guidance>"
}

Rules:
- recommendation must be one of: HOLD, REDUCE, CLOSE, URGENT_REVIEW
- conviction must honestly reflect your certainty (0.0 = no confidence, 1.0 = maximum)
- rationale_summary should explain the WHY, not just restate the recommendation
- key_supporting_points: 2-5 concrete reasons supporting the recommendation
- key_risks: 1-4 specific risks to the position
- suggested_next_move: a practical, actionable step the trader should consider
- If data is limited, say so explicitly rather than guessing
- Do NOT wrap your response in markdown code fences
```

### Role Assessment

**Assigned role**: "BenTrade's active trade reassessment engine" — functional, not persona-based. Less vivid than the TMC's "disciplined portfolio manager," but adequate.

**Options-aware**: Yes — the prompt explicitly mentions "open options position" with "strikes, expiration, DTE." This is the only prompt in the system that explicitly acknowledges options positions.

### What's Missing from the System Prompt

- **No decision framework** — no evaluation order (e.g., "check P&L first, then regime, then time pressure"). The TMC prompt has a 5-step framework; this prompt has none.
- **No threshold guidance** — what conviction level should trigger CLOSE vs REDUCE? TMC says "conviction < 60 → PASS"; this prompt gives no equivalent.
- **No URGENT_REVIEW criteria** — when should the model escalate to URGENT_REVIEW? The prompt lists it as an option but provides no trigger guidance.
- **No "Do NOT invent catalysts" instruction** — this constraint exists only in the separate `/active/model-analysis` route prompt (see Finding F-4D-05).
- **No hallucination guards** — the TMC prompt says "Use ONLY the provided metrics and engine data — do NOT hallucinate." This prompt has no equivalent.
- **No anti-anchoring** — the engine's recommendation, score, and risk flags are sent directly.

---

## 3  Input Data Assembly

### Packet Structure (`build_reassessment_packet()`)

| Section | Fields | Source |
|---------|--------|--------|
| **identity** | trade_key, trade_id, symbol, strategy, strategy_id, spread_type, short_strike, long_strike, expiration, dte, quantity, legs, trade_status | Active trade record |
| **position** | avg_open_price, mark_price, unrealized_pnl, unrealized_pnl_pct, cost_basis_total, market_value, day_change, day_change_pct | Active trade record |
| **market** | regime_label, regime_score, vix | `regime_service` via market_context stage |
| **indicators** | sma20, sma50, rsi14 | `_fetch_indicators()` per symbol |
| **monitor** | status (HOLD/WATCH/REDUCE/CLOSE), score_0_100, breakdown, triggers, recommended_action | `ActiveTradeMonitorService.evaluate_batch()` |
| **data_quality** | degraded_fields, is_degraded, degraded_count | Computed during packet assembly |

**Engine output appended to prompt** (via `_render_reassessment_prompt()`):

| Field | Description |
|-------|-------------|
| `trade_health_score` | 0-100 weighted composite |
| `component_scores` | Per-component breakdown (pnl_health, time_pressure, market_alignment, structure_health, monitor_alignment, event_risk) |
| `risk_flags` | List of risk conditions (SIGNIFICANT_LOSS, EXPIRY_NEAR, REGIME_ADVERSE, etc.) |
| `engine_recommendation` | HOLD/REDUCE/CLOSE/URGENT_REVIEW from deterministic thresholds |
| `urgency` | 1-5 review priority |

### What's Included vs Excluded

**Included (good)**:
- Complete trade structure (strikes, legs, spread_type)
- P&L metrics (both absolute and percentage)
- Market regime and VIX
- Monitor evaluation with triggers and breakdown
- Engine's full output including health score and risk flags
- Data quality tracking

**Excluded (missing — see Section 4)**:
- No original trade thesis
- No stop loss / target levels
- No ATR / historical volatility context
- No strategy historical performance
- No event calendar (earnings, FOMC dates)
- No portfolio context (other positions, correlation)
- No entry date / days held calculation
- No Greeks (delta, theta, gamma, vega)

---

## 4  What's Missing from Input

| Missing Data | Impact | Cross-reference |
|-------------|--------|-----------------|
| **Original trade thesis** | Model cannot assess whether the thesis is still valid — the core question for any position review | Architecture doc flagged this |
| **Stop loss / target levels** | Model asked to evaluate P&L but doesn't know where the planned exit levels are | Same gap as TMC (F-4C-02) |
| **ATR / historical volatility** | Model cannot assess whether current drawdown is normal for this stock — a -3% move in AAPL vs a -3% move in MARA mean different things | Pass 1 findings |
| **Strategy historical performance** | Model cannot compare this trade's trajectory against typical outcomes for similar setups | Not available in system |
| **Event proximity** | No earnings dates, FOMC dates, or ex-div dates; model asked for `event_sensitivity` but has no event data to reference | Pass 3: event calendar wired to nothing |
| **Portfolio context** | No other positions, correlation, or total exposure; model asked for `portfolio_fit` but has no portfolio data | Same gap as TMC (F-4C-01) |
| **Entry date / days held** | Model sees DTE but not when the trade was opened or how long it's been held | Trade record may have this but packet doesn't include it |
| **Greeks** | No delta, theta, gamma, vega — critical for options position management; theta especially relevant for time decay assessment | Options-specific gap |

### Finding F-4D-01 (H): Model Asked for Fields It Cannot Compute

The output schema asks for:
- `portfolio_fit`: "whether this position still makes sense in context" — **no portfolio data in input**
- `event_sensitivity`: "high/moderate/low/none" — **no event calendar data in input**

These fields require data the model does not receive. The model must fabricate or provide generic responses.

---

## 5  Output Schema

### Pipeline Prompt (`_ACTIVE_TRADE_SYSTEM_PROMPT`)

| Field | Type | Purpose |
|-------|------|---------|
| `recommendation` | HOLD\|REDUCE\|CLOSE\|URGENT_REVIEW | Position action |
| `conviction` | float 0.0-1.0 | Self-assessed certainty |
| `rationale_summary` | string | 2-4 sentence explanation |
| `key_supporting_points` | array of strings | 2-5 supporting reasons |
| `key_risks` | array of strings | 1-4 risk descriptions |
| `market_alignment` | string | Market conditions impact |
| `portfolio_fit` | string | Portfolio context assessment |
| `event_sensitivity` | high\|moderate\|low\|none | Event proximity rating |
| `suggested_next_move` | string | Actionable guidance |

### Output Coercion (in `run_model_analysis()`)

Minimal validation:
- `recommendation` → uppercased, validated against `VALID_RECOMMENDATIONS`, set to `None` if invalid (not defaulted)
- `conviction` → clamped to [0.0, 1.0]
- All other fields → passed through without validation

**No fallback recommendation**: Unlike the TMC prompt (which defaults to PASS), an invalid recommendation becomes `None`, falling through to the engine's recommendation in `normalize_recommendation()`.

---

## 6  Two Separate Model Endpoints — Finding F-4D-05 (H)

There are **two completely different LLM prompts** for active trade analysis:

| Dimension | Pipeline Prompt (`active_trade_pipeline.py`) | On-Demand Prompt (`routes_active_trades.py`) |
|-----------|----------------------------------------------|----------------------------------------------|
| **Endpoint** | `/api/trading/active/reassess` (batch) | `/api/trading/active/model-analysis` (single) |
| **System prompt** | `_ACTIVE_TRADE_SYSTEM_PROMPT` | `_MODEL_ANALYSIS_SYSTEM_MSG` |
| **Role** | "reassessment engine" | "senior portfolio and risk analyst" |
| **Decisions** | HOLD/REDUCE/CLOSE/URGENT_REVIEW | HOLD/REDUCE/EXIT/ADD/WATCH |
| **Conviction** | 0.0-1.0 float | 0-100 integer |
| **Sees engine output?** | Yes — full engine scores/recommendation | No — raw position + market only |
| **"Don't invent" guard** | Absent | Present |
| **Output schema** | 9 fields (simple) | 12 fields (richer: headline, thesis_status, technical_state, action_plan, memo) |
| **max_tokens** | 1200 | Not visible in prompt constant |
| **temperature** | 0.0 | 0.0 |

### Key Problems

1. **Different decision labels**: Pipeline uses CLOSE, on-demand uses EXIT. Pipeline has URGENT_REVIEW, on-demand has ADD and WATCH instead. These are not translatable.
2. **Different conviction scales**: Pipeline uses 0.0-1.0, on-demand uses 0-100. Same concept, different ranges.
3. **Anti-anchoring divergence**: Pipeline sends the engine's recommendation/score/risk_flags directly. On-demand sends "ONLY raw position + raw market context" — explicitly withholds engine output.
4. **Hallucination guard divergence**: On-demand says "Do NOT invent catalysts, fundamentals, or news." Pipeline has no equivalent instruction.
5. **Schema incompatibility**: On-demand has `thesis_status` (INTACT/WEAKENING/BROKEN) and `action_plan` with `risk_trigger`/`upside_trigger` — genuinely useful fields absent from the pipeline prompt.

---

## 7  Decision Quality

### Criteria Differentiation

**Engine (deterministic)**:
- `trade_health_score >= 70` → HOLD
- `trade_health_score >= 45` → REDUCE
- `trade_health_score >= 25` → CLOSE
- `trade_health_score < 25` → URGENT_REVIEW
- Override: 2+ critical risk flags → URGENT_REVIEW

**Model**: No equivalent thresholds. The system prompt says:
- "recommendation must be one of: HOLD, REDUCE, CLOSE, URGENT_REVIEW"
- "conviction must honestly reflect your certainty"

### Finding F-4D-02 (M): No Decision Criteria for Model

The model has no guidance on:
- When to recommend REDUCE vs CLOSE (e.g., at what P&L, what DTE threshold)
- What conviction level should map to each recommendation
- When URGENT_REVIEW should trigger (the engine has a clear rule; the model does not)
- Whether a losing position with 30 DTE should be treated differently than one with 3 DTE

The engine has clear, documented thresholds. The model has none. This means the model's decision is uncalibrated.

### Time Decay / Options vs Stocks

**Finding F-4D-03 (M): Options-aware but not theta-aware**

The system prompt says "open options position" — acknowledging options. The engine includes a `time_pressure` component (DTE-based). However:
- No theta decay rate in the input data
- No Greeks at all (delta, gamma, vega)
- The prompt does not differentiate between stock positions and options positions beyond the identity fields
- Time decay behavior differs dramatically between long options (theta enemy) and credit spreads (theta friend) — no guidance on this distinction

---

## 8  "Do NOT Invent Catalysts"

### Finding F-4D-04 (M): Instruction Exists but in Wrong Prompt

- **Pipeline prompt** (`_ACTIVE_TRADE_SYSTEM_PROMPT`): **ABSENT** — no hallucination guard
- **On-demand prompt** (`_MODEL_ANALYSIS_SYSTEM_MSG`): **PRESENT** — "Do NOT invent catalysts, fundamentals, or news."

The pipeline prompt — which runs automatically on all active trades — lacks the hallucination guard. The on-demand prompt, used for individual ad-hoc analysis, has it. This is backwards: the automated batch process should have stronger guardrails, not weaker ones.

The pipeline prompt does have: "If data is limited, say so explicitly rather than guessing" — a weak alternative that doesn't specifically prohibit catalyst fabrication.

---

## 9  Monitor → Model Relationship

### Execution Order

```
Stage 3: build_packets   → Monitor evaluates trades (via ActiveTradeMonitorService)
Stage 4: engine_analysis  → Engine sees monitor result in packet
Stage 5: model_analysis   → Model sees monitor result AND engine output in packet
Stage 6: normalize        → Merges engine + model into final recommendation
```

### Does the Model See the Monitor's Recommendation?

**Yes.** The prompt payload includes:

```json
"existing_monitor": {
  "status": "WATCH",
  "score_0_100": 65,
  "breakdown": { "regime_alignment": 0.7, "trend_strength": 0.5, ... },
  "triggers": [{ "id": "pnl_threshold", "level": "WARNING", "hit": true, ... }],
  "recommended_action": { "action": "WATCH", "reason_short": "P&L within range" }
}
```

Plus the engine's output:

```json
"internal_engine_output": {
  "trade_health_score": 62,
  "component_scores": { "pnl_health": 70, "time_pressure": 50, ... },
  "risk_flags": ["EXPIRY_NEAR"],
  "engine_recommendation": "REDUCE",
  "urgency": 3
}
```

### Can the Model Override the Monitor/Engine?

**Yes.** `normalize_recommendation()` resolution priority:

1. **Model recommendation** (if `model_available` and valid) → used
2. **Engine recommendation** (fallback) → used if model unavailable/invalid
3. **Default HOLD** → last resort

The model can recommend CLOSE when the engine says HOLD. There is **no conflict resolution** — model simply wins when available. This is documented as intentional.

### Finding F-4D-06 (M): Model Anchored to Engine + Monitor

The model sees:
- Monitor's status/score/recommended_action
- Engine's trade_health_score, recommendation, urgency, risk_flags, component_scores

This is an extreme anchoring setup — the model receives TWO prior assessments (monitor + engine) before forming its "independent" opinion. Unlike the TMC prompt where only the engine score anchors, here both a deterministic monitor AND a deterministic engine provide their conclusions.

---

## 10  Value-Add Assessment

### Output Field Classification

| Field | Classification | Rationale |
|-------|---------------|-----------|
| `recommendation` | **Uncertain value** | Model sees both monitor and engine recommendations; likely rubber-stamps the consensus |
| `conviction` | **Uncertain value** | Self-assessed, no calibration, no threshold enforcement |
| `rationale_summary` | **Genuinely new** | Narrative synthesis the engine cannot produce |
| `key_supporting_points` | **Genuinely new** | Prioritized factor list from model reasoning |
| `key_risks` | **Mixed** | Engine already has `risk_flags`; model may restate or add nuance |
| `market_alignment` | **Uncertain value** | Model receives regime_label + VIX but no MI engine outputs — limited data for this assessment |
| `portfolio_fit` | **Fabricated** | No portfolio data in input — model cannot assess this honestly |
| `event_sensitivity` | **Fabricated** | No event calendar data in input — model must guess |
| `suggested_next_move` | **Genuinely new** | Actionable guidance the engine cannot produce |

**Summary**: Of 9 output fields:
- 3 genuinely new (rationale_summary, key_supporting_points, suggested_next_move)
- 2 fabricated (portfolio_fit, event_sensitivity — asked without data)
- 1 mixed (key_risks)
- 3 uncertain (recommendation, conviction, market_alignment)

### What Would You Lose Without the Model?

**Lost**:
- Narrative rationale for the recommendation
- Prioritized supporting points and specific risk descriptions
- Actionable next-step guidance

**Not lost**:
- The recommendation itself — engine produces HOLD/REDUCE/CLOSE/URGENT_REVIEW with documented thresholds
- Risk flags — engine produces these deterministically
- Trade health score — engine's weighted composite is inspectable and reproducible

**Assessment**: The engine provides the critical decision signal with documented, reproducible thresholds. The model adds narrative explanation and actionable guidance but its recommendation is likely anchored to the engine/monitor consensus. The model's highest-value contribution is translating structured data into human-readable analysis — but this could arguably be done with templates rather than LLM calls.

---

## 11  Findings Summary

| ID | Sev | Finding |
|----|-----|---------|
| F-4D-01 | H | Model asked for `portfolio_fit` and `event_sensitivity` but receives no portfolio or event calendar data — must fabricate |
| F-4D-05 | H | Two completely separate LLM prompts for active trade analysis with different decision labels (CLOSE vs EXIT), different conviction scales (0-1 vs 0-100), different output schemas, and different anchoring behavior |
| F-4D-02 | M | No decision criteria in model prompt — no thresholds for when to REDUCE vs CLOSE vs URGENT_REVIEW; engine has clear thresholds, model has none |
| F-4D-03 | M | Options-aware but not theta-aware — no Greeks in input data; no guidance on credit spread time-decay benefit vs long option time-decay cost |
| F-4D-04 | M | "Do NOT invent catalysts" present in on-demand prompt but absent from pipeline prompt — automated batch process has weaker guardrails than ad-hoc endpoint |
| F-4D-06 | M | Model anchored to BOTH monitor and engine — receives two prior recommendations + scores before forming "independent" opinion |
| F-4D-07 | L | Conviction (0.0-1.0) has no behavioral consequence — model can recommend CLOSE with conviction 0.1 and it flows through unchanged |
| F-4D-08 | L | Invalid model recommendation → None (not defaulted) → falls through to engine recommendation; this is actually correct behavior but undocumented |
| F-4D-09 | L | No entry date / days held in input — model cannot assess trade duration against strategy expected hold period |
| F-4D-10 | L | Market context limited to regime_label + regime_score + VIX — no MI engine outputs (unlike TMC prompt which gets all 6 engines) |

**Severity distribution**: 2 High, 4 Medium, 4 Low

---

## 12  Cross-Prompt Patterns (4A-4D)

1. **Anti-anchoring absent across all prompts.** The active trade prompt is the most extreme case — the model receives both the deterministic monitor's recommendation AND the deterministic engine's recommendation, health score, component scores, and risk flags. Three layers of prior assessment before the model forms its opinion.

2. **Schema-asks-without-data is systemic.** TMC asks for risk_reward_verdict without stop/target data. Active trade asks for portfolio_fit and event_sensitivity without portfolio or event data. Every prompt has at least one output field that requires data the model doesn't receive.

3. **Two-system redundancy is now a three-system pattern.** Stock trades: strategy prompt + TMC prompt. Active trades: pipeline prompt + on-demand prompt. In both cases, the two systems use different decision labels, different output schemas, and different anchoring strategies.

4. **Conviction/confidence is decorative everywhere.** Strategy prompt: no threshold. TMC: threshold stated, not enforced. Active trade (pipeline): 0.0-1.0 with no threshold. Active trade (on-demand): 0-100 with no threshold. No prompt layer gates decisions on confidence.

5. **Hallucination guards are inconsistent.** TMC: "Use ONLY the provided metrics." Active pipeline: nothing. Active on-demand: "Do NOT invent catalysts." Strategy prompts: "Use ONLY the provided metrics and engine data." The automated batch processes should have at least the same guardrails as on-demand endpoints.

---

## 13  Recommendations

1. **Unify the two active trade prompts** into a single system that serves both batch and on-demand use cases. The on-demand prompt's richer schema (thesis_status, action_plan, risk_trigger) is superior and should replace the pipeline prompt's simpler schema.

2. **Remove `portfolio_fit` and `event_sensitivity` from the output schema** until portfolio and event calendar data are actually included in the input. Asking for fabricated fields undermines model output trust.

3. **Add decision threshold guidance** to the model prompt (e.g., "CLOSE if conviction > 0.7 that the trade should be exited; URGENT_REVIEW if conditions are deteriorating rapidly and manual attention is needed").

4. **Add "Do NOT invent catalysts"** to the pipeline system prompt.

5. **Include Greeks** (at minimum theta) in the reassessment packet so the model can assess time-decay risk for options positions.

6. **Withhold engine recommendation from model input** to reduce anchoring — let the model see the raw packet metrics but not the engine's conclusion. The engine's recommendation is used as fallback anyway.
