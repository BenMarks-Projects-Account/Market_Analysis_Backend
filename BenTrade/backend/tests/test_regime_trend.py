import unittest

from app.services.regime_service import RegimeService


class _DummyCache:
    async def get_or_set(self, _key, _ttl, fn):
        return await fn()


class _DummyFredSettings:
    FRED_VIX_SERIES_ID = "VIXCLS"


class _DummyFredClient:
    settings = _DummyFredSettings()
    http_client = None


class _DummyBaseDataService:
    def __init__(self, spy_history):
        self._spy_history = list(spy_history)
        self.fred_client = _DummyFredClient()

    async def get_snapshot(self, symbol: str):
        if symbol.upper() != "SPY":
            return {"underlying_price": None, "prices_history": []}
        return {
            "underlying_price": self._spy_history[-1] if self._spy_history else None,
            "prices_history": self._spy_history[-160:],
            "vix": None,
        }

    async def get_prices_history(self, symbol: str, lookback_days: int = 365):
        if symbol.upper() == "SPY":
            return self._spy_history
        return [100.0 + (i * 0.05) for i in range(60)]

    def _mark_success(self, *_args, **_kwargs):
        return None

    def _mark_failure(self, *_args, **_kwargs):
        return None

    def get_source_health_snapshot(self):
        return {}


class RegimeTrendTests(unittest.IsolatedAsyncioTestCase):
    async def test_trend_inputs_and_signals_present_with_full_history(self):
        prices = [400.0 + (i * 1.0) for i in range(260)]
        svc = RegimeService(base_data_service=_DummyBaseDataService(prices), cache=_DummyCache())
        async def _no_fred(*_args, **_kwargs):
            return []
        svc._fred_recent_values = _no_fred

        payload = await svc._compute()
        trend = payload.get("components", {}).get("trend", {})

        self.assertIn("raw_points", trend)
        self.assertIn("inputs", trend)
        self.assertIn("signals", trend)
        self.assertGreater(float(trend.get("score") or 0.0), 0.0)
        self.assertGreater(len(trend.get("signals") or []), 0)

        inputs = trend.get("inputs") or {}
        for key in ("close", "ema20", "ema50", "sma50", "sma200", "close_gt_ema20", "close_gt_ema50", "sma50_gt_sma200"):
            self.assertIn(key, inputs)

    async def test_trend_partial_scoring_with_insufficient_history(self):
        prices = [500.0 + (i * 1.0) for i in range(80)]
        svc = RegimeService(base_data_service=_DummyBaseDataService(prices), cache=_DummyCache())
        async def _no_fred(*_args, **_kwargs):
            return []
        svc._fred_recent_values = _no_fred

        payload = await svc._compute()
        trend = payload.get("components", {}).get("trend", {})
        inputs = trend.get("inputs") or {}
        notes = payload.get("suggested_playbook", {}).get("notes") or []

        self.assertIsNone(inputs.get("sma200"))
        self.assertGreater(float(trend.get("score") or 0.0), 0.0)
        self.assertTrue(any("Insufficient history for SMA200" in str(note) for note in notes))


if __name__ == "__main__":
    unittest.main()
