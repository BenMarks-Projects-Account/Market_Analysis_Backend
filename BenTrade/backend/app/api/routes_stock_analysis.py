from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from app.utils.http import request_json

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


@router.get("/scanner")
async def get_stock_scanner(request: Request) -> dict:
    return await request.app.state.stock_analysis_service.stock_scanner()


@router.get("/watchlist")
async def get_stock_watchlist(request: Request) -> dict:
    return request.app.state.stock_analysis_service.get_watchlist()


@router.post("/watchlist")
async def post_stock_watchlist(payload: StockWatchlistAddRequest, request: Request) -> dict:
    return request.app.state.stock_analysis_service.add_to_watchlist(payload.symbol)


@router.get("/macro")
async def get_macro_indicators(request: Request) -> dict:
    fred = request.app.state.fred_client

    ten_year = None
    fed_funds = None
    cpi_yoy = None
    vix = None
    notes: list[str] = []

    try:
        ten_year = await fred.get_latest_series_value("DGS10")
    except Exception as exc:
        notes.append(f"10Y unavailable: {exc}")

    try:
        fed_funds = await fred.get_latest_series_value("DFF")
    except Exception as exc:
        notes.append(f"Fed funds unavailable: {exc}")

    try:
        vix = await fred.get_latest_series_value(fred.settings.FRED_VIX_SERIES_ID)
    except Exception as exc:
        notes.append(f"VIX unavailable: {exc}")

    try:
        payload = await request_json(
            fred.http_client,
            "GET",
            f"{fred.settings.FRED_BASE_URL}/series/observations",
            params={
                "series_id": "CPIAUCSL",
                "sort_order": "desc",
                "limit": 13,
                "api_key": fred.settings.FRED_KEY,
                "file_type": "json",
            },
        )
        observations = payload.get("observations") or []
        values: list[float] = []
        for row in observations:
            value = row.get("value")
            if value in (None, "."):
                continue
            try:
                values.append(float(value))
            except (TypeError, ValueError):
                continue
        if len(values) >= 13 and values[12] != 0:
            cpi_yoy = (values[0] / values[12]) - 1.0
    except Exception as exc:
        notes.append(f"CPI YoY unavailable: {exc}")

    return {
        "ten_year_yield": ten_year,
        "fed_funds_rate": fed_funds,
        "cpi_yoy": cpi_yoy,
        "vix": vix,
        "notes": notes,
    }
