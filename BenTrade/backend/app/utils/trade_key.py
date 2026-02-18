from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


_SPREAD_TYPE_ALIASES: dict[str, str] = {
    "put_credit_spread": "put_credit_spread",
    "put_credit": "put_credit_spread",
    "credit_put_spread": "put_credit_spread",
    "call_credit_spread": "call_credit_spread",
    "call_credit": "call_credit_spread",
    "credit_call_spread": "call_credit_spread",
    "put_debit": "put_debit",
    "debit_put_spread": "put_debit",
    "call_debit": "call_debit",
    "debit_call_spread": "call_debit",
    "cash_secured_put": "csp",
    "csp": "csp",
    "covered_call": "covered_call",
    "debit_call_butterfly": "butterfly_debit",
    "debit_put_butterfly": "butterfly_debit",
    "debit_butterfly": "butterfly_debit",
    "butterfly_debit": "butterfly_debit",
    "butterflies": "butterfly_debit",
    "calendar_spread": "calendar_spread",
    "calendar_call_spread": "calendar_call_spread",
    "calendar_put_spread": "calendar_put_spread",
    "iron_butterfly": "iron_butterfly",
    "single": "single",
    "long_call": "long_call",
    "long_put": "long_put",
    "income": "income",
}

CANONICAL_STRATEGY_IDS: set[str] = {
    "put_credit_spread",
    "call_credit_spread",
    "put_debit",
    "call_debit",
    "iron_condor",
    "butterfly_debit",
    "iron_butterfly",
    "calendar_spread",
    "calendar_call_spread",
    "calendar_put_spread",
    "income",
    "csp",
    "covered_call",
    "single",
    "long_call",
    "long_put",
}


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


def canonicalize_spread_type(value: Any) -> str | None:
    canonical, _, _ = canonicalize_strategy_id(value)
    return canonical


def canonicalize_strategy_id(value: Any) -> tuple[str | None, bool, str]:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None, False, ""

    mapped = _SPREAD_TYPE_ALIASES.get(normalized, normalized)
    if mapped not in CANONICAL_STRATEGY_IDS:
        return None, False, normalized
    return mapped, mapped != normalized, normalized


def canonicalize_strategy_or_na(value: Any) -> str:
    canonical, _, _ = canonicalize_strategy_id(value)
    return canonical or "NA"


def canonicalize_trade_identity(
    *,
    underlying: Any,
    expiration: Any,
    strategy_id: Any,
    short_strike: Any,
    long_strike: Any,
    dte: Any,
) -> dict[str, Any]:
    canonical_strategy, alias_mapped, provided_strategy = canonicalize_strategy_id(strategy_id)
    key = trade_key(
        underlying=underlying,
        expiration=expiration,
        spread_type=canonical_strategy,
        short_strike=short_strike,
        long_strike=long_strike,
        dte=dte,
    )
    return {
        "strategy_id": canonical_strategy,
        "alias_mapped": alias_mapped,
        "provided_strategy": provided_strategy,
        "trade_key": key,
    }


def is_canonical_trade_key(value: Any) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    return raw == canonicalize_trade_key(raw)


def canonicalize_trade_key(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parts = raw.split("|")
    if len(parts) != 6:
        return raw
    return trade_key(
        underlying=parts[0],
        expiration=parts[1],
        spread_type=parts[2],
        short_strike=parts[3],
        long_strike=parts[4],
        dte=parts[5],
    )


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
    spread_value = canonicalize_strategy_or_na(spread_type)

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
