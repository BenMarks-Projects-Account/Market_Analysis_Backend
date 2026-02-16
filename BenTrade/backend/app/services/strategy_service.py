from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.strategies.base import StrategyPlugin
from app.services.strategies.butterflies import ButterfliesStrategyPlugin
from app.services.strategies.calendars import CalendarsStrategyPlugin
from app.services.strategies.credit_spread import CreditSpreadStrategyPlugin
from app.services.strategies.debit_spreads import DebitSpreadsStrategyPlugin
from app.services.strategies.income import IncomeStrategyPlugin
from app.services.strategies.iron_condor import IronCondorStrategyPlugin
from app.utils.dates import dte_ceil
from app.utils.trade_key import trade_key


class StrategyService:
    def __init__(self, base_data_service: Any, results_dir: Path, risk_policy_service: Any | None = None) -> None:
        self.base_data_service = base_data_service
        self.results_dir = results_dir
        self.risk_policy_service = risk_policy_service
        self.results_dir.mkdir(parents=True, exist_ok=True)

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
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

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

        spread_type = str(
            normalized.get("spread_type")
            or normalized.get("strategy")
            or strategy_id
        )
        normalized["spread_type"] = spread_type
        normalized["strategy"] = spread_type

        exp = str(normalized.get("expiration") or expiration or "").strip() or "NA"
        normalized["expiration"] = exp

        tkey = str(normalized.get("trade_key") or "").strip()
        if not tkey:
            tkey = trade_key(
                underlying=symbol,
                expiration=exp,
                spread_type=spread_type,
                short_strike=normalized.get("short_strike"),
                long_strike=normalized.get("long_strike"),
                dte=normalized.get("dte"),
            )
        normalized["trade_key"] = tkey
        normalized["_trade_key"] = tkey

        if normalized.get("composite_score") is None and normalized.get("rank_score") is not None:
            normalized["composite_score"] = normalized.get("rank_score")

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

    async def generate(self, strategy_id: str, request_payload: dict[str, Any] | None = None) -> dict[str, Any]:
        plugin = self.get_plugin(strategy_id)
        payload = self._apply_request_defaults(strategy_id, request_payload or {})

        symbols = payload.get("symbols") if isinstance(payload.get("symbols"), list) else None
        symbol_list = [str(x).upper() for x in (symbols or []) if str(x).strip()] or [str(payload.get("symbol") or "SPY").upper()]

        snapshots: list[dict[str, Any]] = []
        for symbol in symbol_list:
            expirations = await self._resolve_expirations(symbol, payload, strategy_id)
            for expiration in expirations:
                try:
                    snapshot_inputs = await self.base_data_service.get_analysis_inputs(symbol, expiration)
                except Exception:
                    continue
                snapshots.append({
                    **snapshot_inputs,
                    "symbol": symbol,
                    "expiration": expiration,
                    "dte": dte_ceil(expiration),
                })

        if not snapshots:
            raise ValueError("No analysis snapshots available for requested symbols/expirations")

        primary = snapshots[0]
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

        candidates = plugin.build_candidates(inputs)
        enriched = plugin.enrich(candidates, inputs)

        accepted: list[dict[str, Any]] = []
        for row in enriched:
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

        accepted.sort(
            key=lambda tr: (
                float(tr.get("rank_score") or 0.0),
                float((tr.get("tie_breaks") or {}).get("edge") or (tr.get("tie_breaks") or {}).get("ev_to_risk") or 0.0),
                float((tr.get("tie_breaks") or {}).get("pop") or (tr.get("tie_breaks") or {}).get("liquidity") or 0.0),
                float((tr.get("tie_breaks") or {}).get("liq") or (tr.get("tie_breaks") or {}).get("conviction") or 0.0),
            ),
            reverse=True,
        )

        ts_name = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"{strategy_id}_analysis_{ts_name}.json"
        path = self.results_dir / filename

        source_health = self.base_data_service.get_source_health_snapshot()
        report_stats = self._build_report_stats(accepted)

        blob = {
            "strategyId": strategy_id,
            "generated_at": self._utc_now_iso(),
            "symbol": primary.get("symbol"),
            "expiration": primary.get("expiration"),
            "symbols": symbol_list,
            "report_stats": report_stats,
            "source_health": source_health,
            "trades": accepted,
            "diagnostics": {
                "candidate_count": len(candidates),
                "enriched_count": len(enriched),
                "accepted_count": len(accepted),
            },
        }

        path.write_text(json.dumps(blob, indent=2), encoding="utf-8")
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
