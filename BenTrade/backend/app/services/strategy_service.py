from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from app.services.strategies.base import StrategyPlugin
from app.services.strategies.butterflies import ButterfliesStrategyPlugin
from app.services.strategies.calendars import CalendarsStrategyPlugin
from app.services.strategies.credit_spread import CreditSpreadStrategyPlugin
from app.services.strategies.debit_spreads import DebitSpreadsStrategyPlugin
from app.services.strategies.income import IncomeStrategyPlugin
from app.services.strategies.iron_condor import IronCondorStrategyPlugin
from app.services.validation_events import ValidationEventsService
from app.utils.computed_metrics import apply_metrics_contract
from app.utils.dates import dte_ceil
from app.utils.trade_key import canonicalize_strategy_id, canonicalize_trade_key, trade_key


class StrategyService:
    def __init__(
        self,
        base_data_service: Any,
        results_dir: Path,
        risk_policy_service: Any | None = None,
        signal_service: Any | None = None,
        regime_service: Any | None = None,
    ) -> None:
        self.base_data_service = base_data_service
        self.results_dir = results_dir
        self.risk_policy_service = risk_policy_service
        self.signal_service = signal_service
        self.regime_service = regime_service
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.validation_events = ValidationEventsService(results_dir=self.results_dir)
        self.data_workbench_records_path = self.results_dir / "data_workbench_records.jsonl"
        self._records_lock = RLock()

        self._plugins: dict[str, StrategyPlugin] = {}
        self.register(CreditSpreadStrategyPlugin())
        self.register(DebitSpreadsStrategyPlugin())
        self.register(IronCondorStrategyPlugin())
        self.register(ButterfliesStrategyPlugin())
        self.register(CalendarsStrategyPlugin())
        self.register(IncomeStrategyPlugin())

    def register(self, plugin: StrategyPlugin) -> None:
        key = str(getattr(plugin, "id", "")).strip().lower()
        if not key:
            raise ValueError("plugin.id is required")
        self._plugins[key] = plugin

    def get_plugin(self, strategy_id: str) -> StrategyPlugin:
        key = str(strategy_id or "").strip().lower()
        plugin = self._plugins.get(key)
        if plugin is None:
            raise KeyError(f"Unknown strategy: {strategy_id}")
        return plugin

    def list_strategy_ids(self) -> list[str]:
        return sorted(self._plugins.keys())

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _first_number(row: dict[str, Any], *keys: str) -> float | None:
        for key in keys:
            value = StrategyService._to_float(row.get(key))
            if value is not None:
                return value
        return None

    @staticmethod
    def _upsert_warning(row: dict[str, Any], code: str) -> None:
        warnings = row.get("validation_warnings") if isinstance(row.get("validation_warnings"), list) else []
        if code not in warnings:
            warnings.append(code)
        row["validation_warnings"] = warnings

    @staticmethod
    def _strategy_label(strategy_id: str) -> str:
        key = str(strategy_id or "").strip().lower()
        labels = {
            "put_credit_spread": "Put Credit Spread",
            "call_credit_spread": "Call Credit Spread",
            "put_debit": "Put Debit Spread",
            "call_debit": "Call Debit Spread",
            "iron_condor": "Iron Condor",
            "butterfly_debit": "Debit Butterfly",
            "calendar_spread": "Calendar Spread",
            "calendar_call_spread": "Call Calendar Spread",
            "calendar_put_spread": "Put Calendar Spread",
            "csp": "Cash Secured Put",
            "covered_call": "Covered Call",
            "income": "Income Strategy",
            "single": "Single Option",
            "long_call": "Long Call",
            "long_put": "Long Put",
        }
        return labels.get(key, key.replace("_", " ").title() or "Trade")

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _build_input_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
        data = snapshot if isinstance(snapshot, dict) else None
        if not data:
            return None

        symbol = str(data.get("symbol") or "").upper()
        expiration = str(data.get("expiration") or "").strip()
        dte = StrategyService._to_float(data.get("dte"))
        contracts = data.get("contracts") if isinstance(data.get("contracts"), list) else []
        closes = data.get("prices_history") if isinstance(data.get("prices_history"), list) else []

        return {
            "underlying_snapshot": {
                "symbol": symbol or None,
                "expiration": expiration or None,
                "underlying_price": StrategyService._to_float(data.get("underlying_price")),
                "vix": StrategyService._to_float(data.get("vix")),
                "dte": int(dte) if dte is not None and float(dte).is_integer() else dte,
            },
            "chain_metadata": {
                "contracts_count": len(contracts),
                "prices_history_points": len(closes),
                "has_prices_history": bool(closes),
            },
            "pricing_source": "tradier+fred+yahoo",
            "timestamp": StrategyService._utc_now_iso(),
        }

    @staticmethod
    def _snapshot_index(snapshots: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
        index: dict[tuple[str, str], dict[str, Any]] = {}
        for snap in snapshots:
            if not isinstance(snap, dict):
                continue
            symbol = str(snap.get("symbol") or "").upper().strip()
            expiration = str(snap.get("expiration") or "").strip()
            if not symbol or not expiration:
                continue
            index[(symbol, expiration)] = snap
        return index

    @staticmethod
    def _trade_side_label(trade: dict[str, Any]) -> str | None:
        spread = str(trade.get("strategy_id") or trade.get("spread_type") or trade.get("strategy") or "").strip().lower()
        if not spread:
            return None
        if spread in {"put_credit_spread", "put_debit", "calendar_put_spread", "csp", "long_put"}:
            return "put"
        if spread in {"call_credit_spread", "call_debit", "calendar_call_spread", "covered_call", "long_call"}:
            return "call"
        if spread in {"iron_condor", "butterfly_debit", "calendar_spread", "income"}:
            return "multi"
        if "put" in spread:
            return "put"
        if "call" in spread:
            return "call"
        return None

    @staticmethod
    def _trade_width(trade: dict[str, Any]) -> float | None:
        width = StrategyService._to_float(trade.get("width"))
        if width is not None:
            return abs(width)

        short_strike = StrategyService._to_float(trade.get("short_strike"))
        long_strike = StrategyService._to_float(trade.get("long_strike"))
        if short_strike is not None and long_strike is not None:
            return abs(short_strike - long_strike)

        put_short = StrategyService._to_float(trade.get("put_short_strike"))
        put_long = StrategyService._to_float(trade.get("put_long_strike"))
        call_short = StrategyService._to_float(trade.get("call_short_strike"))
        call_long = StrategyService._to_float(trade.get("call_long_strike"))
        values = []
        if put_short is not None and put_long is not None:
            values.append(abs(put_short - put_long))
        if call_short is not None and call_long is not None:
            values.append(abs(call_short - call_long))
        if values:
            return max(values)

        return None

    def _build_minimal_snapshot_from_trade(self, trade: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(trade, dict):
            return None

        symbol = str(trade.get("underlying") or trade.get("underlying_symbol") or trade.get("symbol") or "").upper().strip()
        expiration = str(trade.get("expiration") or "").strip()
        if not symbol or not expiration:
            return None

        dte = self._to_float(trade.get("dte"))
        contracts_multiplier = self._to_float(trade.get("contractsMultiplier") or trade.get("contracts_multiplier"))
        side = self._trade_side_label(trade)
        width = self._trade_width(trade)

        return {
            "underlying_snapshot": {
                "symbol": symbol,
                "expiration": expiration,
                "underlying_price": self._to_float(trade.get("underlying_price") or trade.get("price")),
                "vix": self._to_float(trade.get("vix")),
                "dte": int(dte) if dte is not None and float(dte).is_integer() else dte,
            },
            "trade_context": {
                "side": side,
                "short_strike": self._to_float(trade.get("short_strike")),
                "long_strike": self._to_float(trade.get("long_strike")),
                "put_short_strike": self._to_float(trade.get("put_short_strike")),
                "put_long_strike": self._to_float(trade.get("put_long_strike")),
                "call_short_strike": self._to_float(trade.get("call_short_strike")),
                "call_long_strike": self._to_float(trade.get("call_long_strike")),
                "strike": self._to_float(trade.get("strike")),
                "center_strike": self._to_float(trade.get("center_strike")),
                "lower_strike": self._to_float(trade.get("lower_strike")),
                "upper_strike": self._to_float(trade.get("upper_strike")),
                "width": width,
                "net_credit": self._to_float(trade.get("net_credit")),
                "net_debit": self._to_float(trade.get("net_debit")),
                "contracts_multiplier": int(contracts_multiplier) if contracts_multiplier is not None and float(contracts_multiplier).is_integer() else contracts_multiplier,
            },
            "chain_metadata": {
                "contracts_count": None,
                "prices_history_points": None,
                "has_prices_history": False,
                "reconstructed": True,
            },
            "pricing_source": "reconstructed_from_trade_output",
            "timestamp": self._utc_now_iso(),
        }

    def _resolve_trade_input_snapshot(
        self,
        trade: dict[str, Any],
        snapshot_index: dict[tuple[str, str], dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, str | None]:
        if isinstance(trade.get("input_snapshot"), dict):
            return trade.get("input_snapshot"), "embedded"

        symbol = str(trade.get("underlying") or trade.get("underlying_symbol") or trade.get("symbol") or "").upper().strip()
        expiration = str(trade.get("expiration") or "").strip()
        if symbol and expiration:
            snap = snapshot_index.get((symbol, expiration))
            built = self._build_input_snapshot(snap)
            if built is not None:
                return built, "analysis_inputs"

        fallback = self._build_minimal_snapshot_from_trade(trade)
        if fallback is not None:
            return fallback, "reconstructed"

        return None, None

    def _attach_input_snapshots_to_trades(self, accepted: list[dict[str, Any]], snapshots: list[dict[str, Any]]) -> None:
        if not accepted:
            return
        snapshot_index = self._snapshot_index(snapshots)
        for trade in accepted:
            if not isinstance(trade, dict):
                continue
            snapshot, source = self._resolve_trade_input_snapshot(trade, snapshot_index)
            if snapshot is not None:
                trade["input_snapshot"] = snapshot
                trade["input_snapshot_source"] = source

    def _persist_data_workbench_records(
        self,
        *,
        report_id: str,
        strategy_id: str,
        accepted: list[dict[str, Any]],
        snapshots: list[dict[str, Any]],
    ) -> None:
        if not accepted:
            return

        snapshot_index = self._snapshot_index(snapshots)

        lines: list[str] = []
        for trade in accepted:
            if not isinstance(trade, dict):
                continue
            key = canonicalize_trade_key(trade.get("trade_key"))
            if not key:
                continue
            snapshot, snapshot_source = self._resolve_trade_input_snapshot(trade, snapshot_index)
            record = {
                "ts": self._utc_now_iso(),
                "record_type": "data_workbench_trade_record_v1",
                "report_id": report_id,
                "trade_key": key,
                "strategy_id": str(trade.get("strategy_id") or trade.get("spread_type") or strategy_id or "").strip().lower(),
                "input_snapshot": snapshot,
                "input_snapshot_source": snapshot_source,
                "trade_output": trade,
                "validation_warnings": list(trade.get("validation_warnings") or []) if isinstance(trade.get("validation_warnings"), list) else [],
            }
            lines.append(json.dumps(record, ensure_ascii=False))

        if not lines:
            return

        self.data_workbench_records_path.parent.mkdir(parents=True, exist_ok=True)
        with self._records_lock:
            with open(self.data_workbench_records_path, "a", encoding="utf-8") as handle:
                for line in lines:
                    handle.write(line + "\n")

    def _apply_request_defaults(self, strategy_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        req = dict(payload or {})

        if strategy_id == "credit_spread":
            req.setdefault("dte_min", 7)
            req.setdefault("dte_max", 21)
            req.setdefault("expected_move_multiple", 1.0)
            req.setdefault("width_min", 1.0)
            req.setdefault("width_max", 5.0)
            req.setdefault("min_pop", 0.65)
            req.setdefault("min_ev_to_risk", 0.02)
            req.setdefault("max_bid_ask_spread_pct", 1.5)
            req.setdefault("min_open_interest", 500)
            req.setdefault("min_volume", 50)

        elif strategy_id == "debit_spreads":
            req.setdefault("dte_min", 14)
            req.setdefault("dte_max", 45)
            req.setdefault("width_min", 2.0)
            req.setdefault("width_max", 10.0)
            req.setdefault("max_debit_pct_width", 0.45)
            req.setdefault("max_iv_rv_ratio_for_buying", 1.0)
            req.setdefault("max_bid_ask_spread_pct", 1.5)
            req.setdefault("min_open_interest", 500)
            req.setdefault("min_volume", 50)

        elif strategy_id == "iron_condor":
            req.setdefault("dte_min", 21)
            req.setdefault("dte_max", 45)
            req.setdefault("distance_mode", "expected_move")
            req.setdefault("distance_target", 1.1)
            req.setdefault("min_sigma_distance", 1.1)
            req.setdefault("wing_width_put", 5.0)
            req.setdefault("wing_width_call", 5.0)
            req.setdefault("wing_width_max", 10.0)
            req.setdefault("min_ror", 0.12)
            req.setdefault("symmetry_target", 0.70)
            req.setdefault("min_open_interest", 500)
            req.setdefault("min_volume", 50)

        elif strategy_id == "butterflies":
            req.setdefault("dte_min", 7)
            req.setdefault("dte_max", 21)
            req.setdefault("center_mode", "spot")
            req.setdefault("width_min", 2.0)
            req.setdefault("width_max", 10.0)
            req.setdefault("min_cost_efficiency", 2.0)
            req.setdefault("min_open_interest", 500)
            req.setdefault("min_volume", 50)

        elif strategy_id == "calendars":
            req.setdefault("near_dte_min", 7)
            req.setdefault("near_dte_max", 14)
            req.setdefault("far_dte_min", 30)
            req.setdefault("far_dte_max", 60)
            req.setdefault("dte_min", 7)
            req.setdefault("dte_max", 60)
            req.setdefault("moneyness", "atm")
            req.setdefault("prefer_term_structure", 1)
            req.setdefault("max_bid_ask_spread_pct", 1.5)
            req.setdefault("min_open_interest", 500)
            req.setdefault("min_volume", 50)

        elif strategy_id == "income":
            req.setdefault("dte_min", 14)
            req.setdefault("dte_max", 45)
            req.setdefault("delta_min", 0.20)
            req.setdefault("delta_max", 0.30)
            req.setdefault("min_annualized_yield", 0.10)
            req.setdefault("min_open_interest", 500)
            req.setdefault("min_volume", 50)

        return req

    async def _resolve_expirations(self, symbol: str, request_payload: dict[str, Any], strategy_id: str) -> list[str]:
        requested_expiration = str(request_payload.get("expiration") or "").strip()
        if requested_expiration:
            return [requested_expiration]

        if strategy_id == "credit_spread":
            default_min, default_max = 7, 21
        elif strategy_id == "debit_spreads":
            default_min, default_max = 14, 45
        elif strategy_id == "iron_condor":
            default_min, default_max = 21, 45
        elif strategy_id == "butterflies":
            default_min, default_max = 7, 21
        elif strategy_id == "calendars":
            default_min, default_max = 7, 60
        elif strategy_id == "income":
            default_min, default_max = 14, 45
        else:
            default_min, default_max = 7, 45
        dte_min = int(request_payload.get("dte_min") or default_min)
        dte_max = int(request_payload.get("dte_max") or default_max)
        if dte_max < dte_min:
            dte_min, dte_max = dte_max, dte_min

        expirations = await self.base_data_service.tradier_client.get_expirations(symbol)
        filtered: list[tuple[int, str]] = []
        for exp in expirations:
            text = str(exp).strip()
            if not text:
                continue
            try:
                dte = dte_ceil(text)
            except Exception:
                continue
            if dte_min <= dte <= dte_max:
                filtered.append((dte, text))

        filtered.sort(key=lambda row: row[0])
        default_max_exp = 12 if strategy_id == "calendars" else (8 if strategy_id == "income" else 4)
        max_exp = int(request_payload.get("max_expirations_per_symbol") or default_max_exp)
        values = [exp for _, exp in filtered[: max(1, max_exp)]]
        if values:
            return values

        clean = [str(x) for x in expirations if str(x).strip()]
        if not clean:
            raise ValueError(f"No expirations available for {symbol}")
        return [clean[0]]

    def _normalize_trade(self, strategy_id: str, expiration: str, trade: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(trade or {})
        normalized["strategyId"] = strategy_id

        symbol = str(
            normalized.get("underlying")
            or normalized.get("underlying_symbol")
            or normalized.get("symbol")
            or ""
        ).upper()
        if symbol:
            normalized["underlying"] = symbol
            normalized["underlying_symbol"] = symbol
            normalized["symbol"] = symbol

        raw_spread_type = (
            normalized.get("spread_type")
            or normalized.get("strategy")
            or strategy_id
        )
        spread_type, alias_mapped, provided_strategy = canonicalize_strategy_id(raw_spread_type)
        spread_type = spread_type or str(strategy_id)
        if alias_mapped:
            try:
                self.validation_events.append_event(
                    severity="warn",
                    code="TRADE_STRATEGY_ALIAS_MAPPED",
                    message="Scanner output strategy alias mapped to canonical strategy_id",
                    context={
                        "strategy_id": spread_type,
                        "provided_strategy": provided_strategy,
                    },
                )
            except Exception:
                pass
        normalized["spread_type"] = spread_type
        normalized["strategy"] = spread_type
        normalized["strategy_id"] = spread_type

        exp = str(normalized.get("expiration") or expiration or "").strip() or "NA"
        normalized["expiration"] = exp

        dte_value = normalized.get("dte")
        if dte_value in (None, "") and exp not in ("", "NA"):
            try:
                dte_value = dte_ceil(exp)
            except Exception:
                dte_value = None
        normalized["dte"] = dte_value

        def _derive_key_strikes(row: dict[str, Any]) -> tuple[Any, Any]:
            short = row.get("short_strike")
            long = row.get("long_strike")
            if short not in (None, "") or long not in (None, ""):
                return short, long

            if spread_type == "iron_condor":
                return (
                    f"P{row.get('put_short_strike') or 'NA'}|C{row.get('call_short_strike') or 'NA'}",
                    f"P{row.get('put_long_strike') or 'NA'}|C{row.get('call_long_strike') or 'NA'}",
                )

            if spread_type == "butterfly_debit":
                center = row.get("center_strike") or row.get("short_strike") or "NA"
                lower = row.get("lower_strike") or "NA"
                upper = row.get("upper_strike") or "NA"
                return center, f"L{lower}|U{upper}"

            if spread_type in {"csp", "covered_call", "single", "long_call", "long_put"}:
                strike = row.get("strike") or row.get("short_strike") or "NA"
                return strike, "NA"

            return short, long

        key_short_strike, key_long_strike = _derive_key_strikes(normalized)
        if normalized.get("short_strike") in (None, ""):
            normalized["short_strike"] = key_short_strike
        if normalized.get("long_strike") in (None, ""):
            normalized["long_strike"] = key_long_strike

        provided_key = str(normalized.get("trade_key") or "").strip()
        generated_key = trade_key(
            underlying=symbol,
            expiration=exp,
            spread_type=spread_type,
            short_strike=key_short_strike,
            long_strike=key_long_strike,
            dte=dte_value,
        )
        tkey = canonicalize_trade_key(provided_key) if provided_key else generated_key
        if provided_key and tkey != provided_key:
            try:
                self.validation_events.append_event(
                    severity="warn",
                    code="TRADE_KEY_NON_CANONICAL",
                    message="Scanner trade_key was rewritten to canonical format",
                    context={
                        "strategy_id": spread_type,
                        "trade_key": tkey,
                        "provided_trade_key": provided_key,
                    },
                )
            except Exception:
                pass
        normalized["trade_key"] = tkey
        if "_trade_key" in normalized:
            normalized.pop("_trade_key", None)

        if normalized.get("composite_score") is None and normalized.get("rank_score") is not None:
            normalized["composite_score"] = normalized.get("rank_score")

        multiplier = self._to_float(normalized.get("contractsMultiplier") or normalized.get("contracts_multiplier")) or 100.0

        expected_value_contract = self._first_number(
            normalized,
            "ev_per_contract",
            "expected_value",
            "ev",
        )
        if expected_value_contract is None:
            ev_share = self._first_number(normalized, "ev_per_share")
            if ev_share is not None:
                expected_value_contract = ev_share * multiplier

        max_profit_contract = self._first_number(normalized, "max_profit_per_contract")
        if max_profit_contract is None:
            mp_share = self._first_number(normalized, "max_profit_per_share")
            if mp_share is not None:
                max_profit_contract = mp_share * multiplier
            else:
                max_profit_contract = self._first_number(normalized, "max_profit")

        max_loss_contract = self._first_number(normalized, "max_loss_per_contract")
        if max_loss_contract is None:
            ml_share = self._first_number(normalized, "max_loss_per_share")
            if ml_share is not None:
                max_loss_contract = ml_share * multiplier
            else:
                max_loss_contract = self._first_number(normalized, "max_loss")

        computed = {
            "max_profit": max_profit_contract,
            "max_loss": max_loss_contract,
            "pop": self._first_number(normalized, "p_win_used", "pop_delta_approx", "pop_approx", "probability_of_touch_center", "implied_prob_profit", "pop"),
            "return_on_risk": self._first_number(normalized, "return_on_risk", "ror"),
            "expected_value": expected_value_contract,
            "kelly_fraction": self._first_number(normalized, "kelly_fraction"),
            "iv_rank": self._first_number(normalized, "iv_rank"),
            "short_strike_z": self._first_number(normalized, "short_strike_z"),
            "bid_ask_pct": self._first_number(normalized, "bid_ask_spread_pct"),
            "strike_dist_pct": self._first_number(normalized, "strike_distance_pct", "strike_distance_vs_expected_move", "expected_move_ratio"),
            "rsi14": self._first_number(normalized, "rsi14", "rsi_14"),
            "rv_20d": self._first_number(normalized, "realized_vol_20d", "rv_20d"),
            "open_interest": self._first_number(normalized, "open_interest"),
            "volume": self._first_number(normalized, "volume"),
        }
        details = {
            "break_even": self._first_number(normalized, "break_even", "break_even_low"),
            "dte": self._first_number(normalized, "dte"),
            "expected_move": self._first_number(normalized, "expected_move", "expected_move_near"),
            "iv_rv_ratio": self._first_number(normalized, "iv_rv_ratio"),
            "trade_quality_score": self._first_number(normalized, "trade_quality_score"),
            "market_regime": str(normalized.get("market_regime") or normalized.get("regime") or "").strip() or None,
        }

        dte_front = self._first_number(normalized, "dte_near")
        dte_back = self._first_number(normalized, "dte_far")
        pills = {
            "strategy_label": self._strategy_label(str(normalized.get("strategy_id") or normalized.get("spread_type") or "")),
            "dte": details["dte"],
            "pop": computed["pop"],
            "oi": computed["open_interest"],
            "vol": computed["volume"],
            "regime_label": details["market_regime"],
        }
        if dte_front is not None and dte_back is not None:
            pills["dte_front"] = dte_front
            pills["dte_back"] = dte_back
            pills["dte_label"] = f"DTE {int(dte_front) if float(dte_front).is_integer() else dte_front}/{int(dte_back) if float(dte_back).is_integer() else dte_back}"

        normalized["computed"] = computed
        normalized["details"] = details
        normalized["pills"] = pills
        normalized = apply_metrics_contract(normalized)

        if normalized.get("p_win_used") is None and computed["pop"] is not None:
            normalized["p_win_used"] = computed["pop"]
        if normalized.get("return_on_risk") is None and computed["return_on_risk"] is not None:
            normalized["return_on_risk"] = computed["return_on_risk"]
        if normalized.get("expected_value") is None and computed["expected_value"] is not None:
            normalized["expected_value"] = computed["expected_value"]
        if normalized.get("ev_per_contract") is None and computed["expected_value"] is not None:
            normalized["ev_per_contract"] = computed["expected_value"]
        if normalized.get("bid_ask_spread_pct") is None and computed["bid_ask_pct"] is not None:
            normalized["bid_ask_spread_pct"] = computed["bid_ask_pct"]
        if normalized.get("strike_distance_pct") is None and computed["strike_dist_pct"] is not None:
            normalized["strike_distance_pct"] = computed["strike_dist_pct"]
        if normalized.get("rsi14") is None and computed["rsi14"] is not None:
            normalized["rsi14"] = computed["rsi14"]
        if normalized.get("realized_vol_20d") is None and computed["rv_20d"] is not None:
            normalized["realized_vol_20d"] = computed["rv_20d"]
        if normalized.get("iv_rv_ratio") is None and details["iv_rv_ratio"] is not None:
            normalized["iv_rv_ratio"] = details["iv_rv_ratio"]
        if normalized.get("trade_quality_score") is None and details["trade_quality_score"] is not None:
            normalized["trade_quality_score"] = details["trade_quality_score"]
        if not str(normalized.get("market_regime") or "").strip() and details["market_regime"] is not None:
            normalized["market_regime"] = details["market_regime"]

        if computed["pop"] is None:
            self._upsert_warning(normalized, "POP_NOT_IMPLEMENTED_FOR_STRATEGY")
        if pills["regime_label"] is None:
            self._upsert_warning(normalized, "REGIME_UNAVAILABLE")
        if computed["max_profit"] is None:
            self._upsert_warning(normalized, "MAX_PROFIT_UNAVAILABLE")
        if computed["max_loss"] is None:
            self._upsert_warning(normalized, "MAX_LOSS_UNAVAILABLE")
        if computed["expected_value"] is None:
            self._upsert_warning(normalized, "EXPECTED_VALUE_UNAVAILABLE")
        if computed["return_on_risk"] is None:
            self._upsert_warning(normalized, "RETURN_ON_RISK_UNAVAILABLE")

        return normalized

    def _build_report_stats(self, trades: list[dict[str, Any]]) -> dict[str, Any]:
        scores = [self._to_float(t.get("rank_score") or t.get("composite_score")) for t in trades]
        scores = [s for s in scores if s is not None]
        pops = [self._to_float(t.get("p_win_used") or t.get("pop_delta_approx")) for t in trades]
        pops = [p for p in pops if p is not None]
        rors = [self._to_float(t.get("return_on_risk")) for t in trades]
        rors = [r for r in rors if r is not None]

        def _avg(values: list[float]) -> float | None:
            return (sum(values) / len(values)) if values else None

        return {
            "total_candidates": len(trades),
            "accepted_trades": len(trades),
            "rejected_trades": 0,
            "acceptance_rate": 1.0 if trades else 0.0,
            "best_trade_score": max(scores) if scores else None,
            "worst_accepted_score": min(scores) if scores else None,
            "avg_trade_score": _avg(scores),
            "avg_probability": _avg(pops),
            "avg_return_on_risk": _avg(rors),
            "best_underlying": str((trades[0].get("underlying") if trades else "") or "").upper() or None,
        }

    @staticmethod
    def _normalize_rank_100(value: Any) -> float:
        try:
            n = float(value)
        except (TypeError, ValueError):
            return 0.0
        if n <= 1.0:
            n *= 100.0
        return max(0.0, min(100.0, n))

    @staticmethod
    def _regime_fit_from_playbook(strategy: str, regime_payload: dict[str, Any] | None) -> float:
        payload = regime_payload if isinstance(regime_payload, dict) else {}
        playbook = payload.get("suggested_playbook") if isinstance(payload.get("suggested_playbook"), dict) else {}
        primary = {str(x).lower() for x in (playbook.get("primary") or [])}
        avoid = {str(x).lower() for x in (playbook.get("avoid") or [])}
        key = str(strategy or "").lower()
        if key in primary:
            return 100.0
        if key in avoid:
            return 10.0

        label = str(payload.get("regime_label") or "NEUTRAL").upper()
        if label == "RISK_OFF" and ("credit_put" in key or "short_put" in key):
            return 15.0
        if label == "RISK_ON" and ("credit_put" in key or "covered_call" in key):
            return 90.0
        return 55.0

    async def _apply_context_scores(self, accepted: list[dict[str, Any]]) -> None:
        if not accepted:
            return

        regime_payload = None
        if self.regime_service and hasattr(self.regime_service, "get_regime"):
            try:
                regime_payload = await self.regime_service.get_regime()
            except Exception:
                regime_payload = None

        signal_scores: dict[str, float] = {}
        if self.signal_service and hasattr(self.signal_service, "get_symbol_signals"):
            symbols = sorted({str(t.get("underlying") or t.get("symbol") or "").upper() for t in accepted if str(t.get("underlying") or t.get("symbol") or "").strip()})
            for symbol in symbols:
                try:
                    payload = await self.signal_service.get_symbol_signals(symbol=symbol, range_key="6mo")
                    score = self._normalize_rank_100((payload.get("composite") or {}).get("score"))
                    signal_scores[symbol] = score
                except Exception:
                    signal_scores[symbol] = 50.0

        for trade in accepted:
            strategy = str(trade.get("spread_type") or trade.get("strategy") or "")
            symbol = str(trade.get("underlying") or trade.get("symbol") or "").upper()
            structure_score = self._normalize_rank_100(trade.get("rank_score") or trade.get("composite_score"))
            underlying_score = signal_scores.get(symbol, 50.0)
            regime_fit = self._regime_fit_from_playbook(strategy, regime_payload)

            blended = (0.60 * structure_score) + (0.20 * underlying_score) + (0.20 * regime_fit)
            trade["rank_score_raw"] = structure_score
            trade["rank_score"] = round(blended, 3)
            trade["rank_components"] = {
                "structure": round(structure_score, 3),
                "underlying_composite": round(underlying_score, 3),
                "regime_fit": round(regime_fit, 3),
                "blended": round(blended, 3),
            }

    async def _emit_progress(self, callback: Any, stage: str, message: str, details: dict[str, Any] | None = None) -> None:
        if callback is None:
            return
        payload = {
            "stage": stage,
            "message": message,
        }
        if isinstance(details, dict) and details:
            payload.update(details)
        try:
            result = callback(payload)
            if inspect.isawaitable(result):
                await result
        except Exception:
            pass

    def _build_report_blob(
        self,
        strategy_id: str,
        payload: dict[str, Any],
        symbol_list: list[str],
        primary: dict[str, Any] | None,
        candidates: list[dict[str, Any]],
        enriched: list[dict[str, Any]],
        accepted: list[dict[str, Any]],
        notes: list[str],
    ) -> dict[str, Any]:
        source_health = self.base_data_service.get_source_health_snapshot()
        report_stats = self._build_report_stats(accepted)
        return {
            "strategyId": strategy_id,
            "generated_at": self._utc_now_iso(),
            "symbol": (primary or {}).get("symbol") if isinstance(primary, dict) else (symbol_list[0] if symbol_list else None),
            "expiration": (primary or {}).get("expiration") if isinstance(primary, dict) else None,
            "symbols": symbol_list,
            "report_stats": report_stats,
            "source_health": source_health,
            "trades": accepted,
            "diagnostics": {
                "candidate_count": len(candidates),
                "enriched_count": len(enriched),
                "accepted_count": len(accepted),
                "notes": list(dict.fromkeys([str(n) for n in notes if str(n).strip()])),
            },
        }

    async def generate(self, strategy_id: str, request_payload: dict[str, Any] | None = None, progress_callback: Any | None = None) -> dict[str, Any]:
        plugin = self.get_plugin(strategy_id)
        payload = self._apply_request_defaults(strategy_id, request_payload or {})
        notes: list[str] = []

        await self._emit_progress(progress_callback, "prepare", f"Preparing {strategy_id} inputs")

        symbols = payload.get("symbols") if isinstance(payload.get("symbols"), list) else None
        symbol_list = [str(x).upper() for x in (symbols or []) if str(x).strip()] or [str(payload.get("symbol") or "SPY").upper()]

        snapshots: list[dict[str, Any]] = []
        for symbol in symbol_list:
            try:
                expirations = await self._resolve_expirations(symbol, payload, strategy_id)
            except Exception as exc:
                notes.append(f"{symbol}: expirations unavailable ({exc})")
                continue

            for expiration in expirations:
                try:
                    snapshot_inputs = await self.base_data_service.get_analysis_inputs(symbol, expiration)
                except Exception as exc:
                    notes.append(f"{symbol} {expiration}: analysis inputs unavailable ({exc})")
                    continue

                contracts = snapshot_inputs.get("contracts") or []
                if not contracts:
                    notes.append(f"{symbol} {expiration}: no_chain")
                    continue

                snapshots.append({
                    **snapshot_inputs,
                    "symbol": symbol,
                    "expiration": expiration,
                    "dte": dte_ceil(expiration),
                })

        await self._emit_progress(progress_callback, "snapshots", "Snapshots collected", {"count": len(snapshots)})

        candidates: list[dict[str, Any]] = []
        enriched: list[dict[str, Any]] = []
        accepted: list[dict[str, Any]] = []

        if snapshots:
            primary = snapshots[0]
        else:
            primary = {
                "symbol": symbol_list[0] if symbol_list else None,
                "expiration": str(payload.get("expiration") or ""),
            }
            notes.append("No analysis snapshots available for requested symbols/expirations")

        policy = self.risk_policy_service.get_policy() if self.risk_policy_service else {}
        inputs = {
            **primary,
            "symbol": primary.get("symbol"),
            "expiration": primary.get("expiration"),
            "snapshots": snapshots,
            "symbols": symbol_list,
            "policy": policy,
        }
        inputs["request"] = payload

        if snapshots:
            await self._emit_progress(progress_callback, "build_candidates", "Building candidates")
            try:
                candidates = plugin.build_candidates(inputs)
            except Exception as exc:
                notes.append(f"build_candidates failed: {exc}")
                raise

            await self._emit_progress(progress_callback, "enrich", "Enriching candidates", {"count": len(candidates)})
            try:
                enriched = plugin.enrich(candidates, inputs)
            except Exception as exc:
                notes.append(f"enrich failed: {exc}")
                raise

            await self._emit_progress(progress_callback, "evaluate", "Evaluating and scoring candidates", {"count": len(enriched)})
            for row in enriched:
                try:
                    row = dict(row)
                    row["_policy"] = policy
                    row["_request"] = payload
                    ok, reasons = plugin.evaluate(row)
                    if not ok:
                        continue
                    rank_score, tie_breaks = plugin.score(row)
                    row.pop("_policy", None)
                    row.pop("_request", None)
                    row["rank_score"] = rank_score
                    row["tie_breaks"] = tie_breaks
                    row["strategyId"] = strategy_id
                    row["selection_reasons"] = reasons
                    accepted.append(self._normalize_trade(strategy_id, str(row.get("expiration") or primary.get("expiration") or "NA"), row))
                except Exception as exc:
                    notes.append(f"candidate skipped: {exc}")
                    continue

        await self._apply_context_scores(accepted)

        deduped: dict[str, dict[str, Any]] = {}
        for trade in accepted:
            key = str(trade.get("trade_key") or "").strip()
            if not key:
                continue
            current = deduped.get(key)
            if current is None or float(trade.get("rank_score") or 0.0) > float(current.get("rank_score") or 0.0):
                deduped[key] = trade
        accepted = list(deduped.values())

        accepted.sort(
            key=lambda tr: (
                float(tr.get("rank_score") or 0.0),
                float((tr.get("tie_breaks") or {}).get("edge") or (tr.get("tie_breaks") or {}).get("ev_to_risk") or 0.0),
                float((tr.get("tie_breaks") or {}).get("pop") or (tr.get("tie_breaks") or {}).get("liquidity") or 0.0),
                float((tr.get("tie_breaks") or {}).get("liq") or (tr.get("tie_breaks") or {}).get("conviction") or 0.0),
            ),
            reverse=True,
        )

        self._attach_input_snapshots_to_trades(accepted=accepted, snapshots=snapshots)

        await self._emit_progress(progress_callback, "write_report", "Writing report", {"accepted_count": len(accepted)})

        ts_name = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"{strategy_id}_analysis_{ts_name}.json"
        path = self.results_dir / filename

        blob = self._build_report_blob(
            strategy_id=strategy_id,
            payload=payload,
            symbol_list=symbol_list,
            primary=primary,
            candidates=candidates,
            enriched=enriched,
            accepted=accepted,
            notes=notes,
        )

        path.write_text(json.dumps(blob, indent=2), encoding="utf-8")
        try:
            self._persist_data_workbench_records(
                report_id=filename,
                strategy_id=strategy_id,
                accepted=accepted,
                snapshots=snapshots,
            )
        except Exception:
            pass
        await self._emit_progress(progress_callback, "completed", "Report generation completed", {"filename": filename})
        return {"filename": filename, **blob}

    def list_reports(self, strategy_id: str) -> list[str]:
        prefix = f"{strategy_id}_analysis_"
        files = [p.name for p in self.results_dir.glob(f"{strategy_id}_analysis_*.json") if p.name.startswith(prefix)]
        files.sort(reverse=True)
        return files

    def get_report(self, strategy_id: str, filename: str) -> dict[str, Any]:
        if not filename.startswith(f"{strategy_id}_analysis_") or not filename.endswith(".json"):
            raise ValueError("Invalid report filename")

        path = self.results_dir / filename
        if not path.exists():
            raise FileNotFoundError(filename)

        payload = json.loads(path.read_text(encoding="utf-8"))
        trades = payload.get("trades") if isinstance(payload, dict) else []
        if not isinstance(trades, list):
            trades = []

        normalized_trades = [self._normalize_trade(strategy_id, str(payload.get("expiration") or "NA"), t) for t in trades if isinstance(t, dict)]
        payload["trades"] = normalized_trades
        payload["report_stats"] = payload.get("report_stats") if isinstance(payload.get("report_stats"), dict) else self._build_report_stats(normalized_trades)
        payload["strategyId"] = strategy_id
        return payload
