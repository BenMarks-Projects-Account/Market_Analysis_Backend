# Audit 4A — Regime Analysis Prompt Review

**Scope**: The LLM call that produces the independent market regime assessment, consumed by the market_picture for downstream trade decisions.  
**Date**: 2026-03-20  
**Auditor**: Copilot  
**Method**: Full code trace of `analyze_regime()` in `common/model_analysis.py`, the deterministic `regime_service.py`, routing infrastructure, and downstream consumers.

---

## 1. System Prompt — Complete Verbatim Text

**Source**: `common/model_analysis.py` — inline string in `analyze_regime()` (not in a separate constants file).

```
You are an independent market regime analyst for an options trading platform.
You will receive a JSON object with:
  - regime_raw_inputs: raw market data values organized across three domains:
    * Legacy factor data (index prices, moving averages, VIX, yields, breadth, RSI)
    * Structural block pillars (liquidity conditions, cross-asset macro signals)
    * Tape block pillars (breadth/participation engine detail, index trend/momentum)
    * Tactical block pillars (volatility/options structure, flows/positioning, news/sentiment)
  - metadata: timestamp and data-source health information

IMPORTANT RULES:
  1. Do NOT use any precomputed regime labels, scores, or playbook recommendations.
     If a label is needed, infer it yourself from the raw inputs.
  2. All assessments must be derived solely from the raw inputs provided.
  3. If a raw input is null/missing, note it explicitly and reduce confidence.
  4. Your analysis should cover THREE regime dimensions:
     - Structural: Is the background environment supportive, restrictive, or unstable?
     - Tape: Is the broad US market trending, broad, rotational, narrow, or weakening?
     - Tactical: Is the short-term outlook expansionary, stable, compressing, or event-risk?

Return valid JSON only (no markdown, no code fences) with exactly these keys:
  risk_regime_label    – string, one of: 'Risk-On', 'Neutral', 'Risk-Off'
  trend_label          – string, one of: 'Uptrend', 'Sideways', 'Downtrend'
  vol_regime_label     – string, one of: 'Low', 'Moderate', 'High'
  structural_assessment – string, one of: 'Supportive', 'Mixed', 'Restrictive', 'Unstable'
     Your independent read of the macro/liquidity/rates environment.
  tape_assessment       – string, one of: 'Trending', 'Broad', 'Rotational', 'Narrow', 'Weakening'
     Your independent read of US market breadth and participation.
  tactical_assessment   – string, one of: 'Expansionary', 'Stable', 'Compression', 'Event-Risk'
     Your independent read of near-term forward pressure and tradability.
  key_drivers          – string array of 3-5 short bullet points describing the top
     factors driving your regime assessment
  executive_summary    – string, 2-4 sentence overview of the current market regime.
     Reference all three blocks (structural, tape, tactical) in your summary.
  regime_breakdown     – object with keys: structural, tape, tactical, trend, volatility,
     breadth, rates, momentum. Each value is a 2-3 sentence analysis.
  what_works           – string array of 2-4 strategies/approaches that tend to work
     in this regime environment
  what_to_avoid        – string array of 2-4 strategies/approaches to avoid
  primary_fit          – string explaining which options strategies fit this regime
  avoid_rationale      – string explaining which strategies are riskier and why
  change_triggers      – string array of 3-5 specific conditions that would shift
     the regime
  confidence_caveats   – string with confidence level and data-quality caveats
  confidence           – float 0-1 representing your overall confidence
  raw_inputs_used      – object listing each raw input name and value received,
     plus a 'missing' array of input names that were null
Do not include any keys beyond this schema.
```

**Assessment**: The system prompt serves as both the role definition AND the complete output schema specification. There is no separate system vs user prompt format — the system message contains everything.

---

## 2. User Prompt Construction

### What Data Is INCLUDED

The user prompt is the JSON-serialized output of `_extract_regime_raw_inputs()`. It contains:

**Legacy Factor Data (direct from component inputs):**

| Field | Source | Type | Example |
|-------|--------|------|---------|
| `trend_indexes` | `components.trend.inputs.{SPY,QQQ,IWM,DIA}` | `dict[str, dict]` | `{"SPY": {"close": 545, "ema20": 542, ...}}` |
| `spy_price` | `trend.inputs.SPY.close` | `float` | `545.30` |
| `spy_ema20` | `trend.inputs.SPY.ema20` | `float` | `542.10` |
| `spy_ema50` | `trend.inputs.SPY.ema50` | `float` | `538.00` |
| `spy_sma50` | `trend.inputs.SPY.sma50` | `float` | `537.50` |
| `spy_sma200` | `trend.inputs.SPY.sma200` | `float` | `510.00` |
| `vix_spot` | `volatility.inputs.vix` | `float` | `18.5` |
| `vix_5d_change_pct` | `volatility.inputs.vix_5d_change` | `float` | `-3.2` |
| `sectors_above_ema20` | `breadth.inputs.sectors_above_ema20` | `int` | `8` |
| `sectors_total` | `breadth.inputs.sectors_total` | `int` | `11` |
| `pct_sectors_above_ema20` | `breadth.inputs.pct_above_ema20` | `float` | `72.7` |
| `ten_year_yield` | `rates.inputs.ten_year_yield` | `float` | `4.28` |
| `ten_year_5d_change_bps` | `rates.inputs.ten_year_5d_change_bps` | `float` | `-5.0` |
| `avg_rsi14` | `momentum.inputs.avg_rsi14` | `float` | `55.3` |
| `rsi14_per_index` | `momentum.inputs.{SPY,QQQ,IWM,DIA}` | `dict[str, float]` | `{"SPY": 54, "QQQ": 58, ...}` |

**Three-Block Pillar Data (from regime_service output, compacted):**

| Field | Source | Content After `_compact_pillar_detail()` |
|-------|--------|------------------------------------------|
| `block_structural_pillars` | `blocks.structural.pillar_detail` | `{"liquidity": {"score": 72, "label": "Strong"}, "macro": {"score": 65, "label": "Mixed"}, "rates_regime": {"score": 60, "ten_year_yield": 4.28}, "volatility_structure": {"score": 80, "vix": 18.5}}` |
| `block_tape_pillars` | `blocks.tape.pillar_detail` | `{"breadth": {"score": 68, "label": "Moderate"}, "trend_quality": {"score": 75}, "momentum_quality": {"score": 60, "avg_rsi14": 55}, "smallcap_confirmation": {"score": 50}}` |
| `block_tactical_pillars` | `blocks.tactical.pillar_detail` | `{"volatility_options": {"score": 70, "label": "Stable"}, "flows_positioning": {"score": 55, "label": "Neutral"}, "news_sentiment": {"score": 60, "label": "Balanced"}, "rate_pressure": {"score": 65}}` |
| `block_structural_signals` | `blocks.structural.key_signals[:6]` | `["Liquidity conditions supportive...", "Credit spreads stable..."]` |
| `block_tape_signals` | `blocks.tape.key_signals[:6]` | `["Breadth 65% above 50-SMA...", "SPY above 20d EMA..."]` |
| `block_tactical_signals` | `blocks.tactical.key_signals[:6]` | `["VIX term structure normal...", "Put/call ratio benign..."]` |

**Metadata:**

| Field | Source |
|-------|--------|
| `timestamp` | `regime_data.as_of` |
| `source_health` | `regime_data.source_health` |

### What Data Is EXCLUDED (Anti-Anchoring List)

`_REGIME_DERIVED_FIELDS` documents 15 excluded field patterns:

| Excluded Field | What It Contains |
|----------------|------------------|
| `regime_label` | 5-tier label: RISK_ON / RISK_ON_CAUTIOUS / NEUTRAL / RISK_OFF_CAUTION / RISK_OFF |
| `regime_score` | 0-100 weighted composite |
| `confidence` | 0-1 algorithmic confidence |
| `interpretation` | Human-readable summary sentence |
| `suggested_playbook` | Primary strategy + avoid list + notes |
| `what_works` | Strategy list from `_build_what_works_avoids()` |
| `what_to_avoid` | Avoid list from `_build_what_works_avoids()` |
| `change_triggers` | Regime shift conditions |
| `key_drivers` | Top signals from each block |
| `agreement` | Block alignment stats (block_aligned, max_spread, conflict_pairs) |
| `blocks.*.score` | Per-block weighted score (0-100) |
| `blocks.*.label` | Per-block label (e.g., "Supportive", "Trending") |
| `blocks.*.confidence` | Per-block confidence |
| `components.*.score` | Per-component normalized score |
| `components.*.raw_points` | Per-component raw scoring points |
| `components.*.signals` | Per-component human-readable signal descriptions |

### Data Format

- User data is `json.dumps()` of `{"regime_raw_inputs": {...}, "metadata": {...}}`
- No indent (compact JSON)
- Budget-capped at 4,000 characters (see Section 6)

### Output Schema Requested

17 fields requested (see system prompt above). The schema is well-constrained with:
- Enumerated label values for 6 classification fields
- Specific array length guidance (3-5 items for key_drivers, 2-4 for what_works/what_to_avoid, 3-5 for change_triggers)
- Typed constraints (confidence: float 0-1)
- "Do not include any keys beyond this schema" — closed schema instruction

---

## 3. Anti-Anchoring Effectiveness

### What's Successfully Excluded

The extraction function `_extract_regime_raw_inputs()` deliberately:
- Does NOT read `regime_data["regime_label"]` — the 5-tier composite label
- Does NOT read `regime_data["regime_score"]` — the 0-100 composite
- Does NOT read `regime_data["confidence"]` — the algorithmic confidence
- Does NOT read `regime_data["suggested_playbook"]` — the strategy recommendations
- Does NOT read `regime_data["what_works"]` or `regime_data["what_to_avoid"]`
- Does NOT read `regime_data["blocks"][*]["score"]` — per-block scores
- Does NOT read `regime_data["blocks"][*]["label"]` — per-block labels
- Does NOT read `regime_data["components"][*]["score"]` — per-component scores

A runtime verification check scans the serialized user_data for 6 forbidden key names and logs `LEAK DETECTED` errors.

### What LEAKS Through — Pillar-Level Scores & Labels

**Critical finding**: `_compact_pillar_detail()` keeps the `score` and `label` fields from each pillar within a block.

The `_KEEP_KEYS` set explicitly includes `"score"` and `"label"`:

```python
_KEEP_KEYS = {"label", "score", "value", "weight", "tone", "spread",
              "level", "direction", "status", "signal", "pct", "delta"}
```

This means the model receives data like:

```json
{
  "block_structural_pillars": {
    "liquidity": {"score": 72, "label": "Strong"},
    "macro": {"score": 65, "label": "Mixed"},
    "rates_regime": {"score": 60},
    "volatility_structure": {"score": 80}
  }
}
```

**These pillar scores ARE engine-derived values.** They come from the MI engines (liquidity_financial_conditions, cross_asset_macro, etc.) and from deterministic scoring functions (`_score_rates_regime()`, `_score_volatility_structure()`). They are processed, normalized 0-100 scores — not raw market data.

### What LEAKS Through — Key Signals

`key_signals` contains narrative descriptions extracted from MI engine outputs:
- `trader_takeaway` field from each engine
- `summary` field
- Up to 2 items each from `bull_factors`, `bear_factors`, `risks`

These are engine-produced narrative interpretations, not raw data. Examples:
- `"Breadth 65% of S&P 500 above 50-SMA, leadership expanding"`
- `"VIX term structure normal, call skew benign"`

While they're truncated to 120 chars and capped at 6 per block, they communicate the engine's assessment in natural language.

### Anti-Anchoring Verification Gap

The runtime leak check only scans for 6 specific key names:
```python
for forbidden in ("regime_label", "regime_score", "suggested_playbook", 
                   "interpretation", "what_works", "what_to_avoid"):
```

It does NOT scan for:
- `"score"` appearing as a nested value in pillar_detail (would false-positive on raw values anyway)
- `"label"` appearing in pillar descriptions
- Block-level `"confidence"` values

The `_REGIME_DERIVED_FIELDS` documentation list includes `"blocks.*.score"` and `"blocks.*.label"` but these refer to the block-level aggregates, not the pillar-level scores that actually leak through.

### Could the Model Infer Engine Scores?

**Yes, trivially.** Even without the pillar scores, the raw data (VIX at 18.5, RSI at 55, 8/11 sectors above EMA20) maps directly to the deterministic scoring rules. The regime_service scoring is simple threshold-based logic that any capable LLM could replicate from the raw inputs alone. The pillar scores just make it easier.

### Anti-Anchoring Assessment

| Aspect | Status | Detail |
|--------|--------|--------|
| Top-level regime label/score | ✅ Excluded | `regime_label`, `regime_score` not sent |
| Block-level scores/labels | ✅ Excluded | `blocks.*.score`, `blocks.*.label` not sent |
| Playbook recommendations | ✅ Excluded | `suggested_playbook`, `what_works`, `what_to_avoid` not sent |
| Component-level scores | ✅ Excluded | `components.*.score`, `components.*.raw_points` not sent |
| **Pillar-level scores** | ❌ **INCLUDED** | `pillar_detail.*.score` (e.g., `liquidity: 72`) sent via `_compact_pillar_detail()` |
| **Pillar-level labels** | ❌ **INCLUDED** | `pillar_detail.*.label` (e.g., `liquidity: "Strong"`) sent |
| **Engine-derived key_signals** | ❌ **INCLUDED** | Narrative descriptions from engine outputs (truncated to 120 chars) |
| Raw market data | ✅ Properly raw | Prices, MAs, VIX, yields, RSI, sector counts — genuinely raw |

**Verdict**: Anti-anchoring is **partial**. The top-level and block-level derived values are successfully excluded. But pillar-level scores and labels (which are direct MI engine outputs) and key_signals (which are engine-generated narratives) leak through. The model sees ~10-15 engine-derived scores and ~12-18 engine-generated signal descriptions alongside the raw data. This significantly compromises the "raw-inputs only" claim.

---

## 4. Output Schema Analysis

### Fields the Model Returns (17 total)

| # | Field | Type | Independent? | Deterministic Equivalent |
|---|-------|------|-------------|-------------------------|
| 1 | `risk_regime_label` | `str` enum (3 values) | Partially — model sees pillar scores | `regime_label` (5 values, finer granularity) |
| 2 | `trend_label` | `str` enum (3 values) | ⚠️ — raw data sufficient to infer | Implicit in tape block label |
| 3 | `vol_regime_label` | `str` enum (3 values) | ⚠️ — VIX value directly provided | Implicit in tactical block |
| 4 | `structural_assessment` | `str` enum (4 values) | ⚠️ — pillar scores included | `blocks.structural.label` (4 values, same enum) |
| 5 | `tape_assessment` | `str` enum (5 values) | ⚠️ — pillar scores included | `blocks.tape.label` (5 values, same enum) |
| 6 | `tactical_assessment` | `str` enum (4 values) | ⚠️ — pillar scores included | `blocks.tactical.label` (4 values, same enum) |
| 7 | `key_drivers` | `str[]` (3-5 items) | ✅ Free-form narrative | `key_drivers` (top signal per block) |
| 8 | `executive_summary` | `str` (2-4 sentences) | ✅ Free-form narrative | `interpretation` (1 sentence) |
| 9 | `regime_breakdown` | `obj` (8 sub-analyses) | ✅ Rich narrative | No equivalent — regime_service has no per-dimension prose |
| 10 | `what_works` | `str[]` (2-4 items) | ✅ Free-form | `what_works` (2-4 items, deterministic) |
| 11 | `what_to_avoid` | `str[]` (2-4 items) | ✅ Free-form | `what_to_avoid` (2-4 items, deterministic) |
| 12 | `primary_fit` | `str` | ✅ Free-form | No direct equivalent |
| 13 | `avoid_rationale` | `str` | ✅ Free-form | No direct equivalent |
| 14 | `change_triggers` | `str[]` (3-5 items) | ✅ Free-form | `change_triggers` (4-5 items, deterministic) |
| 15 | `confidence_caveats` | `str` | ✅ Unique to model | No equivalent |
| 16 | `confidence` | `float` 0-1 | Partially | `confidence` (0.1-0.95, deterministic) |
| 17 | `raw_inputs_used` | `obj` | N/A (diagnostic) | No equivalent |

### Independence Classification

| Category | Fields | Assessment |
|----------|--------|------------|
| **Duplicated with deterministic** | `risk_regime_label`, `structural_assessment`, `tape_assessment`, `tactical_assessment`, `what_works`, `what_to_avoid`, `change_triggers`, `confidence` | 8 of 17 fields. Model and engine both produce these independently, but the model sees pillar-level data that heavily guides it toward the same answer |
| **Genuinely unique to model** | `executive_summary`, `regime_breakdown`, `primary_fit`, `avoid_rationale`, `confidence_caveats` | 5 of 17 fields. The deterministic regime_service produces no equivalent prose analysis |
| **Partially independent** | `trend_label`, `vol_regime_label`, `key_drivers` | 3 fields. Engine doesn't produce exact equivalents but the information is derivable from block labels |
| **Diagnostic only** | `raw_inputs_used` | 1 field. No analytical value |

### What the Model Adds That the Deterministic Service CANNOT

1. **Multi-paragraph regime narrative** (`regime_breakdown`, `executive_summary`): The regime_service produces a 1-sentence `interpretation`. The model produces detailed prose analysis across 8 dimensions. This is genuine value — no deterministic logic can produce contextualized market commentary.

2. **Strategy fit explanation** (`primary_fit`, `avoid_rationale`): The regime_service produces static strategy lists (e.g., "Premium selling on defined-risk bullish spreads"). The model can explain *why* specific strategies fit this particular combination of conditions.

3. **Data quality narrative** (`confidence_caveats`): The deterministic confidence is a number; the model can articulate what data gaps mean for reliability.

4. **Cross-dimension synthessis**: The model can detect when structural conditions contradict tape signals and explain the implications, whereas the regime_service only measures `max_spread` between block scores.

### What the Deterministic Service Produces That the Model ALSO Produces

| Overlap Field | Deterministic | Model | Risk |
|---------------|--------------|-------|------|
| Risk regime label | 5-tier (RISK_ON through RISK_OFF) | 3-tier (Risk-On / Neutral / Risk-Off) | Labels use different granularity — comparison loses nuance |
| Block assessments | Threshold-based from weighted scores | From pillar scores + raw data | Model likely reproduces engine labels since it sees pillar scores |
| What works / what to avoid | Static rule-based lists (~2-4 items) | Free-form (~2-4 items) | Model's version is richer but covers same ground |
| Change triggers | Rule-based (~4-5 items) | Free-form (~3-5 items) | Same concept, parallel generation |
| Confidence | Algorithmic with conflict penalty | Model self-assessment | Different methodology, potentially useful for comparison |
| Key drivers | Top signal from each block | Model's own driver synthesis | Model sees key_signals in input, may just restate them |

---

## 5. Prompt Quality Assessment

### Role Clarity
**Grade: B+**. The prompt clearly states "You are an independent market regime analyst for an options trading platform." The word "independent" signals that the model should form its own view, though it doesn't explain independent *from what*. The model doesn't know what BenTrade's deterministic regime_service already computed — so "independent" is somewhat hollow without that context.

### Instruction Specificity
**Grade: A-**. The 4 "IMPORTANT RULES" are clear and actionable:
1. Don't use precomputed labels/scores — clear prohibition
2. Derive from raw inputs — clear scope
3. Handle nulls — explicit guidance
4. Cover three dimensions — specific structure

### Conflicting Instructions
**Grade: B**. One potential conflict:
- Rule 1 says "Do NOT use any precomputed regime labels, scores"
- But the user data INCLUDES pillar-level scores and labels via `_compact_pillar_detail()`
- The model is told not to use scores but is given scores in the data

This is an instruction-data conflict. The model may or may not honor the instruction when the data contradicts it.

### Output Schema Definition
**Grade: A**. The schema is well-defined:
- 17 fields with clear types (string, float, array, object)
- Enumerated values for classification fields
- Array length guidance (3-5, 2-4)
- Closed schema ("Do not include any keys beyond this schema")

### Hallucination Guards
**Grade: B-**. The prompt says:
- "All assessments must be derived solely from the raw inputs provided" — good
- "If a raw input is null/missing, note it explicitly and reduce confidence" — good

Missing:
- No instruction to avoid inventing data points not in the inputs
- No instruction to avoid describing market events not evidenced in the data
- No instruction about what to do if the raw data is contradictory
- The model is asked to produce `what_works` and `what_to_avoid` strategy recommendations, but there's no constraint on what strategies to consider (could hallucinate exotic strategies)

---

## 6. Token Budget

### User Prompt Size

Approximate character counts for the user data payload:

| Component | Chars | Notes |
|-----------|-------|-------|
| Legacy factor data (SPY/QQQ/IWM/DIA trend indexes) | ~800-1200 | 4 indexes × ~5 fields each |
| SPY individual fields | ~150 | spy_price, ema20, ema50, sma50, sma200 |
| VIX fields | ~60 | vix_spot, vix_5d_change_pct |
| Breadth fields | ~80 | sectors_above, total, pct |
| Rates fields | ~80 | ten_year_yield, 5d_change_bps |
| RSI fields | ~120 | avg_rsi14 + per_index |
| Block pillar data (3 blocks × 4 pillars) | ~600-900 | Compacted to scalar fields only |
| Block signals (3 blocks × 5 signals) | ~900-1800 | Truncated to 120 chars each |
| Metadata | ~100 | timestamp, source_health |
| JSON overhead | ~200 | Keys, braces, quotes |
| **Total** | **~3,000-4,500** | |

**Token estimate**: At ~4 chars/token, this is approximately **750-1,125 tokens** for the user prompt. The system prompt is approximately **~500 tokens**.

### Token Budget Cap

```python
_MAX_USER_DATA_CHARS = 4000
```

When user data exceeds 4,000 characters, progressive trimming is applied:
1. Drop non-SPY trend indexes (QQQ, IWM, DIA price data)
2. Drop RSI per-index detail

This is a reasonable budget. The prompt + user data fits well within any modern LLM's context window.

### Response Token Budget

```python
"max_tokens": 4096
```

**Assessment**: 4096 tokens is generous for the 17-field JSON response. The `regime_breakdown` object (8 sub-analyses of 2-3 sentences each) is the largest component at ~300-500 tokens. Total response is typically ~2000-3000 tokens. **No truncation risk** under normal conditions.

The transport layer logs a warning when `finish_reason == "length"`, providing observability for truncation events.

---

## 7. Model Configuration

| Parameter | Value | Source |
|-----------|-------|--------|
| **Temperature** | `0.0` | Hardcoded in `analyze_regime()` payload |
| **max_tokens** | `4096` | Hardcoded in `analyze_regime()` payload |
| **stream** | `False` | Forced by transport layer |
| **Timeout** | `180s` (default) | Parameter, overridable |
| **Retries** | `0` (default), `1` on route handler | Parameter |
| **Model (local)** | Whatever is loaded in LM Studio | Via `model_sources.py` → `http://localhost:1234/v1/chat/completions` |
| **Model (Bedrock)** | `us.amazon.nova-pro-v1:0` | Via `config.py` `BEDROCK_MODEL_ID` |
| **Routing** | Distributed routing tried first, legacy HTTP fallback | Via `_model_transport()` |

**Model selection**: The system uses a generic OpenAI-compatible endpoint. The actual model depends on:
- **Local path**: Whatever model the user loads in LM Studio (not specified)
- **Bedrock path**: Amazon Nova Pro v1 (when `BEDROCK_ENABLED=True`)
- **Model Machine**: Same as local but on a network machine

The regime analysis prompt does NOT specify a model preference. All prompt types use the same endpoint. There is no task-type → model mapping (e.g., "use Claude for regime, Haiku for per-stock").

**Temperature 0.0**: Good choice for classification tasks. Maximizes determinism and reproducibility. With the same inputs, the model should produce consistent labels across runs (though not guaranteed with all providers).

---

## 8. Value-Add Assessment

### What the Deterministic Regime Service Already Produces

| Output | Method | Quality |
|--------|--------|---------|
| `regime_label` (5-tier) | Weighted score → threshold bucketing | ✅ Deterministic, consistent, fast |
| `regime_score` (0-100) | 30/40/30 weighted average of block scores | ✅ Auditable, stable |
| `blocks.*.score` + `blocks.*.label` | Per-block weighted pillar scoring | ✅ Transparent |
| `confidence` (0-1) | Base confidence with conflict penalties | ✅ Mechanical |
| `what_works` / `what_to_avoid` | Rule-based lookup from label + block labels | ⚠️ Static, limited to ~10 templates |
| `change_triggers` | Rule-based lookup from label | ⚠️ Static, ~4 templates per label |
| `key_drivers` | Top signal from each block | ⚠️ Just relays engine signals |
| `interpretation` | One-line template | ⚠️ Minimal narrative |
| `agreement` | Block conflict detection | ✅ Useful for diagnosing mixed signals |

### What the LLM Adds

| Output | Value-Add | Replaceable by Deterministic? |
|--------|-----------|-------------------------------|
| `executive_summary` | Multi-sentence coherent narrative | ❌ No — requires natural language generation |
| `regime_breakdown` (8 dimensions) | Per-dimension prose analysis | ❌ No — requires interpretation of data patterns |
| `primary_fit` | Contextualized strategy recommendation | ⚠️ Partially — could use richer templates |
| `avoid_rationale` | Contextualized warning | ⚠️ Partially — could use richer templates |
| `confidence_caveats` | Data quality narrative | ⚠️ Partially — could template common caveats |
| `risk_regime_label` (3-tier) | Independent regime classification | ✅ Yes — deterministic already has 5-tier version |
| `structural/tape/tactical_assessment` | Independent block labels | ✅ Yes — deterministic produces same labels |
| `what_works` / `what_to_avoid` | Free-form strategy guidance | ⚠️ Partially — richer than templates but same concepts |
| `change_triggers` | Free-form regime shift conditions | ⚠️ Partially — richer than templates but same concepts |
| `key_drivers` | Narrative driver synthesis | ⚠️ Partially — model may just restate key_signals from input |

### Cost-Benefit Analysis

**Cost per call**:
- ~1,500 input tokens + ~2,500 output tokens = ~4,000 tokens
- At Bedrock Nova Pro pricing: ~$0.003-0.005 per call
- At local LM Studio: Free (but ~5-30 seconds latency)
- Called once per regime analysis request (on-demand, not automatic)

**Benefits**:
- 5 fields (executive_summary, regime_breakdown, primary_fit, avoid_rationale, confidence_caveats) provide genuine narrative value that deterministic logic cannot produce
- Engine-vs-model comparison table enables independent validation of algorithmic regime labels
- Contextual strategy guidance is richer than static templates

**Could the LLM be replaced by deterministic logic?**

For the 8 classification/list fields (labels, what_works, what_to_avoid, change_triggers, key_drivers, confidence): **Yes**, the deterministic service already produces equivalent or better versions.

For the 5 narrative fields: **No**, not without degrading to template strings. However, the question is whether the narrative provides actionable insight beyond what the labels already communicate.

**Bottom line**: The regime analysis LLM call is **marginally valuable** for its narrative output and as an independent validation check. The 8 overlapping classification fields represent wasted compute. A leaner prompt design could request ONLY the 5 narrative fields + confidence, accept the deterministic labels as ground truth, and halve the output token budget.

---

## Findings

### F-4A-01 [HIGH] — Pillar-Level Scores and Labels Leak Into "Raw-Only" Prompt

**What**: The anti-anchoring extraction function `_extract_regime_raw_inputs()` excludes top-level and block-level derived values but includes pillar-level scores and labels via `_compact_pillar_detail()`. The `_KEEP_KEYS` set explicitly includes `"score"` and `"label"`. This means the model receives ~10-15 engine-derived scores (e.g., `liquidity: {"score": 72, "label": "Strong"}`) alongside the raw data.

**Where**: `model_analysis.py` `_compact_pillar_detail()` L503-516 — `_KEEP_KEYS` includes `"score"` and `"label"`

**Impact**: The model's "independent" assessment is significantly anchored by engine-computed pillar scores. When the model sees `breadth: {"score": 68, "label": "Moderate"}`, it's unlikely to independently conclude a different assessment. The anti-anchoring claim ("raw-inputs only, no anchoring") documented in the architecture is **not accurate**.

**Evidence**: The exclusion list `_REGIME_DERIVED_FIELDS` documents `blocks.*.score` and `blocks.*.label` as excluded, but these refer to block-level aggregates. The pillar-level scores within `pillar_detail` are a different path that bypasses the exclusion.

**Recommendation**: Remove `"score"` and `"label"` from `_KEEP_KEYS` in `_compact_pillar_detail()`. If pillar context is needed, include only raw scalar values (`value`, `pct`, `delta`, `level`, `spread`) that represent market data rather than computed assessments.

---

### F-4A-02 [HIGH] — Key Signals Are Engine-Generated Narratives, Not Raw Data

**What**: Each block's `key_signals` list is extracted from MI engine outputs (`trader_takeaway`, `summary`, `bull_factors`, `bear_factors`, `risks`). These are engine-generated narrative interpretations that communicate the engine's assessment in natural language. Up to 18 signals (6 per block × 3 blocks) are included in the prompt, each truncated to 120 characters.

**Where**: `model_analysis.py` L496-500 — `key_signals` forwarded as `block_{block_key}_signals`

**Impact**: Even if pillar scores were removed, key_signals like "Breadth 65% of S&P 500 above 50-SMA, leadership expanding" tell the model what the engine concluded. The model would need to be remarkably strong-willed to form a contradictory view when given these pre-formed assessments.

**Recommendation**: Either (a) exclude key_signals entirely and let the model work from raw numbers only, or (b) acknowledge that the regime analysis is "engine-informed" rather than "independent" and adjust the architecture doc accordingly.

---

### F-4A-03 [MEDIUM] — 8 of 17 Output Fields Duplicate Deterministic Regime Service

**What**: The model produces `risk_regime_label`, `structural_assessment`, `tape_assessment`, `tactical_assessment`, `what_works`, `what_to_avoid`, `change_triggers`, and `confidence` — all of which the deterministic regime_service already computes. The model's versions are used for the "engine vs model" comparison table, but 47% of the model's output is spent reproducing what exists algorithmically.

**Where**: System prompt output schema; compared against `regime_service.py` output

**Impact**: Wasted tokens (~40% of output). The model is asked to classify the regime, produce strategy lists, and generate change triggers — all of which the deterministic service does faster, cheaper, and more consistently.

**Recommendation**: Restructure the prompt to output only the 5 genuinely unique fields (executive_summary, regime_breakdown, primary_fit, avoid_rationale, confidence_caveats) plus a confidence score. Accept the deterministic labels as ground truth. If the comparison table is valued, keep the 3 main labels (risk_regime, trend, vol) but drop what_works/what_to_avoid/change_triggers from the model output.

---

### F-4A-04 [MEDIUM] — `playbook_data` Parameter Accepted but Never Used

**What**: The `analyze_regime()` function signature accepts `playbook_data: dict[str, Any] | None = None`, and the route handler passes it through. However, the function body never references `playbook_data` — it's silently ignored.

**Where**: `model_analysis.py` L759 — parameter declaration; never referenced in function body

**Impact**: The API route sets `playbook_data=payload.get("playbook")` creating the false impression that playbook context enriches the analysis. Dead parameter, misleading contract.

**Recommendation**: Either remove the parameter or implement the intended playbook enrichment.

---

### F-4A-05 [MEDIUM] — Leak Verification Check Is Incomplete

**What**: The runtime verification check scans the serialized user_data for 6 specific key names:
```python
for forbidden in ("regime_label", "regime_score", "suggested_playbook", 
                   "interpretation", "what_works", "what_to_avoid"):
```
This misses 9 fields from the documented `_REGIME_DERIVED_FIELDS` list, including `"confidence"`, `"agreement"`, `"key_drivers"`, and all wildcard patterns (`blocks.*.score`, `components.*.signals`). More critically, it cannot detect the pillar-level score/label leak because those appear as nested values under different key names.

**Where**: `model_analysis.py` — the `for forbidden in (...)` block inside `analyze_regime()`

**Impact**: The verification check provides false confidence. It would log `LEAK DETECTED` if someone accidentally added `what_works` to the raw inputs, but it cannot detect the actual pillar-score leak that currently exists.

**Recommendation**: Either expand the check to cover all documented exclusions (including nested patterns), or replace it with a positive-list approach: define exactly what keys ARE allowed in the raw inputs, and reject anything else.

---

### F-4A-06 [MEDIUM] — Risk Regime Label Granularity Mismatch

**What**: The deterministic regime_service produces a 5-tier label (`RISK_ON`, `RISK_ON_CAUTIOUS`, `NEUTRAL`, `RISK_OFF_CAUTION`, `RISK_OFF`). The model is asked to produce a 3-tier label (`Risk-On`, `Neutral`, `Risk-Off`). The comparison table maps these side-by-side, but the different granularity makes the comparison misleading. A deterministic `RISK_ON_CAUTIOUS` is neither `Risk-On` nor `Neutral` from the model's perspective.

**Where**: System prompt schema (3 enum values) vs `regime_service.py` L22-26 (5 enum values)

**Impact**: The engine-vs-model comparison at the regime label level conflates two different classification systems. Agreement between "Risk-On" (model) and "RISK_ON_CAUTIOUS" (engine) appears as a match when it arguably isn't.

**Recommendation**: Either expand the model's enum to 5 tiers to match the deterministic service, or explicitly define the mapping (e.g., `RISK_ON_CAUTIOUS` → `Risk-On` with caveat).

---

### F-4A-07 [LOW] — No Specific Model Selection for Regime Analysis

**What**: The regime analysis uses whatever model is configured in the model_sources endpoint (LM Studio local model or Bedrock Nova Pro). There is no task-type → model mapping. A complex regime synthesis task uses the same model as a simple stock scoring task.

**Where**: `_model_transport()` routes all task types through the same endpoint

**Impact**: Low for current architecture (single model), but means the regime analysis quality varies based on whatever model the user loads locally. No guarantee that the local model can handle the 17-field structured JSON output reliably.

**Recommendation**: Document minimum model requirements for regime analysis (e.g., "requires instruction-following model with >8K context, good JSON output adherence").

---

### F-4A-08 [LOW] — System Prompt Inlined Rather Than Externalized

**What**: The complete ~500-token system prompt is a multi-line string literal inside the `analyze_regime()` function body. It's not in a constants file, not unit-testable independently, and not versioned separately from the function logic.

**Where**: `model_analysis.py` — inline string in `analyze_regime()` body

**Impact**: Minor maintainability concern. The prompt is stable and well-written, but changes require editing deep inside a 3000+ line file. Other prompt systems in the codebase (stock_strategy_prompts, tmc_final_decision_prompts) are externalized to dedicated modules.

**Recommendation**: Extract to a `common/regime_prompt.py` or add to `common/stock_strategy_prompts.py` for consistency with the pattern used by other prompt types.

---

### F-4A-09 [LOW] — Regime Analysis Is On-Demand Only, Not Pipeline-Integrated

**What**: `analyze_regime()` is called via the `/api/model/analyze_regime` route — triggered by the frontend when the user navigates to the regime analysis view. It is NOT called automatically during the MI workflow pipeline (which has its own separate `_stage_run_model_interpretation` with a different prompt).The stock and options pipelines consume `consumer_summary` from the MI output, not the regime analysis model output. The regime LLM analysis is effectively a standalone diagnostic tool that doesn't feed into trade decisions.

**Where**: `routes_reports.py` L305 — sole call site (excluding tests); MI runner Stage 4 uses `_stage_run_model_interpretation` instead

**Impact**: The "feeds into the market_picture consumed by downstream trade decisions" description of this audit is partially inaccurate. The regime LLM analysis feeds into the dashboard comparison table, but trade pipelines use the deterministic regime_service output, not the model's assessment. This means even if the model disagrees with the engine, trade decisions follow the engine.

**Recommendation**: Clarify the architecture: the regime LLM analysis is an independent validation/diagnostic tool, not a decision input. If model disagreement should influence trade decisions, a feedback loop needs to be built.

---

## Summary

| Severity | Count | Findings |
|----------|-------|----------|
| **HIGH** | 2 | F-4A-01 (pillar scores leak), F-4A-02 (key_signals are engine narratives) |
| **MEDIUM** | 4 | F-4A-03 (47% output overlap), F-4A-04 (dead playbook_data param), F-4A-05 (incomplete leak check), F-4A-06 (label granularity mismatch) |
| **LOW** | 3 | F-4A-07 (no model selection), F-4A-08 (inline prompt), F-4A-09 (on-demand not pipeline) |
| **Total** | **9** | |

### Overall Assessment

**Prompt quality**: B+. Well-structured output schema, clear role definition, reasonable token budget, temperature 0.0 for determinism. Weaknesses: inline prompt, no explicit hallucination guards for strategy recommendations.

**Anti-anchoring effectiveness**: **Partial failure.** Top-level and block-level derived values are properly excluded, but pillar-level scores (the actual engine decisions at the individual metric level) and engine-generated narrative signals leak through. The model's "independent" assessment is substantially informed by engine outputs. The runtime leak verification is incomplete and cannot detect the actual leak path.

**Genuine value-add**: **Moderate.** 5 of 17 output fields (executive_summary, regime_breakdown, primary_fit, avoid_rationale, confidence_caveats) provide genuine narrative value that deterministic logic cannot replicate. The remaining 8 classification/list fields duplicate what the regime_service already produces, and 4 are partially derivative. The model's most important output — the engine-vs-model comparison — is undermined by the anti-anchoring leak.

**Bottom line**: The regime analysis prompt is well-engineered at the prompt/schema level but has a fundamental design tension: it claims to provide independent assessment while receiving engine-derived data that heavily guides its conclusions. The comparison table (engine vs model) is the primary value proposition, but its validity depends on true independence — which the pillar-score and key_signal leaks compromise.
