from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from app.services.position_sizing import PositionSizingEngine, build_portfolio_exposure
from app.utils.http import request_json

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/risk", tags=["risk-capital"])


class RiskPolicyUpdateRequest(BaseModel):
    portfolio_size: float | None = None
    max_total_risk_pct: float | None = None
    max_symbol_risk_pct: float | None = None
    max_trade_risk_pct: float | None = None
    max_dte: int | None = None
    min_cash_reserve_pct: float | None = None
    max_position_size_pct: float | None = None
    default_contracts_cap: int | None = None
    max_risk_per_trade: float | None = None
    max_risk_total: float | None = None
    max_concurrent_trades: int | None = None
    max_risk_per_underlying: float | None = None
    max_same_expiration_risk: float | None = None
    max_short_strike_distance_sigma: float | None = None
    min_open_interest: int | None = None
    min_volume: int | None = None
    max_bid_ask_spread_pct: float | None = None
    min_pop: float | None = None
    min_ev_to_risk: float | None = None
    min_return_on_risk: float | None = None
    max_iv_rv_ratio_for_buying: float | None = None
    min_iv_rv_ratio_for_selling: float | None = None
    notes: str | None = None


@router.get("/policy")
async def get_risk_policy(request: Request) -> dict[str, Any]:
    policy = request.app.state.risk_policy_service.get_policy()
    return {"policy": policy}


@router.put("/policy")
async def put_risk_policy(payload: RiskPolicyUpdateRequest, request: Request) -> dict[str, Any]:
    policy = request.app.state.risk_policy_service.save_policy(payload.model_dump(exclude_none=False))
    return {"ok": True, "policy": policy}


@router.get("/snapshot")
async def get_risk_snapshot(request: Request) -> dict[str, Any]:
    return await request.app.state.risk_policy_service.build_snapshot(request)


# ═══════════════════════════════════════════════════════════════
# Position Sizing Endpoints
# ═══════════════════════════════════════════════════════════════


class PositionSizeRequest(BaseModel):
    """Request body for POST /api/risk/size."""
    symbol: str
    scanner_key: str | None = None
    strategy_id: str | None = None
    max_loss_per_contract: float
    account_mode: str = "paper"


@router.post("/size")
async def compute_position_size(
    payload: PositionSizeRequest, request: Request,
) -> dict[str, Any]:
    """Compute recommended position size for a trade.

    Uses the current risk policy, live account equity, and open positions
    to determine how many contracts to trade.
    """
    account_mode = payload.account_mode.lower().strip()
    if account_mode not in ("live", "paper"):
        account_mode = "paper"

    # ── Fetch account balance ──────────────────────────────────
    account = await _fetch_account_balance(request, account_mode)
    if account.get("_error"):
        return {
            "ok": False,
            "error": account["_error"],
            "sizing": PositionSizingEngine._blocked(account["_error"]),
        }

    # ── Fetch open positions ───────────────────────────────────
    positions = await _fetch_open_positions(request, account_mode)

    # ── Get risk policy ────────────────────────────────────────
    policy = request.app.state.risk_policy_service.get_policy()

    # ── Build trade dict ───────────────────────────────────────
    trade = {
        "symbol": payload.symbol.upper(),
        "max_loss": payload.max_loss_per_contract,
        "scanner_key": payload.scanner_key or payload.strategy_id or "",
    }

    # ── Compute sizing ─────────────────────────────────────────
    engine = PositionSizingEngine(policy)
    sizing = engine.compute_size(trade, account, positions)

    return {
        "ok": True,
        "account_mode": account_mode,
        "sizing": sizing,
    }


@router.get("/state")
async def get_risk_state(
    request: Request,
    account_mode: str = Query("paper", pattern="^(live|paper)$"),
) -> dict[str, Any]:
    """Return current portfolio risk state: equity, exposure, capacity.

    Combines live Tradier account data with the risk policy to provide
    a complete picture of portfolio risk utilization.
    """
    mode = account_mode.lower().strip()
    if mode not in ("live", "paper"):
        mode = "paper"

    # ── Fetch account balance ──────────────────────────────────
    account = await _fetch_account_balance(request, mode)
    if account.get("_error"):
        return {
            "ok": False,
            "error": account["_error"],
            "equity": 0,
            "policy": {},
            "portfolio_exposure": {},
        }

    equity = float(account.get("equity") or account.get("total_equity") or 0)

    # ── Fetch open positions ───────────────────────────────────
    positions = await _fetch_open_positions(request, mode)

    # ── Get risk policy ────────────────────────────────────────
    policy = request.app.state.risk_policy_service.get_policy()

    # ── Build exposure snapshot ────────────────────────────────
    exposure = build_portfolio_exposure(
        positions, equity=equity, policy=policy,
    )

    return {
        "ok": True,
        "account_mode": mode,
        "equity": equity,
        "buying_power": float(
            account.get("option_buying_power")
            or account.get("buying_power")
            or 0
        ),
        "policy": policy,
        "portfolio_exposure": exposure,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


# ── Shared helpers for Tradier account data ────────────────────


async def _fetch_account_balance(
    request: Request, account_mode: str,
) -> dict[str, Any]:
    """Fetch account balances from Tradier for the given account mode.

    Returns the balances dict on success, or a dict with _error key on failure.
    """
    from app.trading.tradier_credentials import get_tradier_context

    settings = request.app.state.trading_service.settings
    try:
        creds = get_tradier_context(settings, account_type=account_mode)
    except Exception as exc:
        logger.warning("risk.creds_failed mode=%s exc=%s", account_mode, exc)
        return {"_error": f"Credentials not configured for {account_mode}"}

    if not creds.api_key or not creds.account_id:
        return {"_error": f"Credentials incomplete for {account_mode}"}

    headers = {
        "Authorization": f"Bearer {creds.api_key}",
        "Accept": "application/json",
    }
    http_client = request.app.state.http_client

    try:
        url = f"{creds.base_url}/accounts/{creds.account_id}/balances"
        result = await request_json(http_client, "GET", url, headers=headers)
        balances = result.get("balances") or result
        if isinstance(balances, dict):
            return balances
        return {"_error": "Unexpected balances response structure"}
    except Exception as exc:
        logger.warning("risk.balance_fetch_failed mode=%s exc=%s", account_mode, exc)
        return {"_error": f"Failed to fetch account balance: {exc}"}


async def _fetch_open_positions(
    request: Request, account_mode: str,
) -> list[dict[str, Any]]:
    """Fetch and normalize open positions from Tradier.

    Returns list of position dicts with symbol, max_loss, quantity, etc.
    Falls back to empty list on any error.
    """
    from app.trading.tradier_credentials import get_tradier_context

    settings = request.app.state.trading_service.settings
    try:
        creds = get_tradier_context(settings, account_type=account_mode)
    except Exception:
        return []

    if not creds.api_key or not creds.account_id:
        return []

    headers = {
        "Authorization": f"Bearer {creds.api_key}",
        "Accept": "application/json",
    }
    http_client = request.app.state.http_client

    try:
        url = f"{creds.base_url}/accounts/{creds.account_id}/positions"
        result = await request_json(http_client, "GET", url, headers=headers)
        raw_positions = result.get("positions", {})

        # Tradier wraps positions: {"positions": {"position": [...]}} or "null"
        if isinstance(raw_positions, dict):
            items = raw_positions.get("position", [])
        elif isinstance(raw_positions, list):
            items = raw_positions
        else:
            items = []

        if isinstance(items, dict):
            items = [items]

        return _normalize_positions_for_sizing(items)
    except Exception as exc:
        logger.warning("risk.positions_fetch_failed mode=%s exc=%s", account_mode, exc)
        return []


def _normalize_positions_for_sizing(
    raw_positions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize Tradier position objects for the sizing engine.

    Maps Tradier fields to the common shape expected by PositionSizingEngine:
        symbol, quantity, max_loss, cost_basis, scanner_key
    """
    normalized: list[dict[str, Any]] = []
    for p in raw_positions:
        symbol = str(p.get("symbol") or "").upper()

        # Extract underlying from OCC symbol if it's an option
        # OCC format: SPY250516P00530000
        underlying = symbol
        if len(symbol) > 6 and any(c in symbol for c in ("C", "P")):
            # Heuristic: strip trailing option details to get raw underlying
            for i, ch in enumerate(symbol):
                if ch.isdigit():
                    underlying = symbol[:i]
                    break

        quantity = abs(float(p.get("quantity") or 1))
        cost_basis = abs(float(p.get("cost_basis") or 0))

        # For options, estimate max_loss from cost_basis / quantity
        # (cost_basis is total, max_loss should be per-contract)
        if quantity > 0 and cost_basis > 0:
            max_loss_per = cost_basis / quantity
        else:
            max_loss_per = cost_basis or 0

        normalized.append({
            "symbol": underlying,
            "underlying": underlying,
            "raw_symbol": symbol,
            "quantity": quantity,
            "max_loss": max_loss_per,
            "cost_basis": cost_basis,
            "scanner_key": "",  # Not available from raw Tradier positions
        })

    return normalized


# ═══════════════════════════════════════════════════════════════
# Pre-Trade Risk Validation
# ═══════════════════════════════════════════════════════════════


class TradeValidationRequest(BaseModel):
    """Request body for POST /api/risk/validate."""
    symbol: str
    scanner_key: str = ""
    max_loss_per_contract: float
    quantity: int = 1
    account_mode: str = "paper"


@router.post("/validate")
async def validate_trade(
    payload: TradeValidationRequest, request: Request,
) -> dict[str, Any]:
    """Validate a proposed trade against the risk policy.

    Runs five checks:
        1. per_trade_risk — total risk of this trade vs single-trade limit
        2. per_underlying — concentration on a single underlying
        3. total_portfolio — cumulative risk vs total portfolio limit
        4. directional_concentration — bullish/bearish skew limit
        5. account_reserve — minimum cash reserve maintained

    Returns approved=True if all checks pass. Never blocks execution;
    the user always has final say. Failures include suggested_max_quantity
    and actionable warning messages.
    """
    account_mode = payload.account_mode.lower().strip()
    if account_mode not in ("live", "paper"):
        account_mode = "paper"

    symbol = payload.symbol.upper().strip()
    scanner_key = payload.scanner_key or ""
    max_loss = abs(payload.max_loss_per_contract)
    quantity = max(1, payload.quantity)

    if max_loss <= 0:
        return {
            "ok": True,
            "approved": False,
            "risk_checks": [],
            "warnings": ["max_loss_per_contract must be > 0"],
            "suggested_max_quantity": 0,
            "summary": "Cannot validate: no max loss provided.",
        }

    proposed_risk = max_loss * quantity

    # ── Fetch account balance ──────────────────────────────────
    account = await _fetch_account_balance(request, account_mode)
    if account.get("_error"):
        return {
            "ok": False,
            "error": account["_error"],
            "approved": False,
            "risk_checks": [],
            "warnings": [account["_error"]],
            "summary": "Cannot validate: account data unavailable.",
        }

    equity = float(account.get("equity") or account.get("total_equity") or 0)
    if equity <= 0:
        return {
            "ok": True,
            "approved": False,
            "risk_checks": [],
            "warnings": ["Account equity is $0 or unavailable"],
            "suggested_max_quantity": 0,
            "summary": "Cannot validate: account equity is zero.",
        }

    # ── Fetch open positions ───────────────────────────────────
    positions = await _fetch_open_positions(request, account_mode)

    # ── Get risk policy ────────────────────────────────────────
    policy = request.app.state.risk_policy_service.get_policy()

    # ── Extract policy limits (same logic as PositionSizingEngine) ──
    per_trade_limit = float(
        policy.get("max_risk_per_trade")
        or equity * float(policy.get("max_trade_risk_pct") or 0.01)
    )
    per_underlying_limit = float(
        policy.get("max_risk_per_underlying")
        or equity * float(policy.get("max_symbol_risk_pct") or 0.02)
    )
    total_limit = float(
        policy.get("max_risk_total")
        or equity * float(policy.get("max_total_risk_pct") or 0.06)
    )
    min_cash_reserve_pct = float(policy.get("min_cash_reserve_pct") or 20.0)
    if min_cash_reserve_pct > 1:
        min_cash_reserve_pct /= 100.0
    max_directional_pct = float(policy.get("max_directional_concentration_pct") or 0.06)
    directional_limit = equity * max_directional_pct

    # ── Current exposures (reuse PositionSizingEngine helpers) ──
    underlying_exposure = PositionSizingEngine._get_underlying_exposure(symbol, positions)
    total_exposure = PositionSizingEngine._get_total_exposure(positions)
    deployed_capital = PositionSizingEngine._get_deployed_capital(positions)
    direction = PositionSizingEngine._get_direction({"scanner_key": scanner_key})
    directional_exposure = PositionSizingEngine._get_directional_exposure(direction, positions)

    checks: list[dict[str, Any]] = []
    all_pass = True
    warnings: list[str] = []

    # ── Check 1: Per-trade risk ────────────────────────────────
    check1: dict[str, Any] = {
        "check": "per_trade_risk",
        "limit": round(per_trade_limit, 2),
        "proposed": round(proposed_risk, 2),
        "pct_of_limit": round(proposed_risk / per_trade_limit * 100, 1) if per_trade_limit > 0 else 999,
        "status": "PASS" if proposed_risk <= per_trade_limit else "FAIL",
    }
    if check1["status"] == "FAIL":
        all_pass = False
        check1["message"] = f"Trade risk ${proposed_risk:,.0f} exceeds per-trade limit ${per_trade_limit:,.0f}"
        safe_qty = int(per_trade_limit / max_loss) if max_loss > 0 else 0
        warnings.append(f"Reduce to {safe_qty} contracts (${safe_qty * max_loss:,.0f} risk)")
    elif check1["pct_of_limit"] > 75:
        warnings.append(f"Per-trade risk at {check1['pct_of_limit']:.0f}% of limit")
    checks.append(check1)

    # ── Check 2: Per-underlying concentration ──────────────────
    proposed_underlying = underlying_exposure + proposed_risk
    check2: dict[str, Any] = {
        "check": "per_underlying",
        "limit": round(per_underlying_limit, 2),
        "current": round(underlying_exposure, 2),
        "proposed_total": round(proposed_underlying, 2),
        "pct_of_limit": round(proposed_underlying / per_underlying_limit * 100, 1) if per_underlying_limit > 0 else 999,
        "status": "PASS" if proposed_underlying <= per_underlying_limit else "FAIL",
    }
    if check2["status"] == "FAIL":
        all_pass = False
        remaining = max(0, per_underlying_limit - underlying_exposure)
        safe_qty = int(remaining / max_loss) if max_loss > 0 else 0
        check2["message"] = (
            f"{symbol} exposure would reach ${proposed_underlying:,.0f} "
            f"(limit ${per_underlying_limit:,.0f})"
        )
        warnings.append(
            f"Already ${underlying_exposure:,.0f} on {symbol}. "
            f"Max {safe_qty} more contracts."
        )
    elif check2["pct_of_limit"] > 75:
        warnings.append(
            f"{symbol} concentration at {check2['pct_of_limit']:.0f}% of limit "
            f"after this trade"
        )
    checks.append(check2)

    # ── Check 3: Total portfolio risk ──────────────────────────
    proposed_total = total_exposure + proposed_risk
    check3: dict[str, Any] = {
        "check": "total_portfolio",
        "limit": round(total_limit, 2),
        "current": round(total_exposure, 2),
        "proposed_total": round(proposed_total, 2),
        "pct_of_limit": round(proposed_total / total_limit * 100, 1) if total_limit > 0 else 999,
        "status": "PASS" if proposed_total <= total_limit else "FAIL",
    }
    if check3["status"] == "FAIL":
        all_pass = False
        remaining = max(0, total_limit - total_exposure)
        safe_qty = int(remaining / max_loss) if max_loss > 0 else 0
        check3["message"] = (
            f"Portfolio risk would reach ${proposed_total:,.0f} "
            f"(limit ${total_limit:,.0f})"
        )
        warnings.append(f"Portfolio at capacity. Max {safe_qty} contracts within limits.")
    elif check3["pct_of_limit"] > 75:
        warnings.append(
            f"Portfolio risk at {check3['pct_of_limit']:.0f}% of limit "
            f"after this trade"
        )
    checks.append(check3)

    # ── Check 4: Directional concentration ─────────────────────
    proposed_dir = directional_exposure + proposed_risk
    check4: dict[str, Any] = {
        "check": "directional_concentration",
        "direction": direction,
        "limit": round(directional_limit, 2),
        "current": round(directional_exposure, 2),
        "proposed_total": round(proposed_dir, 2),
        "pct_of_limit": round(proposed_dir / directional_limit * 100, 1) if directional_limit > 0 else 999,
        "status": "PASS" if proposed_dir <= directional_limit else "FAIL",
    }
    if check4["status"] == "FAIL":
        all_pass = False
        check4["message"] = (
            f"{direction.title()} exposure would reach ${proposed_dir:,.0f} "
            f"(limit ${directional_limit:,.0f})"
        )
        warnings.append(
            f"{direction.title()} concentration at "
            f"{check4['pct_of_limit']:.0f}% of limit"
        )
    elif check4["pct_of_limit"] > 75:
        warnings.append(
            f"{direction.title()} exposure at {check4['pct_of_limit']:.0f}% "
            f"of limit after this trade"
        )
    checks.append(check4)

    # ── Check 5: Account reserve ───────────────────────────────
    reserve_required = equity * min_cash_reserve_pct
    deployed_after = deployed_capital + proposed_risk
    reserve_after = equity - deployed_after
    check5: dict[str, Any] = {
        "check": "account_reserve",
        "reserve_required": round(reserve_required, 2),
        "equity": round(equity, 2),
        "deployed_after": round(deployed_after, 2),
        "reserve_after": round(reserve_after, 2),
        "status": "PASS" if reserve_after >= reserve_required else "FAIL",
    }
    if check5["status"] == "FAIL":
        all_pass = False
        check5["message"] = (
            f"Reserve would drop to ${reserve_after:,.0f} "
            f"(need ${reserve_required:,.0f})"
        )
        available = max(0, equity * (1.0 - min_cash_reserve_pct) - deployed_capital)
        safe_qty = int(available / max_loss) if max_loss > 0 else 0
        warnings.append(
            f"Reserve limit breached. Max {safe_qty} contracts "
            f"to maintain {min_cash_reserve_pct * 100:.0f}% reserve."
        )
    checks.append(check5)

    # ── Compute suggested max quantity if any check failed ─────
    suggested_max = None
    if not all_pass:
        engine = PositionSizingEngine(policy)
        sizing = engine.compute_size(
            {"symbol": symbol, "scanner_key": scanner_key, "max_loss": max_loss},
            account,
            positions,
        )
        suggested_max = sizing.get("suggested_contracts", 0)

    # ── Build summary ──────────────────────────────────────────
    fail_count = sum(1 for c in checks if c["status"] == "FAIL")
    if all_pass:
        summary = "All risk checks pass. Trade is within policy limits."
    else:
        summary = (
            f"{fail_count} risk check(s) failed. "
            f"Review warnings."
        )

    return {
        "ok": True,
        "approved": all_pass,
        "risk_checks": checks,
        "warnings": warnings,
        "suggested_max_quantity": suggested_max,
        "summary": summary,
    }


# ═══════════════════════════════════════════════════════════════
# Management Policies Endpoint
# ═══════════════════════════════════════════════════════════════


@router.get("/management-policies")
async def get_management_policies() -> dict[str, Any]:
    """Return the management policies for all strategy classes.

    These policies define profit targets and stop-loss levels used by the
    position management enrichment layer.
    """
    from app.services.position_management import MANAGEMENT_POLICIES

    return {
        "ok": True,
        "policies": MANAGEMENT_POLICIES,
    }
