"""Position Management Enrichment — add management-level monitoring to active positions.

For each position (recommendation from the active trade pipeline), computes:
    - P&L progress toward profit target and stop loss
    - Distance to profit target and stop loss levels
    - Days held vs. original DTE
    - Management status classification
    - Suggested action with urgency and human-readable message

Design Choices
──────────────
    - Enrichment is ADDITIVE: all original recommendation fields pass through.
    - Management policies are strategy-class based (income / directional / butterfly).
    - Status is deterministic from numeric inputs — no model calls.
    - Profit target and stop loss are computed from entry price + policy,
      NOT from the engine's health score thresholds.
    - P&L direction aware: income positions profit when spread value decreases,
      directional positions profit when spread value increases.

Management Policies (default)
────────────────────────────
    income:       50% profit target, 2× credit stop loss, 7-day gamma warning
    directional:  75% of max profit target, 1× debit stop loss, 5-day warning
    butterfly:    50% of max profit target, 1× debit stop loss, 5-day warning
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

logger = logging.getLogger("bentrade.position_management")

# ── Strategy classification ──────────────────────────────────

# Strategies where premium is RECEIVED (profit when spread value decreases)
_INCOME_STRATEGIES = frozenset({
    "put_credit_spread",
    "call_credit_spread",
    "iron_condor",
    "iron_butterfly",
    "calendar_call_spread",
    "calendar_put_spread",
    "diagonal_call_spread",
    "diagonal_put_spread",
})

# Strategies where premium is PAID (profit when spread value increases)
_DIRECTIONAL_STRATEGIES = frozenset({
    "put_debit",
    "call_debit",
})

_BUTTERFLY_STRATEGIES = frozenset({
    "butterfly_debit",
})

# Stocks / equity positions
_EQUITY_TYPE = "equity"


# ── Default management policies ─────────────────────────────

MANAGEMENT_POLICIES: dict[str, dict[str, Any]] = {
    "income": {
        "profit_target_pct": 0.50,
        "stop_loss_multiplier": 2.0,
        "stop_loss_basis": "credit",
        "min_dte_to_manage": 7,
        "description": "Close at 50% of max profit; stop at 2× credit received",
    },
    "directional": {
        "profit_target_pct": 0.75,
        "stop_loss_multiplier": 1.0,
        "stop_loss_basis": "debit",
        "min_dte_to_manage": 5,
        "description": "Close at 75% of max profit; stop at 1× debit paid",
    },
    "butterfly": {
        "profit_target_pct": 0.50,
        "stop_loss_multiplier": 1.0,
        "stop_loss_basis": "debit",
        "min_dte_to_manage": 5,
        "description": "Close at 50% of max profit; stop at 1× debit paid",
    },
    "equity": {
        "profit_target_pct": 0.10,
        "stop_loss_multiplier": 0.07,
        "stop_loss_basis": "entry_price",
        "min_dte_to_manage": 0,
        "description": "Close at 10% gain; stop at 7% loss from entry",
    },
}


# ── Status constants ─────────────────────────────────────────

STATUS_AT_TARGET = "AT_TARGET"
STATUS_ON_TRACK = "ON_TRACK"
STATUS_NEUTRAL = "NEUTRAL"
STATUS_IN_DANGER = "IN_DANGER"
STATUS_AT_STOP = "AT_STOP"
STATUS_TIME_DECAY = "TIME_DECAY"
STATUS_EXPIRED = "EXPIRED"

ACTION_CLOSE = "CLOSE"
ACTION_HOLD = "HOLD"
ACTION_WATCH = "WATCH"


# ── Public API ───────────────────────────────────────────────

def classify_strategy(strategy_id: str | None, position_type: str | None = None) -> str:
    """Classify a position into a management strategy class.

    Returns one of: "income", "directional", "butterfly", "equity".
    """
    if position_type == _EQUITY_TYPE:
        return "equity"

    sid = (strategy_id or "").lower().strip()

    if sid in _INCOME_STRATEGIES:
        return "income"
    if sid in _DIRECTIONAL_STRATEGIES:
        return "directional"
    if sid in _BUTTERFLY_STRATEGIES:
        return "butterfly"

    # Fallback heuristics
    if "credit" in sid or "iron" in sid:
        return "income"
    if "debit" in sid or "call_" in sid or "put_" in sid:
        return "directional"
    if "butterfly" in sid:
        return "butterfly"
    if "calendar" in sid or "diagonal" in sid:
        return "income"  # treat time spreads as income

    return "income"  # safe default for options


def get_management_policy(strategy_class: str) -> dict[str, Any]:
    """Return the management policy for a strategy class."""
    return MANAGEMENT_POLICIES.get(strategy_class, MANAGEMENT_POLICIES["income"])


def enrich_recommendation_with_management(rec: dict[str, Any]) -> dict[str, Any]:
    """Add management-level monitoring to a normalized pipeline recommendation.

    Takes a recommendation dict from normalize_recommendation() and adds:
        - strategy_class, management_policy
        - profit_target_value, stop_loss_value
        - profit_progress_pct, loss_progress_pct
        - management_status, suggested_action
        - days_held, entry_dte

    Returns a NEW dict (does not mutate the input).
    """
    result = dict(rec)  # shallow copy — original fields pass through

    # ── Classify strategy ───────────────────────────────────────
    strategy_id = rec.get("strategy_id") or rec.get("strategy")
    position_type = rec.get("position_type")
    # Infer position_type from strategy if not explicit
    if not position_type:
        if strategy_id and strategy_id.lower() in ("equity", "stock"):
            position_type = "equity"
        elif rec.get("legs"):
            position_type = "options"
        else:
            position_type = "options"

    strategy_class = classify_strategy(strategy_id, position_type)
    policy = get_management_policy(strategy_class)

    result["strategy_class"] = strategy_class
    result["management_policy"] = {
        "profit_target_pct": policy["profit_target_pct"],
        "stop_loss_multiplier": policy["stop_loss_multiplier"],
    }

    # ── Extract position data ───────────────────────────────────
    snapshot = rec.get("position_snapshot") or {}
    entry_price = _to_float(snapshot.get("avg_open_price"))
    mark_price = _to_float(snapshot.get("mark_price"))
    unrealized_pnl = _to_float(snapshot.get("unrealized_pnl"))
    unrealized_pnl_pct = _to_float(snapshot.get("unrealized_pnl_pct"))

    dte = _to_int(rec.get("dte"))
    expiration = rec.get("expiration")

    # ── Compute DTE if not provided ─────────────────────────────
    if dte is None and expiration:
        dte = _compute_dte(expiration)

    # ── Equity positions: simple price-based management ─────────
    if strategy_class == "equity":
        mgmt = _enrich_equity(entry_price, mark_price, unrealized_pnl, policy)
        result.update(mgmt)
        result["dte"] = dte
        result["days_held"] = None  # equity: no expiration-based tracking
        result["entry_dte"] = None
        return result

    # ── Options positions ───────────────────────────────────────
    # For options, we need to determine credit received or debit paid
    # and compute management levels relative to those.

    if strategy_class == "income":
        mgmt = _enrich_income(entry_price, mark_price, unrealized_pnl, policy, snapshot)
    else:
        mgmt = _enrich_debit(entry_price, mark_price, unrealized_pnl, policy, snapshot)

    result.update(mgmt)

    # ── Time tracking ───────────────────────────────────────────
    result["dte"] = dte

    # Estimate entry_dte from date_acquired if available
    entry_dte = _to_int(rec.get("entry_dte")) or _to_int(rec.get("original_dte"))
    if entry_dte is None and expiration:
        # Try to estimate from the recommendation's internal data
        date_acquired = (rec.get("position_snapshot") or {}).get("date_acquired")
        if date_acquired:
            entry_dte = _dte_between(date_acquired, expiration)

    days_held = None
    if entry_dte is not None and dte is not None:
        days_held = max(0, entry_dte - dte)

    result["days_held"] = days_held
    result["entry_dte"] = entry_dte

    # ── Management status ───────────────────────────────────────
    profit_pct = result.get("profit_progress_pct") or 0
    loss_pct = result.get("loss_progress_pct") or 0

    status = determine_status(profit_pct, loss_pct, dte, strategy_class, policy)
    result["management_status"] = status

    action = suggest_action(status, profit_pct, loss_pct, dte,
                            result.get("total_pnl") or unrealized_pnl or 0)
    result["suggested_action"] = action

    return result


def enrich_all_recommendations(
    recommendations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Enrich a list of pipeline recommendations with management data."""
    return [enrich_recommendation_with_management(r) for r in recommendations]


def build_portfolio_summary(enriched: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate stats across all enriched positions.

    Input: list of enriched recommendations (output of enrich_all_recommendations).
    Output: portfolio-level summary dict.
    """
    total_pnl = 0.0
    status_counts: dict[str, int] = {}
    winning = 0
    losing = 0
    actions_needed = 0

    for r in enriched:
        pnl = _to_float(r.get("total_pnl")) or _to_float(
            (r.get("position_snapshot") or {}).get("unrealized_pnl")
        ) or 0
        total_pnl += pnl

        if pnl > 0:
            winning += 1
        elif pnl < 0:
            losing += 1

        ms = r.get("management_status") or "NEUTRAL"
        status_counts[ms] = status_counts.get(ms, 0) + 1

        if ms in (STATUS_AT_TARGET, STATUS_AT_STOP, STATUS_EXPIRED):
            actions_needed += 1

    return {
        "total_positions": len(enriched),
        "total_pnl": round(total_pnl, 2),
        "positions_at_target": status_counts.get(STATUS_AT_TARGET, 0),
        "positions_at_stop": status_counts.get(STATUS_AT_STOP, 0),
        "positions_in_danger": status_counts.get(STATUS_IN_DANGER, 0),
        "positions_on_track": status_counts.get(STATUS_ON_TRACK, 0),
        "positions_neutral": status_counts.get(STATUS_NEUTRAL, 0),
        "positions_time_decay": status_counts.get(STATUS_TIME_DECAY, 0),
        "positions_expired": status_counts.get(STATUS_EXPIRED, 0),
        "actions_needed": actions_needed,
        "winning": winning,
        "losing": losing,
        "status_distribution": status_counts,
    }


# ── Status determination ─────────────────────────────────────

def determine_status(
    profit_pct: float,
    loss_pct: float,
    dte: int | None,
    strategy_class: str,
    policy: dict[str, Any],
) -> str:
    """Determine the management status of a position.

    Returns one of:
        AT_TARGET   — profit target reached, should close
        ON_TRACK    — profitable, progressing toward target
        NEUTRAL     — near breakeven, within normal range
        IN_DANGER   — losing, approaching stop loss
        AT_STOP     — stop loss reached, should close
        TIME_DECAY  — profitable but running out of time (gamma zone)
        EXPIRED     — at or past expiration
    """
    if dte is not None and dte <= 0:
        return STATUS_EXPIRED

    if profit_pct >= 95:
        return STATUS_AT_TARGET

    if loss_pct >= 90:
        return STATUS_AT_STOP

    min_dte = policy.get("min_dte_to_manage", 7)
    if dte is not None and dte <= min_dte and strategy_class != "equity":
        if profit_pct >= 60:
            return STATUS_TIME_DECAY  # profitable but gamma zone
        return STATUS_IN_DANGER  # not profitable in gamma zone

    if loss_pct >= 50:
        return STATUS_IN_DANGER

    if profit_pct >= 30:
        return STATUS_ON_TRACK

    return STATUS_NEUTRAL


# ── Action suggestions ───────────────────────────────────────

def suggest_action(
    status: str,
    profit_pct: float,
    loss_pct: float,
    dte: int | None,
    total_pnl: float,
) -> dict[str, Any]:
    """Generate a human-readable action suggestion."""
    pnl_str = f"${abs(total_pnl):,.0f}" if total_pnl else "$0"
    dte_str = str(dte) if dte is not None else "?"

    actions: dict[str, dict[str, Any]] = {
        STATUS_AT_TARGET: {
            "action": ACTION_CLOSE,
            "urgency": "high",
            "message": f"Profit target reached ({profit_pct:.0f}%). Close to lock in {pnl_str} profit.",
        },
        STATUS_AT_STOP: {
            "action": ACTION_CLOSE,
            "urgency": "high",
            "message": f"Stop loss reached ({loss_pct:.0f}%). Close to limit loss at {pnl_str}.",
        },
        STATUS_ON_TRACK: {
            "action": ACTION_HOLD,
            "urgency": "low",
            "message": f"Position is {profit_pct:.0f}% toward target. Theta is working. Hold.",
        },
        STATUS_NEUTRAL: {
            "action": ACTION_HOLD,
            "urgency": "low",
            "message": "Near breakeven. Still early — let the trade develop.",
        },
        STATUS_IN_DANGER: {
            "action": ACTION_WATCH,
            "urgency": "medium",
            "message": f"Position losing ({loss_pct:.0f}% toward stop). Watch closely.",
        },
        STATUS_TIME_DECAY: {
            "action": ACTION_CLOSE,
            "urgency": "medium",
            "message": f"Profitable ({profit_pct:.0f}%) but entering gamma zone ({dte_str} DTE). Consider closing.",
        },
        STATUS_EXPIRED: {
            "action": ACTION_CLOSE,
            "urgency": "high",
            "message": "Position at or past expiration. Close or let expire.",
        },
    }

    return actions.get(status, {
        "action": ACTION_HOLD,
        "urgency": "low",
        "message": "No action needed.",
    })


# ── Internal enrichment helpers ──────────────────────────────

def _enrich_income(
    entry_price: float | None,
    mark_price: float | None,
    unrealized_pnl: float | None,
    policy: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Enrich an income (credit) position with management levels.

    Income positions profit when spread value decreases from entry.
    Credit received = entry_price (for correctly normalized positions).
    Profit target: close when spread value drops to (1 - profit_target_pct) × credit.
    Stop loss: close when spread value rises to (1 + stop_loss_multiplier) × credit.
    """
    result: dict[str, Any] = {}

    if entry_price is None or entry_price <= 0:
        return _null_management("entry_price missing or zero")

    credit = abs(entry_price)
    current = abs(mark_price) if mark_price is not None else None

    target_pct = policy["profit_target_pct"]
    stop_mult = policy["stop_loss_multiplier"]

    # Profit target: spread should decrease to this value
    # e.g., credit=1.00, target_pct=0.50 → target_value=0.50 (close when worth $0.50)
    profit_target_value = credit * (1.0 - target_pct)

    # Stop loss: spread should not increase beyond this value
    # e.g., credit=1.00, stop_mult=2.0 → stop_value=3.00 (stop when worth $3.00)
    stop_loss_value = credit * (1.0 + stop_mult)

    # Max achievable profit (in per-unit terms)
    max_profit = credit * target_pct  # e.g., $0.50

    # Max loss before stop
    max_loss = credit * stop_mult  # e.g., $2.00

    if current is not None:
        # P&L per unit: credit positions profit when current < entry
        pnl_per_unit = credit - current

        # Progress toward target: how much of max_profit have we captured?
        if max_profit > 0:
            profit_progress = max(0.0, min(100.0, (pnl_per_unit / max_profit) * 100))
        else:
            profit_progress = 0.0

        # Progress toward stop: how much of max_loss have we realized?
        if max_loss > 0:
            loss_progress = max(0.0, min(100.0, (-pnl_per_unit / max_loss) * 100))
        else:
            loss_progress = 0.0

        # Use pipeline's P&L if available (more accurate with multiplier/quantity)
        total_pnl = unrealized_pnl

        result["pnl_per_unit"] = round(pnl_per_unit, 4)
        result["profit_progress_pct"] = round(profit_progress, 1)
        result["loss_progress_pct"] = round(loss_progress, 1)
        result["total_pnl"] = round(total_pnl, 2) if total_pnl is not None else None
    else:
        result["pnl_per_unit"] = None
        result["profit_progress_pct"] = 0
        result["loss_progress_pct"] = 0
        result["total_pnl"] = unrealized_pnl

    result["profit_target_value"] = round(profit_target_value, 4)
    result["stop_loss_value"] = round(stop_loss_value, 4)
    result["max_profit_per_unit"] = round(max_profit, 4)
    result["max_loss_per_unit"] = round(max_loss, 4)

    return result


def _enrich_debit(
    entry_price: float | None,
    mark_price: float | None,
    unrealized_pnl: float | None,
    policy: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Enrich a debit (directional/butterfly) position with management levels.

    Debit positions profit when spread value increases from entry.
    Profit target: close when spread value reaches entry + (max_profit × target_pct).
    Stop loss: close when spread value drops to entry × (1 - stop_loss_multiplier).
    """
    result: dict[str, Any] = {}

    if entry_price is None or entry_price <= 0:
        return _null_management("entry_price missing or zero")

    debit = abs(entry_price)
    current = abs(mark_price) if mark_price is not None else None

    target_pct = policy["profit_target_pct"]
    stop_mult = policy["stop_loss_multiplier"]

    # For debit spreads, max theoretical profit depends on width
    # If width is available, max_profit = width - debit (per-unit)
    # Otherwise, estimate max_profit = debit (100% return)
    width = _to_float(snapshot.get("width"))
    if width and width > debit:
        theoretical_max = width - debit
    else:
        theoretical_max = debit  # fallback: 100% return as max

    max_profit = theoretical_max * target_pct

    # Stop loss: lose this fraction of debit paid
    max_loss = debit * stop_mult

    # Target value: spread should increase to this
    profit_target_value = debit + max_profit

    # Stop value: spread should not decrease below this
    stop_loss_value = max(0, debit - max_loss)

    if current is not None:
        pnl_per_unit = current - debit

        if max_profit > 0:
            profit_progress = max(0.0, min(100.0, (pnl_per_unit / max_profit) * 100))
        else:
            profit_progress = 0.0

        if max_loss > 0:
            loss_progress = max(0.0, min(100.0, (-pnl_per_unit / max_loss) * 100))
        else:
            loss_progress = 0.0

        total_pnl = unrealized_pnl

        result["pnl_per_unit"] = round(pnl_per_unit, 4)
        result["profit_progress_pct"] = round(profit_progress, 1)
        result["loss_progress_pct"] = round(loss_progress, 1)
        result["total_pnl"] = round(total_pnl, 2) if total_pnl is not None else None
    else:
        result["pnl_per_unit"] = None
        result["profit_progress_pct"] = 0
        result["loss_progress_pct"] = 0
        result["total_pnl"] = unrealized_pnl

    result["profit_target_value"] = round(profit_target_value, 4)
    result["stop_loss_value"] = round(stop_loss_value, 4)
    result["max_profit_per_unit"] = round(max_profit, 4)
    result["max_loss_per_unit"] = round(max_loss, 4)

    return result


def _enrich_equity(
    entry_price: float | None,
    mark_price: float | None,
    unrealized_pnl: float | None,
    policy: dict[str, Any],
) -> dict[str, Any]:
    """Enrich an equity (stock) position with management levels."""
    result: dict[str, Any] = {}

    if entry_price is None or entry_price <= 0:
        return _null_management("entry_price missing or zero")

    target_pct = policy["profit_target_pct"]  # e.g., 0.10 = 10%
    stop_mult = policy["stop_loss_multiplier"]  # e.g., 0.07 = 7%

    profit_target_value = entry_price * (1 + target_pct)
    stop_loss_value = entry_price * (1 - stop_mult)
    max_profit = entry_price * target_pct
    max_loss = entry_price * stop_mult

    if mark_price is not None:
        pnl_per_unit = mark_price - entry_price

        if max_profit > 0:
            profit_progress = max(0.0, min(100.0, (pnl_per_unit / max_profit) * 100))
        else:
            profit_progress = 0.0

        if max_loss > 0:
            loss_progress = max(0.0, min(100.0, (-pnl_per_unit / max_loss) * 100))
        else:
            loss_progress = 0.0

        result["pnl_per_unit"] = round(pnl_per_unit, 4)
        result["profit_progress_pct"] = round(profit_progress, 1)
        result["loss_progress_pct"] = round(loss_progress, 1)
        result["total_pnl"] = round(unrealized_pnl, 2) if unrealized_pnl is not None else None
    else:
        result["pnl_per_unit"] = None
        result["profit_progress_pct"] = 0
        result["loss_progress_pct"] = 0
        result["total_pnl"] = unrealized_pnl

    result["profit_target_value"] = round(profit_target_value, 4)
    result["stop_loss_value"] = round(stop_loss_value, 4)
    result["max_profit_per_unit"] = round(max_profit, 4)
    result["max_loss_per_unit"] = round(max_loss, 4)

    # Status for equity
    status = determine_status(
        result.get("profit_progress_pct", 0),
        result.get("loss_progress_pct", 0),
        None,  # equity has no expiration
        "equity",
        policy,
    )
    result["management_status"] = status
    result["suggested_action"] = suggest_action(
        status,
        result.get("profit_progress_pct", 0),
        result.get("loss_progress_pct", 0),
        None,
        result.get("total_pnl") or unrealized_pnl or 0,
    )

    return result


def _null_management(reason: str) -> dict[str, Any]:
    """Return null management fields when enrichment isn't possible."""
    return {
        "profit_target_value": None,
        "stop_loss_value": None,
        "profit_progress_pct": None,
        "loss_progress_pct": None,
        "max_profit_per_unit": None,
        "max_loss_per_unit": None,
        "pnl_per_unit": None,
        "total_pnl": None,
        "management_status": STATUS_NEUTRAL,
        "suggested_action": {
            "action": ACTION_HOLD,
            "urgency": "low",
            "message": f"Management levels unavailable: {reason}.",
        },
    }


# ── Utility helpers ──────────────────────────────────────────

def _to_float(val: Any) -> float | None:
    """Safely convert to float, returning None on failure."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _to_int(val: Any) -> int | None:
    """Safely convert to int, returning None on failure."""
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _compute_dte(expiration: str | None) -> int | None:
    """Compute days-to-expiration from an expiration date string."""
    if not expiration:
        return None
    try:
        exp_date = datetime.strptime(str(expiration)[:10], "%Y-%m-%d").date()
        today = date.today()
        return max(0, (exp_date - today).days)
    except (ValueError, TypeError):
        return None


def _dte_between(start_date_str: str, end_date_str: str) -> int | None:
    """Compute days between two date strings."""
    try:
        start = datetime.strptime(str(start_date_str)[:10], "%Y-%m-%d").date()
        end = datetime.strptime(str(end_date_str)[:10], "%Y-%m-%d").date()
        return max(0, (end - start).days)
    except (ValueError, TypeError):
        return None
