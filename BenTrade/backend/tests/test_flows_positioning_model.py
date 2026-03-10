"""Tests for Flows & Positioning model analysis (LLM layer).

Covers:
  - _extract_flows_positioning_raw_evidence: field inclusion/exclusion
  - _coerce_flows_positioning_model_output: normalization, clamping, fallbacks
"""

import pytest


# ═══════════════════════════════════════════════════════════════════════
# EVIDENCE EXTRACTION TESTS (Item 6)
# ═══════════════════════════════════════════════════════════════════════


class TestExtractFlowsPositioningRawEvidence:
    """Verify raw evidence excludes derived fields and includes only raw inputs."""

    def _make_engine_result(self):
        return {
            "engine": "flows_positioning",
            "score": 68.5,
            "label": "Supportive Positioning",
            "short_label": "Supportive",
            "summary": "Flows are supportive.",
            "confidence_score": 72.0,
            "signal_quality": "medium",
            "positive_contributors": ["Flow direction is strong"],
            "negative_contributors": ["Crowding slightly elevated"],
            "conflicting_signals": ["Some conflict"],
            "trader_takeaway": "Normal sizing.",
            "strategy_bias": {
                "continuation_support": 65.0,
                "reversal_risk": 30.0,
                "squeeze_potential": 25.0,
                "fragility": 35.0,
            },
            "pillar_scores": {
                "positioning_pressure": 70.0,
                "crowding_stretch": 65.0,
                "squeeze_unwind_risk": 72.0,
                "flow_direction_persistence": 75.0,
                "positioning_stability": 60.0,
            },
            "pillar_weights": {
                "positioning_pressure": 0.25,
                "crowding_stretch": 0.20,
                "squeeze_unwind_risk": 0.20,
                "flow_direction_persistence": 0.20,
                "positioning_stability": 0.15,
            },
            "raw_inputs": {
                "positioning": {"put_call_ratio": 0.78, "vix": 15.0},
                "crowding": {"futures_net_long_pct": 52.0},
                "squeeze": {"short_interest_pct": 1.5},
                "flow": {"flow_direction_score": 68.0},
                "stability": {"vix": 15.0, "flow_volatility": 25.0},
            },
            "warnings": ["Some warning"],
            "missing_inputs": ["some_missing"],
        }

    def test_includes_raw_inputs(self):
        from common.model_analysis import _extract_flows_positioning_raw_evidence
        evidence = _extract_flows_positioning_raw_evidence(self._make_engine_result())
        assert "raw_inputs" in evidence
        for key in ("positioning", "crowding", "squeeze", "flow", "stability"):
            assert key in evidence["raw_inputs"], f"Missing raw_inputs.{key}"

    def test_includes_pillar_scores(self):
        from common.model_analysis import _extract_flows_positioning_raw_evidence
        evidence = _extract_flows_positioning_raw_evidence(self._make_engine_result())
        assert "pillar_scores" in evidence
        assert evidence["pillar_scores"]["positioning_pressure"] == 70.0

    def test_includes_pillar_weights(self):
        from common.model_analysis import _extract_flows_positioning_raw_evidence
        evidence = _extract_flows_positioning_raw_evidence(self._make_engine_result())
        assert "pillar_weights" in evidence
        assert evidence["pillar_weights"]["positioning_pressure"] == 0.25

    def test_includes_warnings_and_missing(self):
        from common.model_analysis import _extract_flows_positioning_raw_evidence
        evidence = _extract_flows_positioning_raw_evidence(self._make_engine_result())
        assert len(evidence["warnings"]) == 1
        assert len(evidence["missing_inputs"]) == 1

    def test_excludes_all_derived_fields(self):
        from common.model_analysis import (
            _FLOWS_POSITIONING_EXCLUDED_FIELDS,
            _extract_flows_positioning_raw_evidence,
        )
        evidence = _extract_flows_positioning_raw_evidence(self._make_engine_result())
        for field in _FLOWS_POSITIONING_EXCLUDED_FIELDS:
            assert field not in evidence, \
                f"Derived field '{field}' should be excluded from evidence"

    def test_handles_empty_engine_result(self):
        from common.model_analysis import _extract_flows_positioning_raw_evidence
        evidence = _extract_flows_positioning_raw_evidence({})
        # Should still have structure with empty defaults
        assert evidence["raw_inputs"]["positioning"] == {}
        assert evidence["pillar_scores"] == {}
        assert evidence["pillar_weights"] == {}

    def test_handles_partial_raw_inputs(self):
        from common.model_analysis import _extract_flows_positioning_raw_evidence
        partial = {"raw_inputs": {"positioning": {"vix": 15.0}}}
        evidence = _extract_flows_positioning_raw_evidence(partial)
        assert evidence["raw_inputs"]["positioning"] == {"vix": 15.0}
        assert evidence["raw_inputs"]["crowding"] == {}


# ═══════════════════════════════════════════════════════════════════════
# OUTPUT COERCION TESTS (Item 6)
# ═══════════════════════════════════════════════════════════════════════


class TestCoerceFlowsPositioningModelOutput:
    """Verify LLM output normalization/validation for flows positioning."""

    def test_valid_output(self):
        from common.model_analysis import _coerce_flows_positioning_model_output
        raw = {
            "label": "SUPPORTIVE",
            "score": 72.5,
            "confidence": 0.8,
            "summary": "Flows are supportive of continuation.",
            "pillar_analysis": {
                "positioning_pressure": "Moderate net long, healthy",
                "crowding_stretch": "Not overcrowded",
            },
            "flow_drivers": {
                "supportive_factors": ["Strong inflows"],
                "risk_factors": ["Moderate VIX"],
                "ambiguous_factors": [],
            },
            "trading_implications": {
                "continuation_support": "Strong",
                "reversal_risk": "Low",
                "position_sizing": "Normal",
                "strategy_recommendation": "Credit spreads",
                "squeeze_guidance": "No concern",
            },
            "uncertainty_flags": ["Limited data"],
            "trader_takeaway": "Continue with normal sizing.",
        }
        result = _coerce_flows_positioning_model_output(raw)
        assert result is not None
        assert result["label"] == "SUPPORTIVE"
        assert result["score"] == 72.5
        assert result["confidence"] == 0.8
        assert result["summary"] == "Flows are supportive of continuation."
        assert result["trader_takeaway"] == "Continue with normal sizing."
        assert len(result["flow_drivers"]["supportive_factors"]) == 1

    def test_clamps_score_above_100(self):
        from common.model_analysis import _coerce_flows_positioning_model_output
        result = _coerce_flows_positioning_model_output({
            "label": "STRONG", "score": 150, "confidence": 1.5,
            "summary": "Test",
        })
        assert result["score"] == 100.0
        assert result["confidence"] == 1.0

    def test_clamps_negative_score(self):
        from common.model_analysis import _coerce_flows_positioning_model_output
        result = _coerce_flows_positioning_model_output({
            "label": "WEAK", "score": -10, "confidence": -0.5,
            "summary": "Test",
        })
        assert result["score"] == 0.0
        assert result["confidence"] == 0.0

    def test_returns_none_missing_label(self):
        from common.model_analysis import _coerce_flows_positioning_model_output
        assert _coerce_flows_positioning_model_output(
            {"score": 50, "summary": "X"}
        ) is None

    def test_returns_none_missing_score(self):
        from common.model_analysis import _coerce_flows_positioning_model_output
        assert _coerce_flows_positioning_model_output(
            {"label": "WEAK", "summary": "X"}
        ) is None

    def test_returns_none_missing_summary(self):
        from common.model_analysis import _coerce_flows_positioning_model_output
        assert _coerce_flows_positioning_model_output(
            {"label": "WEAK", "score": 50}
        ) is None

    def test_returns_none_for_non_dict(self):
        from common.model_analysis import _coerce_flows_positioning_model_output
        assert _coerce_flows_positioning_model_output("not a dict") is None
        assert _coerce_flows_positioning_model_output(None) is None
        assert _coerce_flows_positioning_model_output(42) is None

    def test_returns_none_for_invalid_score_type(self):
        from common.model_analysis import _coerce_flows_positioning_model_output
        assert _coerce_flows_positioning_model_output({
            "label": "WEAK", "score": "not_a_number", "summary": "X",
        }) is None

    def test_default_confidence(self):
        from common.model_analysis import _coerce_flows_positioning_model_output
        result = _coerce_flows_positioning_model_output({
            "label": "MIXED", "score": 55, "summary": "Test",
        })
        assert result["confidence"] == 0.5  # Default when omitted

    def test_pillar_analysis_normalization(self):
        from common.model_analysis import _coerce_flows_positioning_model_output
        result = _coerce_flows_positioning_model_output({
            "label": "MIXED", "score": 55, "summary": "Test",
            "pillar_analysis": {"key": "  value with spaces  "},
        })
        assert result["pillar_analysis"]["key"] == "value with spaces"

    def test_pillar_analysis_defaults_to_empty_dict(self):
        from common.model_analysis import _coerce_flows_positioning_model_output
        result = _coerce_flows_positioning_model_output({
            "label": "MIXED", "score": 55, "summary": "Test",
        })
        assert result["pillar_analysis"] == {}

    def test_flow_drivers_defaults(self):
        from common.model_analysis import _coerce_flows_positioning_model_output
        result = _coerce_flows_positioning_model_output({
            "label": "MIXED", "score": 55, "summary": "Test",
        })
        fd = result["flow_drivers"]
        assert fd["supportive_factors"] == []
        assert fd["risk_factors"] == []
        assert fd["ambiguous_factors"] == []

    def test_trading_implications_defaults(self):
        from common.model_analysis import _coerce_flows_positioning_model_output
        result = _coerce_flows_positioning_model_output({
            "label": "MIXED", "score": 55, "summary": "Test",
        })
        assert result["trading_implications"] == {}

    def test_uncertainty_flags_coercion(self):
        from common.model_analysis import _coerce_flows_positioning_model_output
        result = _coerce_flows_positioning_model_output({
            "label": "MIXED", "score": 55, "summary": "Test",
            "uncertainty_flags": "single string",
        })
        # _coerce_string_list should wrap single string → list
        assert isinstance(result["uncertainty_flags"], list)

    def test_label_uppercased(self):
        from common.model_analysis import _coerce_flows_positioning_model_output
        result = _coerce_flows_positioning_model_output({
            "label": "mixed but tradable", "score": 55, "summary": "Test",
        })
        assert result["label"] == "MIXED BUT TRADABLE"

    def test_trader_takeaway_defaults_to_empty(self):
        from common.model_analysis import _coerce_flows_positioning_model_output
        result = _coerce_flows_positioning_model_output({
            "label": "MIXED", "score": 55, "summary": "Test",
        })
        assert result["trader_takeaway"] == ""
