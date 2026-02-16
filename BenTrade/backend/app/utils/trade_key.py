from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


def normalize_strike(x: Any) -> str:
    if x in (None, ""):
        return "NA"

    try:
        value = Decimal(str(x).strip())
    except (InvalidOperation, ValueError):
        raw = str(x).strip()
        return raw if raw else "NA"

    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")

    if text in ("", "-0"):
        return "0"
    return text


def trade_key(
    underlying: Any,
    expiration: Any = None,
    spread_type: Any = None,
    short_strike: Any = None,
    long_strike: Any = None,
    dte: Any = None,
) -> str:
    underlying_value = str(underlying or "").strip().upper() or "NA"
    expiration_value = str(expiration).strip() if expiration not in (None, "") else "NA"
    spread_value = str(spread_type).strip() if spread_type not in (None, "") else "NA"

    short_value = normalize_strike(short_strike)
    long_value = normalize_strike(long_strike)

    if dte in (None, ""):
        dte_value = "NA"
    else:
        try:
            dte_number = float(dte)
            dte_value = str(int(dte_number)) if dte_number.is_integer() else str(dte_number)
        except (TypeError, ValueError):
            dte_value = str(dte).strip() or "NA"

    return f"{underlying_value}|{expiration_value}|{spread_value}|{short_value}|{long_value}|{dte_value}"
