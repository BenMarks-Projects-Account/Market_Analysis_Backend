from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.models.trade_contract import TradeContract

router = APIRouter(tags=["reports"])


def _to_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_report_stats_from_trades(trades: list[dict]) -> dict:
    total_candidates = len(trades)
    accepted_trades = len(trades)
    rejected_trades = 0
    acceptance_rate = 1.0 if total_candidates > 0 else 0.0

    scores = [_to_float(t.get("composite_score")) for t in trades]
    scores = [s for s in scores if s is not None]
    probabilities = [_to_float(t.get("p_win_used", t.get("pop_delta_approx"))) for t in trades]
    probabilities = [p for p in probabilities if p is not None]
    ror_values = [_to_float(t.get("return_on_risk")) for t in trades]
    ror_values = [r for r in ror_values if r is not None]

    best_underlying = None
    if trades:
        best_trade = max(trades, key=lambda t: _to_float(t.get("composite_score")) or -1.0)
        best_underlying = str(best_trade.get("underlying") or best_trade.get("underlying_symbol") or "").upper() or None

    def _avg(values: list[float]):
        return (sum(values) / len(values)) if values else None

    return {
        "total_candidates": total_candidates,
        "accepted_trades": accepted_trades,
        "rejected_trades": rejected_trades,
        "acceptance_rate": acceptance_rate,
        "best_trade_score": max(scores) if scores else None,
        "worst_accepted_score": min(scores) if scores else None,
        "avg_trade_score": _avg(scores),
        "avg_probability": _avg(probabilities),
        "avg_return_on_risk": _avg(ror_values),
        "best_underlying": best_underlying,
    }


@router.get("/api/reports")
async def list_reports(request: Request) -> list[str]:
    results_dir: Path = request.app.state.results_dir
    if not results_dir.exists():
        return []
    files = [p.name for p in results_dir.glob("analysis_*.json")]
    files.sort(reverse=True)
    return files


@router.get("/api/reports/{filename}")
async def get_report(filename: str, request: Request):
    if not filename.startswith("analysis_") or not filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path = request.app.state.results_dir / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Report not found")

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="Invalid JSON report") from exc

    if isinstance(data, list):
        return JSONResponse(content={"report_stats": _build_report_stats_from_trades(data), "trades": data})

    if isinstance(data, dict):
        trades = data.get("trades")
        if isinstance(trades, list):
            stats = data.get("report_stats")
            if not isinstance(stats, dict):
                stats = _build_report_stats_from_trades(trades)
            source_health = data.get("source_health") if isinstance(data.get("source_health"), dict) else {}
            diagnostics = data.get("diagnostics") if isinstance(data.get("diagnostics"), dict) else {}
            validation_mode = bool(data.get("validation_mode", False))
            return JSONResponse(
                content={
                    "report_stats": stats,
                    "trades": trades,
                    "source_health": source_health,
                    "diagnostics": diagnostics,
                    "validation_mode": validation_mode,
                }
            )

    raise HTTPException(status_code=500, detail="Unexpected report format")


@router.get("/api/generate")
async def generate_report_stream(request: Request):
    async def _stream():
        queue: asyncio.Queue[tuple[str, dict | None]] = asyncio.Queue()

        async def progress_callback(payload: dict):
            await queue.put(("progress", payload))

        async def run_generation():
            try:
                await queue.put(("progress", {"step": "starting", "message": "Starting report generation..."}))
                summary = await request.app.state.report_service.generate_live_report(
                    "SPY",
                    progress_callback=progress_callback,
                )
                await queue.put(("done", {"filename": summary["filename"]}))
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


@router.post("/api/model/analyze")
async def model_analyze(payload: dict):
    if not payload or "trade" not in payload:
        raise HTTPException(status_code=400, detail='Missing "trade" in request body')

    trade = payload.get("trade")
    source = payload.get("source")
    if not source:
        raise HTTPException(status_code=400, detail='Missing "source" filename in request body')

    try:
        from common.model_analysis import analyze_trade

        contract = TradeContract.from_dict(trade)
        evaluated = analyze_trade(contract, source)
        if evaluated is None:
            raise HTTPException(status_code=500, detail="Model call failed or returned unparsable response")
        return {"ok": True, "evaluated_trade": evaluated}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
