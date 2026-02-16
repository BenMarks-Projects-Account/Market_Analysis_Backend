from __future__ import annotations

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/decisions", tags=["decisions"])


class RejectDecisionRequest(BaseModel):
    report_file: str
    trade_key: str
    reason: str | None = None


@router.post("/reject")
async def reject_decision(payload: RejectDecisionRequest, request: Request) -> dict:
    report_file = (payload.report_file or "").strip()
    trade_key = (payload.trade_key or "").strip()

    if not report_file:
        raise HTTPException(status_code=400, detail="report_file is required")
    if not trade_key:
        raise HTTPException(status_code=400, detail="trade_key is required")

    decision = request.app.state.decision_service.append_reject(
        report_file=report_file,
        trade_key=trade_key,
        reason=payload.reason,
    )
    return {"ok": True, "decision": decision}


@router.get("/{report_file}")
async def get_decisions(report_file: str, request: Request) -> dict:
    items = request.app.state.decision_service.list_decisions(report_file)
    return {"report_file": report_file, "decisions": items}
