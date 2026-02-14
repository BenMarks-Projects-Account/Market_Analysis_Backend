from fastapi import APIRouter, Request

from app.models.schemas import UnderlyingSnapshotResponse

router = APIRouter(prefix="/api/underlying", tags=["underlying"])


@router.get("/{symbol}/snapshot", response_model=UnderlyingSnapshotResponse)
async def get_snapshot(symbol: str, request: Request) -> UnderlyingSnapshotResponse:
    snapshot = await request.app.state.base_data_service.get_snapshot(symbol)
    return UnderlyingSnapshotResponse(**snapshot)
