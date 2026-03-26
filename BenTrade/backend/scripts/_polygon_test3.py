import requests, json, time

KEY = ""
BASE = "https://api.polygon.io"

def prev_close(ticker):
    r = requests.get(f"{BASE}/v2/aggs/ticker/{ticker}/prev", 
                     params={"adjusted": "true", "apiKey": KEY}, timeout=15)
    d = r.json()
    if r.status_code == 200 and d.get("results"):
        bar = d["results"][0]
        return {"status": 200, "close": bar.get("c"), "volume": bar.get("v"), "ticker_returned": bar.get("T")}
    return {"status": r.status_code, "error": d.get("message", d.get("error", "?"))}

def daily_bars(ticker):
    r = requests.get(f"{BASE}/v2/aggs/ticker/{ticker}/range/1/day/2026-03-17/2026-03-24",
                     params={"adjusted": "true", "sort": "asc", "limit": "10", "apiKey": KEY}, timeout=15)
    d = r.json()
    if r.status_code == 200 and d.get("results"):
        bar = d["results"][-1]
        return {"status": 200, "bars": len(d["results"]), "last_close": bar.get("c"), "ticker_returned": d.get("ticker")}
    return {"status": r.status_code, "error": d.get("message", d.get("error", "?"))}

# Batch 1: CL, DX, GC prev close
print("=== Commodities prev close ===")
for t in ["CL", "DX", "GC"]:
    time.sleep(12)  # 5 req/min = 12s spacing
    result = prev_close(t)
    print(f"  {t}: {json.dumps(result)}")

print("\n=== Commodities daily bars ===")
for t in ["CL", "DX", "GC"]:
    time.sleep(12)
    result = daily_bars(t)
    print(f"  {t}: {json.dumps(result)}")

print("\n=== VIX proxy ETFs ===")
for t in ["VXX", "VIXY", "SVXY"]:
    time.sleep(12)
    result = prev_close(t)
    print(f"  {t}: {json.dumps(result)}")

# Also check I:SPX with daily bars (to confirm 403, not rate limit)
print("\n=== Confirm I:SPX bars 403 (not rate limit) ===")
time.sleep(12)
result = daily_bars("I:SPX")
print(f"  I:SPX: {json.dumps(result)}")

# NDX 5min bars (confirm it works)
print("\n=== I:NDX 5min bars ===")
time.sleep(12)
r = requests.get(
    f"{BASE}/v2/aggs/ticker/I:NDX/range/5/minute/2026-03-24/2026-03-24",
    params={"adjusted": "true", "sort": "asc", "limit": "5", "apiKey": KEY}, timeout=15
)
d = r.json()
if r.status_code == 200 and d.get("results"):
    print(f"  I:NDX: {len(d['results'])} bars")
    for bar in d["results"][:3]:
        print(f"    o={bar.get('o'):.2f} h={bar.get('h'):.2f} l={bar.get('l'):.2f} c={bar.get('c'):.2f}")
else:
    print(f"  I:NDX: {r.status_code}")
