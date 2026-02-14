from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.trading.models import OrderLeg, OrderTicket


@dataclass
class RiskResult:
    checks: dict[str, bool | float | int]
    warnings: list[str]


def _is_market_open(now: datetime | None = None) -> bool:
    ts = now or datetime.now(timezone.utc)
    if ts.weekday() >= 5:
        return False
    # Simple UTC window for US regular session: 14:30-21:00 UTC (approx, DST not handled)
    mins = ts.hour * 60 + ts.minute
    return 14 * 60 + 30 <= mins <= 21 * 60


def _spread_pct(leg: OrderLeg) -> float | None:
    if leg.bid is None or leg.ask is None:
        return None
    mid = (leg.bid + leg.ask) / 2
    if mid <= 0:
        return None
    return (leg.ask - leg.bid) / mid


def evaluate_preview_risk(
    *,
    settings: Settings,
    strategy: str,
    width: float,
    max_loss_per_spread: float,
    net_credit_or_debit: float,
    short_leg: OrderLeg,
    long_leg: OrderLeg,
    limit_price: float,
) -> RiskResult:
    checks: dict[str, bool | float | int] = {}
    warnings: list[str] = []

    checks["width_ok"] = width <= settings.MAX_WIDTH_DEFAULT
    checks["max_loss_ok"] = max_loss_per_spread <= settings.MAX_LOSS_PER_SPREAD_DEFAULT
    checks["legs_have_bid_ask"] = (
        short_leg.bid is not None
        and short_leg.ask is not None
        and long_leg.bid is not None
        and long_leg.ask is not None
    )

    is_credit = strategy in ("put_credit", "call_credit")
    checks["credit_floor_ok"] = (not is_credit) or (net_credit_or_debit >= settings.MIN_CREDIT_DEFAULT)

    short_spread_pct = _spread_pct(short_leg)
    checks["short_leg_spread_pct"] = short_spread_pct if short_spread_pct is not None else -1.0
    if short_spread_pct is not None and short_spread_pct > 0.10:
        warnings.append("Short leg bid-ask spread is wider than 10% of mid")

    if not _is_market_open():
        warnings.append("Market appears closed; fills may be delayed or less reliable")

    if is_credit:
        checks["limit_near_mid"] = limit_price <= net_credit_or_debit * 1.05
    else:
        checks["limit_near_mid"] = limit_price >= net_credit_or_debit * 0.95
    if not bool(checks["limit_near_mid"]):
        warnings.append("Limit price may be too aggressive relative to estimated spread mid")

    return RiskResult(checks=checks, warnings=warnings)


def evaluate_submit_freshness(ticket: OrderTicket, *, max_age_seconds: int) -> dict[str, bool | float]:
    now = datetime.now(timezone.utc)
    quote_age = (now - ticket.asof_quote_ts).total_seconds()
    chain_age = (now - ticket.asof_chain_ts).total_seconds()

    return {
        "quote_age_seconds": round(quote_age, 3),
        "chain_age_seconds": round(chain_age, 3),
        "data_fresh": quote_age <= max_age_seconds and chain_age <= max_age_seconds,
    }
