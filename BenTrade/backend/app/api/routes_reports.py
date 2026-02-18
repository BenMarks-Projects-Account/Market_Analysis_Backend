from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.models.trade_contract import TradeContract
from app.services.validation_events import emit_validation_event
from app.utils.computed_metrics import apply_metrics_contract
from app.utils.normalize import normalize_trade, strategy_label as _strategy_label
from app.utils.trade_key import canonicalize_trade_key, canonicalize_strategy_id, trade_key

router = APIRouter(tags=["reports"])


class StockModelAnalyzeRequest(BaseModel):
    symbol: str
    idea: dict[str, Any]
    source: str = "local_llm"


def _to_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sanitize_finite(value: Any, *, path: str = "payload", warnings: list[str] | None = None) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        numeric = float(value)
        if math.isfinite(numeric):
            return value
        if warnings is not None:
            warnings.append(path)
        return None
    if isinstance(value, list):
        out: list[Any] = []
        for index, item in enumerate(value):
            out.append(_sanitize_finite(item, path=f"{path}[{index}]", warnings=warnings))
        return out
    if isinstance(value, dict):
        out_dict: dict[str, Any] = {}
        for key, val in value.items():
            key_name = str(key)
            out_dict[key_name] = _sanitize_finite(
                val,
                path=f"{path}.{key_name}",
                warnings=warnings,
            )
        return out_dict
    return value


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


def _normalize_report_trade(row: dict, expiration_hint: str | None = None) -> dict:
    """Normalize a single trade from a persisted report via the shared builder."""
    return normalize_trade(row, expiration=expiration_hint)


def _normalize_report_trades(rows: list[dict], expiration_hint: str | None = None) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(_normalize_report_trade(row, expiration_hint=expiration_hint))
    return out


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
        trades = _normalize_report_trades([row for row in data if isinstance(row, dict)])
        return JSONResponse(content={"report_stats": _build_report_stats_from_trades(trades), "trades": trades})

    if isinstance(data, dict):
        trades = data.get("trades")
        if isinstance(trades, list):
            expiration_hint = str(data.get("expiration") or "").strip() or None
            trades = _normalize_report_trades([row for row in trades if isinstance(row, dict)], expiration_hint=expiration_hint)
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


@router.post("/api/model/analyze_stock")
async def model_analyze_stock(payload: StockModelAnalyzeRequest, request: Request):
    symbol = str(payload.symbol or "").strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail='Missing "symbol" in request body')

    idea = payload.idea if isinstance(payload.idea, dict) else {}
    if not idea:
        raise HTTPException(status_code=400, detail='Missing "idea" snapshot in request body')

    source = str(payload.source or "local_llm").strip() or "local_llm"

    try:
        from common.model_analysis import LocalModelUnavailableError, analyze_stock_idea

        model_output = analyze_stock_idea(
            symbol=symbol,
            idea=idea,
            source=source,
        )
    except LocalModelUnavailableError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Stock model analysis failed: {exc}") from exc

    safe_source = "".join(ch for ch in source.lower() if ch.isalnum() or ch in ("_", "-")) or "local_llm"
    artifact_path: Path = request.app.state.results_dir / f"model_stock_{safe_source}.jsonl"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)

    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "idea_key": str(idea.get("idea_key") or f"{symbol}|stock_scanner"),
        "model_output": model_output,
        "input_snapshot": {
            "symbol": symbol,
            "idea": idea,
            "source": source,
        },
    }
    numeric_warnings: list[str] = []
    record = _sanitize_finite(record, warnings=numeric_warnings)

    for warning_path in numeric_warnings:
        emit_validation_event(
            severity="error",
            code="NUMERIC_NONFINITE",
            message="Non-finite numeric value was sanitized before model artifact persistence",
            context={
                "source": source,
                "symbol": symbol,
                "path": warning_path,
            },
        )

    try:
        with open(artifact_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to persist stock model artifact: {exc}") from exc

    return model_output
