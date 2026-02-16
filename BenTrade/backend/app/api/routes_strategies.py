from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


@router.post("/{strategy_id}/generate")
async def generate_strategy_report(
    strategy_id: str = Path(..., description="Strategy plugin ID"),
    payload: dict[str, Any] | None = None,
    request: Request = None,
) -> dict[str, Any]:
    try:
        generated = await request.app.state.strategy_service.generate(strategy_id=strategy_id, request_payload=payload or {})
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to generate strategy report: {exc}") from exc

    return {
        "ok": True,
        "strategyId": strategy_id,
        "filename": generated.get("filename"),
        "report_stats": generated.get("report_stats") or {},
        "source_health": generated.get("source_health") or {},
        "trades": generated.get("trades") or [],
        "diagnostics": generated.get("diagnostics") or {},
    }


@router.get("/{strategy_id}/generate")
async def generate_strategy_report_stream(strategy_id: str, request: Request):
    query = request.query_params
    request_payload: dict[str, Any] = {}
    for key in ("symbol", "direction", "width", "distance_mode", "butterfly_type", "option_side", "center_mode", "moneyness"):
        value = query.get(key)
        if value not in (None, ""):
            request_payload[key] = value
    for key in ("dte_min", "dte_max", "max_candidates", "near_dte_min", "near_dte_max", "far_dte_min", "far_dte_max", "min_open_interest", "min_volume", "prefer_term_structure"):
        value = query.get(key)
        if value not in (None, ""):
            try:
                request_payload[key] = int(value)
            except ValueError:
                pass
    for key in (
        "max_debit",
        "distance_target",
        "wing_width",
        "wing_width_put",
        "wing_width_call",
        "wing_width_max",
        "min_credit",
        "min_sigma_distance",
        "delta_target",
        "delta_min",
        "delta_max",
        "min_annualized_yield",
        "min_buffer",
        "width",
        "width_min",
        "width_max",
        "expected_move_multiple",
        "min_pop",
        "min_ev_to_risk",
        "max_bid_ask_spread_pct",
        "max_debit_pct_width",
        "max_iv_rv_ratio_for_buying",
        "min_cost_efficiency",
        "min_ror",
        "symmetry_target",
    ):
        value = query.get(key)
        if value not in (None, ""):
            try:
                request_payload[key] = float(value)
            except ValueError:
                pass
    allow_skewed = query.get("allow_skewed")
    if allow_skewed not in (None, ""):
        request_payload["allow_skewed"] = str(allow_skewed)

    async def _stream():
        queue: asyncio.Queue[tuple[str, dict | None]] = asyncio.Queue()

        async def run_generation():
            try:
                await queue.put(("progress", {"step": "starting", "message": f"Starting {strategy_id} generation..."}))
                generated = await request.app.state.strategy_service.generate(strategy_id=strategy_id, request_payload=request_payload)
                await queue.put(("done", {"filename": generated.get("filename")}))
            except KeyError as exc:
                await queue.put(("error", {"message": str(exc)}))
            except Exception as exc:
                await queue.put(("error", {"message": str(exc)}))
            finally:
                await queue.put(("__end__", None))

        task = asyncio.create_task(run_generation())
        try:
            while True:
                event, payload = await queue.get()
                if event == "__end__":
                    break
                yield f"event: {event}\ndata: {json.dumps(payload or {})}\n\n"
        finally:
            await task

    return StreamingResponse(_stream(), media_type="text/event-stream")


@router.get("/{strategy_id}/reports")
async def list_strategy_reports(strategy_id: str, request: Request) -> list[str]:
    try:
        _ = request.app.state.strategy_service.get_plugin(strategy_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return request.app.state.strategy_service.list_reports(strategy_id)


@router.get("/{strategy_id}/reports/{filename}")
async def get_strategy_report(strategy_id: str, filename: str, request: Request) -> dict[str, Any]:
    try:
        payload = request.app.state.strategy_service.get_report(strategy_id, filename)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Report not found: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read strategy report: {exc}") from exc

    return payload


@router.get("")
async def list_strategies(request: Request) -> dict[str, Any]:
    ids = request.app.state.strategy_service.list_strategy_ids()
    return {"strategies": ids}
