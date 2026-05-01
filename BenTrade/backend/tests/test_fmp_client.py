"""Tests for FMP client — new data methods and rate limiter.

Covers:
  - get_historical_price_eod()
  - get_quote()
  - get_technical_indicator()
  - get_macd()
  - TokenBucketRateLimiter
  - VIX symbol handling (^VIX)
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.clients.fmp_client import FMPClient, TokenBucketRateLimiter


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture()
def settings():
    s = MagicMock()
    s.FMP_API_KEY = "test-key"
    s.FMP_BASE_URL = "https://financialmodelingprep.com/stable"
    return s


@pytest.fixture()
def cache():
    """Cache that always calls the loader (no caching in tests)."""
    c = MagicMock()

    async def _passthrough(key, ttl, loader):
        return await loader()

    c.get_or_set = AsyncMock(side_effect=_passthrough)
    return c


@pytest.fixture()
def rate_limiter():
    """Rate limiter with no throttling (high capacity)."""
    rl = TokenBucketRateLimiter(max_per_minute=99999, safety_pct=1.0)
    return rl


@pytest.fixture()
def http_client():
    return AsyncMock()


@pytest.fixture()
def client(settings, http_client, cache, rate_limiter):
    return FMPClient(settings, http_client, cache, rate_limiter=rate_limiter)


def _mock_response(data: Any, status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    resp.text = str(data)
    return resp


# ── get_historical_price_eod ──────────────────────────────────────────────

class TestGetHistoricalPriceEod:

    @pytest.mark.asyncio
    async def test_returns_normalised_bars_oldest_first(self, client, http_client):
        # FMP returns newest-first
        http_client.get.return_value = _mock_response([
            {"date": "2026-04-15", "open": 520.0, "high": 525.0, "low": 518.0,
             "close": 523.0, "volume": 10000000, "adjClose": 523.0, "vwap": 521.5},
            {"date": "2026-04-14", "open": 515.0, "high": 521.0, "low": 514.0,
             "close": 520.0, "volume": 9000000, "adjClose": 520.0, "vwap": 517.5},
        ])

        result = await client.get_historical_price_eod("SPY")

        assert result is not None
        assert len(result) == 2
        # Oldest-first ordering
        assert result[0]["date"] == "2026-04-14"
        assert result[1]["date"] == "2026-04-15"
        # Normalised field names (Polygon-compatible)
        assert set(result[0].keys()) == {"date", "open", "high", "low", "close", "volume"}
        assert result[0]["open"] == 515.0
        assert result[0]["volume"] == 9000000

    @pytest.mark.asyncio
    async def test_passes_date_params(self, client, http_client):
        http_client.get.return_value = _mock_response([])

        await client.get_historical_price_eod("AAPL", from_date="2026-01-01", to_date="2026-04-15")

        call_kwargs = http_client.get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert params["symbol"] == "AAPL"
        assert params["from"] == "2026-01-01"
        assert params["to"] == "2026-04-15"

    @pytest.mark.asyncio
    async def test_vix_symbol_passed_through(self, client, http_client):
        http_client.get.return_value = _mock_response([
            {"date": "2026-04-15", "open": 18.0, "high": 19.0, "low": 17.5,
             "close": 18.5, "volume": 0},
        ])

        result = await client.get_historical_price_eod("^VIX")

        assert result is not None
        call_kwargs = http_client.get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert params["symbol"] == "^VIX"

    @pytest.mark.asyncio
    async def test_returns_none_on_api_error(self, client, http_client):
        http_client.get.return_value = _mock_response(None, status_code=500)

        result = await client.get_historical_price_eod("SPY")
        assert result is None

    @pytest.mark.asyncio
    async def test_skips_malformed_rows(self, client, http_client):
        http_client.get.return_value = _mock_response([
            {"date": "2026-04-14", "open": 515.0, "high": 521.0, "low": 514.0,
             "close": 520.0, "volume": 9000000},
            {"bad_row": True},  # missing "date" key
        ])

        result = await client.get_historical_price_eod("SPY")
        assert result is not None
        assert len(result) == 1


# ── get_quote ─────────────────────────────────────────────────────────────

class TestGetQuote:

    @pytest.mark.asyncio
    async def test_returns_polygon_compatible_shape(self, client, http_client):
        http_client.get.return_value = _mock_response([{
            "symbol": "SPY",
            "price": 523.45,
            "changesPercentage": 0.65,
            "change": 3.40,
            "dayLow": 518.0,
            "dayHigh": 525.0,
            "open": 520.0,
            "previousClose": 520.05,
            "volume": 45000000,
            "yearHigh": 560.0,
            "yearLow": 410.0,
            "timestamp": "2026-04-15T16:00:00",
        }])

        result = await client.get_quote("SPY")

        assert result is not None
        assert result["symbol"] == "SPY"
        assert result["price"] == 523.45
        assert result["last"] == 523.45
        assert result["open"] == 520.0
        assert result["high"] == 525.0
        assert result["low"] == 518.0
        assert result["close"] == 523.45
        assert result["volume"] == 45000000
        assert result["prev_close"] == 520.05
        assert result["change"] == 3.40
        assert result["change_percentage"] == 0.65
        assert result["week_52_high"] == 560.0
        assert result["week_52_low"] == 410.0
        assert result["source"] == "fmp"

    @pytest.mark.asyncio
    async def test_year_high_low_none_when_missing(self, client, http_client):
        http_client.get.return_value = _mock_response([{
            "symbol": "SPY",
            "price": 523.45,
            "changesPercentage": 0.65,
            "change": 3.40,
            "dayLow": 518.0,
            "dayHigh": 525.0,
            "open": 520.0,
            "previousClose": 520.05,
            "volume": 45000000,
            "timestamp": "2026-04-15T16:00:00",
        }])

        result = await client.get_quote("SPY")

        assert result is not None
        assert result["week_52_high"] is None
        assert result["week_52_low"] is None

    @pytest.mark.asyncio
    async def test_vix_quote(self, client, http_client):
        http_client.get.return_value = _mock_response([{
            "symbol": "^VIX",
            "price": 18.5,
            "changesPercentage": -2.1,
            "change": -0.4,
            "dayLow": 17.8,
            "dayHigh": 19.2,
            "open": 18.9,
            "previousClose": 18.9,
            "volume": 0,
            "yearHigh": 65.73,
            "yearLow": 10.62,
            "timestamp": "2026-04-15T16:00:00",
        }])

        result = await client.get_quote("^VIX")

        assert result is not None
        assert result["symbol"] == "^VIX"
        assert result["price"] == 18.5
        assert result["week_52_high"] == 65.73
        assert result["week_52_low"] == 10.62

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_response(self, client, http_client):
        http_client.get.return_value = _mock_response([])
        result = await client.get_quote("BAD")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_api_error(self, client, http_client):
        http_client.get.return_value = _mock_response(None, status_code=401)
        result = await client.get_quote("SPY")
        assert result is None


# ── get_technical_indicator ───────────────────────────────────────────────

class TestGetTechnicalIndicator:

    @pytest.mark.asyncio
    async def test_rsi_normalised_oldest_first(self, client, http_client):
        http_client.get.return_value = _mock_response([
            {"date": "2026-04-15", "rsi": 65.3},
            {"date": "2026-04-14", "rsi": 62.1},
        ])

        result = await client.get_technical_indicator("SPY", "rsi", 14)

        assert result is not None
        assert len(result) == 2
        assert result[0]["date"] == "2026-04-14"
        assert result[0]["value"] == 62.1
        assert result[1]["date"] == "2026-04-15"
        assert result[1]["value"] == 65.3

    @pytest.mark.asyncio
    async def test_sma_url_construction(self, client, http_client):
        http_client.get.return_value = _mock_response([
            {"date": "2026-04-15", "sma": 510.5},
        ])

        await client.get_technical_indicator("AAPL", "sma", 200, "1day")

        call_args = http_client.get.call_args
        url = call_args[0][0] if call_args[0] else call_args.kwargs.get("url", "")
        assert "/technical-indicators/sma" in url
        params = call_args.kwargs.get("params") or call_args[1].get("params", {})
        assert params["symbol"] == "AAPL"
        assert params["periodLength"] == 200
        assert params["timeframe"] == "1day"

    @pytest.mark.asyncio
    async def test_ema_uses_indicator_key(self, client, http_client):
        http_client.get.return_value = _mock_response([
            {"date": "2026-04-15", "ema": 515.0},
        ])

        result = await client.get_technical_indicator("SPY", "ema", 12)

        assert result is not None
        assert result[0]["value"] == 515.0

    @pytest.mark.asyncio
    async def test_returns_none_on_failure(self, client, http_client):
        http_client.get.return_value = _mock_response(None, status_code=402)
        result = await client.get_technical_indicator("SPY", "rsi", 14)
        assert result is None


# ── get_macd ──────────────────────────────────────────────────────────────

class TestGetMacd:

    @pytest.mark.asyncio
    async def test_macd_calculation(self, client, http_client):
        """Verify MACD = EMA(fast) - EMA(slow) with correct signal + histogram."""
        # EMA(12) call returns these values
        ema_fast_data = [
            {"date": "2026-04-10", "ema": 100.0},
            {"date": "2026-04-11", "ema": 102.0},
            {"date": "2026-04-12", "ema": 104.0},
            {"date": "2026-04-13", "ema": 103.0},
            {"date": "2026-04-14", "ema": 105.0},
        ]
        # EMA(26) call returns these values
        ema_slow_data = [
            {"date": "2026-04-10", "ema": 98.0},
            {"date": "2026-04-11", "ema": 99.0},
            {"date": "2026-04-12", "ema": 100.0},
            {"date": "2026-04-13", "ema": 100.5},
            {"date": "2026-04-14", "ema": 101.0},
        ]

        call_count = 0

        async def _side_effect(url, **kwargs):
            nonlocal call_count
            params = kwargs.get("params", {})
            period = params.get("periodLength", 0)
            call_count += 1
            if period == 12:
                return _mock_response(ema_fast_data)
            else:
                return _mock_response(ema_slow_data)

        http_client.get.side_effect = _side_effect

        result = await client.get_macd("SPY")

        assert result is not None
        assert "macd" in result
        assert "signal" in result
        assert "histogram" in result

        # MACD line = fast - slow
        macd_vals = [p["value"] for p in result["macd"]]
        expected_macd = [2.0, 3.0, 4.0, 2.5, 4.0]
        for got, want in zip(macd_vals, expected_macd):
            assert abs(got - want) < 1e-9

        # Signal = EMA(9) of MACD line.  k = 2/(9+1) = 0.2
        k = 0.2
        expected_signal = []
        ema_val = None
        for m in expected_macd:
            if ema_val is None:
                ema_val = m
            else:
                ema_val = m * k + ema_val * (1 - k)
            expected_signal.append(ema_val)

        signal_vals = [p["value"] for p in result["signal"]]
        for got, want in zip(signal_vals, expected_signal):
            assert abs(got - want) < 1e-9

        # Histogram = MACD - Signal
        hist_vals = [p["value"] for p in result["histogram"]]
        for i, (m, s) in enumerate(zip(expected_macd, expected_signal)):
            assert abs(hist_vals[i] - (m - s)) < 1e-9

        # Dates should be aligned
        dates = [p["date"] for p in result["macd"]]
        assert dates == ["2026-04-10", "2026-04-11", "2026-04-12", "2026-04-13", "2026-04-14"]

    @pytest.mark.asyncio
    async def test_returns_none_when_ema_fails(self, client, http_client):
        http_client.get.return_value = _mock_response(None, status_code=500)
        result = await client.get_macd("SPY")
        assert result is None

    @pytest.mark.asyncio
    async def test_custom_periods(self, client, http_client):
        """Verify custom fast/slow/signal periods are passed through."""
        http_client.get.return_value = _mock_response([
            {"date": "2026-04-15", "ema": 100.0},
        ])

        result = await client.get_macd(
            "AAPL",
            fast_period=8,
            slow_period=21,
            signal_period=5,
        )

        # Should have made 2 EMA calls with periods 8 and 21
        calls = http_client.get.call_args_list
        periods = set()
        for call in calls:
            params = call.kwargs.get("params") or call[1].get("params", {})
            if "periodLength" in params:
                periods.add(params["periodLength"])
        assert 8 in periods
        assert 21 in periods


# ── Intraday bars ─────────────────────────────────────────────────────────

class TestGetIntradayBars:

    @pytest.mark.asyncio
    async def test_returns_normalised_bars_oldest_first(self, client, http_client):
        """Verify normalised {date, close} shape, oldest-first ordering."""
        http_client.get.return_value = _mock_response([
            {"date": "2026-04-15 14:00:00", "open": 180.0, "high": 181.5,
             "low": 179.5, "close": 181.0, "volume": 50000},
            {"date": "2026-04-15 13:00:00", "open": 179.0, "high": 180.5,
             "low": 178.5, "close": 180.0, "volume": 45000},
            {"date": "2026-04-15 12:00:00", "open": 178.0, "high": 179.5,
             "low": 177.5, "close": 179.0, "volume": 40000},
        ])

        result = await client.get_intraday_bars("AAPL")

        assert result is not None
        assert len(result) == 3
        # Oldest-first
        assert result[0]["date"] == "2026-04-15 12:00:00"
        assert result[-1]["date"] == "2026-04-15 14:00:00"
        # Only date + close in output
        assert set(result[0].keys()) == {"date", "close"}
        assert result[0]["close"] == 179.0

    @pytest.mark.asyncio
    async def test_url_construction_default_interval(self, client, http_client):
        """Default interval is 1hour → URL path includes /historical-chart/1hour."""
        http_client.get.return_value = _mock_response([])

        await client.get_intraday_bars("SPY")

        url = http_client.get.call_args[0][0]
        assert "/historical-chart/1hour" in url

    @pytest.mark.asyncio
    async def test_url_construction_custom_interval(self, client, http_client):
        """Custom interval in URL path."""
        http_client.get.return_value = _mock_response([])

        await client.get_intraday_bars("SPY", interval="5min")

        url = http_client.get.call_args[0][0]
        assert "/historical-chart/5min" in url

    @pytest.mark.asyncio
    async def test_date_params_passthrough(self, client, http_client):
        """from_date and to_date are passed as 'from' and 'to' query params."""
        http_client.get.return_value = _mock_response([])

        await client.get_intraday_bars("AAPL", from_date="2026-04-01", to_date="2026-04-15")

        params = http_client.get.call_args.kwargs.get("params", {})
        assert params["from"] == "2026-04-01"
        assert params["to"] == "2026-04-15"
        assert params["symbol"] == "AAPL"

    @pytest.mark.asyncio
    async def test_cache_hit_within_ttl(self, settings, http_client, rate_limiter):
        """Second call within TTL returns cached data without a second HTTP call."""
        # Use a real dict-based cache to test TTL behaviour
        _store: dict[str, Any] = {}

        class SimpleCache:
            async def get_or_set(self, key, ttl, loader):
                if key in _store:
                    return _store[key]
                val = await loader()
                _store[key] = val
                return val

        cache = SimpleCache()
        c = FMPClient(settings, http_client, cache, rate_limiter=rate_limiter)

        http_client.get.return_value = _mock_response([
            {"date": "2026-04-15 13:00:00", "close": 180.0, "open": 179, "high": 181, "low": 178, "volume": 1},
        ])

        r1 = await c.get_intraday_bars("SPY")
        r2 = await c.get_intraday_bars("SPY")

        assert r1 == r2
        # HTTP should only have been called once (second call hit cache)
        assert http_client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_returns_none_on_api_error(self, client, http_client):
        http_client.get.return_value = _mock_response(None, status_code=500)
        result = await client.get_intraday_bars("SPY")
        assert result is None

    @pytest.mark.asyncio
    async def test_skips_malformed_rows(self, client, http_client):
        """Rows missing 'date' or 'close' are silently dropped."""
        http_client.get.return_value = _mock_response([
            {"date": "2026-04-15 12:00:00", "close": 179.0},
            {"open": 180.0},  # missing date and close
            {"date": "2026-04-15 13:00:00", "close": 180.5},
        ])
        result = await client.get_intraday_bars("SPY")
        assert len(result) == 2


# ── TokenBucketRateLimiter ────────────────────────────────────────────────

class TestTokenBucketRateLimiter:

    @pytest.mark.asyncio
    async def test_acquire_under_limit(self):
        rl = TokenBucketRateLimiter(max_per_minute=600, safety_pct=1.0)
        # Should not block
        t0 = time.monotonic()
        for _ in range(10):
            await rl.acquire()
        elapsed = time.monotonic() - t0
        assert elapsed < 1.0  # Near-instant

    @pytest.mark.asyncio
    async def test_throttles_when_exhausted(self):
        # 6 tokens per minute = 0.1 tokens/sec → after 6 acquires, must wait
        rl = TokenBucketRateLimiter(max_per_minute=6, safety_pct=1.0)
        for _ in range(6):
            await rl.acquire()
        # Next acquire should sleep
        t0 = time.monotonic()
        await rl.acquire()
        elapsed = time.monotonic() - t0
        # Should have waited ~10 seconds for 1 token at 0.1/sec
        # But let's just verify it waited at all (> 0.5s)
        assert elapsed > 0.5

    @pytest.mark.asyncio
    async def test_safety_margin_reduces_capacity(self):
        rl = TokenBucketRateLimiter(max_per_minute=100, safety_pct=0.5)
        # Effective capacity = 50
        assert rl._capacity == 50

    @pytest.mark.asyncio
    async def test_default_capacity_ultimate_tier(self):
        rl = TokenBucketRateLimiter()
        # Default: 3000 * 0.80 = 2400
        assert rl._capacity == 2400

    @pytest.mark.asyncio
    async def test_logs_warning_on_throttle(self):
        rl = TokenBucketRateLimiter(max_per_minute=6, safety_pct=1.0)
        for _ in range(6):
            await rl.acquire()

        with patch("app.clients.fmp_client.logger") as mock_logger:
            await rl.acquire()
            mock_logger.warning.assert_called()
            assert "throttling" in mock_logger.warning.call_args[0][0].lower()


# ── Rate limiter integration ──────────────────────────────────────────────

class TestRateLimiterIntegration:

    @pytest.mark.asyncio
    async def test_fetch_calls_rate_limiter(self, settings, cache):
        """Verify that _fetch() calls rate_limiter.acquire() before HTTP request."""
        mock_rl = MagicMock()
        mock_rl.acquire = AsyncMock()
        http = AsyncMock()
        http.get.return_value = _mock_response([{"ok": True}])

        c = FMPClient(settings, http, cache, rate_limiter=mock_rl)
        await c.get_market_gainers()

        mock_rl.acquire.assert_called_once()

    @pytest.mark.asyncio
    async def test_new_methods_use_rate_limiter(self, settings, cache):
        """Verify new methods are also rate-limited."""
        mock_rl = MagicMock()
        mock_rl.acquire = AsyncMock()
        http = AsyncMock()
        http.get.return_value = _mock_response([
            {"date": "2026-04-15", "open": 100, "high": 101, "low": 99,
             "close": 100.5, "volume": 1000},
        ])

        c = FMPClient(settings, http, cache, rate_limiter=mock_rl)
        await c.get_historical_price_eod("SPY")

        assert mock_rl.acquire.call_count == 1

    @pytest.mark.asyncio
    async def test_intraday_bars_use_rate_limiter(self, settings, cache):
        """Verify get_intraday_bars() calls rate_limiter.acquire()."""
        mock_rl = MagicMock()
        mock_rl.acquire = AsyncMock()
        http = AsyncMock()
        http.get.return_value = _mock_response([
            {"date": "2026-04-15 13:00:00", "close": 180.0,
             "open": 179, "high": 181, "low": 178, "volume": 1},
        ])

        c = FMPClient(settings, http, cache, rate_limiter=mock_rl)
        await c.get_intraday_bars("SPY")

        assert mock_rl.acquire.call_count == 1


# ── Health check ──────────────────────────────────────────────────────────

class TestFMPHealth:

    @pytest.mark.asyncio
    async def test_health_returns_true_on_valid_quote(self, client, http_client):
        http_client.get.return_value = _mock_response([{
            "symbol": "SPY", "price": 500.0, "changesPercentage": 0.5,
            "change": 2.5, "volume": 1000000, "dayLow": 498.0,
            "dayHigh": 502.0, "open": 499.0, "previousClose": 497.5,
            "timestamp": 1700000000,
        }])
        result = await client.health()
        assert result is True

    @pytest.mark.asyncio
    async def test_health_returns_false_when_no_key(self, http_client, cache, rate_limiter):
        s = MagicMock()
        s.FMP_API_KEY = ""
        s.FMP_BASE_URL = "https://financialmodelingprep.com/stable"
        c = FMPClient(s, http_client, cache, rate_limiter=rate_limiter)
        result = await c.health()
        assert result is False

    @pytest.mark.asyncio
    async def test_health_returns_false_on_api_error(self, client, http_client):
        http_client.get.return_value = _mock_response(None, status_code=500)
        result = await client.health()
        assert result is False

    @pytest.mark.asyncio
    async def test_health_returns_false_on_exception(self, client, http_client):
        http_client.get.side_effect = Exception("network error")
        result = await client.health()
        assert result is False
