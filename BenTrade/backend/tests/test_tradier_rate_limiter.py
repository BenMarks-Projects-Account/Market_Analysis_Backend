"""Tests for TradierClient rate limiter and 429 retry logic."""

import asyncio
import time
import pytest

from unittest.mock import AsyncMock, patch, MagicMock

from app.clients.tradier_client import _AsyncRateLimiter, TradierClient, _MAX_429_RETRIES
from app.utils.http import UpstreamError


# ═══════════════════════════════════════════════════════════════════════
# UNIT TESTS: _AsyncRateLimiter
# ═══════════════════════════════════════════════════════════════════════


class TestAsyncRateLimiter:
    """Direct unit tests for the rate limiter."""

    @pytest.mark.asyncio
    async def test_first_acquire_is_instant(self):
        """First request should not wait."""
        limiter = _AsyncRateLimiter(max_per_second=2.0)
        t0 = time.monotonic()
        await limiter.acquire()
        elapsed = time.monotonic() - t0
        assert elapsed < 0.1  # Should be near-instant

    @pytest.mark.asyncio
    async def test_rapid_acquires_are_spaced(self):
        """Back-to-back acquires should be spaced ~500ms apart at 2 req/s."""
        limiter = _AsyncRateLimiter(max_per_second=2.0)
        t0 = time.monotonic()
        await limiter.acquire()
        await limiter.acquire()
        elapsed = time.monotonic() - t0
        # Should have waited ~500ms for the second acquire
        assert elapsed >= 0.4, f"Expected >= 400ms spacing, got {elapsed:.3f}s"

    @pytest.mark.asyncio
    async def test_respects_configured_rate(self):
        """Higher rate = shorter spacing."""
        limiter = _AsyncRateLimiter(max_per_second=10.0)  # 100ms spacing
        t0 = time.monotonic()
        await limiter.acquire()
        await limiter.acquire()
        elapsed = time.monotonic() - t0
        assert elapsed >= 0.08  # ~100ms
        assert elapsed < 0.3   # But not nearly as slow as 2 req/s

    @pytest.mark.asyncio
    async def test_no_wait_after_interval(self):
        """If enough time passes between acquires, no wait needed."""
        limiter = _AsyncRateLimiter(max_per_second=10.0)
        await limiter.acquire()
        await asyncio.sleep(0.15)  # Wait longer than 100ms interval
        t0 = time.monotonic()
        await limiter.acquire()
        elapsed = time.monotonic() - t0
        assert elapsed < 0.05  # Should be near-instant


# ═══════════════════════════════════════════════════════════════════════
# UNIT TESTS: _rate_limited_request 429 retry
# ═══════════════════════════════════════════════════════════════════════


def _make_client():
    """Create a TradierClient with mocked dependencies."""
    settings = MagicMock()
    settings.TRADIER_TOKEN = "test-token"
    settings.TRADIER_BASE_URL = "https://api.tradier.com/v1"
    settings.TRADIER_ACCOUNT_ID = "test-account"
    http_client = AsyncMock()
    cache = MagicMock()
    client = TradierClient(settings, http_client, cache)
    # Speed up rate limiter for tests
    client._rate_limiter = _AsyncRateLimiter(max_per_second=100.0)
    return client


class TestRateLimitedRequest429Retry:
    """Test 429 retry with exponential backoff."""

    @pytest.mark.asyncio
    async def test_429_retries_then_succeeds(self):
        """First call returns 429, second succeeds."""
        client = _make_client()
        call_count = 0

        async def mock_request_json(http_client, method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise UpstreamError(
                    "Upstream returned HTTP 429",
                    details={"status_code": 429, "url": url, "body": "rate limited"},
                )
            return {"quotes": {"quote": {"symbol": "SPY", "last": 500.0}}}

        with patch("app.clients.tradier_client.request_json", side_effect=mock_request_json):
            with patch("asyncio.sleep", new_callable=AsyncMock):  # Skip actual sleep
                result = await client._rate_limited_request("GET", "https://api.tradier.com/v1/markets/quotes")

        assert call_count == 2
        assert result["quotes"]["quote"]["symbol"] == "SPY"

    @pytest.mark.asyncio
    async def test_429_exhausts_retries(self):
        """All attempts return 429 → raises UpstreamError."""
        client = _make_client()
        call_count = 0

        async def mock_request_json(http_client, method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            raise UpstreamError(
                "Upstream returned HTTP 429",
                details={"status_code": 429, "url": url, "body": "rate limited"},
            )

        with patch("app.clients.tradier_client.request_json", side_effect=mock_request_json):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(UpstreamError) as exc_info:
                    await client._rate_limited_request("GET", "https://api.tradier.com/v1/markets/quotes")

        # Should have tried 1 + _MAX_429_RETRIES times
        assert call_count == _MAX_429_RETRIES + 1
        assert exc_info.value.details["status_code"] == 429

    @pytest.mark.asyncio
    async def test_non_429_error_not_retried(self):
        """A 401 error should not be retried."""
        client = _make_client()
        call_count = 0

        async def mock_request_json(http_client, method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            raise UpstreamError(
                "Upstream returned HTTP 401",
                details={"status_code": 401, "url": url, "body": "unauthorized"},
            )

        with patch("app.clients.tradier_client.request_json", side_effect=mock_request_json):
            with pytest.raises(UpstreamError) as exc_info:
                await client._rate_limited_request("GET", "https://api.tradier.com/v1/markets/quotes")

        assert call_count == 1  # No retries
        assert exc_info.value.details["status_code"] == 401

    @pytest.mark.asyncio
    async def test_429_backoff_delay_increases(self):
        """Verify exponential backoff: delays should be 2, 4, 8 seconds."""
        client = _make_client()
        sleep_delays = []

        async def mock_request_json(http_client, method, url, **kwargs):
            raise UpstreamError(
                "Upstream returned HTTP 429",
                details={"status_code": 429, "url": url, "body": "rate limited"},
            )

        async def mock_sleep(delay):
            sleep_delays.append(delay)

        with patch("app.clients.tradier_client.request_json", side_effect=mock_request_json):
            with patch("app.clients.tradier_client.asyncio.sleep", side_effect=mock_sleep):
                with pytest.raises(UpstreamError):
                    await client._rate_limited_request("GET", "https://api.tradier.com/v1/test")

        # Filter out rate limiter sub-second sleeps — only check 429 backoff delays
        backoff_delays = [d for d in sleep_delays if d >= 1.0]
        assert backoff_delays == [2, 4, 8]

    @pytest.mark.asyncio
    async def test_success_no_retry(self):
        """Successful request should not retry."""
        client = _make_client()
        call_count = 0

        async def mock_request_json(http_client, method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            return {"data": "ok"}

        with patch("app.clients.tradier_client.request_json", side_effect=mock_request_json):
            result = await client._rate_limited_request("GET", "https://api.tradier.com/v1/test")

        assert call_count == 1
        assert result == {"data": "ok"}


# ═══════════════════════════════════════════════════════════════════════
# INTEGRATION: rate limiter is wired into public methods
# ═══════════════════════════════════════════════════════════════════════


class TestRateLimiterWiring:
    """Verify all public methods go through the rate limiter."""

    @pytest.mark.asyncio
    async def test_rate_limiter_is_instance_attribute(self):
        """TradierClient should have _rate_limiter on init."""
        client = _make_client()
        assert hasattr(client, "_rate_limiter")
        assert isinstance(client._rate_limiter, _AsyncRateLimiter)

    @pytest.mark.asyncio
    async def test_get_balances_uses_rate_limiter(self):
        """get_balances() should flow through _rate_limited_request."""
        client = _make_client()
        client._rate_limited_request = AsyncMock(return_value={"balances": {}})
        result = await client.get_balances()
        client._rate_limited_request.assert_called_once()
        assert result == {"balances": {}}

    @pytest.mark.asyncio
    async def test_get_positions_uses_rate_limiter(self):
        """get_positions() should flow through _rate_limited_request."""
        client = _make_client()
        client._rate_limited_request = AsyncMock(return_value={"positions": {}})
        result = await client.get_positions()
        client._rate_limited_request.assert_called_once()
        assert result == {"positions": {}}

    @pytest.mark.asyncio
    async def test_get_orders_uses_rate_limiter(self):
        """get_orders() should flow through _rate_limited_request."""
        client = _make_client()
        client._rate_limited_request = AsyncMock(return_value={"orders": {}})
        result = await client.get_orders()
        client._rate_limited_request.assert_called_once()
        assert result == {"orders": {}}
