from __future__ import annotations

import logging
import math
from statistics import pstdev
from typing import Any

from app.services.ranking import safe_float

logger = logging.getLogger("bentrade.iron_condor")


# ---------------------------------------------------------------------------
# Per-leg diagnostic helpers (trace-only, no scoring impact)
# ---------------------------------------------------------------------------

def _leg_quote_diagnostic(leg: Any, leg_name: str) -> dict[str, Any]:
    """Build a quote-lookup diagnostic record for a single leg.

    Returns a dict with: leg_name, strike, option_symbol, side,
    success, error_code, error_message, bid, ask, mid.
    """
    strike = safe_float(getattr(leg, "strike", None))
    option_symbol = getattr(leg, "symbol", None)
    side = "long" if leg_name.startswith("long") else "short"

    bid_raw = getattr(leg, "bid", None)
    ask_raw = getattr(leg, "ask", None)
    bid = safe_float(bid_raw)
    ask = safe_float(ask_raw)

    # Determine success / failure
    error_code: str | None = None
    error_message: str | None = None
    success = True

    if bid is None and ask is None:
        success = False
        error_code = "MISSING_BID_ASK"
        error_message = f"Both bid and ask are None for {leg_name}"
    elif bid is None:
        success = False
        error_code = "MISSING_BID"
        error_message = f"bid is None for {leg_name}"
    elif ask is None:
        success = False
        error_code = "MISSING_ASK"
        error_message = f"ask is None for {leg_name}"
    elif ask <= 0:
        success = False
        error_code = "ZERO_OR_NEGATIVE_ASK"
        error_message = f"ask={ask} for {leg_name}"
    elif bid < 0:
        success = False
        error_code = "NEGATIVE_BID"
        error_message = f"bid={bid} for {leg_name}"

    mid = ((bid + ask) / 2.0) if bid is not None and ask is not None else None

    return {
        "leg_name": leg_name,
        "strike": strike,
        "option_symbol": option_symbol,
        "side": side,
        "success": success,
        "error_code": error_code,
        "error_message": error_message,
        "bid": bid,
        "ask": ask,
        "mid": mid,
    }


def _leg_greeks_diagnostic(leg: Any, leg_name: str) -> dict[str, Any]:
    """Build a greeks-lookup diagnostic record for a single leg.

    Returns a dict with: leg_name, success, delta, error_code, error_message.
    """
    delta_raw = getattr(leg, "delta", None)
    delta = safe_float(delta_raw)

    success = delta is not None
    error_code: str | None = None
    error_message: str | None = None
    if not success:
        error_code = "MISSING_DELTA"
        error_message = f"delta is None for {leg_name}"

    return {
        "leg_name": leg_name,
        "success": success,
        "delta": delta,
        "error_code": error_code,
        "error_message": error_message,
    }


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

                    # ── Explicit 4-leg candidate structure ──────────────────
                    _put_long_leg = put_map.get(put_long)
                    _call_long_leg = call_map.get(call_long)
                    legs = [
                        {"name": "long_put",   "right": "put",  "side": "buy",  "strike": put_long,   "qty": 1, "_contract": _put_long_leg},
                        {"name": "short_put",  "right": "put",  "side": "sell", "strike": put_short,  "qty": 1, "_contract": put_short_leg},
                        {"name": "short_call", "right": "call", "side": "sell", "strike": call_short, "qty": 1, "_contract": call_short_leg},
                        {"name": "long_call",  "right": "call", "side": "buy",  "strike": call_long,  "qty": 1, "_contract": _call_long_leg},
                    ]

                    results.append(
                        {
                            "strategy": "iron_condor",
                            "spread_type": "iron_condor",
                            "symbol": symbol,
                            "expiration": expiration,
                            "dte": dte,
                            "underlying_price": spot,
                            # ── Explicit legs array ─────────────────────────
                            "legs": legs,
                            # ── Convenience strike fields ───────────────────
                            "short_put_strike": put_short,
                            "long_put_strike": put_long,
                            "short_call_strike": call_short,
                            "long_call_strike": call_long,
                            # Backward-compat aliases (existing downstream code)
                            "put_short_strike": put_short,
                            "put_long_strike": put_long,
                            "call_short_strike": call_short,
                            "call_long_strike": call_long,
                            # ── Wing widths ─────────────────────────────────
                            "put_wing_width": width_put,
                            "call_wing_width": width_call,
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

        # Pre-compute realized vol once per unique snapshot to avoid redundant math
        _rv_cache: dict[int, float | None] = {}

        # ── DQ-fail sample collector (capped at 10) ────────────────────────
        _DQ_FAIL_CAP = 10
        dq_fail_samples: list[dict[str, Any]] = []

        out: list[dict[str, Any]] = []
        for idx, row in enumerate(candidates):
            # ── Extract leg contracts from legs[] array ─────────────────────
            legs_list = row.get("legs") or []
            _legs_by_name: dict[str, Any] = {}
            for leg_entry in legs_list:
                _legs_by_name[leg_entry["name"]] = leg_entry.get("_contract")

            put_long_leg = _legs_by_name.get("long_put")
            put_short_leg = _legs_by_name.get("short_put")
            call_short_leg = _legs_by_name.get("short_call")
            call_long_leg = _legs_by_name.get("long_call")

            if not all([put_short_leg, put_long_leg, call_short_leg, call_long_leg]):
                continue

            symbol = str(row.get("symbol") or "").upper()
            expiration = str(row.get("expiration") or "")
            dte = int(row.get("dte") or 0)
            spot = float(row.get("underlying_price") or 0.0)
            width_put = float(row.get("width_put") or 0.0)
            width_call = float(row.get("width_call") or 0.0)
            expected_move = float(row.get("expected_move") or max(spot * 0.02, 1.0))

            # ── Per-leg quote & greeks diagnostics (trace only) ─────────────
            _leg_map = {
                "long_put": put_long_leg,
                "short_put": put_short_leg,
                "short_call": call_short_leg,
                "long_call": call_long_leg,
            }
            legs_diag: list[dict[str, Any]] = []
            quote_diags: list[dict[str, Any]] = []
            greeks_diags: list[dict[str, Any]] = []
            failed_legs: list[str] = []

            for leg_name, leg_obj in _leg_map.items():
                q_diag = _leg_quote_diagnostic(leg_obj, leg_name)
                g_diag = _leg_greeks_diagnostic(leg_obj, leg_name)
                quote_diags.append(q_diag)
                greeks_diags.append(g_diag)
                legs_diag.append({
                    "leg_name": leg_name,
                    "strike": q_diag["strike"],
                    "option_symbol": q_diag["option_symbol"],
                    "side": q_diag["side"],
                })
                if not q_diag["success"]:
                    failed_legs.append(leg_name)

            # Build candidate_id / trade_key early for diagnostics
            candidate_id = (
                f"{symbol}|{expiration}|iron_condor|"
                f"P{row.get('put_short_strike')}/{row.get('put_long_strike')}|"
                f"C{row.get('call_short_strike')}/{row.get('call_long_strike')}|{dte}"
            )

            _candidate_diag: dict[str, Any] = {
                "candidate_id": candidate_id,
                "candidate_idx": idx,
                "legs": legs_diag,
                "quote_lookup_results": quote_diags,
                "greeks_lookup_results": greeks_diags,
                "dq_failed": bool(failed_legs),
                "dq_reasons": [
                    f"LEG_QUOTE_LOOKUP_FAILED:{ln}" for ln in failed_legs
                ],
            }

            # ── If ANY leg failed quote lookup → record DQ sample ─────────
            if failed_legs:
                if len(dq_fail_samples) < _DQ_FAIL_CAP:
                    dq_fail_samples.append(_candidate_diag)
                logger.info(
                    "event=ic_leg_quote_dq candidate_id=%s failed_legs=%s",
                    candidate_id, failed_legs,
                )

            # ── Per-leg field extraction (pricing + enriched output + counters) ─
            # bid, ask, delta, iv, open_interest, volume, occ_symbol per leg.
            # Initialised here so they're always available even when
            # pricing_valid is False.
            _leg_fields: dict[str, dict[str, Any]] = {}
            for _lname, _lobj in _leg_map.items():
                _leg_fields[_lname] = {
                    "bid":  safe_float(getattr(_lobj, "bid", None)),
                    "ask":  safe_float(getattr(_lobj, "ask", None)),
                    "delta": safe_float(getattr(_lobj, "delta", None)),
                    "iv":    safe_float(getattr(_lobj, "iv", None)),
                    "open_interest": safe_float(getattr(_lobj, "open_interest", None)),
                    "volume": safe_float(getattr(_lobj, "volume", None)),
                    "occ_symbol": getattr(_lobj, "symbol", None),
                }

            # Convenience aliases for pricing block (kept for readability)
            _sp_bid = _leg_fields["short_put"]["bid"]
            _sp_ask = _leg_fields["short_put"]["ask"]
            _lp_bid = _leg_fields["long_put"]["bid"]
            _lp_ask = _leg_fields["long_put"]["ask"]
            _sc_bid = _leg_fields["short_call"]["bid"]
            _sc_ask = _leg_fields["short_call"]["ask"]
            _lc_bid = _leg_fields["long_call"]["bid"]
            _lc_ask = _leg_fields["long_call"]["ask"]

            # Per-leg OCC symbols (for enriched legs array)
            _lp_sym = _leg_fields["long_put"]["occ_symbol"]
            _sp_sym = _leg_fields["short_put"]["occ_symbol"]
            _sc_sym = _leg_fields["short_call"]["occ_symbol"]
            _lc_sym = _leg_fields["long_call"]["occ_symbol"]

            # ── 4-leg mid pricing ────────────────────────────────────────
            # mid = (bid + ask) / 2 for each leg
            # net_credit = (short_put.mid + short_call.mid)
            #            - (long_put.mid  + long_call.mid)
            # max_loss   = max(put_wing_width, call_wing_width) * 100
            #            - net_credit * 100
            # ror        = (net_credit * 100) / max_loss
            _pricing_valid = not failed_legs
            if _pricing_valid:
                # Belt-and-suspenders: any None → pricing invalid
                if any(q is None for q in (_sp_bid, _sp_ask, _lp_bid, _lp_ask,
                                           _sc_bid, _sc_ask, _lc_bid, _lc_ask)):
                    _pricing_valid = False

            if _pricing_valid:
                # mid = (bid + ask) / 2 for each leg
                short_put_mid  = (_sp_bid + _sp_ask) / 2.0
                long_put_mid   = (_lp_bid + _lp_ask) / 2.0
                short_call_mid = (_sc_bid + _sc_ask) / 2.0
                long_call_mid  = (_lc_bid + _lc_ask) / 2.0

                # net_credit = (short_put.mid + short_call.mid)
                #            - (long_put.mid  + long_call.mid)
                net_credit = (short_put_mid + short_call_mid) - (long_put_mid + long_call_mid)
                total_credit = net_credit  # backward-compat alias

                if net_credit <= 0:
                    continue

                # max_loss = max(put_wing_width, call_wing_width) * 100 - net_credit * 100
                max_loss = max(width_put, width_call) * 100.0 - net_credit * 100.0
                if max_loss <= 0:
                    continue

                # ror = (net_credit * 100) / max_loss
                return_on_risk = (net_credit * 100.0) / max_loss

                break_even_low = float(row.get("put_short_strike") or 0.0) - net_credit
                break_even_high = float(row.get("call_short_strike") or 0.0) + net_credit

                # POP via normal CDF: probability stock ends between break-evens
                if expected_move > 0:
                    z_high = (break_even_high - spot) / expected_move
                    z_low = (break_even_low - spot) / expected_move
                    pop_approx = self._clamp(self._normal_cdf(z_high) - self._normal_cdf(z_low))
                else:
                    pop_approx = 0.5

                # EV from POP-based formula
                ev_per_contract = pop_approx * (net_credit * 100.0) - (1.0 - pop_approx) * max_loss
                ev_per_share = ev_per_contract / 100.0
                ev_to_risk = ev_per_contract / max_loss if max_loss > 0 else 0.0

                theta_capture = (net_credit / max(1.0, dte)) / (max_loss / 100.0) if max_loss > 0 else 0.0

                readiness = True
                # pop_model_used: identifies which model produced p_win_used.
                # 'normal_cdf' = POP via break-even distances and normal distribution.
                pop_model_used = "normal_cdf"
            else:
                # ── Missing/invalid leg quote → do not compute credit/ror ──
                net_credit = None
                total_credit = None
                max_loss = None
                return_on_risk = None
                break_even_low = None
                break_even_high = None
                pop_approx = None
                ev_per_contract = None
                ev_per_share = None
                ev_to_risk = None
                theta_capture = None
                readiness = False
                pop_model_used = "NONE"

                _candidate_diag["pricing_dq"] = True
                if "LEG_QUOTE_INCOMPLETE" not in _candidate_diag.get("dq_reasons", []):
                    _candidate_diag.setdefault("dq_reasons", []).append("LEG_QUOTE_INCOMPLETE")

            # ── Credit-independent metrics (always computed) ────────────
            put_distance = max(0.0, spot - float(row.get("put_short_strike") or 0.0))
            call_distance = max(0.0, float(row.get("call_short_strike") or 0.0) - spot)
            em_ratio = min(put_distance, call_distance) / expected_move if expected_move > 0 else 0.0

            vega_short = abs(safe_float(getattr(put_short_leg, "vega", None)) or 0.0) + abs(safe_float(getattr(call_short_leg, "vega", None)) or 0.0)
            vega_long = abs(safe_float(getattr(put_long_leg, "vega", None)) or 0.0) + abs(safe_float(getattr(call_long_leg, "vega", None)) or 0.0)
            vega_exposure_approx = max(0.0, vega_short - vega_long)

            theta_short = abs(safe_float(getattr(put_short_leg, "theta", None)) or 0.0) + abs(safe_float(getattr(call_short_leg, "theta", None)) or 0.0)
            theta_long = abs(safe_float(getattr(put_long_leg, "theta", None)) or 0.0) + abs(safe_float(getattr(call_long_leg, "theta", None)) or 0.0)
            theta_capture_raw = max(0.0, theta_short - theta_long)

            iv_values = [
                safe_float(getattr(put_short_leg, "iv", None)),
                safe_float(getattr(put_long_leg, "iv", None)),
                safe_float(getattr(call_short_leg, "iv", None)),
                safe_float(getattr(call_long_leg, "iv", None)),
            ]
            iv_values = [v for v in iv_values if v is not None]
            iv_avg = (sum(iv_values) / len(iv_values)) if iv_values else None

            snapshot = row.get("snapshot") or {}
            snap_id = id(snapshot)
            if snap_id not in _rv_cache:
                prices = [float(x) for x in snapshot.get("prices_history", []) if self._to_float(x) is not None]
                _rv_cache[snap_id] = self._realized_vol(prices)
            rv = _rv_cache[snap_id]
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
            # spread_score: use total_credit when available, else fallback
            _spread_denom = max((total_credit or 0.0) * 1.5, 0.1)
            spread_score = self._clamp(1.0 - (liquidity_worst_spread / _spread_denom))
            liquidity_score = self._clamp((0.42 * oi_score) + (0.30 * vol_score) + (0.28 * spread_score))

            sym = self._clamp(float(row.get("symmetry_score") or 0.0))
            distance_score = self._clamp(em_ratio / 1.6)
            theta_score = self._clamp((theta_capture or 0.0) / 0.08)

            _width_credit_ratio = ((total_credit or 0.0) / max(width_put, width_call, 0.01))
            width_penalty = self._clamp((0.35 - _width_credit_ratio) / 0.35)
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

            # ── Serializable legs array (no _contract refs) ────────────────
            # Full per-leg market data: bid, ask, mid, delta, iv, OI, volume,
            # occ_symbol.  Used by strategy_service counters, near-miss
            # builder, and UI consumption.
            _leg_strike_map = {
                "long_put":   float(row.get("put_long_strike") or 0.0),
                "short_put":  float(row.get("put_short_strike") or 0.0),
                "short_call": float(row.get("call_short_strike") or 0.0),
                "long_call":  float(row.get("call_long_strike") or 0.0),
            }
            _leg_right_map = {
                "long_put": "put", "short_put": "put",
                "short_call": "call", "long_call": "call",
            }
            _leg_side_map = {
                "long_put": "buy", "short_put": "sell",
                "short_call": "sell", "long_call": "buy",
            }
            _mid_map = {
                "long_put":   long_put_mid   if readiness else None,
                "short_put":  short_put_mid  if readiness else None,
                "short_call": short_call_mid if readiness else None,
                "long_call":  long_call_mid  if readiness else None,
            }
            _enriched_legs = []
            for _elname in ("long_put", "short_put", "short_call", "long_call"):
                _lf = _leg_fields[_elname]
                _enriched_legs.append({
                    "name": _elname,
                    "right": _leg_right_map[_elname],
                    "side": _leg_side_map[_elname],
                    "strike": _leg_strike_map[_elname],
                    "qty": 1,
                    "bid": _lf["bid"],
                    "ask": _lf["ask"],
                    "mid": _mid_map[_elname],
                    "delta": _lf["delta"],
                    "iv": _lf["iv"],
                    "open_interest": int(_lf["open_interest"]) if _lf["open_interest"] is not None else None,
                    "volume": int(_lf["volume"]) if _lf["volume"] is not None else None,
                    "occ_symbol": _lf["occ_symbol"],
                })

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
                    # ── Explicit legs array ─────────────────────────────────
                    "legs": _enriched_legs,
                    # ── Convenience strike fields ──────────────────────────
                    "short_put_strike": row.get("put_short_strike"),
                    "long_put_strike": row.get("put_long_strike"),
                    "short_call_strike": row.get("call_short_strike"),
                    "long_call_strike": row.get("call_long_strike"),
                    # Backward-compat aliases (strategy_service, frontend)
                    "put_short_strike": row.get("put_short_strike"),
                    "put_long_strike": row.get("put_long_strike"),
                    "call_short_strike": row.get("call_short_strike"),
                    "call_long_strike": row.get("call_long_strike"),
                    # ── Wing widths ────────────────────────────────────────
                    "put_wing_width": width_put,
                    "call_wing_width": width_call,
                    # width = max(put_wing, call_wing) — used by strategy_service
                    # _trade_width / near-miss builder
                    "width": max(width_put, width_call),
                    "readiness": readiness,
                    "total_credit": total_credit,
                    "net_credit": net_credit,
                    "max_profit": (net_credit * 100.0) if net_credit is not None else None,
                    "max_profit_per_contract": (net_credit * 100.0) if net_credit is not None else None,
                    "max_loss": max_loss,
                    "max_loss_per_contract": max_loss,
                    "break_even_low": break_even_low,
                    "break_even_high": break_even_high,
                    "break_evens_low": break_even_low,
                    "break_evens_high": break_even_high,
                    "break_even": break_even_low,
                    "width_put": width_put,
                    "width_call": width_call,
                    # ── Per-leg mids (trace / near-miss consumption) ───────
                    "short_put_mid": short_put_mid if readiness else None,
                    "long_put_mid": long_put_mid if readiness else None,
                    "short_call_mid": short_call_mid if readiness else None,
                    "long_call_mid": long_call_mid if readiness else None,
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
                    "bid_ask_spread_pct": self._clamp((liquidity_worst_spread / max(total_credit or 0.0, 0.01)), 0.0, 9.99),
                    "iv_rv_ratio": iv_rv_ratio,
                    "ev_to_risk": ev_to_risk,
                    "ev_per_contract": ev_per_contract,
                    "ev_per_share": ev_per_share,
                    "expected_value": ev_per_contract,
                    "return_on_risk": return_on_risk,
                    "pop_model_used": pop_model_used,
                    "rank_score": rank_score,
                    "trade_key": condor_key,
                    "contractsMultiplier": 100,
                    "selection_reasons": [],
                    # ── Spread-level bid/ask (counter compat) ───────────────
                    # spread_bid = conservative natural credit (sell at bid, buy at ask)
                    # spread_ask = best-case credit (sell at ask, buy at bid)
                    # Used by spread_quote_derived_success counter.
                    "spread_bid": (
                        round((_sp_bid + _sc_bid) - (_lp_ask + _lc_ask), 4)
                        if readiness else None
                    ),
                    "spread_ask": (
                        round((_sp_ask + _sc_ask) - (_lp_bid + _lc_bid), 4)
                        if readiness else None
                    ),
                    # ── 2-leg compat fields (near-miss top-level mapping) ──
                    # For IC: short_bid/ask → short_put, long_bid/ask → long_put.
                    # This is a documented deterministic mapping; these fields
                    # are ambiguous for 4-leg strategies but kept for UI compat.
                    "_short_bid": _sp_bid,   # = short_put.bid
                    "_short_ask": _sp_ask,   # = short_put.ask
                    "_long_bid":  _lp_bid,   # = long_put.bid
                    "_long_ask":  _lp_ask,   # = long_put.ask
                    # ── Per-leg bid/ask (counter / near-miss / rejection log) ─
                    "_short_put_bid": _sp_bid,
                    "_short_put_ask": _sp_ask,
                    "_long_put_bid": _lp_bid,
                    "_long_put_ask": _lp_ask,
                    "_short_call_bid": _sc_bid,
                    "_short_call_ask": _sc_ask,
                    "_long_call_bid": _lc_bid,
                    "_long_call_ask": _lc_ask,
                    # ── Per-leg delta (missing_delta counter compat) ─────────
                    # short_delta_abs = |short_put.delta| (credit-critical leg).
                    # Also store per-leg deltas for full trace.
                    "delta": _leg_fields["short_put"]["delta"],
                    "short_delta": _leg_fields["short_put"]["delta"],
                    "short_delta_abs": (
                        abs(_leg_fields["short_put"]["delta"])
                        if _leg_fields["short_put"]["delta"] is not None else None
                    ),
                    "_short_put_delta": _leg_fields["short_put"]["delta"],
                    "_long_put_delta":  _leg_fields["long_put"]["delta"],
                    "_short_call_delta": _leg_fields["short_call"]["delta"],
                    "_long_call_delta":  _leg_fields["long_call"]["delta"],
                    # ── Credit basis ────────────────────────────────────────
                    "_credit_basis": "mid",
                    # ── Per-candidate trace diagnostics (no scoring impact) ─
                    "_leg_diagnostics": _candidate_diag,
                }
            )

        # ── Attach DQ-fail samples to inputs for filter-trace consumption ──
        if dq_fail_samples:
            inputs["_ic_dq_fail_samples"] = dq_fail_samples
            logger.info(
                "event=ic_enrich_dq_summary dq_sample_count=%d",
                len(dq_fail_samples),
            )

        # ── Debug guardrail: log first enriched trade's canonical legs ──
        # Verify bid/ask/delta/iv persist from OptionContract through to
        # enriched output.  This log is required to prevent silent regression
        # where mids exist but bid/ask/delta are dropped.
        if out:
            _sample = out[0]
            _sample_legs = _sample.get("legs") or []
            _leg_audit = [
                {
                    "name": lg.get("name"),
                    "bid": lg.get("bid"),
                    "ask": lg.get("ask"),
                    "mid": lg.get("mid"),
                    "delta": lg.get("delta"),
                    "iv": lg.get("iv"),
                    "oi": lg.get("open_interest"),
                    "vol": lg.get("volume"),
                    "occ": lg.get("occ_symbol"),
                }
                for lg in _sample_legs if isinstance(lg, dict)
            ]
            # INVARIANT: if mid exists, bid and ask MUST also exist
            _mid_without_ba = [
                lg["name"] for lg in _leg_audit
                if lg.get("mid") is not None and (lg.get("bid") is None or lg.get("ask") is None)
            ]
            if _mid_without_ba:
                logger.warning(
                    "event=ic_enrich_invariant_violation "
                    "mid_exists_without_bid_ask legs=%s",
                    _mid_without_ba,
                )
            logger.info(
                "event=ic_enrich_sample total=%d readiness=%s "
                "net_credit=%s spread_bid=%s spread_ask=%s "
                "delta=%s short_delta_abs=%s "
                "leg_count=%d legs=%s",
                len(out),
                _sample.get("readiness"),
                _sample.get("net_credit"),
                _sample.get("spread_bid"),
                _sample.get("spread_ask"),
                _sample.get("delta"),
                _sample.get("short_delta_abs"),
                len(_sample_legs),
                _leg_audit,
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
