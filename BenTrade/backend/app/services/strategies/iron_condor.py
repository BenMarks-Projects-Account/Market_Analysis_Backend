from __future__ import annotations

import math
from statistics import pstdev
from typing import Any

from app.services.ranking import safe_float


class IronCondorStrategyPlugin:
    id = "iron_condor"
    display_name = "Iron Condor"

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
            else:
                curr_oi = safe_float(getattr(current, "open_interest", None)) or 0.0
                new_oi = safe_float(getattr(leg, "open_interest", None)) or 0.0
                if new_oi > curr_oi:
                    out[strike] = leg
        return out

    def _expected_move(self, spot: float, dte: int, rv: float | None, iv_guess: float | None) -> float:
        vol = iv_guess if iv_guess not in (None, 0) else rv
        if vol in (None, 0) or dte <= 0:
            return max(spot * 0.02, 1.0)
        return max(spot * float(vol) * math.sqrt(dte / 365.0), 0.5)

    def build_candidates(self, inputs: dict[str, Any]) -> list[dict[str, Any]]:
        payload = inputs.get("request") or {}
        snapshots = inputs.get("snapshots") or []
        allow_skewed = str(payload.get("allow_skewed") or "false").lower() in {"1", "true", "yes", "y"}

        wing_put_target = self._to_float(payload.get("wing_width_put"))
        wing_call_target = self._to_float(payload.get("wing_width_call"))
        wing_shared = self._to_float(payload.get("wing_width"))
        if wing_put_target is None:
            wing_put_target = wing_shared
        if wing_call_target is None:
            wing_call_target = wing_shared

        distance_mode = str(payload.get("distance_mode") or "expected_move").lower()
        distance_target = self._to_float(payload.get("distance_target"))
        if distance_target is None:
            distance_target = 1.0 if distance_mode == "expected_move" else 0.20

        max_candidates = int(payload.get("max_candidates") or 220)
        results: list[dict[str, Any]] = []

        for snapshot in snapshots:
            symbol = str(snapshot.get("symbol") or "").upper()
            expiration = str(snapshot.get("expiration") or "")
            dte = int(snapshot.get("dte") or 0)
            spot = self._to_float(snapshot.get("underlying_price"))
            contracts = snapshot.get("contracts") or []
            prices = [float(x) for x in (snapshot.get("prices_history") or []) if self._to_float(x) is not None]
            if not symbol or not expiration or spot is None or dte <= 0 or not contracts:
                continue

            put_map = self._strike_map(contracts, "put")
            call_map = self._strike_map(contracts, "call")
            put_strikes = sorted(put_map.keys())
            call_strikes = sorted(call_map.keys())
            if not put_strikes or not call_strikes:
                continue

            iv_candidates = []
            for leg in list(put_map.values())[:20] + list(call_map.values())[:20]:
                iv = self._to_float(getattr(leg, "iv", None))
                if iv not in (None, 0):
                    iv_candidates.append(iv)
            iv_guess = (sum(iv_candidates) / len(iv_candidates)) if iv_candidates else None
            rv = self._realized_vol(prices)
            exp_move = self._expected_move(spot, dte, rv, iv_guess)

            for put_short in reversed([s for s in put_strikes if s < spot]):
                put_short_leg = put_map.get(put_short)
                if put_short_leg is None:
                    continue

                for call_short in [s for s in call_strikes if s > spot]:
                    call_short_leg = call_map.get(call_short)
                    if call_short_leg is None:
                        continue

                    put_dist = (spot - put_short)
                    call_dist = (call_short - spot)

                    if distance_mode == "delta":
                        put_delta = abs(self._to_float(getattr(put_short_leg, "delta", None)) or 0.0)
                        call_delta = abs(self._to_float(getattr(call_short_leg, "delta", None)) or 0.0)
                        if put_delta <= 0 or call_delta <= 0:
                            continue
                        put_target_err = abs(put_delta - distance_target)
                        call_target_err = abs(call_delta - distance_target)
                        if put_target_err > 0.14 or call_target_err > 0.14:
                            continue
                    else:
                        put_ratio = put_dist / exp_move if exp_move > 0 else 0.0
                        call_ratio = call_dist / exp_move if exp_move > 0 else 0.0
                        if put_ratio < distance_target * 0.5 or call_ratio < distance_target * 0.5:
                            continue

                    wing_put = wing_put_target if wing_put_target is not None else (2.0 if spot < 120 else 5.0)
                    wing_call = wing_call_target if wing_call_target is not None else (2.0 if spot < 120 else 5.0)

                    put_long = min((s for s in put_strikes if s < put_short), key=lambda s: abs((put_short - s) - wing_put), default=None)
                    call_long = min((s for s in call_strikes if s > call_short), key=lambda s: abs((s - call_short) - wing_call), default=None)
                    if put_long is None or call_long is None:
                        continue

                    width_put = put_short - put_long
                    width_call = call_long - call_short
                    if width_put <= 0 or width_call <= 0:
                        continue

                    symmetry = 1.0 - abs(width_put - width_call) / max(width_put, width_call)
                    if not allow_skewed and symmetry < 0.55:
                        continue

                    results.append(
                        {
                            "strategy": "iron_condor",
                            "spread_type": "iron_condor",
                            "symbol": symbol,
                            "expiration": expiration,
                            "dte": dte,
                            "underlying_price": spot,
                            "put_short_strike": put_short,
                            "put_long_strike": put_long,
                            "call_short_strike": call_short,
                            "call_long_strike": call_long,
                            "put_short_leg": put_short_leg,
                            "put_long_leg": put_map.get(put_long),
                            "call_short_leg": call_short_leg,
                            "call_long_leg": call_map.get(call_long),
                            "width_put": width_put,
                            "width_call": width_call,
                            "symmetry_score": self._clamp(symmetry),
                            "expected_move": exp_move,
                            "snapshot": snapshot,
                        }
                    )

                    if len(results) >= max_candidates:
                        return results

        return results

    def enrich(self, candidates: list[dict[str, Any]], inputs: dict[str, Any]) -> list[dict[str, Any]]:
        payload = inputs.get("request") or {}
        policy = inputs.get("policy") or {}

        out: list[dict[str, Any]] = []
        for row in candidates:
            put_short_leg = row.get("put_short_leg")
            put_long_leg = row.get("put_long_leg")
            call_short_leg = row.get("call_short_leg")
            call_long_leg = row.get("call_long_leg")
            if not all([put_short_leg, put_long_leg, call_short_leg, call_long_leg]):
                continue

            symbol = str(row.get("symbol") or "").upper()
            expiration = str(row.get("expiration") or "")
            dte = int(row.get("dte") or 0)
            spot = float(row.get("underlying_price") or 0.0)
            width_put = float(row.get("width_put") or 0.0)
            width_call = float(row.get("width_call") or 0.0)
            expected_move = float(row.get("expected_move") or max(spot * 0.02, 1.0))

            put_short_bid = safe_float(getattr(put_short_leg, "bid", None)) or 0.0
            put_long_ask = safe_float(getattr(put_long_leg, "ask", None)) or 0.0
            call_short_bid = safe_float(getattr(call_short_leg, "bid", None)) or 0.0
            call_long_ask = safe_float(getattr(call_long_leg, "ask", None)) or 0.0

            put_short_ask = safe_float(getattr(put_short_leg, "ask", None)) or put_short_bid
            put_long_bid = safe_float(getattr(put_long_leg, "bid", None)) or put_long_ask
            call_short_ask = safe_float(getattr(call_short_leg, "ask", None)) or call_short_bid
            call_long_bid = safe_float(getattr(call_long_leg, "bid", None)) or call_long_ask

            put_short_mid = (put_short_bid + put_short_ask) / 2.0
            put_long_mid = (put_long_bid + put_long_ask) / 2.0
            call_short_mid = (call_short_bid + call_short_ask) / 2.0
            call_long_mid = (call_long_bid + call_long_ask) / 2.0

            put_credit = put_short_bid - put_long_ask
            call_credit = call_short_bid - call_long_ask
            if put_credit <= 0:
                put_credit = put_short_mid - put_long_mid
            if call_credit <= 0:
                call_credit = call_short_mid - call_long_mid
            total_credit = put_credit + call_credit
            if total_credit <= 0:
                continue

            max_loss = max(width_put, width_call) * 100.0 - (total_credit * 100.0)
            if max_loss <= 0:
                continue

            break_even_low = float(row.get("put_short_strike") or 0.0) - total_credit
            break_even_high = float(row.get("call_short_strike") or 0.0) + total_credit
            return_on_risk = (total_credit * 100.0) / max_loss if max_loss > 0 else 0.0

            put_distance = max(0.0, spot - float(row.get("put_short_strike") or 0.0))
            call_distance = max(0.0, float(row.get("call_short_strike") or 0.0) - spot)
            em_ratio = min(put_distance, call_distance) / expected_move if expected_move > 0 else 0.0

            # POP via normal CDF: probability stock ends between break-evens
            if expected_move > 0:
                z_high = (break_even_high - spot) / expected_move
                z_low = (break_even_low - spot) / expected_move
                pop_approx = self._clamp(self._normal_cdf(z_high) - self._normal_cdf(z_low))
            else:
                pop_approx = 0.5

            # EV from POP-based formula
            ev_per_contract = pop_approx * (total_credit * 100.0) - (1.0 - pop_approx) * max_loss
            ev_per_share = ev_per_contract / 100.0
            ev_to_risk = ev_per_contract / max_loss if max_loss > 0 else 0.0

            vega_short = abs(safe_float(getattr(put_short_leg, "vega", None)) or 0.0) + abs(safe_float(getattr(call_short_leg, "vega", None)) or 0.0)
            vega_long = abs(safe_float(getattr(put_long_leg, "vega", None)) or 0.0) + abs(safe_float(getattr(call_long_leg, "vega", None)) or 0.0)
            vega_exposure_approx = max(0.0, vega_short - vega_long)

            theta_short = abs(safe_float(getattr(put_short_leg, "theta", None)) or 0.0) + abs(safe_float(getattr(call_short_leg, "theta", None)) or 0.0)
            theta_long = abs(safe_float(getattr(put_long_leg, "theta", None)) or 0.0) + abs(safe_float(getattr(call_long_leg, "theta", None)) or 0.0)
            theta_capture_raw = max(0.0, theta_short - theta_long)
            theta_capture = (total_credit / max(1.0, dte)) / (max_loss / 100.0) if max_loss > 0 else 0.0

            iv_values = [
                safe_float(getattr(put_short_leg, "iv", None)),
                safe_float(getattr(put_long_leg, "iv", None)),
                safe_float(getattr(call_short_leg, "iv", None)),
                safe_float(getattr(call_long_leg, "iv", None)),
            ]
            iv_values = [v for v in iv_values if v is not None]
            iv_avg = (sum(iv_values) / len(iv_values)) if iv_values else None

            prices = [float(x) for x in (row.get("snapshot") or {}).get("prices_history", []) if self._to_float(x) is not None]
            rv = self._realized_vol(prices)
            iv_rv_ratio = (iv_avg / rv) if iv_avg not in (None, 0) and rv not in (None, 0) else None

            tail_risk_score = self._clamp(1.0 - min(put_distance, call_distance) / max(expected_move * 2.5, 0.01))
            liquidity_worst_spread = max(
                (safe_float(getattr(put_short_leg, "ask", None)) or 0.0) - (safe_float(getattr(put_short_leg, "bid", None)) or 0.0),
                (safe_float(getattr(put_long_leg, "ask", None)) or 0.0) - (safe_float(getattr(put_long_leg, "bid", None)) or 0.0),
                (safe_float(getattr(call_short_leg, "ask", None)) or 0.0) - (safe_float(getattr(call_short_leg, "bid", None)) or 0.0),
                (safe_float(getattr(call_long_leg, "ask", None)) or 0.0) - (safe_float(getattr(call_long_leg, "bid", None)) or 0.0),
            )

            min_oi = min(
                int(safe_float(getattr(put_short_leg, "open_interest", None)) or 0),
                int(safe_float(getattr(put_long_leg, "open_interest", None)) or 0),
                int(safe_float(getattr(call_short_leg, "open_interest", None)) or 0),
                int(safe_float(getattr(call_long_leg, "open_interest", None)) or 0),
            )
            min_vol = min(
                int(safe_float(getattr(put_short_leg, "volume", None)) or 0),
                int(safe_float(getattr(put_long_leg, "volume", None)) or 0),
                int(safe_float(getattr(call_short_leg, "volume", None)) or 0),
                int(safe_float(getattr(call_long_leg, "volume", None)) or 0),
            )

            oi_ref = max(float(policy.get("min_open_interest") or 500), 1.0)
            vol_ref = max(float(policy.get("min_volume") or 50), 1.0)
            oi_score = self._clamp((min_oi / oi_ref) / 2.0)
            vol_score = self._clamp((min_vol / vol_ref) / 2.0)
            spread_score = self._clamp(1.0 - (liquidity_worst_spread / max(total_credit * 1.5, 0.1)))
            liquidity_score = self._clamp((0.42 * oi_score) + (0.30 * vol_score) + (0.28 * spread_score))

            sym = self._clamp(float(row.get("symmetry_score") or 0.0))
            distance_score = self._clamp(em_ratio / 1.6)
            theta_score = self._clamp(theta_capture / 0.08)

            width_penalty = self._clamp((0.35 - (total_credit / max(width_put, width_call, 0.01))) / 0.35)
            tail_penalty = tail_risk_score
            liq_penalty = self._clamp(1.0 - liquidity_score)

            rank_score = self._clamp(
                (0.34 * theta_score)
                + (0.26 * distance_score)
                + (0.20 * sym)
                + (0.20 * liquidity_score)
                - (0.22 * tail_penalty)
                - (0.14 * liq_penalty)
                - (0.12 * width_penalty)
            )

            condor_key = (
                f"{symbol}|{expiration}|iron_condor|"
                f"P{row.get('put_short_strike')}/{row.get('put_long_strike')}|"
                f"C{row.get('call_short_strike')}/{row.get('call_long_strike')}|{dte}"
            )

            out.append(
                {
                    "strategy": "iron_condor",
                    "spread_type": "iron_condor",
                    "underlying": symbol,
                    "underlying_symbol": symbol,
                    "symbol": symbol,
                    "expiration": expiration,
                    "dte": dte,
                    "underlying_price": spot,
                    "put_short_strike": row.get("put_short_strike"),
                    "put_long_strike": row.get("put_long_strike"),
                    "call_short_strike": row.get("call_short_strike"),
                    "call_long_strike": row.get("call_long_strike"),
                    "short_strike": row.get("put_short_strike"),
                    "long_strike": row.get("call_short_strike"),
                    "total_credit": total_credit,
                    "net_credit": total_credit,
                    "max_profit": total_credit * 100.0,
                    "max_profit_per_contract": total_credit * 100.0,
                    "max_loss": max_loss,
                    "max_loss_per_contract": max_loss,
                    "break_even_low": break_even_low,
                    "break_even_high": break_even_high,
                    "break_evens_low": break_even_low,
                    "break_evens_high": break_even_high,
                    "break_even": break_even_low,
                    "width_put": width_put,
                    "width_call": width_call,
                    "symmetry_score": sym,
                    "pop_approx": pop_approx,
                    "p_win_used": pop_approx,
                    "expected_move": expected_move,
                    "expected_move_ratio": em_ratio,
                    "vega_exposure_approx": vega_exposure_approx,
                    "theta_capture": theta_capture,
                    "theta_capture_raw": theta_capture_raw,
                    "tail_risk_score": tail_risk_score,
                    "liquidity_score": liquidity_score,
                    "liquidity_worst_leg_spread": liquidity_worst_spread,
                    "open_interest": min_oi,
                    "volume": min_vol,
                    "bid_ask_spread_pct": self._clamp((liquidity_worst_spread / max(total_credit, 0.01)), 0.0, 9.99),
                    "iv_rv_ratio": iv_rv_ratio,
                    "ev_to_risk": ev_to_risk,
                    "ev_per_contract": ev_per_contract,
                    "ev_per_share": ev_per_share,
                    "expected_value": ev_per_contract,
                    "return_on_risk": return_on_risk,
                    "rank_score": rank_score,
                    "trade_key": condor_key,
                    "contractsMultiplier": 100,
                    "selection_reasons": [],
                }
            )

        return out

    def evaluate(self, trade: dict[str, Any]) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        request_payload = trade.get("_request") if isinstance(trade.get("_request"), dict) else {}

        min_sigma = safe_float(request_payload.get("min_sigma_distance"))
        if min_sigma is None:
            min_sigma = 1.10
        em_ratio = safe_float(trade.get("expected_move_ratio"))
        if em_ratio is None or em_ratio < min_sigma:
            reasons.append("distance_below_min_sigma")

        allow_skewed = str(request_payload.get("allow_skewed") or "false").lower() in {"1", "true", "yes", "y"}
        if not allow_skewed:
            symmetry_target = safe_float(request_payload.get("symmetry_target"))
            if symmetry_target is None:
                symmetry_target = 0.70
            if (safe_float(trade.get("symmetry_score")) or 0.0) < symmetry_target:
                reasons.append("condor_too_skewed")

        min_credit = safe_float(request_payload.get("min_credit"))
        if min_credit is None:
            min_credit = 0.10
        total_credit = safe_float(trade.get("total_credit"))
        if total_credit is None or total_credit < min_credit:
            reasons.append("credit_below_min")

        min_ror = safe_float(request_payload.get("min_ror"))
        if min_ror is None:
            min_ror = 0.12
        if (safe_float(trade.get("return_on_risk")) or 0.0) < min_ror:
            reasons.append("ror_below_floor")

        return len(reasons) == 0, reasons

    def score(self, trade: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        rank_score = float(safe_float(trade.get("rank_score")) or 0.0)
        tie_breaks = {
            "edge": safe_float(trade.get("theta_capture")) or 0.0,
            "liquidity": safe_float(trade.get("liquidity_score")) or 0.0,
            "conviction": 1.0 - (safe_float(trade.get("tail_risk_score")) or 1.0),
        }
        return rank_score, tie_breaks
