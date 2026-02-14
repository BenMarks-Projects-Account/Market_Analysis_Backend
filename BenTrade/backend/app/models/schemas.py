from typing import Any, Literal

from pydantic import BaseModel, Field


class ErrorEnvelope(BaseModel):
    error: dict[str, Any]


class OptionContract(BaseModel):
    option_type: Literal["put", "call"]
    strike: float
    expiration: str
    bid: float | None = None
    ask: float | None = None
    open_interest: int | None = None
    volume: int | None = None
    delta: float | None = None
    iv: float | None = None
    symbol: str | None = None


class ExpirationsResponse(BaseModel):
    symbol: str
    expirations: list[str]


class OptionChainResponse(BaseModel):
    symbol: str
    expiration: str
    contracts: list[OptionContract]


class UnderlyingSnapshotResponse(BaseModel):
    symbol: str
    underlying_price: float | None = None
    vix: float | None = None
    prices_history: list[float]


class HealthResponse(BaseModel):
    ok: bool
    upstream: dict[str, str]


class SpreadCandidate(BaseModel):
    short_strike: float
    long_strike: float


class SpreadAnalyzeRequest(BaseModel):
    symbol: str
    expiration: str
    strategy: Literal["put_credit", "call_credit"]
    candidates: list[SpreadCandidate]
    contracts_multiplier: int = Field(default=100, alias="contractsMultiplier")

    model_config = {
        "populate_by_name": True,
        "extra": "ignore",
    }


class SpreadAnalyzeResponse(BaseModel):
    trades: list[dict[str, Any]]
