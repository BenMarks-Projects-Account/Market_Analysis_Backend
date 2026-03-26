"""Quick test of yfinance rate limiting and data availability."""
import yfinance as yf
import time

tickers = ["ES=F", "NQ=F", "^VIX", "CL=F", "DX-Y.NYB"]

print("Testing sequential Yahoo Finance requests with 2s delay between each...")
for ticker in tickers:
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="5d", interval="1d")
        if hist is not None and not hist.empty:
            last = hist.iloc[-1]["Close"]
            prev = hist.iloc[-2]["Close"] if len(hist) > 1 else None
            print(f"  {ticker}: last={last:.2f}, prev={prev:.2f if prev else 0}, rows={len(hist)}")
        else:
            print(f"  {ticker}: EMPTY")
    except Exception as e:
        print(f"  {ticker}: {type(e).__name__} - {e}")
    time.sleep(2)

print("\nDone.")
