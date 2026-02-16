from fastapi import APIRouter, Request

router = APIRouter(prefix="/api", tags=["regime"])


@router.get("/regime")
async def get_regime(request: Request) -> dict:
    return await request.app.state.regime_service.get_regime()
