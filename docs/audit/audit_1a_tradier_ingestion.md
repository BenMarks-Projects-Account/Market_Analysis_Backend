# Audit 1A: Tradier Data Ingestion Map

> **Pass 1 — Data Integrity Audit**
> Generated: 2026-03-20 | Auditor: Copilot (code-traced)

---

## Executive Summary

Tradier is BenTrade's **source of truth** for live market data and order execution. All option chains, equity quotes, OHLCV history, and broker operations flow through a single `TradierClient` class. The implementation is generally solid — sanitization of bid/ask, retry on transient HTTP errors, TTL caching — but has notable gaps in **rate limiting**, **staleness detection**, and **extreme-value validation**.

**Critical findings:**
- **No semaphore/throttle** on Tradier API calls (breadth engine fires ~150 bar requests per cycle)
- **No market-hours awareness** — cache TTLs are fixed regardless of whether markets are open or closed
- **No maximum spread tolerance** — bid=0 / ask=50 passes validation
- **Live order freshness gate exists** but only for live execution; paper/scanner paths have none

---

## 1. Quotes — `/v1/markets/quotes`

### 1.1 Endpoint / Method

| Item | Value |
|------|-------|
| Tradier endpoint | `GET /v1/markets/quotes` |
| Client methods | `get_quote(symbol)`, `get_quotes(symbols)`, `get_option_quotes(option_symbols)` |
| File | `app/clients/tradier_client.py` |
| Auth | Bearer token via `_headers` property |

### 1.2 Call Chains (Where Called From)

**Single-symbol `get_quote()`:**
| Entry Point | Intermediate | Terminal Call |
|-------------|-------------|---------------|
| `GET /api/health/status` | `tradier_client.health()` | `get_quote("SPY")` |
| `POST /api/trading/preview` | `trading_service.preview()` → `base_data_service` | `get_quote(symbol)` |
| MI Runner (scheduled ~5min) | `market_context_service._vix_from_tradier()` | `get_quote("VIX")` |

**Multi-symbol `get_quotes()`:**
| Entry Point | Intermediate | Terminal Call |
|-------------|-------------|---------------|
| `GET /api/trading/active` | `routes_active_trades._build_active_payload()` | `get_quotes(position_symbols)` |
| MI Runner → breadth engine | `breadth_data_provider.fetch_breadth_data()` | `get_quotes(batch)` × ~7-8 batches of 50 |
| MI Runner → breadth engine | `breadth_data_provider.fetch_breadth_data()` | `get_quotes(["SPY", "RSP"])` |

**OCC-symbol `get_option_quotes()`:**
| Entry Point | Intermediate | Terminal Call |
|-------------|-------------|---------------|
| Future: active trade enrichment | `routes_active_trades` | `get_option_quotes(occ_symbols)` |

### 1.3 Fields Extracted

```python
# From tradier_client.py — _sanitize_quote():
# Tradier quote response → cleaned dict
quote_obj = (payload.get("quotes") or {}).get("quote")

# Fields passed through (all preserved from raw response):
cleaned = dict(quote_obj)           # ALL Tradier fields kept
cleaned["bid"] = sanitized_bid      # float | None (negative → None)
cleaned["ask"] = sanitized_ask      # float | None (negative → None, ask<bid → both None)

# Downstream consumers extract:
# - quote.get("last")              → VIX current value (market_context_service)
# - quote.get("prevclose")         → VIX previous close
# - quote.get("bid"), quote.get("ask") → trading preview
# - quote.get("close")             → active trade current price
```

**Tradier → BenTrade field mapping:**

| Tradier Field | BenTrade Usage | Validated? |
|---------------|----------------|-----------|
| `bid` | Order preview, active trade enrichment | ✅ negative → None, ask < bid → both None |
| `ask` | Order preview, active trade enrichment | ✅ negative → None, ask < bid → both None |
| `last` | VIX intraday value, general display | ❌ No validation (checked > 0 only for VIX) |
| `prevclose` / `previous_close` | VIX previous close | ❌ No validation |
| `close` | Active trade current price | ❌ No validation |
| `volume` | Not directly extracted from quotes | N/A |
| `mark` | Not extracted | N/A |

### 1.4 Freshness Handling

- **Cache TTL**: 10 seconds (`QUOTE_CACHE_TTL_SECONDS`)
- **Market-hours awareness**: ❌ NONE — same 10s TTL during market hours, after-hours, weekends
- **Stale detection**: ❌ No timestamp comparison — if the quote is in cache, it's "fresh"
- **Impact**: At 4:01 PM ET a cached quote from 3:59 PM looks the same as a real-time quote from 4:00 PM. During weekends, the 10s cache means Tradier gets hit every 10 seconds for data that hasn't changed since Friday

### 1.5 Error / Missing Data Handling

```python
# tradier_client.py — get_quote():
quote_obj = (payload.get("quotes") or {}).get("quote")
if isinstance(quote_obj, list):
    quote_obj = quote_obj[0] if quote_obj else {}
return self._sanitize_quote(quote_obj or {}, symbol=normalized_symbol)
# If Tradier returns no quote → empty dict {}
# If bid/ask null → None (not 0)
# If network error → UpstreamError raised → caller must handle
```

**Assessment**: Missing quotes return `{}` — callers must check for empty dict. Null fields become `None` (good, not fabricated). Network errors propagate as exceptions.

### 1.6 Quote Validation

**Present:**
```python
# _sanitize_quote() in tradier_client.py:
if bid is not None and bid < 0:           # ✅ Negative bid rejected
    bid = None
if ask is not None and ask < 0:           # ✅ Negative ask rejected
    ask = None
if bid is not None and ask is not None and ask < bid:  # ✅ Inverted spread rejected
    bid = None
    ask = None
```

**ABSENT (data integrity gaps):**
- ❌ No maximum spread width check (bid=0.01, ask=50.00 passes)
- ❌ No check for `last` being a reasonable price (could be 0 or negative)
- ❌ No check that `last` is between `bid` and `ask`
- ❌ No volume validation on quote level
- ❌ No detection of "stale quote" patterns (volume=0, unchanged bid/ask for extended period)

---

## 2. Option Chains — `/v1/markets/options/chains`

### 2.1 Endpoint / Method

| Item | Value |
|------|-------|
| Tradier endpoint | `GET /v1/markets/options/chains` |
| Client methods | `get_chain(symbol, expiration, greeks=True)`, `fetch_chain_raw_payload(...)` |
| File | `app/clients/tradier_client.py` (raw fetch), `app/services/base_data_service.py` (normalization) |

### 2.2 Call Chains

| Entry Point | Intermediate | Terminal Call |
|-------------|-------------|---------------|
| Options Scanner (scheduled) | `options_scanner_service.scan()` → `base_data_service.get_analysis_inputs()` → `_get_chain_with_health()` → `TradierChainSource` | `get_chain(symbol, exp, greeks=True)` |
| `POST /api/trading/preview` | `trading_service.preview()` → `base_data_service` | `get_chain(symbol, exp, greeks=True)` |
| `GET /api/options/{symbol}/chain` | `routes_options.get_chain()` | `get_chain(symbol, exp)` |
| `POST /api/dev/snapshots/capture` | `routes_dev` | `fetch_chain_raw_payload(symbol, exp)` |

### 2.3 Fields Extracted — Raw to OptionContract

The chain flows through two layers: raw client fetch → `base_data_service.normalize_chain()`:

```python
# base_data_service.py — normalize_chain() field mapping:
```

| Tradier Field | Extraction Logic | OptionContract Field | Validation |
|---|---|---|---|
| `option_type` / `type` | Lowercase; infer from OCC symbol if missing | `option_type` | Must be "put" or "call"; skip row if invalid |
| `strike` | `_to_float()` | `strike` | Must be finite number; skip row + warning if invalid |
| `expiration_date` / `expiration` | Parse YYYY-MM-DD; reject if past | `expiration` | Parsed; past dates rejected; skip row |
| `bid` | `validate_bid_ask()` from `validation.py` | `bid` | Negative → None; non-finite → None |
| `ask` | `validate_bid_ask()` from `validation.py` | `ask` | Negative → None; non-finite → None; ask < bid → both None, skip row |
| `open_interest` | `_to_int()` then `clamp(min=0)` | `open_interest` | Negative clamped to 0; warning logged |
| `volume` | `_to_int()` then `clamp(min=0)` | `volume` | Negative clamped to 0; warning logged |
| `greeks.delta` | `_to_float()` then `clamp(-1.0, 1.0)` | `delta` | Out of [-1, 1] clamped; warning logged |
| `greeks.smv_vol` / `iv` / `implied_vol` / `greeks.mid_iv` | `_to_float()` then `_normalize_iv()` | `iv` | If > 1.0 divide by 100; non-finite → None |
| `symbol` | Direct passthrough | `symbol` | OCC format validated elsewhere for order paths |

### 2.4 Freshness Handling

- **Cache TTL**: 60 seconds (`CHAIN_CACHE_TTL_SECONDS`)
- **`fetch_chain_raw_payload()`**: Bypasses cache entirely (administrative use)
- **Market-hours awareness**: ❌ NONE — same 60s TTL whether market is open or closed
- **Impact**: During pre-market (4-9:30 ET), an options scanner run uses chains that may reflect Friday's close or overnight movement — no way to distinguish

### 2.5 Error / Missing Data Handling

```python
# tradier_client.py — get_chain():
options = ((payload.get("options") or {}).get("option")) or []
if isinstance(options, dict):
    return [options]  # Tradier returns dict instead of list for single option
return options
# Empty chain → returns []

# base_data_service.py — normalize_chain():
# Each row validated independently — bad rows skipped, good rows included
# If ASK_LT_BID → row skipped entirely (not just bid/ask nulled)
# Missing strike or expiration → row skipped with warning
```

**Assessment**: Graceful degradation — individual bad contracts are skipped, not the entire chain. But there's no minimum chain completeness check (e.g., "warn if < 10 contracts returned for an expected chain").

### 2.6 Chain Validation

**Present (comprehensive):**
```python
# base_data_service.py — normalize_chain():
# validate_bid_ask() from app/utils/validation.py:
def validate_bid_ask(bid, ask) -> tuple[float|None, float|None, list[str]]:
    # BID_NOT_FINITE, BID_NEGATIVE, ASK_NOT_FINITE, ASK_NEGATIVE, ASK_LT_BID
    # Returns cleaned values + warning codes
```

| Validation | Present? | Action on Failure |
|-----------|----------|-------------------|
| Option type valid | ✅ | Skip row |
| Strike finite number | ✅ | Skip row + warning |
| Expiration parseable | ✅ | Skip row + warning |
| Expiration not past | ✅ | Skip row + warning |
| Bid finite | ✅ | Bid → None |
| Bid non-negative | ✅ | Bid → None |
| Ask finite | ✅ | Ask → None |
| Ask non-negative | ✅ | Ask → None |
| Ask ≥ Bid | ✅ | Both → None, **skip row** |
| Delta in [-1, 1] | ✅ | Clamped + warning |
| OI non-negative | ✅ | Clamped to 0 + warning |
| Volume non-negative | ✅ | Clamped to 0 + warning |
| IV finite | ✅ | IV → None |
| IV > 1.0 (percentage fix) | ✅ | Divided by 100 |

**ABSENT (data integrity gaps):**
- ❌ No bid=0 rejection (a contract with bid=0 and ask=0.05 passes — it's technically valid but illiquid)
- ❌ No maximum spread percentage check (e.g., ask = 10× bid)
- ❌ No IV extreme rejection (IV > 500% passes through)
- ❌ No strike sanity check vs underlying price
- ❌ No chain completeness check (chain with 2 contracts looks same as chain with 200)
- ❌ No greeks completeness check (delta=None contracts pass through without counts being tracked at this layer)

---

## 3. Option Expirations — `/v1/markets/options/expirations`

### 3.1 Endpoint / Method

| Item | Value |
|------|-------|
| Tradier endpoint | `GET /v1/markets/options/expirations` |
| Client method | `get_expirations(symbol)` |
| Params | `symbol`, `includeAllRoots=true` |

### 3.2 Call Chains

| Entry Point | Intermediate | Terminal Call |
|-------------|-------------|---------------|
| Options Scanner | `options_scanner_service._run_one()` | `get_expirations(symbol)` |
| `GET /api/options/{symbol}/expirations` | `routes_options.get_expirations()` | `get_expirations(symbol)` |

### 3.3 Fields Extracted

```python
# tradier_client.py — get_expirations():
dates = ((payload.get("expirations") or {}).get("date")) or []
if isinstance(dates, str):
    dates = [dates]  # Tradier returns string for single expiration
```

| Tradier Field | BenTrade Field | Validation |
|---|---|---|
| `expirations.date[]` | List of `"YYYY-MM-DD"` strings | Parsed, past dates rejected |

### 3.4 Freshness & Validation

- **Cache TTL**: 300 seconds / 5 min (`EXPIRATIONS_CACHE_TTL_SECONDS`)
- **Validation**: Past expirations filtered out; malformed dates skipped with warning
- **Missing validation**: No check for "expected number of expirations" — if Tradier returns only 1 expiration for SPY, no alarm fires

---

## 4. Historical Bars — `/v1/markets/history`

### 4.1 Endpoint / Methods

| Item | Value |
|------|-------|
| Tradier endpoint | `GET /v1/markets/history` |
| Client methods | `get_daily_closes(symbol, start, end)`, `get_daily_closes_dated(...)`, `get_daily_bars(...)`, `get_intraday_bars(...)` |
| Params | `symbol`, `interval` (daily/15min), `start`, `end` |

### 4.2 Call Chains

| Entry Point | Intermediate | Terminal Call |
|-------------|-------------|---------------|
| MI Runner → breadth engine | `breadth_data_provider.fetch_breadth_data()` (Semaphore(10)) | `get_daily_bars(ticker, start, end)` × ~150 tickers |
| Mean reversion analysis | `mean_reversion_service.calculate_levels_for_symbol()` | `get_daily_bars(symbol, start, end)` |
| `base_data_service.get_prices_history()` | Polygon first → Tradier fallback | `get_daily_closes(symbol, start, end)` |
| `base_data_service.get_intraday_bars()` | Polygon first → Tradier fallback | `get_intraday_bars(symbol, start, end, "15min")` |

### 4.3 Fields Extracted

**Daily bars (`get_daily_bars`):**

| Tradier Field | Output Field | Extraction |
|---|---|---|
| `history.day[].date` | `"date"` (str) | Direct string passthrough |
| `history.day[].open` | `"open"` (float\|None) | `_to_float()` |
| `history.day[].high` | `"high"` (float\|None) | `_to_float()` |
| `history.day[].low` | `"low"` (float\|None) | `_to_float()` |
| `history.day[].close` | `"close"` (float) | `_to_float()`, skipped if None |
| `history.day[].volume` | `"volume"` (int\|None) | `_to_int()` |

**Daily closes (`get_daily_closes`):**
- Extracts only `close` as a flat `list[float]` — date information discarded.
- **⚠️ FLAG**: Consumers assume the list is in chronological order but don't verify dates.

### 4.4 Freshness Handling

- **Cache TTL**: 1800 seconds / 30 min (`CANDLES_CACHE_TTL_SECONDS`)
- **Market-hours awareness**: ❌ NONE
- **Impact**: During market hours, a 30-minute cache on historical bars means the "today" bar could be 30 minutes stale for intraday calculations

### 4.5 Error Handling

```python
# Days with no close are silently skipped in get_daily_closes:
close = day.get("close")
if close is None:
    continue
# If Tradier returns fewer days than expected, the list is just shorter
# No validation against expected date range
```

### 4.6 Validation

**Present:**
- Float/int parsing with `_to_float()` / `_to_int()` (NaN, Inf rejected)
- Rows where close is None are skipped

**ABSENT:**
- ❌ No date range validation (requesting 200 days but getting 180 back is silent)
- ❌ No gap detection (missing trading days within range)
- ❌ No OHLC sanity check (low ≤ open ≤ high, low ≤ close ≤ high)
- ❌ No volume sanity check (0 volume on a trading day)
- ❌ No verification that bars are in chronological order

---

## 5. Account Data — Positions / Orders / Balances

### 5.1 Endpoints / Methods

| Endpoint | Method | Client Method |
|----------|--------|---------------|
| `GET /v1/accounts/{id}/positions` | GET | `get_positions()` |
| `GET /v1/accounts/{id}/orders` | GET | `get_orders(status=None)` |
| `GET /v1/accounts/{id}/balances` | GET | `get_balances()` |

### 5.2 Call Chains

| Entry Point | Terminal Call |
|-------------|---------------|
| `GET /api/trading/active` | `get_positions()` + `get_orders()` + `get_quotes(symbols)` for enrichment |
| `GET /api/trading/test-connection` | `get_balances()` |

### 5.3 Tradier String Quirk

```python
# Tradier returns {"positions": "null"} (string) instead of null — must guard:
def _extract_positions(payload):
    positions_wrapper = (payload or {}).get("positions")
    if not isinstance(positions_wrapper, dict):
        return []  # Filters out "null" string
```

### 5.4 Freshness / Caching

- **Caching**: ❌ NOT cached — always fetches fresh from Tradier
- **Market-hours awareness**: N/A (account data is always "current")

### 5.5 Validation

**ABSENT:**
- ❌ No validation that `account_id` in response matches configured `TRADIER_ACCOUNT_ID`
- ❌ No position quantity bounds check
- ❌ No cross-validation between positions and orders (e.g., order for a position that doesn't exist)

---

## 6. Order Execution — POST to `/v1/accounts/{id}/orders`

### 6.1 Endpoints / Methods

| Operation | Endpoint | Client Method |
|-----------|----------|---------------|
| Preview | `POST /v1/accounts/{id}/orders` (with `preview=true`) | `preview_multileg_order()` / `preview_raw_payload()` |
| Submit | `POST /v1/accounts/{id}/orders` | `place_multileg_order()` |
| Status check | `GET /v1/accounts/{id}/orders/{oid}` | `get_order_status()` |

### 6.2 Credential Routing

```python
# tradier_credentials.py — resolve_tradier_credentials():
# Purpose="DATA"      → Always LIVE credentials (market data is same data)
# Purpose="EXECUTION" + mode="paper" → PAPER credentials (sandbox.tradier.com)
# Purpose="EXECUTION" + mode="live"  → LIVE credentials (api.tradier.com)
```

### 6.3 Order Data Freshness Gate (LIVE ONLY)

```python
# trading/service.py — submit():
if account_mode == "live":
    freshness = evaluate_submit_freshness(
        ticket,
        max_age_seconds=self.settings.LIVE_DATA_MAX_AGE_SECONDS
    )
    if not freshness["data_fresh"]:
        raise HTTPException(400, detail=f"Live submit rejected: stale data ({freshness})")
```

**⚠️ FLAG**: This freshness gate only applies to **live** order submission. Paper orders and scanner evaluations have **no** freshness gate — they'll evaluate on whatever's in cache.

### 6.4 Execution Flow Safety

```python
# config.py:
TRADIER_EXECUTION_ENABLED: bool = os.getenv("TRADIER_EXECUTION_ENABLED", "false") == "true"
# Default is OFF — all orders become DRY_RUN

# service.py — submit():
if settings.ENVIRONMENT == "development" and mode == "live":
    mode = "paper"  # Force paper in development
```

---

## 7. Rate Limiting

### 7.1 Current State

**NO semaphore/throttle on the TradierClient itself.**

| Component | Rate Control |
|-----------|-------------|
| `TradierClient` | ❌ None — relies on cache + service batching |
| `PolygonClient` | ✅ `asyncio.Semaphore(5)` |
| `breadth_data_provider` | ✅ `asyncio.Semaphore(10)` for concurrent bar fetches |

### 7.2 Breadth Engine Load

The breadth engine is the **heaviest Tradier consumer**:
- ~150 `get_daily_bars()` calls per MI cycle (one per universe ticker)
- ~7-8 `get_quotes()` batches (50 symbols each)
- Runs every ~5 minutes on scheduler
- The Semaphore(10) in the breadth provider limits concurrent bar requests to 10

### 7.3 Rate Limit Detection

```python
# base_data_service.py — source health tracking:
if "too many requests" in text or "rate limit" in text or "429" in text:
    return "rate_limit"
# HTTP 429 is detected but NOT retried — logged as source health failure
```

### 7.4 HTTP Retry Configuration

```python
# http.py — request_json():
retries: int = 2           # 2 automatic retries
backoff_ms: int = 300      # 300ms → 600ms → 1200ms
# Retries on: 502, 503, 504 (transient 5xx)
# NOT retried: 401, 403, 404, 429, 500
```

**⚠️ FLAG**: HTTP 429 (rate limit) is NOT retried. If Tradier rate-limits the breadth engine mid-cycle, those tickers get no data for that cycle.

---

## 8. Caching

### 8.1 Cache Implementation

In-memory `TTLCache` (`app/utils/cache.py`):
- Async-safe with `asyncio.Lock()`
- LRU eviction at `maxsize=1024` entries
- TTL-based expiration
- No disk persistence (lost on restart)
- No "serve stale" fallback

### 8.2 TTL Configuration

| Data Type | Cache Key Pattern | TTL | Setting |
|-----------|-------------------|-----|---------|
| Equity quote | `tradier:quote:{symbol}` | 10s | `QUOTE_CACHE_TTL_SECONDS` |
| Multi-symbol quotes | `tradier:quotes:{sorted,symbols}` | 10s | `QUOTE_CACHE_TTL_SECONDS` |
| Option quotes (OCC) | `tradier:option_quotes:{sorted,symbols}` | 10s | `QUOTE_CACHE_TTL_SECONDS` |
| Expirations | `tradier:expirations:{symbol}` | 300s (5 min) | `EXPIRATIONS_CACHE_TTL_SECONDS` |
| Option chains | `tradier:chain:{symbol}:{exp}:{greeks}` | 60s | `CHAIN_CACHE_TTL_SECONDS` |
| Daily closes | `tradier:history:{symbol}:{start}:{end}` | 1800s (30 min) | `CANDLES_CACHE_TTL_SECONDS` |
| Daily bars | `tradier:bars:{symbol}:{start}:{end}` | 1800s (30 min) | `CANDLES_CACHE_TTL_SECONDS` |
| Intraday bars | `tradier:intraday:{symbol}:{start}:{end}:{interval}` | 1800s (30 min) | `CANDLES_CACHE_TTL_SECONDS` |
| Account data | Not cached | — | Always fresh |

### 8.3 Cache vs Fresh Distinguishability

**Can downstream consumers tell if data is cached?**

❌ **NO** — the `get_or_set()` cache API returns the value with no metadata about whether it was a cache hit. There is no `fetched_at` timestamp attached to Tradier responses at the cache layer.

The `_metric()` envelope used by `MarketContextService` adds a `fetched_at` field, but that's **above** the cache layer — it records when the metric was assembled into the context object, not when the underlying Tradier call actually happened.

---

## 9. Source Health Tracking

### 9.1 Implementation

`base_data_service.py` tracks per-provider health:

```python
# Health snapshot per provider:
{
    "last_http": int,               # Last HTTP status code
    "last_ok_ts": str,              # Timestamp of last success (ISO)
    "last_error_kind": str | None,  # "auth", "http_5xx", "rate_limit", "timeout", "network"
    "consecutive_5xx": int,         # Failure counter
    "failure_events": list,         # 5-minute rolling window
}
```

### 9.2 Health Classification

| Status | Classification |
|--------|---------------|
| 2xx | `green` |
| 1-2 recent failures | `yellow` |
| ≥3 consecutive failures | `red` |

### 9.3 Error Propagation

```python
# routes_active_trades.py — _error_payload_from_exception():
# Tradier-specific status code → user message:
401/403 → "API key invalid or unauthorized"
429     → "Rate limited" (with retry_after if available)
404     → "Account or endpoint not found"
5xx     → "Server error"
timeout → "Timeout connecting to Tradier"
```

---

## 10. Snapshot / Replay System

`SnapshotRecorder` captures raw Tradier chain payloads to disk for offline testing:
- `fetch_chain_raw_payload()` bypasses cache
- Raw JSON saved to `data/snapshots/`
- `SnapshotChainSource` replays saved payloads
- Used for development without live API access

---

## 11. Summary: Validation Matrix

| Field / Check | Quotes | Chains | Bars | Positions | Orders |
|---|---|---|---|---|---|
| Negative value rejection | ✅ bid/ask | ✅ bid/ask | — | — | — |
| Inverted spread (ask < bid) | ✅ both→None | ✅ skip row | — | — | — |
| Non-finite number rejection | — | ✅ strike/bid/ask/IV | ✅ close | — | — |
| Past date rejection | — | ✅ expiration | — | — | — |
| Type validation | — | ✅ put/call required | — | — | — |
| Range clamping | — | ✅ delta[-1,1], OI≥0, vol≥0 | — | — | — |
| IV normalization (>1→/100) | — | ✅ | — | — | — |
| Symbol format validation | ✅ `[A-Z0-9.\-]{1,10}` | ✅ | ✅ | — | — |
| OCC symbol format | — | — | — | — | ✅ via order build |
| **Maximum spread width** | ❌ | ❌ | — | — | — |
| **Zero bid detection** | ❌ | ❌ | — | — | — |
| **Extreme price/IV** | ❌ | ❌ (IV > 500% OK) | — | — | — |
| **Market-hours awareness** | ❌ | ❌ | ❌ | — | — |
| **Chain completeness** | — | ❌ | — | — | — |
| **Bar gap detection** | — | — | ❌ | — | — |
| **OHLC sanity** | — | — | ❌ | — | — |

---

## 12. Flagged Concerns

### 🔴 Critical

1. **No rate-limit semaphore on TradierClient** — breadth engine fires ~150 bar requests per cycle through only Semaphore(10) in the provider. If cache is cold (restart/eviction), all 150 requests hit Tradier within seconds.

2. **HTTP 429 not retried** — if Tradier rate-limits, the data is simply missing for that cycle (logged but not retried).

3. **No market-hours awareness** — fixed TTLs mean unnecessary API load on weekends and no distinction between live vs stale data during extended hours.

### 🟡 High

4. **Scanner operates on cached data with no freshness gate** — the options scanner can evaluate candidates using 60-second-old chain data with no warning. The freshness gate (`evaluate_submit_freshness()`) only applies to live order submission.

5. **Bid=0 passes validation** — a contract with bid=0 and ask=0.01 is considered valid. This produces 0 credit in spread calculations, passing through to EV computation.

6. **No chain completeness check** — if Tradier returns a chain with 3 contracts instead of 300, no alarm fires.

7. **Cache hit/miss invisible to consumers** — downstream code cannot distinguish fresh from cached data.

### 🟢 Low / Informational

8. **Daily closes discard date info** — `get_daily_closes()` returns `list[float]` without dates. Consumers assume chronological order without verification.

9. **Token lifecycle not managed** — no refresh/expiry handling for long-running processes.

10. **Tradier "null" string quirk** — handled correctly in positions/orders parsing but worth noting as a data integrity trap maintained by manual guards.

---

## 13. All Tradier Client Methods — Summary Table

| Method | Endpoint | Cached | TTL | Retry | Validation Layer | Primary Consumer |
|--------|----------|--------|-----|-------|-----------------|------------------|
| `get_quote()` | `/markets/quotes` | ✅ | 10s | 2× 5xx | _sanitize_quote | VIX, preview, health |
| `get_quotes()` | `/markets/quotes` | ✅ | 10s | 2× 5xx | _sanitize_quote per item | Breadth engine, active trades |
| `get_option_quotes()` | `/markets/quotes` | ✅ | 10s | 2× 5xx | _sanitize_quote per item | Active trade enrichment |
| `get_expirations()` | `/markets/options/expirations` | ✅ | 300s | 2× 5xx | Date parse + past rejection | Scanner orchestration |
| `get_chain()` | `/markets/options/chains` | ✅ | 60s | 2× 5xx | normalize_chain (comprehensive) | Scanner, preview |
| `fetch_chain_raw_payload()` | `/markets/options/chains` | ❌ | — | 2× 5xx | None (raw passthrough) | Snapshot capture |
| `get_daily_closes()` | `/markets/history` | ✅ | 1800s | 2× 5xx | Float parse | Price history fallback |
| `get_daily_closes_dated()` | `/markets/history` | ✅ | 1800s | 2× 5xx | Float + date parse | Dated price history |
| `get_daily_bars()` | `/markets/history` | ✅ | 1800s | 2× 5xx | Float/int parse | Breadth engine, mean reversion |
| `get_intraday_bars()` | `/markets/history` | ✅ | 1800s | 2× 5xx | Float/int + datetime merge | Intraday analysis |
| `health()` | `/markets/quotes` (SPY) | — | — | Implicit | Exception → False | Startup probe |
| `get_balances()` | `/accounts/{id}/balances` | ❌ | — | 2× 5xx | None | Connection test |
| `get_positions()` | `/accounts/{id}/positions` | ❌ | — | 2× 5xx | "null" string guard | Active trades |
| `get_orders()` | `/accounts/{id}/orders` | ❌ | — | 2× 5xx | "null" string guard | Active trades |
| `preview_multileg_order()` | `POST /accounts/{id}/orders` | ❌ | — | 2× 5xx | Payload build validation | Trading preview |
| `place_multileg_order()` | `POST /accounts/{id}/orders` | ❌ | — | 2× 5xx | Status mapping | Trading submit |
| `get_order_status()` | `/accounts/{id}/orders/{oid}` | ❌ | — | 2× 5xx | None | Reconciliation poll |
