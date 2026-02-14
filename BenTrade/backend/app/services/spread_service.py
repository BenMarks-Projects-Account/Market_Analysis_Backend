from typing import Any

from fastapi import HTTPException

from app.models.schemas import OptionContract, SpreadAnalyzeRequest
from app.services.base_data_service import BaseDataService
from app.utils.dates import dte_ceil

try:
    from common.quant_analysis import enrich_trades_batch
except Exception:
    from quant_analysis import enrich_trades_batch


class SpreadService:
    def __init__(self, base_data_service: BaseDataService) -> None:
        self.base_data_service = base_data_service

    @staticmethod
    def _mid_price(contract: OptionContract) -> float | None:
        if contract.bid is not None and contract.ask is not None:
            return (contract.bid + contract.ask) / 2.0
        return None

    @staticmethod
    def _credit_fallback(short_leg: OptionContract, long_leg: OptionContract) -> float | None:
        short_px = short_leg.bid if short_leg.bid is not None else 0.0
        long_px = long_leg.ask if long_leg.ask is not None else 0.0
        return short_px - long_px

    @staticmethod
    def _strike_key(strike: float) -> str:
        return f"{float(strike):.8f}"

    def _index_contracts(
        self,
        contracts: list[OptionContract],
        option_type: str,
        expiration: str,
    ) -> dict[str, OptionContract]:
        out: dict[str, OptionContract] = {}
        for contract in contracts:
            if contract.option_type != option_type:
                continue
            if contract.expiration != expiration:
                continue
            out[self._strike_key(contract.strike)] = contract
        return out

    def _build_base_trade(
        self,
        *,
        req: SpreadAnalyzeRequest,
        underlying_price: float | None,
        vix: float | None,
        short_leg: OptionContract,
        long_leg: OptionContract,
    ) -> dict[str, Any]:
        mid_short = self._mid_price(short_leg)
        mid_long = self._mid_price(long_leg)
        if mid_short is not None and mid_long is not None:
            net_credit = mid_short - mid_long
        else:
            net_credit = self._credit_fallback(short_leg, long_leg)

        trade: dict[str, Any] = {
            "spread_type": req.strategy,
            "underlying": req.symbol.upper(),
            "underlying_symbol": req.symbol.upper(),
            "short_strike": short_leg.strike,
            "long_strike": long_leg.strike,
            "dte": dte_ceil(req.expiration),
            "underlying_price": underlying_price,
            "price": underlying_price,
            "vix": vix,
            "bid": short_leg.bid,
            "ask": short_leg.ask,
            "open_interest": short_leg.open_interest,
            "volume": short_leg.volume,
            "short_delta_abs": abs(short_leg.delta) if short_leg.delta is not None else None,
            "iv": short_leg.iv,
            "implied_vol": short_leg.iv,
            "width": abs(short_leg.strike - long_leg.strike),
            "net_credit": net_credit,
            "contractsMultiplier": req.contracts_multiplier,
        }
        return trade

    async def analyze_spreads(self, req: SpreadAnalyzeRequest) -> list[dict[str, Any]]:
        inputs = await self.base_data_service.get_analysis_inputs(req.symbol, req.expiration)
        underlying_price: float | None = inputs["underlying_price"]
        contracts: list[OptionContract] = inputs["contracts"]
        prices_history: list[float] = inputs["prices_history"]
        vix: float | None = inputs["vix"]

        if underlying_price is None:
            raise HTTPException(status_code=502, detail="Unable to determine underlying price")

        option_type = "put" if req.strategy == "put_credit" else "call"
        contract_map = self._index_contracts(contracts, option_type=option_type, expiration=req.expiration)
        if not contract_map:
            raise HTTPException(status_code=404, detail="No matching option contracts found for strategy/expiration")

        base_trades: list[dict[str, Any]] = []
        for candidate in req.candidates:
            short_leg = contract_map.get(self._strike_key(candidate.short_strike))
            long_leg = contract_map.get(self._strike_key(candidate.long_strike))
            if not short_leg or not long_leg:
                continue
            base_trades.append(
                self._build_base_trade(
                    req=req,
                    underlying_price=underlying_price,
                    vix=vix,
                    short_leg=short_leg,
                    long_leg=long_leg,
                )
            )

        if not base_trades:
            raise HTTPException(status_code=404, detail="No candidates could be matched to option chain")

        enriched = enrich_trades_batch(
            base_trades,
            prices_history=prices_history,
            vix=vix,
            iv_low=None,
            iv_high=None,
        )
        return enriched
