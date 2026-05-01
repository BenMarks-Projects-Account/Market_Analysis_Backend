"""Tests for quote_routing — Tradier primary + FMP fallback."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.quote_routing import get_batch_quotes


# ── Helpers ────────────────────────────────────────────────────────────

def _tradier_quote(symbol: str) -> dict[str, Any]:
    """Minimal Tradier-shaped quote dict."""
    return {
        "symbol": symbol,
        "last": 100.0,
        "close": 100.0,
        "price": 100.0,
        "change": 1.5,
        "change_percentage": 1.5,
        "volume": 5_000_000,
        "prev_close": 98.5,
        "week_52_high": 120.0,
        "week_52_low": 80.0,
        "source": "tradier",
    }


def _fmp_quote(symbol: str) -> dict[str, Any]:
    """Minimal FMP-normalised quote dict."""
    return {
        "symbol": symbol,
        "last": 100.0,
        "close": 100.0,
        "price": 100.0,
        "change": 1.5,
        "change_percentage": 1.5,
        "volume": 5_000_000,
        "prev_close": 98.5,
        "week_52_high": 120.0,
        "week_52_low": 80.0,
        "source": "fmp",
    }


def _make_tradier(quotes: dict[str, dict] | Exception | None = None):
    """Build a mock TradierClient."""
    tc = AsyncMock()
    if isinstance(quotes, Exception):
        tc.get_quotes = AsyncMock(side_effect=quotes)
    elif quotes is not None:
        tc.get_quotes = AsyncMock(return_value=quotes)
    else:
        tc.get_quotes = AsyncMock(return_value={})
    tc.health = AsyncMock(return_value=True)
    return tc


def _make_fmp(quotes: dict[str, dict] | None = None, available: bool = True):
    """Build a mock FMPClient."""
    fc = MagicMock()
    fc.is_available.return_value = available

    async def _get_quote(sym: str):
        if quotes and sym in quotes:
            return quotes[sym]
        return _fmp_quote(sym) if available else None

    fc.get_quote = AsyncMock(side_effect=_get_quote)
    return fc


# ── Tradier primary path ──────────────────────────────────────────────

class TestTradierPrimary:

    @pytest.mark.asyncio
    async def test_tradier_returns_all_quotes(self):
        symbols = ["AAPL", "MSFT", "NVDA"]
        tradier_data = {s: _tradier_quote(s) for s in symbols}
        tc = _make_tradier(tradier_data)
        fc = _make_fmp()

        result = await get_batch_quotes(tc, fc, symbols)

        assert set(result.keys()) == set(symbols)
        fc.get_quote.assert_not_called()

    @pytest.mark.asyncio
    async def test_tradier_batches_over_50(self):
        symbols = [f"SYM{i}" for i in range(75)]
        tradier_data = {s: _tradier_quote(s) for s in symbols}
        tc = _make_tradier(tradier_data)
        fc = _make_fmp()

        result = await get_batch_quotes(tc, fc, symbols)

        assert len(result) == 75
        # Should have been called twice: 50 + 25
        assert tc.get_quotes.call_count == 2

    @pytest.mark.asyncio
    async def test_tradier_marks_health_good(self):
        tc = _make_tradier({"AAPL": _tradier_quote("AAPL")})
        fc = _make_fmp()
        health: dict[str, Any] = {}

        await get_batch_quotes(tc, fc, ["AAPL"], _tradier_health_cache=health)

        assert health["healthy"] is True


# ── FMP fallback path ─────────────────────────────────────────────────

class TestFMPFallback:

    @pytest.mark.asyncio
    async def test_tradier_failure_falls_back_to_fmp(self):
        tc = _make_tradier(Exception("connection refused"))
        fc = _make_fmp()

        result = await get_batch_quotes(tc, fc, ["AAPL", "MSFT"])

        assert "AAPL" in result
        assert "MSFT" in result
        assert fc.get_quote.call_count == 2

    @pytest.mark.asyncio
    async def test_tradier_none_skips_to_fmp(self):
        fc = _make_fmp()

        result = await get_batch_quotes(None, fc, ["AAPL"])

        assert "AAPL" in result
        assert fc.get_quote.call_count == 1

    @pytest.mark.asyncio
    async def test_tradier_health_red_skips_to_fmp(self):
        tc = _make_tradier({"AAPL": _tradier_quote("AAPL")})
        fc = _make_fmp()
        # Simulate health red within grace period
        health: dict[str, Any] = {
            "healthy": False,
            "checked_at": time.monotonic() - 10,  # 10s ago, within 60s grace
        }

        result = await get_batch_quotes(tc, fc, ["AAPL"], _tradier_health_cache=health)

        # Tradier should NOT have been called
        tc.get_quotes.assert_not_called()
        assert fc.get_quote.call_count == 1

    @pytest.mark.asyncio
    async def test_tradier_health_red_expired_retries_tradier(self):
        tc = _make_tradier({"AAPL": _tradier_quote("AAPL")})
        fc = _make_fmp()
        # Simulate health red but >60s ago
        health: dict[str, Any] = {
            "healthy": False,
            "checked_at": time.monotonic() - 120,  # 120s ago, past 60s grace
        }

        result = await get_batch_quotes(tc, fc, ["AAPL"], _tradier_health_cache=health)

        # Tradier SHOULD have been retried
        tc.get_quotes.assert_called_once()
        assert "AAPL" in result

    @pytest.mark.asyncio
    async def test_tradier_failure_marks_health_red(self):
        tc = _make_tradier(Exception("timeout"))
        fc = _make_fmp()
        health: dict[str, Any] = {}

        await get_batch_quotes(tc, fc, ["AAPL"], _tradier_health_cache=health)

        assert health["healthy"] is False


# ── Mixed source path ─────────────────────────────────────────────────

class TestMixedSource:

    @pytest.mark.asyncio
    async def test_partial_tradier_fills_missing_from_fmp(self):
        # Tradier returns AAPL but not MSFT
        tc = _make_tradier({"AAPL": _tradier_quote("AAPL")})
        fc = _make_fmp()

        result = await get_batch_quotes(tc, fc, ["AAPL", "MSFT"])

        assert "AAPL" in result
        assert "MSFT" in result
        # FMP only called for the missing symbol
        fc.get_quote.assert_called_once_with("MSFT")


# ── Edge cases ────────────────────────────────────────────────────────

class TestEdgeCases:

    @pytest.mark.asyncio
    async def test_empty_symbols_returns_empty(self):
        tc = _make_tradier({})
        fc = _make_fmp()

        result = await get_batch_quotes(tc, fc, [])

        assert result == {}

    @pytest.mark.asyncio
    async def test_both_sources_down_returns_empty(self):
        tc = _make_tradier(Exception("down"))
        fc = _make_fmp(available=False)

        result = await get_batch_quotes(tc, fc, ["AAPL"])

        assert result == {}

    @pytest.mark.asyncio
    async def test_fmp_none_returns_partial(self):
        tc = _make_tradier(Exception("down"))
        fc = MagicMock()
        fc.is_available.return_value = True

        async def _failing_quote(sym: str):
            return None

        fc.get_quote = AsyncMock(side_effect=_failing_quote)

        result = await get_batch_quotes(tc, fc, ["AAPL"])

        assert result == {}
