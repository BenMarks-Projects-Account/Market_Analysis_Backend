from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.models.schemas import SpreadAnalyzeRequest, SpreadCandidate
from app.utils.normalize import normalize_trade, strip_legacy_fields
from app.utils.trade_key import canonicalize_strategy_id, canonicalize_trade_key, trade_key

router = APIRouter(prefix="/api/workbench", tags=["workbench"])

_SCENARIO_FILE = "workbench_scenarios.json"
_LOCK = RLock()


class WorkbenchAnalyzeRequest(BaseModel):
    symbol: str
    expiration: str
    strategy: str
    short_strike: float | None = None
    long_strike: float | None = None
    strike: float | None = None
    put_short_strike: float | None = None
    put_long_strike: float | None = None
    call_short_strike: float | None = None
    call_long_strike: float | None = None
    center_strike: float | None = None
    workbench_key_parts: dict[str, Any] | None = None
    contracts_multiplier: int = Field(default=100, alias="contractsMultiplier")

    model_config = {
        "populate_by_name": True,
        "extra": "ignore",
    }


class WorkbenchScenarioCreateRequest(BaseModel):
    name: str
    input: WorkbenchAnalyzeRequest
    notes: str | None = ""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strategy_to_spread(strategy: str) -> tuple[str, str, bool]:
    key = str(strategy or "").strip().lower()
    canonical, alias_mapped, _ = canonicalize_strategy_id(key)
    target = canonical or key
    mapping = {
        "put_credit": "put_credit",
        "call_credit": "call_credit",
        "put_credit_spread": "put_credit",
        "call_credit_spread": "call_credit",
        "put_debit": "put_credit",
        "call_debit": "call_credit",
    }
    spread_strategy = mapping.get(target)
    if not spread_strategy:
        raise HTTPException(status_code=400, detail="strategy is under construction for analysis")
    return spread_strategy, target, alias_mapped


def _emit_alias_event(request: Request, *, strategy_id: str, provided_strategy: str) -> None:
    if strategy_id == provided_strategy:
        return
    try:
        request.app.state.validation_events.append_event(
            severity="warn",
            code="TRADE_STRATEGY_ALIAS_MAPPED",
            message="Workbench strategy alias mapped to canonical strategy_id",
            context={
                "strategy_id": strategy_id,
                "provided_strategy": provided_strategy,
            },
        )
    except Exception:
        return


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_candidate(spread_strategy: str, short_strike: float | None, long_strike: float | None) -> tuple[float, float]:
    short_value = _to_float(short_strike)
    long_value = _to_float(long_strike)
    if short_value is None or long_value is None:
        raise HTTPException(status_code=400, detail="short_strike and long_strike are required for this strategy")

    if spread_strategy == "put_credit":
        return max(short_value, long_value), min(short_value, long_value)
    return min(short_value, long_value), max(short_value, long_value)


def _scenario_path(request: Request) -> Path:
    return Path(request.app.state.results_dir) / _SCENARIO_FILE


def _read_scenarios(path: Path) -> list[dict]:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[]", encoding="utf-8")
        return []

    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        return []
    except Exception:
        return []


def _write_scenarios(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(items, fh, indent=2)


def _input_trade_key(payload: WorkbenchAnalyzeRequest) -> str:
    dte = None
    try:
        exp_date = datetime.strptime(payload.expiration, "%Y-%m-%d").date()
        dte = (exp_date - datetime.now(timezone.utc).date()).days
    except Exception:
        dte = None

    parts = payload.workbench_key_parts if isinstance(payload.workbench_key_parts, dict) else {}
    short_part = parts.get("short_strike", payload.short_strike if payload.short_strike is not None else payload.strike)
    long_part = parts.get("long_strike", payload.long_strike)

    canonical_strategy, _, _ = canonicalize_strategy_id(payload.strategy)
    return trade_key(
        underlying=payload.symbol,
        expiration=payload.expiration,
        spread_type=canonical_strategy,
        short_strike=short_part,
        long_strike=long_part,
        dte=dte,
    )


@router.post("/analyze")
async def analyze_workbench_trade(payload: WorkbenchAnalyzeRequest, request: Request) -> dict:
    spread_strategy, canonical_strategy, alias_mapped = _strategy_to_spread(payload.strategy)
    if alias_mapped:
        _emit_alias_event(request, strategy_id=canonical_strategy, provided_strategy=str(payload.strategy or "").strip().lower())
    normalized_short, normalized_long = _normalize_candidate(
        spread_strategy,
        payload.short_strike,
        payload.long_strike,
    )

    spread_payload = SpreadAnalyzeRequest(
        symbol=payload.symbol,
        expiration=payload.expiration,
        strategy=spread_strategy,
        candidates=[
            SpreadCandidate(
                short_strike=normalized_short,
                long_strike=normalized_long,
            )
        ],
        contractsMultiplier=payload.contracts_multiplier,
    )

    enriched = await request.app.state.spread_service.analyze_spreads(spread_payload)
    if not enriched:
        raise HTTPException(status_code=404, detail="No matching enriched trade found")

    trade = dict(enriched[0])
    trade["strategy"] = canonical_strategy
    trade["spread_type"] = canonical_strategy
    trade["strategy_id"] = canonical_strategy
    trade["expiration"] = payload.expiration
    trade["underlying"] = str(payload.symbol or "").upper()
    trade["underlying_symbol"] = str(payload.symbol or "").upper()
    trade["short_strike"] = payload.short_strike if payload.short_strike is not None else normalized_short
    trade["long_strike"] = payload.long_strike if payload.long_strike is not None else normalized_long
    trade["contractsMultiplier"] = payload.contracts_multiplier
    if payload.strategy.startswith("debit_"):
        trade["analysis_note"] = "debit spread analysis is a first-pass mapping using current spread engine"

    parts = payload.workbench_key_parts if isinstance(payload.workbench_key_parts, dict) else {}
    short_key_part = parts.get("short_strike", trade.get("short_strike"))
    long_key_part = parts.get("long_strike", trade.get("long_strike"))

    trade["trade_key"] = trade_key(
        underlying=trade.get("underlying") or payload.symbol,
        expiration=payload.expiration,
        spread_type=canonical_strategy,
        short_strike=short_key_part,
        long_strike=long_key_part,
        dte=trade.get("dte"),
    )
    trade["trade_key"] = canonicalize_trade_key(trade["trade_key"])
    trade["workbench_key_parts"] = {
        "short_strike": short_key_part,
        "long_strike": long_key_part,
    }
    trade["trade_id"] = trade["trade_key"]

    # Normalize through the canonical pipeline and strip legacy fields.
    trade = normalize_trade(trade)
    trade = strip_legacy_fields(trade)

    return {
        "trade": trade,
        "source_health": request.app.state.base_data_service.get_source_health_snapshot(),
        "as_of": _utc_now_iso(),
    }


@router.get("/scenarios")
async def list_workbench_scenarios(request: Request) -> dict:
    path = _scenario_path(request)
    with _LOCK:
        items = _read_scenarios(path)
    return {"scenarios": items}


@router.post("/scenarios")
async def save_workbench_scenario(payload: WorkbenchScenarioCreateRequest, request: Request) -> dict:
    scenario_name = str(payload.name or "").strip()
    if not scenario_name:
        raise HTTPException(status_code=400, detail="name is required")

    scenario = {
        "id": str(uuid4()),
        "name": scenario_name,
        "created_at": _utc_now_iso(),
        "input": payload.input.model_dump(by_alias=True),
        "trade_key": canonicalize_trade_key(_input_trade_key(payload.input)),
        "notes": str(payload.notes or ""),
    }

    path = _scenario_path(request)
    with _LOCK:
        existing = _read_scenarios(path)
        existing.append(scenario)
        _write_scenarios(path, existing)

    return {"ok": True, "scenario": scenario}


@router.delete("/scenarios/{scenario_id}")
async def delete_workbench_scenario(scenario_id: str, request: Request) -> dict:
    target = str(scenario_id or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="scenario id is required")

    path = _scenario_path(request)
    with _LOCK:
        existing = _read_scenarios(path)
        kept = [item for item in existing if str(item.get("id") or "") != target]
        if len(kept) == len(existing):
            raise HTTPException(status_code=404, detail="scenario not found")
        _write_scenarios(path, kept)

    return {"ok": True, "deleted_id": target}
