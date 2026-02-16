from __future__ import annotations

from app.models.trade_contract import TradeContract
from app.services import ranking as legacy_ranking


def sort_trades_by_rank(trades: list[TradeContract]) -> list[TradeContract]:
    sorted_payloads = legacy_ranking.sort_trades_by_rank([trade.to_dict() for trade in trades])
    return [TradeContract.from_dict(item) for item in sorted_payloads]
