from fastapi import APIRouter, Request

from app.models.schemas import HealthResponse

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    tradier_ok = await request.app.state.tradier_client.health()
    finnhub_ok = await request.app.state.finnhub_client.health()
    yahoo_ok = await request.app.state.yahoo_client.health()
    fred_ok = await request.app.state.fred_client.health()

    upstream = {
        "tradier": "ok" if tradier_ok else "down",
        "finnhub": "ok" if finnhub_ok else "down",
        "yahoo": "ok" if yahoo_ok else "down",
        "fred": "ok" if fred_ok else "down",
    }
    return HealthResponse(ok=all(x == "ok" for x in upstream.values()), upstream=upstream)
