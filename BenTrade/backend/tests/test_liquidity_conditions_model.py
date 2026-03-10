"""Tests for the Liquidity & Financial Conditions model analysis layer.

Covers:
  - Raw evidence extraction (excluded fields, included raw_inputs)
  - LLM output coercion (normalization, clamping, missing-field rejection)
"""

from __future__ import annotations

import pytest

from common.model_analysis import (
    _coerce_liquidity_conditions_model_output,
    _extract_liquidity_conditions_raw_evidence,
    _LIQUIDITY_CONDITIONS_EXCLUDED_FIELDS,
)


# ═══════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════

def _sample_engine_result() -> dict:
    """A realistic engine result with all fields."""
    return {
        "engine": "liquidity_financial_conditions",
        "as_of": "2025-06-01T12:00:00Z",
        "score": 72.5,
        "label": "Supportive Conditions",
        "short_label": "Supportive",
        "confidence_score": 85,
        "signal_quality": "high",
        "summary": "Broad liquidity conditions are supportive.",
        "pillar_scores": {
            "rates_policy_pressure": 75.0,
            "financial_conditions_tightness": 70.0,
            "credit_funding_stress": 68.0,
            "dollar_global_liquidity": 80.0,
            "liquidity_stability_fragility": 72.0,
        },
        "pillar_weights": {
            "rates_policy_pressure": 0.25,
            "financial_conditions_tightness": 0.25,
            "credit_funding_stress": 0.20,
            "dollar_global_liquidity": 0.15,
            "liquidity_stability_fragility": 0.15,
        },
        "pillar_explanations": {
            "rates_policy_pressure": "Low front-end rates supportive.",
            "financial_conditions_tightness": "FCI proxy is easy.",
            "credit_funding_stress": "IG/HY spreads tight.",
            "dollar_global_liquidity": "Weak dollar supports liquidity.",
            "liquidity_stability_fragility": "Conditions stable.",
        },
        "support_vs_stress": {
            "supportive_for_risk_assets": 70,
            "tightening_pressure": 25,
            "stress_risk": 15,
            "fragility": 10,
        },
        "positive_contributors": ["Rates & Policy Pressure", "Dollar / Global Liquidity"],
        "negative_contributors": [],
        "conflicting_signals": [],
        "trader_takeaway": "Conditions favor normal risk deployment.",
        "warnings": [],
        "missing_inputs": [],
        "diagnostics": {"pillar_details": {}},
        "raw_inputs": {
            "rates": {"two_year_yield": 2.0, "ten_year_yield": 3.2},
            "conditions": {"vix": 13.0},
            "credit": {"ig_spread": 0.8, "hy_spread": 3.2},
            "dollar": {"dxy_level": 97.0},
            "stability": {"vix": 13.0},
        },
    }


def _valid_model_output() -> dict:
    return {
        "label": "Supportive Conditions",
        "score": 74,
        "confidence": 0.85,
        "summary": "Liquidity conditions are broadly supportive.",
        "tone": "bullish",
        "pillar_interpretation": {
            "rates_policy_pressure": "Low rates ease front-end pressure.",
            "financial_conditions_tightness": "FCI shows easy conditions.",
            "credit_funding_stress": "Credit spreads are tight.",
            "dollar_global_liquidity": "Weak dollar supports flows.",
            "liquidity_stability_fragility": "Conditions are stable.",
        },
        "liquidity_drivers": {
            "supportive_factors": ["Low 2Y yield", "Tight IG spreads"],
            "restrictive_factors": [],
            "latent_stress_signals": ["Proxy FCI, not true NFCI"],
        },
        "score_drivers": {
            "primary_driver": "Low front-end rates",
            "secondary_drivers": ["Tight credit", "Weak dollar"],
        },
        "market_implications": {
            "risk_asset_outlook": "Favorable",
            "credit_conditions": "Healthy",
            "funding_assessment": "Normal",
            "position_sizing": "Full size",
            "strategy_recommendation": "Standard credit spreads",
        },
        "uncertainty_flags": ["Proxy FCI used instead of NFCI"],
        "trader_takeaway": "Liquidity backdrop supports normal risk positioning.",
    }


# ═══════════════════════════════════════════════════════════════════════
# EVIDENCE EXTRACTION TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestExtractLiquidityConditionsRawEvidence:
    """Verify that extraction strips derived fields and preserves raw inputs."""

    def test_excludes_all_derived_fields(self):
        evidence = _extract_liquidity_conditions_raw_evidence(_sample_engine_result())
        for excluded in _LIQUIDITY_CONDITIONS_EXCLUDED_FIELDS:
            assert excluded not in evidence, f"Derived field '{excluded}' leaked into evidence"

    def test_includes_raw_inputs(self):
        evidence = _extract_liquidity_conditions_raw_evidence(_sample_engine_result())
        ri = evidence["raw_inputs"]
        assert "rates" in ri
        assert "conditions" in ri
        assert "credit" in ri
        assert "dollar" in ri
        assert "stability" in ri

    def test_includes_pillar_scores(self):
        evidence = _extract_liquidity_conditions_raw_evidence(_sample_engine_result())
        assert "pillar_scores" in evidence
        assert len(evidence["pillar_scores"]) == 5

    def test_includes_pillar_weights(self):
        evidence = _extract_liquidity_conditions_raw_evidence(_sample_engine_result())
        assert "pillar_weights" in evidence

    def test_includes_warnings_and_missing(self):
        evidence = _extract_liquidity_conditions_raw_evidence(_sample_engine_result())
        assert "warnings" in evidence
        assert "missing_inputs" in evidence

    def test_preserves_raw_values(self):
        evidence = _extract_liquidity_conditions_raw_evidence(_sample_engine_result())
        assert evidence["raw_inputs"]["rates"]["two_year_yield"] == 2.0
        assert evidence["raw_inputs"]["credit"]["ig_spread"] == 0.8

    def test_handles_empty_engine_result(self):
        evidence = _extract_liquidity_conditions_raw_evidence({})
        assert evidence["raw_inputs"]["rates"] == {}
        assert evidence["pillar_scores"] == {}

    def test_handles_partial_raw_inputs(self):
        partial = _sample_engine_result()
        partial["raw_inputs"] = {"rates": {"two_year_yield": 3.0}}
        evidence = _extract_liquidity_conditions_raw_evidence(partial)
        assert evidence["raw_inputs"]["rates"]["two_year_yield"] == 3.0
        assert evidence["raw_inputs"]["conditions"] == {}


# ═══════════════════════════════════════════════════════════════════════
# OUTPUT COERCION TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestCoerceLiquidityConditionsModelOutput:
    """Verify LLM output normalization."""

    def test_valid_output_accepted(self):
        result = _coerce_liquidity_conditions_model_output(_valid_model_output())
        assert result is not None
        assert result["label"] == "SUPPORTIVE CONDITIONS"
        assert result["score"] == 74.0
        assert result["confidence"] == 0.85

    def test_label_uppercased(self):
        out = _valid_model_output()
        out["label"] = "mixed but manageable"
        result = _coerce_liquidity_conditions_model_output(out)
        assert result["label"] == "MIXED BUT MANAGEABLE"

    def test_score_clamped_to_0_100(self):
        out = _valid_model_output()
        out["score"] = 150
        result = _coerce_liquidity_conditions_model_output(out)
        assert result["score"] == 100.0

        out["score"] = -10
        result = _coerce_liquidity_conditions_model_output(out)
        assert result["score"] == 0.0

    def test_confidence_clamped_to_0_1(self):
        out = _valid_model_output()
        out["confidence"] = 1.5
        result = _coerce_liquidity_conditions_model_output(out)
        assert result["confidence"] == 1.0

        out["confidence"] = -0.5
        result = _coerce_liquidity_conditions_model_output(out)
        assert result["confidence"] == 0.0

    def test_confidence_defaults_to_05_when_missing(self):
        out = _valid_model_output()
        del out["confidence"]
        result = _coerce_liquidity_conditions_model_output(out)
        assert result["confidence"] == 0.5

    def test_missing_label_returns_none(self):
        out = _valid_model_output()
        del out["label"]
        assert _coerce_liquidity_conditions_model_output(out) is None

    def test_missing_score_returns_none(self):
        out = _valid_model_output()
        del out["score"]
        assert _coerce_liquidity_conditions_model_output(out) is None

    def test_missing_summary_returns_none(self):
        out = _valid_model_output()
        del out["summary"]
        assert _coerce_liquidity_conditions_model_output(out) is None

    def test_non_numeric_score_returns_none(self):
        out = _valid_model_output()
        out["score"] = "not_a_number"
        assert _coerce_liquidity_conditions_model_output(out) is None

    def test_non_dict_returns_none(self):
        assert _coerce_liquidity_conditions_model_output("string") is None
        assert _coerce_liquidity_conditions_model_output(42) is None
        assert _coerce_liquidity_conditions_model_output(None) is None

    def test_pillar_interpretation_preserved(self):
        result = _coerce_liquidity_conditions_model_output(_valid_model_output())
        pi = result["pillar_interpretation"]
        assert "rates_policy_pressure" in pi
        assert "financial_conditions_tightness" in pi

    def test_pillar_interpretation_whitespace_stripped(self):
        out = _valid_model_output()
        out["pillar_interpretation"]["rates_policy_pressure"] = "  padded text  "
        result = _coerce_liquidity_conditions_model_output(out)
        assert result["pillar_interpretation"]["rates_policy_pressure"] == "padded text"

    def test_liquidity_drivers_coerced(self):
        result = _coerce_liquidity_conditions_model_output(_valid_model_output())
        ld = result["liquidity_drivers"]
        assert isinstance(ld["supportive_factors"], list)
        assert isinstance(ld["restrictive_factors"], list)
        assert isinstance(ld["latent_stress_signals"], list)

    def test_liquidity_drivers_missing_defaults_to_empty(self):
        out = _valid_model_output()
        del out["liquidity_drivers"]
        result = _coerce_liquidity_conditions_model_output(out)
        ld = result["liquidity_drivers"]
        assert ld["supportive_factors"] == []
        assert ld["restrictive_factors"] == []

    def test_score_drivers_coerced(self):
        result = _coerce_liquidity_conditions_model_output(_valid_model_output())
        sd = result["score_drivers"]
        assert sd["primary_driver"] == "Low front-end rates"
        assert isinstance(sd["secondary_drivers"], list)

    def test_market_implications_coerced(self):
        result = _coerce_liquidity_conditions_model_output(_valid_model_output())
        mi = result["market_implications"]
        assert mi["risk_asset_outlook"] == "Favorable"
        assert mi["credit_conditions"] == "Healthy"

    def test_uncertainty_flags_coerced(self):
        result = _coerce_liquidity_conditions_model_output(_valid_model_output())
        assert isinstance(result["uncertainty_flags"], list)
        assert len(result["uncertainty_flags"]) >= 1

    def test_trader_takeaway_coerced(self):
        result = _coerce_liquidity_conditions_model_output(_valid_model_output())
        assert result["trader_takeaway"] != ""

    def test_tone_defaults_to_neutral(self):
        out = _valid_model_output()
        del out["tone"]
        result = _coerce_liquidity_conditions_model_output(out)
        assert result["tone"] == "neutral"

    def test_minimal_valid_output(self):
        """Only required fields — everything else defaults."""
        minimal = {"label": "Mixed", "score": 55, "summary": "Middle of road."}
        result = _coerce_liquidity_conditions_model_output(minimal)
        assert result is not None
        assert result["label"] == "MIXED"
        assert result["score"] == 55.0
        assert result["confidence"] == 0.5
        assert result["tone"] == "neutral"
        assert result["pillar_interpretation"] == {}
        assert result["liquidity_drivers"]["supportive_factors"] == []
