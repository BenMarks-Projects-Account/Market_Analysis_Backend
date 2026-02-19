"""Tests for Polygon client parsing and base_data_service integration."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.clients.polygon_client import PolygonClient
from app.services.base_data_service import BaseDataService


def _run(coro):
    """Python 3.14-safe helper to run a coroutine synchronously."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

class _DummySettings:
    POLYGON_API_KEY = "test-key"
    POLYGON_BASE_URL = "https://api.polygon.io"
    TRADIER_TOKEN = "x"
    FINNHUB_KEY = "x"
    FRED_KEY = "x"
    CANDLES_CACHE_TTL_SECONDS = 1800
    QUOTE_CACHE_TTL_SECONDS = 10


class _NoKeySettings(_DummySettings):
    POLYGON_API_KEY = ""


class _NullCache:
    """Bypass cache â€“ always call the loader."""
    async def get_or_set(self, _key: str, _ttl: int, loader):
        return await loader()


class _DummyClient:
    """Generic duck-typed client for slots the test doesn't exercise."""
    def __init__(self):
        self.settings = _DummySettings()


SAMPLE_POLYGON_RESPONSE: dict[str, Any] = {
    "ticker": "SPY",
    "queryCount": 5,
    "resultsCount": 5,
    "adjusted": True,
    "status": "OK",
    "results": [
        {"v": 100000, "vw": 510.5, "o": 509.0, "c": 511.2, "h": 512.0, "l": 508.5, "t": 1770681600000, "n": 500},
        {"v": 110000, "vw": 512.0, "o": 511.0, "c": 513.4, "h": 514.0, "l": 510.0, "t": 1770768000000, "n": 600},
        {"v": 90000, "vw": 513.0, "o": 513.0, "c": 512.1, "h": 515.0, "l": 511.5, "t": 1770854400000, "n": 550},
        {"v": 105000, "vw": 511.5, "o": 512.0, "c": 510.8, "h": 513.0, "l": 509.0, "t": 1770940800000, "n": 520},
        {"v": 95000, "vw": 510.0, "o": 510.5, "c": 511.0, "h": 512.5, "l": 509.5, "t": 1771027200000, "n": 480},
    ],
}

EMPTY_POLYGON_RESPONSE: dict[str, Any] = {
    "ticker": "ZZZZZ",
    "queryCount": 0,
    "resultsCount": 0,
    "adjusted": True,
    "status": "OK",
    "results": [],
}


# ---------------------------------------------------------------------------
# Unit tests â€“ PolygonClient._parse_aggs
# ---------------------------------------------------------------------------

class TestParseAggs:
    def test_normal_bars(self) -> None:
        bars = PolygonClient._parse_aggs(SAMPLE_POLYGON_RESPONSE)
        assert len(bars) == 5
        assert bars[0]["date"] == "2026-02-10"
        assert bars[0]["close"] == 511.2
        assert bars[0]["open"] == 509.0
        assert bars[0]["high"] == 512.0
        assert bars[0]["low"] == 508.5
        assert bars[0]["volume"] == 100000

    def test_empty_results(self) -> None:
        bars = PolygonClient._parse_aggs(EMPTY_POLYGON_RESPONSE)
        assert bars == []

    def test_missing_timestamp_skipped(self) -> None:
        payload = {"results": [{"o": 1, "h": 2, "l": 0.5, "c": 1.5}]}
        bars = PolygonClient._parse_aggs(payload)
        assert bars == []

    def test_missing_ohlc_skipped(self) -> None:
        payload = {"results": [{"t": 1739145600000}]}
        bars = PolygonClient._parse_aggs(payload)
        assert bars == []

    def test_no_results_key(self) -> None:
        bars = PolygonClient._parse_aggs({"status": "OK"})
        assert bars == []


# ---------------------------------------------------------------------------
# Unit tests â€“ PolygonClient.get_daily_closes
# ---------------------------------------------------------------------------

class TestGetDailyCloses:
    def test_returns_flat_close_list(self) -> None:
        http = MagicMock()
        client = PolygonClient(settings=_DummySettings(), http_client=http, cache=_NullCache())

        with patch.object(client, "_request_with_retry", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = SAMPLE_POLYGON_RESPONSE
            closes = _run(client.get_daily_closes("SPY"))

        assert closes == [511.2, 513.4, 512.1, 510.8, 511.0]

    def test_empty_ticker_returns_empty(self) -> None:
        http = MagicMock()
        client = PolygonClient(settings=_DummySettings(), http_client=http, cache=_NullCache())
        closes = _run(client.get_daily_closes(""))
        assert closes == []


# ---------------------------------------------------------------------------
# Unit tests â€“ PolygonClient.health
# ---------------------------------------------------------------------------

class TestPolygonHealth:
    def test_health_returns_false_when_no_api_key(self) -> None:
        http = MagicMock()
        client = PolygonClient(settings=_NoKeySettings(), http_client=http, cache=_NullCache())
        result = _run(client.health())
        assert result is False


# ---------------------------------------------------------------------------
# Integration-style: BaseDataService.get_prices_history via Polygon
# ---------------------------------------------------------------------------

class TestBaseDataServicePolygonIntegration:
    def _make_service(self, polygon_closes: list[float] | None = None) -> BaseDataService:
        """Build a BaseDataService with a mocked polygon_client."""
        polygon = MagicMock()
        polygon.settings = _DummySettings()
        polygon.get_daily_closes = AsyncMock(
            return_value=polygon_closes if polygon_closes is not None else []
        )

        svc = BaseDataService(
            tradier_client=_DummyClient(),
            finnhub_client=_DummyClient(),
            fred_client=_DummyClient(),
            polygon_client=polygon,
        )
        return svc

    def test_polygon_returns_closes(self) -> None:
        svc = self._make_service(polygon_closes=[500.0, 501.0, 502.0])
        closes = _run(svc.get_prices_history("SPY"))
        assert closes == [500.0, 501.0, 502.0]

    def test_polygon_empty_falls_back_to_tradier(self) -> None:
        svc = self._make_service(polygon_closes=[])
        # Tradier fallback will also fail (duck-typed dummy), so we get []
        closes = _run(svc.get_prices_history("SPY"))
        assert closes == []

    def test_polygon_exception_falls_back(self) -> None:
        polygon = MagicMock()
        polygon.settings = _DummySettings()
        polygon.get_daily_closes = AsyncMock(side_effect=Exception("Polygon down"))

        svc = BaseDataService(
            tradier_client=_DummyClient(),
            finnhub_client=_DummyClient(),
            fred_client=_DummyClient(),
            polygon_client=polygon,
        )
        # Both Polygon and Tradier fail â†’ []
        closes = _run(svc.get_prices_history("SPY"))
        assert closes == []

    def test_no_polygon_client_falls_back(self) -> None:
        svc = BaseDataService(
            tradier_client=_DummyClient(),
            finnhub_client=_DummyClient(),
            fred_client=_DummyClient(),
            polygon_client=None,
        )
        closes = _run(svc.get_prices_history("SPY"))
        assert closes == []

    def test_source_health_shows_polygon_configured(self) -> None:
        svc = self._make_service()
        assert svc._source_configured("polygon") is True

    def test_source_health_shows_polygon_misconfigured(self) -> None:
        polygon = MagicMock()
        polygon.settings = _NoKeySettings()
        svc = BaseDataService(
            tradier_client=_DummyClient(),
            finnhub_client=_DummyClient(),
            fred_client=_DummyClient(),
            polygon_client=polygon,
        )
        assert svc._source_configured("polygon") is False
        snapshot = svc.get_source_health_snapshot()
        assert snapshot["polygon"]["message"] == "misconfigured"
