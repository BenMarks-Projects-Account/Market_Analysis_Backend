from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from app.services.regime_service import RegimeService
from app.services.stock_analysis_service import StockAnalysisService
from app.services.strategy_service import StrategyService


class RecommendationService:
    def __init__(
        self,
        strategy_service: StrategyService,
        stock_analysis_service: StockAnalysisService,
        regime_service: RegimeService,
    ) -> None:
        self.strategy_service = strategy_service
        self.stock_analysis_service = stock_analysis_service
        self.regime_service = regime_service
        self.logger = logging.getLogger(__name__)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        if value in (None, "", "."):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_score(value: Any) -> float:
        n = RecommendationService._safe_float(value)
        if n is None:
            return 0.0
        if n <= 1.0:
            n *= 100.0
        return max(0.0, min(100.0, n))

    @staticmethod
    def _strategy_name(row: dict[str, Any]) -> str:
        return str(
            row.get("spread_type")
            or row.get("strategy")
            or row.get("type")
            or row.get("recommended_strategy")
            or row.get("strategyId")
            or "stock"
        ).strip().lower()

    @staticmethod
    def _open_route_for_strategy(strategy: str, pick_type: str) -> str:
        key = str(strategy or "").lower()
        if pick_type == "stock":
            return "#/stock-analysis"
        if "credit_put" in key:
            return "#/strategy-credit-put"
        if "credit_call" in key:
            return "#/strategy-credit-call"
        if "credit_spread" in key:
            return "#/credit-spread"
        if "debit" in key:
            return "#/debit-spreads"
        if "iron_condor" in key:
            return "#/iron-condor"
        if "butter" in key:
            return "#/butterflies"
        if "calendar" in key:
            return "#/calendar"
        if "income" in key or "covered_call" in key:
            return "#/income"
        return "#/credit-spread"

    def _liquidity_score(self, row: dict[str, Any], pick_type: str) -> float:
        if pick_type == "stock":
            symbol = str(row.get("symbol") or "").upper()
            if symbol in {"SPY", "QQQ", "IWM", "DIA", "AAPL", "MSFT", "NVDA"}:
                return 90.0
            return 70.0

        oi = self._safe_float(row.get("open_interest"))
        vol = self._safe_float(row.get("volume"))
        bid = self._safe_float(row.get("bid"))
        ask = self._safe_float(row.get("ask"))

        if oi is None and vol is None:
            return 60.0

        oi_part = min(60.0, ((oi or 0.0) / 1000.0) * 60.0)
        vol_part = min(30.0, ((vol or 0.0) / 500.0) * 30.0)
        spread_part = 10.0
        if bid not in (None, 0) and ask is not None and ask >= bid:
            spread_pct = (ask - bid) / max(abs(bid), 0.01)
            spread_part = 10.0 if spread_pct <= 0.1 else (5.0 if spread_pct <= 0.25 else 1.5)

        return max(0.0, min(100.0, oi_part + vol_part + spread_part))

    def _regime_fit_score(self, strategy: str, regime: dict[str, Any]) -> tuple[float, list[str]]:
        label = str(regime.get("regime_label") or "NEUTRAL").upper()
        playbook = regime.get("suggested_playbook") if isinstance(regime.get("suggested_playbook"), dict) else {}
        primary = {str(x).lower() for x in (playbook.get("primary") or [])}
        avoid = {str(x).lower() for x in (playbook.get("avoid") or [])}
        strategy_key = str(strategy or "").lower()

        reasons: list[str] = []
        score = 50.0

        if strategy_key in primary:
            score = 100.0
            reasons.append("Matches current regime primary playbook")
        elif strategy_key in avoid:
            score = 10.0
            reasons.append("Strategy appears in current regime avoid list")

        if label == "RISK_OFF":
            if "credit_put" in strategy_key or "short_put" in strategy_key:
                score = min(score, 12.0)
                reasons.append("Risk-Off penalty for short puts / short gamma")
            if "debit_put" in strategy_key or "hedge" in strategy_key:
                score = max(score, 90.0)
                reasons.append("Risk-Off boost for protective bearish structures")
        elif label == "RISK_ON":
            if "credit_put" in strategy_key or "covered_call" in strategy_key:
                score = max(score, 92.0)
                reasons.append("Risk-On boost for bullish premium strategies")

        return max(0.0, min(100.0, score)), reasons

    def _collect_strategy_candidates(self) -> tuple[list[dict[str, Any]], list[str]]:
        out: list[dict[str, Any]] = []
        notes: list[str] = []

        try:
            strategy_ids = self.strategy_service.list_strategy_ids()
        except Exception as exc:
            self.logger.exception("recommendations.list_strategy_ids_failed")
            notes.append(f"strategy list unavailable: {exc}")
            return out, notes

        for strategy_id in strategy_ids:
            try:
                reports = self.strategy_service.list_reports(strategy_id)
                if not reports:
                    notes.append(f"{strategy_id}: no reports available")
                    continue
                latest = reports[0]
                payload = self.strategy_service.get_report(strategy_id, latest)
                trades = payload.get("trades") if isinstance(payload, dict) else []
                if not isinstance(trades, list):
                    trades = []
                for trade in trades:
                    if not isinstance(trade, dict):
                        continue
                    symbol = str(trade.get("underlying") or trade.get("symbol") or "").upper()
                    if not symbol:
                        continue
                    rank = self._normalize_score(trade.get("rank_score") or trade.get("composite_score"))
                    out.append({
                        "id": str(trade.get("trade_key") or f"{symbol}|{strategy_id}"),
                        "symbol": symbol,
                        "type": "options",
                        "strategy": self._strategy_name(trade),
                        "rank_score": rank,
                        "source": f"{strategy_id}:{latest}",
                        "raw": trade,
                    })
            except Exception as exc:
                self.logger.exception("recommendations.strategy_report_unavailable strategy_id=%s", strategy_id)
                notes.append(f"{strategy_id} latest report unavailable: {exc}")

        return out, notes

    async def _collect_stock_scanner_candidates(self) -> tuple[list[dict[str, Any]], list[str]]:
        out: list[dict[str, Any]] = []
        notes: list[str] = []

        try:
            payload = await self.stock_analysis_service.stock_scanner(max_candidates=15)
            candidates = payload.get("candidates") if isinstance(payload, dict) else []
            if not isinstance(candidates, list):
                candidates = []
            for row in candidates:
                if not isinstance(row, dict):
                    continue
                symbol = str(row.get("symbol") or "").upper()
                if not symbol:
                    continue
                rank = self._normalize_score(row.get("composite_score") or row.get("scanner_score"))
                out.append({
                    "id": str(row.get("idea_key") or f"{symbol}|stock_scanner"),
                    "symbol": symbol,
                    "type": "stock",
                    "strategy": self._strategy_name(row),
                    "rank_score": rank,
                    "source": "stock_scanner",
                    "raw": row,
                })
        except Exception as exc:
            self.logger.exception("recommendations.stock_scanner_unavailable")
            notes.append(f"stock scanner unavailable: {exc}")

        return out, notes

    def _derive_ror(self, raw: dict[str, Any]) -> float | None:
        computed = raw.get("computed") if isinstance(raw.get("computed"), dict) else {}
        direct = self._safe_float(computed.get("return_on_risk") or raw.get("return_on_risk") or raw.get("ror"))
        if direct is not None:
            return direct

        max_profit = self._safe_float(computed.get("max_profit") or raw.get("max_profit_per_contract") or raw.get("max_profit_per_share") or raw.get("max_profit"))
        max_loss = self._safe_float(computed.get("max_loss") or raw.get("max_loss_per_contract") or raw.get("max_loss_per_share") or raw.get("max_loss"))
        if max_profit is not None and max_loss not in (None, 0):
            return max_profit / max_loss
        return None

    def _extract_model(self, raw: dict[str, Any]) -> dict[str, Any]:
        model_row = raw.get("model_evaluation") if isinstance(raw.get("model_evaluation"), dict) else None
        if not model_row:
            return {
                "status": "not_run",
                "recommendation": "Not run",
                "confidence": None,
                "summary": "",
            }

        return {
            "status": "available",
            "recommendation": str(model_row.get("recommendation") or "UNKNOWN").upper(),
            "confidence": self._safe_float(model_row.get("confidence")),
            "summary": str(model_row.get("summary") or "").strip(),
        }

    def _build_pick(self, candidate: dict[str, Any], regime: dict[str, Any]) -> dict[str, Any]:
        raw = candidate.get("raw") if isinstance(candidate.get("raw"), dict) else {}
        computed = raw.get("computed") if isinstance(raw.get("computed"), dict) else {}
        metrics = raw.get("metrics") if isinstance(raw.get("metrics"), dict) else {}
        signals = raw.get("signals") if isinstance(raw.get("signals"), dict) else {}
        strategy = str(candidate.get("strategy") or "stock")
        pick_type = str(candidate.get("type") or "options")
        symbol = str(candidate.get("symbol") or "N/A")

        regime_fit, fit_reasons = self._regime_fit_score(strategy, regime)
        liquidity_score = self._liquidity_score(raw, pick_type)
        rank_score = self._normalize_score(candidate.get("rank_score"))
        final_score = (0.6 * rank_score) + (0.2 * regime_fit) + (0.2 * liquidity_score)

        # Prefer per-contract EV from computed (set by _normalize_trade), then fall back
        ev = self._safe_float(computed.get("expected_value") or raw.get("ev_per_contract") or raw.get("expected_value") or raw.get("ev") or raw.get("edge"))
        ev_to_risk = self._safe_float(raw.get("ev_to_risk"))
        if ev is None:
            ev = ev_to_risk

        pop = self._safe_float(computed.get("pop") or raw.get("p_win_used"))
        if pop is None:
            pop = self._safe_float(raw.get("pop_delta_approx") or raw.get("probability_of_profit") or raw.get("pop"))

        ror = self._derive_ror(raw)
        iv_rv_ratio = self._safe_float(
            raw.get("iv_rv_ratio")
            or signals.get("iv_rv_ratio")
            or metrics.get("iv_rv_ratio")
        )
        model_payload = self._extract_model(raw)

        pick_notes: list[str] = []
        if pick_type == "stock":
            if ev is None:
                pick_notes.append("EV not computed for equities yet")
            if pop is None:
                pick_notes.append("POP not computed for equities yet")
            if ror is None:
                pick_notes.append("RoR not computed for equities yet")

        why: list[str] = []
        why.extend(fit_reasons)
        if rank_score >= 75:
            why.append("Strong intrinsic rank score")
        elif rank_score >= 60:
            why.append("Solid rank score")
        if liquidity_score >= 80:
            why.append("High liquidity profile")
        if model_payload.get("status") == "available":
            why.append(f"Model recommendation: {model_payload.get('recommendation')}")
        if not why:
            why.append("Selected via blended rank/regime/liquidity score")

        route = self._open_route_for_strategy(strategy, pick_type)
        send_payload = {
            "from": "top_pick_engine",
            "ts": self._now_iso(),
            "input": {
                "symbol": symbol,
                "strategy": strategy,
                "expiration": raw.get("expiration") or "NA",
                "short_strike": raw.get("short_strike"),
                "long_strike": raw.get("long_strike"),
                "contractsMultiplier": 100,
            },
            "trade_key": str(raw.get("trade_key") or f"{symbol}|NA|{strategy}|NA|NA|NA"),
            "note": f"Top pick ({candidate.get('source')})",
        }

        return {
            "id": str(candidate.get("id") or send_payload["trade_key"]),
            "symbol": symbol,
            "type": pick_type,
            "strategy": strategy,
            "rank_score": round(final_score, 2),
            "why": why[:4],
            "key_metrics": {
                "ev": ev,
                "pop": pop,
                "ev_to_risk": ev_to_risk,
                "ror": ror,
                "max_profit": self._safe_float(computed.get("max_profit") or raw.get("max_profit_per_contract") or raw.get("max_profit")),
                "max_loss": self._safe_float(computed.get("max_loss") or raw.get("max_loss_per_contract") or raw.get("max_loss")),
                "iv_rv_ratio": iv_rv_ratio,
                "price": self._safe_float(raw.get("price") or metrics.get("price")),
                "rsi14": self._safe_float(raw.get("rsi14") or signals.get("rsi_14") or metrics.get("rsi14")),
                "ema20": self._safe_float(raw.get("ema20") or metrics.get("ema20")),
                "trend": str(raw.get("trend") or "").lower() or None,
            },
            "computed": computed,
            "computed_metrics": raw.get("computed_metrics") if isinstance(raw.get("computed_metrics"), dict) else {},
            "metrics_status": raw.get("metrics_status") if isinstance(raw.get("metrics_status"), dict) else {},
            "model": model_payload,
            "notes": pick_notes,
            "actions": {
                "open_route": route,
                "send_to_workbench_payload": send_payload,
            },
        }

    async def get_top_recommendations(self, limit: int = 3) -> dict[str, Any]:
        notes: list[str] = []
        endpoint_error: dict[str, Any] | None = None

        try:
            regime_payload = await self.regime_service.get_regime()
            if not isinstance(regime_payload, dict):
                regime_payload = {}
        except Exception as exc:
            self.logger.exception("recommendations.regime_unavailable")
            endpoint_error = {
                "message": str(exc),
                "type": type(exc).__name__,
                "source": "regime_service",
            }
            notes.append(f"regime unavailable: {exc}")
            regime_payload = {
                "regime_label": "NEUTRAL",
                "regime_score": 50.0,
                "suggested_playbook": {
                    "primary": ["iron_condor", "calendar"],
                    "avoid": [],
                    "notes": ["Regime service unavailable; using neutral fallback"],
                },
            }

        strategy_candidates, strategy_notes = self._collect_strategy_candidates()
        scanner_candidates, scanner_notes = await self._collect_stock_scanner_candidates()

        notes.extend(strategy_notes)
        notes.extend(scanner_notes)

        candidates = strategy_candidates + scanner_candidates
        if not strategy_candidates and scanner_candidates:
            notes.append("No strategy reports found; using stock scanner fallback")

        picks: list[dict[str, Any]] = []
        for candidate in candidates:
            try:
                picks.append(self._build_pick(candidate, regime_payload))
            except Exception as exc:
                self.logger.exception("recommendations.build_pick_failed symbol=%s", candidate.get("symbol"))
                notes.append(f"candidate skipped: {candidate.get('symbol') or 'unknown'} ({exc})")

        def _safe_sort_score(row: dict[str, Any]) -> float:
            value = self._safe_float(row.get("rank_score"))
            return value if value is not None else 0.0

        picks.sort(key=_safe_sort_score, reverse=True)
        picks = picks[: max(1, min(int(limit or 3), 3))]

        if not picks:
            notes.append("Run a scan to generate picks")

        payload = {
            "as_of": self._now_iso(),
            "regime": {
                "label": regime_payload.get("regime_label"),
                "score": regime_payload.get("regime_score"),
            },
            "picks": picks,
            "notes": notes,
        }

        if endpoint_error:
            payload["error"] = endpoint_error

        return payload
