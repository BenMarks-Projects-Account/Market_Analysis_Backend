from __future__ import annotations

from dataclasses import dataclass
from typing import Any

FILTER_RELAX_LIQUIDITY = "FILTER_RELAX_LIQUIDITY"
FILTER_RELAX_RETURN = "FILTER_RELAX_RETURN"
FILTER_RELAX_DISTANCE = "FILTER_RELAX_DISTANCE"
FILTERS_TOO_STRICT = "FILTERS_TOO_STRICT"

MIN_RESULTS_DEFAULT = 12

_ALIASES: dict[str, str] = {
    "credit_spread": "put_credit_spread",
    "credit_put_spread": "put_credit_spread",
    "put_credit": "put_credit_spread",
    "put_credit_spread": "put_credit_spread",
    "credit_call_spread": "call_credit_spread",
    "call_credit": "call_credit_spread",
    "call_credit_spread": "call_credit_spread",
    "debit_spreads": "call_debit",
    "debit_call_spread": "call_debit",
    "call_debit": "call_debit",
    "debit_put_spread": "put_debit",
    "put_debit": "put_debit",
    "iron_condor": "iron_condor",
    "butterflies": "debit_butterfly",
    "debit_butterfly": "debit_butterfly",
    "debit_call_butterfly": "debit_butterfly",
    "debit_put_butterfly": "debit_butterfly",
    "income": "income",
    "cash_secured_put": "csp",
    "csp": "csp",
    "covered_call": "covered_call",
}


@dataclass(frozen=True)
class RelaxationStep:
    name: str
    warning_code: str
    updates: dict[str, Any]
    reason: str


@dataclass(frozen=True)
class StrategyDefaults:
    strategy_id: str
    min_results: int
    params: dict[str, Any]
    filters: dict[str, Any]
    relaxation_plan: tuple[RelaxationStep, ...]

    def as_payload(self) -> dict[str, Any]:
        payload = dict(self.params)
        payload.update(self.filters)
        return payload


def canonicalize_strategy_id(strategy_id: str) -> str:
    key = str(strategy_id or "").strip().lower()
    return _ALIASES.get(key, key)


def _liquidity_step(name: str, updates: dict[str, Any], reason: str) -> RelaxationStep:
    return RelaxationStep(
        name=name,
        warning_code=FILTER_RELAX_LIQUIDITY,
        updates=updates,
        reason=reason,
    )


def _return_step(name: str, updates: dict[str, Any], reason: str) -> RelaxationStep:
    return RelaxationStep(
        name=name,
        warning_code=FILTER_RELAX_RETURN,
        updates=updates,
        reason=reason,
    )


def _distance_step(name: str, updates: dict[str, Any], reason: str) -> RelaxationStep:
    return RelaxationStep(
        name=name,
        warning_code=FILTER_RELAX_DISTANCE,
        updates=updates,
        reason=reason,
    )


_DEFAULTS: dict[str, StrategyDefaults] = {
    "put_credit_spread": StrategyDefaults(
        strategy_id="put_credit_spread",
        min_results=MIN_RESULTS_DEFAULT,
        params={
            "dte_min": 7,
            "dte_max": 21,
            "width_min": 1.0,
            "width_max": 5.0,
            "expected_move_multiple": 1.0,
        },
        filters={
            "min_pop": 0.65,
            "min_ev_to_risk": 0.02,
            "max_bid_ask_spread_pct": 1.5,
            "min_open_interest": 200,
            "min_volume": 10,
        },
        relaxation_plan=(
            _liquidity_step("liquidity_1", {"min_volume": 5, "min_open_interest": 150}, "Lower liquidity floors modestly"),
            _liquidity_step("liquidity_2", {"min_volume": 2, "min_open_interest": 100}, "Broaden liquidity acceptance while preserving sanity checks"),
            _return_step("return_1", {"min_pop": 0.62, "min_ev_to_risk": 0.015}, "Allow slightly lower edge thresholds"),
            _distance_step("distance_1", {"expected_move_multiple": 0.9, "width_max": 6.0}, "Loosen strike-distance rigidity"),
        ),
    ),
    "call_credit_spread": StrategyDefaults(
        strategy_id="call_credit_spread",
        min_results=MIN_RESULTS_DEFAULT,
        params={
            "dte_min": 7,
            "dte_max": 21,
            "width_min": 1.0,
            "width_max": 5.0,
            "expected_move_multiple": 1.0,
        },
        filters={
            "min_pop": 0.65,
            "min_ev_to_risk": 0.02,
            "max_bid_ask_spread_pct": 1.5,
            "min_open_interest": 200,
            "min_volume": 10,
        },
        relaxation_plan=(
            _liquidity_step("liquidity_1", {"min_volume": 5, "min_open_interest": 150}, "Lower liquidity floors modestly"),
            _liquidity_step("liquidity_2", {"min_volume": 2, "min_open_interest": 100}, "Broaden liquidity acceptance while preserving sanity checks"),
            _return_step("return_1", {"min_pop": 0.62, "min_ev_to_risk": 0.015}, "Allow slightly lower edge thresholds"),
            _distance_step("distance_1", {"expected_move_multiple": 0.9, "width_max": 6.0}, "Loosen strike-distance rigidity"),
        ),
    ),
    "put_debit": StrategyDefaults(
        strategy_id="put_debit",
        min_results=MIN_RESULTS_DEFAULT,
        params={
            "dte_min": 14,
            "dte_max": 45,
            "width_min": 2.0,
            "width_max": 10.0,
        },
        filters={
            "max_debit_pct_width": 0.65,
            "max_iv_rv_ratio_for_buying": 1.5,
            "max_bid_ask_spread_pct": 1.5,
            "min_open_interest": 200,
            "min_volume": 10,
        },
        relaxation_plan=(
            _liquidity_step("liquidity_1", {"min_volume": 5, "min_open_interest": 150}, "Lower liquidity floors modestly"),
            _liquidity_step("liquidity_2", {"min_volume": 2, "min_open_interest": 100}, "Broaden liquidity acceptance while preserving sanity checks"),
            _return_step("return_1", {"max_debit_pct_width": 0.72, "max_iv_rv_ratio_for_buying": 1.7}, "Permit slightly richer debit and IV/RV"),
            _distance_step("distance_1", {"dte_min": 10, "dte_max": 55, "width_max": 12.0}, "Widen tenor/width search range"),
        ),
    ),
    "call_debit": StrategyDefaults(
        strategy_id="call_debit",
        min_results=MIN_RESULTS_DEFAULT,
        params={
            "dte_min": 14,
            "dte_max": 45,
            "width_min": 2.0,
            "width_max": 10.0,
        },
        filters={
            "max_debit_pct_width": 0.65,
            "max_iv_rv_ratio_for_buying": 1.5,
            "max_bid_ask_spread_pct": 1.5,
            "min_open_interest": 200,
            "min_volume": 10,
        },
        relaxation_plan=(
            _liquidity_step("liquidity_1", {"min_volume": 5, "min_open_interest": 150}, "Lower liquidity floors modestly"),
            _liquidity_step("liquidity_2", {"min_volume": 2, "min_open_interest": 100}, "Broaden liquidity acceptance while preserving sanity checks"),
            _return_step("return_1", {"max_debit_pct_width": 0.72, "max_iv_rv_ratio_for_buying": 1.7}, "Permit slightly richer debit and IV/RV"),
            _distance_step("distance_1", {"dte_min": 10, "dte_max": 55, "width_max": 12.0}, "Widen tenor/width search range"),
        ),
    ),
    "iron_condor": StrategyDefaults(
        strategy_id="iron_condor",
        min_results=MIN_RESULTS_DEFAULT,
        params={
            "dte_min": 21,
            "dte_max": 45,
            "distance_mode": "expected_move",
            "distance_target": 1.0,
            "min_sigma_distance": 1.0,
            "wing_width_put": 5.0,
            "wing_width_call": 5.0,
            "wing_width_max": 10.0,
        },
        filters={
            "min_ror": 0.08,
            "symmetry_target": 0.5,
            "min_open_interest": 200,
            "min_volume": 10,
        },
        relaxation_plan=(
            _liquidity_step("liquidity_1", {"min_volume": 5, "min_open_interest": 150}, "Lower liquidity floors modestly"),
            _liquidity_step("liquidity_2", {"min_volume": 2, "min_open_interest": 100}, "Broaden liquidity acceptance while preserving sanity checks"),
            _return_step("return_1", {"min_ror": 0.06, "symmetry_target": 0.4}, "Accept lower RoR and looser symmetry"),
            _distance_step("distance_1", {"distance_target": 0.9, "min_sigma_distance": 0.9, "wing_width_max": 12.0}, "Permit closer shorts/wider wings"),
        ),
    ),
    "debit_butterfly": StrategyDefaults(
        strategy_id="debit_butterfly",
        min_results=MIN_RESULTS_DEFAULT,
        params={
            "dte_min": 7,
            "dte_max": 21,
            "width_min": 2.0,
            "width_max": 10.0,
            "butterfly_type": "debit",
        },
        filters={
            "min_cost_efficiency": 1.2,
            "min_open_interest": 150,
            "min_volume": 10,
        },
        relaxation_plan=(
            _liquidity_step("liquidity_1", {"min_volume": 5, "min_open_interest": 100}, "Lower liquidity floors modestly"),
            _liquidity_step("liquidity_2", {"min_volume": 2, "min_open_interest": 75}, "Broaden liquidity acceptance while preserving sanity checks"),
            _return_step("return_1", {"min_cost_efficiency": 1.0}, "Allow neutral cost-efficiency setups"),
            _distance_step("distance_1", {"width_max": 12.0, "dte_max": 28}, "Expand wing and tenor range"),
        ),
    ),
    "income": StrategyDefaults(
        strategy_id="income",
        min_results=MIN_RESULTS_DEFAULT,
        params={
            "dte_min": 14,
            "dte_max": 45,
            "delta_min": 0.15,
            "delta_max": 0.35,
            "income_modes": ["csp", "covered_call"],
            "min_buffer": None,
        },
        filters={
            "min_annualized_yield": 0.06,
            "min_open_interest": 200,
            "min_volume": 10,
            "missing_buffer_is_warning": True,
        },
        relaxation_plan=(
            _liquidity_step("liquidity_1", {"min_volume": 5, "min_open_interest": 150}, "Lower liquidity floors modestly"),
            _liquidity_step("liquidity_2", {"min_volume": 2, "min_open_interest": 100}, "Broaden liquidity acceptance while preserving sanity checks"),
            _return_step("return_1", {"min_annualized_yield": 0.04}, "Allow lower-yield but still valid income setups"),
            _distance_step("distance_1", {"delta_min": 0.10, "delta_max": 0.40, "dte_max": 60}, "Broaden strike-distance and tenor range"),
        ),
    ),
    "csp": StrategyDefaults(
        strategy_id="csp",
        min_results=MIN_RESULTS_DEFAULT,
        params={
            "dte_min": 14,
            "dte_max": 45,
            "delta_min": 0.15,
            "delta_max": 0.35,
            "option_side": "put",
            "min_buffer": None,
        },
        filters={
            "min_annualized_yield": 0.06,
            "min_open_interest": 200,
            "min_volume": 10,
            "missing_buffer_is_warning": True,
        },
        relaxation_plan=(
            _liquidity_step("liquidity_1", {"min_volume": 5, "min_open_interest": 150}, "Lower liquidity floors modestly"),
            _liquidity_step("liquidity_2", {"min_volume": 2, "min_open_interest": 100}, "Broaden liquidity acceptance while preserving sanity checks"),
            _return_step("return_1", {"min_annualized_yield": 0.04}, "Allow lower-yield but still valid income setups"),
            _distance_step("distance_1", {"delta_min": 0.10, "delta_max": 0.40, "dte_max": 60}, "Broaden strike-distance and tenor range"),
        ),
    ),
    "covered_call": StrategyDefaults(
        strategy_id="covered_call",
        min_results=MIN_RESULTS_DEFAULT,
        params={
            "dte_min": 14,
            "dte_max": 45,
            "delta_min": 0.15,
            "delta_max": 0.35,
            "option_side": "call",
            "min_buffer": None,
        },
        filters={
            "min_annualized_yield": 0.06,
            "min_open_interest": 200,
            "min_volume": 10,
            "missing_buffer_is_warning": True,
        },
        relaxation_plan=(
            _liquidity_step("liquidity_1", {"min_volume": 5, "min_open_interest": 150}, "Lower liquidity floors modestly"),
            _liquidity_step("liquidity_2", {"min_volume": 2, "min_open_interest": 100}, "Broaden liquidity acceptance while preserving sanity checks"),
            _return_step("return_1", {"min_annualized_yield": 0.04}, "Allow lower-yield but still valid income setups"),
            _distance_step("distance_1", {"delta_min": 0.10, "delta_max": 0.40, "dte_max": 60}, "Broaden strike-distance and tenor range"),
        ),
    ),
}


def get_strategy_defaults(strategy_id: str) -> StrategyDefaults:
    canonical = canonicalize_strategy_id(strategy_id)
    profile = _DEFAULTS.get(canonical)
    if profile is None:
        raise KeyError(f"Unknown strategy defaults: {strategy_id}")
    return profile


def build_relaxation_event_context(
    *,
    strategy_id: str,
    step_name: str,
    previous_count: int,
    new_count: int,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = {
        "strategy_id": canonicalize_strategy_id(strategy_id),
        "step_name": str(step_name or ""),
        "previous_count": int(previous_count),
        "new_count": int(new_count),
    }
    if isinstance(extra, dict) and extra:
        context.update(extra)
    return context
