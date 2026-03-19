"""Tests for GET /api/regime/proxies endpoint logic."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from app.api.routes_regime import _PROXY_SYMBOLS, get_regime_proxies


class _FakeRequest:
    """Minimal stand-in for FastAPI Request."""

    def __init__(self, bds, cache=None):
        self.app = MagicMock()
        self.app.state.base_data_service = bds
        self.app.state.cache = cache


class _DummyCache:
    """In-memory async TTL cache stub."""

    def __init__(self):
        self._store: dict = {}

    async def get(self, key):
        return self._store.get(key)

    async def set(self, key, value, ttl):
        self._store[key] = value


class TestRegimeProxies(unittest.TestCase):

    def _run(self, coro):
        return asyncio.run(coro)

    def test_returns_all_proxy_symbols(self):
        bds = MagicMock()
        bds.get_intraday_bars = AsyncMock(return_value=[
            {"date": "2026-03-17T10:00:00+00:00", "close": 100.0},
            {"date": "2026-03-18T15:00:00+00:00", "close": 110.0},
        ])
        bds.get_prices_history_dated = AsyncMock(return_value=[])
        req = _FakeRequest(bds, cache=_DummyCache())
        result = self._run(get_regime_proxies(req))

        self.assertIn("proxies", result)
        self.assertIn("as_of", result)
        for sym in _PROXY_SYMBOLS:
            self.assertIn(sym, result["proxies"])
            entry = result["proxies"][sym]
            self.assertEqual(entry["symbol"], sym)
            self.assertIsInstance(entry["history"], list)
            self.assertGreater(len(entry["history"]), 0)
            self.assertEqual(entry["bar_size"], "1h")

    def test_change_pct_calculated(self):
        bds = MagicMock()
        bds.get_intraday_bars = AsyncMock(return_value=[
            {"date": "2026-03-17T10:00:00+00:00", "close": 100.0},
            {"date": "2026-03-18T15:00:00+00:00", "close": 110.0},
        ])
        bds.get_prices_history_dated = AsyncMock(return_value=[])
        req = _FakeRequest(bds, cache=_DummyCache())
        result = self._run(get_regime_proxies(req))

        entry = result["proxies"]["VTI"]
        self.assertAlmostEqual(entry["change_pct"], 0.1, places=3)

    def test_handles_fetch_failure_gracefully(self):
        bds = MagicMock()
        bds.get_intraday_bars = AsyncMock(side_effect=Exception("API down"))
        bds.get_prices_history_dated = AsyncMock(side_effect=Exception("API down"))
        req = _FakeRequest(bds, cache=_DummyCache())
        result = self._run(get_regime_proxies(req))

        for sym in _PROXY_SYMBOLS:
            entry = result["proxies"][sym]
            self.assertEqual(entry["history"], [])
            self.assertIsNone(entry["change_pct"])

    def test_caches_result(self):
        cache = _DummyCache()
        bds = MagicMock()
        bds.get_intraday_bars = AsyncMock(return_value=[
            {"date": "2026-03-17T10:00:00+00:00", "close": 50.0},
            {"date": "2026-03-18T15:00:00+00:00", "close": 55.0},
        ])
        bds.get_prices_history_dated = AsyncMock(return_value=[])
        req = _FakeRequest(bds, cache=cache)

        # First call — populates cache
        self._run(get_regime_proxies(req))
        self.assertEqual(bds.get_intraday_bars.call_count, len(_PROXY_SYMBOLS))

        # Second call — should use cache
        bds.get_intraday_bars.reset_mock()
        self._run(get_regime_proxies(req))
        bds.get_intraday_bars.assert_not_called()

    def test_empty_history(self):
        bds = MagicMock()
        bds.get_intraday_bars = AsyncMock(return_value=[])
        bds.get_prices_history_dated = AsyncMock(return_value=[])
        req = _FakeRequest(bds, cache=_DummyCache())
        result = self._run(get_regime_proxies(req))

        entry = result["proxies"]["VTI"]
        self.assertEqual(entry["history"], [])
        self.assertIsNone(entry["change_pct"])
        self.assertEqual(entry["bar_size"], "daily")

    def test_works_without_cache(self):
        bds = MagicMock()
        bds.get_intraday_bars = AsyncMock(return_value=[
            {"date": "2026-03-18T10:00:00+00:00", "close": 100.0},
        ])
        bds.get_prices_history_dated = AsyncMock(return_value=[])
        req = _FakeRequest(bds, cache=None)
        result = self._run(get_regime_proxies(req))

        self.assertIn("proxies", result)
        self.assertEqual(len(result["proxies"]), len(_PROXY_SYMBOLS))

    def test_falls_back_to_daily_when_intraday_empty(self):
        bds = MagicMock()
        bds.get_intraday_bars = AsyncMock(return_value=[])  # intraday unavailable
        bds.get_prices_history_dated = AsyncMock(return_value=[
            {"date": "2026-03-17", "close": 200.0},
            {"date": "2026-03-18", "close": 210.0},
        ])
        req = _FakeRequest(bds, cache=_DummyCache())
        result = self._run(get_regime_proxies(req))

        entry = result["proxies"]["VTI"]
        self.assertEqual(entry["bar_size"], "daily")
        self.assertEqual(len(entry["history"]), 2)
        self.assertAlmostEqual(entry["change_pct"], 0.05, places=3)

    def test_intraday_preferred_over_daily(self):
        bds = MagicMock()
        bds.get_intraday_bars = AsyncMock(return_value=[
            {"date": "2026-03-18T09:30:00+00:00", "close": 300.0},
            {"date": "2026-03-18T10:00:00+00:00", "close": 305.0},
        ])
        bds.get_prices_history_dated = AsyncMock(return_value=[
            {"date": "2026-03-17", "close": 290.0},
        ])
        req = _FakeRequest(bds, cache=_DummyCache())
        result = self._run(get_regime_proxies(req))

        entry = result["proxies"]["VTI"]
        self.assertEqual(entry["bar_size"], "1h")
        # Daily fallback should NOT have been called
        bds.get_prices_history_dated.assert_not_called()


if __name__ == "__main__":
    unittest.main()
