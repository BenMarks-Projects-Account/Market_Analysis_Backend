"""Tests for confidence_framework v1.

Coverage targets:
- normalize_confidence (scale conversion, edge cases)
- confidence_label / signal_quality_label / uncertainty_level
- make_impact + helpers (impact_from_quality, etc.)
- aggregate_impacts / apply_impacts
- build_confidence_assessment (full builder, all paths)
- build_uncertainty_summary
- quick_assess
- Integration: orchestrator, prompt_payload, decision_response_contract
"""

import math
import pytest

from app.services.confidence_framework import (
    CONFLICT_PENALTIES,
    COVERAGE_PENALTIES,
    FRESHNESS_PENALTIES,
    QUALITY_PENALTIES,
    VALID_IMPACT_CATEGORIES,
    aggregate_impacts,
    apply_impacts,
    build_confidence_assessment,
    build_uncertainty_summary,
    confidence_label,
    impact_from_conflict,
    impact_from_coverage,
    impact_from_freshness,
    impact_from_quality,
    make_impact,
    normalize_confidence,
    quick_assess,
    signal_quality_label,
    uncertainty_level,
)


# =====================================================================
#  normalize_confidence
# =====================================================================

class TestNormalizeConfidence:
    """Tests for normalize_confidence()."""

    def test_none_returns_none(self):
        assert normalize_confidence(None) is None

    def test_non_numeric_string_returns_none(self):
        assert normalize_confidence("abc") is None

    def test_nan_returns_none(self):
        assert normalize_confidence(float("nan")) is None

    def test_zero_float(self):
        assert normalize_confidence(0.0) == 0.0

    def test_one_float(self):
        assert normalize_confidence(1.0) == 1.0

    def test_mid_range_float(self):
        assert normalize_confidence(0.65) == 0.65

    def test_integer_zero(self):
        assert normalize_confidence(0) == 0.0

    def test_integer_one(self):
        assert normalize_confidence(1) == 1.0

    def test_integer_50_converts(self):
        assert normalize_confidence(50) == 0.5

    def test_integer_100_converts(self):
        assert normalize_confidence(100) == 1.0

    def test_float_85_converts(self):
        assert normalize_confidence(85.0) == 0.85

    def test_negative_clamps_zero(self):
        assert normalize_confidence(-5) == 0.0

    def test_over_100_clamps_one(self):
        assert normalize_confidence(150) == 1.0

    def test_string_numeric(self):
        assert normalize_confidence("0.75") == 0.75

    def test_string_integer_scale(self):
        assert normalize_confidence("90") == 0.9

    def test_empty_string_returns_none(self):
        assert normalize_confidence("") is None

    def test_bool_true(self):
        # bool is subclass of int → True=1 → 1.0
        assert normalize_confidence(True) == 1.0

    def test_bool_false(self):
        assert normalize_confidence(False) == 0.0


# =====================================================================
#  confidence_label
# =====================================================================

class TestConfidenceLabel:
    """Tests for confidence_label()."""

    def test_none_returns_none_label(self):
        assert confidence_label(None) == "none"

    def test_nan_returns_none_label(self):
        assert confidence_label(float("nan")) == "none"

    def test_zero(self):
        assert confidence_label(0.0) == "none"

    def test_low_boundary(self):
        assert confidence_label(0.30) == "low"

    def test_below_low(self):
        assert confidence_label(0.29) == "none"

    def test_moderate_boundary(self):
        assert confidence_label(0.60) == "moderate"

    def test_below_moderate(self):
        assert confidence_label(0.59) == "low"

    def test_high_boundary(self):
        assert confidence_label(0.80) == "high"

    def test_below_high(self):
        assert confidence_label(0.79) == "moderate"

    def test_perfect(self):
        assert confidence_label(1.0) == "high"


# =====================================================================
#  signal_quality_label
# =====================================================================

class TestSignalQualityLabel:
    """Tests for signal_quality_label()."""

    def test_none(self):
        assert signal_quality_label(None) == "low"

    def test_zero(self):
        assert signal_quality_label(0.0) == "low"

    def test_medium_boundary(self):
        assert signal_quality_label(0.60) == "medium"

    def test_below_medium(self):
        assert signal_quality_label(0.59) == "low"

    def test_high_boundary(self):
        assert signal_quality_label(0.80) == "high"

    def test_below_high(self):
        assert signal_quality_label(0.79) == "medium"

    def test_perfect(self):
        assert signal_quality_label(1.0) == "high"


# =====================================================================
#  uncertainty_level
# =====================================================================

class TestUncertaintyLevel:
    """Tests for uncertainty_level()."""

    def test_none(self):
        assert uncertainty_level(None) == "very_high"

    def test_zero(self):
        assert uncertainty_level(0.0) == "low"

    def test_low_upper_boundary(self):
        assert uncertainty_level(0.20) == "moderate"

    def test_moderate_upper(self):
        assert uncertainty_level(0.40) == "high"

    def test_high_upper(self):
        assert uncertainty_level(0.65) == "very_high"

    def test_one(self):
        assert uncertainty_level(1.0) == "very_high"

    def test_0_15_is_low(self):
        assert uncertainty_level(0.15) == "low"

    def test_0_35_is_moderate(self):
        assert uncertainty_level(0.35) == "moderate"


# =====================================================================
#  make_impact
# =====================================================================

class TestMakeImpact:
    """Tests for make_impact()."""

    def test_basic(self):
        imp = make_impact("quality", 0.15, "degraded data")
        assert imp["category"] == "quality"
        assert imp["penalty"] == 0.15
        assert imp["reason"] == "degraded data"
        assert imp["source"] == ""

    def test_with_source(self):
        imp = make_impact("freshness", 0.10, "stale", source="engine_x")
        assert imp["source"] == "engine_x"

    def test_penalty_clamped_to_one(self):
        imp = make_impact("quality", 2.0, "over")
        assert imp["penalty"] == 1.0

    def test_penalty_clamped_to_zero(self):
        imp = make_impact("quality", -0.5, "under")
        assert imp["penalty"] == 0.0


# =====================================================================
#  impact_from_* helpers
# =====================================================================

class TestImpactFromQuality:
    def test_good_returns_none(self):
        assert impact_from_quality("good") is None

    def test_degraded(self):
        imp = impact_from_quality("degraded")
        assert imp is not None
        assert imp["penalty"] == QUALITY_PENALTIES["degraded"]
        assert imp["category"] == "quality"

    def test_unavailable(self):
        imp = impact_from_quality("unavailable")
        assert imp["penalty"] == 0.40

    def test_unknown(self):
        imp = impact_from_quality("unknown")
        assert imp is not None
        assert imp["penalty"] == 0.10

    def test_case_insensitive(self):
        imp = impact_from_quality("POOR")
        assert imp is not None
        assert imp["penalty"] == 0.30


class TestImpactFromFreshness:
    def test_live_returns_none(self):
        assert impact_from_freshness("live") is None

    def test_recent_returns_none(self):
        assert impact_from_freshness("recent") is None

    def test_stale(self):
        imp = impact_from_freshness("stale")
        assert imp is not None
        assert imp["penalty"] == 0.10

    def test_very_stale(self):
        imp = impact_from_freshness("very_stale")
        assert imp["penalty"] == 0.25


class TestImpactFromConflict:
    def test_none_conflict(self):
        assert impact_from_conflict("none") is None

    def test_moderate(self):
        imp = impact_from_conflict("moderate")
        assert imp is not None
        assert imp["penalty"] == 0.15

    def test_high(self):
        imp = impact_from_conflict("high")
        assert imp["penalty"] == 0.30


class TestImpactFromCoverage:
    def test_full_returns_none(self):
        assert impact_from_coverage("full") is None

    def test_partial(self):
        imp = impact_from_coverage("partial")
        assert imp is not None
        assert imp["penalty"] == 0.10

    def test_none_coverage(self):
        imp = impact_from_coverage("none")
        assert imp["penalty"] == 0.40


# =====================================================================
#  aggregate_impacts / apply_impacts
# =====================================================================

class TestAggregateImpacts:
    def test_empty_list(self):
        total, reasons = aggregate_impacts([])
        assert total == 0.0
        assert reasons == []

    def test_none_input(self):
        total, reasons = aggregate_impacts(None)
        assert total == 0.0

    def test_single(self):
        imp = make_impact("quality", 0.20, "bad data")
        total, reasons = aggregate_impacts([imp])
        assert total == 0.20
        assert "bad data" in reasons

    def test_multiple(self):
        imps = [
            make_impact("quality", 0.15, "degraded"),
            make_impact("freshness", 0.10, "stale"),
        ]
        total, reasons = aggregate_impacts(imps)
        assert total == 0.25
        assert len(reasons) == 2

    def test_clamped_to_one(self):
        imps = [
            make_impact("quality", 0.60, "bad"),
            make_impact("freshness", 0.60, "stale"),
        ]
        total, _ = aggregate_impacts(imps)
        assert total == 1.0

    def test_non_dict_items_skipped(self):
        imps = [make_impact("quality", 0.10, "x"), "not_a_dict", 42]
        total, reasons = aggregate_impacts(imps)
        assert total == 0.10
        assert len(reasons) == 1


class TestApplyImpacts:
    def test_no_impacts(self):
        assert apply_impacts(0.90, []) == 0.90

    def test_none_impacts(self):
        assert apply_impacts(0.80, None) == 0.80

    def test_single_penalty(self):
        assert apply_impacts(0.90, [make_impact("q", 0.15, "x")]) == 0.75

    def test_clamp_to_zero(self):
        assert apply_impacts(0.10, [make_impact("q", 0.50, "x")]) == 0.0

    def test_none_base_score(self):
        assert apply_impacts(None, [make_impact("q", 0.10, "x")]) == 0.0


# =====================================================================
#  build_confidence_assessment
# =====================================================================

class TestBuildConfidenceAssessment:
    """Tests for the full builder."""

    def test_minimal_call(self):
        result = build_confidence_assessment()
        assert "framework_version" in result
        assert result["base_score"] == 0.0
        assert result["adjusted_score"] == 0.0
        assert result["confidence_label"] == "none"
        assert result["uncertainty_level"] == "very_high"

    def test_high_raw_confidence(self):
        result = build_confidence_assessment(raw_confidence=0.95)
        assert result["base_score"] == 0.95
        assert result["adjusted_score"] == 0.95
        assert result["confidence_label"] == "high"
        assert result["uncertainty_level"] == "low"

    def test_integer_raw_confidence(self):
        result = build_confidence_assessment(raw_confidence=85)
        assert result["base_score"] == 0.85
        assert result["confidence_label"] == "high"

    def test_base_score_overrides_raw(self):
        result = build_confidence_assessment(
            raw_confidence=0.95, base_score=0.50,
        )
        assert result["base_score"] == 0.50

    def test_quality_degraded(self):
        result = build_confidence_assessment(
            raw_confidence=0.90, quality_status="degraded",
        )
        assert result["adjusted_score"] == 0.75
        assert result["total_penalty"] == 0.15
        assert len(result["impacts"]) == 1

    def test_multiple_degradations(self):
        result = build_confidence_assessment(
            raw_confidence=0.90,
            quality_status="poor",
            freshness_status="stale",
            conflict_severity="moderate",
        )
        # poor=0.30, stale=0.10, moderate=0.15 → total=0.55
        assert result["total_penalty"] == 0.55
        assert result["adjusted_score"] == 0.35
        assert result["confidence_label"] == "low"

    def test_coverage_impact(self):
        result = build_confidence_assessment(
            base_score=0.80, coverage_level="minimal",
        )
        # minimal → 0.25 penalty
        assert result["adjusted_score"] == 0.55
        assert result["confidence_label"] == "low"

    def test_extra_impacts(self):
        extras = [make_impact("fallback", 0.10, "using proxy data")]
        result = build_confidence_assessment(
            raw_confidence=0.80, extra_impacts=extras,
        )
        assert result["adjusted_score"] == 0.70
        assert result["total_penalty"] == 0.10

    def test_zero_penalty_extras_skipped(self):
        extras = [make_impact("fallback", 0.0, "no penalty")]
        result = build_confidence_assessment(
            raw_confidence=0.80, extra_impacts=extras,
        )
        assert result["total_penalty"] == 0.0
        assert len(result["impacts"]) == 0

    def test_source_propagated(self):
        result = build_confidence_assessment(
            raw_confidence=0.80, source="test_module",
        )
        assert result["source"] == "test_module"

    def test_context_propagated(self):
        ctx = {"strategy": "iron_condor"}
        result = build_confidence_assessment(
            raw_confidence=0.80, context=ctx,
        )
        assert result["context"] == {"strategy": "iron_condor"}

    def test_context_none_gives_empty_dict(self):
        result = build_confidence_assessment(raw_confidence=0.80)
        assert result["context"] == {}

    def test_generated_at_present(self):
        result = build_confidence_assessment(raw_confidence=0.80)
        assert "generated_at" in result
        assert len(result["generated_at"]) > 10  # ISO date string

    def test_framework_version(self):
        result = build_confidence_assessment(raw_confidence=0.80)
        assert result["framework_version"] == "1.0"

    def test_confidence_reasons_present(self):
        result = build_confidence_assessment(raw_confidence=0.90)
        assert isinstance(result["confidence_reasons"], list)
        assert len(result["confidence_reasons"]) > 0

    def test_uncertainty_reasons_healthy(self):
        result = build_confidence_assessment(raw_confidence=0.90)
        assert any("healthy" in r or "low uncertainty" in r
                    for r in result["uncertainty_reasons"])

    def test_uncertainty_reasons_degraded(self):
        result = build_confidence_assessment(
            raw_confidence=0.90, quality_status="poor",
        )
        assert any("quality" in r for r in result["uncertainty_reasons"])

    def test_very_low_adjusted_adds_reason(self):
        result = build_confidence_assessment(
            raw_confidence=0.20,
        )
        assert any("below" in r or "weak" in r
                    for r in result["uncertainty_reasons"] + result["confidence_reasons"])

    def test_signal_quality_field(self):
        result = build_confidence_assessment(raw_confidence=0.85)
        assert result["signal_quality"] == "high"

    def test_uncertainty_score_inverse(self):
        result = build_confidence_assessment(raw_confidence=0.80)
        assert abs(result["uncertainty_score"] - (1.0 - result["adjusted_score"])) < 1e-6

    def test_all_degradation_categories(self):
        """Hit all four standard categories at once."""
        result = build_confidence_assessment(
            raw_confidence=1.0,
            quality_status="unavailable",
            freshness_status="very_stale",
            conflict_severity="high",
            coverage_level="none",
        )
        # unavailable=0.40 + very_stale=0.25 + high=0.30 + none=0.40 = 1.35 → clamped 1.0
        assert result["total_penalty"] == 1.0
        assert result["adjusted_score"] == 0.0
        assert result["confidence_label"] == "none"
        assert result["uncertainty_level"] == "very_high"


# =====================================================================
#  build_uncertainty_summary
# =====================================================================

class TestBuildUncertaintySummary:
    def test_from_assessment(self):
        assessment = build_confidence_assessment(raw_confidence=0.80)
        summary = build_uncertainty_summary(assessment)
        assert "uncertainty_score" in summary
        assert "uncertainty_level" in summary
        assert "uncertainty_reasons" in summary
        assert "confidence_label" in summary
        assert "adjusted_score" in summary
        assert summary["adjusted_score"] == assessment["adjusted_score"]

    def test_from_none(self):
        summary = build_uncertainty_summary(None)
        assert summary["uncertainty_level"] == "very_high"
        assert summary["confidence_label"] == "none"
        assert summary["adjusted_score"] == 0.0

    def test_from_empty_dict(self):
        summary = build_uncertainty_summary({})
        assert summary["uncertainty_level"] == "very_high"
        assert summary["confidence_label"] == "none"


# =====================================================================
#  quick_assess
# =====================================================================

class TestQuickAssess:
    def test_basic(self):
        result = quick_assess(0.85, quality="good")
        assert result["base_score"] == 0.85
        assert result["confidence_label"] == "high"

    def test_with_degradation(self):
        result = quick_assess(0.90, freshness="stale", source="test")
        assert result["adjusted_score"] == 0.80
        assert result["source"] == "test"

    def test_integer_scale(self):
        result = quick_assess(75)
        assert result["base_score"] == 0.75


# =====================================================================
#  Integration: trade_decision_orchestrator
# =====================================================================

class TestOrchestratorIntegration:
    """Verify orchestrator emits confidence_assessment / uncertainty_summary."""

    def test_empty_packet_has_confidence(self):
        from app.services.trade_decision_orchestrator import build_decision_packet
        pkt = build_decision_packet()
        qo = pkt["quality_overview"]
        assert "confidence_assessment" in qo
        assert "uncertainty_summary" in qo
        ca = qo["confidence_assessment"]
        assert ca["framework_version"] == "1.0"
        assert ca["source"] == "trade_decision_orchestrator"

    def test_full_packet_high_confidence(self):
        from app.services.trade_decision_orchestrator import build_decision_packet
        pkt = build_decision_packet(
            candidate={"symbol": "SPY", "strategy": "iron_condor"},
            market={"overall_bias": "bullish"},
            conflicts={"conflicts": []},
            portfolio={"exposure": {}},
            policy={"checks": []},
            events={"events": []},
            model_context={"summary": "ok"},
        )
        qo = pkt["quality_overview"]
        ca = qo["confidence_assessment"]
        # All subsystems present → high coverage → higher confidence
        assert ca["adjusted_score"] >= 0.60

    def test_partial_packet_lower_confidence(self):
        from app.services.trade_decision_orchestrator import build_decision_packet
        pkt = build_decision_packet(
            candidate={"symbol": "SPY"},
        )
        qo = pkt["quality_overview"]
        ca = qo["confidence_assessment"]
        # Only 1 of several subsystems → low coverage
        assert ca["adjusted_score"] < 0.60

    def test_uncertainty_summary_matches_assessment(self):
        from app.services.trade_decision_orchestrator import build_decision_packet
        pkt = build_decision_packet()
        qo = pkt["quality_overview"]
        assert qo["uncertainty_summary"]["adjusted_score"] == qo["confidence_assessment"]["adjusted_score"]


# =====================================================================
#  Integration: decision_prompt_payload
# =====================================================================

class TestPayloadIntegration:
    """Verify confidence propagation through prompt payload."""

    def test_confidence_propagated_from_packet(self):
        from app.services.trade_decision_orchestrator import build_decision_packet
        from app.services.decision_prompt_payload import build_prompt_payload
        pkt = build_decision_packet(
            candidate={"symbol": "SPY", "strategy": "iron_condor"},
            market={"overall_bias": "bullish"},
        )
        payload = build_prompt_payload(decision_packet=pkt)
        qb = payload["quality_block"]
        assert "confidence_assessment" in qb
        assert qb["confidence_assessment"]["framework_version"] == "1.0"

    def test_uncertainty_propagated_from_packet(self):
        from app.services.trade_decision_orchestrator import build_decision_packet
        from app.services.decision_prompt_payload import build_prompt_payload
        pkt = build_decision_packet()
        payload = build_prompt_payload(decision_packet=pkt)
        qb = payload["quality_block"]
        assert "uncertainty_summary" in qb

    def test_no_packet_no_assessment(self):
        from app.services.decision_prompt_payload import build_prompt_payload
        payload = build_prompt_payload(
            candidate={"symbol": "SPY"},
        )
        qb = payload["quality_block"]
        # Fallback path doesn't have confidence_assessment (no packet)
        assert "confidence_assessment" not in qb


# =====================================================================
#  Integration: decision_response_contract
# =====================================================================

class TestResponseContractIntegration:
    """Verify decision_response_contract includes confidence_assessment."""

    def test_approve_high_conviction(self):
        from app.services.decision_response_contract import build_decision_response
        resp = build_decision_response(decision="approve", conviction="high")
        ca = resp.get("confidence_assessment")
        assert ca is not None
        assert ca["confidence_label"] == "high"
        assert ca["framework_version"] == "1.0"

    def test_insufficient_data_low_confidence(self):
        from app.services.decision_response_contract import build_decision_response
        resp = build_decision_response(decision="insufficient_data")
        ca = resp.get("confidence_assessment")
        assert ca is not None
        # conviction forced to "none" → base 0.15 → low confidence
        assert ca["confidence_label"] == "none"

    def test_misaligned_reduces_confidence(self):
        from app.services.decision_response_contract import build_decision_response
        resp_aligned = build_decision_response(
            decision="approve", conviction="high", market_alignment="aligned",
        )
        resp_misaligned = build_decision_response(
            decision="approve", conviction="high", market_alignment="misaligned",
        )
        assert (resp_misaligned["confidence_assessment"]["adjusted_score"]
                < resp_aligned["confidence_assessment"]["adjusted_score"])

    def test_policy_blocked_reduces_confidence(self):
        from app.services.decision_response_contract import build_decision_response
        resp = build_decision_response(
            decision="approve", conviction="high",
            policy_alignment="blocked",
        )
        ca = resp["confidence_assessment"]
        assert ca["adjusted_score"] < 0.95  # penalty applied

    def test_many_warnings_reduce_confidence(self):
        from app.services.decision_response_contract import build_decision_response
        warnings = [f"warning_{i}" for i in range(6)]
        resp = build_decision_response(
            decision="approve", conviction="high", warning_flags=warnings,
        )
        ca = resp["confidence_assessment"]
        # 6 warnings → (6-2)=4 → min(4,4)=4 → 0.05*4=0.20 penalty
        assert ca["total_penalty"] >= 0.15

    def test_placeholder_also_has_assessment(self):
        from app.services.decision_response_contract import build_placeholder_response
        resp = build_placeholder_response()
        assert "confidence_assessment" in resp
        assert resp["confidence_assessment"]["framework_version"] == "1.0"

    def test_conviction_none_base_score(self):
        from app.services.decision_response_contract import build_decision_response
        resp = build_decision_response(decision="reject", conviction="none")
        ca = resp["confidence_assessment"]
        assert ca["base_score"] == 0.15

    def test_conviction_moderate_base_score(self):
        from app.services.decision_response_contract import build_decision_response
        resp = build_decision_response(decision="approve", conviction="moderate")
        ca = resp["confidence_assessment"]
        assert ca["base_score"] == 0.70


# =====================================================================
#  Penalty tables sanity
# =====================================================================

class TestPenaltyTables:
    """Verify penalty tables are well-formed and monotonic."""

    def test_quality_monotonic(self):
        # good ≤ acceptable ≤ degraded ≤ poor ≤ unavailable
        assert QUALITY_PENALTIES["good"] <= QUALITY_PENALTIES["acceptable"]
        assert QUALITY_PENALTIES["acceptable"] <= QUALITY_PENALTIES["degraded"]
        assert QUALITY_PENALTIES["degraded"] <= QUALITY_PENALTIES["poor"]
        assert QUALITY_PENALTIES["poor"] <= QUALITY_PENALTIES["unavailable"]

    def test_freshness_monotonic(self):
        assert FRESHNESS_PENALTIES["live"] <= FRESHNESS_PENALTIES["recent"]
        assert FRESHNESS_PENALTIES["recent"] <= FRESHNESS_PENALTIES["stale"]
        assert FRESHNESS_PENALTIES["stale"] <= FRESHNESS_PENALTIES["very_stale"]

    def test_conflict_monotonic(self):
        assert CONFLICT_PENALTIES["none"] <= CONFLICT_PENALTIES["low"]
        assert CONFLICT_PENALTIES["low"] <= CONFLICT_PENALTIES["moderate"]
        assert CONFLICT_PENALTIES["moderate"] <= CONFLICT_PENALTIES["high"]

    def test_coverage_monotonic(self):
        assert COVERAGE_PENALTIES["full"] <= COVERAGE_PENALTIES["high"]
        assert COVERAGE_PENALTIES["high"] <= COVERAGE_PENALTIES["partial"]
        assert COVERAGE_PENALTIES["partial"] <= COVERAGE_PENALTIES["minimal"]
        assert COVERAGE_PENALTIES["minimal"] <= COVERAGE_PENALTIES["none"]

    def test_all_penalties_non_negative(self):
        for table in (QUALITY_PENALTIES, FRESHNESS_PENALTIES,
                      CONFLICT_PENALTIES, COVERAGE_PENALTIES):
            for k, v in table.items():
                assert v >= 0, f"Negative penalty for {k}: {v}"

    def test_valid_categories_non_empty(self):
        assert len(VALID_IMPACT_CATEGORIES) >= 6
