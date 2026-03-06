"""Tradier Multi-Leg Order Payload Builder — foundation for paper/live routing.

Builds a structured Tradier-API-compatible payload from a validated trade
object WITHOUT submitting it.  The payload can be inspected, logged, and
later forwarded to the Tradier broker for paper or live execution.

Usage:
    from app.trading.tradier_order_builder import build_multileg_order

    payload = build_multileg_order(trade, account_mode="paper")
    # → { "class": "multileg", "symbol": "SPY", ... }

Key design choices:
    - Uses OCC symbols from each leg (REQUIRED — caller must validate first)
    - Detects credit vs debit from strategy_id or explicit price_effect
    - Supports 2-leg spreads AND 4-leg condors
    - Returns structured dict (NOT submitted) for inspection
    - Includes metadata for audit trail
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

_log = logging.getLogger("bentrade.tradier_order_builder")


# ── Strategy → price effect mapping ──────────────────────────────────────

_CREDIT_STRATEGIES = frozenset({
    "put_credit_spread", "call_credit_spread", "credit_spread",
    "iron_condor", "iron_butterfly",
    "csp", "covered_call", "income",
})

_DEBIT_STRATEGIES = frozenset({
    "call_debit", "put_debit", "debit_spreads",
    "butterfly_debit", "butterflies",
    "calendar_call_spread", "calendar_put_spread", "calendar_spread", "calendars",
    "long_call", "long_put",
})


def _detect_price_effect(trade: dict[str, Any]) -> str:
    """Determine CREDIT or DEBIT from strategy_id or explicit field.

    Input fields checked (in order):
        price_effect → strategy_id / strategy / spread_type
    """
    explicit = str(trade.get("price_effect") or "").upper()
    if explicit in ("CREDIT", "DEBIT"):
        return explicit

    sid = str(
        trade.get("strategy_id")
        or trade.get("strategy")
        or trade.get("spread_type")
        or ""
    ).strip().lower()

    if sid in _CREDIT_STRATEGIES:
        return "CREDIT"
    if sid in _DEBIT_STRATEGIES:
        return "DEBIT"

    # Fall back: if net_credit is set, it's credit; else assume debit
    if trade.get("net_credit") is not None:
        return "CREDIT"
    return "DEBIT"


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ── Side mapping ─────────────────────────────────────────────────────────

_SIDE_TO_TRADIER = {
    "sell":           "sell_to_open",
    "buy":            "buy_to_open",
    "sell_to_open":   "sell_to_open",
    "buy_to_open":    "buy_to_open",
    "sell_to_close":  "sell_to_close",
    "buy_to_close":   "buy_to_close",
}


def build_multileg_order(
    trade: dict[str, Any],
    account_mode: str = "paper",
    *,
    limit_price: float | None = None,
    quantity: int = 1,
    time_in_force: str = "day",
    tag: str | None = None,
) -> dict[str, Any]:
    """Build a Tradier-compatible multi-leg order payload.

    Parameters
    ----------
    trade : dict
        Trade dict with ``legs`` list.  Each leg must have:
            occ_symbol, side, qty/quantity
    account_mode : str
        "paper" or "live" — included in metadata, does NOT affect payload shape.
    limit_price : float | None
        Override limit price.  If None, derived from trade's net_credit/net_debit
        or spread_mid.
    quantity : int
        Number of spreads (default 1).
    time_in_force : str
        "day" or "gtc" (default "day").
    tag : str | None
        Optional order tag for Tradier (e.g. trade_key).

    Returns
    -------
    dict with:
        payload   : dict — the Tradier API form-encoded payload fields
        metadata  : dict — audit info (account_mode, price_effect, timestamp)
        legs_used : list — summary of legs included

    Raises
    ------
    ValueError
        If legs are missing or any leg lacks an OCC symbol.
    """
    legs = trade.get("legs")
    if not isinstance(legs, list) or len(legs) == 0:
        raise ValueError("Cannot build Tradier payload: no legs present")

    underlying = str(
        trade.get("underlying")
        or trade.get("underlying_symbol")
        or trade.get("symbol")
        or ""
    ).upper()
    if not underlying:
        raise ValueError("Cannot build Tradier payload: underlying symbol missing")

    price_effect = _detect_price_effect(trade)

    # Resolve limit price
    # Input fields: limit_price param → net_credit / net_debit → spread_mid
    effective_limit = limit_price
    if effective_limit is None:
        if price_effect == "CREDIT":
            effective_limit = _to_float(trade.get("net_credit")) or _to_float(trade.get("spread_mid"))
        else:
            effective_limit = _to_float(trade.get("net_debit")) or _to_float(trade.get("spread_mid"))
    if effective_limit is not None:
        effective_limit = round(abs(effective_limit), 2)

    # Build Tradier form-encoded payload with bracket-indexed legs.
    # Tradier multileg orders use type="credit"/"debit"/"even", NOT "limit".
    payload: dict[str, Any] = {
        "class": "multileg",
        "symbol": underlying,
        "duration": time_in_force.lower(),
    }
    if effective_limit is not None:
        payload["type"] = price_effect.lower()
        payload["price"] = str(round(effective_limit, 2))
    if tag:
        payload["tag"] = tag

    legs_used: list[dict[str, Any]] = []

    for i, leg in enumerate(legs):
        occ = str(leg.get("occ_symbol") or leg.get("option_symbol") or "").strip()
        if not occ:
            raise ValueError(f"leg[{i}]: OCC symbol is missing — cannot build Tradier payload")

        raw_side = str(leg.get("side") or "").lower()
        tradier_side = _SIDE_TO_TRADIER.get(raw_side, raw_side)
        if not tradier_side:
            raise ValueError(f"leg[{i}]: side is missing or invalid ({raw_side!r})")

        leg_qty = int(leg.get("qty") or leg.get("quantity") or 1) * quantity

        payload[f"side[{i}]"] = tradier_side
        payload[f"option_symbol[{i}]"] = occ
        payload[f"quantity[{i}]"] = str(leg_qty)

        legs_used.append({
            "index": i,
            "occ_symbol": occ,
            "side": tradier_side,
            "quantity": leg_qty,
            "strike": _to_float(leg.get("strike")),
            "right": str(leg.get("right") or leg.get("callput") or "").lower(),
        })

    metadata = {
        "account_mode": account_mode,
        "price_effect": price_effect,
        "limit_price": effective_limit,
        "underlying": underlying,
        "strategy_id": str(trade.get("strategy_id") or trade.get("strategy") or "").lower(),
        "expiration": str(trade.get("expiration") or ""),
        "leg_count": len(legs_used),
        "built_at": datetime.now(timezone.utc).isoformat(),
    }

    _log.info(
        "[TradierOrderBuilder] built payload: underlying=%s strategy=%s "
        "legs=%d price_effect=%s limit=%.2f mode=%s",
        underlying,
        metadata["strategy_id"],
        len(legs_used),
        price_effect,
        effective_limit or 0.0,
        account_mode,
    )
    _log.debug("[TradierOrderBuilder] multileg payload=%s", payload)

    return {
        "payload": payload,
        "metadata": metadata,
        "legs_used": legs_used,
    }
