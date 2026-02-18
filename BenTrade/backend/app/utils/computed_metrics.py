from __future__ import annotations

from typing import Any

CORE_COMPUTED_METRIC_FIELDS: tuple[str, ...] = (
    "max_profit",
    "max_loss",
    "pop",
    "expected_value",
    "return_on_risk",
    "kelly_fraction",
    "break_even",
    "dte",
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
    }


def build_metrics_status(computed_metrics: dict[str, Any]) -> dict[str, Any]:
    metrics = computed_metrics if isinstance(computed_metrics, dict) else {}
    missing_fields = [field for field in CORE_COMPUTED_METRIC_FIELDS if metrics.get(field) is None]
    return {
        "ready": len(missing_fields) == 0,
        "missing_fields": missing_fields,
    }


def apply_metrics_contract(trade: dict[str, Any]) -> dict[str, Any]:
    payload = dict(trade or {})
    computed_metrics = build_computed_metrics(payload)
    payload["computed_metrics"] = computed_metrics
    payload["metrics_status"] = build_metrics_status(computed_metrics)
    return payload
