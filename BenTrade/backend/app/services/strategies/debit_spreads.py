from __future__ import annotations

import math
from statistics import pstdev
from typing import Any

from app.services.ranking import safe_float


class DebitSpreadsStrategyPlugin:
    id = "debit_spreads"
    display_name = "Debit Spreads"

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
    def _choose_widths(underlying_price: float, payload: dict[str, Any]) -> list[float]:
        req_width = payload.get("width")
        if req_width not in (None, ""):
            try:
                width = max(0.5, float(req_width))
                return [width]
            except (TypeError, ValueError):
                pass

        if underlying_price < 50:
            widths = [0.5, 1.0, 2.0]
        elif underlying_price < 150:
            widths = [1.0, 2.0, 5.0]
        else:
            widths = [2.0, 5.0, 10.0]

        return widths

    @staticmethod
    def _best_by_strike(contracts: list[Any]) -> dict[float, Any]:
        out: dict[float, Any] = {}
        for contract in contracts:
            strike = safe_float(getattr(contract, "strike", None))
            if strike is None:
                continue
            current = out.get(strike)
            if current is None:
                out[strike] = contract
                continue
            curr_oi = safe_float(getattr(current, "open_interest", None)) or 0.0
            new_oi = safe_float(getattr(contract, "open_interest", None)) or 0.0
            if new_oi > curr_oi:
                out[strike] = contract
        return out

    def build_candidates(self, inputs: dict[str, Any]) -> list[dict[str, Any]]:
        snapshots = inputs.get("snapshots") or []
        payload = inputs.get("request") or {}
        direction = str(payload.get("direction") or "both").strip().lower()
        if direction not in {"both", "call", "put"}:
            direction = "both"

        candidates: list[dict[str, Any]] = []
        max_candidates = int(payload.get("max_candidates") or 260)

        for snapshot in snapshots:
            symbol = str(snapshot.get("symbol") or "").upper()
            expiration = str(snapshot.get("expiration") or "")
            dte = int(snapshot.get("dte") or 0)
            underlying_price = self._to_float(snapshot.get("underlying_price"))
            contracts = snapshot.get("contracts") or []
            if not symbol or not expiration or underlying_price is None or not contracts:
                continue

            widths = self._choose_widths(underlying_price, payload)
            call_contracts = [c for c in contracts if str(getattr(c, "option_type", "")).lower() == "call"]
            put_contracts = [c for c in contracts if str(getattr(c, "option_type", "")).lower() == "put"]
            call_map = self._best_by_strike(call_contracts)
            put_map = self._best_by_strike(put_contracts)

            strike_window = underlying_price * 0.12

            if direction in {"both", "call"}:
                call_strikes = sorted(call_map.keys())
                for long_strike in call_strikes:
                    if abs(long_strike - underlying_price) > strike_window:
                        continue
                    for width in widths:
                        target = long_strike + width
                        short_strike = min((s for s in call_strikes if s > long_strike), key=lambda s: abs(s - target), default=None)
                        if short_strike is None:
                            continue
                        if abs((short_strike - long_strike) - width) > max(0.25, width * 0.4):
                            continue
                        candidates.append(
                            {
                                "strategy": "call_debit",
                                "spread_type": "call_debit",
                                "symbol": symbol,
                                "expiration": expiration,
                                "dte": dte,
                                "underlying_price": underlying_price,
                                "width": abs(short_strike - long_strike),
                                "long_strike": long_strike,
                                "short_strike": short_strike,
                                "long_leg": call_map.get(long_strike),
                                "short_leg": call_map.get(short_strike),
                                "snapshot": snapshot,
                            }
                        )
                        if len(candidates) >= max_candidates:
                            return candidates

            if direction in {"both", "put"}:
                put_strikes = sorted(put_map.keys())
                for long_strike in put_strikes:
                    if abs(long_strike - underlying_price) > strike_window:
                        continue
                    for width in widths:
                        target = long_strike - width
                        short_strike = min((s for s in put_strikes if s < long_strike), key=lambda s: abs(s - target), default=None)
                        if short_strike is None:
                            continue
                        if abs((long_strike - short_strike) - width) > max(0.25, width * 0.4):
                            continue
                        candidates.append(
                            {
                                "strategy": "put_debit",
                                "spread_type": "put_debit",
                                "symbol": symbol,
                                "expiration": expiration,
                                "dte": dte,
                                "underlying_price": underlying_price,
                                "width": abs(long_strike - short_strike),
                                "long_strike": long_strike,
                                "short_strike": short_strike,
                                "long_leg": put_map.get(long_strike),
                                "short_leg": put_map.get(short_strike),
                                "snapshot": snapshot,
                            }
                        )
                        if len(candidates) >= max_candidates:
                            return candidates

        return candidates

    @staticmethod
    def _realized_vol_from_prices(prices: list[float]) -> float | None:
        if not prices or len(prices) < 25:
            return None
        returns: list[float] = []
        for i in range(1, len(prices)):
            prev = float(prices[i - 1])
            cur = float(prices[i])
            if prev <= 0 or cur <= 0:
                continue
            returns.append(math.log(cur / prev))
        if len(returns) < 10:
            return None
        sigma = pstdev(returns)
        return float(sigma * math.sqrt(252.0))

    @staticmethod
    def _combo_spread_pct(long_leg: Any, short_leg: Any, debit: float | None) -> float | None:
        long_bid = safe_float(getattr(long_leg, "bid", None))
        long_ask = safe_float(getattr(long_leg, "ask", None))
        short_bid = safe_float(getattr(short_leg, "bid", None))
        short_ask = safe_float(getattr(short_leg, "ask", None))
        if long_bid is None or long_ask is None or short_bid is None or short_ask is None:
            return None
        spread_bid = long_bid - short_ask
        spread_ask = long_ask - short_bid
        if debit is None or debit <= 0:
            mid = (spread_bid + spread_ask) / 2.0
        else:
            mid = debit
        if mid <= 0:
            return None
        return max(0.0, (spread_ask - spread_bid) / mid)

    def enrich(self, candidates: list[dict[str, Any]], inputs: dict[str, Any]) -> list[dict[str, Any]]:
        payload = inputs.get("request") or {}
        policy = inputs.get("policy") or {}
        max_ivrv_for_buy = safe_float(policy.get("max_iv_rv_ratio_for_buying"))
        min_oi = int(policy.get("min_open_interest") or 100)
        min_vol = int(policy.get("min_volume") or 20)

        # Pre-compute realized vol once per unique snapshot to avoid redundant math
        _rv_cache: dict[int, float | None] = {}

        out: list[dict[str, Any]] = []

        for candidate in candidates:
            long_leg = candidate.get("long_leg")
            short_leg = candidate.get("short_leg")
            if long_leg is None or short_leg is None:
                continue

            strategy = str(candidate.get("strategy") or "call_debit")
            symbol = str(candidate.get("symbol") or "").upper()
            expiration = str(candidate.get("expiration") or "")
            dte = int(candidate.get("dte") or 0)
            width = float(candidate.get("width") or 0.0)
            underlying_price = float(candidate.get("underlying_price") or 0.0)
            snapshot = candidate.get("snapshot") or {}

            long_bid = safe_float(getattr(long_leg, "bid", None))
            long_ask = safe_float(getattr(long_leg, "ask", None))
            short_bid = safe_float(getattr(short_leg, "bid", None))
            short_ask = safe_float(getattr(short_leg, "ask", None))

            debit = None
            if long_ask is not None and short_bid is not None:
                debit = long_ask - short_bid
            elif long_bid is not None and short_ask is not None:
                debit = long_bid - short_ask

            if debit is None or debit <= 0 or width <= 0:
                continue

            max_profit = max(width - debit, 0.0) * 100.0
            max_loss = debit * 100.0
            return_on_risk = (max_profit / max_loss) if max_loss > 0 else 0.0

            long_strike = float(candidate.get("long_strike") or 0.0)
            short_strike = float(candidate.get("short_strike") or 0.0)
            break_even = (long_strike + debit) if strategy == "call_debit" else (long_strike - debit)
            debit_as_pct = debit / width if width > 0 else 1.0
            implied_prob_profit = self._clamp(1.0 - debit_as_pct)

            snap_id = id(snapshot)
            if snap_id not in _rv_cache:
                prices_history = snapshot.get("prices_history") or []
                _rv_cache[snap_id] = self._realized_vol_from_prices([float(x) for x in prices_history if self._to_float(x) is not None])
            rv = _rv_cache[snap_id]
            iv = safe_float(getattr(long_leg, "iv", None))
            if iv is None:
                iv = safe_float(getattr(short_leg, "iv", None))
            if iv is None and safe_float(getattr(long_leg, "iv", None)) is not None and safe_float(getattr(short_leg, "iv", None)) is not None:
                iv = (safe_float(getattr(long_leg, "iv", None)) + safe_float(getattr(short_leg, "iv", None))) / 2.0

            iv_rv_ratio = (iv / rv) if iv is not None and rv not in (None, 0) else None
            iv_pref = 0.5
            if iv_rv_ratio is not None:
                denom = max_ivrv_for_buy if (max_ivrv_for_buy is not None and max_ivrv_for_buy > 0) else 1.0
                iv_pref = self._clamp((denom - iv_rv_ratio) / denom)

            exp_move = None
            if iv is not None and underlying_price > 0 and dte > 0:
                exp_move = underlying_price * iv * math.sqrt(dte / 365.0)

            strike_distance = abs(short_strike - underlying_price)
            alignment = 0.5
            if exp_move and exp_move > 0:
                ratio = strike_distance / exp_move
                alignment = self._clamp(1.0 - abs(1.0 - ratio))

            long_theta = safe_float(getattr(long_leg, "theta", None))
            short_theta = safe_float(getattr(short_leg, "theta", None))
            theta_net = None
            theta_penalty = 0.0
            if long_theta is not None and short_theta is not None:
                theta_net = long_theta - short_theta
                theta_penalty = max(0.0, -theta_net)
            theta_penalty_norm = self._clamp(theta_penalty / 0.06)

            oi = min(
                int(safe_float(getattr(long_leg, "open_interest", None)) or 0),
                int(safe_float(getattr(short_leg, "open_interest", None)) or 0),
            )
            volume = min(
                int(safe_float(getattr(long_leg, "volume", None)) or 0),
                int(safe_float(getattr(short_leg, "volume", None)) or 0),
            )
            spread_pct = self._combo_spread_pct(long_leg, short_leg, debit)

            oi_score = self._clamp((oi / max(min_oi, 1)) / 2.0)
            vol_score = self._clamp((volume / max(min_vol, 1)) / 2.0)
            spread_score = 0.5
            if spread_pct is not None:
                spread_score = self._clamp(1.0 - (spread_pct / 0.30))
            liquidity_score = self._clamp((0.45 * oi_score) + (0.30 * vol_score) + (0.25 * spread_score))

            p = self._clamp((0.75 * implied_prob_profit) + (0.25 * alignment))
            ev = (p * max_profit) - ((1.0 - p) * max_loss)
            ev_to_risk = (ev / max_loss) if max_loss > 0 else 0.0

            edge_score = self._clamp((ev_to_risk + 0.20) / 0.60)
            ror_score = self._clamp(return_on_risk / 1.50)
            debit_penalty = self._clamp((debit_as_pct - 0.65) / 0.30)
            iv_penalty = self._clamp(((iv_rv_ratio or 1.0) - (max_ivrv_for_buy or 1.0)) / 1.0) if iv_rv_ratio is not None else 0.0

            bid_ask_spread_pct = spread_pct
            if bid_ask_spread_pct is None and safe_float(policy.get("max_bid_ask_spread_pct")) is not None:
                bid_ask_spread_pct = 99.0

            risk_fit = self._clamp(1.0 - (0.5 * debit_penalty + 0.3 * theta_penalty_norm + 0.2 * iv_penalty))
            conviction_score = self._clamp(edge_score * liquidity_score * risk_fit)

            rank_score = self._clamp(
                (0.34 * edge_score)
                + (0.22 * ror_score)
                + (0.22 * liquidity_score)
                + (0.22 * conviction_score)
                - (0.14 * debit_penalty)
                - (0.08 * theta_penalty_norm)
                - (0.08 * iv_penalty)
            )

            out.append(
                {
                    "strategy": strategy,
                    "spread_type": strategy,
                    "underlying": symbol,
                    "underlying_symbol": symbol,
                    "symbol": symbol,
                    "expiration": expiration,
                    "dte": dte,
                    "underlying_price": underlying_price,
                    "price": underlying_price,
                    "long_strike": long_strike,
                    "short_strike": short_strike,
                    "width": width,
                    "net_debit": debit,
                    "max_profit": max_profit,
                    "max_profit_per_contract": max_profit,
                    "max_loss": max_loss,
                    "max_loss_per_contract": max_loss,
                    "break_even": break_even,
                    "return_on_risk": return_on_risk,
                    "debit_as_pct_of_width": debit_as_pct,
                    "implied_prob_profit": implied_prob_profit,
                    "expected_move": exp_move,
                    "expected_move_alignment": alignment,
                    "strike_distance_vs_expected_move": (strike_distance / exp_move) if exp_move not in (None, 0) else None,
                    "theta_net": theta_net,
                    "theta_decay_penalty": theta_penalty,
                    "iv_rv_ratio": iv_rv_ratio,
                    "iv_rv_ratio_preference_for_buying": iv_pref,
                    "liquidity_score": liquidity_score,
                    "open_interest": oi,
                    "volume": volume,
                    "bid_ask_spread_pct": bid_ask_spread_pct,
                    "ev_per_contract": ev,
                    "ev_per_share": (ev / 100.0),
                    "ev_to_risk": ev_to_risk,
                    "conviction_score": conviction_score,
                    "rank_score": rank_score,
                    "contractsMultiplier": 100,
                    "selection_reasons": [],
                }
            )

        return out

    def evaluate(self, trade: dict[str, Any]) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        policy = trade.get("_policy") if isinstance(trade.get("_policy"), dict) else {}
        payload = trade.get("_request") if isinstance(trade.get("_request"), dict) else {}

        spread_pct_policy = safe_float(payload.get("max_bid_ask_spread_pct"))
        if spread_pct_policy is None:
            spread_pct_policy = safe_float(policy.get("max_bid_ask_spread_pct"))
        if spread_pct_policy is None:
            spread_pct_policy = 1.5
        spread_pct = safe_float(trade.get("bid_ask_spread_pct"))
        if spread_pct_policy is not None and spread_pct is not None:
            if (spread_pct * 100.0) > spread_pct_policy:
                reasons.append("spread_too_wide")

        min_oi = int(safe_float(payload.get("min_open_interest")) or 0)
        if min_oi <= 0:
            min_oi = max(int(safe_float(policy.get("min_open_interest")) or 0), 500)

        min_vol = int(safe_float(payload.get("min_volume")) or 0)
        if min_vol <= 0:
            min_vol = max(int(safe_float(policy.get("min_volume")) or 0), 50)

        if min_oi > 0 and int(safe_float(trade.get("open_interest")) or 0) < min_oi:
            reasons.append("open_interest_below_min")
        if min_vol > 0 and int(safe_float(trade.get("volume")) or 0) < min_vol:
            reasons.append("volume_below_min")

        debit_as_pct = safe_float(trade.get("debit_as_pct_of_width"))
        max_debit = safe_float((trade.get("_request") or {}).get("max_debit_pct_width"))
        if max_debit is None:
            max_debit = safe_float((trade.get("_request") or {}).get("max_debit"))
        if max_debit is None:
            max_debit = 0.45
        if debit_as_pct is None or debit_as_pct > max_debit:
            reasons.append("debit_too_close_to_width")

        return len(reasons) == 0, reasons

    def score(self, trade: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        rank_score = float(safe_float(trade.get("rank_score")) or 0.0)
        tie_breaks = {
            "edge": safe_float(trade.get("ev_to_risk")) or 0.0,
            "liquidity": safe_float(trade.get("liquidity_score")) or 0.0,
            "conviction": safe_float(trade.get("conviction_score")) or 0.0,
        }
        return rank_score, tie_breaks
