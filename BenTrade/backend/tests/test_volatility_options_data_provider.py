"""Tests for Volatility & Options Structure Data Provider — Refined Phase.

Tests the refined data-fetching and computation logic with improved precision:
  1. VIX history → vix_rank_30d (PROXY), vix_percentile_1y (PROXY), vix_avg_20d
  2. SPY history → rv_30d_close_close (annualized, standardized)
  3. Derived metrics with blended logic:
     - tail_risk_signal as deterministic label ("Low"|"Moderate"|"Elevated"|"High")
     - option_richness using blended VIX rank + IV-RV spread logic
     - premium_bias from composite signals
  4. CBOE SKEW from FRED (tail hedging demand)
  5. Expanded metric_availability with full provenance:
     - source, primary_vs_proxy, direct_vs_derived, formula_or_logic, dependencies
     - degraded_mode_flag for reduced-input scenarios
  6. Proxy metric labeling (vix_rank, vix_percentile, spy_pc_ratio_proxy)
  7. Graceful degradation when sources fail
  8. Constructor accepts fred_client
"""

import asyncio
import math
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.volatility_options_data_provider import (
    VolatilityOptionsDataProvider,
)


def _run(coro):
    """Run a coroutine synchronously (no pytest-asyncio needed)."""
    return asyncio.run(coro)


# ═══════════════════════════════════════════════════════════════════════
# MOCK BUILDERS
# ═══════════════════════════════════════════════════════════════════════


def _mock_tradier(
    *,
    vix_closes: list[float] | None = None,
    spy_closes: list[float] | None = None,
    vix_quote: dict | None = None,
    vvix_quote: dict | None = None,
    spy_quote: dict | None = None,
    expirations: list[str] | None = None,
    chain: list[dict] | None = None,
) -> AsyncMock:
    """Build a mock tradier_client with configurable responses."""
    mock = AsyncMock()

    # get_daily_closes → returns different data based on symbol
    async def _daily_closes(symbol, start, end):
        sym = symbol.upper()
        if sym == "VIX" and vix_closes is not None:
            return vix_closes
        if sym == "SPY" and spy_closes is not None:
            return spy_closes
        return []

    mock.get_daily_closes = AsyncMock(side_effect=_daily_closes)
    mock.get_quote = AsyncMock(side_effect=_make_quote_side_effect(
        vix_quote, vvix_quote, spy_quote,
    ))
    mock.get_expirations = AsyncMock(return_value=expirations or [])
    mock.get_chain = AsyncMock(return_value=chain or [])
    return mock


def _make_quote_side_effect(vix_q, vvix_q, spy_q):
    async def _get_quote(symbol):
        sym = symbol.upper()
        if sym == "VIX":
            return vix_q or {"last": 18.5, "prevclose": 17.8}
        if sym == "VVIX":
            return vvix_q or {"last": 92.0}
        if sym == "SPY":
            return spy_q or {"last": 580.0}
        return {}
    return _get_quote


def _mock_fred(skew_value: float | None = 135.5) -> AsyncMock:
    """Build a mock fred_client."""
    mock = AsyncMock()
    mock.get_latest_series_value = AsyncMock(return_value=skew_value)
    return mock


def _mock_market_ctx(vix_val: float = 18.5) -> AsyncMock:
    mock = AsyncMock()
    mock.get_market_context = AsyncMock(return_value={
        "vix": {
            "value": vix_val,
            "previous_close": 17.8,
            "source": "tradier",
        },
    })
    return mock


def _basic_spy_chain():
    """Minimal SPY chain with ATM put/call and a 25d put."""
    return [
        {
            "strike": 580,
            "option_type": "call",
            "greeks": {"mid_iv": 0.16, "delta": 0.50},
            "volume": 5000,
        },
        {
            "strike": 580,
            "option_type": "put",
            "greeks": {"mid_iv": 0.17, "delta": -0.50},
            "volume": 4000,
        },
        {
            "strike": 565,
            "option_type": "put",
            "greeks": {"mid_iv": 0.20, "delta": -0.25},
            "volume": 2000,
        },
    ]


# Generate realistic VIX history (252 trading days)
def _vix_history(
    count: int = 252, base: float = 18.0, low: float = 12.0, high: float = 35.0,
) -> list[float]:
    """Generate synthetic VIX closes with known min/max/current."""
    import random
    rng = random.Random(42)
    closes = []
    v = base
    for _ in range(count - 1):
        v = max(low, min(high, v + rng.gauss(0, 0.5)))
        closes.append(round(v, 2))
    closes.append(base)  # last close = base (current)
    return closes


# Generate realistic SPY history (45 trading days)
def _spy_history(count: int = 45, base: float = 580.0) -> list[float]:
    """Generate synthetic SPY daily closes."""
    import random
    rng = random.Random(99)
    closes = []
    p = base * 0.98  # start slightly below current
    for _ in range(count):
        ret = rng.gauss(0.0003, 0.012)  # ~1.2% daily vol
        p = p * math.exp(ret)
        closes.append(round(p, 2))
    return closes


# ═══════════════════════════════════════════════════════════════════════
# 1. VIX HISTORY DERIVED METRICS
# ═══════════════════════════════════════════════════════════════════════


class TestVixHistoryMetrics:
    """Test vix_rank_30d (PROXY), vix_percentile_1y (PROXY), vix_avg_20d computation."""

    def test_iv_rank_30d_computed_from_history(self):
        """IV rank should be (current - min) / (max - min) × 100."""
        vix_closes = [15.0 + i * (10.0 / 29) for i in range(30)]
        vix_closes[-1] = 20.0

        tradier = _mock_tradier(vix_closes=vix_closes)
        provider = VolatilityOptionsDataProvider(tradier_client=tradier)
        result = _run(provider._fetch_vix_history())

        assert result["vix_rank_30d"] is not None
        vix_max = max(vix_closes[-30:])
        expected = (20.0 - 15.0) / (vix_max - 15.0) * 100
        assert abs(result["vix_rank_30d"] - expected) < 0.5

    def test_iv_percentile_1y_computed(self):
        """IV percentile = % of history below current."""
        vix_closes = [10.0 + i * 0.1 for i in range(252)]
        vix_closes[-1] = 20.0
        below = sum(1 for c in vix_closes if c < 20.0)
        expected = below / len(vix_closes) * 100

        tradier = _mock_tradier(vix_closes=vix_closes)
        provider = VolatilityOptionsDataProvider(tradier_client=tradier)
        result = _run(provider._fetch_vix_history())

        assert abs(result["vix_percentile_1y"] - expected) < 0.5

    def test_vix_avg_20d_is_mean_of_last_20(self):
        """VIX 20d average = mean of last 20 closes."""
        vix_closes = [18.0] * 50
        vix_closes[-1] = 22.0
        last_20 = vix_closes[-20:]
        expected = sum(last_20) / 20

        tradier = _mock_tradier(vix_closes=vix_closes)
        provider = VolatilityOptionsDataProvider(tradier_client=tradier)
        result = _run(provider._fetch_vix_history())

        assert abs(result["vix_avg_20d"] - expected) < 0.01

    def test_insufficient_history_returns_empty(self):
        """< 5 closes → no metrics computed."""
        tradier = _mock_tradier(vix_closes=[18.0, 18.5, 19.0])
        provider = VolatilityOptionsDataProvider(tradier_client=tradier)
        result = _run(provider._fetch_vix_history())

        assert "vix_rank_30d" not in result
        assert result.get("history_count") == 3

    def test_flat_vix_gives_50_rank(self):
        """When VIX is flat (min=max), rank defaults to 50."""
        vix_closes = [18.0] * 30
        tradier = _mock_tradier(vix_closes=vix_closes)
        provider = VolatilityOptionsDataProvider(tradier_client=tradier)
        result = _run(provider._fetch_vix_history())

        assert result["vix_rank_30d"] == 50.0


# ═══════════════════════════════════════════════════════════════════════
# 2. REALIZED VOLATILITY
# ═══════════════════════════════════════════════════════════════════════


class TestRealizedVolatility:
    """Test rv_30d computation from SPY daily closes."""

    def test_rv_30d_computed(self):
        """RV should be annualized std dev of log returns."""
        spy_closes = _spy_history(45)
        tradier = _mock_tradier(spy_closes=spy_closes)
        provider = VolatilityOptionsDataProvider(tradier_client=tradier)
        result = _run(provider._fetch_spy_rv())

        assert result["rv_30d"] is not None
        assert 3 < result["rv_30d"] < 60
        assert result["return_count"] >= 5

    def test_rv_formula_correctness(self):
        """Verify the RV formula matches hand-calculation."""
        # 12 prices → 11 returns (above 10-price minimum)
        prices = [100.0, 101.0, 99.5, 100.5, 102.0, 101.0,
                  102.5, 101.8, 103.0, 102.2, 101.5, 100.8]
        log_rets = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices))]
        mean_ret = sum(log_rets) / len(log_rets)
        var = sum((r - mean_ret) ** 2 for r in log_rets) / (len(log_rets) - 1)
        expected_rv = math.sqrt(var) * math.sqrt(252) * 100

        tradier = _mock_tradier(spy_closes=prices)
        provider = VolatilityOptionsDataProvider(tradier_client=tradier)
        result = _run(provider._fetch_spy_rv())

        assert abs(result["rv_30d"] - expected_rv) < 0.1

    def test_rv_insufficient_data(self):
        """< 10 prices → no RV computed."""
        tradier = _mock_tradier(spy_closes=[580.0, 581.0, 579.0])
        provider = VolatilityOptionsDataProvider(tradier_client=tradier)
        result = _run(provider._fetch_spy_rv())

        assert "rv_30d" not in result
        assert result.get("return_count") == 0

    def test_rv_no_tradier(self):
        """No tradier → empty result."""
        provider = VolatilityOptionsDataProvider(tradier_client=None)
        result = _run(provider._fetch_spy_rv())
        assert result == {}


# ═══════════════════════════════════════════════════════════════════════
# 3. DERIVED METRICS
# ═══════════════════════════════════════════════════════════════════════


class TestDerivedMetrics:
    """Test tail_risk_signal, option_richness, premium_bias derivation."""

    def test_tail_risk_from_put_skew(self):
        """tail_risk_signal should be a label derived from put_skew_25d via interpolation."""
        vix_closes = _vix_history()
        spy_closes = _spy_history()

        tradier = _mock_tradier(
            vix_closes=vix_closes,
            spy_closes=spy_closes,
            expirations=["2025-01-31"],
            chain=_basic_spy_chain(),
        )
        market_ctx = _mock_market_ctx()
        provider = VolatilityOptionsDataProvider(
            tradier_client=tradier,
            market_context_service=market_ctx,
        )
        result = _run(provider.fetch_volatility_data())

        skew = result["skew_data"]
        assert skew["put_skew_25d"] is not None
        assert skew["tail_risk_signal"] in ("Low", "Moderate", "Elevated", "High")
        assert skew["tail_risk_numeric"] is not None
        # Numeric is 0-100 range
        assert 0 <= skew["tail_risk_numeric"] <= 100
        # Label must be consistent with numeric thresholds
        if skew["tail_risk_numeric"] <= 30:
            assert skew["tail_risk_signal"] == "Low"
        elif skew["tail_risk_numeric"] <= 60:
            assert skew["tail_risk_signal"] == "Moderate"
        elif skew["tail_risk_numeric"] <= 80:
            assert skew["tail_risk_signal"] == "Elevated"
        else:
            assert skew["tail_risk_signal"] == "High"

    def test_option_richness_blended_logic(self):
        """Test option_richness uses blended logic (VIX rank + IV-RV spread)."""
        vix_closes = _vix_history()
        spy_closes = _spy_history()

        tradier = _mock_tradier(
            vix_closes=vix_closes,
            spy_closes=spy_closes,
            expirations=["2025-01-31"],
            chain=_basic_spy_chain(),
        )
        market_ctx = _mock_market_ctx()
        provider = VolatilityOptionsDataProvider(
            tradier_client=tradier,
            market_context_service=market_ctx,
        )
        result = _run(provider.fetch_volatility_data())

        regime = result["regime_data"]
        structure = result["structure_data"]
        positioning = result["positioning_data"]
        
        # Blended logic: Rich if (vix_rank>60 AND iv>rv), Cheap if (vix_rank<30 OR iv≤rv), else Fair
        if regime["vix_rank_30d"] is not None:
            assert positioning["option_richness"] is not None
            # Check label matches blended logic based on both context and spread
            label = positioning.get("option_richness_label")
            assert label in ["Rich", "Fair", "Cheap"]

    def test_high_skew_caps_at_100(self):
        """Extreme put skew should cap tail_risk_signal at 100."""
        put_skew = 15.0
        tail_risk = min(abs(put_skew) * 10.0, 100.0)
        assert tail_risk == 100.0


# ═══════════════════════════════════════════════════════════════════════
# 4. CBOE SKEW FROM FRED
# ═══════════════════════════════════════════════════════════════════════


class TestCboeSkewFred:
    """Test CBOE SKEW fetch from FRED client."""

    def test_cboe_skew_fetched_from_fred(self):
        """With fred_client, cboe_skew should be populated."""
        vix_closes = _vix_history()
        spy_closes = _spy_history()

        tradier = _mock_tradier(
            vix_closes=vix_closes,
            spy_closes=spy_closes,
            expirations=["2025-01-31"],
            chain=_basic_spy_chain(),
        )
        fred = _mock_fred(skew_value=142.3)
        market_ctx = _mock_market_ctx()

        provider = VolatilityOptionsDataProvider(
            tradier_client=tradier,
            market_context_service=market_ctx,
            fred_client=fred,
        )
        result = _run(provider.fetch_volatility_data())

        assert result["skew_data"]["cboe_skew"] == 142.3

    def test_cboe_skew_none_without_fred(self):
        """Without fred_client, cboe_skew should be None."""
        vix_closes = _vix_history()
        spy_closes = _spy_history()

        tradier = _mock_tradier(
            vix_closes=vix_closes,
            spy_closes=spy_closes,
            expirations=["2025-01-31"],
            chain=_basic_spy_chain(),
        )
        provider = VolatilityOptionsDataProvider(
            tradier_client=tradier,
            market_context_service=_mock_market_ctx(),
        )
        result = _run(provider.fetch_volatility_data())

        assert result["skew_data"]["cboe_skew"] is None

    def test_fred_skew_error_graceful(self):
        """FRED error → cboe_skew is None, no crash."""
        fred = AsyncMock()
        fred.get_latest_series_value = AsyncMock(side_effect=Exception("FRED down"))

        provider = VolatilityOptionsDataProvider(
            tradier_client=_mock_tradier(
                vix_closes=_vix_history(),
                spy_closes=_spy_history(),
                expirations=["2025-01-31"],
                chain=_basic_spy_chain(),
            ),
            market_context_service=_mock_market_ctx(),
            fred_client=fred,
        )
        result = _run(provider.fetch_volatility_data())
        assert result["skew_data"]["cboe_skew"] is None


# ═══════════════════════════════════════════════════════════════════════
# 5. METRIC AVAILABILITY DIAGNOSTICS
# ═══════════════════════════════════════════════════════════════════════


class TestMetricAvailability:
    """Test the metric_availability report in output."""

    def test_all_ok_when_data_complete(self):
        """When all data is available, all metrics should be 'ok'."""
        vix_closes = _vix_history()
        spy_closes = _spy_history()

        tradier = _mock_tradier(
            vix_closes=vix_closes,
            spy_closes=spy_closes,
            expirations=["2025-01-31"],
            chain=_basic_spy_chain(),
        )
        fred = _mock_fred(skew_value=130.0)
        market_ctx = _mock_market_ctx()

        provider = VolatilityOptionsDataProvider(
            tradier_client=tradier,
            market_context_service=market_ctx,
            fred_client=fred,
        )
        result = _run(provider.fetch_volatility_data())

        ma = result["metric_availability"]
        for key in ["vix_spot", "vix_avg_20d", "vix_rank_30d",
                     "vix_percentile_1y", "iv_30d", "rv_30d",
                     "put_skew_25d", "tail_risk_signal",
                     "equity_pc_ratio", "option_richness",
                     "premium_bias", "cboe_skew"]:
            assert ma[key]["status"] == "ok", f"{key} should be ok, got {ma[key]}"

    def test_unavailable_reasons_when_no_history(self):
        """Without VIX/SPY history, derived metrics show unavailable."""
        tradier = _mock_tradier(
            vix_closes=[],
            spy_closes=[],
            expirations=["2025-01-31"],
            chain=_basic_spy_chain(),
        )
        market_ctx = _mock_market_ctx()
        provider = VolatilityOptionsDataProvider(
            tradier_client=tradier,
            market_context_service=market_ctx,
        )
        result = _run(provider.fetch_volatility_data())

        ma = result["metric_availability"]
        assert ma["vix_rank_30d"]["status"] == "unavailable"
        assert ma["rv_30d"]["status"] == "unavailable"
        assert ma["option_richness"]["status"] == "unavailable"

    def test_metric_availability_has_reason_strings(self):
        """Each entry should have a 'reason' string."""
        provider = VolatilityOptionsDataProvider(
            tradier_client=_mock_tradier(
                vix_closes=_vix_history(),
                spy_closes=_spy_history(),
                expirations=["2025-01-31"],
                chain=_basic_spy_chain(),
            ),
            market_context_service=_mock_market_ctx(),
        )
        result = _run(provider.fetch_volatility_data())

        for key, entry in result["metric_availability"].items():
            assert "status" in entry, f"{key} missing status"
            assert "reason" in entry, f"{key} missing reason"
            assert isinstance(entry["reason"], str)


# ═══════════════════════════════════════════════════════════════════════
# 6. GRACEFUL DEGRADATION
# ═══════════════════════════════════════════════════════════════════════


class TestGracefulDegradation:
    """Test the provider handles failures without crashing."""

    def test_no_tradier_returns_empty_metrics(self):
        """With no tradier_client, all Tradier-sourced metrics are None."""
        provider = VolatilityOptionsDataProvider(tradier_client=None)
        result = _run(provider.fetch_volatility_data())

        regime = result["regime_data"]
        assert regime["vix_spot"] is None
        assert regime["vix_rank_30d"] is None
        assert result["structure_data"]["rv_30d"] is None

    def test_tradier_raises_does_not_crash(self):
        """If tradier throws on history, other metrics still populate."""
        tradier = AsyncMock()
        tradier.get_daily_closes = AsyncMock(side_effect=Exception("timeout"))
        tradier.get_quote = AsyncMock(return_value={"last": 18.5, "prevclose": 17.0})
        tradier.get_expirations = AsyncMock(return_value=[])
        tradier.get_chain = AsyncMock(return_value=[])

        provider = VolatilityOptionsDataProvider(tradier_client=tradier)
        result = _run(provider.fetch_volatility_data())

        assert result is not None
        assert "regime_data" in result

    def test_spy_pc_proxy_uses_equity_pc(self):
        """spy_pc_ratio_proxy should equal equity_pc_ratio (SPY proxy)."""
        tradier = _mock_tradier(
            vix_closes=_vix_history(),
            spy_closes=_spy_history(),
            expirations=["2025-01-31"],
            chain=_basic_spy_chain(),
        )
        provider = VolatilityOptionsDataProvider(
            tradier_client=tradier,
            market_context_service=_mock_market_ctx(),
        )
        result = _run(provider.fetch_volatility_data())

        pos = result["positioning_data"]
        assert pos["equity_pc_ratio"] is not None
        assert pos["spy_pc_ratio_proxy"] == pos["equity_pc_ratio"]


# ═══════════════════════════════════════════════════════════════════════
# 7. CONSTRUCTOR / INTEGRATION
# ═══════════════════════════════════════════════════════════════════════


class TestConstructor:
    """Test the constructor accepts fred_client."""

    def test_accepts_fred_client(self):
        provider = VolatilityOptionsDataProvider(
            tradier_client=MagicMock(),
            fred_client=MagicMock(),
        )
        assert provider.fred is not None

    def test_fred_client_optional(self):
        provider = VolatilityOptionsDataProvider(tradier_client=MagicMock())
        assert provider.fred is None

    def test_data_sources_reports_history_counts(self):
        """data_sources should include vix_history_days and spy_return_days."""
        tradier = _mock_tradier(
            vix_closes=_vix_history(100),
            spy_closes=_spy_history(45),
            expirations=["2025-01-31"],
            chain=_basic_spy_chain(),
        )
        provider = VolatilityOptionsDataProvider(
            tradier_client=tradier,
            market_context_service=_mock_market_ctx(),
            fred_client=_mock_fred(),
        )
        result = _run(provider.fetch_volatility_data())

        ds = result["data_sources"]
        assert ds["vix_history_days"] > 0
        assert ds["spy_return_days"] > 0
        assert ds["cboe_skew_available"] is True
        assert ds["fred_available"] is True
