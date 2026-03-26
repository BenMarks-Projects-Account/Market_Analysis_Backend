"""Tests for PreMarketIntelligenceService.

Pure-unit tests for gap classification and overnight signal computation,
plus one integration test against live data (requires network).

Run from backend/: python -m pytest tests/test_pre_market_intelligence.py -v
"""
import pytest

from app.services.pre_market_intelligence import (
    PreMarketIntelligenceService,
    classify_gap,
    compute_overnight_signal,
    check_position_exposure,
    _gap_to_score,
)
from app.clients.futures_client import FuturesClient
from app.config import Settings
from app.utils.cache import TTLCache


# ── Unit: classify_gap ──────────────────────────────────────────────


def test_classify_gap_large_up():
    result = classify_gap(5000.0, 5060.0)  # +1.2%
    assert result["classification"] == "large_gap_up"
    assert result["gap_pct"] > 0.01


def test_classify_gap_up():
    result = classify_gap(5000.0, 5030.0)  # +0.6%
    assert result["classification"] == "gap_up"


def test_classify_gap_flat():
    result = classify_gap(5000.0, 5010.0)  # +0.2%
    assert result["classification"] == "flat"


def test_classify_gap_flat_negative():
    result = classify_gap(5000.0, 4990.0)  # -0.2%
    assert result["classification"] == "flat"


def test_classify_gap_down():
    result = classify_gap(5000.0, 4965.0)  # -0.7%
    assert result["classification"] == "gap_down"


def test_classify_gap_large_down():
    result = classify_gap(5000.0, 4940.0)  # -1.2%
    assert result["classification"] == "large_gap_down"
    assert result["gap_pct"] < -0.01


def test_classify_gap_zero_prior_close():
    result = classify_gap(0, 100)
    assert result["classification"] == "unknown"


def test_classify_gap_fields():
    result = classify_gap(5000.0, 5060.0)
    assert "gap_pct" in result
    assert "gap_points" in result
    assert "prior_close" in result
    assert "current" in result
    assert result["gap_points"] == 60.0
    assert result["prior_close"] == 5000.0
    assert result["current"] == 5060.0


# ── Unit: _gap_to_score ────────────────────────────────────────────


def test_gap_to_score_extremes():
    assert _gap_to_score(0.03) == 1.0
    assert _gap_to_score(-0.03) == -1.0


def test_gap_to_score_flat():
    assert _gap_to_score(0.001) == 0.0
    assert _gap_to_score(-0.001) == 0.0


def test_gap_to_score_moderate():
    assert _gap_to_score(0.012) == 0.7
    assert _gap_to_score(-0.012) == -0.7


# ── Unit: compute_overnight_signal ──────────────────────────────────


def test_overnight_signal_strong_bullish():
    """All indices gap up + contango VIX → BULLISH."""
    result = compute_overnight_signal(
        es_gap=classify_gap(5000, 5070),   # +1.4%
        nq_gap=classify_gap(18000, 18250),  # +1.4%
        rty_gap=classify_gap(2000, 2030),   # +1.5%
        vix_structure={"structure": "contango", "spot": 15.0, "vxx_implied": 17.0},
    )
    assert result["signal"] == "BULLISH"
    assert result["direction_score"] > 0.3


def test_overnight_signal_strong_bearish():
    """All indices gap down + backwardation VIX → BEARISH."""
    result = compute_overnight_signal(
        es_gap=classify_gap(5000, 4930),
        nq_gap=classify_gap(18000, 17740),
        rty_gap=classify_gap(2000, 1970),
        vix_structure={"structure": "backwardation", "spot": 30.0, "vxx_implied": 25.0},
    )
    assert result["signal"] == "BEARISH"
    assert result["direction_score"] < -0.3


def test_overnight_signal_neutral():
    """Flat across the board → NEUTRAL."""
    result = compute_overnight_signal(
        es_gap=classify_gap(5000, 5005),
        nq_gap=classify_gap(18000, 18010),
        rty_gap=classify_gap(2000, 1998),
        vix_structure={"structure": "contango", "spot": 16.0, "vxx_implied": 18.0},
    )
    assert result["signal"] == "NEUTRAL"


def test_overnight_signal_has_all_fields():
    result = compute_overnight_signal(
        es_gap=classify_gap(5000, 5070),
        nq_gap=classify_gap(18000, 18250),
        rty_gap=classify_gap(2000, 2030),
        vix_structure={"structure": "contango"},
    )
    assert "signal" in result
    assert "conviction" in result
    assert "direction_score" in result
    assert "gap_risk" in result
    assert "vix_term_structure" in result
    assert "cross_asset_confirmation" in result


def test_cross_asset_confirming():
    """Oil up + dollar down + bonds down with bullish indices → CONFIRMING."""
    result = compute_overnight_signal(
        es_gap=classify_gap(5000, 5070),
        nq_gap=classify_gap(18000, 18250),
        rty_gap=classify_gap(2000, 2030),
        vix_structure={"structure": "contango"},
        oil_change_pct=0.01,
        dollar_change_pct=-0.005,
        bond_change_pct=-0.002,
    )
    assert result["cross_asset_confirmation"] == "CONFIRMING"


def test_cross_asset_diverging():
    """Oil down + dollar up with bullish indices → DIVERGING."""
    result = compute_overnight_signal(
        es_gap=classify_gap(5000, 5070),
        nq_gap=classify_gap(18000, 18250),
        rty_gap=classify_gap(2000, 2030),
        vix_structure={"structure": "contango"},
        oil_change_pct=-0.02,
        dollar_change_pct=0.01,
        bond_change_pct=0.005,
    )
    assert result["cross_asset_confirmation"] == "DIVERGING"


# ── Unit: check_position_exposure ──────────────────────────────────


def test_exposure_critical_on_large_gap():
    trades = [
        {"trade_key": "t1", "underlying": "SPY", "strategy": "put_credit_spread"},
    ]
    gaps = {"es": classify_gap(5000, 4860)}  # -2.8%
    alerts = check_position_exposure(trades, gaps)
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "critical"
    assert alerts[0]["trade_key"] == "t1"


def test_exposure_warning_on_moderate_gap():
    trades = [
        {"trade_key": "t2", "underlying": "QQQ", "strategy": "iron_condor"},
    ]
    gaps = {"nq": classify_gap(18000, 17880)}  # ~-0.67%
    alerts = check_position_exposure(trades, gaps)
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "warning"


def test_no_alert_on_flat():
    trades = [
        {"trade_key": "t3", "underlying": "SPY", "strategy": "put_credit_spread"},
    ]
    gaps = {"es": classify_gap(5000, 5005)}  # +0.1% flat
    alerts = check_position_exposure(trades, gaps)
    assert len(alerts) == 0


def test_no_alert_for_unmapped_underlying():
    trades = [
        {"trade_key": "t4", "underlying": "AAPL", "strategy": "call_debit"},
    ]
    gaps = {"es": classify_gap(5000, 4900)}
    alerts = check_position_exposure(trades, gaps)
    assert len(alerts) == 0


def test_equity_long_gap_down_warning():
    trades = [
        {"trade_key": "t5", "underlying": "IWM", "strategy": "equity_long"},
    ]
    gaps = {"rty": classify_gap(2000, 1970)}  # -1.5%
    alerts = check_position_exposure(trades, gaps)
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "warning"


# ── Unit: conviction ───────────────────────────────────────────────


def test_conviction_high_agreement_plus_confirming():
    result = compute_overnight_signal(
        es_gap=classify_gap(5000, 5070),
        nq_gap=classify_gap(18000, 18250),
        rty_gap=classify_gap(2000, 2030),
        vix_structure={"structure": "contango"},
        oil_change_pct=0.01,
        dollar_change_pct=-0.005,
    )
    assert result["conviction"] == "HIGH"


def test_conviction_low_mixed():
    result = compute_overnight_signal(
        es_gap=classify_gap(5000, 5005),
        nq_gap=classify_gap(18000, 17995),
        rty_gap=classify_gap(2000, 2001),
        vix_structure={"structure": "unknown"},
    )
    assert result["conviction"] == "LOW"


# ── Integration: build_briefing (requires network) ──────────────────


@pytest.fixture
def service():
    settings = Settings()
    cache = TTLCache()
    fc = FuturesClient(settings=settings, cache=cache)
    return PreMarketIntelligenceService(futures_client=fc, cache=cache)


@pytest.mark.asyncio
async def test_build_briefing_live(service):
    """Full briefing returns all expected top-level keys."""
    result = await service.build_briefing()
    assert "timestamp" in result
    assert "market_status" in result
    assert result["market_status"] in ("open", "extended", "closed")
    assert "snapshots" in result
    assert "gap_analysis" in result
    assert "overnight_signal" in result
    assert "vix_term_structure" in result
    assert "cross_asset" in result
    assert "position_alerts" in result

    sig = result["overnight_signal"]
    assert sig["signal"] in ("BULLISH", "NEUTRAL", "BEARISH")
    assert sig["conviction"] in ("HIGH", "MODERATE", "LOW")
