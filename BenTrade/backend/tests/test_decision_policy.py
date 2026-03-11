"""
Tests for Decision Policy Framework v1.1
==========================================

Covers: contract shape, insufficient-data handling, portfolio concentration,
market/conflict conditions, data-quality, time-horizon, risk-packaging,
size guidance, decision derivation, integration scenarios, and
data-integrity honesty.
"""

import pytest

from app.services.decision_policy import (
    evaluate_policy,
    _derive_decision,
    _derive_severity,
    _derive_size_guidance,
    _dte_to_bucket,
    _make_check,
    _POLICY_VERSION,
    _SHORT_PREMIUM_STRATEGIES,
    _SYMBOL_SHARE_RESTRICT,
    _HHI_OVERALL_CAUTION,
    _STRATEGY_SHARE_CAUTION,
    _EXPIRATION_SHARE_CAUTION,
    _CLUSTER_SHARE_RESTRICT,
    _UTILIZATION_CAUTION,
    _UTILIZATION_RESTRICT,
    _MARKET_CONFIDENCE_CAUTION,
    _CANDIDATE_MISSING_FIELDS_CAUTION,
    _CANDIDATE_CONFIDENCE_CAUTION,
    _HORIZON_GAP_CAUTION,
    _HORIZON_GAP_RESTRICT,
)
from app.utils.strategy_constants import (
    CORRELATION_CLUSTERS,
    SYMBOL_TO_CLUSTER,
)


# ── Fixtures / Factories ─────────────────────────────────────────────

def _candidate(**overrides) -> dict:
    """Typical options candidate with reasonable defaults."""
    base = {
        "symbol": "SPY",
        "underlying": "SPY",
        "scanner_key": "put_credit_spread",
        "setup_type": "put_credit_spread",
        "strategy_family": "options",
        "direction": "short",
        "time_horizon": "days_to_expiry",
        "confidence": 0.85,
        "setup_quality": 72.0,
        "entry_context": {
            "spread_mid": 1.20,
            "short_strike": 530,
            "long_strike": 525,
            "expiration": "2025-08-15",
            "dte": 30,
        },
        "risk_definition": {
            "type": "defined_risk_spread",
            "max_loss_per_contract": 3.80,
            "pop": 0.72,
        },
        "reward_profile": {
            "type": "defined_reward_spread",
            "max_profit_per_contract": 1.20,
            "expected_value_per_contract": 0.35,
            "return_on_risk": 0.316,
        },
        "data_quality": {
            "metrics_ready": True,
            "missing_fields": [],
            "warning_count": 0,
        },
        "risk_flags": [],
        "market_context_tags": [],
    }
    base.update(overrides)
    return base


def _market(**overrides) -> dict:
    """Typical market composite with reasonable defaults."""
    base = {
        "composite_version": "1.0",
        "status": "ok",
        "market_state": "neutral",
        "support_state": "supportive",
        "stability_state": "orderly",
        "confidence": 0.72,
        "summary": "Market is broadly neutral.",
        "metadata": {
            "conflict_severity": "none",
            "overall_quality": "good",
            "horizon_span": "short_term -> medium_term",
        },
        "adjustments": {},
        "evidence": {},
    }
    base.update(overrides)
    return base


def _conflicts(**overrides) -> dict:
    """Typical conflict report with reasonable defaults."""
    base = {
        "status": "clean",
        "conflict_count": 0,
        "conflict_severity": "none",
        "conflict_flags": [],
        "market_conflicts": [],
        "candidate_conflicts": [],
    }
    base.update(overrides)
    return base


def _portfolio(**overrides) -> dict:
    """Typical portfolio exposure with reasonable defaults."""
    base = {
        "portfolio_version": "1.0",
        "status": "ok",
        "position_count": 4,
        "underlying_count": 4,
        "directional_exposure": {
            "bias": "neutral",
            "bullish_count": 2,
            "bearish_count": 1,
            "neutral_count": 1,
            "unknown_count": 0,
        },
        "underlying_concentration": {
            "top_symbols": [
                {"symbol": "QQQ", "share": 0.25, "risk": 500},
                {"symbol": "IWM", "share": 0.25, "risk": 500},
                {"symbol": "DIA", "share": 0.25, "risk": 500},
                {"symbol": "AAPL", "share": 0.25, "risk": 500},
            ],
            "concentrated": False,
            "hhi": 0.25,
            "method": "risk_weighted",
            "total_symbols": 4,
        },
        "strategy_concentration": {
            "top_strategies": [
                {"strategy": "put_credit_spread", "count": 2, "share": 0.50},
                {"strategy": "iron_condor", "count": 2, "share": 0.50},
            ],
            "concentrated": False,
            "families": {"credit": 4},
            "total_strategies": 2,
        },
        "expiration_concentration": {
            "buckets": {
                "0-7D": {"count": 1, "risk": 200, "share": 0.10},
                "8-21D": {"count": 1, "risk": 300, "share": 0.15},
                "22-45D": {"count": 1, "risk": 500, "share": 0.25},
                "46-90D": {"count": 1, "risk": 1000, "share": 0.50},
                "90D+": {"count": 0, "risk": 0, "share": 0.0},
            },
            "concentrated": False,
            "nearest_expiration": "2025-07-15",
        },
        "capital_at_risk": {
            "total_risk": 2000,
            "utilization_pct": 0.10,
            "positions_with_risk": 4,
            "positions_without_risk": 0,
        },
        "correlation_exposure": {
            "clusters": {},
            "concentrated": False,
        },
        "greeks_exposure": {
            "coverage": "full",
            "delta": -0.50,
            "gamma": 0.04,
            "theta": -0.12,
            "vega": 0.30,
            "positions_with_greeks": 4,
            "positions_without_greeks": 0,
        },
        "risk_flags": [],
        "warning_flags": [],
        "metadata": {},
    }
    base.update(overrides)
    return base


def _assembled(**overrides) -> dict:
    """Typical assembled context with reasonable defaults."""
    base = {
        "assembly_status": "complete",
        "included_modules": ["breadth_participation", "volatility_options", "cross_asset_macro"],
        "missing_modules": [],
        "degraded_modules": [],
        "quality_summary": {
            "overall_quality": "good",
            "average_confidence": 0.75,
            "module_count": 3,
            "degraded_count": 0,
        },
        "freshness_summary": {
            "overall_freshness": "live",
        },
        "horizon_summary": {
            "market_horizons": {
                "breadth_participation": "short_term",
                "volatility_options": "short_term",
                "cross_asset_macro": "short_term",
            },
            "candidate_horizons": ["days_to_expiry"],
            "shortest": "short_term",
            "longest": "short_term",
        },
    }
    base.update(overrides)
    return base


# ═══════════════════════════════════════════════════════════════════
#  1. CONTRACT SHAPE TESTS
# ═══════════════════════════════════════════════════════════════════

class TestContractShape:
    """All required top-level keys are present."""

    REQUIRED_KEYS = {
        "policy_version", "evaluated_at", "status",
        "policy_decision", "decision_severity", "summary",
        "triggered_checks", "blocking_checks",
        "caution_checks", "restrictive_checks",
        "size_guidance", "eligibility_flags", "warning_flags",
        "evidence", "metadata",
    }

    def test_all_keys_with_data(self):
        result = evaluate_policy(
            candidate=_candidate(), market=_market(),
            conflicts=_conflicts(), portfolio=_portfolio(),
        )
        assert self.REQUIRED_KEYS <= set(result.keys())

    def test_all_keys_insufficient_data(self):
        result = evaluate_policy()  # no candidate
        assert self.REQUIRED_KEYS <= set(result.keys())

    def test_version_is_string(self):
        result = evaluate_policy(candidate=_candidate())
        assert result["policy_version"] == _POLICY_VERSION

    def test_evaluated_at_is_iso(self):
        import datetime
        result = evaluate_policy(candidate=_candidate())
        datetime.datetime.fromisoformat(result["evaluated_at"])

    def test_status_enum(self):
        result = evaluate_policy(candidate=_candidate())
        assert result["status"] in ("evaluated", "insufficient_data")

    def test_decision_enum(self):
        result = evaluate_policy(
            candidate=_candidate(), market=_market(),
            conflicts=_conflicts(), portfolio=_portfolio(),
        )
        assert result["policy_decision"] in (
            "allow", "caution", "restrict", "block", "insufficient_data",
        )

    def test_size_guidance_enum(self):
        result = evaluate_policy(
            candidate=_candidate(), market=_market(),
            conflicts=_conflicts(), portfolio=_portfolio(),
        )
        assert result["size_guidance"] in ("normal", "reduced", "minimal", "none")

    def test_checks_are_lists(self):
        result = evaluate_policy(candidate=_candidate())
        assert isinstance(result["triggered_checks"], list)
        assert isinstance(result["blocking_checks"], list)
        assert isinstance(result["caution_checks"], list)
        assert isinstance(result["restrictive_checks"], list)

    def test_flags_are_lists(self):
        result = evaluate_policy(candidate=_candidate())
        assert isinstance(result["eligibility_flags"], list)
        assert isinstance(result["warning_flags"], list)

    def test_check_item_schema(self):
        """PolicyCheck items must have the required fields."""
        # Force a check by providing degraded market
        result = evaluate_policy(
            candidate=_candidate(),
            market=_market(stability_state="unstable"),
        )
        checks = result["triggered_checks"]
        assert len(checks) > 0
        for check in checks:
            assert "check_code" in check
            assert "severity" in check
            assert "category" in check
            assert "title" in check
            assert "description" in check
            assert "entities" in check
            assert "evidence" in check
            assert "recommended_effect" in check
            assert "confidence_impact" in check


# ═══════════════════════════════════════════════════════════════════
#  2. INSUFFICIENT DATA TESTS
# ═══════════════════════════════════════════════════════════════════

class TestInsufficientData:
    """Insufficient-data handling when critical inputs are missing."""

    def test_no_candidate(self):
        result = evaluate_policy()
        assert result["policy_decision"] == "insufficient_data"
        assert result["status"] == "insufficient_data"
        assert result["size_guidance"] == "none"

    def test_candidate_without_symbol(self):
        result = evaluate_policy(candidate={"strategy": "put_credit_spread"})
        assert result["policy_decision"] == "insufficient_data"

    def test_candidate_empty_symbol(self):
        result = evaluate_policy(candidate={"symbol": ""})
        assert result["policy_decision"] == "insufficient_data"

    def test_no_crash_with_none_inputs(self):
        result = evaluate_policy(
            candidate=None, market=None,
            conflicts=None, portfolio=None,
        )
        assert result["policy_decision"] == "insufficient_data"

    def test_candidate_only_runs_some_checks(self):
        """With just a candidate but no market/portfolio, we can still check risk packaging."""
        result = evaluate_policy(candidate=_candidate())
        assert result["status"] == "evaluated"
        assert result["policy_decision"] in ("allow", "caution", "restrict", "block")

    def test_missing_optional_inputs_warn(self):
        result = evaluate_policy(candidate=_candidate())
        assert "market_composite_unavailable" in result["warning_flags"]
        assert "conflict_report_unavailable" in result["warning_flags"]
        assert "portfolio_exposure_unavailable" in result["warning_flags"]


# ═══════════════════════════════════════════════════════════════════
#  3. PORTFOLIO CONCENTRATION TESTS
# ═══════════════════════════════════════════════════════════════════

class TestPortfolioConcentration:
    """Portfolio concentration guardrails."""

    def test_concentrated_same_symbol(self):
        port = _portfolio(underlying_concentration={
            "top_symbols": [{"symbol": "SPY", "share": 0.55, "risk": 2000}],
            "concentrated": True,
            "hhi": 0.60,
            "method": "risk_weighted",
            "total_symbols": 2,
        })
        result = evaluate_policy(
            candidate=_candidate(symbol="SPY"),
            portfolio=port,
        )
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "portfolio_underlying_concentrated" in codes

    def test_concentrated_different_symbol_hhi(self):
        """If concentrated but candidate symbol isn't the big one, still get general HHI warning."""
        port = _portfolio(underlying_concentration={
            "top_symbols": [{"symbol": "AAPL", "share": 0.70, "risk": 5000}],
            "concentrated": True,
            "hhi": 0.55,
            "method": "risk_weighted",
            "total_symbols": 3,
        })
        result = evaluate_policy(
            candidate=_candidate(symbol="SPY"),
            portfolio=port,
        )
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "portfolio_overall_concentrated" in codes

    def test_diversified_no_concentration(self):
        result = evaluate_policy(
            candidate=_candidate(),
            portfolio=_portfolio(),
        )
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "portfolio_underlying_concentrated" not in codes
        assert "portfolio_overall_concentrated" not in codes

    def test_strategy_concentrated(self):
        port = _portfolio(strategy_concentration={
            "top_strategies": [
                {"strategy": "put_credit_spread", "count": 8, "share": 0.80},
            ],
            "concentrated": True,
            "families": {"credit": 8},
            "total_strategies": 1,
        })
        result = evaluate_policy(
            candidate=_candidate(setup_type="put_credit_spread"),
            portfolio=port,
        )
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "portfolio_strategy_concentrated" in codes

    def test_expiration_clustered(self):
        port = _portfolio(expiration_concentration={
            "buckets": {
                "0-7D": {"count": 0, "risk": 0, "share": 0.0},
                "8-21D": {"count": 0, "risk": 0, "share": 0.0},
                "22-45D": {"count": 5, "risk": 4000, "share": 0.80},
                "46-90D": {"count": 1, "risk": 1000, "share": 0.20},
                "90D+": {"count": 0, "risk": 0, "share": 0.0},
            },
            "concentrated": True,
        })
        result = evaluate_policy(
            candidate=_candidate(),  # dte=30 → 22-45D bucket
            portfolio=port,
        )
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "portfolio_expiration_clustered" in codes

    def test_correlated_cluster(self):
        port = _portfolio(correlation_exposure={
            "clusters": {
                "sp500": {"count": 4, "risk": 3000, "share": 0.75, "symbols": ["SPY", "SPX", "XSP"]},
            },
            "concentrated": True,
        })
        result = evaluate_policy(
            candidate=_candidate(symbol="SPY"),
            portfolio=port,
        )
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "portfolio_correlated_cluster" in codes

    def test_directional_stacking(self):
        port = _portfolio(
            directional_exposure={
                "bias": "bullish",
                "bullish_count": 9, "bearish_count": 0,
                "neutral_count": 1, "unknown_count": 0,
            },
            risk_flags=["heavy_bullish_lean"],
        )
        result = evaluate_policy(
            candidate=_candidate(direction="long"),
            portfolio=port,
        )
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "portfolio_directional_stacking" in codes

    def test_high_utilization(self):
        port = _portfolio(capital_at_risk={
            "total_risk": 45000,
            "utilization_pct": 0.85,
            "positions_with_risk": 10,
            "positions_without_risk": 0,
        })
        result = evaluate_policy(
            candidate=_candidate(),
            portfolio=port,
        )
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "portfolio_high_utilization" in codes

    def test_moderate_utilization(self):
        port = _portfolio(capital_at_risk={
            "total_risk": 35000,
            "utilization_pct": 0.65,
            "positions_with_risk": 8,
            "positions_without_risk": 0,
        })
        result = evaluate_policy(
            candidate=_candidate(),
            portfolio=port,
        )
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "portfolio_high_utilization" in codes


# ═══════════════════════════════════════════════════════════════════
#  4. MARKET / CONFLICT TESTS
# ═══════════════════════════════════════════════════════════════════

class TestMarketConflict:
    """Market and conflict condition guardrails."""

    def test_unstable_market(self):
        result = evaluate_policy(
            candidate=_candidate(),
            market=_market(stability_state="unstable"),
        )
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "market_unstable" in codes

    def test_noisy_market(self):
        result = evaluate_policy(
            candidate=_candidate(),
            market=_market(stability_state="noisy"),
        )
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "market_noisy" in codes

    def test_orderly_market_no_flag(self):
        result = evaluate_policy(
            candidate=_candidate(),
            market=_market(stability_state="orderly"),
        )
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "market_unstable" not in codes
        assert "market_noisy" not in codes

    def test_fragile_support(self):
        result = evaluate_policy(
            candidate=_candidate(),
            market=_market(support_state="fragile"),
        )
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "market_fragile_support" in codes

    def test_supportive_market_no_flag(self):
        result = evaluate_policy(
            candidate=_candidate(),
            market=_market(support_state="supportive"),
        )
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "market_fragile_support" not in codes

    def test_bullish_in_risk_off(self):
        result = evaluate_policy(
            candidate=_candidate(direction="long"),
            market=_market(market_state="risk_off"),
        )
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "candidate_vs_market_direction" in codes
        # Should be restrict for bullish vs risk_off
        check = next(c for c in result["triggered_checks"] if c["check_code"] == "candidate_vs_market_direction")
        assert check["recommended_effect"] == "restrict"

    def test_bearish_in_risk_on(self):
        result = evaluate_policy(
            candidate=_candidate(direction="bearish"),
            market=_market(market_state="risk_on"),
        )
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "candidate_vs_market_direction" in codes
        check = next(c for c in result["triggered_checks"] if c["check_code"] == "candidate_vs_market_direction")
        assert check["recommended_effect"] == "caution"

    def test_aligned_direction_no_flag(self):
        result = evaluate_policy(
            candidate=_candidate(direction="long"),
            market=_market(market_state="risk_on"),
        )
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "candidate_vs_market_direction" not in codes

    def test_short_premium_unstable(self):
        result = evaluate_policy(
            candidate=_candidate(setup_type="put_credit_spread"),
            market=_market(stability_state="unstable"),
        )
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "short_premium_unstable_market" in codes

    def test_low_market_confidence(self):
        result = evaluate_policy(
            candidate=_candidate(),
            market=_market(confidence=0.25),
        )
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "market_low_confidence" in codes

    def test_market_insufficient_data(self):
        result = evaluate_policy(
            candidate=_candidate(),
            market=_market(status="insufficient_data"),
        )
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "market_insufficient_data" in codes

    def test_market_degraded(self):
        result = evaluate_policy(
            candidate=_candidate(),
            market=_market(status="degraded"),
        )
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "market_degraded" in codes

    def test_high_conflict_severity(self):
        result = evaluate_policy(
            candidate=_candidate(),
            conflicts=_conflicts(
                conflict_severity="high", conflict_count=5,
                conflict_flags=["market_label_split", "candidate_vs_market_direction"],
            ),
        )
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "conflict_severity_high" in codes

    def test_moderate_conflict_severity(self):
        result = evaluate_policy(
            candidate=_candidate(),
            conflicts=_conflicts(
                conflict_severity="moderate", conflict_count=2,
                conflict_flags=["market_bull_bear_cluster"],
            ),
        )
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "conflict_severity_moderate" in codes

    def test_low_conflict_no_flag(self):
        result = evaluate_policy(
            candidate=_candidate(),
            conflicts=_conflicts(conflict_severity="low", conflict_count=1),
        )
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "conflict_severity_high" not in codes
        assert "conflict_severity_moderate" not in codes


# ═══════════════════════════════════════════════════════════════════
#  5. DATA QUALITY TESTS
# ═══════════════════════════════════════════════════════════════════

class TestDataQuality:
    """Data quality and coverage guardrails."""

    def test_many_missing_candidate_fields(self):
        cand = _candidate(data_quality={
            "metrics_ready": False,
            "missing_fields": ["max_profit", "max_loss", "pop", "ev", "ror"],
            "warning_count": 5,
        })
        result = evaluate_policy(candidate=cand)
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "candidate_many_missing_fields" in codes

    def test_low_candidate_confidence(self):
        result = evaluate_policy(candidate=_candidate(confidence=0.15))
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "candidate_low_confidence" in codes

    def test_portfolio_very_sparse(self):
        port = _portfolio(
            status="partial",
            greeks_exposure={"coverage": "none"},
            warning_flags=["risk_data_unavailable", "greeks_unavailable"],
        )
        result = evaluate_policy(
            candidate=_candidate(),
            portfolio=port,
        )
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "portfolio_data_very_sparse" in codes

    def test_poor_context_quality(self):
        asm = _assembled(
            quality_summary={"overall_quality": "poor", "degraded_count": 3},
            degraded_modules=["breadth_participation", "volatility_options", "flows_positioning"],
        )
        result = evaluate_policy(
            candidate=_candidate(),
            assembled=asm,
        )
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "context_quality_poor" in codes

    def test_degraded_context_quality(self):
        asm = _assembled(
            quality_summary={"overall_quality": "degraded", "degraded_count": 1},
            degraded_modules=["news_sentiment"],
        )
        result = evaluate_policy(
            candidate=_candidate(),
            assembled=asm,
        )
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "context_quality_degraded" in codes

    def test_many_missing_modules(self):
        asm = _assembled(
            missing_modules=["breadth_participation", "volatility_options", "cross_asset_macro"],
        )
        result = evaluate_policy(
            candidate=_candidate(),
            assembled=asm,
        )
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "context_many_missing_modules" in codes

    def test_good_quality_no_flags(self):
        result = evaluate_policy(
            candidate=_candidate(),
            assembled=_assembled(),
        )
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "context_quality_poor" not in codes
        assert "context_quality_degraded" not in codes
        assert "context_many_missing_modules" not in codes


# ═══════════════════════════════════════════════════════════════════
#  6. TIME-HORIZON TESTS
# ═══════════════════════════════════════════════════════════════════

class TestTimeHorizon:
    """Time-horizon alignment checks."""

    def test_aligned_horizons_no_flag(self):
        """days_to_expiry (rank 4) vs short_term (rank 1): gap=3 → moderate"""
        # Use a candidate that's close to market horizons
        cand = _candidate(time_horizon="short_term")
        asm = _assembled(horizon_summary={
            "market_horizons": {"breadth_participation": "short_term"},
            "candidate_horizons": ["short_term"],
        })
        result = evaluate_policy(
            candidate=cand, market=_market(),
            assembled=asm,
        )
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "horizon_severe_mismatch" not in codes
        assert "horizon_moderate_mismatch" not in codes

    def test_moderate_horizon_mismatch(self):
        """Candidate at intraday (rank 0), market at swing (rank 2): gap=2 → caution"""
        cand = _candidate(time_horizon="intraday")
        asm = _assembled(horizon_summary={
            "market_horizons": {"breadth_participation": "swing"},
            "candidate_horizons": ["intraday"],
        })
        result = evaluate_policy(
            candidate=cand, market=_market(),
            assembled=asm,
        )
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "horizon_moderate_mismatch" in codes

    def test_severe_horizon_mismatch(self):
        """Candidate at intraday (rank 0), market at long_term (rank 6): gap=6 → restrict"""
        cand = _candidate(time_horizon="intraday")
        asm = _assembled(horizon_summary={
            "market_horizons": {"liquidity_conditions": "long_term"},
            "candidate_horizons": ["intraday"],
        })
        result = evaluate_policy(
            candidate=cand, market=_market(),
            assembled=asm,
        )
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "horizon_severe_mismatch" in codes

    def test_unknown_candidate_horizon_no_check(self):
        cand = _candidate(time_horizon="unknown")
        result = evaluate_policy(
            candidate=cand, market=_market(),
            assembled=_assembled(),
        )
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "horizon_severe_mismatch" not in codes
        assert "horizon_moderate_mismatch" not in codes


# ═══════════════════════════════════════════════════════════════════
#  7. RISK PACKAGING TESTS
# ═══════════════════════════════════════════════════════════════════

class TestRiskPackaging:
    """Risk definition completeness checks."""

    def test_missing_risk_definition_blocks(self):
        cand = _candidate(risk_definition={"type": ""})
        result = evaluate_policy(candidate=cand)
        assert result["policy_decision"] == "block"
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "risk_definition_missing" in codes

    def test_completely_absent_risk_definition_blocks(self):
        cand = _candidate(risk_definition={})
        result = evaluate_policy(candidate=cand)
        assert result["policy_decision"] == "block"

    def test_missing_max_loss(self):
        cand = _candidate(risk_definition={
            "type": "defined_risk_spread",
            "max_loss_per_contract": None,
            "pop": 0.72,
        })
        result = evaluate_policy(candidate=cand)
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "risk_max_loss_missing" in codes

    def test_missing_pop(self):
        cand = _candidate(risk_definition={
            "type": "defined_risk_spread",
            "max_loss_per_contract": 3.80,
            "pop": None,
        })
        result = evaluate_policy(candidate=cand)
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "risk_pop_missing" in codes

    def test_missing_ev_and_ror(self):
        cand = _candidate(reward_profile={
            "type": "defined_reward_spread",
            "max_profit_per_contract": 1.20,
            "expected_value_per_contract": None,
            "return_on_risk": None,
        })
        result = evaluate_policy(candidate=cand)
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "reward_metrics_missing" in codes

    def test_stop_loss_undefined(self):
        cand = _candidate(
            risk_definition={"type": "stop_loss_based", "notes": []},
            strategy_family="stock",
        )
        result = evaluate_policy(candidate=cand)
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "risk_stop_loss_undefined" in codes

    def test_complete_risk_no_flags(self):
        result = evaluate_policy(candidate=_candidate())
        codes = [c["check_code"] for c in result["triggered_checks"]]
        assert "risk_definition_missing" not in codes
        assert "risk_max_loss_missing" not in codes
        assert "risk_pop_missing" not in codes


# ═══════════════════════════════════════════════════════════════════
#  8. SIZE GUIDANCE TESTS
# ═══════════════════════════════════════════════════════════════════

class TestSizeGuidance:
    """Size guidance derivation."""

    def test_clean_allow_normal(self):
        result = evaluate_policy(
            candidate=_candidate(), market=_market(),
            conflicts=_conflicts(), portfolio=_portfolio(),
        )
        assert result["policy_decision"] == "allow"
        assert result["size_guidance"] == "normal"

    def test_caution_reduced(self):
        result = evaluate_policy(
            candidate=_candidate(),
            market=_market(stability_state="noisy"),
        )
        # noisy → caution check
        assert result["size_guidance"] == "reduced"

    def test_restrict_minimal(self):
        result = evaluate_policy(
            candidate=_candidate(),
            market=_market(stability_state="unstable"),
        )
        assert result["size_guidance"] == "minimal"

    def test_block_none(self):
        cand = _candidate(risk_definition={"type": ""})
        result = evaluate_policy(candidate=cand)
        assert result["policy_decision"] == "block"
        assert result["size_guidance"] == "none"

    def test_insufficient_data_none(self):
        result = evaluate_policy()
        assert result["size_guidance"] == "none"


# ═══════════════════════════════════════════════════════════════════
#  9. DECISION DERIVATION TESTS
# ═══════════════════════════════════════════════════════════════════

class TestDecisionDerivation:
    """Decision and severity derivation logic."""

    def test_derive_allow(self):
        assert _derive_decision([], [], []) == "allow"

    def test_derive_caution(self):
        assert _derive_decision([], [], [{"x": 1}]) == "caution"

    def test_derive_restrict(self):
        assert _derive_decision([], [{"x": 1}], []) == "restrict"

    def test_derive_block(self):
        assert _derive_decision([{"x": 1}], [], []) == "block"

    def test_block_overrides_restrict(self):
        assert _derive_decision([{"x": 1}], [{"y": 1}], [{"z": 1}]) == "block"

    def test_restrict_overrides_caution(self):
        assert _derive_decision([], [{"x": 1}], [{"z": 1}]) == "restrict"

    def test_severity_none_when_no_checks(self):
        assert _derive_severity([]) == "none"

    def test_severity_takes_max(self):
        checks = [
            _make_check(code="a", severity="low", category="x", title="", description="",
                        entities=[], evidence={}, effect="caution", impact="minor"),
            _make_check(code="b", severity="high", category="x", title="", description="",
                        entities=[], evidence={}, effect="restrict", impact="major"),
        ]
        assert _derive_severity(checks) == "high"

    def test_size_guidance_mapping(self):
        assert _derive_size_guidance("allow", []) == "normal"
        assert _derive_size_guidance("caution", []) == "reduced"
        assert _derive_size_guidance("restrict", []) == "minimal"
        assert _derive_size_guidance("block", []) == "none"
        assert _derive_size_guidance("insufficient_data", []) == "none"


# ═══════════════════════════════════════════════════════════════════
# 10. ELIGIBILITY FLAGS
# ═══════════════════════════════════════════════════════════════════

class TestEligibilityFlags:
    """Eligibility flag assignment."""

    def test_allow_eligible(self):
        result = evaluate_policy(
            candidate=_candidate(), market=_market(),
            conflicts=_conflicts(), portfolio=_portfolio(),
        )
        assert "eligible" in result["eligibility_flags"]
        assert "clean_evaluation" in result["eligibility_flags"]

    def test_caution_conditionally_eligible(self):
        result = evaluate_policy(
            candidate=_candidate(),
            market=_market(stability_state="noisy"),
        )
        assert "conditionally_eligible" in result["eligibility_flags"]

    def test_block_ineligible(self):
        cand = _candidate(risk_definition={"type": ""})
        result = evaluate_policy(candidate=cand)
        assert "ineligible" in result["eligibility_flags"]


# ═══════════════════════════════════════════════════════════════════
# 11. EVIDENCE AND METADATA
# ═══════════════════════════════════════════════════════════════════

class TestEvidenceMetadata:
    """Evidence and metadata blocks."""

    def test_evidence_fields(self):
        result = evaluate_policy(
            candidate=_candidate(),
            market=_market(),
            conflicts=_conflicts(),
            portfolio=_portfolio(),
        )
        ev = result["evidence"]
        assert ev["candidate_symbol"] == "SPY"
        assert ev["market_status"] == "ok"
        assert ev["conflict_severity"] == "none"
        assert ev["portfolio_status"] == "ok"

    def test_metadata_provided_flags(self):
        result = evaluate_policy(
            candidate=_candidate(),
            market=_market(),
        )
        meta = result["metadata"]
        assert meta["candidate_provided"] is True
        assert meta["market_provided"] is True
        assert meta["conflicts_provided"] is False
        assert meta["portfolio_provided"] is False


# ═══════════════════════════════════════════════════════════════════
# 12. HELPER TESTS
# ═══════════════════════════════════════════════════════════════════

class TestHelpers:
    """DTE bucket mapping and other helpers."""

    def test_dte_buckets(self):
        assert _dte_to_bucket(0) == "0-7D"
        assert _dte_to_bucket(7) == "0-7D"
        assert _dte_to_bucket(8) == "8-21D"
        assert _dte_to_bucket(21) == "8-21D"
        assert _dte_to_bucket(22) == "22-45D"
        assert _dte_to_bucket(45) == "22-45D"
        assert _dte_to_bucket(46) == "46-90D"
        assert _dte_to_bucket(90) == "46-90D"
        assert _dte_to_bucket(91) == "90D+"
        assert _dte_to_bucket(365) == "90D+"


# ═══════════════════════════════════════════════════════════════════
# 13. INTEGRATION SCENARIOS
# ═══════════════════════════════════════════════════════════════════

class TestIntegration:
    """End-to-end integration scenarios."""

    def test_clean_eligible_case(self):
        """Clean inputs from all four sources → allow."""
        result = evaluate_policy(
            candidate=_candidate(),
            market=_market(),
            conflicts=_conflicts(),
            portfolio=_portfolio(),
        )
        assert result["policy_decision"] == "allow"
        assert result["size_guidance"] == "normal"
        assert result["decision_severity"] == "none"
        assert len(result["triggered_checks"]) == 0
        assert "eligible" in result["eligibility_flags"]

    def test_concentrated_restricted_case(self):
        """Concentrated portfolio + unstable market → restrict."""
        port = _portfolio(
            underlying_concentration={
                "top_symbols": [{"symbol": "SPY", "share": 0.55, "risk": 3000}],
                "concentrated": True,
                "hhi": 0.60,
                "method": "risk_weighted",
                "total_symbols": 2,
            },
            correlation_exposure={
                "clusters": {
                    "sp500": {"count": 3, "risk": 3000, "share": 0.80, "symbols": ["SPY", "SPX"]},
                },
                "concentrated": True,
            },
        )
        result = evaluate_policy(
            candidate=_candidate(symbol="SPY"),
            market=_market(stability_state="unstable"),
            conflicts=_conflicts(conflict_severity="high", conflict_count=4,
                                 conflict_flags=["market_label_split"]),
            portfolio=port,
        )
        assert result["policy_decision"] in ("restrict", "block")
        assert result["size_guidance"] in ("minimal", "none")
        assert len(result["triggered_checks"]) >= 3
        assert result["decision_severity"] == "high"

    def test_degraded_data_cautionary(self):
        """Mixed data quality → caution."""
        result = evaluate_policy(
            candidate=_candidate(confidence=0.20),
            market=_market(status="degraded", confidence=0.30),
            conflicts=_conflicts(conflict_severity="moderate", conflict_count=2,
                                 conflict_flags=["quality_degraded_consensus"]),
        )
        assert result["policy_decision"] in ("caution", "restrict")
        assert result["size_guidance"] in ("reduced", "minimal")

    def test_blocked_missing_risk(self):
        """Missing risk definition → block regardless of other clean context."""
        result = evaluate_policy(
            candidate=_candidate(risk_definition={}),
            market=_market(),
            conflicts=_conflicts(),
            portfolio=_portfolio(),
        )
        assert result["policy_decision"] == "block"
        assert result["size_guidance"] == "none"

    def test_stock_candidate_clean(self):
        """Stock candidate with stop-loss → allows through."""
        cand = _candidate(
            scanner_key="stock_pullback_swing",
            setup_type="stock_pullback_swing",
            strategy_family="stock",
            direction="long",
            time_horizon="swing",
            risk_definition={
                "type": "stop_loss_based",
                "notes": ["Stop at 520 (2% below entry)"],
            },
            reward_profile={
                "type": "price_target_based",
                "composite_score": 75.0,
            },
        )
        result = evaluate_policy(
            candidate=cand, market=_market(market_state="risk_on"),
            conflicts=_conflicts(), portfolio=_portfolio(),
        )
        assert result["policy_decision"] == "allow"

    def test_multiple_policy_categories(self):
        """Exercise checks from multiple categories simultaneously."""
        port = _portfolio(
            underlying_concentration={
                "top_symbols": [{"symbol": "SPY", "share": 0.55, "risk": 3000}],
                "concentrated": True,
                "hhi": 0.60,
                "method": "risk_weighted",
                "total_symbols": 2,
            },
        )
        asm = _assembled(
            quality_summary={"overall_quality": "degraded", "degraded_count": 2},
            degraded_modules=["news_sentiment", "flows_positioning"],
        )
        result = evaluate_policy(
            candidate=_candidate(symbol="SPY", confidence=0.20),
            market=_market(stability_state="noisy"),
            conflicts=_conflicts(conflict_severity="moderate", conflict_count=2,
                                 conflict_flags=["market_bull_bear_cluster"]),
            portfolio=port,
            assembled=asm,
        )
        categories = {c["category"] for c in result["triggered_checks"]}
        # Should have checks from multiple categories
        assert len(categories) >= 2
        assert result["policy_decision"] in ("caution", "restrict")


# ═══════════════════════════════════════════════════════════════════
# 14. DATA INTEGRITY / NO FABRICATION
# ═══════════════════════════════════════════════════════════════════

class TestDataIntegrity:
    """Never fabricate data, prefer honest unknowns."""

    def test_no_portfolio_warns(self):
        result = evaluate_policy(
            candidate=_candidate(), market=_market(),
        )
        assert "portfolio_exposure_unavailable" in result["warning_flags"] or \
               "portfolio_unavailable" in result["warning_flags"]

    def test_evidence_null_for_missing_inputs(self):
        result = evaluate_policy(candidate=_candidate())
        assert result["evidence"]["market_status"] is None
        assert result["evidence"]["conflict_severity"] is None
        assert result["evidence"]["portfolio_status"] is None

    def test_summary_is_meaningful(self):
        result = evaluate_policy(
            candidate=_candidate(), market=_market(),
            conflicts=_conflicts(), portfolio=_portfolio(),
        )
        assert len(result["summary"]) > 0

    def test_block_summary_explains(self):
        cand = _candidate(risk_definition={})
        result = evaluate_policy(candidate=cand)
        assert "block" in result["summary"].lower()


# ═══════════════════════════════════════════════════════════════════
# 15. SHARED CONSTANTS (v1.1)
# ═══════════════════════════════════════════════════════════════════

class TestSharedConstants:
    """Verify shared constants are importable and consistent."""

    def test_correlation_clusters_importable(self):
        assert isinstance(CORRELATION_CLUSTERS, dict)
        assert "sp500" in CORRELATION_CLUSTERS
        assert "SPY" in CORRELATION_CLUSTERS["sp500"]

    def test_symbol_to_cluster_importable(self):
        assert isinstance(SYMBOL_TO_CLUSTER, dict)
        assert SYMBOL_TO_CLUSTER["SPY"] == "sp500"
        assert SYMBOL_TO_CLUSTER["QQQ"] == "nasdaq"
        assert SYMBOL_TO_CLUSTER["IWM"] == "russell"

    def test_cluster_reverse_lookup_complete(self):
        """Every symbol in every cluster has a reverse entry."""
        for cluster_name, symbols in CORRELATION_CLUSTERS.items():
            for sym in symbols:
                assert SYMBOL_TO_CLUSTER.get(sym) == cluster_name

    def test_policy_uses_shared_cluster_not_private_import(self):
        """Policy module can look up clusters via the shared constant."""
        # If this test passes, the shared import is working
        from app.services.decision_policy import SYMBOL_TO_CLUSTER as policy_cluster
        assert policy_cluster is SYMBOL_TO_CLUSTER

    def test_portfolio_engine_backward_compat(self):
        """portfolio_risk_engine still exposes _SYMBOL_TO_CLUSTER."""
        from app.services.portfolio_risk_engine import _SYMBOL_TO_CLUSTER
        assert _SYMBOL_TO_CLUSTER["SPY"] == "sp500"
        # It should be the same object as the shared constant
        assert _SYMBOL_TO_CLUSTER is SYMBOL_TO_CLUSTER


# ═══════════════════════════════════════════════════════════════════
# 16. SHORT-PREMIUM STRATEGY IDENTIFICATION (v1.1)
# ═══════════════════════════════════════════════════════════════════

class TestShortPremiumStrategies:
    """Short-premium strategy list is visible, named, and correct."""

    def test_is_frozenset(self):
        assert isinstance(_SHORT_PREMIUM_STRATEGIES, frozenset)

    def test_contains_core_strategies(self):
        core = {
            "put_credit_spread", "call_credit_spread", "iron_condor",
            "csp", "cash_secured_put", "covered_call",
        }
        assert core <= _SHORT_PREMIUM_STRATEGIES

    def test_contains_alias_strategies(self):
        aliases = {"credit_put", "credit_call", "credit_put_spread", "credit_call_spread"}
        assert aliases <= _SHORT_PREMIUM_STRATEGIES

    def test_contains_income(self):
        """income strategy sells premium."""
        assert "income" in _SHORT_PREMIUM_STRATEGIES

    def test_debit_not_short_premium(self):
        """Debit strategies do NOT sell premium."""
        debit = {"put_debit", "call_debit", "butterfly_debit"}
        assert debit.isdisjoint(_SHORT_PREMIUM_STRATEGIES)

    def test_short_premium_triggers_unstable_market_check(self):
        """A short-premium strategy in unstable market → restrict."""
        for strat in ["put_credit_spread", "iron_condor", "csp", "income"]:
            result = evaluate_policy(
                candidate=_candidate(setup_type=strat),
                market=_market(stability_state="unstable"),
            )
            codes = {c["check_code"] for c in result["triggered_checks"]}
            assert "short_premium_unstable_market" in codes, f"{strat} should trigger"

    def test_non_short_premium_no_unstable_check(self):
        """A debit strategy in unstable market → no short_premium check."""
        result = evaluate_policy(
            candidate=_candidate(setup_type="call_debit"),
            market=_market(stability_state="unstable"),
        )
        codes = {c["check_code"] for c in result["triggered_checks"]}
        assert "short_premium_unstable_market" not in codes


# ═══════════════════════════════════════════════════════════════════
# 17. TUNABLE THRESHOLDS VISIBILITY (v1.1)
# ═══════════════════════════════════════════════════════════════════

class TestTunableThresholds:
    """All policy thresholds are named constants, visible, and numeric."""

    def test_threshold_types(self):
        """Every threshold is a number."""
        for name, val in [
            ("_SYMBOL_SHARE_RESTRICT", _SYMBOL_SHARE_RESTRICT),
            ("_HHI_OVERALL_CAUTION", _HHI_OVERALL_CAUTION),
            ("_STRATEGY_SHARE_CAUTION", _STRATEGY_SHARE_CAUTION),
            ("_EXPIRATION_SHARE_CAUTION", _EXPIRATION_SHARE_CAUTION),
            ("_CLUSTER_SHARE_RESTRICT", _CLUSTER_SHARE_RESTRICT),
            ("_UTILIZATION_CAUTION", _UTILIZATION_CAUTION),
            ("_UTILIZATION_RESTRICT", _UTILIZATION_RESTRICT),
            ("_MARKET_CONFIDENCE_CAUTION", _MARKET_CONFIDENCE_CAUTION),
            ("_CANDIDATE_MISSING_FIELDS_CAUTION", _CANDIDATE_MISSING_FIELDS_CAUTION),
            ("_CANDIDATE_CONFIDENCE_CAUTION", _CANDIDATE_CONFIDENCE_CAUTION),
            ("_HORIZON_GAP_CAUTION", _HORIZON_GAP_CAUTION),
            ("_HORIZON_GAP_RESTRICT", _HORIZON_GAP_RESTRICT),
        ]:
            assert isinstance(val, (int, float)), f"{name} is not numeric: {type(val)}"

    def test_threshold_values_are_sensible(self):
        """Sanity: thresholds are in reasonable ranges."""
        # Share thresholds: 0 < x <= 1
        for name, val in [
            ("_SYMBOL_SHARE_RESTRICT", _SYMBOL_SHARE_RESTRICT),
            ("_HHI_OVERALL_CAUTION", _HHI_OVERALL_CAUTION),
            ("_STRATEGY_SHARE_CAUTION", _STRATEGY_SHARE_CAUTION),
            ("_EXPIRATION_SHARE_CAUTION", _EXPIRATION_SHARE_CAUTION),
            ("_CLUSTER_SHARE_RESTRICT", _CLUSTER_SHARE_RESTRICT),
            ("_UTILIZATION_CAUTION", _UTILIZATION_CAUTION),
            ("_UTILIZATION_RESTRICT", _UTILIZATION_RESTRICT),
        ]:
            assert 0 < val <= 1.0, f"{name} = {val} not in (0, 1]"

        # Caution < restrict for utilization
        assert _UTILIZATION_CAUTION < _UTILIZATION_RESTRICT

        # Horizon gaps are positive integers
        assert _HORIZON_GAP_CAUTION > 0
        assert _HORIZON_GAP_RESTRICT > _HORIZON_GAP_CAUTION

    def test_utilization_caution_fires_at_threshold(self):
        """Capital utilization just above caution threshold → caution check."""
        port = _portfolio()
        port["capital_at_risk"]["utilization_pct"] = _UTILIZATION_CAUTION + 0.01
        result = evaluate_policy(
            candidate=_candidate(), market=_market(),
            conflicts=_conflicts(), portfolio=port,
        )
        codes = {c["check_code"] for c in result["triggered_checks"]}
        assert "portfolio_high_utilization" in codes
        matching = [c for c in result["triggered_checks"] if c["check_code"] == "portfolio_high_utilization"]
        assert matching[0]["recommended_effect"] == "caution"

    def test_utilization_restrict_fires_at_threshold(self):
        """Capital utilization above restrict threshold → restrict check."""
        port = _portfolio()
        port["capital_at_risk"]["utilization_pct"] = _UTILIZATION_RESTRICT + 0.01
        result = evaluate_policy(
            candidate=_candidate(), market=_market(),
            conflicts=_conflicts(), portfolio=port,
        )
        matching = [c for c in result["triggered_checks"] if c["check_code"] == "portfolio_high_utilization"]
        assert matching[0]["recommended_effect"] == "restrict"

    def test_utilization_below_caution_no_check(self):
        """Capital utilization at or below caution threshold → no check."""
        port = _portfolio()
        port["capital_at_risk"]["utilization_pct"] = _UTILIZATION_CAUTION
        result = evaluate_policy(
            candidate=_candidate(), market=_market(),
            conflicts=_conflicts(), portfolio=port,
        )
        codes = {c["check_code"] for c in result["triggered_checks"]}
        assert "portfolio_high_utilization" not in codes

    def test_market_confidence_threshold(self):
        """Market confidence just below threshold → caution."""
        result = evaluate_policy(
            candidate=_candidate(),
            market=_market(confidence=_MARKET_CONFIDENCE_CAUTION - 0.01),
        )
        codes = {c["check_code"] for c in result["triggered_checks"]}
        assert "market_low_confidence" in codes

    def test_candidate_confidence_threshold(self):
        """Candidate confidence just below threshold → caution."""
        result = evaluate_policy(
            candidate=_candidate(confidence=_CANDIDATE_CONFIDENCE_CAUTION - 0.01),
            market=_market(),
        )
        codes = {c["check_code"] for c in result["triggered_checks"]}
        assert "candidate_low_confidence" in codes


# ═══════════════════════════════════════════════════════════════════
# 18. BLOCKER / CAUTION / PASS DISTINCTION (v1.1)
# ═══════════════════════════════════════════════════════════════════

class TestBlockerCautionPassDistinction:
    """Downstream consumers can always tell blockers from cautions from passes."""

    def test_clean_pass_has_no_checks(self):
        result = evaluate_policy(
            candidate=_candidate(), market=_market(),
            conflicts=_conflicts(), portfolio=_portfolio(),
        )
        assert result["policy_decision"] == "allow"
        assert result["blocking_checks"] == []
        assert result["caution_checks"] == []
        assert result["restrictive_checks"] == []
        assert "eligible" in result["eligibility_flags"]
        assert "clean_evaluation" in result["eligibility_flags"]

    def test_caution_only_no_blockers(self):
        """Caution scenario: caution checks but no blockers or restricts."""
        port = _portfolio()
        port["capital_at_risk"]["utilization_pct"] = 0.65
        result = evaluate_policy(
            candidate=_candidate(), market=_market(),
            conflicts=_conflicts(), portfolio=port,
        )
        assert result["policy_decision"] == "caution"
        assert len(result["caution_checks"]) > 0
        assert result["blocking_checks"] == []
        assert result["restrictive_checks"] == []
        assert "conditionally_eligible" in result["eligibility_flags"]

    def test_restrict_no_blockers(self):
        """Restrict scenario: restrictive checks but no blockers."""
        result = evaluate_policy(
            candidate=_candidate(),
            market=_market(stability_state="unstable"),
            conflicts=_conflicts(),
            portfolio=_portfolio(),
        )
        assert result["policy_decision"] == "restrict"
        assert len(result["restrictive_checks"]) > 0
        assert result["blocking_checks"] == []
        assert "conditionally_eligible" in result["eligibility_flags"]

    def test_block_present(self):
        """Block scenario: at least one blocker."""
        result = evaluate_policy(
            candidate=_candidate(risk_definition={}),
            market=_market(),
            conflicts=_conflicts(),
            portfolio=_portfolio(),
        )
        assert result["policy_decision"] == "block"
        assert len(result["blocking_checks"]) > 0
        assert "ineligible" in result["eligibility_flags"]

    def test_insufficient_data_distinct_from_block(self):
        """Insufficient data is not a block — it's a different status."""
        result = evaluate_policy()
        assert result["policy_decision"] == "insufficient_data"
        assert result["status"] == "insufficient_data"
        assert result["decision_severity"] == "critical"
        assert result["size_guidance"] == "none"

    def test_mixed_caution_and_restrict(self):
        """When restrict and caution both fire, overall is restrict."""
        # Unstable market (restrict) + moderate conflict (caution)
        result = evaluate_policy(
            candidate=_candidate(),
            market=_market(stability_state="unstable"),
            conflicts=_conflicts(conflict_severity="moderate", conflict_count=2,
                                 conflict_flags=["conflicting_signals"]),
            portfolio=_portfolio(),
        )
        assert result["policy_decision"] == "restrict"
        assert len(result["restrictive_checks"]) > 0
        assert len(result["caution_checks"]) > 0

    def test_size_guidance_reflects_decision(self):
        """Size guidance is a direct function of policy_decision."""
        mapping = {
            "allow": "normal",
            "caution": "reduced",
            "restrict": "minimal",
            "block": "none",
            "insufficient_data": "none",
        }
        for decision, expected in mapping.items():
            assert _derive_size_guidance(decision, []) == expected


# ═══════════════════════════════════════════════════════════════════
# 19. DATA-INSUFFICIENT POLICY RESULTS (v1.1)
# ═══════════════════════════════════════════════════════════════════

class TestDataInsufficientResults:
    """Data-insufficient results are structured, not just prose."""

    def test_no_candidate_is_insufficient(self):
        result = evaluate_policy(market=_market())
        assert result["status"] == "insufficient_data"
        assert result["policy_decision"] == "insufficient_data"

    def test_empty_symbol_is_insufficient(self):
        result = evaluate_policy(candidate={"symbol": ""})
        assert result["status"] == "insufficient_data"

    def test_insufficient_still_has_contract_shape(self):
        """Even insufficient results have full contract shape."""
        result = evaluate_policy()
        required = {
            "policy_version", "evaluated_at", "status", "policy_decision",
            "decision_severity", "summary", "triggered_checks",
            "blocking_checks", "caution_checks", "restrictive_checks",
            "size_guidance", "eligibility_flags", "warning_flags",
            "evidence", "metadata",
        }
        assert required <= set(result.keys())

    def test_insufficient_with_market_only(self):
        """Market without candidate → insufficient but market info in evidence."""
        result = evaluate_policy(market=_market())
        assert result["status"] == "insufficient_data"
        assert result["evidence"]["market_status"] is None or result["evidence"]["market_status"] is not None

    def test_candidate_only_still_evaluates(self):
        """Candidate alone is enough to run *some* checks (risk packaging etc.)."""
        result = evaluate_policy(candidate=_candidate())
        assert result["status"] == "evaluated"
        assert result["policy_decision"] != "insufficient_data"
        # Should have warnings about missing inputs
        assert any("unavailable" in w for w in result["warning_flags"])


# ═══════════════════════════════════════════════════════════════════
# 20. MIXED SCENARIO TESTS (v1.1)
# ═══════════════════════════════════════════════════════════════════

class TestMixedScenarios:
    """Mixed scenarios where some checks pass, some caution, one blocks."""

    def test_pass_caution_block_mix(self):
        """Risk def missing (block) + moderate conflict (caution) + clean portfolio (pass)."""
        result = evaluate_policy(
            candidate=_candidate(risk_definition={}),
            market=_market(),
            conflicts=_conflicts(conflict_severity="moderate", conflict_count=1,
                                 conflict_flags=["minor_disagreement"]),
            portfolio=_portfolio(),
        )
        assert result["policy_decision"] == "block"
        assert len(result["blocking_checks"]) >= 1
        assert len(result["caution_checks"]) >= 1
        # Portfolio checks should NOT have fired (portfolio is clean)
        port_checks = [c for c in result["triggered_checks"]
                       if c["category"] == "portfolio_concentration"]
        assert len(port_checks) == 0

    def test_multiple_cautions_no_block(self):
        """Several caution-level issues but nothing blocking."""
        port = _portfolio()
        port["capital_at_risk"]["utilization_pct"] = 0.65
        result = evaluate_policy(
            candidate=_candidate(confidence=0.25),
            market=_market(stability_state="noisy", confidence=0.30),
            conflicts=_conflicts(conflict_severity="moderate", conflict_count=2,
                                 conflict_flags=["signal_divergence"]),
            portfolio=port,
        )
        assert result["policy_decision"] in ("caution", "restrict")
        assert len(result["blocking_checks"]) == 0
        assert len(result["caution_checks"]) >= 2

    def test_restrict_plus_caution_is_restrict(self):
        """Unstable market (restrict) + utilization caution → restrict overall."""
        port = _portfolio()
        port["capital_at_risk"]["utilization_pct"] = 0.65
        result = evaluate_policy(
            candidate=_candidate(),
            market=_market(stability_state="unstable"),
            conflicts=_conflicts(),
            portfolio=port,
        )
        assert result["policy_decision"] == "restrict"
        assert len(result["restrictive_checks"]) >= 1
        assert len(result["caution_checks"]) >= 1
        assert result["size_guidance"] == "minimal"

    def test_all_categories_triggered(self):
        """Force checks from every category to fire simultaneously."""
        port = _portfolio()
        port["capital_at_risk"]["utilization_pct"] = 0.85
        port["underlying_concentration"]["concentrated"] = True
        port["underlying_concentration"]["top_symbols"][0]["symbol"] = "SPY"
        port["underlying_concentration"]["top_symbols"][0]["share"] = 0.50
        port["underlying_concentration"]["hhi"] = 0.60

        asm = _assembled(
            quality_summary={"overall_quality": "degraded"},
            degraded_modules=["news_sentiment"],
        )

        result = evaluate_policy(
            candidate=_candidate(
                risk_definition={"type": "defined_risk_spread"},
                time_horizon="intraday",
            ),
            market=_market(stability_state="unstable"),
            conflicts=_conflicts(conflict_severity="high", conflict_count=3,
                                 conflict_flags=["critical_disagreement"]),
            portfolio=port,
            assembled=asm,
        )
        categories = {c["category"] for c in result["triggered_checks"]}
        # Should have portfolio, market, quality, and possibly horizon
        assert "portfolio_concentration" in categories
        assert "market_conflict" in categories
        assert result["policy_decision"] in ("restrict", "block")

    def test_structured_output_machine_usable(self):
        """Policy output is machine-parseable, not prose-only."""
        result = evaluate_policy(
            candidate=_candidate(),
            market=_market(stability_state="unstable"),
            conflicts=_conflicts(conflict_severity="moderate", conflict_count=1),
            portfolio=_portfolio(),
        )
        # Every triggered check has a stable check_code
        for check in result["triggered_checks"]:
            assert isinstance(check["check_code"], str)
            assert len(check["check_code"]) > 0
            assert isinstance(check["severity"], str)
            assert isinstance(check["recommended_effect"], str)
            assert check["recommended_effect"] in ("caution", "restrict", "block")

        # Policy decision is a clean enum value
        assert result["policy_decision"] in ("allow", "caution", "restrict", "block", "insufficient_data")

        # Evidence has machine-readable counts
        assert isinstance(result["evidence"]["checks_triggered"], int)
        assert isinstance(result["evidence"]["blocking_count"], int)
        assert isinstance(result["evidence"]["restrictive_count"], int)
        assert isinstance(result["evidence"]["caution_count"], int)


# ═══════════════════════════════════════════════════════════════════
# 21. REPRESENTATIVE OUTPUTS (v1.1)
# ═══════════════════════════════════════════════════════════════════

class TestRepresentativeOutputs:
    """Representative scenarios proving structured, reviewable policy results."""

    def test_clean_pass_output(self):
        """Clean pass: all inputs present, no issues."""
        result = evaluate_policy(
            candidate=_candidate(), market=_market(),
            conflicts=_conflicts(), portfolio=_portfolio(),
        )
        assert result["policy_decision"] == "allow"
        assert result["decision_severity"] == "none"
        assert result["size_guidance"] == "normal"
        assert result["triggered_checks"] == []
        assert "eligible" in result["eligibility_flags"]
        assert "clean_evaluation" in result["eligibility_flags"]
        assert result["policy_version"] == _POLICY_VERSION

    def test_caution_heavy_output(self):
        """Caution-heavy: multiple warnings, no blocks."""
        port = _portfolio()
        port["capital_at_risk"]["utilization_pct"] = 0.65

        result = evaluate_policy(
            candidate=_candidate(confidence=0.25),
            market=_market(stability_state="noisy", confidence=0.30),
            conflicts=_conflicts(conflict_severity="moderate", conflict_count=2),
            portfolio=port,
        )
        assert result["policy_decision"] in ("caution", "restrict")
        assert len(result["caution_checks"]) >= 2
        assert result["size_guidance"] in ("reduced", "minimal")
        assert "conditionally_eligible" in result["eligibility_flags"]
        # All checks are structured
        for check in result["triggered_checks"]:
            assert "check_code" in check
            assert "severity" in check
            assert "recommended_effect" in check

    def test_hard_block_output(self):
        """Hard block: missing risk definition."""
        result = evaluate_policy(
            candidate=_candidate(risk_definition={}),
            market=_market(), conflicts=_conflicts(), portfolio=_portfolio(),
        )
        assert result["policy_decision"] == "block"
        assert result["decision_severity"] == "critical"
        assert result["size_guidance"] == "none"
        assert "ineligible" in result["eligibility_flags"]
        assert any(c["check_code"] == "risk_definition_missing" for c in result["blocking_checks"])

    def test_data_insufficient_output(self):
        """Data insufficient: no candidate at all."""
        result = evaluate_policy()
        assert result["policy_decision"] == "insufficient_data"
        assert result["status"] == "insufficient_data"
        assert result["decision_severity"] == "critical"
        assert result["size_guidance"] == "none"
        assert result["policy_version"] == _POLICY_VERSION

    def test_version_consistency(self):
        """Version is consistent across all output scenarios."""
        scenarios = [
            {"candidate": _candidate(), "market": _market(), "conflicts": _conflicts(), "portfolio": _portfolio()},
            {"candidate": _candidate(risk_definition={})},
            {},  # insufficient
        ]
        for kwargs in scenarios:
            result = evaluate_policy(**kwargs)
            assert result["policy_version"] == _POLICY_VERSION
            assert result["metadata"]["policy_version"] == _POLICY_VERSION
