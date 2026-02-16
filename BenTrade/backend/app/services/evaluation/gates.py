from __future__ import annotations

from collections.abc import Callable

from app.models.trade_contract import TradeContract
from app.services.evaluation.types import EvaluationContext, EvaluationResult


def evaluate_trade(
    trade: TradeContract,
    ctx: EvaluationContext,
    legacy_evaluator: Callable[[dict, dict, bool], tuple[bool, list[str]]] | None = None,
) -> EvaluationResult:
    payload = trade.to_dict()

    if legacy_evaluator is not None:
        accepted, reasons = legacy_evaluator(payload, ctx.rules, ctx.validation_mode)
        return EvaluationResult(accepted=bool(accepted), reasons=list(reasons or []))

    return EvaluationResult(accepted=True, reasons=[])
