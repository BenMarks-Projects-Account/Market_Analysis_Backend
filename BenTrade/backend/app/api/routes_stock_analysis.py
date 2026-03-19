from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/stock", tags=["stock-analysis"])

logger = logging.getLogger(__name__)

_TICKER_RE = re.compile(r"^[A-Z\^]{1,6}$")

# ── Ticker Universe ─────────────────────────────────────────────────────────
# Combined quotable universe for the banner ticker.  Sources:
#   1. Options scanner symbols (DEFAULT_SCANNER_SYMBOLS minus non-quotable)
#   2. Balanced stock scanner universe (~196 symbols)
#   3. Sector / thematic ETFs
# Non-quotable index tickers (RUT, NDX, XSP, SPX, DJX) are excluded.
_TICKER_UNIVERSE: list[str] = [
    # Core index ETFs
    "SPY", "QQQ", "IWM", "DIA",
    # Sector ETFs
    "XLF", "XLK", "XLE", "XLY", "XLP", "XLV", "XLI", "XLU", "XLB", "XLRE", "XLC",
    # Technology
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "AMD", "CRM", "AVGO",
    "ADBE", "INTC", "CSCO", "ORCL", "NOW", "SHOP", "SNOW", "PANW", "CRWD", "NET",
    "PLTR", "MDB", "DDOG", "ZS", "FTNT", "MRVL", "ANET", "TEAM", "WDAY", "TTD",
    "UBER", "DASH", "COIN", "SQ", "PYPL", "INTU", "SNPS", "CDNS", "KLAC", "LRCX",
    "AMAT", "MU", "ON", "MCHP", "TXN", "QCOM", "ARM", "SMCI",
    # Healthcare
    "UNH", "JNJ", "LLY", "ABBV", "MRK", "PFE", "TMO", "ABT", "DHR", "BMY",
    "AMGN", "GILD", "VRTX", "REGN", "ISRG", "MDT", "SYK", "BSX", "EW", "ZTS",
    "DXCM", "MRNA", "BIIB", "GEHC", "HCA", "IDXX",
    # Financials
    "JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "SCHW", "AXP", "V",
    "MA", "COF", "USB", "PNC", "CME", "ICE", "MCO", "SPGI", "CB", "MMC",
    "AIG", "MET", "PRU", "ALL", "TRV",
    # Consumer discretionary
    "HD", "LOW", "NKE", "SBUX", "MCD", "TGT", "COST", "WMT", "TJX", "ROST",
    "LULU", "YUM", "DPZ", "CMG", "BKNG", "MAR", "HLT", "ABNB", "RCL", "NCLH",
    "F", "GM", "RIVN", "LCID",
    # Consumer staples
    "PG", "KO", "PEP", "PM", "MO", "CL", "EL", "KMB", "GIS", "K",
    "MDLZ", "HSY", "SJM", "STZ", "SAM",
    # Industrials
    "CAT", "DE", "HON", "UNP", "UPS", "FDX", "BA", "RTX", "LMT", "GD",
    "NOC", "GE", "MMM", "EMR", "ITW", "PH", "ROK", "ETN", "WM", "RSG",
    # Energy
    "XOM", "CVX", "COP", "EOG", "SLB", "MPC", "VLO", "PSX", "OXY", "DVN",
    "HAL", "BKR", "FANG", "PXD",
    # Communication
    "NFLX", "DIS", "CMCSA", "T", "VZ", "TMUS", "CHTR",
    # Materials
    "LIN", "APD", "SHW", "ECL", "DD", "DOW", "NEM", "FCX", "STLD", "NUE",
    # REITs
    "AMT", "PLD", "CCI", "EQIX", "SPG", "O", "PSA",
    # Utilities
    "NEE", "DUK", "SO", "AEP", "D",
]


class StockWatchlistAddRequest(BaseModel):
    symbol: str


@router.get("/ticker-universe")
async def get_ticker_universe() -> dict:
    """Return the full quotable ticker universe for the banner ticker.

    Aggregates core index ETFs, sector ETFs, and the balanced stock
    scanner universe (~210 symbols).  Non-quotable index tickers
    (RUT, NDX, XSP, SPX, DJX) are excluded.
    """
    return {"symbols": _TICKER_UNIVERSE, "count": len(_TICKER_UNIVERSE)}


# ── Ticker snapshot cache ────────────────────────────────────────────────
# In-memory cache refreshed at most once every _TICKER_SNAP_TTL seconds.
# Prevents the persistent banner animation from hammering the Tradier API.
_TICKER_SNAP_TTL = 300  # 5 minutes
_TICKER_SNAP_BATCH = 50  # symbols per Tradier request
_ticker_snap: dict = {"quotes": {}, "as_of": None, "ts": 0.0}
_ticker_snap_lock: asyncio.Lock | None = None


def _safe_float(v) -> float | None:
    try:
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return f
    except (TypeError, ValueError):
        return None


async def _refresh_ticker_snap(tradier) -> dict:
    """Fetch quotes for the full universe in batches and compute derived fields.

    For each symbol the snapshot stores:
      last, open, change, change_pct
    If the Tradier-native change/change_percentage fields are null or zero
    but last and prevclose are available, change is derived:
      change      = last - prevclose
      change_pct  = (change / prevclose) * 100
    """
    all_quotes: dict[str, dict] = {}

    for start in range(0, len(_TICKER_UNIVERSE), _TICKER_SNAP_BATCH):
        batch = _TICKER_UNIVERSE[start : start + _TICKER_SNAP_BATCH]
        try:
            quote_map = await tradier.get_quotes(batch)
        except Exception as exc:
            logger.warning("ticker_snap_batch_fail start=%d exc=%s", start, exc)
            continue

        for sym in batch:
            q = quote_map.get(sym)
            if not q or not isinstance(q, dict):
                continue
            last = _safe_float(q.get("last"))
            if last is None:
                continue

            opn = _safe_float(q.get("open"))
            prevclose = _safe_float(q.get("prevclose"))
            change = _safe_float(q.get("change"))
            change_pct = _safe_float(q.get("change_percentage"))

            # Derive change from prevclose when native fields are missing/zero
            if (change is None or change == 0) and prevclose and prevclose != 0:
                derived = last - prevclose
                if derived != 0:
                    change = round(derived, 4)
                    change_pct = round((derived / prevclose) * 100, 4)

            all_quotes[sym] = {
                "last": last,
                "open": opn,
                "change": change,
                "change_pct": change_pct,
            }

    as_of = datetime.now(timezone.utc).isoformat()
    _ticker_snap["quotes"] = all_quotes
    _ticker_snap["as_of"] = as_of
    _ticker_snap["ts"] = time.monotonic()
    logger.info("ticker_snap_refreshed symbols=%d", len(all_quotes))
    return {"quotes": all_quotes, "as_of": as_of}


@router.get("/ticker-snapshot")
async def get_ticker_snapshot(request: Request) -> dict:
    """Cached batch-quote snapshot for the banner ticker.

    Refreshes from Tradier every 5 minutes (batched, ~5 API calls).
    Between refreshes the cached payload is returned instantly.
    The frontend should poll this endpoint instead of /quotes to avoid
    putting continuous pressure on source APIs.
    """
    global _ticker_snap_lock
    if _ticker_snap_lock is None:
        _ticker_snap_lock = asyncio.Lock()

    now = time.monotonic()
    if now - _ticker_snap["ts"] < _TICKER_SNAP_TTL and _ticker_snap["quotes"]:
        return {"quotes": _ticker_snap["quotes"], "as_of": _ticker_snap["as_of"]}

    async with _ticker_snap_lock:
        # Double-check after acquiring lock (another request may have refreshed)
        if time.monotonic() - _ticker_snap["ts"] < _TICKER_SNAP_TTL and _ticker_snap["quotes"]:
            return {"quotes": _ticker_snap["quotes"], "as_of": _ticker_snap["as_of"]}
        try:
            tradier = request.app.state.tradier_client
            return await _refresh_ticker_snap(tradier)
        except Exception as exc:
            logger.warning("ticker_snap_refresh_fail exc=%s", exc)
            return {"quotes": _ticker_snap["quotes"], "as_of": _ticker_snap["as_of"]}


@router.get("/summary")
async def get_stock_summary(
    request: Request,
    symbol: str = Query("SPY", description="Ticker symbol"),
    range: str = Query("6mo", description="1mo|3mo|6mo|1y"),
) -> dict:
    try:
        return await request.app.state.stock_analysis_service.get_summary(symbol=symbol, range_key=range)
    except Exception as exc:
        ticker = str(symbol or "SPY").strip().upper() or "SPY"
        source_health = {}
        try:
            source_health = request.app.state.base_data_service.get_source_health_snapshot()
        except Exception:
            source_health = {}

        return {
            "symbol": ticker,
            "as_of": None,
            "price": {
                "last": None,
                "prev_close": None,
                "change": None,
                "change_pct": None,
                "range_high": None,
                "range_low": None,
            },
            "history": [],
            "indicators": {
                "rsi14": None,
                "sma20": None,
                "sma50": None,
                "ema20": None,
                "realized_vol": None,
            },
            "options_context": {
                "expiration": None,
                "iv": None,
                "expected_move": None,
                "iv_rv": None,
                "dte": None,
                "vix": None,
            },
            "source_health": source_health,
            "notes": [f"Stock summary fallback: {exc}"],
        }


@router.get("/scan")
async def get_stock_scan(
    request: Request,
    universe: str = Query("default", description="Scanner universe key"),
) -> dict:
    """DEPRECATED: Legacy scan endpoint. Use /api/stocks/<strategy> endpoints instead."""
    return await request.app.state.stock_analysis_service.scan_universe(universe=universe)


@router.get("/scanner")
async def get_stock_scanner(request: Request) -> dict:
    """DEPRECATED: Generic scanner endpoint. Use /api/stocks/<strategy> endpoints instead."""
    return await request.app.state.stock_analysis_service.stock_scanner()


@router.get("/watchlist")
async def get_stock_watchlist(request: Request) -> dict:
    return request.app.state.stock_analysis_service.get_watchlist()


@router.post("/watchlist")
async def post_stock_watchlist(payload: StockWatchlistAddRequest, request: Request) -> dict:
    return request.app.state.stock_analysis_service.add_to_watchlist(payload.symbol)


@router.get("/quotes")
async def get_batch_quotes(
    request: Request,
    symbols: str = Query(..., description="Comma-separated ticker symbols (max 60)"),
) -> dict:
    """Lightweight batch quote endpoint for the banner ticker.

    Returns {symbol: {last, open, change, change_pct}} for each valid symbol.
    Backed by the Tradier quotes API with caching.
    """
    raw_syms = [s.strip().upper() for s in str(symbols).split(",") if s.strip()]
    # Validate: only allow 1-6 uppercase letter tickers, cap at 60
    validated = [s for s in raw_syms if _TICKER_RE.match(s)][:60]
    if not validated:
        return {"quotes": {}, "as_of": None}

    try:
        tradier = request.app.state.tradier_client
        quote_map = await tradier.get_quotes(validated)
    except Exception as exc:
        logger.warning("banner_quotes_failed exc=%s", exc)
        return {"quotes": {}, "as_of": None, "error": str(exc)}

    out: dict[str, dict] = {}
    for sym in validated:
        q = quote_map.get(sym)
        if not q or not isinstance(q, dict):
            continue
        last = q.get("last")
        opn = q.get("open")
        change = q.get("change")
        change_pct = q.get("change_percentage")
        if last is None:
            continue

        # Derive change from prevclose when native fields are missing/zero
        prevclose = _safe_float(q.get("prevclose"))
        if (change is None or change == 0) and last is not None and prevclose and prevclose != 0:
            derived = last - prevclose
            if derived != 0:
                change = round(derived, 4)
                change_pct = round((derived / prevclose) * 100, 4)

        out[sym] = {
            "last": last,
            "open": opn,
            "change": change,
            "change_pct": change_pct,
        }

    return {"quotes": out, "as_of": datetime.now(timezone.utc).isoformat()}


@router.get("/macro")
async def get_macro_indicators(request: Request) -> dict:
    mcs = request.app.state.market_context_service
    return await mcs.get_flat_macro()
