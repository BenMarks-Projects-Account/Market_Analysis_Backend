from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Strategy classification helpers
# ---------------------------------------------------------------------------

# Strategies where the dominant cashflow is a CREDIT received up-front.
_CREDIT_STRATEGIES: frozenset[str] = frozenset({
    "put_credit_spread", "call_credit_spread",
    "credit_spread",  # generic alias
    "iron_condor", "iron_butterfly",
    "csp", "covered_call", "income",
    "single",  # CSP / covered-call singles
})

# Strategies where the dominant cashflow is a DEBIT paid up-front.
_DEBIT_STRATEGIES: frozenset[str] = frozenset({
    "call_debit", "put_debit",
    "debit_spreads",  # generic alias
    "butterfly_debit", "butterflies",
    "calendar_call_spread", "calendar_put_spread", "calendar_spread",
    "calendars",  # generic alias
    "long_call", "long_put",
})


def is_credit_strategy(strategy_id: str | None) -> bool:
    """Return True when strategy_id is a credit-based strategy."""
    return (strategy_id or "").strip().lower() in _CREDIT_STRATEGIES


def is_debit_strategy(strategy_id: str | None) -> bool:
    """Return True when strategy_id is a debit-based strategy."""
    return (strategy_id or "").strip().lower() in _DEBIT_STRATEGIES


# ---------------------------------------------------------------------------
# normalize_spread_cashflows — single source of truth for credit / debit
# ---------------------------------------------------------------------------

def normalize_spread_cashflows(
    strategy_id: str | None,
    net_value: float | None,
    *,
    width: float | None = None,
) -> dict[str, float | None | list[str]]:
    """Return canonical ``net_credit``, ``net_debit``, and any ``validation_warnings``.

    Rules
    -----
    * Credit strategy  → ``net_credit = net_value``, ``net_debit = None``
    * Debit strategy   → ``net_debit  = net_value``, ``net_credit = None``
    * Unknown strategy → keep both None, emit warning.

    Invariant checks (produce warnings, never silently drop):
    * 0 < net_credit < width  (credit)
    * 0 < net_debit  < width  (debit)

    Parameters
    ----------
    strategy_id : canonical strategy id (e.g. ``put_credit_spread``)
    net_value   : per-share net premium (positive)
    width       : spread width in dollars (for invariant check)

    Returns
    -------
    dict with keys: ``net_credit``, ``net_debit``, ``validation_warnings``
    """
    warnings: list[str] = []
    sid = (strategy_id or "").strip().lower()

    if is_credit_strategy(sid):
        nc = net_value
        nd = None
        if nc is not None:
            if nc <= 0:
                warnings.append("SCHEMA_INVARIANT:net_credit_non_positive")
            elif width is not None and nc >= width:
                warnings.append("SCHEMA_INVARIANT:net_credit_ge_width")
    elif is_debit_strategy(sid):
        nc = None
        nd = net_value
        if nd is not None:
            if nd <= 0:
                warnings.append("SCHEMA_INVARIANT:net_debit_non_positive")
            elif width is not None and nd >= width:
                warnings.append("SCHEMA_INVARIANT:net_debit_ge_width")
    else:
        # Unknown — do not guess
        nc = None
        nd = None
        if net_value is not None:
            warnings.append(f"CASHFLOW_UNKNOWN_STRATEGY:{sid}")

    return {"net_credit": nc, "net_debit": nd, "validation_warnings": warnings}


# ---------------------------------------------------------------------------
# Readiness & core-metric field lists
# ---------------------------------------------------------------------------

# Fields required for metrics_status.ready = True.
# Only core pricing/risk metrics — advanced analytics do NOT block readiness.
# 11 explicit fields + virtual cashflow gate (net_credit OR net_debit):
#            risk (pop, expected_value, ev_to_risk, return_on_risk),
#            liquidity (bid_ask_pct, open_interest, volume),
#            structure (dte).
# NOTE: net_credit/net_debit use a virtual readiness gate — readiness is
#       satisfied when EITHER is non-None.  See build_metrics_status().
READINESS_REQUIRED_FIELDS: tuple[str, ...] = (
    "max_profit",
    "max_loss",
    "break_even",
    "pop",
    "expected_value",
    "ev_to_risk",
    "return_on_risk",
    "bid_ask_pct",
    "open_interest",
    "volume",
    "dte",
    # net_credit / net_debit: virtual gate — checked separately in build_metrics_status
)

# Full set of computed metrics tracked for completeness reporting.
CORE_COMPUTED_METRIC_FIELDS: tuple[str, ...] = (
    "max_profit",
    "max_loss",
    "pop",
    "expected_value",
    "return_on_risk",
    "kelly_fraction",
    "break_even",
    "dte",
    "net_credit",
    "net_debit",
    "expected_move",
    "iv_rank",
    "iv_rv_ratio",
    "trade_quality_score",
    "short_strike_z",
    "bid_ask_pct",
    "strike_dist_pct",
    "rsi14",
    "rv_20d",
    "open_interest",
    "volume",
    "rank_score",
    "composite_score",
    "ev_to_risk",
)


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _collect_containers(trade: dict[str, Any]) -> list[dict[str, Any]]:
    containers: list[dict[str, Any]] = []
    for key in ("computed_metrics", "computed", "details"):
        value = trade.get(key)
        if isinstance(value, dict):
            containers.append(value)
    containers.append(trade)
    return containers


def _first_number(containers: list[dict[str, Any]], *keys: str) -> float | None:
    for container in containers:
        for key in keys:
            value = _to_float(container.get(key))
            if value is not None:
                return value
    return None


def build_computed_metrics(trade: dict[str, Any]) -> dict[str, float | None]:
    payload = trade if isinstance(trade, dict) else {}
    containers = _collect_containers(payload)

    multiplier = _to_float(payload.get("contractsMultiplier") or payload.get("contracts_multiplier")) or 100.0

    expected_value = _first_number(containers, "expected_value", "ev_per_contract", "ev")
    if expected_value is None:
        ev_per_share = _first_number(containers, "ev_per_share")
        if ev_per_share is not None:
            expected_value = ev_per_share * multiplier

    max_profit = _first_number(containers, "max_profit", "max_profit_per_contract")
    if max_profit is None:
        mp_share = _first_number(containers, "max_profit_per_share")
        if mp_share is not None:
            max_profit = mp_share * multiplier

    max_loss = _first_number(containers, "max_loss", "max_loss_per_contract")
    if max_loss is None:
        ml_share = _first_number(containers, "max_loss_per_share")
        if ml_share is not None:
            max_loss = ml_share * multiplier

    return {
        "max_profit": max_profit,
        "max_loss": max_loss,
        "pop": _first_number(
            containers,
            "pop",
            "p_win_used",
            "pop_delta_approx",
            "pop_approx",
            "probability_of_touch_center",
            "implied_prob_profit",
        ),
        "expected_value": expected_value,
        "return_on_risk": _first_number(containers, "return_on_risk", "ror"),
        "kelly_fraction": _first_number(containers, "kelly_fraction"),
        "break_even": _first_number(containers, "break_even", "break_even_low"),
        "dte": _first_number(containers, "dte"),
        "expected_move": _first_number(containers, "expected_move", "expected_move_near"),
        "iv_rank": _first_number(containers, "iv_rank"),
        "iv_rv_ratio": _first_number(containers, "iv_rv_ratio"),
        "trade_quality_score": _first_number(containers, "trade_quality_score"),
        "short_strike_z": _first_number(containers, "short_strike_z"),
        "bid_ask_pct": _first_number(containers, "bid_ask_pct", "bid_ask_spread_pct"),
        "strike_dist_pct": _first_number(
            containers,
            "strike_dist_pct",
            "strike_distance_pct",
            "strike_distance_vs_expected_move",
            "expected_move_ratio",
        ),
        "rsi14": _first_number(containers, "rsi14", "rsi_14"),
        "rv_20d": _first_number(containers, "rv_20d", "realized_vol_20d"),
        "open_interest": _first_number(containers, "open_interest"),
        "volume": _first_number(containers, "volume"),
        "rank_score": _first_number(containers, "rank_score"),
        "composite_score": _first_number(containers, "composite_score"),
        "ev_to_risk": _first_number(containers, "ev_to_risk"),
        # ── Cashflow fields — NO cross-fallback (credit ≠ debit) ─────
        "net_credit": _first_number(containers, "net_credit"),
        "net_debit": _first_number(containers, "net_debit"),
    }


def build_metrics_status(computed_metrics: dict[str, Any]) -> dict[str, Any]:
    metrics = computed_metrics if isinstance(computed_metrics, dict) else {}
    # Readiness gated only on core pricing/risk fields.
    # Advanced metrics (iv_rank, rsi14, kelly_fraction, etc.) are tracked
    # as missing but do NOT block ready = True.
    missing_required = [f for f in READINESS_REQUIRED_FIELDS if metrics.get(f) is None]

    # Virtual gate: at least one cashflow field (net_credit or net_debit) must
    # be non-None.  When neither is present, report "net_credit" in missing
    # (a real CORE field name) so downstream subset-of-CORE assertions hold.
    has_cashflow = (metrics.get("net_credit") is not None
                    or metrics.get("net_debit") is not None)
    if not has_cashflow:
        missing_required.append("net_credit")

    # Cashflow fields are excluded from optional tracking — one being None is
    # expected (credit strategies have no net_debit, debit strategies have no
    # net_credit).  The virtual gate above handles readiness.
    _cashflow = {"net_credit", "net_debit"}
    _optional = set(CORE_COMPUTED_METRIC_FIELDS) - set(READINESS_REQUIRED_FIELDS) - _cashflow
    missing_optional = sorted(f for f in _optional if metrics.get(f) is None)
    return {
        "ready": len(missing_required) == 0,
        # missing_fields lists ONLY missing REQUIRED fields (gate-blocking).
        "missing_fields": missing_required,
        "missing_required": missing_required,
        "missing_optional": missing_optional,
    }


def apply_metrics_contract(trade: dict[str, Any]) -> dict[str, Any]:
    payload = dict(trade or {})
    computed_metrics = build_computed_metrics(payload)
    payload["computed_metrics"] = computed_metrics
    payload["metrics_status"] = build_metrics_status(computed_metrics)
    return payload
