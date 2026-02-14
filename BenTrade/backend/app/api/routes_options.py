from fastapi import APIRouter, Query, Request

from app.models.schemas import ExpirationsResponse, OptionChainResponse

router = APIRouter(prefix="/api/options", tags=["options"])


@router.get("/{symbol}/expirations", response_model=ExpirationsResponse)
async def get_expirations(symbol: str, request: Request) -> ExpirationsResponse:
    expirations = await request.app.state.tradier_client.get_expirations(symbol)
    return ExpirationsResponse(symbol=symbol.upper(), expirations=expirations)


@router.get("/{symbol}/chain", response_model=OptionChainResponse)
async def get_chain(
    symbol: str,
    request: Request,
    expiration: str = Query(..., description="YYYY-MM-DD"),
    greeks: bool = Query(default=True),
) -> OptionChainResponse:
    raw_contracts = await request.app.state.tradier_client.get_chain(symbol, expiration, greeks=greeks)
    contracts = request.app.state.base_data_service.normalize_chain(raw_contracts)
    return OptionChainResponse(symbol=symbol.upper(), expiration=expiration, contracts=contracts)
