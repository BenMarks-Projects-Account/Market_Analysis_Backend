"""Execution Pre-Flight Validator — institutional safety gate.

If the order is even slightly malformed, it does not leave the building.

This module validates a trade/ticket before it can be submitted for
execution (preview or submit).  It checks:

  Leg integrity:
    - OCC symbol present on every leg
    - quantity > 0
    - strike valid (positive number)
    - expiration valid (non-empty, parseable date string)

  Pricing sanity:
    - net_credit or net_debit present
    - max_loss > 0
    - spread_mid not wildly inconsistent (optional)
    - bid/ask available on short leg

  Liquidity (non-blocking warnings):
    - open_interest == 0
    - volume very low

Usage:
    from app.trading.execution_validator import validate_trade_for_execution

    result = validate_trade_for_execution(trade_dict)
    if not result["valid"]:
        # block execution
    for w in result["warnings"]:
        # display in UI
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

_log = logging.getLogger("bentrade.execution_validator")

# Minimum OI threshold for liquidity warning (non-blocking).
_LOW_OI_THRESHOLD = 10
# Minimum volume threshold for liquidity warning (non-blocking).
_LOW_VOLUME_THRESHOLD = 5
# OCC symbol format:  e.g.  SPY250321P00550000  (root + YYMMDD + P/C + 8-digit price)
_OCC_PATTERN = re.compile(r"^[A-Z]{1,6}\d{6}[PC]\d{8}$")


def _to_float(value: Any) -> float | None:
    """Safe float conversion."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _validate_leg(
    leg: dict[str, Any],
    index: int,
    *,
    blocking_errors: list[str],
    warnings: list[str],
) -> None:
    """Validate a single leg dict and append errors/warnings."""
    prefix = f"leg[{index}]"

    # OCC symbol
    occ = leg.get("occ_symbol") or leg.get("option_symbol") or leg.get("optionSymbol")
    if not occ or not str(occ).strip():
        blocking_errors.append(f"{prefix}: OCC symbol missing")
    else:
        occ_str = str(occ).strip()
        if not _OCC_PATTERN.match(occ_str):
            warnings.append(f"{prefix}: OCC symbol '{occ_str}' does not match standard format")

    # Quantity
    qty = leg.get("qty") or leg.get("quantity")
    qty_val = _to_float(qty)
    if qty_val is None or qty_val <= 0:
        blocking_errors.append(f"{prefix}: quantity must be > 0 (got {qty!r})")

    # Strike
    strike = _to_float(leg.get("strike"))
    if strike is None or strike <= 0:
        blocking_errors.append(f"{prefix}: strike must be a positive number (got {leg.get('strike')!r})")

    # Expiration
    exp = str(leg.get("expiration") or "").strip()
    if not exp:
        blocking_errors.append(f"{prefix}: expiration is missing")
    else:
        try:
            datetime.strptime(exp, "%Y-%m-%d")
        except ValueError:
            warnings.append(f"{prefix}: expiration '{exp}' could not be parsed as YYYY-MM-DD")

    # Side
    side = str(leg.get("side") or "").lower()
    valid_sides = {"buy", "sell", "buy_to_open", "sell_to_open", "buy_to_close", "sell_to_close"}
    if side not in valid_sides:
        blocking_errors.append(f"{prefix}: side '{side}' is not valid")

    # Right (put/call)
    right = str(leg.get("right") or leg.get("callput") or leg.get("option_type") or "").lower()
    if right not in ("put", "call"):
        blocking_errors.append(f"{prefix}: right must be 'put' or 'call' (got {right!r})")

    # Liquidity warnings (non-blocking)
    oi = _to_float(leg.get("open_interest"))
    vol = _to_float(leg.get("volume"))
    if oi is not None and oi == 0:
        warnings.append(f"{prefix}: open_interest is 0")
    elif oi is not None and oi < _LOW_OI_THRESHOLD:
        warnings.append(f"{prefix}: open_interest is low ({int(oi)})")
    if vol is not None and vol == 0:
        warnings.append(f"{prefix}: volume is 0")
    elif vol is not None and vol < _LOW_VOLUME_THRESHOLD:
        warnings.append(f"{prefix}: volume is low ({int(vol)})")

    # Bid/Ask on sell legs
    if side in ("sell", "sell_to_open"):
        bid = _to_float(leg.get("bid"))
        ask = _to_float(leg.get("ask"))
        if bid is None:
            warnings.append(f"{prefix}: bid missing on short leg")
        if ask is None:
            warnings.append(f"{prefix}: ask missing on short leg")


def validate_trade_for_execution(trade: dict[str, Any]) -> dict[str, Any]:
    """Validate a trade dict for execution readiness.

    Parameters
    ----------
    trade : dict
        A trade dictionary (raw, normalized, or ticket-shaped).
        Must contain ``legs`` list and pricing fields.

    Returns
    -------
    dict with keys:
        valid            : bool — True if no blocking errors
        blocking_errors  : list[str] — hard failures that block execution
        warnings         : list[str] — advisory issues (non-blocking)
    """
    blocking_errors: list[str] = []
    warnings: list[str] = []

    # ── Leg integrity ────────────────────────────────────────────────
    legs = trade.get("legs")
    if not isinstance(legs, list) or len(legs) == 0:
        blocking_errors.append("No legs present — cannot validate")
    else:
        for i, leg in enumerate(legs):
            if not isinstance(leg, dict):
                blocking_errors.append(f"leg[{i}]: not a dict")
                continue
            _validate_leg(leg, i, blocking_errors=blocking_errors, warnings=warnings)

    # ── Pricing sanity ───────────────────────────────────────────────
    net_credit = _to_float(trade.get("net_credit"))
    net_debit = _to_float(trade.get("net_debit"))
    has_cashflow = (net_credit is not None and net_credit > 0) or \
                   (net_debit is not None and net_debit > 0)
    if not has_cashflow:
        # Also check computed_metrics
        cm = trade.get("computed_metrics") if isinstance(trade.get("computed_metrics"), dict) else {}
        nc_cm = _to_float(cm.get("net_credit"))
        nd_cm = _to_float(cm.get("net_debit"))
        has_cashflow = (nc_cm is not None and nc_cm > 0) or \
                       (nd_cm is not None and nd_cm > 0)
    if not has_cashflow:
        blocking_errors.append("net_credit/net_debit not present or not positive")

    # Max loss
    max_loss = _to_float(trade.get("max_loss")) or \
               _to_float(trade.get("max_loss_per_share")) or \
               _to_float((trade.get("computed") or {}).get("max_loss")) or \
               _to_float((trade.get("computed_metrics") or {}).get("max_loss"))
    if max_loss is None or max_loss <= 0:
        blocking_errors.append(f"max_loss must be > 0 (got {max_loss!r})")

    # Spread mid consistency check
    spread_mid = _to_float(trade.get("spread_mid"))
    if spread_mid is not None and net_credit is not None:
        # For credit spreads, spread_mid should be reasonably close to net_credit
        delta = abs(spread_mid - net_credit)
        if net_credit > 0 and delta / net_credit > 0.50:
            warnings.append(
                f"spread_mid ({spread_mid:.4f}) differs from net_credit "
                f"({net_credit:.4f}) by {delta/net_credit:.0%}"
            )

    # ── Negative natural price (BLOCKING) ────────────────────────
    # If normalize.py flagged NEGATIVE_NATURAL_PRICE, block execution.
    vw = trade.get("validation_warnings")
    if isinstance(vw, list) and "NEGATIVE_NATURAL_PRICE" in vw:
        blocking_errors.append(
            "spread_natural is negative (inverted market) — unsafe to execute"
        )

    # Short leg bid/ask check
    if isinstance(legs, list):
        for i, leg in enumerate(legs):
            if not isinstance(leg, dict):
                continue
            side = str(leg.get("side") or "").lower()
            if side in ("sell", "sell_to_open"):
                bid = _to_float(leg.get("bid"))
                if bid is None or bid <= 0:
                    blocking_errors.append(f"leg[{i}]: short leg bid missing or zero — unsafe to sell")

    # ── Build result ─────────────────────────────────────────────────
    valid = len(blocking_errors) == 0
    trade_id = trade.get("trade_key") or trade.get("trade_id") or "unknown"

    _log.info(
        "[ExecutionValidator] trade_id=%s valid=%s blocking=%d warnings=%s",
        trade_id, valid, len(blocking_errors), warnings,
    )
    if not valid:
        _log.warning(
            "[ExecutionValidator] BLOCKED trade_id=%s errors=%s",
            trade_id, blocking_errors,
        )

    return {
        "valid": valid,
        "blocking_errors": blocking_errors,
        "warnings": warnings,
    }
