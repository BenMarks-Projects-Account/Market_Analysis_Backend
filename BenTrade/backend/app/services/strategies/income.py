from __future__ import annotations

import math
from statistics import pstdev
from typing import Any

from app.services.ranking import safe_float


class IncomeStrategyPlugin:
    id = "income"
    display_name = "Income Strategies"

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
        return max(lo, min(hi, value))

    @staticmethod
    def _realized_vol(prices: list[float]) -> float | None:
        if len(prices) < 25:
            return None
        returns: list[float] = []
        for idx in range(1, len(prices)):
            prev = float(prices[idx - 1])
            cur = float(prices[idx])
            if prev <= 0 or cur <= 0:
                continue
            returns.append(math.log(cur / prev))
        if len(returns) < 12:
            return None
        return pstdev(returns) * math.sqrt(252.0)

    @staticmethod
    def _strike_map(contracts: list[Any], option_type: str) -> dict[float, Any]:
        out: dict[float, Any] = {}
        for leg in contracts:
            if str(getattr(leg, "option_type", "")).lower() != option_type:
                continue
            strike = safe_float(getattr(leg, "strike", None))
            if strike is None:
                continue
            current = out.get(strike)
            if current is None:
                out[strike] = leg
                continue
            curr_oi = safe_float(getattr(current, "open_interest", None)) or 0.0
            new_oi = safe_float(getattr(leg, "open_interest", None)) or 0.0
            if new_oi > curr_oi:
                out[strike] = leg
        return out

    def build_candidates(self, inputs: dict[str, Any]) -> list[dict[str, Any]]:
        payload = inputs.get("request") or {}
        snapshots = inputs.get("snapshots") or []

        delta_target = self._to_float(payload.get("delta_target"))
        if delta_target is None:
            delta_target = 0.22
        delta_target = self._clamp(delta_target, 0.05, 0.45)

        delta_min = self._to_float(payload.get("delta_min"))
        delta_max = self._to_float(payload.get("delta_max"))
        if delta_min is None or delta_max is None:
            delta_min = max(0.05, delta_target - 0.08)
            delta_max = min(0.45, delta_target + 0.08)
        if delta_max < delta_min:
            delta_min, delta_max = delta_max, delta_min

        max_candidates = int(payload.get("max_candidates") or 300)
        candidates: list[dict[str, Any]] = []

        for snapshot in snapshots:
            symbol = str(snapshot.get("symbol") or "").upper()
            expiration = str(snapshot.get("expiration") or "")
            dte = int(snapshot.get("dte") or 0)
            spot = self._to_float(snapshot.get("underlying_price"))
            contracts = snapshot.get("contracts") or []
            if not symbol or not expiration or dte <= 0 or spot is None or not contracts:
                continue

            put_map = self._strike_map(contracts, "put")
            call_map = self._strike_map(contracts, "call")

            put_side_count = 0

            for strike, leg in put_map.items():
                if strike >= spot:
                    continue
                delta_abs = abs(safe_float(getattr(leg, "delta", None)) or 0.0)
                if delta_abs <= 0:
                    continue
                if delta_abs < delta_min or delta_abs > delta_max:
                    continue
                candidates.append(
                    {
                        "strategy": "income",
                        "spread_type": "cash_secured_put",
                        "symbol": symbol,
                        "expiration": expiration,
                        "dte": dte,
                        "underlying_price": float(spot),
                        "short_strike": float(strike),
                        "long_strike": None,
                        "short_leg": leg,
                        "snapshot": snapshot,
                    }
                )
                put_side_count += 1
                if len(candidates) >= max_candidates:
                    return candidates

            if put_side_count == 0:
                fallback_put = min(
                    (
                        (strike, leg)
                        for strike, leg in put_map.items()
                        if strike < spot and abs(safe_float(getattr(leg, "delta", None)) or 0.0) >= 0.03
                    ),
                    key=lambda item: abs((abs(safe_float(getattr(item[1], "delta", None)) or 0.0) - delta_target)),
                    default=None,
                )
                if fallback_put is not None:
                    strike, leg = fallback_put
                    candidates.append(
                        {
                            "strategy": "income",
                            "spread_type": "cash_secured_put",
                            "symbol": symbol,
                            "expiration": expiration,
                            "dte": dte,
                            "underlying_price": float(spot),
                            "short_strike": float(strike),
                            "long_strike": None,
                            "short_leg": leg,
                            "snapshot": snapshot,
                        }
                    )
                    if len(candidates) >= max_candidates:
                        return candidates

            call_side_count = 0

            for strike, leg in call_map.items():
                if strike <= spot:
                    continue
                delta_abs = abs(safe_float(getattr(leg, "delta", None)) or 0.0)
                if delta_abs <= 0:
                    continue
                if delta_abs < delta_min or delta_abs > delta_max:
                    continue
                candidates.append(
                    {
                        "strategy": "income",
                        "spread_type": "covered_call",
                        "symbol": symbol,
                        "expiration": expiration,
                        "dte": dte,
                        "underlying_price": float(spot),
                        "short_strike": float(strike),
                        "long_strike": None,
                        "short_leg": leg,
                        "snapshot": snapshot,
                    }
                )
                call_side_count += 1
                if len(candidates) >= max_candidates:
                    return candidates

            if call_side_count == 0:
                fallback_call = min(
                    (
                        (strike, leg)
                        for strike, leg in call_map.items()
                        if strike > spot and abs(safe_float(getattr(leg, "delta", None)) or 0.0) >= 0.03
                    ),
                    key=lambda item: abs((abs(safe_float(getattr(item[1], "delta", None)) or 0.0) - delta_target)),
                    default=None,
                )
                if fallback_call is not None:
                    strike, leg = fallback_call
                    candidates.append(
                        {
                            "strategy": "income",
                            "spread_type": "covered_call",
                            "symbol": symbol,
                            "expiration": expiration,
                            "dte": dte,
                            "underlying_price": float(spot),
                            "short_strike": float(strike),
                            "long_strike": None,
                            "short_leg": leg,
                            "snapshot": snapshot,
                        }
                    )
                    if len(candidates) >= max_candidates:
                        return candidates

        return candidates

    def enrich(self, candidates: list[dict[str, Any]], inputs: dict[str, Any]) -> list[dict[str, Any]]:
        payload = inputs.get("request") or {}
        policy = inputs.get("policy") or {}

        # Pre-compute realized vol once per unique snapshot to avoid redundant math
        _rv_cache: dict[int, float | None] = {}

        min_annualized_yield = self._to_float(payload.get("min_annualized_yield"))
        if min_annualized_yield is None:
            min_annualized_yield = 0.10
        min_buffer = self._to_float(payload.get("min_buffer"))
        event_risk_flag = str(payload.get("event_risk_flag") or "false").lower() in {"1", "true", "yes", "y"}

        portfolio_size = self._to_float(policy.get("portfolio_size")) or 0.0
        max_trade_risk_pct = self._to_float(policy.get("max_trade_risk_pct"))
        max_position_size_pct = self._to_float(policy.get("max_position_size_pct"))
        max_risk_per_trade = self._to_float(policy.get("max_risk_per_trade"))
        min_cash_reserve_pct = self._to_float(policy.get("min_cash_reserve_pct"))
        default_contracts_cap = int(self._to_float(policy.get("default_contracts_cap")) or 1)
        requested_contracts = int(self._to_float(payload.get("contracts")) or 1)
        contracts = max(1, requested_contracts)

        budget_limits: list[float] = []
        if portfolio_size > 0 and max_trade_risk_pct is not None and max_trade_risk_pct > 0:
            budget_limits.append(portfolio_size * max_trade_risk_pct)
        if portfolio_size > 0 and max_position_size_pct is not None and max_position_size_pct > 0:
            budget_limits.append(portfolio_size * (max_position_size_pct / 100.0))
        if max_risk_per_trade is not None and max_risk_per_trade > 0:
            budget_limits.append(max_risk_per_trade)
        if portfolio_size > 0 and min_cash_reserve_pct is not None:
            investable = portfolio_size * max(0.0, 1.0 - (min_cash_reserve_pct / 100.0))
            if investable > 0:
                budget_limits.append(investable)
        max_collateral_allowed = max(budget_limits) if budget_limits else float("inf")

        out: list[dict[str, Any]] = []
        for row in candidates:
            leg = row.get("short_leg")
            if leg is None:
                continue

            spread_type = str(row.get("spread_type") or "income")
            symbol = str(row.get("symbol") or "").upper()
            expiration = str(row.get("expiration") or "")
            dte = int(row.get("dte") or 0)
            spot = float(row.get("underlying_price") or 0.0)
            strike = float(row.get("short_strike") or 0.0)
            if dte <= 0 or spot <= 0 or strike <= 0:
                continue

            bid = safe_float(getattr(leg, "bid", None))
            ask = safe_float(getattr(leg, "ask", None))
            if bid is None and ask is None:
                continue
            mid = ((bid or 0.0) + (ask or bid or 0.0)) / 2.0
            premium = bid if (bid is not None and bid > 0) else mid
            if premium <= 0:
                continue

            delta_abs = abs(safe_float(getattr(leg, "delta", None)) or 0.0)
            iv = safe_float(getattr(leg, "iv", None))
            snapshot = row.get("snapshot") or {}
            snap_id = id(snapshot)
            if snap_id not in _rv_cache:
                prices = [float(x) for x in snapshot.get("prices_history", []) if self._to_float(x) is not None]
                _rv_cache[snap_id] = self._realized_vol(prices)
            rv = _rv_cache[snap_id]
            iv_rv_ratio = (iv / rv) if iv not in (None, 0) and rv not in (None, 0) else None
            vol_for_em = iv if iv not in (None, 0) else rv
            expected_move = (spot * float(vol_for_em) * math.sqrt(dte / 365.0)) if vol_for_em not in (None, 0) and dte > 0 else None
            expected_move_ratio = (expected_move / spot) if expected_move not in (None, 0) and spot > 0 else None

            collateral_per_contract = strike * 100.0
            required_capital = collateral_per_contract * contracts

            annualized_yield = (premium / max(strike, 0.01)) * (365.0 / max(dte, 1))
            premium_per_day = (premium * 100.0) / max(dte, 1)

            if spread_type == "cash_secured_put":
                downside_buffer = self._clamp((spot - strike) / max(spot, 0.01), 0.0, 0.99)
                break_even = strike - premium
                max_profit = premium * 100.0
                max_loss = max((strike - premium) * 100.0, 0.0)
                assignment_raw = (0.65 * delta_abs) + (0.35 * (1.0 - downside_buffer))
                pop_est = self._clamp(1.0 - delta_abs)
            else:
                downside_buffer = self._clamp((strike - spot) / max(spot, 0.01), 0.0, 0.99)
                break_even = max(spot - premium, 0.01)
                max_profit = max((strike - spot + premium) * 100.0, premium * 100.0)
                max_loss = max((spot - premium) * 100.0, 0.0)
                assignment_raw = (0.60 * delta_abs) + (0.40 * (1.0 - downside_buffer))
                pop_est = self._clamp(1.0 - delta_abs)

            assignment_risk_score = self._clamp(assignment_raw)

            # Proper EV from POP-based formula
            ev_per_contract = pop_est * max_profit - (1.0 - pop_est) * max_loss
            ev_per_share = ev_per_contract / 100.0

            oi = int(safe_float(getattr(leg, "open_interest", None)) or 0)
            volume = int(safe_float(getattr(leg, "volume", None)) or 0)
            spread = max(0.0, (ask or bid or 0.0) - (bid or 0.0))

            min_oi = int(self._to_float(policy.get("min_open_interest")) or 100)
            min_volume = int(self._to_float(policy.get("min_volume")) or 20)
            oi_score = self._clamp((oi / max(min_oi, 1)) / 2.0)
            vol_score = self._clamp((volume / max(min_volume, 1)) / 2.0)
            spread_score = self._clamp(1.0 - (spread / max(premium, 0.05)))
            liquidity_score = self._clamp((0.45 * oi_score) + (0.30 * vol_score) + (0.25 * spread_score))

            iv_rich_score = 0.5
            if iv_rv_ratio is not None:
                min_sell_ratio = self._to_float(policy.get("min_iv_rv_ratio_for_selling")) or 1.1
                iv_rich_score = self._clamp((iv_rv_ratio - (min_sell_ratio - 0.20)) / 0.70)

            yield_score = self._clamp(annualized_yield / 0.35)
            buffer_score = self._clamp(downside_buffer / 0.10)
            event_penalty = 0.15 if event_risk_flag else 0.0
            low_buffer_penalty = self._clamp((0.02 - downside_buffer) / 0.02)
            low_liq_penalty = self._clamp(1.0 - liquidity_score)

            rank_score = self._clamp(
                (0.33 * yield_score)
                + (0.24 * buffer_score)
                + (0.20 * liquidity_score)
                + (0.15 * iv_rich_score)
                - (0.18 * assignment_risk_score)
                - (0.15 * low_buffer_penalty)
                - (0.15 * low_liq_penalty)
                - event_penalty
            )

            effective_min_buffer = min_buffer
            if effective_min_buffer is None:
                if expected_move_ratio is not None and expected_move_ratio > 0:
                    effective_min_buffer = expected_move_ratio
                else:
                    effective_min_buffer = self._clamp(delta_abs * 0.5, 0.02, 0.20)

            if min_annualized_yield is not None and annualized_yield < min_annualized_yield:
                continue
            if effective_min_buffer is not None and downside_buffer < effective_min_buffer:
                continue

            out.append(
                {
                    "strategy": "income",
                    "spread_type": spread_type,
                    "underlying": symbol,
                    "underlying_symbol": symbol,
                    "symbol": symbol,
                    "expiration": expiration,
                    "dte": dte,
                    "underlying_price": spot,
                    "short_strike": strike,
                    "long_strike": None,
                    "break_even": break_even,
                    "max_profit": max_profit,
                    "max_profit_per_contract": max_profit,
                    "max_loss": max_loss,
                    "max_loss_per_contract": max_loss,
                    "return_on_risk": premium / max(strike, 0.01),
                    "annualized_yield_on_collateral": annualized_yield,
                    "premium_per_day": premium_per_day,
                    "downside_buffer": downside_buffer,
                    "assignment_risk_score": assignment_risk_score,
                    "iv_rv_ratio": iv_rv_ratio,
                    "liquidity_score": liquidity_score,
                    "open_interest": oi,
                    "volume": volume,
                    "bid_ask_spread_pct": self._clamp(spread / max(premium, 0.05), 0.0, 9.99),
                    "event_risk_flag": event_risk_flag,
                    "ev_per_contract": ev_per_contract,
                    "ev_per_share": ev_per_share,
                    "expected_value": ev_per_contract,
                    "p_win_used": pop_est,
                    "rank_score": rank_score,
                    "collateral_per_contract": collateral_per_contract,
                    "required_capital": required_capital,
                    "max_collateral_allowed": max_collateral_allowed,
                    "contracts_cap": max(1, default_contracts_cap),
                    "contracts": contracts,
                    "why_yield": yield_score,
                    "why_buffer": buffer_score,
                    "why_liquidity": liquidity_score,
                    "why_iv_rich": iv_rich_score,
                    "effective_min_buffer": effective_min_buffer,
                    "expected_move_ratio": expected_move_ratio,
                    "contractsMultiplier": 100,
                    "selection_reasons": [],
                }
            )

        return out

    def evaluate(self, trade: dict[str, Any]) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        policy = trade.get("_policy") if isinstance(trade.get("_policy"), dict) else {}
        payload = trade.get("_request") if isinstance(trade.get("_request"), dict) else {}

        required_capital = self._to_float(trade.get("required_capital"))
        max_collateral_allowed = self._to_float(trade.get("max_collateral_allowed"))
        if required_capital is not None and max_collateral_allowed is not None and max_collateral_allowed > 0:
            if required_capital > max_collateral_allowed:
                reasons.append("collateral_above_policy_limit")

        min_oi_policy = int(self._to_float(payload.get("min_open_interest")) or 0)
        if min_oi_policy <= 0:
            min_oi_policy = max(int(self._to_float(policy.get("min_open_interest")) or 0), 500)
        min_vol_policy = int(self._to_float(payload.get("min_volume")) or 0)
        if min_vol_policy <= 0:
            min_vol_policy = max(int(self._to_float(policy.get("min_volume")) or 0), 50)
        min_oi = max(5, int(min_oi_policy * 0.2)) if min_oi_policy > 0 else 5
        min_vol = max(1, int(min_vol_policy * 0.2)) if min_vol_policy > 0 else 1
        if int(self._to_float(trade.get("open_interest")) or 0) < min_oi:
            reasons.append("open_interest_below_min")
        if int(self._to_float(trade.get("volume")) or 0) < min_vol:
            reasons.append("volume_below_min")

        spread_pct_limit = self._to_float(policy.get("max_bid_ask_spread_pct"))
        spread_pct = self._to_float(trade.get("bid_ask_spread_pct"))
        if spread_pct_limit is not None and spread_pct is not None and (spread_pct * 100.0) > spread_pct_limit:
            reasons.append("spread_too_wide")

        min_annualized_yield = self._to_float(payload.get("min_annualized_yield"))
        if min_annualized_yield is None:
            min_annualized_yield = 0.10
        annualized_yield = self._to_float(trade.get("annualized_yield_on_collateral"))
        if min_annualized_yield is not None and annualized_yield is not None and annualized_yield < min_annualized_yield:
            reasons.append("annualized_yield_below_floor")

        min_buffer = self._to_float(payload.get("min_buffer"))
        if min_buffer is None:
            min_buffer = self._to_float(trade.get("effective_min_buffer"))
        downside_buffer = self._to_float(trade.get("downside_buffer"))
        if min_buffer is not None and downside_buffer is not None and downside_buffer < min_buffer:
            reasons.append("buffer_below_floor")

        if (self._to_float(trade.get("liquidity_score")) or 0.0) < 0.10:
            reasons.append("liquidity_score_low")

        return len(reasons) == 0, reasons

    def score(self, trade: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        rank_score = float(safe_float(trade.get("rank_score")) or 0.0)
        tie_breaks = {
            "edge": safe_float(trade.get("why_yield")) or 0.0,
            "liquidity": safe_float(trade.get("why_liquidity")) or 0.0,
            "conviction": safe_float(trade.get("why_buffer")) or 0.0,
        }
        return rank_score, tie_breaks