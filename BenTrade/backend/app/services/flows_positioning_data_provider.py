"""Flows & Positioning — Data Provider.

Fetches raw inputs needed by the flows_positioning_engine.
Uses MarketContextService for VIX and VIX3M data, plus heuristic
proxy computations for positioning signals that lack direct feeds.

Phase 1 data sources (all proxy or partial):
  MarketContextService → VIX, VIX3M (if available)
  Derived proxies      → put/call ratio, systematic allocation estimate,
                          futures net long %, short interest estimate,
                          flow direction/persistence, retail sentiment

Honesty note:
  Most inputs in Phase 1 are PROXY ESTIMATES, not direct institutional data.
  The engine and confidence system account for this.  Future phases will
  integrate CFTC COT, true ETF flow feeds, dealer gamma reports, etc.

Returns a dict with pillar-keyed sub-dicts matching the engine's expected
parameters, plus source_meta and source_errors.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.services.market_context_service import MarketContextService

logger = logging.getLogger(__name__)


def _extract_value(metric: dict[str, Any] | None) -> float | None:
    """Extract the numeric value from a MarketContextService metric envelope."""
    if metric is None:
        return None
    return metric.get("value")


class FlowsPositioningDataProvider:
    """Fetches raw data for the Flows & Positioning engine."""

    def __init__(
        self,
        market_context_service: MarketContextService,
    ) -> None:
        self.market_context = market_context_service

    async def fetch_flows_positioning_data(self) -> dict[str, Any]:
        """Fetch all flows/positioning data and return pillar-keyed input dicts.

        Fault-tolerant: upstream failures degrade gracefully; the engine
        receives None for any failed source instead of crashing.

        Returns
        -------
        dict with keys:
          positioning_data       → Pillar 1 (Positioning Pressure)
          crowding_data          → Pillar 2 (Crowding / Stretch)
          squeeze_data           → Pillar 3 (Squeeze / Unwind Risk)
          flow_data              → Pillar 4 (Flow Direction & Persistence)
          stability_data         → Pillar 5 (Positioning Stability)
          source_meta            → data provenance / freshness
          source_errors          → per-source error details
        """
        logger.info("event=flows_positioning_fetch_start")

        source_errors: dict[str, str] = {}

        # ── Fetch market context (VIX, etc.) ─────────────────────
        market_ctx: dict[str, Any] | None = None
        try:
            market_ctx = await self.market_context.get_market_context()
        except Exception as exc:
            source_errors["market_context"] = str(exc)
            logger.error(
                "event=flows_positioning_source_failed source=market_context error=%s",
                exc,
            )

        if market_ctx is None:
            market_ctx = {}

        # Extract from market context
        vix = _extract_value(market_ctx.get("vix"))
        # VIX3M may or may not exist in MarketContextService
        vix3m = _extract_value(market_ctx.get("vix3m"))

        # ── PROXY ESTIMATES ──────────────────────────────────────
        # Phase 1: derive proxy signals from available data.
        # These are explicitly labeled as proxies throughout.

        # VIX term structure ratio (VIX / VIX3M)
        # <1.0 = contango (normal), >1.0 = backwardation (stress)
        vix_term_ratio = None
        if vix is not None and vix3m is not None and vix3m > 0:
            vix_term_ratio = round(vix / vix3m, 4)

        # Put/call ratio proxy — derived from VIX level heuristic.
        # In Phase 1 we don't have actual exchange p/c ratio.
        # Heuristic: VIX 12→p/c 0.65, VIX 18→0.85, VIX 25→1.05, VIX 35→1.25
        put_call_proxy = None
        if vix is not None:
            put_call_proxy = round(0.45 + vix * 0.023, 3)

        # Systematic allocation proxy — inverse of VIX regime.
        # Low VIX → high allocation; high VIX → low allocation.
        # VIX 10→allocation 90, VIX 20→60, VIX 30→30, VIX 40→10
        systematic_proxy = None
        if vix is not None:
            systematic_proxy = round(max(5, min(95, 110 - vix * 2.5)), 1)

        # Futures net long % proxy — moderate heuristic from VIX.
        # Low VIX tends to correlate with elevated net long positioning.
        # VIX 10→net_long 80%, VIX 20→55%, VIX 30→35%, VIX 40→15%
        futures_proxy = None
        if vix is not None:
            futures_proxy = round(max(10, min(90, 100 - vix * 2.2)), 1)

        # Short interest proxy — estimated from VIX regime.
        # Low VIX → low SI; high VIX → higher SI (more hedging/shorting).
        # VIX 10→SI 1.2%, VIX 20→2.5%, VIX 30→3.8%, VIX 40→5.0%
        short_interest_proxy = None
        if vix is not None:
            short_interest_proxy = round(max(0.8, min(6.0, 0.1 + vix * 0.12)), 2)

        # Retail sentiment proxy — AAII-style estimates.
        # Low VIX → bullish retail; high VIX → bearish retail.
        retail_bull_proxy = None
        retail_bear_proxy = None
        if vix is not None:
            # VIX 10→bull 55%, VIX 20→40%, VIX 30→28%, VIX 40→20%
            retail_bull_proxy = round(max(15, min(60, 65 - vix * 1.1)), 1)
            # VIX 10→bear 20%, VIX 20→30%, VIX 30→42%, VIX 40→52%
            retail_bear_proxy = round(max(15, min(55, 10 + vix * 1.05)), 1)

        # Flow direction proxy — inflow-biased when VIX is low.
        # 50 = neutral, >50 = inflow, <50 = outflow.
        flow_direction_proxy = None
        if vix is not None:
            # VIX 10→flow 72, VIX 18→58, VIX 25→42, VIX 35→28
            flow_direction_proxy = round(max(15, min(85, 90 - vix * 1.8)), 1)

        # Flow persistence proxies (5d and 20d) — moderate persistence assumed.
        # Lower VIX → more persistent inflows; higher VIX → erratic flows.
        flow_persistence_5d = None
        flow_persistence_20d = None
        flow_volatility_proxy = None
        if vix is not None:
            flow_persistence_5d = round(max(20, min(85, 95 - vix * 2.2)), 1)
            flow_persistence_20d = round(max(15, min(80, 88 - vix * 2.0)), 1)
            # Flow volatility — lower VIX → lower flow vol
            flow_volatility_proxy = round(max(10, min(90, vix * 2.5 - 10)), 1)

        # Inflow/outflow balance and follow-through (mirrors flow direction)
        inflow_balance_proxy = flow_direction_proxy
        follow_through_proxy = None
        if vix is not None and flow_direction_proxy is not None:
            # Follow-through is slightly dampened version of flow direction
            follow_through_proxy = round(max(20, min(80, flow_direction_proxy * 0.85 + 8)), 1)

        # ── Build pillar input dicts ─────────────────────────────

        positioning_data = {
            "put_call_ratio": put_call_proxy,
            "vix": vix,
            "retail_bull_pct": retail_bull_proxy,
            "systematic_allocation": systematic_proxy,
            "futures_net_long_pct": futures_proxy,
        }

        crowding_data = {
            "futures_net_long_pct": futures_proxy,
            "put_call_ratio": put_call_proxy,
            "retail_bull_pct": retail_bull_proxy,
            "retail_bear_pct": retail_bear_proxy,
            "vix": vix,
            "short_interest_pct": short_interest_proxy,
        }

        squeeze_data = {
            "short_interest_pct": short_interest_proxy,
            "futures_net_long_pct": futures_proxy,
            "put_call_ratio": put_call_proxy,
            "vix": vix,
            "vix_term_structure": vix_term_ratio,
        }

        flow_data = {
            "flow_direction_score": flow_direction_proxy,
            "flow_persistence_5d": flow_persistence_5d,
            "flow_persistence_20d": flow_persistence_20d,
            "inflow_outflow_balance": inflow_balance_proxy,
            "follow_through_score": follow_through_proxy,
        }

        stability_data = {
            "vix": vix,
            "vix_term_structure": vix_term_ratio,
            "futures_net_long_pct": futures_proxy,
            "flow_direction_score": flow_direction_proxy,
            "flow_volatility": flow_volatility_proxy,
            "put_call_ratio": put_call_proxy,
        }

        # Count available vs total proxy signals
        all_proxies = [
            put_call_proxy, systematic_proxy, futures_proxy,
            short_interest_proxy, retail_bull_proxy, retail_bear_proxy,
            flow_direction_proxy, flow_persistence_5d, flow_persistence_20d,
            flow_volatility_proxy, follow_through_proxy,
        ]
        proxy_avail = sum(1 for p in all_proxies if p is not None)
        total_proxy = len(all_proxies)

        logger.info(
            "event=flows_positioning_fetch_complete vix=%s vix3m=%s "
            "vix_term=%s proxies_available=%d/%d source_errors=%d",
            vix, vix3m, vix_term_ratio, proxy_avail, total_proxy,
            len(source_errors),
        )
        if source_errors:
            logger.warning(
                "event=flows_positioning_partial_failure errors=%s",
                source_errors,
            )

        source_meta = {
            "market_context_generated_at": market_ctx.get("context_generated_at"),
            "vix_source": market_ctx.get("vix", {}).get("source"),
            "vix_freshness": market_ctx.get("vix", {}).get("freshness"),
            "vix_value": vix,
            "vix3m_value": vix3m,
            "vix_term_structure": vix_term_ratio,
            "proxy_source_count": total_proxy,
            "proxy_available_count": proxy_avail,
            "has_direct_flow_data": False,  # Phase 1: always False
            "has_futures_positioning": False,  # Phase 1: always False (proxy only)
            "unique_upstream_count": 1,  # Phase 1: VIX is the only upstream source
            "data_note": (
                "Phase 1 — all positioning/flow signals are VIX-derived proxies. "
                "Confidence is reduced accordingly. Future phases will integrate "
                "CFTC COT, ETF fund flows, dealer gamma reports, and AAII surveys."
            ),
        }

        return {
            "positioning_data": positioning_data,
            "crowding_data": crowding_data,
            "squeeze_data": squeeze_data,
            "flow_data": flow_data,
            "stability_data": stability_data,
            "source_meta": source_meta,
            "source_errors": source_errors,
        }
