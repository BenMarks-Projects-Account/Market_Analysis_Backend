"""Yahoo Finance futures data discovery."""
import yfinance as yf
import json
from datetime import datetime

# Futures tickers on Yahoo
futures_tickers = {
    "ES=F": "E-mini S&P 500 Futures",
    "NQ=F": "E-mini Nasdaq 100 Futures",
    "RTY=F": "E-mini Russell 2000 Futures",
    "YM=F": "Mini Dow Futures",
    "CL=F": "Crude Oil Futures",
    "DX-Y.NYB": "US Dollar Index Futures",
    "ZN=F": "10-Year Treasury Note Futures",
    "VX=F": "VIX Futures (front month)",  # may not work
}

index_tickers = {
    "^GSPC": "S&P 500 Index",
    "^NDX": "Nasdaq 100 Index",
    "^RUT": "Russell 2000 Index",
    "^DJI": "Dow Jones Industrial Average",
    "^VIX": "CBOE Volatility Index",
    "^TNX": "10-Year Treasury Yield",
}

results = {}

# Test futures
print("=== YAHOO FINANCE FUTURES TICKERS ===")
for ticker, name in futures_tickers.items():
    try:
        t = yf.Ticker(ticker)
        info = t.fast_info
        hist = t.history(period="5d", interval="1h")
        
        entry = {
            "name": name,
            "available": not hist.empty,
            "rows": len(hist),
        }
        if not hist.empty:
            last_row = hist.iloc[-1]
            entry["last_close"] = round(float(last_row["Close"]), 2)
            entry["last_timestamp"] = str(hist.index[-1])
            entry["columns"] = list(hist.columns)
            print(f"  {ticker} ({name}): OK - last={entry['last_close']} @ {entry['last_timestamp']}, {len(hist)} bars")
        else:
            print(f"  {ticker} ({name}): EMPTY")
        results[ticker] = entry
    except Exception as e:
        results[ticker] = {"name": name, "available": False, "error": str(e)}
        print(f"  {ticker} ({name}): ERROR - {e}")

# Test indices
print("\n=== YAHOO FINANCE INDEX TICKERS ===")
for ticker, name in index_tickers.items():
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="5d", interval="1h")
        
        entry = {
            "name": name,
            "available": not hist.empty,
            "rows": len(hist),
        }
        if not hist.empty:
            last_row = hist.iloc[-1]
            entry["last_close"] = round(float(last_row["Close"]), 2)
            entry["last_timestamp"] = str(hist.index[-1])
            print(f"  {ticker} ({name}): OK - last={entry['last_close']} @ {entry['last_timestamp']}, {len(hist)} bars")
        else:
            print(f"  {ticker} ({name}): EMPTY")
        results[ticker] = entry
    except Exception as e:
        results[ticker] = {"name": name, "available": False, "error": str(e)}
        print(f"  {ticker} ({name}): ERROR - {e}")

# Test daily bars for futures
print("\n=== YAHOO FINANCE DAILY BARS (futures) ===")
for ticker in ["ES=F", "NQ=F", "RTY=F", "YM=F", "CL=F", "^VIX"]:
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="5d", interval="1d")
        if not hist.empty:
            last = hist.iloc[-1]
            print(f"  {ticker}: {len(hist)} daily bars, last close={round(float(last['Close']), 2)}")
        else:
            print(f"  {ticker}: EMPTY")
    except Exception as e:
        print(f"  {ticker}: ERROR - {e}")

# Test 5-min bars
print("\n=== YAHOO FINANCE 5-MIN BARS (futures) ===")
for ticker in ["ES=F", "NQ=F", "^VIX"]:
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="1d", interval="5m")
        if not hist.empty:
            print(f"  {ticker}: {len(hist)} 5min bars, last={round(float(hist.iloc[-1]['Close']), 2)} @ {hist.index[-1]}")
        else:
            print(f"  {ticker}: EMPTY")
    except Exception as e:
        print(f"  {ticker}: ERROR - {e}")

# Write results
print("\n\nResults summary:")
print(json.dumps(results, indent=2, default=str))

with open("_yahoo_results.json", "w") as f:
    json.dump(results, f, indent=2, default=str)
