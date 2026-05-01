"""Second-pass Step-0 probes: alternate FMP paths + options/PC sources."""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx
from dotenv import dotenv_values

ENV_PATH = Path(__file__).resolve().parents[1] / "BenTrade" / "backend" / ".env"
env = dotenv_values(ENV_PATH)
FMP = env.get("FMP_API_KEY", "")
FINN = env.get("FINNHUB_API_KEY") or env.get("FINNHUB_KEY", "")


def probe(name: str, url: str, params: dict | None = None, headers: dict | None = None) -> None:
    print("\n" + "=" * 70)
    print(name)
    print("=" * 70)
    safe_params = {k: v for k, v in (params or {}).items() if k not in ("apikey", "token")}
    print(f"URL: {url}")
    print(f"Params: {safe_params}")
    try:
        r = httpx.get(url, params=params, headers=headers, timeout=20.0)
        print(f"Status: {r.status_code}  CT: {r.headers.get('content-type','')}")
        if "json" in r.headers.get("content-type", ""):
            d = r.json()
            if isinstance(d, list):
                print(f"List length: {len(d)}")
                if d and isinstance(d[0], dict):
                    print("Keys:", list(d[0].keys())[:20])
                    print("Sample:", json.dumps(d[0], indent=2, default=str)[:1500])
            elif isinstance(d, dict):
                print("Keys:", list(d.keys())[:20])
                print("Sample:", json.dumps(d, indent=2, default=str)[:1500])
            else:
                print("Body:", str(d)[:800])
        else:
            print(r.text[:800])
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")


def main() -> int:
    today = date.today()
    # FMP legacy v3 paths for etf-info (ETF NAV/shares_outstanding)
    probe("FMP v3: /api/v3/etf-info/SPY",
          "https://financialmodelingprep.com/api/v3/etf-info/SPY",
          params={"apikey": FMP})
    probe("FMP v3: /api/v3/etf-holder/SPY",
          "https://financialmodelingprep.com/api/v3/etf-holder/SPY",
          params={"apikey": FMP})
    probe("FMP stable: /etf/holdings?symbol=SPY",
          "https://financialmodelingprep.com/stable/etf/holdings",
          params={"symbol": "SPY", "apikey": FMP})
    probe("FMP stable: /etf-holdings?symbol=SPY",
          "https://financialmodelingprep.com/stable/etf-holdings",
          params={"symbol": "SPY", "apikey": FMP})
    probe("FMP stable: /etf/info?symbol=SPY",
          "https://financialmodelingprep.com/stable/etf/info",
          params={"symbol": "SPY", "apikey": FMP})
    probe("FMP stable: /etfs (list)",
          "https://financialmodelingprep.com/stable/etfs",
          params={"apikey": FMP})
    probe("FMP stable: /profile?symbol=SPY (has sharesOutstanding)",
          "https://financialmodelingprep.com/stable/profile",
          params={"symbol": "SPY", "apikey": FMP})
    # FMP put/call / options signals
    probe("FMP stable: /options?symbol=SPY",
          "https://financialmodelingprep.com/stable/options",
          params={"symbol": "SPY", "apikey": FMP})
    probe("FMP stable: /put-call-ratio?symbol=SPY",
          "https://financialmodelingprep.com/stable/put-call-ratio",
          params={"symbol": "SPY", "apikey": FMP})
    probe("FMP stable: /equity-put-call-ratio",
          "https://financialmodelingprep.com/stable/equity-put-call-ratio",
          params={"apikey": FMP})
    # CBOE with browser UA
    ua = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
          "Accept": "text/csv, text/plain, */*"}
    probe("CBOE: equity_pc.csv WITH UA",
          "https://cdn.cboe.com/api/global/us_indices/daily_market_statistics/equity_pc.csv",
          headers=ua)
    probe("CBOE: Pubic PC via www.cboe.com",
          "https://www.cboe.com/us/options/market_statistics/daily/",
          headers=ua)
    # Finnhub — check plan
    probe("Finnhub: /quote SPY (sanity)",
          "https://finnhub.io/api/v1/quote",
          params={"symbol": "SPY", "token": FINN})
    probe("Finnhub: stock/option-chain",
          "https://finnhub.io/api/v1/stock/option-chain",
          params={"symbol": "SPY", "token": FINN})
    # FMP stable technical indicators (might be used to derive things)
    probe("FMP stable: /technical-indicators/sma?symbol=SPY",
          "https://financialmodelingprep.com/stable/technical-indicators/sma",
          params={"symbol": "SPY", "periodLength": 20, "timeframe": "1day", "apikey": FMP})
    return 0


if __name__ == "__main__":
    sys.exit(main())
