"""Pre-Market Intelligence Service.

Combines FuturesClient data into actionable overnight analysis:
  - Gap analysis for all 4 index futures
  - VIX term-structure classification
  - Overnight regime signal (BULLISH / NEUTRAL / BEARISH)
  - Cross-asset confirmation (oil, dollar, bonds)
  - Position exposure alerts for active trades

Designed to run 24/5 but most valuable during the pre-market window
(04:00–09:30 ET) when futures are trading but equities are not.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from app.clients.futures_client import FuturesClient
from app.utils.cache import TTLCache
from app.utils.market_hours import market_status

_log = logging.getLogger(__name__)

# ── Gap classification thresholds (as fraction of underlying) ────────
_LARGE_GAP = 0.01   # ±1.0%
_MEDIUM_GAP = 0.005  # ±0.5%

# ── Briefing cache TTL ───────────────────────────────────────────────
_BRIEFING_TTL = 30  # seconds


class PreMarketIntelligenceService:
    """Orchestrates futures data into a pre-market briefing."""

    # Instruments used for equity-index gap analysis
    _INDEX_KEYS = ("es", "nq", "rty", "ym")

    # Maps ETF underlyings to futures instrument keys
    FUTURES_MAP: dict[str, str] = {
        "SPY": "es", "QQQ": "nq", "IWM": "rty", "DIA": "ym",
    }

    def __init__(
        self,
        futures_client: FuturesClient,
        cache: TTLCache,
    ) -> None:
        self.futures_client = futures_client
        self.cache = cache

    # ------------------------------------------------------------------
    # Public: full briefing
    # ------------------------------------------------------------------

    async def build_briefing(self, active_trades: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Build the complete pre-market intelligence briefing.

        Returns a dict with keys:
            timestamp, market_status, snapshots, gap_analysis,
            vix_term_structure, overnight_signal, cross_asset,
            position_alerts
        """
        cache_key = "premarket:briefing"

        async def _load() -> dict[str, Any]:
            return await self._compute_briefing(active_trades)

        # When active_trades are provided, bypass cache so alerts are fresh
        if active_trades:
            return await self._compute_briefing(active_trades)

        return await self.cache.get_or_set(cache_key, _BRIEFING_TTL, _load)

    # ------------------------------------------------------------------
    # Internal: compute everything
    # ------------------------------------------------------------------

    async def _compute_briefing(self, active_trades: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        snapshots = await self.futures_client.get_all_snapshots()
        vix_ts = await self.futures_client.get_vix_term_structure()

        # Gap analysis for equity-index futures
        gap_analysis = {}
        for key in self._INDEX_KEYS:
            snap = snapshots.get(key)
            if snap and snap.get("prev_close") and snap.get("last"):
                gap_analysis[key] = classify_gap(snap["prev_close"], snap["last"])
            else:
                gap_analysis[key] = {"gap_pct": 0, "classification": "unknown", "gap_points": 0}

        # Cross-asset % changes
        cross_asset = self._extract_cross_asset(snapshots)

        # Overnight signal
        overnight_signal = compute_overnight_signal(
            es_gap=gap_analysis.get("es", _EMPTY_GAP),
            nq_gap=gap_analysis.get("nq", _EMPTY_GAP),
            rty_gap=gap_analysis.get("rty", _EMPTY_GAP),
            vix_structure=vix_ts,
            oil_change_pct=cross_asset.get("oil_change_pct"),
            dollar_change_pct=cross_asset.get("dollar_change_pct"),
            bond_change_pct=cross_asset.get("bond_change_pct"),
        )

        # Position alerts
        position_alerts: list[dict[str, Any]] = []
        if active_trades:
            position_alerts = check_position_exposure(active_trades, gap_analysis)

        return {
            "timestamp": datetime.now().isoformat(),
            "market_status": market_status(),
            "snapshots": snapshots,
            "gap_analysis": gap_analysis,
            "vix_term_structure": vix_ts,
            "overnight_signal": overnight_signal,
            "cross_asset": cross_asset,
            "position_alerts": position_alerts,
        }

    # ------------------------------------------------------------------
    # Internal: extract cross-asset changes from snapshots
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_cross_asset(snapshots: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "oil_change_pct": None,
            "dollar_change_pct": None,
            "bond_change_pct": None,
        }
        cl = snapshots.get("cl")
        if cl and cl.get("change_pct") is not None:
            result["oil_change_pct"] = cl["change_pct"]

        dx = snapshots.get("dx")
        if dx and dx.get("change_pct") is not None:
            result["dollar_change_pct"] = dx["change_pct"]

        zn = snapshots.get("zn")
        if zn and zn.get("change_pct") is not None:
            result["bond_change_pct"] = zn["change_pct"]

        return result


# =====================================================================
# Gap Analysis
# =====================================================================

_EMPTY_GAP: dict[str, Any] = {"gap_pct": 0, "classification": "unknown", "gap_points": 0}


def classify_gap(prior_close: float, current: float) -> dict[str, Any]:
    """Classify the overnight gap between prior close and current price.

    Returns::
        {
            "gap_pct": -0.008,
            "gap_points": -42.50,
            "classification": "gap_down",
            "prior_close": 5260.75,
            "current": 5218.25,
        }

    Classification buckets:
        large_gap_up    >  +1.0%
        gap_up          >  +0.5%
        flat            ±  0.5%
        gap_down        < -0.5%
        large_gap_down  < -1.0%
    """
    if prior_close <= 0:
        return {
            "gap_pct": 0,
            "gap_points": 0,
            "classification": "unknown",
            "prior_close": prior_close,
            "current": current,
        }

    gap_pct = (current - prior_close) / prior_close

    if gap_pct > _LARGE_GAP:
        classification = "large_gap_up"
    elif gap_pct > _MEDIUM_GAP:
        classification = "gap_up"
    elif gap_pct > -_MEDIUM_GAP:
        classification = "flat"
    elif gap_pct > -_LARGE_GAP:
        classification = "gap_down"
    else:
        classification = "large_gap_down"

    return {
        "gap_pct": round(gap_pct, 4),
        "gap_points": round(current - prior_close, 2),
        "classification": classification,
        "prior_close": round(prior_close, 2),
        "current": round(current, 2),
    }


# =====================================================================
# Overnight Regime Signal
# =====================================================================

def compute_overnight_signal(
    *,
    es_gap: dict[str, Any],
    nq_gap: dict[str, Any],
    rty_gap: dict[str, Any],
    vix_structure: dict[str, Any],
    oil_change_pct: float | None = None,
    dollar_change_pct: float | None = None,
    bond_change_pct: float | None = None,
) -> dict[str, Any]:
    """Compute overnight regime signal from futures data.

    Returns::
        {
            "signal": "BULLISH" | "NEUTRAL" | "BEARISH",
            "conviction": "HIGH" | "MODERATE" | "LOW",
            "direction_score": -0.65,
            "gap_risk": "large_gap_down",
            "vix_term_structure": "BACKWARDATION",
            "cross_asset_confirmation": "CONFIRMING" | "MIXED" | "DIVERGING",
        }
    """
    # Direction scores weighted by market-cap representation
    es_score = _gap_to_score(es_gap.get("gap_pct", 0))   # 0.40
    nq_score = _gap_to_score(nq_gap.get("gap_pct", 0))   # 0.30
    rty_score = _gap_to_score(rty_gap.get("gap_pct", 0))  # 0.20

    # VIX term structure signal
    structure = (vix_structure.get("structure") or "unknown").lower()
    if structure == "contango":
        vix_score = 0.6    # complacent / mildly bullish
    elif structure == "backwardation":
        vix_score = -0.8   # fear premium / bearish
    else:
        vix_score = 0.0

    # Weighted composite (−1 … +1)
    direction_score = (
        es_score * 0.40
        + nq_score * 0.30
        + rty_score * 0.20
        + vix_score * 0.10
    )

    # ── Cross-asset confirmation ────────────────────────────────
    confirming = 0
    diverging = 0

    if oil_change_pct is not None:
        # Oil generally moves with risk appetite
        if (direction_score > 0 and oil_change_pct > 0.005) or \
           (direction_score < 0 and oil_change_pct < -0.005):
            confirming += 1
        elif abs(oil_change_pct) > 0.005:
            diverging += 1

    if dollar_change_pct is not None:
        # Dollar typically inverse to equities
        if (direction_score > 0 and dollar_change_pct < -0.002) or \
           (direction_score < 0 and dollar_change_pct > 0.002):
            confirming += 1
        elif abs(dollar_change_pct) > 0.002:
            diverging += 1

    if bond_change_pct is not None:
        # Treasuries: price up = yield down = risk-off = equities down
        if (direction_score > 0 and bond_change_pct < 0) or \
           (direction_score < 0 and bond_change_pct > 0):
            confirming += 1
        elif abs(bond_change_pct) > 0.001:
            diverging += 1

    if confirming >= 2:
        cross_asset = "CONFIRMING"
    elif diverging >= 2:
        cross_asset = "DIVERGING"
    else:
        cross_asset = "MIXED"

    # ── Signal classification ───────────────────────────────────
    if direction_score > 0.3:
        signal = "BULLISH"
    elif direction_score < -0.3:
        signal = "BEARISH"
    else:
        signal = "NEUTRAL"

    # ── Conviction ──────────────────────────────────────────────
    agreement = (
        abs(es_score) > 0.3
        and abs(nq_score) > 0.3
        and (es_score * nq_score > 0)   # same direction
    )
    if agreement and cross_asset == "CONFIRMING":
        conviction = "HIGH"
    elif agreement or cross_asset == "CONFIRMING":
        conviction = "MODERATE"
    else:
        conviction = "LOW"

    return {
        "signal": signal,
        "conviction": conviction,
        "direction_score": round(direction_score, 3),
        "gap_risk": es_gap.get("classification", "unknown"),
        "vix_term_structure": structure.upper() if structure != "unknown" else "UNKNOWN",
        "cross_asset_confirmation": cross_asset,
    }


def _gap_to_score(gap_pct: float) -> float:
    """Convert a gap percentage to a −1 … +1 directional score.

    Derived from empirical gap-to-morning-direction correlation.
    Input: gap_pct as decimal (e.g. −0.012 = −1.2%).
    """
    if gap_pct > 0.02:
        return 1.0
    if gap_pct > 0.01:
        return 0.7
    if gap_pct > 0.005:
        return 0.4
    if gap_pct > -0.005:
        return 0.0
    if gap_pct > -0.01:
        return -0.4
    if gap_pct > -0.02:
        return -0.7
    return -1.0


# =====================================================================
# Position Exposure Alerts
# =====================================================================

def check_position_exposure(
    trades: list[dict[str, Any]],
    gap_analysis: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Flag active positions threatened by overnight gaps.

    Args:
        trades:       Active trade dicts (must have ``underlying``, ``strategy``).
        gap_analysis: Gap analysis dict keyed by instrument (es, nq, …).

    Returns a list of alert dicts with severity ``"warning"`` or ``"critical"``.
    """
    futures_map: dict[str, str] = {
        "SPY": "es", "QQQ": "nq", "IWM": "rty", "DIA": "ym",
    }
    alerts: list[dict[str, Any]] = []

    for trade in trades:
        underlying = trade.get("underlying", "")
        instrument = futures_map.get(underlying)
        if instrument is None or instrument not in gap_analysis:
            continue

        gap = gap_analysis[instrument]
        gap_pct = gap.get("gap_pct", 0)
        strategy = (trade.get("strategy") or "").lower()

        impact: str | None = None
        severity: str | None = None

        # Credit / iron-condor positions — hurt by large moves either way
        if _is_credit_strategy(strategy):
            if abs(gap_pct) > 0.02:
                impact = f"Large overnight move ({gap_pct:+.1%}) threatens credit position"
                severity = "critical"
            elif abs(gap_pct) > 0.01:
                impact = f"Significant overnight move ({gap_pct:+.1%}) threatens credit position"
                severity = "critical"
            elif abs(gap_pct) > 0.005:
                impact = f"Moderate overnight move ({gap_pct:+.1%}) — monitor at open"
                severity = "warning"

        # Long / debit positions — hurt by gap against their direction
        elif _is_debit_strategy(strategy):
            if "put" in strategy and gap_pct > 0.01:
                impact = f"Gap up ({gap_pct:+.1%}) works against bearish debit position"
                severity = "warning"
            elif "call" in strategy and gap_pct < -0.01:
                impact = f"Gap down ({gap_pct:+.1%}) works against bullish debit position"
                severity = "warning"

        # Equity long positions — hurt by gap down
        elif strategy in ("equity_long", "equity", "stock_long"):
            if gap_pct < -0.02:
                impact = f"Gap down ({gap_pct:+.1%}) pressures long equity position"
                severity = "critical"
            elif gap_pct < -0.01:
                impact = f"Gap down ({gap_pct:+.1%}) pressures long equity position"
                severity = "warning"

        if impact is not None and severity is not None:
            alerts.append({
                "trade_key": trade.get("trade_key"),
                "symbol": underlying,
                "strategy": trade.get("strategy"),
                "futures_instrument": instrument,
                "gap_pct": gap_pct,
                "gap_classification": gap.get("classification"),
                "impact": impact,
                "severity": severity,
            })

    return alerts


def _is_credit_strategy(strategy: str) -> bool:
    return any(kw in strategy for kw in (
        "credit", "iron_condor", "iron_butterfly",
        "put_credit_spread", "call_credit_spread",
    ))


def _is_debit_strategy(strategy: str) -> bool:
    return any(kw in strategy for kw in (
        "debit", "put_debit", "call_debit",
        "butterfly_debit", "calendar", "diagonal",
    ))
