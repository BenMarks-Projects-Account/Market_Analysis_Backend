from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from app.services.base_data_service import BaseDataService
from app.utils.cache import TTLCache
from app.utils.http import request_json
from common.quant_analysis import rsi, simple_moving_average

logger = logging.getLogger(__name__)

# ── Block weights for three-block synthesis ───────────────────────
# Structural: background environment (rates, liquidity, macro)
# Tape:       broad US market behavior (breadth, participation, trend)
# Tactical:   short/medium-term pressure (vol, flows, sentiment)
_BLOCK_WEIGHTS = {"structural": 0.30, "tape": 0.40, "tactical": 0.30}

# Conflict penalty threshold: if max block spread exceeds this,
# confidence is penalized proportionally.
_CONFLICT_SPREAD_THRESHOLD = 30.0


class RegimeService:
    def __init__(
        self,
        base_data_service: BaseDataService,
        cache: TTLCache,
        *,
        ttl_seconds: int = 45,
    ) -> None:
        self.base_data_service = base_data_service
        self.cache = cache
        self.ttl_seconds = ttl_seconds
        # MI engine services — set via bind_engines() after construction
        self._breadth_service: Any = None
        self._volatility_options_service: Any = None
        self._cross_asset_macro_service: Any = None
        self._flows_positioning_service: Any = None
        self._liquidity_conditions_service: Any = None
        self._news_sentiment_service: Any = None

    def bind_engines(
        self,
        *,
        breadth_service: Any = None,
        volatility_options_service: Any = None,
        cross_asset_macro_service: Any = None,
        flows_positioning_service: Any = None,
        liquidity_conditions_service: Any = None,
        news_sentiment_service: Any = None,
    ) -> None:
        """Late-bind MI engine services (constructed after RegimeService in main.py)."""
        if breadth_service is not None:
            self._breadth_service = breadth_service
        if volatility_options_service is not None:
            self._volatility_options_service = volatility_options_service
        if cross_asset_macro_service is not None:
            self._cross_asset_macro_service = cross_asset_macro_service
        if flows_positioning_service is not None:
            self._flows_positioning_service = flows_positioning_service
        if liquidity_conditions_service is not None:
            self._liquidity_conditions_service = liquidity_conditions_service
        if news_sentiment_service is not None:
            self._news_sentiment_service = news_sentiment_service

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _ema(prices: list[float], period: int) -> float | None:
        if period <= 0 or len(prices) < period:
            return None
        k = 2.0 / (period + 1.0)
        value = sum(prices[:period]) / period
        for price in prices[period:]:
            value = (price * k) + (value * (1.0 - k))
        return value

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        if value in (None, "", "."):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _mark_fred_success(self, message: str) -> None:
        self.base_data_service._mark_success("fred", http_status=200, message=message)

    def _mark_fred_failure(self, err: Exception) -> None:
        self.base_data_service._mark_failure("fred", err)

    async def _fred_recent_values(self, series_id: str, count: int) -> list[float]:
        fred = self.base_data_service.fred_client
        try:
            payload = await request_json(
                fred.http_client,
                "GET",
                f"{fred.settings.FRED_BASE_URL}/series/observations",
                params={
                    "series_id": series_id,
                    "sort_order": "desc",
                    "limit": max(2, int(count)),
                    "api_key": fred.settings.FRED_KEY,
                    "file_type": "json",
                },
            )
            obs = payload.get("observations") or []
            out: list[float] = []
            for row in obs:
                value = self._safe_float(row.get("value"))
                if value is None:
                    continue
                out.append(value)
            self._mark_fred_success(f"series {series_id} ok")
            return out
        except Exception as exc:
            self._mark_fred_failure(exc)
            return []

    @staticmethod
    def _bounded(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    @staticmethod
    def _normalize_component(points: float, max_points: float) -> float:
        if max_points <= 0:
            return 0.0
        return max(0.0, min(100.0, (points / max_points) * 100.0))

    async def _compute(self) -> dict[str, Any]:
        notes: list[str] = []

        # ── Phase 1: Fetch MI engines + raw market data concurrently ──
        mi_results, market_data = await asyncio.gather(
            self._fetch_mi_engines(notes),
            self._fetch_market_data(notes),
        )

        # ── Phase 2: Compute index metrics (shared foundation) ─────
        index_metrics = self._compute_index_metrics(market_data, notes)

        # ── Phase 3: Compute three blocks (first-class engine outputs) ──
        structural = self._compute_structural_block(mi_results, market_data, notes)
        tape = self._compute_tape_block(mi_results, index_metrics, notes)
        tactical = self._compute_tactical_block(mi_results, market_data, notes)

        blocks = {
            "structural": structural,
            "tape": tape,
            "tactical": tactical,
        }

        # ── Phase 4: Weighted synthesis from blocks ────────────────
        block_scores: dict[str, float | None] = {
            k: v["score"] for k, v in blocks.items()
        }
        regime_score, confidence, agreement = self._synthesize(block_scores, notes)

        # ── Phase 5: Label assignment ──────────────────────────────
        label = self._assign_label(regime_score, confidence, agreement)

        # ── Phase 6: Interpretation & playbook ─────────────────────
        interpretation = self._build_interpretation(
            label, regime_score, confidence, blocks, agreement,
        )
        playbook = self._build_playbook(label, regime_score, blocks)
        what_works, what_to_avoid = self._build_what_works_avoids(label, blocks)
        change_triggers = self._build_change_triggers(blocks, label)
        key_drivers = self._build_key_drivers(blocks)

        if notes:
            playbook["notes"] = playbook.get("notes", []) + notes

        # ── Phase 7: Legacy components (backward compat shim) ──────
        components = self._assemble_legacy_components(market_data, index_metrics)

        return {
            "as_of": self._now_iso(),
            "regime_label": label,
            "regime_score": round(regime_score, 2),
            "confidence": round(confidence, 2),
            "interpretation": interpretation,
            "blocks": blocks,
            "agreement": agreement,
            "what_works": what_works,
            "what_to_avoid": what_to_avoid,
            "change_triggers": change_triggers,
            "key_drivers": key_drivers,
            # Backward compat — legacy consumers expect these
            "components": components,
            "suggested_playbook": playbook,
            "source_health": self.base_data_service.get_source_health_snapshot(),
        }

    # ── MI Engine Fetching ─────────────────────────────────────────

    async def _fetch_mi_engines(self, notes: list[str]) -> dict[str, dict[str, Any] | None]:
        """Fetch all bound MI engine results concurrently. Returns normalized outputs."""
        engines = {
            "breadth_participation": self._breadth_service,
            "volatility_options": self._volatility_options_service,
            "cross_asset_macro": self._cross_asset_macro_service,
            "flows_positioning": self._flows_positioning_service,
            "liquidity_financial_conditions": self._liquidity_conditions_service,
            "news_sentiment": self._news_sentiment_service,
        }

        async def _safe_fetch(key: str, svc: Any) -> tuple[str, dict[str, Any] | None]:
            if svc is None:
                notes.append(f"MI engine {key}: service not bound")
                return key, None
            try:
                method_map = {
                    "breadth_participation": "get_breadth_analysis",
                    "volatility_options": "get_volatility_analysis",
                    "cross_asset_macro": "get_cross_asset_analysis",
                    "flows_positioning": "get_flows_positioning_analysis",
                    "liquidity_financial_conditions": "get_liquidity_conditions_analysis",
                    "news_sentiment": "get_news_sentiment",
                }
                method = getattr(svc, method_map[key])
                result = await method()
                # Prefer the pre-computed normalized output
                normalized = result.get("normalized") if isinstance(result, dict) else None
                if normalized and isinstance(normalized, dict):
                    return key, normalized
                # Fallback: return raw engine_result
                engine_result = result.get("engine_result") if isinstance(result, dict) else None
                if engine_result and isinstance(engine_result, dict):
                    return key, engine_result
                return key, result if isinstance(result, dict) else None
            except Exception as exc:
                logger.warning("MI engine %s fetch failed: %s", key, exc)
                notes.append(f"MI engine {key}: fetch failed ({exc})")
                return key, None

        tasks = [_safe_fetch(k, v) for k, v in engines.items()]
        results_list = await asyncio.gather(*tasks)
        return dict(results_list)

    # ── Shared Market Data ─────────────────────────────────────────

    async def _fetch_market_data(self, notes: list[str]) -> dict[str, Any]:
        """Fetch raw market data shared across blocks and legacy components.

        Centralizes all BaseDataService + FRED API calls so each piece of
        data is fetched exactly once, regardless of how many blocks consume it.

        Returns dict with: index_data, vix_now, vix_5d_change,
        ten_year_now, ten_year_delta_bps, sector_breadth.
        """
        TREND_INDEXES = ["SPY", "QQQ", "IWM", "DIA"]
        index_data: dict[str, dict[str, Any]] = {}

        for symbol in TREND_INDEXES:
            snapshot = await self.base_data_service.get_snapshot(symbol)
            try:
                history_full = await self.base_data_service.get_prices_history(
                    symbol, lookback_days=365,
                )
                prices = [float(x) for x in (history_full or []) if x is not None]
            except Exception:
                prices = [
                    float(x)
                    for x in (snapshot.get("prices_history") or [])
                    if x is not None
                ]
            last = self._safe_float(snapshot.get("underlying_price"))
            if last is None and prices:
                last = prices[-1]
            index_data[symbol] = {"prices": prices, "last": last, "snapshot": snapshot}

        spy_snapshot = index_data.get("SPY", {}).get("snapshot", {})

        # ── FRED VIX ──
        vix_recent = await self._fred_recent_values(
            self.base_data_service.fred_client.settings.FRED_VIX_SERIES_ID, 6,
        )
        vix_now = vix_recent[0] if vix_recent else self._safe_float(spy_snapshot.get("vix"))
        vix_5d_prev = vix_recent[5] if len(vix_recent) > 5 else None
        vix_5d_change = (
            (vix_now - vix_5d_prev) / vix_5d_prev
            if (vix_now is not None and vix_5d_prev not in (None, 0))
            else None
        )

        # ── FRED 10Y ──
        ten_year_recent = await self._fred_recent_values("DGS10", 6)
        ten_year_now = ten_year_recent[0] if ten_year_recent else None
        ten_year_5d_prev = ten_year_recent[5] if len(ten_year_recent) > 5 else None
        ten_year_delta_bps = (
            (ten_year_now - ten_year_5d_prev) * 100.0
            if (ten_year_now is not None and ten_year_5d_prev is not None)
            else None
        )

        # ── Sector breadth ──
        sector_symbols = [
            "XLF", "XLK", "XLE", "XLY", "XLP", "XLV",
            "XLI", "XLB", "XLRE", "XLU", "XLC",
        ]
        sector_above = 0
        sector_valid = 0
        for symbol in sector_symbols:
            history = await self.base_data_service.get_prices_history(
                symbol, lookback_days=365,
            )
            prices = [float(x) for x in (history or []) if x is not None]
            if not prices:
                continue
            sector_ema20 = self._ema(prices, 20)
            if sector_ema20 is None:
                continue
            sector_valid += 1
            if prices[-1] > sector_ema20:
                sector_above += 1
        pct_above = (sector_above / sector_valid) if sector_valid else 0.0

        return {
            "index_data": index_data,
            "vix_now": vix_now,
            "vix_5d_change": vix_5d_change,
            "ten_year_now": ten_year_now,
            "ten_year_delta_bps": ten_year_delta_bps,
            "sector_breadth": {
                "sectors_above": sector_above,
                "sectors_total": sector_valid,
                "pct_above": pct_above,
            },
        }

    # ── Block Computations ─────────────────────────────────────────

    @staticmethod
    def _extract_engine_score(mi_results: dict, key: str) -> float | None:
        """Extract composite score from an MI engine normalized output."""
        data = mi_results.get(key)
        if not data or not isinstance(data, dict):
            return None
        score = data.get("score")
        if score is None:
            return None
        try:
            return max(0.0, min(100.0, float(score)))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_engine_confidence(mi_results: dict, key: str) -> float | None:
        """Extract confidence from an MI engine normalized output."""
        data = mi_results.get(key)
        if not data or not isinstance(data, dict):
            return None
        conf = data.get("confidence")
        if conf is None:
            conf = data.get("confidence_score")
        if conf is None:
            return None
        try:
            conf = float(conf)
        except (TypeError, ValueError):
            return None
        # Engine confidence is on 0-100 scale; normalize to 0-1
        if conf > 1.0:
            conf = conf / 100.0
        return max(0.0, min(1.0, conf))

    @staticmethod
    def _extract_key_signals(mi_results: dict, key: str, limit: int = 3) -> list[str]:
        """Extract key signals/summaries from an MI engine normalized output."""
        data = mi_results.get(key)
        if not data or not isinstance(data, dict):
            return []
        signals: list[str] = []
        # Try trader_takeaway first (concise)
        ta = data.get("trader_takeaway")
        if ta and isinstance(ta, str):
            signals.append(ta)
        # Then summary
        summary = data.get("summary")
        if summary and isinstance(summary, str) and summary != ta:
            signals.append(summary)
        # Bull/bear/risk factors
        for fkey in ("bull_factors", "bear_factors", "risks"):
            items = data.get(fkey)
            if isinstance(items, list):
                for item in items[:2]:
                    if isinstance(item, str) and item not in signals:
                        signals.append(item)
        return signals[:limit]

    # ── Index Metrics (shared foundation for Tape block + legacy) ──

    def _compute_index_metrics(
        self,
        market_data: dict[str, Any],
        notes: list[str],
    ) -> dict[str, Any]:
        """Compute trend quality + momentum metrics from index price data.

        Used by: Tape block (primary), legacy components (backward compat).
        Directly computes from raw index prices — no legacy dependency.
        """
        TREND_INDEXES = ["SPY", "QQQ", "IWM", "DIA"]
        index_data = market_data.get("index_data", {})

        trend_signals: list[str] = []
        per_index_scores: dict[str, float] = {}
        trend_inputs: dict[str, Any] = {}
        rsi_values: list[float] = []
        momentum_inputs: dict[str, Any] = {}
        momentum_signals: list[str] = []

        for symbol in TREND_INDEXES:
            idata = index_data.get(symbol)
            if not idata:
                continue
            prices = idata["prices"]
            last = idata["last"]

            if prices and last is not None:
                i_ema20 = self._ema(prices, 20)
                i_ema50 = self._ema(prices, 50)
                i_sma50 = simple_moving_average(prices, 50)
                i_sma200 = simple_moving_average(prices, 200)
                i_points = 0.0
                i_avail = 0.0
                checks: list[str] = []
                if i_ema20 is not None:
                    i_avail += 10.0
                    if last > i_ema20:
                        i_points += 10.0
                    checks.append(f">{' ' if last > i_ema20 else '≤'}EMA20")
                if i_ema50 is not None:
                    i_avail += 5.0
                    if last > i_ema50:
                        i_points += 5.0
                    checks.append(f">{' ' if last > i_ema50 else '≤'}EMA50")
                if i_sma50 is not None and i_sma200 is not None:
                    i_avail += 10.0
                    if i_sma50 > i_sma200:
                        i_points += 10.0
                    checks.append(
                        f"SMA50{' >' if i_sma50 > i_sma200 else ' ≤'}SMA200"
                    )
                if i_avail > 0:
                    idx_score = (i_points / i_avail) * 100.0
                    per_index_scores[symbol] = idx_score
                    trend_signals.append(
                        f"{symbol} {idx_score:.0f}/100 ({', '.join(checks)})"
                    )
                trend_inputs[symbol] = {
                    "close": last, "ema20": i_ema20, "ema50": i_ema50,
                    "sma50": i_sma50, "sma200": i_sma200,
                }

            # RSI / momentum
            if len(prices) >= 15:
                r14 = rsi(prices, 14)
                if r14 is not None:
                    rsi_values.append(r14)
                    momentum_inputs[symbol] = round(r14, 1)

        # Trend score (average of per-index scores, 0-100)
        trend_score = (
            sum(per_index_scores.values()) / len(per_index_scores)
            if per_index_scores else 0.0
        )

        # Momentum score (0-100, based on avg RSI proximity to ideal band)
        momentum_score = 50.0
        if rsi_values:
            avg_rsi = sum(rsi_values) / len(rsi_values)
            momentum_inputs["avg_rsi14"] = round(avg_rsi, 1)
            if 45 <= avg_rsi <= 65:
                momentum_score = 100.0
                momentum_signals.append(f"Avg RSI {avg_rsi:.1f} in ideal band 45-65")
            else:
                distance = min(abs(avg_rsi - 45), abs(avg_rsi - 65))
                scale = max(0.0, 1.0 - min(distance, 25.0) / 25.0)
                momentum_score = scale * 100.0
                momentum_signals.append(f"Avg RSI {avg_rsi:.1f} outside ideal band")
            for sym in TREND_INDEXES:
                rv = momentum_inputs.get(sym)
                if rv is not None:
                    momentum_signals.append(f"{sym} RSI {rv}")
        else:
            momentum_signals.append("No RSI data available")

        # Small-cap confirmation (IWM trend relative to large-cap average)
        smallcap_score: float | None = None
        if "IWM" in per_index_scores and len(per_index_scores) > 1:
            iwm = per_index_scores["IWM"]
            avg_ex_iwm = sum(
                s for sym, s in per_index_scores.items() if sym != "IWM"
            ) / max(1, len(per_index_scores) - 1)
            # IWM at avg → 50; 20+ above → ~60; 20+ below → ~40
            diff = iwm - avg_ex_iwm
            smallcap_score = max(0.0, min(100.0, 50.0 + diff * 0.5))

        return {
            "per_index_scores": per_index_scores,
            "trend_score": trend_score,
            "trend_signals": trend_signals,
            "trend_inputs": trend_inputs,
            "momentum_score": momentum_score,
            "momentum_signals": momentum_signals,
            "momentum_inputs": momentum_inputs,
            "smallcap_score": smallcap_score,
        }

    # ── Block Scoring Helpers ──────────────────────────────────────

    @staticmethod
    def _score_rates_regime(
        ten_year: float | None, delta_bps: float | None,
    ) -> float | None:
        """Score the rates environment for the structural block (0-100).

        Input: 10Y yield level + 5-day change in bps.
        Output: higher = more supportive for risk assets.

        Formula:
          Base from yield level: <3.5% → 90, 3.5-4.0 → 75, 4.0-4.5 → 60,
                                 4.5-5.0 → 40, >5.0 → 20
          Direction adjustment: sharp rise → penalty, falling → bonus.
        Proxy: FRED DGS10 (temporary proxy for full rates complex).
        """
        if ten_year is None:
            return None
        if ten_year < 3.5:
            base = 90.0
        elif ten_year < 4.0:
            base = 75.0
        elif ten_year < 4.5:
            base = 60.0
        elif ten_year < 5.0:
            base = 40.0
        else:
            base = 20.0
        adj = 0.0
        if delta_bps is not None:
            if delta_bps > 25:
                adj = -25.0
            elif delta_bps > 15:
                adj = -15.0
            elif delta_bps > 8:
                adj = -8.0
            elif delta_bps < -10:
                adj = 10.0
            elif delta_bps < -5:
                adj = 5.0
        return max(0.0, min(100.0, base + adj))

    @staticmethod
    def _score_volatility_structure(
        vix: float | None, vix_5d_change: float | None,
    ) -> float | None:
        """Score the volatility structure for the structural block (0-100).

        Input: VIX level + 5-day % change.
        Output: higher = more stable/supportive vol environment.

        Formula:
          Base from VIX level: <14 → 90, 14-18 → 80, 18-22 → 55,
                               22-28 → 35, >28 → 15
          Direction adjustment: rising sharply → penalty.
        Proxy: FRED VIXCLS (temporary proxy for vol term structure analysis).
        """
        if vix is None:
            return None
        if vix < 14:
            base = 90.0
        elif vix < 18:
            base = 80.0
        elif vix < 22:
            base = 55.0
        elif vix < 28:
            base = 35.0
        else:
            base = 15.0
        adj = 0.0
        if vix_5d_change is not None:
            if vix_5d_change > 0.20:
                adj = -15.0
            elif vix_5d_change > 0.10:
                adj = -8.0
            elif vix_5d_change < -0.10:
                adj = 5.0
        return max(0.0, min(100.0, base + adj))

    @staticmethod
    def _score_rate_pressure(delta_bps: float | None) -> float | None:
        """Score short-term rate pressure for the tactical block (0-100).

        Input: 10Y yield 5-day change in bps.
        Output: higher = less headwind for risk assets.

        Formula:
          Falling (<-15bps) → 90, slight fall → 75, stable → 60,
          mild rise → 45, rising → 30, spiking → 15.
        Proxy: FRED DGS10 5D Δ (temporary proxy for DXY + rates complex).
        """
        if delta_bps is None:
            return None
        if delta_bps < -15:
            return 90.0
        elif delta_bps < -5:
            return 75.0
        elif delta_bps <= 5:
            return 60.0
        elif delta_bps <= 15:
            return 45.0
        elif delta_bps <= 25:
            return 30.0
        else:
            return 15.0

    def _compute_structural_block(
        self,
        mi_results: dict[str, dict | None],
        market_data: dict[str, Any],
        notes: list[str],
    ) -> dict[str, Any]:
        """Structural Block: background environment.

        Input families (with weights):
          - liquidity_financial_conditions MI engine (35%): credit, financial conditions, stress
          - cross_asset_macro MI engine (35%): yield curve, commodities, risk appetite, coherence
          - rates regime from FRED 10Y data (15%): yield level & direction
          - volatility structure from VIX data (15%): base vol level & stability

        Temporary proxies:
          - Rates regime: FRED DGS10 (proxy for full rates complex)
          - Volatility structure: FRED VIXCLS (proxy for vol term structure)
        """
        _STRUCTURAL_WEIGHTS = {
            "liquidity": 0.35,
            "macro": 0.35,
            "rates": 0.15,
            "vol_structure": 0.15,
        }

        pillar_scores: dict[str, float | None] = {}

        # MI engines
        liq_score = self._extract_engine_score(mi_results, "liquidity_financial_conditions")
        macro_score = self._extract_engine_score(mi_results, "cross_asset_macro")
        pillar_scores["liquidity"] = liq_score
        pillar_scores["macro"] = macro_score

        # Rates regime from raw 10Y data
        ten_year_now = market_data.get("ten_year_now")
        ten_year_delta_bps = market_data.get("ten_year_delta_bps")
        rates_score = self._score_rates_regime(ten_year_now, ten_year_delta_bps)
        pillar_scores["rates"] = rates_score

        # Volatility structure from VIX data
        vix_now = market_data.get("vix_now")
        vix_5d_change = market_data.get("vix_5d_change")
        vol_struct_score = self._score_volatility_structure(vix_now, vix_5d_change)
        pillar_scores["vol_structure"] = vol_struct_score

        # Confidence from source engines + raw data coverage
        liq_conf = self._extract_engine_confidence(mi_results, "liquidity_financial_conditions")
        macro_conf = self._extract_engine_confidence(mi_results, "cross_asset_macro")

        # Map pillar keys → confidence (engine or fixed for direct data)
        pillar_confidences: dict[str, float] = {}
        if liq_conf is not None:
            pillar_confidences["liquidity"] = liq_conf
        if macro_conf is not None:
            pillar_confidences["macro"] = macro_conf
        if rates_score is not None:
            pillar_confidences["rates"] = 0.9   # Direct FRED data
        if vol_struct_score is not None:
            pillar_confidences["vol_structure"] = 0.9

        # Confidence-adjusted weighted synthesis
        weighted_sum = 0.0
        weight_total = 0.0
        for key, w in _STRUCTURAL_WEIGHTS.items():
            s = pillar_scores.get(key)
            if s is not None:
                conf = pillar_confidences.get(key, 0.85)
                adj_w = w * conf
                weighted_sum += s * adj_w
                weight_total += adj_w

        if weight_total > 0:
            block_score = weighted_sum / weight_total
        else:
            block_score = 50.0
            notes.append("Structural: no data available; defaulting to neutral")

        confs: list[float] = list(pillar_confidences.values())
        block_confidence = sum(confs) / len(confs) if confs else 0.5

        # Label
        if block_score >= 70:
            block_label = "Supportive"
        elif block_score >= 50:
            block_label = "Mixed"
        elif block_score >= 30:
            block_label = "Restrictive"
        else:
            block_label = "Unstable"

        # Collect signals
        key_signals = (
            self._extract_key_signals(mi_results, "liquidity_financial_conditions", 2)
            + self._extract_key_signals(mi_results, "cross_asset_macro", 2)
        )
        if ten_year_now is not None:
            key_signals.append(f"10Y at {ten_year_now:.2f}%")
        if vix_now is not None:
            key_signals.append(f"VIX at {vix_now:.1f}")

        # Pillar detail from normalized outputs
        pillar_detail: dict[str, Any] = {}
        liq_data = mi_results.get("liquidity_financial_conditions")
        if liq_data and isinstance(liq_data, dict):
            pillar_detail["liquidity"] = {
                "score": liq_score,
                "label": liq_data.get("label") or liq_data.get("short_label"),
                "pillar_scores": liq_data.get("pillar_scores"),
            }
        macro_data = mi_results.get("cross_asset_macro")
        if macro_data and isinstance(macro_data, dict):
            pillar_detail["macro"] = {
                "score": macro_score,
                "label": macro_data.get("label") or macro_data.get("short_label"),
                "pillar_scores": macro_data.get("pillar_scores"),
            }
        pillar_detail["rates_regime"] = {
            "score": rates_score,
            "ten_year_yield": ten_year_now,
            "ten_year_delta_bps": ten_year_delta_bps,
            "proxy": "FRED DGS10 — temporary proxy for full rates complex",
        }
        pillar_detail["volatility_structure"] = {
            "score": vol_struct_score,
            "vix": vix_now,
            "vix_5d_change_pct": vix_5d_change,
            "proxy": "FRED VIXCLS — temporary proxy for vol term structure",
        }

        return {
            "score": round(block_score, 1),
            "label": block_label,
            "confidence": round(block_confidence, 2),
            "key_signals": key_signals[:5],
            "source_engines": ["liquidity_financial_conditions", "cross_asset_macro"],
            "input_families": [
                "liquidity_financial_conditions", "rates_regime",
                "cross_asset_confirmation", "volatility_structure", "macro_pressure",
            ],
            "pillar_detail": pillar_detail,
        }

    def _compute_tape_block(
        self,
        mi_results: dict[str, dict | None],
        index_metrics: dict[str, Any],
        notes: list[str],
    ) -> dict[str, Any]:
        """Tape Block: broad US market behavior.

        Input families (with weights):
          - breadth_participation MI engine (45%): participation, breadth, volume, leadership
          - trend quality from index prices (25%): SPY/QQQ/IWM/DIA moving average structure
          - momentum quality from RSI (15%): avg RSI14 across four indexes
          - small-cap confirmation (15%): IWM trend relative to large-cap average

        Temporary proxies:
          - Equal-weight / cap-weight confirmation not yet available (no RSP data)
        """
        _TAPE_WEIGHTS = {
            "breadth": 0.45,
            "trend": 0.25,
            "momentum": 0.15,
            "smallcap": 0.15,
        }

        pillar_scores: dict[str, float | None] = {}

        # Breadth MI engine
        breadth_score = self._extract_engine_score(mi_results, "breadth_participation")
        pillar_scores["breadth"] = breadth_score

        # Trend quality from index metrics (direct computation)
        trend_score = index_metrics.get("trend_score")
        pillar_scores["trend"] = trend_score if trend_score else None

        # Momentum quality from index metrics (direct computation)
        momentum_score = index_metrics.get("momentum_score")
        pillar_scores["momentum"] = momentum_score if momentum_score is not None else None

        # Small-cap confirmation from index metrics
        smallcap_score = index_metrics.get("smallcap_score")
        pillar_scores["smallcap"] = smallcap_score

        # Confidence from breadth engine + direct computation coverage
        breadth_conf = self._extract_engine_confidence(mi_results, "breadth_participation")

        # Map pillar keys → confidence (engine or fixed for direct data)
        pillar_confidences: dict[str, float] = {}
        if breadth_conf is not None:
            pillar_confidences["breadth"] = breadth_conf
        if trend_score is not None:
            pillar_confidences["trend"] = 0.9     # Direct computation
        if momentum_score is not None:
            pillar_confidences["momentum"] = 0.9  # Direct computation
        if smallcap_score is not None:
            pillar_confidences["smallcap"] = 0.85

        # Confidence-adjusted weighted synthesis
        weighted_sum = 0.0
        weight_total = 0.0
        for key, w in _TAPE_WEIGHTS.items():
            s = pillar_scores.get(key)
            if s is not None:
                conf = pillar_confidences.get(key, 0.85)
                adj_w = w * conf
                weighted_sum += s * adj_w
                weight_total += adj_w

        if weight_total > 0:
            block_score = weighted_sum / weight_total
        else:
            block_score = 50.0
            notes.append("Tape: no tape data available; defaulting to neutral")

        confs: list[float] = list(pillar_confidences.values())
        block_confidence = sum(confs) / len(confs) if confs else 0.5

        # Label
        if block_score >= 75:
            block_label = "Trending"
        elif block_score >= 60:
            block_label = "Broad"
        elif block_score >= 45:
            block_label = "Rotational"
        elif block_score >= 30:
            block_label = "Narrow"
        else:
            block_label = "Weakening"

        key_signals = self._extract_key_signals(mi_results, "breadth_participation", 3)
        trend_signals = index_metrics.get("trend_signals", [])
        if trend_signals:
            key_signals.append(trend_signals[0])

        # Pillar detail
        pillar_detail: dict[str, Any] = {}
        breadth_data = mi_results.get("breadth_participation")
        if breadth_data and isinstance(breadth_data, dict):
            pillar_detail["breadth"] = {
                "score": breadth_score,
                "label": breadth_data.get("label") or breadth_data.get("short_label"),
                "pillar_scores": breadth_data.get("pillar_scores"),
            }
        pillar_detail["trend_quality"] = {
            "score": round(trend_score, 1) if trend_score else None,
            "per_index": index_metrics.get("per_index_scores"),
        }
        pillar_detail["momentum_quality"] = {
            "score": round(momentum_score, 1) if momentum_score is not None else None,
            "avg_rsi14": index_metrics.get("momentum_inputs", {}).get("avg_rsi14"),
        }
        if smallcap_score is not None:
            pillar_detail["smallcap_confirmation"] = {
                "score": round(smallcap_score, 1),
                "proxy": "IWM trend vs large-cap average",
            }

        return {
            "score": round(block_score, 1),
            "label": block_label,
            "confidence": round(block_confidence, 2),
            "key_signals": key_signals[:5],
            "source_engines": ["breadth_participation"],
            "input_families": [
                "breadth_participation", "trend_quality",
                "momentum_quality", "smallcap_confirmation",
            ],
            "pillar_detail": pillar_detail,
        }

    def _compute_tactical_block(
        self,
        mi_results: dict[str, dict | None],
        market_data: dict[str, Any],
        notes: list[str],
    ) -> dict[str, Any]:
        """Tactical Block: short/medium-term forward pressure.

        Input families (with weights):
          - volatility_options MI engine (35%): vol regime, term structure, skew, positioning
          - flows_positioning MI engine (30%): positioning pressure, crowding, flow direction
          - news_sentiment MI engine (20%): headline sentiment, narrative severity
          - short-term rate/dollar pressure (15%): 10Y direction in last 5 days

        Temporary proxies:
          - Futures tone / overnight context not yet available (no futures data)
          - Dollar pressure: FRED DGS10 5D Δ (proxy for DXY + rates complex direction)
        """
        _TACTICAL_WEIGHTS = {
            "volatility": 0.35,
            "flows": 0.30,
            "sentiment": 0.20,
            "rate_pressure": 0.15,
        }

        pillar_scores: dict[str, float | None] = {}

        # MI engines
        vol_score = self._extract_engine_score(mi_results, "volatility_options")
        flows_score = self._extract_engine_score(mi_results, "flows_positioning")
        news_score = self._extract_engine_score(mi_results, "news_sentiment")
        pillar_scores["volatility"] = vol_score
        pillar_scores["flows"] = flows_score
        pillar_scores["sentiment"] = news_score

        # Short-term rate pressure from 10Y direction
        ten_year_delta_bps = market_data.get("ten_year_delta_bps")
        rate_pressure_score = self._score_rate_pressure(ten_year_delta_bps)
        pillar_scores["rate_pressure"] = rate_pressure_score

        # Confidence from source engines + raw data coverage
        vol_conf = self._extract_engine_confidence(mi_results, "volatility_options")
        flows_conf = self._extract_engine_confidence(mi_results, "flows_positioning")
        sent_conf = self._extract_engine_confidence(mi_results, "news_sentiment")

        # Map pillar keys → confidence (engine or fixed for direct data)
        pillar_confidences: dict[str, float] = {}
        if vol_conf is not None:
            pillar_confidences["volatility"] = vol_conf
        if flows_conf is not None:
            pillar_confidences["flows"] = flows_conf
        if sent_conf is not None:
            pillar_confidences["sentiment"] = sent_conf
        if rate_pressure_score is not None:
            pillar_confidences["rate_pressure"] = 0.85  # Direct FRED data

        # Confidence-adjusted weighted synthesis
        weighted_sum = 0.0
        weight_total = 0.0
        for key, w in _TACTICAL_WEIGHTS.items():
            s = pillar_scores.get(key)
            if s is not None:
                conf = pillar_confidences.get(key, 0.85)
                adj_w = w * conf
                weighted_sum += s * adj_w
                weight_total += adj_w

        if weight_total > 0:
            block_score = weighted_sum / weight_total
        else:
            block_score = 50.0
            notes.append("Tactical: no MI engine data; defaulting to neutral")

        valid_confs = list(pillar_confidences.values())
        block_confidence = sum(valid_confs) / len(valid_confs) if valid_confs else 0.5

        # Label
        if block_score >= 70:
            block_label = "Expansionary"
        elif block_score >= 50:
            block_label = "Stable"
        elif block_score >= 35:
            block_label = "Compression"
        else:
            block_label = "Event-Risk"

        key_signals = (
            self._extract_key_signals(mi_results, "volatility_options", 2)
            + self._extract_key_signals(mi_results, "flows_positioning", 1)
            + self._extract_key_signals(mi_results, "news_sentiment", 1)
        )

        # Pillar detail
        pillar_detail: dict[str, Any] = {}
        for eng_key, score_val in [
            ("volatility_options", vol_score),
            ("flows_positioning", flows_score),
            ("news_sentiment", news_score),
        ]:
            eng_data = mi_results.get(eng_key)
            if eng_data and isinstance(eng_data, dict):
                pillar_detail[eng_key] = {
                    "score": score_val,
                    "label": eng_data.get("label") or eng_data.get("short_label"),
                    "pillar_scores": eng_data.get("pillar_scores"),
                }
        pillar_detail["rate_pressure"] = {
            "score": rate_pressure_score,
            "ten_year_delta_bps": ten_year_delta_bps,
            "proxy": "FRED DGS10 5D Δ — temporary proxy for DXY + rates complex",
        }

        return {
            "score": round(block_score, 1),
            "label": block_label,
            "confidence": round(block_confidence, 2),
            "key_signals": key_signals[:5],
            "source_engines": ["volatility_options", "flows_positioning", "news_sentiment"],
            "input_families": [
                "short_term_vol", "flows_positioning",
                "event_sentiment", "rate_dollar_pressure",
            ],
            "pillar_detail": pillar_detail,
        }

    # ── Weighted Synthesis ─────────────────────────────────────────

    def _synthesize(
        self,
        block_scores: dict[str, float | None],
        notes: list[str],
    ) -> tuple[float, float, dict[str, Any]]:
        """Combine three block scores into composite regime score + confidence.

        Returns: (regime_score, confidence, agreement_dict)
        """
        available = {k: v for k, v in block_scores.items() if v is not None}

        if not available:
            notes.append("No block scores available; defaulting to neutral baseline")
            return 50.0, 0.3, {
                "blocks_aligned": False,
                "max_spread": 0.0,
                "conflict_pairs": [],
                "conflict_penalty_applied": 0.0,
            }

        # Weighted sum (renormalize weights for available blocks)
        weight_sum = sum(_BLOCK_WEIGHTS[k] for k in available)
        regime_score = sum(
            available[k] * (_BLOCK_WEIGHTS[k] / weight_sum) for k in available
        )
        regime_score = self._bounded(regime_score, 0.0, 100.0)

        # Agreement analysis
        scores_list = list(available.values())
        max_spread = max(scores_list) - min(scores_list) if len(scores_list) > 1 else 0.0

        conflict_pairs: list[str] = []
        block_keys = list(available.keys())
        for i in range(len(block_keys)):
            for j in range(i + 1, len(block_keys)):
                k1, k2 = block_keys[i], block_keys[j]
                spread = abs(available[k1] - available[k2])
                if spread > _CONFLICT_SPREAD_THRESHOLD:
                    conflict_pairs.append(
                        f"{k1} ({available[k1]:.0f}) vs {k2} ({available[k2]:.0f})"
                    )

        blocks_aligned = max_spread <= _CONFLICT_SPREAD_THRESHOLD

        # Confidence: base from data coverage + penalty for conflicts
        coverage = len(available) / 3.0
        base_confidence = coverage * 0.85  # Max 0.85 from coverage alone
        # Conflict penalty: proportional to spread beyond threshold
        conflict_penalty = 0.0
        if max_spread > _CONFLICT_SPREAD_THRESHOLD:
            excess = max_spread - _CONFLICT_SPREAD_THRESHOLD
            conflict_penalty = min(0.30, excess / 100.0)
        confidence = self._bounded(base_confidence - conflict_penalty, 0.1, 0.95)

        agreement = {
            "blocks_aligned": blocks_aligned,
            "max_spread": round(max_spread, 1),
            "conflict_pairs": conflict_pairs,
            "conflict_penalty_applied": round(conflict_penalty, 3),
        }

        if not blocks_aligned:
            notes.append(
                f"Block conflict detected (spread {max_spread:.0f}): "
                f"confidence penalized by {conflict_penalty:.2f}"
            )

        return round(regime_score, 2), round(confidence, 2), agreement

    # ── Label Assignment ───────────────────────────────────────────

    @staticmethod
    def _assign_label(
        score: float, confidence: float, agreement: dict[str, Any],
    ) -> str:
        """Assign regime label based on score, confidence, and block agreement.

        Labels (5-tier):
          RISK_ON          : score >= 65, aligned
          RISK_ON_CAUTIOUS : score >= 65, conflicted; OR score 55-64 aligned
          NEUTRAL          : score 40-64 (default bucket)
          RISK_OFF_CAUTION : score < 40, conflicted; OR score 30-39 aligned
          RISK_OFF         : score < 40, aligned

        Confidence < 0.4 pushes toward NEUTRAL regardless of score.
        """
        aligned = agreement.get("blocks_aligned", True)

        if confidence < 0.4:
            return "NEUTRAL"

        if score >= 65:
            return "RISK_ON" if aligned else "RISK_ON_CAUTIOUS"
        elif score >= 55:
            return "RISK_ON_CAUTIOUS" if aligned else "NEUTRAL"
        elif score >= 40:
            return "NEUTRAL"
        elif score >= 30:
            return "RISK_OFF_CAUTION" if aligned else "NEUTRAL"
        else:
            return "RISK_OFF" if aligned else "RISK_OFF_CAUTION"

    # ── Interpretation ─────────────────────────────────────────────

    @staticmethod
    def _build_interpretation(
        label: str,
        score: float,
        confidence: float,
        blocks: dict[str, dict],
        agreement: dict[str, Any],
    ) -> str:
        """Build a one-line human-readable regime interpretation."""
        label_text = {
            "RISK_ON": "Risk-on environment",
            "RISK_ON_CAUTIOUS": "Cautiously risk-on",
            "NEUTRAL": "Neutral / mixed environment",
            "RISK_OFF_CAUTION": "Cautiously risk-off",
            "RISK_OFF": "Risk-off environment",
        }.get(label, "Unknown")

        structural = blocks.get("structural", {})
        tape = blocks.get("tape", {})
        tactical = blocks.get("tactical", {})

        parts = [f"{label_text} ({score:.0f}/100)"]

        # Structural context
        sl = structural.get("label", "?")
        parts.append(f"structure is {sl.lower()}")

        # Tape context
        tl = tape.get("label", "?")
        parts.append(f"tape is {tl.lower()}")

        # Tactical context
        xl = tactical.get("label", "?")
        parts.append(f"tactical outlook is {xl.lower()}")

        if not agreement.get("blocks_aligned", True):
            parts.append("with internal conflict")

        return "; ".join(parts) + "."

    # ── Playbook ───────────────────────────────────────────────────

    @staticmethod
    def _build_playbook(
        label: str, score: float, blocks: dict[str, dict],
    ) -> dict[str, Any]:
        """Build suggested playbook based on regime label."""
        if label in ("RISK_ON", "RISK_ON_CAUTIOUS"):
            primary = ["put_credit_spread", "covered_call", "call_debit"]
            avoid = ["short_gamma", "debit_butterfly"]
            base_notes = [
                "Favor bullish premium-selling structures with defined risk",
                "Use selective directional long-premium only with strong trend continuation",
            ]
            if label == "RISK_ON_CAUTIOUS":
                base_notes.append("Blocks show some conflict — size down or widen strikes")
        elif label in ("RISK_OFF", "RISK_OFF_CAUTION"):
            primary = ["put_debit", "cash", "hedges"]
            avoid = ["short_puts_near_spot", "short_gamma"]
            base_notes = [
                "Reduce net short downside exposure",
                "Prioritize convex downside protection and smaller risk units",
            ]
            if label == "RISK_OFF_CAUTION":
                base_notes.append("Blocks show some conflict — don't over-commit to bearish thesis")
        else:
            primary = ["iron_condor", "credit_spread_wider_distance", "calendar"]
            avoid = ["high_conviction_directional_bets"]
            base_notes = [
                "Favor range-aware structures and balanced risk",
                "Widen short strikes and tighten entry quality filters",
            ]
        return {"primary": primary, "avoid": avoid, "notes": base_notes}

    @staticmethod
    def _build_what_works_avoids(
        label: str, blocks: dict[str, dict],
    ) -> tuple[list[str], list[str]]:
        """Build what-tends-to-work and what-to-avoid lists."""
        tape_label = blocks.get("tape", {}).get("label", "")
        tactical_label = blocks.get("tactical", {}).get("label", "")

        works: list[str] = []
        avoids: list[str] = []

        if label in ("RISK_ON", "RISK_ON_CAUTIOUS"):
            works.extend([
                "Premium selling on defined-risk bullish spreads",
                "Call directional plays in confirmed trends",
            ])
            if tape_label == "Trending":
                works.append("Momentum continuation entries on pullbacks")
            if tactical_label == "Expansionary":
                works.append("Wider credit spreads with room to breathe")
            avoids.extend([
                "Naked short gamma in elevated vol",
                "Fading strong trends without clear reversal signal",
            ])
        elif label in ("RISK_OFF", "RISK_OFF_CAUTION"):
            works.extend([
                "Protective puts and hedges",
                "Cash preservation and position reduction",
            ])
            if tape_label == "Weakening":
                works.append("Bearish debit spreads on breakdown confirmations")
            avoids.extend([
                "Adding to naked short put exposure near spot",
                "Assuming dip-buy without structural support",
            ])
        else:
            works.extend([
                "Iron condors in range-bound conditions",
                "Calendar spreads exploiting term structure",
            ])
            if tape_label == "Rotational":
                works.append("Sector rotation plays with defined risk")
            avoids.extend([
                "High-conviction directional bets without clear edge",
                "Over-sizing positions when tape is mixed",
            ])
        return works, avoids

    @staticmethod
    def _build_change_triggers(blocks: dict[str, dict], label: str) -> list[str]:
        """Build list of conditions that would shift the regime."""
        triggers: list[str] = []
        structural = blocks.get("structural", {})
        tape = blocks.get("tape", {})
        tactical = blocks.get("tactical", {})

        if label in ("RISK_ON", "RISK_ON_CAUTIOUS"):
            triggers.append("Breadth deteriorates below 40 (tape weakening)")
            triggers.append("VIX spikes above 25 with term structure inversion")
            triggers.append("Credit spreads widen sharply (IG > 150bps)")
            triggers.append("Structural score drops below 40 (liquidity/macro pressure)")
        elif label in ("RISK_OFF", "RISK_OFF_CAUTION"):
            triggers.append("VIX drops below 20 with improving term structure")
            triggers.append("Breadth recovers above 55 (participation broadens)")
            triggers.append("Credit spreads narrow (risk appetite returns)")
            triggers.append("Multiple indexes reclaim key moving averages")
        else:
            triggers.append("Breadth decisively breaks above 65 or below 35")
            triggers.append("Structural block moves beyond neutral zone")
            triggers.append("Tactical block shifts from stable to expansionary or event-risk")
            triggers.append("Block alignment improves (conflict resolves)")

        return triggers[:5]

    @staticmethod
    def _build_key_drivers(blocks: dict[str, dict]) -> list[str]:
        """Build top key drivers from across all blocks."""
        drivers: list[str] = []
        block_order = [
            ("tape", "Tape"),
            ("structural", "Structural"),
            ("tactical", "Tactical"),
        ]
        for bkey, bname in block_order:
            block = blocks.get(bkey, {})
            signals = block.get("key_signals", [])
            if signals:
                drivers.append(f"{bname}: {signals[0]}")
        return drivers[:5]

    # ── Legacy 5-Factor Components (backward compat) ───────────────

    def _assemble_legacy_components(
        self,
        market_data: dict[str, Any],
        index_metrics: dict[str, Any],
    ) -> dict[str, Any]:
        """Assemble legacy 5-factor components from shared data (backward compat).

        All data comes from already-fetched market_data and computed index_metrics.
        No additional API calls — this is a pure reformatter.

        Downstream consumers expect:
          components.{trend,volatility,breadth,rates,momentum}.{score,signals,inputs}
        """
        # ── Trend (from index_metrics) ─────────────────────────────
        trend_score = index_metrics.get("trend_score", 0.0)
        trend_inputs = index_metrics.get("trend_inputs", {})
        trend_signals = index_metrics.get("trend_signals", [])
        trend_max = 25.0
        trend_points = (trend_score / 100.0) * trend_max

        # ── Momentum (from index_metrics) ──────────────────────────
        momentum_score_raw = index_metrics.get("momentum_score", 50.0)
        momentum_signals = index_metrics.get("momentum_signals", [])
        momentum_inputs = index_metrics.get("momentum_inputs", {})
        momentum_max = 10.0
        momentum_points = (momentum_score_raw / 100.0) * momentum_max

        # ── Volatility (from market_data) ──────────────────────────
        vix_now = market_data.get("vix_now")
        vix_5d_change = market_data.get("vix_5d_change")
        vol_points = 0.0
        vol_max = 25.0
        vol_signals: list[str] = []
        if vix_now is not None:
            if vix_now < 18:
                vol_points = 25.0
                vol_signals.append("VIX < 18 (+25)")
            elif vix_now <= 25:
                vol_points = 12.0
                vol_signals.append("VIX 18-25 (+12)")
            else:
                vol_signals.append("VIX > 25 (+0)")
            if vix_5d_change is not None and vix_5d_change > 0.10:
                vol_points -= 5.0
                vol_signals.append("VIX up >10% in 5D (-5)")
        vol_points = self._bounded(vol_points, 0.0, vol_max)

        # ── Breadth (from market_data) ─────────────────────────────
        sector_breadth = market_data.get("sector_breadth", {})
        sectors_above = sector_breadth.get("sectors_above", 0)
        sectors_total = sector_breadth.get("sectors_total", 0)
        pct_above = sector_breadth.get("pct_above", 0.0)
        breadth_max = 25.0
        breadth_points = pct_above * breadth_max
        breadth_signals = [f"{sectors_above}/{sectors_total} sectors above EMA20"]

        # ── Rates (from market_data) ───────────────────────────────
        ten_year_now = market_data.get("ten_year_now")
        ten_year_delta_bps = market_data.get("ten_year_delta_bps")
        rates_points = 15.0
        rates_max = 15.0
        rates_signals: list[str] = []
        if ten_year_now is None:
            rates_points = 0.0
        else:
            rates_signals.append(f"10Y now {ten_year_now:.2f}%")
            if ten_year_delta_bps is not None and ten_year_delta_bps > 15:
                penalty = 10.0 if ten_year_delta_bps > 25 else 7.0
                rates_points -= penalty
                rates_signals.append(
                    f"10Y +{ten_year_delta_bps:.1f}bps in 5D (-{penalty:.0f})"
                )
            elif ten_year_delta_bps is not None and ten_year_delta_bps > 8:
                rates_points -= 5.0
                rates_signals.append(
                    f"10Y +{ten_year_delta_bps:.1f}bps in 5D (-5)"
                )
        rates_points = self._bounded(rates_points, 0.0, rates_max)

        return {
            "trend": {
                "score": trend_score,
                "raw_points": trend_points,
                "signals": trend_signals,
                "inputs": trend_inputs,
            },
            "volatility": {
                "score": self._normalize_component(vol_points, vol_max),
                "signals": vol_signals,
                "inputs": {"vix": vix_now, "vix_5d_change": vix_5d_change},
            },
            "breadth": {
                "score": self._normalize_component(breadth_points, breadth_max),
                "signals": breadth_signals,
                "inputs": {
                    "sectors_above_ema20": sectors_above,
                    "sectors_total": sectors_total,
                    "pct_above_ema20": pct_above,
                },
            },
            "rates": {
                "score": self._normalize_component(rates_points, rates_max),
                "signals": rates_signals,
                "inputs": {
                    "ten_year_yield": ten_year_now,
                    "ten_year_5d_change_bps": ten_year_delta_bps,
                },
            },
            "momentum": {
                "score": self._normalize_component(momentum_points, momentum_max),
                "signals": momentum_signals,
                "inputs": momentum_inputs,
            },
        }

    async def get_regime(self) -> dict[str, Any]:
        return await self.cache.get_or_set("regime:v2", self.ttl_seconds, self._compute)
