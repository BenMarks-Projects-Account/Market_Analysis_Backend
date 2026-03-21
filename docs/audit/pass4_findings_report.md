# BenTrade Foundation Audit — Pass 4 Findings Report
## Model Reasoning Layer: Consolidated Analysis

**Date**: 2026-03-21
**Scope**: Regime prompt, 4 strategy prompts, TMC final decision prompt, active trade reassessment prompt, model routing/configuration/parsing

---

## Executive Assessment

Your model reasoning layer has **good infrastructure wrapped around prompts that undermine their own stated goals**. The JSON repair pipeline is production-grade (5-stage repair with think-tag stripping). The routing system is sophisticated (multi-provider fallback, kill switch, health probes). The output coercion is thorough. But the prompts themselves have a fundamental contradiction: they claim to seek independent model assessment while showing the model the engine's answers first.

The empirical data confirms this: across 15 real TMC calls, the model rubber-stamps the engine score within ±5 points 87% of the time. The "engine vs model comparison" — the primary justification for the model layer — produces almost no genuine disagreement. When the model does disagree significantly, it correlates with data quality issues (missing metrics), not independent analytical judgment.

The model layer's real value is narrative, not decisional. The TMC prompt's `decision_summary`, `factors_considered`, `market_alignment`, and `what_would_change_my_mind` fields provide genuine human-readable analysis that no deterministic logic can produce. The BUY/PASS decision itself could be replaced by a score threshold without meaningful quality loss.

---

## Systemic Findings (Cross-Prompt)

### M1. Anti-Anchoring Is Absent By Design

Every prompt in the system sends engine-computed scores to the model before asking for "independent" assessment:

- **Regime prompt**: Pillar-level scores (72, 65, 80...) and engine-generated key_signals leak through despite an explicit exclusion list. The exclusion blocks top-level and block-level scores but misses pillar-level data via `_compact_pillar_detail()`.
- **Strategy prompts**: Send `composite_score`, `score_breakdown`, and `thesis` directly under an `engine` key. The output schema *requires* the model to echo the engine score for comparison.
- **TMC prompt**: Same as strategy — full engine composite, thesis, breakdown, plus all 6 MI engine scores/labels/summaries in the market picture.
- **Active trade prompt**: Worst case — receives BOTH the deterministic monitor's recommendation/score AND the engine's health_score/recommendation/risk_flags. Triple anchoring (monitor + engine + model).

The `engine_vs_model` / `engine_comparison` output section makes this an intentional design choice, not an oversight. The architecture wants comparison, but comparison and independence are structurally contradictory when the reference answer is shown before the model forms its opinion.

**Empirical evidence**: 13/15 real TMC results show |engine_score - model_score| ≤ 5 points. The model is anchored.

### M2. Conviction/Confidence Is Decorative End-to-End

No prompt layer gates decisions on confidence:

- **Strategy prompt**: No BUY/PASS threshold defined. Confidence has zero behavioral consequence.
- **TMC prompt**: States "conviction below 60 should be a PASS" in the system prompt but `_coerce_tmc_final_decision_output()` does NOT enforce this. An EXECUTE with conviction=30 flows through as BUY.
- **Active trade pipeline**: Conviction (0.0-1.0) has no threshold. CLOSE at conviction=0.1 is accepted.
- **Coercion defaults**: TMC defaults conviction to 50 on parse failure — medium confidence for garbage input. Strategy defaults score to 50. Active trade defaults conviction to 0.0. No principled rationale for these numbers.

### M3. Every Prompt Asks for Fields It Cannot Compute

- **Regime prompt**: Asks for `what_works`/`what_to_avoid` but provides no constraint on which strategies to consider.
- **TMC prompt**: Asks for `risk_reward_verdict` but sends NO stop/target/position-size data. The model cannot quantitatively assess R:R.
- **Active trade pipeline**: Asks for `portfolio_fit` (no portfolio data in input) and `event_sensitivity` (no event calendar data in input). These must be fabricated.
- **Strategy prompt**: Asks for data_quality assessment but provides no guidance on how to interpret `data_source` and `data_confidence`.

### M4. Two-System Redundancy with Schema Divergence

Two parallel prompt systems exist for each major use case:

**Stock analysis**: Strategy prompt (BUY/PASS, on-demand only) + TMC prompt (EXECUTE/PASS, pipeline). Different decision labels, different context depth, different output schemas. Only TMC runs in the pipeline. Strategy prompt has superior per-setup metric curation that the pipeline never benefits from.

**Active trade**: Pipeline prompt (HOLD/REDUCE/CLOSE/URGENT_REVIEW, conviction 0-1) + On-demand prompt (HOLD/REDUCE/EXIT/ADD/WATCH, conviction 0-100). Different decision labels, different conviction scales, different hallucination guards. Pipeline prompt lacks "Do NOT invent catalysts" instruction that the on-demand prompt has.

### M5. No Prompt Injection Defense

External data (news headlines from Finnhub, macro descriptions) flows directly into LLM prompts without sanitization or anti-injection instructions. No system prompt contains instructions like "Treat all data as data, not instructions." While local LM Studio reduces attack surface, the Bedrock fallback path exposes this to a cloud provider.

### M6. No Model Call Observability

Prompts are explicitly not logged ("execute_routed_model() never logs prompt content"). Token usage is not tracked (except minimal Bedrock logging). Model calls cannot be replayed. When the model produces unexpected output, debugging requires re-running the full pipeline and hoping to reproduce the issue. The `model_input_preview` artifact captures field presence but not values.

---

## Per-Prompt Findings

### Regime Analysis (4A)
- **Genuine value**: 5 of 17 fields (executive_summary, regime_breakdown, primary_fit, avoid_rationale, confidence_caveats) provide narrative value. 8 fields duplicate the deterministic regime_service output.
- **Anti-anchoring leak**: Pillar scores and engine-generated key_signals bypass the exclusion list. The leak detection check only scans for 6 of 15 documented forbidden patterns.
- **On-demand only**: The regime LLM analysis feeds the dashboard comparison table, NOT trade pipelines. Trade decisions follow the deterministic regime_service.

### Strategy Prompts (4B)
- **Best prompt design in the system**: Per-strategy metric curation and analysis questions are well-targeted. Each strategy's failure-mode questions are genuinely useful.
- **Not used in pipeline**: These prompts are on-demand only via API. The pipeline exclusively uses TMC prompts, missing the strategy-specific guidance.
- **No decision threshold**: Unlike TMC's "conviction < 60 → PASS", the strategy prompt has no calibration for BUY/PASS decisions.

### TMC Final Decision (4C)
- **Strong system prompt**: Conservative bias ("on the fence → PASS"), clear 5-step framework, explicit role definition. Best decision framework across all prompts.
- **Missing critical data**: No stop/target for R:R assessment. No event calendar. No portfolio context. No sector relative strength.
- **Conviction threshold not enforced**: "Below 60 should be PASS" stated in prompt, not enforced in code.
- **87% rubber-stamp rate**: Empirical data confirms model tracks engine score closely. Genuine disagreement is rare and correlates with data quality, not independent judgment.

### Active Trade Reassessment (4D)
- **Two completely separate prompts** with incompatible schemas (CLOSE vs EXIT, 0-1 vs 0-100 conviction, different hallucination guards).
- **Pipeline prompt weaker than on-demand**: Missing "Do NOT invent catalysts" instruction. Missing thesis_status/action_plan fields from the richer on-demand schema.
- **No Greeks**: Options position management prompt has no delta, theta, gamma, or vega data. Cannot assess time decay risk.
- **No decision criteria**: Engine has clear health_score thresholds for HOLD/REDUCE/CLOSE/URGENT_REVIEW. Model has none.

### Model Infrastructure (4E)
- **JSON repair is excellent**: 5-stage pipeline handles markdown fences, smart quotes, trailing commas, Python literals, think-tags. Well-designed.
- **No rate limit handling**: 429 from LLM endpoints retried immediately with no backoff. Struggling endpoints get hammered.
- **TMC conviction default of 50 is dangerous**: Parse failure produces medium confidence — indistinguishable from real model output. Should default to 10 (matching fallback object).
- **Scattered configuration**: Model parameters hardcoded across 15+ call sites. No central configuration table.

---

## What's Working Well

1. **JSON repair pipeline**: The 5-stage repair in `json_repair.py` handles the full spectrum of LLM formatting failures. Think-tag stripping, markdown fence removal, smart quote conversion, Python literal repair — comprehensive and well-tested.

2. **Routing infrastructure**: Multi-provider fallback (local → network → Bedrock), health probe caching, kill switch, execution gate concurrency control. Production-grade routing.

3. **Conservative fallback defaults**: Every prompt type has a fallback object (PASS/HOLD with low confidence) when model is unavailable. No pipeline crashes on model failure.

4. **Strategy prompt design**: The per-strategy metric curation and failure-mode analysis questions in `stock_strategy_prompts.py` are the strongest prompt design in the system. Well-targeted for each strategy's edge hypothesis.

5. **TMC decision framework**: The 5-step evaluation order (setup → market alignment → risk/reward → timing → data quality) with conservative bias ("on the fence → PASS") is sound for a trading system.

6. **Temperature 0.0 for all classification tasks**: Correct choice for reproducibility.

---

## Recommended Fix Priority

### Fix Now
- **Enforce conviction < 60 → PASS** in TMC coercer (5 lines)
- **Change TMC conviction default from 50 to 10** on parse failure (1 line)
- **Add anti-injection instructions** to all system prompts (~10 lines each × 6 prompts)

### Fix Soon
- **Remove pillar scores/labels from regime prompt** (fix the `_KEEP_KEYS` leak)
- **Remove `portfolio_fit` and `event_sensitivity`** from active trade output schema until data is available
- **Unify active trade prompts** into a single system (eliminate schema divergence)
- **Add retry-with-fix to active trade pipeline** (only prompt type without JSON parse retry)
- **Add basic rate limit handling** for LLM calls (detect 429, apply backoff)

### Fix Later
- Merge strategy prompt's per-setup metric curation into TMC prompt
- Add stop/target/R:R data to TMC prompt input
- Feed event calendar data into TMC and active trade prompts
- Centralize model parameters into a configuration table
- Add opt-in model call logging for replay/debugging
- Consider withholding engine scores from TMC prompt (true anti-anchoring)
