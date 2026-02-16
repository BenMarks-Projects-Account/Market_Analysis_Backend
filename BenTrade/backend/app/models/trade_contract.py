from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class TradeContract(BaseModel):
    model_config = ConfigDict(extra="allow")

    spread_type: str | None = None
    underlying: str | None = None
    short_strike: float | None = None
    long_strike: float | None = None
    dte: int | None = None
    net_credit: float | None = None
    width: float | None = None
    max_profit_per_share: float | None = None
    max_loss_per_share: float | None = None
    break_even: float | None = None
    return_on_risk: float | None = None
    pop_delta_approx: float | None = None
    p_win_used: float | None = None
    ev_per_share: float | None = None
    ev_to_risk: float | None = None
    kelly_fraction: float | None = None
    trade_quality_score: float | None = None
    iv: float | None = None
    realized_vol: float | None = None
    iv_rv_ratio: float | None = None
    expected_move: float | None = None
    short_strike_z: float | None = None
    bid_ask_spread_pct: float | None = None
    composite_score: float | None = None
    rank_score: float | None = None
    rank_in_report: int | None = None
    model_evaluation: dict[str, Any] | None = None

    expiration: str | None = None
    underlying_symbol: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TradeContract":
        return cls.model_validate(d or {})

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="python", by_alias=False, exclude_none=False)
