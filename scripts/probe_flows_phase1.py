"""One-off Step-0 discovery probes for Flows & Positioning Phase 1.

Runs synchronous httpx calls against FMP, Finnhub, CBOE, Polygon to confirm
availability and field shapes. No dependencies on app code.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx
from dotenv import dotenv_values


ENV_PATH = Path(__file__).resolve().parents[1] / "BenTrade" / "backend" / ".env"
env = dotenv_values(ENV_PATH)
FMP = env.get("FMP_API_KEY", "")
FINN = env.get("FINNHUB_API_KEY") or env.get("FINNHUB_KEY", "")
POLY = env.get("POLYGON_API_KEY", "")


def hr(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def probe(name: str, url: str, params: dict | None = None, headers: dict | None = None) -> None:
    hr(name)
    print(f"URL: {url}")
    safe_params = {k: v for k, v in (params or {}).items() if k != "apikey"}
    print(f"Params (ex-apikey): {safe_params}")
    try:
        r = httpx.get(url, params=params, headers=headers, timeout=20.0)
        print(f"Status: {r.status_code}")
        ct = r.headers.get("content-type", "")
        print(f"Content-Type: {ct}")
        if "json" in ct:
            data = r.json()
            if isinstance(data, list):
                print(f"List length: {len(data)}")
                if data:
                    print("First item keys:", list(data[0].keys()) if isinstance(data[0], dict) else type(data[0]).__name__)
                    print("First item:", json.dumps(data[0], indent=2, default=str)[:2000])
                    if len(data) > 1:
                        print("Second item:", json.dumps(data[1], indent=2, default=str)[:1000])
            elif isinstance(data, dict):
                print("Top-level keys:", list(data.keys()))
                print("Full (truncated):", json.dumps(data, indent=2, default=str)[:2000])
            else:
                print("Body:", str(data)[:1000])
        else:
            body = r.text[:1500]
            print(f"Body (first 1500 chars):\n{body}")
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")


def main() -> int:
    print(f"FMP key present: {bool(FMP)}  Finnhub key present: {bool(FINN)}  Polygon key present: {bool(POLY)}")

    # 1. FMP COT list
    probe(
        "FMP: commitment-of-traders-report-list",
        "https://financialmodelingprep.com/stable/commitment-of-traders-report-list",
        params={"apikey": FMP},
    )

    # 2. FMP COT ES — last 8 weeks
    today = date.today()
    eight_weeks_ago = today - timedelta(weeks=10)
    probe(
        "FMP: commitment-of-traders-report symbol=ES (10w)",
        "https://financialmodelingprep.com/stable/commitment-of-traders-report",
        params={"symbol": "ES", "from": eight_weeks_ago.isoformat(), "to": today.isoformat(), "apikey": FMP},
    )

    # 2b. Try common alternate codes
    for sym in ("NQ", "VX", "ZN", "ZB", "SP"):
        probe(
            f"FMP: commitment-of-traders-report symbol={sym}",
            "https://financialmodelingprep.com/stable/commitment-of-traders-report",
            params={"symbol": sym, "from": eight_weeks_ago.isoformat(), "to": today.isoformat(), "apikey": FMP},
        )

    # 3. FMP COT analysis
    probe(
        "FMP: commitment-of-traders-report-analysis symbol=ES",
        "https://financialmodelingprep.com/stable/commitment-of-traders-report-analysis",
        params={"symbol": "ES", "apikey": FMP},
    )

    # 4. FMP etf-info
    probe(
        "FMP: etf-info symbol=SPY",
        "https://financialmodelingprep.com/stable/etf-info",
        params={"symbol": "SPY", "apikey": FMP},
    )

    # 5. FMP etf-holder / historical etf
    probe(
        "FMP: etf-holder symbol=SPY",
        "https://financialmodelingprep.com/stable/etf-holder",
        params={"symbol": "SPY", "apikey": FMP},
    )

    # 6. FMP historical eod for SPY (60d)
    sixty_days = today - timedelta(days=60)
    probe(
        "FMP: historical-price-eod/full symbol=SPY",
        "https://financialmodelingprep.com/stable/historical-price-eod/full",
        params={"symbol": "SPY", "from": sixty_days.isoformat(), "to": today.isoformat(), "apikey": FMP},
    )

    # 7. Finnhub put/call: try /stock/indicator? no, Finnhub doesn't have p/c. Try likely endpoints.
    probe(
        "Finnhub: option chain index?",
        "https://finnhub.io/api/v1/indicator",
        params={"symbol": "SPY", "resolution": "D", "indicator": "sma", "timeperiod": 20, "from": int((today - timedelta(days=90)).strftime("%s") if sys.platform != "win32" else "0"), "to": int(today.strftime("%s") if sys.platform != "win32" else "0"), "token": FINN},
    )

    # 8. CBOE equity put/call CSV
    probe(
        "CBOE: equity_pc.csv (equity put/call ratio)",
        "https://cdn.cboe.com/api/global/us_indices/daily_market_statistics/equity_pc.csv",
    )
    probe(
        "CBOE: index_pc.csv (index put/call ratio)",
        "https://cdn.cboe.com/api/global/us_indices/daily_market_statistics/index_pc.csv",
    )
    # Also try the volume_ratios endpoint (another CBOE public CSV commonly used)
    probe(
        "CBOE: volume_ratios_spx.csv",
        "https://cdn.cboe.com/api/global/us_indices/daily_market_statistics/volume_ratios_spx.csv",
    )
    probe(
        "CBOE: total_pc.csv",
        "https://cdn.cboe.com/api/global/us_indices/daily_market_statistics/total_pc.csv",
    )

    # 9. AAII sentiment (scrape candidate)
    probe(
        "AAII: sentiment survey results page",
        "https://www.aaii.com/sentimentsurvey/sent_results",
    )

    # 10. Polygon smoke test (prompt mentions it — confirm independent)
    probe(
        "Polygon: aggregate SPY daily 30d",
        f"https://api.polygon.io/v2/aggs/ticker/SPY/range/1/day/{(today - timedelta(days=40)).isoformat()}/{today.isoformat()}",
        params={"adjusted": "true", "sort": "asc", "apiKey": POLY},
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
