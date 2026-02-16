from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from app.utils.trade_key import trade_key


class RiskPolicyService:
    def __init__(self, results_dir: Path) -> None:
        self.results_dir = results_dir
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.policy_path = self.results_dir / "risk_policy.json"
        self._lock = RLock()

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    def default_policy(self) -> dict[str, Any]:
        return {
            "portfolio_size": 100000.0,
            "max_total_risk_pct": 0.06,
            "max_symbol_risk_pct": 0.02,
            "max_trade_risk_pct": 0.01,
            "max_dte": 45,
            "min_cash_reserve_pct": 20.0,
            "max_position_size_pct": 5.0,
            "default_contracts_cap": 3,
            "max_risk_per_trade": 1000.0,
            "max_risk_total": 6000.0,
            "max_concurrent_trades": 10,
            "max_risk_per_underlying": 2000.0,
            "max_same_expiration_risk": 500.0,
            "max_short_strike_distance_sigma": 2.5,
            "min_open_interest": 500,
            "min_volume": 50,
            "max_bid_ask_spread_pct": 1.5,
            "min_pop": 0.60,
            "min_ev_to_risk": 0.02,
            "min_return_on_risk": 0.10,
            "max_iv_rv_ratio_for_buying": 1.0,
            "min_iv_rv_ratio_for_selling": 1.1,
            "notes": "",
        }

    def get_policy(self) -> dict[str, Any]:
        with self._lock:
            if not self.policy_path.exists():
                policy = self.default_policy()
                self.policy_path.write_text(json.dumps(policy, indent=2), encoding="utf-8")
                return policy

            try:
                with open(self.policy_path, "r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
                if not isinstance(loaded, dict):
                    return self.default_policy()
            except Exception:
                return self.default_policy()

        merged = self.default_policy()
        merged.update(loaded)

        if loaded != merged:
            with self._lock:
                self.policy_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
        return merged

    def save_policy(self, updates: dict[str, Any]) -> dict[str, Any]:
        incoming = updates if isinstance(updates, dict) else {}
        policy = self.get_policy()

        float_keys = (
            "portfolio_size",
            "max_total_risk_pct",
            "max_symbol_risk_pct",
            "max_trade_risk_pct",
            "min_cash_reserve_pct",
            "max_position_size_pct",
            "max_risk_per_trade",
            "max_risk_total",
            "max_risk_per_underlying",
            "max_same_expiration_risk",
            "max_short_strike_distance_sigma",
            "max_bid_ask_spread_pct",
            "min_pop",
            "min_ev_to_risk",
            "min_return_on_risk",
            "max_iv_rv_ratio_for_buying",
            "min_iv_rv_ratio_for_selling",
        )
        for key in float_keys:
            if key in incoming:
                val = self._safe_float(incoming.get(key))
                if val is not None and val >= 0:
                    policy[key] = val

        int_keys = ("max_dte", "default_contracts_cap", "max_concurrent_trades", "min_open_interest", "min_volume")
        for key in int_keys:
            if key in incoming:
                parsed = self._safe_int(incoming.get(key))
                if parsed is not None and parsed >= 0:
                    policy[key] = parsed

        if "notes" in incoming:
            policy["notes"] = str(incoming.get("notes") or "")

        with self._lock:
            self.policy_path.write_text(json.dumps(policy, indent=2), encoding="utf-8")

        return policy

    def _estimate_risk_from_active(self, trade: dict[str, Any]) -> tuple[float | None, dict[str, Any]]:
        quantity = self._safe_float(trade.get("quantity"))
        short_strike = self._safe_float(trade.get("short_strike"))
        long_strike = self._safe_float(trade.get("long_strike"))
        avg_open_price = self._safe_float(trade.get("avg_open_price"))

        width = abs(short_strike - long_strike) if short_strike is not None and long_strike is not None else None
        credit = avg_open_price
        max_loss = None
        note = "estimated from active trade"

        if width is not None and quantity is not None:
            if credit is not None:
                max_loss = max(width - credit, 0.0) * abs(quantity) * 100.0
            else:
                max_loss = width * abs(quantity) * 100.0
                note = "credit missing; width-only estimate"
        elif trade.get("strategy") == "single":
            mark = self._safe_float(trade.get("mark_price"))
            if mark is not None and quantity is not None:
                max_loss = abs(mark) * abs(quantity) * 100.0
                note = "single-leg estimate from mark"

        breakdown = {
            "width": width,
            "credit": credit,
            "max_loss": max_loss,
            "kelly_fraction": self._safe_float(trade.get("kelly_fraction")),
            "notes": note,
        }
        return max_loss, breakdown

    def _trade_row_from_active(self, trade: dict[str, Any]) -> dict[str, Any]:
        est_risk, breakdown = self._estimate_risk_from_active(trade)
        symbol = str(trade.get("symbol") or trade.get("underlying") or "").upper()
        tkey = str(trade.get("trade_key") or trade_key(
            underlying=symbol,
            expiration=trade.get("expiration"),
            spread_type=trade.get("strategy") or trade.get("spread_type"),
            short_strike=trade.get("short_strike"),
            long_strike=trade.get("long_strike"),
            dte=trade.get("dte"),
        ))

        return {
            "trade_key": tkey,
            "symbol": symbol,
            "estimated_risk": est_risk,
            "dte": self._safe_int(trade.get("dte")),
            "expiration": trade.get("expiration"),
            "quantity": self._safe_int(trade.get("quantity")),
            "strategy": trade.get("strategy") or trade.get("spread_type"),
            "notes": breakdown.get("notes") or "",
            "width": breakdown.get("width"),
            "credit": breakdown.get("credit"),
            "max_loss": breakdown.get("max_loss"),
            "kelly_fraction": breakdown.get("kelly_fraction"),
            "short_strike_z": self._safe_float(trade.get("short_strike_z")),
            "open_interest": self._safe_int(trade.get("open_interest")),
            "volume": self._safe_int(trade.get("volume")),
            "bid_ask_spread_pct": self._safe_float(trade.get("bid_ask_spread_pct")),
            "p_win_used": self._safe_float(trade.get("p_win_used") or trade.get("pop_delta_approx")),
            "return_on_risk": self._safe_float(trade.get("return_on_risk")),
            "iv_rv_ratio": self._safe_float(trade.get("iv_rv_ratio")),
            "ev_per_share": self._safe_float(trade.get("ev_per_share") or trade.get("expected_value")),
        }

    def _latest_report_file_candidates(self) -> list[Path]:
        candidates = list(self.results_dir.glob("analysis_*.json"))

        try:
            outer = self.results_dir.parent.parent / "results"
            if outer.exists() and outer.is_dir():
                candidates.extend(list(outer.glob("analysis_*.json")))
        except Exception:
            pass

        return sorted(set(candidates), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)

    def _extract_report_trades(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        if isinstance(payload, dict):
            trades = payload.get("trades")
            if isinstance(trades, list):
                return [x for x in trades if isinstance(x, dict)]
        return []

    def _trade_row_from_report(self, trade: dict[str, Any]) -> dict[str, Any]:
        symbol = str(trade.get("underlying") or trade.get("underlying_symbol") or "").upper()
        expiration = trade.get("expiration")
        spread_type = trade.get("spread_type") or trade.get("strategy")
        short_strike = trade.get("short_strike")
        long_strike = trade.get("long_strike")
        dte = self._safe_int(trade.get("dte"))

        max_loss = self._safe_float(trade.get("max_loss"))
        max_loss_share = self._safe_float(trade.get("max_loss_per_share"))
        contracts_multiplier = self._safe_float(trade.get("contractsMultiplier")) or 100.0

        est_risk = max_loss
        note = "estimated from report"
        if est_risk is None and max_loss_share is not None:
            est_risk = max_loss_share * contracts_multiplier
        if est_risk is None:
            note = "under construction: max loss unavailable"

        width = self._safe_float(trade.get("width"))
        credit = self._safe_float(trade.get("net_credit"))
        kelly = self._safe_float(trade.get("kelly_fraction"))

        tkey = trade_key(
            underlying=symbol,
            expiration=expiration,
            spread_type=spread_type,
            short_strike=short_strike,
            long_strike=long_strike,
            dte=dte,
        )

        return {
            "trade_key": tkey,
            "symbol": symbol,
            "estimated_risk": est_risk,
            "dte": dte,
            "expiration": expiration,
            "quantity": self._safe_int(trade.get("quantity") or trade.get("contracts") or trade.get("contracts_count")) or 1,
            "strategy": spread_type,
            "notes": note,
            "width": width,
            "credit": credit,
            "max_loss": est_risk,
            "kelly_fraction": kelly,
            "short_strike_z": self._safe_float(trade.get("short_strike_z")),
            "open_interest": self._safe_int(trade.get("open_interest")),
            "volume": self._safe_int(trade.get("volume")),
            "bid_ask_spread_pct": self._safe_float(trade.get("bid_ask_spread_pct")),
            "p_win_used": self._safe_float(trade.get("p_win_used") or trade.get("pop_delta_approx")),
            "return_on_risk": self._safe_float(trade.get("return_on_risk")),
            "iv_rv_ratio": self._safe_float(trade.get("iv_rv_ratio")),
            "ev_per_share": self._safe_float(trade.get("ev_per_share") or trade.get("expected_value")),
        }

    async def _trades_from_active(self, request: Any) -> list[dict[str, Any]]:
        from app.api.routes_active_trades import _build_active_payload

        try:
            payload = await _build_active_payload(request)
        except Exception:
            return []

        if not isinstance(payload, dict) or payload.get("error"):
            return []

        active = payload.get("active_trades")
        if not isinstance(active, list):
            return []

        out: list[dict[str, Any]] = []
        for trade in active:
            if not isinstance(trade, dict):
                continue
            try:
                out.append(self._trade_row_from_active(trade))
            except Exception:
                continue
        return out

    def _trades_from_report(self) -> list[dict[str, Any]]:
        for path in self._latest_report_file_candidates():
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    payload = json.load(fh)
                trades = self._extract_report_trades(payload)
                if not trades:
                    continue

                rows: list[dict[str, Any]] = []
                for trade in trades:
                    rec = str((trade.get("model_evaluation") or {}).get("recommendation") or "").upper()
                    if rec == "REJECT":
                        continue
                    rows.append(self._trade_row_from_report(trade))
                if rows:
                    return rows
            except Exception:
                continue
        return []

    def _build_warning_groups(self, policy: dict[str, Any], trades: list[dict[str, Any]], total_used: float | None, risk_by_symbol: list[dict[str, Any]]) -> dict[str, list[str]]:
        hard_limits: list[str] = []
        soft_gates: list[str] = []

        portfolio = self._safe_float(policy.get("portfolio_size"))
        max_total_pct = self._safe_float(policy.get("max_total_risk_pct"))
        max_symbol_pct = self._safe_float(policy.get("max_symbol_risk_pct"))
        max_trade_pct = self._safe_float(policy.get("max_trade_risk_pct"))
        max_dte = self._safe_int(policy.get("max_dte"))

        max_risk_total = self._safe_float(policy.get("max_risk_total"))
        max_risk_per_underlying = self._safe_float(policy.get("max_risk_per_underlying"))
        max_risk_per_trade = self._safe_float(policy.get("max_risk_per_trade"))
        max_concurrent = self._safe_int(policy.get("max_concurrent_trades"))
        min_cash_reserve_pct = self._safe_float(policy.get("min_cash_reserve_pct"))
        max_position_size_pct = self._safe_float(policy.get("max_position_size_pct"))
        default_contracts_cap = self._safe_int(policy.get("default_contracts_cap"))
        max_same_exp_risk = self._safe_float(policy.get("max_same_expiration_risk"))

        max_short_strike_distance_sigma = self._safe_float(policy.get("max_short_strike_distance_sigma"))
        min_open_interest = self._safe_int(policy.get("min_open_interest"))
        min_volume = self._safe_int(policy.get("min_volume"))
        max_bid_ask_spread_pct = self._safe_float(policy.get("max_bid_ask_spread_pct"))

        min_pop = self._safe_float(policy.get("min_pop"))
        min_ev_to_risk = self._safe_float(policy.get("min_ev_to_risk"))
        min_return_on_risk = self._safe_float(policy.get("min_return_on_risk"))
        max_iv_rv_ratio_for_buying = self._safe_float(policy.get("max_iv_rv_ratio_for_buying"))
        min_iv_rv_ratio_for_selling = self._safe_float(policy.get("min_iv_rv_ratio_for_selling"))

        if max_risk_total is not None and total_used is not None and total_used > max_risk_total:
            hard_limits.append("Total estimated risk exceeds max_risk_total.")

        if portfolio is not None and max_total_pct is not None and total_used is not None:
            pct_cap = portfolio * max_total_pct
            if total_used > pct_cap:
                hard_limits.append("Total estimated risk exceeds max_total_risk_pct cap.")

        if max_concurrent is not None and len(trades) > max_concurrent:
            hard_limits.append("Open trades exceed max_concurrent_trades.")

        if portfolio is not None and min_cash_reserve_pct is not None and total_used is not None:
            reserve_floor = portfolio * (min_cash_reserve_pct / 100.0)
            remaining_cash = portfolio - total_used
            if remaining_cash < reserve_floor:
                hard_limits.append("Estimated cash reserve is below min_cash_reserve_pct.")

        if portfolio is not None and max_position_size_pct is not None:
            per_position_cap = portfolio * (max_position_size_pct / 100.0)
            for trade in trades:
                risk = self._safe_float(trade.get("estimated_risk"))
                if risk is not None and risk > per_position_cap:
                    hard_limits.append(f"Trade {trade.get('trade_key')} exceeds max_position_size_pct.")

        for row in risk_by_symbol:
            risk = self._safe_float(row.get("risk"))
            symbol = row.get("symbol")
            if max_risk_per_underlying is not None and risk is not None and risk > max_risk_per_underlying:
                hard_limits.append(f"{symbol} exceeds max_risk_per_underlying.")

            if portfolio is not None and max_symbol_pct is not None and risk is not None:
                pct_cap = portfolio * max_symbol_pct
                if risk > pct_cap:
                    hard_limits.append(f"{symbol} exceeds max_symbol_risk_pct cap.")

        exp_risk: dict[str, float] = {}
        for trade in trades:
            risk = self._safe_float(trade.get("estimated_risk"))
            exp = str(trade.get("expiration") or "")
            if risk is None or not exp:
                continue
            exp_risk[exp] = exp_risk.get(exp, 0.0) + risk
        if max_same_exp_risk is not None:
            for exp, exp_total in exp_risk.items():
                if exp_total > max_same_exp_risk:
                    hard_limits.append(f"Expiration {exp} exceeds max_same_expiration_risk.")

        for trade in trades:
            risk = self._safe_float(trade.get("estimated_risk"))
            key = str(trade.get("trade_key") or "")
            if max_risk_per_trade is not None and risk is not None and risk > max_risk_per_trade:
                hard_limits.append(f"Trade {key} exceeds max_risk_per_trade.")

            if portfolio is not None and max_trade_pct is not None and risk is not None:
                pct_cap = portfolio * max_trade_pct
                if risk > pct_cap:
                    hard_limits.append(f"Trade {key} exceeds max_trade_risk_pct cap.")

            dte = self._safe_int(trade.get("dte"))
            if max_dte is not None and dte is not None and dte > max_dte:
                hard_limits.append(f"Trade {key} exceeds max_dte policy.")

            contracts = self._safe_int(trade.get("quantity"))
            if default_contracts_cap is not None and contracts is not None and contracts > default_contracts_cap:
                hard_limits.append(f"Trade {key} exceeds default_contracts_cap.")

            short_z = self._safe_float(trade.get("short_strike_z"))
            if max_short_strike_distance_sigma is not None and short_z is not None and short_z > max_short_strike_distance_sigma:
                hard_limits.append(f"Trade {key} exceeds max_short_strike_distance_sigma.")

            oi = self._safe_int(trade.get("open_interest"))
            if min_open_interest is not None and oi is not None and oi < min_open_interest:
                hard_limits.append(f"Trade {key} open interest below min_open_interest.")

            volume = self._safe_int(trade.get("volume"))
            if min_volume is not None and volume is not None and volume < min_volume:
                hard_limits.append(f"Trade {key} volume below min_volume.")

            spread_pct = self._safe_float(trade.get("bid_ask_spread_pct"))
            if max_bid_ask_spread_pct is not None and spread_pct is not None and spread_pct > max_bid_ask_spread_pct:
                hard_limits.append(f"Trade {key} bid/ask spread exceeds max_bid_ask_spread_pct.")

            pop = self._safe_float(trade.get("p_win_used"))
            if min_pop is not None and pop is not None and pop < min_pop:
                soft_gates.append(f"Trade {key} POP below min_pop.")

            ror = self._safe_float(trade.get("return_on_risk"))
            if min_return_on_risk is not None and ror is not None and ror < min_return_on_risk:
                soft_gates.append(f"Trade {key} return_on_risk below minimum.")

            ev = self._safe_float(trade.get("ev_per_share"))
            if min_ev_to_risk is not None and ev is not None and risk not in (None, 0):
                ev_to_risk = ev / risk
                if ev_to_risk < min_ev_to_risk:
                    soft_gates.append(f"Trade {key} EV/risk below minimum.")

            iv_rv = self._safe_float(trade.get("iv_rv_ratio"))
            strategy = str(trade.get("strategy") or "").lower()
            if iv_rv is not None:
                is_selling = "credit" in strategy or "covered" in strategy or "cash_secured" in strategy
                is_buying = "debit" in strategy or "long_" in strategy
                if is_selling and min_iv_rv_ratio_for_selling is not None and iv_rv < min_iv_rv_ratio_for_selling:
                    soft_gates.append(f"Trade {key} IV/RV below selling threshold.")
                if is_buying and max_iv_rv_ratio_for_buying is not None and iv_rv > max_iv_rv_ratio_for_buying:
                    soft_gates.append(f"Trade {key} IV/RV above buying threshold.")

        unknown_risk_count = len([t for t in trades if self._safe_float(t.get("estimated_risk")) is None])
        if unknown_risk_count > 0:
            hard_limits.append(f"{unknown_risk_count} trade(s) missing complete risk fields (under construction).")

        return {
            "hard_limits": hard_limits,
            "soft_gates": soft_gates,
        }

    async def build_snapshot(self, request: Any) -> dict[str, Any]:
        policy = self.get_policy()
        trades = await self._trades_from_active(request)
        source = "tradier"

        if not trades:
            trades = self._trades_from_report()
            source = "report" if trades else "none"

        known_risks = [self._safe_float(t.get("estimated_risk")) for t in trades]
        known_risks = [x for x in known_risks if x is not None]
        total_used = sum(known_risks) if known_risks else (0.0 if trades else None)

        by_symbol: dict[str, float] = {}
        for trade in trades:
            symbol = str(trade.get("symbol") or "").upper() or "UNKNOWN"
            risk = self._safe_float(trade.get("estimated_risk"))
            if risk is None:
                continue
            by_symbol[symbol] = by_symbol.get(symbol, 0.0) + risk

        risk_by_symbol = [
            {"symbol": sym, "risk": risk}
            for sym, risk in sorted(by_symbol.items(), key=lambda item: item[1], reverse=True)
        ]

        portfolio = self._safe_float(policy.get("portfolio_size"))
        max_total_pct = self._safe_float(policy.get("max_total_risk_pct"))
        total_budget = (portfolio * max_total_pct) if (portfolio is not None and max_total_pct is not None) else None

        risk_remaining = None
        if total_budget is not None and total_used is not None:
            risk_remaining = total_budget - total_used

        warning_groups = self._build_warning_groups(policy, trades, total_used, risk_by_symbol)

        return {
            "as_of": self._utc_now_iso(),
            "exposure_source": source,
            "policy": policy,
            "exposure": {
                "open_trades": len(trades),
                "total_risk_used": total_used,
                "risk_remaining": risk_remaining,
                "risk_by_underlying": risk_by_symbol,
                "trades": trades,
                "warnings": warning_groups,
            },
        }
