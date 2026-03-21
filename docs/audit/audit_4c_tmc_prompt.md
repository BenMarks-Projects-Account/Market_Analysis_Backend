# Audit 4C — TMC Final Decision Prompt Review

**Scope**: TMC (Trade Management Center) final decision prompt — the portfolio-level EXECUTE/PASS LLM call  
**Date**: 2025-07-18  
**Status**: Complete  

---

## 1  Files Examined

| File | Role |
|------|------|
| `common/tmc_final_decision_prompts.py` | System prompt + user prompt builder + metric extraction |
| `app/services/model_routing_integration.py` L334-530 | `routed_tmc_final_decision()` — routing dispatch with fallback |
| `common/model_analysis.py` L1436-1640 | `_coerce_tmc_final_decision_output()` + `_build_fallback_tmc_decision()` |
| `common/model_analysis.py` L1643-1820 | `analyze_tmc_final_decision()` — legacy transport path |
| `app/workflows/stock_opportunity_runner.py` L1163-1380 | Stage 7 — pipeline integration layer |
| `results/model_stock_strategy_*.jsonl` | Real engine-vs-model score data |

---

## 2  System Prompt Analysis

### Complete System Prompt

The full `TMC_FINAL_DECISION_SYSTEM_PROMPT` is a ~1400-token constant covering:

### Role Assignment
**"Disciplined short-term portfolio manager making real allocation decisions with real capital."**

This is a strong role definition — significantly more concrete than the strategy prompt's "risk advisor." The prompt emphasizes:
- "This is not an academic exercise — you are deciding whether to commit money."
- "You prioritize staying net positive over any single trade's upside."
- "A skipped good trade costs nothing, but a bad trade costs capital and opportunity."

**Risk-aversion bias is explicitly designed in** — the system prompt deliberately creates a conservative default ("if you are on the fence, PASS").

### Decision Framework (5-step)

The prompt defines this evaluation order:

| Step | Dimension | Instruction |
|------|-----------|-------------|
| 1 | **Trade Setup** | "Does the technical setup have genuine edge, or is it noise?" |
| 2 | **Market Alignment** | "Does the broader market environment support this type of trade right now?" |
| 3 | **Risk/Reward** | "Is the potential gain worth the risk of loss?" |
| 4 | **Timing** | "Is this the right moment, or could patience improve the entry?" |
| 5 | **Data Quality** | "Is the data you're seeing trustworthy and complete?" |

### Decision Framework Assessment (Deliverable #3)

**The prompt spec asks about**: event risk → market alignment → setup quality → risk/reward → sector → timing → data quality.

**What the prompt actually defines**: trade setup → market alignment → risk/reward → timing → data quality.

**Missing from the framework**:
- **Event risk** — not mentioned at all. No instruction to check for earnings, FOMC, or other catalysts.
- **Sector context** — no instruction to evaluate sector rotation or relative strength.
- **Portfolio context** — no instruction to consider existing positions, correlation, or exposure.

**Is the defined order correct for the trading philosophy?**  
Partially. Starting with trade setup quality is reasonable — reject bad setups before wasting analysis on market alignment. However, event risk should arguably be step 0 (a great setup 2 days before earnings is a different trade entirely). The BenTrade philosophy emphasizes "high-probability, risk-defined" — event risk is a first-order concern that is completely absent.

### Decision Rules

Clear and well-specified:
- EXECUTE = "you would size the position for your own portfolio right now"
- PASS = "edge insufficient, risk too high, market environment wrong, or timing poor"
- "If you are on the fence, PASS."
- **"Conviction below 60 should be a PASS."** — this is guidance, not a hard rule ("should be" vs "must be")

### Uncertainty Handling
- "If you are on the fence, PASS."
- Conservative by design: the system prompt creates asymmetric incentives where PASS is the safe default.
- "Be brutally honest. Do not recommend trades you wouldn't take yourself."

### Bias Countermeasures
- **Action bias**: Addressed — "skipped good trade costs nothing" + "on the fence → PASS"
- **Anchoring**: NOT addressed — engine score is sent directly (see Finding F-4C-04)
- **Overconfidence**: Partially addressed — "Be brutally honest" + conviction threshold
- **Confirmation bias**: NOT addressed — engine thesis is sent, and the prompt asks the model to evaluate it, not to independently form a thesis first

### Output Schema

9 top-level fields, well-structured:

| Field | Type | Purpose |
|-------|------|---------|
| `decision` | "EXECUTE"\|"PASS" | Binary portfolio decision |
| `conviction` | int 0-100 | Self-assessed confidence |
| `decision_summary` | string | 2-3 sentence thesis with metric citations |
| `technical_analysis` | object (6 sub-fields) | Structured metric-by-metric breakdown |
| `factors_considered` | array of objects | Categorized factor analysis |
| `market_alignment` | object | Overall + detail on market support |
| `risk_assessment` | object | Primary risks + biggest concern + R:R verdict |
| `what_would_change_my_mind` | string | Reversibility condition |
| `engine_comparison` | object | Engine score vs model score + reasoning |

---

## 3  User Prompt Construction

### `build_tmc_final_decision_prompt()` — Payload Structure

```
{
  "trade_setup": {
    "symbol", "price", "as_of", "strategy_id", "strategy_description",
    "direction",
    "engine": { "composite_score", "thesis", "score_breakdown", "confidence" }
  },
  "technical_metrics": {
    "trend": { trend_state, sma20, sma50, sma200, slope_20, slope_50, dist_sma20, dist_sma50 },
    "momentum": { rsi14, rsi2, rsi_change_5d, roc_10, roc_20, return_1d..return_20d },
    "volatility": { atr_pct, atr_ratio_10, rv_ratio, bb_width_20, realized_vol_20, range_*_pct },
    "volume_liquidity": { avg_vol_20, avg_dollar_vol_20, today_vol, today_vol_vs_avg, vol_spike_ratio },
    "price_levels": { price, high_20, high_55, high_252, pullback_from_*_high, pct_from_52w_high },
    "strategy_specific": { breakout_state, reversion_state, expansion_state, compression_score, ... },
    "scores": { composite_score }
  },
  "market_environment": {
    "description": "...",
    "engines": {
      "<engine_key>": { score, label, confidence, summary, trader_takeaway, bull_factors, bear_factors, risks }
    }
  },
  "regime_context": {
    "market_regime", "risk_environment", "vix", "regime_tags", "support_state"
  },
  "signals_and_flags": {
    "supporting_signals", "risk_flags", "entry_context"
  },
  "decision_prompt": "Based on ALL the above data..."
}
```

### What's Included
- Full engine composite score, thesis, score breakdown, confidence
- All available technical metrics (up to ~50 individual metrics organized into 7 groups)
- All 6 MI engine outputs with scores/labels/summaries
- Regime context (VIX, tags, risk environment)
- Supporting signals and risk flags from the scanner

### What's Excluded
- **No proposed_trade data** (stop loss, target, R:R, position size) — the model cannot evaluate risk/reward quantitatively
- **No event calendar** (earnings dates, FOMC, ex-div)
- **No portfolio context** (existing positions, correlation, total exposure)
- **No historical strategy performance** (win rate, avg gain/loss for this strategy)
- **No ATR-based stop/target suggestions** from the engine
- **No sector relative strength** data

### Approximate Token Count
- System prompt: ~1400 tokens
- User prompt (with market picture): ~2000-3500 tokens depending on metric density and engine count
- **Total context budget**: ~3500-5000 tokens
- **Max response tokens**: 3000
- **Truncation risk**: Low — payload is compact JSON, well within typical context windows

---

## 4  Factor Analysis Schema

### Factor Categories Defined

| Category | Coverage | Assessment |
|----------|----------|------------|
| `trade_setup` | Technical setup quality | Well-covered — metrics are comprehensive |
| `market_environment` | Broader market conditions | Covered — 6 engine outputs provided |
| `risk_reward` | Potential gain vs loss | **Structurally hollow** — no stop/target data sent |
| `timing` | Entry timing | Available — current price vs SMAs, RSI timing |
| `data_quality` | Data trustworthiness | Available — data_source, confidence markers |

### Assessment Values: `favorable | unfavorable | neutral | concerning`
Well-chosen — 4 gradations with clear semantic meaning. The coercion layer validates these strictly and defaults invalid values to "neutral."

### Weight Values: `high | medium | low`
Reasonable for priority signaling. The coercion layer clamps to these 3 values.

### Missing Factor Categories (Finding F-4C-01)

| Missing Category | Why It Matters |
|------------------|---------------|
| **Event risk** | Earnings/FOMC/ex-div within DTE can dominate all other factors |
| **Portfolio context** | Correlation with existing positions; total portfolio exposure |
| **Sector** | Sector rotation, relative strength vs benchmark |
| **Liquidity** | While volume data is in metrics, no explicit factor category for execution risk |

The factor category list is hardcoded in the coercion layer: `valid_categories = {"trade_setup", "market_environment", "risk_reward", "timing", "data_quality"}`. If the model uses a category outside this set (e.g., "event_risk"), it is silently remapped to "trade_setup." This means any intelligent event-risk reasoning the model produces would be miscategorized.

---

## 5  Engine Comparison — Anti-Anchoring

### Finding F-4C-04 (H): Engine Score Sent Directly — Anchoring By Design

The user prompt includes:
```json
"engine": {
  "composite_score": 87,
  "thesis": ["Strong uptrend with healthy pullback..."],
  "score_breakdown": {"trend": 92, "momentum": 78, ...},
  "confidence": 0.85
}
```

The output schema then asks:
```json
"engine_comparison": {
  "engine_score": <number from input>,
  "model_score": <your independent 0-100 score>,
  "agreement": "agree|disagree|partial",
  "reasoning": "..."
}
```

The model sees the engine's conclusion before forming its own assessment. The `engine_comparison` section explicitly asks the model to compare its score to the engine's — making independent thinking structurally contradictory.

### Real Score Distribution Data

Analysis of actual results files (`model_stock_strategy_*.jsonl`) reveals the anchoring effect empirically:

| Engine Score | Model Score | Delta | Agreement | Symbol | Strategy |
|-------------|-------------|-------|-----------|--------|----------|
| 71 | 73 | +2 | agree | RTX | momentum_breakout |
| 68 | 72 | +4 | agree | CVX | momentum_breakout |
| 73 | 78 | +5 | agree | GS | mean_reversion |
| 69 | 65 | -4 | agree | USB | mean_reversion |
| 63 | 58 | -5 | disagree | BAC | mean_reversion |
| 67 | 78 | +11 | disagree | HD | mean_reversion |
| 80 | 50 | -30 | disagree | AAPL | pullback_swing (missing data) |
| 87 | 65 | -22 | disagree | PFE | pullback_swing |
| 87 | 82 | -5 | agree | SLB | pullback_swing |
| 87 | 85 | -2 | agree | PFE | pullback_swing |
| 87 | 85 | -2 | agree | SLB | pullback_swing |
| 91 | 93 | +2 | agree | WMT | pullback_swing |
| 72 | 75 | +3 | agree | KO | pullback_swing |
| 85 | 88 | +3 | agree | SBUX | pullback_swing |
| 82 | 78 | -4 | agree | O | pullback_swing |

**Observations**:
- **13/15 results show |delta| ≤ 5 points** — the model score tracks the engine score very closely.
- **Only 2 results show significant disagreement** (AAPL -30 with missing data, PFE -22, HD +11).
- **When the model does "disagree," it self-labels as "agree" in 2/4 cases** (USB at -4 says "agree").
- **The model almost never produces a score lower than 50** regardless of conditions — it appears anchored to the engine's already-positive score range (candidates reaching TMC are pre-filtered to engine scores ≥60).

**Conclusion**: The model rubber-stamps the engine score within ±5 points approximately 87% of the time. Genuine independent assessment is rare and appears correlated with data quality issues rather than substantive disagreement.

---

## 6  Market Picture Integration

### How the Model Receives Engine Data

Each of 6 engines is included as:
```json
"<engine_key>": {
  "score": 72,
  "label": "moderately_bullish",
  "confidence": 85,
  "summary": "Breadth metrics show broad participation...",
  "trader_takeaway": "Conditions favor long setups...",
  "bull_factors": ["advancing issues > declining", ...],
  "bear_factors": ["small-cap lagging", ...],
  "risks": ["rotation from tech", ...]
}
```

### Can the Model Detect Engine Disagreement?

Yes — the data structure supports it. If `breadth_participation.label = "bearish"` and `volatility_options.label = "bullish"`, the model can see this. However:

### Finding F-4C-05 (M): No Guidance on Weighting Conflicting Signals

The prompt says: "Use these to judge whether the broader environment supports this trade." It does not:
- Define which engines matter more for which strategy types
- Guide how to resolve conflicts (e.g., breadth bearish + volatility bullish)
- Specify a threshold for "market environment conflicting enough to override a good setup"
- Tell the model whether engine unanimity matters or if a single bearish engine should change the decision

The model must improvise a weighting scheme with no calibration. This means the market picture context adds variable, unreliable signal.

---

## 7  Risk Assessment Quality

### Finding F-4C-02 (H): No Stop/Target Data — Risk/Reward Is Guesswork

The output schema requests:
- `risk_assessment.risk_reward_verdict`: "favorable" | "marginal" | "unfavorable"
- `risk_assessment.primary_risks`: array of risk descriptions
- `risk_assessment.biggest_concern`: single most threatening factor

But the **input data contains NO proposed trade parameters**:
- No stop loss level
- No target price
- No risk:reward ratio
- No position size
- No ATR-based stop suggestions

The model is asked to assess risk/reward without the information needed to make that assessment. The result is generic prose ("favorable" because the setup looks good, not because the R:R ratio is 2.5:1). The prompt's decision framework step 3 ("Is the potential gain worth the risk of loss? Consider the downside scenario, not just the base case") is unanswerable with the data provided.

**This was identified as missing in the architecture doc and has NOT been added.**

### What Data IS Available for Risk Assessment

The model does receive:
- Technical metrics (ATR, distance to SMAs) — could infer approximate ranges
- `risk_flags` from the scanner — but these are string labels, not quantitative levels
- `supporting_signals` — similarly qualitative

This allows for qualitative risk commentary but not the quantitative R:R evaluation the schema implies.

---

## 8  Conviction < 60 Enforcement

### Finding F-4C-03 (H): Conviction Threshold NOT Enforced in Parsing

The system prompt says: **"Conviction below 60 should be a PASS."**

The coercion layer (`_coerce_tmc_final_decision_output()`):
- Parses conviction as an integer 0-100
- Clamps to [0, 100]
- Handles 0-1 scale → 0-100 conversion
- **Does NOT check whether decision="EXECUTE" with conviction < 60**

This means the model CAN return `{"decision": "EXECUTE", "conviction": 45}` and it will be accepted as a valid EXECUTE. The prompt provides guidance but the code does not enforce it.

The pipeline consumer (`_stage_run_final_model_analysis` in stock_opportunity_runner.py) maps TMC decisions:
```python
cand["model_recommendation"] = "BUY" if decision == "EXECUTE" else "PASS"
cand["model_confidence"] = model_result.get("conviction")
```

There is no downstream conviction check either. An EXECUTE with conviction=30 flows through as BUY with confidence=30.

---

## 9  What the Model Adds vs What's Already Computed

### Output Field Classification

| Field | Classification | Rationale |
|-------|---------------|-----------|
| `decision` (EXECUTE/PASS) | **Uncertain value** | Anchored to engine score; ~87% rubber-stamp; not calibrated against outcomes |
| `conviction` (0-100) | **Uncertain value** | Self-assessed; no external calibration; not enforced as a gate |
| `decision_summary` | **Genuinely new** | Narrative synthesis citing metrics — the pipeline cannot produce this |
| `technical_analysis` | **Mixed** | `key_metrics_cited` is a reformatted subset of input; `setup_quality_assessment` adds narrative assessment |
| `technical_analysis.trend_context` | **Restated** | Rephrases SMA positions already in the input |
| `technical_analysis.momentum_read` | **Restated** | Rephrases RSI/ROC already in the input |
| `technical_analysis.volatility_read` | **Restated** | Rephrases ATR/BB already in the input |
| `technical_analysis.volume_read` | **Restated** | Rephrases volume ratio already in the input |
| `factors_considered` | **Genuinely new** | Factor categorization and weighting is model reasoning; pipeline doesn't produce this |
| `market_alignment` | **Genuinely new** | Cross-engine synthesis is something the pipeline doesn't do |
| `risk_assessment.primary_risks` | **Uncertain value** | Qualitative without stop/target data; quality unknown |
| `risk_assessment.biggest_concern` | **Genuinely new** | Prioritization of risks is model reasoning |
| `risk_assessment.risk_reward_verdict` | **Uncertain value** | No quantitative R:R data available to assess |
| `what_would_change_my_mind` | **Genuinely new** | Reversibility reasoning is unique model output |
| `engine_comparison` | **Restated + Uncertain** | `engine_score` is echo; `model_score` is anchored; `agreement` is decorative |

**Summary**: Of 15 distinct output fields/sub-fields:
- **4 Genuinely new** (decision_summary, factors_considered, market_alignment, what_would_change_my_mind, biggest_concern)
- **5 Restated** (trend_context, momentum_read, volatility_read, volume_read, engine_comparison.engine_score)
- **6 Uncertain value** (decision, conviction, risk_reward_verdict, primary_risks, model_score, agreement)

### Is EXECUTE/PASS Independent from BUY/PASS?

No. The TMC prompt receives the engine's composite_score and thesis — the same inputs that drove the scanner's BUY/PASS. The empirical data shows the model agrees with the engine ~87% of the time. The EXECUTE decision is not genuinely independent.

### What Would You Lose Without the TMC Prompt?

**Lost (genuinely valuable)**:
- Market alignment synthesis — no other component cross-references all 6 engines against a specific trade
- Factor categorization — no other component produces prioritized factor analysis
- `what_would_change_my_mind` — unique reversibility reasoning
- Narrative decision justification for human review

**Not lost (available deterministically)**:
- The EXECUTE/PASS decision itself — a score threshold on composite_score would produce a similar (arguably more consistent) decision
- Technical metric summaries — already available as raw data
- Engine score comparison — trivially computed without LLM

---

## 10  Findings Summary

| ID | Sev | Finding |
|----|-----|---------|
| F-4C-01 | M | Factor categories missing event_risk, portfolio_context, sector; out-of-set categories silently remapped to "trade_setup" |
| F-4C-02 | H | No stop/target/position-size data in input — risk/reward assessment is qualitative guesswork; prompt asks for R:R verdict without providing the numbers |
| F-4C-03 | H | "Conviction below 60 should be a PASS" NOT enforced in coercion layer — EXECUTE with conviction=30 flows through as BUY |
| F-4C-04 | H | Engine composite_score, thesis, and score_breakdown sent directly — model rubber-stamps within ±5 points 87% of the time (empirical data from results files) |
| F-4C-05 | M | No guidance on weighting conflicting engine signals — model improvises when breadth/volatility/flows disagree |
| F-4C-06 | M | Event risk completely absent — no earnings dates, FOMC, or catalyst proximity in prompt framework or factor categories |
| F-4C-07 | L | Decision framework has 5 steps, not the 7 described in architecture docs (event risk and sector missing) |
| F-4C-08 | L | `technical_analysis` sub-fields (trend_context, momentum_read, volatility_read, volume_read) restate input metrics in prose — low value-add |
| F-4C-09 | L | Agreement values differ between TMC ("agree/disagree/partial") and strategy prompt ("agree/disagree/mixed") — inconsistent enum |
| F-4C-10 | L | Fallback conviction fixed at 10 (TMC) vs 20 (strategy) — inconsistent fallback confidence levels |

**Severity distribution**: 3 High, 3 Medium, 4 Low

---

## 11  Cross-Prompt Patterns (4A + 4B + 4C)

1. **Anti-anchoring is absent by design across all three prompt layers.** Engine scores are always sent. The `engine_comparison` / `engine_vs_model` output section makes this an intentional (not accidental) architectural choice, but one that undermines the "independent model assessment" goal.

2. **Conviction/confidence is decorative across all layers.** Strategy prompt: no threshold. TMC prompt: threshold stated but not enforced. Neither prompt layer gates decisions on confidence.

3. **Missing event risk is systemic.** Pass 3 found the event calendar exists but is wired to nothing. Now 4C confirms: neither the TMC prompt's decision framework nor its factor categories include event risk. A "great trade" 2 days before earnings is indistinguishable from one with no catalysts.

4. **Risk/reward is phantom.** The TMC prompt asks the model to assess risk/reward (framework step 3) and produce a `risk_reward_verdict`, but no stop/target/R:R data exists in the input. This is a prompt-schema disconnect — the schema asks for data the prompt cannot support.

5. **Two-system redundancy confirmed.** 4B's strategy prompt produces BUY/PASS. 4C's TMC prompt produces EXECUTE/PASS. The pipeline only runs the TMC prompt (Stage 7), but the strategy prompt remains accessible via API. The TMC prompt is context-richer but strategy-shallower; the strategy prompt is the opposite. Neither subsumes the other.

---

## 12  Recommendations

1. **Enforce conviction < 60 → PASS** in `_coerce_tmc_final_decision_output()`. Add: if decision == "EXECUTE" and conviction < 60, flip to PASS and add a warning.

2. **Add proposed_trade section** to the user prompt with at minimum ATR-based stop and target suggestions, allowing the model to produce quantitative R:R assessment.

3. **Add event_risk to factor categories** and feed event calendar data (earnings, FOMC, ex-div dates) into the user prompt.

4. **Consider withholding engine composite_score from the prompt input** and instead providing only the raw metrics — let the model form its assessment first, then compare post-hoc. This would require restructuring the `engine_comparison` output section.

5. **Add engine conflict guidance** — e.g., "If 2+ engines disagree on direction, increase your PASS threshold to conviction > 70."
