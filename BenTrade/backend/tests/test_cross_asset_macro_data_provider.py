"""Tests for CrossAssetMacroDataProvider fault tolerance.

Scenarios:
  1. All sources succeed — normal operation
  2. Single FRED series fails (HTTP 400) — other sources still return data
  3. MarketContextService fails entirely — FRED series still return data
  4. All FRED series fail — market context still returns data
  5. All sources fail — returns all-None data, no crash
  6. source_errors dict tracks which sources failed
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.cross_asset_macro_data_provider import CrossAssetMacroDataProvider
from app.utils.http import UpstreamError


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════


def _mock_market_context() -> dict[str, Any]:
    """Minimal market context response with all metric envelopes."""
    def _m(value):
        return {"value": value, "source": "fred", "freshness": "eod",
                "is_intraday": False, "observation_date": "2025-01-10",
                "fetched_at": "2025-01-10T12:00:00Z", "source_timestamp": None,
                "previous_close": None}
    return {
        "vix": _m(18.5),
        "ten_year_yield": _m(4.20),
        "two_year_yield": _m(4.00),
        "fed_funds_rate": _m(5.25),
        "oil_wti": _m(75.0),
        "usd_index": _m(100.0),
        "yield_curve_spread": 0.20,
        "cpi_yoy": _m(0.032),
        "context_generated_at": "2025-01-10T12:00:00Z",
    }


def _mock_fred_obs(value: float, date: str = "2025-01-10") -> dict[str, Any]:
    return {"value": value, "observation_date": date}


def _make_provider(
    market_ctx_side_effect=None,
    fred_side_effects: dict[str, Any] | None = None,
) -> CrossAssetMacroDataProvider:
    """Build a provider with mocked dependencies."""
    market_ctx_svc = AsyncMock()
    if isinstance(market_ctx_side_effect, Exception):
        market_ctx_svc.get_market_context.side_effect = market_ctx_side_effect
    else:
        market_ctx_svc.get_market_context.return_value = (
            market_ctx_side_effect or _mock_market_context()
        )

    fred_client = AsyncMock()
    fred_effects = fred_side_effects or {}

    async def _mock_get_series(series_id: str | None = None) -> dict | None:
        sid = series_id or "VIXCLS"
        effect = fred_effects.get(sid)
        if isinstance(effect, Exception):
            raise effect
        if effect is not None:
            return effect
        # Default: return a sensible value based on series
        defaults = {
            "GOLDAMGBD228NLBM": _mock_fred_obs(2050.0),
            "PCOPPUSDM": _mock_fred_obs(8500.0),
            "BAMLC0A0CM": _mock_fred_obs(0.90),
            "BAMLH0A0HYM2": _mock_fred_obs(3.50),
        }
        return defaults.get(sid)

    fred_client.get_series_with_date = _mock_get_series
    return CrossAssetMacroDataProvider(
        market_context_service=market_ctx_svc,
        fred_client=fred_client,
    )


# ═══════════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_all_sources_succeed():
    """Normal case — all 5 fetches succeed, no source_errors."""
    provider = _make_provider()
    result = await provider.fetch_cross_asset_data()

    assert result["source_errors"] == {}
    assert result["rates_data"]["ten_year_yield"] == 4.20
    assert result["dollar_commodity_data"]["gold_price"] == 2050.0
    assert result["credit_data"]["ig_spread"] == 0.90
    assert result["credit_data"]["hy_spread"] == 3.50


@pytest.mark.asyncio
async def test_single_fred_fails_others_ok():
    """One FRED series returns HTTP 400 — others still present."""
    provider = _make_provider(
        fred_side_effects={
            "GOLDAMGBD228NLBM": UpstreamError("Upstream returned HTTP 400"),
        }
    )
    result = await provider.fetch_cross_asset_data()

    # Gold should be None, others should be fine
    assert result["dollar_commodity_data"]["gold_price"] is None
    assert result["dollar_commodity_data"]["copper_price"] == 8500.0
    assert result["credit_data"]["ig_spread"] == 0.90
    assert result["rates_data"]["ten_year_yield"] == 4.20
    # source_errors should track the failure
    assert "fred_gold" in result["source_errors"]
    assert "400" in result["source_errors"]["fred_gold"]


@pytest.mark.asyncio
async def test_market_context_fails_fred_ok():
    """MarketContextService throws — FRED series still available."""
    provider = _make_provider(
        market_ctx_side_effect=UpstreamError("Upstream returned HTTP 400"),
    )
    result = await provider.fetch_cross_asset_data()

    # Market context metrics should be None
    assert result["rates_data"]["ten_year_yield"] is None
    assert result["credit_data"]["vix"] is None
    # FRED-direct metrics should still be present
    assert result["dollar_commodity_data"]["gold_price"] == 2050.0
    assert result["credit_data"]["ig_spread"] == 0.90
    assert "market_context" in result["source_errors"]


@pytest.mark.asyncio
async def test_all_fred_fail_market_ctx_ok():
    """All 4 direct FRED series fail — market context data survives."""
    provider = _make_provider(
        fred_side_effects={
            "GOLDAMGBD228NLBM": UpstreamError("400"),
            "PCOPPUSDM": UpstreamError("400"),
            "BAMLC0A0CM": UpstreamError("400"),
            "BAMLH0A0HYM2": UpstreamError("400"),
        }
    )
    result = await provider.fetch_cross_asset_data()

    # Market context data should survive
    assert result["rates_data"]["ten_year_yield"] == 4.20
    assert result["credit_data"]["vix"] == 18.5
    # FRED-direct should be None
    assert result["dollar_commodity_data"]["gold_price"] is None
    assert result["dollar_commodity_data"]["copper_price"] is None
    assert result["credit_data"]["ig_spread"] is None
    assert result["credit_data"]["hy_spread"] is None
    # 4 source errors
    assert len(result["source_errors"]) == 4


@pytest.mark.asyncio
async def test_all_sources_fail_no_crash():
    """Complete upstream failure — still returns structured data, no crash."""
    provider = _make_provider(
        market_ctx_side_effect=UpstreamError("total failure"),
        fred_side_effects={
            "GOLDAMGBD228NLBM": UpstreamError("400"),
            "PCOPPUSDM": UpstreamError("400"),
            "BAMLC0A0CM": UpstreamError("400"),
            "BAMLH0A0HYM2": UpstreamError("400"),
        }
    )
    result = await provider.fetch_cross_asset_data()

    # Everything None, but structured output intact
    assert result["rates_data"]["ten_year_yield"] is None
    assert result["dollar_commodity_data"]["gold_price"] is None
    assert result["credit_data"]["ig_spread"] is None
    assert len(result["source_errors"]) == 5
    # Should have all pillar keys
    for key in ("rates_data", "dollar_commodity_data", "credit_data",
                "defensive_growth_data", "coherence_data", "source_meta"):
        assert key in result


@pytest.mark.asyncio
async def test_source_errors_not_present_on_success():
    """When everything works, source_errors is an empty dict."""
    provider = _make_provider()
    result = await provider.fetch_cross_asset_data()
    assert result["source_errors"] == {}


@pytest.mark.asyncio
async def test_network_error_captured():
    """Network-level exception is handled same as HTTP error."""
    provider = _make_provider(
        fred_side_effects={
            "PCOPPUSDM": ConnectionError("connection reset"),
        }
    )
    result = await provider.fetch_cross_asset_data()

    assert result["dollar_commodity_data"]["copper_price"] is None
    assert "fred_copper" in result["source_errors"]
    # Other sources unaffected
    assert result["dollar_commodity_data"]["gold_price"] == 2050.0
