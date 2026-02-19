from __future__ import annotations

import logging
from typing import Any

from app.services.ranking import compute_rank_score, safe_float
from app.utils.dates import dte_ceil
from common.quant_analysis import enrich_trades_batch

logger = logging.getLogger("bentrade.credit_spread")

# Tolerance: reject candidates whose net_credit is within this many dollars
# of the spread width.  Prevents near-zero-profit or floating-point-edge trades.
_EPSILON = 0.01


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
        if not snapshots:
            snapshots = [inputs]
        payload = inputs.get("request") or {}

        # Read distance / width params once (same for all snapshots)
        distance_min = self._to_float(payload.get("distance_min")) or 0.01
        distance_max = self._to_float(payload.get("distance_max")) or 0.12
        width_min = self._to_float(payload.get("width_min"))
        width_max = self._to_float(payload.get("width_max"))
        if width_min is None:
            width_min = 1.0
        if width_max is None:
            width_max = 5.0
        if width_max < width_min:
            width_min, width_max = width_max, width_min
        base_widths = [1.0, 2.0, 3.0, 5.0, 10.0]
        target_widths = tuple(w for w in base_widths if width_min <= w <= width_max) or (2.0, 3.0, 5.0)

        candidates: list[dict[str, Any]] = []

        for snapshot in snapshots:
            contracts = snapshot.get("contracts") or []
            underlying_price = self._to_float(snapshot.get("underlying_price"))
            if underlying_price is None:
                continue

            puts = [
                c for c in contracts
                if str(getattr(c, "option_type", "")).lower() == "put"
                and self._to_float(getattr(c, "strike", None)) is not None
            ]
            if not puts:
                continue

            puts.sort(key=lambda c: float(c.strike), reverse=True)

            for short_leg in puts:
                short_strike = float(short_leg.strike)
                if short_strike >= underlying_price:
                    continue

                distance_pct = (underlying_price - short_strike) / underlying_price
                if distance_pct < distance_min or distance_pct > distance_max:
                    continue

                long_candidates = [
                    leg for leg in puts
                    if float(leg.strike) < short_strike
                ]
                if not long_candidates:
                    continue

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
                        "strategy": "put_credit_spread",
                        "width": width,
                        "snapshot": snapshot,
                    }
                )

                if len(candidates) >= 80:
                    break

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

            short_strike = self._to_float(getattr(short_leg, "strike", None))
            long_strike = self._to_float(getattr(long_leg, "strike", None))

            short_bid = self._to_float(getattr(short_leg, "bid", None))
            short_ask = self._to_float(getattr(short_leg, "ask", None))
            long_bid = self._to_float(getattr(long_leg, "bid", None))
            long_ask = self._to_float(getattr(long_leg, "ask", None))

            # -- Defensive quote checks before computing net_credit -----------
            rejection: str | None = None

            if short_bid is None or short_bid <= 0:
                rejection = "MISSING_QUOTES:short_bid"
            elif long_ask is None or long_ask <= 0:
                rejection = "MISSING_QUOTES:long_ask"
            elif short_ask is not None and short_ask < short_bid:
                rejection = "ASK_LT_BID:short_leg"
            elif long_bid is not None and long_ask < long_bid:
                rejection = "ASK_LT_BID:long_leg"

            if rejection:
                net_credit = None
            else:
                net_credit = short_bid - long_ask

            # Pre-validate net_credit versus width before sending to enrich_trade
            width = abs((short_strike or 0.0) - (long_strike or 0.0))
            if rejection is None and net_credit is not None:
                if net_credit <= 0:
                    rejection = "NON_POSITIVE_CREDIT"
                elif net_credit > width - _EPSILON:
                    rejection = "NET_CREDIT_GE_WIDTH"

            # Resolve delta safely: pass None (not 0.0) when missing
            raw_delta = self._to_float(getattr(short_leg, "delta", None))
            short_delta_abs = abs(raw_delta) if raw_delta is not None else None

            base_trades.append(
                {
                    "spread_type": "put_credit_spread",
                    "strategy": "put_credit_spread",
                    "underlying": symbol,
                    "underlying_symbol": symbol,
                    "expiration": expiration,
                    "dte": dte_ceil(expiration),
                    "short_strike": short_strike,
                    "long_strike": long_strike,
                    "underlying_price": underlying_price,
                    "price": underlying_price,
                    "vix": vix,
                    "bid": short_bid,
                    "ask": short_ask,
                    "open_interest": getattr(short_leg, "open_interest", None),
                    "volume": getattr(short_leg, "volume", None),
                    "short_delta_abs": short_delta_abs,
                    "iv": self._to_float(getattr(short_leg, "iv", None)),
                    "implied_vol": self._to_float(getattr(short_leg, "iv", None)),
                    "width": width,
                    "net_credit": net_credit,
                    "contractsMultiplier": 100,
                    # -- enrichment debug fields (consumed by evaluate, not persisted) --
                    "_quote_rejection": rejection,
                    "_short_bid": short_bid,
                    "_short_ask": short_ask,
                    "_long_bid": long_bid,
                    "_long_ask": long_ask,
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

        # -- Pre-enrichment quote rejection (set during enrich()) --
        quote_rej = trade.get("_quote_rejection")
        if quote_rej:
            return False, [quote_rej]

        # -- CreditSpread metric failure (set during enrich_trade()) --
        data_warn = trade.get("data_warning") or ""
        if "CreditSpread metrics unavailable" in data_warn:
            reasons.append("CREDIT_SPREAD_METRICS_FAILED")

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
