from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/stock", tags=["stock-analysis"])


class StockWatchlistAddRequest(BaseModel):
    symbol: str


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
    return await request.app.state.stock_analysis_service.scan_universe(universe=universe)


@router.get("/watchlist")
async def get_stock_watchlist(request: Request) -> dict:
    return request.app.state.stock_analysis_service.get_watchlist()


@router.post("/watchlist")
async def post_stock_watchlist(payload: StockWatchlistAddRequest, request: Request) -> dict:
    return request.app.state.stock_analysis_service.add_to_watchlist(payload.symbol)
