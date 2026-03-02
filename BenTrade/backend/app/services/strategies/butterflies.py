from __future__ import annotations

import logging
import math
from statistics import pstdev
from typing import Any

from app.services.ranking import safe_float
from app.services.strategies.base import (
    POP_SOURCE_NONE,
    POP_SOURCE_NORMAL_CDF,
    StrategyPlugin,
)
from app.utils.expected_fill import apply_expected_fill

logger = logging.getLogger("bentrade.butterflies")


class ButterfliesStrategyPlugin(StrategyPlugin):
    id = "butterflies"
    display_name = "Butterfly Analysis"

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
    def _normal_cdf(x: float) -> float:
        """Standard normal cumulative distribution function."""
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

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

    @staticmethod
    def _step_size(strikes: list[float]) -> float:
        if len(strikes) < 2:
            return 1.0
        diffs: list[float] = []
        for idx in range(1, len(strikes)):
            diff = round(abs(strikes[idx] - strikes[idx - 1]), 6)
            if diff > 0:
                diffs.append(diff)
        if not diffs:
            return 1.0
        diffs.sort()
        return max(0.5, diffs[0])

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

    def _expected_move(self, spot: float, dte: int, iv_guess: float | None, rv: float | None) -> float:
        vol = iv_guess if iv_guess not in (None, 0) else rv
        if vol in (None, 0) or dte <= 0:
            return max(spot * 0.02, 1.0)
        return max(spot * float(vol) * math.sqrt(dte / 365.0), 0.5)

    @staticmethod
    def _pick_nearest(strikes: list[float], target: float) -> float | None:
        if not strikes:
            return None
        return min(strikes, key=lambda s: abs(float(s) - target))

    def _center_target(self, snapshot: dict[str, Any], option_side: str, center_mode: str, expected_move: float) -> float:
        spot = float(snapshot.get("underlying_price") or 0.0)
        if center_mode == "expected_move":
            if option_side == "call":
                return spot + expected_move
            if option_side == "put":
                return spot - expected_move
            return spot

        if center_mode == "forecast":
            prices = [float(x) for x in (snapshot.get("prices_history") or []) if self._to_float(x) is not None]
            if len(prices) >= 12 and spot > 0:
                returns: list[float] = []
                for idx in range(1, len(prices)):
                    prev = prices[idx - 1]
                    cur = prices[idx]
                    if prev <= 0 or cur <= 0:
                        continue
                    returns.append(math.log(cur / prev))
                if returns:
                    drift = sum(returns[-15:]) / min(len(returns), 15)
                    dte = int(snapshot.get("dte") or 0)
                    projected = spot * math.exp(drift * max(1, dte))
                    lower = spot - (1.25 * expected_move)
                    upper = spot + (1.25 * expected_move)
                    return min(max(projected, lower), upper)

        return spot

    def build_candidates(self, inputs: dict[str, Any]) -> list[dict[str, Any]]:
        payload = inputs.get("request") or {}
        snapshots = inputs.get("snapshots") or []

        butterfly_type = str(payload.get("butterfly_type") or "debit").strip().lower()
        if butterfly_type not in {"debit", "iron", "both"}:
            butterfly_type = "debit"

        option_side = str(payload.get("option_side") or "call").strip().lower()
        if option_side not in {"call", "put", "both"}:
            option_side = "call"

        center_mode = str(payload.get("center_mode") or "spot").strip().lower()
        if center_mode not in {"spot", "forecast", "expected_move"}:
            center_mode = "spot"

        width_input = self._to_float(payload.get("width"))
        # Safety ceiling only — preset max_candidates is applied centrally
        # by select_top_n() in strategy_service.generate().
        max_candidates = int(inputs.get("_generation_cap") or 20_000)
        candidates: list[dict[str, Any]] = []

        for snapshot in snapshots:
            symbol = str(snapshot.get("symbol") or "").upper()
            expiration = str(snapshot.get("expiration") or "")
            dte = int(snapshot.get("dte") or 0)
            spot = self._to_float(snapshot.get("underlying_price"))
            contracts = snapshot.get("contracts") or []
            if not symbol or not expiration or spot is None or dte <= 0 or not contracts:
                continue

            call_map = self._strike_map(contracts, "call")
            put_map = self._strike_map(contracts, "put")
            call_strikes = sorted(call_map.keys())
            put_strikes = sorted(put_map.keys())
            if not call_strikes or not put_strikes:
                continue

            iv_values = []
            for leg in list(call_map.values())[:25] + list(put_map.values())[:25]:
                iv = self._to_float(getattr(leg, "iv", None))
                if iv not in (None, 0):
                    iv_values.append(iv)
            iv_guess = (sum(iv_values) / len(iv_values)) if iv_values else None
            prices = [float(x) for x in (snapshot.get("prices_history") or []) if self._to_float(x) is not None]
            rv = self._realized_vol(prices)
            expected_move = self._expected_move(float(spot), dte, iv_guess, rv)

            step = self._step_size(call_strikes if len(call_strikes) >= 2 else put_strikes)
            widths = [width_input] if width_input not in (None, 0) else [1.0 * step, 2.0 * step, 5.0 * step]

            sides = [option_side] if option_side != "both" else ["call", "put"]
            types = [butterfly_type] if butterfly_type != "both" else ["debit", "iron"]

            for btype in types:
                for side in sides:
                    if btype == "iron" and side == "both":
                        continue

                    strikes_for_center = put_strikes if side == "put" else call_strikes
                    center_target = self._center_target(snapshot, side, center_mode, expected_move)
                    center = self._pick_nearest(strikes_for_center, center_target)
                    if center is None:
                        continue

                    for width in widths:
                        wing = max(step, float(width))
                        lower_target = center - wing
                        upper_target = center + wing

                        lower = self._pick_nearest(put_strikes if btype == "iron" else strikes_for_center, lower_target)
                        upper = self._pick_nearest(call_strikes if btype == "iron" else strikes_for_center, upper_target)
                        if lower is None or upper is None:
                            continue
                        if lower >= center or upper <= center:
                            continue

                        if btype == "debit":
                            if side == "call":
                                lower_leg = call_map.get(lower)
                                center_leg = call_map.get(center)
                                upper_leg = call_map.get(upper)
                                spread_type = "debit_call_butterfly"
                            else:
                                lower_leg = put_map.get(lower)
                                center_leg = put_map.get(center)
                                upper_leg = put_map.get(upper)
                                spread_type = "debit_put_butterfly"
                            if not all([lower_leg, center_leg, upper_leg]):
                                continue

                            candidates.append(
                                {
                                    "strategy": "butterflies",
                                    "spread_type": spread_type,
                                    "butterfly_type": "debit",
                                    "option_side": side,
                                    "symbol": symbol,
                                    "expiration": expiration,
                                    "dte": dte,
                                    "underlying_price": float(spot),
                                    "center_strike": center,
                                    "lower_strike": lower,
                                    "upper_strike": upper,
                                    "wing_width": min(center - lower, upper - center),
                                    "expected_move": expected_move,
                                    "center_mode": center_mode,
                                    "lower_leg": lower_leg,
                                    "center_leg": center_leg,
                                    "upper_leg": upper_leg,
                                    "snapshot": snapshot,
                                }
                            )
                        else:
                            put_long = put_map.get(lower)
                            put_short = put_map.get(center)
                            call_short = call_map.get(center)
                            call_long = call_map.get(upper)
                            if not all([put_long, put_short, call_short, call_long]):
                                continue

                            candidates.append(
                                {
                                    "strategy": "butterflies",
                                    "spread_type": "iron_butterfly",
                                    "butterfly_type": "iron",
                                    "option_side": "neutral",
                                    "symbol": symbol,
                                    "expiration": expiration,
                                    "dte": dte,
                                    "underlying_price": float(spot),
                                    "center_strike": center,
                                    "lower_strike": lower,
                                    "upper_strike": upper,
                                    "wing_width": min(center - lower, upper - center),
                                    "expected_move": expected_move,
                                    "center_mode": center_mode,
                                    "put_long_leg": put_long,
                                    "put_short_leg": put_short,
                                    "call_short_leg": call_short,
                                    "call_long_leg": call_long,
                                    "snapshot": snapshot,
                                }
                            )

                        if len(candidates) >= max_candidates:
                            return candidates

        return candidates

    def _leg_liquidity(self, leg: Any) -> tuple[int, int, float]:
        oi = int(safe_float(getattr(leg, "open_interest", None)) or 0)
        vol = int(safe_float(getattr(leg, "volume", None)) or 0)
        bid = safe_float(getattr(leg, "bid", None)) or 0.0
        ask = safe_float(getattr(leg, "ask", None)) or bid
        spread = max(0.0, ask - bid)
        return oi, vol, spread

    def enrich(self, candidates: list[dict[str, Any]], inputs: dict[str, Any]) -> list[dict[str, Any]]:
        policy = inputs.get("policy") or {}

        out: list[dict[str, Any]] = []
        for row in candidates:
            spread_type = str(row.get("spread_type") or "")
            symbol = str(row.get("symbol") or "").upper()
            expiration = str(row.get("expiration") or "")
            dte = int(row.get("dte") or 0)
            spot = float(row.get("underlying_price") or 0.0)
            center = float(row.get("center_strike") or 0.0)
            lower = float(row.get("lower_strike") or 0.0)
            upper = float(row.get("upper_strike") or 0.0)
            wing_width = float(row.get("wing_width") or 0.0)
            expected_move = float(row.get("expected_move") or max(spot * 0.02, 1.0))

            total_debit = None
            total_credit = None
            max_profit = None
            max_loss = None
            break_even_low = None
            break_even_high = None
            net_gamma = 0.0
            net_theta = 0.0
            leg_metrics: list[tuple[int, int, float]] = []

            # ── Spread pricing fields (new) ───────────────────────────
            # spread_mid     = mid-based debit (best estimate)
            # spread_natural  = worst-case fill debit (buy at ask, sell at bid)
            # spread_mark     = conservative mid (mirrors spread_mid)
            # All are per-share values for a 1x2x1 butterfly.
            spread_mid: float | None = None
            spread_natural: float | None = None
            spread_mark: float | None = None
            # execution_invalid: set True when pricing cannot be trusted
            execution_invalid = False
            execution_invalid_reason: str | None = None

            # Per-leg quote fields for output
            _leg_quotes: dict[str, dict[str, float | None]] = {}

            if spread_type in {"debit_call_butterfly", "debit_put_butterfly"}:
                lower_leg = row.get("lower_leg")
                center_leg = row.get("center_leg")
                upper_leg = row.get("upper_leg")
                if not all([lower_leg, center_leg, upper_leg]):
                    continue

                lower_ask = safe_float(getattr(lower_leg, "ask", None))
                lower_bid = safe_float(getattr(lower_leg, "bid", None))
                center_bid = safe_float(getattr(center_leg, "bid", None))
                center_ask = safe_float(getattr(center_leg, "ask", None))
                upper_ask = safe_float(getattr(upper_leg, "ask", None))
                upper_bid = safe_float(getattr(upper_leg, "bid", None))

                # Store per-leg quotes for output
                _leg_quotes = {
                    "lower": {"bid": lower_bid, "ask": lower_ask},
                    "center": {"bid": center_bid, "ask": center_ask},
                    "upper": {"bid": upper_bid, "ask": upper_ask},
                }

                # ── Compute per-leg mids ──────────────────────────────
                # mid = (bid + ask) / 2; None if either is missing
                lower_mid = (
                    (lower_bid + lower_ask) / 2.0
                    if lower_bid is not None and lower_ask is not None else None
                )
                center_mid = (
                    (center_bid + center_ask) / 2.0
                    if center_bid is not None and center_ask is not None else None
                )
                upper_mid = (
                    (upper_bid + upper_ask) / 2.0
                    if upper_bid is not None and upper_ask is not None else None
                )

                # ── Spread pricing: 1x2x1 debit butterfly ──────────────
                # spread_mid     = mid(lower) + mid(upper) - 2*mid(center)
                # spread_natural  = ask(lower) + ask(upper) - 2*bid(center)
                #                   (worst-case fill: buy at ask, sell at bid)
                # spread_mark    = spread_mid (mirrors mid initially)
                if lower_mid is not None and center_mid is not None and upper_mid is not None:
                    spread_mid = round(lower_mid + upper_mid - 2.0 * center_mid, 4)
                    spread_mark = spread_mid

                if (lower_ask is not None and upper_ask is not None
                        and center_bid is not None):
                    spread_natural = round(
                        lower_ask + upper_ask - 2.0 * center_bid, 4
                    )

                # ── Determine net_debit ───────────────────────────────
                # Primary: spread_mid (best estimate).
                # Fallback to spread_natural if mid unavailable.
                if spread_mid is not None:
                    total_debit = spread_mid
                elif spread_natural is not None:
                    total_debit = spread_natural
                else:
                    # All pricing unavailable
                    execution_invalid = True
                    execution_invalid_reason = "pricing_unavailable"

                # Validate debit sanity
                if total_debit is not None:
                    if total_debit <= 0:
                        execution_invalid = True
                        execution_invalid_reason = "net_debit_non_positive"
                    elif wing_width > 0 and total_debit >= wing_width:
                        execution_invalid = True
                        execution_invalid_reason = "net_debit_ge_wing_width"

                # Check for missing leg quotes
                _any_leg_missing = any(
                    q["bid"] is None or q["ask"] is None
                    for q in _leg_quotes.values()
                )
                if _any_leg_missing and not execution_invalid:
                    execution_invalid = True
                    execution_invalid_reason = "leg_quote_missing"

                if execution_invalid or total_debit is None or total_debit < 0.01:
                    # Still emit the row with execution_invalid=True so
                    # evaluate can reject it with a clear reason code.
                    # Use fallback values for downstream fields.
                    if total_debit is None or total_debit <= 0:
                        total_debit = total_debit  # preserve None
                        max_profit = None
                        max_loss = None
                    else:
                        max_profit = max(wing_width - total_debit, 0.0) * 100.0
                        max_loss = total_debit * 100.0
                    if not execution_invalid:
                        execution_invalid = True
                        execution_invalid_reason = "net_debit_too_small"
                else:
                    max_profit = max(wing_width - total_debit, 0.0) * 100.0
                    max_loss = total_debit * 100.0

                if total_debit is not None and total_debit > 0:
                    break_even_low = lower + total_debit
                    break_even_high = upper - total_debit

                gamma_lower = safe_float(getattr(lower_leg, "gamma", None)) or 0.0
                gamma_center = safe_float(getattr(center_leg, "gamma", None)) or 0.0
                gamma_upper = safe_float(getattr(upper_leg, "gamma", None)) or 0.0
                net_gamma = gamma_lower + gamma_upper - (2.0 * gamma_center)

                theta_lower = safe_float(getattr(lower_leg, "theta", None)) or 0.0
                theta_center = safe_float(getattr(center_leg, "theta", None)) or 0.0
                theta_upper = safe_float(getattr(upper_leg, "theta", None)) or 0.0
                net_theta = theta_lower + theta_upper - (2.0 * theta_center)

                leg_metrics.extend([self._leg_liquidity(lower_leg), self._leg_liquidity(center_leg), self._leg_liquidity(upper_leg)])

                _td_for_payoff = total_debit if total_debit is not None and total_debit > 0 else 0.0

                def payoff_at(price: float, _td: float = _td_for_payoff, _ww: float = wing_width, _c: float = center, _ml: float = (max_loss or 0.0)) -> float:
                    if _ww <= 0 or _td <= 0:
                        return -_ml
                    distance = abs(price - _c)
                    intrinsic = max(0.0, _ww - distance)
                    return (intrinsic - _td) * 100.0

                cost_efficiency = (max_profit / max(max_loss, 0.01)) if max_profit is not None and max_loss is not None and max_loss > 0 else 0.0
                debit_vs_expected_move = (total_debit / max(expected_move, 0.1)) if total_debit is not None else 0.0
                net_cost = total_debit if total_debit is not None else 0.0
            else:
                put_long = row.get("put_long_leg")
                put_short = row.get("put_short_leg")
                call_short = row.get("call_short_leg")
                call_long = row.get("call_long_leg")
                if not all([put_long, put_short, call_short, call_long]):
                    continue

                put_short_bid = safe_float(getattr(put_short, "bid", None))
                put_short_ask = safe_float(getattr(put_short, "ask", None))
                call_short_bid = safe_float(getattr(call_short, "bid", None))
                call_short_ask = safe_float(getattr(call_short, "ask", None))
                put_long_ask = safe_float(getattr(put_long, "ask", None))
                put_long_bid = safe_float(getattr(put_long, "bid", None))
                call_long_ask = safe_float(getattr(call_long, "ask", None))
                call_long_bid = safe_float(getattr(call_long, "bid", None))

                _leg_quotes = {
                    "put_long":   {"bid": put_long_bid,   "ask": put_long_ask},
                    "put_short":  {"bid": put_short_bid,  "ask": put_short_ask},
                    "call_short": {"bid": call_short_bid,  "ask": call_short_ask},
                    "call_long":  {"bid": call_long_bid,   "ask": call_long_ask},
                }

                # Iron butterfly: credit spread
                # spread_mid = mid(put_short) + mid(call_short) - mid(put_long) - mid(call_long)
                # spread_natural = bid(put_short) + bid(call_short) - ask(put_long) - ask(call_long)
                _ps_mid = ((put_short_bid + put_short_ask) / 2.0
                           if put_short_bid is not None and put_short_ask is not None else None)
                _cs_mid = ((call_short_bid + call_short_ask) / 2.0
                           if call_short_bid is not None and call_short_ask is not None else None)
                _pl_mid = ((put_long_bid + put_long_ask) / 2.0
                           if put_long_bid is not None and put_long_ask is not None else None)
                _cl_mid = ((call_long_bid + call_long_ask) / 2.0
                           if call_long_bid is not None and call_long_ask is not None else None)

                if all(m is not None for m in (_ps_mid, _cs_mid, _pl_mid, _cl_mid)):
                    spread_mid = round(_ps_mid + _cs_mid - _pl_mid - _cl_mid, 4)
                    spread_mark = spread_mid

                if None not in (put_short_bid, call_short_bid, put_long_ask, call_long_ask):
                    spread_natural = round(
                        float(put_short_bid) + float(call_short_bid)
                        - float(put_long_ask) - float(call_long_ask), 4
                    )
                    total_credit = spread_natural
                elif spread_mid is not None:
                    total_credit = spread_mid

                _any_leg_missing = any(
                    q["bid"] is None or q["ask"] is None
                    for q in _leg_quotes.values()
                )
                if _any_leg_missing:
                    execution_invalid = True
                    execution_invalid_reason = "leg_quote_missing"

                if total_credit is None or total_credit <= 0:
                    if not execution_invalid:
                        execution_invalid = True
                        execution_invalid_reason = "non_positive_credit"
                    # Still emit row for evaluate rejection
                    max_profit = None
                    max_loss = None
                else:
                    max_profit = total_credit * 100.0
                    max_loss = max(wing_width - total_credit, 0.0) * 100.0
                    break_even_low = center - total_credit
                    break_even_high = center + total_credit

                gamma_put_long = safe_float(getattr(put_long, "gamma", None)) or 0.0
                gamma_put_short = safe_float(getattr(put_short, "gamma", None)) or 0.0
                gamma_call_short = safe_float(getattr(call_short, "gamma", None)) or 0.0
                gamma_call_long = safe_float(getattr(call_long, "gamma", None)) or 0.0
                net_gamma = gamma_put_long - gamma_put_short - gamma_call_short + gamma_call_long

                theta_put_long = safe_float(getattr(put_long, "theta", None)) or 0.0
                theta_put_short = safe_float(getattr(put_short, "theta", None)) or 0.0
                theta_call_short = safe_float(getattr(call_short, "theta", None)) or 0.0
                theta_call_long = safe_float(getattr(call_long, "theta", None)) or 0.0
                net_theta = theta_put_long - theta_put_short - theta_call_short + theta_call_long

                leg_metrics.extend([
                    self._leg_liquidity(put_long),
                    self._leg_liquidity(put_short),
                    self._leg_liquidity(call_short),
                    self._leg_liquidity(call_long),
                ])

                _tc_for_payoff = total_credit if total_credit is not None and total_credit > 0 else 0.0

                def payoff_at(price: float, _tc: float = _tc_for_payoff, _ww: float = wing_width, _c: float = center, _l: float = lower, _u: float = upper, _ml: float = (max_loss or 0.0)) -> float:
                    if _ww <= 0 or _tc <= 0:
                        return -_ml
                    if price <= _l:
                        return -_ml
                    if price < _c:
                        return ((price - _l) - max(_ww - _tc, 0.0)) * 100.0
                    if price <= _u:
                        return ((_u - price) - max(_ww - _tc, 0.0)) * 100.0
                    return -_ml

                cost_efficiency = (max_profit / max(max_loss, 0.01)) if max_profit is not None and max_loss is not None and max_loss > 0 else 0.0
                debit_vs_expected_move = 0.0
                net_cost = -(total_credit or 0.0)

            # ── Probabilities ──────────────────────────────────────
            # prob_touch_center: set to None until a proper barrier-option
            # touch model is implemented.  DO NOT hardcode to 1.0.
            prob_touch_center: float | None = None

            # pop_butterfly (POP): probability underlying finishes between
            # break-evens at expiration via normal CDF.
            pop: float | None = None
            pop_model_used = POP_SOURCE_NONE
            if (expected_move > 0 and break_even_low is not None
                    and break_even_high is not None and not execution_invalid):
                z_high = (break_even_high - spot) / expected_move
                z_low = (break_even_low - spot) / expected_move
                pop = self._clamp(self._normal_cdf(z_high) - self._normal_cdf(z_low))
                pop_model_used = POP_SOURCE_NORMAL_CDF

            # ── Expected value via numerical integration ──────────────
            expected_value: float | None = None
            ev_to_risk: float | None = None
            if not execution_invalid and max_loss is not None and max_loss > 0:
                n_ev_steps = 201
                half_range = 4.0 * max(expected_move, spot * 0.01)
                ev_step = (2.0 * half_range) / (n_ev_steps - 1)
                sigma = max(expected_move, 0.01)
                ev_sum = 0.0
                for ev_idx in range(n_ev_steps):
                    p_point = max(0.01, spot - half_range + ev_idx * ev_step)
                    z_ev = (p_point - spot) / sigma
                    pdf_w = math.exp(-0.5 * z_ev * z_ev) / (sigma * math.sqrt(2.0 * math.pi))
                    ev_sum += payoff_at(p_point) * pdf_w * ev_step
                expected_value = ev_sum
                ev_to_risk = expected_value / max_loss if max_loss > 0 else None

            center_alignment = self._clamp(1.0 - (abs(spot - center) / max(expected_move * 1.25, 0.25)))

            # ── debit_pct_of_width (lottery ticket protection) ─────
            # Derived metric: net_debit / wing_width.
            # High values = overpriced butterfly; low values = lottery ticket.
            # Used by evaluate() gate max_debit_pct_width.
            debit_pct_of_width: float | None = None
            if total_debit is not None and wing_width > 0:
                debit_pct_of_width = total_debit / wing_width

            min_oi = min([m[0] for m in leg_metrics]) if leg_metrics else 0
            min_vol = min([m[1] for m in leg_metrics]) if leg_metrics else 0
            worst_spread = max([m[2] for m in leg_metrics]) if leg_metrics else 99.0

            oi_ref = max(float(policy.get("min_open_interest") or 100), 1.0)
            vol_ref = max(float(policy.get("min_volume") or 20), 1.0)
            oi_score = self._clamp((min_oi / oi_ref) / 1.5)
            vol_score = self._clamp((min_vol / vol_ref) / 1.5)
            spread_score = self._clamp(1.0 - (worst_spread / max(abs(net_cost) * 2.0, 0.25)))
            liquidity_score = self._clamp((0.45 * oi_score) + (0.30 * vol_score) + (0.25 * spread_score))

            gamma_peak_score = self._clamp(abs(net_gamma) / 0.08)
            time_decay_risk = self._clamp(max(0.0, -net_theta) / 0.08)
            peak_profit_at_center = max_profit
            payoff_slope = -(max_profit / max(wing_width, 0.01)) if max_profit is not None else 0.0
            return_on_risk = (max_profit / max(max_loss, 0.01)) if max_profit is not None and max_loss is not None and max_loss > 0 else 0.0

            # ── Ranking score ──────────────────────────────────────
            # Trades with missing pricing get rank_score = 0 to prevent
            # structurally broken candidates from floating to the top.
            if execution_invalid or max_profit is None or max_loss is None or max_loss <= 0:
                rank_score = 0.0
            else:
                debit_vs_em_penalty = self._clamp((debit_vs_expected_move - 0.45) / 0.65)
                ev_score = self._clamp(((expected_value or 0.0) + (0.20 * max_loss)) / max(max_profit + max_loss, 1.0))
                efficiency_score = self._clamp(cost_efficiency / 2.0)
                # pop_penalty: penalize missing or very low POP
                _pop_val = pop if pop is not None else 0.0
                pop_penalty = self._clamp((0.30 - _pop_val) / 0.30)

                rank_score = self._clamp(
                    (0.30 * efficiency_score)
                    + (0.22 * center_alignment)
                    + (0.22 * liquidity_score)
                    + (0.12 * ev_score)
                    + (0.14 * gamma_peak_score)
                    - (0.15 * debit_vs_em_penalty)
                    - (0.14 * pop_penalty)
                    - (0.08 * time_decay_risk)
                )

            butterfly_key = (
                f"{symbol}|{expiration}|{spread_type}|"
                f"L{lower}|C{center}|U{upper}|{dte}"
            )

            out.append(
                {
                    "strategy": "butterflies",
                    "spread_type": spread_type,
                    "butterfly_type": row.get("butterfly_type"),
                    "option_side": row.get("option_side"),
                    "underlying": symbol,
                    "underlying_symbol": symbol,
                    "symbol": symbol,
                    "expiration": expiration,
                    "dte": dte,
                    "underlying_price": spot,
                    "center_strike": center,
                    "lower_strike": lower,
                    "upper_strike": upper,
                    "short_strike": center,
                    "long_strike": lower,
                    "wing_width": wing_width,
                    "break_even_low": break_even_low,
                    "break_even_high": break_even_high,
                    "break_evens_low": break_even_low,
                    "break_evens_high": break_even_high,
                    "break_even": break_even_low,
                    "max_profit": max_profit,
                    "max_profit_per_contract": max_profit,
                    "max_loss": max_loss,
                    "max_loss_per_contract": max_loss,
                    "peak_profit_at_center": peak_profit_at_center,
                    "payoff_slope": payoff_slope,
                    # ── Spread pricing (new) ──────────────────────────
                    # spread_mid     = mid-based debit/credit (best estimate)
                    # spread_natural  = worst-case fill
                    # spread_mark    = conservative mid (mirrors spread_mid)
                    "spread_mid": spread_mid,
                    "spread_natural": spread_natural,
                    "spread_mark": spread_mark,
                    # ── Cashflow fields ──────────────────────────────
                    "net_debit": total_debit if spread_type in {"debit_call_butterfly", "debit_put_butterfly"} else None,
                    "net_credit": total_credit if spread_type == "iron_butterfly" else None,
                    "total_credit": total_credit,
                    "total_debit": total_debit,
                    # ── Execution validity ────────────────────────────
                    "execution_invalid": execution_invalid,
                    "execution_invalid_reason": execution_invalid_reason,
                    "readiness": not execution_invalid,
                    # ── Probabilities ────────────────────────────────
                    # prob_touch_center: None until real touch model exists.
                    # DO NOT display as 100% in UI.
                    "probability_of_touch_center": prob_touch_center,
                    "pop_butterfly": pop,
                    "p_win_used": pop,
                    "pop_model_used": pop_model_used,
                    # ── EV & risk metrics ────────────────────────────
                    "expected_value": expected_value,
                    "ev_per_contract": expected_value,
                    "ev_per_share": (expected_value / 100.0) if expected_value is not None else None,
                    "ev_to_risk": ev_to_risk,
                    "cost_efficiency": cost_efficiency,
                    "gamma_peak_score": gamma_peak_score,
                    "time_decay_risk": time_decay_risk,
                    "liquidity_score": liquidity_score,
                    "worst_leg_spread": worst_spread,
                    "open_interest": min_oi,
                    "volume": min_vol,
                    "bid_ask_spread_pct": self._clamp((worst_spread / max(abs(net_cost), 0.10)), 0.0, 9.99),
                    "return_on_risk": return_on_risk,
                    "center_alignment": center_alignment,
                    "debit_vs_expected_move": debit_vs_expected_move,
                    "debit_pct_of_width": debit_pct_of_width,
                    "expected_move": expected_move,
                    "trade_key": butterfly_key,
                    "rank_score": rank_score,
                    "contractsMultiplier": 100,
                    "selection_reasons": [],
                }
            )

        # ── Expected fill pricing ─────────────────────────────────────────
        # Butterflies have explicit spread_mid and spread_natural.
        # apply_expected_fill auto-detects credit (iron_butterfly) vs
        # debit (debit_call_butterfly / debit_put_butterfly).
        for trade in out:
            apply_expected_fill(trade)

        return out

    def evaluate(self, trade: dict[str, Any]) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        policy = trade.get("_policy") if isinstance(trade.get("_policy"), dict) else {}
        payload = trade.get("_request") if isinstance(trade.get("_request"), dict) else {}

        # ── 0. Execution validity gate ────────────────────────────────────
        # If pricing is structurally broken, reject immediately.
        if trade.get("execution_invalid"):
            reason_code = trade.get("execution_invalid_reason") or "pricing_unavailable"
            reasons.append(f"execution_invalid:{reason_code}")

        # ── 0a. Missing spread pricing gate ───────────────────────────────
        spread_mid = safe_float(trade.get("spread_mid"))
        net_debit = safe_float(trade.get("net_debit"))
        net_credit = safe_float(trade.get("net_credit"))
        is_debit = trade.get("spread_type") in {"debit_call_butterfly", "debit_put_butterfly"}

        if is_debit and (spread_mid is None or net_debit is None):
            if "execution_invalid:pricing_unavailable" not in reasons:
                reasons.append("pricing_unavailable")
        elif not is_debit and (spread_mid is None or net_credit is None):
            if "execution_invalid:non_positive_credit" not in reasons:
                reasons.append("pricing_unavailable")

        # ── 0b. Debit sanity gates ────────────────────────────────────────
        wing_width = safe_float(trade.get("wing_width")) or 0.0
        if is_debit and net_debit is not None:
            if net_debit <= 0:
                reasons.append("non_positive_debit")
            elif wing_width > 0 and net_debit >= wing_width:
                reasons.append("debit_ge_width")

        # ── 0c. Required metrics gate ─────────────────────────────────────
        # Missing core metrics → HARD FAIL.  Butterflies MUST have POP
        # and EV computed (via normal CDF + numerical integration).
        _missing_metrics: list[str] = []
        if safe_float(trade.get("p_win_used")) is None:
            _missing_metrics.append("pop")
        if safe_float(trade.get("expected_value")) is None:
            _missing_metrics.append("expected_value")
        if safe_float(trade.get("return_on_risk")) is None:
            _missing_metrics.append("return_on_risk")
        if safe_float(trade.get("max_profit")) is None:
            _missing_metrics.append("max_profit")
        for _mm in _missing_metrics:
            reasons.append(f"METRICS_MISSING:{_mm}")

        # ── 0d. debit_pct_of_width gate (lottery ticket / overpriced) ────
        # Rejects butterflies where debit is too large a fraction of width.
        if is_debit:
            debit_pct_of_width = safe_float(trade.get("debit_pct_of_width"))
            max_debit_pct = safe_float(payload.get("max_debit_pct_width"))
            if max_debit_pct is None:
                max_debit_pct = safe_float(policy.get("max_debit_pct_width"))
            if max_debit_pct is None:
                max_debit_pct = 0.60  # balanced default
            if debit_pct_of_width is not None and debit_pct_of_width > max_debit_pct:
                reasons.append("BUTTERFLY_DEBIT_TOO_LARGE")

        # ── 1. Liquidity gates ────────────────────────────────────────────
        min_oi_policy = int(safe_float(payload.get("min_open_interest")) or 0)
        if min_oi_policy <= 0:
            min_oi_policy = max(int(safe_float(policy.get("min_open_interest")) or 0), 500)
        min_vol_policy = int(safe_float(payload.get("min_volume")) or 0)
        if min_vol_policy <= 0:
            min_vol_policy = max(int(safe_float(policy.get("min_volume")) or 0), 50)
        min_oi_req = max(5, int(min_oi_policy * 0.2)) if min_oi_policy > 0 else 5
        min_vol_req = max(1, int(min_vol_policy * 0.2)) if min_vol_policy > 0 else 1

        open_interest = int(safe_float(trade.get("open_interest")) or 0)
        volume = int(safe_float(trade.get("volume")) or 0)
        if open_interest < min_oi_req and volume < min_vol_req:
            reasons.append("liquidity_open_interest_low")

        if (safe_float(trade.get("liquidity_score")) or 0.0) < 0.15:
            reasons.append("liquidity_score_low")

        spread_pct = safe_float(trade.get("bid_ask_spread_pct"))
        worst_leg_spread = safe_float(trade.get("worst_leg_spread"))

        max_spread_pct = safe_float(payload.get("max_bid_ask_spread_pct"))
        if max_spread_pct is None:
            max_spread_pct = 2.5
        if spread_pct is not None and spread_pct > max_spread_pct:
            reasons.append("bid_ask_too_wide")

        max_worst_leg = safe_float(payload.get("max_worst_leg_spread"))
        if max_worst_leg is None:
            max_worst_leg = 1.5
        if worst_leg_spread is not None and worst_leg_spread > max_worst_leg:
            reasons.append("worst_leg_too_wide")

        # ── 2. Profit zone gate ───────────────────────────────────────────
        if (safe_float(trade.get("max_profit")) or 0.0) <= 0:
            reasons.append("no_profit_zone")

        # ── 3. Cost efficiency gate ───────────────────────────────────────
        min_eff = safe_float(payload.get("min_cost_efficiency"))
        if min_eff is None:
            min_eff = 2.0
        if (safe_float(trade.get("cost_efficiency")) or 0.0) < min_eff:
            reasons.append("cost_efficiency_below_floor")

        # ── 4. POP gate (probability of profit) ──────────────────────────
        pop = safe_float(trade.get("p_win_used"))
        if pop is None:
            if "METRICS_MISSING:pop" not in reasons:
                reasons.append("DQ_MISSING:pop")
        else:
            min_pop = safe_float(payload.get("min_pop"))
            if min_pop is None:
                min_pop = safe_float(policy.get("min_pop"))
            if min_pop is None:
                min_pop = 0.04  # balanced default
            if pop < min_pop:
                reasons.append("pop_below_threshold")

        # ── 5. EV sanity gate ────────────────────────────────────────────
        ev = safe_float(trade.get("expected_value"))
        if ev is not None:
            min_ev = safe_float(payload.get("min_expected_value"))
            if min_ev is None:
                min_ev = safe_float(policy.get("min_expected_value"))
            if min_ev is not None and ev < min_ev:
                reasons.append("expected_value_below_threshold")

        # ── 6. EV-to-risk gate ───────────────────────────────────────────
        # Fill-aware: prefer ev_to_risk_fill when available, fall back to mid
        ev_to_risk = safe_float(trade.get("ev_to_risk_fill")) or safe_float(trade.get("ev_to_risk"))
        if ev_to_risk is not None:
            min_ev_to_risk = safe_float(payload.get("min_ev_to_risk"))
            if min_ev_to_risk is None:
                min_ev_to_risk = safe_float(policy.get("min_ev_to_risk"))
            if min_ev_to_risk is not None and ev_to_risk < min_ev_to_risk:
                reasons.append("ev_to_risk_below_threshold")

        return len(reasons) == 0, reasons

    def score(self, trade: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        rank_score = float(safe_float(trade.get("rank_score")) or 0.0)
        tie_breaks = {
            "edge": safe_float(trade.get("cost_efficiency")) or 0.0,
            "liquidity": safe_float(trade.get("liquidity_score")) or 0.0,
            "conviction": safe_float(trade.get("center_alignment")) or 0.0,
        }
        return rank_score, tie_breaks
