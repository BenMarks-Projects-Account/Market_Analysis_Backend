from __future__ import annotations

from typing import Any

from app.services.ranking import compute_rank_score, safe_float
from app.utils.dates import dte_ceil
from common.quant_analysis import enrich_trades_batch


class CreditSpreadStrategyPlugin:
    id = "credit_spread"
    display_name = "Credit Spread"

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def build_candidates(self, inputs: dict[str, Any]) -> list[dict[str, Any]]:
        snapshots = inputs.get("snapshots") or []
        snapshot = snapshots[0] if snapshots else inputs
        payload = inputs.get("request") or {}
        contracts = snapshot.get("contracts") or []
        underlying_price = self._to_float(snapshot.get("underlying_price"))
        if underlying_price is None:
            return []

        puts = [
            c for c in contracts
            if str(getattr(c, "option_type", "")).lower() == "put"
            and self._to_float(getattr(c, "strike", None)) is not None
        ]
        if not puts:
            return []

        puts.sort(key=lambda c: float(c.strike), reverse=True)

        candidates: list[dict[str, Any]] = []
        for short_leg in puts:
            short_strike = float(short_leg.strike)
            if short_strike >= underlying_price:
                continue

            distance_pct = (underlying_price - short_strike) / underlying_price
            if distance_pct < 0.01 or distance_pct > 0.12:
                continue

            long_candidates = [
                leg for leg in puts
                if float(leg.strike) < short_strike
            ]
            if not long_candidates:
                continue

            width_min = self._to_float(payload.get("width_min"))
            width_max = self._to_float(payload.get("width_max"))
            if width_min is None:
                width_min = 1.0
            if width_max is None:
                width_max = 5.0
            if width_max < width_min:
                width_min, width_max = width_max, width_min

            base_widths = [1.0, 2.0, 3.0, 5.0, 10.0]
            target_widths = tuple(width for width in base_widths if width_min <= width <= width_max) or (2.0, 3.0, 5.0)
            chosen_long = None
            for width in target_widths:
                chosen_long = min(
                    long_candidates,
                    key=lambda leg: abs((short_strike - float(leg.strike)) - width),
                    default=None,
                )
                if chosen_long is not None:
                    break
            if chosen_long is None:
                continue

            width = abs(short_strike - float(chosen_long.strike))
            if width <= 0:
                continue

            candidates.append(
                {
                    "short_leg": short_leg,
                    "long_leg": chosen_long,
                    "strategy": "credit_put_spread",
                    "width": width,
                    "snapshot": snapshot,
                }
            )

            if len(candidates) >= 80:
                break

        return candidates

    def enrich(self, candidates: list[dict[str, Any]], inputs: dict[str, Any]) -> list[dict[str, Any]]:
        if not candidates:
            return []

        symbol = str(inputs.get("symbol") or "").upper()
        expiration = str(inputs.get("expiration") or "")
        underlying_price = self._to_float(inputs.get("underlying_price"))
        vix = self._to_float(inputs.get("vix"))
        prices_history = inputs.get("prices_history") or []

        base_trades: list[dict[str, Any]] = []
        for candidate in candidates:
            short_leg = candidate.get("short_leg")
            long_leg = candidate.get("long_leg")
            if short_leg is None or long_leg is None:
                continue

            snapshot = candidate.get("snapshot") if isinstance(candidate.get("snapshot"), dict) else inputs
            symbol = str(snapshot.get("symbol") or inputs.get("symbol") or "").upper()
            expiration = str(snapshot.get("expiration") or inputs.get("expiration") or "")
            underlying_price = self._to_float(snapshot.get("underlying_price") if isinstance(snapshot, dict) else inputs.get("underlying_price"))
            vix = self._to_float(snapshot.get("vix") if isinstance(snapshot, dict) else inputs.get("vix"))

            short_bid = self._to_float(getattr(short_leg, "bid", None))
            long_ask = self._to_float(getattr(long_leg, "ask", None))
            net_credit = None
            if short_bid is not None and long_ask is not None:
                net_credit = short_bid - long_ask

            base_trades.append(
                {
                    "spread_type": "put_credit",
                    "strategy": "credit_put_spread",
                    "underlying": symbol,
                    "underlying_symbol": symbol,
                    "expiration": expiration,
                    "dte": dte_ceil(expiration),
                    "short_strike": self._to_float(getattr(short_leg, "strike", None)),
                    "long_strike": self._to_float(getattr(long_leg, "strike", None)),
                    "underlying_price": underlying_price,
                    "price": underlying_price,
                    "vix": vix,
                    "bid": short_bid,
                    "ask": self._to_float(getattr(short_leg, "ask", None)),
                    "open_interest": getattr(short_leg, "open_interest", None),
                    "volume": getattr(short_leg, "volume", None),
                    "short_delta_abs": abs(self._to_float(getattr(short_leg, "delta", None)) or 0.0),
                    "iv": self._to_float(getattr(short_leg, "iv", None)),
                    "implied_vol": self._to_float(getattr(short_leg, "iv", None)),
                    "width": abs((self._to_float(getattr(short_leg, "strike", None)) or 0.0) - (self._to_float(getattr(long_leg, "strike", None)) or 0.0)),
                    "net_credit": net_credit,
                    "contractsMultiplier": 100,
                }
            )

        if not base_trades:
            return []

        enriched = enrich_trades_batch(
            base_trades,
            prices_history=[float(x) for x in prices_history if self._to_float(x) is not None],
            vix=vix,
            iv_low=None,
            iv_high=None,
        )
        return [row for row in (enriched or []) if isinstance(row, dict)]

    def evaluate(self, trade: dict[str, Any]) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        policy = trade.get("_policy") if isinstance(trade.get("_policy"), dict) else {}
        payload = trade.get("_request") if isinstance(trade.get("_request"), dict) else {}

        pop = safe_float(trade.get("p_win_used") or trade.get("pop_delta_approx"))
        ev = safe_float(trade.get("ev_per_share") or trade.get("expected_value"))
        ev_to_risk = safe_float(trade.get("ev_to_risk"))
        ror = safe_float(trade.get("return_on_risk"))
        width = safe_float(trade.get("width"))
        net_credit = safe_float(trade.get("net_credit"))
        spread_pct = safe_float(trade.get("bid_ask_spread_pct"))
        open_interest = int(safe_float(trade.get("open_interest")) or 0)
        volume = int(safe_float(trade.get("volume")) or 0)

        min_pop = safe_float(payload.get("min_pop"))
        if min_pop is None:
            min_pop = 0.65

        min_ev_to_risk = safe_float(payload.get("min_ev_to_risk"))
        if min_ev_to_risk is None:
            min_ev_to_risk = 0.02

        spread_pct_limit = safe_float(payload.get("max_bid_ask_spread_pct"))
        if spread_pct_limit is None:
            spread_pct_limit = safe_float(policy.get("max_bid_ask_spread_pct"))
        if spread_pct_limit is None:
            spread_pct_limit = 1.5

        min_oi = int(safe_float(payload.get("min_open_interest")) or 0)
        if min_oi <= 0:
            min_oi = max(int(safe_float(policy.get("min_open_interest")) or 0), 500)

        min_vol = int(safe_float(payload.get("min_volume")) or 0)
        if min_vol <= 0:
            min_vol = max(int(safe_float(policy.get("min_volume")) or 0), 50)

        if pop is not None and pop < min_pop:
            reasons.append("pop_below_floor")
        if ev_to_risk is not None and ev_to_risk < min_ev_to_risk:
            reasons.append("ev_to_risk_below_floor")
        elif ev is not None and ev < -0.05:
            reasons.append("ev_negative")
        if ror is not None and ror < 0.01:
            reasons.append("ror_below_floor")
        if width is None or width <= 0:
            reasons.append("invalid_width")
        if net_credit is None or net_credit <= 0:
            reasons.append("non_positive_credit")
        if spread_pct is not None and (spread_pct * 100.0) > spread_pct_limit:
            reasons.append("spread_too_wide")
        if open_interest < min_oi:
            reasons.append("open_interest_below_min")
        if volume < min_vol:
            reasons.append("volume_below_min")

        return len(reasons) == 0, reasons

    def score(self, trade: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        rank_score = float(compute_rank_score(trade))
        tie_breaks = {
            "edge": safe_float(trade.get("ev_to_risk")) or 0.0,
            "pop": safe_float(trade.get("p_win_used") or trade.get("pop_delta_approx")) or 0.0,
            "liq": -(safe_float(trade.get("bid_ask_spread_pct")) or 1.0),
        }
        return rank_score, tie_breaks
