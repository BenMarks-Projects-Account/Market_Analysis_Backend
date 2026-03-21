# Audit 3A — Stock Pipeline End-to-End Flow

**Scope**: Complete trace of the stock opportunity pipeline — from universe definition through scanner execution, aggregation, enrichment, model analysis, filtering, ranking, and final output packaging. Eight stages mapped with candidate counts, data transformations, and failure modes at each boundary.

**Date**: 2025-07-19
**Auditor**: Copilot (automated deep-read)

---

## Source Files

| Component | File | Key Lines |
|-----------|------|-----------|
| Runner (pipeline orchestrator) | `app/workflows/stock_opportunity_runner.py` | L85–110 (constants), L414–640 (orchestrator), L638–930 (stages 1-4), L930–1095 (stage 5), L1095–1163 (stage 6), L1163–1406 (stage 7), L1406–1490 (stage 7b), L1490–1600 (stage 8) |
| Engine service (scanner dispatch) | `app/services/stock_engine_service.py` | L26 (TOP_N=9), L106–225 (scan method) |
| Scanner candidate contract | `app/services/scanner_candidate_contract.py` | L1–100 (27-field contract), L341+ (normalize_candidate_output) |
| Pullback Swing scanner | `app/services/pullback_swing_service.py` | Universe ~196 symbols, NO filter chain, 4-component scoring |
| Momentum Breakout scanner | `app/services/momentum_breakout_service.py` | 6-stage filter chain, 4-component scoring |
| Mean Reversion scanner | `app/services/mean_reversion_service.py` | 4-stage filter chain, 4-component scoring |
| Volatility Expansion scanner | `app/services/volatility_expansion_service.py` | 3-stage filter chain, 4-component scoring |
| Model routing integration | `app/services/model_routing_integration.py` | `routed_tmc_final_decision()` |

---

## Pipeline Constants

| Constant | Value | Location | Purpose |
|----------|-------|----------|---------|
| `MIN_SETUP_QUALITY` | 30.0 | runner L93 | Quality gate in Stage 5 |
| `DEFAULT_TOP_N` | 20 | runner L96 | Final output cap (Stage 5 select) |
| `_ENGINE_SCAN_LIMIT` | 200 | runner L101 | Overrides engine's internal TOP_N |
| `MODEL_FILTER_TOP_N` | 10 | runner L1406 | Post-model rank cap (Stage 7b) |
| `TOP_N` (engine default) | 9 | engine L26 | StockEngineService default (overridden by runner) |
| `STOCK_SCANNER_KEYS` | 4 keys | runner L106 | pullback_swing, momentum_breakout, mean_reversion, volatility_expansion |

---

## 1. Stage Inventory & Pipeline Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    STOCK OPPORTUNITY PIPELINE                          │
│                    (stock_opportunity_runner.py)                       │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  Stage 1: load_market_state                                           │
│    └─ Load latest market state (regime, VIX, tags)                    │
│         Status: required (failed → abort)                             │
│         Degraded: OK (enrichment-only)                                │
│                                                                       │
│  Stage 2: resolve_stock_scanner_suite                                 │
│    └─ Enumerate 4 configured scanners                                 │
│         All 4 unconditionally available (no feature flags)            │
│                                                                       │
│  Stage 3: run_stock_scanner_suite                                     │
│    └─ StockEngineService.scan(top_n=200)                              │
│    └─ 4 scanners run SEQUENTIALLY                                     │
│    └─ Each scanner: universe → filter → score → sort → return         │
│         Status: required (failed → abort)                             │
│         Partial: individual scanner failure OK                        │
│                                                                       │
│  Stage 4: aggregate_dedup_candidates                                  │
│    └─ normalize_candidate_output() → 27-field contract                │
│    └─ Dedup: group by symbol, keep max(setup_quality)                 │
│    └─ Attach source_scanners provenance                               │
│         Status: required (failed → abort)                             │
│                                                                       │
│  Stage 5: enrich_filter_rank_select                                   │
│    └─ Enrich: attach market_regime, VIX, regime_tags, etc.            │
│    └─ Filter: reject setup_quality < 30                               │
│    └─ Rank: sort by (-setup_quality, symbol)                          │
│    └─ Select: keep top DEFAULT_TOP_N (20)                             │
│         Status: required (failed → abort)                             │
│                                                                       │
│  Stage 6: append_market_picture_context                               │
│    └─ Attach 6-module MI engine context for model analysis            │
│         Status: degradable (proceeds without enrichment)              │
│                                                                       │
│  Stage 7: run_final_model_analysis                                    │
│    └─ LLM call per candidate via routed_tmc_final_decision            │
│    └─ Max 4 concurrent, 2-pass retry                                  │
│    └─ Attaches: model_recommendation, model_score, etc.               │
│         Status: degradable (candidates pass through without review)   │
│                                                                       │
│  Stage 7b: model_filter_rank                                          │
│    └─ Remove PASS recommendations                                     │
│    └─ Remove candidates without model analysis                        │
│    └─ Rank by model_score DESC                                        │
│    └─ Keep top MODEL_FILTER_TOP_N (10)                                │
│                                                                       │
│  Stage 8: package_publish_output       ← ALWAYS EXECUTES             │
│    └─ Write output.json, summary.json, manifest.json, latest.json    │
│    └─ Write truth-audit & model-input-preview artifacts               │
│                                                                       │
└─────────────────────────────────────────────────────────────────────────┘
```

### Candidate Count Funnel (typical run)

```
Universe:      ~196 symbols (shared _BALANCED_UNIVERSE)
                  ↓
Scanner raw:   4 scanners × variable yield
               Pullback Swing:       ~196 (NO filters — all score)
               Momentum Breakout:    ~10-40 (6-stage filter)
               Mean Reversion:       ~5-20 (4-stage filter)
               Volatility Expansion: ~5-25 (3-stage filter)
                  ↓
Engine sort:   All combined → sorted by _sort_key → top 200 returned
                  ↓
Normalize:     raw → 27-field canonical contract via normalize_candidate_output()
                  ↓
Dedup:         Group by symbol → keep max(setup_quality) → attach source_scanners
               ~196 → ~120-160 unique (pullback swing overlaps dominate)
                  ↓
Quality filter: Reject setup_quality < 30.0
               Typically removes ~40-70% of pullback swing low-scorers
                  ↓
Rank + select: Sort by (-setup_quality, symbol) → top 20
                  ↓
Market Picture: Enrich with 6-module MI context (no filtering)
                  ↓
Model analysis: LLM review → BUY/PASS per candidate (up to 20)
                  ↓
Model filter:  Remove PASS + unanalyzed → rank by model_score → top 10
                  ↓
Output:        ≤10 final candidates → output.json
```

---

## 2. Universe → Scanner Flow

### Universe Definition

All 4 stock scanners share `_BALANCED_UNIVERSE` (~196 symbols), a hardcoded list in each scanner service. The list includes ~80+ equities and excludes most ETFs (only sector-representative ETFs like XLK, XLF remain).

**Key observation**: The universe is duplicated in each scanner's source file. There is no centralized universe module. Changes to the universe must be made in 4 places simultaneously.

### Scanner Dispatch

`StockEngineService.scan()` dispatches to scanners **sequentially** via an ordered dict:

```python
self._scanners = {
    "stock_pullback_swing": pullback_swing_service,
    "stock_momentum_breakout": momentum_breakout_service,
    "stock_mean_reversion": mean_reversion_service,
    "stock_volatility_expansion": volatility_expansion_service,
}
```

**Rationale for sequential execution** (documented in engine service):
- All 4 scanners share the same Tradier API key and HTTP client
- Concurrent execution (4×8 = 32 parallel requests) exceeds Tradier's rate limit
- Sequential execution lets later scanners benefit from TTLCache populated by earlier ones

Each scanner independently:
1. Iterates the universe
2. Fetches OHLCV data per symbol from Tradier (with caching)
3. Applies strategy-specific filters (if any)
4. Computes a 4-component composite_score (0–100)
5. Returns `{"status": "ok", "candidates": [...], ...}`

The engine aggregates all candidates, sorts by `_sort_key`, and returns `top_n`.

---

## 3. Multi-Scanner Aggregation (Stage 4)

### Normalization

Every raw candidate dict is passed through `normalize_candidate_output(scanner_key, raw_candidate)` which maps scanner-specific field names into the 27-field canonical contract defined in `scanner_candidate_contract.py`.

Critical mapping: `setup_quality = composite_score` (1:1, no transform).

Candidates that fail normalization are silently skipped with a warning appended.

### Deduplication Logic

```python
# Core dedup algorithm (runner L863-895)
best_by_symbol: dict[str, dict] = {}
scanners_by_symbol: dict[str, list[str]] = {}

for cand in normalized:
    sym = cand["symbol"]
    sq = cand["setup_quality"]
    
    # Track ALL scanners that found this symbol
    scanners_by_symbol[sym].append(scanner_key)
    
    # Keep candidate with highest setup_quality
    if sym not in best_by_symbol or sq > best_by_symbol[sym]["setup_quality"]:
        best_by_symbol[sym] = cand

# Attach full provenance
for sym, cand in best_by_symbol.items():
    cand["source_scanners"] = scanners_by_symbol[sym]
```

**Implications**:
- When the same symbol appears in multiple scanners, only the highest-scoring version survives
- The winning candidate's `scanner_key` determines which strategy is displayed
- `source_scanners` preserves provenance (e.g., `["stock_pullback_swing", "stock_momentum_breakout"]`)
- The losing scanner's per-component scores, thesis, and metrics are discarded

### Aggregation Counts (emitted in stage artifact)

```
raw_input           → total candidates across all scanners
normalized          → successfully normalized
skipped             → failed normalization
dedup_removed       → removed as duplicates
after_dedup         → unique symbols proceeding
multi_scanner_symbols → symbols found by 2+ scanners
per_scanner_normalized → count per scanner key
```

---

## 4. Enrichment (Stages 5 + 6)

### Stage 5: Market State Enrichment

Each candidate receives the following fields from `consumer_summary` (loaded in Stage 1):

| Field | Source |
|-------|--------|
| `market_state_ref` | Market state file reference |
| `market_regime` | `consumer_summary.market_state` |
| `risk_environment` | `consumer_summary.stability_state` |
| `vix` | `consumer_summary.vix` |
| `regime_tags` | `consumer_summary.regime_tags` |
| `support_state` | `consumer_summary.support_state` |
| `market_summary_text` | `consumer_summary.summary_text` |
| `market_confidence` | `consumer_summary.confidence` |

If market state is degraded, a warning is appended but **no candidates are rejected**. Enrichment is purely additive.

### Stage 6: Market Picture Context

Extracts compact context from 6 MI engine modules:
- `breadth_participation`
- `volatility_options`
- `cross_asset_macro`
- `flows_positioning`
- `liquidity_financial_conditions`
- `news_sentiment`

Attaches `market_picture_context` (full dict for model analysis) and `market_picture_summary` (compact view for diagnostics) to each candidate.

**Degradable**: if no MI engines are available, candidates pass through unenriched. Stage returns status `"degraded"` but pipeline continues.

---

## 5. Model Analysis (Stage 7)

### Dispatch

Each selected candidate (up to 20 from Stage 5) receives an LLM analysis call via `routed_tmc_final_decision()`:

```python
model_result = routed_tmc_final_decision(
    candidate=raw_cand,            # enriched raw candidate
    market_picture_context=mpc,    # 6-module MI context
    strategy_id=scanner_key,       # strategy routing
    retries=2,                     # per-call retry count
)
```

### Concurrency Model

- `ThreadPoolExecutor` with `max_workers=min(len(candidates), 4)`
- `asyncio.Semaphore(4)` gates concurrent dispatch
- LLM calls are synchronous, run in executor to avoid blocking event loop

### Retry Strategy

1. **First pass**: All candidates dispatched concurrently (max 4 at a time) via `asyncio.gather`
2. **Second pass**: Any failures from first pass are retried after a 3-second delay, sequentially
3. Candidates that fail both passes get `model_review = None`

### Fields Attached

| Field | Source | Type |
|-------|--------|------|
| `model_recommendation` | `decision == "EXECUTE" → "BUY"`, else `"PASS"` | str |
| `model_confidence` | `model_result.conviction` | 0-100 |
| `model_score` | `model_result.engine_comparison.model_score` | 0-100 |
| `model_review_summary` | `model_result.decision_summary` | str |
| `model_key_factors` | `model_result.factors_considered` (remapped) | list[dict] |
| `model_caution_notes` | `model_result.risk_assessment.primary_risks` | list[str] |
| `model_technical_analysis` | `model_result.technical_analysis` | dict |
| `model_review` | Full model result dict (for debug) | dict |

### Degradation

- If `model_request_fn` is not configured → all candidates get `model_review = None`, stage status `"degraded"`
- If total import failure → same degraded behavior, pipeline continues
- If individual failures → only failed candidates get `None`, stage status `"degraded"` if any fail

---

## 6. Model Filter & Rank (Stage 7b)

### Filter Rules (applied in order)

```python
# Stage 7b logic (runner L1408-1470)
for cand in selected:
    if cand["model_review"] is None:
        no_analysis.append(cand)          # Rule 2: drop unanalyzed
    elif cand["model_recommendation"] == "PASS":
        passed.append(cand)               # Rule 1: drop PASS
    else:
        buy_candidates.append(cand)       # Keep BUY candidates

# Rule 3: Rank by model_score DESC (None scores sort last → -1)
buy_candidates.sort(
    key=lambda c: c.get("model_score") if c.get("model_score") is not None else -1,
    reverse=True,
)

# Rule 4: Trim to top 10
trimmed = buy_candidates[:MODEL_FILTER_TOP_N]
```

### Filter Counts (emitted in stage artifact)

```
before              → input count (from Stage 5, up to 20)
passed_removed      → count of PASS recommendations removed
passed_symbols      → symbols removed as PASS
no_analysis_removed → count without model analysis removed
no_analysis_symbols → symbols removed as unanalyzed
buy_candidates      → remaining BUY candidates after filter
dropped_by_rank     → BUY candidates beyond top 10 cut
dropped_symbols     → symbols dropped by rank cap
after               → final count (≤10)
```

---

## 7. Final Output (Stage 8)

### Always Executes

Stage 8 is wrapped in a `try/except/finally` pattern in the orchestrator. Even if Stages 1-7 encounter `CancelledError` or unexpected exceptions, Stage 8 still runs to package whatever candidates are available.

### Artifacts Written

| File | Content |
|------|---------|
| `stage_package_publish_output.json` | Full selected candidates |
| `output.json` | Compact consumer output |
| `summary.json` | Run summary with stage statuses |
| `manifest.json` | Run-level index |
| `latest.json` | Workflow pointer update |

### Post-Pipeline Artifacts

After Stage 8, two additional diagnostic artifacts are written:
- **Truth audit artifact** — `_write_truth_audit_artifact()` — verifies output / input traceability
- **Model input preview artifact** — `_write_model_input_preview_artifact()` — captures what was sent to the model

### Output Contract Shape

```json
{
  "contract_version": "...",
  "workflow_id": "stock_opportunity",
  "run_id": "...",
  "generated_at": "ISO 8601",
  "batch_status": "completed | partial",
  "market_state_ref": "...",
  "publication": { "status": "valid | degraded", "market_state_publication_status": "..." },
  "candidates": [ /* compact candidates */ ],
  "quality": {
    "level": "good | degraded | no_candidates",
    "total_candidates_found": N,
    "selected_count": N,
    "top_n_cap": 20,
    "scanners_ok": N,
    "scanners_total": 4
  },
  "scanner_coverage": { /* per-scanner counts, timing */ },
  "filter_counts": { /* Stage 5 filter trace */ },
  "market_picture_summary": { /* 6-module summary */ },
  "model_analysis_counts": { /* Stage 7 success/failure counts */ }
}
```

---

## 8. Failure Modes & Error Handling

### Stage Failure Matrix

| Stage | On Failure | Pipeline Impact |
|-------|-----------|-----------------|
| 1 — load_market_state | Failed → abort, Degraded → continue | Hard failures abort; stale/degraded data is acceptable |
| 2 — resolve_scanner_suite | Unconditional success | All 4 scanners always available (no feature flags) |
| 3 — run_stock_scanner_suite | Failed → abort, Partial (1-3 scanners fail) → continue | Total engine failure aborts; individual scanner failures tolerated |
| 4 — aggregate_dedup_candidates | Failed → abort | Normalization failure aborts |
| 5 — enrich_filter_rank_select | Failed → abort | Filter/rank failure aborts |
| 6 — append_market_picture_context | Failed → degraded, continue | Candidates proceed without MI enrichment |
| 7 — run_final_model_analysis | Failed → degraded, continue | Candidates proceed without model review |
| 7b — model_filter_rank | Part of try block | Runs even if Stage 7 degraded |
| 8 — package_publish_output | Always runs | Packages whatever candidates are available |

### CancelledError Handling

The orchestrator catches `asyncio.CancelledError` and `Exception` around Stages 1-7b, then **always executes Stage 8**:

```python
except asyncio.CancelledError:
    warnings.append("[pipeline] Run interrupted — packaging partial output")
except Exception as exc:
    warnings.append(f"[pipeline] Unexpected error — packaging partial output: {exc}")

# Stage 8 ALWAYS executes
outcome = _stage_package_publish_output(...)
```

This ensures `latest.json` is always updated and downstream consumers see the freshest available data.

### Silent Drop Points

| Location | What Gets Dropped | Tracking |
|----------|------------------|----------|
| Stage 4 normalization | Candidates failing `normalize_candidate_output()` | Counted in `skipped`, warning appended |
| Stage 4 dedup | Lower-scoring duplicates for same symbol | Counted in `dedup_removed` |
| Stage 5 quality filter | Candidates with `setup_quality < 30` | Counted in `rejected`, reason: `below_quality_threshold` |
| Stage 5 top-N cap | Candidates ranked > 20th | Implicit — `passed - selected` in filter_counts |
| Stage 7b PASS filter | Model PASS recommendations | Counted in `passed_removed`, symbols logged |
| Stage 7b no-analysis filter | Candidates without model review | Counted in `no_analysis_removed`, symbols logged |
| Stage 7b rank cap | BUY candidates ranked > 10th | Counted in `dropped_by_rank`, symbols logged |

---

## Findings

### F-3A-01 — HIGH: Pullback Swing Has No Strategy-Specific Filters

**Evidence**: `pullback_swing_service.py` — unlike the other 3 scanners, Pullback Swing has NO filter chain. All ~196 symbols from `_BALANCED_UNIVERSE` proceed directly to the scoring function. This floods the pipeline with ~196 low-quality scored candidates while the other scanners produce 5-40 targets each.

**Impact**:
- Pullback Swing candidates dominate raw candidate volume (~70-80% of total)
- Dedup favors Pullback Swing when its score happens to be highest for a given symbol
- The MIN_SETUP_QUALITY=30 filter in Stage 5 becomes the de facto filter for this scanner
- Scanner comparison metrics are skewed — Pullback Swing shows 196 candidates vs 10-40 for others

**Risk**: A symbol with a poor pullback setup could score 31/100, pass the quality gate, and occupy a slot that a better-filtered candidate from another scanner would have filled.

**Recommendation**: Add a proximity/trend filter gate to Pullback Swing (consistent with the 6-stage filter chain in Momentum Breakout) to reduce noise before scoring.

---

### F-3A-02 — HIGH: StockEngineService Default TOP_N=9 Is a Hidden Bottleneck

**Evidence**: `stock_engine_service.py` L26 defines `TOP_N = 9` as the default parameter for `scan()`. The runner properly overrides this with `_ENGINE_SCAN_LIMIT = 200`. However, any other caller of `StockEngineService.scan()` without an explicit `top_n` parameter would silently receive only 9 candidates out of potentially 200+.

**Impact**: Currently harmless because the runner always passes `top_n=200`. But creates a fragile API surface — any new integration calling `.scan()` without reading the runner's pattern would get unexpectedly truncated results.

**Recommendation**: Either increase the engine default to a safe value (e.g., 100) or add a log warning when the default is used.

---

### F-3A-03 — HIGH: MODEL_FILTER_TOP_N=10 Creates a Hard Cliff After Model Analysis

**Evidence**: `runner L1406-1449` — Stage 7b keeps only the top 10 BUY candidates by `model_score`. If 15 candidates receive a BUY recommendation, 5 are silently dropped solely by rank position.

**Impact**:
- A candidate scoring model_score=78 could be dropped while model_score=79 survives — a 1-point difference determines inclusion
- No configurable knob — the constant is hardcoded at module level
- The dropped candidates' `setup_quality` (scanner score) is irrelevant at this stage; only `model_score` determines survival
- Combined with Stage 5's top-20 cap and PASS filtering, the effective yield is often well below 10

**Recommendation**: Make `MODEL_FILTER_TOP_N` configurable via `RunnerConfig` alongside `top_n`. Consider whether a minimum `model_score` threshold would be more appropriate than a hard rank cap.

---

### F-3A-04 — MEDIUM: Candidates Without Model Analysis Are Silently Dropped

**Evidence**: Stage 7b rule 2 — candidates where `model_review is None` are removed. This happens when:
- Model analysis fails for a candidate (both passes) in Stage 7
- `model_request_fn` is not configured (all candidates get None)

When Stage 7 is degraded (all model calls fail), **every candidate is dropped in Stage 7b**, resulting in 0 final output. The pipeline reports `status: "completed"` with 0 candidates.

**Impact**: A Tradier-validated, high-quality-scored candidate (setup_quality=85) could be lost because an unrelated LLM service was temporarily unavailable.

**Recommendation**: When Stage 7 is fully degraded, bypass Stage 7b entirely and pass candidates through with a warning flag. Alternatively, retain unanalyzed candidates at the end of the ranked list rather than dropping them.

---

### F-3A-05 — MEDIUM: No Regime-Aware Gating in Stock Pipeline Selection

**Evidence**: Stage 5 enriches candidates with `market_regime` and `risk_environment` but **never uses these fields for filtering or scoring adjustment**. A momentum breakout candidate receives the same treatment whether the regime is "strong_uptrend" or "correction".

**Impact**: The pipeline may surface long-biased momentum breakout trades in bearish regimes. Regime awareness is deferred entirely to the LLM model analysis (Stage 7), but if the model also doesn't gate on regime, inappropriate trades can reach the final output.

**Recommendation**: Add optional regime-aware gates in Stage 5 (e.g., suppress momentum breakout candidates in "correction"/"bear" regimes) or add regime-based score adjustments. At minimum, verify that the model analysis prompt includes regime context and acts on it.

---

### F-3A-06 — MEDIUM: Sequential Scanner Execution Adds Latency

**Evidence**: `stock_engine_service.py` L106-136 — all 4 scanners run sequentially because they share a Tradier API key. The comment documents that concurrent execution caused mass 429 errors.

**Impact**: Total scan time = sum of all 4 scanner runtimes. With TTLCache, later scanners run faster, but the first scanner (Pullback Swing, ~196 symbols) dominates wall time. Estimated 30-90 seconds for a full scan depending on cache state.

**Risk**: This is a deliberate, documented design decision. The risk is operational — long scan times degrade user experience on the Home Dashboard refresh.

**Mitigation**: The TTLCache sharing is effective and well-documented. No action needed unless API rate limits change or a second API key becomes available.

---

### F-3A-07 — MEDIUM: Universe Duplication Across 4 Scanners

**Evidence**: `_BALANCED_UNIVERSE` is defined separately in each of the 4 scanner service files. There is no shared universe module.

**Impact**: Adding or removing a symbol requires editing 4 files. Risk of drift if one file is updated and others are not. Currently they appear synchronized, but this is fragile.

**Recommendation**: Extract `_BALANCED_UNIVERSE` into a shared module (e.g., `app/services/stock_universe.py`) imported by all 4 scanners.

---

### F-3A-08 — MEDIUM: Dedup Discards Losing Scanner's Full Analysis

**Evidence**: Stage 4 dedup keeps only the candidate with the highest `setup_quality` when the same symbol appears from multiple scanners. The losing scanner's component scores, thesis, metrics, and strategy-specific context are entirely discarded.

**Impact**: A symbol might score 72 in Pullback Swing (winner) and 65 in Momentum Breakout (discarded). The downstream model analysis only sees the pullback thesis, missing the confluence signal that two independent strategies identified the same symbol.

**Recommendation**: Consider passing a `multi_scanner_confluence` signal to the model analysis when `source_scanners` has 2+ entries. This doesn't require keeping both full candidates — just a flag and summary of the losing scanner's thesis.

---

### F-3A-09 — LOW: Stage 5 Sort Tie-Breaking Uses Symbol Name

**Evidence**: `runner L1003-1005` — ranking sorts by `(-setup_quality, symbol)`. Two candidates with identical `setup_quality` are ordered alphabetically by symbol.

**Impact**: AAPL always beats TSLA when setup_quality is tied. This is deterministic (good for reproducibility) but not merit-based. With 196 Pullback Swing candidates feeding in, ties are plausible around the quality boundary.

**Recommendation**: Consider adding a secondary merit-based tie-breaker (e.g., volume, ATR) before the alphabetical fallback.

---

### F-3A-10 — LOW: No Data-Quality Validation Before Scoring

**Evidence**: Scanners score candidates using metrics from Tradier OHLCV data, but there is no explicit validation stage that checks for data completeness (e.g., sufficient history length, no stale prices, valid volume > 0) before entering the scoring function.

**Impact**: A symbol with incomplete history or zero volume could receive a misleading score. The scoring functions handle some edge cases internally (e.g., defaulting missing values to 0), but there is no centralized data-quality gate.

**Recommendation**: Add a pre-scoring data-quality check in each scanner (or centralized in a shared utility) that validates minimum history length, non-zero volume, and price staleness before computing the composite score.

---

### F-3A-11 — LOW: model_score Sort Uses -1 for None (Potential Misranking)

**Evidence**: `runner L1444-1448` — `model_score` sort key uses `-1` for `None` values:

```python
key=lambda c: c.get("model_score") if c.get("model_score") is not None else -1
```

**Impact**: A candidate with `model_score = 0` (a real score from the model indicating zero confidence) would rank above `model_score = None` (model call failed), but a candidate with `model_score = -1` (if ever produced) would sort identically to a failed analysis. In practice, model_score is 0-100, so -1 never appears — but the sentinel value is undocumented.

**Recommendation**: Use `float('-inf')` instead of `-1` for clarity and safety.

---

## Summary

| Severity | Count | Key Theme |
|----------|-------|-----------|
| HIGH | 3 | Pullback Swing filter gap, engine default bottleneck, hard rank cliff |
| MEDIUM | 5 | Silent drops on model failure, no regime gating, universe duplication, dedup information loss, sequential latency |
| LOW | 3 | Sort tie-breaking, data quality pre-check, sort sentinel value |
| **Total** | **11** | |

### Pipeline Health Assessment

The stock pipeline is **well-structured** with clear stage boundaries, comprehensive artifact emission, and good error tracking. The 8-stage design provides excellent traceability — every drop point has explicit counts and reason codes in the stage artifacts.

**Primary concerns**:
1. Pullback Swing's missing filter chain creates volume imbalance that ripples through dedup and selection
2. The double-funnel (top-20 in Stage 5, top-10 in Stage 7b) aggressively narrows the candidate set, with zero configurability on the Stage 7b cap
3. Total model analysis failure silently zeroes the output — the pipeline should degrade more gracefully when the LLM layer is unavailable
