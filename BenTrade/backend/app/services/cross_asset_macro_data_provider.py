"""Cross-Asset / Macro Confirmation — Data Provider.

Fetches all raw inputs needed by the cross-asset scoring engine.
Uses MarketContextService for metrics already available (VIX, yields, oil, USD)
and FRED directly for additional series (gold, copper, credit spreads).

Data sources:
  MarketContextService → VIX, 10Y/2Y yields, fed funds, oil, USD, yield curve spread
  FRED NASDAQQGLDI      → Gold price index (LBMA-based, daily)
  FRED PCOPPUSDM        → Copper price (monthly, LME)
  FRED BAMLC0A0CM       → IG OAS spread (ICE BofA)
  FRED BAMLH0A0HYM2     → HY OAS spread (ICE BofA)

Returns a dict with pillar-keyed sub-dicts:
  rates_data, dollar_commodity_data, credit_data, defensive_growth_data, coherence_data
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

from app.clients.fred_client import FredClient
from app.services.data_quality_utils import (
    build_data_quality_summary,
    days_stale,
    extract_value as _extract_value,
    staleness_tier,
)
from app.services.market_context_service import MarketContextService
from app.utils.market_hours import market_status

logger = logging.getLogger(__name__)

# Additional FRED series IDs not in MarketContextService
# NOTE: GOLDAMGBD228NLBM (London PM Gold Fixing) was removed from FRED.
# Replaced with NASDAQQGLDI — NASDAQ Gold FLOWS103 Price Index (daily),
# which tracks the LBMA Gold Price in USD and provides daily observations.
_FRED_GOLD = "NASDAQQGLDI"            # NASDAQ Gold Price Index (LBMA-based, daily, USD)
_FRED_COPPER = "PCOPPUSDM"            # Global copper price (USD/metric ton, monthly)
_FRED_IG_SPREAD = "BAMLC0A0CM"        # ICE BofA US Corp IG OAS
_FRED_HY_SPREAD = "BAMLH0A0HYM2"     # ICE BofA US HY OAS


# _extract_value imported from data_quality_utils


class CrossAssetMacroDataProvider:
    """Fetches raw data for the Cross-Asset / Macro Confirmation engine."""

    def __init__(
        self,
        market_context_service: MarketContextService,
        fred_client: FredClient,
        futures_client: Any | None = None,
    ) -> None:
        self.market_context = market_context_service
        self.fred = fred_client
        self.futures_client = futures_client

    async def fetch_cross_asset_data(self) -> dict[str, Any]:
        """Fetch all cross-asset data and return pillar-keyed input dicts.

        Fault-tolerant: individual upstream failures degrade gracefully.
        The engine receives None for any failed source instead of crashing.

        Returns
        -------
        dict with keys:
          rates_data: inputs for Pillar 1 (Rates & Yield Curve)
          dollar_commodity_data: inputs for Pillar 2 (Dollar & Commodity)
          credit_data: inputs for Pillar 3 (Credit & Risk Appetite)
          defensive_growth_data: inputs for Pillar 4 (Defensive vs Growth)
          coherence_data: inputs for Pillar 5 (Macro Coherence)
          source_meta: data freshness / source metadata
          source_errors: per-source error details (empty if all OK)
        """
        logger.info("event=cross_asset_fetch_start")

        # ── Fetch with partial-failure tolerance ─────────────────
        # Each source is wrapped so a single HTTP 400 / timeout does
        # NOT crash the entire gather.  The engine already handles None.
        source_errors: dict[str, str] = {}

        async def _safe_market_ctx() -> dict[str, Any] | None:
            try:
                return await self.market_context.get_market_context()
            except Exception as exc:
                source_errors["market_context"] = str(exc)
                logger.error(
                    "event=cross_asset_source_failed source=market_context error=%s",
                    exc,
                )
                return None

        async def _safe_fred(series_id: str, label: str) -> dict[str, Any] | None:
            try:
                logger.info(
                    "event=cross_asset_fred_fetch_start source=%s series_id=%s",
                    label, series_id,
                )
                result = await self.fred.get_series_with_date(series_id)
                if result is not None:
                    logger.info(
                        "event=cross_asset_fred_fetch_ok source=%s series_id=%s "
                        "value=%s observation_date=%s",
                        label, series_id, result.get("value"),
                        result.get("observation_date"),
                    )
                else:
                    logger.warning(
                        "event=cross_asset_fred_fetch_empty source=%s series_id=%s "
                        "reason=no_observations_returned",
                        label, series_id,
                    )
                return result
            except Exception as exc:
                source_errors[label] = str(exc)
                logger.error(
                    "event=cross_asset_source_failed source=%s series=%s error=%s",
                    label, series_id, exc,
                )
                return None

        market_ctx, gold_obs, copper_obs, ig_obs, hy_obs = await asyncio.gather(
            _safe_market_ctx(),
            _safe_fred(_FRED_GOLD, "fred_gold"),
            _safe_fred(_FRED_COPPER, "fred_copper"),
            _safe_fred(_FRED_IG_SPREAD, "fred_ig_spread"),
            _safe_fred(_FRED_HY_SPREAD, "fred_hy_spread"),
        )

        # If market_context failed entirely, use empty dict so
        # downstream extraction safely returns None for each metric.
        if market_ctx is None:
            market_ctx = {}

        # Extract values from market context metric envelopes
        ten_year = _extract_value(market_ctx.get("ten_year_yield"))
        two_year = _extract_value(market_ctx.get("two_year_yield"))
        fed_funds = _extract_value(market_ctx.get("fed_funds_rate"))
        vix = _extract_value(market_ctx.get("vix"))
        oil = _extract_value(market_ctx.get("oil_wti"))
        usd = _extract_value(market_ctx.get("usd_index"))
        yield_spread = _extract_value(market_ctx.get("yield_curve_spread"))
        cpi_yoy = _extract_value(market_ctx.get("cpi_yoy"))

        # Extract FRED observation values
        gold = gold_obs["value"] if gold_obs else None
        copper = copper_obs["value"] if copper_obs else None
        ig_spread = ig_obs["value"] if ig_obs else None
        hy_spread = hy_obs["value"] if hy_obs else None

        # ── Live futures overlay (market/extended hours only) ────
        # During trading hours, real-time futures prices for oil (CL),
        # USD (DX), and 10Y yield (TNX) are fresher than FRED's 1-day
        # lagged observations.  We try each independently and tag the source.
        futures_sources: dict[str, str] = {}  # metric → "futures_live" | "fred"
        _status = market_status()

        if _status in ("open", "extended") and self.futures_client is not None:
            today_iso = date.today().isoformat()

            # Oil: CL=F → DCOILWTICO fallback
            try:
                cl_snap = await self.futures_client.get_snapshot("cl")
                if cl_snap and cl_snap.get("last") is not None:
                    oil = cl_snap["last"]
                    futures_sources["oil_wti"] = "futures_live"
                    # Override the observation date / staleness to today
                    oil_date = today_iso
                    logger.info(
                        "event=futures_overlay metric=oil_wti price=%.2f source=CL=F",
                        oil,
                    )
            except Exception as exc:
                logger.warning("event=futures_overlay_failed metric=oil_wti error=%s", exc)

            # USD: DX-Y.NYB → DTWEXBGS fallback
            try:
                dx_snap = await self.futures_client.get_snapshot("dx")
                if dx_snap and dx_snap.get("last") is not None:
                    usd = dx_snap["last"]
                    futures_sources["usd_index"] = "futures_live"
                    usd_date = today_iso
                    logger.info(
                        "event=futures_overlay metric=usd_index price=%.2f source=DX-Y.NYB",
                        usd,
                    )
            except Exception as exc:
                logger.warning("event=futures_overlay_failed metric=usd_index error=%s", exc)

            # 10Y yield: ^TNX → DGS10 fallback
            try:
                tnx_snap = await self.futures_client.get_snapshot("tnx")
                if tnx_snap and tnx_snap.get("last") is not None:
                    ten_year = tnx_snap["last"]
                    futures_sources["ten_year_yield"] = "futures_live"
                    ten_y_date = today_iso
                    # Recompute yield curve spread if we have both yields
                    if two_year is not None:
                        yield_spread = round(ten_year - two_year, 4)
                    logger.info(
                        "event=futures_overlay metric=ten_year_yield value=%.3f source=^TNX",
                        ten_year,
                    )
            except Exception as exc:
                logger.warning("event=futures_overlay_failed metric=ten_year_yield error=%s", exc)

        # Log data availability
        available = sum(1 for v in [ten_year, two_year, fed_funds, vix, oil,
                                     usd, yield_spread, gold, copper,
                                     ig_spread, hy_spread, cpi_yoy] if v is not None)
        logger.info(
            "event=cross_asset_fetch_complete available=%d/12 source_errors=%d "
            "vix=%s ten_year=%s two_year=%s oil=%s usd=%s gold=%s copper=%s "
            "ig_spread=%s hy_spread=%s",
            available, len(source_errors), vix, ten_year, two_year, oil, usd,
            gold, copper, ig_spread, hy_spread,
        )
        if source_errors:
            logger.warning(
                "event=cross_asset_partial_failure errors=%s", source_errors,
            )

        # -- Build pillar input dicts --

        # Pillar 1: Rates & Yield Curve (25%)
        rates_data = {
            "ten_year_yield": ten_year,
            "two_year_yield": two_year,
            "yield_curve_spread": yield_spread,
            "fed_funds_rate": fed_funds,
        }

        # Pillar 2: Dollar & Commodity (20%)
        dollar_commodity_data = {
            "usd_index": usd,
            "oil_wti": oil,
            "gold_price": gold,
            "copper_price": copper,
        }

        # Pillar 3: Credit & Risk Appetite (25%)
        credit_data = {
            "ig_spread": ig_spread,
            "hy_spread": hy_spread,
            "vix": vix,
        }

        # Pillar 4: Defensive vs Growth Alignment (15%)
        # Second-pass: VIX and hy_spread removed — Pillar 4 is VIX-free
        defensive_growth_data = {
            "gold_price": gold,
            "ten_year_yield": ten_year,
            "copper_price": copper,
        }

        # Pillar 5: Macro Coherence (15%)
        # Meta-pillar that checks consistency across pillar inputs
        coherence_data = {
            "vix": vix,
            "yield_curve_spread": yield_spread,
            "ig_spread": ig_spread,
            "hy_spread": hy_spread,
            "usd_index": usd,
            "oil_wti": oil,
            "gold_price": gold,
            "copper_price": copper,
            "cpi_yoy": cpi_yoy,
        }

        # Compute staleness for ALL FRED sources
        gold_date = gold_obs.get("observation_date") if gold_obs else None
        copper_date = copper_obs.get("observation_date") if copper_obs else None
        ig_date = ig_obs.get("observation_date") if ig_obs else None
        hy_date = hy_obs.get("observation_date") if hy_obs else None

        gold_age = days_stale(gold_date)
        copper_age = days_stale(copper_date)
        ig_age = days_stale(ig_date)
        hy_age = days_stale(hy_date)

        # Also compute staleness for market-context FRED metrics
        # Only use market_ctx dates if not already overridden by live futures
        if "ten_year_yield" not in futures_sources:
            ten_y_date = market_ctx.get("ten_year_yield", {}).get("observation_date") if isinstance(market_ctx.get("ten_year_yield"), dict) else None
        two_y_date = market_ctx.get("two_year_yield", {}).get("observation_date") if isinstance(market_ctx.get("two_year_yield"), dict) else None
        if "oil_wti" not in futures_sources:
            oil_date = market_ctx.get("oil_wti", {}).get("observation_date") if isinstance(market_ctx.get("oil_wti"), dict) else None
        if "usd_index" not in futures_sources:
            usd_date = market_ctx.get("usd_index", {}).get("observation_date") if isinstance(market_ctx.get("usd_index"), dict) else None
        ff_date = market_ctx.get("fed_funds_rate", {}).get("observation_date") if isinstance(market_ctx.get("fed_funds_rate"), dict) else None

        ten_y_age = days_stale(ten_y_date)
        two_y_age = days_stale(two_y_date)
        oil_age = days_stale(oil_date)
        usd_age = days_stale(usd_date)
        ff_age = days_stale(ff_date)

        # Log stale daily series (> 3 days) at WARNING, monthly at INFO
        _daily_series_staleness = [
            ("NASDAQQGLDI", gold_age, gold_date),
            ("BAMLC0A0CM", ig_age, ig_date),
            ("BAMLH0A0HYM2", hy_age, hy_date),
            ("DGS10", ten_y_age, ten_y_date),
            ("DGS2", two_y_age, two_y_date),
            ("DCOILWTICO", oil_age, oil_date),
            ("DTWEXBGS", usd_age, usd_date),
            ("DFF", ff_age, ff_date),
        ]
        for series_name, age, obs_date in _daily_series_staleness:
            if age is not None and age > 3:
                logger.warning(
                    "event=fred_data_stale series=%s age_days=%d tier=%s observation_date=%s",
                    series_name, age, staleness_tier(age), obs_date,
                )

        # Monthly series logged at INFO (high staleness is expected)
        if copper_age is not None and copper_age > 5:
            logger.info(
                "event=cross_asset_copper_stale days_stale=%d observation_date=%s",
                copper_age, copper_date,
            )

        # Build staleness summary for source_meta
        staleness_summary: dict[str, dict[str, Any]] = {}
        for series_name, age, obs_date in [
            ("NASDAQQGLDI", gold_age, gold_date),
            ("PCOPPUSDM", copper_age, copper_date),
            ("BAMLC0A0CM", ig_age, ig_date),
            ("BAMLH0A0HYM2", hy_age, hy_date),
            ("DGS10", ten_y_age, ten_y_date),
            ("DGS2", two_y_age, two_y_date),
            ("DCOILWTICO", oil_age, oil_date),
            ("DTWEXBGS", usd_age, usd_date),
            ("DFF", ff_age, ff_date),
        ]:
            staleness_summary[series_name] = {
                "age_days": age,
                "tier": staleness_tier(age),
                "observation_date": obs_date,
            }

        # Source metadata for UI freshness display
        # Build per-metric data quality tags from market context envelopes
        _quality_metrics = {
            k: market_ctx.get(k)
            for k in ("ten_year_yield", "two_year_yield", "fed_funds_rate",
                      "vix", "oil_wti", "usd_index", "yield_curve_spread", "cpi_yoy")
        }
        data_quality = build_data_quality_summary(_quality_metrics)

        source_meta: dict[str, Any] = {
            "data_quality": data_quality,
            "market_context_generated_at": market_ctx.get("context_generated_at"),
            "vix_source": market_ctx.get("vix", {}).get("source"),
            "vix_freshness": market_ctx.get("vix", {}).get("freshness"),
            "futures_sources": futures_sources,  # which metrics used live futures
            "fred_gold_date": gold_date,
            "fred_copper_date": copper_date,
            "fred_copper_days_stale": copper_age,
            "fred_ig_date": ig_date,
            "fred_hy_date": hy_date,
            "staleness": staleness_summary,
            # FRED source honesty — frequency and delay metadata
            "fred_source_detail": {
                _FRED_GOLD: {
                    "series_id": _FRED_GOLD,
                    "label": "NASDAQ Gold FLOWS103 Price Index (LBMA-based)",
                    "frequency": "daily",
                    "typical_delay": "1 business day",
                    "unit": "USD (gold spot proxy)",
                    "notes": (
                        "Tracks LBMA Gold Price via NASDAQ index. "
                        "Replaced discontinued GOLDAMGBD228NLBM."
                    ),
                },
                _FRED_COPPER: {
                    "series_id": _FRED_COPPER,
                    "label": "Global Copper Price (LME)",
                    "frequency": "monthly",
                    "typical_delay": "up to 30 days (monthly average)",
                    "unit": "USD/metric ton",
                    "days_stale": copper_age,
                    "stale_warning": (
                        "Monthly series — may not reflect recent price "
                        "movements. Treat as slow proxy for growth."
                    ),
                },
                _FRED_IG_SPREAD: {
                    "series_id": _FRED_IG_SPREAD,
                    "label": "ICE BofA US Corporate IG OAS",
                    "frequency": "daily",
                    "typical_delay": "1-2 business days",
                    "unit": "percent (OAS)",
                },
                _FRED_HY_SPREAD: {
                    "series_id": _FRED_HY_SPREAD,
                    "label": "ICE BofA US High Yield OAS",
                    "frequency": "daily",
                    "typical_delay": "1-2 business days",
                    "unit": "percent (OAS)",
                },
            },
        }

        return {
            "rates_data": rates_data,
            "dollar_commodity_data": dollar_commodity_data,
            "credit_data": credit_data,
            "defensive_growth_data": defensive_growth_data,
            "coherence_data": coherence_data,
            "source_meta": source_meta,
            "source_errors": source_errors,
        }
