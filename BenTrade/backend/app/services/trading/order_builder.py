"""Clean payload generator for Tradier multi-leg orders.

Converts the internal trade ticket format into Tradier's exact API schema.
This is the single authoritative function for building Tradier payloads
from trade ticket objects (as opposed to tradier_order_builder.py which
works with raw trade dicts from the scanner).

Tradier multi-leg order form-encoded format:
    {
        "class": "multileg",
        "symbol": "IWM",
        "type": "credit",   # or "debit" — NOT "limit"
        "duration": "day",
        "price": "0.25",
        "side[0]": "sell_to_open",
        "option_symbol[0]": "IWM260309P00255000",
        "quantity[0]": "1",
        "side[1]": "buy_to_open",
        "option_symbol[1]": "IWM260309P00254000",
        "quantity[1]": "1",
    }
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any

logger = logging.getLogger("bentrade.order_builder")

# Tradier expects lowercase side values
_SIDE_MAP = {
    "SELL_TO_OPEN": "sell_to_open",
    "BUY_TO_OPEN": "buy_to_open",
    "SELL_TO_CLOSE": "sell_to_close",
    "BUY_TO_CLOSE": "buy_to_close",
    "sell_to_open": "sell_to_open",
    "buy_to_open": "buy_to_open",
    "sell_to_close": "sell_to_close",
    "buy_to_close": "buy_to_close",
    "sell": "sell_to_open",
    "buy": "buy_to_open",
}

# OCC symbol format: ROOT(1-6 uppercase) + YYMMDD + P/C + 8-digit strike
_OCC_PATTERN = re.compile(r"^[A-Z]{1,6}\d{6}[PC]\d{8}$")


def validate_occ_symbol(symbol: str) -> str | None:
    """Validate OCC symbol format. Returns error message or None if valid."""
    if not symbol or not symbol.strip():
        return "OCC symbol is empty"
    # Strip all whitespace including embedded spaces
    # (Tradier occasionally returns "SPY 260419C00450000" with a space)
    cleaned = "".join(symbol.split())
    if not _OCC_PATTERN.match(cleaned):
        return f"OCC symbol format invalid: {symbol!r} — expected ROOT+YYMMDD+P/C+8digits"
    return None


def build_tradier_multileg_order(
    trade_ticket: dict[str, Any],
    *,
    preview: bool = False,
) -> dict[str, Any]:
    """Convert internal trade ticket to Tradier multi-leg order payload.

    Parameters
    ----------
    trade_ticket : dict
        Internal trade structure with keys:
            strategy  – e.g. "credit_spread", "put_credit", etc.
            symbol    – underlying symbol e.g. "IWM"
            limit_price – per-spread limit price (float)
            duration  – "day" or "gtc" (default "day")
            legs      – list of leg dicts, each with:
                side     – "sell_to_open", "buy_to_open", etc.
                symbol   – OCC option symbol
                qty      – number of contracts (int)
    preview : bool
        If True, adds preview=true to the payload so Tradier validates
        without placing the order.

    Returns
    -------
    dict[str, Any]
        JSON payload matching Tradier's POST /accounts/{id}/orders

    Raises
    ------
    ValueError
        If required fields are missing or invalid.
    """
    # Resolve underlying symbol
    # Input fields: symbol, underlying
    underlying = str(
        trade_ticket.get("symbol")
        or trade_ticket.get("underlying")
        or ""
    ).strip().upper()
    if not underlying:
        raise ValueError("Missing required field: symbol (underlying)")

    # Resolve limit price
    # Input fields: limit_price
    limit_price = trade_ticket.get("limit_price")
    if limit_price is None:
        raise ValueError("Missing required field: limit_price")
    try:
        limit_price = float(limit_price)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid limit_price: {trade_ticket.get('limit_price')!r}") from exc
    if limit_price <= 0:
        raise ValueError(f"limit_price must be positive, got {limit_price}")

    # Resolve duration
    # Input fields: duration, time_in_force
    duration = str(
        trade_ticket.get("duration")
        or trade_ticket.get("time_in_force")
        or "day"
    ).strip().lower()
    if duration not in ("day", "gtc"):
        raise ValueError(f"Invalid duration: {duration!r} — must be 'day' or 'gtc'")

    # Resolve legs
    legs = trade_ticket.get("legs")
    if not isinstance(legs, list) or len(legs) < 2:
        raise ValueError(f"Multi-leg order requires at least 2 legs, got {len(legs) if isinstance(legs, list) else 0}")

    # Detect credit vs debit for Tradier multileg type field.
    # Tradier multileg orders use type="credit"/"debit"/"even", NOT "limit".
    # Input fields: price_effect, strategy
    price_effect = str(trade_ticket.get("price_effect") or "").upper()
    if price_effect not in ("CREDIT", "DEBIT"):
        strategy = str(trade_ticket.get("strategy") or "").lower()
        if "credit" in strategy:
            price_effect = "CREDIT"
        elif "debit" in strategy:
            price_effect = "DEBIT"
        else:
            price_effect = "DEBIT"  # safe default

    # Build payload — Tradier multi-leg order form-encoded schema
    # Formula: { class: multileg, type: credit|debit, side[i], option_symbol[i], quantity[i] }
    payload: dict[str, Any] = {
        "class": "multileg",
        "symbol": underlying,
        "type": price_effect.lower(),
        "duration": duration,
        "price": str(round(limit_price, 2)),
    }

    for i, leg in enumerate(legs):
        # Resolve OCC symbol
        # Input fields: symbol, occ_symbol, option_symbol
        occ = str(
            leg.get("symbol")
            or leg.get("occ_symbol")
            or leg.get("option_symbol")
            or ""
        )
        # Strip all whitespace (Tradier sometimes returns spaced OCC symbols)
        occ = "".join(occ.split())
        occ_error = validate_occ_symbol(occ)
        if occ_error:
            raise ValueError(f"leg[{i}]: {occ_error}")

        # Resolve side
        raw_side = str(leg.get("side") or "").strip()
        tradier_side = _SIDE_MAP.get(raw_side)
        if not tradier_side:
            raise ValueError(
                f"leg[{i}]: invalid side {raw_side!r} — "
                f"expected one of: {', '.join(sorted(set(_SIDE_MAP.values())))}"
            )

        # Resolve quantity
        qty = leg.get("qty") or leg.get("quantity") or 1
        try:
            qty = int(qty)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"leg[{i}]: invalid quantity {leg.get('qty')!r}") from exc
        if qty < 1:
            raise ValueError(f"leg[{i}]: quantity must be >= 1, got {qty}")

        payload[f"side[{i}]"] = tradier_side
        payload[f"option_symbol[{i}]"] = occ
        payload[f"quantity[{i}]"] = str(qty)

    if preview:
        payload["preview"] = "true"

    logger.debug("event=build_tradier_multileg_order payload=%s", payload)

    return payload


def build_occ_symbol(
    root: str,
    expiration: str | date,
    strike: float,
    option_type: str,
) -> str:
    """Construct an OCC option symbol from components.

    OCC format: ROOT(1-6 uppercase) + YYMMDD + P/C + 8-digit strike
    Input fields: root (underlying), expiration (YYYY-MM-DD or date),
                  strike (float), option_type ("put"/"call"/"P"/"C")
    Formula: f"{root.upper()}{yy}{mm}{dd}{pc}{strike*1000:08d}"

    Examples:
        build_occ_symbol("IWM", "2026-03-09", 255.0, "put")
        → "IWM260309P00255000"
    """
    root = root.strip().upper()
    if not root or len(root) > 6:
        raise ValueError(f"OCC root must be 1-6 uppercase chars, got {root!r}")

    # Parse expiration → YYMMDD
    if isinstance(expiration, str):
        try:
            exp_date = datetime.strptime(expiration, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError(
                f"Expiration must be YYYY-MM-DD, got {expiration!r}"
            ) from exc
    elif isinstance(expiration, date):
        exp_date = expiration
    else:
        raise ValueError(f"Expiration must be str or date, got {type(expiration)}")
    yymmdd = exp_date.strftime("%y%m%d")

    # Option type → P or C
    ot = option_type.strip().upper()
    if ot in ("PUT", "P"):
        pc = "P"
    elif ot in ("CALL", "C"):
        pc = "C"
    else:
        raise ValueError(f"option_type must be 'put'/'call'/'P'/'C', got {option_type!r}")

    # Strike → 8-digit integer (strike * 1000, zero-padded)
    strike_int = int(round(strike * 1000))
    if strike_int <= 0:
        raise ValueError(f"Strike must be positive, got {strike}")
    strike_str = f"{strike_int:08d}"

    occ = f"{root}{yymmdd}{pc}{strike_str}"
    err = validate_occ_symbol(occ)
    if err:
        raise ValueError(f"Constructed OCC symbol failed validation: {err}")
    return occ


# Strategy → (option_type, short_side, long_side)
_STRATEGY_MAP = {
    "put_credit":  ("put",  "sell_to_open", "buy_to_open"),
    "call_credit": ("call", "sell_to_open", "buy_to_open"),
    "put_debit":   ("put",  "sell_to_open", "buy_to_open"),
    "call_debit":  ("call", "sell_to_open", "buy_to_open"),
}


def build_multileg_credit_spread(
    request: dict[str, Any],
    *,
    preview: bool = False,
) -> dict[str, Any]:
    """Build a Tradier multileg order payload from a UI trade ticket request.

    Constructs OCC symbols from the request fields instead of requiring
    pre-built OCC symbols.  This is the authoritative translator from
    BenTrade's internal trade ticket → Tradier POST body.

    Input fields:
        symbol       – underlying symbol (e.g. "IWM")
        strategy     – "put_credit" | "call_credit" | "put_debit" | "call_debit"
        expiration   – YYYY-MM-DD (e.g. "2026-03-09")
        short_strike – strike of the sold leg (float)
        long_strike  – strike of the bought leg (float)
        quantity     – number of spreads (int, ≥1)
        limit_price  – per-spread limit price (float, >0)
        time_in_force – "DAY" or "GTC" (default "DAY")

    Formula:
        OCC = ROOT + YYMMDD + P/C + (strike*1000 zero-padded to 8)
        payload = {class:"multileg", symbol, type:"limit", duration,
                   price (numeric), legs: [{option_symbol, side, quantity}]}

    Returns dict[str, Any] ready for JSON POST to
    /v1/accounts/{account_id}/orders
    """
    symbol = str(request.get("symbol") or "").strip().upper()
    if not symbol:
        raise ValueError("Missing required field: symbol")

    strategy = str(request.get("strategy") or "").strip().lower()
    if strategy not in _STRATEGY_MAP:
        raise ValueError(
            f"Unknown strategy {strategy!r}, "
            f"expected one of: {', '.join(_STRATEGY_MAP)}"
        )

    expiration = str(request.get("expiration") or "").strip()
    if not expiration:
        raise ValueError("Missing required field: expiration")

    short_strike = request.get("short_strike")
    long_strike = request.get("long_strike")
    if short_strike is None or long_strike is None:
        raise ValueError("Missing required fields: short_strike and long_strike")
    try:
        short_strike = float(short_strike)
        long_strike = float(long_strike)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid strike values: short={short_strike!r}, long={long_strike!r}") from exc

    quantity = int(request.get("quantity", 1))
    if quantity < 1:
        raise ValueError(f"quantity must be ≥ 1, got {quantity}")

    limit_price = request.get("limit_price")
    if limit_price is None:
        raise ValueError("Missing required field: limit_price")
    limit_price = float(limit_price)
    if limit_price <= 0:
        raise ValueError(f"limit_price must be positive, got {limit_price}")

    duration = str(request.get("time_in_force") or request.get("duration") or "day").strip().lower()
    if duration not in ("day", "gtc"):
        raise ValueError(f"Invalid duration: {duration!r}")

    # Resolve strategy components
    option_type, short_side, long_side = _STRATEGY_MAP[strategy]

    # Construct OCC symbols from components
    short_occ = build_occ_symbol(symbol, expiration, short_strike, option_type)
    long_occ = build_occ_symbol(symbol, expiration, long_strike, option_type)

    # Build Tradier form-encoded payload
    # Tradier multileg orders use type="credit"/"debit", NOT "limit".
    tradier_type = "credit" if "credit" in strategy else "debit"
    payload: dict[str, Any] = {
        "class": "multileg",
        "symbol": symbol,
        "type": tradier_type,
        "duration": duration,
        "price": str(round(limit_price, 2)),
        "side[0]": short_side,
        "option_symbol[0]": short_occ,
        "quantity[0]": str(quantity),
        "side[1]": long_side,
        "option_symbol[1]": long_occ,
        "quantity[1]": str(quantity),
    }

    if preview:
        payload["preview"] = "true"

    logger.debug("event=build_multileg_credit_spread payload=%s", payload)

    return payload
