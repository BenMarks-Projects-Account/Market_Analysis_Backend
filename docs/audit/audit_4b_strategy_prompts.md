# Audit 4B — Strategy Prompt Review (Per-Setup Scoring)

**Scope**: All 4 stock strategy LLM prompt builders in `stock_strategy_prompts.py`  
**Date**: 2025-07-18  
**Status**: Complete  

---

## 1  Files Examined

| File | Role |
|------|------|
| `common/stock_strategy_prompts.py` | Shared system prompt + 4 per-strategy user prompt builders + dispatcher |
| `common/model_analysis.py` L1087-1270 | `_coerce_stock_strategy_output()`, `_build_fallback_stock_analysis()`, `analyze_stock_strategy()` |
| `common/tmc_final_decision_prompts.py` | TMC final-decision prompt (overlap comparison target) |
| `app/api/routes_reports.py` | `/api/model/analyze_stock_strategy` endpoint (on-demand call site) |
| `app/workflows/stock_opportunity_runner.py` L1163-1380 | Stage 7 — uses TMC prompt, NOT strategy prompts |

---

## 2  Cross-Strategy Comparison Table

| Dimension | Pullback Swing | Momentum Breakout | Mean Reversion | Volatility Expansion |
|-----------|---------------|-------------------|----------------|---------------------|
| **Builder** | `_build_pullback_swing_prompt` | `_build_momentum_breakout_prompt` | `_build_mean_reversion_prompt` | `_build_volatility_expansion_prompt` |
| **Ideal RSI** | 40-60 | 50-70 | < 30 | n/a |
| **Hold period** | 3-15 days | 5-20 days | 1-7 days | 5-20 days |
| **Primary gate** | Pullback -1% to -6% from 20D high | Tight base + volume spike | Z-score < -2, oversold | ATR ratio > 1.3, BB expanding |
| **Metric groups** | 5 (18 fields) | 5 (25 fields) | 5 (20 fields) | 5 (26 fields) |
| **Analysis Qs** | 3 | 3 | 3 | 3 |
| **Anti-anchoring** | None | None | None | None |
| **Engine data sent** | composite_score, score_breakdown, thesis | same | same | same |
| **Output schema** | Shared (BUY/PASS, score, confidence, …) | same | same | same |
| **Market picture** | Appended if available | same | same | same |

---

## 3  System Prompt Analysis

**Constant**: `STOCK_STRATEGY_SYSTEM_PROMPT` — shared identically across all 4 strategies.

### Strengths
- Clear role definition: "short-term stock risk advisor", 1-30 day horizon, long-only, BUY/PASS only.
- Explicit JSON formatting rules reduce parse failures (raw JSON, no markdown, no trailing commas).
- Demands the model produce an independent 0-100 score separate from the engine score.
- Rule #4: "You MUST compare your score to the engine score and explain any disagreement."
- Strict data boundary: "Use ONLY the provided metrics and engine data."

### Weaknesses
- **No temperature/model identity guidance** in the prompt itself (temperature=0.0 is set in the transport payload, which is correct, but the model has no knowledge of whether it is running deterministically).
- **No strategy-awareness at the system level**. The system prompt says "Strategies: long-only equity positions" generically — all strategy context comes in the user prompt. This means the model cannot adjust its reasoning framework (e.g., mean reversion vs momentum) at the system level.
- **"Never give financial guarantees"** — generic legal hedge, adds token budget without changing behavior.
- **Missing**: No guidance on what constitutes a strong BUY vs a marginal BUY. Rule #4 demands comparison but does not set a threshold for disagreement (e.g., "if you differ from the engine by >20 points, explain why").

---

## 4  Per-Strategy Prompt Construction

All 4 builders follow an identical structural pattern:

```
1. _extract_common_fields(candidate)  →  symbol, price, as_of, composite_score, score_breakdown, thesis, data_source, data_confidence
2. Per-strategy metric extraction from candidate["metrics"]
3. JSON payload construction: { strategy, strategy_description, symbol, price, as_of, engine: {...}, <metric_groups>, analysis_questions }
4. json.dumps(payload) → return
5. Dispatcher appends market_picture_context if present
```

### _extract_common_fields

Sends to the model:
- `composite_score` (engine's aggregate score)
- `score_breakdown` (per-pillar engine scores)
- `thesis` (engine-generated thesis summary)
- `data_source` and `data_confidence` (provenance metadata)

These are wrapped in an `engine` key: `{"composite_score": ..., "score_breakdown": ..., "thesis": ...}`.

### Analysis Questions

Each strategy has exactly 3 tailored questions. These are well-designed to test the setup's failure modes rather than confirm the thesis — a positive quality.

Examples:
- Pullback Swing: "Is the pullback deep enough to offer meaningful upside, or is it a minor dip in a flattening trend?"
- Mean Reversion: "Is there evidence the selling is slowing, or is the stock still in free fall?"
- Momentum Breakout: "Has the breakout already happened (i.e., is this chasing), or is there still room to the upside?"

---

## 5  Strategy-Specific Guidance Assessment

Each builder provides:
1. **strategy_description**: 2-3 sentence description of the ideal setup with numeric thresholds (e.g., "RSI 40-60", "pullback -1% to -6%").
2. **Curated metric groups**: Metrics chosen to match the strategy's edge hypothesis.
3. **Analysis questions**: 3 questions targeting the most likely failure mode for that strategy.

### Quality Assessment

| Strategy | Description quality | Metric relevance | Question quality |
|----------|-------------------|-------------------|-----------------|
| Pullback Swing | Good — clear thresholds | Good — trend_metrics + pullback_metrics + momentum_reset | Good — tests "is it deep enough?" and "is momentum resetting?" |
| Momentum Breakout | Good — clear thresholds | Good — breakout_metrics + compression + volume | Good — tests "is this chasing?" and "is volume real?" |
| Mean Reversion | Good — clear thresholds | Good — oversold + distance + stabilization | Good — tests "is selling slowing?" and "snapback room?" |
| Vol Expansion | Good — clear thresholds | Good — expansion + compression_history + directional_bias | Good — tests "is expansion sustainable?" and "directional bias?" |

**Verdict**: The per-strategy guidance is one of the strongest parts of the prompt system. Each strategy's description, metrics, and questions are coherent and well-targeted.

---

## 6  Output Schema Analysis

### Demanded Fields (7 top-level)

| Field | Type | Purpose | Coercion behavior |
|-------|------|---------|-------------------|
| `recommendation` | "BUY"\|"PASS" | Binary decision | Invalid → "PASS" |
| `score` | int 0-100 | Model's independent assessment | Parse failure → 50 |
| `confidence` | int 0-100 | Model's self-assessed reliability | Parse failure → 50; ≤1 treated as 0-1 scale → ×100 |
| `summary` | string | 1-2 sentence thesis | Empty → "Model returned no summary." |
| `key_drivers` | array of {factor, impact, evidence} | Factor analysis | Tolerates string items; aliases `name`→`factor`, `detail`→`evidence` |
| `risk_review` | object | Primary risks + vol/timing risk levels | Defaults to "medium" for risk levels |
| `engine_vs_model` | object | Engine score echo + model score + agreement | Agreement validated to agree/disagree/mixed; defaults "mixed" |
| `data_quality` | object | Provider + warnings | Defaults provider to "tradier" |

### Coercion Concerns

- **F-4B-01 (M): score defaults to 50 on parse failure**. This creates a phantom mid-range score that is neither the engine's opinion nor the model's. A failed parse should produce a null score or map to the fallback path, not inject a specific numeric value.
- **F-4B-02 (L): confidence ≤1 rescaled to ×100**. Clever, but creates ambiguity: is confidence=1 "1 out of 100" or "100%"? This heuristic could produce false 100-confidence results if the model legitimately returns 1/100.

### Fallback Contract

`_build_fallback_stock_analysis()` returns a well-formed PASS with confidence=20, score=engine_score, `_fallback=True`. This is conservative and correct — the system never returns a broken shape.

---

## 7  Anti-Anchoring Assessment

### Finding F-4B-03 (H): Complete Anti-Anchoring Failure

All 4 strategy prompts include the engine's `composite_score`, `score_breakdown`, and `thesis` directly in the user prompt payload under the `engine` key. The model sees the engine's assessment before producing its "independent" score.

**Additionally**, the output schema explicitly demands `engine_vs_model.engine_score`, requiring the model to echo the engine score in its output and compare against it. This makes anti-anchoring structurally impossible.

**Root cause**: The system prompt says "Your score is YOUR independent 0-100 assessment, separate from the engine score" (Rule #3), but then immediately shows the engine score in the input data. This creates a contradictory instruction: "be independent but here's the answer."

**When market_picture_context is present**, the anchoring deepens further — all 6 MI engine scores/labels/summaries are appended, each with a 0-100 score.

**Research consensus**: Providing a reference number before asking for an independent estimate produces systematic anchoring bias (Tversky & Kahneman, 1974). LLMs are particularly susceptible because they optimize for coherence with context.

**Comparison to 4A (Regime Prompt)**: Same pattern — pillar scores leak into the regime prompt. This is a systemic design choice, not a bug in any single prompt.

---

## 8  TMC Final Decision Prompt Overlap Analysis

### Finding F-4B-04 (H): Two Prompt Systems Evaluate the Same Candidate

The codebase contains two independent prompt systems that can evaluate the same stock candidate:

| Dimension | Strategy Prompt (`stock_strategy_prompts.py`) | TMC Prompt (`tmc_final_decision_prompts.py`) |
|-----------|----------------------------------------------|---------------------------------------------|
| **Decision label** | BUY / PASS | EXECUTE / PASS |
| **Score field** | `score` (0-100) | `conviction` (0-100) + `engine_comparison.model_score` |
| **System role** | "short-term stock risk advisor" | "disciplined short-term portfolio manager" |
| **Strategy awareness** | Full — per-strategy metric groups + questions | Light — strategy_description + generic tech analysis |
| **Market picture** | Appended if available | Built-in section with fuller structure |
| **Regime context** | Not included | Full regime section (vix, regime_tags, risk_environment) |
| **Risk analysis** | `risk_review` (3 fields) | `risk_assessment` (3 fields) + factors_considered |
| **Tech analysis** | Not structured | Full `technical_analysis` section (6 sub-fields) |
| **Factor analysis** | `key_drivers` (factor/impact/evidence) | `factors_considered` (category/factor/assessment/weight/detail) — richer |
| **Output fields** | 8 | 9 |
| **Used in pipeline?** | No — on-demand only via API | Yes — Stage 7 of stock_opportunity_runner |
| **Call site** | `routes_reports.py` `/api/model/analyze_stock_strategy` | `model_routing_integration.routed_tmc_final_decision` |

### Key Overlap Concerns

1. **Different decision labels for same concept**: BUY vs EXECUTE both mean "take the trade." Frontend and downstream code must translate between them.
2. **Two independent scores**: `score` (strategy prompt) vs `model_score` in `engine_comparison` (TMC prompt). If both are run on the same candidate, which score is authoritative?
3. **Pipeline vs on-demand disconnect**: Stage 7 uses the TMC prompt exclusively. The strategy prompt is only accessible via the API button. If a user runs the on-demand strategy analysis after the pipeline has already run TMC analysis, they get two potentially conflicting assessments.
4. **Strategy prompt is richer for setup quality**: Per-strategy metric curation and analysis questions give the strategy prompt better signal for setup evaluation. But the TMC prompt is what actually runs in the pipeline.
5. **TMC prompt is richer for context**: Regime tags, risk flags, supporting signals, entry context — all absent from the strategy prompt.

### Recommendation

Consider: (a) merging the strategy prompt's per-setup metric curation into the TMC prompt so the pipeline benefits from strategy-specific guidance, or (b) deprecating one system entirely to eliminate dual-assessment confusion.

---

## 9  BUY/PASS Decision Quality Assessment

### Finding F-4B-05 (M): No Decision Threshold Guidance

The system prompt says "Recommendations: BUY or PASS (never SELL / SHORT / HOLD / WAIT)" but provides no guidance on what score threshold implies BUY vs PASS.

- The TMC prompt explicitly says: "Conviction below 60 should be a PASS."
- The strategy prompt has no equivalent.
- This means the model must decide on its own when to say BUY vs PASS, with no calibration anchor.

### Finding F-4B-06 (M): No Edge Definition

The strategy prompt asks the model to evaluate "probability-weighted edge, risk, and timing" but never defines what constitutes sufficient edge. Without a minimum threshold (e.g., "only recommend BUY if you believe the expected value is positive after accounting for transaction costs"), the model has no basis for calibrating its BUY rate.

### Finding F-4B-07 (M): Confidence Has No Behavioral Consequence

The model returns `confidence` (0-100) but neither the prompt nor the coercion layer defines what happens at different confidence levels. In the TMC prompt, conviction < 60 → PASS. In the strategy prompt, confidence is purely informational — a BUY with confidence=30 is treated identically to a BUY with confidence=90.

---

## 10  Consistency Assessment

### Finding F-4B-08 (L): Temperature 0.0 Is Correct for Reproducibility

All strategy analyses use `temperature=0.0` and `max_tokens=2048`. This is appropriate for deterministic evaluation of structured data. No issue here.

### Finding F-4B-09 (L): Market Picture Inclusion Is Conditional

Market picture context is appended only if `candidate["market_picture_context"]` is present. This means:
- On-demand API calls may or may not have market picture depending on whether the caller passes it.
- Two analyses of the same candidate could produce different results depending on whether market picture was available.

This is not necessarily wrong (more data = better), but it means the prompt is structurally variable in a way that is not visible to the end user.

### Finding F-4B-10 (L): data_source and data_confidence Sent but Not Used

`_extract_common_fields()` extracts `data_source` and `data_confidence` from the candidate, but these appear in the common fields and are not highlighted or asked about in any of the 4 strategy builders' analysis questions. The model receives them but has no guidance on how to use them.

---

## 11  Findings Summary

| ID | Sev | Finding |
|----|-----|---------|
| F-4B-01 | M | Score defaults to 50 on parse failure — phantom mid-range value injected instead of null/fallback |
| F-4B-02 | L | Confidence ≤1 rescaling heuristic creates ambiguity between 1/100 and 100% |
| F-4B-03 | H | Complete anti-anchoring failure — engine composite_score, score_breakdown, and thesis sent directly; output schema demands engine_score echo |
| F-4B-04 | H | Two prompt systems (strategy + TMC) evaluate the same candidate with different decision labels, scores, and context depth; only TMC runs in the pipeline |
| F-4B-05 | M | No decision threshold guidance — unlike TMC's "conviction < 60 → PASS", strategy prompt provides no BUY/PASS calibration |
| F-4B-06 | M | No edge definition — model asked to evaluate "probability-weighted edge" without any minimum-edge threshold |
| F-4B-07 | M | Confidence has no behavioral consequence — a BUY@confidence=30 is treated identically to BUY@confidence=90 |
| F-4B-08 | L | Temperature 0.0 is correct for reproducibility (no issue) |
| F-4B-09 | L | Market picture inclusion is conditional — prompt structure varies invisibly depending on data availability |
| F-4B-10 | L | data_source and data_confidence sent to model but no guidance on how to use them |

**Severity distribution**: 2 High, 4 Medium, 4 Low (including 1 positive observation)

---

## 12  Systemic Patterns (Cross-Prompt)

Combining 4A (Regime Prompt) and 4B (Strategy Prompts):

1. **Anti-anchoring is absent by design** across all model prompts. Both pillar scores (4A) and composite scores (4B) are sent to the model. The `engine_vs_model` / `engine_comparison` output field makes this an intentional architectural choice, not an oversight.

2. **Two-system redundancy**: The strategy prompt and TMC prompt both evaluate stock candidates with overlapping but non-identical context. The strategy prompt is strategy-deeper; the TMC prompt is context-wider. Neither subsumes the other.

3. **Confidence is decorative** in both systems. The strategy prompt attaches no behavioral consequence to confidence. The TMC prompt at least says "conviction < 60 → PASS" in the prompt text, but `_coerce_stock_strategy_output` does not enforce this at the coercion layer for strategy analyses.

4. **On-demand vs pipeline gap**: Strategy prompts are only available on-demand via API. The pipeline exclusively uses TMC prompts. This means the strategy prompt's superior per-setup metric curation is never used in automated workflows.
