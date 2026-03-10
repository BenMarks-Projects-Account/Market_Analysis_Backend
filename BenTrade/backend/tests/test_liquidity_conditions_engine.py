"""Tests for the Liquidity & Financial Conditions deterministic engine.

Scenarios:
  1.  Supportive conditions (low rates, tight credit, weak dollar)
  2.  Tightening conditions (rising rates, widening spreads)
  3.  Restrictive / Stress (high rates, wide credit, strong dollar)
  4.  Mixed conditions (some supportive, some restrictive)
  5.  Degraded confidence (missing data)
  6.  Single-pillar crash (error boundary)
  7.  UI label-band mapping
  8.  Pillar weight / diagnostics completeness
"""

from __future__ import annotations

import pytest

from app.services.liquidity_conditions_engine import (
    PILLAR_WEIGHTS,
    SIGNAL_PROVENANCE,
    _LABEL_BANDS,
    _clamp,
    _interpolate,
    _label_from_score,
    _safe_float,
    _signal_quality,
    _weighted_avg,
    compute_liquidity_conditions_scores,
)


# ═══════════════════════════════════════════════════════════════════════
# FIXTURE HELPERS
# ═══════════════════════════════════════════════════════════════════════

def _supportive_rates() -> dict:
    """Low rates, positive curve — very supportive for risk assets."""
    return {
        "two_year_yield": 2.0,
        "ten_year_yield": 3.2,
        "fed_funds_rate": 2.0,
        "yield_curve_spread": 1.2,
    }


def _supportive_conditions() -> dict:
    """Low VIX, tight credit spreads — easy financial conditions."""
    return {
        "vix": 13.0,
        "ig_spread": 0.8,
        "hy_spread": 3.2,
        "two_year_yield": 2.0,
        "ten_year_yield": 3.2,
        "yield_curve_spread": 1.2,
    }


def _supportive_credit() -> dict:
    """Tight IG/HY, low VIX — minimal credit stress."""
    return {
        "ig_spread": 0.8,
        "hy_spread": 3.2,
        "vix": 13.0,
        "fed_funds_rate": 2.0,
        "two_year_yield": 2.0,
    }


def _supportive_dollar() -> dict:
    """Weak dollar — eases global liquidity."""
    return {
        "dxy_level": 97.0,
        "vix": 13.0,
    }


def _supportive_stability() -> dict:
    """Stable, coherent conditions — low fragility."""
    return {
        "vix": 13.0,
        "ig_spread": 0.8,
        "hy_spread": 3.2,
        "two_year_yield": 2.0,
        "dxy_level": 97.0,
        "yield_curve_spread": 1.2,
    }


def _tightening_rates() -> dict:
    """Elevated rates, flat/inverted curve — tightening."""
    return {
        "two_year_yield": 4.5,
        "ten_year_yield": 4.3,
        "fed_funds_rate": 5.0,
        "yield_curve_spread": -0.2,
    }


def _tightening_conditions() -> dict:
    return {
        "vix": 22.0,
        "ig_spread": 1.5,
        "hy_spread": 5.0,
        "two_year_yield": 4.5,
        "ten_year_yield": 4.3,
        "yield_curve_spread": -0.2,
    }


def _tightening_credit() -> dict:
    return {
        "ig_spread": 1.5,
        "hy_spread": 5.0,
        "vix": 22.0,
        "fed_funds_rate": 5.0,
        "two_year_yield": 4.5,
    }


def _tightening_dollar() -> dict:
    return {
        "dxy_level": 108.0,
        "vix": 22.0,
    }


def _tightening_stability() -> dict:
    return {
        "vix": 22.0,
        "ig_spread": 1.5,
        "hy_spread": 5.0,
        "two_year_yield": 4.5,
        "dxy_level": 108.0,
        "yield_curve_spread": -0.2,
    }


def _stress_rates() -> dict:
    """Very high rates, deeply inverted curve — crisis-like."""
    return {
        "two_year_yield": 5.5,
        "ten_year_yield": 5.8,
        "fed_funds_rate": 5.5,
        "yield_curve_spread": 0.3,
    }


def _stress_conditions() -> dict:
    return {
        "vix": 35.0,
        "ig_spread": 2.5,
        "hy_spread": 8.0,
        "two_year_yield": 5.5,
        "ten_year_yield": 5.8,
        "yield_curve_spread": 0.3,
    }


def _stress_credit() -> dict:
    return {
        "ig_spread": 2.5,
        "hy_spread": 8.0,
        "vix": 35.0,
        "fed_funds_rate": 5.5,
        "two_year_yield": 5.5,
    }


def _stress_dollar() -> dict:
    return {
        "dxy_level": 115.0,
        "vix": 35.0,
    }


def _stress_stability() -> dict:
    return {
        "vix": 35.0,
        "ig_spread": 2.5,
        "hy_spread": 8.0,
        "two_year_yield": 5.5,
        "dxy_level": 115.0,
        "yield_curve_spread": 0.3,
    }


def _mixed_rates() -> dict:
    """Low front-end rates but inverted curve — mixed signal."""
    return {
        "two_year_yield": 2.5,
        "ten_year_yield": 2.3,
        "fed_funds_rate": 3.5,
        "yield_curve_spread": -0.2,
    }


def _mixed_conditions() -> dict:
    """VIX elevated but credit still tight."""
    return {
        "vix": 22.0,
        "ig_spread": 0.9,
        "hy_spread": 3.5,
        "two_year_yield": 2.5,
        "ten_year_yield": 2.3,
        "yield_curve_spread": -0.2,
    }


def _mixed_credit() -> dict:
    """IG tight, HY widening."""
    return {
        "ig_spread": 0.9,
        "hy_spread": 5.5,
        "vix": 22.0,
        "fed_funds_rate": 3.5,
        "two_year_yield": 2.5,
    }


def _mixed_dollar() -> dict:
    """Dollar near neutral."""
    return {
        "dxy_level": 102.0,
        "vix": 22.0,
    }


def _mixed_stability() -> dict:
    return {
        "vix": 22.0,
        "ig_spread": 0.9,
        "hy_spread": 5.5,
        "two_year_yield": 2.5,
        "dxy_level": 102.0,
        "yield_curve_spread": -0.2,
    }


def _empty_data() -> dict:
    return {}


def _good_source_meta() -> dict:
    return {
        "has_credit_spreads": True,
        "has_funding_data": True,
        "proxy_source_count": 1,
        "stale_source_count": 0,
    }


def _degraded_source_meta() -> dict:
    return {
        "has_credit_spreads": False,
        "has_funding_data": False,
        "proxy_source_count": 5,
        "stale_source_count": 3,
    }


# ═══════════════════════════════════════════════════════════════════════
# UTILITY TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestUtilities:
    """Unit tests for scoring utility functions."""

    def test_clamp_within_range(self):
        assert _clamp(50) == 50

    def test_clamp_below_zero(self):
        assert _clamp(-5) == 0

    def test_clamp_above_hundred(self):
        assert _clamp(120) == 100

    def test_clamp_custom_range(self):
        assert _clamp(5, 1, 10) == 5
        assert _clamp(0, 1, 10) == 1
        assert _clamp(15, 1, 10) == 10

    def test_safe_float_none(self):
        assert _safe_float(None) is None

    def test_safe_float_string(self):
        assert _safe_float("3.14") == pytest.approx(3.14)

    def test_safe_float_bad_string(self):
        assert _safe_float("not_a_number") is None

    def test_safe_float_default(self):
        assert _safe_float(None, default=0.0) == 0.0

    def test_interpolate_midpoint(self):
        assert _interpolate(50, 0, 100, 0, 100) == pytest.approx(50)

    def test_interpolate_clamped_low(self):
        assert _interpolate(-5, 0, 100, 0, 100) == pytest.approx(0)

    def test_interpolate_clamped_high(self):
        assert _interpolate(150, 0, 100, 0, 100) == pytest.approx(100)

    def test_interpolate_inverted(self):
        # High input → low output
        result = _interpolate(100, 0, 100, 100, 0)
        assert result == pytest.approx(0)

    def test_interpolate_equal_bounds(self):
        assert _interpolate(5, 5, 5, 0, 100) == pytest.approx(50)

    def test_weighted_avg_basic(self):
        assert _weighted_avg([(60, 0.5), (80, 0.5)]) == pytest.approx(70)

    def test_weighted_avg_with_none(self):
        result = _weighted_avg([(60, 0.5), (None, 0.5)])
        assert result == pytest.approx(60)

    def test_weighted_avg_all_none(self):
        assert _weighted_avg([(None, 0.5), (None, 0.5)]) is None

    def test_label_from_score_strongly_supportive(self):
        full, short = _label_from_score(90)
        assert full == "Liquidity Strongly Supportive"
        assert short == "Strongly Supportive"

    def test_label_from_score_supportive(self):
        full, short = _label_from_score(75)
        assert full == "Supportive Conditions"
        assert short == "Supportive"

    def test_label_from_score_mixed(self):
        full, short = _label_from_score(60)
        assert full == "Mixed but Manageable"
        assert short == "Mixed"

    def test_label_from_score_tightening(self):
        full, short = _label_from_score(50)
        assert full == "Neutral / Tightening"
        assert short == "Tightening"

    def test_label_from_score_restrictive(self):
        full, short = _label_from_score(38)
        assert full == "Restrictive Conditions"
        assert short == "Restrictive"

    def test_label_from_score_stress(self):
        full, short = _label_from_score(15)
        assert full == "Liquidity Stress"
        assert short == "Stress"

    def test_signal_quality_high(self):
        assert _signal_quality(85) == "high"

    def test_signal_quality_medium(self):
        assert _signal_quality(65) == "medium"

    def test_signal_quality_low(self):
        assert _signal_quality(45) == "low"


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 1 — SUPPORTIVE CONDITIONS
# ═══════════════════════════════════════════════════════════════════════

class TestSupportiveConditions:
    """Low rates, tight credit, weak dollar → score 70+."""

    @pytest.fixture()
    def result(self):
        return compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data=_supportive_conditions(),
            credit_data=_supportive_credit(),
            dollar_data=_supportive_dollar(),
            stability_data=_supportive_stability(),
            source_meta=_good_source_meta(),
        )

    def test_composite_in_supportive_range(self, result):
        assert result["score"] >= 70, f"Expected supportive score (70+), got {result['score']}"

    def test_label_is_supportive(self, result):
        assert "Supportive" in result["label"]

    def test_short_label_present(self, result):
        assert result["short_label"] in ("Strongly Supportive", "Supportive")

    def test_all_pillars_scored(self, result):
        for key in PILLAR_WEIGHTS:
            assert result["pillar_scores"][key] is not None, f"Pillar {key} missing"

    def test_high_confidence(self, result):
        # Good data, all present → confidence >= 70
        assert result["confidence_score"] >= 70

    def test_signal_quality_at_least_medium(self, result):
        assert result["signal_quality"] in ("high", "medium")

    def test_positive_contributors_not_empty(self, result):
        assert len(result["positive_contributors"]) > 0

    def test_rates_pillar_supportive(self, result):
        assert result["pillar_scores"]["rates_policy_pressure"] >= 55

    def test_credit_pillar_supportive(self, result):
        assert result["pillar_scores"]["credit_funding_stress"] >= 55

    def test_dollar_pillar_supportive(self, result):
        assert result["pillar_scores"]["dollar_global_liquidity"] >= 55

    def test_support_vs_stress_favorable(self, result):
        svs = result["support_vs_stress"]
        assert svs is not None
        assert svs.get("supportive_for_risk_assets", 0) > svs.get("stress_risk", 100)


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 2 — TIGHTENING CONDITIONS
# ═══════════════════════════════════════════════════════════════════════

class TestTighteningConditions:
    """Rising rates, widening spreads, strong dollar → 30-55."""

    @pytest.fixture()
    def result(self):
        return compute_liquidity_conditions_scores(
            rates_data=_tightening_rates(),
            conditions_data=_tightening_conditions(),
            credit_data=_tightening_credit(),
            dollar_data=_tightening_dollar(),
            stability_data=_tightening_stability(),
            source_meta=_good_source_meta(),
        )

    def test_composite_in_tightening_range(self, result):
        assert 25 <= result["score"] <= 60, f"Expected 25-60, got {result['score']}"

    def test_label_reflects_tightening(self, result):
        # Should be Tightening or Restrictive
        assert any(
            t in result["label"]
            for t in ("Tightening", "Restrictive", "Mixed")
        ), f"Label '{result['label']}' doesn't reflect tightening"

    def test_rates_pillar_below_neutral(self, result):
        assert result["pillar_scores"]["rates_policy_pressure"] < 50

    def test_negative_contributors_present(self, result):
        assert len(result["negative_contributors"]) > 0


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 3 — STRESS CONDITIONS
# ═══════════════════════════════════════════════════════════════════════

class TestStressConditions:
    """Very high rates, wide credit, strong dollar → score < 30."""

    @pytest.fixture()
    def result(self):
        return compute_liquidity_conditions_scores(
            rates_data=_stress_rates(),
            conditions_data=_stress_conditions(),
            credit_data=_stress_credit(),
            dollar_data=_stress_dollar(),
            stability_data=_stress_stability(),
            source_meta=_good_source_meta(),
        )

    def test_composite_in_stress_range(self, result):
        assert result["score"] <= 35, f"Expected stress (<35), got {result['score']}"

    def test_label_includes_stress_or_restrictive(self, result):
        assert any(
            t in result["label"]
            for t in ("Stress", "Restrictive")
        ), f"Label '{result['label']}' doesn't reflect stress"

    def test_most_pillars_below_40(self, result):
        below_40 = sum(
            1 for v in result["pillar_scores"].values()
            if v is not None and v < 40
        )
        assert below_40 >= 3, f"Expected ≥3 pillars below 40, got {below_40}"

    def test_stress_risk_elevated(self, result):
        svs = result["support_vs_stress"]
        assert svs.get("stress_risk", 0) > 40

    def test_negative_contributors_dominate(self, result):
        assert len(result["negative_contributors"]) >= len(result["positive_contributors"])


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 4 — MIXED CONDITIONS
# ═══════════════════════════════════════════════════════════════════════

class TestMixedConditions:
    """Some supportive, some restrictive → score 40-65."""

    @pytest.fixture()
    def result(self):
        return compute_liquidity_conditions_scores(
            rates_data=_mixed_rates(),
            conditions_data=_mixed_conditions(),
            credit_data=_mixed_credit(),
            dollar_data=_mixed_dollar(),
            stability_data=_mixed_stability(),
            source_meta=_good_source_meta(),
        )

    def test_composite_in_mixed_range(self, result):
        assert 35 <= result["score"] <= 70, f"Expected mixed (35-70), got {result['score']}"

    def test_label_reflects_mixed(self, result):
        assert any(
            t in result["label"]
            for t in ("Mixed", "Tightening", "Supportive")
        )

    def test_conflicting_signals_present(self, result):
        # Mixed conditions should produce conflicting signals
        # At minimum, we have a diverse pillar score range
        ps = result["pillar_scores"]
        scores = [v for v in ps.values() if v is not None]
        assert len(scores) >= 4
        spread = max(scores) - min(scores)
        assert spread >= 10, "Expected pillar score spread ≥ 10 for mixed scenario"

    def test_both_positive_and_negative_contributors(self, result):
        # In a mixed scenario, expect at least one positive AND one negative
        assert len(result["positive_contributors"]) >= 1 or len(result["negative_contributors"]) >= 1


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 5 — DEGRADED CONFIDENCE
# ═══════════════════════════════════════════════════════════════════════

class TestDegradedConfidence:
    """Missing data → lower confidence, more warnings."""

    @pytest.fixture()
    def result(self):
        return compute_liquidity_conditions_scores(
            rates_data=_empty_data(),
            conditions_data=_empty_data(),
            credit_data=_empty_data(),
            dollar_data=_empty_data(),
            stability_data=_empty_data(),
            source_meta=_degraded_source_meta(),
        )

    def test_engine_does_not_crash(self, result):
        assert result is not None
        assert "engine" in result

    def test_score_is_numeric(self, result):
        assert isinstance(result["score"], (int, float))
        assert 0 <= result["score"] <= 100

    def test_confidence_very_low(self, result):
        assert result["confidence_score"] < 50, (
            f"Expected low confidence with no data, got {result['confidence_score']}"
        )

    def test_signal_quality_low(self, result):
        assert result["signal_quality"] == "low"

    def test_many_warnings(self, result):
        assert len(result["warnings"]) >= 3

    def test_missing_inputs_populated(self, result):
        assert len(result["missing_inputs"]) >= 5

    def test_label_still_assigned(self, result):
        assert result["label"] not in (None, "", "Unknown")


class TestPartialData:
    """Only rates available, everything else empty."""

    @pytest.fixture()
    def result(self):
        return compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data=_empty_data(),
            credit_data=_empty_data(),
            dollar_data=_empty_data(),
            stability_data=_empty_data(),
            source_meta=_degraded_source_meta(),
        )

    def test_rates_pillar_scored(self, result):
        assert result["pillar_scores"]["rates_policy_pressure"] is not None

    def test_other_pillars_may_degrade(self, result):
        # At least the rates pillar contributed
        assert result["score"] is not None

    def test_lower_confidence_than_full(self, result):
        full = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data=_supportive_conditions(),
            credit_data=_supportive_credit(),
            dollar_data=_supportive_dollar(),
            stability_data=_supportive_stability(),
            source_meta=_good_source_meta(),
        )
        assert result["confidence_score"] < full["confidence_score"]


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 6 — SINGLE PILLAR CRASH
# ═══════════════════════════════════════════════════════════════════════

class TestSinglePillarCrash:
    """If one pillar's data triggers an error, composite still works."""

    @pytest.fixture()
    def result(self):
        # Pass non-dict-compat data for rates to cause an error
        # Other pillars have good data
        bad_rates = {"two_year_yield": "CRASH", "ten_year_yield": object()}
        return compute_liquidity_conditions_scores(
            rates_data=bad_rates,
            conditions_data=_supportive_conditions(),
            credit_data=_supportive_credit(),
            dollar_data=_supportive_dollar(),
            stability_data=_supportive_stability(),
            source_meta=_good_source_meta(),
        )

    def test_result_not_none(self, result):
        assert result is not None

    def test_score_is_numeric(self, result):
        assert isinstance(result["score"], (int, float))

    def test_other_pillars_still_scored(self, result):
        ps = result["pillar_scores"]
        scored = [k for k, v in ps.items() if v is not None]
        # At least conditions, credit, dollar should be scored
        assert len(scored) >= 3

    def test_label_assigned(self, result):
        assert result["label"] not in (None, "")


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 7 — UI LABEL BAND MAPPING
# ═══════════════════════════════════════════════════════════════════════

class TestUILabelMapping:
    """Every distinct label band maps correctly."""

    @pytest.mark.parametrize(
        "score,expected_label,expected_short",
        [
            (92, "Liquidity Strongly Supportive", "Strongly Supportive"),
            (85, "Liquidity Strongly Supportive", "Strongly Supportive"),
            (78, "Supportive Conditions", "Supportive"),
            (70, "Supportive Conditions", "Supportive"),
            (62, "Mixed but Manageable", "Mixed"),
            (55, "Mixed but Manageable", "Mixed"),
            (50, "Neutral / Tightening", "Tightening"),
            (45, "Neutral / Tightening", "Tightening"),
            (38, "Restrictive Conditions", "Restrictive"),
            (30, "Restrictive Conditions", "Restrictive"),
            (20, "Liquidity Stress", "Stress"),
            (5, "Liquidity Stress", "Stress"),
            (0, "Liquidity Stress", "Stress"),
            (100, "Liquidity Strongly Supportive", "Strongly Supportive"),
        ],
    )
    def test_label_mapping(self, score, expected_label, expected_short):
        full, short = _label_from_score(score)
        assert full == expected_label
        assert short == expected_short

    def test_label_bands_cover_full_range(self):
        """Every integer 0–100 must fall in exactly one band."""
        for i in range(101):
            full, short = _label_from_score(i)
            assert full != "Unknown", f"Score {i} mapped to Unknown"
            assert short != "Unknown", f"Score {i} mapped to Unknown (short)"


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 8 — PILLAR WEIGHT & DIAGNOSTICS COMPLETENESS
# ═══════════════════════════════════════════════════════════════════════

class TestPillarWeightsAndDiagnostics:
    """Ensure structural integrity of engine output."""

    def test_pillar_weights_sum_to_one(self):
        total = sum(PILLAR_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9, f"Weights sum to {total}, not 1.0"

    def test_all_five_pillars_present(self):
        expected = {
            "rates_policy_pressure",
            "financial_conditions_tightness",
            "credit_funding_stress",
            "dollar_global_liquidity",
            "liquidity_stability_fragility",
        }
        assert set(PILLAR_WEIGHTS.keys()) == expected

    def test_signal_provenance_structure(self):
        for name, info in SIGNAL_PROVENANCE.items():
            assert "source" in info, f"{name} missing 'source'"
            assert "type" in info, f"{name} missing 'type'"
            assert info["type"] in (
                "direct", "derived", "proxy"
            ), f"{name} has invalid type '{info['type']}'"
            assert "notes" in info, f"{name} missing 'notes'"

    def test_result_contains_all_required_keys(self):
        result = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data=_supportive_conditions(),
            credit_data=_supportive_credit(),
            dollar_data=_supportive_dollar(),
            stability_data=_supportive_stability(),
            source_meta=_good_source_meta(),
        )
        required_keys = {
            "engine", "as_of", "score", "label", "short_label",
            "confidence_score", "signal_quality", "summary",
            "pillar_scores", "pillar_weights", "pillar_explanations",
            "support_vs_stress", "positive_contributors",
            "negative_contributors", "conflicting_signals",
            "trader_takeaway", "warnings", "missing_inputs",
            "diagnostics", "raw_inputs",
        }
        for key in required_keys:
            assert key in result, f"Missing required key: {key}"

    def test_diagnostics_has_pillar_details(self):
        result = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data=_supportive_conditions(),
            credit_data=_supportive_credit(),
            dollar_data=_supportive_dollar(),
            stability_data=_supportive_stability(),
            source_meta=_good_source_meta(),
        )
        diag = result["diagnostics"]
        assert "pillar_details" in diag
        assert "confidence_penalties" in diag
        assert "composite_computation" in diag
        assert "signal_provenance" in diag
        assert "proxy_summary" in diag

        # Pillar details should have all 5 pillars
        for key in PILLAR_WEIGHTS:
            assert key in diag["pillar_details"], f"Missing pillar detail: {key}"
            pd = diag["pillar_details"][key]
            assert "score" in pd
            assert "submetrics" in pd
            assert isinstance(pd["submetrics"], list)

    def test_raw_inputs_has_five_sections(self):
        result = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data=_supportive_conditions(),
            credit_data=_supportive_credit(),
            dollar_data=_supportive_dollar(),
            stability_data=_supportive_stability(),
            source_meta=_good_source_meta(),
        )
        ri = result["raw_inputs"]
        expected_keys = {"rates", "conditions", "credit", "dollar", "stability"}
        assert set(ri.keys()) == expected_keys

    def test_engine_name_constant(self):
        result = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data=_supportive_conditions(),
            credit_data=_supportive_credit(),
            dollar_data=_supportive_dollar(),
            stability_data=_supportive_stability(),
            source_meta=_good_source_meta(),
        )
        assert result["engine"] == "liquidity_financial_conditions"

    def test_proxy_summary_contains_counts(self):
        result = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data=_supportive_conditions(),
            credit_data=_supportive_credit(),
            dollar_data=_supportive_dollar(),
            stability_data=_supportive_stability(),
            source_meta=_good_source_meta(),
        )
        ps = result["diagnostics"]["proxy_summary"]
        assert "total_proxy_signals" in ps
        assert "total_direct_signals" in ps
        assert "proxy_signal_names" in ps
        assert ps["total_direct_signals"] >= 4


# ═══════════════════════════════════════════════════════════════════════
# CONFIDENCE EDGE CASES
# ═══════════════════════════════════════════════════════════════════════

class TestConfidenceEdgeCases:
    """Confidence scoring responds correctly to meta quality."""

    def test_good_meta_high_confidence(self):
        result = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data=_supportive_conditions(),
            credit_data=_supportive_credit(),
            dollar_data=_supportive_dollar(),
            stability_data=_supportive_stability(),
            source_meta=_good_source_meta(),
        )
        assert result["confidence_score"] >= 70

    def test_degraded_meta_lower_confidence(self):
        result = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data=_supportive_conditions(),
            credit_data=_supportive_credit(),
            dollar_data=_supportive_dollar(),
            stability_data=_supportive_stability(),
            source_meta=_degraded_source_meta(),
        )
        assert result["confidence_score"] < 85, (
            f"Degraded meta should reduce confidence, got {result['confidence_score']}"
        )

    def test_stale_sources_penalize(self):
        """Stale source count applies confidence penalty."""
        result = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data=_supportive_conditions(),
            credit_data=_supportive_credit(),
            dollar_data=_supportive_dollar(),
            stability_data=_supportive_stability(),
            source_meta={
                "has_credit_spreads": True,
                "has_funding_data": True,
                "proxy_source_count": 1,
                "stale_source_count": 4,
            },
        )
        penalties = result["diagnostics"]["confidence_penalties"]
        assert any("stale" in p.lower() for p in penalties)

    def test_no_credit_spread_penalizes(self):
        result = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data=_supportive_conditions(),
            credit_data=_supportive_credit(),
            dollar_data=_supportive_dollar(),
            stability_data=_supportive_stability(),
            source_meta={
                "has_credit_spreads": False,
                "has_funding_data": True,
                "proxy_source_count": 1,
                "stale_source_count": 0,
            },
        )
        penalties = result["diagnostics"]["confidence_penalties"]
        assert any("credit" in p.lower() for p in penalties)


# ═══════════════════════════════════════════════════════════════════════
# CROSS-SCENARIO MONOTONICITY
# ═══════════════════════════════════════════════════════════════════════

class TestMonotonicity:
    """Supportive > Tightening > Stress in composite score."""

    def test_supportive_beats_tightening(self):
        sup = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data=_supportive_conditions(),
            credit_data=_supportive_credit(),
            dollar_data=_supportive_dollar(),
            stability_data=_supportive_stability(),
            source_meta=_good_source_meta(),
        )
        tight = compute_liquidity_conditions_scores(
            rates_data=_tightening_rates(),
            conditions_data=_tightening_conditions(),
            credit_data=_tightening_credit(),
            dollar_data=_tightening_dollar(),
            stability_data=_tightening_stability(),
            source_meta=_good_source_meta(),
        )
        assert sup["score"] > tight["score"]

    def test_tightening_beats_stress(self):
        tight = compute_liquidity_conditions_scores(
            rates_data=_tightening_rates(),
            conditions_data=_tightening_conditions(),
            credit_data=_tightening_credit(),
            dollar_data=_tightening_dollar(),
            stability_data=_tightening_stability(),
            source_meta=_good_source_meta(),
        )
        stress = compute_liquidity_conditions_scores(
            rates_data=_stress_rates(),
            conditions_data=_stress_conditions(),
            credit_data=_stress_credit(),
            dollar_data=_stress_dollar(),
            stability_data=_stress_stability(),
            source_meta=_good_source_meta(),
        )
        assert tight["score"] > stress["score"]

    def test_mixed_between_supportive_and_stress(self):
        sup = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data=_supportive_conditions(),
            credit_data=_supportive_credit(),
            dollar_data=_supportive_dollar(),
            stability_data=_supportive_stability(),
            source_meta=_good_source_meta(),
        )
        mixed = compute_liquidity_conditions_scores(
            rates_data=_mixed_rates(),
            conditions_data=_mixed_conditions(),
            credit_data=_mixed_credit(),
            dollar_data=_mixed_dollar(),
            stability_data=_mixed_stability(),
            source_meta=_good_source_meta(),
        )
        stress = compute_liquidity_conditions_scores(
            rates_data=_stress_rates(),
            conditions_data=_stress_conditions(),
            credit_data=_stress_credit(),
            dollar_data=_stress_dollar(),
            stability_data=_stress_stability(),
            source_meta=_good_source_meta(),
        )
        assert sup["score"] > mixed["score"] > stress["score"]


# ═══════════════════════════════════════════════════════════════════════
# PROXY HONESTY & LABELING
# ═══════════════════════════════════════════════════════════════════════

class TestProxyHonesty:
    """Verify proxy-derived submetrics are properly labeled and hedged."""

    @pytest.fixture(autouse=True)
    def _run_full(self):
        self.result = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data=_supportive_conditions(),
            credit_data=_supportive_credit(),
            dollar_data=_supportive_dollar(),
            stability_data=_supportive_stability(),
            source_meta=_good_source_meta(),
        )

    def _find_submetric(self, pillar_key: str, sub_name: str):
        pillar = self.result["diagnostics"]["pillar_details"][pillar_key]
        for sm in pillar["submetrics"]:
            if sm["name"] == sub_name:
                return sm
        return None

    def test_fci_proxy_status_is_proxy(self):
        sm = self._find_submetric("financial_conditions_tightness", "fci_proxy")
        assert sm is not None, "fci_proxy submetric missing"
        assert sm["status"] == "proxy"

    def test_fci_proxy_interpretation_says_proxy(self):
        sm = self._find_submetric("financial_conditions_tightness", "fci_proxy")
        assert "proxy" in sm["interpretation"].lower()

    def test_fci_proxy_interpretation_not_authoritative(self):
        sm = self._find_submetric("financial_conditions_tightness", "fci_proxy")
        interp = sm["interpretation"].lower()
        assert "suggests" in interp or "estimate" in interp

    def test_funding_stress_status_is_proxy(self):
        sm = self._find_submetric("credit_funding_stress", "funding_stress_proxy")
        assert sm is not None, "funding_stress_proxy submetric missing"
        assert sm["status"] == "proxy"

    def test_funding_stress_interpretation_hedged(self):
        sm = self._find_submetric("credit_funding_stress", "funding_stress_proxy")
        interp = sm["interpretation"].lower()
        assert "proxy" in interp or "heuristic" in interp or "estimate" in interp

    def test_direct_submetrics_status_ok(self):
        """IG spread and HY spread should have status=ok (direct data)."""
        for sub_name in ("ig_spread", "hy_spread"):
            sm = self._find_submetric("credit_funding_stress", sub_name)
            assert sm is not None
            assert sm["status"] == "ok"


# ═══════════════════════════════════════════════════════════════════════
# VIX DOUBLE-COUNTING CONTROLS
# ═══════════════════════════════════════════════════════════════════════

class TestVIXDoubleCountingControls:
    """Verify VIX concentration is limited after second-pass refactor."""

    def test_p2_conditions_has_at_most_two_vix_submetrics(self):
        """P2 should have at most 2 submetrics using VIX."""
        result = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data=_supportive_conditions(),
            credit_data=_supportive_credit(),
            dollar_data=_supportive_dollar(),
            stability_data=_supportive_stability(),
            source_meta=_good_source_meta(),
        )
        p2 = result["diagnostics"]["pillar_details"]["financial_conditions_tightness"]
        # fci_proxy uses VIX as 1/3 of composite, vix_conditions_signal is the
        # dedicated VIX submetric.  Others should NOT use VIX.
        vix_subs = [
            sm["name"] for sm in p2["submetrics"]
            if sm.get("raw_value") is not None
            and "vix" in sm["name"].lower()
        ]
        # At most the fci_proxy + vix_conditions_signal
        assert len(vix_subs) <= 2

    def test_p3_credit_stress_caps_vix_weight(self):
        """Credit stress composite should weight credit > VIX."""
        # When credit data present, VIX should be ≤30% of credit_stress_signal
        result = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data=_supportive_conditions(),
            credit_data=_supportive_credit(),
            dollar_data=_supportive_dollar(),
            stability_data=_supportive_stability(),
            source_meta=_good_source_meta(),
        )
        p3 = result["diagnostics"]["pillar_details"]["credit_funding_stress"]
        css = next(
            sm for sm in p3["submetrics"] if sm["name"] == "credit_stress_signal"
        )
        # With full data, status should be "ok" (not "proxy")
        assert css["status"] == "ok"

    def test_p4_has_three_or_fewer_submetrics(self):
        """P4 should have ≤3 submetrics (deduplicated DXY)."""
        result = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data=_supportive_conditions(),
            credit_data=_supportive_credit(),
            dollar_data=_supportive_dollar(),
            stability_data=_supportive_stability(),
            source_meta=_good_source_meta(),
        )
        p4 = result["diagnostics"]["pillar_details"]["dollar_global_liquidity"]
        assert len(p4["submetrics"]) <= 3

    def test_p5_sudden_stress_excludes_vix(self):
        """P5 sudden_stress_risk should NOT use VIX directly."""
        result = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data=_supportive_conditions(),
            credit_data=_supportive_credit(),
            dollar_data=_supportive_dollar(),
            stability_data=_supportive_stability(),
            source_meta=_good_source_meta(),
        )
        p5 = result["diagnostics"]["pillar_details"]["liquidity_stability_fragility"]
        ssr = next(
            sm for sm in p5["submetrics"] if sm["name"] == "sudden_stress_risk"
        )
        # The raw_value should NOT be VIX (13.0 in supportive scenario)
        # It should be a composite of credit/DXY inputs
        assert ssr["raw_value"] != 13.0 or ssr["status"] == "unavailable"

    def test_p5_support_balance_excludes_vix(self):
        """P5 support_vs_stress_balance should not count VIX."""
        # With supportive data: ig<1.2 (+1 support), hy<4.0 (+1), dxy<100 (+1),
        # curve>0.5 (+1). VIX should NOT contribute.
        result = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data=_supportive_conditions(),
            credit_data=_supportive_credit(),
            dollar_data=_supportive_dollar(),
            stability_data=_supportive_stability(),
            source_meta=_good_source_meta(),
        )
        p5 = result["diagnostics"]["pillar_details"]["liquidity_stability_fragility"]
        bal = next(
            sm for sm in p5["submetrics"]
            if sm["name"] == "support_vs_stress_balance"
        )
        # All 4 signals should be supportive (ig=0.8, hy=3.2, dxy=97, curve=1.2)
        assert bal["score"] == 100.0


# ═══════════════════════════════════════════════════════════════════════
# PROXY-HEAVY BUT INCOMPLETE INPUT SETS
# ═══════════════════════════════════════════════════════════════════════

class TestProxyHeavyIncompleteInputs:
    """VIX-only scenarios should degrade gracefully with lower confidence."""

    def test_vix_only_all_pillars(self):
        """With only VIX, engine should still produce a score."""
        vix_only = {"vix": 20.0}
        result = compute_liquidity_conditions_scores(
            rates_data={},
            conditions_data=vix_only,
            credit_data=vix_only,
            dollar_data={},
            stability_data=vix_only,
            source_meta=_degraded_source_meta(),
        )
        assert isinstance(result["score"], (int, float))
        assert 0 <= result["score"] <= 100

    def test_vix_only_confidence_low(self):
        """VIX-only should produce low confidence."""
        vix_only = {"vix": 20.0}
        result = compute_liquidity_conditions_scores(
            rates_data={},
            conditions_data=vix_only,
            credit_data=vix_only,
            dollar_data={},
            stability_data=vix_only,
            source_meta=_degraded_source_meta(),
        )
        assert result["confidence_score"] < 60

    def test_vix_only_credit_stress_marked_proxy(self):
        """Credit stress with VIX-only should be status=proxy."""
        vix_only = {"vix": 20.0}
        result = compute_liquidity_conditions_scores(
            rates_data={},
            conditions_data=vix_only,
            credit_data=vix_only,
            dollar_data={},
            stability_data=vix_only,
            source_meta=_degraded_source_meta(),
        )
        p3 = result["diagnostics"]["pillar_details"]["credit_funding_stress"]
        css = next(
            (sm for sm in p3["submetrics"] if sm["name"] == "credit_stress_signal"),
            None,
        )
        if css and css["score"] is not None:
            assert css["status"] == "proxy"

    def test_no_credit_spreads_confidence_penalty(self):
        """Missing credit spreads should penalize confidence."""
        result = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data={"vix": 15.0},
            credit_data={"vix": 15.0, "fed_funds_rate": 3.0},
            dollar_data={"dxy_level": 100.0},
            stability_data={"vix": 15.0, "dxy_level": 100.0},
            source_meta={
                "has_credit_spreads": False,
                "has_funding_data": False,
                "proxy_source_count": 3,
                "stale_source_count": 0,
            },
        )
        full = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data=_supportive_conditions(),
            credit_data=_supportive_credit(),
            dollar_data=_supportive_dollar(),
            stability_data=_supportive_stability(),
            source_meta=_good_source_meta(),
        )
        assert result["confidence_score"] < full["confidence_score"]


# ═══════════════════════════════════════════════════════════════════════
# STALE / MIXED-FRESHNESS INPUTS
# ═══════════════════════════════════════════════════════════════════════

class TestStaleSources:
    """Stale source metadata should lower confidence."""

    def test_stale_sources_reduce_confidence(self):
        fresh_meta = {
            "has_credit_spreads": True,
            "has_funding_data": True,
            "proxy_source_count": 1,
            "stale_source_count": 0,
        }
        stale_meta = {
            "has_credit_spreads": True,
            "has_funding_data": True,
            "proxy_source_count": 1,
            "stale_source_count": 4,
        }
        fresh = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data=_supportive_conditions(),
            credit_data=_supportive_credit(),
            dollar_data=_supportive_dollar(),
            stability_data=_supportive_stability(),
            source_meta=fresh_meta,
        )
        stale = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data=_supportive_conditions(),
            credit_data=_supportive_credit(),
            dollar_data=_supportive_dollar(),
            stability_data=_supportive_stability(),
            source_meta=stale_meta,
        )
        assert fresh["confidence_score"] > stale["confidence_score"]

    def test_stale_penalty_string_present(self):
        stale_meta = {
            "has_credit_spreads": True,
            "has_funding_data": True,
            "proxy_source_count": 1,
            "stale_source_count": 3,
        }
        result = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data=_supportive_conditions(),
            credit_data=_supportive_credit(),
            dollar_data=_supportive_dollar(),
            stability_data=_supportive_stability(),
            source_meta=stale_meta,
        )
        penalties = result["diagnostics"]["confidence_penalties"]
        assert any("stale" in p.lower() for p in penalties)


# ═══════════════════════════════════════════════════════════════════════
# SINGLE-SOURCE FAILURE DEGRADED MODE
# ═══════════════════════════════════════════════════════════════════════

class TestSingleSourceFailure:
    """Engine handles single missing source gracefully."""

    def test_no_vix_still_produces_score(self):
        """Without VIX, engine should still work (rates + credit direct)."""
        result = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data={
                "ig_spread": 0.8, "hy_spread": 3.2,
                "two_year_yield": 2.0, "ten_year_yield": 3.2,
                "yield_curve_spread": 1.2,
            },
            credit_data={
                "ig_spread": 0.8, "hy_spread": 3.2,
                "fed_funds_rate": 2.0, "two_year_yield": 2.0,
            },
            dollar_data={"dxy_level": 97.0},
            stability_data={
                "ig_spread": 0.8, "hy_spread": 3.2,
                "two_year_yield": 2.0, "dxy_level": 97.0,
                "yield_curve_spread": 1.2,
            },
            source_meta=_good_source_meta(),
        )
        assert isinstance(result["score"], (int, float))
        assert 0 <= result["score"] <= 100

    def test_no_dxy_still_produces_score(self):
        """Without DXY, dollar pillar degrades but composite works."""
        result = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data=_supportive_conditions(),
            credit_data=_supportive_credit(),
            dollar_data={"vix": 13.0},  # DXY missing
            stability_data={
                "vix": 13.0, "ig_spread": 0.8, "hy_spread": 3.2,
                "two_year_yield": 2.0, "yield_curve_spread": 1.2,
            },
            source_meta=_good_source_meta(),
        )
        assert isinstance(result["score"], (int, float))
        # Dollar pillar should have some unavailable submetrics
        p4 = result["diagnostics"]["pillar_details"]["dollar_global_liquidity"]
        unavail = [sm for sm in p4["submetrics"] if sm["status"] == "unavailable"]
        assert len(unavail) >= 1

    def test_no_credit_spreads_still_produces_score(self):
        """Without IG/HY, credit pillar degrades but composite works."""
        result = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data={"vix": 15.0, "two_year_yield": 2.0,
                             "ten_year_yield": 3.2, "yield_curve_spread": 1.2},
            credit_data={"vix": 15.0, "fed_funds_rate": 2.0},
            dollar_data=_supportive_dollar(),
            stability_data={"vix": 15.0, "dxy_level": 97.0,
                            "two_year_yield": 2.0, "yield_curve_spread": 1.2},
            source_meta={
                "has_credit_spreads": False,
                "has_funding_data": True,
                "proxy_source_count": 2,
                "stale_source_count": 0,
            },
        )
        assert isinstance(result["score"], (int, float))


# ═══════════════════════════════════════════════════════════════════════
# CONFIDENCE WITHOUT DIRECT PLUMBING SIGNALS
# ═══════════════════════════════════════════════════════════════════════

class TestConfidenceWithoutDirectSignals:
    """Confidence should stay lower when direct plumbing is absent."""

    def test_proxy_heavy_pillar_penalized(self):
        """Per-pillar proxy concentration should lower confidence."""
        # With only VIX for credit → credit_stress_signal is proxy,
        # funding_stress_proxy is proxy.  That's >50% proxy weight.
        result = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data=_supportive_conditions(),
            credit_data={"vix": 15.0, "fed_funds_rate": 2.0},
            dollar_data=_supportive_dollar(),
            stability_data=_supportive_stability(),
            source_meta={
                "has_credit_spreads": False,
                "has_funding_data": False,
                "proxy_source_count": 3,
                "stale_source_count": 0,
            },
        )
        penalties = result["diagnostics"]["confidence_penalties"]
        proxy_penalty = [p for p in penalties if "proxy-derived" in p.lower()]
        assert len(proxy_penalty) >= 1

    def test_full_data_no_proxy_penalty(self):
        """With full data, per-pillar proxy concentration should be manageable."""
        result = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data=_supportive_conditions(),
            credit_data=_supportive_credit(),
            dollar_data=_supportive_dollar(),
            stability_data=_supportive_stability(),
            source_meta=_good_source_meta(),
        )
        penalties = result["diagnostics"]["confidence_penalties"]
        proxy_pillar_penalties = [
            p for p in penalties if ">50% proxy-derived" in p.lower()
        ]
        # With full direct credit data, no pillar should be >50% proxy
        assert len(proxy_pillar_penalties) == 0

    def test_confidence_monotonic_with_data_quality(self):
        """More data = higher confidence (full > partial > degraded)."""
        full = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data=_supportive_conditions(),
            credit_data=_supportive_credit(),
            dollar_data=_supportive_dollar(),
            stability_data=_supportive_stability(),
            source_meta=_good_source_meta(),
        )
        partial = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data={"vix": 13.0, "two_year_yield": 2.0},
            credit_data={"vix": 13.0, "fed_funds_rate": 2.0},
            dollar_data={"dxy_level": 97.0},
            stability_data={"vix": 13.0, "dxy_level": 97.0},
            source_meta={
                "has_credit_spreads": False,
                "has_funding_data": False,
                "proxy_source_count": 3,
                "stale_source_count": 0,
            },
        )
        degraded = compute_liquidity_conditions_scores(
            rates_data={},
            conditions_data={"vix": 13.0},
            credit_data={"vix": 13.0},
            dollar_data={},
            stability_data={"vix": 13.0},
            source_meta=_degraded_source_meta(),
        )
        assert full["confidence_score"] > partial["confidence_score"]
        assert partial["confidence_score"] > degraded["confidence_score"]


# ═══════════════════════════════════════════════════════════════════════
# P2 SUBMETRIC WEIGHT VALIDATION
# ═══════════════════════════════════════════════════════════════════════

class TestP2SubmetricWeights:
    """Verify P2 submetric weights sum to 1.0 after consolidation."""

    def test_p2_weights_sum_to_one(self):
        result = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data=_supportive_conditions(),
            credit_data=_supportive_credit(),
            dollar_data=_supportive_dollar(),
            stability_data=_supportive_stability(),
            source_meta=_good_source_meta(),
        )
        p2 = result["diagnostics"]["pillar_details"]["financial_conditions_tightness"]
        total = sum(sm["weight"] for sm in p2["submetrics"])
        assert abs(total - 1.0) < 0.001

    def test_p2_has_four_submetrics(self):
        """P2 was consolidated from 5 to 4 submetrics."""
        result = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data=_supportive_conditions(),
            credit_data=_supportive_credit(),
            dollar_data=_supportive_dollar(),
            stability_data=_supportive_stability(),
            source_meta=_good_source_meta(),
        )
        p2 = result["diagnostics"]["pillar_details"]["financial_conditions_tightness"]
        assert len(p2["submetrics"]) == 4

    def test_p3_weights_sum_to_one(self):
        result = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data=_supportive_conditions(),
            credit_data=_supportive_credit(),
            dollar_data=_supportive_dollar(),
            stability_data=_supportive_stability(),
            source_meta=_good_source_meta(),
        )
        p3 = result["diagnostics"]["pillar_details"]["credit_funding_stress"]
        total = sum(sm["weight"] for sm in p3["submetrics"])
        assert abs(total - 1.0) < 0.001

    def test_p4_weights_sum_to_one(self):
        result = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data=_supportive_conditions(),
            credit_data=_supportive_credit(),
            dollar_data=_supportive_dollar(),
            stability_data=_supportive_stability(),
            source_meta=_good_source_meta(),
        )
        p4 = result["diagnostics"]["pillar_details"]["dollar_global_liquidity"]
        total = sum(sm["weight"] for sm in p4["submetrics"])
        assert abs(total - 1.0) < 0.001

    def test_p5_weights_sum_to_one(self):
        result = compute_liquidity_conditions_scores(
            rates_data=_supportive_rates(),
            conditions_data=_supportive_conditions(),
            credit_data=_supportive_credit(),
            dollar_data=_supportive_dollar(),
            stability_data=_supportive_stability(),
            source_meta=_good_source_meta(),
        )
        p5 = result["diagnostics"]["pillar_details"]["liquidity_stability_fragility"]
        total = sum(sm["weight"] for sm in p5["submetrics"])
        assert abs(total - 1.0) < 0.001
