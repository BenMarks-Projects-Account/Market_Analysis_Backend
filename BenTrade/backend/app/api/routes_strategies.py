from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.responses import StreamingResponse

from app.utils.normalize import strip_legacy_fields

router = APIRouter(prefix="/api/strategies", tags=["strategies"])
logger = logging.getLogger(__name__)

# ── Debug trade logging (set BENTRADE_DEBUG_TRADES=1 to enable) ──────
_DEBUG_TRADES = os.environ.get("BENTRADE_DEBUG_TRADES", "").strip().lower() in ("1", "true", "yes", "on")

_AUDIT_KEYS = (
    "max_profit", "max_loss", "pop", "expected_value", "ev", "return_on_risk",
    "kelly_fraction", "iv_rank", "iv_rv_ratio", "rank_score", "break_even",
)


def _audit_trade(trade: dict, label: str, idx: int = 0) -> None:
    if not _DEBUG_TRADES:
        return
    symbol = trade.get("symbol") or trade.get("underlying") or "?"
    strategy = trade.get("strategy_id") or trade.get("spread_type") or "?"
    computed = {k: v for k, v in (trade.get("computed") or {}).items() if v is not None}
    cm = {k: v for k, v in (trade.get("computed_metrics") or {}).items() if v is not None}
    logger.info(
        "[DEBUG_TRADES:%s] trade[%d] %s %s  computed=%s  computed_metrics=%s",
        label, idx, symbol, strategy, computed, cm,
    )


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

    raw_trades = generated.get("trades") or []
    stripped = []
    for i, t in enumerate(raw_trades):
        _audit_trade(t, "GENERATE_PRE_STRIP", i)
        s = strip_legacy_fields(t)
        _audit_trade(s, "GENERATE_POST_STRIP", i)
        stripped.append(s)
    return {
        "ok": True,
        "strategyId": strategy_id,
        "filename": generated.get("filename"),
        "report_status": generated.get("report_status") or ("ok" if raw_trades else "empty"),
        "report_warnings": generated.get("report_warnings") or [],
        "symbols": generated.get("symbols") or [],
        "report_stats": generated.get("report_stats") or {},
        "source_health": generated.get("source_health") or {},
        "trades": stripped,
        "diagnostics": generated.get("diagnostics") or {},
    }


@router.get("/{strategy_id}/generate")
async def generate_strategy_report_stream(strategy_id: str, request: Request):
    query = request.query_params
    request_payload: dict[str, Any] = {}
    for key in ("symbol", "direction", "width", "distance_mode", "butterfly_type", "option_side", "center_mode", "moneyness", "preset"):
        value = query.get(key)
        if value not in (None, ""):
            request_payload[key] = value
    # Comma-separated symbols list (e.g. symbols=SPY,QQQ,IWM)
    symbols_raw = query.get("symbols")
    if symbols_raw not in (None, ""):
        request_payload["symbols"] = [s.strip().upper() for s in symbols_raw.split(",") if s.strip()]
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
        "distance_min",
        "distance_max",
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

    timeout_seconds = 180
    timeout_q = query.get("timeout_seconds")
    if timeout_q not in (None, ""):
        try:
            timeout_seconds = max(30, min(int(timeout_q), 900))
        except ValueError:
            timeout_seconds = 180

    async def _stream():
        queue: asyncio.Queue[tuple[str, dict | None]] = asyncio.Queue()
        done_event = asyncio.Event()
        current_stage = "starting"

        def _hint_for_error(stage: str, exc: Exception) -> str:
            text = str(exc or "").lower()
            if "unknown strategy" in text:
                return "Verify strategyId matches a registered plugin.id"
            if "no analysis snapshots" in text or "no expirations" in text:
                return "No viable expirations/chains were available for current filters"
            if "timed out" in text or "timeout" in text:
                return "Reduce universe or loosen filters, then retry"
            if "chain" in text:
                return "Options chain data missing; try another symbol/expiration window"
            if stage in {"build_candidates", "enrich", "evaluate"}:
                return "Strategy plugin data assumptions failed; check diagnostics notes"
            return "See server logs with trace_id for full stack details"

        async def progress_callback(payload: dict[str, Any]):
            nonlocal current_stage
            stage = str((payload or {}).get("stage") or "progress")
            current_stage = stage
            await queue.put(("status", payload or {"stage": stage, "message": "Working"}))

        async def run_generation():
            trace_id = str(uuid4())
            try:
                await queue.put(("status", {"stage": "starting", "message": f"Starting {strategy_id} generation..."}))
                generated = await asyncio.wait_for(
                    request.app.state.strategy_service.generate(
                        strategy_id=strategy_id,
                        request_payload=request_payload,
                        progress_callback=progress_callback,
                    ),
                    timeout=timeout_seconds,
                )
                payload = {
                    "strategyId": strategy_id,
                    "filename": generated.get("filename"),
                    "message": "Report generation completed",
                }
                await queue.put(("completed", payload))
                await queue.put(("done", payload))
            except KeyError as exc:
                logger.exception("strategy_sse_unknown_strategy strategy_id=%s trace_id=%s", strategy_id, trace_id)
                await queue.put(("error", {
                    "strategyId": strategy_id,
                    "stage": current_stage,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "hint": _hint_for_error(current_stage, exc),
                    "trace_id": trace_id,
                    "message": str(exc),
                }))
            except asyncio.TimeoutError:
                message = f"Generation timed out after {timeout_seconds}s"
                logger.error("strategy_sse_timeout strategy_id=%s timeout_seconds=%s trace_id=%s", strategy_id, timeout_seconds, trace_id)
                await queue.put(("error", {
                    "strategyId": strategy_id,
                    "stage": current_stage,
                    "error_type": "TimeoutError",
                    "error_message": message,
                    "hint": _hint_for_error(current_stage, asyncio.TimeoutError(message)),
                    "trace_id": trace_id,
                    "message": message,
                }))
            except Exception as exc:
                logger.exception("strategy_sse_failed strategy_id=%s stage=%s trace_id=%s", strategy_id, current_stage, trace_id)
                await queue.put(("error", {
                    "strategyId": strategy_id,
                    "stage": current_stage,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "hint": _hint_for_error(current_stage, exc),
                    "trace_id": trace_id,
                    "message": str(exc),
                }))
            finally:
                done_event.set()
                await queue.put(("__end__", None))

        task = asyncio.create_task(run_generation())
        try:
            elapsed = 0
            while True:
                try:
                    event, payload = await asyncio.wait_for(queue.get(), timeout=5)
                except asyncio.TimeoutError:
                    if done_event.is_set():
                        break
                    elapsed += 5
                    heartbeat = {
                        "step": "progress",
                        "stage": current_stage,
                        "strategyId": strategy_id,
                        "message": f"Working... {elapsed}s elapsed",
                    }
                    yield f"event: status\ndata: {json.dumps(heartbeat)}\n\n"
                    continue
                if event == "__end__":
                    break
                yield f"event: {event}\ndata: {json.dumps(payload or {})}\n\n"
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except Exception:
                    pass
            else:
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

    # Strip legacy flat fields from trades before returning.
    if isinstance(payload, dict) and isinstance(payload.get("trades"), list):
        stripped = []
        for i, t in enumerate(payload["trades"]):
            _audit_trade(t, "REPORT_PRE_STRIP", i)
            s = strip_legacy_fields(t)
            _audit_trade(s, "REPORT_POST_STRIP", i)
            stripped.append(s)
        payload["trades"] = stripped
    return payload


@router.get("")
async def list_strategies(request: Request) -> dict[str, Any]:
    ids = request.app.state.strategy_service.list_strategy_ids()
    return {"strategies": ids}
