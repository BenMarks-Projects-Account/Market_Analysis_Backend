from __future__ import annotations

import math
import re
from datetime import date, datetime, timezone
from typing import Any

_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9.\-]{1,10}$")


def validate_symbol(symbol: Any) -> str | None:
    value = str(symbol or "").strip().upper()
    if not value:
        return None
    if not _SYMBOL_PATTERN.fullmatch(value):
        return None
    return value


def parse_expiration(expiration: Any, *, today: date | None = None) -> tuple[str | None, int | None]:
    value = str(expiration or "").strip()
    if not value:
        return None, None
    try:
        exp_date = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None, None

    ref_date = today or datetime.now(timezone.utc).date()
    dte = (exp_date - ref_date).days
    if dte < 0:
        return None, dte
    return value, dte


def is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(parsed)


def validate_bid_ask(bid: Any, ask: Any) -> tuple[float | None, float | None, list[str]]:
    warnings: list[str] = []

    bid_value: float | None = None
    ask_value: float | None = None

    if bid not in (None, ""):
        if not is_finite_number(bid):
            warnings.append("BID_NOT_FINITE")
        else:
            bid_value = float(bid)
            if bid_value < 0:
                warnings.append("BID_NEGATIVE")
                bid_value = None

    if ask not in (None, ""):
        if not is_finite_number(ask):
            warnings.append("ASK_NOT_FINITE")
        else:
            ask_value = float(ask)
            if ask_value < 0:
                warnings.append("ASK_NEGATIVE")
                ask_value = None

    if bid_value is not None and ask_value is not None and ask_value < bid_value:
        warnings.append("ASK_LT_BID")
        bid_value = None
        ask_value = None

    return bid_value, ask_value, warnings


def clamp(
    value: Any,
    *,
    minimum: float | int | None = None,
    maximum: float | int | None = None,
    field: str = "value",
    warning_code: str = "CLAMPED",
) -> tuple[float | int | None, str | None]:
    if value is None:
        return None, None
    if not is_finite_number(value):
        return None, f"{warning_code}:{field}:non_finite"

    parsed = float(value)
    if minimum is not None and parsed < float(minimum):
        return minimum, f"{warning_code}:{field}:min"
    if maximum is not None and parsed > float(maximum):
        return maximum, f"{warning_code}:{field}:max"

    if isinstance(value, int):
        return int(parsed), None
    return parsed, None
