from fastapi import APIRouter, Query, Request

router = APIRouter(prefix="/api/signals", tags=["signals"])


@router.get("")
async def get_signals(
    request: Request,
    symbol: str = Query("SPY", description="Ticker symbol"),
    range: str = Query("6mo", description="1mo|3mo|6mo|1y"),
) -> dict:
    return await request.app.state.signal_service.get_symbol_signals(symbol=symbol, range_key=range)


@router.get("/universe")
async def get_universe_signals(
    request: Request,
    universe: str = Query("default", description="default|watchlist"),
    range: str = Query("6mo", description="1mo|3mo|6mo|1y"),
) -> dict:
    return await request.app.state.signal_service.get_universe_signals(universe=universe, range_key=range)
