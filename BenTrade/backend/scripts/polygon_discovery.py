"""Polygon.io futures & index data discovery script."""
import requests
import json
import sys
from datetime import date, timedelta

KEY = ""
BASE = "https://api.polygon.io"

today = date.today().isoformat()
week_ago = (date.today() - timedelta(days=7)).isoformat()

results = []

def test_snapshot(description, tickers_csv):
    """Test the v3/snapshot endpoint."""
    url = f"{BASE}/v3/snapshot"
    params = {"ticker.any_of": tickers_csv, "apiKey": KEY}
    r = requests.get(url, params=params, timeout=15)
    entry = {
        "description": description,
        "endpoint": "v3/snapshot",
        "tickers_queried": tickers_csv,
        "status": r.status_code,
    }
    data = r.json()
    ticker_results = []
    for item in data.get("results", []):
        tr = {"ticker": item.get("ticker")}
        if item.get("error"):
            tr["error"] = item["error"]
            tr["message"] = item.get("message", "")
        else:
            # Extract value from session/last_trade
            session = item.get("session", {})
            tr["available"] = True
            tr["close"] = session.get("close")
            tr["open"] = session.get("open")
            tr["high"] = session.get("high")
            tr["low"] = session.get("low")
            tr["previous_close"] = session.get("previous_close")
            tr["change_percent"] = session.get("change_percent")
            if item.get("value"):
                tr["value"] = item["value"]
            if item.get("last_trade"):
                tr["last_trade"] = item["last_trade"]
        ticker_results.append(tr)
    entry["ticker_results"] = ticker_results
    results.append(entry)
    print(f"  [{r.status_code}] {description}")
    for tr in ticker_results:
        status = "OK" if tr.get("available") else tr.get("error", "UNKNOWN")
        print(f"    {tr['ticker']}: {status}")
    return entry

def test_prev_close(description, ticker):
    """Test the v2/aggs/ticker/{ticker}/prev endpoint."""
    url = f"{BASE}/v2/aggs/ticker/{ticker}/prev"
    params = {"adjusted": "true", "apiKey": KEY}
    r = requests.get(url, params=params, timeout=15)
    entry = {
        "description": description,
        "endpoint": f"v2/aggs/ticker/{ticker}/prev",
        "ticker": ticker,
        "status": r.status_code,
    }
    data = r.json()
    if r.status_code == 200 and data.get("results"):
        bar = data["results"][0]
        entry["available"] = True
        entry["sample_data"] = {
            "open": bar.get("o"),
            "high": bar.get("h"),
            "low": bar.get("l"),
            "close": bar.get("c"),
            "volume": bar.get("v"),
            "timestamp": bar.get("t"),
        }
    else:
        entry["available"] = False
        entry["error"] = data.get("message", data.get("status", "Unknown"))
    results.append(entry)
    status = "OK" if entry.get("available") else entry.get("error", "FAIL")
    print(f"  [{r.status_code}] {description}: {status}")
    if entry.get("sample_data"):
        print(f"    close={entry['sample_data']['close']}")
    return entry

def test_agg_bars(description, ticker, timespan="day", multiplier=1):
    """Test the v2/aggs/ticker/{ticker}/range endpoint."""
    url = f"{BASE}/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{week_ago}/{today}"
    params = {"adjusted": "true", "sort": "asc", "limit": "10", "apiKey": KEY}
    r = requests.get(url, params=params, timeout=15)
    entry = {
        "description": description,
        "endpoint": f"v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}",
        "ticker": ticker,
        "timespan": timespan,
        "multiplier": multiplier,
        "status": r.status_code,
    }
    data = r.json()
    if r.status_code == 200 and data.get("results"):
        entry["available"] = True
        entry["results_count"] = data.get("resultsCount", len(data["results"]))
        bar = data["results"][-1]  # most recent
        entry["sample_data"] = {
            "open": bar.get("o"),
            "high": bar.get("h"),
            "low": bar.get("l"),
            "close": bar.get("c"),
            "volume": bar.get("v"),
            "timestamp": bar.get("t"),
        }
    else:
        entry["available"] = False
        entry["error"] = data.get("message", data.get("status", "Unknown"))
    results.append(entry)
    status = "OK" if entry.get("available") else entry.get("error", "FAIL")
    print(f"  [{r.status_code}] {description}: {status}")
    if entry.get("sample_data"):
        print(f"    bars={entry['results_count']}, last_close={entry['sample_data']['close']}")
    return entry

# ============================================================
# SNAPSHOT TESTS
# ============================================================
print("\n=== SNAPSHOT TESTS ===")
test_snapshot("Index snapshots (SPX, NDX, RUT, DJI)", "I:SPX,I:NDX,I:RUT,I:DJI")
test_snapshot("Futures plain (ES, NQ, RTY, YM)", "ES,NQ,RTY,YM")
test_snapshot("VIX indices", "I:VIX,I:VIX1D,I:VIX9D,I:VIX3M,I:VIX6M")
test_snapshot("Commodities/FX", "CL,GC,DX,C:EURUSD")
test_snapshot("Treasury indices", "I:TNX,I:TYX,I:IRX,I:FVX")

# ============================================================
# PREVIOUS CLOSE TESTS
# ============================================================
print("\n=== PREVIOUS CLOSE TESTS ===")
# Core indices
for t in ["I:SPX", "I:NDX", "I:RUT", "I:DJI"]:
    test_prev_close(f"Prev close {t}", t)

# VIX family
for t in ["I:VIX", "I:VIX1D", "I:VIX9D", "I:VIX3M", "I:VIX6M"]:
    test_prev_close(f"Prev close {t}", t)

# Treasury indices
for t in ["I:TNX", "I:TYX", "I:IRX", "I:FVX"]:
    test_prev_close(f"Prev close {t}", t)

# Commodities/Dollar
for t in ["CL", "DX", "GC"]:
    test_prev_close(f"Prev close {t}", t)

# ============================================================
# AGGREGATE BAR TESTS
# ============================================================
print("\n=== AGGREGATE BAR TESTS (daily) ===")
for t in ["I:SPX", "I:NDX", "I:RUT", "I:DJI", "I:VIX", "I:VIX1D", "I:VIX3M"]:
    test_agg_bars(f"Daily bars {t}", t, "day", 1)

print("\n=== AGGREGATE BAR TESTS (5min intraday) ===")
for t in ["I:SPX", "I:NDX", "I:VIX"]:
    test_agg_bars(f"5min bars {t}", t, "minute", 5)

print("\n=== AGGREGATE BAR TESTS (hourly) ===")
for t in ["I:SPX", "I:NDX", "I:VIX"]:
    test_agg_bars(f"Hourly bars {t}", t, "hour", 1)

# Try ETF proxies as fallback (these should work on basic plans)
print("\n=== ETF PROXY TESTS (prev close) ===")
for t in ["SPY", "QQQ", "IWM", "DIA"]:
    test_prev_close(f"Prev close ETF {t}", t)

print("\n=== ETF PROXY TESTS (daily bars) ===")
for t in ["SPY", "QQQ", "IWM", "DIA", "UVXY", "VXX"]:
    test_agg_bars(f"Daily bars ETF {t}", t, "day", 1)

# ============================================================
# TICKER SEARCH - discover available futures tickers
# ============================================================
print("\n=== TICKER SEARCH ===")
for search_term in ["ES", "VIX", "SPX"]:
    url = f"{BASE}/v3/reference/tickers"
    params = {"search": search_term, "market": "indices", "limit": 5, "apiKey": KEY}
    r = requests.get(url, params=params, timeout=15)
    data = r.json()
    print(f"  Search '{search_term}' (market=indices): {r.status_code}")
    for item in data.get("results", [])[:5]:
        print(f"    {item.get('ticker')}: {item.get('name')}")

# Write results
output = {
    "polygon_plan": "Stocks Basic (inferred from entitlements)",
    "api_key_found": True,
    "api_key_source": "BenTrade/backend/.env (POLYGON_API_KEY)",
    "base_url": BASE,
    "auth_pattern": "Query param apiKey=",
    "existing_usage": "OHLCV daily bars for stock scanning (polygon_client.py)", 
    "test_date": today,
    "tests": results,
}

print("\n\nWriting results...")
print(json.dumps(output, indent=2)[:500])

# Save the full results for parsing
with open("_polygon_raw_results.json", "w") as f:
    json.dump(output, f, indent=2)

print("\nRaw results saved to _polygon_raw_results.json")
