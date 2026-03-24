"""Close-order builder for the active trade pipeline.

Given a pipeline trade dict and a recommendation (CLOSE / REDUCE),
produces a structured close-order payload that can be passed directly
to TradingService.preview() or the TradierOrderBuilder.

Design:
    - Stateless module-level functions (no class state).
    - Handles equity (market sell) and multi-leg option positions.
    - For REDUCE, closes a fraction of the position (default 50%).
    - Close-side logic: short legs → buy_to_close, long legs → sell_to_close.
    - Returns ``ready_for_preview: True`` so TMC can route to preview flow.

Input fields used from trade dict:
    symbol, strategy, strategy_id, legs, quantity, mark_price, expiration
Leg fields used:
    symbol (OCC symbol), side ("buy"/"sell"), qty, strike, option_type, price
"""

from __future__ import annotations

import logging
import math
from typing import Any

_log = logging.getLogger("bentrade.close_order_builder")


def build_close_order(
    trade: dict[str, Any],
    action: str = "CLOSE",
    reduce_pct: float = 0.5,
) -> dict[str, Any] | None:
    """Build a close/reduce order payload from a pipeline trade dict.

    Parameters
    ----------
    trade : dict
        Trade dict produced by ``_build_active_trades()`` — must have ``legs``.
    action : str
        ``"CLOSE"`` (full exit) or ``"REDUCE"`` (partial exit).
    reduce_pct : float
        Fraction of the position to close when action is REDUCE (0 < pct <= 1).

    Returns
    -------
    dict | None
        Structured order payload with ``ready_for_preview: True``, or None
        if legs are missing / empty.
    """
    legs = trade.get("legs")
    if not isinstance(legs, list) or len(legs) == 0:
        _log.warning("build_close_order: no legs on trade %s", trade.get("trade_key"))
        return None

    underlying = str(trade.get("symbol") or "").upper()
    if not underlying:
        _log.warning("build_close_order: missing symbol on trade %s", trade.get("trade_key"))
        return None

    strategy = str(trade.get("strategy") or "").lower()
    is_equity = strategy == "equity"

    if is_equity:
        return _build_equity_close(trade, legs[0], action, reduce_pct, underlying)
    return _build_option_close(trade, legs, action, reduce_pct, underlying)


# ── Equity close ─────────────────────────────────────────────────────────

def _build_equity_close(
    trade: dict[str, Any],
    leg: dict[str, Any],
    action: str,
    reduce_pct: float,
    underlying: str,
) -> dict[str, Any]:
    """Build a simple equity sell-to-close order."""
    full_qty = int(leg.get("qty") or trade.get("quantity") or 0)
    close_qty = _close_quantity(full_qty, action, reduce_pct)
    if close_qty <= 0:
        return None  # type: ignore[return-value]

    mark = _safe_float(leg.get("mark_price") or leg.get("price"))
    estimated_proceeds = round(mark * close_qty, 2) if mark is not None else None

    return {
        "order_type": "equity",
        "action": action,
        "symbol": underlying,
        "side": "sell",
        "quantity": close_qty,
        "limit_price": mark,
        "estimated_proceeds": estimated_proceeds,
        "time_in_force": "DAY",
        "description": _build_description(underlying, trade, action, close_qty, is_equity=True),
        "ready_for_preview": True,
    }


# ── Multi-leg option close ───────────────────────────────────────────────

def _build_option_close(
    trade: dict[str, Any],
    legs: list[dict[str, Any]],
    action: str,
    reduce_pct: float,
    underlying: str,
) -> dict[str, Any]:
    """Build a multi-leg option close order (verticals, condors, etc.)."""
    order_legs: list[dict[str, Any]] = []
    total_cost_estimate = 0.0
    has_price = True

    for leg in legs:
        raw_side = str(leg.get("side") or "").lower()
        close_side = _invert_side(raw_side)
        if close_side is None:
            _log.warning("build_close_order: unknown side %r on leg %s", raw_side, leg.get("symbol"))
            continue

        full_qty = int(leg.get("qty") or 1)
        close_qty = _close_quantity(full_qty, action, reduce_pct)
        if close_qty <= 0:
            continue

        mark = _safe_float(leg.get("price"))
        if mark is not None:
            # buy_to_close costs money; sell_to_close receives premium
            if close_side == "buy_to_close":
                total_cost_estimate += mark * close_qty * 100
            else:
                total_cost_estimate -= mark * close_qty * 100
        else:
            has_price = False

        order_legs.append({
            "option_symbol": leg.get("symbol"),
            "side": close_side,
            "quantity": close_qty,
            "strike": leg.get("strike"),
            "option_type": leg.get("option_type"),
        })

    if not order_legs:
        return None  # type: ignore[return-value]

    # Net close price per spread (for limit order)
    spread_qty = min(ol["quantity"] for ol in order_legs) if order_legs else 1
    net_close_price = None
    if has_price and spread_qty > 0:
        net_close_price = round(abs(total_cost_estimate) / (spread_qty * 100), 2)

    # Is closing a net debit (we pay) or credit (we receive)?
    price_effect = "debit" if total_cost_estimate > 0 else "credit"

    return {
        "order_type": "multileg",
        "action": action,
        "symbol": underlying,
        "strategy_id": trade.get("strategy_id"),
        "expiration": trade.get("expiration"),
        "legs": order_legs,
        "quantity": spread_qty,
        "price_effect": price_effect,
        "limit_price": net_close_price,
        "estimated_cost": round(total_cost_estimate, 2) if has_price else None,
        "time_in_force": "DAY",
        "description": _build_description(
            underlying, trade, action, spread_qty, is_equity=False,
        ),
        "ready_for_preview": True,
    }


# ── Helpers ──────────────────────────────────────────────────────────────

def _invert_side(side: str) -> str | None:
    """Map an opening side to its closing counterpart.

    Input: "buy" (long) or "sell" (short) from the position.
    Output: "sell_to_close" or "buy_to_close".
    """
    mapping = {
        "buy": "sell_to_close",
        "sell": "buy_to_close",
        "buy_to_open": "sell_to_close",
        "sell_to_open": "buy_to_close",
    }
    return mapping.get(side.lower().strip()) if side else None


def _close_quantity(full_qty: int, action: str, reduce_pct: float) -> int:
    """Compute how many contracts/shares to close.

    CLOSE → full_qty.
    REDUCE → max(1, round(full_qty * reduce_pct)).
    """
    if action.upper() == "CLOSE":
        return full_qty
    return max(1, round(full_qty * reduce_pct))


def _safe_float(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        f = float(val)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _build_description(
    underlying: str,
    trade: dict[str, Any],
    action: str,
    qty: int,
    *,
    is_equity: bool,
) -> str:
    """Human-readable description for the close order."""
    verb = "Close" if action.upper() == "CLOSE" else "Reduce"
    strategy = trade.get("strategy") or trade.get("strategy_id") or ""
    expiration = trade.get("expiration") or ""

    if is_equity:
        return f"{verb} {qty} shares of {underlying}"

    exp_label = f" exp {expiration}" if expiration else ""
    return f"{verb} {qty}x {underlying} {strategy}{exp_label}"
