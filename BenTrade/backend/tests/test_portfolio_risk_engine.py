"""
Tests for Portfolio Risk / Exposure Engine v1.1
=================================================

Covers: contract shape, directional exposure, underlying concentration,
sector concentration, strategy concentration, expiration concentration,
capital at risk, Greeks exposure, event exposure, correlation exposure,
risk flags, warning flags, status determination, edge cases, and
data-quality honesty.
"""

import datetime as dt
import pytest

from app.services.portfolio_risk_engine import (
    build_portfolio_exposure,
    _infer_direction,
    _dte_bucket,
    _classify_strategy_family,
    _sanitize_positions,
    _safe_float,
    _safe_int,
    _CORRELATION_CLUSTERS,
    _SYMBOL_TO_CLUSTER,
)


# ── Fixtures ─────────────────────────────────────────────────────────


def _pos(**kwargs) -> dict:
    """Shorthand position factory with defaults."""
    base = {"symbol": "SPY", "strategy": "put_credit_spread", "quantity": -1}
    base.update(kwargs)
    return base


def _spread(symbol="SPY", strategy="put_credit_spread", risk=500.0,
            dte=30, delta=-0.25, quantity=-1, **kw) -> dict:
    """Typical credit spread position."""
    return {
        "symbol": symbol, "strategy": strategy, "risk": risk,
        "dte": dte, "delta": delta, "quantity": quantity,
        "expiration": "2025-08-15", "gamma": 0.01,
        "theta": -0.05, "vega": 0.10,
        **kw,
    }


def _diverse_portfolio() -> list[dict]:
    """5-position portfolio across 3 symbols for reusable tests."""
    return [
        _spread("SPY", "put_credit_spread", risk=500, dte=30),
        _spread("SPY", "put_credit_spread", risk=500, dte=30),
        _spread("QQQ", "call_credit_spread", risk=400, dte=14),
        _spread("IWM", "put_credit_spread", risk=300, dte=45),
        _spread("IWM", "iron_condor", risk=200, dte=45, delta=0.0),
    ]


# ═══════════════════════════════════════════════════════════════════
#  1. CONTRACT SHAPE TESTS
# ═══════════════════════════════════════════════════════════════════

class TestContractShape:
    """All required top-level keys are present in output."""

    REQUIRED_KEYS = {
        "portfolio_version", "generated_at", "status",
        "position_count", "underlying_count",
        "portfolio_summary", "directional_exposure",
        "underlying_concentration", "sector_concentration",
        "strategy_concentration", "expiration_concentration",
        "capital_at_risk", "greeks_exposure",
        "event_exposure", "correlation_exposure",
        "dimension_coverage",
        "risk_flags", "warning_flags",
        "evidence", "metadata",
    }

    def test_all_keys_present_with_data(self):
        result = build_portfolio_exposure([_spread()])
        assert self.REQUIRED_KEYS <= set(result.keys())

    def test_all_keys_present_empty(self):
        result = build_portfolio_exposure([])
        assert self.REQUIRED_KEYS <= set(result.keys())

    def test_version_is_string(self):
        result = build_portfolio_exposure([_spread()])
        assert result["portfolio_version"] == "1.1"

    def test_generated_at_is_iso(self):
        result = build_portfolio_exposure([_spread()])
        dt.datetime.fromisoformat(result["generated_at"])

    def test_status_enum(self):
        result = build_portfolio_exposure([_spread()])
        assert result["status"] in ("ok", "partial", "empty")

    def test_position_count_matches(self):
        positions = [_spread(), _spread(), _spread()]
        result = build_portfolio_exposure(positions)
        assert result["position_count"] == 3

    def test_underlying_count(self):
        positions = [_spread("SPY"), _spread("QQQ"), _spread("SPY")]
        result = build_portfolio_exposure(positions)
        assert result["underlying_count"] == 2

    def test_risk_flags_is_list(self):
        result = build_portfolio_exposure([_spread()])
        assert isinstance(result["risk_flags"], list)

    def test_warning_flags_is_list(self):
        result = build_portfolio_exposure([_spread()])
        assert isinstance(result["warning_flags"], list)


# ═══════════════════════════════════════════════════════════════════
#  2. EMPTY / EDGE CASES
# ═══════════════════════════════════════════════════════════════════

class TestEmptyEdgeCases:
    """Edge cases: empty input, None, bad types."""

    def test_empty_list(self):
        result = build_portfolio_exposure([])
        assert result["status"] == "empty"
        assert result["position_count"] == 0

    def test_none_input(self):
        result = build_portfolio_exposure(None)
        assert result["status"] == "empty"

    def test_non_list_input(self):
        result = build_portfolio_exposure("not a list")
        assert result["status"] == "empty"

    def test_list_of_non_dicts(self):
        result = build_portfolio_exposure([1, 2, 3])
        assert result["status"] == "empty"

    def test_dicts_without_symbol(self):
        result = build_portfolio_exposure([{"risk": 500}, {"strategy": "put_credit_spread"}])
        assert result["status"] == "empty"

    def test_single_position(self):
        result = build_portfolio_exposure([_spread()])
        assert result["position_count"] == 1
        assert result["status"] in ("ok", "partial")


# ═══════════════════════════════════════════════════════════════════
#  3. DIRECTIONAL EXPOSURE
# ═══════════════════════════════════════════════════════════════════

class TestDirectionalExposure:
    """Direction inference and aggregation."""

    def test_explicit_long_is_bullish(self):
        assert _infer_direction({"direction": "long"}) == "bullish"

    def test_explicit_short_is_bearish(self):
        assert _infer_direction({"direction": "short"}) == "bearish"

    def test_put_credit_spread_is_bullish(self):
        assert _infer_direction({"strategy": "put_credit_spread"}) == "bullish"

    def test_call_credit_spread_is_bearish(self):
        assert _infer_direction({"strategy": "call_credit_spread"}) == "bearish"

    def test_iron_condor_is_neutral(self):
        assert _infer_direction({"strategy": "iron_condor"}) == "neutral"

    def test_positive_qty_is_bullish(self):
        assert _infer_direction({"quantity": 5}) == "bullish"

    def test_negative_qty_is_bearish(self):
        assert _infer_direction({"quantity": -5}) == "bearish"

    def test_no_data_is_unknown(self):
        assert _infer_direction({}) == "unknown"

    def test_bullish_bias_when_majority_bullish(self):
        positions = [
            _spread(strategy="put_credit_spread"),
            _spread(strategy="put_credit_spread"),
            _spread(strategy="call_credit_spread"),
        ]
        result = build_portfolio_exposure(positions)
        assert result["directional_exposure"]["bias"] == "bullish"

    def test_bearish_bias_when_majority_bearish(self):
        positions = [
            _spread(strategy="call_credit_spread"),
            _spread(strategy="call_credit_spread"),
            _spread(strategy="put_credit_spread"),
        ]
        result = build_portfolio_exposure(positions)
        assert result["directional_exposure"]["bias"] == "bearish"

    def test_mixed_bias(self):
        positions = [
            _spread(strategy="put_credit_spread"),
            _spread(strategy="call_credit_spread"),
        ]
        result = build_portfolio_exposure(positions)
        assert result["directional_exposure"]["bias"] == "mixed"

    def test_neutral_bias(self):
        positions = [
            _spread(strategy="iron_condor"),
            _spread(strategy="iron_condor"),
        ]
        result = build_portfolio_exposure(positions)
        assert result["directional_exposure"]["bias"] == "neutral"

    def test_all_unknown_directions(self):
        positions = [{"symbol": "ABC"}, {"symbol": "DEF"}]
        result = build_portfolio_exposure(positions)
        assert result["directional_exposure"]["bias"] == "neutral"
        assert result["directional_exposure"]["unknown_count"] == 2

    def test_direction_counts(self):
        positions = [
            _spread(strategy="put_credit_spread"),
            _spread(strategy="call_credit_spread"),
            _spread(strategy="iron_condor"),
        ]
        result = build_portfolio_exposure(positions)
        d = result["directional_exposure"]
        assert d["bullish_count"] == 1
        assert d["bearish_count"] == 1
        assert d["neutral_count"] == 1

    def test_explicit_direction_overrides_strategy(self):
        """Explicit direction='short' should be bearish even with bullish strategy."""
        assert _infer_direction({"direction": "short", "strategy": "put_credit_spread"}) == "bearish"

    def test_strategy_overrides_quantity(self):
        """Strategy inference beats quantity sign."""
        assert _infer_direction({"strategy": "call_credit_spread", "quantity": 5}) == "bearish"


# ═══════════════════════════════════════════════════════════════════
#  4. UNDERLYING CONCENTRATION
# ═══════════════════════════════════════════════════════════════════

class TestUnderlyingConcentration:
    """Symbol concentration metrics."""

    def test_single_symbol_is_concentrated(self):
        positions = [_spread("SPY", risk=500), _spread("SPY", risk=500)]
        result = build_portfolio_exposure(positions)
        conc = result["underlying_concentration"]
        assert conc["concentrated"] is True
        assert conc["hhi"] == 1.0

    def test_diverse_not_concentrated(self):
        positions = [
            _spread("SPY", risk=100),
            _spread("QQQ", risk=100),
            _spread("IWM", risk=100),
            _spread("DIA", risk=100),
        ]
        result = build_portfolio_exposure(positions)
        conc = result["underlying_concentration"]
        assert conc["concentrated"] is False
        assert conc["hhi"] == 0.25

    def test_top_symbols_sorted_by_share(self):
        positions = [
            _spread("SPY", risk=800),
            _spread("QQQ", risk=200),
        ]
        result = build_portfolio_exposure(positions)
        top = result["underlying_concentration"]["top_symbols"]
        assert top[0]["symbol"] == "SPY"
        assert top[1]["symbol"] == "QQQ"

    def test_risk_weighted_method(self):
        positions = [_spread(risk=500)]
        result = build_portfolio_exposure(positions)
        assert result["underlying_concentration"]["method"] == "risk_weighted"

    def test_count_weighted_fallback(self):
        positions = [{"symbol": "SPY"}, {"symbol": "QQQ"}]
        result = build_portfolio_exposure(positions)
        assert result["underlying_concentration"]["method"] == "count_weighted"

    def test_hhi_range(self):
        positions = _diverse_portfolio()
        result = build_portfolio_exposure(positions)
        hhi = result["underlying_concentration"]["hhi"]
        assert 0.0 <= hhi <= 1.0

    def test_total_symbols_count(self):
        positions = [_spread("SPY"), _spread("QQQ"), _spread("SPY")]
        result = build_portfolio_exposure(positions)
        assert result["underlying_concentration"]["total_symbols"] == 2


# ═══════════════════════════════════════════════════════════════════
#  5. SECTOR CONCENTRATION
# ═══════════════════════════════════════════════════════════════════

class TestSectorConcentration:
    """Sector coverage and aggregation."""

    def test_no_sector_data(self):
        positions = [_spread()]  # no sector field
        result = build_portfolio_exposure(positions)
        sec = result["sector_concentration"]
        assert sec["coverage"] == "none"
        assert sec["concentrated"] is False
        assert sec["concentration_reliable"] is False

    def test_full_sector_coverage(self):
        positions = [
            _spread(sector="Technology"),
            _spread(sector="Healthcare"),
        ]
        result = build_portfolio_exposure(positions)
        sec = result["sector_concentration"]
        assert sec["coverage"] == "full"
        assert sec["concentration_reliable"] is True
        assert "Technology" in sec["sectors"]
        assert "Healthcare" in sec["sectors"]
        # total_share should equal share when coverage is full
        assert sec["sectors"]["Technology"]["total_share"] == sec["sectors"]["Technology"]["share"]

    def test_partial_sector_coverage(self):
        positions = [
            _spread(sector="Technology"),
            _spread(),  # no sector
        ]
        result = build_portfolio_exposure(positions)
        sec = result["sector_concentration"]
        assert sec["coverage"] == "partial"
        assert sec["positions_with_sector"] == 1
        assert sec["positions_without_sector"] == 1
        assert sec["concentration_reliable"] is False
        # share (sector-relative) vs total_share (portfolio-relative)
        assert sec["sectors"]["Technology"]["share"] == 1.0
        assert sec["sectors"]["Technology"]["total_share"] == 0.5

    def test_sector_counts(self):
        positions = [
            _spread(sector="Technology"),
            _spread(sector="Technology"),
            _spread(sector="Healthcare"),
        ]
        result = build_portfolio_exposure(positions)
        sec = result["sector_concentration"]["sectors"]
        assert sec["Technology"]["count"] == 2
        assert sec["Healthcare"]["count"] == 1


# ═══════════════════════════════════════════════════════════════════
#  6. STRATEGY CONCENTRATION
# ═══════════════════════════════════════════════════════════════════

class TestStrategyConcentration:
    """Strategy type distribution."""

    def test_single_strategy_is_concentrated(self):
        positions = [_spread(strategy="put_credit_spread")] * 3
        result = build_portfolio_exposure(positions)
        assert result["strategy_concentration"]["concentrated"] is True

    def test_diverse_strategies_not_concentrated(self):
        positions = [
            _spread(strategy="put_credit_spread"),
            _spread(strategy="call_credit_spread"),
            _spread(strategy="iron_condor"),
        ]
        result = build_portfolio_exposure(positions)
        assert result["strategy_concentration"]["concentrated"] is False

    def test_family_classification(self):
        assert _classify_strategy_family("put_credit_spread") == "credit"
        assert _classify_strategy_family("call_debit") == "debit"
        assert _classify_strategy_family("stock_long") == "stock"
        assert _classify_strategy_family("xyz") == "other"

    def test_families_dict(self):
        positions = [
            _spread(strategy="put_credit_spread"),
            _spread(strategy="call_debit"),
            _spread(strategy="iron_condor"),
        ]
        result = build_portfolio_exposure(positions)
        families = result["strategy_concentration"]["families"]
        assert families.get("credit", 0) >= 2  # pcs + ic
        assert families.get("debit", 0) >= 1

    def test_top_strategies_shares_sum_to_one(self):
        positions = _diverse_portfolio()
        result = build_portfolio_exposure(positions)
        total = sum(s["share"] for s in result["strategy_concentration"]["top_strategies"])
        assert abs(total - 1.0) < 0.01

    def test_unknown_strategy_handled(self):
        positions = [{"symbol": "SPY"}]  # no strategy field
        result = build_portfolio_exposure(positions)
        top = result["strategy_concentration"]["top_strategies"]
        assert any(s["strategy"] == "unknown" for s in top)


# ═══════════════════════════════════════════════════════════════════
#  7. EXPIRATION CONCENTRATION
# ═══════════════════════════════════════════════════════════════════

class TestExpirationConcentration:
    """DTE bucket distribution."""

    def test_dte_bucket_mapping(self):
        assert _dte_bucket(0) == "0-7D"
        assert _dte_bucket(7) == "0-7D"
        assert _dte_bucket(8) == "8-21D"
        assert _dte_bucket(21) == "8-21D"
        assert _dte_bucket(22) == "22-45D"
        assert _dte_bucket(45) == "22-45D"
        assert _dte_bucket(46) == "46-90D"
        assert _dte_bucket(90) == "46-90D"
        assert _dte_bucket(91) == "90D+"
        assert _dte_bucket(365) == "90D+"

    def test_dte_bucket_none(self):
        assert _dte_bucket(None) == "unknown"

    def test_expiration_buckets_present(self):
        result = build_portfolio_exposure([_spread(dte=30)])
        buckets = result["expiration_concentration"]["buckets"]
        assert "0-7D" in buckets
        assert "8-21D" in buckets
        assert "22-45D" in buckets
        assert "46-90D" in buckets
        assert "90D+" in buckets

    def test_clustered_near_term(self):
        positions = [
            _spread(dte=3, risk=500),
            _spread(dte=5, risk=500),
            _spread(dte=6, risk=500),
        ]
        result = build_portfolio_exposure(positions)
        exp = result["expiration_concentration"]
        assert exp["buckets"]["0-7D"]["count"] == 3

    def test_nearest_expiration(self):
        positions = [
            _spread(expiration="2025-07-10", dte=10),
            _spread(expiration="2025-08-15", dte=45),
        ]
        result = build_portfolio_exposure(positions)
        assert result["expiration_concentration"]["nearest_expiration"] == "2025-07-10"

    def test_concentration_detected(self):
        """Risk-weighted clustering in one bucket should flag concentrated."""
        positions = [
            _spread(dte=3, risk=1000),
            _spread(dte=5, risk=1000),
            _spread(dte=30, risk=100),
        ]
        result = build_portfolio_exposure(positions)
        assert result["expiration_concentration"]["concentrated"] is True

    def test_no_expiration_count(self):
        positions = [{"symbol": "SPY"}]  # no dte, no expiration
        result = build_portfolio_exposure(positions)
        assert result["expiration_concentration"]["no_expiration_count"] == 1


# ═══════════════════════════════════════════════════════════════════
#  8. CAPITAL AT RISK
# ═══════════════════════════════════════════════════════════════════

class TestCapitalAtRisk:
    """Risk aggregation and utilization."""

    def test_total_risk_sums(self):
        positions = [_spread(risk=500), _spread(risk=300)]
        result = build_portfolio_exposure(positions)
        assert result["capital_at_risk"]["total_risk"] == 800.0

    def test_with_risk_count(self):
        positions = [_spread(risk=500), _spread(risk=300)]
        result = build_portfolio_exposure(positions)
        assert result["capital_at_risk"]["positions_with_risk"] == 2

    def test_without_risk_count(self):
        positions = [_spread(risk=500), {"symbol": "SPY"}]
        result = build_portfolio_exposure(positions)
        assert result["capital_at_risk"]["positions_without_risk"] == 1

    def test_utilization_with_equity(self):
        positions = [_spread(risk=5000)]
        result = build_portfolio_exposure(positions, account_equity=50000)
        assert result["capital_at_risk"]["utilization_pct"] == 0.1

    def test_utilization_without_equity(self):
        positions = [_spread(risk=5000)]
        result = build_portfolio_exposure(positions)
        assert result["capital_at_risk"]["utilization_pct"] is None

    def test_account_equity_provided_flag(self):
        result = build_portfolio_exposure([_spread()], account_equity=10000)
        assert result["capital_at_risk"]["account_equity_provided"] is True

    def test_max_profit_aggregated(self):
        positions = [
            _spread(risk=500, max_profit=100),
            _spread(risk=300, max_profit=75),
        ]
        result = build_portfolio_exposure(positions)
        assert result["capital_at_risk"]["total_max_profit"] == 175.0

    def test_max_profit_none_when_missing(self):
        positions = [_spread(risk=500)]  # no max_profit
        result = build_portfolio_exposure(positions)
        assert result["capital_at_risk"]["total_max_profit"] is None

    def test_zero_risk_not_counted(self):
        positions = [_spread(risk=0.0)]
        result = build_portfolio_exposure(positions)
        assert result["capital_at_risk"]["positions_with_risk"] == 0
        assert result["capital_at_risk"]["positions_without_risk"] == 1


# ═══════════════════════════════════════════════════════════════════
#  9. GREEKS EXPOSURE
# ═══════════════════════════════════════════════════════════════════

class TestGreeksExposure:
    """Greeks aggregation and coverage."""

    def test_full_coverage(self):
        positions = [
            _spread(delta=-0.25, gamma=0.01, theta=-0.05, vega=0.10),
            _spread(delta=-0.15, gamma=0.02, theta=-0.03, vega=0.08),
        ]
        result = build_portfolio_exposure(positions)
        g = result["greeks_exposure"]
        assert g["coverage"] == "full"
        assert abs(g["delta"] - (-0.40)) < 0.001
        assert abs(g["gamma"] - 0.03) < 0.001
        assert abs(g["theta"] - (-0.08)) < 0.001
        assert abs(g["vega"] - 0.18) < 0.001

    def test_no_coverage(self):
        positions = [{"symbol": "SPY"}, {"symbol": "QQQ"}]
        result = build_portfolio_exposure(positions)
        assert result["greeks_exposure"]["coverage"] == "none"
        assert result["greeks_exposure"]["positions_without_greeks"] == 2

    def test_partial_coverage(self):
        positions = [
            _spread(delta=-0.25),
            {"symbol": "QQQ"},
        ]
        result = build_portfolio_exposure(positions)
        g = result["greeks_exposure"]
        assert g["coverage"] == "partial"
        assert g["positions_with_greeks"] == 1
        assert g["positions_without_greeks"] == 1

    def test_greeks_summed(self):
        positions = [
            _spread(delta=0.5, gamma=0.0, theta=0.0, vega=0.0),
            _spread(delta=-0.3, gamma=0.0, theta=0.0, vega=0.0),
        ]
        result = build_portfolio_exposure(positions)
        assert abs(result["greeks_exposure"]["delta"] - 0.2) < 0.001


# ═══════════════════════════════════════════════════════════════════
# 10. EVENT EXPOSURE
# ═══════════════════════════════════════════════════════════════════

class TestEventExposure:
    """Event tag aggregation."""

    def test_no_events(self):
        result = build_portfolio_exposure([_spread()])
        assert result["event_exposure"]["coverage"] == "none"
        assert result["event_exposure"]["events"] == []

    def test_events_present(self):
        positions = [
            _spread(event_tag="FOMC"),
            _spread(event_tag="FOMC"),
            _spread(event_tag="CPI"),
        ]
        result = build_portfolio_exposure(positions)
        ev = result["event_exposure"]
        assert ev["coverage"] == "partial"
        assert len(ev["events"]) == 2
        assert ev["events"][0]["event"] == "FOMC"
        assert ev["events"][0]["position_count"] == 2

    def test_empty_event_tag_ignored(self):
        positions = [_spread(event_tag=""), _spread(event_tag=None)]
        result = build_portfolio_exposure(positions)
        assert result["event_exposure"]["coverage"] == "none"


# ═══════════════════════════════════════════════════════════════════
# 11. CORRELATION EXPOSURE
# ═══════════════════════════════════════════════════════════════════

class TestCorrelationExposure:
    """Correlated-asset cluster detection."""

    def test_sp500_cluster(self):
        positions = [
            _spread("SPY", risk=500),
            _spread("SPX", risk=500),
        ]
        result = build_portfolio_exposure(positions)
        clusters = result["correlation_exposure"]["clusters"]
        assert "sp500" in clusters
        assert clusters["sp500"]["count"] == 2
        assert set(clusters["sp500"]["symbols"]) == {"SPY", "SPX"}

    def test_no_cluster(self):
        positions = [_spread("AAPL", risk=500)]
        result = build_portfolio_exposure(positions)
        assert result["correlation_exposure"]["clusters"] == {}

    def test_concentrated_cluster(self):
        positions = [
            _spread("SPY", risk=500),
            _spread("XSP", risk=500),
            _spread("VOO", risk=500),
        ]
        result = build_portfolio_exposure(positions)
        assert result["correlation_exposure"]["concentrated"] is True

    def test_clusters_dict_shape(self):
        positions = [_spread("SPY", risk=500)]
        result = build_portfolio_exposure(positions)
        clusters = result["correlation_exposure"]["clusters"]
        if "sp500" in clusters:
            c = clusters["sp500"]
            assert "count" in c
            assert "risk" in c
            assert "share" in c
            assert "symbols" in c

    def test_multiple_clusters(self):
        positions = [
            _spread("SPY", risk=500),
            _spread("QQQ", risk=500),
        ]
        result = build_portfolio_exposure(positions)
        clusters = result["correlation_exposure"]["clusters"]
        assert "sp500" in clusters
        assert "nasdaq" in clusters

    def test_cluster_reverse_lookup(self):
        assert _SYMBOL_TO_CLUSTER.get("SPY") == "sp500"
        assert _SYMBOL_TO_CLUSTER.get("QQQ") == "nasdaq"
        assert _SYMBOL_TO_CLUSTER.get("IWM") == "russell"
        assert _SYMBOL_TO_CLUSTER.get("DIA") == "dow"


# ═══════════════════════════════════════════════════════════════════
# 12. RISK FLAGS
# ═══════════════════════════════════════════════════════════════════

class TestRiskFlags:
    """Risk flag generation."""

    def test_underlying_concentrated_flag(self):
        positions = [_spread("SPY", risk=1000), _spread("SPY", risk=1000)]
        result = build_portfolio_exposure(positions)
        assert "underlying_concentrated" in result["risk_flags"]

    def test_strategy_concentrated_flag(self):
        positions = [_spread(strategy="put_credit_spread")] * 5
        result = build_portfolio_exposure(positions)
        assert "strategy_concentrated" in result["risk_flags"]

    def test_heavy_bullish_lean(self):
        positions = [_spread(strategy="put_credit_spread")] * 10
        result = build_portfolio_exposure(positions)
        assert "heavy_bullish_lean" in result["risk_flags"]

    def test_heavy_bearish_lean(self):
        positions = [_spread(strategy="call_credit_spread")] * 10
        result = build_portfolio_exposure(positions)
        assert "heavy_bearish_lean" in result["risk_flags"]

    def test_high_utilization_flag(self):
        positions = [_spread(risk=30000)]
        result = build_portfolio_exposure(positions, account_equity=50000)
        assert "high_utilization" in result["risk_flags"]

    def test_no_flags_for_clean_portfolio(self):
        positions = [
            _spread("SPY", strategy="put_credit_spread", risk=100, delta=-0.1, dte=5, expiration="2025-07-05"),
            _spread("QQQ", strategy="call_credit_spread", risk=100, delta=0.1, dte=15, expiration="2025-07-15"),
            _spread("IWM", strategy="iron_condor", risk=100, delta=0.0, dte=35, expiration="2025-08-04"),
            _spread("DIA", strategy="put_credit_spread", risk=100, delta=-0.1, dte=60, expiration="2025-08-29"),
        ]
        result = build_portfolio_exposure(positions)
        assert len(result["risk_flags"]) == 0

    def test_large_aggregate_delta(self):
        positions = [_spread(delta=-3.0)] * 3
        result = build_portfolio_exposure(positions)
        assert "large_aggregate_delta" in result["risk_flags"]

    def test_flags_are_sorted(self):
        positions = [_spread("SPY", strategy="put_credit_spread", risk=30000)] * 10
        result = build_portfolio_exposure(positions, account_equity=50000)
        flags = result["risk_flags"]
        assert flags == sorted(flags)

    def test_correlated_cluster_flag(self):
        positions = [
            _spread("SPY", risk=500),
            _spread("XSP", risk=500),
            _spread("VOO", risk=500),
        ]
        result = build_portfolio_exposure(positions)
        assert "correlated_cluster_concentrated" in result["risk_flags"]


# ═══════════════════════════════════════════════════════════════════
# 13. WARNING FLAGS
# ═══════════════════════════════════════════════════════════════════

class TestWarningFlags:
    """Warning flag generation for data-quality caveats."""

    def test_greeks_unavailable_warning(self):
        positions = [{"symbol": "SPY"}]
        result = build_portfolio_exposure(positions)
        assert "greeks_unavailable" in result["warning_flags"]

    def test_greeks_partial_warning(self):
        positions = [_spread(delta=-0.25), {"symbol": "QQQ"}]
        result = build_portfolio_exposure(positions)
        assert "greeks_partial_coverage" in result["warning_flags"]

    def test_sector_unavailable_warning(self):
        positions = [_spread()]
        result = build_portfolio_exposure(positions)
        assert "sector_data_unavailable" in result["warning_flags"]

    def test_sector_partial_warning(self):
        positions = [_spread(sector="Tech"), _spread()]
        result = build_portfolio_exposure(positions)
        assert "sector_data_partial" in result["warning_flags"]

    def test_event_unavailable_warning(self):
        positions = [_spread()]
        result = build_portfolio_exposure(positions)
        assert "event_data_unavailable" in result["warning_flags"]

    def test_risk_partial_warning(self):
        positions = [_spread(risk=500), {"symbol": "QQQ"}]
        result = build_portfolio_exposure(positions)
        assert "risk_data_partial" in result["warning_flags"]

    def test_risk_unavailable_warning(self):
        positions = [{"symbol": "SPY"}, {"symbol": "QQQ"}]
        result = build_portfolio_exposure(positions)
        assert "risk_data_unavailable" in result["warning_flags"]

    def test_warnings_sorted(self):
        result = build_portfolio_exposure([{"symbol": "SPY"}])
        assert result["warning_flags"] == sorted(result["warning_flags"])


# ═══════════════════════════════════════════════════════════════════
# 14. STATUS DETERMINATION
# ═══════════════════════════════════════════════════════════════════

class TestStatus:
    """Status inference: ok / partial / empty."""

    def test_empty_status(self):
        result = build_portfolio_exposure([])
        assert result["status"] == "empty"

    def test_ok_status_with_good_data(self):
        positions = [
            _spread(delta=-0.25, gamma=0.01, theta=-0.05, vega=0.10, risk=500, sector="Tech"),
        ]
        result = build_portfolio_exposure(positions)
        assert result["status"] == "ok"

    def test_partial_status_with_gaps(self):
        # No greeks, no sector, no risk → multiple gaps
        positions = [{"symbol": "SPY"}, {"symbol": "QQQ"}]
        result = build_portfolio_exposure(positions)
        assert result["status"] == "partial"


# ═══════════════════════════════════════════════════════════════════
# 15. PORTFOLIO SUMMARY
# ═══════════════════════════════════════════════════════════════════

class TestPortfolioSummary:
    """Summary block for human consumption."""

    def test_summary_keys(self):
        result = build_portfolio_exposure([_spread()])
        summary = result["portfolio_summary"]
        assert "description" in summary
        assert "directional_bias" in summary
        assert "risk_level" in summary
        assert "flags_count" in summary

    def test_risk_level_low(self):
        positions = [
            _spread("SPY", strategy="put_credit_spread", risk=100, delta=-0.1, dte=5, expiration="2025-07-05"),
            _spread("QQQ", strategy="call_credit_spread", risk=100, delta=0.1, dte=15, expiration="2025-07-15"),
            _spread("IWM", strategy="iron_condor", risk=100, delta=0.0, dte=35, expiration="2025-08-04"),
            _spread("DIA", strategy="put_credit_spread", risk=100, delta=-0.1, dte=60, expiration="2025-08-29"),
        ]
        result = build_portfolio_exposure(positions)
        assert result["portfolio_summary"]["risk_level"] == "low"

    def test_risk_level_elevated(self):
        # Force many flags: concentrated, strategy concentrated, heavy lean, high utilization
        positions = [_spread("SPY", strategy="put_credit_spread", risk=30000, delta=-3.0)] * 10
        result = build_portfolio_exposure(positions, account_equity=50000)
        assert result["portfolio_summary"]["risk_level"] == "elevated"

    def test_summary_description_not_empty(self):
        result = build_portfolio_exposure([_spread()])
        assert len(result["portfolio_summary"]["description"]) > 0


# ═══════════════════════════════════════════════════════════════════
# 16. EVIDENCE AND METADATA
# ═══════════════════════════════════════════════════════════════════

class TestEvidenceMetadata:
    """Evidence and metadata blocks."""

    def test_evidence_symbols(self):
        positions = [_spread("SPY"), _spread("QQQ")]
        result = build_portfolio_exposure(positions)
        assert "SPY" in result["evidence"]["symbols"]
        assert "QQQ" in result["evidence"]["symbols"]
        assert result["evidence"]["symbols"] == sorted(result["evidence"]["symbols"])

    def test_evidence_has_account_equity(self):
        result = build_portfolio_exposure([_spread()], account_equity=10000)
        assert result["evidence"]["has_account_equity"] is True

    def test_evidence_no_account_equity(self):
        result = build_portfolio_exposure([_spread()])
        assert result["evidence"]["has_account_equity"] is False

    def test_metadata_version(self):
        result = build_portfolio_exposure([_spread()])
        assert result["metadata"]["portfolio_version"] == "1.1"

    def test_metadata_greeks_coverage(self):
        result = build_portfolio_exposure([_spread(delta=-0.25)])
        assert result["metadata"]["greeks_coverage"] == "full"


# ═══════════════════════════════════════════════════════════════════
# 17. SANITIZATION
# ═══════════════════════════════════════════════════════════════════

class TestSanitization:
    """Input sanitization."""

    def test_symbol_uppercased(self):
        result = _sanitize_positions([{"symbol": "spy"}])
        assert result[0]["symbol"] == "SPY"

    def test_underlying_field_fallback(self):
        result = _sanitize_positions([{"underlying": "QQQ"}])
        assert result[0]["symbol"] == "QQQ"

    def test_whitespace_stripped(self):
        result = _sanitize_positions([{"symbol": "  SPY  "}])
        assert result[0]["symbol"] == "SPY"

    def test_empty_symbol_skipped(self):
        result = _sanitize_positions([{"symbol": ""}, {"symbol": "SPY"}])
        assert len(result) == 1

    def test_none_symbol_skipped(self):
        result = _sanitize_positions([{"symbol": None}])
        assert len(result) == 0

    def test_non_dict_skipped(self):
        result = _sanitize_positions([42, "str", None, {"symbol": "SPY"}])
        assert len(result) == 1

    def test_safe_float(self):
        assert _safe_float(1.5) == 1.5
        assert _safe_float("2.5") == 2.5
        assert _safe_float(None) is None
        assert _safe_float("") is None
        assert _safe_float("abc") is None

    def test_safe_int(self):
        assert _safe_int(5) == 5
        assert _safe_int("3") == 3
        assert _safe_int(2.7) == 2
        assert _safe_int(None) is None
        assert _safe_int("") is None


# ═══════════════════════════════════════════════════════════════════
# 18. INTEGRATION / DIVERSE PORTFOLIO
# ═══════════════════════════════════════════════════════════════════

class TestIntegration:
    """End-to-end tests with realistic multi-position portfolios."""

    def test_diverse_portfolio_all_keys(self):
        result = build_portfolio_exposure(_diverse_portfolio())
        assert result["position_count"] == 5
        assert result["underlying_count"] == 3
        assert result["status"] in ("ok", "partial")

    def test_large_portfolio(self):
        """10 positions across 5 symbols."""
        symbols = ["SPY", "QQQ", "IWM", "DIA", "AAPL"]
        positions = [
            _spread(sym, risk=100 + i * 50, dte=10 + i * 5,
                    delta=-0.1 - i * 0.02)
            for i, sym in enumerate(symbols)
        ] * 2
        result = build_portfolio_exposure(positions, account_equity=100000)
        assert result["position_count"] == 10
        assert result["underlying_count"] == 5
        assert result["capital_at_risk"]["utilization_pct"] is not None

    def test_mixed_data_quality(self):
        """Mix of rich and sparse positions."""
        positions = [
            _spread("SPY", risk=1000, delta=-0.3, sector="Index"),
            {"symbol": "QQQ"},  # minimal data
            _spread("IWM", risk=500, delta=-0.2),
        ]
        result = build_portfolio_exposure(positions)
        assert result["status"] in ("ok", "partial")
        assert result["greeks_exposure"]["coverage"] == "partial"

    def test_all_stock_strategies(self):
        positions = [
            _spread(strategy="stock_pullback_swing", risk=1000),
            _spread(strategy="stock_momentum_breakout", risk=500),
        ]
        result = build_portfolio_exposure(positions)
        families = result["strategy_concentration"]["families"]
        assert families.get("stock", 0) == 2

    def test_real_active_trade_shape(self):
        """Position shape matching routes_active_trades output."""
        position = {
            "trade_key": "SPY_20250815_PUT_420/415",
            "symbol": "SPY",
            "strategy": "put_credit_spread",
            "strategy_id": "put_credit_spread",
            "spread_type": "put",
            "short_strike": 420,
            "long_strike": 415,
            "expiration": "2025-08-15",
            "legs": [
                {"symbol": "SPY250815P00420000", "quantity": -1},
                {"symbol": "SPY250815P00415000", "quantity": 1},
            ],
            "quantity": -1,
            "avg_open_price": 1.50,
            "mark_price": 0.85,
            "unrealized_pnl": 65.0,
            "unrealized_pnl_pct": 43.33,
            "dte": 30,
            "status": "OPEN",
        }
        result = build_portfolio_exposure([position])
        assert result["position_count"] == 1
        assert result["underlying_count"] == 1
        assert result["directional_exposure"]["bias"] == "bullish"

    def test_real_risk_row_shape(self):
        """Position shape matching routes_portfolio_risk normalized rows."""
        row = {
            "trade_key": "SPY_20250815_PUT_420/415",
            "symbol": "SPY",
            "strategy": "put_credit_spread",
            "expiration": "2025-08-15",
            "dte": 30,
            "quantity": 1,
            "risk": 350.0,
            "delta": -0.25,
            "gamma": 0.01,
            "theta": -0.05,
            "vega": 0.10,
            "reference_price": 545.0,
        }
        result = build_portfolio_exposure([row])
        assert result["position_count"] == 1
        assert result["capital_at_risk"]["total_risk"] == 350.0
        assert result["greeks_exposure"]["coverage"] == "full"


# ═══════════════════════════════════════════════════════════════════
# 19. DATA INTEGRITY HONESTY
# ═══════════════════════════════════════════════════════════════════

class TestDataIntegrity:
    """Never fabricate; prefer null/unknown over wrong."""

    def test_no_utilization_without_equity(self):
        """utilization_pct must be None when no equity provided."""
        result = build_portfolio_exposure([_spread(risk=5000)])
        assert result["capital_at_risk"]["utilization_pct"] is None

    def test_no_sector_fabrication(self):
        """Sector coverage must be 'none' when no position has sector data."""
        result = build_portfolio_exposure([_spread()])
        assert result["sector_concentration"]["coverage"] == "none"

    def test_no_event_fabrication(self):
        """Events must be empty when no position has event_tag."""
        result = build_portfolio_exposure([_spread()])
        assert result["event_exposure"]["coverage"] == "none"
        assert result["event_exposure"]["events"] == []

    def test_greeks_not_fabricated(self):
        """Greeks must be zero when no position has greek data, not filled in."""
        positions = [{"symbol": "SPY"}, {"symbol": "QQQ"}]
        result = build_portfolio_exposure(positions)
        g = result["greeks_exposure"]
        assert g["delta"] == 0.0
        assert g["coverage"] == "none"

    def test_max_profit_not_fabricated(self):
        positions = [_spread(risk=500)]  # no max_profit in input
        result = build_portfolio_exposure(positions)
        assert result["capital_at_risk"]["total_max_profit"] is None


# ═══════════════════════════════════════════════════════════════════
# 20. DIMENSION COVERAGE (v1.1)
# ═══════════════════════════════════════════════════════════════════

class TestDimensionCoverage:
    """Per-dimension evaluation quality: fully_evaluated / partially_evaluated / heuristic / unavailable."""

    VALID_STATUSES = {"fully_evaluated", "partially_evaluated", "heuristic", "unavailable"}
    DIMENSION_KEYS = {
        "directional_exposure", "underlying_concentration",
        "sector_concentration", "strategy_concentration",
        "expiration_concentration", "capital_at_risk",
        "greeks_exposure", "event_exposure", "correlation_exposure",
    }

    def test_dimension_coverage_keys_present(self):
        result = build_portfolio_exposure([_spread()])
        dc = result["dimension_coverage"]
        assert self.DIMENSION_KEYS <= set(dc.keys())

    def test_all_statuses_are_valid(self):
        result = build_portfolio_exposure([_spread()])
        dc = result["dimension_coverage"]
        for dim, status in dc.items():
            assert status in self.VALID_STATUSES, f"{dim} has invalid status: {status}"

    def test_rich_position_fully_evaluated(self):
        """Position with all fields → most dimensions fully_evaluated."""
        pos = _spread(sector="Technology", event_tag="FOMC")
        result = build_portfolio_exposure([pos])
        dc = result["dimension_coverage"]
        assert dc["directional_exposure"] == "fully_evaluated"
        assert dc["underlying_concentration"] == "fully_evaluated"
        assert dc["sector_concentration"] == "fully_evaluated"
        assert dc["strategy_concentration"] == "fully_evaluated"
        assert dc["expiration_concentration"] == "fully_evaluated"
        assert dc["capital_at_risk"] == "fully_evaluated"
        assert dc["greeks_exposure"] == "fully_evaluated"
        assert dc["event_exposure"] == "partially_evaluated"  # events are opt-in
        assert dc["correlation_exposure"] == "heuristic"  # always heuristic

    def test_sparse_position_partial_or_unavailable(self):
        """Minimal position → dimensions with no data show unavailable."""
        result = build_portfolio_exposure([{"symbol": "AAPL"}])
        dc = result["dimension_coverage"]
        assert dc["directional_exposure"] == "fully_evaluated"
        assert dc["underlying_concentration"] == "fully_evaluated"
        assert dc["sector_concentration"] == "unavailable"
        assert dc["capital_at_risk"] == "unavailable"
        assert dc["greeks_exposure"] == "unavailable"
        assert dc["event_exposure"] == "unavailable"
        assert dc["correlation_exposure"] == "heuristic"

    def test_mixed_data_richness(self):
        """Mix of rich and sparse → partially_evaluated where applicable."""
        positions = [
            _spread("SPY", sector="Index", risk=500, delta=-0.25),
            {"symbol": "AAPL"},  # no risk, no greeks, no sector
        ]
        result = build_portfolio_exposure(positions)
        dc = result["dimension_coverage"]
        assert dc["greeks_exposure"] == "partially_evaluated"
        assert dc["sector_concentration"] == "partially_evaluated"
        assert dc["capital_at_risk"] == "partially_evaluated"

    def test_empty_portfolio_all_unavailable(self):
        result = build_portfolio_exposure([])
        dc = result["dimension_coverage"]
        for dim in self.DIMENSION_KEYS:
            assert dc[dim] == "unavailable"

    def test_correlation_always_heuristic(self):
        """Correlation is always heuristic regardless of data richness."""
        result = build_portfolio_exposure([_spread("SPY", risk=500)])
        assert result["dimension_coverage"]["correlation_exposure"] == "heuristic"

    def test_expiration_unavailable_when_no_dte(self):
        """Positions without DTE or expiration → expiration unavailable."""
        positions = [{"symbol": "SPY"}, {"symbol": "QQQ"}]
        result = build_portfolio_exposure(positions)
        assert result["dimension_coverage"]["expiration_concentration"] == "unavailable"

    def test_expiration_partially_evaluated_mixed(self):
        """Mix of positions with and without DTE."""
        positions = [
            _spread(dte=30, expiration="2025-08-15"),
            {"symbol": "QQQ"},  # no dte, no expiration
        ]
        result = build_portfolio_exposure(positions)
        assert result["dimension_coverage"]["expiration_concentration"] == "partially_evaluated"


# ═══════════════════════════════════════════════════════════════════
# 21. SECTOR CONCENTRATION HONESTY (v1.1)
# ═══════════════════════════════════════════════════════════════════

class TestSectorConcentrationHonesty:
    """Sector concentration claims must be honest about data quality."""

    def test_partial_sector_not_reliable(self):
        """When only some positions have sector data, concentration is not reliable."""
        positions = [
            _spread(sector="Technology"),
            _spread(sector="Technology"),
            _spread(),  # no sector
            _spread(),  # no sector
        ]
        result = build_portfolio_exposure(positions)
        sec = result["sector_concentration"]
        assert sec["coverage"] == "partial"
        assert sec["concentration_reliable"] is False
        # share is 1.0 (both with-sector are Technology)
        # total_share is 0.5 (2/4 total positions)
        assert sec["sectors"]["Technology"]["share"] == 1.0
        assert sec["sectors"]["Technology"]["total_share"] == 0.5

    def test_full_sector_concentrated(self):
        """All positions have sector, one sector dominant → concentrated + reliable."""
        positions = [
            _spread(sector="Technology"),
            _spread(sector="Technology"),
            _spread(sector="Technology"),
            _spread(sector="Healthcare"),
        ]
        result = build_portfolio_exposure(positions)
        sec = result["sector_concentration"]
        assert sec["coverage"] == "full"
        assert sec["concentrated"] is True
        assert sec["concentration_reliable"] is True

    def test_full_sector_not_concentrated(self):
        """All positions have sector, evenly spread → not concentrated."""
        positions = [
            _spread(sector="Technology"),
            _spread(sector="Healthcare"),
            _spread(sector="Financials"),
        ]
        result = build_portfolio_exposure(positions)
        sec = result["sector_concentration"]
        assert sec["coverage"] == "full"
        assert sec["concentrated"] is False

    def test_single_position_with_sector_not_concentrated(self):
        """Single position → concentrated is False (need > 1 to compare)."""
        positions = [_spread(sector="Technology")]
        result = build_portfolio_exposure(positions)
        sec = result["sector_concentration"]
        assert sec["concentrated"] is False

    def test_sector_concentrated_flag_only_when_reliable(self):
        """sector_concentrated risk flag requires concentration_reliable=True."""
        # Partial coverage: concentrated but not reliable → no risk_flag
        positions = [
            _spread(sector="Technology"),
            _spread(sector="Technology"),
            _spread(),  # no sector
        ]
        result = build_portfolio_exposure(positions)
        assert "sector_concentrated" not in result["risk_flags"]

    def test_sector_concentrated_risk_flag_when_reliable(self):
        """Full coverage + concentrated → sector_concentrated in risk_flags."""
        positions = [
            _spread(sector="Technology"),
            _spread(sector="Technology"),
            _spread(sector="Healthcare"),
        ]
        result = build_portfolio_exposure(positions)
        assert "sector_concentrated" in result["risk_flags"]

    def test_no_sector_data_warning(self):
        """No sector data → warning flag present, no concentration claims."""
        result = build_portfolio_exposure([_spread()])
        assert "sector_data_unavailable" in result["warning_flags"]
        sec = result["sector_concentration"]
        assert sec["concentrated"] is False
        assert sec["concentration_reliable"] is False


# ═══════════════════════════════════════════════════════════════════
# 22. CORRELATION METHOD METADATA (v1.1)
# ═══════════════════════════════════════════════════════════════════

class TestCorrelationMethodMetadata:
    """Correlation clustering must be explicitly labeled as heuristic/static."""

    def test_method_is_static_cluster(self):
        result = build_portfolio_exposure([_spread("SPY", risk=500)])
        corr = result["correlation_exposure"]
        assert corr["method"] == "static_cluster"

    def test_method_note_present(self):
        result = build_portfolio_exposure([_spread("SPY", risk=500)])
        corr = result["correlation_exposure"]
        assert "method_note" in corr
        assert "static" in corr["method_note"].lower() or "predefined" in corr["method_note"].lower()

    def test_metadata_includes_correlation_method(self):
        result = build_portfolio_exposure([_spread("SPY", risk=500)])
        assert result["metadata"]["correlation_method"] == "static_cluster"

    def test_empty_portfolio_still_has_method(self):
        result = build_portfolio_exposure([])
        corr = result["correlation_exposure"]
        assert corr["method"] == "static_cluster"

    def test_no_matching_cluster_still_has_method(self):
        """Symbols outside predefined clusters still get method label."""
        positions = [_spread("AAPL", risk=500)]
        result = build_portfolio_exposure(positions)
        corr = result["correlation_exposure"]
        assert corr["method"] == "static_cluster"
        assert corr["clusters"] == {}


# ═══════════════════════════════════════════════════════════════════
# 23. CAPITAL AT RISK COVERAGE (v1.1)
# ═══════════════════════════════════════════════════════════════════

class TestCapitalAtRiskCoverage:
    """Capital at risk coverage field."""

    def test_full_coverage(self):
        positions = [_spread(risk=500), _spread(risk=300)]
        result = build_portfolio_exposure(positions)
        assert result["capital_at_risk"]["coverage"] == "full"

    def test_no_coverage(self):
        positions = [{"symbol": "SPY"}, {"symbol": "QQQ"}]
        result = build_portfolio_exposure(positions)
        assert result["capital_at_risk"]["coverage"] == "none"

    def test_partial_coverage(self):
        positions = [_spread(risk=500), {"symbol": "QQQ"}]
        result = build_portfolio_exposure(positions)
        assert result["capital_at_risk"]["coverage"] == "partial"

    def test_empty_coverage(self):
        result = build_portfolio_exposure([])
        assert result["capital_at_risk"]["coverage"] == "none"


# ═══════════════════════════════════════════════════════════════════
# 24. REPRESENTATIVE OUTPUTS (v1.1)
# ═══════════════════════════════════════════════════════════════════

class TestRepresentativeOutputs:
    """Representative portfolio scenarios proving honest coverage semantics."""

    def test_strong_portfolio_coverage(self):
        """All positions have full enrichment → all dimensions fully_evaluated (except correlation=heuristic)."""
        positions = [
            _spread("SPY", strategy="put_credit_spread", risk=500, dte=30,
                    delta=-0.25, gamma=0.01, theta=-0.05, vega=0.10,
                    sector="Index", event_tag="FOMC", expiration="2025-08-15"),
            _spread("QQQ", strategy="call_credit_spread", risk=400, dte=14,
                    delta=0.20, gamma=0.02, theta=-0.03, vega=0.08,
                    sector="Index", event_tag="CPI", expiration="2025-07-25"),
            _spread("IWM", strategy="iron_condor", risk=300, dte=45,
                    delta=0.0, gamma=0.01, theta=-0.02, vega=0.05,
                    sector="Index", expiration="2025-09-15"),
        ]
        result = build_portfolio_exposure(positions, account_equity=50000)
        assert result["status"] == "ok"
        dc = result["dimension_coverage"]
        # All fully evaluated except correlation (heuristic) and events (opt-in → partial)
        assert dc["directional_exposure"] == "fully_evaluated"
        assert dc["sector_concentration"] == "fully_evaluated"
        assert dc["greeks_exposure"] == "fully_evaluated"
        assert dc["capital_at_risk"] == "fully_evaluated"
        assert dc["correlation_exposure"] == "heuristic"
        assert result["sector_concentration"]["concentration_reliable"] is True
        assert result["capital_at_risk"]["coverage"] == "full"

    def test_partial_coverage_missing_enrichment(self):
        """Mix of rich and sparse positions → partial/unavailable dimensions."""
        positions = [
            _spread("SPY", risk=500, delta=-0.25, sector="Index",
                    dte=30, expiration="2025-08-15"),
            {"symbol": "AAPL"},  # bare minimum
            {"symbol": "MSFT", "risk": 200},  # risk only
        ]
        result = build_portfolio_exposure(positions)
        assert result["status"] in ("ok", "partial")
        dc = result["dimension_coverage"]
        assert dc["greeks_exposure"] == "partially_evaluated"
        assert dc["sector_concentration"] == "partially_evaluated"
        assert dc["capital_at_risk"] == "partially_evaluated"
        # Sector concentration is not reliable
        assert result["sector_concentration"]["concentration_reliable"] is False

    def test_sector_limited_case(self):
        """No sector enrichment → sector unavailable, rest can still be strong."""
        positions = [
            _spread("SPY", risk=500, delta=-0.25, dte=30, expiration="2025-08-15"),
            _spread("QQQ", risk=400, delta=0.20, dte=14, expiration="2025-07-25"),
        ]
        result = build_portfolio_exposure(positions)
        dc = result["dimension_coverage"]
        assert dc["sector_concentration"] == "unavailable"
        assert dc["greeks_exposure"] == "fully_evaluated"
        assert dc["capital_at_risk"] == "fully_evaluated"
        assert result["sector_concentration"]["concentrated"] is False
        assert "sector_data_unavailable" in result["warning_flags"]

    def test_heuristic_correlation_case(self):
        """Correlation exposure is always heuristic, even with rich data."""
        positions = [
            _spread("SPY", risk=500, delta=-0.25, sector="Index",
                    dte=30, expiration="2025-08-15"),
            _spread("SPX", risk=500, delta=-0.25, sector="Index",
                    dte=30, expiration="2025-08-15"),
        ]
        result = build_portfolio_exposure(positions)
        assert result["dimension_coverage"]["correlation_exposure"] == "heuristic"
        assert result["correlation_exposure"]["method"] == "static_cluster"
        assert result["correlation_exposure"]["concentrated"] is True
        assert "correlated_cluster_concentrated" in result["risk_flags"]

    def test_version_in_all_representative_outputs(self):
        for positions in [
            [_spread()],
            [_spread(sector="Tech"), {"symbol": "AAPL"}],
            [],
        ]:
            result = build_portfolio_exposure(positions)
            assert result["portfolio_version"] == "1.1"
            assert result["metadata"]["portfolio_version"] == "1.1"
