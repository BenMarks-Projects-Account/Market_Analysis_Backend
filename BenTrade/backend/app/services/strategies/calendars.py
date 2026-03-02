from __future__ import annotations

import logging
import math
from typing import Any

from app.services.ranking import safe_float
from app.services.strategies.base import POP_SOURCE_NONE, StrategyPlugin

logger = logging.getLogger(__name__)


class CalendarsStrategyPlugin(StrategyPlugin):
    id = "calendars"
    display_name = "Calendar Spread Analysis"

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
        symbols = sorted(set(str((s or {}).get("symbol") or "").upper() for s in snapshots if isinstance(s, dict)))

        near_dte_min = int(payload.get("near_dte_min") or 7)
        near_dte_max = int(payload.get("near_dte_max") or 14)
        far_dte_min = int(payload.get("far_dte_min") or 30)
        far_dte_max = int(payload.get("far_dte_max") or 60)
        if near_dte_max < near_dte_min:
            near_dte_min, near_dte_max = near_dte_max, near_dte_min
        if far_dte_max < far_dte_min:
            far_dte_min, far_dte_max = far_dte_max, far_dte_min

        moneyness = str(payload.get("moneyness") or "atm").strip().lower()
        if moneyness not in {"atm", "itm", "otm"}:
            moneyness = "atm"

        # Safety ceiling only — preset max_candidates is applied centrally
        # by select_top_n() in strategy_service.generate().
        max_candidates = int(inputs.get("_generation_cap") or 20_000)
        results: list[dict[str, Any]] = []

        for symbol in symbols:
            symbol_snaps = [s for s in snapshots if str((s or {}).get("symbol") or "").upper() == symbol]
            near_snaps = [s for s in symbol_snaps if near_dte_min <= int((s or {}).get("dte") or 0) <= near_dte_max]
            far_snaps = [s for s in symbol_snaps if far_dte_min <= int((s or {}).get("dte") or 0) <= far_dte_max]
            if not near_snaps or not far_snaps:
                continue

            near_snaps.sort(key=lambda s: int((s or {}).get("dte") or 0))
            far_snaps.sort(key=lambda s: int((s or {}).get("dte") or 0))

            for near in near_snaps:
                near_dte = int((near or {}).get("dte") or 0)
                near_price = self._to_float((near or {}).get("underlying_price"))
                near_contracts = (near or {}).get("contracts") or []
                if near_price is None or not near_contracts:
                    continue

                far = next((f for f in far_snaps if int((f or {}).get("dte") or 0) > near_dte), None)
                if far is None:
                    continue
                far_dte = int((far or {}).get("dte") or 0)
                far_contracts = (far or {}).get("contracts") or []
                if not far_contracts:
                    continue

                near_calls = self._strike_map(near_contracts, "call")
                near_puts = self._strike_map(near_contracts, "put")
                far_calls = self._strike_map(far_contracts, "call")
                far_puts = self._strike_map(far_contracts, "put")

                for side, near_map, far_map in (("call", near_calls, far_calls), ("put", near_puts, far_puts)):
                    common_strikes = sorted(set(near_map.keys()) & set(far_map.keys()))
                    if not common_strikes:
                        continue

                    strike_window = near_price * 0.06
                    candidate_strikes = [s for s in common_strikes if abs(s - near_price) <= strike_window]
                    if not candidate_strikes:
                        continue

                    if moneyness == "itm":
                        if side == "call":
                            candidate_strikes = [s for s in candidate_strikes if s <= near_price]
                        else:
                            candidate_strikes = [s for s in candidate_strikes if s >= near_price]
                    elif moneyness == "otm":
                        if side == "call":
                            candidate_strikes = [s for s in candidate_strikes if s >= near_price]
                        else:
                            candidate_strikes = [s for s in candidate_strikes if s <= near_price]

                    if not candidate_strikes:
                        continue

                    center_strike = min(candidate_strikes, key=lambda s: abs(s - near_price))
                    near_leg = near_map.get(center_strike)
                    far_leg = far_map.get(center_strike)
                    if near_leg is None or far_leg is None:
                        continue

                    results.append(
                        {
                            "strategy": "calendar_spread",
                            "spread_type": f"calendar_{side}_spread",
                            "option_side": side,
                            "symbol": symbol,
                            "expiration_near": str((near or {}).get("expiration") or ""),
                            "expiration_far": str((far or {}).get("expiration") or ""),
                            "expiration": str((far or {}).get("expiration") or ""),
                            "dte_near": near_dte,
                            "dte_far": far_dte,
                            "dte": far_dte,
                            "underlying_price": float(near_price),
                            "strike": float(center_strike),
                            "short_strike": float(center_strike),
                            "long_strike": float(center_strike),
                            "near_leg": near_leg,
                            "far_leg": far_leg,
                            "near_snapshot": near,
                            "far_snapshot": far,
                        }
                    )

                    if len(results) >= max_candidates:
                        return results

        return results

    @staticmethod
    def _leg_spread(leg: Any) -> float:
        bid = safe_float(getattr(leg, "bid", None)) or 0.0
        ask = safe_float(getattr(leg, "ask", None)) or bid
        return max(0.0, ask - bid)

    def enrich(self, candidates: list[dict[str, Any]], inputs: dict[str, Any]) -> list[dict[str, Any]]:
        payload = inputs.get("request") or {}
        policy = inputs.get("policy") or {}
        max_debit_req = self._to_float(payload.get("max_debit"))
        event_risk_flag = str(payload.get("event_risk_flag") or "false").lower() in {"1", "true", "yes", "y"}

        out: list[dict[str, Any]] = []
        for row in candidates:
            near_leg = row.get("near_leg")
            far_leg = row.get("far_leg")
            if near_leg is None or far_leg is None:
                continue

            symbol = str(row.get("symbol") or "").upper()
            spread_type = str(row.get("spread_type") or "calendar_spread")
            strike = float(row.get("strike") or 0.0)
            spot = float(row.get("underlying_price") or 0.0)
            dte_near = int(row.get("dte_near") or 0)
            dte_far = int(row.get("dte_far") or 0)
            near_exp = str(row.get("expiration_near") or "")
            far_exp = str(row.get("expiration_far") or "")

            # ── Per-leg bid/ask extraction (None-safe) ───────────────
            near_bid = safe_float(getattr(near_leg, "bid", None))
            near_ask = safe_float(getattr(near_leg, "ask", None))
            far_bid = safe_float(getattr(far_leg, "bid", None))
            far_ask = safe_float(getattr(far_leg, "ask", None))

            # ── Per-leg mid (None if either bid or ask missing) ──────
            # Formula: mid = (bid + ask) / 2.  None if either missing.
            near_mid: float | None = None
            if near_bid is not None and near_ask is not None:
                near_mid = (near_bid + near_ask) / 2.0
            far_mid: float | None = None
            if far_bid is not None and far_ask is not None:
                far_mid = (far_bid + far_ask) / 2.0

            # ── Spread pricing ───────────────────────────────────────
            # spread_mid = mid(far) - mid(near)  (mid-market fill)
            # spread_natural = ask(far) - bid(near)  (worst-case fill)
            # spread_mark = spread_mid  (initially = mid)
            spread_mid: float | None = None
            if far_mid is not None and near_mid is not None:
                spread_mid = far_mid - near_mid
            spread_natural: float | None = None
            if far_ask is not None and near_bid is not None:
                spread_natural = far_ask - near_bid
            spread_mark = spread_mid  # mark = mid initially

            # ── execution_invalid gating ─────────────────────────────
            execution_invalid = False
            execution_invalid_reason: str | None = None

            # Gate: both legs must have both bid and ask
            _near_missing = near_bid is None or near_ask is None
            _far_missing = far_bid is None or far_ask is None
            if _near_missing or _far_missing:
                execution_invalid = True
                execution_invalid_reason = "leg_quote_missing"

            # Gate: spread_mid must be computable
            if not execution_invalid and spread_mid is None:
                execution_invalid = True
                execution_invalid_reason = "pricing_unavailable"

            # ── net_debit computation ────────────────────────────────
            # Primary: spread_mid.  Fallback: spread_natural.
            # Formula: net_debit = spread_mid (preferred) or spread_natural
            net_debit: float | None = spread_mid
            if net_debit is None:
                net_debit = spread_natural

            # Gate: net_debit must be positive
            if not execution_invalid:
                if net_debit is None or net_debit <= 0:
                    execution_invalid = True
                    execution_invalid_reason = "net_debit_non_positive"
            # Gate: small debit (< $0.05/share)
            if not execution_invalid and net_debit is not None and net_debit < 0.05:
                execution_invalid = True
                execution_invalid_reason = "net_debit_too_small"

            # max_debit cap (skip entirely if over cap)
            if net_debit is not None and max_debit_req is not None and net_debit > max_debit_req:
                continue

            # ── max_loss (= debit paid × 100) ───────────────────────
            # Max loss on a calendar = the debit paid.
            max_loss: float | None = None
            if net_debit is not None and net_debit > 0:
                max_loss = net_debit * 100.0

            # ── max_profit: unknown for calendars (path-dependent) ───
            # Calendar max profit depends on near-term IV and price at
            # near expiration — not solvable from static quotes alone.
            max_profit: float | None = None
            max_profit_per_contract: float | None = None
            return_on_risk: float | None = None
            expected_value: float | None = None
            ev_per_contract: float | None = None
            ev_per_share: float | None = None
            p_win_used: float | None = None

            # ── Greeks & structure ───────────────────────────────────
            theta_near = safe_float(getattr(near_leg, "theta", None)) or 0.0
            theta_far = safe_float(getattr(far_leg, "theta", None)) or 0.0
            theta_structure = (-theta_near) - (-theta_far)

            vega_near = safe_float(getattr(near_leg, "vega", None)) or 0.0
            vega_far = safe_float(getattr(far_leg, "vega", None)) or 0.0
            vega_exposure = vega_far - vega_near

            iv_near = safe_float(getattr(near_leg, "iv", None))
            iv_far = safe_float(getattr(far_leg, "iv", None))
            iv_term_structure_score = 0.5
            if iv_near not in (None, 0) and iv_far not in (None, 0):
                iv_term_structure_score = self._clamp((iv_far - iv_near + 0.12) / 0.30)

            expected_move_near = spot * max(iv_near or iv_far or 0.20, 0.05) * math.sqrt(max(dte_near, 1) / 365.0)
            blow_through_risk = self._clamp(max(0.0, abs(spot - strike) / max(expected_move_near, 0.1)))

            break_even_low = strike - (net_debit * 1.5) if net_debit is not None and net_debit > 0 else None
            break_even_high = strike + (net_debit * 1.5) if net_debit is not None and net_debit > 0 else None

            # ── Liquidity ────────────────────────────────────────────
            near_oi = int(safe_float(getattr(near_leg, "open_interest", None)) or 0)
            far_oi = int(safe_float(getattr(far_leg, "open_interest", None)) or 0)
            near_vol = int(safe_float(getattr(near_leg, "volume", None)) or 0)
            far_vol = int(safe_float(getattr(far_leg, "volume", None)) or 0)
            near_spread = self._leg_spread(near_leg)
            far_spread = self._leg_spread(far_leg)

            min_oi = min(near_oi, far_oi)
            min_vol = min(near_vol, far_vol)
            worst_spread = max(near_spread, far_spread)

            oi_ref = max(float(policy.get("min_open_interest") or 100), 1.0)
            vol_ref = max(float(policy.get("min_volume") or 20), 1.0)
            oi_score = self._clamp((min_oi / oi_ref) / 1.5)
            vol_score = self._clamp((min_vol / vol_ref) / 1.5)
            spread_score = self._clamp(1.0 - (worst_spread / max((net_debit or 0.01) * 1.5, 0.25)))
            liquidity_score = self._clamp((0.45 * oi_score) + (0.30 * vol_score) + (0.25 * spread_score))

            move_risk_score = self._clamp(1.0 - (abs(spot - strike) / max(expected_move_near, 0.25)))
            debit_vs_move_penalty = self._clamp(((net_debit or 0) / max(expected_move_near, 0.1) - 0.45) / 0.8)

            # ── Sanity / diagnostic metrics ──────────────────────────
            # These are NOT substitutes for POP/EV — diagnostics only.
            # debit_as_pct_of_underlying = net_debit / underlying_price
            debit_as_pct_of_underlying: float | None = None
            if net_debit is not None and spot > 0:
                debit_as_pct_of_underlying = round(net_debit / spot, 6)
            # debit_as_pct_of_expected_move = net_debit / expected_move_near
            debit_as_pct_of_expected_move: float | None = None
            if net_debit is not None and expected_move_near > 0:
                debit_as_pct_of_expected_move = round(net_debit / expected_move_near, 4)
            # term_structure_ok: True if iv_term_structure_score >= 0.40
            term_structure_ok = iv_term_structure_score >= 0.40

            # ── Ranking ──────────────────────────────────────────────
            why_term_structure = iv_term_structure_score
            why_move_risk = move_risk_score
            why_liquidity = liquidity_score

            if execution_invalid:
                # Invalid trades get rank_score = 0 so they never surface
                rank_score = 0.0
            else:
                rank_score = self._clamp(
                    (0.36 * why_term_structure)
                    + (0.28 * why_move_risk)
                    + (0.24 * why_liquidity)
                    + (0.12 * self._clamp((vega_exposure + 0.05) / 0.25))
                    - (0.18 * debit_vs_move_penalty)
                )

            bid_ask_spread_pct = self._clamp(worst_spread / max(net_debit or 0.10, 0.10), 0.0, 9.99)

            trade_key = f"{symbol}|{near_exp}->{far_exp}|{spread_type}|K{strike}|{dte_near}->{dte_far}"

            out.append(
                {
                    "strategy": "calendar_spread",
                    "spread_type": spread_type,
                    "option_side": row.get("option_side"),
                    "underlying": symbol,
                    "underlying_symbol": symbol,
                    "symbol": symbol,
                    "expiration": far_exp,
                    "expiration_near": near_exp,
                    "expiration_far": far_exp,
                    "dte": dte_far,
                    "dte_near": dte_near,
                    "dte_far": dte_far,
                    "underlying_price": spot,
                    "strike": strike,
                    "short_strike": strike,
                    "long_strike": strike,
                    # ── Pricing (Requirement 1) ──────────────────────
                    "spread_mid": spread_mid,
                    "spread_natural": spread_natural,
                    "spread_mark": spread_mark,
                    "net_debit": net_debit,
                    "net_credit": None,
                    # Per-leg quotes for traceability
                    "near_bid": near_bid,
                    "near_ask": near_ask,
                    "near_mid": near_mid,
                    "far_bid": far_bid,
                    "far_ask": far_ask,
                    "far_mid": far_mid,
                    # ── Payoff ───────────────────────────────────────
                    "max_profit": max_profit,
                    "max_profit_per_contract": max_profit_per_contract,
                    "max_loss": max_loss,
                    "max_loss_per_contract": max_loss if max_loss is not None else None,
                    "return_on_risk": return_on_risk,
                    # ── Probability / EV ─────────────────────────────
                    "p_win_used": p_win_used,
                    "pop_model_used": POP_SOURCE_NONE,
                    "ev_per_contract": ev_per_contract,
                    "ev_per_share": ev_per_share,
                    "expected_value": expected_value,
                    # ── Greeks & structure ────────────────────────────
                    "theta_structure": theta_structure,
                    "vega_exposure": vega_exposure,
                    "iv_term_structure_score": iv_term_structure_score,
                    "expected_move_near": expected_move_near,
                    "break_even_low": break_even_low,
                    "break_even_high": break_even_high,
                    "break_evens_low": break_even_low,
                    "break_evens_high": break_even_high,
                    "break_even": break_even_low,
                    # ── Liquidity ────────────────────────────────────
                    "liquidity_score": liquidity_score,
                    "open_interest": min_oi,
                    "volume": min_vol,
                    "worst_leg_spread": worst_spread,
                    "bid_ask_spread_pct": bid_ask_spread_pct,
                    # ── Risk & diagnostics ───────────────────────────
                    "event_risk_flag": event_risk_flag,
                    "move_risk_score": move_risk_score,
                    "why_term_structure": why_term_structure,
                    "why_move_risk": why_move_risk,
                    "why_liquidity": why_liquidity,
                    # ── Sanity metrics (Requirement 5) ───────────────
                    "debit_as_pct_of_underlying": debit_as_pct_of_underlying,
                    "debit_as_pct_of_expected_move": debit_as_pct_of_expected_move,
                    "term_structure_ok": term_structure_ok,
                    # ── Execution gating (Requirement 2) ─────────────
                    "execution_invalid": execution_invalid,
                    "execution_invalid_reason": execution_invalid_reason,
                    "readiness": not execution_invalid,
                    # ── Scoring ──────────────────────────────────────
                    "trade_key": trade_key,
                    "rank_score": rank_score,
                    "contractsMultiplier": 100,
                    "selection_reasons": [],
                }
            )

        return out

    def evaluate(self, trade: dict[str, Any]) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        policy = trade.get("_policy") if isinstance(trade.get("_policy"), dict) else {}
        request_payload = trade.get("_request") if isinstance(trade.get("_request"), dict) else {}

        # ── Gate 0: execution_invalid pre-check ──────────────────────
        if trade.get("execution_invalid"):
            reason = trade.get("execution_invalid_reason") or "pricing_unavailable"
            reasons.append(f"execution_invalid:{reason}")
            return False, reasons

        # ── Gate 0a: pricing must exist ──────────────────────────────
        if trade.get("spread_mid") is None and trade.get("net_debit") is None:
            reasons.append("pricing_unavailable")
            return False, reasons

        # ── Gate 0b: strategy_metrics_complete ───────────────────────
        # Calendars intentionally lack POP / EV / max_profit.
        # This is NOT a data-quality failure — these metrics are
        # unknowable without a full pricing model.  Mark as
        # METRICS_NOT_IMPLEMENTED so the UI can show "INCOMPLETE METRICS"
        # and block execution.  Per-field METRICS_MISSING reasons are
        # also emitted for traceability.
        _strategy_incomplete_fields: list[str] = []
        if trade.get("p_win_used") is None:
            _strategy_incomplete_fields.append("pop")
        if trade.get("expected_value") is None:
            _strategy_incomplete_fields.append("expected_value")
        if trade.get("max_profit") is None:
            _strategy_incomplete_fields.append("max_profit")
        if trade.get("return_on_risk") is None:
            _strategy_incomplete_fields.append("return_on_risk")

        if _strategy_incomplete_fields:
            reasons.append("METRICS_NOT_IMPLEMENTED")
            for _sif in _strategy_incomplete_fields:
                reasons.append(f"METRICS_MISSING:{_sif}")

        # ── Gate 1: event risk ───────────────────────────────────────
        allow_event_risk = str(request_payload.get("allow_event_risk") or "false").lower() in {"1", "true", "yes", "y"}
        if bool(trade.get("event_risk_flag")) and not allow_event_risk:
            reasons.append("event_risk_flagged")

        # ── Gate 2: liquidity (OI / volume) ──────────────────────────
        min_oi_policy = int(safe_float(request_payload.get("min_open_interest")) or 0)
        if min_oi_policy <= 0:
            min_oi_policy = max(int(safe_float(policy.get("min_open_interest")) or 0), 500)

        min_vol_policy = int(safe_float(request_payload.get("min_volume")) or 0)
        if min_vol_policy <= 0:
            min_vol_policy = max(int(safe_float(policy.get("min_volume")) or 0), 50)

        min_oi_req = max(5, int(min_oi_policy * 0.2)) if min_oi_policy > 0 else 5
        min_vol_req = max(1, int(min_vol_policy * 0.2)) if min_vol_policy > 0 else 1

        open_interest = int(safe_float(trade.get("open_interest")) or 0)
        volume = int(safe_float(trade.get("volume")) or 0)
        if open_interest < min_oi_req and volume < min_vol_req:
            reasons.append("calendar_liquidity_low")

        if (safe_float(trade.get("liquidity_score")) or 0.0) < 0.15:
            reasons.append("calendar_liquidity_score_low")

        # ── Gate 3: bid-ask spread ───────────────────────────────────
        spread_pct_limit = self._to_float(request_payload.get("max_bid_ask_spread_pct"))
        if spread_pct_limit is None:
            spread_pct_limit = self._to_float(policy.get("max_bid_ask_spread_pct"))
        if spread_pct_limit is None:
            spread_pct_limit = 1.5

        spread_pct = safe_float(trade.get("bid_ask_spread_pct"))
        if spread_pct is not None and (spread_pct * 100.0) > spread_pct_limit:
            reasons.append("calendar_spread_too_wide")

        # ── Gate 4: max debit ────────────────────────────────────────
        max_debit = self._to_float(request_payload.get("max_debit"))
        net_debit = self._to_float(trade.get("net_debit"))
        if max_debit is not None and net_debit is not None and net_debit > max_debit:
            reasons.append("calendar_debit_above_max")

        return len(reasons) == 0, reasons

    def score(self, trade: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        rank_score = float(safe_float(trade.get("rank_score")) or 0.0)
        tie_breaks = {
            "edge": safe_float(trade.get("why_term_structure")) or 0.0,
            "liquidity": safe_float(trade.get("why_liquidity")) or 0.0,
            "conviction": safe_float(trade.get("why_move_risk")) or 0.0,
        }
        return rank_score, tie_breaks
