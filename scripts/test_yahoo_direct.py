"""Test direct Yahoo Finance API (bypassing yfinance library)."""
import requests
import json

tickers = {
    "es": "ES=F",
    "nq": "NQ=F",
    "rty": "RTY=F",
    "ym": "YM=F",
    "vix": "^VIX",
    "cl": "CL=F",
    "dx": "DX-Y.NYB",
    "zn": "ZN=F",
    "tnx": "^TNX",
    "vxx": "VXX",
}

headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
base = "https://query1.finance.yahoo.com/v8/finance/chart"

print("Testing direct Yahoo Finance chart API for all instruments...\n")
for inst, ticker in tickers.items():
    url = f"{base}/{ticker}?range=5d&interval=1d"
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            result = data.get("chart", {}).get("result", [])
            if result:
                meta = result[0].get("meta", {})
                prev_close = meta.get("chartPreviousClose") or meta.get("previousClose")
                reg_price = meta.get("regularMarketPrice")
                quotes = result[0].get("indicators", {}).get("quote", [{}])[0]
                closes = quotes.get("close", [])
                # Last non-None close
                last_close = None
                for c in reversed(closes):
                    if c is not None:
                        last_close = c
                        break
                print(f"  {inst:4s} ({ticker:12s}): last={last_close}, prevClose={prev_close}, regPrice={reg_price}, bars={len(closes)}")
            else:
                print(f"  {inst:4s} ({ticker:12s}): NO RESULT in response")
        else:
            print(f"  {inst:4s} ({ticker:12s}): HTTP {resp.status_code}")
    except Exception as e:
        print(f"  {inst:4s} ({ticker:12s}): ERROR - {e}")

# Also test intraday bars (5 min)
print("\n--- Intraday 5min bars (1 day) ---")
for inst, ticker in [("es", "ES=F"), ("vix", "^VIX")]:
    url = f"{base}/{ticker}?range=1d&interval=5m"
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            result = data.get("chart", {}).get("result", [])
            if result:
                closes = result[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
                non_null = [c for c in closes if c is not None]
                print(f"  {inst:4s} ({ticker:12s}): {len(closes)} bars total, {len(non_null)} non-null")
            else:
                print(f"  {inst:4s} ({ticker:12s}): NO RESULT")
        else:
            print(f"  {inst:4s} ({ticker:12s}): HTTP {resp.status_code}")
    except Exception as e:
        print(f"  {inst:4s} ({ticker:12s}): ERROR - {e}")

print("\nDone.")
