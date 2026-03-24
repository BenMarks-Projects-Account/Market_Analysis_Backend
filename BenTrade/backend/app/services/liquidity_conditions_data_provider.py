"""Liquidity & Financial Conditions — Data Provider.

Fetches raw inputs needed by the liquidity_conditions_engine.
Uses MarketContextService for VIX, yields, USD index, and the
FRED client for credit-spread series not in the base market context.

Data sources
------------
Direct (FRED / MarketContextService):
  VIX, 10Y yield (DGS10), 2Y yield (DGS2), Fed Funds (DFF),
  USD Index (DTWEXBGS), yield curve spread (10Y-2Y).

Direct (FRED extra series via FredClient):
  IG credit spread (BAMLC0A0CM), HY credit spread (BAMLH0A0HYM2).

Proxy / Derived:
  FCI composite (from VIX + credit + rates), funding stress proxy
  (from VIX + fed funds heuristic), policy pressure (from fed funds
  vs neutral estimate).

Returns a dict with pillar-keyed sub-dicts matching the engine's
expected parameters, plus source_meta and source_errors.
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.data_quality_utils import (
    build_data_quality_summary,
    days_stale,
    extract_value as _extract_value,
    staleness_tier,
)
from app.services.market_context_service import MarketContextService

logger = logging.getLogger(__name__)


# _extract_value imported from data_quality_utils


def _extract_source(metric: dict[str, Any] | float | int | None) -> str | None:
    """Extract the source from a MarketContextService metric envelope.

    Returns None for raw scalars (no source metadata available).
    """
    if isinstance(metric, dict):
        return metric.get("source")
    return None


def _extract_freshness(metric: dict[str, Any] | float | int | None) -> str | None:
    """Extract the freshness from a MarketContextService metric envelope.

    Returns None for raw scalars (no freshness metadata available).
    """
    if isinstance(metric, dict):
        return metric.get("freshness")
    return None


class LiquidityConditionsDataProvider:
    """Fetches raw data for the Liquidity & Financial Conditions engine."""

    def __init__(
        self,
        market_context_service: MarketContextService,
    ) -> None:
        self.market_context = market_context_service

    async def fetch_liquidity_conditions_data(self) -> dict[str, Any]:
        """Fetch all liquidity/conditions data and return pillar-keyed input dicts.

        Fault-tolerant: upstream failures degrade gracefully; the engine
        receives None for any failed source instead of crashing.

        Returns
        -------
        dict with keys:
          rates_data       → Pillar 1 (Rates & Policy Pressure)
          conditions_data  → Pillar 2 (Financial Conditions Tightness)
          credit_data      → Pillar 3 (Credit & Funding Stress)
          dollar_data      → Pillar 4 (Dollar / Global Liquidity)
          stability_data   → Pillar 5 (Liquidity Stability & Fragility)
          source_meta      → data provenance / freshness
          source_errors    → per-source error details
        """
        logger.info("event=liquidity_conditions_fetch_start")

        source_errors: dict[str, str] = {}

        # ── Fetch market context (VIX, yields, rates, USD, etc.) ─
        market_ctx: dict[str, Any] | None = None
        try:
            market_ctx = await self.market_context.get_market_context()
        except Exception as exc:
            source_errors["market_context"] = str(exc)
            logger.error(
                "event=liquidity_source_failed source=market_context error=%s",
                exc,
            )

        if market_ctx is None:
            market_ctx = {}

        # Extract from market context — per-metric safe extraction
        def _safe_extract(key: str) -> float | None:
            """Extract value from market context, logging failures individually."""
            try:
                return _extract_value(market_ctx.get(key))
            except Exception as exc:
                source_errors[f"extract_{key}"] = str(exc)
                logger.warning(
                    "event=liquidity_metric_extract_failed metric=%s "
                    "raw_type=%s error=%s",
                    key, type(market_ctx.get(key)).__name__, exc,
                )
                return None

        vix = _safe_extract("vix")
        ten_y = _safe_extract("ten_year_yield")
        two_y = _safe_extract("two_year_yield")
        fed_funds = _safe_extract("fed_funds_rate")
        dxy = _safe_extract("usd_index")
        curve_spread = _safe_extract("yield_curve_spread")

        # ── Fetch credit spreads from FRED (not in base market context) ─
        ig_spread: float | None = None
        hy_spread: float | None = None

        try:
            ig_obs = await self.market_context.fred.get_series_with_date("BAMLC0A0CM")
            ig_spread = ig_obs["value"] if ig_obs else None
        except Exception as exc:
            ig_obs = None
            source_errors["ig_spread_BAMLC0A0CM"] = str(exc)
            logger.warning(
                "event=liquidity_source_failed source=BAMLC0A0CM error=%s", exc,
            )

        try:
            hy_obs = await self.market_context.fred.get_series_with_date("BAMLH0A0HYM2")
            hy_spread = hy_obs["value"] if hy_obs else None
        except Exception as exc:
            hy_obs = None
            source_errors["hy_spread_BAMLH0A0HYM2"] = str(exc)
            logger.warning(
                "event=liquidity_source_failed source=BAMLH0A0HYM2 error=%s", exc,
            )

        # ── Build pillar input dicts ─────────────────────────────

        rates_data = {
            "two_year_yield": two_y,
            "ten_year_yield": ten_y,
            "fed_funds_rate": fed_funds,
            "yield_curve_spread": curve_spread,
        }

        conditions_data = {
            "vix": vix,
            "ig_spread": ig_spread,
            "hy_spread": hy_spread,
            "two_year_yield": two_y,
            "ten_year_yield": ten_y,
            "yield_curve_spread": curve_spread,
        }

        credit_data = {
            "ig_spread": ig_spread,
            "hy_spread": hy_spread,
            "vix": vix,
            "fed_funds_rate": fed_funds,
            "two_year_yield": two_y,
        }

        dollar_data = {
            "dxy_level": dxy,
            "vix": vix,
        }

        stability_data = {
            "vix": vix,
            "ig_spread": ig_spread,
            "hy_spread": hy_spread,
            "two_year_yield": two_y,
            "dxy_level": dxy,
            "yield_curve_spread": curve_spread,
        }

        # ── Source meta ──────────────────────────────────────────
        has_credit = ig_spread is not None or hy_spread is not None
        has_funding = vix is not None and fed_funds is not None

        # Count proxies vs direct
        proxy_count = 0
        stale_count = 0
        source_detail: dict[str, dict[str, Any]] = {}

        for name, metric_key in [
            ("vix", "vix"),
            ("ten_year_yield", "ten_year_yield"),
            ("two_year_yield", "two_year_yield"),
            ("fed_funds_rate", "fed_funds_rate"),
            ("usd_index", "usd_index"),
            ("yield_curve_spread", "yield_curve_spread"),
        ]:
            metric = market_ctx.get(metric_key)
            source_detail[name] = {
                "value": _extract_value(metric),
                "source": _extract_source(metric),
                "freshness": _extract_freshness(metric),
            }
            if metric and _extract_freshness(metric) in ("stale", "very_stale"):
                stale_count += 1

        source_detail["ig_spread"] = {
            "value": ig_spread,
            "source": "FRED BAMLC0A0CM",
            "freshness": "eod" if ig_spread is not None else "unavailable",
        }
        source_detail["hy_spread"] = {
            "value": hy_spread,
            "source": "FRED BAMLH0A0HYM2",
            "freshness": "eod" if hy_spread is not None else "unavailable",
        }

        # ── Staleness tracking for all FRED-sourced metrics ──────
        ig_date = ig_obs.get("observation_date") if ig_obs else None
        hy_date = hy_obs.get("observation_date") if hy_obs else None
        ig_age = days_stale(ig_date)
        hy_age = days_stale(hy_date)

        # Market context FRED metrics
        ten_y_date = market_ctx.get("ten_year_yield", {}).get("observation_date") if isinstance(market_ctx.get("ten_year_yield"), dict) else None
        two_y_date = market_ctx.get("two_year_yield", {}).get("observation_date") if isinstance(market_ctx.get("two_year_yield"), dict) else None
        ff_date = market_ctx.get("fed_funds_rate", {}).get("observation_date") if isinstance(market_ctx.get("fed_funds_rate"), dict) else None
        usd_date = market_ctx.get("usd_index", {}).get("observation_date") if isinstance(market_ctx.get("usd_index"), dict) else None

        ten_y_age = days_stale(ten_y_date)
        two_y_age = days_stale(two_y_date)
        ff_age = days_stale(ff_date)
        usd_age = days_stale(usd_date)

        # Log stale daily series (> 3 days)
        for series_name, age, obs_date in [
            ("BAMLC0A0CM", ig_age, ig_date),
            ("BAMLH0A0HYM2", hy_age, hy_date),
            ("DGS10", ten_y_age, ten_y_date),
            ("DGS2", two_y_age, two_y_date),
            ("DFF", ff_age, ff_date),
            ("DTWEXBGS", usd_age, usd_date),
        ]:
            if age is not None and age > 3:
                logger.warning(
                    "event=fred_data_stale series=%s age_days=%d tier=%s observation_date=%s",
                    series_name, age, staleness_tier(age), obs_date,
                )

        staleness_summary: dict[str, dict[str, Any]] = {}
        for series_name, age, obs_date in [
            ("BAMLC0A0CM", ig_age, ig_date),
            ("BAMLH0A0HYM2", hy_age, hy_date),
            ("DGS10", ten_y_age, ten_y_date),
            ("DGS2", two_y_age, two_y_date),
            ("DFF", ff_age, ff_date),
            ("DTWEXBGS", usd_age, usd_date),
        ]:
            staleness_summary[series_name] = {
                "age_days": age,
                "tier": staleness_tier(age),
                "observation_date": obs_date,
            }

        # Proxy signals: FCI composite, funding stress proxy
        proxy_count = 2  # FCI proxy and funding stress proxy are always proxy

        total_signals = 8  # vix, 10y, 2y, ff, dxy, curve, ig, hy
        direct_available = sum(
            1 for v in [vix, ten_y, two_y, fed_funds, dxy, curve_spread, ig_spread, hy_spread]
            if v is not None
        )

        logger.info(
            "event=liquidity_conditions_fetch_complete direct_available=%d/%d "
            "ig_spread=%s hy_spread=%s vix=%s source_errors=%d",
            direct_available, total_signals,
            ig_spread, hy_spread, vix,
            len(source_errors),
        )
        if source_errors:
            logger.warning(
                "event=liquidity_conditions_partial_failure errors=%s",
                source_errors,
            )

        # Build per-metric data quality tags from market context envelopes
        _quality_metrics = {
            k: market_ctx.get(k)
            for k in ("vix", "ten_year_yield", "two_year_yield", "fed_funds_rate",
                      "usd_index", "yield_curve_spread")
        }
        data_quality = build_data_quality_summary(_quality_metrics)

        source_meta = {
            "data_quality": data_quality,
            "market_context_generated_at": market_ctx.get("context_generated_at"),
            "source_detail": source_detail,
            "direct_signals_available": direct_available,
            "direct_signals_total": total_signals,
            "proxy_source_count": proxy_count,
            "stale_source_count": stale_count,
            "has_credit_spreads": has_credit,
            "has_funding_data": has_funding,
            "staleness": staleness_summary,
            "data_note": (
                "Most rate/credit/dollar data is direct from FRED. "
                "FCI composite and funding stress are proxy estimates. "
                "True FCI (e.g., Chicago Fed NFCI) and direct SOFR/repo "
                "data not yet integrated."
            ),
        }

        return {
            "rates_data": rates_data,
            "conditions_data": conditions_data,
            "credit_data": credit_data,
            "dollar_data": dollar_data,
            "stability_data": stability_data,
            "source_meta": source_meta,
            "source_errors": source_errors,
        }
