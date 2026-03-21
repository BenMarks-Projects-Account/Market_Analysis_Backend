# Audit 4E — Model Routing, Configuration & Output Parsing

**Scope**: Cross-cutting review of how LLM calls are routed, configured, and their outputs parsed/coerced across all prompt types  
**Date**: 2025-07-18  
**Status**: Complete  

---

## 1  Files Examined

| File | Role |
|------|------|
| `app/services/model_routing_integration.py` L55-530 | `execute_routed_model()`, `routed_tmc_final_decision()`, `routed_model_interpretation()`, routing kill switch |
| `app/services/model_router.py` L44-214 | `get_model_endpoint()`, `model_request()`, `execute_with_provider()`, `route_and_execute()` |
| `app/services/model_router_policy.py` | Policy engine: probes, eligibility, candidate ranking, dispatch |
| `app/services/model_routing_config.py` | `RoutingConfig` dataclass, env var kill switch, concurrency settings |
| `app/services/model_routing_contract.py` | `ExecutionMode`, `Provider`, `ExecutionRequest`, `ExecutionTrace` enums/dataclasses |
| `app/services/model_provider_registry.py` | Singleton provider registry, status snapshots |
| `app/services/model_provider_adapters.py` | LocalhostLLM, NetworkModelMachine, Bedrock adapters |
| `app/model_sources.py` | Legacy source registry (`local`, `model_machine`, `premium_online`) |
| `common/model_analysis.py` L40-260, L309-425, L1087-1255, L1436-1640, L1643-1820, L2795-2820 | `_model_transport()`, all `_coerce_*` functions, think-tag stripping, legacy `_extract_json_payload()` |
| `common/json_repair.py` | `extract_and_repair_json()` — 5-stage JSON repair pipeline |
| `app/services/active_trade_pipeline.py` L618-860 | `_default_model_executor`, `_routed_model_executor`, `run_model_analysis()` |
| `app/api/routes_active_trades.py` L840-1210 | On-demand model analysis, `_sanitize_model_analysis()`, legacy fallback |

---

## 2  Routing Architecture

### The Routing Stack

```
  Call site (analyze_*, routed_*, pipeline, routes)
       │
       ▼
  _model_transport()  OR  execute_routed_model() directly
       │
       ▼
  execute_routed_model()  [model_routing_integration.py L155]
       │
       ├─ _routing_is_enabled()? ─── No ──→ RoutingDisabledError → legacy fallback
       │
       ▼
  resolve_effective_execution_mode()  (premium > caller > UI-selected > default)
       │
       ▼
  ExecutionRequest built → route_and_execute()  [model_router.py L196]
       │
       ▼
  model_router_policy.route_and_execute()  [policy engine]
       │
       ├─ Probe all candidates (cached per routing cycle)
       ├─ Check eligibility (config + state + gate capacity)
       ├─ Acquire execution slot (max_concurrency=1 per provider)
       ├─ Dispatch to provider adapter
       └─ On failure: advance to next provider or exhaust chain
       │
       ▼
  ProviderResult → adapt_to_legacy() → (legacy_result_dict, ExecutionTrace)
```

### Execution Modes

| Mode | Provider Order | Use Case |
|------|---------------|----------|
| `local` | localhost_llm only | Direct local LM Studio |
| `model_machine` | network_model_machine only | Direct network LM Studio |
| `local_distributed` | localhost → model_machine | **Default** — tries local, falls back to network |
| `online_distributed` | localhost → model_machine → bedrock | TMC decision path (premium) |
| `premium_online` | bedrock only | No candidates (placeholder) |

### Routing Kill Switch

`_routing_is_enabled()` in `model_routing_integration.py` L57-66 reads `RoutingConfig.routing_enabled` (env var `ROUTING_ENABLED`, default `True`). Re-read per request — no caching. When disabled, `RoutingDisabledError` is raised and callers fall through to legacy HTTP.

---

## 3  Transport Paths

### Path A: `_model_transport()` (11 callers)

Used by all `analyze_*` functions in `model_analysis.py`:

| Function | task_type |
|----------|-----------|
| `analyze_regime` | regime_analysis |
| `analyze_stock_idea` | stock_idea |
| `analyze_stock_strategy` | stock_strategy |
| `analyze_tmc_final_decision` | tmc_final_decision |
| `analyze_news_sentiment` | news_sentiment |
| `analyze_breadth_participation` | breadth_participation |
| `analyze_volatility_options` | volatility_options |
| `analyze_cross_asset_macro` | cross_asset_macro |
| `analyze_flows_positioning` | flows_positioning |
| `analyze_liquidity_conditions` | liquidity_conditions |
| Format-enforcement retry | varies |

**Flow**: Try routing → on failure/disabled → legacy HTTP via `model_request()`.

### Path B: Direct `execute_routed_model()` callers (bypass `_model_transport`)

| Caller | Context |
|--------|---------|
| `routed_tmc_final_decision()` (model_routing_integration.py L311) | TMC with `online_distributed` mode, own retry-with-fix, falls back to `analyze_tmc_final_decision()` |
| `routed_model_interpretation()` (model_routing_integration.py L538) | MI runner wrapper, rewraps into OpenAI `choices` shape |
| `_routed_model_executor()` (active_trade_pipeline.py L726) | Pipeline reassessment, falls back to `_default_model_executor` |
| `routes_active_trades.py` endpoints (L840, L1321) | On-demand monitor narrative + model analysis, async legacy fallback |

### Finding F-4E-01 (M): Two Parallel Transport Paths

Every call site must independently decide whether to use `_model_transport()` or call `execute_routed_model()` directly. The four Path B callers each implement their own routing → legacy fallback logic:
- `routed_tmc_final_decision`: checks `_routing_is_enabled()` itself, calls `analyze_tmc_final_decision()` as fallback
- `_routed_model_executor`: catches all exceptions, delegates to `_default_model_executor`
- `routes_active_trades.py`: catches `RoutingDisabledError` + general exceptions, falls back to direct HTTP
- `routed_model_interpretation`: catches `RoutingDisabledError`, follows different fallback Duplicated fallback patterns across 4+ call sites.

---

## 4  Model Configuration Inventory

### Complete Configuration Table

| Call Site | task_type | max_tokens | temp | timeout | Model Name |
|-----------|-----------|-----------|------|---------|------------|
| `analyze_regime` | regime_analysis | 4096 | 0.0 | 180s | via `get_model_endpoint()` |
| `analyze_stock_idea` | stock_idea | 1800 | 0.0 | 180s | via `get_model_endpoint()` |
| `analyze_stock_strategy` | stock_strategy | 2048 | 0.0 | 180s | via `get_model_endpoint()` |
| stock_strategy retry | stock_strategy_fix | 2048 | 0.0 | 180s | same |
| `analyze_tmc_final_decision` | tmc_final_decision | 3000 | 0.0 | 180s | via `get_model_endpoint()` |
| TMC retry | tmc_final_decision_fix | 3000 | 0.0 | 180s | same |
| `routed_tmc_final_decision` | tmc_final_decision | 3000 | 0.0 | passthrough | via routing |
| `analyze_news_sentiment` | news_sentiment | 3500 | 0.0 | 180s | via `get_model_endpoint()` |
| `analyze_breadth` | breadth_participation | 3500 | 0.0 | 180s | via `get_model_endpoint()` |
| `analyze_volatility` | volatility_options | 3500 | 0.0 | 180s | via `get_model_endpoint()` |
| `analyze_cross_asset` | cross_asset_macro | 3500 | 0.0 | 180s | via `get_model_endpoint()` |
| `analyze_flows` | flows_positioning | 3500 | 0.0 | 180s | via `get_model_endpoint()` |
| `analyze_liquidity` | liquidity_conditions | 3500 | 0.0 | 180s | via `get_model_endpoint()` |
| Pipeline reassessment | active_trade_reassessment | 1200 | 0.0 | 120s | via routing |
| Pipeline default executor | (direct HTTP) | 1200 | 0.0 | 120s | via `get_model_endpoint()` |
| On-demand model analysis | active_trade_model_analysis | 900 | **0.2** | 90s | via routing |
| Monitor narrative | monitor_narrative | 600 | **0.2** | 90s | via routing |
| Legacy on-demand fallback | (direct HTTP) | 600 | **0.2** | 90s | `"local-model"` hardcoded |

### Finding F-4E-02 (M): Temperature Inconsistency for Active Trade Analysis

The pipeline reassessment uses `temperature=0.0` (deterministic), while the on-demand model analysis and monitor narrative use `temperature=0.2` (slightly random). These serve the same purpose — evaluating active trade positions. A scheduled batch reassessment should not produce fundamentally different output characteristics than an on-demand analysis of the same position.

### Finding F-4E-03 (L): Scattered Configuration

Model parameters (max_tokens, temperature, timeout) are hardcoded at each call site. There is no central configuration table or settings file for model parameters. Changing the timeout for all calls requires editing 15+ locations. The runtime config files (`runtime_config.json`, `platform_settings.json`) do not exist on disk.

### Model Name Resolution

Model name is **not explicitly set** in most calls. Local LM Studio ignores the `model` field. The legacy fallback in `routes_active_trades.py` hardcodes `"local-model"`. Provider adapters handle actual model selection internally. This means there is no way to target a specific model from the call site — the model is whatever is loaded in LM Studio at runtime.

---

## 5  Output Parsing Pipeline

### Common Pattern

All prompt types follow the same general flow:

```
Raw LLM text
  → _strip_think_tags()        (removes <think>, <scratchpad>)
  → extract_and_repair_json()  (5-stage repair)
  → _coerce_*_output()         (type validation, defaults, constraints)
  → fallback object             (if all parsing fails)
```

### JSON Repair Pipeline (`json_repair.py`)

5 stages, executed in order:

1. **Direct parse** — `json.loads(text)`
2. **Strip markdown fences** — removes ` ```json ``` ` wrappers
3. **Extract JSON block** — finds first `{...}` or `[...]`
4. **Text repairs** — smart quotes, trailing commas, `//` comments, Python literals (`True`→`true`), control characters, `<think>`/`<scratchpad>` tags
5. **Full-text repair** — applies repair to entire text, then re-extracts

Tracks `REPAIR_METRICS` counters: `parse_ok`, `parse_repaired`, `parse_failed`.

### Legacy Parser Still Present

`_extract_json_payload()` in `model_analysis.py` L40-68 is deprecated but still exists. It only handles stages 1 and 3 (direct parse + block extraction). No current callers use it, but it remains as dead code.

---

## 6  Coercion Comparison Matrix

| Dimension | Regime | Stock Strategy | TMC Decision | Pipeline AT | On-Demand AT |
|-----------|--------|---------------|-------------|-------------|-------------|
| **JSON repair** | `extract_and_repair_json` | `extract_and_repair_json` | `extract_and_repair_json` | `extract_and_repair_json` | `extract_and_repair_json` |
| **Retry on bad JSON** | Yes (format enforcement) | Yes (retry-with-fix) | Yes (retry-with-fix) | **No** | Yes (MAX_ATTEMPTS=2) |
| **Decision default** | N/A | PASS | PASS | None (→ engine) | HOLD |
| **Score/conviction default** | confidence → None | score=50, confidence=50 | conviction=50 | conviction → 0.0 | confidence=0 |
| **Enum validation** | Multiple fields | recommendation, agreement | decision, agreement, alignment, verdict | recommendation only | stance, thesis_status, urgency, price_vs_sma |
| **Sub-object validation** | Sections pass-through | key_drivers, risk_review, engine_vs_model | factors, market_alignment, risk_assessment, technical_analysis | **None** — all pass-through | technical_state, action_plan, memo |
| **Fallback marker** | `_fallback_from_plaintext: True` | `_fallback: True` | `_fallback: True` | `model_available: False` | `confidence: 0`, `headline: "Analysis unavailable"` |
| **Fallback decision** | N/A | PASS, confidence=20 | PASS, conviction=10 | None (→ engine) | WATCH, confidence=0 |

### Finding F-4E-04 (H): Default Conviction=50 in TMC Coercer

When the TMC model returns an unparseable `conviction` field (null, non-numeric, or missing), `_coerce_tmc_final_decision_output()` defaults it to `50` — medium confidence. Combined with the fact that conviction thresholds are **not enforced** in the coercion layer (see F-4C-03), this means:

- Model returns garbage → conviction defaults to 50 → EXECUTE decision passes through at "medium" confidence
- No downstream code checks whether conviction is a coerced default vs genuine model output

The fallback object uses conviction=10, which is safe. But the coercer's intermediate default of 50 applies when JSON parsing succeeds but the conviction field is bad — a more likely failure mode than total JSON failure.

### Finding F-4E-05 (M): No Retry on Active Trade Pipeline Parse Failure

Every other prompt type retries when JSON parsing fails:
- Regime: format enforcement retry with correction prompt
- Stock strategy: retry-with-fix (sends bad output back)
- TMC: retry-with-fix in both legacy and routed paths
- On-demand active: MAX_ATTEMPTS=2

The pipeline active trade executor returns `{"status": "error", "error": "json_parse_failed"}` immediately — no retry, no correction prompt. This falls through to `_degraded_model_output()`, and the engine recommendation takes over.

While the fallback-to-engine behavior is reasonable, the lack of retry is inconsistent. One bad token in the model's JSON output silently eliminates the model's contribution for that position.

### Finding F-4E-06 (L): Stock Strategy Default Score=50

When the model returns an unparseable score, `_coerce_stock_strategy_output()` defaults to `score=50`. Combined with the engine's `composite_score` being injected into the fallback object, a partially-parsed response could produce a plausible-looking score that didn't come from either the engine or the model.

---

## 7  Think-Tag Stripping

### Double-Stripping Pattern

Think-tag removal happens at multiple layers:

1. **`_model_transport()`** — strips after successful routing (`_strip_think_tags()`)
2. **`json_repair.py`** — strips `<think>`/`<scratchpad>` during text repair (stage 4)
3. **`_routed_model_executor()`** (active_trade_pipeline.py) — strips again after `execute_routed_model()`
4. **`sanitize_model_text()`** (routes_active_trades.py) — strips before JSON extraction

The double/triple stripping is **safe** but indicates unclear ownership. The routing integration layer should guarantee clean output, making downstream stripping unnecessary.

---

## 8  Fallback Object Analysis

### Can Fallback Objects Be Mistaken for Real Model Output?

| Prompt Type | Fallback Marker | Risk of Confusion |
|-------------|----------------|-------------------|
| Regime | `_fallback_from_plaintext: True` | Low — explicit flag |
| Stock Strategy | `_fallback: True`, score from engine | **Medium** — score looks plausible because it comes from engine data |
| TMC Decision | `_fallback: True`, conviction=10 | Low — very low conviction is suspicious |
| Pipeline AT | `model_available: False`, degraded_reasons | Low — clear "model down" signal |
| On-Demand AT | confidence=0, headline="Analysis unavailable" | Low — zero confidence is clear |

### Finding F-4E-07 (L): No Unified Fallback Convention

Each prompt type uses a different fallback marker:
- `_fallback_from_plaintext: True`
- `_fallback: True`
- `model_available: False`
- `confidence: 0` + sentinel headline

There is no standard way for a downstream consumer to ask "is this a real model response?" — each consumer must know the specific prompt type's fallback convention.

---

## 9  Routing Configuration

### Provider Configuration

| Provider | Endpoint | Default Max Concurrency |
|----------|----------|------------------------|
| `localhost_llm` | `http://localhost:1234/v1/chat/completions` | 1 |
| `network_model_machine` | `http://192.168.1.143:1234/v1/chat/completions` | 1 |
| `bedrock_titan_nova_pro` | AWS Bedrock (stub) | 1 |

### Default Execution Mode

`DEFAULT_ROUTED_MODE = "local_distributed"` — tries localhost first, then network model machine. The TMC decision path explicitly requests `online_distributed` (adds bedrock as third fallback).

### Probe Configuration

- Probe timeout: 3.0s
- Degraded threshold: 2000ms (slow probe → DEGRADED state, still eligible)
- Busy detection: HTTP 429 / 503 → BUSY state

### Mode Resolution Precedence

```
premium_override=True  →  online_distributed
caller_mode (explicit)  →  as specified
UI-selected mode        →  from model_state
DEFAULT_ROUTED_MODE     →  local_distributed
```

---

## 10  Findings Summary

| ID | Sev | Finding |
|----|-----|---------|
| F-4E-04 | H | TMC coercer defaults conviction to 50 on parse failure — medium confidence for garbage input, no downstream detection |
| F-4E-01 | M | Two parallel transport paths (_model_transport vs direct execute_routed_model) with duplicated fallback logic in 4+ call sites |
| F-4E-02 | M | Temperature inconsistency: pipeline AT uses 0.0 (deterministic) while on-demand AT uses 0.2 (random) for equivalent analysis |
| F-4E-05 | M | Pipeline AT is the only prompt type with no retry on JSON parse failure — model contribution silently lost |
| F-4E-03 | L | Model parameters (max_tokens, temp, timeout) hardcoded across 15+ call sites with no central configuration |
| F-4E-06 | L | Stock strategy coercer defaults score to 50 on parse failure — plausible-looking score from neither engine nor model |
| F-4E-07 | L | No unified fallback marker convention — each prompt type uses different flag names/patterns |

**Severity distribution**: 1 High, 3 Medium, 3 Low

---

## 11  Cross-Prompt Observations (4A-4E Complete)

### Systemic Patterns Confirmed Across All Prompts

1. **Anti-anchoring absent everywhere** (4A-4D): Every prompt receives prior engine scores/recommendations before the model forms its opinion. The routing layer faithfully delivers the full data context — no architectural mechanism exists to withhold engine conclusions.

2. **Conviction/confidence is decorative end-to-end** (4A-4E): No coercion layer enforces a conviction threshold. Default values on parse failure (50 for TMC and stock strategy) create plausible-looking confidence from garbage input. The entire system treats confidence as metadata, not a gate.

3. **Two-system redundancy with schema divergence** (4D, 4E): Stock analysis (strategy prompt + TMC prompt) and active trade analysis (pipeline prompt + on-demand prompt) each have two LLM systems with incompatible schemas, different decision labels, and different conviction scales.

4. **Output schema asks for unfillable fields** (4A-4D): Regime asks for event impact with no event data. TMC asks for risk/reward verdict with no stop/target data. Active trade asks for portfolio_fit with no portfolio data and event_sensitivity with no event calendar. Every prompt has output fields that require data not present in input.

5. **JSON repair is robust but coercion defaults vary widely** (4E): The 5-stage JSON repair pipeline is well-designed and consistent. But after successful JSON parsing, the coercion defaults are inconsistent — TMC defaults conviction to 50, strategy defaults score to 50, active trade pipeline defaults conviction to 0.0. There is no principled rationale for why some defaults are "safe" (low/zero) and others are "plausible" (50).

---

## 12  Recommendations

1. **Set TMC conviction default to 10 (not 50)** — match the fallback object's conviction level. A model that can't produce a conviction field should not get medium confidence.

2. **Add retry-with-fix to active trade pipeline** — all other prompt types retry on JSON failure. The pipeline should too, especially since it's the automated batch process.

3. **Centralize model parameters** — create a single configuration table mapping `task_type` → `{max_tokens, temperature, timeout}`. Eliminate hardcoded values at 15+ call sites.

4. **Standardize fallback markers** — add a common `_is_fallback: True` field to all fallback/degraded objects across all prompt types.

5. **Consolidate transport paths** — all callers that currently call `execute_routed_model()` directly should use `_model_transport()` (or a shared wrapper) to eliminate duplicated fallback logic.

6. **Resolve temperature inconsistency** — pipeline and on-demand active trade analysis should use the same temperature for equivalent work.
