from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.models.trade_contract import TradeContract
from app.services.validation_events import emit_validation_event
from app.utils.report_conformance import validate_report_file
from app.utils.computed_metrics import apply_metrics_contract
from app.utils.normalize import normalize_trade, strategy_label as _strategy_label, strip_legacy_fields
from app.utils.trade_key import canonicalize_trade_key, canonicalize_strategy_id, trade_key

logger = logging.getLogger(__name__)

# ── Debug trade logging (set BENTRADE_DEBUG_TRADES=1 to enable) ──────
_DEBUG_TRADES = os.environ.get("BENTRADE_DEBUG_TRADES", "").strip().lower() in ("1", "true", "yes", "on")

# Must-have metric keys to audit at each pipeline stage
_AUDIT_KEYS = (
    "max_profit", "max_loss", "pop", "expected_value", "ev", "return_on_risk",
    "kelly_fraction", "iv_rank", "iv_rv_ratio", "rank_score", "break_even",
    "ev_per_contract", "ev_per_share", "p_win_used",
)


def _audit_trade(trade: dict, label: str, idx: int = 0) -> None:
    """Log the presence and value of must-have fields across all sub-dicts."""
    if not _DEBUG_TRADES:
        return
    symbol = trade.get("symbol") or trade.get("underlying") or "?"
    strategy = trade.get("strategy_id") or trade.get("spread_type") or "?"
    exp = trade.get("expiration") or "?"
    root_vals = {k: trade.get(k) for k in _AUDIT_KEYS if trade.get(k) is not None}
    computed = {k: v for k, v in (trade.get("computed") or {}).items() if v is not None}
    computed_metrics = {k: v for k, v in (trade.get("computed_metrics") or {}).items() if v is not None}
    details = {k: v for k, v in (trade.get("details") or {}).items() if v is not None}
    missing_root = [k for k in _AUDIT_KEYS if trade.get(k) is None]
    logger.info(
        "[DEBUG_TRADES:%s] trade[%d] %s %s exp=%s\n"
        "  root:            %s\n"
        "  computed:        %s\n"
        "  computed_metrics:%s\n"
        "  details:         %s\n"
        "  missing@root:    %s",
        label, idx, symbol, strategy, exp,
        root_vals, computed, computed_metrics, details, missing_root,
    )

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
    probabilities = [_to_float((t.get("computed") or {}).get("pop") or t.get("p_win_used") or t.get("pop_delta_approx")) for t in trades]
    probabilities = [p for p in probabilities if p is not None]
    ror_values = [_to_float((t.get("computed") or {}).get("return_on_risk") or t.get("return_on_risk")) for t in trades]
    ror_values = [r for r in ror_values if r is not None]

    best_underlying = None
    if trades:
        best_trade = max(trades, key=lambda t: _to_float(t.get("composite_score")) or -1.0)
        best_underlying = str(best_trade.get("symbol") or best_trade.get("underlying") or best_trade.get("underlying_symbol") or "").upper() or None

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
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        _audit_trade(row, "RAW_JSON", i)
        normalized = _normalize_report_trade(row, expiration_hint=expiration_hint)
        _audit_trade(normalized, "POST_NORMALIZE", i)
        out.append(normalized)
    return out


@router.get("/api/reports")
async def list_reports(request: Request) -> list[str]:
    results_dir: Path = request.app.state.results_dir
    if not results_dir.exists():
        return []
    files = [p.name for p in results_dir.glob("analysis_*.json")]
    files.sort(reverse=True)
    return files


def _strip_and_audit(trades: list[dict]) -> list[dict]:
    """Strip legacy fields and audit the result when debug is enabled."""
    out = []
    for i, t in enumerate(trades):
        stripped = strip_legacy_fields(t)
        _audit_trade(stripped, "POST_STRIP", i)
        out.append(stripped)
    return out


@router.get("/api/reports/{filename}")
async def get_report(filename: str, request: Request):
    if not filename.startswith("analysis_") or not filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path = request.app.state.results_dir / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Report not found")

    ve = getattr(request.app.state, "validation_events", None)
    data = validate_report_file(file_path, validation_events=ve, auto_delete=True)
    if data is None:
        raise HTTPException(status_code=404, detail="Report removed: non-conforming")

    if isinstance(data, list):
        trades = _normalize_report_trades([row for row in data if isinstance(row, dict)])
        return JSONResponse(content={"report_stats": _build_report_stats_from_trades(trades), "trades": _strip_and_audit(trades)})

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
                    "trades": _strip_and_audit(trades),
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
    import logging as _logging

    _log = _logging.getLogger("bentrade.model_trace")
    if not payload or "trade" not in payload:
        raise HTTPException(status_code=400, detail='Missing "trade" in request body')

    trade = payload.get("trade")
    source = payload.get("source")
    _log.info("[MODEL_TRACE] /api/model/analyze hit — source=%s symbol=%s strategy=%s",
              source, trade.get("symbol") if isinstance(trade, dict) else None,
              trade.get("strategy_id") if isinstance(trade, dict) else None)
    if not source:
        raise HTTPException(status_code=400, detail='Missing "source" filename in request body')

    try:
        from common.model_analysis import analyze_trade

        contract = TradeContract.from_dict(trade)
        evaluated = analyze_trade(contract, source)
        if evaluated is None:
            _log.warning("[MODEL_TRACE] analyze_trade returned None for source=%s", source)
            raise HTTPException(status_code=500, detail="Model call failed or returned unparsable response")
        rec = (evaluated.get("model_evaluation") or {}).get("recommendation") if isinstance(evaluated, dict) else None
        _log.info("[MODEL_TRACE] /api/model/analyze OK — source=%s recommendation=%s", source, rec)
        return {"ok": True, "evaluated_trade": evaluated}
    except HTTPException:
        raise
    except Exception as exc:
        _log.exception("[MODEL_TRACE] /api/model/analyze error — source=%s", source)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/model/analyze_regime")
async def model_analyze_regime(payload: dict, request: Request):
    """On-demand LLM analysis of the current Market Regime + Suggested Playbook."""
    regime_data = payload.get("regime")
    if not regime_data or not isinstance(regime_data, dict):
        raise HTTPException(status_code=400, detail='Missing or invalid "regime" in request body')

    playbook_data = payload.get("playbook")  # optional enriched playbook

    try:
        from common.model_analysis import LocalModelUnavailableError, analyze_regime

        model_output = analyze_regime(
            regime_data=regime_data,
            playbook_data=playbook_data if isinstance(playbook_data, dict) else None,
        )
    except LocalModelUnavailableError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Regime model analysis failed: {exc}") from exc

    return {"ok": True, "analysis": model_output}


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
