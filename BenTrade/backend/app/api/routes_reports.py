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


def _strategy_label(strategy_id: str) -> str:
    key = str(strategy_id or "").strip().lower()
    labels = {
        "put_credit_spread": "Put Credit Spread",
        "call_credit_spread": "Call Credit Spread",
        "put_debit": "Put Debit Spread",
        "call_debit": "Call Debit Spread",
        "iron_condor": "Iron Condor",
        "butterfly_debit": "Debit Butterfly",
        "calendar_spread": "Calendar Spread",
        "calendar_call_spread": "Call Calendar Spread",
        "calendar_put_spread": "Put Calendar Spread",
        "csp": "Cash Secured Put",
        "covered_call": "Covered Call",
        "income": "Income Strategy",
        "single": "Single Option",
        "long_call": "Long Call",
        "long_put": "Long Put",
    }
    return labels.get(key, key.replace("_", " ").title() or "Trade")


def _normalize_report_trade(row: dict, expiration_hint: str | None = None) -> dict:
    trade = dict(row or {})

    symbol = str(
        trade.get("underlying")
        or trade.get("underlying_symbol")
        or trade.get("symbol")
        or ""
    ).upper()
    if symbol:
        trade["underlying"] = symbol
        trade["underlying_symbol"] = symbol
        trade["symbol"] = symbol

    expiration = str(trade.get("expiration") or expiration_hint or "NA").strip() or "NA"
    trade["expiration"] = expiration

    strategy_raw = trade.get("spread_type") or trade.get("strategy") or trade.get("strategy_id")
    strategy_id, _alias_mapped, _provided = canonicalize_strategy_id(strategy_raw)
    strategy_id = strategy_id or str(strategy_raw or "NA").strip().lower() or "NA"
    trade["spread_type"] = strategy_id
    trade["strategy"] = strategy_id
    trade["strategy_id"] = strategy_id

    incoming_key = str(trade.get("trade_key") or trade.get("_trade_key") or "").strip()
    if incoming_key:
        normalized_key = canonicalize_trade_key(incoming_key)
    else:
        normalized_key = trade_key(
            underlying=symbol,
            expiration=expiration,
            spread_type=strategy_id,
            short_strike=trade.get("short_strike") if trade.get("short_strike") not in (None, "") else trade.get("strike"),
            long_strike=trade.get("long_strike") if trade.get("long_strike") not in (None, "") else "NA",
            dte=trade.get("dte"),
        )
    trade["trade_key"] = normalized_key

    def _first_number(*keys: str) -> float | None:
        for key in keys:
            value = _to_float(trade.get(key))
            if value is not None:
                return value
        return None

    multiplier = _to_float(trade.get("contractsMultiplier") or trade.get("contracts_multiplier")) or 100.0

    expected_value_contract = _first_number("ev_per_contract", "expected_value", "ev")
    if expected_value_contract is None:
        ev_share = _first_number("ev_per_share")
        if ev_share is not None:
            expected_value_contract = ev_share * multiplier

    max_profit_contract = _first_number("max_profit_per_contract")
    if max_profit_contract is None:
        mp_share = _first_number("max_profit_per_share")
        if mp_share is not None:
            max_profit_contract = mp_share * multiplier
        else:
            max_profit_contract = _first_number("max_profit")

    max_loss_contract = _first_number("max_loss_per_contract")
    if max_loss_contract is None:
        ml_share = _first_number("max_loss_per_share")
        if ml_share is not None:
            max_loss_contract = ml_share * multiplier
        else:
            max_loss_contract = _first_number("max_loss")

    computed = {
        "max_profit": max_profit_contract,
        "max_loss": max_loss_contract,
        "pop": _first_number("p_win_used", "pop_delta_approx", "pop_approx", "probability_of_touch_center", "implied_prob_profit", "pop"),
        "return_on_risk": _first_number("return_on_risk", "ror"),
        "expected_value": expected_value_contract,
        "kelly_fraction": _first_number("kelly_fraction"),
        "iv_rank": _first_number("iv_rank"),
        "short_strike_z": _first_number("short_strike_z"),
        "bid_ask_pct": _first_number("bid_ask_spread_pct"),
        "strike_dist_pct": _first_number("strike_distance_pct", "strike_distance_vs_expected_move", "expected_move_ratio"),
        "rsi14": _first_number("rsi14", "rsi_14"),
        "rv_20d": _first_number("realized_vol_20d", "rv_20d"),
        "open_interest": _first_number("open_interest"),
        "volume": _first_number("volume"),
    }
    details = {
        "break_even": _first_number("break_even", "break_even_low"),
        "dte": _first_number("dte"),
        "expected_move": _first_number("expected_move", "expected_move_near"),
        "iv_rv_ratio": _first_number("iv_rv_ratio"),
        "trade_quality_score": _first_number("trade_quality_score"),
        "market_regime": str(trade.get("market_regime") or trade.get("regime") or "").strip() or None,
    }
    dte_front = _first_number("dte_near")
    dte_back = _first_number("dte_far")
    pills = {
        "strategy_label": _strategy_label(strategy_id),
        "dte": details["dte"],
        "pop": computed["pop"],
        "oi": computed["open_interest"],
        "vol": computed["volume"],
        "regime_label": details["market_regime"],
    }
    if dte_front is not None and dte_back is not None:
        pills["dte_front"] = dte_front
        pills["dte_back"] = dte_back
        front_value = int(dte_front) if float(dte_front).is_integer() else dte_front
        back_value = int(dte_back) if float(dte_back).is_integer() else dte_back
        pills["dte_label"] = f"DTE {front_value}/{back_value}"
    trade["computed"] = computed
    trade["details"] = details
    trade["pills"] = pills
    trade = apply_metrics_contract(trade)

    if trade.get("p_win_used") is None and computed["pop"] is not None:
        trade["p_win_used"] = computed["pop"]
    if trade.get("return_on_risk") is None and computed["return_on_risk"] is not None:
        trade["return_on_risk"] = computed["return_on_risk"]
    if trade.get("expected_value") is None and computed["expected_value"] is not None:
        trade["expected_value"] = computed["expected_value"]
    if trade.get("ev_per_contract") is None and computed["expected_value"] is not None:
        trade["ev_per_contract"] = computed["expected_value"]
    if trade.get("bid_ask_spread_pct") is None and computed["bid_ask_pct"] is not None:
        trade["bid_ask_spread_pct"] = computed["bid_ask_pct"]
    if trade.get("strike_distance_pct") is None and computed["strike_dist_pct"] is not None:
        trade["strike_distance_pct"] = computed["strike_dist_pct"]
    if trade.get("rsi14") is None and computed["rsi14"] is not None:
        trade["rsi14"] = computed["rsi14"]
    if trade.get("realized_vol_20d") is None and computed["rv_20d"] is not None:
        trade["realized_vol_20d"] = computed["rv_20d"]
    if trade.get("iv_rv_ratio") is None and details["iv_rv_ratio"] is not None:
        trade["iv_rv_ratio"] = details["iv_rv_ratio"]
    if trade.get("trade_quality_score") is None and details["trade_quality_score"] is not None:
        trade["trade_quality_score"] = details["trade_quality_score"]
    if not str(trade.get("market_regime") or "").strip() and details["market_regime"] is not None:
        trade["market_regime"] = details["market_regime"]

    warnings = trade.get("validation_warnings") if isinstance(trade.get("validation_warnings"), list) else []
    if computed["pop"] is None and "POP_NOT_IMPLEMENTED_FOR_STRATEGY" not in warnings:
        warnings.append("POP_NOT_IMPLEMENTED_FOR_STRATEGY")
    if pills["regime_label"] is None and "REGIME_UNAVAILABLE" not in warnings:
        warnings.append("REGIME_UNAVAILABLE")
    if computed["max_profit"] is None and "MAX_PROFIT_UNAVAILABLE" not in warnings:
        warnings.append("MAX_PROFIT_UNAVAILABLE")
    if computed["max_loss"] is None and "MAX_LOSS_UNAVAILABLE" not in warnings:
        warnings.append("MAX_LOSS_UNAVAILABLE")
    if computed["expected_value"] is None and "EXPECTED_VALUE_UNAVAILABLE" not in warnings:
        warnings.append("EXPECTED_VALUE_UNAVAILABLE")
    if computed["return_on_risk"] is None and "RETURN_ON_RISK_UNAVAILABLE" not in warnings:
        warnings.append("RETURN_ON_RISK_UNAVAILABLE")
    if warnings:
        trade["validation_warnings"] = warnings

    return trade


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
