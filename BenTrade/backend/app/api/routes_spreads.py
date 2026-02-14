from fastapi import APIRouter, Request

from app.models.schemas import SpreadAnalyzeRequest

router = APIRouter(prefix="/api/spreads", tags=["spreads"])


@router.post("/analyze")
async def analyze_spreads(payload: SpreadAnalyzeRequest, request: Request) -> list[dict]:
    enriched = await request.app.state.spread_service.analyze_spreads(payload)
    return enriched
