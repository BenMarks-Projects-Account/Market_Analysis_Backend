"""
Active Trade Monitor Service — Deterministic position-monitoring engine.

Evaluates each open position and produces:
  - status: HOLD / WATCH / REDUCE / CLOSE
  - score 0–100
  - explainable breakdown (per-component scores)
  - trigger evaluations with level (INFO / WARN / CRITICAL)
  - recommended action with short reason

Inputs (per position):
  position   — symbol, qty, avg_entry, cost_basis, current_price, unrealized P&L, %
  market_ctx — regime label/score from RegimeService
  indicators — SMA20, SMA50, RSI14 from price history (via BaseDataService + quant_analysis)

Caching:
  Results are cached per symbol with configurable TTL (default 45s) via the shared TTLCache.

Data integrity rules (from copilot-instructions.md):
  - Never fabricate values.  Missing → None, partial computation.
  - All derived fields list their inputs and formula.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from common.quant_analysis import rsi, simple_moving_average, trend_features

logger = logging.getLogger(__name__)

# ── Weight configuration (v1, tunable) ───────────────────────────────────────
SCORE_WEIGHTS = {
    "regime_alignment": 25,
    "trend_strength": 25,
    "drawdown_risk": 25,
    "volatility_risk": 15,
    "time_in_trade": 10,
}

# ── Status thresholds ────────────────────────────────────────────────────────
#   score ≥ 65 → HOLD    (position looks healthy)
#   score ≥ 45 → WATCH   (neutral, keep monitoring)
#   score ≥ 25 → REDUCE  (warning signs, consider trimming)
#   score < 25 → CLOSE   (deteriorated, consider exiting)
STATUS_THRESHOLDS = [
    (65, "HOLD"),
    (45, "WATCH"),
    (25, "REDUCE"),
    (0, "CLOSE"),
]

# ── Trigger definitions ─────────────────────────────────────────────────────
TRIGGER_DEFAULTS = {
    "max_drawdown": {"warn_pct": -0.05, "critical_pct": -0.10},
    "trend_break_sma20": {},
    "trend_break_sma50": {},
    "regime_flip": {},
}


# ── Data classes ─────────────────────────────────────────────────────────────
@dataclass
class TriggerResult:
    """Single trigger evaluation.

    Attributes:
        id        — stable trigger identifier
        level     — INFO | WARN | CRITICAL
        message   — human-readable description
        hit       — True if trigger condition is met
        value     — observed value (e.g. current drawdown %)
        threshold — threshold that defines the trigger
    """
    id: str
    level: str   # INFO | WARN | CRITICAL
    message: str
    hit: bool
    value: Optional[float] = None
    threshold: Optional[float] = None


@dataclass
class MonitorResult:
    """Complete monitor evaluation for one position.

    JSON-serialisable output:
    {
      symbol, status, score_0_100,
      breakdown: {factor: score_component, ...},
      triggers: [{id, level, message, hit, value, threshold}, ...],
      recommended_action: {action, reason_short},
      last_evaluated_ts
    }
    """
    symbol: str
    status: str          # HOLD | WATCH | REDUCE | CLOSE
    score_0_100: int
    breakdown: dict[str, float]
    triggers: list[dict[str, Any]]
    recommended_action: dict[str, str]
    last_evaluated_ts: float  # epoch seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "status": self.status,
            "score_0_100": self.score_0_100,
            "breakdown": self.breakdown,
            "triggers": self.triggers,
            "recommended_action": self.recommended_action,
            "last_evaluated_ts": self.last_evaluated_ts,
        }


# ═════════════════════════════════════════════════════════════════════════════
#  Scoring Components
# ═════════════════════════════════════════════════════════════════════════════

def _score_regime_alignment(
    position_side: str,
    regime_label: Optional[str],
    regime_score: Optional[float],
) -> float:
    """Score 0–25: how well the position aligns with market regime.

    Inputs:
      position_side — "long" or "short"
      regime_label  — "RISK_ON" | "NEUTRAL" | "RISK_OFF" (from RegimeService)
      regime_score  — 0–100 composite regime score

    Formula:
      Long + RISK_ON   → full marks (25)
      Long + NEUTRAL   → partial (regime_score / 100 * 25 * 0.6)
      Long + RISK_OFF  → low (max 5)
      Short positions get inverted scoring.
      Missing regime   → neutral 12.5 (half credit).
    """
    max_pts = SCORE_WEIGHTS["regime_alignment"]

    if regime_label is None:
        return max_pts * 0.5  # degrade gracefully

    label = regime_label.upper()
    rs = regime_score if regime_score is not None else 50.0

    if position_side == "long":
        if label == "RISK_ON":
            return max_pts * min(rs / 100, 1.0)
        elif label == "NEUTRAL":
            return max_pts * 0.6 * min(rs / 100, 1.0)
        else:  # RISK_OFF
            return max_pts * 0.2
    else:  # short
        if label == "RISK_OFF":
            return max_pts * min((100 - rs) / 100, 1.0)
        elif label == "NEUTRAL":
            return max_pts * 0.5
        else:  # RISK_ON
            return max_pts * 0.2


def _score_trend_strength(
    position_side: str,
    current_price: Optional[float],
    sma20: Optional[float],
    sma50: Optional[float],
    rsi14: Optional[float],
) -> float:
    """Score 0–25: trend confirmation for the position.

    Inputs:
      position_side  — "long" or "short"
      current_price  — latest price per share
      sma20, sma50   — simple moving averages
      rsi14          — RSI(14)

    Formula (long):
      +8  if price > SMA20
      +7  if price > SMA50
      +5  if SMA20 > SMA50 (golden cross alignment)
      +5  RSI bonus: max 5 * clamp((rsi - 30) / 40, 0, 1)  → favours 50-70 zone
      Short positions: invert price-vs-SMA comparisons.
      Each missing indicator → that sub-component returns 0.
    """
    max_pts = SCORE_WEIGHTS["trend_strength"]
    pts = 0.0
    available_components = 0

    if current_price is not None and sma20 is not None:
        available_components += 1
        if position_side == "long":
            if current_price > sma20:
                pts += 8
        else:
            if current_price < sma20:
                pts += 8

    if current_price is not None and sma50 is not None:
        available_components += 1
        if position_side == "long":
            if current_price > sma50:
                pts += 7
        else:
            if current_price < sma50:
                pts += 7

    if sma20 is not None and sma50 is not None:
        available_components += 1
        if position_side == "long":
            if sma20 > sma50:
                pts += 5
        else:
            if sma20 < sma50:
                pts += 5

    if rsi14 is not None:
        available_components += 1
        if position_side == "long":
            # For longs: RSI 50-70 is ideal; below 30 is bad; above 70 may be overextended
            pts += 5 * max(0, min(1, (rsi14 - 30) / 40))
        else:
            # For shorts: RSI 30-50 is ideal; above 70 is bad
            pts += 5 * max(0, min(1, (70 - rsi14) / 40))

    if available_components == 0:
        return max_pts * 0.5  # half credit when no indicators

    return min(pts, max_pts)


def _score_drawdown_risk(
    pl_pct: Optional[float],
) -> float:
    """Score 0–25: inverse drawdown risk.  Higher = safer position.

    Inputs:
      pl_pct — unrealised P&L as decimal fraction (e.g. -0.05 = -5%)

    Formula:
      pl_pct ≥ +0.10  →  25 (comfortable profit)
      pl_pct =  0.00  →  18 (break-even)
      pl_pct = -0.05  →  10
      pl_pct = -0.10  →   5
      pl_pct ≤ -0.20  →   0

      Linear interpolation between anchor points.
      Missing pl_pct → 12.5 (half credit).
    """
    max_pts = SCORE_WEIGHTS["drawdown_risk"]

    if pl_pct is None:
        return max_pts * 0.5

    # Clamp and interpolate
    if pl_pct >= 0.10:
        return max_pts
    elif pl_pct >= 0.0:
        # 0.00→18, 0.10→25  → linear in [0, 0.10]
        return 18 + (pl_pct / 0.10) * (max_pts - 18)
    elif pl_pct >= -0.05:
        # -0.05→10, 0.00→18
        return 10 + ((pl_pct + 0.05) / 0.05) * (18 - 10)
    elif pl_pct >= -0.10:
        # -0.10→5, -0.05→10
        return 5 + ((pl_pct + 0.10) / 0.05) * (10 - 5)
    elif pl_pct >= -0.20:
        # -0.20→0, -0.10→5
        return 0 + ((pl_pct + 0.20) / 0.10) * 5
    else:
        return 0


def _score_volatility_risk(
    rsi14: Optional[float],
    atr_pct: Optional[float] = None,
) -> float:
    """Score 0–15: lower vol risk → higher score.

    Inputs:
      rsi14   — RSI(14)  as proxy for momentum stability
      atr_pct — ATR as % of price (optional, for future use)

    Formula:
      RSI in 40–60 → full 15 (stable)
      RSI 30–40 or 60–70 → 10
      RSI < 30 or > 70 → 5   (extreme → unstable)
      Missing RSI → 7.5 (half credit).
    """
    max_pts = SCORE_WEIGHTS["volatility_risk"]

    if rsi14 is None:
        return max_pts * 0.5

    if 40 <= rsi14 <= 60:
        return max_pts
    elif 30 <= rsi14 <= 70:
        return max_pts * 0.67
    else:
        return max_pts * 0.33


def _score_time_in_trade() -> float:
    """Score 0–10: placeholder for time-based component.

    Not yet implemented — requires entry date tracking.
    Returns neutral score (half credit) for all positions.
    """
    return SCORE_WEIGHTS["time_in_trade"] * 0.5


# ═════════════════════════════════════════════════════════════════════════════
#  Trigger Evaluation
# ═════════════════════════════════════════════════════════════════════════════

def evaluate_triggers(
    position_side: str,
    pl_pct: Optional[float],
    current_price: Optional[float],
    sma20: Optional[float],
    sma50: Optional[float],
    regime_label: Optional[str],
) -> list[TriggerResult]:
    """Evaluate all v1 triggers for a position.

    Returns a list of TriggerResult objects.  Triggers with ``hit=True``
    indicate an active alert condition.
    """
    triggers: list[TriggerResult] = []

    # ── 1) Max drawdown trigger ──────────────────────────────────────────
    dd = TRIGGER_DEFAULTS["max_drawdown"]
    if pl_pct is not None:
        if pl_pct <= dd["critical_pct"]:
            triggers.append(TriggerResult(
                id="max_drawdown",
                level="CRITICAL",
                message=f"Drawdown {pl_pct:.1%} exceeds critical threshold {dd['critical_pct']:.0%}",
                hit=True,
                value=pl_pct,
                threshold=dd["critical_pct"],
            ))
        elif pl_pct <= dd["warn_pct"]:
            triggers.append(TriggerResult(
                id="max_drawdown",
                level="WARN",
                message=f"Drawdown {pl_pct:.1%} exceeds warning threshold {dd['warn_pct']:.0%}",
                hit=True,
                value=pl_pct,
                threshold=dd["warn_pct"],
            ))
        else:
            triggers.append(TriggerResult(
                id="max_drawdown",
                level="INFO",
                message=f"Drawdown {pl_pct:.1%} within tolerance",
                hit=False,
                value=pl_pct,
                threshold=dd["warn_pct"],
            ))
    else:
        triggers.append(TriggerResult(
            id="max_drawdown",
            level="INFO",
            message="P&L % unavailable — drawdown check skipped",
            hit=False,
        ))

    # ── 2) Trend break SMA20 ────────────────────────────────────────────
    if current_price is not None and sma20 is not None:
        if position_side == "long" and current_price < sma20:
            triggers.append(TriggerResult(
                id="trend_break_sma20",
                level="WARN",
                message=f"Price ${current_price:.2f} below SMA20 ${sma20:.2f}",
                hit=True,
                value=current_price,
                threshold=sma20,
            ))
        elif position_side == "short" and current_price > sma20:
            triggers.append(TriggerResult(
                id="trend_break_sma20",
                level="WARN",
                message=f"Price ${current_price:.2f} above SMA20 ${sma20:.2f} (short position)",
                hit=True,
                value=current_price,
                threshold=sma20,
            ))
        else:
            triggers.append(TriggerResult(
                id="trend_break_sma20",
                level="INFO",
                message="Price is on correct side of SMA20",
                hit=False,
                value=current_price,
                threshold=sma20,
            ))
    else:
        triggers.append(TriggerResult(
            id="trend_break_sma20",
            level="INFO",
            message="SMA20 unavailable — trend break check skipped",
            hit=False,
        ))

    # ── 3) Trend break SMA50 ────────────────────────────────────────────
    if current_price is not None and sma50 is not None:
        if position_side == "long" and current_price < sma50:
            triggers.append(TriggerResult(
                id="trend_break_sma50",
                level="CRITICAL",
                message=f"Price ${current_price:.2f} below SMA50 ${sma50:.2f}",
                hit=True,
                value=current_price,
                threshold=sma50,
            ))
        elif position_side == "short" and current_price > sma50:
            triggers.append(TriggerResult(
                id="trend_break_sma50",
                level="CRITICAL",
                message=f"Price ${current_price:.2f} above SMA50 ${sma50:.2f} (short position)",
                hit=True,
                value=current_price,
                threshold=sma50,
            ))
        else:
            triggers.append(TriggerResult(
                id="trend_break_sma50",
                level="INFO",
                message="Price is on correct side of SMA50",
                hit=False,
                value=current_price,
                threshold=sma50,
            ))
    else:
        triggers.append(TriggerResult(
            id="trend_break_sma50",
            level="INFO",
            message="SMA50 unavailable — trend break check skipped",
            hit=False,
        ))

    # ── 4) Regime flip trigger ──────────────────────────────────────────
    if regime_label is not None:
        label = regime_label.upper()
        if position_side == "long" and label == "RISK_OFF":
            triggers.append(TriggerResult(
                id="regime_flip",
                level="CRITICAL",
                message="Market regime RISK_OFF while holding long position",
                hit=True,
            ))
        elif position_side == "short" and label == "RISK_ON":
            triggers.append(TriggerResult(
                id="regime_flip",
                level="CRITICAL",
                message="Market regime RISK_ON while holding short position",
                hit=True,
            ))
        else:
            triggers.append(TriggerResult(
                id="regime_flip",
                level="INFO",
                message=f"Regime {label} aligns with {position_side} position",
                hit=False,
            ))
    else:
        triggers.append(TriggerResult(
            id="regime_flip",
            level="INFO",
            message="Regime data unavailable — regime flip check skipped",
            hit=False,
        ))

    return triggers


# ═════════════════════════════════════════════════════════════════════════════
#  Main evaluation function
# ═════════════════════════════════════════════════════════════════════════════

def _status_from_score(score: int) -> str:
    """Map composite score 0–100 to status label."""
    for threshold, label in STATUS_THRESHOLDS:
        if score >= threshold:
            return label
    return "CLOSE"


def _apply_trigger_overrides(status: str, triggers: list[TriggerResult]) -> str:
    """Downgrade status if CRITICAL triggers are hit.

    Rules:
      - Any CRITICAL trigger → status can be at most REDUCE.
      - 2+ CRITICAL triggers → status is CLOSE.
    """
    critical_count = sum(1 for t in triggers if t.hit and t.level == "CRITICAL")
    if critical_count >= 2:
        return "CLOSE"
    elif critical_count >= 1:
        # Cannot be better than REDUCE
        if status in ("HOLD", "WATCH"):
            return "REDUCE"
    return status


def _recommended_action(status: str, triggers: list[TriggerResult]) -> dict[str, str]:
    """Build a short action recommendation from status + triggers."""
    hit_triggers = [t for t in triggers if t.hit]
    critical = [t for t in hit_triggers if t.level == "CRITICAL"]
    warns = [t for t in hit_triggers if t.level == "WARN"]

    if status == "CLOSE":
        reason = "Multiple critical alerts active"
        if critical:
            reason = critical[0].message
        return {"action": "CLOSE", "reason_short": reason}
    elif status == "REDUCE":
        reason = "Position showing warning signals"
        if critical:
            reason = critical[0].message
        elif warns:
            reason = warns[0].message
        return {"action": "REDUCE", "reason_short": reason}
    elif status == "WATCH":
        reason = "Position is neutral, monitor closely"
        if warns:
            reason = warns[0].message
        return {"action": "WATCH", "reason_short": reason}
    else:  # HOLD
        return {"action": "HOLD", "reason_short": "Position looks healthy — hold"}


def evaluate_position_monitor(
    position: dict[str, Any],
    market_context: Optional[dict[str, Any]] = None,
    indicators: Optional[dict[str, Any]] = None,
) -> MonitorResult:
    """Core scoring function — deterministic, no I/O.

    Parameters
    ----------
    position : dict
        Keys: symbol, quantity, avg_open_price, mark_price, cost_basis_total,
              market_value, unrealized_pnl, unrealized_pnl_pct
    market_context : dict, optional
        Keys: regime_label (str), regime_score (float 0–100)
    indicators : dict, optional
        Keys: sma20 (float), sma50 (float), rsi14 (float)

    Returns
    -------
    MonitorResult — fully scored and explained.
    """
    symbol = str(position.get("symbol") or "???").upper()
    qty = position.get("quantity")
    position_side = "short" if (qty is not None and qty < 0) else "long"
    current_price = _to_float(position.get("mark_price"))
    pl_pct = _to_float(position.get("unrealized_pnl_pct"))

    mc = market_context or {}
    regime_label = mc.get("regime_label") or mc.get("label")
    regime_score = _to_float(mc.get("regime_score") or mc.get("score"))

    ind = indicators or {}
    sma20 = _to_float(ind.get("sma20"))
    sma50 = _to_float(ind.get("sma50"))
    rsi14 = _to_float(ind.get("rsi14"))

    # ── Score components ─────────────────────────────────────────────────
    regime_pts = round(_score_regime_alignment(position_side, regime_label, regime_score), 1)
    trend_pts = round(_score_trend_strength(position_side, current_price, sma20, sma50, rsi14), 1)
    drawdown_pts = round(_score_drawdown_risk(pl_pct), 1)
    vol_pts = round(_score_volatility_risk(rsi14), 1)
    time_pts = round(_score_time_in_trade(), 1)

    raw_score = regime_pts + trend_pts + drawdown_pts + vol_pts + time_pts
    score = max(0, min(100, round(raw_score)))

    breakdown = {
        "regime_alignment": regime_pts,
        "trend_strength": trend_pts,
        "drawdown_risk": drawdown_pts,
        "volatility_risk": vol_pts,
        "time_in_trade": time_pts,
    }

    # ── Triggers ─────────────────────────────────────────────────────────
    trigger_results = evaluate_triggers(
        position_side, pl_pct, current_price, sma20, sma50, regime_label,
    )

    # ── Status (score-based → trigger-overridden) ────────────────────────
    status = _status_from_score(score)
    status = _apply_trigger_overrides(status, trigger_results)

    # ── Action recommendation ────────────────────────────────────────────
    action = _recommended_action(status, trigger_results)

    triggers_dicts = [
        {
            "id": t.id,
            "level": t.level,
            "message": t.message,
            "hit": t.hit,
            "value": t.value,
            "threshold": t.threshold,
        }
        for t in trigger_results
    ]

    return MonitorResult(
        symbol=symbol,
        status=status,
        score_0_100=score,
        breakdown=breakdown,
        triggers=triggers_dicts,
        recommended_action=action,
        last_evaluated_ts=time.time(),
    )


# ═════════════════════════════════════════════════════════════════════════════
#  Service class — async wrapper with caching + indicator fetching
# ═════════════════════════════════════════════════════════════════════════════

class ActiveTradeMonitorService:
    """Orchestrates indicator fetching + cached monitor evaluation.

    Wired in main.py and consumed by routes_active_trades.py.

    Dependencies:
      base_data_service — for price history (SMA/RSI computation)
      regime_service    — for market regime context
      cache             — shared TTLCache instance
      ttl_seconds       — cache TTL for monitor results (default 45s)
    """

    def __init__(
        self,
        base_data_service: Any,
        regime_service: Any,
        cache: Any,
        ttl_seconds: int = 45,
    ):
        self.base_data_service = base_data_service
        self.regime_service = regime_service
        self.cache = cache
        self.ttl_seconds = ttl_seconds

    async def _fetch_indicators(self, symbol: str) -> dict[str, Any]:
        """Fetch SMA20, SMA50, RSI14 for a symbol from price history.

        Uses BaseDataService.get_prices_history() which already has its own
        caching layer (Polygon/Tradier daily closes).

        Returns dict with keys: sma20, sma50, rsi14 (any may be None).
        """
        try:
            prices = await self.base_data_service.get_prices_history(symbol, lookback_days=120)
        except Exception as exc:
            logger.warning("event=monitor_indicators_unavailable symbol=%s error=%s", symbol, exc)
            return {"sma20": None, "sma50": None, "rsi14": None}

        if not prices:
            return {"sma20": None, "sma50": None, "rsi14": None}

        return {
            "sma20": simple_moving_average(prices, 20),
            "sma50": simple_moving_average(prices, 50),
            "rsi14": rsi(prices, 14),
        }

    async def _fetch_regime(self) -> dict[str, Any]:
        """Fetch current market regime via RegimeService.

        RegimeService has its own caching (TTL ~45s).
        Returns dict with regime_label, regime_score.
        """
        try:
            regime = await self.regime_service.get_regime()
            return {
                "regime_label": regime.get("label"),
                "regime_score": regime.get("score"),
            }
        except Exception as exc:
            logger.warning("event=monitor_regime_unavailable error=%s", exc)
            return {"regime_label": None, "regime_score": None}

    async def evaluate(self, position: dict[str, Any]) -> dict[str, Any]:
        """Evaluate a single position.  Uses cache — won't re-compute within TTL.

        Returns MonitorResult.to_dict().
        """
        symbol = str(position.get("symbol") or "???").upper()
        cache_key = f"monitor:{symbol}"

        cached = await self.cache.get(cache_key)
        if cached is not None:
            return cached

        # Fetch inputs
        indicators = await self._fetch_indicators(symbol)
        market_context = await self._fetch_regime()

        result = evaluate_position_monitor(position, market_context, indicators)
        result_dict = result.to_dict()

        await self.cache.set(cache_key, result_dict, self.ttl_seconds)
        return result_dict

    async def evaluate_batch(self, positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Evaluate all positions.  Regime is fetched once, indicators per symbol.

        Returns list of MonitorResult dicts in same order as input.
        """
        if not positions:
            return []

        market_context = await self._fetch_regime()
        results: list[dict[str, Any]] = []

        # Deduplicate indicator fetches
        symbol_indicators: dict[str, dict[str, Any]] = {}

        for pos in positions:
            symbol = str(pos.get("symbol") or "???").upper()
            cache_key = f"monitor:{symbol}"

            cached = await self.cache.get(cache_key)
            if cached is not None:
                results.append(cached)
                continue

            if symbol not in symbol_indicators:
                symbol_indicators[symbol] = await self._fetch_indicators(symbol)

            indicators = symbol_indicators[symbol]
            result = evaluate_position_monitor(pos, market_context, indicators)
            result_dict = result.to_dict()
            await self.cache.set(cache_key, result_dict, self.ttl_seconds)
            results.append(result_dict)

        return results


# ── Utility ──────────────────────────────────────────────────────────────────

def _to_float(val: Any) -> Optional[float]:
    """Safely coerce to float.  Returns None on failure — never fabricates."""
    if val is None:
        return None
    try:
        f = float(val)
        if f != f:  # NaN check
            return None
        return f
    except (TypeError, ValueError):
        return None
