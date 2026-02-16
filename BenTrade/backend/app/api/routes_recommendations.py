from fastapi import APIRouter, Request
import logging
import traceback
from uuid import uuid4

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])
logger = logging.getLogger(__name__)


@router.get("/top")
async def get_top_recommendations(request: Request) -> dict:
    try:
        payload = await request.app.state.recommendation_service.get_top_recommendations(limit=3)
        if isinstance(payload, dict):
            payload.setdefault("picks", [])
            payload.setdefault("notes", [])
            return payload
        return {
            "picks": [],
            "notes": ["recommendation payload malformed; returned fallback"],
            "error": {"message": "malformed recommendations payload", "type": "PayloadError"},
        }
    except Exception as exc:
        stack_id = str(uuid4())
        logger.error("recommendations.top.failed stack_id=%s err=%s", stack_id, exc)
        logger.debug("recommendations.top.traceback stack_id=%s trace=%s", stack_id, traceback.format_exc())
        return {
            "picks": [],
            "notes": [f"recommendations endpoint fallback: stack_id={stack_id}"],
            "error": {
                "message": str(exc),
                "type": type(exc).__name__,
                "stack_id": stack_id,
            },
        }
