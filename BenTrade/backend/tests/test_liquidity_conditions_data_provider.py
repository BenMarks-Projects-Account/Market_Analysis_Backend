"""Tests for LiquidityConditionsDataProvider fault tolerance.

Scenarios:
  1. All sources succeed (envelopes + FRED obs) — normal operation
  2. yield_curve_spread arrives as raw float (pre-fix regression guard)
  3. yield_curve_spread arrives as proper metric envelope
  4. Market context fails entirely — FRED credit still present
  5. Single FRED credit series fails — other sources survive
  6. All sources fail — returns all-None, no crash
  7. _extract_value / _extract_source / _extract_freshness handle scalars
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.services.liquidity_conditions_data_provider import (
    LiquidityConditionsDataProvider,
    _extract_freshness,
    _extract_source,
    _extract_value,
)


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════


def _metric(value: float | None, source: str = "fred",
            freshness: str = "eod") -> dict[str, Any]:
    """Build a minimal metric envelope."""
    return {
        "value": value, "source": source, "freshness": freshness,
        "is_intraday": False, "observation_date": "2025-01-10",
        "fetched_at": "2025-01-10T12:00:00Z", "source_timestamp": None,
        "previous_close": None,
    }


def _market_ctx_all_envelopes() -> dict[str, Any]:
    """Market context where ALL metrics (including yield_curve_spread)
    are proper metric envelopes."""
    return {
        "vix": _metric(18.5, source="tradier", freshness="intraday"),
        "ten_year_yield": _metric(4.20),
        "two_year_yield": _metric(4.00),
        "fed_funds_rate": _metric(5.25),
        "oil_wti": _metric(75.0),
        "usd_index": _metric(100.0),
        "yield_curve_spread": _metric(0.20, source="derived (10Y-2Y)"),
        "cpi_yoy": _metric(0.032),
        "context_generated_at": "2025-01-10T12:00:00Z",
    }


def _market_ctx_raw_float_spread() -> dict[str, Any]:
    """Market context where yield_curve_spread is a raw float
    (the bug scenario that caused 'float has no attribute get')."""
    ctx = _market_ctx_all_envelopes()
    ctx["yield_curve_spread"] = 0.20  # raw float — NOT a dict
    return ctx


def _fred_obs(value: float) -> dict[str, Any]:
    return {"value": value, "observation_date": "2025-01-10"}


def _make_provider(
    market_ctx: dict[str, Any] | Exception | None = None,
    ig_obs: dict | Exception | None = "default",
    hy_obs: dict | Exception | None = "default",
) -> LiquidityConditionsDataProvider:
    """Build provider with mocked dependencies."""
    svc = AsyncMock()
    if isinstance(market_ctx, Exception):
        svc.get_market_context.side_effect = market_ctx
    else:
        svc.get_market_context.return_value = (
            market_ctx if market_ctx is not None
            else _market_ctx_all_envelopes()
        )

    fred = AsyncMock()

    async def _mock_fred(series_id: str):
        if series_id == "BAMLC0A0CM":
            if isinstance(ig_obs, Exception):
                raise ig_obs
            return _fred_obs(0.90) if ig_obs == "default" else ig_obs
        if series_id == "BAMLH0A0HYM2":
            if isinstance(hy_obs, Exception):
                raise hy_obs
            return _fred_obs(3.50) if hy_obs == "default" else hy_obs
        return None

    fred.get_series_with_date = _mock_fred
    svc.fred = fred

    return LiquidityConditionsDataProvider(market_context_service=svc)


# ═══════════════════════════════════════════════════════════════
# UNIT: _extract_value / _extract_source / _extract_freshness
# ═══════════════════════════════════════════════════════════════


class TestExtractHelpers:
    """Verify helpers handle dict envelopes, raw scalars, None, and unexpected types."""

    def test_extract_value_envelope(self):
        assert _extract_value({"value": 4.2, "source": "fred"}) == 4.2

    def test_extract_value_raw_float(self):
        assert _extract_value(0.3) == 0.3

    def test_extract_value_raw_int(self):
        assert _extract_value(5) == 5.0

    def test_extract_value_none(self):
        assert _extract_value(None) is None

    def test_extract_value_unexpected_type(self):
        assert _extract_value("bad") is None

    def test_extract_source_envelope(self):
        assert _extract_source({"value": 1, "source": "fred"}) == "fred"

    def test_extract_source_raw_float(self):
        assert _extract_source(0.3) is None

    def test_extract_source_none(self):
        assert _extract_source(None) is None

    def test_extract_freshness_envelope(self):
        assert _extract_freshness({"freshness": "eod"}) == "eod"

    def test_extract_freshness_raw_float(self):
        assert _extract_freshness(18.5) is None

    def test_extract_freshness_none(self):
        assert _extract_freshness(None) is None


# ═══════════════════════════════════════════════════════════════
# INTEGRATION: fetch_liquidity_conditions_data
# ═══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_all_sources_succeed_envelopes():
    """Normal case with proper metric envelopes — all data present."""
    provider = _make_provider()
    result = await provider.fetch_liquidity_conditions_data()

    assert result["source_errors"] == {}
    assert result["rates_data"]["two_year_yield"] == 4.00
    assert result["rates_data"]["yield_curve_spread"] == 0.20
    assert result["conditions_data"]["ig_spread"] == 0.90
    assert result["credit_data"]["hy_spread"] == 3.50
    assert result["dollar_data"]["dxy_level"] == 100.0


@pytest.mark.asyncio
async def test_raw_float_yield_spread_no_crash():
    """Regression: yield_curve_spread as raw float must NOT crash.

    This was the original bug — 'float' object has no attribute 'get'.
    """
    provider = _make_provider(market_ctx=_market_ctx_raw_float_spread())
    result = await provider.fetch_liquidity_conditions_data()

    # Must NOT crash — value should still extract correctly
    assert result["rates_data"]["yield_curve_spread"] == 0.20
    assert result["conditions_data"]["yield_curve_spread"] == 0.20
    assert result["stability_data"]["yield_curve_spread"] == 0.20
    # Source meta for yield_curve_spread should reflect lack of metadata
    sd = result["source_meta"]["source_detail"]["yield_curve_spread"]
    assert sd["value"] == 0.20
    assert sd["source"] is None  # raw float has no source metadata
    assert sd["freshness"] is None


@pytest.mark.asyncio
async def test_market_context_fails_fred_ok():
    """MarketContextService throws — FRED credit series still return data."""
    provider = _make_provider(market_ctx=RuntimeError("upstream fail"))
    result = await provider.fetch_liquidity_conditions_data()

    assert "market_context" in result["source_errors"]
    assert result["rates_data"]["two_year_yield"] is None
    assert result["conditions_data"]["vix"] is None
    # FRED credit data should survive
    assert result["conditions_data"]["ig_spread"] == 0.90
    assert result["credit_data"]["hy_spread"] == 3.50


@pytest.mark.asyncio
async def test_single_fred_fails_others_ok():
    """One FRED credit series fails — rest of data intact."""
    provider = _make_provider(ig_obs=RuntimeError("HTTP 400"))
    result = await provider.fetch_liquidity_conditions_data()

    assert "ig_spread_BAMLC0A0CM" in result["source_errors"]
    assert result["conditions_data"]["ig_spread"] is None
    assert result["conditions_data"]["hy_spread"] == 3.50
    assert result["rates_data"]["ten_year_yield"] == 4.20


@pytest.mark.asyncio
async def test_all_sources_fail_no_crash():
    """Complete upstream failure — structured output, no crash."""
    provider = _make_provider(
        market_ctx=RuntimeError("down"),
        ig_obs=RuntimeError("down"),
        hy_obs=RuntimeError("down"),
    )
    result = await provider.fetch_liquidity_conditions_data()

    # Everything None but structure intact
    assert result["rates_data"]["ten_year_yield"] is None
    assert result["conditions_data"]["ig_spread"] is None
    assert result["credit_data"]["hy_spread"] is None
    assert len(result["source_errors"]) == 3
    for key in ("rates_data", "conditions_data", "credit_data",
                "dollar_data", "stability_data", "source_meta"):
        assert key in result


@pytest.mark.asyncio
async def test_source_meta_reports_coverage():
    """source_meta reflects data availability counts."""
    provider = _make_provider()
    result = await provider.fetch_liquidity_conditions_data()

    meta = result["source_meta"]
    assert meta["direct_signals_available"] == 8  # all 8 signals
    assert meta["direct_signals_total"] == 8
    assert meta["has_credit_spreads"] is True
    assert meta["has_funding_data"] is True
