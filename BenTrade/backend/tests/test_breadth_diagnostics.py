"""Tests for Breadth & Participation Data Quality Diagnostics Framework.

Test scenarios:
  1. Warning severity taxonomy — correct severity assignment
  2. Survivorship bias / PIT — critical for historical, high for snapshot
  3. Scaffold separation — deferred metrics grouped separately
  4. Cross-pillar disagreement — signal vs data-driven classification
  5. Disagreement escalation — freshness/sample inconsistencies
  6. Quality score separation — confidence vs data_quality vs historical_validity
  7. Warning grouping — UI sections correctly populated
  8. Integration — full engine output includes diagnostics
"""

import pytest

from app.services.breadth_diagnostics import (
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_INFO,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    CAT_DATA_INTEGRITY,
    CAT_DISAGREEMENT,
    CAT_SCAFFOLD,
    SCAFFOLDED_METRICS,
    analyze_disagreement,
    assess_data_completeness,
    assess_survivorship_risk,
    build_warning,
    compute_quality_scores,
    group_warnings_for_ui,
    is_scaffolded_metric,
)
from app.services.breadth_engine import compute_breadth_scores


# ═══════════════════════════════════════════════════════════════════════
# HELPERS — fixture factories
# ═══════════════════════════════════════════════════════════════════════

def _strong_participation():
    return {
        "advancing": 104, "declining": 20, "unchanged": 6,
        "total_valid": 130,
        "new_highs": 35, "new_lows": 3,
        "sectors_positive": 10, "sectors_total": 11,
        "ew_return": 0.012, "cw_return": 0.010,
    }


def _strong_trend():
    return {
        "pct_above_20dma": 0.82, "pct_above_50dma": 0.75,
        "pct_above_200dma": 0.68,
        "pct_20_over_50": 0.78, "pct_50_over_200": 0.65,
        "trend_momentum_short": 0.08,
        "trend_momentum_intermediate": 0.05,
        "trend_momentum_long": 0.03,
        "total_valid": 130,
    }


def _strong_volume():
    return {
        "up_volume": 4_000_000_000, "down_volume": 1_200_000_000,
        "total_volume": 5_200_000_000,
        "advancing": 104, "declining": 20,
    }


def _strong_leadership():
    return {
        "ew_return": 0.012, "cw_return": 0.010,
        "sector_returns": {
            "Technology": 0.015, "Healthcare": 0.008,
            "Financials": 0.010, "Consumer Discretionary": 0.012,
            "Industrials": 0.009, "Energy": 0.005,
            "Materials": 0.006, "Utilities": 0.003,
            "Consumer Staples": 0.004, "Communication Services": 0.011,
            "REITs": 0.002,
        },
        "pct_outperforming_index": 0.65,
        "median_return": 0.009, "index_return": 0.008,
    }


def _strong_stability():
    return {
        "breadth_persistence_10d": 0.80,
        "ad_ratio_volatility_5d": 0.25,
        "pct_above_20dma_volatility_5d": 0.04,
    }


def _healthy_universe():
    return {
        "name": "SP500_proxy",
        "expected_count": 130,
        "actual_count": 130,
        "survivorship_bias_risk": False,
    }


def _survivorship_risk_universe():
    return {
        "name": "SP500_proxy",
        "expected_count": 130,
        "actual_count": 125,
        "survivorship_bias_risk": True,
    }


def _full_engine_result(**overrides):
    """Run the full engine and return result."""
    kwargs = {
        "participation_data": _strong_participation(),
        "trend_data": _strong_trend(),
        "volume_data": _strong_volume(),
        "leadership_data": _strong_leadership(),
        "stability_data": _strong_stability(),
        "universe_meta": _healthy_universe(),
    }
    kwargs.update(overrides)
    return compute_breadth_scores(**kwargs)


def _make_pillar(score, missing_count=0, submetrics=None):
    """Build a minimal pillar dict for testing."""
    return {
        "score": score,
        "missing_count": missing_count,
        "submetrics": submetrics or [],
        "warnings": [],
        "raw_inputs": {},
    }


def _make_submetric(name, status="valid", observations=100):
    return {
        "name": name,
        "raw_value": 0.5,
        "score": 50.0,
        "status": status,
        "observations": observations,
        "missing_count": 0,
        "fallback_used": False,
        "warnings": [],
        "details": {},
    }


# ═══════════════════════════════════════════════════════════════════════
# TEST 1 — WARNING SEVERITY TAXONOMY
# ═══════════════════════════════════════════════════════════════════════


class TestWarningSeverityTaxonomy:
    """Verify correct severity assignment for known warning types."""

    def test_build_warning_structure(self):
        w = build_warning(
            severity=SEVERITY_CRITICAL,
            category=CAT_DATA_INTEGRITY,
            code="TEST_CODE",
            message="test message",
            impact="test impact",
            recommended_action="fix it",
        )
        assert w["severity"] == SEVERITY_CRITICAL
        assert w["category"] == CAT_DATA_INTEGRITY
        assert w["code"] == "TEST_CODE"
        assert w["message"] == "test message"
        assert w["impact"] == "test impact"
        assert w["recommended_action"] == "fix it"

    def test_scaffolded_metrics_registered(self):
        expected = [
            "accumulation_distribution_bias",
            "volume_thrust_signal",
            "thrust_followthrough",
            "breadth_reversal_frequency",
            "trend_momentum_long",
        ]
        for metric in expected:
            assert is_scaffolded_metric(metric), f"{metric} should be scaffolded"

    def test_non_scaffolded_returns_false(self):
        assert not is_scaffolded_metric("advance_decline_ratio")
        assert not is_scaffolded_metric("pct_above_50dma")


# ═══════════════════════════════════════════════════════════════════════
# TEST 2 — SURVIVORSHIP BIAS / POINT-IN-TIME
# ═══════════════════════════════════════════════════════════════════════


class TestSurvivorshipBias:
    """No point-in-time constituents → critical structural risk."""

    def test_no_pit_historical_mode_is_critical(self):
        result = assess_survivorship_risk(
            _survivorship_risk_universe(), is_historical_mode=True
        )
        assert result["warning"]["severity"] == SEVERITY_CRITICAL
        assert result["confidence_penalty"] > 10
        assert result["historical_validity_penalty"] > 25
        assert result["survivorship_bias_risk"] is True
        assert result["historical_validity_degraded"] is True
        assert result["point_in_time_available"] is False

    def test_no_pit_snapshot_mode_is_high(self):
        result = assess_survivorship_risk(
            _survivorship_risk_universe(), is_historical_mode=False
        )
        assert result["warning"]["severity"] == SEVERITY_HIGH
        assert result["confidence_penalty"] <= 10
        assert result["survivorship_bias_risk"] is True

    def test_snapshot_penalty_less_than_historical(self):
        snap = assess_survivorship_risk(
            _survivorship_risk_universe(), is_historical_mode=False
        )
        hist = assess_survivorship_risk(
            _survivorship_risk_universe(), is_historical_mode=True
        )
        assert snap["confidence_penalty"] < hist["confidence_penalty"]
        assert snap["historical_validity_penalty"] < hist["historical_validity_penalty"]

    def test_pit_available_no_warning(self):
        result = assess_survivorship_risk(_healthy_universe())
        assert result["warning"] is None
        assert result["confidence_penalty"] == 0
        assert result["survivorship_bias_risk"] is False
        assert result["point_in_time_available"] is True


# ═══════════════════════════════════════════════════════════════════════
# TEST 3 — SCAFFOLDED METRICS SEPARATED FROM REAL DEFECTS
# ═══════════════════════════════════════════════════════════════════════


class TestScaffoldSeparation:
    """Scaffolded metrics grouped separately from real data-quality defects."""

    def test_scaffold_warnings_are_info_severity(self):
        pillars = {
            "volume_breadth": _make_pillar(60, missing_count=2, submetrics=[
                _make_submetric("up_down_volume_ratio"),
                _make_submetric("accumulation_distribution_bias", status="unavailable"),
                _make_submetric("volume_thrust_signal", status="unavailable"),
            ]),
            "participation_breadth": _make_pillar(70, submetrics=[
                _make_submetric("advance_decline_ratio"),
                _make_submetric("equal_weight_confirmation"),
            ]),
        }
        warnings = assess_data_completeness(pillars, _healthy_universe())
        scaffold_warnings = [w for w in warnings if w["category"] == CAT_SCAFFOLD]
        assert len(scaffold_warnings) >= 2
        for sw in scaffold_warnings:
            assert sw["severity"] == SEVERITY_INFO

    def test_real_missing_data_not_info(self):
        pillars = {
            "participation_breadth": _make_pillar(70, missing_count=1, submetrics=[
                _make_submetric("advance_decline_ratio"),
                _make_submetric("percent_up", status="unavailable"),
            ]),
        }
        warnings = assess_data_completeness(pillars, _healthy_universe())
        non_scaffold = [w for w in warnings if w["category"] != CAT_SCAFFOLD]
        for w in non_scaffold:
            assert w["severity"] != SEVERITY_INFO

    def test_grouped_warnings_separate_sections(self):
        pillars = {
            "volume_breadth": _make_pillar(60, missing_count=2, submetrics=[
                _make_submetric("up_down_volume_ratio"),
                _make_submetric("accumulation_distribution_bias", status="unavailable"),
                _make_submetric("volume_thrust_signal", status="unavailable"),
            ]),
            "participation_breadth": _make_pillar(70, missing_count=1, submetrics=[
                _make_submetric("advance_decline_ratio"),
                _make_submetric("percent_up", status="unavailable"),
            ]),
        }
        quality = compute_quality_scores(pillars, _survivorship_risk_universe())
        grouped = group_warnings_for_ui(quality["structured_warnings"])
        # Scaffolded items should be in deferred
        assert len(grouped["deferred_enhancements"]) >= 2
        # Survivorship should be in structural risks
        assert len(grouped["structural_risks"]) >= 1


# ═══════════════════════════════════════════════════════════════════════
# TEST 4 — CROSS-PILLAR DISAGREEMENT
# ═══════════════════════════════════════════════════════════════════════


class TestCrossPillarDisagreement:
    """Cross-pillar disagreement classified as interpretation warning by default."""

    def test_low_disagreement_no_warning(self):
        pillars = {
            "participation_breadth": _make_pillar(70),
            "trend_breadth": _make_pillar(65),
            "volume_breadth": _make_pillar(68),
            "leadership_quality": _make_pillar(72),
            "participation_stability": _make_pillar(66),
        }
        result = analyze_disagreement(pillars, _healthy_universe())
        assert result["warning"] is None
        assert result["confidence_penalty"] == 0

    def test_medium_disagreement_signal_warning(self):
        """Genuine signal conflict → medium severity."""
        pillars = {
            "participation_breadth": _make_pillar(85),
            "trend_breadth": _make_pillar(80),
            "volume_breadth": _make_pillar(30),
            "leadership_quality": _make_pillar(75),
            "participation_stability": _make_pillar(70),
        }
        result = analyze_disagreement(pillars, _healthy_universe())
        assert result["warning"] is not None
        assert result["severity"] == SEVERITY_MEDIUM
        assert result["warning"]["category"] == CAT_DISAGREEMENT
        assert result["is_data_driven"] is False

    def test_data_driven_disagreement_escalates(self):
        """Disagreement with observation disparity → escalated to high."""
        pillars = {
            "participation_breadth": _make_pillar(85, submetrics=[
                _make_submetric("advance_decline_ratio", observations=500),
            ]),
            "trend_breadth": _make_pillar(80, submetrics=[
                _make_submetric("pct_above_50dma", observations=500),
            ]),
            "volume_breadth": _make_pillar(25, submetrics=[
                _make_submetric("up_down_volume_ratio", observations=10),
            ]),
            "leadership_quality": _make_pillar(75, submetrics=[
                _make_submetric("ew_vs_cw_relative", observations=500),
            ]),
            "participation_stability": _make_pillar(70, submetrics=[
                _make_submetric("breadth_persistence_10d", observations=500),
            ]),
        }
        result = analyze_disagreement(pillars, _healthy_universe())
        assert result["is_data_driven"] is True
        assert result["severity"] == SEVERITY_HIGH
        assert len(result["suspected_causes"]) > 0

    def test_insufficient_pillars_no_check(self):
        pillars = {
            "participation_breadth": _make_pillar(70),
            "trend_breadth": _make_pillar(30),
        }
        result = analyze_disagreement(pillars, _healthy_universe())
        assert result["warning"] is None
        assert result["disagreement_level"] == 0.0


# ═══════════════════════════════════════════════════════════════════════
# TEST 5 — QUALITY SCORE SEPARATION
# ═══════════════════════════════════════════════════════════════════════


class TestQualityScores:
    """Confidence, data_quality, and historical_validity computed separately."""

    def test_all_three_scores_present(self):
        pillars = {
            "participation_breadth": _make_pillar(70, submetrics=[
                _make_submetric("advance_decline_ratio"),
                _make_submetric("equal_weight_confirmation"),
            ]),
            "trend_breadth": _make_pillar(65, submetrics=[
                _make_submetric("pct_above_50dma"),
            ]),
            "volume_breadth": _make_pillar(60, submetrics=[
                _make_submetric("up_down_volume_ratio"),
            ]),
        }
        result = compute_quality_scores(pillars, _healthy_universe())
        assert "confidence_score" in result
        assert "data_quality_score" in result
        assert "historical_validity_score" in result
        assert 0 <= result["confidence_score"] <= 100
        assert 0 <= result["data_quality_score"] <= 100
        assert 0 <= result["historical_validity_score"] <= 100

    def test_survivorship_degrades_historical_more(self):
        """Survivorship bias should hit historical_validity harder than confidence."""
        pillars = {
            "participation_breadth": _make_pillar(70, submetrics=[
                _make_submetric("advance_decline_ratio"),
                _make_submetric("equal_weight_confirmation"),
            ]),
        }
        clean = compute_quality_scores(pillars, _healthy_universe())
        degraded = compute_quality_scores(pillars, _survivorship_risk_universe())
        hist_drop = clean["historical_validity_score"] - degraded["historical_validity_score"]
        conf_drop = clean["confidence_score"] - degraded["confidence_score"]
        assert hist_drop > conf_drop, (
            f"Historical validity drop ({hist_drop}) should exceed "
            f"confidence drop ({conf_drop})"
        )

    def test_scaffolded_metrics_dont_penalize_data_quality(self):
        """Scaffolded metrics should not count as missing for completeness penalty."""
        pillars_with_scaffold = {
            "volume_breadth": _make_pillar(60, missing_count=2, submetrics=[
                _make_submetric("up_down_volume_ratio"),
                _make_submetric("accumulation_distribution_bias", status="unavailable"),
                _make_submetric("volume_thrust_signal", status="unavailable"),
            ]),
        }
        pillars_without_scaffold = {
            "volume_breadth": _make_pillar(60, missing_count=0, submetrics=[
                _make_submetric("up_down_volume_ratio"),
            ]),
        }
        with_scaffold = compute_quality_scores(
            pillars_with_scaffold, _healthy_universe()
        )
        without_scaffold = compute_quality_scores(
            pillars_without_scaffold, _healthy_universe()
        )
        # Should not be significantly different since scaffolded is excluded
        assert abs(with_scaffold["confidence_score"] - without_scaffold["confidence_score"]) < 5

    def test_signal_quality_labels(self):
        """Verify signal quality thresholds."""
        pillars = {"p": _make_pillar(70, submetrics=[_make_submetric("a")])}
        result = compute_quality_scores(pillars, _healthy_universe())
        assert result["signal_quality"] in ("high", "medium", "low")


# ═══════════════════════════════════════════════════════════════════════
# TEST 6 — WARNING GROUPING FOR UI
# ═══════════════════════════════════════════════════════════════════════


class TestWarningGrouping:
    """UI panel groups warnings correctly by severity/category."""

    def test_structural_risks_group(self):
        warnings = [
            build_warning(
                severity=SEVERITY_CRITICAL,
                category=CAT_DATA_INTEGRITY,
                code="TEST",
                message="test",
                impact="test",
                recommended_action="fix",
            ),
        ]
        grouped = group_warnings_for_ui(warnings)
        assert len(grouped["structural_risks"]) == 1
        assert len(grouped["deferred_enhancements"]) == 0

    def test_scaffold_to_deferred(self):
        warnings = [
            build_warning(
                severity=SEVERITY_INFO,
                category=CAT_SCAFFOLD,
                code="SCAFFOLD_TEST",
                message="test scaffold",
                impact="none",
                recommended_action="planned",
            ),
        ]
        grouped = group_warnings_for_ui(warnings)
        assert len(grouped["deferred_enhancements"]) == 1
        assert len(grouped["structural_risks"]) == 0

    def test_disagreement_to_signal_notes(self):
        warnings = [
            build_warning(
                severity=SEVERITY_MEDIUM,
                category=CAT_DISAGREEMENT,
                code="DISAGREE",
                message="pillars disagree",
                impact="lower confidence",
                recommended_action="review",
            ),
        ]
        grouped = group_warnings_for_ui(warnings)
        assert len(grouped["signal_notes"]) == 1

    def test_all_groups_present(self):
        grouped = group_warnings_for_ui([])
        assert "structural_risks" in grouped
        assert "completeness_issues" in grouped
        assert "signal_notes" in grouped
        assert "deferred_enhancements" in grouped


# ═══════════════════════════════════════════════════════════════════════
# TEST 7 — FULL ENGINE INTEGRATION
# ═══════════════════════════════════════════════════════════════════════


class TestEngineIntegration:
    """Full engine output includes new diagnostics fields."""

    @pytest.fixture
    def result_with_survivorship(self):
        return _full_engine_result(
            universe_meta=_survivorship_risk_universe()
        )

    @pytest.fixture
    def result_clean(self):
        return _full_engine_result()

    def test_engine_includes_quality_scores(self, result_clean):
        diag = result_clean["diagnostics"]
        assert "quality_scores" in diag
        assert "confidence_score" in diag["quality_scores"]
        assert "data_quality_score" in diag["quality_scores"]
        assert "historical_validity_score" in diag["quality_scores"]

    def test_engine_includes_grouped_warnings(self, result_clean):
        diag = result_clean["diagnostics"]
        assert "grouped_warnings" in diag
        grouped = diag["grouped_warnings"]
        assert "structural_risks" in grouped
        assert "deferred_enhancements" in grouped

    def test_engine_includes_structured_warnings(self, result_clean):
        diag = result_clean["diagnostics"]
        assert "structured_warnings" in diag
        # Should have at least scaffold warnings
        scaffold_warnings = [
            w for w in diag["structured_warnings"]
            if w.get("category") == CAT_SCAFFOLD
        ]
        assert len(scaffold_warnings) >= 2

    def test_engine_survivorship_fields(self, result_with_survivorship):
        assert result_with_survivorship["survivorship_bias_risk"] is True
        assert result_with_survivorship["point_in_time_constituents_available"] is False
        assert result_with_survivorship["historical_validity_degraded"] is True

    def test_engine_clean_survivorship_fields(self, result_clean):
        assert result_clean["survivorship_bias_risk"] is False
        assert result_clean["point_in_time_constituents_available"] is True

    def test_engine_new_score_fields(self, result_clean):
        assert "data_quality_score" in result_clean
        assert "historical_validity_score" in result_clean
        assert result_clean["data_quality_score"] >= 0
        assert result_clean["historical_validity_score"] >= 0

    def test_survivorship_degrades_historical_validity(self, result_with_survivorship):
        clean = _full_engine_result()
        diag_clean = clean["diagnostics"]["quality_scores"]
        diag_risk = result_with_survivorship["diagnostics"]["quality_scores"]
        assert diag_risk["historical_validity_score"] < diag_clean["historical_validity_score"]

    def test_scaffold_not_in_structural_risks(self, result_clean):
        grouped = result_clean["diagnostics"]["grouped_warnings"]
        for w in grouped.get("structural_risks", []):
            assert w["category"] != CAT_SCAFFOLD
            assert w["severity"] != SEVERITY_INFO
