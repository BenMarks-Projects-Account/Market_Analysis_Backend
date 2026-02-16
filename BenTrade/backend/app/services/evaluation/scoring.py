from __future__ import annotations

from collections.abc import Callable

from app.models.trade_contract import TradeContract


def compute_composite_score(
    trade: TradeContract,
    legacy_scorer: Callable[[dict], float] | None = None,
) -> float:
    payload = trade.to_dict()
    if legacy_scorer is not None:
        return float(legacy_scorer(payload))

    value = payload.get("composite_score")
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0
