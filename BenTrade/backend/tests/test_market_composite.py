"""Comprehensive tests for the Market Composite Summary v1.1.

Test Classes
------------
1.  TestOutputContract          – top-level shape, vocabulary, types
2.  TestEmptyAndInsufficientData – no market data edge cases
3.  TestMarketStateDimension    – risk_on / neutral / risk_off logic
4.  TestSupportStateDimension   – supportive / mixed / fragile logic
5.  TestStabilityStateDimension – orderly / noisy / unstable logic
6.  TestBaseConfidence          – agreement ratio × coverage factor
7.  TestConflictAdjustment      – conflict-driven downgrades + penalties
8.  TestQualityAdjustment       – quality/freshness/degraded penalties + downgrades
9.  TestHorizonAdjustment       – wide horizon span penalty
10. TestCombinedAdjustments     – multiple adjustments stacking + conflict-aware status
11. TestHumanSummary            – summary text correctness
12. TestToneHelpers             – _classify_label, _classify_score, _engine_tone
13. TestIntegrationProofs       – full assembled context flows
14. TestRepresentativeOutputs   – v1.1 representative compact-output scenarios
"""

import pytest
from app.services.market_composite import (
    build_market_composite,
    MARKET_STATES,
    SUPPORT_STATES,
    STABILITY_STATES,
    _classify_label,
    _classify_score,
    _engine_tone,
    _collect_engine_tones,
    _count_tones,
    _derive_market_state,
    _derive_support_state,
    _derive_stability_state,
    _compute_base_confidence,
    _apply_conflict_adjustment,
    _apply_quality_adjustment,
    _apply_horizon_adjustment,
    _determine_status,
    _build_human_summary,
    _empty_output,
)

# ═════════════════════════════════════════════════════════════════════
# Fixtures — reusable builders
# ═════════════════════════════════════════════════════════════════════

TOP_LEVEL_KEYS = {
    "composite_version", "computed_at", "status",
    "market_state", "support_state", "stability_state",
    "confidence", "evidence", "adjustments", "summary", "metadata",
}

EVIDENCE_KEYS = {"market_state", "support_state", "stability_state"}

ADJUSTMENTS_KEYS = {
    "conflict_adjustment", "quality_adjustment", "horizon_adjustment",
}

METADATA_KEYS = {
    "composite_version", "engines_used", "conflict_count",
    "conflict_severity", "overall_quality", "overall_freshness",
    "horizon_span",
}


def _bull_engine(key: str, score: float = 75.0) -> dict:
    """Market module payload with bullish characteristics."""
    return {
        "normalized": {
            "engine_key": key,
            "score": score,
            "label": "Bullish",
            "short_label": "Bullish",
            "confidence": 85.0,
            "signal_quality": "high",
            "time_horizon": "short_term",
            "bull_factors": ["strong breadth"],
            "bear_factors": [],
        },
    }


def _bear_engine(key: str, score: float = 25.0) -> dict:
    """Market module payload with bearish characteristics."""
    return {
        "normalized": {
            "engine_key": key,
            "score": score,
            "label": "Bearish",
            "short_label": "Cautionary",
            "confidence": 80.0,
            "signal_quality": "high",
            "time_horizon": "short_term",
            "bull_factors": [],
            "bear_factors": ["weak momentum"],
        },
    }


def _neutral_engine(key: str, score: float = 50.0) -> dict:
    """Market module payload with neutral characteristics."""
    return {
        "normalized": {
            "engine_key": key,
            "score": score,
            "label": "Neutral",
            "short_label": "Mixed",
            "confidence": 70.0,
            "signal_quality": "medium",
            "time_horizon": "short_term",
            "bull_factors": ["some positive"],
            "bear_factors": ["some negative"],
        },
    }


def _unknown_engine(key: str) -> dict:
    """Market module payload with unknown/unclassifiable tone."""
    return {
        "normalized": {
            "engine_key": key,
            "score": None,
            "label": "Unknown",
            "short_label": "Unknown",
            "confidence": 30.0,
            "signal_quality": "low",
            "time_horizon": "short_term",
        },
    }


def _quality_summary(overall: str = "good", avg_conf: float = 80.0, degraded: int = 0) -> dict:
    return {
        "overall_quality": overall,
        "average_confidence": avg_conf,
        "module_count": 6,
        "degraded_count": degraded,
        "modules": {},
    }


def _freshness_summary(overall: str = "recent") -> dict:
    return {"overall_freshness": overall, "module_count": 6, "modules": {}}


def _horizon_summary(
    shortest: str = "short_term",
    longest: str = "short_term",
) -> dict:
    return {
        "market_horizons": {},
        "candidate_horizons": [],
        "model_horizons": {},
        "distinct_horizons": [shortest] if shortest == longest else [shortest, longest],
        "shortest": shortest,
        "longest": longest,
    }


def _conflict_report(
    count: int = 0,
    severity: str = "none",
    status: str = "clean",
) -> dict:
    return {
        "status": status,
        "detected_at": "2025-01-01T00:00:00+00:00",
        "conflict_count": count,
        "conflict_severity": severity,
        "conflict_summary": "Test conflict report",
        "conflict_flags": [],
        "market_conflicts": [],
        "candidate_conflicts": [],
        "model_conflicts": [],
        "time_horizon_conflicts": [],
        "quality_conflicts": [],
        "metadata": {
            "detector_version": "1.0",
            "engines_inspected": 6,
            "candidates_inspected": 0,
            "models_inspected": 0,
            "degraded_inputs": 0,
        },
    }


def _assembled(
    market_ctx: dict | None = None,
    quality: dict | None = None,
    freshness: dict | None = None,
    horizon: dict | None = None,
) -> dict:
    """Build a minimal assembled context for testing."""
    return {
        "context_version": "1.0",
        "assembled_at": "2025-01-01T00:00:00+00:00",
        "assembly_status": "complete",
        "assembly_warnings": [],
        "included_modules": list((market_ctx or {}).keys()),
        "missing_modules": [],
        "degraded_modules": [],
        "market_context": market_ctx or {},
        "candidate_context": {"candidates": [], "count": 0, "scanners": [], "families": []},
        "model_context": {"analyses": {}, "count": 0},
        "quality_summary": quality or _quality_summary(),
        "freshness_summary": freshness or _freshness_summary(),
        "horizon_summary": horizon or _horizon_summary(),
        "metadata": {},
    }


def _all_bullish_assembled(n: int = 6) -> dict:
    """Fully aligned bullish assembled context."""
    keys = [
        "breadth_participation", "volatility_options", "cross_asset_macro",
        "flows_positioning", "liquidity_financial_conditions", "news_sentiment",
    ][:n]
    market = {k: _bull_engine(k) for k in keys}
    return _assembled(market_ctx=market)


def _all_bearish_assembled(n: int = 6) -> dict:
    keys = [
        "breadth_participation", "volatility_options", "cross_asset_macro",
        "flows_positioning", "liquidity_financial_conditions", "news_sentiment",
    ][:n]
    market = {k: _bear_engine(k) for k in keys}
    return _assembled(market_ctx=market)


def _mixed_assembled() -> dict:
    """3 bullish, 2 bearish, 1 neutral assembled context."""
    return _assembled(market_ctx={
        "breadth_participation": _bull_engine("breadth_participation"),
        "volatility_options": _bull_engine("volatility_options"),
        "cross_asset_macro": _bull_engine("cross_asset_macro"),
        "flows_positioning": _bear_engine("flows_positioning"),
        "liquidity_financial_conditions": _bear_engine("liquidity_financial_conditions"),
        "news_sentiment": _neutral_engine("news_sentiment"),
    })


# ═════════════════════════════════════════════════════════════════════
# 1. TestOutputContract
# ═════════════════════════════════════════════════════════════════════

class TestOutputContract:
    """Top-level output shape, vocabulary, and type correctness."""

    def test_top_level_keys_complete(self):
        result = build_market_composite(_all_bullish_assembled())
        assert set(result.keys()) == TOP_LEVEL_KEYS

    def test_evidence_keys(self):
        result = build_market_composite(_all_bullish_assembled())
        assert set(result["evidence"].keys()) == EVIDENCE_KEYS

    def test_adjustments_keys(self):
        result = build_market_composite(_all_bullish_assembled())
        assert set(result["adjustments"].keys()) == ADJUSTMENTS_KEYS

    def test_metadata_keys(self):
        result = build_market_composite(_all_bullish_assembled())
        assert set(result["metadata"].keys()) == METADATA_KEYS

    def test_market_state_in_vocabulary(self):
        result = build_market_composite(_all_bullish_assembled())
        assert result["market_state"] in MARKET_STATES

    def test_support_state_in_vocabulary(self):
        result = build_market_composite(_all_bullish_assembled())
        assert result["support_state"] in SUPPORT_STATES

    def test_stability_state_in_vocabulary(self):
        result = build_market_composite(_all_bullish_assembled())
        assert result["stability_state"] in STABILITY_STATES

    def test_confidence_range(self):
        result = build_market_composite(_all_bullish_assembled())
        assert 0.0 <= result["confidence"] <= 1.0

    def test_composite_version(self):
        result = build_market_composite(_all_bullish_assembled())
        assert result["composite_version"] == "1.1"

    def test_computed_at_is_iso(self):
        result = build_market_composite(_all_bullish_assembled())
        assert "T" in result["computed_at"]
        assert "+" in result["computed_at"] or "Z" in result["computed_at"]

    def test_summary_is_string(self):
        result = build_market_composite(_all_bullish_assembled())
        assert isinstance(result["summary"], str)
        assert len(result["summary"]) > 10

    def test_status_vocabulary(self):
        result = build_market_composite(_all_bullish_assembled())
        assert result["status"] in {"ok", "degraded", "insufficient_data"}

    def test_empty_context_still_valid_shape(self):
        result = build_market_composite(_assembled())
        assert set(result.keys()) == TOP_LEVEL_KEYS
        assert set(result["evidence"].keys()) == EVIDENCE_KEYS

    def test_bearish_context_still_valid_shape(self):
        result = build_market_composite(_all_bearish_assembled())
        assert set(result.keys()) == TOP_LEVEL_KEYS


# ═════════════════════════════════════════════════════════════════════
# 2. TestEmptyAndInsufficientData
# ═════════════════════════════════════════════════════════════════════

class TestEmptyAndInsufficientData:
    """Edge cases: no engines, empty dicts, None market_context."""

    def test_empty_market_context(self):
        result = build_market_composite(_assembled(market_ctx={}))
        assert result["status"] == "insufficient_data"
        assert result["market_state"] == "neutral"
        assert result["support_state"] == "fragile"
        assert result["stability_state"] == "unstable"
        assert result["confidence"] == 0.0

    def test_none_market_context(self):
        asm = _assembled()
        asm["market_context"] = None
        result = build_market_composite(asm)
        assert result["status"] == "insufficient_data"

    def test_missing_market_context_key(self):
        asm = {"context_version": "1.0"}
        result = build_market_composite(asm)
        assert result["status"] == "insufficient_data"

    def test_completely_empty_assembled(self):
        result = build_market_composite({})
        assert result["status"] == "insufficient_data"
        assert result["confidence"] == 0.0
        assert result["metadata"]["engines_used"] == 0

    def test_insufficient_data_summary(self):
        result = build_market_composite({})
        assert "Insufficient" in result["summary"]

    def test_insufficient_data_adjustments_are_none(self):
        result = build_market_composite({})
        assert result["adjustments"]["conflict_adjustment"] is None
        assert result["adjustments"]["quality_adjustment"] is None
        assert result["adjustments"]["horizon_adjustment"] is None


# ═════════════════════════════════════════════════════════════════════
# 3. TestMarketStateDimension
# ═════════════════════════════════════════════════════════════════════

class TestMarketStateDimension:
    """market_state derived from engine tone majority."""

    def test_all_bullish_is_risk_on(self):
        result = build_market_composite(_all_bullish_assembled())
        assert result["market_state"] == "risk_on"

    def test_all_bearish_is_risk_off(self):
        result = build_market_composite(_all_bearish_assembled())
        assert result["market_state"] == "risk_off"

    def test_all_neutral_is_neutral(self):
        asm = _assembled(market_ctx={
            "a": _neutral_engine("a"),
            "b": _neutral_engine("b"),
            "c": _neutral_engine("c"),
        })
        result = build_market_composite(asm)
        assert result["market_state"] == "neutral"

    def test_majority_bullish_3_of_5(self):
        asm = _assembled(market_ctx={
            "a": _bull_engine("a"),
            "b": _bull_engine("b"),
            "c": _bull_engine("c"),
            "d": _bear_engine("d"),
            "e": _neutral_engine("e"),
        })
        result = build_market_composite(asm)
        assert result["market_state"] == "risk_on"

    def test_majority_bearish_4_of_6(self):
        asm = _assembled(market_ctx={
            "a": _bear_engine("a"),
            "b": _bear_engine("b"),
            "c": _bear_engine("c"),
            "d": _bear_engine("d"),
            "e": _bull_engine("e"),
            "f": _neutral_engine("f"),
        })
        result = build_market_composite(asm)
        assert result["market_state"] == "risk_off"

    def test_tied_bull_bear_is_neutral(self):
        asm = _assembled(market_ctx={
            "a": _bull_engine("a"),
            "b": _bull_engine("b"),
            "c": _bear_engine("c"),
            "d": _bear_engine("d"),
        })
        result = build_market_composite(asm)
        assert result["market_state"] == "neutral"

    def test_tied_bull_neutral_is_neutral(self):
        asm = _assembled(market_ctx={
            "a": _bull_engine("a"),
            "b": _bull_engine("b"),
            "c": _neutral_engine("c"),
            "d": _neutral_engine("d"),
        })
        result = build_market_composite(asm)
        assert result["market_state"] == "neutral"

    def test_all_unknown_is_neutral(self):
        asm = _assembled(market_ctx={
            "a": _unknown_engine("a"),
            "b": _unknown_engine("b"),
        })
        result = build_market_composite(asm)
        assert result["market_state"] == "neutral"

    def test_evidence_includes_tone_counts(self):
        result = build_market_composite(_all_bullish_assembled(3))
        ev = result["evidence"]["market_state"]
        assert "tone_counts" in ev
        assert ev["tone_counts"]["bullish"] == 3

    def test_evidence_includes_engine_tones(self):
        result = build_market_composite(_all_bullish_assembled(3))
        ev = result["evidence"]["market_state"]
        assert "engine_tones" in ev
        assert len(ev["engine_tones"]) == 3

    def test_single_bullish_engine_is_risk_on(self):
        asm = _assembled(market_ctx={"a": _bull_engine("a")})
        result = build_market_composite(asm)
        assert result["market_state"] == "risk_on"

    def test_single_bearish_engine_is_risk_off(self):
        asm = _assembled(market_ctx={"a": _bear_engine("a")})
        result = build_market_composite(asm)
        assert result["market_state"] == "risk_off"

    def test_score_only_classification(self):
        """Engine with unknown label but bullish score → risk_on."""
        asm = _assembled(market_ctx={
            "a": {"normalized": {"engine_key": "a", "score": 80.0, "label": "Custom Thing", "short_label": "Custom"}},
            "b": {"normalized": {"engine_key": "b", "score": 70.0, "label": "Custom Thing", "short_label": "Custom"}},
        })
        result = build_market_composite(asm)
        assert result["market_state"] == "risk_on"


# ═════════════════════════════════════════════════════════════════════
# 4. TestSupportStateDimension
# ═════════════════════════════════════════════════════════════════════

class TestSupportStateDimension:
    """support_state from alignment + data quality."""

    def test_strong_alignment_good_quality_is_supportive(self):
        asm = _all_bullish_assembled()
        result = build_market_composite(asm)
        assert result["support_state"] == "supportive"

    def test_all_bearish_good_quality_is_supportive(self):
        asm = _all_bearish_assembled()
        result = build_market_composite(asm)
        assert result["support_state"] == "supportive"

    def test_moderate_alignment_is_mixed(self):
        """3 bull, 1 bear, 1 neutral → alignment ~0.6 → mixed."""
        asm = _assembled(market_ctx={
            "a": _bull_engine("a"),
            "b": _bull_engine("b"),
            "c": _bull_engine("c"),
            "d": _bear_engine("d"),
            "e": _neutral_engine("e"),
        })
        result = build_market_composite(asm)
        assert result["support_state"] == "mixed"

    def test_split_engines_is_fragile(self):
        """2 bull, 2 bear → alignment = 0.5 → fragile."""
        asm = _assembled(market_ctx={
            "a": _bull_engine("a"),
            "b": _bull_engine("b"),
            "c": _bear_engine("c"),
            "d": _bear_engine("d"),
        })
        result = build_market_composite(asm)
        assert result["support_state"] == "fragile"

    def test_poor_quality_forces_fragile(self):
        asm = _all_bullish_assembled()
        asm["quality_summary"] = _quality_summary("poor")
        result = build_market_composite(asm)
        assert result["support_state"] == "fragile"

    def test_unavailable_quality_forces_fragile(self):
        asm = _all_bullish_assembled()
        asm["quality_summary"] = _quality_summary("unavailable")
        result = build_market_composite(asm)
        assert result["support_state"] == "fragile"

    def test_degraded_quality_with_alignment_is_mixed(self):
        """Strong alignment but degraded quality → mixed (not supportive)."""
        asm = _all_bullish_assembled()
        asm["quality_summary"] = _quality_summary("degraded")
        result = build_market_composite(asm)
        assert result["support_state"] == "mixed"

    def test_acceptable_quality_with_alignment_is_supportive(self):
        asm = _all_bullish_assembled()
        asm["quality_summary"] = _quality_summary("acceptable")
        result = build_market_composite(asm)
        assert result["support_state"] == "supportive"

    def test_evidence_includes_alignment_ratio(self):
        result = build_market_composite(_all_bullish_assembled())
        ev = result["evidence"]["support_state"]
        assert "alignment_ratio" in ev
        assert ev["alignment_ratio"] == 1.0

    def test_evidence_includes_overall_quality(self):
        result = build_market_composite(_all_bullish_assembled())
        ev = result["evidence"]["support_state"]
        assert "overall_quality" in ev

    def test_all_unknown_engines_gives_fragile(self):
        asm = _assembled(market_ctx={
            "a": _unknown_engine("a"),
            "b": _unknown_engine("b"),
        })
        result = build_market_composite(asm)
        assert result["support_state"] == "fragile"


# ═════════════════════════════════════════════════════════════════════
# 5. TestStabilityStateDimension
# ═════════════════════════════════════════════════════════════════════

class TestStabilityStateDimension:
    """stability_state from conflict severity + tonal spread."""

    def test_no_conflicts_aligned_is_orderly(self):
        asm = _all_bullish_assembled()
        result = build_market_composite(asm)
        assert result["stability_state"] == "orderly"

    def test_no_conflict_report_aligned_is_orderly(self):
        asm = _all_bullish_assembled()
        result = build_market_composite(asm, conflict_report=None)
        assert result["stability_state"] == "orderly"

    def test_high_conflicts_is_unstable(self):
        asm = _all_bullish_assembled()
        cr = _conflict_report(count=3, severity="high", status="conflicts_detected")
        result = build_market_composite(asm, conflict_report=cr)
        assert result["stability_state"] == "unstable"

    def test_moderate_conflicts_is_noisy(self):
        asm = _all_bullish_assembled()
        cr = _conflict_report(count=2, severity="moderate", status="conflicts_detected")
        result = build_market_composite(asm, conflict_report=cr)
        assert result["stability_state"] == "noisy"

    def test_low_conflicts_aligned_is_orderly(self):
        asm = _all_bullish_assembled()
        cr = _conflict_report(count=1, severity="low", status="conflicts_detected")
        result = build_market_composite(asm, conflict_report=cr)
        assert result["stability_state"] == "orderly"

    def test_bull_bear_split_is_noisy(self):
        """Bull + bear engines present → tonal split → noisy even without conflict report."""
        asm = _mixed_assembled()
        result = build_market_composite(asm)
        assert result["stability_state"] == "noisy"

    def test_evidence_includes_conflict_severity(self):
        cr = _conflict_report(count=2, severity="moderate", status="conflicts_detected")
        result = build_market_composite(_all_bullish_assembled(), conflict_report=cr)
        ev = result["evidence"]["stability_state"]
        assert ev["conflict_severity"] == "moderate"

    def test_evidence_includes_has_split(self):
        result = build_market_composite(_mixed_assembled())
        ev = result["evidence"]["stability_state"]
        assert ev["has_bull_bear_split"] is True


# ═════════════════════════════════════════════════════════════════════
# 6. TestBaseConfidence
# ═════════════════════════════════════════════════════════════════════

class TestBaseConfidence:
    """Confidence from agreement_ratio × coverage_factor."""

    def test_full_alignment_full_coverage(self):
        """6 bullish / 6 engines → 1.0 × 1.0 = 1.0."""
        result = build_market_composite(_all_bullish_assembled(6))
        assert result["confidence"] == 1.0

    def test_full_alignment_partial_coverage(self):
        """3 bullish / 3 engines → 1.0 × 0.5 = 0.5."""
        result = build_market_composite(_all_bullish_assembled(3))
        assert result["confidence"] == 0.5

    def test_partial_alignment_full_coverage(self):
        """4 bull, 1 bear, 1 neutral of 6 → 4/6 × 1.0 ≈ 0.67."""
        asm = _assembled(market_ctx={
            "a": _bull_engine("a"),
            "b": _bull_engine("b"),
            "c": _bull_engine("c"),
            "d": _bull_engine("d"),
            "e": _bear_engine("e"),
            "f": _neutral_engine("f"),
        })
        result = build_market_composite(asm)
        assert result["confidence"] == pytest.approx(0.67, abs=0.01)

    def test_split_lowers_confidence(self):
        """2 bull, 2 bear of 4 → 0.5 × 4/6 ≈ 0.33."""
        asm = _assembled(market_ctx={
            "a": _bull_engine("a"),
            "b": _bull_engine("b"),
            "c": _bear_engine("c"),
            "d": _bear_engine("d"),
        })
        result = build_market_composite(asm)
        assert result["confidence"] == pytest.approx(0.33, abs=0.01)

    def test_single_engine(self):
        """1 engine → 1.0 × 1/6 ≈ 0.17."""
        asm = _assembled(market_ctx={"a": _bull_engine("a")})
        result = build_market_composite(asm)
        assert result["confidence"] == pytest.approx(0.17, abs=0.01)

    def test_all_unknown_gives_low_confidence(self):
        """All unknown → total=0 → base = 0.3."""
        asm = _assembled(market_ctx={
            "a": _unknown_engine("a"),
            "b": _unknown_engine("b"),
        })
        result = build_market_composite(asm)
        # Base = 0.3 minus quality/freshness penalties
        assert result["confidence"] <= 0.3

    def test_confidence_never_negative(self):
        """Heavy penalties shouldn't push confidence below 0."""
        asm = _all_bullish_assembled(1)
        asm["quality_summary"] = _quality_summary("unavailable")
        asm["freshness_summary"] = _freshness_summary("very_stale")
        cr = _conflict_report(count=5, severity="high", status="conflicts_detected")
        result = build_market_composite(asm, conflict_report=cr)
        assert result["confidence"] >= 0.0

    def test_confidence_never_above_one(self):
        result = build_market_composite(_all_bullish_assembled(6))
        assert result["confidence"] <= 1.0


# ═════════════════════════════════════════════════════════════════════
# 7. TestConflictAdjustment
# ═════════════════════════════════════════════════════════════════════

class TestConflictAdjustment:
    """Conflict-driven downgrades and confidence penalties."""

    def test_no_conflict_report_gives_none(self):
        result = build_market_composite(_all_bullish_assembled())
        assert result["adjustments"]["conflict_adjustment"] is None

    def test_clean_conflict_report_gives_none(self):
        cr = _conflict_report(count=0, severity="none")
        result = build_market_composite(_all_bullish_assembled(), conflict_report=cr)
        assert result["adjustments"]["conflict_adjustment"] is None

    def test_low_conflict_applies_small_penalty(self):
        cr = _conflict_report(count=1, severity="low", status="conflicts_detected")
        result = build_market_composite(_all_bullish_assembled(), conflict_report=cr)
        adj = result["adjustments"]["conflict_adjustment"]
        assert adj is not None
        assert adj["confidence_penalty"] == 0.05

    def test_moderate_conflict_applies_larger_penalty(self):
        cr = _conflict_report(count=2, severity="moderate", status="conflicts_detected")
        result = build_market_composite(_all_bullish_assembled(), conflict_report=cr)
        adj = result["adjustments"]["conflict_adjustment"]
        assert adj["confidence_penalty"] == 0.15

    def test_high_conflict_applies_largest_penalty(self):
        cr = _conflict_report(count=3, severity="high", status="conflicts_detected")
        result = build_market_composite(_all_bullish_assembled(), conflict_report=cr)
        adj = result["adjustments"]["conflict_adjustment"]
        assert adj["confidence_penalty"] == 0.30

    def test_high_conflict_downgrades_stability_to_unstable(self):
        cr = _conflict_report(count=3, severity="high", status="conflicts_detected")
        result = build_market_composite(_all_bullish_assembled(), conflict_report=cr)
        assert result["stability_state"] == "unstable"

    def test_moderate_conflict_downgrades_orderly_to_noisy(self):
        cr = _conflict_report(count=2, severity="moderate", status="conflicts_detected")
        result = build_market_composite(_all_bullish_assembled(), conflict_report=cr)
        assert result["stability_state"] == "noisy"

    def test_high_conflict_downgrades_supportive_to_mixed(self):
        cr = _conflict_report(count=3, severity="high", status="conflicts_detected")
        result = build_market_composite(_all_bullish_assembled(), conflict_report=cr)
        # support was supportive, high conflict downgrades to mixed
        assert result["support_state"] == "mixed"

    def test_adjustment_has_applied_flag(self):
        cr = _conflict_report(count=1, severity="low", status="conflicts_detected")
        result = build_market_composite(_all_bullish_assembled(), conflict_report=cr)
        adj = result["adjustments"]["conflict_adjustment"]
        assert adj["applied"] is True

    def test_adjustment_records_severity_and_count(self):
        cr = _conflict_report(count=3, severity="high", status="conflicts_detected")
        result = build_market_composite(_all_bullish_assembled(), conflict_report=cr)
        adj = result["adjustments"]["conflict_adjustment"]
        assert adj["conflict_severity"] == "high"
        assert adj["conflict_count"] == 3


# ═════════════════════════════════════════════════════════════════════
# 8. TestQualityAdjustment
# ═════════════════════════════════════════════════════════════════════

class TestQualityAdjustment:
    """Quality/freshness penalties and support downgrades."""

    def test_good_quality_recent_freshness_gives_none(self):
        result = build_market_composite(_all_bullish_assembled())
        assert result["adjustments"]["quality_adjustment"] is None

    def test_poor_quality_applies_penalty(self):
        asm = _all_bullish_assembled()
        asm["quality_summary"] = _quality_summary("poor")
        result = build_market_composite(asm)
        adj = result["adjustments"]["quality_adjustment"]
        assert adj is not None
        assert adj["quality_penalty"] == 0.30

    def test_unavailable_quality_applies_largest_penalty(self):
        asm = _all_bullish_assembled()
        asm["quality_summary"] = _quality_summary("unavailable")
        result = build_market_composite(asm)
        adj = result["adjustments"]["quality_adjustment"]
        assert adj["quality_penalty"] == 0.40

    def test_degraded_quality_applies_moderate_penalty(self):
        asm = _all_bullish_assembled()
        asm["quality_summary"] = _quality_summary("degraded")
        result = build_market_composite(asm)
        adj = result["adjustments"]["quality_adjustment"]
        assert adj["quality_penalty"] == 0.15

    def test_stale_freshness_applies_penalty(self):
        asm = _all_bullish_assembled()
        asm["freshness_summary"] = _freshness_summary("stale")
        result = build_market_composite(asm)
        adj = result["adjustments"]["quality_adjustment"]
        assert adj is not None
        assert adj["freshness_penalty"] == 0.10

    def test_very_stale_freshness_penalty(self):
        asm = _all_bullish_assembled()
        asm["freshness_summary"] = _freshness_summary("very_stale")
        result = build_market_composite(asm)
        adj = result["adjustments"]["quality_adjustment"]
        assert adj["freshness_penalty"] == 0.25

    def test_combined_quality_and_freshness_penalties(self):
        asm = _all_bullish_assembled()
        asm["quality_summary"] = _quality_summary("degraded")
        asm["freshness_summary"] = _freshness_summary("stale")
        result = build_market_composite(asm)
        adj = result["adjustments"]["quality_adjustment"]
        # 0.15 quality + 0.10 freshness + 0.0 degraded_count = 0.25
        assert adj["confidence_penalty"] == pytest.approx(0.25, abs=0.01)

    def test_poor_quality_downgrades_support_to_fragile(self):
        asm = _all_bullish_assembled()
        asm["quality_summary"] = _quality_summary("poor")
        result = build_market_composite(asm)
        assert result["support_state"] == "fragile"

    def test_unknown_quality_applies_small_penalty(self):
        asm = _all_bullish_assembled()
        asm["quality_summary"] = _quality_summary("unknown")
        result = build_market_composite(asm)
        adj = result["adjustments"]["quality_adjustment"]
        assert adj is not None
        assert adj["quality_penalty"] == 0.10

    def test_adjustment_records_quality_and_freshness(self):
        asm = _all_bullish_assembled()
        asm["quality_summary"] = _quality_summary("degraded")
        result = build_market_composite(asm)
        adj = result["adjustments"]["quality_adjustment"]
        assert adj["overall_quality"] == "degraded"

    # ── degraded_count sensitivity (v1.1) ────────────────────────

    def test_degraded_count_zero_no_extra_penalty(self):
        asm = _all_bullish_assembled()
        asm["quality_summary"] = _quality_summary("degraded", degraded=0)
        result = build_market_composite(asm)
        adj = result["adjustments"]["quality_adjustment"]
        assert adj["degraded_penalty"] == 0.0
        assert adj["confidence_penalty"] == 0.15  # quality only

    def test_degraded_count_one_no_extra_penalty(self):
        asm = _all_bullish_assembled()
        asm["quality_summary"] = _quality_summary("degraded", degraded=1)
        result = build_market_composite(asm)
        adj = result["adjustments"]["quality_adjustment"]
        assert adj["degraded_penalty"] == 0.0

    def test_degraded_count_two_adds_small_penalty(self):
        asm = _all_bullish_assembled()
        asm["quality_summary"] = _quality_summary("acceptable", degraded=2)
        result = build_market_composite(asm)
        adj = result["adjustments"]["quality_adjustment"]
        assert adj is not None
        assert adj["degraded_penalty"] == 0.05
        assert adj["confidence_penalty"] == 0.05

    def test_degraded_count_three_adds_moderate_penalty(self):
        asm = _all_bullish_assembled()
        asm["quality_summary"] = _quality_summary("acceptable", degraded=3)
        result = build_market_composite(asm)
        adj = result["adjustments"]["quality_adjustment"]
        assert adj["degraded_penalty"] == 0.10

    def test_degraded_count_four_or_more_adds_large_penalty(self):
        asm = _all_bullish_assembled()
        asm["quality_summary"] = _quality_summary("acceptable", degraded=5)
        result = build_market_composite(asm)
        adj = result["adjustments"]["quality_adjustment"]
        assert adj["degraded_penalty"] == 0.15

    def test_degraded_count_stacks_with_quality_penalty(self):
        """degraded quality + 3 degraded engines = 0.15 + 0.10 = 0.25."""
        asm = _all_bullish_assembled()
        asm["quality_summary"] = _quality_summary("degraded", degraded=3)
        result = build_market_composite(asm)
        adj = result["adjustments"]["quality_adjustment"]
        assert adj["confidence_penalty"] == pytest.approx(0.25, abs=0.01)

    def test_degraded_count_recorded_in_adjustment(self):
        asm = _all_bullish_assembled()
        asm["quality_summary"] = _quality_summary("acceptable", degraded=2)
        result = build_market_composite(asm)
        adj = result["adjustments"]["quality_adjustment"]
        assert adj["degraded_count"] == 2


# ═════════════════════════════════════════════════════════════════════
# 9. TestHorizonAdjustment
# ═════════════════════════════════════════════════════════════════════

class TestHorizonAdjustment:
    """Wide horizon span penalty."""

    def test_narrow_span_gives_none(self):
        asm = _all_bullish_assembled()
        asm["horizon_summary"] = _horizon_summary("short_term", "short_term")
        result = build_market_composite(asm)
        assert result["adjustments"]["horizon_adjustment"] is None

    def test_moderate_span_gives_none(self):
        """intraday→short_term is span=1, still narrow."""
        asm = _all_bullish_assembled()
        asm["horizon_summary"] = _horizon_summary("intraday", "swing")
        result = build_market_composite(asm)
        assert result["adjustments"]["horizon_adjustment"] is None

    def test_notable_span_applies_small_penalty(self):
        """intraday→event_driven is span=3, notable."""
        asm = _all_bullish_assembled()
        asm["horizon_summary"] = _horizon_summary("intraday", "event_driven")
        result = build_market_composite(asm)
        adj = result["adjustments"]["horizon_adjustment"]
        assert adj is not None
        assert adj["confidence_penalty"] == 0.05
        assert adj["span"] == 3

    def test_wide_span_applies_larger_penalty(self):
        """intraday→long_term is span=6, wide."""
        asm = _all_bullish_assembled()
        asm["horizon_summary"] = _horizon_summary("intraday", "long_term")
        result = build_market_composite(asm)
        adj = result["adjustments"]["horizon_adjustment"]
        assert adj is not None
        assert adj["confidence_penalty"] == 0.10
        assert adj["span"] == 6

    def test_missing_horizon_gives_none(self):
        asm = _all_bullish_assembled()
        asm["horizon_summary"] = {}
        result = build_market_composite(asm)
        assert result["adjustments"]["horizon_adjustment"] is None

    def test_adjustment_records_shortest_longest(self):
        asm = _all_bullish_assembled()
        asm["horizon_summary"] = _horizon_summary("intraday", "long_term")
        result = build_market_composite(asm)
        adj = result["adjustments"]["horizon_adjustment"]
        assert adj["shortest"] == "intraday"
        assert adj["longest"] == "long_term"


# ═════════════════════════════════════════════════════════════════════
# 10. TestCombinedAdjustments
# ═════════════════════════════════════════════════════════════════════

class TestCombinedAdjustments:
    """Multiple adjustments stacking correctly."""

    def test_all_penalties_stack(self):
        """Conflict + quality + horizon penalties all reduce confidence."""
        asm = _all_bullish_assembled()
        asm["quality_summary"] = _quality_summary("degraded")  # 0.15
        asm["freshness_summary"] = _freshness_summary("stale")  # 0.10
        asm["horizon_summary"] = _horizon_summary("intraday", "long_term")  # 0.10
        cr = _conflict_report(count=2, severity="moderate", status="conflicts_detected")  # 0.15

        result = build_market_composite(asm, conflict_report=cr)
        # Base confidence = 1.0 (6/6 bull, 6/6 coverage)
        # Total penalty = 0.15 + 0.10 + 0.10 + 0.15 = 0.50
        assert result["confidence"] == pytest.approx(0.50, abs=0.01)

    def test_heavy_penalties_clamp_at_zero(self):
        asm = _assembled(market_ctx={"a": _bull_engine("a")})  # base ~0.17
        asm["quality_summary"] = _quality_summary("unavailable")  # 0.40
        asm["freshness_summary"] = _freshness_summary("very_stale")  # 0.25
        cr = _conflict_report(count=5, severity="high", status="conflicts_detected")  # 0.30
        result = build_market_composite(asm, conflict_report=cr)
        assert result["confidence"] == 0.0

    def test_conflict_and_quality_both_downgrade_support(self):
        """Quality downgrade takes precedence since applied last."""
        asm = _all_bullish_assembled()
        asm["quality_summary"] = _quality_summary("poor")
        cr = _conflict_report(count=3, severity="high", status="conflicts_detected")
        result = build_market_composite(asm, conflict_report=cr)
        # High conflict → mixed, then poor quality → fragile
        assert result["support_state"] == "fragile"

    def test_status_reflects_degraded_quality(self):
        asm = _all_bullish_assembled()
        asm["quality_summary"] = _quality_summary("poor")
        result = build_market_composite(asm)
        assert result["status"] == "degraded"

    def test_status_reflects_very_stale_freshness(self):
        asm = _all_bullish_assembled()
        asm["freshness_summary"] = _freshness_summary("very_stale")
        result = build_market_composite(asm)
        assert result["status"] == "degraded"

    def test_high_conflict_degrades_status(self):
        """High conflict severity → status 'degraded' even with good quality."""
        asm = _all_bullish_assembled()
        cr = _conflict_report(count=3, severity="high", status="conflicts_detected")
        result = build_market_composite(asm, conflict_report=cr)
        assert result["status"] == "degraded"

    def test_moderate_conflict_does_not_degrade_status(self):
        """Only high conflict severity triggers status downgrade."""
        asm = _all_bullish_assembled()
        cr = _conflict_report(count=2, severity="moderate", status="conflicts_detected")
        result = build_market_composite(asm, conflict_report=cr)
        assert result["status"] == "ok"

    def test_low_conflict_does_not_degrade_status(self):
        asm = _all_bullish_assembled()
        cr = _conflict_report(count=1, severity="low", status="conflicts_detected")
        result = build_market_composite(asm, conflict_report=cr)
        assert result["status"] == "ok"


# ═════════════════════════════════════════════════════════════════════
# 11. TestHumanSummary
# ═════════════════════════════════════════════════════════════════════

class TestHumanSummary:
    """Summary text correctness."""

    def test_risk_on_summary(self):
        result = build_market_composite(_all_bullish_assembled())
        assert "Risk-On" in result["summary"]

    def test_risk_off_summary(self):
        result = build_market_composite(_all_bearish_assembled())
        assert "Risk-Off" in result["summary"]

    def test_neutral_summary(self):
        asm = _assembled(market_ctx={
            "a": _neutral_engine("a"),
            "b": _neutral_engine("b"),
        })
        result = build_market_composite(asm)
        assert "Neutral" in result["summary"]

    def test_confidence_pct_in_summary(self):
        result = build_market_composite(_all_bullish_assembled())
        assert "100%" in result["summary"]

    def test_degraded_note_in_summary(self):
        asm = _all_bullish_assembled()
        asm["quality_summary"] = _quality_summary("poor")
        result = build_market_composite(asm)
        assert "degraded" in result["summary"].lower()

    def test_orderly_in_summary(self):
        result = build_market_composite(_all_bullish_assembled())
        assert "orderly" in result["summary"].lower()

    def test_supportive_in_summary(self):
        result = build_market_composite(_all_bullish_assembled())
        assert "supportive" in result["summary"].lower()


# ═════════════════════════════════════════════════════════════════════
# 12. TestToneHelpers
# ═════════════════════════════════════════════════════════════════════

class TestToneHelpers:
    """Unit tests for tone classification private helpers."""

    # _classify_label
    def test_classify_label_bullish(self):
        assert _classify_label("Bullish") == "bullish"

    def test_classify_label_bearish(self):
        assert _classify_label("Cautionary") == "bearish"

    def test_classify_label_neutral(self):
        assert _classify_label("Mixed") == "neutral"

    def test_classify_label_unknown(self):
        assert _classify_label("SomeRandomThing") == "unknown"

    def test_classify_label_none(self):
        assert _classify_label(None) == "unknown"

    def test_classify_label_empty(self):
        assert _classify_label("") == "unknown"

    def test_classify_label_favored(self):
        assert _classify_label("Favored") == "bullish"

    def test_classify_label_strongly_favored(self):
        assert _classify_label("Strongly Favored") == "bullish"

    def test_classify_label_risk_off(self):
        assert _classify_label("Risk Off") == "bearish"

    def test_classify_label_risk_off_compound(self):
        assert _classify_label("risk_off") == "bearish"

    def test_classify_label_elevated_risk(self):
        assert _classify_label("Elevated Risk") == "bearish"

    def test_classify_label_mixed_case(self):
        assert _classify_label("BULLISH") == "bullish"

    # _classify_score
    def test_classify_score_bullish(self):
        assert _classify_score(75.0) == "bullish"

    def test_classify_score_bearish(self):
        assert _classify_score(25.0) == "bearish"

    def test_classify_score_neutral(self):
        assert _classify_score(50.0) == "neutral"

    def test_classify_score_none(self):
        assert _classify_score(None) == "unknown"

    def test_classify_score_boundary_65(self):
        assert _classify_score(65.0) == "bullish"

    def test_classify_score_boundary_35(self):
        assert _classify_score(35.0) == "bearish"

    def test_classify_score_boundary_64(self):
        assert _classify_score(64.0) == "neutral"

    def test_classify_score_boundary_36(self):
        assert _classify_score(36.0) == "neutral"

    # _engine_tone
    def test_engine_tone_label_priority(self):
        """Label bullish overrides neutral score."""
        norm = {"short_label": "Bullish", "score": 50.0}
        assert _engine_tone(norm) == "bullish"

    def test_engine_tone_score_fallback(self):
        """Unknown label → score used."""
        norm = {"short_label": "Custom", "score": 80.0}
        assert _engine_tone(norm) == "bullish"

    def test_engine_tone_both_neutral(self):
        norm = {"short_label": "Mixed", "score": 50.0}
        assert _engine_tone(norm) == "neutral"

    def test_engine_tone_empty_norm(self):
        assert _engine_tone({}) == "unknown"


# ═════════════════════════════════════════════════════════════════════
# 13. TestIntegrationProofs
# ═════════════════════════════════════════════════════════════════════

class TestIntegrationProofs:
    """End-to-end scenarios proving composite correctness."""

    def test_ideal_bullish_scenario(self):
        """All engines bullish, good quality, no conflicts → best composite."""
        result = build_market_composite(_all_bullish_assembled())
        assert result["status"] == "ok"
        assert result["market_state"] == "risk_on"
        assert result["support_state"] == "supportive"
        assert result["stability_state"] == "orderly"
        assert result["confidence"] == 1.0
        assert result["adjustments"]["conflict_adjustment"] is None

    def test_ideal_bearish_scenario(self):
        """All engines bearish, good quality, no conflicts."""
        result = build_market_composite(_all_bearish_assembled())
        assert result["status"] == "ok"
        assert result["market_state"] == "risk_off"
        assert result["support_state"] == "supportive"
        assert result["stability_state"] == "orderly"
        assert result["confidence"] == 1.0

    def test_mixed_with_conflicts(self):
        """Mixed engines + moderate conflict → careful composite."""
        cr = _conflict_report(count=2, severity="moderate", status="conflicts_detected")
        result = build_market_composite(_mixed_assembled(), conflict_report=cr)
        assert result["market_state"] == "risk_on"  # 3 bull > 2 bear > 1 neutral
        assert result["stability_state"] in ("noisy", "unstable")
        assert result["confidence"] < 0.7

    def test_degraded_everything(self):
        """Poor quality, stale, high conflicts, split engines."""
        asm = _assembled(
            market_ctx={
                "a": _bull_engine("a"),
                "b": _bear_engine("b"),
                "c": _neutral_engine("c"),
            },
            quality=_quality_summary("poor"),
            freshness=_freshness_summary("very_stale"),
            horizon=_horizon_summary("intraday", "long_term"),
        )
        cr = _conflict_report(count=5, severity="high", status="conflicts_detected")
        result = build_market_composite(asm, conflict_report=cr)
        assert result["status"] == "degraded"
        assert result["support_state"] == "fragile"
        assert result["stability_state"] == "unstable"
        assert result["confidence"] <= 0.1

    def test_metadata_engines_used(self):
        result = build_market_composite(_all_bullish_assembled(4))
        assert result["metadata"]["engines_used"] == 4

    def test_metadata_conflict_info_from_report(self):
        cr = _conflict_report(count=3, severity="moderate", status="conflicts_detected")
        result = build_market_composite(_all_bullish_assembled(), conflict_report=cr)
        assert result["metadata"]["conflict_count"] == 3
        assert result["metadata"]["conflict_severity"] == "moderate"

    def test_metadata_quality_freshness(self):
        asm = _all_bullish_assembled()
        asm["quality_summary"] = _quality_summary("degraded")
        asm["freshness_summary"] = _freshness_summary("stale")
        result = build_market_composite(asm)
        assert result["metadata"]["overall_quality"] == "degraded"
        assert result["metadata"]["overall_freshness"] == "stale"

    def test_metadata_horizon_span(self):
        asm = _all_bullish_assembled()
        asm["horizon_summary"] = _horizon_summary("intraday", "medium_term")
        result = build_market_composite(asm)
        assert result["metadata"]["horizon_span"] == "intraday → medium_term"

    def test_metadata_same_horizon_no_arrow(self):
        asm = _all_bullish_assembled()
        asm["horizon_summary"] = _horizon_summary("short_term", "short_term")
        result = build_market_composite(asm)
        assert result["metadata"]["horizon_span"] == "short_term"

    def test_total_key_count_stable(self):
        """Ensure no extra keys leak into the output."""
        result = build_market_composite(_all_bullish_assembled())
        assert len(result) == len(TOP_LEVEL_KEYS)


# ═════════════════════════════════════════════════════════════════════
# 14. TestRepresentativeOutputs (v1.1)
# ═════════════════════════════════════════════════════════════════════

class TestRepresentativeOutputs:
    """Representative compact-output scenarios proving world-state clarity."""

    def test_supportive_risk_on(self):
        """Clean bullish, good quality, no conflicts → risk_on / supportive / orderly."""
        result = build_market_composite(_all_bullish_assembled())
        assert result["status"] == "ok"
        assert result["market_state"] == "risk_on"
        assert result["support_state"] == "supportive"
        assert result["stability_state"] == "orderly"
        assert result["confidence"] == 1.0
        assert "Risk-On" in result["summary"]
        assert "supportive" in result["summary"].lower()
        assert "orderly" in result["summary"].lower()

    def test_mixed_scenario(self):
        """3 bull / 2 bear / 1 neutral, no conflicts → risk_on / fragile / noisy.

        alignment_ratio = 3/6 = 0.5 → fragile (split threshold);
        bull+bear split → noisy.
        """
        result = build_market_composite(_mixed_assembled())
        assert result["market_state"] == "risk_on"
        assert result["support_state"] == "fragile"
        assert result["stability_state"] == "noisy"
        assert 0.3 < result["confidence"] < 0.8

    def test_fragile_unstable(self):
        """Split engines + poor quality + high conflicts → fragile / unstable."""
        asm = _assembled(
            market_ctx={
                "a": _bull_engine("a"),
                "b": _bear_engine("b"),
                "c": _neutral_engine("c"),
            },
            quality=_quality_summary("poor"),
        )
        cr = _conflict_report(count=4, severity="high", status="conflicts_detected")
        result = build_market_composite(asm, conflict_report=cr)
        assert result["status"] == "degraded"
        assert result["support_state"] == "fragile"
        assert result["stability_state"] == "unstable"
        assert result["confidence"] <= 0.1

    def test_degraded_confidence(self):
        """Good alignment but degraded data → confidence substantially reduced."""
        asm = _all_bullish_assembled()
        asm["quality_summary"] = _quality_summary("degraded", degraded=3)
        asm["freshness_summary"] = _freshness_summary("stale")
        result = build_market_composite(asm)
        # base 1.0 - quality 0.15 - freshness 0.10 - degraded 0.10 = 0.65
        assert result["confidence"] == pytest.approx(0.65, abs=0.01)
        assert result["market_state"] == "risk_on"
        assert result["support_state"] == "mixed"  # degraded quality → not supportive

    def test_version_in_representative_output(self):
        """All representative outputs carry v1.1."""
        for asm in [_all_bullish_assembled(), _all_bearish_assembled(), _mixed_assembled()]:
            result = build_market_composite(asm)
            assert result["composite_version"] == "1.1"
            assert result["metadata"]["composite_version"] == "1.1"
