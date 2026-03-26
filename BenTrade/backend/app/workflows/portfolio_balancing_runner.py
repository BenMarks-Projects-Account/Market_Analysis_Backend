"""Portfolio Balancing Workflow — chains all workflows into a rebalance plan."""

import logging
import time
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any

_log = logging.getLogger(__name__)


async def run_portfolio_balance_workflow(
    *,
    request: Any = None,
    account_mode: str = "paper",
    stock_results: dict | None = None,
    options_results: dict | None = None,
    active_trade_results: dict | None = None,
    skip_model: bool = False,
) -> dict:
    """Run the full portfolio balancing workflow.

    If stock_results, options_results, or active_trade_results are provided,
    they are used directly (from a prior "Run Full Refresh"). Otherwise,
    each is run fresh.

    Args:
        request: FastAPI Request for accessing app.state services (None for tests)
        account_mode: "live" or "paper"
        stock_results: Pre-computed stock workflow output (or None to run fresh)
        options_results: Pre-computed options workflow output (or None to run fresh)
        active_trade_results: Pre-computed active trade output (or None to run fresh)
        skip_model: Skip LLM analysis in sub-workflows

    Returns:
        Full workflow result with rebalance plan
    """
    started = time.time()
    run_id = f"pb_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"

    stages: dict[str, Any] = {}
    errors: list[str] = []

    _log.info("event=pb_start run_id=%s account_mode=%s", run_id, account_mode)

    # ─── STEP 1: Fetch account state ───
    stage_start = time.time()
    account_balance: dict = {}
    try:
        from app.trading.tradier_credentials import get_tradier_context

        settings = _resolve_settings(request)
        creds = get_tradier_context(settings, account_type=account_mode)

        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            bal_resp = await client.get(
                f"{creds.base_url}/accounts/{creds.account_id}/balances",
                headers={
                    "Authorization": f"Bearer {creds.api_key}",
                    "Accept": "application/json",
                },
            )
            account_balance = bal_resp.json().get("balances") or {}
        stages["account_state"] = {
            "status": "completed",
            "duration_ms": _elapsed_ms(stage_start),
        }
    except Exception as exc:
        _log.error("event=pb_account_failed error=%s", exc)
        errors.append(f"Account state fetch failed: {exc}")
        stages["account_state"] = {"status": "error", "error": str(exc)}

    # ─── STEP 2: Get regime label ───
    stage_start = time.time()
    regime_label: str | None = None
    try:
        regime_service = _resolve_regime_service(request)
        if regime_service:
            regime_result = await regime_service.get_regime()
            regime_label = regime_result.get("regime_label") or regime_result.get("label")
        else:
            _log.warning("event=pb_regime_skipped reason=no_service")
    except Exception as exc:
        _log.warning("event=pb_regime_failed error=%s", exc)
    stages["regime"] = {
        "status": "completed" if regime_label else "degraded",
        "regime_label": regime_label,
        "duration_ms": _elapsed_ms(stage_start),
    }

    # ─── STEP 3: Build dynamic risk policy ───
    stage_start = time.time()
    try:
        from app.services.risk_policy_service import build_dynamic_policy
        risk_policy = build_dynamic_policy(account_balance, regime_label)
    except Exception as exc:
        _log.warning("event=pb_policy_failed error=%s", exc)
        from app.services.risk_policy_service import RiskPolicyService
        risk_policy = RiskPolicyService.static_default_policy()
    stages["risk_policy"] = {
        "status": "completed",
        "dynamic": risk_policy.get("dynamic", False),
        "duration_ms": _elapsed_ms(stage_start),
    }

    # ─── STEP 4: Run active trade pipeline (if not provided) ───
    stage_start = time.time()
    if active_trade_results is None:
        active_trade_results = await _run_active_trades(request, account_mode, skip_model)
    # Defensive: ensure active_trade_results is always a dict
    if not isinstance(active_trade_results, dict):
        active_trade_results = {"recommendations": []}
    if active_trade_results.get("ok") is False:
        errors.append(
            f"Active trade analysis failed: {(active_trade_results.get('error') or {}).get('message', 'unknown')}"
        )
    stages["active_trades"] = {
        "status": "provided" if active_trade_results.get("_provided") else "completed",
        "count": len(active_trade_results.get("recommendations", [])),
        "duration_ms": _elapsed_ms(stage_start),
    }

    # ─── STEP 5: Get stock + options candidates (if not provided) ───
    stock_candidates: list = []
    options_candidates: list = []

    stage_start = time.time()
    if stock_results:
        # Unwrap {status, data: {candidates}} envelope if present
        _sr = stock_results.get("data", stock_results) if isinstance(stock_results, dict) else stock_results
        if isinstance(_sr, dict):
            stock_candidates = _sr.get(
                "candidates", _sr.get("recommendations", [])
            )
    # Guard against explicit null values in JSON responses
    if not isinstance(stock_candidates, list):
        stock_candidates = []
    stages["stock_candidates"] = {
        "status": "provided" if stock_results else "skipped",
        "count": len(stock_candidates),
        "duration_ms": _elapsed_ms(stage_start),
    }

    stage_start = time.time()
    if options_results:
        # Unwrap {status, data: {candidates}} envelope if present
        _or = options_results.get("data", options_results) if isinstance(options_results, dict) else options_results
        if isinstance(_or, dict):
            options_candidates = _or.get(
                "candidates", _or.get("selected", [])
            )
    # Guard against explicit null values in JSON responses
    if not isinstance(options_candidates, list):
        options_candidates = []
    stages["options_candidates"] = {
        "status": "provided" if options_results else "skipped",
        "count": len(options_candidates),
        "duration_ms": _elapsed_ms(stage_start),
    }

    # ─── STEP 6: Get current portfolio state ───
    stage_start = time.time()
    portfolio_greeks, concentration = _build_portfolio_state(active_trade_results, account_balance)
    stages["portfolio_state"] = {
        "status": "completed",
        "duration_ms": _elapsed_ms(stage_start),
    }

    # ─── STEP 7: Run portfolio balancer ───
    stage_start = time.time()
    rebalance_plan: dict | None = None
    _log.info(
        "PORTFOLIO_DEBUG build_rebalance_plan inputs: "
        "active_trade_results type=%s keys=%s, "
        "stock_candidates type=%s len=%s, "
        "options_candidates type=%s len=%s, "
        "account_balance type=%s keys=%s, "
        "risk_policy type=%s, "
        "portfolio_greeks type=%s, "
        "concentration type=%s, "
        "regime_label=%r",
        type(active_trade_results).__name__,
        list(active_trade_results.keys()) if isinstance(active_trade_results, dict) else "N/A",
        type(stock_candidates).__name__,
        len(stock_candidates) if isinstance(stock_candidates, list) else "N/A",
        type(options_candidates).__name__,
        len(options_candidates) if isinstance(options_candidates, list) else "N/A",
        type(account_balance).__name__,
        list(account_balance.keys()) if isinstance(account_balance, dict) else "N/A",
        type(risk_policy).__name__,
        type(portfolio_greeks).__name__,
        type(concentration).__name__,
        regime_label,
    )
    try:
        from app.services.portfolio_balancer import build_rebalance_plan
        rebalance_plan = await build_rebalance_plan(
            active_trade_results=active_trade_results,
            stock_candidates=stock_candidates,
            options_candidates=options_candidates,
            account_balance=account_balance,
            risk_policy=risk_policy,
            portfolio_greeks=portfolio_greeks,
            concentration=concentration,
            regime_label=regime_label,
        )
    except Exception as exc:
        _log.error(
            "event=pb_balancer_failed error=%s\n%s",
            exc,
            traceback.format_exc(),
        )
        errors.append(f"Portfolio balancer failed: {exc}")
    stages["portfolio_balance"] = {
        "status": "completed" if rebalance_plan else "error",
        "duration_ms": _elapsed_ms(stage_start),
    }

    duration_ms = _elapsed_ms(started)

    # Final defensive guard — account_balance must be a dict for safe .get()
    if not isinstance(account_balance, dict):
        account_balance = {}

    return {
        "ok": len(errors) == 0,
        "run_id": run_id,
        "account_mode": account_mode,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "duration_ms": duration_ms,
        "account_equity": account_balance.get("equity"),
        "regime_label": regime_label,
        "rebalance_plan": rebalance_plan,
        "active_trade_summary": {
            "total": len(active_trade_results.get("recommendations", [])),
            "close": len(rebalance_plan.get("close_actions", [])) if rebalance_plan else 0,
            "hold": len(rebalance_plan.get("hold_positions", [])) if rebalance_plan else 0,
            "open_suggested": len(rebalance_plan.get("open_actions", [])) if rebalance_plan else 0,
            "skipped": len(rebalance_plan.get("skip_actions", [])) if rebalance_plan else 0,
        },
        "risk_policy": risk_policy,
        "stages": stages,
        "errors": errors,
    }


# ─── Internal helpers ───


def _elapsed_ms(start: float) -> int:
    return int((time.time() - start) * 1000)


def _resolve_settings(request: Any):
    """Get settings from app state or fall back to global config."""
    if request and hasattr(request, "app"):
        ts = getattr(request.app.state, "trading_service", None)
        if ts:
            return ts.settings
    from app.config import get_settings
    return get_settings()


def _resolve_regime_service(request: Any):
    """Get RegimeService from app state, or None."""
    if request and hasattr(request, "app"):
        return getattr(request.app.state, "regime_service", None)
    return None


async def _run_active_trades(
    request: Any,
    account_mode: str,
    skip_model: bool,
) -> dict[str, Any]:
    """Run active trade pipeline by delegating to the existing route logic."""
    if not request or not hasattr(request, "app"):
        _log.warning("event=pb_active_trades_skipped reason=no_request_context")
        return {"recommendations": [], "ok": True}

    try:
        from app.api.routes_active_trades import _build_active_payload
        from app.services.active_trade_pipeline import run_active_trade_pipeline

        payload = await _build_active_payload(request, account_mode=account_mode)
        if not payload.get("ok"):
            return {
                "recommendations": [],
                "ok": False,
                "error": payload.get("error", {"message": "Failed to load positions"}),
            }

        trades = payload.get("active_trades") or []
        if not trades:
            return {"recommendations": [], "ok": True}

        monitor_service = getattr(request.app.state, "active_trade_monitor_service", None)
        regime_service = getattr(request.app.state, "regime_service", None)
        base_data_service = getattr(request.app.state, "base_data_service", None)

        if not monitor_service or not regime_service or not base_data_service:
            return {
                "recommendations": [],
                "ok": False,
                "error": {"message": "Required services not available", "type": "ServiceUnavailable"},
            }

        result = await run_active_trade_pipeline(
            trades,
            monitor_service,
            regime_service,
            base_data_service,
            skip_model=skip_model,
            positions_metadata={
                "source": "portfolio_balance_workflow",
                "account_mode": account_mode,
            },
        )
        return result
    except Exception as exc:
        _log.error("event=pb_active_trade_pipeline_error error=%s", exc, exc_info=True)
        return {
            "recommendations": [],
            "ok": False,
            "error": {"message": str(exc), "type": type(exc).__name__},
        }


def _build_portfolio_state(
    active_trade_results: dict,
    account_balance: dict,
) -> tuple[dict, dict]:
    """Build portfolio Greeks and concentration from active trade results."""
    active_trade_results = active_trade_results or {"recommendations": []}
    account_balance = account_balance or {}
    try:
        from app.services.portfolio_risk_engine import build_portfolio_exposure

        # Collect position-like dicts from recommendations
        positions: list[dict] = []
        for rec in active_trade_results.get("recommendations", []):
            snap = rec.get("position_snapshot", {})
            greeks = rec.get("live_greeks", {})

            pos = {
                "symbol": rec.get("symbol"),
                "strategy": rec.get("strategy"),
                "risk": abs(snap.get("max_loss") or snap.get("cost_basis_total") or 0),
                "delta": greeks.get("trade_delta", 0),
                "gamma": greeks.get("trade_gamma", 0),
                "theta": greeks.get("trade_theta", 0),
                "vega": greeks.get("trade_vega", 0),
            }
            positions.append(pos)

        if not positions:
            return (
                {"delta": 0, "gamma": 0, "theta": 0, "vega": 0},
                {"by_underlying": {"items": []}},
            )

        equity = float(account_balance.get("equity") or 0) or None
        exposure = build_portfolio_exposure(positions, account_equity=equity)

        greeks_section = exposure.get("greeks_exposure", {})
        portfolio_greeks = {
            "delta": greeks_section.get("net_delta", 0),
            "gamma": greeks_section.get("net_gamma", 0),
            "theta": greeks_section.get("net_theta", 0),
            "vega": greeks_section.get("net_vega", 0),
        }

        concentration = {
            "by_underlying": exposure.get("underlying_concentration", {}),
            "by_strategy": exposure.get("strategy_concentration", {}),
            "by_expiration": exposure.get("expiration_concentration", {}),
        }

        return portfolio_greeks, concentration
    except Exception as exc:
        _log.warning("event=pb_portfolio_state_failed error=%s", exc)
        return (
            {"delta": 0, "gamma": 0, "theta": 0, "vega": 0},
            {"by_underlying": {"items": []}},
        )
