"""TMC workflow API routes — Prompt 8.

Thin app-layer integration seam that exposes Trade Management Center
workflow capabilities through clean API endpoints.

All route handlers delegate to ``TMCExecutionService`` and the compact
read-model loaders in ``tmc_service``.  No raw file walking, no stage
artifact parsing, no archived pipeline patterns.

Endpoints
---------
POST /api/tmc/workflows/stock/run         — trigger stock workflow run
POST /api/tmc/workflows/options/run       — trigger options workflow run
GET  /api/tmc/workflows/stock/latest      — latest stock opportunities
GET  /api/tmc/workflows/options/latest    — latest options opportunities
GET  /api/tmc/workflows/stock/summary     — latest stock run summary
GET  /api/tmc/workflows/options/summary   — latest options run summary

Greenfield design — does NOT reference archived pipeline code.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.workflows.tmc_service import (
    TMCExecutionService,
    TMCStatus,
    load_latest_options_output,
    load_latest_stock_output,
    load_latest_run_summary,
)
from app.services.notification_service import get_notification_service

logger = logging.getLogger("bentrade.routes_tmc")

router = APIRouter(prefix="/api/tmc/workflows", tags=["tmc"])


# ═══════════════════════════════════════════════════════════════════════
# RESPONSE MODELS
# ═══════════════════════════════════════════════════════════════════════


class TMCTriggerResponse(BaseModel):
    """Response from triggering a workflow run."""

    workflow_id: str
    run_id: str
    status: str = Field(description="TMCStatus value")
    started_at: str
    completed_at: str
    candidate_count: int = 0
    warnings_count: int = 0
    market_state_ref: str | None = None
    error: str | None = None


class TMCStockOpportunitiesResponse(BaseModel):
    """Response for latest stock opportunities."""

    status: str = Field(description="TMCStatus value")
    data: dict[str, Any] | None = Field(
        default=None,
        description="StockOpportunityReadModel as dict, or null if no output",
    )


class TMCOptionsOpportunitiesResponse(BaseModel):
    """Response for latest options opportunities."""

    status: str = Field(description="TMCStatus value")
    data: dict[str, Any] | None = Field(
        default=None,
        description="OptionsOpportunityReadModel as dict, or null if no output",
    )


class TMCRunSummaryResponse(BaseModel):
    """Response for latest workflow run summary."""

    status: str = Field(description="TMCStatus value")
    data: dict[str, Any] | None = Field(
        default=None,
        description="WorkflowRunSummaryReadModel as dict, or null if no output",
    )


# ═══════════════════════════════════════════════════════════════════════
# REQUEST MODELS
# ═══════════════════════════════════════════════════════════════════════


class TMCStockTriggerRequest(BaseModel):
    """Optional parameters for triggering a stock workflow run."""

    top_n: int | None = Field(default=None, ge=1, le=100)


class TMCOptionsTriggerRequest(BaseModel):
    """Optional parameters for triggering an options workflow run."""

    top_n: int | None = Field(default=None, ge=1, le=100)
    symbols: list[str] | None = Field(default=None, min_length=1)


# ═══════════════════════════════════════════════════════════════════════
# HELPER — build TMCExecutionService from app state
# ═══════════════════════════════════════════════════════════════════════


def _get_data_dir(request: Request) -> str:
    """Derive the data directory from app state."""
    backend_dir = request.app.state.backend_dir
    return str(backend_dir / "data")


def _build_tmc_service(request: Request) -> TMCExecutionService:
    """Build a TMCExecutionService from app state.

    The service is lightweight (no persistent state), so constructing
    per-request is fine.  Dependencies are pulled from app.state if
    they exist (they may not be wired yet in early prompts).
    """
    data_dir = _get_data_dir(request)

    stock_deps = getattr(request.app.state, "tmc_stock_deps", None)
    options_deps = getattr(request.app.state, "tmc_options_deps", None)

    return TMCExecutionService(
        data_dir=data_dir,
        stock_deps=stock_deps,
        options_deps=options_deps,
    )


# ═══════════════════════════════════════════════════════════════════════
# TRIGGER ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════


@router.post("/stock/run", response_model=TMCTriggerResponse)
async def trigger_stock_workflow(
    request: Request,
    body: TMCStockTriggerRequest | None = None,
) -> TMCTriggerResponse:
    """Trigger a Stock Opportunity workflow run.

    Returns a compact execution result — not the full runner output.
    Uses asyncio.shield() so the workflow continues even if the HTTP
    connection drops (e.g. browser timeout on long model calls).
    """
    tmc = _build_tmc_service(request)
    kwargs: dict[str, Any] = {}
    if body and body.top_n is not None:
        kwargs["top_n"] = body.top_n

    # Shield the workflow from cancellation due to HTTP client disconnect.
    # Without this, CancelledError (BaseException) kills the pipeline
    # before stage 8 writes output.json / latest.json.
    task = asyncio.ensure_future(tmc.run_stock_opportunities(**kwargs))
    try:
        result = await asyncio.shield(task)
    except asyncio.CancelledError:
        logger.warning(
            "Client disconnected during stock workflow — run continues in background"
        )
        raise
    logger.info(
        "[TMC] Stock workflow trigger complete: run_id=%s status=%s candidates=%d",
        result.run_id, result.status, result.candidate_count,
    )

    # Check for BUY/EXECUTE signals (load full output from disk)
    try:
        data_dir = _get_data_dir(request)
        stock_full = load_latest_stock_output(data_dir)
        if stock_full:
            get_notification_service().check_stock_results(stock_full.to_dict())
    except Exception as exc:
        logger.warning("[TMC] Stock notification check failed: %s", exc)

    return TMCTriggerResponse(**result.to_dict())


@router.post("/options/run", response_model=TMCTriggerResponse)
async def trigger_options_workflow(
    request: Request,
    body: TMCOptionsTriggerRequest | None = None,
) -> TMCTriggerResponse:
    """Trigger an Options Opportunity workflow run.

    Uses asyncio.shield() so the workflow continues even if the HTTP
    connection drops.
    """
    tmc = _build_tmc_service(request)
    kwargs: dict[str, Any] = {}
    if body:
        if body.top_n is not None:
            kwargs["top_n"] = body.top_n
        if body.symbols is not None:
            kwargs["symbols"] = body.symbols

    task = asyncio.ensure_future(tmc.run_options_opportunities(**kwargs))
    try:
        result = await asyncio.shield(task)
    except asyncio.CancelledError:
        logger.warning(
            "Client disconnected during options workflow — run continues in background"
        )
        raise
    logger.info(
        "[TMC] Options workflow trigger complete: run_id=%s status=%s candidates=%d",
        result.run_id, result.status, result.candidate_count,
    )

    # Check for BUY/EXECUTE signals (load full output from disk)
    try:
        data_dir = _get_data_dir(request)
        options_full = load_latest_options_output(data_dir)
        if options_full:
            get_notification_service().check_options_results(options_full.to_dict())
    except Exception as exc:
        logger.warning("[TMC] Options notification check failed: %s", exc)

    return TMCTriggerResponse(**result.to_dict())


# ═══════════════════════════════════════════════════════════════════════
# LATEST OUTPUT ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════


_NO_CACHE_HEADERS = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}


@router.get("/stock/latest", response_model=TMCStockOpportunitiesResponse)
async def get_latest_stock_opportunities(
    request: Request,
) -> JSONResponse:
    """Return the latest stock opportunity compact read model.

    Returns ``status: "no_output"`` with null data if no output exists.
    Cache-Control: no-store ensures the browser always hits the backend.
    """
    data_dir = _get_data_dir(request)
    model = load_latest_stock_output(data_dir)

    if model is None:
        logger.debug("[TMC] stock/latest -> no_output (no pointer or output.json)")
        body = TMCStockOpportunitiesResponse(
            status=TMCStatus.NO_OUTPUT,
            data=None,
        )
    else:
        logger.info(
            "[TMC] stock/latest -> run_id=%s batch_status=%s candidates=%d generated_at=%s",
            model.run_id, model.batch_status,
            len(model.candidates), model.generated_at,
        )
        body = TMCStockOpportunitiesResponse(
            status=model.status,
            data=model.to_dict(),
        )

    return JSONResponse(
        content=body.model_dump(),
        headers=_NO_CACHE_HEADERS,
    )


@router.get("/options/latest", response_model=TMCOptionsOpportunitiesResponse)
async def get_latest_options_opportunities(
    request: Request,
) -> JSONResponse:
    """Return the latest options opportunity compact read model.

    Returns ``status: "no_output"`` with null data if no output exists.
    Cache-Control: no-store ensures the browser always hits the backend.
    """
    data_dir = _get_data_dir(request)
    model = load_latest_options_output(data_dir)

    if model is None:
        logger.debug("[TMC] options/latest -> no_output")
        body = TMCOptionsOpportunitiesResponse(
            status=TMCStatus.NO_OUTPUT,
            data=None,
        )
    else:
        logger.info(
            "[TMC] options/latest -> run_id=%s batch_status=%s candidates=%d",
            model.run_id, model.batch_status, len(model.candidates),
        )
        body = TMCOptionsOpportunitiesResponse(
            status=model.status,
            data=model.to_dict(),
        )

    return JSONResponse(
        content=body.model_dump(),
        headers=_NO_CACHE_HEADERS,
    )


# ═══════════════════════════════════════════════════════════════════════
# SUMMARY ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════


@router.get("/stock/summary", response_model=TMCRunSummaryResponse)
async def get_stock_run_summary(
    request: Request,
) -> TMCRunSummaryResponse:
    """Return the latest stock workflow run summary.

    Returns ``status: "no_output"`` with null data if no summary exists.
    """
    data_dir = _get_data_dir(request)
    model = load_latest_run_summary(data_dir, "stock_opportunity")

    if model is None:
        return TMCRunSummaryResponse(
            status=TMCStatus.NO_OUTPUT,
            data=None,
        )

    return TMCRunSummaryResponse(
        status=model.status,
        data=model.to_dict(),
    )


@router.get("/options/summary", response_model=TMCRunSummaryResponse)
async def get_options_run_summary(
    request: Request,
) -> TMCRunSummaryResponse:
    """Return the latest options workflow run summary.

    Returns ``status: "no_output"`` with null data if no summary exists.
    """
    data_dir = _get_data_dir(request)
    model = load_latest_run_summary(data_dir, "options_opportunity")

    if model is None:
        return TMCRunSummaryResponse(
            status=TMCStatus.NO_OUTPUT,
            data=None,
        )

    return TMCRunSummaryResponse(
        status=model.status,
        data=model.to_dict(),
    )


# ═══════════════════════════════════════════════════════════════════════
# TMC FINAL TRADE DECISION (model analysis)
# ═══════════════════════════════════════════════════════════════════════


class TMCFinalDecisionRequest(BaseModel):
    """Request for TMC final trade decision analysis."""

    candidate: dict[str, Any] = Field(description="Compact or full candidate dict")
    strategy_id: str | None = Field(default=None, description="Strategy identifier")


@router.post("/model/final-decision")
async def tmc_final_decision(
    payload: TMCFinalDecisionRequest,
    request: Request,
) -> dict[str, Any]:
    """Run TMC final trade decision analysis via LLM.

    Unlike the per-strategy ``/api/model/analyze_stock_strategy`` endpoint,
    this uses a dedicated portfolio-manager-level prompt that combines
    trade setup data with fresh market picture context.

    The server loads the current market picture from the latest MI artifact
    so the model always sees the most recent market environment.
    """
    import asyncio
    import functools

    candidate = payload.candidate
    if not candidate or not isinstance(candidate, dict):
        raise HTTPException(status_code=400, detail='Missing or invalid "candidate"')

    symbol = str(candidate.get("symbol") or "").strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail='Missing "symbol" in candidate')

    strategy_id = payload.strategy_id or candidate.get("scanner_key") or candidate.get("strategy_id")

    logger.info(
        "[TMC_FINAL_DECISION] endpoint hit — symbol=%s strategy=%s",
        symbol, strategy_id,
    )

    # ── Load fresh market picture context from latest MI artifact ──
    market_picture_context: dict[str, Any] | None = None
    try:
        data_dir = _get_data_dir(request)
        from app.workflows.market_state_consumer import load_market_state_for_consumer

        consumer = load_market_state_for_consumer(data_dir)
        if consumer.loaded and consumer.artifact:
            engines = consumer.artifact.get("engines") or {}
            from app.workflows.stock_opportunity_runner import _build_market_picture_context

            market_picture_context = _build_market_picture_context(engines)
            logger.info(
                "[TMC_FINAL_DECISION] loaded market picture: %d engines",
                len(market_picture_context),
            )
        else:
            logger.warning(
                "[TMC_FINAL_DECISION] market state not available: %s",
                consumer.error,
            )
    except Exception as exc:
        logger.warning("[TMC_FINAL_DECISION] failed to load market picture: %s", exc)

    # ── Run analysis ──
    try:
        from common.model_analysis import LocalModelUnavailableError
        from app.services.model_routing_integration import routed_tmc_final_decision

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            functools.partial(
                routed_tmc_final_decision,
                candidate=candidate,
                market_picture_context=market_picture_context,
                strategy_id=strategy_id,
            ),
        )
    except LocalModelUnavailableError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("[TMC_FINAL_DECISION] analysis failed — symbol=%s", symbol)
        raise HTTPException(
            status_code=500,
            detail=f"TMC final decision analysis failed: {exc}",
        ) from exc

    # ── Persist artifact ──
    try:
        artifact_path: Path = request.app.state.results_dir / "tmc_final_decision.jsonl"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "strategy_id": strategy_id,
            "decision": result.get("decision"),
            "conviction": result.get("conviction"),
            "market_engines_available": len(market_picture_context) if market_picture_context else 0,
            "result": result,
        }
        with open(artifact_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:
        logger.warning("[TMC_FINAL_DECISION] failed to persist artifact: %s", exc)

    logger.info(
        "[TMC_FINAL_DECISION] OK — symbol=%s decision=%s conviction=%s",
        symbol, result.get("decision"), result.get("conviction"),
    )

    return {"ok": True, "analysis": result}


# ═══════════════════════════════════════════════════════════════════════
# PORTFOLIO BALANCE
# ═══════════════════════════════════════════════════════════════════════


class TMCPortfolioBalanceRequest(BaseModel):
    """Request body for portfolio balance workflow."""

    account_mode: str = Field(default="paper", pattern="^(live|paper)$")
    skip_model: bool = Field(default=False)
    stock_results: dict[str, Any] | None = Field(
        default=None,
        description="Pre-computed stock workflow output (from prior run)",
    )
    options_results: dict[str, Any] | None = Field(
        default=None,
        description="Pre-computed options workflow output (from prior run)",
    )
    active_trade_results: dict[str, Any] | None = Field(
        default=None,
        description="Pre-computed active trade pipeline output (from prior run)",
    )


@router.post("/portfolio-balance/run")
async def run_portfolio_balance(
    request: Request,
    body: TMCPortfolioBalanceRequest | None = None,
) -> dict[str, Any]:
    """Run the portfolio balancing workflow.

    Chains: account state → regime → risk policy → active trades → balancer.
    Returns a rebalance plan with close/open/hold actions and net impact.

    Accepts optional pre-computed results from prior workflow runs to avoid
    re-running expensive pipelines.
    """
    from app.workflows.portfolio_balancing_runner import run_portfolio_balance_workflow

    params = body or TMCPortfolioBalanceRequest()

    logger.info(
        "[TMC_PORTFOLIO_BALANCE] endpoint hit — account_mode=%s skip_model=%s "
        "has_stock=%s has_options=%s has_active=%s",
        params.account_mode,
        params.skip_model,
        params.stock_results is not None,
        params.options_results is not None,
        params.active_trade_results is not None,
    )

    result = await run_portfolio_balance_workflow(
        request=request,
        account_mode=params.account_mode,
        stock_results=params.stock_results,
        options_results=params.options_results,
        active_trade_results=params.active_trade_results,
        skip_model=params.skip_model,
    )

    logger.info(
        "[TMC_PORTFOLIO_BALANCE] OK — run_id=%s ok=%s duration_ms=%s",
        result.get("run_id"),
        result.get("ok"),
        result.get("duration_ms"),
    )

    return result
