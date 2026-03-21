# Audit 1B: FRED Data Ingestion Map

> **Pass 1 — Data Integrity Audit**
> Generated: 2026-03-20 | Auditor: Copilot (code-traced)

---

## Executive Summary

BenTrade uses FRED as its **primary source for macro-economic data** — Treasury yields, credit spreads, oil prices, the USD index, CPI, and VIX (as EOD fallback). All FRED interactions flow through a single `FredClient` class with TTL-cached per-series fetches returning `{value, observation_date}` envelopes.

**Critical findings:**
- **PCOPPUSDM (copper) is MONTHLY** — can be 30+ days stale with only a soft confidence penalty, no rejection
- **No cross-series observation_date consistency check** — yield curve spread (DGS10 - DGS2) can mix data from different business days
- **No contiguity validation on multi-observation fetches** — regime service's 6-value "5-day delta" could span > 5 business days if FRED has gaps
- **No last-known-value fallback** — if FRED is down, all metrics become None (graceful but aggressive degradation)

---

## 1. FRED Client Architecture

### 1.1 Client Class

| Item | Value |
|------|-------|
| File | `app/clients/fred_client.py` |
| Class | `FredClient` |
| Base URL | `https://api.stlouisfed.org/fred` (configurable via `FRED_BASE_URL`) |
| Auth | API key via `FRED_KEY` env var (query param, not header) |
| HTTP | Shared `httpx.AsyncClient` via app-level DI |
| Cache | Shared `TTLCache` instance |

### 1.2 Core Methods

| Method | Purpose | Cache | Returns |
|--------|---------|-------|---------|
| `_fetch_latest_observation(series_id)` | Raw FRED API call | ❌ (internal) | `{"value": float, "observation_date": "YYYY-MM-DD"}` or `None` |
| `get_series_with_date(series_id)` | Cached wrapper | ✅ 300s TTL | `{"value": float, "observation_date": "YYYY-MM-DD"}` or `None` |
| `get_latest_series_value(series_id)` | Legacy backward-compat | ✅ (via above) | `float` or `None` (observation_date discarded) |
| `health()` | Heartbeat check | ❌ | `bool` |

### 1.3 API Endpoint

All FRED fetches use **one endpoint**:

```
GET https://api.stlouisfed.org/fred/series/observations
    ?series_id={ID}
    &sort_order=desc
    &limit={1 or N}
    &api_key={key}
    &file_type=json
```

### 1.4 Missing Value Handling

```python
# fred_client.py — _fetch_latest_observation():
raw_value = row.get("value")
if raw_value in (None, "."):  # FRED uses "." for missing/unreported values
    return None
try:
    return {"value": float(raw_value), "observation_date": row.get("date", "")}
except (TypeError, ValueError):
    return None
```

**Assessment**: Correct — FRED's "." sentinel is properly handled. Non-numeric values don't crash.

---

## 2. FRED Series Inventory

### 2.1 Series Consumed via MarketContextService

These are fetched in parallel by `market_context_service.get_market_context()` → `_fred_metric()`:

#### VIXCLS — CBOE Volatility Index (VIX)

| Item | Value |
|------|-------|
| Series ID | `VIXCLS` |
| What it measures | CBOE VIX closing level |
| Frequency | Daily (business days) |
| Publication lag | EOD+1 (Monday's VIX available Tuesday morning) |
| Fetched by | `market_context_service._vix_from_fred()` |
| Consumed by | VIX metric (EOD fallback after Tradier → Finnhub), credit pillar, regime analysis, news sentiment |
| Role | **Third-priority fallback** — only used if both Tradier and Finnhub fail for live VIX quote |
| Transformation | None — raw value used directly |
| Freshness tracking | ✅ `observation_date` in metric envelope, marked as `freshness: "eod"` |

```python
# market_context_service.py — VIX fallback chain:
vix_metric = await self._vix_from_tradier()    # Try live quote first
if vix_metric is None:
    vix_metric = await self._vix_from_finnhub()  # Then Finnhub
if vix_metric is None:
    vix_metric = await self._vix_from_fred()     # Then FRED EOD
```

**⚠️ FLAG**: When VIX falls back to FRED, the value is from the previous close. During a VIX spike (e.g., market open with 25% VIX vs prior close of 15), engines receive the stale 15 — marked as "eod" but this data could be materially wrong.

#### DGS10 — 10-Year Treasury Yield

| Item | Value |
|------|-------|
| Series ID | `DGS10` |
| What it measures | 10-Year Treasury Constant Maturity Rate |
| Frequency | Daily (business days) |
| Publication lag | ~1 business day |
| Fetched by | `market_context_service._fred_metric("DGS10")` |
| Consumed by | Rates & Yield Curve pillar, yield curve spread (derived), regime service (6-value history), cross-asset macro |
| Transformation | None (raw %); regime service computes 5-day delta in basis points |
| Freshness tracking | ✅ observation_date in envelope |

#### DGS2 — 2-Year Treasury Yield

| Item | Value |
|------|-------|
| Series ID | `DGS2` |
| What it measures | 2-Year Treasury Constant Maturity Rate |
| Frequency | Daily (business days) |
| Publication lag | ~1 business day |
| Fetched by | `market_context_service._fred_metric("DGS2")` |
| Consumed by | Rates & Yield Curve pillar, yield curve spread (derived), news sentiment |
| Transformation | None (raw %) |
| Freshness tracking | ✅ observation_date in envelope |

#### DGS30 — 30-Year Treasury Yield

| Item | Value |
|------|-------|
| Series ID | `DGS30` |
| What it measures | 30-Year Treasury Constant Maturity Rate |
| Frequency | Daily (business days) |
| Publication lag | ~1 business day |
| Fetched by | `market_context_service._fred_metric("DGS30")` |
| Consumed by | Rates & Yield Curve pillar |
| Transformation | None (raw %) |
| Freshness tracking | ✅ observation_date in envelope |

#### DFF — Federal Funds Effective Rate

| Item | Value |
|------|-------|
| Series ID | `DFF` |
| What it measures | Effective Federal Funds Rate |
| Frequency | Daily |
| Publication lag | ~1 business day |
| Fetched by | `market_context_service._fred_metric("DFF")` |
| Consumed by | Rates pillar, liquidity/financial conditions, cross-asset credit pillar |
| Transformation | None (raw %) |
| Freshness tracking | ✅ observation_date in envelope |

#### DCOILWTICO — WTI Crude Oil

| Item | Value |
|------|-------|
| Series ID | `DCOILWTICO` |
| What it measures | Cushing, OK WTI Spot Price ($/barrel) |
| Frequency | Daily (business days) |
| Publication lag | ~1 business day |
| Fetched by | `market_context_service._fred_metric("DCOILWTICO")` |
| Consumed by | Dollar & Commodity pillar, cross-asset macro |
| Transformation | Scored via ambiguous-zone logic: $45-$85 = neutral; extremes = risk signal |
| Freshness tracking | ✅ observation_date in envelope |

**⚠️ FLAG**: Oil scoring has an "ambiguous zone" ($45-$85) where the signal is explicitly labeled neutral — but this covers the vast majority of normal oil prices. The metric contributes to macro scoring but is mostly uninformative.

#### DTWEXBGS — Trade-Weighted US Dollar Index

| Item | Value |
|------|-------|
| Series ID | `DTWEXBGS` |
| What it measures | Trade-Weighted US Dollar Index (Broad) |
| Frequency | Daily / Nominal Daily (can be weekly in practice) |
| Publication lag | ~1 business day |
| Fetched by | `market_context_service._fred_metric("DTWEXBGS")` |
| Consumed by | Dollar & Commodity pillar, liquidity engine dollar data |
| Transformation | None — raw index value |
| Freshness tracking | ✅ observation_date in envelope |

**⚠️ FLAG**: DTWEXBGS is a **proxy for DXY**, not DXY itself. It's a Fed-computed trade-weighted index (26 currencies, trade-weighted) vs DXY (6 currencies, euro-heavy). Comment in code acknowledges: "directionally similar but not identical." Downstream consumers receive this as `usd_index` with no "proxy" label.

#### CPIAUCSL — Consumer Price Index

| Item | Value |
|------|-------|
| Series ID | `CPIAUCSL` |
| What it measures | Consumer Price Index for All Urban Consumers |
| Frequency | **Monthly** |
| Publication lag | **~2-3 weeks after month-end** |
| Fetched by | `market_context_service._compute_cpi_yoy()` (special: 13 observations) |
| Consumed by | CPI YoY metric in market context |
| Transformation | YoY: `(values[0] / values[12]) - 1.0` |
| Freshness tracking | ✅ observation_date of most recent month |

```python
# market_context_service.py — _compute_cpi_yoy():
payload = await request_json(
    self.fred.http_client, "GET",
    f"{self.fred.settings.FRED_BASE_URL}/series/observations",
    params={"series_id": "CPIAUCSL", "sort_order": "desc", "limit": 13, ...},
)
# ...
if len(values) >= 13 and values[12] != 0:
    yoy = (values[0] / values[12]) - 1.0
```

**⚠️ FLAG**: No contiguity validation. If any month is missing from FRED (values list has <13 entries after filtering "."s), the fallback is to skip the metric. But if exactly 13 values exist with one being from a non-consecutive month, the YoY calculation silently produces a wrong result.

### 2.2 Series Consumed via CrossAssetMacroDataProvider

These are fetched **directly** by `cross_asset_macro_data_provider.py`, **not** through MarketContextService:

#### NASDAQQGLDI — Gold Price Index

| Item | Value |
|------|-------|
| Series ID | `NASDAQQGLDI` |
| What it measures | NASDAQ Gold FLOWS103 Price Index (LBMA-based, USD) |
| Frequency | Daily (business days) |
| Publication lag | ~1 business day |
| Fetched by | `cross_asset_macro_data_provider._safe_fred("NASDAQQGLDI", "fred_gold")` |
| Consumed by | Defensive vs Growth pillar (Pillar 4), Macro Coherence pillar (Pillar 5) |
| Transformation | None — raw USD price |
| Freshness tracking | ✅ observation_date passed through `_safe_fred()` |
| **Note** | Replaced discontinued `GOLDAMGBD228NLBM` — no migration/alignment check for historical comparisons |

#### PCOPPUSDM — Copper Price 🔴

| Item | Value |
|------|-------|
| Series ID | `PCOPPUSDM` |
| What it measures | Global Price of Copper (USD per metric ton, LME) |
| Frequency | **MONTHLY** |
| Publication lag | **Typically 15-30+ business days** |
| Fetched by | `cross_asset_macro_data_provider._safe_fred("PCOPPUSDM", "fred_copper")` |
| Consumed by | Defensive vs Growth pillar, Macro Coherence pillar (growth confidence signal) |
| Transformation | None — raw USD price |
| Freshness tracking | ✅ observation_date tracked; `_days_stale()` computed |
| Staleness handling | Soft penalty: if > 5 days stale, penalty = `min(3 + max(0, (days - 15)) * 0.25, 8)` |

```python
# cross_asset_macro_data_provider.py:
copper_days_stale = _days_stale(copper_date)
if copper_days_stale is not None and copper_days_stale > 5:
    logger.info("event=cross_asset_copper_stale days_stale=%d ...", copper_days_stale, ...)
# No rejection — data still used; confidence penalty applied in engine
```

**🔴 CRITICAL FLAG**: Copper data is **always** ≥ 15 days stale (monthly frequency with publication lag). The staleness penalty caps at 8 points but never blocks the data. A trade decision made today could be influenced by a copper price from 45 days ago with only a modest confidence reduction.

#### BAMLC0A0CM — IG Credit Spread

| Item | Value |
|------|-------|
| Series ID | `BAMLC0A0CM` |
| What it measures | ICE BofA US Corporate Index Option-Adjusted Spread (Investment Grade) |
| Frequency | Daily (business days) |
| Publication lag | ~1-2 business days |
| Fetched by | `cross_asset_macro_data_provider._safe_fred("BAMLC0A0CM", "fred_ig_spread")` AND `liquidity_conditions_data_provider` (separate fetch) |
| Consumed by | Cross-asset Credit pillar, Liquidity/Financial Conditions Credit pillar |
| Transformation | None — raw basis points (e.g., 1.20 = 120 bps) |
| Freshness tracking | ✅ observation_date in response |

**⚠️ FLAG**: This series is fetched **twice** — once by `cross_asset_macro_data_provider` and once by `liquidity_conditions_data_provider`. Both go through the same `FredClient.get_series_with_date()` and share the 300s cache, so they'll get the same value. But the double-fetch is architecturally wasteful and creates two independent error-handling paths.

#### BAMLH0A0HYM2 — HY Credit Spread

| Item | Value |
|------|-------|
| Series ID | `BAMLH0A0HYM2` |
| What it measures | ICE BofA US High Yield Index Option-Adjusted Spread |
| Frequency | Daily (business days) |
| Publication lag | ~1-2 business days |
| Fetched by | `cross_asset_macro_data_provider._safe_fred("BAMLH0A0HYM2", "fred_hy_spread")` AND `liquidity_conditions_data_provider` (separate fetch) |
| Consumed by | Cross-asset Credit pillar, Liquidity/Financial Conditions Credit pillar, Macro Coherence |
| Transformation | None — raw basis points |
| Freshness tracking | ✅ observation_date in response |

**Same double-fetch flag as BAMLC0A0CM above.**

### 2.3 Series Consumed via RegimeService

The regime service fetches **multi-observation windows** directly from FRED (bypassing FredClient cache):

#### VIXCLS (6 observations) — Regime VIX History

| Item | Value |
|------|-------|
| Fetched by | `regime_service._fred_recent_values(FRED_VIX_SERIES_ID, 6)` |
| Purpose | Compute VIX 5-day change: `(vix_recent[0] - vix_recent[5]) / vix_recent[5]` |
| Transformation | Percentage change over 5 observations |

```python
# regime_service.py:
vix_recent = await self._fred_recent_values(
    self.base_data_service.fred_client.settings.FRED_VIX_SERIES_ID, 6,
)
vix_now = vix_recent[0] if vix_recent else self._safe_float(spy_snapshot.get("vix"))
vix_5d_prev = vix_recent[5] if len(vix_recent) > 5 else None
vix_5d_change = (
    (vix_now - vix_5d_prev) / vix_5d_prev
    if (vix_now is not None and vix_5d_prev not in (None, 0))
    else None
)
```

**⚠️ FLAG**: `_fred_recent_values()` requests 6 most recent observations with `sort_order=desc, limit=6`. It assumes these are consecutive business days. But FRED can have gaps (holidays, missing reports), so "6 observations" could span 7-10 calendar days. The "5-day delta" label is misleading — it's really "5-observation delta across an unknown date span."

#### DGS10 (6 observations) — Regime 10Y History

| Item | Value |
|------|-------|
| Fetched by | `regime_service._fred_recent_values("DGS10", 6)` |
| Purpose | Compute 10Y yield 5-day delta in basis points: `(now - 5d_prev) * 100.0` |
| Transformation | Difference × 100 (to bps) |

**Same contiguity flag applies.**

### 2.4 Series Consumed via NewsSentimentService (Legacy Fallback)

| Series ID | Field Name | Used When |
|-----------|-----------|-----------|
| `VIXCLS` | `vix` | MarketContextService unavailable |
| `DGS10` | `us_10y_yield` | MarketContextService unavailable |
| `DGS2` | `us_2y_yield` | MarketContextService unavailable |
| `FEDFUNDS` | `fed_funds_rate` | MarketContextService unavailable |
| `DCOILWTICO` | `oil_wti` | MarketContextService unavailable |
| `DTWEXBGS` | `usd_index` | MarketContextService unavailable |

```python
# news_sentiment_service.py — _fetch_macro_context():
# Primary path: use MarketContextService (centralized)
# Fallback path: fetch each series directly via FredClient
series_map = {
    "vix": "VIXCLS",
    "us_10y_yield": "DGS10",
    "us_2y_yield": "DGS2",
    "fed_funds_rate": "FEDFUNDS",  # NOTE: Uses FEDFUNDS, not DFF
    "oil_wti": "DCOILWTICO",
    "usd_index": "DTWEXBGS",
}
```

**⚠️ FLAG**: The news sentiment legacy path uses `FEDFUNDS` while the primary path (via MarketContextService) uses `DFF`. These are **different series**: `FEDFUNDS` is monthly effective rate, `DFF` is daily effective rate. In fallback mode, the fed funds data switches from daily to monthly granularity without any signal.

---

## 3. Freshness Handling

### 3.1 Observation Date Tracking

Every `get_series_with_date()` call returns `observation_date` in YYYY-MM-DD format. This date represents **when the observation was reported in the market** (not when BenTrade fetched it).

**MarketContextService wraps each FRED value in a metric envelope:**
```python
def _metric(value, source, observation_date=None, is_intraday=False, ...):
    return {
        "value": value,
        "source": source,
        "freshness": "eod" if observation_date else "delayed",
        "observation_date": observation_date,     # Market date
        "fetched_at": datetime.now(UTC).isoformat(),  # System time
        "is_intraday": is_intraday,
    }
```

✅ **observation_date vs fetched_at correctly distinguishes market date from fetch time.**

### 3.2 Staleness Tolerance

| Series | Staleness Detection | Maximum Tolerance | Action When Stale |
|--------|-------------------|-------------------|-------------------|
| DGS10, DGS2, DGS30, DFF | ❌ None explicit | Infinite (no check) | Silently serves old data |
| DCOILWTICO | ❌ None explicit | Infinite | Silently serves old data |
| DTWEXBGS | ❌ None explicit | Infinite | Silently serves old data |
| VIXCLS | ❌ None explicit | Infinite | Silently serves old data |
| PCOPPUSDM | ✅ `_days_stale()` check | ∞ (penalty only, no rejection) | Confidence penalty: 3-8 pts |
| BAMLC0A0CM | ❌ None explicit | Infinite | Silently serves old data |
| BAMLH0A0HYM2 | ❌ None explicit | Infinite | Silently serves old data |
| CPIAUCSL | ❌ None explicit | Monthly lag inherent | Silently serves old data |

**🔴 CRITICAL**: Only copper (PCOPPUSDM) has any staleness detection. All other series could be days or weeks stale (e.g., extended FRED outage, holiday backlog) with zero downstream signal. The `freshness: "eod"` label says "this is end-of-day data" but doesn't say "this is 5 days old."

### 3.3 Cross-Series Date Consistency

**NOT IMPLEMENTED.** When the MarketContextService fetches DGS10 and DGS2 in parallel, they can have different observation dates:

```python
# market_context_service.py — get_market_context():
ten_year, two_year, ... = await asyncio.gather(
    self._fred_metric("DGS10"),
    self._fred_metric("DGS2"),
    ...
)
# Derived: yield curve spread
if ten_year["value"] is not None and two_year["value"] is not None:
    yield_spread_val = round(ten_year["value"] - two_year["value"], 3)
    # ⚠️ NO CHECK: ten_year["observation_date"] == two_year["observation_date"]
```

**Scenario**: If DGS10 publishes Monday's value but DGS2's Monday value is delayed, the spread could use Monday's 10Y and Friday's 2Y — a cross-day mismatch. The freshness inheritance code picks the "stalest" label but doesn't detect the actual date mismatch.

---

## 4. Caching

### 4.1 Cache Configuration

| Setting | Value |
|---------|-------|
| TTL | 300 seconds (5 min) — `FRED_CACHE_TTL_SECONDS` |
| Implementation | In-memory `TTLCache` (shared with Tradier) |
| Key pattern | `fred:series:{series_id}:obs` |
| Maxsize | 1024 entries (shared pool) |
| Disk persistence | ❌ None |
| Serve-stale fallback | ❌ None |

### 4.2 Cache Bypass

**Regime service bypasses FredClient cache** — calls FRED API directly via `request_json()`:
```python
# regime_service.py — _fred_recent_values():
payload = await request_json(
    fred.http_client, "GET",
    f"{fred.settings.FRED_BASE_URL}/series/observations",
    params={"series_id": series_id, "sort_order": "desc", "limit": count, ...},
)
# This call is NOT cached — hits FRED API directly every time
```

**⚠️ FLAG**: The regime service's FRED calls bypass the TTL cache, meaning each regime calculation hits FRED directly. This is inefficient and could contribute to rate-limit issues during high-frequency regime checks.

### 4.3 CPI computation bypasses FredClient cache

`_compute_cpi_yoy()` also calls FRED directly (via `request_json`) rather than through `FredClient`:
```python
# market_context_service.py — _compute_cpi_yoy():
payload = await request_json(
    self.fred.http_client, "GET",
    f"{self.fred.settings.FRED_BASE_URL}/series/observations",
    params={"series_id": "CPIAUCSL", "limit": 13, ...},
)
```

---

## 5. Error Handling

### 5.1 Per-Series Fault Tolerance

```python
# cross_asset_macro_data_provider.py — _safe_fred():
async def _safe_fred(series_id, label):
    try:
        result = await self.fred.get_series_with_date(series_id)
        if result is not None:
            logger.info("event=cross_asset_fred_fetch_ok ...")
        else:
            logger.warning("event=cross_asset_fred_fetch_empty ...")
        return result
    except Exception as exc:
        source_errors[label] = str(exc)  # Recorded, not re-raised
        return None
```

**Pattern**: One source failure does NOT crash the pipeline. Source errors captured in `source_errors` dict and logged.

### 5.2 HTTP Error Handling

FRED API calls go through the shared `request_json()` utility:
- **Transient errors (502/503/504)**: Retried 2× with 300ms backoff
- **Auth errors (401/403)**: Immediate failure
- **Rate limit (429)**: ❌ Immediate failure, no retry
- **Timeout**: 15 second default → exception → caught by `_safe_fred()`

### 5.3 Source Health Tracking

FRED health tracked via `base_data_service`:
```python
# regime_service.py:
def _mark_fred_success(self, message):
    self.base_data_service._mark_success("fred", http_status=200, message=message)

def _mark_fred_failure(self, err):
    self.base_data_service._mark_failure("fred", err)
```

### 5.4 What Happens When FRED Is Completely Down

If all FRED fetches fail:
- MarketContextService returns `None` values for all macro metrics
- CrossAssetMacroDataProvider returns all-None pillar inputs with `source_errors` dict populated
- LiquidityConditionsDataProvider returns None for credit spreads
- RegimeService returns empty lists for VIX/10Y history → VIX falls back to Tradier quote
- Engines receive None inputs → produce reduced-confidence or fallback scores
- **No stale data served** — there is no "last known value" mechanism

---

## 6. Transformation Inventory

| Series | Transformation | Code Location | Formula |
|--------|---------------|---------------|---------|
| DGS10 - DGS2 | Yield curve spread | `market_context_service.py` | `ten_year["value"] - two_year["value"]` |
| CPIAUCSL (13 obs) | Year-over-Year % | `market_context_service._compute_cpi_yoy()` | `(values[0] / values[12]) - 1.0` |
| VIX (6 obs) | 5-observation % change | `regime_service.py` | `(vix[0] - vix[5]) / vix[5]` |
| DGS10 (6 obs) | 5-observation Δ in bps | `regime_service.py` | `(now - prev) * 100.0` |
| DCOILWTICO | Zone scoring | `cross_asset_macro_engine.py` | $45-$85 = neutral; extremes = risk signal |
| PCOPPUSDM staleness | Confidence penalty | `cross_asset_macro_engine.py` | `min(3 + max(0, (days - 15)) * 0.25, 8)` |

All other FRED series are used **raw** — no normalization, z-scoring, or percentile ranking.

---

## 7. Double-Fetch Analysis

Several FRED series are fetched by multiple consumers:

| Series | Consumer 1 | Consumer 2 | Same Cache? | Risk |
|--------|-----------|-----------|-----------|------|
| BAMLC0A0CM | `cross_asset_macro_data_provider` | `liquidity_conditions_data_provider` | ✅ Yes (shared FredClient cache) | Architecturally duplicative; both get same value |
| BAMLH0A0HYM2 | `cross_asset_macro_data_provider` | `liquidity_conditions_data_provider` | ✅ Yes | Same |
| VIXCLS | `market_context_service` | `regime_service` (6 obs) | ❌ No — regime calls bypass cache | Regime may get different observation than market context |
| DGS10 | `market_context_service` | `regime_service` (6 obs) | ❌ No — regime calls bypass cache | Same |

**⚠️ FLAG**: VIXCLS and DGS10 are consumed by both the market context (single latest value, cached) and the regime service (6 recent values, uncached direct API call). These two paths could return different "latest" values if FRED updates between the calls.

---

## 8. Summary Table

| Series ID | Description | Frequency | Typical Lag | Consuming Engine(s) | Staleness Tolerance | Transformation |
|-----------|-------------|-----------|-------------|---------------------|---------------------|----------------|
| VIXCLS | CBOE VIX | Daily | EOD+1 | VIX fallback, Credit, Regime, News | ❌ Unlimited | Raw; 5-obs Δ% (regime) |
| DGS10 | 10Y Treasury | Daily | ~1 BD | Rates, Spread, Regime, Macro | ❌ Unlimited | Raw; 5-obs Δ bps (regime) |
| DGS2 | 2Y Treasury | Daily | ~1 BD | Rates, Spread, News | ❌ Unlimited | Raw |
| DGS30 | 30Y Treasury | Daily | ~1 BD | Rates | ❌ Unlimited | Raw |
| DFF | Fed Funds (daily) | Daily | ~1 BD | Rates, Credit, Liquidity | ❌ Unlimited | Raw |
| FEDFUNDS | Fed Funds (monthly) | Monthly | ~1 BD | News (legacy fallback only) | ❌ Unlimited | Raw |
| DTWEXBGS | USD Index (proxy) | Daily/Weekly | ~1 BD | Dollar/Commodity, Liquidity | ❌ Unlimited | Raw (labeled as "usd_index", not "proxy") |
| DCOILWTICO | WTI Crude Oil | Daily | ~1 BD | Dollar/Commodity, Macro | ❌ Unlimited | Zone scoring ($45-$85 neutral) |
| NASDAQQGLDI | Gold Price Index | Daily | ~1 BD | Defensive/Growth, Coherence | ❌ Unlimited | Raw |
| PCOPPUSDM | Copper Price | **Monthly** | **15-30+ BD** | Growth signal, Coherence | ✅ Penalty >5d (caps 8 pts) | Raw |
| BAMLC0A0CM | IG Credit Spread | Daily | ~1-2 BD | Credit, Liquidity (2× fetch) | ❌ Unlimited | Raw |
| BAMLH0A0HYM2 | HY Credit Spread | Daily | ~1-2 BD | Credit, Liquidity (2× fetch) | ❌ Unlimited | Raw |
| CPIAUCSL | CPI (all urban) | **Monthly** | **~2-3 weeks** | CPI YoY metric | ❌ Unlimited | YoY: `val[0]/val[12] - 1` |

---

## 9. Flagged Concerns

### 🔴 Critical

1. **PCOPPUSDM (copper) is always ≥15 days stale** — monthly frequency means the "latest" observation is from weeks ago. The confidence penalty (3-8 points) is too soft for data this stale. Growth confidence scores that rely on copper could be making decisions on month-old data.

2. **No staleness tolerance on any daily series** — if FRED has a 3-day outage, the "latest" observations would be from the previous week. No alarm fires. No confidence penalty. Engines consume the value as-is. The `observation_date` is available but nothing checks how far it is from today.

3. **No cross-series date matching** — the yield curve spread (DGS10 - DGS2) can silently mix observations from different business days. Same risk applies to any derived metric combining multiple FRED series.

### 🟡 High

4. **Regime service bypasses FredClient cache** — `_fred_recent_values()` calls FRED API directly via `request_json()`, skipping the 300s TTL cache. Same for CPI YoY computation. This creates redundant API calls and could hit FRED rate limits.

5. **FEDFUNDS vs DFF inconsistency** — news sentiment legacy path uses `FEDFUNDS` (monthly) while primary path uses `DFF` (daily). If fallback triggers, the fed funds granularity silently changes.

6. **CPI YoY no contiguity check** — the 13-observation window assumes consecutive months. Missing months would produce an incorrect YoY calculation.

7. **6-observation "5-day delta" is misleading** — regime service labels it as "5-day" but it's really "5 most recent FRED observations" which could span 7-12 calendar days (weekends, holidays, data gaps).

### 🟢 Low / Informational

8. **DTWEXBGS is proxy for DXY** — acknowledged in code comments but downstream consumers see `usd_index` with no proxy label.

9. **GOLDAMGBD228NLBM replaced by NASDAQQGLDI** — no migration path or alignment check for historical data comparisons.

10. **No last-known-value fallback** — if FRED fails, metrics are None. This is correct behavior per data integrity rules (prefer None over fabricated), but means a FRED outage immediately degrades all macro engines.

11. **Credit spread series fetched by two independent providers** — BAMLC0A0CM and BAMLH0A0HYM2 are fetched by both `cross_asset_macro_data_provider` and `liquidity_conditions_data_provider`. Same cache hit, but two independent error-handling paths and two potential failure modes.
