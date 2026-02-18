# Backend Performance Report

**Branch:** `chore/app-cleanup-phase0`
**Date:** 2025-02-17

## Summary

Four targeted optimizations to reduce redundant API calls, bound cache memory,
eliminate repeated math, and lower log noise. No behaviour or contract changes.

---

## 1. Cache `get_expirations()` (highest impact)

**Problem:** `TradierClient.get_expirations()` was the only data method without
caching. It is called from 6 locations (strategy_service, stock_analysis_service
×2, signal_service, report_service ×2). A typical 18-symbol scan triggered 18+
uncached HTTP round-trips per cycle.

**Fix:** Wrapped `get_expirations` in `cache.get_or_set` with key
`tradier:expirations:{symbol}` and a new `EXPIRATIONS_CACHE_TTL_SECONDS = 300`
(5 min). Expirations change daily; 5 min is safe and eliminates nearly all
redundant calls.

**Files changed:**
- `app/config.py` — added `EXPIRATIONS_CACHE_TTL_SECONDS`
- `app/clients/tradier_client.py` — wrapped `get_expirations` body in `_load()`
  closure + `cache.get_or_set`

**Expected savings:** ~15-17 fewer Tradier HTTP calls per scan cycle.

---

## 2. Bound TTLCache max size

**Problem:** `TTLCache` grew unboundedly; entries only expired by TTL. A
long-running process scanning many symbols could accumulate stale keys
indefinitely.

**Fix:** Added `maxsize=1024` parameter (default). When at capacity on a new
insert, expired entries are purged first; if still full, the entry with the
nearest expiry is evicted.

**Files changed:**
- `app/utils/cache.py` — added `maxsize`, `_evict_expired()`, and
  eviction-on-set logic

**Risk:** None. Default of 1024 keys is well above normal workload (~100 keys
during a full scan). Existing `TTLCache()` call in `app/main.py` uses the
default automatically.

---

## 3. Pre-compute realized vol per snapshot

**Problem:** Three strategy plugins (debit_spreads, iron_condor, income) each
re-computed realized volatility from the same `prices_history` list for every
candidate in the enrichment loop. With 50–200 candidates sharing 3-6 snapshots,
this caused hundreds of redundant log-return + stdev calculations.

**Fix:** Added a per-call `_rv_cache: dict[int, float | None]` keyed by
`id(snapshot)` in each plugin's `enrich()` method. RV is computed once per
unique snapshot object and reused for all candidates referencing it.

**Files changed:**
- `app/services/strategies/debit_spreads.py`
- `app/services/strategies/iron_condor.py`
- `app/services/strategies/income.py`

**Risk:** None. `id(snapshot)` is safe because all candidates reference the same
dict instance created in `build_candidates()`. The cache is local to each
`enrich()` invocation (no cross-request leakage).

---

## 4. Reduce logging noise in report pipeline

**Problem:** `report_service.generate_live_report()` emitted ~8 `logger.info`
lines per symbol × expiration combination. A 6-symbol, 6-expiration scan
produced ~288 info-level log lines, most of which duplicate the SSE progress
callbacks already sent to the frontend.

**Fix:** Downgraded 7 per-expiration log calls from `info` to `debug`:
- `underlying_expiration_start`
- `underlying_expirations_selected`
- `underlying_analysis_no_data`
- `underlying_chain_loaded`
- `underlying_tradeability_rejected`
- `symbol_candidates_generated` (both 0-count and N-count variants)
- `expiration_filter_result`

Kept per-symbol summary (`symbol_filter_result`) and pipeline-level start/end
at `info`.

**Files changed:**
- `app/services/report_service.py`

**Risk:** None. All messages are still emitted at `debug` level and visible when
log level is set to DEBUG. Progress callbacks to the frontend are unaffected.

---

## Not changed (assessed and deferred)

| Area | Reason |
|------|--------|
| Cross-service snapshot sharing | Would require a request-scoped context object; deferred to a future refactor |
| `enrich_trades_batch` adoption in all plugins | Each plugin's enrich loop has strategy-specific logic; forcing through the generic batch path would obscure intent |
| FinnhubClient caching | Finnhub is only a fallback; call volume is negligible |
| butterflies/calendars RV | These plugins don't compute RV at all — no savings available |
