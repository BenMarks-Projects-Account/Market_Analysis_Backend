import requests, json, time

KEY = ""
BASE = "https://api.polygon.io"

# CL, DX, GC prev close details
for t in ["CL", "DX", "GC"]:
    time.sleep(1.5)
    r = requests.get(f"{BASE}/v2/aggs/ticker/{t}/prev", params={"adjusted": "true", "apiKey": KEY}, timeout=15)
    print(f"=== {t} prev close === status={r.status_code}")
    print(json.dumps(r.json(), indent=2))
    print()

# ETF proxies
time.sleep(3)
print("=== ETF prev close ===")
for t in ["SPY", "QQQ", "IWM", "DIA"]:
    time.sleep(1.5)
    r = requests.get(f"{BASE}/v2/aggs/ticker/{t}/prev", params={"adjusted": "true", "apiKey": KEY}, timeout=15)
    d = r.json()
    if r.status_code == 200 and d.get("results"):
        bar = d["results"][0]
        print(f"  {t}: close={bar.get('c')}, vol={bar.get('v')}")
    else:
        print(f"  {t}: {r.status_code} - {d.get('message', '?')}")

time.sleep(3)
print("\n=== ETF daily bars ===")
for t in ["SPY", "QQQ", "IWM", "DIA"]:
    time.sleep(1.5)
    r = requests.get(
        f"{BASE}/v2/aggs/ticker/{t}/range/1/day/2026-03-17/2026-03-24",
        params={"adjusted": "true", "sort": "asc", "limit": "10", "apiKey": KEY},
        timeout=15,
    )
    d = r.json()
    if r.status_code == 200 and d.get("results"):
        print(f"  {t}: {len(d['results'])} bars, last_close={d['results'][-1].get('c')}")
    else:
        print(f"  {t}: {r.status_code} - {d.get('message', '?')}")

# VIX proxy ETFs
time.sleep(3)
print("\n=== VIX proxy ETFs ===")
for t in ["UVXY", "VXX", "VIXY", "SVXY"]:
    time.sleep(1.5)
    r = requests.get(f"{BASE}/v2/aggs/ticker/{t}/prev", params={"adjusted": "true", "apiKey": KEY}, timeout=15)
    d = r.json()
    if r.status_code == 200 and d.get("results"):
        bar = d["results"][0]
        print(f"  {t}: close={bar.get('c')}")
    else:
        print(f"  {t}: {r.status_code} - {d.get('message', d.get('status', '?'))}")
