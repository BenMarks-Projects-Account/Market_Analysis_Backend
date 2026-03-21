# Audit 1D: Market Context Service & Freshness Tracking

> **Generated**: 2026-03-20  
> **Scope**: `MarketContextService`, cache layer, metric envelope, staleness detection, degradation cascading, market hours awareness, concurrent access  
> **Key files**: `app/services/market_context_service.py`, `app/utils/cache.py`, `app/workflows/architecture.py`, `app/workflows/market_intelligence_runner.py`, `app/trading/risk.py`, `app/clients/fred_client.py`, `app/services/confidence_framework.py`

---

## Table of Contents

1. [Service Architecture](#1-service-architecture)
2. [Source Priority Chain](#2-source-priority-chain)
3. [Cache Behavior](#3-cache-behavior)
4. [Metric Envelope](#4-metric-envelope)
5. [Staleness Detection](#5-staleness-detection)
6. [Degradation Cascading](#6-degradation-cascading)
7. [Market Hours Awareness](#7-market-hours-awareness)
8. [Concurrent Access](#8-concurrent-access)
9. [Data Provider Envelope Extraction Patterns](#9-data-provider-envelope-extraction-patterns)
10. [Confidence Framework Integration](#10-confidence-framework-integration)
11. [Summary of Findings](#11-summary-of-findings)

---

## 1. Service Architecture

### 1.1 Class & Constructor

**File**: `app/services/market_context_service.py` (lines 82–91)

```python
class MarketContextService:
    def __init__(
        self,
        fred_client: FredClient,
        finnhub_client: FinnhubClient | None,
        cache: TTLCache,
        tradier_client: Any | None = None,
    ) -> None:
        self.fred = fred_client
        self.finnhub = finnhub_client
        self.tradier = tradier_client
        self.cache = cache
```

**Dependencies injected**:
- `fred_client` — FRED API for economic series (yields, rates, commodities)
- `finnhub_client` — Finnhub for real-time quotes (VIX fallback)
- `tradier_client` — Tradier for live intraday quotes (VIX primary)
- `cache` — Shared `TTLCache` instance

### 1.2 Instantiation

**File**: `app/main.py` (lines 275–281)

```python
market_context_service = MarketContextService(
    fred_client=fred_client,
    finnhub_client=finnhub_client,
    cache=cache,
    tradier_client=tradier_client,
)
app.state.market_context_service = market_context_service
```

All clients share the **same `TTLCache` instance** (constructed at line ~160 as `cache = TTLCache(maxsize=10000)`). This means VIX cached by MarketContextService and VIX cached by TradierClient share the same store (different keys).

### 1.3 Public API

| Method | Returns | Caching | Purpose |
|--------|---------|---------|---------|
| `get_market_context()` | Full dict of metric envelopes | 30s TTL via `cache.get_or_set()` | Primary consumer entry point — all 6 MI data providers call this |
| `get_flat_macro()` | Flat dict with `_freshness` metadata section | Delegates to `get_market_context()` | Backward-compatible REST API shape for `/api/stock/macro` |

### 1.4 Consumers

The **same singleton** `market_context_service` is injected into:

| Consumer | Injection path (`main.py`) | Call method |
|----------|---------------------------|-------------|
| Cross-Asset Macro Data Provider | `CrossAssetMacroDataProvider(market_context_service=...)` line 315 | `get_market_context()` |
| Flows Positioning Data Provider | `FlowsPositioningDataProvider(market_context_service=...)` line 325 | `get_market_context()` |
| Liquidity Conditions Data Provider | `LiquidityConditionsDataProvider(market_context_service=...)` line 334 | `get_market_context()` |
| Volatility Options Data Provider | `VolatilityOptionsDataProvider(market_context_service=...)` line 303 | `get_market_context()` |
| News Sentiment Service | `NewsSentimentService(market_context_service=...)` line 287 | `get_market_context()` |
| MI Runner (Stage 1: collect_inputs) | `MarketIntelligenceDeps(market_context_service=...)` line 370 | `get_market_context()` |
| REST API route `/api/stock/macro` | `request.app.state.market_context_service` | `get_flat_macro()` |

**Note**: Breadth Data Provider does **NOT** call `get_market_context()`. It fetches directly from Tradier via `get_daily_bars()` — no macro context needed.

---

## 2. Source Priority Chain

### 2.1 VIX — Three-Source Waterfall

**File**: `app/services/market_context_service.py` (lines 202–208)

```python
# VIX: try Tradier first, then Finnhub, then FRED
vix_metric = await self._vix_from_tradier()
if vix_metric is None:
    vix_metric = await self._vix_from_finnhub()
if vix_metric is None:
    vix_metric = await self._vix_from_fred()
```

The waterfall is **sequential**, not parallel. Each source is tried only if the previous returned `None`.

### 2.2 Tradier VIX (Primary)

**Lines 94–119**:
```python
async def _vix_from_tradier(self) -> dict[str, Any] | None:
    if not self.tradier:
        return None
    try:
        quote = await self.tradier.get_quote("VIX")
        last = quote.get("last")
        if last is not None and float(last) > 0:
            prev_close = quote.get("prevclose") or quote.get("previous_close")
            prev_close = round(float(prev_close), 2) if prev_close is not None else None
            val = round(float(last), 2)
            return _metric(
                value=val,
                source="tradier",
                is_intraday=True,
                previous_close=prev_close,
            )
    except Exception as exc:
        logger.debug("[MARKET_CONTEXT] tradier_vix_unavailable error=%s", exc)
    return None
```

**Validation**: Checks `last is not None and float(last) > 0`. Negative or zero values are rejected.

**Fallback trigger**: Returns `None` on any exception, missing `last` field, or zero/negative value.

### 2.3 Finnhub VIX (Secondary)

**Lines 122–153**:
```python
async def _vix_from_finnhub(self) -> dict[str, Any] | None:
    if not self.finnhub:
        return None
    try:
        quote = await self.finnhub.get_quote("VIX")
        current = quote.get("c")     # Finnhub 'c' = current price
        prev_close = quote.get("pc") # Finnhub 'pc' = previous close
        ts = quote.get("t")          # Finnhub 't' = unix timestamp
        if current is not None and float(current) > 0:
            val = round(float(current), 2)
            pc = round(float(prev_close), 2) if prev_close else None
            src_ts = None
            if ts and int(ts) > 0:
                src_ts = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
            return _metric(
                value=val,
                source="finnhub",
                is_intraday=True,
                previous_close=pc,
                source_timestamp=src_ts,
            )
    except Exception as exc:
        logger.debug("[MARKET_CONTEXT] finnhub_vix_unavailable error=%s", exc)
    return None
```

**Enrichment vs Tradier**: Finnhub provides `source_timestamp` (exchange timestamp), which Tradier does not capture.

### 2.4 FRED VIX (Tertiary / EOD Fallback)

**Lines 156–171**:
```python
async def _vix_from_fred(self) -> dict[str, Any]:
    try:
        obs = await self.fred.get_series_with_date("VIXCLS")
        if obs:
            return _metric(
                value=obs["value"],
                source="fred",
                observation_date=obs["observation_date"],
                is_intraday=False,
            )
    except Exception as exc:
        logger.warning("[MARKET_CONTEXT] fred_vix_failed error=%s", exc)
    return _metric(None, "fred", is_intraday=False)
```

**Critical**: This is the only VIX source that returns an `observation_date`. If all three sources fail, the final fallback returns `_metric(None, "fred", is_intraday=False)` — value is None but it **does not raise**. Silent degradation.

### 2.5 All Other Metrics — FRED Only

**Lines 210–222**:
```python
ten_year, two_year, thirty_year, fed_funds, oil, usd = await asyncio.gather(
    self._fred_metric("DGS10"),
    self._fred_metric("DGS2"),
    self._fred_metric("DGS30"),
    self._fred_metric("DFF"),
    self._fred_metric("DCOILWTICO"),
    self._fred_metric("DTWEXBGS"),
)
```

All 6 non-VIX metrics are FRED-only with **no fallback chain**. If FRED is down for any series, the metric returns `_metric(None, "fred", is_intraday=False)`.

### 2.6 Fallback Trigger Conditions

| Condition | Triggers fallback? |
|-----------|--------------------|
| API call throws exception | **Yes** — caught, returns None |
| API returns None/missing field | **Yes** — returns None |
| API returns zero or negative value | **Yes** — `float(last) > 0` check |
| API returns stale data | **No** — no staleness check at this layer |
| API returns data from wrong date | **No** — no date validation |
| API is slow but eventually responds | **No** — waits indefinitely (no per-call timeout at this layer) |

> **FLAG [HIGH]**: Fallback triggers only on error/missing. Stale data from Tradier (e.g., Friday's close on Monday morning) does NOT trigger fallback to Finnhub or FRED. The system treats any non-zero value as valid regardless of age.

---

## 3. Cache Behavior

### 3.1 Cache Implementation

**File**: `app/utils/cache.py` (full file, 57 lines)

```python
class TTLCache:
    def __init__(self, maxsize: int = 1024) -> None:
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()
        self._maxsize = maxsize

    def _is_expired(self, expires_at: float) -> bool:
        return expires_at <= time.time()

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            expires_at, value = item
            if self._is_expired(expires_at):
                self._store.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        async with self._lock:
            if len(self._store) >= self._maxsize and key not in self._store:
                self._evict_expired()
                if len(self._store) >= self._maxsize:
                    oldest_key = min(self._store, key=lambda k: self._store[k][0])
                    del self._store[oldest_key]
            self._store[key] = (time.time() + ttl_seconds, value)

    async def get_or_set(self, key, ttl_seconds, loader):
        cached = await self.get(key)
        if cached is not None:
            return cached
        loaded = await loader()
        await self.set(key, loaded, ttl_seconds)
        return loaded
```

### 3.2 What's Cached

| Cache Layer | Key Pattern | TTL | Config |
|-------------|-------------|-----|--------|
| **Market Context (bulk)** | `"market_context:latest"` | **30 seconds** | Hardcoded in service (line 49) |
| Tradier quotes | `"tradier:quote:{symbol}"` | 10 seconds | `QUOTE_CACHE_TTL_SECONDS` |
| Tradier option chains | `"tradier:chain:{symbol}:{exp}"` | 60 seconds | `CHAIN_CACHE_TTL_SECONDS` |
| Tradier expirations | `"tradier:expirations:{symbol}"` | 300 seconds | `EXPIRATIONS_CACHE_TTL_SECONDS` |
| FRED series | `"fred:series:{id}:obs"` | **300 seconds** | `FRED_CACHE_TTL_SECONDS` |
| Tradier candles | `"tradier:candles:{symbol}:{start}:{end}"` | 1800 seconds | `CANDLES_CACHE_TTL_SECONDS` |

### 3.3 Layered Caching Behavior

The Market Context cache (30s) sits **on top of** the FRED cache (300s). This means:

1. First call → cache miss on both → fetches from FRED API → caches at FRED layer (300s) → caches at Market Context layer (30s)
2. Within 30s → returns Market Context cached result (no FRED call)
3. After 30s but within 300s → Market Context cache miss → rebuilds context → FRED cache hit (returns cached FRED data) → new Market Context cache
4. After 300s → both expire → full FRED API fetch

**Consequence**: Even when market context cache expires every 30s, FRED data may be up to 5 minutes stale from the API's perspective. The `fetched_at` timestamp in the metric envelope is set when the envelope is **constructed** (during context build), NOT when the FRED API was last called.

> **FLAG [CRITICAL]**: `fetched_at` in the metric envelope (line 77: `datetime.now(timezone.utc).isoformat()`) is set each time `_metric()` is called during context rebuild, NOT when the underlying data was actually fetched from the API. If FRED data is served from the 300-second cache, `fetched_at` still shows "now". This makes `fetched_at` unreliable for freshness assessment — it reflects envelope construction time, not API call time.

### 3.4 Can Consumers Distinguish Cached vs Fresh?

**Partially**. The `_freshness` section in `get_flat_macro()` output includes `source`, `freshness`, and `observation_date`, which can indicate data age. However:

- **No `is_from_cache` flag** exists in the envelope
- `fetched_at` is misleading (see above — always shows rebuild time, not fetch time)
- The FRED `observation_date` is the true indicator of data age but requires the consumer to compare it against current date

### 3.5 Serve-Stale Behavior

**There is no "serve stale" mode.** When the cache expires and the upstream source is down:

1. `get_or_set()` calls the loader
2. Loader catches exception → returns `_metric(None, source, ...)`
3. `None` value is cached for the next 30/300 seconds
4. Until the next cache cycle, every consumer receives `None`

> **FLAG [MEDIUM]**: After an upstream failure, the system caches `None` for the full TTL period. There is no fallback to a previous valid value. A 5-minute FRED outage during a market context rebuild means 5 minutes of `None` for all FRED-sourced metrics.

### 3.6 Thundering Herd

The `get_or_set()` method in `TTLCache` has a **thundering herd vulnerability**:

```python
async def get_or_set(self, key, ttl_seconds, loader):
    cached = await self.get(key)    # Lock released after get
    if cached is not None:
        return cached
    loaded = await loader()          # No lock here — loader runs unlocked
    await self.set(key, loaded, ttl_seconds)
    return loaded
```

Between `get()` returning `None` and `set()` storing the result, multiple coroutines can enter `loader()` simultaneously. In practice, this is mitigated by:
- The 30-second market context TTL being longer than the typical build time (~1–3s)
- The async event loop serializing CPU-bound portions

But under load, duplicate API calls are possible.

---

## 4. Metric Envelope

### 4.1 Structure

**File**: `app/services/market_context_service.py` (lines 57–79)

```python
def _metric(
    value: float | None,
    source: str,
    observation_date: str | None = None,
    is_intraday: bool = False,
    previous_close: float | None = None,
    source_timestamp: str | None = None,
) -> dict[str, Any]:
    if is_intraday:
        freshness = "intraday"
    elif observation_date:
        freshness = "eod"
    else:
        freshness = "delayed"
    return {
        "value": value,
        "previous_close": previous_close,
        "source": source,
        "freshness": freshness,
        "is_intraday": is_intraday,
        "observation_date": observation_date,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source_timestamp": source_timestamp,
    }
```

### 4.2 Field-by-Field Analysis

| Field | Always Populated? | Format | Who Sets It | Semantics |
|-------|-------------------|--------|-------------|-----------|
| `value` | **No** — `None` on failure | `float \| None` | `_metric()` caller | The actual metric value |
| `previous_close` | **No** — only for VIX from Tradier/Finnhub | `float \| None` | VIX fetch methods | Prior session close |
| `source` | **Yes** — always provided | `str` — `"tradier"`, `"finnhub"`, `"fred"`, `"derived (10Y-2Y)"` | `_metric()` caller | Which API sourced the data |
| `freshness` | **Yes** — derived from inputs | `str` — `"intraday"`, `"eod"`, `"delayed"` | `_metric()` logic | Freshness category. See below. |
| `is_intraday` | **Yes** | `bool` | `_metric()` caller | True for live Tradier/Finnhub, False for FRED |
| `observation_date` | **No** — only for FRED sources | `str \| None` — `"YYYY-MM-DD"` | FredClient from FRED API `date` field | Calendar date of the observation **in the market** |
| `fetched_at` | **Yes** — always UTC ISO | `str` — ISO-8601 | `_metric()` at construction (line 77) | **Misleading** — see §3.3 |
| `source_timestamp` | **Rarely** — only Finnhub VIX | `str \| None` — ISO-8601 | `_vix_from_finnhub()` from Finnhub `t` field | Exchange/provider timestamp |

### 4.3 `observation_date` vs `fetched_at` Semantics

**`observation_date`**:
- Comes from FRED API's `date` field
- Represents the calendar date the value was **OBSERVED** in the market
- Example: DGS10 observation_date = "2026-03-18" means this was Tuesday's 10Y yield, published Wednesday
- Only populated for FRED series; `None` for Tradier/Finnhub quotes

**`fetched_at`**:
- Set to `datetime.now(timezone.utc).isoformat()` when `_metric()` is called
- Represents when the **envelope was constructed**, NOT when the API was called
- Due to layered caching, FRED data can be 0–300 seconds old when the envelope is rebuilt, but `fetched_at` always shows "now"

> **FLAG [CRITICAL]**: These two timestamps serve different purposes but the system conflates them downstream. The MI runner's `_build_freshness_section()` uses `fetched_at` to compute `age_seconds` for staleness tiers — but since `fetched_at` is always "now" at envelope construction, every metric looks "fresh" regardless of actual data age. Only `observation_date` reflects true data currency, and it's only available for FRED series.

### 4.4 `freshness` Classification Logic

```
if is_intraday:       → "intraday"
elif observation_date: → "eod"
else:                  → "delayed"
```

**Problem**: The `freshness` field is set based on **source type**, not actual staleness. An "intraday" VIX quote from Tradier at 3pm Friday is still labeled "intraday" when served from cache at 9am Monday. A FRED value with `observation_date = "2026-03-10"` (10 days old) is labeled "eod" identically to a value from yesterday.

> **FLAG [HIGH]**: `freshness` is a source-type label, not an age indicator. "intraday" means "this came from a live-quote source", not "this data is current". There is no mechanism to downgrade freshness based on elapsed time at this layer.

---

## 5. Staleness Detection

### 5.1 Service Layer — No Staleness Detection

The `MarketContextService` itself performs **zero staleness checks**. It does not:
- Compare `observation_date` to current date
- Compare `fetched_at` to current time
- Enforce maximum age thresholds
- Reject or flag stale values

Staleness detection is fully delegated to downstream consumers.

### 5.2 MI Runner — `_build_freshness_section()`

**File**: `app/workflows/market_intelligence_runner.py` (lines 846–908)

```python
def _build_freshness_section(
    market_snapshot: dict[str, Any],
    policy: FreshnessPolicy,
) -> dict[str, Any]:
    metrics = market_snapshot.get("metrics", {})
    per_source: dict[str, Any] = {}
    tier_rank = {"fresh": 0, "warning": 1, "stale": 2, "unknown": 3}
    worst_tier = "fresh"
    now = datetime.now(timezone.utc)

    for key, metric in metrics.items():
        if metric is None:
            per_source[key] = {"tier": "unknown", "age_seconds": None, "last_update": None}
            continue

        fetched_at = metric.get("fetched_at") if isinstance(metric, dict) else None
        if fetched_at:
            try:
                fetched = datetime.fromisoformat(fetched_at)
                if fetched.tzinfo is None:
                    fetched = fetched.replace(tzinfo=timezone.utc)
                age_seconds = (now - fetched).total_seconds()
                if age_seconds < policy.warn_after_seconds:
                    tier = "fresh"
                elif age_seconds < policy.degrade_after_seconds:
                    tier = "warning"
                else:
                    tier = "stale"
            except (ValueError, TypeError):
                age_seconds = None
                tier = "unknown"
        else:
            age_seconds = None
            tier = "unknown"

        per_source[key] = {"tier": tier, "age_seconds": ..., "last_update": fetched_at}
        if tier_rank.get(tier, 0) > tier_rank.get(worst_tier, 0):
            worst_tier = tier

    return {"overall": worst_tier, "per_source": per_source}
```

**Thresholds** (from `FreshnessPolicy`, `app/workflows/architecture.py` lines 202–210):

```python
@dataclass(frozen=True)
class FreshnessPolicy:
    warn_after_seconds: int = 600       # 10 minutes
    degrade_after_seconds: int = 1800   # 30 minutes
    allow_stale: bool = True            # if False, fail fast
```

| Tier | Age threshold | Consequence |
|------|---------------|-------------|
| `fresh` | < 600s (10 min) | No action |
| `warning` | 600–1800s (10–30 min) | Logged, publication may be DEGRADED |
| `stale` | > 1800s (30 min) | Publication status becomes DEGRADED |

> **FLAG [CRITICAL]**: This staleness check uses `fetched_at`, which is set at envelope construction time — always approximately "now" during a fresh MI run. Since the MI runner calls `get_market_context()` which rebuilds envelopes (resetting `fetched_at`), **these freshness tiers will almost always show "fresh"** even if the underlying FRED data is hours or days old from the 300s cache layer. The check is structurally ineffective.

### 5.3 Cross-Asset Macro — Copper-Only Staleness Check

**File**: `app/services/cross_asset_macro_data_provider.py` (lines 223–241)

```python
def _days_stale(obs_date_str: str | None) -> int | None:
    if not obs_date_str:
        return None
    try:
        obs = datetime.strptime(obs_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - obs).days
    except (ValueError, TypeError):
        return None

copper_days_stale = _days_stale(copper_date)
if copper_days_stale is not None and copper_days_stale > 5:
    logger.info(
        "event=cross_asset_copper_stale days_stale=%d observation_date=%s",
        copper_days_stale, copper_date,
    )
```

This is the **only per-metric staleness check using `observation_date`** in the entire data provider layer. It:
- Uses the correct field (`observation_date` from FRED)
- Only applies to copper (monthly FRED series)
- Only **logs** — does not reject, reduce confidence, or flag the metric as degraded
- Has a 5-day threshold, which is reasonable for a monthly series but inconsistent with no similar check for other metrics

> **FLAG [HIGH]**: Only copper has an `observation_date`-based staleness check out of 9+ FRED series. No staleness detection exists for yields (DGS10, DGS2, DGS30), fed funds rate (DFF), oil (DCOILWTICO), USD index (DTWEXBGS), or credit spreads (BAMLC0A0CM, BAMLH0A0HYM2). These could be days stale without detection.

---

## 6. Degradation Cascading

### 6.1 How Data Providers Handle Missing Values

When a metric from `get_market_context()` has `value: None`:

| Data Provider | Handling | Code |
|---------------|----------|------|
| Cross-Asset Macro | `_extract_value()` returns `None` → passed as `None` in pillar dicts | Lines 48–54 |
| Flows Positioning | `_extract_value()` returns `None` → all 12 proxy formulas produce `None` (VIX input) | Lines 26–29 |
| Liquidity Conditions | `_safe_extract()` returns `None` → pillar dict values = `None` | Lines 95–111 |
| Volatility Options | `vix_val` checked → exits early if None → degraded metric_availability | Lines 349–353 |
| News Sentiment | `setattr(ctx, attr, metric["value"])` — only if value is not None | Lines 301–325 |

**Pattern**: `None` values propagate through to engine input dicts. Engines are expected to handle `None` internally (usually via conditional scoring that skips missing inputs).

### 6.2 Envelope Stripping at Data Provider Layer

The critical data integrity question: **Do engines know their inputs are stale or proxy?**

All data providers call `_extract_value(envelope)` which **strips the freshness metadata** and returns only the bare numeric value:

```python
def _extract_value(metric: dict[str, Any] | float | int | None) -> float | None:
    if metric is None:
        return None
    if isinstance(metric, (int, float)):
        return float(metric)
    if isinstance(metric, dict):
        return metric.get("value")
    return None
```

**What's preserved vs stripped**:

| Data Element | Preserved? | Where? |
|-------------|------------|--------|
| Numeric value | **Yes** | Passed to engine pillar dicts |
| `source` | **Partially** | Saved in `source_meta` dict (not passed to engine) |
| `freshness` | **Partially** | Saved in `source_meta` dict (not passed to engine) |
| `observation_date` | **Partially** | Saved in `source_meta` for some providers |
| `fetched_at` | **Partially** | Saved in `source_meta` for some providers |
| `is_intraday` | **No** | Stripped |
| `source_timestamp` | **No** | Stripped |
| `previous_close` | **No** | Stripped (except VIX in vol data provider) |

> **FLAG [CRITICAL]**: Engines receive bare numeric values with no freshness, source, or staleness metadata. A 10-day-old copper price and a live VIX quote arrive at the engine in the same dict shape. The engine has no way to know which inputs are current and which are stale.

### 6.3 Source Metadata — Parallel Channel

Several data providers preserve freshness in a `source_meta` dict that accompanies engine inputs:

**Cross-Asset Macro** (line 246–263):
```python
source_meta = {
    "market_context_generated_at": market_ctx.get("context_generated_at"),
    "vix_source": market_ctx.get("vix", {}).get("source"),
    "vix_freshness": market_ctx.get("vix", {}).get("freshness"),
    "fred_copper_days_stale": copper_days_stale,
    ...
}
```

**Liquidity Conditions** (lines 210–250):
```python
source_detail[name] = {
    "value": _extract_value(metric),
    "source": _extract_source(metric),
    "freshness": _extract_freshness(metric),
}
```

**Status**: `source_meta` is stored in engine output artifacts but is **not used to reduce pillar scores or engine confidence**. It's diagnostic information, not an active degradation signal.

### 6.4 Degradation Signal Path Summary

```
MarketContextService (value + full envelope)
      │
      ▼
Data Provider (_extract_value() strips envelope → bare float)
      │
      ├──→ Engine input dict (bare values only, no freshness)
      │         │
      │         ▼
      │    Engine (scores from values, unaware of staleness)
      │
      └──→ source_meta (freshness preserved, but unused for scoring)
                │
                ▼
           Output artifact (diagnostic only, not fed back into scores)
```

The **only feedback loop** where freshness affects scoring happens in the MI Runner's Stage 2 (`_build_freshness_section`), which uses the flawed `fetched_at`-based check (see §5.2).

### 6.5 Publication Status Degradation

**File**: `app/workflows/market_intelligence_runner.py` (lines 623–650)

```python
def _determine_publication_status(engine_health, source_health, model_interp):
    succeeded = engine_health.get("engines_succeeded", 0)
    degraded = engine_health.get("engines_degraded", 0)
    failed = engine_health.get("engines_failed", 0)
    total = engine_health.get("engines_total", 6)

    if succeeded == 0 and degraded == 0:
        return "FAILED"
    if (succeeded + degraded) < (total // 2):
        return "INCOMPLETE"
    if degraded > 0 or failed > 0:
        return "DEGRADED"
    if src_failed > 0 or src_degraded > 0:
        return "DEGRADED"
    return "VALID"
```

This tracks engine/source-level success/failure but **not data freshness**. An engine that successfully scored using 5-day-old data produces a "succeeded" status.

---

## 7. Market Hours Awareness

### 7.1 Implementation

**File**: `app/trading/risk.py` (lines 16–22)

```python
def _is_market_open(now: datetime | None = None) -> bool:
    ts = now or datetime.now(timezone.utc)
    if ts.weekday() >= 5:
        return False
    # Simple UTC window for US regular session: 14:30-21:00 UTC
    mins = ts.hour * 60 + ts.minute
    return 14 * 60 + 30 <= mins <= 21 * 60
```

### 7.2 Limitations

| Feature | Status |
|---------|--------|
| Regular session (9:30–16:00 ET) | **Approximate** — hardcoded UTC window, no DST |
| Pre-market (4:00–9:30 ET) | **Not recognized** |
| After-hours (16:00–20:00 ET) | **Not recognized** |
| Weekends | **Detected** (weekday >= 5) |
| US holidays | **Not detected** — treats holidays as regular trading days |
| DST transitions | **Not handled** — UTC window shifts by 1 hour during DST |

### 7.3 Where Used

`_is_market_open()` is only used in the **risk evaluation** module (`evaluate_preview_risk()` at line 65):

```python
if not _is_market_open():
    warnings.append("Market appears closed; fills may be delayed or less reliable")
```

### 7.4 Market Context Service — No Hours Awareness

The `MarketContextService` has **no market hours logic**. It:
- Fetches and caches data on every call regardless of time
- Does not adjust TTL based on market hours (30s TTL applies 24/7)
- Does not distinguish "data is stale because market is closed" from "data is stale because something is wrong"
- Does not downgrade `freshness` from "intraday" to a different label during off-hours

> **FLAG [HIGH]**: During weekends and holidays, the 30-second market context cache aggressively re-fetches data that cannot have changed. Tradier VIX "intraday" quotes on Saturday are Friday's close — correctly sourced but misleadingly labeled "intraday" with `freshness: "intraday"`. No system component distinguishes this from truly live data.

### 7.5 MI Runner Scheduling — No Hours Gate

**File**: `app/services/data_population_service.py` (lines 118–131)

```python
async def _run_loop(self) -> None:
    await self._run_once()  # First run on startup
    while not self._stopped:
        await asyncio.sleep(INTERVAL_SECONDS)  # 300 seconds (5 min)
        if not self._stopped:
            await self._run_once()
```

The MI runner loops every 5 minutes **continuously**, including weekends and holidays. There is no gate to skip runs when:
- Markets are closed
- Data cannot meaningfully change (FRED publishes EOD+1 on business days)
- API rate limits should be conserved

---

## 8. Concurrent Access

### 8.1 Shared Singleton

All consumers (6 data providers, MI runner, REST API) share the **same `MarketContextService` instance**. Concurrent access patterns:

| Caller | Trigger | Frequency | Calls |
|--------|---------|-----------|-------|
| MI Runner (scheduled) | `DataPopulationService._run_loop()` | Every 5 minutes | `get_market_context()` via Stage 1 |
| MI Runner (manual) | `DataPopulationService.trigger()` | On-demand | Same path |
| Cross-Asset Data Provider | MI engine dispatch | During MI run | `get_market_context()` |
| Flows Data Provider | MI engine dispatch | During MI run | `get_market_context()` |
| Liquidity Data Provider | MI engine dispatch | During MI run | `get_market_context()` |
| Vol Data Provider | MI engine dispatch | During MI run | `get_market_context()` |
| News Sentiment Service | MI engine dispatch | During MI run | `get_market_context()` |
| REST API `/api/stock/macro` | HTTP request | On-demand | `get_flat_macro()` → `get_market_context()` |

### 8.2 Cache-Level Concurrency Protection

`TTLCache` uses `asyncio.Lock()` to serialize `get()` and `set()` operations. This prevents data corruption but introduces serialization at the cache level.

### 8.3 Race Condition: `get_or_set()` Thundering Herd

```python
async def get_or_set(self, key, ttl_seconds, loader):
    cached = await self.get(key)     # Takes lock, releases
    if cached is not None:
        return cached
    # GAP: Between get() returning None and set() storing the value,
    # multiple coroutines can enter loader() concurrently
    loaded = await loader()           # Runs without lock
    await self.set(key, loaded, ttl_seconds)
    return loaded
```

During a single MI run, up to 6 data providers may call `get_market_context()` near-simultaneously. If the 30-second cache has expired:
1. First provider calls `get_or_set()` → cache miss → enters `_build()` (fetches Tradier/Finnhub/FRED)
2. Second provider calls `get_or_set()` → also cache miss → also enters `_build()`
3. Both complete, second overwrites first in cache

**Mitigation**: In practice, the MI runner calls `get_market_context()` in Stage 1 (`_stage_collect_inputs`) **before** engine dispatch. The cached result is available for the subsequent data provider calls. But REST API requests arriving during the cache gap window could trigger duplicate fetches.

### 8.4 DataPopulationService Locking

**File**: `app/services/data_population_service.py` (lines 134–135)

```python
async def _run_once(self) -> None:
    async with self._lock:
        ...
```

The `_run_once()` method holds an `asyncio.Lock` for its entire duration, preventing concurrent MI runs. The `trigger()` method also checks status before launching:

```python
async def trigger(self) -> PopulationStatus:
    if self._status.phase in ("market_data", "model_analysis"):
        logger.info("event=data_population_trigger_skipped reason=already_running")
        return self._status
```

This is effective: no two MI runs execute concurrently. However, REST API calls to `get_flat_macro()` are **not gated** by this lock and can run concurrently with MI runs.

---

## 9. Data Provider Envelope Extraction Patterns

### 9.1 Extraction Function Variants

Four different `_extract_value()` implementations exist across data providers:

| Provider | File | Accepts `int/float`? | Signature |
|----------|------|---------------------|-----------|
| Cross-Asset Macro | `cross_asset_macro_data_provider.py:48` | **Yes** | `dict \| float \| int \| None → float \| None` |
| Flows Positioning | `flows_positioning_data_provider.py:26` | **No** | `dict \| None → float \| None` |
| Liquidity Conditions | `liquidity_conditions_data_provider.py:28` | **Yes** | `dict \| float \| int \| None → float \| None` |
| Volatility Options | (inline) | **N/A** | Direct `vix_metric.get("value")` |

> **FLAG [LOW]**: Four independent implementations of `_extract_value()` with slightly different signatures. Functional equivalence, but violates DRY and risks divergence.

### 9.2 Freshness Preservation by Provider

| Provider | Preserves Source? | Preserves Freshness? | Preserves observation_date? | Staleness Check? |
|----------|-------------------|---------------------|----------------------------|-----------------|
| Cross-Asset Macro | ✅ in source_meta | ✅ in source_meta | ✅ copper/gold dates | ✅ Copper only (5-day log) |
| Flows Positioning | ✅ in source_meta | ✅ in source_meta | ❌ | ❌ |
| Liquidity Conditions | ✅ in source_detail | ✅ in source_detail | ❌ | ✅ Counts stale sources |
| Volatility Options | ✅ vix_source | ❌ | ❌ | ❌ |
| News Sentiment | ✅ in _freshness map | ✅ in _freshness map | ✅ in _freshness map | ❌ |
| Breadth | ❌ (no market context) | ❌ | ❌ | ❌ (`stale_data_flag: False` always) |

---

## 10. Confidence Framework Integration

### 10.1 Framework Design

**File**: `app/services/confidence_framework.py`

The framework defines penalty tables for freshness:

```python
FRESHNESS_PENALTIES: dict[str, float] = {
    "live":       0.00,
    "recent":     0.00,
    "stale":      0.10,   # 10% confidence penalty
    "very_stale": 0.25,   # 25% confidence penalty
    "unknown":    0.05,
}
```

And provides `impact_from_freshness()` which creates a structured impact dict:

```python
def impact_from_freshness(freshness_status: str, *, source: str = "") -> dict | None:
    status = str(freshness_status).lower().strip() if freshness_status else "unknown"
    penalty = FRESHNESS_PENALTIES.get(status, FRESHNESS_PENALTIES.get("unknown", 0.05))
    if penalty <= 0.0:
        return None
    return make_impact("freshness", penalty, f"freshness: {status}", source=source)
```

### 10.2 Gap: Framework Exists But Underutilized

The confidence framework defines the **right abstractions** (freshness penalties, impact records, assessment builder) but:

1. **Input disconnect**: The framework expects `freshness_status` values like `"stale"` or `"very_stale"`, but the metric envelope produces `"intraday"`, `"eod"`, `"delayed"` — different vocabulary
2. **The MI runner's freshness tiers** (`fresh`, `warning`, `stale`) are a third vocabulary that doesn't map 1:1 to the framework's penalty table
3. **No automatic invocation**: Data providers don't call `build_confidence_assessment()` with freshness data. It must be called explicitly by the MI runner or composite builder.

> **FLAG [MEDIUM]**: Three separate freshness vocabularies exist:
> - Metric envelope: `"intraday"` / `"eod"` / `"delayed"` (source-type based)
> - MI runner tiers: `"fresh"` / `"warning"` / `"stale"` (`fetched_at` age based)
> - Confidence framework: `"live"` / `"recent"` / `"stale"` / `"very_stale"` (penalty-mapped)
> 
> No mapping layer translates between them automatically.

---

## 11. Summary of Findings

### Critical Findings

| # | Finding | Location | Impact |
|---|---------|----------|--------|
| C1 | **`fetched_at` misrepresents data age** — set at envelope construction, not API call time. Makes all freshness checks based on it structurally ineffective. | `market_context_service.py:77` + `market_intelligence_runner.py:871` | MI runner freshness tiers always show "fresh" for a fresh run, even if underlying data is hours/days old from FRED cache |
| C2 | **Engines receive bare values with no freshness metadata** — envelope stripped at data provider layer via `_extract_value()` | All 5 data providers | An engine scoring with 10-day-old copper price has no way to know or adjust |
| C3 | **No staleness enforcement at service layer** — MarketContextService returns whatever it has, never rejects or flags stale data | `market_context_service.py` entire file | Stale data can flow through the entire pipeline without any component raising an alarm |

### High Findings

| # | Finding | Location | Impact |
|---|---------|----------|--------|
| H1 | **Stale data doesn't trigger source fallback** — only errors/missing trigger VIX waterfall; stale Tradier quote accepted without age check | `market_context_service.py:202–208` | Friday's close served as "intraday" on Monday morning if Tradier returns it |
| H2 | **Only copper has observation_date staleness check** — 8+ other FRED series have no age detection | `cross_asset_macro_data_provider.py:223–241` | Yields, oil, USD index could be days stale without detection |
| H3 | **Market hours not wired into data pipeline** — `_is_market_open()` exists only in risk module; MI runner and MarketContextService have no hours awareness | `risk.py:16–22` vs `data_population_service.py:118–131` | Aggressive re-fetch during weekends/holidays; "intraday" label applied when market is closed |
| H4 | **Freshness label reflects source type, not actual age** — `"intraday"` from Friday served as `"intraday"` on Monday | `market_context_service.py:64–68` | Consumers who trust `freshness == "intraday"` to mean "current" are misled |

### Medium Findings

| # | Finding | Location | Impact |
|---|---------|----------|--------|
| M1 | **No "serve stale" fallback** — when upstream fails, `None` is cached for full TTL; previous valid value is discarded | `cache.py` + `market_context_service.py:174–189` | 5-minute FRED outage = 5 minutes of `None` for all FRED metrics |
| M2 | **Three incompatible freshness vocabularies** — metric envelope, MI runner tiers, and confidence framework use different labels with no mapping | Multiple files | Freshness penalties from confidence framework may never be correctly applied |
| M3 | **`get_or_set()` thundering herd** — concurrent callers can bypass cache and duplicate API calls when cache expires | `cache.py:43–53` | Minor — mitigated by MI run serialization; mainly affects REST API concurrency |
| M4 | **No DST handling in market hours** — UTC window shifts by 1 hour seasonally | `risk.py:16–22` | Risk warning off by 1 hour during DST transition periods |
| M5 | **No US holiday calendar** — treats Christmas, July 4th, etc. as regular trading days | `risk.py:16–22` | Unnecessary re-fetches; possible "market is open" when it's not |

### Low Findings

| # | Finding | Location | Impact |
|---|---------|----------|--------|
| L1 | **Four duplicate `_extract_value()` implementations** across data providers | 4 files | DRY violation, minor divergence risk |
| L2 | **`source_timestamp` only captured for Finnhub VIX** — Tradier quotes don't include exchange timestamp | `market_context_service.py:137` | Minor — Tradier doesn't provide this field |
| L3 | **CPI staleness not enforced** — monthly series could be 30+ days old with 13 observations | `market_context_service.py:271–304` | CPI changes slowly; impact limited |

### Data Flow Diagram

```
┌────────────────────────────────────────────────────────────────┐
│ EXTERNAL APIs                                                  │
│  Tradier (VIX quote)  │  Finnhub (VIX quote)  │  FRED (series)│
└──────┬────────────────┴──────────┬─────────────┴──────┬────────┘
       │                          │                     │
       ▼                          ▼                     ▼
┌──────────────────────────────────────────────────────────────┐
│ Client Layer (each has own TTLCache key)                      │
│  TradierClient (10s)  │  FinnhubClient  │  FredClient (300s) │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────┐
│ MarketContextService                                         │
│  VIX: Tradier → Finnhub → FRED waterfall                    │
│  All others: FRED only (parallel asyncio.gather)             │
│  Wraps in metric envelope: {value, source, freshness,        │
│    observation_date, fetched_at, source_timestamp}           │
│  Cached as "market_context:latest" (30s TTL)                 │
│                                                              │
│  ⚠ No staleness check                                       │
│  ⚠ fetched_at = envelope build time, not API call time       │
│  ⚠ freshness = source type label, not age indicator          │
└──────────────────────────┬───────────────────────────────────┘
                           │
          ┌────────────────┼────────────────────┐
          │                │                    │
          ▼                ▼                    ▼
┌─────────────────┐ ┌─────────────┐   ┌──────────────────┐
│ Data Providers  │ │ MI Runner   │   │ REST API         │
│ (5 of 6)        │ │ Stage 1     │   │ /api/stock/macro │
│                 │ │             │   │                  │
│ _extract_value()│ │ Stores full │   │ get_flat_macro() │
│ STRIPS envelope │ │ envelopes   │   │ Returns with     │
│ → bare float    │ │ Stage 2:    │   │ _freshness block │
│                 │ │ freshness   │   │                  │
│ source_meta     │ │ check uses  │   └──────────────────┘
│ (diagnostic)    │ │ fetched_at  │
│                 │ │ (⚠ flawed)  │
└────────┬────────┘ └──────┬──────┘
         │                 │
         ▼                 ▼
┌─────────────────┐ ┌──────────────┐
│ Engine          │ │ Publication  │
│ Receives bare   │ │ Status       │
│ values only     │ │ VALID /      │
│ ⚠ No freshness │ │ DEGRADED /   │
│   awareness     │ │ FAILED       │
└─────────────────┘ └──────────────┘
```

---

## Cross-References

| Related Audit | Connection |
|--------------|------------|
| **1A (Tradier Ingestion)** | Tradier quote caching (10s TTL) feeds into VIX waterfall step 1. No rate-limit retry found in 1A affects VIX availability. |
| **1B (FRED Ingestion)** | FRED 300s cache layer creates data-age opacity. Copper staleness issue flagged in 1B aligns with §5.3 finding that only copper has a staleness check. |
| **1C (Proxy Inventory)** | Proxy laundering (engines don't know inputs are proxy) is compounded by freshness laundering (engines don't know inputs are stale). Both metadata types are stripped at the same extraction point. |

---

*End of Audit 1D*
