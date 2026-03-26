# TMC Full Refresh — Performance Audit

**Date:** 2025-01-27
**Scope:** End-to-end trace of `handleFullRefresh()` → all backend runners
**Goal:** Identify bottlenecks, quantify API call budgets, rank optimisation opportunities

---

## 1. Architecture Overview

The "Full Refresh" button in TMC triggers **4 stages strictly in sequence** via a `.then()` promise chain in `trade_management_center.js` (line 2458):

```
Stock Scan → Options Scan → Active Trades → Portfolio Balance
```

Each stage issues a POST to the backend, awaits the full response, renders the UI, then starts the next stage. There is **zero parallelism** at the frontend level.

### Backend Endpoints

| Stage | API Endpoint | Backend Runner |
|---|---|---|
| 1 – Stock | `POST /api/tmc/workflows/stock/run` | `stock_opportunity_runner.run_stock_opportunity()` |
| 2 – Options | `POST /api/tmc/workflows/options/run` | `options_opportunity_runner.run_options_opportunity()` |
| 3 – Active Trades | `POST /api/active-trade-pipeline/run` | `active_trade_pipeline.run_active_trade_pipeline()` |
| 4 – Portfolio Balance | `POST /api/tmc/workflows/portfolio-balance/run` | `portfolio_balancing_runner.run_portfolio_balance()` |

`TMCExecutionService` is a thin async wrapper — negligible overhead.

---

## 2. Per-Stage Breakdown

### 2.1 Stage 1 — Stock Opportunity Runner (8 sub-stages)

**File:** `stock_opportunity_runner.py` line 401

| Sub-stage | Work | Parallelism | Notes |
|---|---|---|---|
| 1. init | Config, constants | — | Instant |
| 2. load_market_state | Fetch market picture | 1 call | Fast (~1s) |
| 3. run_stock_scanner_suite | 4 scanners × ~196 symbols | **SEQUENTIAL scanners**, Semaphore(8) within each | **Dominant cost** |
| 4. deduplicate | Cross-scanner dedup | Sync | Fast |
| 5. rank_and_select | Score + top-N | Sync | Fast |
| 6. annotate_market_context | Attach regime data | Sync | Fast |
| 7. run_final_model_analysis | LLM calls on top ~20 | ThreadPoolExecutor(4), Semaphore(4) | ~20 × 10-30s per call |
| 8. build_final_package | JSON assembly + file write | Sync | Fast |

**Scanner suite detail** (`stock_engine_service.py` line 121):
- Scanners: `pullback_swing`, `momentum_breakout`, `mean_reversion`, `volatility_expansion`
- Run **SEQUENTIALLY** — documented rationale: "4×8 = 32 parallel requests exceeds Tradier rate limit"
- Each scanner: ~196 symbols, `Semaphore(8)` concurrency, 12s per-symbol timeout
- Each symbol fetch = 1 `get_candles()` call to Tradier (cached at 1800s TTL)
- Later scanners benefit from TTLCache warm-up from earlier scanners

**Estimated API calls:** ~196 symbols × 1 bar call = 196 calls for scanner 1, declining for scanners 2-4 due to cache hits. Total: ~350–500 actual Tradier calls.

**Estimated time at 2 req/sec:** 175–250 seconds (3–4 min) for bar fetches alone, plus model analysis (~2–5 min for 20 LLM calls at 4 concurrent).

### 2.2 Stage 2 — Options Opportunity Runner (7 sub-stages)

**File:** `options_opportunity_runner.py` line 510

| Sub-stage | Work | Parallelism | Notes |
|---|---|---|---|
| 1. load_market_state | Fetch market regime | 1 call | Fast |
| 2. scan | 11 scanner_keys × 4 symbols | **ALL SEQUENTIAL** | **THE BOTTLENECK** |
| 3. validate_math | Filter bad math | Sync | Fast |
| 4. enrich_evaluate | Rank + credibility gate → top ~30 | Sync | Fast |
| 5. model_analysis | LLM on top 15 | ThreadPoolExecutor(4), Semaphore(4), 2-pass retry | ~15 × 10-30s/call |
| 6. model_filter | Keep top 10 EXECUTE | Sync | Fast |
| 7. select_package | Final JSON + file write | Sync | Fast |

**Scan sub-stage deep dive** (`options_scanner_service.py` line 85):

```python
for scanner_key in scanner_keys:        # 11 keys
    for symbol in symbols:               # 4 symbols (SPY, QQQ, IWM, DIA)
        await self._run_one(...)         # SEQUENTIAL await
```

Each `_run_one()` call:
1. `get_expirations(symbol)` → 1 Tradier call (cached 300s)
2. Loop over **all** expirations: `for exp in expirations: await get_analysis_inputs(...)` → **SEQUENTIAL** per-expiration
3. Each `get_analysis_inputs()` fires 3 parallel tasks internally: quote (cached 10s), chain (cached 60s), VIX

**Total scanner iterations:** 11 scanner_keys × 4 symbols = **44 sequential `_run_one()` calls**

**Per `_run_one()` Tradier calls** (assuming ~8-12 expirations per symbol):
- 1 `get_expirations` (cached after first call per symbol)
- ~10 `get_option_chain` calls (many cached within 60s TTL if same symbol scanned within the window)
- ~10 `get_quote` calls (cached within 10s — will expire between scanner keys)
- 1 VIX call per expiration (FRED, cached 300s)

**Redundancy factor:** The same symbol's chains are fetched up to 11 times (once per scanner_key). Due to 60s chain cache TTL:
- If all 44 iterations for a symbol complete within 60s → perfect cache hits after first key
- If the scan takes >60s per symbol (common at 2 req/sec) → **cache expires, chains re-fetched**
- With 4 symbols at ~10 expirations each, even within one scanner_key: 40 sequential `get_analysis_inputs` calls = 40 × 0.5s rate limit = 20s minimum

**Estimated total Tradier calls for options scan:**
- Worst case (no caching): 44 × (1 + 10 + 10) ≈ 924 calls
- Realistic (with caching): ~150–250 actual Tradier calls (expirations cached well, chains partially cached, quotes mostly expired)
- At 2 req/sec: **75–125 seconds** minimum just for rate limiting

**Model analysis:** 15 candidates × ThreadPoolExecutor(4) = ~4 batches × 10-30s per LLM call = 40–120s

**DIAGNOSTIC FILE WRITES:** Each `_run_one()` writes a JSON diagnostic file to `results/diagnostics/chain_diag_*.json`. With 44 iterations per run, this accumulates files rapidly. The file I/O itself is minor but the diagnostic code adds unnecessary overhead in production.

### 2.3 Stage 3 — Active Trade Pipeline (7 sub-stages)

**File:** `active_trade_pipeline.py` line 1339

| Sub-stage | Work | Parallelism | Notes |
|---|---|---|---|
| 1. load_positions | Tradier account positions | 1 call | Fast |
| 2. load_market_context | Market picture | 1 call | Fast |
| 3. build_packets | Greeks refresh via chain fetches | **SEQUENTIAL** per position | Variable |
| 4. deterministic_engine | Rule-based analysis | Sync | Fast |
| 5. model_analysis | LLM per position | ThreadPoolExecutor(min(N, 4)) | Depends on position count |
| 6. normalize_results | Shape output | Sync | Fast |
| 7. complete | File write | Sync | Fast |

**Packet building** (stage 3): Each position requires a chain fetch to refresh Greeks. This is sequential per position. With ~5-15 open positions, this means 5-15 sequential chain fetches at 0.5s each = 2.5-7.5s.

**Model analysis** (stage 5): Parallel dispatch via `ThreadPoolExecutor(max_workers=min(N, 4))` with `asyncio.gather`. Good parallelism. With 10 positions: 3 batches × ~15-30s = 45-90s.

**Has per-stage timing instrumentation** via `_start_stage()` / `_complete_stage()` with `duration_ms`.

### 2.4 Stage 4 — Portfolio Balancing Runner (8 sub-stages)

**File:** `portfolio_balancing_runner.py`

- Receives active trade results from Stage 3 (passed as parameter — no re-fetch)
- Primarily computational: concentration analysis, delta budgets, Greek aggregation
- Has full per-stage `duration_ms` timing
- **Fastest stage** — typically <5s total, no external API calls

---

## 3. Global Infrastructure

### 3.1 Tradier Rate Limiter

**File:** `tradier_client.py` line 25

```python
_DEFAULT_MAX_PER_SECOND = 2.0  # conservative for Tradier's 120/min limit
```

- **Type:** Leaky bucket via `_AsyncRateLimiter`
- **Effect:** Minimum 500ms between ANY two Tradier API calls, globally
- **429 retry:** Up to 3 retries with exponential backoff (2s, 4s, 8s, cap 30s)
- **Scope:** ONE rate limiter per `TradierClient` instance — all methods (quotes, chains, candles, expirations) share it

**Impact:** The rate limiter is the true global bottleneck. At 2 req/sec, the theoretical minimum time for N Tradier calls is `N × 0.5s`. Since all 4 stages run sequentially and share one `TradierClient`, the rate limiter serializes ALL data fetches across the entire refresh.

Tradier's actual limit is 120 req/min = 2 req/sec, so the rate limiter is correctly configured but creates a hard floor on total wall-clock time.

### 3.2 Cache TTLs

| Data Type | TTL | Source |
|---|---|---|
| Quote | 10s | `QUOTE_CACHE_TTL_SECONDS` |
| Expirations | 300s (5 min) | `EXPIRATIONS_CACHE_TTL_SECONDS` |
| Option Chain | 60s | `CHAIN_CACHE_TTL_SECONDS` |
| Candles (OHLCV) | 1800s (30 min) | `CANDLES_CACHE_TTL_SECONDS` |
| FRED data | 300s (5 min) | `FRED_CACHE_TTL_SECONDS` |

**Key observation:** The 60s chain TTL is too short for the options scan. With 44 sequential iterations at 2 req/sec, the scan takes well over 60s, causing chain cache misses for later scanner_keys scanning the same symbol.

### 3.3 Model Routing

**File:** `model_routing_config.py`

- Per-provider execution gate: `max_concurrency = 1` by default (`_SAFE_DEFAULT_MAX_CONCURRENCY`)
- Model timeout: 180s per call
- 2-pass retry with 3s sleep between passes
- Dispatch: `ThreadPoolExecutor(max_workers=4)` with `asyncio.Semaphore(4)` in runners

**Effective parallelism:** With per-provider `max_concurrency=1`, if using a single provider (e.g., only Bedrock), model calls are effectively **serialized** despite the ThreadPoolExecutor. True parallel execution only happens when multiple providers are configured.

---

## 4. Estimated Total API Call Budget

| Stage | Tradier Calls | LLM Calls | Notes |
|---|---|---|---|
| Stock Scan | ~350–500 | ~20 | Bar fetches, declining via cache |
| Options Scan | ~150–250 | ~15 | Chain/quote/expiration fetches |
| Active Trades | ~10–30 | ~5–15 | Per-position chain refresh |
| Portfolio Balance | 0 | 0 | Reuses Stage 3 data |
| **Total** | **~510–780** | **~40–50** |

**Rate limiter floor:** 510–780 calls ÷ 2/sec = **255–390 seconds (4.3–6.5 min)** of pure rate-limiting wait time, even with zero processing overhead.

**LLM time:** 40–50 calls with per-provider `max_concurrency=1` and ThreadPool(4):
- Single provider: effectively serial → 40 × ~15s = 600s (~10 min)
- Multi-provider: up to 4 concurrent → ~150s (~2.5 min)

**Total wall-clock estimate:** 8–20 minutes depending on provider concurrency and cache hit rates.

If chain caches expire mid-scan (60s TTL exceeded), options scan alone can inflate to 10+ minutes.

---

## 5. Bottleneck Root Causes (Ranked by Impact)

### #1 — CRITICAL: Options Scanner Sequential Nested Loops ⏱ ~5–12 min

**Location:** `options_scanner_service.py` lines 100-130

The scan loop is `for scanner_key × for symbol → await _run_one()` — fully sequential. Each `_run_one()` internally loops `for exp in expirations → await get_analysis_inputs()` — also sequential. This creates a triple-nested sequential chain: **scanner_key × symbol × expiration**.

The same symbol's chains are fetched redundantly across scanner_keys. Scanner key 1 fetches SPY chains, scanner key 2 fetches SPY chains again (maybe from cache, maybe not — 60s TTL).

**Why it matters:** With 11 × 4 × ~10 = 440 total iterations, each hitting the rate limiter, this dominates the entire refresh.

### #2 — HIGH: Frontend Sequential Stage Chain ⏱ adds ~0% throughput

**Location:** `trade_management_center.js` line 2458

Stages 1 (Stock), 2 (Options), and 3 (Active Trades) are completely independent — they fetch different data, use different API endpoints, and write to different UI sections. Running them in parallel would overlap their execution.

Stage 4 (Portfolio Balance) depends on Stage 3 output, so it must remain sequential after Active Trades.

### #3 — HIGH: Stock Scanners Sequential ⏱ ~3–4 min

**Location:** `stock_engine_service.py` line 121

4 scanners run SEQUENTIALLY to avoid API exhaustion. This is a reasonable safeguard but means ~196 × 4 = 784 potential bar fetches serialized across scanners (with cache reducing actual calls to ~350–500).

### #4 — MEDIUM: Model Provider Serialization ⏱ variable

**Location:** `model_routing_config.py` line 37

`_SAFE_DEFAULT_MAX_CONCURRENCY = 1` means each provider handles only 1 LLM request at a time. With a single configured provider, the ThreadPoolExecutor(4) is useless — all 4 threads queue behind the same provider's gate.

### #5 — LOW: Chain Cache TTL Too Short ⏱ causes re-fetches

**Location:** `config.py` line 78

`CHAIN_CACHE_TTL_SECONDS = 60` is too aggressive for the options scan, which takes well over 60s. Chains fetched early in the scan expire before later scanner_keys can reuse them.

### #6 — LOW: Diagnostic File Writes in Scanner ⏱ ~1-2s total

**Location:** `options_scanner_service.py` lines 200-220

44 JSON file writes per scan run. Minor but unnecessary I/O in production.

---

## 6. Optimisation Opportunities

### 6.1 Quick Wins (minimal code changes)

#### QW-1: Frontend Parallel Stages 1+2+3

**Impact:** ~40-50% wall-clock reduction
**Effort:** Small — change `.then()` chain to `Promise.all()`
**Risk:** Low — stages are independent

```js
// Current: sequential
api.tmcRunStock().then(...).then(api.tmcRunOptions).then(...)

// Proposed: parallel for stages 1-3
Promise.all([
  runStockStage(),
  runOptionsStage(),
  runActiveTradesStage()
]).then(([stock, options, active]) => {
  return runPortfolioBalance(active);  // stage 4 depends on 3
});
```

**Caveat:** Stages 1-3 will all compete for the SAME Tradier rate limiter (2 req/sec shared). The total Tradier call budget doesn't shrink — but LLM calls and non-Tradier processing overlap, saving the sequential wait between stages.

**True gain with shared rate limiter:** Overlaps model analysis time (~5-10 min) with data fetching in other stages. Estimated real-world savings: 3-8 minutes depending on model call counts.

#### QW-2: Increase Chain Cache TTL to 300s

**Impact:** Eliminates redundant chain re-fetches across scanner_keys
**Effort:** One-line config change
**Risk:** Low — chains don't change within 5 minutes during a scan

```python
CHAIN_CACHE_TTL_SECONDS: int = 300  # was 60
```

This ensures chains fetched for scanner_key 1 are still cached when scanner_key 11 processes the same symbol.

#### QW-3: Increase Quote Cache TTL to 60s

**Impact:** Reduces quote re-fetches across scanner iterations
**Effort:** One-line config change
**Risk:** Low for scanning (quotes used for construction, not execution)

```python
QUOTE_CACHE_TTL_SECONDS: int = 60  # was 10
```

#### QW-4: Remove Diagnostic File Writes

**Impact:** Minor — eliminates 44 JSON file writes per scan
**Effort:** Delete the diagnostic block in `options_scanner_service.py` lines 170-220
**Risk:** None — this was explicitly marked as TEMPORARY

### 6.2 Medium Fixes (moderate refactoring)

#### MF-1: Options Chain Prefetch — Symbol-First Architecture

**Impact:** ~60-70% reduction in options scan time
**Effort:** Moderate — restructure scan loop

Instead of: `for scanner_key → for symbol → fetch chains → run scanner`

Do: `for symbol → prefetch ALL chains once → for scanner_key → run scanner with cached chains`

```python
async def scan(self, symbols, scanner_keys, context):
    # Phase 1: Prefetch all chains per symbol (once)
    chain_store = {}
    for symbol in symbols:
        expirations = await self._bds.tradier_client.get_expirations(symbol)
        chains = {}
        for exp in expirations:
            chains[exp] = await self._bds.get_analysis_inputs(symbol, exp, include_prices_history=False)
        chain_store[symbol] = chains

    # Phase 2: Run all scanners using prefetched data
    for scanner_key in scanner_keys:
        for symbol in symbols:
            result = scanner.run(scanner_key, symbol, chain_store[symbol], ...)
            all_results.append(result)
```

This reduces Tradier calls from ~150-250 to ~40-50 (4 symbols × 10 expirations) and eliminates the 11x redundancy.

#### MF-2: Concurrent Per-Symbol Fetches Within Each Scanner

**Impact:** ~50% reduction in per-scanner data fetch time
**Effort:** Small — change sequential loop to `asyncio.gather()` with semaphore

Within each stock scanner, symbols are already scanned concurrently (Semaphore(8)). Apply the same pattern to the options scanner's per-expiration loop:

```python
# Instead of sequential:
for exp in expirations:
    inputs = await self._bds.get_analysis_inputs(...)

# Use asyncio.gather:
tasks = [self._bds.get_analysis_inputs(symbol, exp, ...) for exp in expirations]
results = await asyncio.gather(*tasks)
```

**Caveat:** Still constrained by the 2 req/sec rate limiter, so actual speedup is limited unless the rate limit is also increased.

#### MF-3: Increase Tradier Rate Limit

**Impact:** Proportional speedup across ALL stages
**Effort:** One-line config change + monitoring

Tradier's documented limit is 120 req/min (production API). The current setting of `2.0 req/sec` is correctly matched. However, their actual enforcement may allow bursting. Options:

- Production accounts often have higher limits (check with Tradier)
- Increase to `3.0 req/sec` with 429 backoff as safety net (already implemented)
- Consider separate rate limiters for different call types (chains vs quotes vs candles)

### 6.3 Larger Refactors

#### LR-1: Decouple Rate Limiters by Call Type

**Impact:** Significant — allows chain fetches and candle fetches to proceed in parallel buckets
**Effort:** Moderate — create per-endpoint rate limiter instances

Currently ONE `_AsyncRateLimiter` instance gates ALL Tradier calls. Chains, quotes, candles, and expirations all compete for the same 2 req/sec budget. Tradier likely counts them as a single rate limit, but testing could reveal per-endpoint headroom.

#### LR-2: Background Pre-Computation

**Impact:** Perceived latency → near-zero
**Effort:** Significant — requires job scheduler, staleness tracking

Run Stock and Options scans on a timer (e.g., every 15min during market hours). "Full Refresh" would just display the latest cached results or trigger a delta refresh.

#### LR-3: Batch Chain Fetch API

**Impact:** Dramatic — reduce N calls to 1
**Effort:** Depends on Tradier API capabilities

Check if Tradier's API supports fetching chains for multiple symbols or multiple expirations in a single request. Their multi-quote endpoint (`/v1/markets/quotes?symbols=SPY,QQQ,IWM,DIA`) accepts comma-separated symbols — verify if the chains endpoint does too.

---

## 7. Timing Estimates Summary

| Scenario | Stock | Options | Active | Balance | Total |
|---|---|---|---|---|---|
| **Current (sequential)** | 5-9 min | 8-15 min | 3-8 min | <5s | **16-32 min** |
| **+ QW-1 (parallel stages)** | — | — | — | — | **8-15 min** |
| **+ QW-2/3 (cache TTLs)** | 5-9 min | 5-10 min | 3-8 min | <5s | **6-12 min** |
| **+ MF-1 (chain prefetch)** | 5-9 min | 2-5 min | 3-8 min | <5s | **5-9 min** |
| **All quick+medium wins** | — | — | — | — | **3-6 min** |

*Timing ranges depend on cache warm state, LLM response times, and Tradier API latency.*

---

## 8. Existing Instrumentation

| Component | Has Timing? | Location |
|---|---|---|
| Stock scanners | Per-scanner `elapsed_ms` | `stock_engine_service.py` |
| Options runner | Per-stage timing in metadata | `options_opportunity_runner.py` |
| Active trade pipeline | Per-stage `duration_ms` via `_start_stage()`/`_complete_stage()` | `active_trade_pipeline.py` |
| Portfolio balancing | Per-stage `duration_ms` via `_elapsed_ms()` | `portfolio_balancing_runner.py` |
| Frontend | Status text only ("Running stock scan…") | `trade_management_center.js` |
| Rate limiter | Debug log only | `tradier_client.py` |
| Scanner service | Diagnostic JSON file writes (TEMP) | `options_scanner_service.py` |

**Gap:** No aggregate timing summary across all 4 stages. Frontend doesn't log per-stage durations. No dashboard or console summary of total refresh time.

---

## 9. Recommended Action Order

| Priority | Action | Impact | Effort |
|---|---|---|---|
| 1 | QW-2: Chain cache TTL → 300s | High | 1 line |
| 2 | QW-3: Quote cache TTL → 60s | Medium | 1 line |
| 3 | QW-4: Remove diagnostic file writes | Low | Delete block |
| 4 | QW-1: Frontend parallel stages 1-3 | High | ~30 lines JS |
| 5 | MF-1: Symbol-first chain prefetch | Very High | ~100 lines Python |
| 6 | MF-2: Concurrent per-expiration fetches | Medium | ~20 lines Python |
| 7 | MF-3: Test higher rate limit | Variable | 1 line + testing |
| 8 | LR-2: Background pre-computation | Transformative | Architecture work |

---

## 10. Key Code Locations

| File | Lines | What |
|---|---|---|
| `trade_management_center.js` | 2434–2530 | `handleFullRefresh()` sequential chain |
| `options_scanner_service.py` | 85–280 | Sequential nested loops (THE bottleneck) |
| `options_opportunity_runner.py` | 510–600, 1354–1680 | Options 7-stage pipeline + model analysis |
| `stock_opportunity_runner.py` | 401–500, 1260–1370 | Stock 8-stage pipeline + model dispatch |
| `stock_engine_service.py` | 85–180 | Sequential scanner execution |
| `active_trade_pipeline.py` | 1339–1500, 1710–1760 | Active trade 7-stage pipeline |
| `tradier_client.py` | 1–60 | Rate limiter (2 req/sec leaky bucket) |
| `base_data_service.py` | 740–850 | `get_analysis_inputs()` — parallel sub-tasks |
| `config.py` | 76–80 | Cache TTL settings |
| `model_routing_config.py` | 37, 135, 181 | Provider concurrency gates |
