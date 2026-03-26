from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.trading.models import OrderLeg, OrderTicket
from app.utils.market_hours import is_market_open


@dataclass
class RiskResult:
    checks: dict[str, bool | float | int]
    warnings: list[str]
    hard_failures: list[str]
    soft_warnings: list[str]


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
    expiration: str | None = None,
) -> RiskResult:
    checks: dict[str, bool | float | int] = {}
    warnings: list[str] = []
    hard_failures: list[str] = []
    soft_warnings: list[str] = []

    # ── HARD checks (block execution, no override) ──────────────────

    # Expiration must not be in the past
    if expiration:
        try:
            exp_date = datetime.strptime(expiration, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, tzinfo=timezone.utc
            )
            checks["expiration_valid"] = exp_date >= datetime.now(timezone.utc)
        except ValueError:
            checks["expiration_valid"] = False
        if not checks["expiration_valid"]:
            hard_failures.append(
                f"Options expired on {expiration} — cannot execute"
            )
    else:
        checks["expiration_valid"] = False
        hard_failures.append("Expiration date is missing — cannot evaluate risk")

    checks["legs_have_bid_ask"] = (
        short_leg.bid is not None
        and short_leg.ask is not None
        and long_leg.bid is not None
        and long_leg.ask is not None
    )
    if not checks["legs_have_bid_ask"]:
        hard_failures.append("legs_have_bid_ask")

    # ── SOFT checks (warn, allow override) ──────────────────────────
    checks["width_ok"] = width <= settings.MAX_WIDTH_DEFAULT
    if not checks["width_ok"]:
        soft_warnings.append(
            f"Spread width ${width:.0f} exceeds ${settings.MAX_WIDTH_DEFAULT:.0f} limit"
        )

    checks["max_loss_ok"] = max_loss_per_spread <= settings.MAX_LOSS_PER_SPREAD_DEFAULT
    if not checks["max_loss_ok"]:
        soft_warnings.append(
            f"Estimated max loss ${max_loss_per_spread:.0f} exceeds "
            f"${settings.MAX_LOSS_PER_SPREAD_DEFAULT:.0f} limit"
        )

    is_credit = strategy in ("put_credit", "call_credit", "iron_condor")
    checks["credit_floor_ok"] = (not is_credit) or (net_credit_or_debit >= settings.MIN_CREDIT_DEFAULT)
    if not checks["credit_floor_ok"]:
        soft_warnings.append(
            f"Net credit ${net_credit_or_debit:.2f} below "
            f"${settings.MIN_CREDIT_DEFAULT:.2f} minimum"
        )

    short_spread_pct = _spread_pct(short_leg)
    checks["short_leg_spread_pct"] = short_spread_pct if short_spread_pct is not None else -1.0
    if short_spread_pct is not None and short_spread_pct > 0.10:
        warnings.append("Short leg bid-ask spread is wider than 10% of mid")

    if not is_market_open():
        warnings.append("Market appears closed; fills may be delayed or less reliable")

    if is_credit:
        checks["limit_near_mid"] = limit_price <= net_credit_or_debit * 1.05
    else:
        checks["limit_near_mid"] = limit_price >= net_credit_or_debit * 0.95
    if not bool(checks["limit_near_mid"]):
        soft_warnings.append("Limit price may be too aggressive relative to estimated spread mid")

    return RiskResult(
        checks=checks,
        warnings=warnings,
        hard_failures=hard_failures,
        soft_warnings=soft_warnings,
    )


def evaluate_submit_freshness(ticket: OrderTicket, *, max_age_seconds: int) -> dict[str, bool | float]:
    now = datetime.now(timezone.utc)
    quote_age = (now - ticket.asof_quote_ts).total_seconds()
    chain_age = (now - ticket.asof_chain_ts).total_seconds()

    return {
        "quote_age_seconds": round(quote_age, 3),
        "chain_age_seconds": round(chain_age, 3),
        "data_fresh": quote_age <= max_age_seconds and chain_age <= max_age_seconds,
    }
